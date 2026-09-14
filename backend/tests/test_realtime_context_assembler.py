from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import re

import pytest

from ava_backend.protocol import AgeBand
from ava_backend.realtime.context_assembler import (
    MAX_DYNAMIC_CONTEXT_UTF8_BYTES,
    AssembledRealtimeContext,
    DynamicContextAssembler,
    DynamicContextBuildReason,
    DynamicContextContractError,
)
from ava_backend.realtime.memory import (
    DeliveredConversationTurn,
    DevelopmentAuthoritativeMemoryStore,
    StoredDeliveredConversationTurn,
    DialogueFrame,
)


NOW = datetime(2026, 8, 31, 6, 30, tzinfo=UTC)


def _turn(index: int, *, marker: str | None = None) -> StoredDeliveredConversationTurn:
    return StoredDeliveredConversationTurn.model_validate(
        {
            "turnId": f"turn-{index}",
            "resetRevision": 0,
            "turnSequence": index,
            "occurredAt": NOW,
            "timezoneId": "UTC",
            "childText": marker or f"child-{index}",
            "avaDeliveredText": f"ava-{index}",
            "deliveredSampleCount": 24_000,
            "childTurnSummary": f"summary-{index}",
            "terminal": "completed",
        }
    )


def _assemble(
    *,
    reason: DynamicContextBuildReason = DynamicContextBuildReason.SESSION_BOOTSTRAP,
    turns: tuple[StoredDeliveredConversationTurn, ...] = (),
    canvas_context: str | None = None,
    max_bytes: int = MAX_DYNAMIC_CONTEXT_UTF8_BYTES,
    build_id: str = "ctx-1",
    dialogue_frame: DialogueFrame | None = None,
    current_time_utc: datetime = NOW,
) -> AssembledRealtimeContext:
    return DynamicContextAssembler(max_dynamic_utf8_bytes=max_bytes).assemble(
        build_id=build_id,
        reason=reason,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        timezone_id="Asia/Kolkata",
        current_time_utc=current_time_utc,
        bootstrap_turns=turns,
        dialogue_frame=dialogue_frame,
        canvas_context=canvas_context,
    )


def test_context_build_identity_is_metadata_not_serialized_content() -> None:
    first = _assemble(build_id="ctx-17")
    second = _assemble(build_id="ctx-18")

    assert first.build_id == "ctx-17"
    assert second.build_id == "ctx-18"
    assert first.content == second.content
    assert first.content_fingerprint == second.content_fingerprint
    assert "ctx-17" not in first.content
    assert "ctx-18" not in second.content
    assert first.final_utf8_bytes == len(first.content.encode("utf-8"))


async def test_store_selected_mixed_continuity_and_oldest_detail_eviction() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    search = {
        "tool": "search_memory",
        "request": {"query": "telescope", "order": "oldest"},
        "result": {
            "status": "available",
            "references": [
                {
                    "sourceKind": "rawEvent",
                    "startedAt": NOW,
                    "endedAt": NOW,
                    "timezoneIds": ["UTC"],
                    "text": "Earlier telescope evidence <&> " * 30,
                }
            ],
        },
    }
    canvas = {
        "tool": "manage_canvas_presentation",
        "request": {"action": "hide", "completion": "requestComplete"},
        "result": {
            "status": "accepted",
            "operation": "hide",
            "changed": True,
            "visible": False,
        },
    }
    for index in range(1, 6):
        payload = _turn(index).model_dump(
            by_alias=True,
            exclude={"turn_sequence", "reset_revision", "occurred_at", "timezone_id"},
        )
        payload["avaDeliveredText"] = "Heard prefix <&> 孩子 " * 35
        payload["childText"] = f"input-{index} " + "बच्चे " * 50
        payload["toolExchanges"] = [search] if index in (1, 4) else []
        if index == 2:
            payload["terminal"] = "cancelled"
        if index == 3:
            payload.update(
                inputKind="canvasInteraction",
                childText=None,
                childTextSource=None,
                canvasInteraction={
                    "sceneId": "scene-private",
                    "sceneRevision": 3,
                    "interactionRevision": 4,
                    "interactionMode": "steps",
                    "bindingId": "binding-private",
                    "targetId": "target-private",
                    "accessibilityLabel": "The red telescope",
                    "stepIndex": 2,
                },
            )
        if index == 5:
            payload.update(
                avaDeliveredText="", deliveredSampleCount=0, toolExchanges=[canvas]
            )
        await store.commit_delivered_turn(
            subject="child:mixed",
            expected_reset_revision=0,
            turn=DeliveredConversationTurn.model_validate(payload),
            occurred_at=NOW - timedelta(days=index),
            timezone_id="UTC",
        )
    snapshot = await store.load_snapshot(
        subject="child:mixed", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    turns = tuple(snapshot.delivered_turns)
    full = _assemble(turns=turns)
    assert full.included_bootstrap_turns == 5
    assert (
        full.content.index("input-1")
        < full.content.index("input-2")
        < full.content.index("The red telescope")
        < full.content.index("input-4")
    )
    assert (
        '<interaction mode="steps" step="2">The red telescope</interaction>'
        in full.content
    )
    assert "<terminal>cancelled</terminal>" in full.content
    assert '"visible":false' in full.content
    assert "Earlier telescope evidence &lt;&amp;&gt;" in full.content
    assert all(
        secret not in full.content
        for secret in (
            "scene-private",
            "binding-private",
            "target-private",
            "turnSequence",
            "generationId",
        )
    )
    # Set the budget to exactly the serialization without the oldest detail.
    no_old_detail = (turns[0].model_copy(update={"tool_exchanges": ()}), *turns[1:])
    after_old_detail = _assemble(turns=no_old_detail)
    bounded = _assemble(turns=turns, max_bytes=after_old_detail.final_utf8_bytes)
    assert bounded.content == after_old_detail.content
    assert bounded.included_bootstrap_turns == 5
    assert bounded.content.count('"tool":"search_memory"') == 1
    # When all optional details are exhausted, evict the oldest whole turn.
    cores = tuple(t.model_copy(update={"tool_exchanges": ()}) for t in turns)
    core_tail = _assemble(turns=cores[1:])
    smallest = _assemble(turns=turns, max_bytes=core_tail.final_utf8_bytes)
    assert smallest.content == core_tail.content
    assert smallest.omitted_bootstrap_turns == 1
    assert smallest.final_utf8_bytes <= 20_000


def test_content_fingerprint_is_exact_lowercase_sha256() -> None:
    context = _assemble()

    assert (
        context.content_fingerprint
        == sha256(context.content.encode("utf-8")).hexdigest()
    )
    assert re.fullmatch(r"[0-9a-f]{64}", context.content_fingerprint)


def test_build_reason_alone_does_not_change_content_fingerprint() -> None:
    committed = _assemble(reason=DynamicContextBuildReason.TURN_COMMITTED)
    focused = _assemble(reason=DynamicContextBuildReason.CANVAS_FOCUSED)

    assert committed.content == focused.content
    assert committed.content_fingerprint == focused.content_fingerprint


def test_dialogue_frame_change_changes_content_fingerprint() -> None:
    original = _assemble(dialogue_frame=DialogueFrame())
    changed = _assemble(
        dialogue_frame=DialogueFrame.model_validate(
            {"revision": 1, "activeTopics": ["Saturn"]}
        )
    )

    assert original.content_fingerprint != changed.content_fingerprint


def test_canvas_semantic_context_change_changes_content_fingerprint() -> None:
    first = _assemble(
        canvas_context=(
            '<canvas_context trust="backend_semantic_canvas_metadata" scene_id="first" '
            'revision="1">First</canvas_context>'
        )
    )
    second = _assemble(
        canvas_context=(
            '<canvas_context trust="backend_semantic_canvas_metadata" scene_id="second" '
            'revision="1">Second</canvas_context>'
        )
    )

    assert first.content_fingerprint != second.content_fingerprint


def test_relevant_local_date_and_daypart_changes_change_fingerprint() -> None:
    original = _assemble(current_time_utc=NOW)
    later_daypart = _assemble(current_time_utc=NOW + timedelta(hours=8))
    next_date = _assemble(current_time_utc=NOW + timedelta(days=1))

    assert original.content_fingerprint != later_daypart.content_fingerprint
    assert original.content_fingerprint != next_date.content_fingerprint


def test_bootstrap_includes_durable_turns_in_chronological_order() -> None:
    context = _assemble(turns=(_turn(1), _turn(2), _turn(3)))

    assert context.included_bootstrap_turns == 3
    assert context.omitted_bootstrap_turns == 0
    assert context.content.index("child-1") < context.content.index("child-2")
    assert context.content.index("child-2") < context.content.index("child-3")


@pytest.mark.parametrize(
    "reason",
    [
        DynamicContextBuildReason.TURN_COMMITTED,
        DynamicContextBuildReason.CANVAS_ACTIVATED,
        DynamicContextBuildReason.CANVAS_INTERACTION_COMMITTED,
        DynamicContextBuildReason.MEMORY_COMPACTED,
    ],
)
def test_same_provider_call_does_not_reinject_committed_dialogue(
    reason: DynamicContextBuildReason,
) -> None:
    context = _assemble(reason=reason, turns=(_turn(1, marker="must-not-repeat"),))

    assert "must-not-repeat" not in context.content
    assert context.included_bootstrap_turns == 0
    assert context.omitted_bootstrap_turns == 1


def test_fresh_bootstrap_restores_durable_continuity() -> None:
    context = _assemble(
        reason=DynamicContextBuildReason.SESSION_BOOTSTRAP,
        turns=(_turn(1, marker="restored dialogue"),),
    )

    assert "restored dialogue" in context.content


def test_current_environment_and_focused_canvas_are_preserved() -> None:
    canvas = (
        '<canvas_context trust="backend_semantic_canvas_metadata" scene_id="water" '
        'revision="2">Water &amp; clouds</canvas_context>'
    )
    context = _assemble(canvas_context=canvas)

    assert "<age_band>middleChildhood</age_band>" in context.content
    assert "<timezone_id>Asia/Kolkata</timezone_id>" in context.content
    assert "<local_date>2026-08-31</local_date>" in context.content
    assert "<weekday>Monday</weekday>" in context.content
    assert "<daypart>afternoon</daypart>" in context.content
    assert canvas in context.content


def test_empty_optional_sources_add_no_filler_sections() -> None:
    context = _assemble()

    assert "recent_delivered_turns" not in context.content
    assert "memory_references" not in context.content
    assert "canvas_context" not in context.content
    assert "canvas_presentation" not in context.content
    assert "dialogue_frame" not in context.content


def test_mandatory_current_state_fails_loudly_if_contract_cannot_fit() -> None:
    with pytest.raises(DynamicContextContractError, match="mandatory"):
        _assemble(max_bytes=128)


@pytest.mark.parametrize(
    "historical_text",
    [
        ("plain historical conversation " * 45)[:1_200],
        ("बच्चे ने अंतरिक्ष और सितारों के बारे में पूछा। " * 24)[:1_200],
        ("孩子以前讨论过行星、月亮和星星。" * 42)[:1_200],
        ("<&> \"quoted\" 'apostrophe' <memory> & evidence " * 30)[:1_200],
    ],
)
def test_final_serialized_multilingual_and_escaped_context_obeys_byte_ceiling(
    historical_text: str,
) -> None:
    turns = tuple(_turn(index, marker=historical_text) for index in range(1, 25))

    context = _assemble(turns=turns)
    final_utf8_bytes = len(context.content.encode("utf-8"))

    assert final_utf8_bytes <= MAX_DYNAMIC_CONTEXT_UTF8_BYTES
    assert context.omitted_bootstrap_turns > 0


def test_ascii_context_can_approach_but_never_cross_hard_byte_limit() -> None:
    turns = tuple(
        _turn(index, marker=(f"historical-{index} " * 70)) for index in range(1, 25)
    )

    context = _assemble(turns=turns)
    final_utf8_bytes = len(context.content.encode("utf-8"))

    assert 15_000 < final_utf8_bytes <= MAX_DYNAMIC_CONTEXT_UTF8_BYTES


def test_estimated_tokens_are_telemetry_not_the_hard_safety_measure() -> None:
    context = _assemble(
        turns=tuple(
            _turn(index, marker="इतिहास और बातचीत " * 45) for index in range(1, 12)
        )
    )
    final_utf8_bytes = len(context.content.encode("utf-8"))

    assert final_utf8_bytes <= MAX_DYNAMIC_CONTEXT_UTF8_BYTES
    assert final_utf8_bytes >= context.estimated_dynamic_tokens * 3


@pytest.mark.parametrize(
    "canvas_context",
    [
        "draw anything",
        '<active_canvas trust="wrong">bad</canvas_context>',
        '<canvas_context trust="backend_semantic_canvas_metadata">' + ("x" * 2_500),
    ],
)
def test_canvas_declared_trust_boundary_is_unchanged(canvas_context: str) -> None:
    with pytest.raises(ValueError, match="canvas_context"):
        _assemble(canvas_context=canvas_context)
