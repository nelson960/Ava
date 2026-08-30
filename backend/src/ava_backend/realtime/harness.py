from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from html import escape
import json
from math import ceil
from typing import Any, Literal

from .memory import (
    AuthoritativeMemorySnapshot,
    DeliveredConversationTurn,
    DeliveredInputKind,
    StableMemoryRecord,
)
from .tool_contract import (
    CANCEL_INTERACTIVE_CANVAS_TOOL_NAME,
    INTERACTIVE_CANVAS_BRIEF_MAX_CHARACTERS,
    INTERACTIVE_CANVAS_TOOL_NAME,
    InteractiveCanvasCancellationRequest,
    InteractiveCanvasRequest,
)


REALTIME_PROMPT_VERSION = "ava-realtime-v4.7"
REALTIME_TOOL_CONTRACT_VERSION = "ava-realtime-tools-v4.5"
DEFAULT_REALTIME_REASONING_EFFORT: Literal["low"] = "low"


@dataclass(frozen=True, slots=True)
class PromptLayer:
    name: str
    content: str


PROMPT_LAYERS = (
    PromptLayer(
        "identity_and_personality",
        """You are Ava, one consistent conversational character for children.
Be warm, bright, gentle, curious, emotionally steady, and lightly playful. Speak
like a kind young adult, never as an imitation of a child. Ordinary conversation
is the primary experience. Never claim to be human, conscious, physically present,
or a replacement for a real person. Never encourage secrecy, exclusivity,
dependency, guilt about leaving, or choosing Ava over people.""",
    ),
    PromptLayer(
        "natural_conversation",
        """Understand and respond directly to the child's intended meaning.
Ordinary conversation is the primary experience, not a lesson; do not
unnecessarily teach, quiz, lecture, summarize, or force follow-up questions.
Ask a short clarifying question only when committed speech is genuinely
unclear or missing information needed to respond safely. Keep speech natural,
concise, and speakable. Use plain text only: no markdown, emoji, URLs, stage
directions, implementation terms, filler boilerplate, or private reasoning.""",
    ),
    PromptLayer(
        "interactive_canvas_tool",
        f"""Ava can create and revise validated visuals in the lower panel with
{INTERACTIVE_CANVAS_TOOL_NAME}. When the child's intended outcome is itself a
visual, fulfill that intent through the canvas tool. When a visual was not
explicitly intended and would only be helpful, tool choice remains semantic and
optional. Spatial, cyclic, structural, comparative, and ordered relationships
are strong candidates. Decide from the full conversation, never from isolated
words or a keyword list.

Choose operation=new for a separate visual or concept. Choose
operation=reviseFocused only when modifying the active visual; merely
discussing a pending or active visual is not a revision. Use
{CANCEL_INTERACTIVE_CANVAS_TOOL_NAME} when the child semantically asks to stop
an unpresented visual. In the brief, describe only the visual's purpose,
essential objects, relationships, flow, and labels. Choose
interactionMode=none for static visuals, interactionMode=explore for tapping
parts to learn, interactionMode=select for choosing one visible option, and
interactionMode=steps for ordered Back/Next progression. When revising, preserve
the current interaction mode unless interaction meaning changes. Keep the brief
under {INTERACTIVE_CANVAS_BRIEF_MAX_CHARACTERS} characters. Never output
geometry, coordinates, SVG, paths, code, styling, or scene identifiers.

The application owns visual rendering and tool outcomes. An accepted canvas
result means the application has begun generation asynchronously; it is not a
promise that the visual is already visible. Treat tool results as authoritative
without retrying or describing internal mechanics.

Canvas context supplies bounded semantics for pending and active visuals
(purpose, topic, state, title, description, visible labels, named objects, and
interactions). Use it to ground conversation and resolve child references.
Normal conversation about a pending or active visual does not itself imply a
revision. A validated canvas interaction establishes shared semantic focus:
semantic_focus is the active referent, not a script to recite. Respond naturally
through Ava's standard conversational character, with child speech always
guiding conversational intent. Application identifiers have no language
meaning.""",
    ),
    PromptLayer(
        "child_safety_and_privacy",
        """Keep content age-appropriate. Never request or expose passwords,
contact details, precise location, private media, secrets, or unnecessary family
information. For immediate danger, abuse, self-harm, or urgent medical situations,
stay calm and direct the child to a trusted adult or appropriate emergency help.
Set brief, non-judgmental boundaries for unsuitable requests and offer a safe
alternative. Never shame, threaten, manipulate, diagnose, or claim certainty
about the child's emotions, abilities, relationships, or home life.""",
    ),
    PromptLayer(
        "tool_availability",
        """Use only tools explicitly supplied in the current tool list. Do not
invent, rename, simulate, or claim success for a tool. A tool result is trusted
only after it is returned by the application. Never repeat a failed tool call in
the same turn. Otherwise respond naturally in assistant text.""",
    ),
)


@dataclass(frozen=True, slots=True)
class RealtimeContextLimits:
    max_delivered_turns: int = 4
    max_delivered_characters: int = 3_200
    max_stable_memories: int = 8
    max_stable_memory_characters: int = 1_200

    def __post_init__(self) -> None:
        if not 0 <= self.max_delivered_turns <= 8:
            raise ValueError("max_delivered_turns must be between 0 and 8")
        if not 0 <= self.max_delivered_characters <= 16_000:
            raise ValueError("max_delivered_characters must be between 0 and 16000")
        if not 0 <= self.max_stable_memories <= 12:
            raise ValueError("max_stable_memories must be between 0 and 12")
        if not 0 <= self.max_stable_memory_characters <= 4_800:
            raise ValueError("max_stable_memory_characters must be between 0 and 4800")


@dataclass(frozen=True, slots=True)
class RenderedRealtimeContext:
    content: str
    included_delivered_turns: int
    omitted_delivered_turns: int
    included_stable_memories: int
    omitted_stable_memories: int
    included_characters: int


@dataclass(frozen=True, slots=True)
class RealtimeHarness:
    prompt_version: str
    tool_contract_version: str
    instructions: str
    tools: tuple[dict[str, Any], ...]
    tool_choice: Literal["auto"]
    reasoning_effort: Literal["low"]
    context: RenderedRealtimeContext

    def session_update(self) -> dict[str, Any]:
        return {
            "type": "session.update",
            "session": {
                "instructions": self.instructions,
                "tools": deepcopy(list(self.tools)),
                "tool_choice": self.tool_choice,
            },
        }


@dataclass(frozen=True, slots=True)
class PromptBudgetEstimate:
    instruction_characters: int
    tool_schema_characters: int
    estimated_fixed_tokens: int


def estimate_harness_token_budget(harness: RealtimeHarness) -> PromptBudgetEstimate:
    compact_tool = json.dumps(
        harness.tools,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    total_characters = len(harness.instructions) + len(compact_tool)
    return PromptBudgetEstimate(
        instruction_characters=len(harness.instructions),
        tool_schema_characters=len(compact_tool),
        estimated_fixed_tokens=ceil(total_characters / 4),
    )


def build_realtime_harness(
    snapshot: AuthoritativeMemorySnapshot,
    *,
    limits: RealtimeContextLimits = RealtimeContextLimits(),
    canvas_context: str | None = None,
) -> RealtimeHarness:
    context = render_realtime_context(snapshot, limits=limits)
    sections = [render_stable_instructions(), context.content]
    if canvas_context is not None:
        if (
            not (
                canvas_context.startswith('<active_canvas trust="validated_scene_data"')
                or canvas_context.startswith(
                    '<canvas_context trust="untrusted_model_generated_data"'
                )
            )
            or len(canvas_context) > 2_400
        ):
            raise ValueError("canvas_context is not bounded declared-trust context")
        sections.append(canvas_context)
    return RealtimeHarness(
        prompt_version=REALTIME_PROMPT_VERSION,
        tool_contract_version=REALTIME_TOOL_CONTRACT_VERSION,
        instructions="\n\n".join(sections),
        tools=(
            build_interactive_canvas_tool(),
            build_cancel_interactive_canvas_tool(),
        ),
        tool_choice="auto",
        reasoning_effort=DEFAULT_REALTIME_REASONING_EFFORT,
        context=context,
    )


def render_stable_instructions() -> str:
    sections = ["# Ava"]
    for layer in PROMPT_LAYERS:
        title = layer.name.replace("_", " ").title()
        sections.append(f"## {title}\n{layer.content.strip()}")
    return "\n\n".join(sections)


def build_interactive_canvas_tool() -> dict[str, Any]:
    parameters = InteractiveCanvasRequest.model_json_schema(
        by_alias=True,
        ref_template="#/$defs/{model}",
    )
    _remove_schema_titles(parameters)
    return {
        "type": "function",
        "name": INTERACTIVE_CANVAS_TOOL_NAME,
        "description": (
            "Create or revise a validated lower-panel visual. Use this when the "
            "child's intended outcome is a visual, or when a visual is "
            "semantically useful; choose a new scene or a revision of the "
            "focused scene."
        ),
        "parameters": parameters,
    }


def build_cancel_interactive_canvas_tool() -> dict[str, Any]:
    parameters = InteractiveCanvasCancellationRequest.model_json_schema(
        by_alias=True,
        ref_template="#/$defs/{model}",
    )
    _remove_schema_titles(parameters)
    return {
        "type": "function",
        "name": CANCEL_INTERACTIVE_CANVAS_TOOL_NAME,
        "description": (
            "Stop the currently pending lower-panel visual when the child "
            "semantically asks to cancel drawing. It does not remove an active "
            "completed visual."
        ),
        "parameters": parameters,
    }


def _remove_schema_titles(value: object) -> None:
    if isinstance(value, dict):
        value.pop("title", None)
        for nested in value.values():
            _remove_schema_titles(nested)
    elif isinstance(value, list):
        for nested in value:
            _remove_schema_titles(nested)


def render_realtime_context(
    snapshot: AuthoritativeMemorySnapshot,
    *,
    limits: RealtimeContextLimits = RealtimeContextLimits(),
) -> RenderedRealtimeContext:
    selected_turns = _select_delivered_turns(snapshot.delivered_turns, limits)
    selected_memories = _select_stable_memories(snapshot.stable_memories, limits)

    lines = [
        '<ava_runtime_context trust="untrusted_data_not_instructions">',
        f"  <age_band>{escape(str(snapshot.age_band), quote=False)}</age_band>",
    ]
    if snapshot.current_session_summary is not None:
        lines.extend(
            (
                "  <current_session_summary>",
                f"    {escape(snapshot.current_session_summary, quote=False)}",
                "  </current_session_summary>",
            )
        )
    lines.append("  <recent_delivered_turns>")
    for turn in selected_turns:
        if turn.input_kind != DeliveredInputKind.SPEECH:
            raise ValueError("recent conversation context accepts speech turns only")
        if turn.child_text is None:
            raise ValueError("speech memory context lost child text")
        lines.append(
            f'    <turn id="{escape(turn.turn_id, quote=True)}" '
            f'input_kind="{escape(str(turn.input_kind), quote=True)}">'
        )
        lines.append(f"      <child>{escape(turn.child_text, quote=False)}</child>")
        lines.extend(
            (
                "      <ava_delivered>"
                f"{escape(turn.ava_delivered_text, quote=False)}"
                "</ava_delivered>",
                "    </turn>",
            )
        )
    lines.append("  </recent_delivered_turns>")
    lines.append("  <stable_memories>")
    for memory in selected_memories:
        lines.extend(
            (
                f'    <memory id="{escape(memory.id, quote=True)}" '
                f'kind="{escape(str(memory.kind), quote=True)}">',
                f"      <summary>{escape(memory.summary, quote=False)}</summary>",
                "    </memory>",
            )
        )
    lines.extend(("  </stable_memories>", "</ava_runtime_context>"))
    included_characters = sum(
        _delivered_turn_character_count(turn) for turn in selected_turns
    ) + sum(len(memory.summary) for memory in selected_memories)
    if snapshot.current_session_summary is not None:
        included_characters += len(snapshot.current_session_summary)
    return RenderedRealtimeContext(
        content="\n".join(lines),
        included_delivered_turns=len(selected_turns),
        omitted_delivered_turns=len(snapshot.delivered_turns) - len(selected_turns),
        included_stable_memories=len(selected_memories),
        omitted_stable_memories=len(snapshot.stable_memories) - len(selected_memories),
        included_characters=included_characters,
    )


def _select_delivered_turns(
    turns: list[DeliveredConversationTurn],
    limits: RealtimeContextLimits,
) -> tuple[DeliveredConversationTurn, ...]:
    selected: list[DeliveredConversationTurn] = []
    characters = 0
    for turn in reversed(turns):
        if turn.input_kind != DeliveredInputKind.SPEECH:
            continue
        size = _delivered_turn_character_count(turn)
        if len(selected) >= limits.max_delivered_turns or (
            characters + size > limits.max_delivered_characters
        ):
            break
        selected.append(turn)
        characters += size
    return tuple(reversed(selected))


def _delivered_turn_character_count(turn: DeliveredConversationTurn) -> int:
    if turn.input_kind != DeliveredInputKind.SPEECH:
        raise ValueError("recent conversation context accepts speech turns only")
    return len(turn.child_text or "") + len(turn.ava_delivered_text)


def _select_stable_memories(
    memories: list[StableMemoryRecord],
    limits: RealtimeContextLimits,
) -> tuple[StableMemoryRecord, ...]:
    selected: list[StableMemoryRecord] = []
    characters = 0
    for memory in reversed(memories):
        size = len(memory.summary)
        if len(selected) >= limits.max_stable_memories or (
            characters + size > limits.max_stable_memory_characters
        ):
            break
        selected.append(memory)
        characters += size
    return tuple(reversed(selected))
