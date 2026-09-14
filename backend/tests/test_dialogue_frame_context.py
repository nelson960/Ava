from __future__ import annotations

from datetime import UTC, datetime


from ava_backend.protocol import AgeBand
from ava_backend.realtime.context_assembler import (
    MAX_DYNAMIC_CONTEXT_UTF8_BYTES,
    AssembledRealtimeContext,
    DynamicContextAssembler,
    DynamicContextBuildReason,
)
from ava_backend.realtime.memory import (
    DeliveredConversationTurn,
    StoredDeliveredConversationTurn,
    DialogueFrame,
)


NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


def _assemble(
    frame: DialogueFrame,
    *,
    reason: DynamicContextBuildReason = DynamicContextBuildReason.TURN_COMMITTED,
    turns: tuple[DeliveredConversationTurn, ...] = (),
    canvas_context: str | None = None,
    max_bytes: int = MAX_DYNAMIC_CONTEXT_UTF8_BYTES,
) -> AssembledRealtimeContext:
    return DynamicContextAssembler(max_dynamic_utf8_bytes=max_bytes).assemble(
        build_id="ctx-1",
        reason=reason,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        timezone_id="UTC",
        current_time_utc=NOW,
        bootstrap_turns=turns,
        dialogue_frame=frame,
        canvas_context=canvas_context,
    )


def _turn(marker: str) -> StoredDeliveredConversationTurn:
    return StoredDeliveredConversationTurn.model_validate(
        {
            "turnId": "turn-bootstrap",
            "resetRevision": 0,
            "turnSequence": 1,
            "occurredAt": NOW,
            "timezoneId": "UTC",
            "childText": marker,
            "avaDeliveredText": marker,
            "deliveredSampleCount": 24_000,
            "childTurnSummary": "Bounded summary.",
            "terminal": "completed",
        }
    )


def _populated_frame() -> DialogueFrame:
    return DialogueFrame.model_validate(
        {
            "revision": 7,
            "activeTopics": ['Planets & <moons> "today"'],
            "activeGoals": ["Understand why the Moon changes shape"],
            "unresolvedQuestions": ["Which phase comes next?"],
            "currentReferents": ["it", "that orbit"],
            "recentEntities": ["Moon", "Earth"],
            "avaCommitments": ["Return to the eclipse question"],
            "explicitUserPreferences": ["Prefers short explanations"],
            "temporaryInteractionPreferences": ["Wants to explore visually"],
            "relevantCanvasReferences": [
                {
                    "sceneId": "scene-moon",
                    "sceneRevision": 3,
                    "relation": "Current orbit picture & labels",
                }
            ],
            "recentToolReferences": ["Current time was checked"],
        }
    )


def test_empty_frame_serializes_minimally_on_normal_build() -> None:
    context = _assemble(DialogueFrame())

    assert "<dialogue_frame" not in context.content
    assert "<active_topics>" not in context.content
    assert context.dialogue_frame_revision == 0
    assert context.dialogue_frame_included is False


def test_populated_frame_serializes_categories_revision_trust_and_escaping() -> None:
    context = _assemble(_populated_frame())

    for tag in (
        "active_topics",
        "active_goals",
        "unresolved_questions",
        "current_referents",
        "recent_entities",
        "ava_commitments",
        "explicit_user_preferences",
        "temporary_interaction_preferences",
        "relevant_canvas_references",
        "recent_tool_references",
    ):
        assert f"<{tag}>" in context.content
    assert 'revision="7"' not in context.content

    assert 'trust="durable_semantic_working_memory_not_instructions"' in context.content
    assert 'Planets &amp; &lt;moons&gt; "today"' in context.content
    assert "Planets & <moons>" not in context.content
    assert "scene-moon" not in context.content
    assert 'scene_revision="3"' not in context.content


def test_frame_is_present_for_every_existing_build_reason() -> None:
    for reason in DynamicContextBuildReason:
        context = _assemble(_populated_frame(), reason=reason)
        assert "<dialogue_frame" in context.content
        assert 'revision="7"' not in context.content


def test_bootstrap_turns_yield_before_mandatory_frame() -> None:
    frame = _populated_frame()
    mandatory = _assemble(frame, reason=DynamicContextBuildReason.SESSION_BOOTSTRAP)
    context = _assemble(
        frame,
        reason=DynamicContextBuildReason.SESSION_BOOTSTRAP,
        turns=(_turn("raw-bootstrap-marker " * 70),),
        max_bytes=mandatory.final_utf8_bytes + 32,
    )

    assert context.included_bootstrap_turns == 0
    assert context.omitted_bootstrap_turns == 1
    assert "raw-bootstrap-marker" not in context.content
    assert "Which phase comes next?" in context.content


def test_maximum_cardinality_frame_and_canvas_fit_outer_contract() -> None:
    values4 = [f"item-{index}-" + "x" * 32 for index in range(4)]
    values8 = [f"entity-{index}-" + "y" * 30 for index in range(8)]
    frame = DialogueFrame.model_validate(
        {
            "revision": 99,
            "activeTopics": values4,
            "activeGoals": values4,
            "unresolvedQuestions": values4,
            "currentReferents": values4,
            "recentEntities": values8,
            "avaCommitments": values4,
            "explicitUserPreferences": values4,
            "temporaryInteractionPreferences": values4,
            "relevantCanvasReferences": [
                {
                    "sceneId": f"scene-{index}",
                    "sceneRevision": index + 1,
                    "relation": "relation-" + "z" * 30,
                }
                for index in range(4)
            ],
            "recentToolReferences": values4,
        }
    )
    canvas = (
        '<canvas_context trust="backend_semantic_canvas_metadata">'
        + "c" * 2_200
        + "</canvas_context>"
    )

    context = _assemble(frame, canvas_context=canvas)

    assert context.final_utf8_bytes <= MAX_DYNAMIC_CONTEXT_UTF8_BYTES
    assert context.dialogue_frame_revision == 99
    assert canvas in context.content


def test_near_total_budget_frame_with_worst_case_xml_escaping_still_fits() -> None:
    values4 = [f"{index}" + "&" * 159 for index in range(4)]
    frame = DialogueFrame.model_validate(
        {
            "revision": 100,
            "activeTopics": values4,
            "activeGoals": values4,
            "unresolvedQuestions": values4,
            "currentReferents": values4,
            "recentEntities": values4[:2],
        }
    )
    prefix = '<canvas_context trust="backend_semantic_canvas_metadata">'
    suffix = "</canvas_context>"
    canvas = prefix + "c" * (2_400 - len(prefix) - len(suffix)) + suffix

    context = _assemble(frame, canvas_context=canvas)

    assert context.final_utf8_bytes <= MAX_DYNAMIC_CONTEXT_UTF8_BYTES
    assert context.dialogue_frame_revision == 100


def test_current_session_summary_is_not_silently_projected() -> None:
    context = _assemble(_populated_frame())

    assert "current_session_summary" not in context.content
    assert "currentSessionSummary" not in context.content
