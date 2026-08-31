from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ava_backend.realtime.memory import (
    AuthoritativeMemorySnapshot,
    ChildTurnTextSource,
    DeliveredConversationTurn,
    DevelopmentAuthoritativeMemoryStore,
    MemoryResetRevisionMismatchError,
    evaluate_memory_proposal,
)
from ava_backend.realtime.memory import AvaMemoryProposal


def _turn(
    *,
    turn_id: str = "turn-1",
    child_text: str = "I love building model airplanes.",
    delivered_text: str = "Model airplanes have so many clever little parts.",
) -> DeliveredConversationTurn:
    return DeliveredConversationTurn.model_validate(
        {
            "turnId": turn_id,
            "childText": child_text,
            "avaDeliveredText": delivered_text,
            "deliveredSampleCount": 24000 if delivered_text else 0,
            "childTurnSummary": "The child shared an interest.",
            "terminal": "completed",
        }
    )


def _proposal(
    *,
    summary: str = "The child enjoys building model airplanes.",
    evidence: str = "I love building model airplanes.",
    confidence: float = 0.9,
) -> AvaMemoryProposal:
    return AvaMemoryProposal.model_validate(
        {
            "kind": "interest",
            "summary": summary,
            "evidence": evidence,
            "confidence": confidence,
        }
    )


async def test_v3_store_commits_only_the_alignment_derived_delivered_prefix() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    turn = _turn(
        delivered_text="Model airplanes have",
    )

    decision = await store.commit_delivered_turn(
        subject="child:123",
        expected_reset_revision=0,
        turn=turn,
        proposal=_proposal(),
        memory_id="memory-1",
    )
    snapshot = await store.load_snapshot(
        subject="child:123",
        age_band="middleChildhood",
    )

    assert decision.accepted is True
    assert snapshot.delivered_turns[0].ava_delivered_text == ("Model airplanes have")
    assert (
        "so many clever little parts"
        not in snapshot.delivered_turns[0].ava_delivered_text
    )
    assert snapshot.stable_memories[0].source_turn_id == "turn-1"


async def test_v3_store_rejects_duplicate_turn_commit_and_reset_clears_context() -> (
    None
):
    store = DevelopmentAuthoritativeMemoryStore()
    await store.commit_delivered_turn(
        subject="child:123",
        expected_reset_revision=0,
        turn=_turn(),
        proposal=None,
        memory_id=None,
    )
    with pytest.raises(ValueError, match="already committed"):
        await store.commit_delivered_turn(
            subject="child:123",
            expected_reset_revision=0,
            turn=_turn(),
            proposal=None,
            memory_id=None,
        )

    await store.set_session_summary(
        subject="child:123",
        expected_reset_revision=0,
        summary="A bounded factual summary.",
    )
    receipt = await store.reset_conversation(subject="child:123")
    snapshot = await store.load_snapshot(
        subject="child:123",
        age_band="middleChildhood",
    )
    assert snapshot.delivered_turns == []
    assert snapshot.stable_memories == []
    assert snapshot.current_session_summary is None
    assert receipt.revision == 1
    second_receipt = await store.reset_conversation(subject="child:123")
    assert second_receipt.revision == 2


async def test_v3_store_compacts_evicted_turns_without_a_model_call() -> None:
    store = DevelopmentAuthoritativeMemoryStore(max_turns_per_subject=2)
    for index in range(1, 4):
        await store.commit_delivered_turn(
            subject="child:123",
            expected_reset_revision=0,
            turn=_turn(
                turn_id=f"turn-{index}",
                child_text=f"Child statement {index}.",
                delivered_text=f"Ava response {index}.",
            ).model_copy(update={"child_turn_summary": f"Topic {index}."}),
            proposal=None,
            memory_id=None,
        )

    snapshot = await store.load_snapshot(
        subject="child:123",
        age_band="middleChildhood",
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == [
        "turn-2",
        "turn-3",
    ]
    assert snapshot.current_session_summary == "Topic 1."


async def test_v3_weekly_recap_is_bounded_factual_and_resettable() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    await store.commit_delivered_turn(
        subject="child:123",
        expected_reset_revision=0,
        turn=_turn().model_copy(
            update={"child_turn_summary": "Talked about model airplanes."}
        ),
        proposal=None,
        memory_id=None,
    )
    now = datetime.now(UTC)
    period_start = now.date() - timedelta(days=now.weekday())
    recap = await store.load_weekly_recap(
        subject="child:123",
        period_start=period_start,
        generated_at=now,
    )
    assert [record.summary for record in recap.records] == [
        "Talked about model airplanes."
    ]
    assert recap.reset_revision == 0

    await store.reset_conversation(subject="child:123")
    reset_recap = await store.load_weekly_recap(
        subject="child:123",
        period_start=period_start,
        generated_at=now,
    )
    assert reset_recap.records == []
    assert reset_recap.reset_revision == 1


async def test_v3_rejects_sensitive_weekly_summary_without_partial_commit() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    with pytest.raises(ValueError, match="sensitive"):
        await store.commit_delivered_turn(
            subject="child:123",
            expected_reset_revision=0,
            turn=_turn().model_copy(
                update={"child_turn_summary": "Home address is 12 River Street."}
            ),
            proposal=None,
            memory_id=None,
        )
    snapshot = await store.load_snapshot(
        subject="child:123",
        age_band="middleChildhood",
    )
    assert snapshot.delivered_turns == []


async def test_v3_rejects_emotional_certainty_in_recap_and_session_summary() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    with pytest.raises(ValueError, match="hidden inference"):
        await store.commit_delivered_turn(
            subject="child:123",
            expected_reset_revision=0,
            turn=_turn().model_copy(
                update={"child_turn_summary": "The child was sad."}
            ),
            proposal=None,
            memory_id=None,
        )
    with pytest.raises(ValueError, match="hidden inference"):
        await store.set_session_summary(
            subject="child:123",
            expected_reset_revision=0,
            summary="The child seems angry.",
        )


async def test_v3_rejects_semantically_duplicate_stable_memory() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    await store.commit_delivered_turn(
        subject="child:123",
        expected_reset_revision=0,
        turn=_turn(turn_id="turn-1"),
        proposal=_proposal(),
        memory_id="memory-1",
    )
    decision = await store.commit_delivered_turn(
        subject="child:123",
        expected_reset_revision=0,
        turn=_turn(turn_id="turn-2"),
        proposal=_proposal(summary="THE CHILD ENJOYS BUILDING MODEL AIRPLANES."),
        memory_id="memory-2",
    )
    assert decision.accepted is False
    assert "equivalent" in decision.reason.lower()


async def test_memory_capacity_rejects_proposal_but_keeps_delivered_turn() -> None:
    store = DevelopmentAuthoritativeMemoryStore(max_memories_per_subject=1)
    await store.commit_delivered_turn(
        subject="child:123",
        expected_reset_revision=0,
        turn=_turn(turn_id="turn-1"),
        proposal=_proposal(),
        memory_id="memory-1",
    )

    second_turn = _turn(
        turn_id="turn-2",
        child_text="I prefer short stories.",
        delivered_text="I can keep stories short.",
    )
    second_proposal = AvaMemoryProposal.model_validate(
        {
            "kind": "preference",
            "summary": "The child prefers short stories.",
            "evidence": "I prefer short stories.",
            "confidence": 0.9,
        }
    )
    decision = await store.commit_delivered_turn(
        subject="child:123",
        expected_reset_revision=0,
        turn=second_turn,
        proposal=second_proposal,
        memory_id="memory-2",
    )
    snapshot = await store.load_snapshot(
        subject="child:123",
        age_band="middleChildhood",
    )

    assert decision.accepted is False
    assert "capacity" in decision.reason.lower()
    assert [turn.turn_id for turn in snapshot.delivered_turns] == [
        "turn-1",
        "turn-2",
    ]
    assert [memory.id for memory in snapshot.stable_memories] == ["memory-1"]


@pytest.mark.parametrize(
    ("proposal", "child_text", "expected_reason"),
    (
        (
            _proposal(confidence=0.79),
            "I love building model airplanes.",
            "confidence",
        ),
        (
            _proposal(evidence="The child likes airplanes."),
            "I love building model airplanes.",
            "exact excerpt",
        ),
        (
            _proposal(
                summary="The child's home address is 12 River Street.",
                evidence="My home address is 12 River Street.",
            ),
            "My home address is 12 River Street.",
            "sensitive",
        ),
        (
            _proposal(
                summary="The child is gifted.",
                evidence="I got a good score.",
            ),
            "I got a good score.",
            "hidden or diagnostic",
        ),
    ),
)
def test_memory_policy_rejects_weak_sensitive_or_inferred_proposals(
    proposal: AvaMemoryProposal,
    child_text: str,
    expected_reason: str,
) -> None:
    decision = evaluate_memory_proposal(
        proposal=proposal,
        child_text=child_text,
        source_turn_id="turn-1",
        memory_id="memory-1",
    )
    assert decision.accepted is False
    assert expected_reason in decision.reason


def test_memory_policy_rejects_model_summary_as_exact_evidence() -> None:
    decision = evaluate_memory_proposal(
        proposal=_proposal(),
        child_text="The child enjoys model airplanes.",
        child_text_source=ChildTurnTextSource.MODEL_SUMMARY,
        source_turn_id="turn-1",
        memory_id="memory-1",
    )
    assert decision.accepted is False
    assert "transcript" in decision.reason


def test_delivered_turn_requires_audio_and_text_to_agree() -> None:
    payload = _turn().model_dump(mode="json", by_alias=True)
    payload["avaDeliveredText"] = ""
    immediate_stop = DeliveredConversationTurn.model_validate(payload)
    assert immediate_stop.delivered_sample_count > 0
    assert immediate_stop.ava_delivered_text == ""

    empty = _turn(delivered_text="")
    assert empty.delivered_sample_count == 0
    assert empty.ava_delivered_text == ""

    payload = empty.model_dump(mode="json", by_alias=True)
    payload["avaDeliveredText"] = "Unheard text"
    with pytest.raises(ValueError, match="positive"):
        DeliveredConversationTurn.model_validate(payload)


def test_canvas_interaction_is_a_typed_input_without_fabricated_child_text() -> None:
    turn = DeliveredConversationTurn.model_validate(
        {
            "turnId": "turn-interaction",
            "inputKind": "canvasInteraction",
            "childText": None,
            "childTextSource": None,
            "canvasInteraction": {
                "sceneId": "scene-A",
                "sceneRevision": 2,
                "interactionRevision": 7,
                "interactionMode": "explore",
                "bindingId": "binding-A",
                "targetId": "target-A",
                "accessibilityLabel": "target-A label",
                "stepIndex": None,
            },
            "avaDeliveredText": "A heard response.",
            "deliveredSampleCount": 24_000,
            "childTurnSummary": "Canvas interaction with target-A label.",
            "terminal": "completed",
        }
    )

    assert turn.input_kind == "canvasInteraction"
    assert turn.child_text is None
    assert turn.child_text_source is None
    assert turn.canvas_interaction is not None
    assert turn.canvas_interaction.binding_id == "binding-A"


async def test_canvas_interaction_never_creates_stable_memory_without_speech() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    interaction_turn = DeliveredConversationTurn.model_validate(
        {
            "turnId": "turn-interaction-stable",
            "inputKind": "canvasInteraction",
            "childText": None,
            "childTextSource": None,
            "canvasInteraction": {
                "sceneId": "scene-A",
                "sceneRevision": 1,
                "interactionRevision": 1,
                "interactionMode": "select",
                "bindingId": "binding-A",
                "targetId": "target-A",
                "accessibilityLabel": "target-A label",
                "stepIndex": None,
            },
            "avaDeliveredText": "",
            "deliveredSampleCount": 0,
            "childTurnSummary": "Canvas interaction.",
            "terminal": "cancelled",
        }
    )

    decision = await store.commit_delivered_turn(
        subject="child:interaction-stable",
        expected_reset_revision=0,
        turn=interaction_turn,
        proposal=_proposal(),
        memory_id="memory-from-tap",
    )
    snapshot = await store.load_snapshot(
        subject="child:interaction-stable",
        age_band="middleChildhood",
    )

    assert decision.accepted is False
    assert "speech transcript" in decision.reason
    assert snapshot.delivered_turns == [interaction_turn]
    assert snapshot.stable_memories == []


def test_snapshot_rejects_duplicate_records() -> None:
    turn = _turn().model_dump(mode="json", by_alias=True)
    with pytest.raises(ValueError, match="turn IDs"):
        AuthoritativeMemorySnapshot.model_validate(
            {
                "ageBand": "middleChildhood",
                "resetRevision": 0,
                "currentSessionSummary": None,
                "deliveredTurns": [turn, turn],
                "stableMemories": [],
            }
        )


async def test_stale_delivered_commit_after_reset_is_rejected_without_restore() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    snapshot = await store.load_snapshot(
        subject="child:reset-fence",
        age_band="middleChildhood",
    )
    assert snapshot.reset_revision == 0

    receipt = await store.reset_conversation(subject="child:reset-fence")
    assert receipt.revision == 1

    with pytest.raises(MemoryResetRevisionMismatchError) as caught:
        await store.commit_delivered_turn(
            subject="child:reset-fence",
            expected_reset_revision=snapshot.reset_revision,
            turn=_turn(turn_id="turn-before-reset"),
            proposal=None,
            memory_id=None,
        )

    assert caught.value.expected_reset_revision == 0
    assert caught.value.current_reset_revision == 1
    after = await store.load_snapshot(
        subject="child:reset-fence",
        age_band="middleChildhood",
    )
    assert after.reset_revision == 1
    assert after.delivered_turns == []
    assert after.stable_memories == []
    assert after.current_session_summary is None


async def test_stale_session_summary_after_reset_is_rejected() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    snapshot = await store.load_snapshot(
        subject="child:summary-reset-fence",
        age_band="middleChildhood",
    )
    await store.reset_conversation(subject="child:summary-reset-fence")

    with pytest.raises(MemoryResetRevisionMismatchError):
        await store.set_session_summary(
            subject="child:summary-reset-fence",
            expected_reset_revision=snapshot.reset_revision,
            summary="This stale summary must not return.",
        )

    after = await store.load_snapshot(
        subject="child:summary-reset-fence",
        age_band="middleChildhood",
    )
    assert after.current_session_summary is None


async def test_fresh_post_reset_epoch_writes_and_duplicate_behavior_is_unchanged() -> (
    None
):
    store = DevelopmentAuthoritativeMemoryStore()
    await store.reset_conversation(subject="child:fresh-reset-epoch")
    snapshot = await store.load_snapshot(
        subject="child:fresh-reset-epoch",
        age_band="middleChildhood",
    )
    assert snapshot.reset_revision == 1

    await store.commit_delivered_turn(
        subject="child:fresh-reset-epoch",
        expected_reset_revision=snapshot.reset_revision,
        turn=_turn(turn_id="turn-after-reset"),
        proposal=None,
        memory_id=None,
    )
    with pytest.raises(ValueError, match="already committed"):
        await store.commit_delivered_turn(
            subject="child:fresh-reset-epoch",
            expected_reset_revision=snapshot.reset_revision,
            turn=_turn(turn_id="turn-after-reset"),
            proposal=None,
            memory_id=None,
        )

    after = await store.load_snapshot(
        subject="child:fresh-reset-epoch",
        age_band="middleChildhood",
    )
    assert [turn.turn_id for turn in after.delivered_turns] == ["turn-after-reset"]
