from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ava_backend.realtime.memory import (
    MAX_SEMANTIC_MEMORY_PROPOSAL_UTF8_BYTES,
    DeliveredConversationTurn,
    DevelopmentAuthoritativeMemoryStore,
    MemoryResetRevisionMismatchError,
    SemanticJournalConflictError,
    SemanticJournalEventProposal,
    SemanticJournalEventRecord,
    SemanticMemoryProposal,
    SemanticMemoryProposalConflictError,
    SemanticMemorySourceTurnMissingError,
    canonical_semantic_journal_event_fingerprint,
    canonical_semantic_memory_proposal_fingerprint,
    semantic_journal_created_event_id,
    semantic_journal_identity_key,
)


def _turn(turn_id: str) -> DeliveredConversationTurn:
    return DeliveredConversationTurn.model_validate(
        {
            "turnId": turn_id,
            "childText": "I like Jupiter.",
            "avaDeliveredText": "Jupiter is a fascinating planet.",
            "deliveredSampleCount": 24_000,
            "childTurnSummary": "Discussed a planet preference.",
            "terminal": "completed",
        }
    )


async def _commit_turn(
    store: DevelopmentAuthoritativeMemoryStore,
    turn_id: str,
    *,
    subject: str = "child:semantic",
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


def _event(
    summary: str = "The child prefers Jupiter.",
    *,
    family: str = "userPreference",
    semantic_key: str = "favorite_planet",
    canvas_references: list[dict[str, object]] | None = None,
    supersedes: list[str] | None = None,
) -> SemanticJournalEventProposal:
    return SemanticJournalEventProposal.model_validate(
        {
            "family": family,
            "semanticKey": semantic_key,
            "summary": summary,
            "canonicalCanvasReferences": canvas_references or [],
            "supersedesEventIds": supersedes or [],
        }
    )


def _proposal(
    *events: SemanticJournalEventProposal,
    base_revision: int | None = None,
    topic: str = "Planets",
) -> SemanticMemoryProposal:
    payload: dict[str, object] = {
        "journalEvents": [
            event.model_dump(mode="json", by_alias=True) for event in events
        ]
    }
    if base_revision is not None:
        payload["dialogueFrameUpdate"] = {
            "baseRevision": base_revision,
            "frame": {"activeTopics": [topic]},
        }
    return SemanticMemoryProposal.model_validate(payload)


def _records(
    store: DevelopmentAuthoritativeMemoryStore,
    subject: str = "child:semantic",
) -> tuple[SemanticJournalEventRecord, ...]:
    return tuple(store._semantic_journal_events.get(subject, {}).values())


def test_semantic_journal_model_bounds_and_typed_family() -> None:
    event = _event(
        canvas_references=[
            {"sceneId": "scene-jupiter", "sceneRevision": 2, "relation": "orbit"}
        ]
    )
    assert event.family == "userPreference"
    assert event.semantic_key == "favorite_planet"

    with pytest.raises(ValidationError):
        _event(family="miscellaneous")
    with pytest.raises(ValidationError):
        _event(semantic_key="Favorite Planet")
    with pytest.raises(ValidationError):
        _event("x" * 241)
    with pytest.raises(ValidationError):
        _event(
            canvas_references=[
                {"sceneId": f"scene-{index}", "sceneRevision": 1} for index in range(5)
            ]
        )
    with pytest.raises(ValidationError):
        _event(supersedes=[f"sje-{'a' * 59}{index}" for index in range(5)])
    duplicate_id = f"sje-{'a' * 60}"
    with pytest.raises(ValidationError, match="duplicates"):
        _event(supersedes=[duplicate_id, duplicate_id])


def test_semantic_proposal_requires_content_and_unique_identities() -> None:
    with pytest.raises(ValidationError, match="requires"):
        SemanticMemoryProposal.model_validate({})
    with pytest.raises(ValidationError, match="duplicate semantic identities"):
        _proposal(_event(), _event("The child really prefers Jupiter."))


def test_semantic_proposal_total_utf8_budget_is_enforced() -> None:
    events = [
        _event(
            "界" * 240,
            family=family,
            semantic_key=f"large_{index}",
            canvas_references=[
                {
                    "sceneId": f"scene-{index}-{reference}",
                    "sceneRevision": 1,
                    "relation": "界" * 160,
                }
                for reference in range(4)
            ],
        )
        for index, family in enumerate(
            ["userPreference", "avaCommitment", "userFact", "canvasReference"]
        )
    ]
    with pytest.raises(
        ValidationError, match=str(MAX_SEMANTIC_MEMORY_PROPOSAL_UTF8_BYTES)
    ):
        _proposal(*events)


def test_identity_fingerprints_and_event_id_are_deterministic() -> None:
    first = _event()
    exact = SemanticJournalEventProposal.model_validate(
        {
            "summary": "The child prefers Jupiter.",
            "semanticKey": "favorite_planet",
            "family": "userPreference",
        }
    )
    changed = _event("The child prefers Saturn.")
    first_proposal = _proposal(first, base_revision=0)
    exact_proposal = SemanticMemoryProposal.model_validate(
        first_proposal.model_dump(mode="json", by_alias=True)
    )

    assert semantic_journal_identity_key(first.family, first.semantic_key) == (
        "userPreference:favorite_planet"
    )
    assert canonical_semantic_journal_event_fingerprint(first) == (
        canonical_semantic_journal_event_fingerprint(exact)
    )
    assert canonical_semantic_journal_event_fingerprint(first) != (
        canonical_semantic_journal_event_fingerprint(changed)
    )
    assert canonical_semantic_memory_proposal_fingerprint(first_proposal) == (
        canonical_semantic_memory_proposal_fingerprint(exact_proposal)
    )
    fingerprint = canonical_semantic_journal_event_fingerprint(first)
    identity = semantic_journal_identity_key(first.family, first.semantic_key)
    assert semantic_journal_created_event_id(
        subject="child:semantic",
        reset_revision=0,
        source_turn_id="turn-1",
        identity_key=identity,
        event_fingerprint=fingerprint,
    ) == semantic_journal_created_event_id(
        subject="child:semantic",
        reset_revision=0,
        source_turn_id="turn-1",
        identity_key=identity,
        event_fingerprint=fingerprint,
    )
    assert semantic_journal_created_event_id(
        subject="child:semantic",
        reset_revision=0,
        source_turn_id="turn-1",
        identity_key=identity,
        event_fingerprint=fingerprint,
    ) != semantic_journal_created_event_id(
        subject="child:semantic",
        reset_revision=0,
        source_turn_id="turn-2",
        identity_key=identity,
        event_fingerprint=fingerprint,
    )


async def test_source_turn_is_required_before_any_semantic_mutation() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    with pytest.raises(SemanticMemorySourceTurnMissingError):
        await store.apply_semantic_memory_proposal(
            subject="child:semantic",
            expected_reset_revision=0,
            source_turn_id="turn-1",
            proposal=_proposal(_event(), base_revision=0),
        )
    snapshot = await store.load_snapshot(
        subject="child:semantic", age_band="middleChildhood"
    )
    assert snapshot.dialogue_frame.revision == 0
    assert _records(store) == ()


async def test_atomic_frame_and_journal_apply_and_exact_retry() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    await _commit_turn(store, "turn-1")
    proposal = _proposal(_event(), base_revision=0)

    applied = await store.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-1",
        proposal=proposal,
    )
    retry = await store.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-1",
        proposal=proposal,
    )

    assert applied.status == "applied"
    assert applied.dialogue_frame.revision == 1
    assert applied.journal_results[0].disposition == "created"
    assert retry.status == "idempotent"
    assert retry.dialogue_frame.revision == 1
    records = _records(store)
    assert len(records) == 1
    assert store._semantic_journal_sources["child:semantic"][records[0].event_id] == {
        "turn-1"
    }

    m1_retry = await store.apply_dialogue_frame_update(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-1",
        update=proposal.dialogue_frame_update,  # type: ignore[arg-type]
    )
    assert m1_retry.status == "idempotent"
    assert m1_retry.frame.revision == 1


async def test_conflicting_retry_and_journal_conflict_roll_back_frame() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    await _commit_turn(store, "turn-1")
    await store.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-1",
        proposal=_proposal(_event()),
    )
    with pytest.raises(SemanticMemoryProposalConflictError):
        await store.apply_semantic_memory_proposal(
            subject="child:semantic",
            expected_reset_revision=0,
            source_turn_id="turn-1",
            proposal=_proposal(_event(semantic_key="favorite_moon")),
        )

    await _commit_turn(store, "turn-2")
    with pytest.raises(SemanticJournalConflictError, match="explicit supersession"):
        await store.apply_semantic_memory_proposal(
            subject="child:semantic",
            expected_reset_revision=0,
            source_turn_id="turn-2",
            proposal=_proposal(_event("The child prefers Saturn."), base_revision=0),
        )
    snapshot = await store.load_snapshot(
        subject="child:semantic", age_band="middleChildhood"
    )
    assert snapshot.dialogue_frame.revision == 0
    assert len(_records(store)) == 1


async def test_stale_frame_rejects_entire_semantic_proposal_without_receipt() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    await _commit_turn(store, "turn-1")
    await store.apply_dialogue_frame_update(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-1",
        update=_proposal(base_revision=0).dialogue_frame_update,  # type: ignore[arg-type]
    )
    await _commit_turn(store, "turn-2")
    stale_proposal = _proposal(
        _event(semantic_key="favorite_moon"), base_revision=0, topic="Moons"
    )
    stale = await store.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-2",
        proposal=stale_proposal,
    )
    assert stale.status == "stale"
    assert stale.dialogue_frame.revision == 1
    assert _records(store) == ()

    journal_only = _proposal(_event(semantic_key="favorite_moon"))
    applied = await store.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-2",
        proposal=journal_only,
    )
    assert applied.status == "applied"


async def test_exact_event_deduplicates_across_turns_and_attaches_provenance() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    proposal = _proposal(_event())
    await _commit_turn(store, "turn-a")
    await store.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-a",
        proposal=proposal,
    )
    await _commit_turn(store, "turn-z")
    decision = await store.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-z",
        proposal=proposal,
    )
    assert decision.journal_results[0].disposition == "deduplicated"
    records = _records(store)
    assert len(records) == 1
    assert store._semantic_journal_sources["child:semantic"][records[0].event_id] == {
        "turn-a",
        "turn-z",
    }


async def test_superseded_content_can_recur_as_a_new_immutable_occurrence() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    await _commit_turn(store, "turn-a")
    first = await store.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-a",
        proposal=_proposal(_event()),
    )
    first_id = first.journal_results[0].event.event_id
    first_payload = first.journal_results[0].event.model_dump(
        mode="json", by_alias=True
    )

    await _commit_turn(store, "turn-b")
    second = await store.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-b",
        proposal=_proposal(_event("The child prefers Saturn.", supersedes=[first_id])),
    )
    second_id = second.journal_results[0].event.event_id
    second_payload = second.journal_results[0].event.model_dump(
        mode="json", by_alias=True
    )

    await _commit_turn(store, "turn-c")
    third = await store.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-c",
        proposal=_proposal(_event(supersedes=[second_id])),
    )
    third_id = third.journal_results[0].event.event_id

    records = {record.event_id: record for record in _records(store)}
    assert third.journal_results[0].disposition == "created"
    assert first_id != third_id
    assert len(records) == 3
    assert records[first_id].model_dump(mode="json", by_alias=True) == first_payload
    assert records[second_id].model_dump(mode="json", by_alias=True) == second_payload
    assert store._semantic_journal_supersessions["child:semantic"] == {
        first_id: {second_id},
        second_id: {third_id},
    }
    superseded = set(store._semantic_journal_supersessions["child:semantic"])
    assert set(records).difference(superseded) == {third_id}


async def test_one_proposal_cannot_assert_and_supersede_the_same_event() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    await _commit_turn(store, "turn-a")
    first = await store.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-a",
        proposal=_proposal(_event()),
    )
    first_id = first.journal_results[0].event.event_id

    await _commit_turn(store, "turn-b")
    with pytest.raises(SemanticJournalConflictError, match="assert and supersede"):
        await store.apply_semantic_memory_proposal(
            subject="child:semantic",
            expected_reset_revision=0,
            source_turn_id="turn-b",
            proposal=_proposal(
                _event(
                    "The child likes astronomy.",
                    semantic_key="favorite_activity",
                    supersedes=[first_id],
                ),
                _event(),
            ),
        )

    assert len(_records(store)) == 1
    assert store._semantic_journal_supersessions["child:semantic"] == {}
    assert "turn-b" not in store._semantic_memory_receipts["child:semantic"]


async def test_explicit_supersession_preserves_old_event_and_rejects_invalid_targets() -> (
    None
):
    store = DevelopmentAuthoritativeMemoryStore()
    await _commit_turn(store, "turn-a")
    first = await store.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-a",
        proposal=_proposal(_event()),
    )
    first_id = first.journal_results[0].event.event_id
    await _commit_turn(store, "turn-b")
    replacement = await store.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-b",
        proposal=_proposal(_event("The child prefers Saturn.", supersedes=[first_id])),
    )
    replacement_id = replacement.journal_results[0].event.event_id
    records = _records(store)
    assert len(records) == 2
    assert records[0].summary == "The child prefers Jupiter."
    assert store._semantic_journal_supersessions["child:semantic"][first_id] == {
        replacement_id
    }

    await _commit_turn(store, "turn-c")
    with pytest.raises(SemanticJournalConflictError, match="not active"):
        await store.apply_semantic_memory_proposal(
            subject="child:semantic",
            expected_reset_revision=0,
            source_turn_id="turn-c",
            proposal=_proposal(
                _event("The child prefers Mars.", supersedes=[first_id])
            ),
        )
    await _commit_turn(store, "turn-d")
    with pytest.raises(SemanticJournalConflictError, match="does not exist"):
        await store.apply_semantic_memory_proposal(
            subject="child:semantic",
            expected_reset_revision=0,
            source_turn_id="turn-d",
            proposal=_proposal(
                _event(
                    "The child prefers Venus.",
                    supersedes=[f"sje-{'f' * 60}"],
                )
            ),
        )

    await _commit_turn(store, "turn-other", subject="child:other")
    other = await store.apply_semantic_memory_proposal(
        subject="child:other",
        expected_reset_revision=0,
        source_turn_id="turn-other",
        proposal=_proposal(_event(semantic_key="other_preference")),
    )
    await _commit_turn(store, "turn-e")
    with pytest.raises(SemanticJournalConflictError, match="another subject"):
        await store.apply_semantic_memory_proposal(
            subject="child:semantic",
            expected_reset_revision=0,
            source_turn_id="turn-e",
            proposal=_proposal(
                _event(
                    "The child prefers Neptune.",
                    supersedes=[other.journal_results[0].event.event_id],
                )
            ),
        )

    self_event = _event(semantic_key="favorite_dwarf_planet")
    self_identity = semantic_journal_identity_key(
        self_event.family, self_event.semantic_key
    )
    self_id = semantic_journal_created_event_id(
        subject="child:semantic",
        reset_revision=0,
        source_turn_id="turn-f",
        identity_key=self_identity,
        event_fingerprint=canonical_semantic_journal_event_fingerprint(self_event),
    )
    await _commit_turn(store, "turn-f")
    with pytest.raises(SemanticJournalConflictError, match="itself"):
        await store.apply_semantic_memory_proposal(
            subject="child:semantic",
            expected_reset_revision=0,
            source_turn_id="turn-f",
            proposal=_proposal(
                _event(
                    semantic_key="favorite_dwarf_planet",
                    supersedes=[self_id],
                )
            ),
        )


async def test_subject_isolation_and_reset_clear_all_semantic_state() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    await _commit_turn(store, "turn-a", subject="child:a")
    await store.apply_semantic_memory_proposal(
        subject="child:a",
        expected_reset_revision=0,
        source_turn_id="turn-a",
        proposal=_proposal(_event()),
    )
    old_event_id = next(iter(store._semantic_journal_events["child:a"]))
    assert _records(store, "child:b") == ()

    receipt = await store.reset_conversation(subject="child:a")
    assert receipt.revision == 1
    snapshot = await store.load_snapshot(subject="child:a", age_band="middleChildhood")
    assert snapshot.dialogue_frame.revision == 0
    assert _records(store, "child:a") == ()
    await _commit_turn(store, "turn-new", subject="child:a", reset_revision=1)
    with pytest.raises(SemanticJournalConflictError, match="current reset revision"):
        await store.apply_semantic_memory_proposal(
            subject="child:a",
            expected_reset_revision=1,
            source_turn_id="turn-new",
            proposal=_proposal(
                _event(
                    "The child now prefers Saturn.",
                    supersedes=[old_event_id],
                )
            ),
        )
    with pytest.raises(MemoryResetRevisionMismatchError):
        await store.apply_semantic_memory_proposal(
            subject="child:a",
            expected_reset_revision=0,
            source_turn_id="turn-a",
            proposal=_proposal(_event()),
        )
