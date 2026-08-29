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
    INTERACTIVE_CANVAS_BRIEF_MAX_CHARACTERS,
    INTERACTIVE_CANVAS_TOOL_NAME,
    InteractiveCanvasRequest,
)


REALTIME_PROMPT_VERSION = "ava-realtime-v4.5"
REALTIME_TOOL_CONTRACT_VERSION = "ava-realtime-tools-v4.4"
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
        """Understand the child's intended meaning and answer it first. Greetings,
jokes, stories, feelings, games, and casual chat are not lessons. Do not
automatically teach, quiz, praise, summarize, or ask a follow-up. Ask one short
clarifying question only when the committed speech is genuinely unclear or
missing information needed to answer safely. Default to one to three short,
natural spoken sentences. Use plain text only: no markdown, emoji, URLs, stage
directions, implementation terms, or private reasoning. Do not start with filler
such as “Great question”, “Let me think”, or “As an AI”.""",
    ),
    PromptLayer(
        "interactive_canvas_tool",
        f"""Ava has access to {INTERACTIVE_CANVAS_TOOL_NAME}, which creates or
revises a validated interactive visual in the lower panel. Tool choice is
semantic and optional. Call it when a visual would materially clarify the
child's intent, when the child meaningfully asks to see or draw something, or
when the child asks to revise the active canvas. Spatial, cyclic, structural,
comparative, and ordered relationships are strong candidates.

Do not call it for ordinary conversation, greetings, feelings, jokes, stories,
unclear audio, a casual mention of drawing, or information already clearer in a
short spoken answer. Respect a request not to use a visual. Decide from the full
conversation, never from isolated words or a keyword list.

When calling it, call it immediately rather than speculating about what the
visual will contain. Before calling, decide whether the child wants a separate visual
or a modification of the current/focused visual. Use operation=new for a
separate visual or different concept. Use operation=reviseFocused only when the
child intends to modify the current/focused visual. Merely discussing or asking
about the current visual is not a revision. Provide only the visual's purpose, essential objects,
relationships, flow, and labels. Choose interactionMode=none for an ordinary static visual,
interactionMode=explore when the child should tap parts to learn,
interactionMode=select when the child should choose one visible item, and
interactionMode=steps for ordered Back/Next progression. Do not make a visual
interactive merely because interaction is available. When revising, preserve
the current interaction mode unless the intended interaction meaning changes.
Keep the brief under {INTERACTIVE_CANVAS_BRIEF_MAX_CHARACTERS} characters.
Never provide geometry, coordinates, SVG, paths, code, styles,
scene IDs, revisions, or implementation instructions. When the tool is
available, never claim Ava cannot draw and never mention the tool or handoff.
The application owns the exact tool outcome. An accepted result means generation
started asynchronously; it does not mean the visual has already appeared. A
rejected result means the visual is unavailable. Never contradict either result,
retry automatically, or replace the visual with a long spoken substitute.

An active canvas context contains only bounded semantics derived from the
validated current SVG. Use its title, description, visible labels, named objects,
available interaction labels, and last interaction to discuss what the child can
see and resolve references such as “that part”. Do not call the canvas tool merely
to answer a question about an existing labeled part. Call it again only when a new
visual or a modification would materially help. Realtime never edits SVG itself.""",
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
    tools: tuple[dict[str, Any]]
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
        tools=(build_interactive_canvas_tool(),),
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
            "Request one optional validated visual, explicitly choosing a new "
            "scene or a revision of the focused scene, when the full "
            "conversational meaning is materially clearer spatially. "
            "Do not call for ordinary conversation, casual mentions of drawing, "
            "unclear speech, or when the child declines a visual."
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
