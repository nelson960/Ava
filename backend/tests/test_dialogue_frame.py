from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ava_backend.realtime.memory import (
    MAX_DIALOGUE_FRAME_UTF8_BYTES,
    DevelopmentAuthoritativeMemoryStore,
    DialogueFrame,
    DialogueFrameSourceTurnMissingError,
    DialogueFrameState,
    DialogueFrameUpdate,
    DialogueFrameUpdateConflictError,
    DeliveredConversationTurn,
    MemoryResetRevisionMismatchError,
    RAW_RETENTION_HIGH_WATER,
    canonical_dialogue_frame_update_fingerprint,
)


def _turn(turn_id: str) -> DeliveredConversationTurn:
    return DeliveredConversationTurn.model_validate(
        {
            "turnId": turn_id,
            "childText": "Tell me about the moon.",
            "avaDeliveredText": "The moon travels around Earth.",
            "deliveredSampleCount": 24_000,
            "childTurnSummary": "Discussed the moon.",
            "terminal": "completed",
        }
    )


async def _commit(
    store: DevelopmentAuthoritativeMemoryStore,
    *,
    subject: str,
    turn_id: str,
    reset_revision: int = 0,
) -> None:
    await store.commit_delivered_turn(
        subject=subject,
        expected_reset_revision=reset_revision,
        turn=_turn(turn_id),
        proposal=None,
        memory_id=None,
        occurred_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def _update(base_revision: int, topic: str) -> DialogueFrameUpdate:
    return DialogueFrameUpdate.model_validate(
        {"baseRevision": base_revision, "frame": {"activeTopics": [topic]}}
    )


def test_empty_dialogue_frame_is_revision_zero() -> None:
    frame = DialogueFrame()

    assert frame.revision == 0
    assert frame.active_topics == ()
    assert frame.relevant_canvas_references == ()


def test_populated_dialogue_frame_is_bounded_and_normalized() -> None:
    frame = DialogueFrame.model_validate(
        {
            "revision": 3,
            "activeTopics": ["  The solar system  "],
            "recentEntities": ["Moon", "Earth"],
            "relevantCanvasReferences": [
                {"sceneId": "scene-moon", "sceneRevision": 2, "relation": "Orbit"}
            ],
            "recentToolReferences": ["Looked up the current time"],
        }
    )

    assert frame.active_topics == ("The solar system",)
    assert frame.relevant_canvas_references[0].scene_revision == 2


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_semantic_item_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        DialogueFrameState.model_validate({"activeTopics": [value]})


def test_semantic_item_length_and_list_count_are_bounded() -> None:
    with pytest.raises(ValidationError):
        DialogueFrameState.model_validate({"activeTopics": ["x" * 161]})
    with pytest.raises(ValidationError):
        DialogueFrameState.model_validate(
            {"activeGoals": [f"goal-{index}" for index in range(5)]}
        )


def test_combined_serialized_frame_budget_is_enforced() -> None:
    oversized = {
        "activeTopics": [f"{index}" + "a" * 159 for index in range(4)],
        "activeGoals": [f"{index}" + "b" * 159 for index in range(4)],
        "unresolvedQuestions": [f"{index}" + "c" * 159 for index in range(4)],
        "currentReferents": [f"{index}" + "d" * 159 for index in range(4)],
        "recentEntities": [f"{index}" + "e" * 159 for index in range(8)],
        "avaCommitments": [f"{index}" + "f" * 159 for index in range(4)],
    }

    with pytest.raises(ValidationError, match=str(MAX_DIALOGUE_FRAME_UTF8_BYTES)):
        DialogueFrameState.model_validate(oversized)


def test_case_normalized_duplicates_are_rejected_without_semantic_merging() -> None:
    with pytest.raises(ValidationError, match="case-normalized duplicates"):
        DialogueFrameState.model_validate({"activeTopics": ["The Moon", "  the moon "]})
    distinct = DialogueFrameState.model_validate(
        {"activeTopics": ["Moon phases", "How the Moon looks"]}
    )
    assert len(distinct.active_topics) == 2


def test_canvas_reference_and_update_revision_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        DialogueFrameState.model_validate(
            {
                "relevantCanvasReferences": [
                    {"sceneId": "scene-a", "sceneRevision": 0, "relation": "Shown"}
                ]
            }
        )
    with pytest.raises(ValidationError):
        DialogueFrameUpdate.model_validate({"baseRevision": -1, "frame": {}})


def test_update_fingerprint_is_canonical_and_order_sensitive() -> None:
    first = DialogueFrameUpdate.model_validate(
        {"baseRevision": 0, "frame": {"activeTopics": ["Moon", "Earth"]}}
    )
    exact = DialogueFrameUpdate.model_validate(
        {"frame": {"activeTopics": ["Moon", "Earth"]}, "baseRevision": 0}
    )
    reordered = DialogueFrameUpdate.model_validate(
        {"baseRevision": 0, "frame": {"activeTopics": ["Earth", "Moon"]}}
    )

    assert canonical_dialogue_frame_update_fingerprint(first) == (
        canonical_dialogue_frame_update_fingerprint(exact)
    )
    assert canonical_dialogue_frame_update_fingerprint(first) != (
        canonical_dialogue_frame_update_fingerprint(reordered)
    )


async def test_development_store_requires_source_turn_then_applies_cas() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    update = _update(0, "Moon")

    with pytest.raises(DialogueFrameSourceTurnMissingError):
        await store.apply_dialogue_frame_update(
            subject="child:a",
            expected_reset_revision=0,
            source_turn_id="turn-1",
            update=update,
        )
    await _commit(store, subject="child:a", turn_id="turn-1")
    first = await store.apply_dialogue_frame_update(
        subject="child:a",
        expected_reset_revision=0,
        source_turn_id="turn-1",
        update=update,
    )
    await _commit(store, subject="child:a", turn_id="turn-2")
    second = await store.apply_dialogue_frame_update(
        subject="child:a",
        expected_reset_revision=0,
        source_turn_id="turn-2",
        update=_update(1, "Earth"),
    )

    assert first.status == "applied"
    assert first.frame.revision == 1
    assert second.frame.revision == 2
    assert second.frame.active_topics == ("Earth",)


async def test_development_store_stale_idempotent_and_conflicting_retries() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    await _commit(store, subject="child:a", turn_id="turn-1")
    update = _update(0, "Moon")
    applied = await store.apply_dialogue_frame_update(
        subject="child:a",
        expected_reset_revision=0,
        source_turn_id="turn-1",
        update=update,
    )
    exact_retry = await store.apply_dialogue_frame_update(
        subject="child:a",
        expected_reset_revision=0,
        source_turn_id="turn-1",
        update=update,
    )
    with pytest.raises(DialogueFrameUpdateConflictError):
        await store.apply_dialogue_frame_update(
            subject="child:a",
            expected_reset_revision=0,
            source_turn_id="turn-1",
            update=_update(0, "Earth"),
        )
    await _commit(store, subject="child:a", turn_id="turn-2")
    stale = await store.apply_dialogue_frame_update(
        subject="child:a",
        expected_reset_revision=0,
        source_turn_id="turn-2",
        update=_update(0, "Mars"),
    )

    assert applied.frame.revision == 1
    assert exact_retry.status == "idempotent"
    assert exact_retry.frame.revision == 1
    assert stale.status == "stale"
    assert stale.frame.active_topics == ("Moon",)


async def test_development_store_reset_and_subject_isolation() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    await _commit(store, subject="child:a", turn_id="turn-1")
    await store.apply_dialogue_frame_update(
        subject="child:a",
        expected_reset_revision=0,
        source_turn_id="turn-1",
        update=_update(0, "Moon"),
    )

    other = await store.load_snapshot(subject="child:b", age_band="middleChildhood")
    receipt = await store.reset_conversation(subject="child:a")
    reset = await store.load_snapshot(subject="child:a", age_band="middleChildhood")

    assert other.dialogue_frame == DialogueFrame()
    assert receipt.revision == 1
    assert reset.dialogue_frame == DialogueFrame()
    with pytest.raises(MemoryResetRevisionMismatchError):
        await store.apply_dialogue_frame_update(
            subject="child:a",
            expected_reset_revision=0,
            source_turn_id="turn-1",
            update=_update(0, "Moon"),
        )


async def test_development_receipts_survive_evidence_compaction_and_pruning() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    subject = "child:receipt-retention"
    old_base = datetime.now(UTC) - timedelta(days=181)
    source_update = _update(0, "Moon")
    await store.commit_delivered_turn(
        subject=subject,
        expected_reset_revision=0,
        turn=_turn("turn-source"),
        proposal=None,
        memory_id=None,
        occurred_at=old_base,
    )
    await store.apply_dialogue_frame_update(
        subject=subject,
        expected_reset_revision=0,
        source_turn_id="turn-source",
        update=source_update,
    )

    for index in range(1, RAW_RETENTION_HIGH_WATER + 1):
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(f"turn-retention-{index}"),
            proposal=None,
            memory_id=None,
            occurred_at=old_base + timedelta(minutes=index),
        )

    retained_evidence_ids = {event.turn.turn_id for event in store._turns[subject]} | {
        turn_id
        for episode in store._episodes[subject]
        for turn_id in episode.source_turn_ids
    }
    assert "turn-source" not in retained_evidence_ids
    with pytest.raises(ValueError, match="already committed"):
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn("turn-source"),
            proposal=None,
            memory_id=None,
        )

    exact_retry = await store.apply_dialogue_frame_update(
        subject=subject,
        expected_reset_revision=0,
        source_turn_id="turn-source",
        update=source_update,
    )
    assert exact_retry.status == "idempotent"
    assert exact_retry.frame.active_topics == ("Moon",)
    with pytest.raises(DialogueFrameUpdateConflictError):
        await store.apply_dialogue_frame_update(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-source",
            update=_update(0, "Earth"),
        )
