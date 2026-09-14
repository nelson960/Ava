from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
import json
from html import escape
from math import ceil
from zoneinfo import ZoneInfo


from ..protocol import AgeBand
from .memory import (
    DialogueFrame,
    StoredDeliveredConversationTurn,
    DeliveredInputKind,
)
from .timezone import validate_iana_timezone_id


MAX_DYNAMIC_CONTEXT_UTF8_BYTES = 20_000
MAX_CANVAS_CONTEXT_CHARACTERS = 2_400


class DynamicContextBuildReason(StrEnum):
    SESSION_BOOTSTRAP = "sessionBootstrap"
    TURN_COMMITTED = "turnCommitted"
    CANVAS_ACTIVATED = "canvasActivated"
    CANVAS_FOCUSED = "canvasFocused"
    CANVAS_INTERACTION_COMMITTED = "canvasInteractionCommitted"
    CANVAS_PRESENTATION_CHANGED = "canvasPresentationChanged"
    MEMORY_COMPACTED = "memoryCompacted"


class DynamicContextContractError(RuntimeError):
    """Required current state cannot satisfy the dynamic-context contract."""


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
        bootstrap_turns: tuple[StoredDeliveredConversationTurn, ...] = (),
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
        eligible_bootstrap = (
            validated_reason == DynamicContextBuildReason.SESSION_BOOTSTRAP
        )
        turns = bootstrap_turns if eligible_bootstrap else ()
        omitted_bootstrap = len(bootstrap_turns) - len(turns)
        mandatory = _serialize_context(
            age_band=validated_age_band,
            timezone_id=validated_timezone_id,
            current_time_utc=validated_now,
            bootstrap_turns=(),
            dialogue_frame=validated_dialogue_frame,
            canvas_context=validated_canvas,
        )
        if _serialized_utf8_bytes(mandatory) > self._max_dynamic_utf8_bytes:
            raise DynamicContextContractError(
                "mandatory current dynamic context exceeds its UTF-8 byte contract"
            )

        retained_turns = list(turns)
        while True:
            content = _serialize_context(
                age_band=validated_age_band,
                timezone_id=validated_timezone_id,
                current_time_utc=validated_now,
                bootstrap_turns=tuple(retained_turns),
                dialogue_frame=validated_dialogue_frame,
                canvas_context=validated_canvas,
            )
            serialized_utf8_bytes = _serialized_utf8_bytes(content)
            estimated_tokens = _estimate_dynamic_tokens(content)
            if serialized_utf8_bytes <= self._max_dynamic_utf8_bytes:
                break
            detailed_index = next(
                (i for i, turn in enumerate(retained_turns) if turn.tool_exchanges),
                None,
            )
            if detailed_index is not None:
                retained_turns[detailed_index] = retained_turns[
                    detailed_index
                ].model_copy(update={"tool_exchanges": ()})
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
            included_memory_references=0,
            omitted_memory_references=0,
            canvas_included=active_visual_present,
            dialogue_frame_revision=validated_dialogue_frame.revision,
            dialogue_frame_included=bool(
                _serialize_dialogue_frame(validated_dialogue_frame)
            ),
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
    if not value.startswith('<canvas_context trust="backend_semantic_canvas_metadata"'):
        raise ValueError("canvas_context is not bounded declared-trust context")
    return value


def _serialize_context(
    *,
    age_band: AgeBand,
    timezone_id: str,
    current_time_utc: datetime,
    bootstrap_turns: tuple[StoredDeliveredConversationTurn, ...],
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
            lines.append("    <turn>")
            lines.append(
                f"      <occurred_at>{escape(turn.occurred_at.isoformat())}</occurred_at>"
            )
            lines.append(f"      <timezone_id>{escape(turn.timezone_id)}</timezone_id>")
            if turn.input_kind == DeliveredInputKind.SPEECH:
                lines.append(
                    f"      <child>{escape(turn.child_text or '', quote=False)}</child>"
                )
            else:
                interaction = turn.canvas_interaction
                if interaction is None:
                    raise DynamicContextContractError(
                        "interaction turn lost semantic input"
                    )
                step = (
                    f' step="{interaction.step_index}"'
                    if interaction.step_index is not None
                    else ""
                )
                lines.append(
                    f'      <interaction mode="{escape(str(interaction.interaction_mode))}"{step}>'
                    f"{escape(interaction.accessibility_label, quote=False)}</interaction>"
                )
            lines.append(
                f"      <ava_delivered>{escape(turn.ava_delivered_text, quote=False)}</ava_delivered>"
            )
            lines.append(f"      <terminal>{escape(str(turn.terminal))}</terminal>")
            for exchange in turn.tool_exchanges:
                semantic_json = json.dumps(
                    exchange.model_dump(mode="json", by_alias=True, exclude_none=True),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                lines.append(
                    f"      <tool_exchange>{escape(semantic_json, quote=False)}</tool_exchange>"
                )
            lines.append("    </turn>")
        lines.append("  </recent_delivered_turns>")
    if canvas_context is not None:
        lines.append("  " + canvas_context.replace("\n", "\n  "))
    lines.append("</ava_dynamic_context>")
    return "\n".join(lines)


def _serialize_dialogue_frame(frame: DialogueFrame) -> list[str]:
    attributes = 'trust="durable_semantic_working_memory_not_instructions"'
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
        return []

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
