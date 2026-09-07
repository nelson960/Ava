from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import isfinite
from time import monotonic
from typing import Literal

from .resource_governor import ResourceGovernorSnapshot


# SAFETY/RESOURCE: observability must remain bounded even if a provider session
# outlives the expected warm conversation window. Saturation is exposed rather
# than evicting records for provider items that may still exist.
MAX_PROVIDER_LEDGER_ITEMS = 1_024
MAX_PROVIDER_LEDGER_RESPONSES = 1_024
MAX_PENDING_CONTINUATIONS = 64
MAX_PENDING_ITEM_ADMISSIONS = 64
MAX_RATE_LIMIT_ENTRIES = 32


@dataclass(frozen=True, slots=True)
class ProviderInputTranscriptionUsageObservation:
    provider_item_id: str
    local_turn_id: str | None
    observed_sequence: int
    usage_type: str
    input_tokens: int | None
    input_text_tokens: int | None
    input_audio_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    duration_seconds: float | None


@dataclass(frozen=True, slots=True)
class ProviderConversationItem:
    provider_item_id: str
    provider_response_id: str | None
    local_turn_id: str | None
    item_type: str
    observed_sequence: int
    logical_group_id: str | None
    function_call_id: str | None = None
    dependency_call_id: str | None = None
    input_transcription_usage: ProviderInputTranscriptionUsageObservation | None = None


@dataclass(frozen=True, slots=True)
class ProviderResponseObservation:
    provider_response_id: str
    local_turn_id: str | None
    observed_sequence: int
    logical_group_id: str | None
    continuation_of_call_id: str | None = None
    terminal_observed: bool = False
    usage: ProviderUsageObservation | None = None


@dataclass(frozen=True, slots=True)
class ProviderUsageObservation:
    provider_response_id: str
    local_turn_id: str
    input_tokens: int
    cached_input_tokens: int | None
    uncached_input_tokens: int | None
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class ProviderRateLimitObservation:
    name: str
    limit: int | None
    remaining: int | None
    reset_seconds: float | None
    observed_at: datetime
    observed_sequence: int


@dataclass(frozen=True, slots=True)
class SessionHealthSnapshot:
    session_id: str | None
    provider_call_id: str | None
    created_at: datetime
    uptime_seconds: float
    closed: bool
    observed_provider_item_count: int
    provider_item_observation_complete: bool
    provider_inventory_authoritative: Literal[False]
    observability_degraded: bool
    response_count: int
    response_count_complete: bool
    function_call_count: int
    continuation_count: int
    pending_response_count: int
    latest_usage: ProviderUsageObservation | None
    latest_input_transcription_usage: ProviderInputTranscriptionUsageObservation | None
    cumulative_input_tokens: int
    cumulative_output_tokens: int
    cumulative_total_tokens: int
    cumulative_cached_input_tokens: int | None
    cumulative_uncached_input_tokens: int | None
    rate_limits: tuple[ProviderRateLimitObservation, ...]
    rate_limit_snapshot_truncated: bool
    tpm: ProviderRateLimitObservation | None
    rpm: ProviderRateLimitObservation | None
    resource_governor: ResourceGovernorSnapshot | None = None


class ProviderConversationLedger:
    """Bounded, session-owned observation of OpenAI conversation events.

    The ledger never decides response, turn, cancellation, or provider-history
    lifecycle. Those owners supply explicit admissions after their existing
    identity fences accept an item or response. ``items`` and the observed item
    count describe sideband observations, minus explicit deletion acknowledgements;
    they are never an authoritative inventory of provider-retained inference context
    because automatic provider truncation is opaque to this event stream.
    """

    def __init__(
        self,
        *,
        session_id: str | None,
        provider_call_id: str | None,
        max_items: int = MAX_PROVIDER_LEDGER_ITEMS,
        max_responses: int = MAX_PROVIDER_LEDGER_RESPONSES,
        max_pending_continuations: int = MAX_PENDING_CONTINUATIONS,
        max_pending_item_admissions: int = MAX_PENDING_ITEM_ADMISSIONS,
        max_rate_limit_entries: int = MAX_RATE_LIMIT_ENTRIES,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        for value, name in (
            (max_items, "max_items"),
            (max_responses, "max_responses"),
            (max_pending_continuations, "max_pending_continuations"),
            (max_pending_item_admissions, "max_pending_item_admissions"),
            (max_rate_limit_entries, "max_rate_limit_entries"),
        ):
            if value < 1:
                raise ValueError(f"{name} must be positive")
        if session_id is not None and not session_id.strip():
            raise ValueError("session_id cannot be empty")
        if provider_call_id is not None and not provider_call_id.strip():
            raise ValueError("provider_call_id cannot be empty")

        self._session_id = session_id
        self._provider_call_id = provider_call_id
        self._max_items = max_items
        self._max_responses = max_responses
        self._max_pending_continuations = max_pending_continuations
        self._max_pending_item_admissions = max_pending_item_admissions
        self._max_rate_limit_entries = max_rate_limit_entries
        self._now = now
        self._monotonic_clock = monotonic_clock
        self._created_at = now()
        if self._created_at.tzinfo is None:
            raise ValueError("observability clock must return timezone-aware values")
        self._started_at = monotonic_clock()
        self._closed = False

        self._items: OrderedDict[str, ProviderConversationItem] = OrderedDict()
        self._responses: OrderedDict[str, ProviderResponseObservation] = OrderedDict()
        self._call_item_ids: dict[str, str] = {}
        self._pending_continuations: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._pending_item_owners: OrderedDict[str, tuple[str, str | None]] = (
            OrderedDict()
        )
        self._provider_item_observation_complete = True
        self._response_observation_complete = True
        self._observability_degraded = False

        self._response_count = 0
        self._function_call_count = 0
        self._continuation_count = 0
        self._latest_usage: ProviderUsageObservation | None = None
        self._latest_input_transcription_usage: (
            ProviderInputTranscriptionUsageObservation | None
        ) = None
        self._cumulative_input_tokens = 0
        self._cumulative_output_tokens = 0
        self._cumulative_total_tokens = 0
        self._cumulative_cached_input_tokens: int | None = None
        self._cumulative_uncached_input_tokens: int | None = None
        self._usage_count = 0
        self._all_usage_has_cached_detail = True

        self._rate_limits_by_name: OrderedDict[str, ProviderRateLimitObservation] = (
            OrderedDict()
        )
        self._rate_limit_snapshot_truncated = False

    @property
    def items(self) -> tuple[ProviderConversationItem, ...]:
        return tuple(self._items.values())

    @property
    def responses(self) -> tuple[ProviderResponseObservation, ...]:
        return tuple(self._responses.values())

    def item(self, provider_item_id: str) -> ProviderConversationItem | None:
        return self._items.get(provider_item_id)

    def response(self, provider_response_id: str) -> ProviderResponseObservation | None:
        return self._responses.get(provider_response_id)

    def mark_degraded(self) -> None:
        """Permanently mark this optional observation stream as incomplete."""

        self._observability_degraded = True
        self._provider_item_observation_complete = False
        self._response_observation_complete = False

    def observe_provider_event(
        self,
        event: Mapping[str, object],
        *,
        observed_sequence: int,
    ) -> None:
        """Observe identity-bearing provider data without interpreting content."""

        if self._closed or observed_sequence < 1:
            return
        event_type = _string(event.get("type"))
        if event_type is None:
            return

        if event_type == "rate_limits.updated":
            self._observe_rate_limits(event, observed_sequence=observed_sequence)
            return
        if event_type == "conversation.item.deleted":
            item_id = _string(event.get("item_id"))
            if item_id is not None:
                removed = self._items.pop(item_id, None)
                if removed is not None and removed.function_call_id is not None:
                    self._call_item_ids.pop(removed.function_call_id, None)
                self._pending_item_owners.pop(item_id, None)
            return
        if event_type == "input_audio_buffer.committed":
            item_id = _string(event.get("item_id"))
            if item_id is not None:
                self._observe_item(
                    {"id": item_id, "type": "message", "role": "user"},
                    provider_response_id=None,
                    observed_sequence=observed_sequence,
                )
            return
        if event_type in {"conversation.item.added", "conversation.item.created"}:
            item = _mapping(event.get("item"))
            if item is not None:
                self._observe_item(
                    item,
                    provider_response_id=None,
                    observed_sequence=observed_sequence,
                )
            return
        if event_type == "conversation.item.input_audio_transcription.completed":
            self._observe_input_transcription_usage(
                event,
                observed_sequence=observed_sequence,
            )
            return

        response_id = _response_id(event)
        if event_type == "response.created" and response_id is not None:
            self._observe_response(response_id, observed_sequence=observed_sequence)
            return
        if event_type in {"response.output_item.added", "response.output_item.done"}:
            item = _mapping(event.get("item"))
            if item is not None:
                self._observe_item(
                    item,
                    provider_response_id=response_id,
                    observed_sequence=observed_sequence,
                )
            return
        if event_type == "response.done" and response_id is not None:
            response = self._observe_response(
                response_id, observed_sequence=observed_sequence
            )
            if response is not None:
                self._responses[response_id] = replace(
                    response,
                    terminal_observed=True,
                )
            response_value = _mapping(event.get("response"))
            output = (
                response_value.get("output") if response_value is not None else None
            )
            if isinstance(output, Sequence) and not isinstance(output, (str, bytes)):
                for value in output:
                    item = _mapping(value)
                    if item is not None:
                        self._observe_item(
                            item,
                            provider_response_id=response_id,
                            observed_sequence=observed_sequence,
                        )

    def assign_input_owner(self, *, provider_item_id: str, local_turn_id: str) -> None:
        """Observe authoritative input admission without requiring item arrival."""

        self.expect_item_owner(
            provider_item_id=provider_item_id,
            local_turn_id=local_turn_id,
        )

    def assign_response_owner(
        self,
        *,
        provider_response_id: str,
        local_turn_id: str,
    ) -> None:
        if self._closed:
            return
        response = self._responses.get(provider_response_id)
        if response is None:
            self._response_observation_complete = False
            return
        if response.local_turn_id not in {None, local_turn_id}:
            self._response_observation_complete = False
            return

        continuation_call_id = response.continuation_of_call_id
        if continuation_call_id is None:
            continuation_key = next(
                (key for key in self._pending_continuations if key[0] == local_turn_id),
                None,
            )
            if continuation_key is not None:
                _, continuation_call_id = continuation_key
                self._pending_continuations.pop(continuation_key, None)
                self._continuation_count += 1
        logical_group_id = _logical_group_id(local_turn_id)
        self._responses[provider_response_id] = replace(
            response,
            local_turn_id=local_turn_id,
            logical_group_id=logical_group_id,
            continuation_of_call_id=continuation_call_id,
        )

        response_call_ids: set[str] = set()
        for item_id, item in tuple(self._items.items()):
            if item.provider_response_id != provider_response_id:
                continue
            updated = replace(
                item,
                local_turn_id=local_turn_id,
                logical_group_id=logical_group_id,
                dependency_call_id=(
                    continuation_call_id
                    if item.item_type == "assistant_output"
                    else item.dependency_call_id
                ),
            )
            self._items[item_id] = updated
            if updated.function_call_id is not None:
                response_call_ids.add(updated.function_call_id)

        if response_call_ids:
            for item_id, item in tuple(self._items.items()):
                if item.dependency_call_id not in response_call_ids:
                    continue
                self._items[item_id] = replace(
                    item,
                    local_turn_id=local_turn_id,
                    logical_group_id=logical_group_id,
                )

    def expect_item_owner(
        self,
        *,
        provider_item_id: str,
        local_turn_id: str,
        dependency_call_id: str | None = None,
    ) -> None:
        """Bind an explicit item owner before or after provider observation."""

        if self._closed:
            return
        existing = self._items.get(provider_item_id)
        if existing is not None:
            if existing.local_turn_id not in {None, local_turn_id}:
                self._provider_item_observation_complete = False
                return
            self._items[provider_item_id] = replace(
                existing,
                local_turn_id=local_turn_id,
                logical_group_id=_logical_group_id(local_turn_id),
                dependency_call_id=(dependency_call_id or existing.dependency_call_id),
                input_transcription_usage=_assign_transcription_usage_owner(
                    existing.input_transcription_usage,
                    local_turn_id=local_turn_id,
                ),
            )
            if (
                self._latest_input_transcription_usage is not None
                and self._latest_input_transcription_usage.provider_item_id
                == provider_item_id
            ):
                self._latest_input_transcription_usage = replace(
                    self._latest_input_transcription_usage,
                    local_turn_id=local_turn_id,
                )
            self._pending_item_owners.pop(provider_item_id, None)
            return
        pending_owner = self._pending_item_owners.get(provider_item_id)
        if pending_owner is not None:
            if pending_owner != (local_turn_id, dependency_call_id):
                self._provider_item_observation_complete = False
            return
        if len(self._pending_item_owners) >= self._max_pending_item_admissions:
            self._provider_item_observation_complete = False
            return
        self._pending_item_owners[provider_item_id] = (
            local_turn_id,
            dependency_call_id,
        )

    def expect_continuation(self, *, local_turn_id: str, function_call_id: str) -> None:
        if self._closed or not local_turn_id.strip() or not function_call_id.strip():
            return
        key = (local_turn_id, function_call_id)
        if key in self._pending_continuations:
            return
        if len(self._pending_continuations) >= self._max_pending_continuations:
            self._response_observation_complete = False
            return
        self._pending_continuations[key] = None

    def settle_turn(self, *, local_turn_id: str) -> None:
        """Release unresolved observational expectations for a settled turn."""

        for key in tuple(self._pending_continuations):
            if key[0] == local_turn_id:
                self._pending_continuations.pop(key, None)

    def observe_usage(
        self,
        *,
        provider_response_id: str,
        local_turn_id: str,
        input_tokens: int,
        cached_input_tokens: int | None,
        output_tokens: int,
        total_tokens: int,
    ) -> None:
        if self._closed:
            return
        uncached_input_tokens = (
            input_tokens - cached_input_tokens
            if cached_input_tokens is not None and cached_input_tokens <= input_tokens
            else None
        )
        usage = ProviderUsageObservation(
            provider_response_id=provider_response_id,
            local_turn_id=local_turn_id,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            uncached_input_tokens=uncached_input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )
        response = self._responses.get(provider_response_id)
        if response is None:
            self._response_observation_complete = False
        else:
            if response.local_turn_id != local_turn_id:
                self._response_observation_complete = False
                return
            if response.usage is not None:
                if response.usage != usage:
                    self._response_observation_complete = False
                return
            self._responses[provider_response_id] = replace(response, usage=usage)
        self._latest_usage = usage
        self._cumulative_input_tokens += input_tokens
        self._cumulative_output_tokens += output_tokens
        self._cumulative_total_tokens += total_tokens
        self._usage_count += 1
        if cached_input_tokens is None or uncached_input_tokens is None:
            self._all_usage_has_cached_detail = False
            self._cumulative_cached_input_tokens = None
            self._cumulative_uncached_input_tokens = None
        elif self._all_usage_has_cached_detail:
            self._cumulative_cached_input_tokens = (
                self._cumulative_cached_input_tokens or 0
            ) + cached_input_tokens
            self._cumulative_uncached_input_tokens = (
                self._cumulative_uncached_input_tokens or 0
            ) + uncached_input_tokens

    def health_snapshot(self) -> SessionHealthSnapshot:
        elapsed = max(0.0, self._monotonic_clock() - self._started_at)
        rate_limits = tuple(self._rate_limits_by_name.values())
        tpm = self._rate_limits_by_name.get("tokens")
        rpm = self._rate_limits_by_name.get("requests")
        return SessionHealthSnapshot(
            session_id=self._session_id,
            provider_call_id=self._provider_call_id,
            created_at=self._created_at,
            uptime_seconds=elapsed,
            closed=self._closed,
            observed_provider_item_count=len(self._items),
            provider_item_observation_complete=(
                self._provider_item_observation_complete
            ),
            provider_inventory_authoritative=False,
            observability_degraded=self._observability_degraded,
            response_count=self._response_count,
            response_count_complete=self._response_observation_complete,
            function_call_count=self._function_call_count,
            continuation_count=self._continuation_count,
            pending_response_count=sum(
                not response.terminal_observed for response in self._responses.values()
            ),
            latest_usage=self._latest_usage,
            latest_input_transcription_usage=(self._latest_input_transcription_usage),
            cumulative_input_tokens=self._cumulative_input_tokens,
            cumulative_output_tokens=self._cumulative_output_tokens,
            cumulative_total_tokens=self._cumulative_total_tokens,
            cumulative_cached_input_tokens=(
                self._cumulative_cached_input_tokens
                if self._usage_count and self._all_usage_has_cached_detail
                else None
            ),
            cumulative_uncached_input_tokens=(
                self._cumulative_uncached_input_tokens
                if self._usage_count and self._all_usage_has_cached_detail
                else None
            ),
            rate_limits=rate_limits,
            rate_limit_snapshot_truncated=self._rate_limit_snapshot_truncated,
            tpm=tpm,
            rpm=rpm,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._items.clear()
        self._responses.clear()
        self._call_item_ids.clear()
        self._pending_continuations.clear()
        self._pending_item_owners.clear()

    def _observe_response(
        self,
        provider_response_id: str,
        *,
        observed_sequence: int,
    ) -> ProviderResponseObservation | None:
        existing = self._responses.get(provider_response_id)
        if existing is not None:
            return existing
        if len(self._responses) >= self._max_responses:
            self._response_observation_complete = False
            return None
        response = ProviderResponseObservation(
            provider_response_id=provider_response_id,
            local_turn_id=None,
            observed_sequence=observed_sequence,
            logical_group_id=None,
        )
        self._responses[provider_response_id] = response
        self._response_count += 1
        return response

    def _observe_item(
        self,
        item: Mapping[str, object],
        *,
        provider_response_id: str | None,
        observed_sequence: int,
    ) -> None:
        provider_item_id = _string(item.get("id"))
        if provider_item_id is None:
            self._provider_item_observation_complete = False
            return
        existing = self._items.get(provider_item_id)
        raw_type = _string(item.get("type")) or "unknown"
        role = _string(item.get("role"))
        item_type = _item_type(raw_type, role=role)
        function_call_id = _string(item.get("call_id"))

        response = (
            self._responses.get(provider_response_id)
            if provider_response_id is not None
            else None
        )
        local_turn_id = response.local_turn_id if response is not None else None
        logical_group_id = response.logical_group_id if response is not None else None
        dependency_call_id = None
        if item_type == "function_call_output" and function_call_id is not None:
            dependency_call_id = function_call_id
            call_item_id = self._call_item_ids.get(function_call_id)
            call_item = (
                self._items.get(call_item_id) if call_item_id is not None else None
            )
            if call_item is not None:
                local_turn_id = call_item.local_turn_id
                logical_group_id = call_item.logical_group_id
        expected_owner = self._pending_item_owners.pop(provider_item_id, None)
        if expected_owner is not None:
            local_turn_id = expected_owner[0]
            logical_group_id = _logical_group_id(expected_owner[0])
            dependency_call_id = expected_owner[1] or dependency_call_id

        if existing is not None:
            if (
                provider_response_id is not None
                and existing.provider_response_id is not None
                and provider_response_id != existing.provider_response_id
            ):
                self._provider_item_observation_complete = False
                provider_response_id = existing.provider_response_id
            if (
                local_turn_id is not None
                and existing.local_turn_id is not None
                and local_turn_id != existing.local_turn_id
            ):
                self._provider_item_observation_complete = False
                local_turn_id = existing.local_turn_id
                logical_group_id = existing.logical_group_id
            if (
                function_call_id is not None
                and existing.function_call_id is not None
                and function_call_id != existing.function_call_id
            ):
                self._provider_item_observation_complete = False
                function_call_id = existing.function_call_id
            self._items[provider_item_id] = replace(
                existing,
                provider_response_id=(
                    provider_response_id or existing.provider_response_id
                ),
                local_turn_id=local_turn_id or existing.local_turn_id,
                logical_group_id=logical_group_id or existing.logical_group_id,
                function_call_id=function_call_id or existing.function_call_id,
                dependency_call_id=(dependency_call_id or existing.dependency_call_id),
            )
            return
        if len(self._items) >= self._max_items:
            self._provider_item_observation_complete = False
            return

        observed = ProviderConversationItem(
            provider_item_id=provider_item_id,
            provider_response_id=provider_response_id,
            local_turn_id=local_turn_id,
            item_type=item_type,
            observed_sequence=observed_sequence,
            logical_group_id=logical_group_id,
            function_call_id=function_call_id,
            dependency_call_id=dependency_call_id,
        )
        self._items[provider_item_id] = observed
        if item_type == "function_call" and function_call_id is not None:
            self._call_item_ids[function_call_id] = provider_item_id
            self._function_call_count += 1

    def _observe_input_transcription_usage(
        self,
        event: Mapping[str, object],
        *,
        observed_sequence: int,
    ) -> None:
        provider_item_id = _string(event.get("item_id"))
        if provider_item_id is None:
            return
        item = self._items.get(provider_item_id)
        if item is None:
            # A transcription event is itself an explicit observation of its
            # provider input item identity, even if committed/created has not
            # reached the sideband observer yet.
            self._observe_item(
                {"id": provider_item_id, "type": "message", "role": "user"},
                provider_response_id=None,
                observed_sequence=observed_sequence,
            )
            item = self._items.get(provider_item_id)
        usage = _mapping(event.get("usage"))
        if usage is None:
            return
        usage_type = _string(usage.get("type"))
        if usage_type not in {"tokens", "duration"}:
            return
        input_tokens: int | None = None
        output_tokens: int | None = None
        total_tokens: int | None = None
        input_details: Mapping[str, object] | None = None
        duration_seconds: float | None = None
        if usage_type == "tokens":
            input_tokens = _non_negative_int(usage.get("input_tokens"))
            output_tokens = _non_negative_int(usage.get("output_tokens"))
            total_tokens = _non_negative_int(usage.get("total_tokens"))
            if input_tokens is None or output_tokens is None or total_tokens is None:
                return
            input_details = _mapping(usage.get("input_token_details"))
        else:
            duration_seconds = _non_negative_number(usage.get("seconds"))
            if duration_seconds is None:
                return
        observation = ProviderInputTranscriptionUsageObservation(
            provider_item_id=provider_item_id,
            local_turn_id=item.local_turn_id if item is not None else None,
            observed_sequence=observed_sequence,
            usage_type=usage_type,
            input_tokens=input_tokens,
            input_text_tokens=(
                _non_negative_int(input_details.get("text_tokens"))
                if input_details is not None
                else None
            ),
            input_audio_tokens=(
                _non_negative_int(input_details.get("audio_tokens"))
                if input_details is not None
                else None
            ),
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            duration_seconds=duration_seconds,
        )
        self._latest_input_transcription_usage = observation
        if item is not None:
            self._items[provider_item_id] = replace(
                item,
                input_transcription_usage=observation,
            )

    def _observe_rate_limits(
        self,
        event: Mapping[str, object],
        *,
        observed_sequence: int,
    ) -> None:
        raw_entries = event.get("rate_limits")
        if not isinstance(raw_entries, Sequence) or isinstance(
            raw_entries, (str, bytes)
        ):
            return
        observed_at = self._now()
        truncated = len(raw_entries) > self._max_rate_limit_entries
        for value in raw_entries[: self._max_rate_limit_entries]:
            entry = _mapping(value)
            if entry is None:
                continue
            name = _string(entry.get("name"))
            if name is None:
                continue
            observation = ProviderRateLimitObservation(
                name=name,
                limit=_non_negative_int(entry.get("limit")),
                remaining=_non_negative_int(entry.get("remaining")),
                reset_seconds=_non_negative_number(entry.get("reset_seconds")),
                observed_at=observed_at,
                observed_sequence=observed_sequence,
            )
            if name in self._rate_limits_by_name:
                self._rate_limits_by_name[name] = observation
                continue
            if len(self._rate_limits_by_name) >= self._max_rate_limit_entries:
                truncated = True
                continue
            self._rate_limits_by_name[name] = observation
        self._rate_limit_snapshot_truncated = (
            self._rate_limit_snapshot_truncated or truncated
        )


def _response_id(event: Mapping[str, object]) -> str | None:
    direct = _string(event.get("response_id"))
    if direct is not None:
        return direct
    response = _mapping(event.get("response"))
    return _string(response.get("id")) if response is not None else None


def _logical_group_id(local_turn_id: str) -> str:
    return f"turn:{local_turn_id}"


def _assign_transcription_usage_owner(
    usage: ProviderInputTranscriptionUsageObservation | None,
    *,
    local_turn_id: str,
) -> ProviderInputTranscriptionUsageObservation | None:
    return replace(usage, local_turn_id=local_turn_id) if usage is not None else None


def _item_type(raw_type: str, *, role: str | None) -> str:
    if raw_type == "function_call":
        return "function_call"
    if raw_type == "function_call_output":
        return "function_call_output"
    if raw_type == "message" and role == "user":
        return "child_input"
    if raw_type == "message" and role == "assistant":
        return "assistant_output"
    return raw_type


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _non_negative_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    try:
        normalized = float(value)
    except (OverflowError, ValueError):
        return None
    return normalized if isfinite(normalized) else None
