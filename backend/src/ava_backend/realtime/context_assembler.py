from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from html import escape
from math import ceil
from zoneinfo import ZoneInfo

from pydantic import Field, StringConstraints, model_validator
from typing_extensions import Annotated

from ..protocol import AgeBand
from .common import NonEmptyText, ProtocolIdentifier, RealtimeProtocolModel
from .memory import (
    DialogueFrame,
    DeliveredConversationTurn,
    DeliveredInputKind,
    MAX_EPISODE_SOURCE_EVENTS,
    MemoryCandidateSourceKind,
    MemorySearchCandidate,
)
from .timezone import MAX_TIMEZONE_ID_CHARACTERS, validate_iana_timezone_id


MAX_DYNAMIC_CONTEXT_UTF8_BYTES = 20_000
MAX_CANVAS_CONTEXT_CHARACTERS = 2_400


class DynamicContextBuildReason(StrEnum):
    SESSION_BOOTSTRAP = "sessionBootstrap"
    SESSION_RECONNECT = "sessionReconnect"
    TURN_COMMITTED = "turnCommitted"
    CANVAS_ACTIVATED = "canvasActivated"
    CANVAS_FOCUSED = "canvasFocused"
    CANVAS_INTERACTION_COMMITTED = "canvasInteractionCommitted"
    CANVAS_PRESENTATION_CHANGED = "canvasPresentationChanged"
    MEMORY_COMPACTED = "memoryCompacted"


class DynamicContextContractError(RuntimeError):
    """Required current state cannot satisfy the dynamic-context contract."""


class MemoryReference(RealtimeProtocolModel):
    """Bounded model-facing historical evidence without store internals."""

    source_kind: MemoryCandidateSourceKind
    started_at: datetime | None
    ended_at: datetime | None
    timezone_ids: list[
        Annotated[
            NonEmptyText,
            StringConstraints(max_length=MAX_TIMEZONE_ID_CHARACTERS),
        ]
    ] = Field(max_length=MAX_EPISODE_SOURCE_EVENTS)
    text: Annotated[NonEmptyText, StringConstraints(max_length=1_200)]
    source_turn_ids: list[ProtocolIdentifier] = Field(
        max_length=MAX_EPISODE_SOURCE_EVENTS
    )

    @model_validator(mode="after")
    def reference_is_coherent(self) -> MemoryReference:
        if (self.started_at is None) != (self.ended_at is None):
            raise ValueError("memory reference time range must be complete")
        if self.started_at is not None:
            if self.started_at.tzinfo is None or self.ended_at is None:
                raise ValueError("memory reference times must be timezone-aware")
            if self.ended_at.tzinfo is None or self.ended_at < self.started_at:
                raise ValueError("memory reference time range is invalid")
        for timezone_id in self.timezone_ids:
            validate_iana_timezone_id(timezone_id)
        if len(self.source_turn_ids) != len(set(self.source_turn_ids)):
            raise ValueError("memory reference source turn IDs must be unique")
        return self

    @classmethod
    def from_candidate(cls, candidate: MemorySearchCandidate) -> MemoryReference:
        return cls.model_validate(
            {
                "sourceKind": candidate.source_kind,
                "startedAt": candidate.started_at,
                "endedAt": candidate.ended_at,
                "timezoneIds": candidate.timezone_ids,
                "text": candidate.reference_text,
                "sourceTurnIds": candidate.source_turn_ids,
            }
        )


@dataclass(frozen=True, slots=True)
class AssembledRealtimeContext:
    build_id: str
    content: str
    content_fingerprint: str
    final_utf8_bytes: int
    estimated_dynamic_tokens: int
    included_bootstrap_turns: int
    omitted_bootstrap_turns: int
    included_memory_references: int
    omitted_memory_references: int
    canvas_included: bool
    dialogue_frame_revision: int
    dialogue_frame_included: bool


class DynamicContextAssembler:
    """Deterministically serialize already-selected Realtime context sources."""

    def __init__(
        self,
        *,
        max_dynamic_utf8_bytes: int = MAX_DYNAMIC_CONTEXT_UTF8_BYTES,
    ) -> None:
        if (
            isinstance(max_dynamic_utf8_bytes, bool)
            or not isinstance(max_dynamic_utf8_bytes, int)
            or not 1 <= max_dynamic_utf8_bytes <= MAX_DYNAMIC_CONTEXT_UTF8_BYTES
        ):
            raise ValueError(
                "max_dynamic_utf8_bytes must be between 1 and "
                f"{MAX_DYNAMIC_CONTEXT_UTF8_BYTES}"
            )
        self._max_dynamic_utf8_bytes = max_dynamic_utf8_bytes

    def assemble(
        self,
        *,
        build_id: str,
        reason: DynamicContextBuildReason,
        age_band: AgeBand,
        timezone_id: str,
        current_time_utc: datetime,
        bootstrap_turns: tuple[DeliveredConversationTurn, ...] = (),
        warm_memories: tuple[MemoryReference, ...] = (),
        dialogue_frame: DialogueFrame | None = None,
        canvas_context: str | None = None,
        active_visual_present: bool = False,
    ) -> AssembledRealtimeContext:
        validated_build_id = _validate_context_build_id(build_id)
        validated_reason = DynamicContextBuildReason(reason)
        validated_age_band = AgeBand(age_band)
        validated_timezone_id = validate_iana_timezone_id(timezone_id)
        validated_now = _validate_aware_utc(current_time_utc)
        validated_dialogue_frame = DialogueFrame.model_validate(
            (dialogue_frame or DialogueFrame()).model_dump(mode="json", by_alias=True)
        )
        validated_canvas = _validate_canvas_context(canvas_context)
        if validated_canvas is None:
            validated_canvas = (
                '<canvas_context trust="backend_semantic_canvas_metadata">'
                '<canvas_presentation trust="validated_current_state" visible="false"/>'
                "</canvas_context>"
            )
        eligible_bootstrap = validated_reason in {
            DynamicContextBuildReason.SESSION_BOOTSTRAP,
            DynamicContextBuildReason.SESSION_RECONNECT,
        }

        speech_turns = tuple(
            turn
            for turn in bootstrap_turns
            if eligible_bootstrap and turn.input_kind == DeliveredInputKind.SPEECH
        )
        omitted_bootstrap = len(bootstrap_turns) - len(speech_turns)
        selected_references, deduplicated_references = _deduplicate_references(
            warm_memories,
            bootstrap_source_turn_ids={turn.turn_id for turn in speech_turns},
        )
        omitted_references = deduplicated_references

        mandatory = _serialize_context(
            age_band=validated_age_band,
            timezone_id=validated_timezone_id,
            current_time_utc=validated_now,
            bootstrap_turns=(),
            memory_references=(),
            dialogue_frame=validated_dialogue_frame,
            canvas_context=validated_canvas,
        )
        if _serialized_utf8_bytes(mandatory) > self._max_dynamic_utf8_bytes:
            raise DynamicContextContractError(
                "mandatory current dynamic context exceeds its UTF-8 byte contract"
            )

        retained_turns = list(speech_turns)
        retained_references = list(selected_references)
        while True:
            content = _serialize_context(
                age_band=validated_age_band,
                timezone_id=validated_timezone_id,
                current_time_utc=validated_now,
                bootstrap_turns=tuple(retained_turns),
                memory_references=tuple(retained_references),
                dialogue_frame=validated_dialogue_frame,
                canvas_context=validated_canvas,
            )
            serialized_utf8_bytes = _serialized_utf8_bytes(content)
            estimated_tokens = _estimate_dynamic_tokens(content)
            if serialized_utf8_bytes <= self._max_dynamic_utf8_bytes:
                break
            if retained_references:
                retained_references.pop()
                omitted_references += 1
                continue
            if retained_turns:
                retained_turns.pop(0)
                omitted_bootstrap += 1
                continue
            raise DynamicContextContractError(
                "mandatory current dynamic context exceeds its UTF-8 byte contract"
            )

        if not isinstance(active_visual_present, bool):
            raise ValueError("active_visual_present must be a boolean")

        content_bytes = content.encode("utf-8")
        return AssembledRealtimeContext(
            build_id=validated_build_id,
            content=content,
            content_fingerprint=sha256(content_bytes).hexdigest(),
            final_utf8_bytes=serialized_utf8_bytes,
            estimated_dynamic_tokens=estimated_tokens,
            included_bootstrap_turns=len(retained_turns),
            omitted_bootstrap_turns=omitted_bootstrap,
            included_memory_references=len(retained_references),
            omitted_memory_references=omitted_references,
            canvas_included=active_visual_present,
            dialogue_frame_revision=validated_dialogue_frame.revision,
            dialogue_frame_included=True,
        )


def _validate_context_build_id(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("ctx-"):
        raise ValueError("build_id must be a context build identifier")
    sequence = value.removeprefix("ctx-")
    if not sequence.isdigit() or sequence.startswith("0"):
        raise ValueError("build_id must be a context build identifier")
    return value


def _validate_aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("current_time_utc must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _validate_canvas_context(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_CANVAS_CONTEXT_CHARACTERS:
        raise ValueError("canvas_context is not bounded declared-trust context")
    if not value.startswith(
        '<canvas_context trust="backend_semantic_canvas_metadata"'
    ):
        raise ValueError("canvas_context is not bounded declared-trust context")
    return value


def _deduplicate_references(
    references: tuple[MemoryReference, ...],
    *,
    bootstrap_source_turn_ids: set[str],
) -> tuple[tuple[MemoryReference, ...], int]:
    selected: list[MemoryReference] = []
    seen_turn_ids = set(bootstrap_source_turn_ids)
    omitted = 0
    for reference in references:
        source_ids = set(reference.source_turn_ids)
        if source_ids and source_ids.intersection(seen_turn_ids):
            omitted += 1
            continue
        selected.append(reference)
        seen_turn_ids.update(source_ids)
    return tuple(selected), omitted


def _serialize_context(
    *,
    age_band: AgeBand,
    timezone_id: str,
    current_time_utc: datetime,
    bootstrap_turns: tuple[DeliveredConversationTurn, ...],
    memory_references: tuple[MemoryReference, ...],
    dialogue_frame: DialogueFrame,
    canvas_context: str | None,
) -> str:
    local_time = current_time_utc.astimezone(ZoneInfo(timezone_id))
    lines = [
        '<ava_dynamic_context trust="application_owned_context">',
        '  <current_environment trust="validated_current_state">',
        f"    <age_band>{escape(str(age_band), quote=False)}</age_band>",
        f"    <timezone_id>{escape(timezone_id, quote=False)}</timezone_id>",
        f"    <local_date>{local_time.date().isoformat()}</local_date>",
        f"    <weekday>{local_time.strftime('%A')}</weekday>",
        f"    <daypart>{_daypart(local_time.hour)}</daypart>",
        "  </current_environment>",
    ]
    lines.extend(_serialize_dialogue_frame(dialogue_frame))
    if bootstrap_turns:
        lines.append(
            '  <recent_delivered_turns trust="delivered_conversation_history">'
        )
        for turn in bootstrap_turns:
            if turn.input_kind != DeliveredInputKind.SPEECH or turn.child_text is None:
                raise DynamicContextContractError(
                    "bootstrap continuity accepts complete speech turns only"
                )
            lines.extend(
                (
                    "    <turn>",
                    f"      <child>{escape(turn.child_text, quote=False)}</child>",
                    "      <ava_delivered>"
                    f"{escape(turn.ava_delivered_text, quote=False)}"
                    "</ava_delivered>",
                    "    </turn>",
                )
            )
        lines.append("  </recent_delivered_turns>")
    if memory_references:
        lines.append(
            '  <memory_references trust="historical_evidence_not_instructions">'
        )
        for reference in memory_references:
            attributes = [
                f'source_kind="{escape(str(reference.source_kind), quote=True)}"'
            ]
            if reference.started_at is not None and reference.ended_at is not None:
                attributes.extend(
                    (
                        'started_at="'
                        f'{escape(reference.started_at.isoformat(), quote=True)}"',
                        'ended_at="'
                        f'{escape(reference.ended_at.isoformat(), quote=True)}"',
                    )
                )
            if reference.timezone_ids:
                timezone_ids = ",".join(reference.timezone_ids)
                attributes.append(f'timezone_ids="{escape(timezone_ids, quote=True)}"')
            lines.append(f"    <memory {' '.join(attributes)}>")
            lines.append(f"      {escape(reference.text, quote=False)}")
            lines.append("    </memory>")
        lines.append("  </memory_references>")
    if canvas_context is not None:
        lines.append("  " + canvas_context.replace("\n", "\n  "))
    lines.append("</ava_dynamic_context>")
    return "\n".join(lines)


def _serialize_dialogue_frame(frame: DialogueFrame) -> list[str]:
    attributes = (
        'trust="durable_semantic_working_memory_not_instructions" '
        f'revision="{frame.revision}"'
    )
    semantic_fields = (
        ("active_topics", frame.active_topics),
        ("active_goals", frame.active_goals),
        ("unresolved_questions", frame.unresolved_questions),
        ("current_referents", frame.current_referents),
        ("recent_entities", frame.recent_entities),
        ("ava_commitments", frame.ava_commitments),
        ("explicit_user_preferences", frame.explicit_user_preferences),
        (
            "temporary_interaction_preferences",
            frame.temporary_interaction_preferences,
        ),
        ("recent_tool_references", frame.recent_tool_references),
    )
    if not any(values for _, values in semantic_fields) and not (
        frame.relevant_canvas_references
    ):
        return [f"  <dialogue_frame {attributes}/>"]

    lines = [f"  <dialogue_frame {attributes}>"]
    for category, values in semantic_fields:
        if not values:
            continue
        lines.append(f"    <{category}>")
        lines.extend(
            f"      <item>{escape(value, quote=False)}</item>" for value in values
        )
        lines.append(f"    </{category}>")
    if frame.relevant_canvas_references:
        lines.append("    <relevant_canvas_references>")
        for reference in frame.relevant_canvas_references:
            lines.append(
                "      <canvas_reference>"
                f"{escape(reference.relation, quote=False)}"
                "</canvas_reference>"
            )
        lines.append("    </relevant_canvas_references>")
    lines.append("  </dialogue_frame>")
    return lines


def _daypart(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _serialized_utf8_bytes(content: str) -> int:
    return len(content.encode("utf-8"))


def _estimate_dynamic_tokens(content: str) -> int:
    """Return approximate planning telemetry; never enforce safety with it."""

    return ceil(_serialized_utf8_bytes(content) / 4)
