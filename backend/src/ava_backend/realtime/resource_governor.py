from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic
from typing import Callable


class ResourceGovernorState(StrEnum):
    HEALTHY = "healthy"
    PRESSURE = "pressure"
    DEFERRED = "deferred"
    RATE_LIMITED = "rateLimited"
    RECOVERING = "recovering"
    PROVIDER_UNAVAILABLE = "providerUnavailable"


class ResourceAdmissionAction(StrEnum):
    ALLOW = "allow"
    DEFER = "defer"


class ResourceAdmissionReason(StrEnum):
    SUFFICIENT_CAPACITY = "sufficientCapacity"
    MISSING_RATE_LIMIT_SNAPSHOT = "missingRateLimitSnapshot"
    UNKNOWN_BOOTSTRAP_INPUT = "unknownBootstrapInput"
    TPM_CAPACITY = "tpmCapacity"
    RPM_CAPACITY = "rpmCapacity"
    TPM_AND_RPM_CAPACITY = "tpmAndRpmCapacity"
    RESERVATION_CAPACITY = "reservationCapacity"
    RECOVERY_PROBE = "recoveryProbe"


@dataclass(frozen=True, slots=True)
class RateLimitWindow:
    """Latest provider snapshot for one independently governed limit."""

    limit: int | None
    remaining: int | None
    reset_seconds: float | None
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class LogicalTurnEstimate:
    estimated_tokens: int | None
    estimated_responses: int
    output_token_limits: tuple[int, ...]
    input_growth_token_limits: tuple[int, ...]
    initial_input_growth_tokens: int
    calibrated_input_tokens: int | None
    calibrated_output_tokens: int | None


@dataclass(frozen=True, slots=True)
class ResourceAdmissionDecision:
    action: ResourceAdmissionAction
    reason: ResourceAdmissionReason
    state: ResourceGovernorState
    estimate: LogicalTurnEstimate
    wait_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ResourceGovernorSnapshot:
    state: ResourceGovernorState
    admission_decision: ResourceAdmissionAction | None
    admission_reason: ResourceAdmissionReason | None
    provider_tpm_remaining: int | None
    provider_rpm_remaining: int | None
    local_reserved_tokens: int
    local_reserved_requests: int
    estimated_logical_turn_tokens: int | None
    estimated_logical_turn_responses: int | None
    deferred_turn_id: str | None
    recovery_attempt: int
    recovery_deadline_seconds_remaining: float | None
    rate_limit_recovery_count: int
    resource_deferral_count: int


@dataclass(slots=True)
class _TurnReservation:
    turn_id: str
    tokens: int
    requests: int
    estimate: LogicalTurnEstimate
    provider_response_id: str | None = None


class ResourceGovernor:
    """Session-owned resource mechanics for one serialized Realtime channel.

    Provider rate-limit observations are deliberately supplied to each decision;
    the governor does not create a second provider snapshot store. Its only
    retained capacity state is the AVA-owned reservation for the channel's one
    active logical turn plus bounded calibration/recovery counters.
    """

    def __init__(
        self,
        *,
        max_recovery_attempts: int,
        fallback_wait_seconds: float,
        monotonic_clock: Callable[[], float] = monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not 1 <= max_recovery_attempts <= 10:
            raise ValueError("max_recovery_attempts must be between 1 and 10")
        if not 0.05 <= fallback_wait_seconds <= 30:
            raise ValueError("fallback_wait_seconds must be between 0.05 and 30")
        self._max_recovery_attempts = max_recovery_attempts
        self._fallback_wait_seconds = float(fallback_wait_seconds)
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._reservation: _TurnReservation | None = None
        self._capture_reservation: _TurnReservation | None = None
        self._latest_actual_input_tokens: int | None = None
        self._latest_actual_output_tokens: int | None = None
        self._state = ResourceGovernorState.PRESSURE
        self._last_decision: ResourceAdmissionDecision | None = None
        self._deferred_turn_id: str | None = None
        self._deferred_episode: tuple[str, ResourceAdmissionReason] | None = None
        self._recovery_turn_id: str | None = None
        self._recovery_attempt = 0
        self._recovery_started_at: float | None = None
        self._resource_deferral_count = 0
        self._rate_limit_recovery_count = 0
        self._closed = False

    @property
    def latest_actual_input_tokens(self) -> int | None:
        return self._latest_actual_input_tokens

    @property
    def latest_actual_output_tokens(self) -> int | None:
        return self._latest_actual_output_tokens

    def estimate(
        self,
        *,
        output_token_limits: tuple[int, ...],
        input_growth_token_limits: tuple[int, ...] = (),
        initial_input_growth_tokens: int = 0,
    ) -> LogicalTurnEstimate:
        if len(output_token_limits) != 1:
            raise ValueError("resource admission must reserve exactly one response")
        if any(limit < 1 for limit in output_token_limits):
            raise ValueError("response output token limits must be positive")
        if input_growth_token_limits:
            raise ValueError("concrete response admission cannot reserve future input growth")
        if initial_input_growth_tokens < 0 or any(
            growth < 0 for growth in input_growth_token_limits
        ):
            raise ValueError("input growth token bounds cannot be negative")
        calibrated_input = self._latest_actual_input_tokens
        calibrated_output = self._latest_actual_output_tokens
        estimated_tokens: int | None = None
        if calibrated_input is not None:
            # A new response sees the latest measured provider input, the
            # preceding actual model output, and any AVA-created function input.
            # Later mechanically possible responses add their bounded model and
            # function-output growth. Ava uses UTF-8 bytes as a conservative
            # no-tokenizer token upper bound for AVA-created JSON.
            estimated_input = (
                calibrated_input
                + (calibrated_output or 0)
                + initial_input_growth_tokens
            )
            estimated_tokens = estimated_input + output_token_limits[0]
        return LogicalTurnEstimate(
            estimated_tokens=estimated_tokens,
            estimated_responses=len(output_token_limits),
            output_token_limits=output_token_limits,
            input_growth_token_limits=input_growth_token_limits,
            initial_input_growth_tokens=initial_input_growth_tokens,
            calibrated_input_tokens=calibrated_input,
            calibrated_output_tokens=calibrated_output,
        )

    def admit(
        self,
        *,
        turn_id: str,
        output_token_limits: tuple[int, ...],
        input_growth_token_limits: tuple[int, ...] = (),
        initial_input_growth_tokens: int = 0,
        tpm: RateLimitWindow | None,
        rpm: RateLimitWindow | None,
        recovery_probe: bool = False,
    ) -> ResourceAdmissionDecision:
        self._require_open_turn_id(turn_id)
        if (
            self._state is ResourceGovernorState.PROVIDER_UNAVAILABLE
            and self._recovery_turn_id is not None
            and self._recovery_turn_id != turn_id
        ):
            # Provider-unavailable is the bounded outcome of one logical turn,
            # not a permanent poison pill for the persistent session.
            self._clear_recovery()
            self._state = ResourceGovernorState.PRESSURE
        estimate = self.estimate(
            output_token_limits=output_token_limits,
            input_growth_token_limits=input_growth_token_limits,
            initial_input_growth_tokens=initial_input_growth_tokens,
        )
        is_capture = turn_id.startswith("capture_")
        reservation = self._capture_reservation if is_capture else self._reservation
        if reservation is not None:
            if reservation.turn_id != turn_id:
                decision = self._decision(
                    action=ResourceAdmissionAction.DEFER,
                    reason=ResourceAdmissionReason.RESERVATION_CAPACITY,
                    state=ResourceGovernorState.DEFERRED,
                    estimate=estimate,
                    wait_seconds=self._fallback_wait_seconds,
                )
                self._note_deferred(turn_id=turn_id, reason=decision.reason)
                return decision
            # A completed concrete response reconciles this same-turn reservation
            # to zero. Rebase it atomically only when the next actual response
            # exists; retries re-admit the same concrete estimate in place.
            reservation.tokens = estimate.estimated_tokens or 0
            reservation.requests = estimate.estimated_responses
            reservation.estimate = estimate

        token_remaining = self._effective_remaining(tpm)
        request_remaining = self._effective_remaining(rpm)
        other_reservation = self._reservation if is_capture else self._capture_reservation
        if other_reservation is not None:
            if token_remaining is not None:
                token_remaining = max(0, token_remaining - other_reservation.tokens)
            if request_remaining is not None:
                request_remaining = max(0, request_remaining - other_reservation.requests)
        token_insufficient = token_remaining is not None and (
            token_remaining <= 0
            if estimate.estimated_tokens is None
            else token_remaining < estimate.estimated_tokens
        )
        request_insufficient = (
            request_remaining is not None
            and request_remaining < estimate.estimated_responses
        )
        if (token_insufficient or request_insufficient) and not recovery_probe:
            if token_insufficient and request_insufficient:
                reason = ResourceAdmissionReason.TPM_AND_RPM_CAPACITY
            elif token_insufficient:
                reason = ResourceAdmissionReason.TPM_CAPACITY
            else:
                reason = ResourceAdmissionReason.RPM_CAPACITY
            decision = self._decision(
                action=ResourceAdmissionAction.DEFER,
                reason=reason,
                state=ResourceGovernorState.DEFERRED,
                estimate=estimate,
                wait_seconds=self._capacity_wait_seconds(
                    tpm=tpm if token_insufficient else None,
                    rpm=rpm if request_insufficient else None,
                ),
            )
            self._note_deferred(turn_id=turn_id, reason=reason)
            return decision

        if recovery_probe:
            state = ResourceGovernorState.RECOVERING
            reason = ResourceAdmissionReason.RECOVERY_PROBE
        elif (
            tpm is None or tpm.remaining is None or rpm is None or rpm.remaining is None
        ):
            state = ResourceGovernorState.PRESSURE
            reason = ResourceAdmissionReason.MISSING_RATE_LIMIT_SNAPSHOT
        elif estimate.estimated_tokens is None:
            state = ResourceGovernorState.PRESSURE
            reason = ResourceAdmissionReason.UNKNOWN_BOOTSTRAP_INPUT
        else:
            state = ResourceGovernorState.HEALTHY
            reason = ResourceAdmissionReason.SUFFICIENT_CAPACITY

        # Unknown bootstrap input remains unknown in telemetry. When the
        # provider has supplied a token snapshot, reserving all currently
        # available tokens prevents another logical turn from pretending that
        # the same opaque headroom is independently available.
        reserved_tokens = estimate.estimated_tokens
        if reserved_tokens is None:
            reserved_tokens = max(0, token_remaining or 0)
        if reservation is None:
            new_reservation = _TurnReservation(
                turn_id=turn_id,
                tokens=reserved_tokens,
                requests=estimate.estimated_responses,
                estimate=estimate,
            )
            if is_capture:
                self._capture_reservation = new_reservation
            else:
                self._reservation = new_reservation
        else:
            reservation.tokens = reserved_tokens
            reservation.requests = estimate.estimated_responses
            reservation.estimate = estimate
        self._deferred_turn_id = None
        self._deferred_episode = None
        return self._decision(
            action=ResourceAdmissionAction.ALLOW,
            reason=reason,
            state=state,
            estimate=estimate,
        )

    def bind_provider_response(
        self, *, turn_id: str, provider_response_id: str
    ) -> None:
        self._require_open_turn_id(turn_id)
        if not provider_response_id.strip():
            raise ValueError("provider_response_id cannot be empty")
        reservation = (
            self._capture_reservation
            if turn_id.startswith("capture_")
            else self._reservation
        )
        if reservation is None or reservation.turn_id != turn_id:
            raise ValueError("provider response has no matching resource reservation")
        if (
            reservation.provider_response_id is not None
            and reservation.provider_response_id != provider_response_id
        ):
            raise ValueError("resource reservation changed provider response identity")
        reservation.provider_response_id = provider_response_id

    def observe_actual_usage(
        self, *, turn_id: str, input_tokens: int, output_tokens: int
    ) -> None:
        self._require_open_turn_id(turn_id)
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("actual token usage cannot be negative")
        self._latest_actual_input_tokens = input_tokens
        self._latest_actual_output_tokens = output_tokens

    def reconcile_response(
        self,
        *,
        turn_id: str,
        provider_response_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        reservation = (
            self._capture_reservation
            if turn_id.startswith("capture_")
            else self._reservation
        )
        if reservation is None or reservation.turn_id != turn_id:
            raise ValueError("response reconciliation has no matching reservation")
        if reservation.provider_response_id != provider_response_id:
            raise ValueError(
                "response reconciliation changed provider response identity"
            )
        if reservation.requests < 1:
            raise ValueError("resource reservation has no response left to reconcile")
        prior_estimate = reservation.estimate
        self.observe_actual_usage(
            turn_id=turn_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        remaining_outputs = prior_estimate.output_token_limits[1:]
        remaining_growth = prior_estimate.input_growth_token_limits[1:]
        if remaining_outputs:
            rebased = self.estimate(
                output_token_limits=remaining_outputs,
                input_growth_token_limits=remaining_growth,
                initial_input_growth_tokens=(
                    prior_estimate.input_growth_token_limits[0]
                ),
            )
            reservation.tokens = rebased.estimated_tokens or 0
            reservation.requests = rebased.estimated_responses
            reservation.estimate = rebased
        else:
            reservation.tokens = 0
            reservation.requests = 0
        reservation.provider_response_id = None
        if self._recovery_turn_id == turn_id:
            self._rate_limit_recovery_count += 1
            self._clear_recovery()
        self._state = ResourceGovernorState.HEALTHY

    def release_turn(self, *, turn_id: str) -> bool:
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        is_capture = turn_id.startswith("capture_")
        if is_capture:
            released = (
                self._capture_reservation is not None
                and self._capture_reservation.turn_id == turn_id
            )
            if released:
                self._capture_reservation = None
        else:
            released = (
                self._reservation is not None and self._reservation.turn_id == turn_id
            )
            if released:
                self._reservation = None
        if self._deferred_turn_id == turn_id:
            self._deferred_turn_id = None
            self._deferred_episode = None
        if self._recovery_turn_id == turn_id:
            self._clear_recovery()
        if (
            not self._closed
            and self._state != ResourceGovernorState.PROVIDER_UNAVAILABLE
        ):
            self._state = ResourceGovernorState.PRESSURE
        return released

    def mark_rate_limited(self, *, turn_id: str) -> bool:
        self._require_open_turn_id(turn_id)
        is_capture = turn_id.startswith("capture_")
        reservation = (
            self._capture_reservation
            if is_capture
            else self._reservation
        )
        if reservation is None or reservation.turn_id != turn_id:
            raise ValueError("rate-limited turn has no matching resource reservation")
        # The rejected provider response is terminal. The same logical-turn
        # reservation is retained for a response-generation retry, whose
        # provider response ID will necessarily be new.
        reservation.provider_response_id = None
        now = self._monotonic_clock()
        if self._recovery_turn_id != turn_id:
            self._recovery_turn_id = turn_id
            self._recovery_attempt = 0
            self._recovery_started_at = now
        self._recovery_attempt += 1
        started = self._recovery_started_at
        assert started is not None
        within_attempts = self._recovery_attempt <= self._max_recovery_attempts
        if not within_attempts:
            if is_capture:
                self._capture_reservation = None
            else:
                self._reservation = None
            self._deferred_turn_id = None
            self._deferred_episode = None
            self._state = ResourceGovernorState.PROVIDER_UNAVAILABLE
            return False
        self._deferred_turn_id = turn_id
        self._state = ResourceGovernorState.RATE_LIMITED
        return True

    def begin_recovery(self, *, turn_id: str) -> None:
        self._require_open_turn_id(turn_id)
        if self._recovery_turn_id != turn_id:
            raise ValueError("recovery changed local turn ownership")
        self._state = ResourceGovernorState.RECOVERING
        self._deferred_turn_id = None

    def recovery_wait_seconds(
        self,
        *,
        limit_kind: str | None,
        tpm: RateLimitWindow | None,
        rpm: RateLimitWindow | None,
    ) -> float:
        if limit_kind == "tokens":
            provider_wait = self._capacity_wait_seconds(tpm=tpm, rpm=None)
        elif limit_kind == "requests":
            provider_wait = self._capacity_wait_seconds(tpm=None, rpm=rpm)
        else:
            provider_wait = self._capacity_wait_seconds(tpm=tpm, rpm=rpm)
        started = self._recovery_started_at
        if started is None:
            raise ValueError("recovery wait requested outside a recovery lifecycle")
        return provider_wait

    def snapshot(
        self,
        *,
        tpm: RateLimitWindow | None,
        rpm: RateLimitWindow | None,
    ) -> ResourceGovernorSnapshot:
        total_tokens = 0
        total_requests = 0
        for res in (self._reservation, self._capture_reservation):
            if res is not None:
                total_tokens += res.tokens
                total_requests += res.requests
        estimate = self._last_decision.estimate if self._last_decision else None
        return ResourceGovernorSnapshot(
            state=self._state,
            admission_decision=(
                self._last_decision.action if self._last_decision else None
            ),
            admission_reason=(
                self._last_decision.reason if self._last_decision else None
            ),
            provider_tpm_remaining=tpm.remaining if tpm is not None else None,
            provider_rpm_remaining=rpm.remaining if rpm is not None else None,
            local_reserved_tokens=total_tokens,
            local_reserved_requests=total_requests,
            estimated_logical_turn_tokens=(
                estimate.estimated_tokens if estimate is not None else None
            ),
            estimated_logical_turn_responses=(
                estimate.estimated_responses if estimate is not None else None
            ),
            deferred_turn_id=self._deferred_turn_id,
            recovery_attempt=self._recovery_attempt,
            recovery_deadline_seconds_remaining=None,
            rate_limit_recovery_count=self._rate_limit_recovery_count,
            resource_deferral_count=self._resource_deferral_count,
        )

    def close(self) -> None:
        self._closed = True
        self._reservation = None
        self._capture_reservation = None
        self._deferred_turn_id = None
        self._deferred_episode = None
        self._clear_recovery()

    def _decision(
        self,
        *,
        action: ResourceAdmissionAction,
        reason: ResourceAdmissionReason,
        state: ResourceGovernorState,
        estimate: LogicalTurnEstimate,
        wait_seconds: float | None = None,
    ) -> ResourceAdmissionDecision:
        self._state = state
        decision = ResourceAdmissionDecision(
            action=action,
            reason=reason,
            state=state,
            estimate=estimate,
            wait_seconds=wait_seconds,
        )
        self._last_decision = decision
        return decision

    def _note_deferred(self, *, turn_id: str, reason: ResourceAdmissionReason) -> None:
        episode = (turn_id, reason)
        if self._deferred_episode != episode:
            self._resource_deferral_count += 1
            self._deferred_episode = episode
        self._deferred_turn_id = turn_id

    def _effective_remaining(self, window: RateLimitWindow | None) -> int | None:
        if window is None or window.remaining is None:
            return None
        observed_at = window.observed_at
        if observed_at.tzinfo is None:
            raise ValueError("rate-limit observation timestamp must be timezone-aware")
        if window.reset_seconds is None or window.limit is None:
            return window.remaining
        elapsed = max(0.0, (self._wall_clock() - observed_at).total_seconds())
        if elapsed >= window.reset_seconds:
            return window.limit
        return window.remaining

    def _capacity_wait_seconds(
        self,
        *,
        tpm: RateLimitWindow | None,
        rpm: RateLimitWindow | None,
    ) -> float:
        waits = [
            remaining
            for window in (tpm, rpm)
            if window is not None
            and (remaining := self._remaining_reset_seconds(window)) is not None
        ]
        return max(waits, default=self._fallback_wait_seconds)

    def _remaining_reset_seconds(self, window: RateLimitWindow) -> float | None:
        if window.reset_seconds is None:
            return None
        if window.observed_at.tzinfo is None:
            raise ValueError("rate-limit observation timestamp must be timezone-aware")
        elapsed = max(0.0, (self._wall_clock() - window.observed_at).total_seconds())
        return max(0.0, window.reset_seconds - elapsed)

    def _clear_recovery(self) -> None:
        self._recovery_turn_id = None
        self._recovery_attempt = 0
        self._recovery_started_at = None

    def _require_open_turn_id(self, turn_id: str) -> None:
        if self._closed:
            raise ValueError("resource governor is closed")
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
