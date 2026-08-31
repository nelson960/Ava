from __future__ import annotations

import asyncio
import hashlib
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Protocol

from pydantic import Field, StringConstraints, model_validator

from ..protocol import AgeBand
from .common import NonEmptyText, ProtocolIdentifier, RealtimeProtocolModel
from .tool_contract import InteractiveCanvasMode
from .timezone import MAX_TIMEZONE_ID_CHARACTERS, validate_iana_timezone_id

MAX_SNAPSHOT_DELIVERED_TURNS = 8
RAW_RETENTION_HIGH_WATER = 256
RAW_RETENTION_LOW_WATER = 128
MAX_EPISODE_SOURCE_EVENTS = 32
MAX_EPISODES = 128
EPISODE_RETENTION_DAYS = 180
MAX_STABLE_MEMORIES = 12
MAX_WEEKLY_RECORDS = 64
MAX_SESSION_SUMMARY_CHARACTERS = 1_200
MAX_CHILD_TURN_SUMMARY_CHARACTERS = 240
MINIMUM_STABLE_MEMORY_CONFIDENCE = 0.8


class MemoryResetRevisionMismatchError(RuntimeError):
    """A session attempted to write memory after its reset epoch ended."""

    def __init__(
        self,
        *,
        expected_reset_revision: int,
        current_reset_revision: int,
    ) -> None:
        super().__init__("stale-session memory write rejected after conversation reset")
        self.expected_reset_revision = expected_reset_revision
        self.current_reset_revision = current_reset_revision


class MemoryProposalKind(StrEnum):
    INTEREST = "interest"
    PREFERENCE = "preference"


class AvaMemoryProposal(RealtimeProtocolModel):
    kind: MemoryProposalKind
    summary: Annotated[NonEmptyText, StringConstraints(max_length=160)]
    evidence: Annotated[NonEmptyText, StringConstraints(max_length=160)]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class DeliveredTurnTerminal(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ChildTurnTextSource(StrEnum):
    TRANSCRIPT = "transcript"
    MODEL_SUMMARY = "modelSummary"


class DeliveredInputKind(StrEnum):
    SPEECH = "speech"
    CANVAS_INTERACTION = "canvasInteraction"


class DeliveredCanvasInteraction(RealtimeProtocolModel):
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    interaction_revision: int = Field(ge=1, le=2_147_483_647)
    interaction_mode: InteractiveCanvasMode
    binding_id: ProtocolIdentifier
    target_id: ProtocolIdentifier
    accessibility_label: Annotated[
        NonEmptyText,
        StringConstraints(max_length=160),
    ]
    step_index: int | None = Field(default=None, ge=1, le=12)

    @model_validator(mode="after")
    def mode_matches_step_identity(self) -> DeliveredCanvasInteraction:
        if self.interaction_mode == InteractiveCanvasMode.NONE:
            raise ValueError("a static canvas cannot produce an interaction input")
        if (self.interaction_mode == InteractiveCanvasMode.STEPS) != (
            self.step_index is not None
        ):
            raise ValueError("steps interaction input requires an exact stepIndex")
        return self


class DeliveredConversationTurn(RealtimeProtocolModel):
    """Authoritative turn content derived from rendered playback.

    ``ava_delivered_text`` is the alignment-derived prefix the child actually
    heard. Generated but unplayed text is never accepted by this boundary.
    """

    turn_id: ProtocolIdentifier
    input_kind: DeliveredInputKind = DeliveredInputKind.SPEECH
    child_text: Annotated[NonEmptyText, StringConstraints(max_length=2_000)] | None = (
        None
    )
    child_text_source: ChildTurnTextSource | None = ChildTurnTextSource.TRANSCRIPT
    canvas_interaction: DeliveredCanvasInteraction | None = None
    ava_delivered_text: Annotated[str, StringConstraints(max_length=1_800)]
    delivered_sample_count: int = Field(ge=0, le=2_147_483_647)
    child_turn_summary: Annotated[
        NonEmptyText,
        StringConstraints(max_length=MAX_CHILD_TURN_SUMMARY_CHARACTERS),
    ]
    terminal: DeliveredTurnTerminal

    @model_validator(mode="after")
    def delivered_samples_match_text(self) -> DeliveredConversationTurn:
        if self.input_kind == DeliveredInputKind.SPEECH:
            if self.child_text is None or self.child_text_source is None:
                raise ValueError("speech input requires childText and childTextSource")
            if self.canvas_interaction is not None:
                raise ValueError("speech input cannot contain a canvas interaction")
        else:
            if self.child_text is not None or self.child_text_source is not None:
                raise ValueError(
                    "canvas interaction input cannot contain child speech fields"
                )
            if self.canvas_interaction is None:
                raise ValueError(
                    "canvas interaction input requires typed interaction identity"
                )
        has_audio = self.delivered_sample_count > 0
        has_text = bool(self.ava_delivered_text)
        if has_text and not has_audio:
            raise ValueError(
                "avaDeliveredText requires a positive deliveredSampleCount"
            )
        return self


class ConversationEpisode(RealtimeProtocolModel):
    subject: Annotated[NonEmptyText, StringConstraints(max_length=256)]
    episode_id: ProtocolIdentifier
    started_at: datetime
    ended_at: datetime
    timezone_id: Annotated[
        NonEmptyText,
        StringConstraints(max_length=MAX_TIMEZONE_ID_CHARACTERS),
    ]
    digest: Annotated[NonEmptyText, StringConstraints(max_length=32_768)]
    source_turn_ids: list[ProtocolIdentifier] = Field(
        min_length=1,
        max_length=MAX_EPISODE_SOURCE_EVENTS,
    )
    source_event_count: int = Field(ge=1, le=MAX_EPISODE_SOURCE_EVENTS)
    created_at: datetime

    @model_validator(mode="after")
    def episode_is_coherent(self) -> ConversationEpisode:
        _validate_subject(self.subject)
        if self.started_at.tzinfo is None or self.ended_at.tzinfo is None:
            raise ValueError("episode bounds must be timezone-aware")
        if self.created_at.tzinfo is None:
            raise ValueError("episode createdAt must be timezone-aware")
        if self.ended_at < self.started_at:
            raise ValueError("episode endedAt cannot precede startedAt")
        if self.source_event_count != len(self.source_turn_ids):
            raise ValueError("episode sourceEventCount must match sourceTurnIds")
        if len(self.source_turn_ids) != len(set(self.source_turn_ids)):
            raise ValueError("episode source turn IDs must be unique")
        validate_iana_timezone_id(self.timezone_id)
        return self


class MemoryCandidateSourceKind(StrEnum):
    RAW_EVENT = "rawEvent"
    EPISODE = "episode"
    STABLE_MEMORY = "stableMemory"


class MemorySearchCandidate(RealtimeProtocolModel):
    source_id: Annotated[
        str,
        StringConstraints(
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z][A-Za-z0-9._:-]*$",
        ),
    ]
    source_kind: MemoryCandidateSourceKind
    started_at: datetime | None
    ended_at: datetime | None
    timezone_ids: list[
        Annotated[
            NonEmptyText,
            StringConstraints(max_length=MAX_TIMEZONE_ID_CHARACTERS),
        ]
    ] = Field(max_length=MAX_EPISODE_SOURCE_EVENTS)
    reference_text: Annotated[NonEmptyText, StringConstraints(max_length=1_200)]
    source_turn_ids: list[ProtocolIdentifier] = Field(
        max_length=MAX_EPISODE_SOURCE_EVENTS
    )
    relevance: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def candidate_is_coherent(self) -> MemorySearchCandidate:
        if (self.started_at is None) != (self.ended_at is None):
            raise ValueError("memory candidate time range must be complete")
        if self.started_at is not None:
            if self.started_at.tzinfo is None or self.ended_at is None:
                raise ValueError("memory candidate times must be timezone-aware")
            if self.ended_at.tzinfo is None or self.ended_at < self.started_at:
                raise ValueError("memory candidate time range is invalid")
        for timezone_id in self.timezone_ids:
            validate_iana_timezone_id(timezone_id)
        return self


class StableMemoryRecord(RealtimeProtocolModel):
    id: ProtocolIdentifier
    kind: MemoryProposalKind
    summary: Annotated[NonEmptyText, StringConstraints(max_length=160)]
    evidence: Annotated[NonEmptyText, StringConstraints(max_length=160)]
    source_turn_id: ProtocolIdentifier


class WeeklyConversationRecord(RealtimeProtocolModel):
    turn_id: ProtocolIdentifier
    summary: Annotated[NonEmptyText, StringConstraints(max_length=240)]
    occurred_at: datetime

    @model_validator(mode="after")
    def occurred_at_is_aware(self) -> WeeklyConversationRecord:
        if self.occurred_at.tzinfo is None:
            raise ValueError("weekly record occurredAt must be timezone-aware")
        return self


class WeeklyConversationRecap(RealtimeProtocolModel):
    period_start: date
    period_end: date
    generated_at: datetime
    reset_revision: int = Field(ge=0)
    records: list[WeeklyConversationRecord] = Field(max_length=MAX_WEEKLY_RECORDS)

    @model_validator(mode="after")
    def period_is_valid(self) -> WeeklyConversationRecap:
        if self.period_end != self.period_start + timedelta(days=7):
            raise ValueError("weekly recap period must span exactly seven days")
        if self.generated_at.tzinfo is None:
            raise ValueError("weekly recap generatedAt must be timezone-aware")
        return self


class ConversationResetReceipt(RealtimeProtocolModel):
    revision: int = Field(ge=1)
    reset_at: datetime

    @model_validator(mode="after")
    def reset_at_is_aware(self) -> ConversationResetReceipt:
        if self.reset_at.tzinfo is None:
            raise ValueError("conversation resetAt must be timezone-aware")
        return self


class AuthoritativeMemorySnapshot(RealtimeProtocolModel):
    age_band: AgeBand
    reset_revision: int = Field(ge=0)
    current_session_summary: (
        Annotated[str, StringConstraints(min_length=1, max_length=1_200)] | None
    )
    delivered_turns: list[DeliveredConversationTurn] = Field(
        max_length=MAX_SNAPSHOT_DELIVERED_TURNS
    )
    stable_memories: list[StableMemoryRecord] = Field(max_length=MAX_STABLE_MEMORIES)

    @model_validator(mode="after")
    def records_are_unique(self) -> AuthoritativeMemorySnapshot:
        turn_ids = [turn.turn_id for turn in self.delivered_turns]
        if len(turn_ids) != len(set(turn_ids)):
            raise ValueError("delivered turn IDs must be unique")
        memory_ids = [memory.id for memory in self.stable_memories]
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("stable memory IDs must be unique")
        return self


class MemoryProposalDecision(RealtimeProtocolModel):
    accepted: bool
    reason: Annotated[NonEmptyText, StringConstraints(max_length=200)]
    record: StableMemoryRecord | None

    @model_validator(mode="after")
    def accepted_decisions_have_records(self) -> MemoryProposalDecision:
        if self.accepted != (self.record is not None):
            raise ValueError("accepted memory decisions require a record")
        return self


class AuthoritativeMemoryStore(Protocol):
    async def open(self) -> None: ...

    async def close(self) -> None: ...

    async def ping(self) -> None: ...

    async def load_snapshot(
        self,
        *,
        subject: str,
        age_band: AgeBand,
    ) -> AuthoritativeMemorySnapshot: ...

    async def commit_delivered_turn(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        turn: DeliveredConversationTurn,
        proposal: AvaMemoryProposal | None,
        memory_id: str | None,
        occurred_at: datetime | None = None,
        timezone_id: str = "UTC",
    ) -> MemoryProposalDecision: ...

    async def search_memory_candidates(
        self,
        *,
        subject: str,
        query: str,
        limit: int = 5,
        from_instant: datetime | None = None,
        to_instant: datetime | None = None,
    ) -> list[MemorySearchCandidate]: ...

    async def set_session_summary(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        summary: str | None,
    ) -> None: ...

    async def load_weekly_recap(
        self,
        *,
        subject: str,
        period_start: date,
        generated_at: datetime,
    ) -> WeeklyConversationRecap: ...

    async def reset_conversation(
        self,
        *,
        subject: str,
    ) -> ConversationResetReceipt: ...


@dataclass(frozen=True, slots=True)
class StoredDeliveredConversationEvent:
    occurred_at: datetime
    timezone_id: str
    turn: DeliveredConversationTurn


class DevelopmentAuthoritativeMemoryStore:
    """Deterministic development store for realtime coordinator tests."""

    def __init__(
        self,
        *,
        max_memories_per_subject: int = MAX_STABLE_MEMORIES,
    ) -> None:
        if not 1 <= max_memories_per_subject <= MAX_STABLE_MEMORIES:
            raise ValueError("max_memories_per_subject must be between 1 and 12")
        self._turns: defaultdict[str, list[StoredDeliveredConversationEvent]] = (
            defaultdict(list)
        )
        self._episodes: defaultdict[str, list[ConversationEpisode]] = defaultdict(list)
        self._memories: defaultdict[str, dict[str, StableMemoryRecord]] = defaultdict(
            dict
        )
        self._summaries: dict[str, str] = {}
        self._weekly_records: defaultdict[str, list[WeeklyConversationRecord]] = (
            defaultdict(list)
        )
        self._committed_turn_ids: defaultdict[str, set[str]] = defaultdict(set)
        self._reset_revisions: defaultdict[str, int] = defaultdict(int)
        self._max_memories_per_subject = max_memories_per_subject
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def ping(self) -> None:
        return None

    async def load_snapshot(
        self,
        *,
        subject: str,
        age_band: AgeBand,
    ) -> AuthoritativeMemorySnapshot:
        normalized_subject = _validate_subject(subject)
        async with self._lock:
            return AuthoritativeMemorySnapshot.model_validate(
                {
                    "ageBand": age_band,
                    "resetRevision": self._reset_revisions[normalized_subject],
                    "currentSessionSummary": self._summaries.get(normalized_subject),
                    "deliveredTurns": [
                        event.turn.model_dump(mode="json", by_alias=True)
                        for event in self._turns.get(normalized_subject, ())[
                            -MAX_SNAPSHOT_DELIVERED_TURNS:
                        ]
                    ],
                    "stableMemories": [
                        memory.model_dump(mode="json", by_alias=True)
                        for memory in self._memories.get(
                            normalized_subject, {}
                        ).values()
                    ],
                }
            )

    async def commit_delivered_turn(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        turn: DeliveredConversationTurn,
        proposal: AvaMemoryProposal | None,
        memory_id: str | None,
        occurred_at: datetime | None = None,
        timezone_id: str = "UTC",
    ) -> MemoryProposalDecision:
        normalized_subject = _validate_subject(subject)
        validated_expected_reset_revision = _validate_reset_revision(
            expected_reset_revision,
            field_name="expected_reset_revision",
        )
        normalized_occurred_at = _validate_aware_datetime(
            occurred_at or datetime.now(UTC),
            field_name="occurred_at",
        )
        validated_timezone_id = validate_iana_timezone_id(timezone_id)
        validated_turn = DeliveredConversationTurn.model_validate(
            turn.model_dump(mode="json", by_alias=True)
        )
        weekly_summary = _validate_weekly_summary(validated_turn.child_turn_summary)
        decision = evaluate_delivered_turn_memory_proposal(
            turn=validated_turn,
            proposal=proposal,
            memory_id=memory_id,
        )

        async with self._lock:
            current_reset_revision = self._reset_revisions[normalized_subject]
            if current_reset_revision != validated_expected_reset_revision:
                raise MemoryResetRevisionMismatchError(
                    expected_reset_revision=validated_expected_reset_revision,
                    current_reset_revision=current_reset_revision,
                )
            if validated_turn.turn_id in self._committed_turn_ids[normalized_subject]:
                raise ValueError(
                    f"delivered turn {validated_turn.turn_id!r} is already committed"
                )
            if decision.record is not None:
                record = decision.record
                memories = self._memories[normalized_subject]
                has_semantic_duplicate = any(
                    existing.kind == record.kind
                    and existing.summary.casefold() == record.summary.casefold()
                    for existing in memories.values()
                )
                if has_semantic_duplicate:
                    decision = _decision(
                        False,
                        "Equivalent stable memory already exists.",
                    )
                elif (
                    record.id not in memories
                    and len(memories) >= self._max_memories_per_subject
                ):
                    decision = _decision(
                        False,
                        "Stable memory capacity is exhausted; the delivered turn "
                        "was retained.",
                    )
                else:
                    memories[record.id] = record
            turns = self._turns[normalized_subject]
            turns.append(
                StoredDeliveredConversationEvent(
                    occurred_at=normalized_occurred_at,
                    timezone_id=validated_timezone_id,
                    turn=validated_turn,
                )
            )
            turns.sort(key=lambda item: (item.occurred_at, item.turn.turn_id))
            if len(turns) > RAW_RETENTION_HIGH_WATER:
                compacted_count = len(turns) - RAW_RETENTION_LOW_WATER
                source_events = tuple(turns[:compacted_count])
                new_episodes = build_conversation_episodes(
                    subject=normalized_subject,
                    source_events=source_events,
                    created_at=normalized_occurred_at,
                )
                self._episodes[normalized_subject].extend(new_episodes)
                del turns[:compacted_count]
            _prune_development_episodes(
                self._episodes[normalized_subject],
                reference_time=datetime.now(UTC),
            )
            self._committed_turn_ids[normalized_subject].add(validated_turn.turn_id)
            weekly_records = self._weekly_records[normalized_subject]
            weekly_records.append(
                WeeklyConversationRecord.model_validate(
                    {
                        "turnId": validated_turn.turn_id,
                        "summary": weekly_summary,
                        "occurredAt": normalized_occurred_at,
                    }
                )
            )
            weekly_records.sort(key=lambda record: (record.occurred_at, record.turn_id))
            if len(weekly_records) > MAX_WEEKLY_RECORDS:
                del weekly_records[:-MAX_WEEKLY_RECORDS]
        return decision

    async def search_memory_candidates(
        self,
        *,
        subject: str,
        query: str,
        limit: int = 5,
        from_instant: datetime | None = None,
        to_instant: datetime | None = None,
    ) -> list[MemorySearchCandidate]:
        normalized_subject = _validate_subject(subject)
        query_tokens = _validate_search_query(query)
        validated_limit = _validate_search_limit(limit)
        lower, upper = _validate_search_bounds(from_instant, to_instant)
        async with self._lock:
            raw_events = tuple(self._turns.get(normalized_subject, ()))
            raw_turn_ids = {event.turn.turn_id for event in raw_events}
            episodes = tuple(self._episodes.get(normalized_subject, ()))
            results: list[MemorySearchCandidate] = []

            for event in raw_events:
                if not _instant_is_in_range(event.occurred_at, lower, upper):
                    continue
                text = _raw_event_search_text(event.turn)
                relevance = _lexical_relevance(text, query_tokens)
                if relevance <= 0:
                    continue
                results.append(
                    _memory_candidate(
                        source_id=event.turn.turn_id,
                        source_kind=MemoryCandidateSourceKind.RAW_EVENT,
                        started_at=event.occurred_at,
                        ended_at=event.occurred_at,
                        timezone_ids=(event.timezone_id,),
                        reference_text=text,
                        source_turn_ids=(event.turn.turn_id,),
                        relevance=relevance,
                    )
                )

            for episode in episodes:
                if raw_turn_ids.intersection(episode.source_turn_ids):
                    continue
                if not _range_overlaps(
                    episode.started_at,
                    episode.ended_at,
                    lower,
                    upper,
                ):
                    continue
                relevance = _lexical_relevance(episode.digest, query_tokens)
                if relevance <= 0:
                    continue
                results.append(
                    _memory_candidate(
                        source_id=episode.episode_id,
                        source_kind=MemoryCandidateSourceKind.EPISODE,
                        started_at=episode.started_at,
                        ended_at=episode.ended_at,
                        timezone_ids=(episode.timezone_id,),
                        reference_text=episode.digest,
                        source_turn_ids=episode.source_turn_ids,
                        relevance=relevance,
                    )
                )

            for memory in self._memories.get(normalized_subject, {}).values():
                text = f"{memory.summary} {memory.evidence}"
                relevance = _lexical_relevance(text, query_tokens)
                if relevance <= 0:
                    continue
                provenance = _find_provenance_range(
                    source_turn_id=memory.source_turn_id,
                    raw_events=raw_events,
                    episodes=episodes,
                )
                if provenance is None:
                    if lower is not None or upper is not None:
                        continue
                    started_at = ended_at = None
                    timezone_ids: Sequence[str] = ()
                else:
                    started_at, ended_at, timezone_ids = provenance
                    if not _range_overlaps(
                        started_at,
                        ended_at,
                        lower,
                        upper,
                    ):
                        continue
                results.append(
                    _memory_candidate(
                        source_id=memory.id,
                        source_kind=MemoryCandidateSourceKind.STABLE_MEMORY,
                        started_at=started_at,
                        ended_at=ended_at,
                        timezone_ids=timezone_ids,
                        reference_text=text,
                        source_turn_ids=(memory.source_turn_id,),
                        relevance=relevance,
                    )
                )

        results.sort(
            key=lambda candidate: (
                -candidate.relevance,
                -(
                    candidate.ended_at.timestamp()
                    if candidate.ended_at is not None
                    else float("-inf")
                ),
                candidate.source_id,
            )
        )
        return results[:validated_limit]

    async def set_session_summary(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        summary: str | None,
    ) -> None:
        normalized_subject = _validate_subject(subject)
        validated_expected_reset_revision = _validate_reset_revision(
            expected_reset_revision,
            field_name="expected_reset_revision",
        )
        validated = _validate_optional_summary(summary)
        async with self._lock:
            current_reset_revision = self._reset_revisions[normalized_subject]
            if current_reset_revision != validated_expected_reset_revision:
                raise MemoryResetRevisionMismatchError(
                    expected_reset_revision=validated_expected_reset_revision,
                    current_reset_revision=current_reset_revision,
                )
            if validated is None:
                self._summaries.pop(normalized_subject, None)
            else:
                self._summaries[normalized_subject] = validated

    async def load_weekly_recap(
        self,
        *,
        subject: str,
        period_start: date,
        generated_at: datetime,
    ) -> WeeklyConversationRecap:
        normalized_subject = _validate_subject(subject)
        normalized_generated_at = _validate_aware_datetime(
            generated_at,
            field_name="generated_at",
        )
        period_end = period_start + timedelta(days=7)
        async with self._lock:
            records = [
                record
                for record in self._weekly_records.get(normalized_subject, ())
                if period_start <= record.occurred_at.date() < period_end
            ]
            return WeeklyConversationRecap.model_validate(
                {
                    "periodStart": period_start,
                    "periodEnd": period_end,
                    "generatedAt": normalized_generated_at,
                    "resetRevision": self._reset_revisions[normalized_subject],
                    "records": [
                        record.model_dump(mode="json", by_alias=True)
                        for record in records
                    ],
                }
            )

    async def reset_conversation(
        self,
        *,
        subject: str,
    ) -> ConversationResetReceipt:
        normalized_subject = _validate_subject(subject)
        async with self._lock:
            self._turns.pop(normalized_subject, None)
            self._episodes.pop(normalized_subject, None)
            self._memories.pop(normalized_subject, None)
            self._summaries.pop(normalized_subject, None)
            self._weekly_records.pop(normalized_subject, None)
            self._committed_turn_ids.pop(normalized_subject, None)
            self._reset_revisions[normalized_subject] += 1
            return ConversationResetReceipt.model_validate(
                {
                    "revision": self._reset_revisions[normalized_subject],
                    "resetAt": datetime.now(UTC),
                }
            )


def conversation_episode_id(source_turn_ids: Sequence[str]) -> str:
    if not source_turn_ids or len(source_turn_ids) > MAX_EPISODE_SOURCE_EVENTS:
        raise ValueError("episode source batch must contain between 1 and 32 turns")
    validated_ids = list(source_turn_ids)
    if len(validated_ids) != len(set(validated_ids)):
        raise ValueError("episode source turn IDs must be unique")
    for turn_id in validated_ids:
        if not isinstance(turn_id, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9._:-]{0,63}", turn_id
        ):
            raise ValueError("episode source turn ID is invalid")
    digest = hashlib.sha256("\x1f".join(validated_ids).encode("utf-8")).hexdigest()
    return f"ep-{digest[:60]}"


def build_conversation_episodes(
    *,
    subject: str,
    source_events: Sequence[StoredDeliveredConversationEvent],
    created_at: datetime,
) -> list[ConversationEpisode]:
    normalized_subject = _validate_subject(subject)
    normalized_created_at = _validate_aware_datetime(
        created_at,
        field_name="created_at",
    )
    episodes: list[ConversationEpisode] = []
    for offset in range(0, len(source_events), MAX_EPISODE_SOURCE_EVENTS):
        batch = tuple(source_events[offset : offset + MAX_EPISODE_SOURCE_EVENTS])
        if not batch:
            continue
        ordered = tuple(
            sorted(batch, key=lambda event: (event.occurred_at, event.turn.turn_id))
        )
        source_turn_ids = tuple(event.turn.turn_id for event in ordered)
        digest = "\n".join(_episode_event_text(event) for event in ordered)
        episodes.append(
            ConversationEpisode.model_validate(
                {
                    "subject": normalized_subject,
                    "episodeId": conversation_episode_id(source_turn_ids),
                    "startedAt": ordered[0].occurred_at,
                    "endedAt": ordered[-1].occurred_at,
                    "timezoneId": ordered[0].timezone_id,
                    "digest": digest,
                    "sourceTurnIds": source_turn_ids,
                    "sourceEventCount": len(ordered),
                    "createdAt": normalized_created_at,
                }
            )
        )
    return episodes


def _episode_event_text(event: StoredDeliveredConversationEvent) -> str:
    turn = event.turn
    fields = [_bounded_search_text(turn.child_turn_summary, 240)]
    if turn.input_kind == DeliveredInputKind.SPEECH:
        if turn.child_text is not None:
            fields.append(_bounded_search_text(turn.child_text, 180))
    elif turn.canvas_interaction is not None:
        fields.append(
            _bounded_search_text(turn.canvas_interaction.accessibility_label, 160)
        )
    if turn.ava_delivered_text:
        fields.append(_bounded_search_text(turn.ava_delivered_text, 180))
    return " ".join(fields)


def _prune_development_episodes(
    episodes: list[ConversationEpisode],
    *,
    reference_time: datetime,
) -> None:
    cutoff = reference_time - timedelta(days=EPISODE_RETENTION_DAYS)
    retained = [episode for episode in episodes if episode.ended_at >= cutoff]
    retained.sort(key=lambda episode: (episode.ended_at, episode.episode_id))
    if len(retained) > MAX_EPISODES:
        retained = retained[-MAX_EPISODES:]
    episodes[:] = retained


def _validate_search_query(query: str) -> tuple[str, ...]:
    if not isinstance(query, str):
        raise ValueError("memory search query must be text")
    normalized = _normalize_whitespace(query)
    if not normalized or len(normalized) > 500:
        raise ValueError("memory search query must contain 1 to 500 characters")
    tokens = tuple(token.casefold() for token in re.findall(r"\w+", normalized))
    if not tokens:
        raise ValueError("memory search query must contain searchable text")
    return tokens


def _validate_search_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5:
        raise ValueError("memory search limit must be between 1 and 5")
    return limit


def _validate_search_bounds(
    from_instant: datetime | None,
    to_instant: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    lower = (
        _validate_aware_datetime(from_instant, field_name="from_instant")
        if from_instant is not None
        else None
    )
    upper = (
        _validate_aware_datetime(to_instant, field_name="to_instant")
        if to_instant is not None
        else None
    )
    if lower is not None and upper is not None and lower >= upper:
        raise ValueError("memory search UTC interval must be increasing")
    return lower, upper


def _instant_is_in_range(
    instant: datetime,
    lower: datetime | None,
    upper: datetime | None,
) -> bool:
    return (lower is None or instant >= lower) and (upper is None or instant < upper)


def _range_overlaps(
    started_at: datetime,
    ended_at: datetime,
    lower: datetime | None,
    upper: datetime | None,
) -> bool:
    return (lower is None or ended_at >= lower) and (
        upper is None or started_at < upper
    )


def _raw_event_search_text(turn: DeliveredConversationTurn) -> str:
    fields = [turn.child_turn_summary]
    if turn.input_kind == DeliveredInputKind.SPEECH and turn.child_text is not None:
        fields.append(turn.child_text)
    elif turn.canvas_interaction is not None:
        fields.append(turn.canvas_interaction.accessibility_label)
    if turn.ava_delivered_text:
        fields.append(turn.ava_delivered_text)
    return _bounded_search_text(" ".join(fields), 1_200)


def _bounded_search_text(value: str, max_characters: int) -> str:
    normalized = _normalize_whitespace(value)
    if len(normalized) <= max_characters:
        return normalized
    return normalized[: max_characters - 1].rstrip() + "…"


def _lexical_relevance(text: str, query_tokens: Sequence[str]) -> float:
    folded = text.casefold()
    return float(sum(folded.count(token) for token in query_tokens))


def _find_provenance_range(
    *,
    source_turn_id: str,
    raw_events: Sequence[StoredDeliveredConversationEvent],
    episodes: Sequence[ConversationEpisode],
) -> tuple[datetime, datetime, Sequence[str]] | None:
    for event in raw_events:
        if event.turn.turn_id == source_turn_id:
            return event.occurred_at, event.occurred_at, (event.timezone_id,)
    for episode in episodes:
        if source_turn_id in episode.source_turn_ids:
            return (
                episode.started_at,
                episode.ended_at,
                (episode.timezone_id,),
            )
    return None


def _memory_candidate(
    *,
    source_id: str,
    source_kind: MemoryCandidateSourceKind,
    started_at: datetime | None,
    ended_at: datetime | None,
    timezone_ids: Sequence[str],
    reference_text: str,
    source_turn_ids: Sequence[str],
    relevance: float,
) -> MemorySearchCandidate:
    return MemorySearchCandidate.model_validate(
        {
            "sourceId": source_id,
            "sourceKind": source_kind,
            "startedAt": started_at,
            "endedAt": ended_at,
            "timezoneIds": list(timezone_ids),
            "referenceText": _bounded_search_text(reference_text, 1_200),
            "sourceTurnIds": list(source_turn_ids),
            "relevance": relevance,
        }
    )


_SENSITIVE_MEMORY_PATTERN = re.compile(
    r"\b(?:address|email|e-mail|phone|password|passcode|secret|"
    r"school(?:'s)? name|precise location|where (?:i|we) live)\b",
    re.IGNORECASE,
)
_HIDDEN_INFERENCE_PATTERN = re.compile(
    r"\b(?:diagnos(?:is|ed)|gifted|intelligence|iq|depressed|anxious|"
    r"lonely|trauma|abuse|emotionally)\b",
    re.IGNORECASE,
)
_EMOTIONAL_CERTAINTY_PATTERN = re.compile(
    r"\b(?:the child|they) (?:is|was|feels?|seems?) "
    r"(?:sad|happy|angry|upset|lonely|anxious|afraid)\b",
    re.IGNORECASE,
)


def evaluate_memory_proposal(
    *,
    proposal: AvaMemoryProposal | None,
    child_text: str,
    source_turn_id: str,
    memory_id: str | None,
    child_text_source: ChildTurnTextSource = ChildTurnTextSource.TRANSCRIPT,
) -> MemoryProposalDecision:
    if proposal is None:
        if memory_id is not None:
            raise ValueError("memory_id must be absent when there is no proposal")
        return _decision(False, "No stable memory was proposed.")
    if memory_id is None:
        raise ValueError("memory_id is required for a proposed memory")
    if child_text_source != ChildTurnTextSource.TRANSCRIPT:
        return _decision(
            False,
            "Stable memory requires a provider transcript for exact evidence.",
        )
    if proposal.confidence < MINIMUM_STABLE_MEMORY_CONFIDENCE:
        return _decision(False, "Proposal confidence is below 0.8.")

    evidence = _normalize_whitespace(proposal.evidence)
    normalized_child = _normalize_whitespace(child_text)
    if evidence.casefold() not in normalized_child.casefold():
        return _decision(
            False,
            "Proposal evidence is not an exact excerpt from the child turn.",
        )

    candidate_text = f"{proposal.summary} {proposal.evidence}"
    if _SENSITIVE_MEMORY_PATTERN.search(candidate_text):
        return _decision(False, "Proposal contains prohibited sensitive data.")
    if _HIDDEN_INFERENCE_PATTERN.search(
        candidate_text
    ) or _EMOTIONAL_CERTAINTY_PATTERN.search(candidate_text):
        return _decision(False, "Proposal contains a hidden or diagnostic inference.")

    record = StableMemoryRecord.model_validate(
        {
            "id": memory_id,
            "kind": proposal.kind,
            "summary": proposal.summary,
            "evidence": evidence,
            "sourceTurnId": source_turn_id,
        }
    )
    return MemoryProposalDecision.model_validate(
        {
            "accepted": True,
            "reason": "Proposal is explicit, allowlisted, evidenced, and confident.",
            "record": record.model_dump(mode="json", by_alias=True),
        }
    )


def evaluate_delivered_turn_memory_proposal(
    *,
    turn: DeliveredConversationTurn,
    proposal: AvaMemoryProposal | None,
    memory_id: str | None,
) -> MemoryProposalDecision:
    if turn.input_kind == DeliveredInputKind.CANVAS_INTERACTION:
        if proposal is None:
            if memory_id is not None:
                raise ValueError("memory_id must be absent when there is no proposal")
            return _decision(
                False,
                "Canvas interaction input is retained without a stable memory proposal.",
            )
        return _decision(
            False,
            "Stable memory requires genuine child speech transcript evidence.",
        )
    if turn.child_text is None or turn.child_text_source is None:
        raise ValueError("speech delivered turn lost its transcript evidence")
    return evaluate_memory_proposal(
        proposal=proposal,
        child_text=turn.child_text,
        source_turn_id=turn.turn_id,
        memory_id=memory_id,
        child_text_source=turn.child_text_source,
    )


def _decision(accepted: bool, reason: str) -> MemoryProposalDecision:
    return MemoryProposalDecision.model_validate(
        {"accepted": accepted, "reason": reason, "record": None}
    )


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def _validate_optional_summary(summary: str | None) -> str | None:
    if summary is None:
        return None
    normalized = summary.strip()
    if not normalized:
        raise ValueError("session summary cannot be empty")
    if len(normalized) > MAX_SESSION_SUMMARY_CHARACTERS:
        raise ValueError("session summary cannot exceed 1200 characters")
    if _SENSITIVE_MEMORY_PATTERN.search(normalized):
        raise ValueError("session summary contains prohibited sensitive data")
    if _HIDDEN_INFERENCE_PATTERN.search(
        normalized
    ) or _EMOTIONAL_CERTAINTY_PATTERN.search(normalized):
        raise ValueError("session summary contains a hidden inference")
    return normalized


def _validate_weekly_summary(summary: str) -> str:
    normalized = _normalize_whitespace(summary)
    if not normalized:
        raise ValueError("weekly record summary cannot be empty")
    if len(normalized) > MAX_CHILD_TURN_SUMMARY_CHARACTERS:
        raise ValueError(
            "weekly record summary cannot exceed "
            f"{MAX_CHILD_TURN_SUMMARY_CHARACTERS} characters"
        )
    if _SENSITIVE_MEMORY_PATTERN.search(normalized):
        raise ValueError("weekly record summary contains prohibited sensitive data")
    if _HIDDEN_INFERENCE_PATTERN.search(
        normalized
    ) or _EMOTIONAL_CERTAINTY_PATTERN.search(normalized):
        raise ValueError("weekly record summary contains a hidden inference")
    return normalized


def _validate_aware_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_reset_revision(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _validate_subject(subject: str) -> str:
    if not subject or subject != subject.strip():
        raise ValueError(
            "memory subject must be non-empty and have no outer whitespace"
        )
    if len(subject) > 256:
        raise ValueError("memory subject cannot exceed 256 characters")
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:@-"
    if any(character not in allowed for character in subject):
        raise ValueError("memory subject contains unsupported characters")
    return subject


def bounded_snapshot(
    snapshot: AuthoritativeMemorySnapshot,
    *,
    max_turns: int,
    max_memories: int,
) -> AuthoritativeMemorySnapshot:
    """Return newest bounded context without mutating the stored snapshot."""

    if not 0 <= max_turns <= MAX_SNAPSHOT_DELIVERED_TURNS:
        raise ValueError("max_turns must be between 0 and 8")
    if not 0 <= max_memories <= MAX_STABLE_MEMORIES:
        raise ValueError("max_memories must be between 0 and 12")
    turns: Sequence[DeliveredConversationTurn] = snapshot.delivered_turns
    memories: Sequence[StableMemoryRecord] = snapshot.stable_memories
    return AuthoritativeMemorySnapshot.model_validate(
        {
            "ageBand": snapshot.age_band,
            "resetRevision": snapshot.reset_revision,
            "currentSessionSummary": snapshot.current_session_summary,
            "deliveredTurns": [
                turn.model_dump(mode="json", by_alias=True)
                for turn in turns[-max_turns:]
            ]
            if max_turns
            else [],
            "stableMemories": [
                memory.model_dump(mode="json", by_alias=True)
                for memory in memories[-max_memories:]
            ]
            if max_memories
            else [],
        }
    )
