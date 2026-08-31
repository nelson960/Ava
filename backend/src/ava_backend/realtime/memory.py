from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Protocol

from pydantic import Field, StringConstraints, model_validator

from ..protocol import AgeBand
from .common import NonEmptyText, ProtocolIdentifier, RealtimeProtocolModel
from .tool_contract import InteractiveCanvasMode

MAX_DELIVERED_TURNS = 8
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
        max_length=MAX_DELIVERED_TURNS
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
    ) -> MemoryProposalDecision: ...

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


class DevelopmentAuthoritativeMemoryStore:
    """Deterministic development store for realtime coordinator tests."""

    def __init__(
        self,
        *,
        max_turns_per_subject: int = MAX_DELIVERED_TURNS,
        max_memories_per_subject: int = MAX_STABLE_MEMORIES,
    ) -> None:
        if not 1 <= max_turns_per_subject <= MAX_DELIVERED_TURNS:
            raise ValueError("max_turns_per_subject must be between 1 and 8")
        if not 1 <= max_memories_per_subject <= MAX_STABLE_MEMORIES:
            raise ValueError("max_memories_per_subject must be between 1 and 12")
        self._turns: defaultdict[
            str, list[tuple[datetime, DeliveredConversationTurn]]
        ] = defaultdict(list)
        self._memories: defaultdict[str, dict[str, StableMemoryRecord]] = defaultdict(
            dict
        )
        self._summaries: dict[str, str] = {}
        self._weekly_records: defaultdict[str, list[WeeklyConversationRecord]] = (
            defaultdict(list)
        )
        self._committed_turn_ids: defaultdict[str, set[str]] = defaultdict(set)
        self._reset_revisions: defaultdict[str, int] = defaultdict(int)
        self._max_turns_per_subject = max_turns_per_subject
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
                        turn.model_dump(mode="json", by_alias=True)
                        for _, turn in self._turns.get(normalized_subject, ())
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
            turns.append((normalized_occurred_at, validated_turn))
            turns.sort(key=lambda item: (item[0], item[1].turn_id))
            if len(turns) > self._max_turns_per_subject:
                _, evicted = turns.pop(0)
                self._summaries[normalized_subject] = compact_session_summary(
                    self._summaries.get(normalized_subject),
                    (evicted.child_turn_summary,),
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


def compact_session_summary(
    current: str | None,
    additions: Sequence[str],
    *,
    max_characters: int = MAX_SESSION_SUMMARY_CHARACTERS,
) -> str:
    """Compact older factual turn summaries without another model call."""

    if not 240 <= max_characters <= MAX_SESSION_SUMMARY_CHARACTERS:
        raise ValueError("max_characters must be between 240 and 1200")
    items: list[str] = []
    seen: set[str] = set()
    for raw in (*((current,) if current is not None else ()), *additions):
        normalized = _normalize_whitespace(raw).strip(" ;")
        if not normalized:
            continue
        for candidate in normalized.split(" | "):
            item = candidate.strip(" ;")
            folded = item.casefold()
            if item and folded not in seen:
                seen.add(folded)
                items.append(item)
    while items and len(" | ".join(items)) > max_characters:
        items.pop(0)
    if not items:
        raise ValueError("session summary compaction produced no factual content")
    return " | ".join(items)


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
    return value


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

    if not 0 <= max_turns <= MAX_DELIVERED_TURNS:
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
