from __future__ import annotations

import asyncio
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from ..protocol import AgeBand
from .common import NonEmptyText, ProtocolIdentifier, RealtimeProtocolModel, _to_camel
from .tool_contract import (
    InteractiveCanvasMode,
    InteractiveCanvasRequest,
    InteractiveCanvasCancellationRequest,
    InteractiveCanvasCancellationToolOutputV4,
    ManageCanvasPresentationRequest,
    ManageCanvasPresentationToolOutputV4,
    SearchMemoryRequest,
    SearchMemoryToolOutputV4,
    GetCurrentTimeRequest,
    GetCurrentTimeToolOutputV4,
    RejectedUtilityToolOutputV4,
    MAX_SEMANTIC_TOOL_EXCHANGES,
    MemorySearchOrder,
)
from .timezone import MAX_TIMEZONE_ID_CHARACTERS, validate_iana_timezone_id

MAX_SNAPSHOT_DELIVERED_TURNS = 8
MAX_EPISODE_SOURCE_EVENTS = 32
MAX_STABLE_MEMORIES = 12
MAX_WEEKLY_RECORDS = 64
MAX_CHILD_TURN_SUMMARY_CHARACTERS = 240
MINIMUM_STABLE_MEMORY_CONFIDENCE = 0.8
MAX_DIALOGUE_FRAME_ITEM_CHARACTERS = 160
MAX_DIALOGUE_FRAME_LIST_ITEMS = 4
MAX_DIALOGUE_FRAME_RECENT_ENTITIES = 8
MAX_DIALOGUE_FRAME_CANVAS_REFERENCES = 4
MAX_DIALOGUE_FRAME_TOOL_REFERENCES = 4
MAX_DIALOGUE_FRAME_UTF8_BYTES = 3_200
MAX_SEMANTIC_JOURNAL_EVENTS_PER_PROPOSAL = 4
MAX_SEMANTIC_JOURNAL_KEY_CHARACTERS = 96
MAX_SEMANTIC_JOURNAL_SUMMARY_CHARACTERS = 240
MAX_SEMANTIC_JOURNAL_CANVAS_REFERENCES = 4
MAX_SEMANTIC_JOURNAL_SUPERSEDES_EVENT_IDS = 4
MAX_SEMANTIC_MEMORY_PROPOSAL_UTF8_BYTES = 4_096


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


class DialogueFrameSourceTurnMissingError(RuntimeError):
    """A frame update referenced a turn that is not durably committed."""


class DialogueFrameUpdateConflictError(RuntimeError):
    """One source turn attempted two different frame replacements."""


class SemanticMemorySourceTurnMissingError(RuntimeError):
    """A semantic proposal referenced a turn that is not durably committed."""


class SemanticMemoryProposalConflictError(RuntimeError):
    """One source turn attempted two different semantic proposals."""


class SemanticJournalConflictError(RuntimeError):
    """A Journal proposal conflicts with durable identity or lineage state."""


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


class SearchMemoryExchange(RealtimeProtocolModel):
    tool: Literal["search_memory"] = "search_memory"
    request: SearchMemoryRequest | None
    result: SearchMemoryToolOutputV4 | RejectedUtilityToolOutputV4


class CurrentTimeExchange(RealtimeProtocolModel):
    tool: Literal["get_current_time"] = "get_current_time"
    request: GetCurrentTimeRequest | None
    result: GetCurrentTimeToolOutputV4 | RejectedUtilityToolOutputV4


class CanvasExchange(RealtimeProtocolModel):
    tool: Literal["request_interactive_canvas"] = "request_interactive_canvas"
    request: InteractiveCanvasRequest | None
    # This result has the same semantic accepted/rejected shape without generation ID.
    result: InteractiveCanvasCancellationToolOutputV4


class CanvasPresentationExchange(RealtimeProtocolModel):
    tool: Literal["manage_canvas_presentation"] = "manage_canvas_presentation"
    request: ManageCanvasPresentationRequest | None
    result: ManageCanvasPresentationToolOutputV4


class CanvasCancellationExchange(RealtimeProtocolModel):
    tool: Literal["cancel_interactive_canvas"] = "cancel_interactive_canvas"
    request: InteractiveCanvasCancellationRequest | None
    result: InteractiveCanvasCancellationToolOutputV4


SemanticToolExchange = Annotated[
    SearchMemoryExchange
    | CurrentTimeExchange
    | CanvasExchange
    | CanvasPresentationExchange
    | CanvasCancellationExchange,
    Field(discriminator="tool"),
]


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
    tool_exchanges: tuple[SemanticToolExchange, ...] = Field(
        default=(), max_length=MAX_SEMANTIC_TOOL_EXCHANGES
    )

    @model_validator(mode="after")
    def delivered_samples_match_text(self) -> DeliveredConversationTurn:
        for index, exchange in enumerate(self.tool_exchanges):
            if (
                exchange.request is None
                and getattr(exchange.result, "status", None) != "rejected"
            ):
                raise ValueError(
                    "only rejected malformed calls may omit the semantic request"
                )
            if index and (
                self.tool_exchanges[index - 1].tool
                not in {"search_memory", "get_current_time"}
                or exchange.tool in {"search_memory", "get_current_time"}
            ):
                raise ValueError(
                    "tool exchanges must follow the utility then terminal Canvas chain"
                )
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


class StoredDeliveredConversationTurn(DeliveredConversationTurn):
    reset_revision: int = Field(ge=0, le=9_223_372_036_854_775_807)
    turn_sequence: int = Field(ge=1, le=9_223_372_036_854_775_807)
    occurred_at: datetime
    timezone_id: str

    @model_validator(mode="after")
    def validate_time(self) -> StoredDeliveredConversationTurn:
        _validate_aware_datetime(self.occurred_at, field_name="occurred_at")
        validate_iana_timezone_id(self.timezone_id)
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
    SEMANTIC_JOURNAL = "semanticJournal"


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
    # Internal provenance must be complete: truncation can hide an overlap.
    # Only the bounded semantic reference is sent to Realtime.
    source_turn_ids: list[ProtocolIdentifier]
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


DialogueFrameText = Annotated[
    NonEmptyText,
    StringConstraints(max_length=MAX_DIALOGUE_FRAME_ITEM_CHARACTERS),
]


class DialogueFrameCanvasReference(RealtimeProtocolModel):
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    relation: DialogueFrameText

    @model_validator(mode="after")
    def semantic_text_is_safe(self) -> DialogueFrameCanvasReference:
        _reject_control_characters(self.relation)
        return self


_DIALOGUE_FRAME_TEXT_COLLECTION_LIMITS: dict[str, int] = {
    "active_topics": MAX_DIALOGUE_FRAME_LIST_ITEMS,
    "active_goals": MAX_DIALOGUE_FRAME_LIST_ITEMS,
    "unresolved_questions": MAX_DIALOGUE_FRAME_LIST_ITEMS,
    "current_referents": MAX_DIALOGUE_FRAME_LIST_ITEMS,
    "recent_entities": MAX_DIALOGUE_FRAME_RECENT_ENTITIES,
    "ava_commitments": MAX_DIALOGUE_FRAME_LIST_ITEMS,
    "explicit_user_preferences": MAX_DIALOGUE_FRAME_LIST_ITEMS,
    "temporary_interaction_preferences": MAX_DIALOGUE_FRAME_LIST_ITEMS,
    "recent_tool_references": MAX_DIALOGUE_FRAME_TOOL_REFERENCES,
}
_DIALOGUE_FRAME_TEXT_FIELDS = tuple(_DIALOGUE_FRAME_TEXT_COLLECTION_LIMITS.keys())
_DIALOGUE_FRAME_COLLECTION_FIELDS = _DIALOGUE_FRAME_TEXT_FIELDS + (
    "relevant_canvas_references",
)


class DialogueFrameState(RealtimeProtocolModel):
    """Bounded semantic working state supplied by an external authority."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        validate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )

    active_topics: tuple[DialogueFrameText, ...] = Field(
        default=(), max_length=MAX_DIALOGUE_FRAME_LIST_ITEMS
    )
    active_goals: tuple[DialogueFrameText, ...] = Field(
        default=(), max_length=MAX_DIALOGUE_FRAME_LIST_ITEMS
    )
    unresolved_questions: tuple[DialogueFrameText, ...] = Field(
        default=(), max_length=MAX_DIALOGUE_FRAME_LIST_ITEMS
    )
    current_referents: tuple[DialogueFrameText, ...] = Field(
        default=(),
        max_length=MAX_DIALOGUE_FRAME_LIST_ITEMS,
        description=(
            "Objects actively referred to in current context, ordered "
            "highest-current-relevance first (max 4)."
        ),
    )
    recent_entities: tuple[DialogueFrameText, ...] = Field(
        default=(),
        max_length=MAX_DIALOGUE_FRAME_RECENT_ENTITIES,
        description=(
            "Recently mentioned entities, ordered highest-current-relevance "
            "first (max 8)."
        ),
    )
    ava_commitments: tuple[DialogueFrameText, ...] = Field(
        default=(), max_length=MAX_DIALOGUE_FRAME_LIST_ITEMS
    )
    explicit_user_preferences: tuple[DialogueFrameText, ...] = Field(
        default=(), max_length=MAX_DIALOGUE_FRAME_LIST_ITEMS
    )
    temporary_interaction_preferences: tuple[DialogueFrameText, ...] = Field(
        default=(), max_length=MAX_DIALOGUE_FRAME_LIST_ITEMS
    )
    relevant_canvas_references: tuple[DialogueFrameCanvasReference, ...] = Field(
        default=(), max_length=MAX_DIALOGUE_FRAME_CANVAS_REFERENCES
    )
    recent_tool_references: tuple[DialogueFrameText, ...] = Field(
        default=(), max_length=MAX_DIALOGUE_FRAME_TOOL_REFERENCES
    )

    @model_validator(mode="after")
    def frame_is_mechanically_bounded(self) -> DialogueFrameState:
        for field_name in _DIALOGUE_FRAME_TEXT_FIELDS:
            values = getattr(self, field_name)
            normalized_values: set[str] = set()
            for value in values:
                _reject_control_characters(value)
                normalized = value.casefold()
                if normalized in normalized_values:
                    raise ValueError(
                        f"{field_name} cannot contain case-normalized duplicates"
                    )
                normalized_values.add(normalized)
        canvas_identities: set[tuple[str, int, str]] = set()
        for reference in self.relevant_canvas_references:
            identity = (
                reference.scene_id.casefold(),
                reference.scene_revision,
                reference.relation.casefold(),
            )
            if identity in canvas_identities:
                raise ValueError(
                    "relevant_canvas_references cannot contain exact duplicates"
                )
            canvas_identities.add(identity)
        byte_count = len(_canonical_dialogue_frame_state_json(self).encode("utf-8"))
        if byte_count > MAX_DIALOGUE_FRAME_UTF8_BYTES:
            raise ValueError(
                "DialogueFrame serialized payload exceeds "
                f"{MAX_DIALOGUE_FRAME_UTF8_BYTES} UTF-8 bytes"
            )
        return self


class DialogueFrame(DialogueFrameState):
    revision: int = Field(default=0, ge=0)

    def to_state(self) -> DialogueFrameState:
        return DialogueFrameState.model_validate(
            self.model_dump(mode="python", exclude={"revision"})
        )


class DialogueFrameUpdate(RealtimeProtocolModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        validate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )
    base_revision: int = Field(ge=0)
    frame: DialogueFrameState


class DialogueFrameUpdateStatus(StrEnum):
    APPLIED = "applied"
    IDEMPOTENT = "idempotent"
    STALE = "stale"


class DialogueFrameUpdateDecision(RealtimeProtocolModel):
    status: DialogueFrameUpdateStatus
    frame: DialogueFrame


class SemanticJournalEventFamily(StrEnum):
    USER_PREFERENCE = "userPreference"
    AVA_COMMITMENT = "avaCommitment"
    USER_FACT = "userFact"
    CANVAS_REFERENCE = "canvasReference"


def canonicalize_semantic_key(key: str) -> str:
    """Normalize wire semantic key representation to canonical snake_case slug syntax.

    Allowed behavior:
      - trim surrounding whitespace
      - split camelCase/PascalCase boundaries into snake_case
      - lowercase
      - preserve already-valid '.', '_', ':', '-'
    """
    if not isinstance(key, str):
        return key
    s = key.strip()
    if not s:
        return s
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s.lower()


SemanticJournalKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_SEMANTIC_JOURNAL_KEY_CHARACTERS,
        pattern=r"^[a-z][a-z0-9._:-]{0,95}$",
    ),
]
SemanticJournalSummary = Annotated[
    NonEmptyText,
    StringConstraints(max_length=MAX_SEMANTIC_JOURNAL_SUMMARY_CHARACTERS),
]


class SemanticJournalCanvasReference(RealtimeProtocolModel):
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    relation: DialogueFrameText | None = None

    @model_validator(mode="after")
    def relation_is_safe(self) -> SemanticJournalCanvasReference:
        if self.relation is not None:
            _reject_control_characters(self.relation)
        return self


class SemanticJournalEventProposal(RealtimeProtocolModel):
    family: SemanticJournalEventFamily
    semantic_key: SemanticJournalKey
    summary: SemanticJournalSummary
    canonical_canvas_references: tuple[SemanticJournalCanvasReference, ...] = Field(
        default=(), max_length=MAX_SEMANTIC_JOURNAL_CANVAS_REFERENCES
    )
    supersedes_event_ids: tuple[ProtocolIdentifier, ...] = Field(
        default=(), max_length=MAX_SEMANTIC_JOURNAL_SUPERSEDES_EVENT_IDS
    )

    @model_validator(mode="after")
    def event_is_mechanically_bounded(self) -> SemanticJournalEventProposal:
        _reject_control_characters(self.summary)
        canvas_identities = [
            (reference.scene_id, reference.scene_revision, reference.relation)
            for reference in self.canonical_canvas_references
        ]
        if len(canvas_identities) != len(set(canvas_identities)):
            raise ValueError("canonicalCanvasReferences cannot contain duplicates")
        if len(self.supersedes_event_ids) != len(set(self.supersedes_event_ids)):
            raise ValueError("supersedesEventIds cannot contain duplicates")
        return self


class SemanticCaptureDisposition(StrEnum):
    CHANGED = "changed"
    NO_CHANGE = "noChange"


class SemanticCaptureJournalDraft(RealtimeProtocolModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        validate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )

    family: SemanticJournalEventFamily
    semantic_key: SemanticJournalKey
    summary: SemanticJournalSummary
    canonical_canvas_references: tuple[SemanticJournalCanvasReference, ...] = Field(
        default=(), max_length=MAX_SEMANTIC_JOURNAL_CANVAS_REFERENCES
    )

    @model_validator(mode="before")
    @classmethod
    def canonicalize_draft(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for key in ("canonicalCanvasReferences", "canonical_canvas_references"):
            if key in data and data[key] is None:
                data[key] = []
        for key in ("semanticKey", "semantic_key"):
            if key in data and isinstance(data[key], str):
                data[key] = canonicalize_semantic_key(data[key])
        family = data.get("family")
        if (
            family != SemanticJournalEventFamily.CANVAS_REFERENCE
            and family != "canvasReference"
        ):
            for key in ("canonicalCanvasReferences", "canonical_canvas_references"):
                if key in data and isinstance(data[key], (list, tuple)):
                    valid_refs = []
                    for item in data[key]:
                        if isinstance(item, SemanticJournalCanvasReference):
                            valid_refs.append(item)
                        elif isinstance(item, dict):
                            try:
                                valid_refs.append(
                                    SemanticJournalCanvasReference.model_validate(item)
                                )
                            except Exception:
                                continue
                    data[key] = valid_refs
        return data

    @model_validator(mode="after")
    def draft_is_mechanically_bounded(self) -> SemanticCaptureJournalDraft:
        _reject_control_characters(self.summary)
        canvas_identities = [
            (reference.scene_id, reference.scene_revision, reference.relation)
            for reference in self.canonical_canvas_references
        ]
        if len(canvas_identities) != len(set(canvas_identities)):
            raise ValueError("canonicalCanvasReferences cannot contain duplicates")
        return self


def _canonicalize_capture_dialogue_frame(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    data = dict(data)
    for field, max_items in _DIALOGUE_FRAME_TEXT_COLLECTION_LIMITS.items():
        camel = _to_camel(field)
        for key in (camel, field):
            if key in data:
                val = data[key]
                if val is None:
                    data[key] = []
                elif isinstance(val, (list, tuple)):
                    retained: list[Any] = []
                    for item in val:
                        if isinstance(item, str):
                            stripped = item.strip()
                            if (
                                not stripped
                                or len(stripped) > MAX_DIALOGUE_FRAME_ITEM_CHARACTERS
                            ):
                                continue
                            retained.append(stripped)
                        else:
                            retained.append(item)
                    data[key] = retained[:max_items]

    for key in ("relevantCanvasReferences", "relevant_canvas_references"):
        if key in data:
            val = data[key]
            if val is None:
                data[key] = []
            elif isinstance(val, (list, tuple)):
                valid_refs: list[DialogueFrameCanvasReference] = []
                for item in data[key]:
                    if isinstance(item, DialogueFrameCanvasReference):
                        valid_refs.append(item)
                    elif isinstance(item, dict):
                        try:
                            valid_refs.append(
                                DialogueFrameCanvasReference.model_validate(item)
                            )
                        except Exception:
                            continue
                data[key] = valid_refs[:MAX_DIALOGUE_FRAME_CANVAS_REFERENCES]

    return data


class SemanticCaptureFunctionArguments(RealtimeProtocolModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        validate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )
    disposition: SemanticCaptureDisposition
    dialogue_frame: DialogueFrameState | None = None
    journal_events: tuple[SemanticCaptureJournalDraft, ...] = Field(
        default=(), max_length=MAX_SEMANTIC_JOURNAL_EVENTS_PER_PROPOSAL
    )

    @model_validator(mode="before")
    @classmethod
    def canonicalize_wire_arguments(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for key in ("dialogueFrame", "dialogue_frame"):
            if key in data and data[key] is not None:
                data[key] = _canonicalize_capture_dialogue_frame(data[key])
        for key in ("journalEvents", "journal_events"):
            if key in data and data[key] is None:
                data[key] = []
        return data

    @model_validator(mode="after")
    def arguments_are_valid(self) -> SemanticCaptureFunctionArguments:
        if self.disposition == SemanticCaptureDisposition.NO_CHANGE:
            if self.dialogue_frame is not None or self.journal_events:
                raise ValueError(
                    "noChange capture disposition forbids dialogueFrame and journalEvents"
                )
        else:
            if self.dialogue_frame is None and not self.journal_events:
                raise ValueError(
                    "changed capture disposition requires dialogueFrame or journalEvents"
                )
        identities = [
            semantic_journal_identity_key(event.family, event.semantic_key)
            for event in self.journal_events
        ]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "journalEvents cannot contain duplicate semantic identities"
            )
        return self


class SemanticMemoryProposal(RealtimeProtocolModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        validate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )
    disposition: SemanticCaptureDisposition
    dialogue_frame_update: DialogueFrameUpdate | None = None
    journal_events: tuple[SemanticJournalEventProposal, ...] = Field(
        default=(), max_length=MAX_SEMANTIC_JOURNAL_EVENTS_PER_PROPOSAL
    )

    @model_validator(mode="after")
    def proposal_is_bounded_and_unambiguous(self) -> SemanticMemoryProposal:
        if self.disposition == SemanticCaptureDisposition.NO_CHANGE:
            if self.dialogue_frame_update is not None or self.journal_events:
                raise ValueError(
                    "noChange semantic proposal cannot contain frame updates or journal events"
                )
        else:
            if self.dialogue_frame_update is None and not self.journal_events:
                raise ValueError(
                    "changed semantic memory proposal requires a DialogueFrame update or "
                    "Journal event"
                )
        identities = [
            semantic_journal_identity_key(event.family, event.semantic_key)
            for event in self.journal_events
        ]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "journalEvents cannot contain duplicate semantic identities"
            )
        byte_count = len(_canonical_model_json(self).encode("utf-8"))
        if byte_count > MAX_SEMANTIC_MEMORY_PROPOSAL_UTF8_BYTES:
            raise ValueError(
                "semantic memory proposal exceeds "
                f"{MAX_SEMANTIC_MEMORY_PROPOSAL_UTF8_BYTES} UTF-8 bytes"
            )
        return self


class SemanticJournalEventRecord(RealtimeProtocolModel):
    event_id: ProtocolIdentifier
    subject: Annotated[NonEmptyText, StringConstraints(max_length=256)]
    reset_revision: int = Field(ge=0)
    family: SemanticJournalEventFamily
    semantic_key: SemanticJournalKey
    summary: SemanticJournalSummary
    identity_key: Annotated[NonEmptyText, StringConstraints(max_length=128)]
    fingerprint: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    canonical_canvas_references: tuple[SemanticJournalCanvasReference, ...] = Field(
        default=(), max_length=MAX_SEMANTIC_JOURNAL_CANVAS_REFERENCES
    )
    created_at: datetime

    @model_validator(mode="after")
    def record_is_coherent(self) -> SemanticJournalEventRecord:
        _validate_subject(self.subject)
        if self.created_at.tzinfo is None:
            raise ValueError("semantic Journal createdAt must be timezone-aware")
        expected_identity = semantic_journal_identity_key(
            self.family, self.semantic_key
        )
        if self.identity_key != expected_identity:
            raise ValueError("semantic Journal identityKey is not canonical")
        return self


class SemanticJournalEventDisposition(StrEnum):
    CREATED = "created"
    DEDUPLICATED = "deduplicated"


class SemanticJournalEventResult(RealtimeProtocolModel):
    disposition: SemanticJournalEventDisposition
    event: SemanticJournalEventRecord


class SemanticMemoryCommitStatus(StrEnum):
    APPLIED = "applied"
    IDEMPOTENT = "idempotent"
    STALE = "stale"


class SemanticMemoryCommitDecision(RealtimeProtocolModel):
    status: SemanticMemoryCommitStatus
    dialogue_frame: DialogueFrame
    journal_results: tuple[SemanticJournalEventResult, ...] = Field(
        max_length=MAX_SEMANTIC_JOURNAL_EVENTS_PER_PROPOSAL
    )


def _semantic_memory_decision(
    status: SemanticMemoryCommitStatus,
    frame: DialogueFrame,
    results: Sequence[SemanticJournalEventResult],
) -> SemanticMemoryCommitDecision:
    return SemanticMemoryCommitDecision.model_validate(
        {
            "status": status,
            "dialogueFrame": frame.model_dump(mode="json", by_alias=True),
            "journalResults": [
                result.model_dump(mode="json", by_alias=True) for result in results
            ],
        }
    )


def canonical_dialogue_frame_update_fingerprint(
    update: DialogueFrameUpdate,
) -> str:
    validated = DialogueFrameUpdate.model_validate(
        update.model_dump(mode="json", by_alias=True)
    )
    payload = json.dumps(
        validated.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def semantic_journal_identity_key(
    family: SemanticJournalEventFamily | str,
    semantic_key: str,
) -> str:
    """Derive identity only from the authority-supplied typed family and key."""

    family_value = str(family)
    if family_value not in {member.value for member in SemanticJournalEventFamily}:
        raise ValueError("semantic Journal family is invalid")
    if not re.fullmatch(r"[a-z][a-z0-9._:-]{0,95}", semantic_key):
        raise ValueError("semantic Journal semanticKey is invalid")
    return f"{family_value}:{semantic_key}"


def canonical_semantic_journal_event_fingerprint(
    event: SemanticJournalEventProposal,
) -> str:
    """Hash immutable event content; supersession lineage is deliberately excluded."""

    validated = SemanticJournalEventProposal.model_validate(
        event.model_dump(mode="json", by_alias=True)
    )
    payload = {
        "canonicalCanvasReferences": [
            reference.model_dump(mode="json", by_alias=True)
            for reference in validated.canonical_canvas_references
        ],
        "family": validated.family,
        "semanticKey": validated.semantic_key,
        "summary": validated.summary,
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def canonical_semantic_memory_proposal_fingerprint(
    proposal: SemanticMemoryProposal,
) -> str:
    """Hash the whole proposal, including frame CAS and supersession lineage."""

    validated = SemanticMemoryProposal.model_validate(
        proposal.model_dump(mode="json", by_alias=True)
    )
    return hashlib.sha256(
        _canonical_json_bytes(validated.model_dump(mode="json", by_alias=True))
    ).hexdigest()


def _validate_semantic_journal_identity_key(identity_key: str) -> str:
    family, separator, semantic_key = identity_key.partition(":")
    if (
        not separator
        or semantic_journal_identity_key(family, semantic_key) != identity_key
    ):
        raise ValueError("semantic Journal identity key is invalid")
    return identity_key


def semantic_journal_created_event_id(
    *,
    subject: str,
    reset_revision: int,
    source_turn_id: str,
    identity_key: str,
    event_fingerprint: str,
) -> str:
    """Return a deterministic backend-owned ID for one created occurrence."""

    normalized_subject = _validate_subject(subject)
    validated_revision = _validate_reset_revision(
        reset_revision, field_name="reset_revision"
    )
    validated_source_turn_id = _validate_protocol_identifier(
        source_turn_id, field_name="source_turn_id"
    )
    _validate_semantic_journal_identity_key(identity_key)
    if not re.fullmatch(r"[0-9a-f]{64}", event_fingerprint):
        raise ValueError("semantic Journal event fingerprint is invalid")
    digest = hashlib.sha256(
        _canonical_json_bytes(
            {
                "eventFingerprint": event_fingerprint,
                "identityKey": identity_key,
                "resetRevision": validated_revision,
                "sourceTurnId": validated_source_turn_id,
                "subject": normalized_subject,
            }
        )
    ).hexdigest()
    return f"sje-{digest[:60]}"


def _canonical_model_json(model: RealtimeProtocolModel) -> str:
    return _canonical_json_bytes(model.model_dump(mode="json", by_alias=True)).decode(
        "utf-8"
    )


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_dialogue_frame_state_json(frame: DialogueFrameState) -> str:
    payload = {
        alias: getattr(frame, field_name)
        for field_name, alias in _DIALOGUE_FRAME_STATE_FIELDS
    }
    return json.dumps(
        payload,
        default=lambda value: value.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


_DIALOGUE_FRAME_STATE_FIELDS = (
    ("active_topics", "activeTopics"),
    ("active_goals", "activeGoals"),
    ("unresolved_questions", "unresolvedQuestions"),
    ("current_referents", "currentReferents"),
    ("recent_entities", "recentEntities"),
    ("ava_commitments", "avaCommitments"),
    ("explicit_user_preferences", "explicitUserPreferences"),
    ("temporary_interaction_preferences", "temporaryInteractionPreferences"),
    ("relevant_canvas_references", "relevantCanvasReferences"),
    ("recent_tool_references", "recentToolReferences"),
)


def _reject_control_characters(value: str) -> None:
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError(
            "DialogueFrame semantic text cannot contain control characters"
        )


class AuthoritativeMemorySnapshot(RealtimeProtocolModel):
    age_band: AgeBand
    reset_revision: int = Field(ge=0)
    dialogue_frame: DialogueFrame = Field(default_factory=DialogueFrame)
    current_session_summary: (
        Annotated[str, StringConstraints(min_length=1, max_length=1_200)] | None
    )
    delivered_turns: list[StoredDeliveredConversationTurn] = Field(
        max_length=MAX_SNAPSHOT_DELIVERED_TURNS
    )
    stable_memories: list[StableMemoryRecord] = Field(max_length=MAX_STABLE_MEMORIES)

    @model_validator(mode="after")
    def records_are_unique(self) -> AuthoritativeMemorySnapshot:
        turn_ids = [turn.turn_id for turn in self.delivered_turns]
        if len(turn_ids) != len(set(turn_ids)):
            raise ValueError("delivered turn IDs must be unique")
        sequences = [turn.turn_sequence for turn in self.delivered_turns]
        if any(left >= right for left, right in zip(sequences, sequences[1:])):
            raise ValueError("delivered turns must be in strict logical sequence order")
        if any(
            turn.reset_revision != self.reset_revision for turn in self.delivered_turns
        ):
            raise ValueError("delivered turns must belong to the snapshot reset epoch")
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
        occurred_at: datetime | None = None,
        timezone_id: str = "UTC",
    ) -> None: ...

    async def apply_dialogue_frame_update(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        source_turn_id: str,
        update: DialogueFrameUpdate,
    ) -> DialogueFrameUpdateDecision: ...

    async def apply_semantic_memory_proposal(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        source_turn_id: str,
        proposal: SemanticMemoryProposal,
    ) -> SemanticMemoryCommitDecision: ...

    async def load_semantic_memory_receipt(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        source_turn_id: str,
    ) -> SemanticMemoryReceipt | None: ...

    async def get_active_semantic_journal_event(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        identity_key: str,
    ) -> SemanticJournalEventRecord | None: ...

    async def search_memory_candidates(
        self,
        *,
        subject: str,
        query: str,
        limit: int = 5,
        from_instant: datetime | None = None,
        to_instant: datetime | None = None,
        order: MemorySearchOrder = MemorySearchOrder.BEST_MATCH,
    ) -> list[MemorySearchCandidate]: ...

    async def load_weekly_recap(
        self,
        *,
        subject: str,
        timezone_id: str,
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
    turn: StoredDeliveredConversationTurn


@dataclass(frozen=True, slots=True)
class SemanticMemoryReceipt:
    source_turn_id: str
    disposition: SemanticCaptureDisposition
    proposal_fingerprint: str
    event_results: tuple[tuple[str, SemanticJournalEventDisposition], ...]
    resulting_frame_revision: int
    applied_at: datetime


async def resolve_semantic_journal_proposals(
    *,
    store: AuthoritativeMemoryStore,
    subject: str,
    reset_revision: int,
    drafts: Sequence[SemanticCaptureJournalDraft],
) -> tuple[SemanticJournalEventProposal, ...]:
    proposals: list[SemanticJournalEventProposal] = []
    for draft in drafts:
        identity_key = semantic_journal_identity_key(draft.family, draft.semantic_key)
        active_record = await store.get_active_semantic_journal_event(
            subject=subject,
            expected_reset_revision=reset_revision,
            identity_key=identity_key,
        )
        if active_record is None:
            supersedes_event_ids: tuple[str, ...] = ()
        else:
            tentative_proposal = SemanticJournalEventProposal.model_validate(
                {
                    "family": draft.family,
                    "semanticKey": draft.semantic_key,
                    "summary": draft.summary,
                    "canonicalCanvasReferences": [
                        ref.model_dump(mode="json", by_alias=True)
                        for ref in draft.canonical_canvas_references
                    ],
                    "supersedesEventIds": (),
                }
            )
            cand_fp = canonical_semantic_journal_event_fingerprint(tentative_proposal)
            if active_record.fingerprint == cand_fp:
                supersedes_event_ids = ()
            else:
                supersedes_event_ids = (active_record.event_id,)
        proposals.append(
            SemanticJournalEventProposal.model_validate(
                {
                    "family": draft.family,
                    "semanticKey": draft.semantic_key,
                    "summary": draft.summary,
                    "canonicalCanvasReferences": [
                        ref.model_dump(mode="json", by_alias=True)
                        for ref in draft.canonical_canvas_references
                    ],
                    "supersedesEventIds": list(supersedes_event_ids),
                }
            )
        )
    return tuple(proposals)


@dataclass(frozen=True, slots=True)
class _PreparedSemanticJournalEvent:
    proposal: SemanticJournalEventProposal
    record: SemanticJournalEventRecord
    disposition: SemanticJournalEventDisposition


def _prepare_semantic_journal_batch(
    *,
    subject: str,
    reset_revision: int,
    source_turn_id: str,
    proposals: Sequence[SemanticJournalEventProposal],
    events: dict[str, SemanticJournalEventRecord],
    superseded_event_ids: set[str],
    event_subjects: dict[str, str],
    created_at: datetime,
) -> tuple[_PreparedSemanticJournalEvent, ...]:
    """Prevalidate a complete Journal batch without mutating durable state."""

    active_by_identity: dict[str, SemanticJournalEventRecord] = {}
    for record in events.values():
        if record.event_id in superseded_event_ids:
            continue
        if record.identity_key in active_by_identity:
            raise SemanticJournalConflictError(
                "semantic Journal contains multiple active events for one identity"
            )
        active_by_identity[record.identity_key] = record
    claimed_targets: set[str] = set()
    prepared: list[_PreparedSemanticJournalEvent] = []
    for event_proposal in proposals:
        identity_key = semantic_journal_identity_key(
            event_proposal.family, event_proposal.semantic_key
        )
        fingerprint = canonical_semantic_journal_event_fingerprint(event_proposal)
        active = active_by_identity.get(identity_key)
        if active is not None and active.fingerprint == fingerprint:
            disposition = SemanticJournalEventDisposition.DEDUPLICATED
            record = active
        else:
            disposition = SemanticJournalEventDisposition.CREATED
            event_id = semantic_journal_created_event_id(
                subject=subject,
                reset_revision=reset_revision,
                source_turn_id=source_turn_id,
                identity_key=identity_key,
                event_fingerprint=fingerprint,
            )
            if event_id in events:
                raise SemanticJournalConflictError(
                    "created semantic Journal occurrence ID already exists"
                )
            record = SemanticJournalEventRecord.model_validate(
                {
                    "eventId": event_id,
                    "subject": subject,
                    "resetRevision": reset_revision,
                    "family": event_proposal.family,
                    "semanticKey": event_proposal.semantic_key,
                    "summary": event_proposal.summary,
                    "identityKey": identity_key,
                    "fingerprint": fingerprint,
                    "canonicalCanvasReferences": [
                        reference.model_dump(mode="json", by_alias=True)
                        for reference in event_proposal.canonical_canvas_references
                    ],
                    "createdAt": created_at,
                }
            )
        if disposition == SemanticJournalEventDisposition.DEDUPLICATED and (
            event_proposal.supersedes_event_ids
        ):
            raise SemanticJournalConflictError(
                "a deduplicated event cannot add supersession lineage"
            )
        for target_id in event_proposal.supersedes_event_ids:
            if target_id == record.event_id:
                raise SemanticJournalConflictError(
                    "semantic Journal event cannot supersede itself"
                )
            if target_id in claimed_targets:
                raise SemanticJournalConflictError(
                    "one active event cannot be superseded twice in a proposal"
                )
            target = events.get(target_id)
            if target is None:
                owner = event_subjects.get(target_id)
                reason = (
                    "belongs to another subject"
                    if owner is not None and owner != subject
                    else "does not exist in the current reset revision"
                )
                raise SemanticJournalConflictError(
                    f"superseded semantic Journal event {reason}"
                )
            if target.reset_revision != reset_revision:
                raise SemanticJournalConflictError(
                    "superseded semantic Journal event belongs to another reset "
                    "revision"
                )
            if target_id in superseded_event_ids:
                raise SemanticJournalConflictError(
                    "superseded semantic Journal event is not active"
                )
            claimed_targets.add(target_id)
        if active is not None and active.fingerprint != fingerprint:
            if active.event_id not in event_proposal.supersedes_event_ids:
                raise SemanticJournalConflictError(
                    "active semantic identity has different content and requires "
                    "explicit supersession"
                )
        prepared.append(
            _PreparedSemanticJournalEvent(
                proposal=event_proposal,
                record=record,
                disposition=disposition,
            )
        )
    asserted_event_ids = {item.record.event_id for item in prepared}
    superseded_in_proposal = {
        target_id
        for item in prepared
        for target_id in item.proposal.supersedes_event_ids
    }
    if asserted_event_ids.intersection(superseded_in_proposal):
        raise SemanticJournalConflictError(
            "one proposal cannot both assert and supersede the same semantic "
            "Journal event"
        )
    return tuple(prepared)


class DevelopmentAuthoritativeMemoryStore:
    """Deterministic development store for realtime coordinator tests."""

    def __init__(self) -> None:
        self._turns: defaultdict[str, list[StoredDeliveredConversationEvent]] = (
            defaultdict(list)
        )
        self._episodes: defaultdict[str, list[ConversationEpisode]] = defaultdict(list)
        self._committed_turn_ids: defaultdict[str, set[str]] = defaultdict(set)
        self._reset_revisions: defaultdict[str, int] = defaultdict(int)
        self._dialogue_frames: dict[str, DialogueFrame] = {}
        self._dialogue_frame_receipts: defaultdict[str, dict[str, tuple[str, int]]] = (
            defaultdict(dict)
        )
        self._semantic_journal_events: defaultdict[
            str, dict[str, SemanticJournalEventRecord]
        ] = defaultdict(dict)
        self._semantic_journal_sources: defaultdict[str, defaultdict[str, set[str]]] = (
            defaultdict(lambda: defaultdict(set))
        )
        self._semantic_journal_supersessions: defaultdict[
            str, defaultdict[str, set[str]]
        ] = defaultdict(lambda: defaultdict(set))
        self._semantic_memory_receipts: defaultdict[
            str, dict[str, SemanticMemoryReceipt]
        ] = defaultdict(dict)
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
                    "dialogueFrame": self._dialogue_frames.get(
                        normalized_subject, DialogueFrame()
                    ).model_dump(mode="json", by_alias=True),
                    "currentSessionSummary": None,
                    "deliveredTurns": [
                        event.turn.model_dump(mode="json", by_alias=True)
                        for event in self._turns.get(normalized_subject, ())[
                            -MAX_SNAPSHOT_DELIVERED_TURNS:
                        ]
                    ],
                    "stableMemories": [],
                }
            )

    async def commit_delivered_turn(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        turn: DeliveredConversationTurn,
        occurred_at: datetime | None = None,
        timezone_id: str = "UTC",
    ) -> None:
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
        _validate_weekly_summary(validated_turn.child_turn_summary)

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
            turns = self._turns[normalized_subject]
            turns.append(
                StoredDeliveredConversationEvent(
                    occurred_at=normalized_occurred_at,
                    timezone_id=validated_timezone_id,
                    turn=StoredDeliveredConversationTurn(
                        **validated_turn.model_dump(by_alias=True),
                        resetRevision=current_reset_revision,
                        turnSequence=len(turns) + 1,
                        occurredAt=normalized_occurred_at,
                        timezoneId=validated_timezone_id,
                    ),
                )
            )
            self._committed_turn_ids[normalized_subject].add(validated_turn.turn_id)
        return None

    async def apply_dialogue_frame_update(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        source_turn_id: str,
        update: DialogueFrameUpdate,
    ) -> DialogueFrameUpdateDecision:
        validated_update = DialogueFrameUpdate.model_validate(
            update.model_dump(mode="json", by_alias=True)
        )
        proposal = SemanticMemoryProposal.model_validate(
            {
                "disposition": "changed",
                "dialogueFrameUpdate": validated_update.model_dump(
                    mode="json", by_alias=True
                ),
            }
        )
        try:
            decision = await self._apply_semantic_memory_proposal(
                subject=subject,
                expected_reset_revision=expected_reset_revision,
                source_turn_id=source_turn_id,
                proposal=proposal,
                allow_m1_frame_retry=True,
            )
        except SemanticMemorySourceTurnMissingError as error:
            raise DialogueFrameSourceTurnMissingError(str(error)) from error
        except SemanticMemoryProposalConflictError as error:
            raise DialogueFrameUpdateConflictError(str(error)) from error
        return DialogueFrameUpdateDecision.model_validate(
            {
                "status": decision.status,
                "frame": decision.dialogue_frame.model_dump(mode="json", by_alias=True),
            }
        )

    async def apply_semantic_memory_proposal(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        source_turn_id: str,
        proposal: SemanticMemoryProposal,
    ) -> SemanticMemoryCommitDecision:
        return await self._apply_semantic_memory_proposal(
            subject=subject,
            expected_reset_revision=expected_reset_revision,
            source_turn_id=source_turn_id,
            proposal=proposal,
            allow_m1_frame_retry=False,
        )

    async def load_semantic_memory_receipt(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        source_turn_id: str,
    ) -> SemanticMemoryReceipt | None:
        normalized_subject = _validate_subject(subject)
        validated_expected_reset_revision = _validate_reset_revision(
            expected_reset_revision,
            field_name="expected_reset_revision",
        )
        validated_source_turn_id = _validate_protocol_identifier(
            source_turn_id, field_name="source_turn_id"
        )
        async with self._lock:
            current_reset_revision = self._reset_revisions[normalized_subject]
            if current_reset_revision != validated_expected_reset_revision:
                raise MemoryResetRevisionMismatchError(
                    expected_reset_revision=validated_expected_reset_revision,
                    current_reset_revision=current_reset_revision,
                )
            receipt = self._semantic_memory_receipts[normalized_subject].get(
                validated_source_turn_id
            )
            if receipt is not None:
                return receipt
            legacy_frame_receipt = self._dialogue_frame_receipts[
                normalized_subject
            ].get(validated_source_turn_id)
            if legacy_frame_receipt is not None:
                return SemanticMemoryReceipt(
                    source_turn_id=validated_source_turn_id,
                    disposition=SemanticCaptureDisposition.CHANGED,
                    proposal_fingerprint=legacy_frame_receipt[0],
                    event_results=(),
                    resulting_frame_revision=legacy_frame_receipt[1],
                    applied_at=datetime.now(UTC),
                )
            return None

    async def get_active_semantic_journal_event(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        identity_key: str,
    ) -> SemanticJournalEventRecord | None:
        normalized_subject = _validate_subject(subject)
        validated_expected_reset_revision = _validate_reset_revision(
            expected_reset_revision,
            field_name="expected_reset_revision",
        )
        validated_identity_key = _validate_semantic_journal_identity_key(identity_key)
        async with self._lock:
            current_reset_revision = self._reset_revisions[normalized_subject]
            if current_reset_revision != validated_expected_reset_revision:
                raise MemoryResetRevisionMismatchError(
                    expected_reset_revision=validated_expected_reset_revision,
                    current_reset_revision=current_reset_revision,
                )
            superseded_ids = set(
                self._semantic_journal_supersessions[normalized_subject]
            )
            for record in self._semantic_journal_events[normalized_subject].values():
                if (
                    record.identity_key == validated_identity_key
                    and record.event_id not in superseded_ids
                ):
                    return record
            return None

    async def _apply_semantic_memory_proposal(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        source_turn_id: str,
        proposal: SemanticMemoryProposal,
        allow_m1_frame_retry: bool,
    ) -> SemanticMemoryCommitDecision:
        normalized_subject = _validate_subject(subject)
        validated_expected_reset_revision = _validate_reset_revision(
            expected_reset_revision,
            field_name="expected_reset_revision",
        )
        validated_source_turn_id = _validate_protocol_identifier(
            source_turn_id, field_name="source_turn_id"
        )
        validated_proposal = SemanticMemoryProposal.model_validate(
            proposal.model_dump(mode="json", by_alias=True)
        )
        proposal_fingerprint = canonical_semantic_memory_proposal_fingerprint(
            validated_proposal
        )

        async with self._lock:
            current_reset_revision = self._reset_revisions[normalized_subject]
            if current_reset_revision != validated_expected_reset_revision:
                raise MemoryResetRevisionMismatchError(
                    expected_reset_revision=validated_expected_reset_revision,
                    current_reset_revision=current_reset_revision,
                )
            if (
                validated_source_turn_id
                not in self._committed_turn_ids[normalized_subject]
            ):
                raise SemanticMemorySourceTurnMissingError(
                    "semantic memory source turn is not durably committed"
                )
            current_frame = self._dialogue_frames.get(
                normalized_subject, DialogueFrame()
            )
            receipt = self._semantic_memory_receipts[normalized_subject].get(
                validated_source_turn_id
            )
            if receipt is not None:
                if receipt.proposal_fingerprint != proposal_fingerprint:
                    frame_update = validated_proposal.dialogue_frame_update
                    frame_receipt = self._dialogue_frame_receipts[
                        normalized_subject
                    ].get(validated_source_turn_id)
                    is_exact_m1_retry = (
                        allow_m1_frame_retry
                        and frame_update is not None
                        and not validated_proposal.journal_events
                        and frame_receipt is not None
                        and frame_receipt[0]
                        == canonical_dialogue_frame_update_fingerprint(frame_update)
                    )
                    if not is_exact_m1_retry:
                        raise SemanticMemoryProposalConflictError(
                            "source turn already applied a different semantic memory "
                            "proposal"
                        )
                    return _semantic_memory_decision(
                        SemanticMemoryCommitStatus.IDEMPOTENT,
                        current_frame,
                        (),
                    )
                return self._semantic_memory_decision_from_receipt(
                    subject=normalized_subject,
                    receipt=receipt,
                    frame=current_frame,
                )

            frame_update = validated_proposal.dialogue_frame_update
            legacy_frame_receipt = self._dialogue_frame_receipts[
                normalized_subject
            ].get(validated_source_turn_id)
            if legacy_frame_receipt is not None:
                frame_fingerprint = (
                    canonical_dialogue_frame_update_fingerprint(frame_update)
                    if frame_update is not None
                    else None
                )
                if (
                    frame_fingerprint != legacy_frame_receipt[0]
                    or validated_proposal.journal_events
                ):
                    raise SemanticMemoryProposalConflictError(
                        "source turn already applied a different DialogueFrame update"
                    )
                migrated_receipt = SemanticMemoryReceipt(
                    source_turn_id=validated_source_turn_id,
                    disposition=SemanticCaptureDisposition.CHANGED,
                    proposal_fingerprint=proposal_fingerprint,
                    event_results=(),
                    resulting_frame_revision=legacy_frame_receipt[1],
                    applied_at=datetime.now(UTC),
                )
                self._semantic_memory_receipts[normalized_subject][
                    validated_source_turn_id
                ] = migrated_receipt
                return self._semantic_memory_decision_from_receipt(
                    subject=normalized_subject,
                    receipt=migrated_receipt,
                    frame=current_frame,
                )

            if validated_proposal.disposition == SemanticCaptureDisposition.NO_CHANGE:
                semantic_receipt = SemanticMemoryReceipt(
                    source_turn_id=validated_source_turn_id,
                    disposition=SemanticCaptureDisposition.NO_CHANGE,
                    proposal_fingerprint=proposal_fingerprint,
                    event_results=(),
                    resulting_frame_revision=current_frame.revision,
                    applied_at=datetime.now(UTC),
                )
                self._semantic_memory_receipts[normalized_subject][
                    validated_source_turn_id
                ] = semantic_receipt
                return _semantic_memory_decision(
                    SemanticMemoryCommitStatus.APPLIED,
                    current_frame,
                    (),
                )

            if (
                frame_update is not None
                and frame_update.base_revision != current_frame.revision
            ):
                return _semantic_memory_decision(
                    SemanticMemoryCommitStatus.STALE, current_frame, ()
                )

            prepared = _prepare_semantic_journal_batch(
                subject=normalized_subject,
                reset_revision=current_reset_revision,
                source_turn_id=validated_source_turn_id,
                proposals=validated_proposal.journal_events,
                events=self._semantic_journal_events[normalized_subject],
                superseded_event_ids=set(
                    self._semantic_journal_supersessions[normalized_subject]
                ),
                event_subjects={
                    event_id: event_subject
                    for event_subject, subject_events in (
                        self._semantic_journal_events.items()
                    )
                    for event_id in subject_events
                },
                created_at=datetime.now(UTC),
            )
            replacement = current_frame
            if frame_update is not None:
                replacement = DialogueFrame.model_validate(
                    {
                        **frame_update.frame.model_dump(mode="json", by_alias=True),
                        "revision": current_frame.revision + 1,
                    }
                )

            # Every check above is side-effect free. Mutations below cannot fail.
            if frame_update is not None:
                self._dialogue_frames[normalized_subject] = replacement
                self._dialogue_frame_receipts[normalized_subject][
                    validated_source_turn_id
                ] = (
                    canonical_dialogue_frame_update_fingerprint(frame_update),
                    replacement.revision,
                )
            event_results: list[SemanticJournalEventResult] = []
            for item in prepared:
                record = item.record
                if item.disposition == SemanticJournalEventDisposition.CREATED:
                    self._semantic_journal_events[normalized_subject][
                        record.event_id
                    ] = record
                self._semantic_journal_sources[normalized_subject][record.event_id].add(
                    validated_source_turn_id
                )
                for target_id in item.proposal.supersedes_event_ids:
                    self._semantic_journal_supersessions[normalized_subject][
                        target_id
                    ].add(record.event_id)
                event_results.append(
                    SemanticJournalEventResult.model_validate(
                        {
                            "disposition": item.disposition,
                            "event": record.model_dump(mode="json", by_alias=True),
                        }
                    )
                )
            semantic_receipt = SemanticMemoryReceipt(
                source_turn_id=validated_source_turn_id,
                disposition=SemanticCaptureDisposition.CHANGED,
                proposal_fingerprint=proposal_fingerprint,
                event_results=tuple(
                    (result.event.event_id, result.disposition)
                    for result in event_results
                ),
                resulting_frame_revision=replacement.revision,
                applied_at=datetime.now(UTC),
            )
            self._semantic_memory_receipts[normalized_subject][
                validated_source_turn_id
            ] = semantic_receipt
            return _semantic_memory_decision(
                SemanticMemoryCommitStatus.APPLIED,
                replacement,
                event_results,
            )

    def _semantic_memory_decision_from_receipt(
        self,
        *,
        subject: str,
        receipt: SemanticMemoryReceipt,
        frame: DialogueFrame,
    ) -> SemanticMemoryCommitDecision:
        results = [
            SemanticJournalEventResult.model_validate(
                {
                    "disposition": disposition,
                    "event": self._semantic_journal_events[subject][
                        event_id
                    ].model_dump(mode="json", by_alias=True),
                }
            )
            for event_id, disposition in receipt.event_results
        ]
        return _semantic_memory_decision(
            SemanticMemoryCommitStatus.IDEMPOTENT, frame, results
        )

    async def search_memory_candidates(
        self,
        *,
        subject: str,
        query: str,
        limit: int = 5,
        from_instant: datetime | None = None,
        to_instant: datetime | None = None,
        order: MemorySearchOrder = MemorySearchOrder.BEST_MATCH,
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
                        reference_text=_matched_episode_excerpt(
                            episode.digest,
                            query_tokens,
                        ),
                        source_turn_ids=episode.source_turn_ids,
                        relevance=relevance,
                    )
                )

            current_reset_revision = self._reset_revisions.get(normalized_subject, 0)
            superseded_ids = set(
                self._semantic_journal_supersessions.get(normalized_subject, {})
            )
            for record in self._semantic_journal_events.get(
                normalized_subject, {}
            ).values():
                if record.reset_revision != current_reset_revision:
                    continue
                if record.event_id in superseded_ids:
                    continue
                relevance = _lexical_relevance(record.summary, query_tokens)
                if relevance <= 0:
                    continue
                sources_by_event = self._semantic_journal_sources.get(
                    normalized_subject
                )
                source_turn_ids: tuple[str, ...] = ()
                if sources_by_event is not None and record.event_id in sources_by_event:
                    source_turn_ids = tuple(sorted(sources_by_event[record.event_id]))
                provenance = [
                    value
                    for source_id in source_turn_ids
                    if (
                        value := _find_provenance_range(
                            source_turn_id=source_id,
                            raw_events=raw_events,
                            episodes=episodes,
                        )
                    )
                    is not None
                ]
                started = min((p[0] for p in provenance), default=record.created_at)
                ended = max((p[1] for p in provenance), default=record.created_at)
                zones = sorted({zone for p in provenance for zone in p[2]})
                if not _range_overlaps(started, ended, lower, upper):
                    continue
                results.append(
                    _memory_candidate(
                        source_id=record.event_id,
                        source_kind=MemoryCandidateSourceKind.SEMANTIC_JOURNAL,
                        started_at=started,
                        ended_at=ended,
                        timezone_ids=zones,
                        reference_text=record.summary,
                        source_turn_ids=source_turn_ids,
                        relevance=relevance,
                    )
                )

        return select_memory_candidates(results, limit=validated_limit, order=order)

    async def load_weekly_recap(
        self,
        *,
        subject: str,
        timezone_id: str,
        generated_at: datetime,
    ) -> WeeklyConversationRecap:
        normalized_subject = _validate_subject(subject)
        period_start, period_end, lower_bound_utc, upper_bound_utc = (
            current_local_week_utc_bounds(
                timezone_id=timezone_id,
                generated_at=generated_at,
            )
        )
        normalized_generated_at = _validate_aware_datetime(
            generated_at, field_name="generated_at"
        )
        async with self._lock:
            events = sorted(
                (
                    event
                    for event in self._turns.get(normalized_subject, ())
                    if lower_bound_utc <= event.occurred_at < upper_bound_utc
                ),
                key=lambda event: (event.occurred_at, event.turn.turn_id),
                reverse=True,
            )[:MAX_WEEKLY_RECORDS]
            return WeeklyConversationRecap.model_validate(
                {
                    "periodStart": period_start,
                    "periodEnd": period_end,
                    "generatedAt": normalized_generated_at,
                    "resetRevision": self._reset_revisions[normalized_subject],
                    "records": [
                        WeeklyConversationRecord.model_validate(
                            {
                                "turnId": event.turn.turn_id,
                                "summary": event.turn.child_turn_summary,
                                "occurredAt": event.occurred_at,
                            }
                        ).model_dump(mode="json", by_alias=True)
                        for event in events
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
            self._committed_turn_ids.pop(normalized_subject, None)
            self._dialogue_frames.pop(normalized_subject, None)
            self._dialogue_frame_receipts.pop(normalized_subject, None)
            self._semantic_journal_events.pop(normalized_subject, None)
            self._semantic_journal_sources.pop(normalized_subject, None)
            self._semantic_journal_supersessions.pop(normalized_subject, None)
            self._semantic_memory_receipts.pop(normalized_subject, None)
            self._reset_revisions[normalized_subject] += 1
            return ConversationResetReceipt.model_validate(
                {
                    "revision": self._reset_revisions[normalized_subject],
                    "resetAt": datetime.now(UTC),
                }
            )


def current_local_week_utc_bounds(
    *,
    timezone_id: str,
    generated_at: datetime,
) -> tuple[date, date, datetime, datetime]:
    """Return the current local Monday-to-Monday window as UTC instants."""

    validated_timezone_id = validate_iana_timezone_id(timezone_id)
    normalized_generated_at = _validate_aware_datetime(
        generated_at,
        field_name="generated_at",
    )
    zone = ZoneInfo(validated_timezone_id)
    local_now = normalized_generated_at.astimezone(zone)
    period_start = local_now.date() - timedelta(days=local_now.weekday())
    period_end = period_start + timedelta(days=7)
    lower_bound_utc = datetime.combine(period_start, time.min, zone).astimezone(UTC)
    upper_bound_utc = datetime.combine(period_end, time.min, zone).astimezone(UTC)
    return period_start, period_end, lower_bound_utc, upper_bound_utc


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


MEMORY_SEARCH_CANDIDATE_POOL = 40


def select_memory_candidates(
    candidates: Sequence[MemorySearchCandidate],
    *,
    limit: int,
    order: MemorySearchOrder,
) -> list[MemorySearchCandidate]:
    """Bounded ranked selection using canonical provenance, never text similarity."""
    limit = _validate_search_limit(limit)
    order = MemorySearchOrder(order)

    def rank(candidate: MemorySearchCandidate) -> tuple[float, float, str, str]:
        instant = (
            candidate.ended_at
            if order != MemorySearchOrder.OLDEST
            else candidate.started_at
        )
        timestamp = instant.timestamp() if instant is not None else 0.0
        if order == MemorySearchOrder.NEWEST:
            return (
                -timestamp,
                -candidate.relevance,
                candidate.source_id,
                str(candidate.source_kind),
            )
        if order == MemorySearchOrder.OLDEST:
            return (
                timestamp,
                -candidate.relevance,
                candidate.source_id,
                str(candidate.source_kind),
            )
        return (
            -candidate.relevance,
            -timestamp,
            candidate.source_id,
            str(candidate.source_kind),
        )

    represented: set[str] = set()
    identities: set[tuple[str, str]] = set()
    selected: list[MemorySearchCandidate] = []
    for candidate in sorted(candidates, key=rank)[:MEMORY_SEARCH_CANDIDATE_POOL]:
        provenance = set(candidate.source_turn_ids)
        identity = (str(candidate.source_kind), candidate.source_id)
        if provenance.intersection(represented) or identity in identities:
            continue
        selected.append(candidate)
        represented.update(provenance)
        identities.add(identity)
        if len(selected) == limit:
            break
    return selected


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


def _matched_episode_excerpt(
    digest: str,
    query_tokens: Sequence[str],
    *,
    max_characters: int = 1_200,
) -> str:
    """Return chronological matching digest lines, never a blind prefix."""

    if not 1 <= max_characters <= 1_200:
        raise ValueError("episode excerpt limit must be between 1 and 1200")
    matched_lines = [
        _normalize_whitespace(line)
        for line in digest.splitlines()
        if _normalize_whitespace(line) and _lexical_relevance(line, query_tokens) > 0
    ]
    if not matched_lines:
        raise ValueError("matched episode did not contain matching semantic lines")
    excerpt = ""
    for line in matched_lines:
        candidate = line if not excerpt else f"{excerpt}\n{line}"
        if len(candidate) <= max_characters:
            excerpt = candidate
            continue
        if not excerpt:
            excerpt = _bounded_search_text(line, max_characters)
        break
    return excerpt


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
            "timezoneIds": sorted(set(timezone_ids))[:MAX_EPISODE_SOURCE_EVENTS],
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


def _decision(accepted: bool, reason: str) -> MemoryProposalDecision:
    return MemoryProposalDecision.model_validate(
        {"accepted": accepted, "reason": reason, "record": None}
    )


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


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


def _validate_protocol_identifier(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9._:-]{0,63}", value
    ):
        raise ValueError(f"{field_name} must be a protocol identifier")
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
