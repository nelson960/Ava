from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
from types import MethodType, SimpleNamespace
from typing import cast

import pytest

from ava_backend.realtime.coordinator import (
    CoordinatorStateError,
    DeterministicRealtimeSessionCoordinator,
    _CapturedMemoryCommit,
    _TurnResult,
    _delivered_turn_from_memory_payload,
)
from ava_backend.realtime.context_assembler import (
    DynamicContextBuildReason,
    MemoryReference,
)
from ava_backend.realtime.memory import (
    ChildTurnTextSource,
    DeliveredTurnTerminal,
    DevelopmentAuthoritativeMemoryStore,
    DialogueFrame,
    MemorySearchCandidate,
    SemanticMemoryProposal,
)


class _Workspace:
    def __init__(self, scenes: dict[str, int] | None = None) -> None:
        self.scenes = scenes or {}

    def scene(self, scene_id: str):  # type: ignore[no-untyped-def]
        revision = self.scenes.get(scene_id)
        return SimpleNamespace(revision=revision) if revision is not None else None

    def manifest_scene(self, scene_id: str):  # type: ignore[no-untyped-def]
        del scene_id
        return None


class _CanvasJobs:
    def __init__(self, workspace: _Workspace) -> None:
        self.state = SimpleNamespace(workspace=workspace)

    async def reset_conversation(self) -> tuple[str, ...]:
        return ()


class _WarmSearchFailingMemory(DevelopmentAuthoritativeMemoryStore):
    async def search_memory_candidates(
        self,
        *,
        subject: str,
        query: str,
        limit: int = 5,
        from_instant: datetime | None = None,
        to_instant: datetime | None = None,
    ) -> list[MemorySearchCandidate]:
        del subject, query, limit, from_instant, to_instant
        raise RuntimeError("injected optional warm-memory failure")


def _proposal(
    *,
    summary: str = "The child prefers Jupiter.",
    scene_revision: int | None = None,
    frame: bool = False,
) -> SemanticMemoryProposal:
    event: dict[str, object] = {
        "family": "userPreference",
        "semanticKey": "favorite_planet",
        "summary": summary,
    }
    if scene_revision is not None:
        event["canonicalCanvasReferences"] = [
            {
                "sceneId": "scene-jupiter",
                "sceneRevision": scene_revision,
                "relation": "The discussed planet",
            }
        ]
    payload: dict[str, object] = {"journalEvents": [event]}
    if frame:
        payload["dialogueFrameUpdate"] = {
            "baseRevision": 0,
            "frame": {"activeTopics": ["Planets"]},
        }
    return SemanticMemoryProposal.model_validate(payload)


def _coordinator(
    *,
    memory: DevelopmentAuthoritativeMemoryStore | None = None,
    workspace: _Workspace | None = None,
) -> DeterministicRealtimeSessionCoordinator:
    coordinator = cast(
        DeterministicRealtimeSessionCoordinator,
        object.__new__(DeterministicRealtimeSessionCoordinator),
    )
    coordinator._session_id = "session-semantic"
    coordinator._turn_id = "turn-1"
    coordinator._subject = "child:semantic"
    coordinator._timezone_id = "UTC"
    coordinator._terminal = False
    coordinator._started = True
    coordinator._closed = False
    coordinator._memory = memory or DevelopmentAuthoritativeMemoryStore()
    coordinator._memory_reset_revision = 0
    coordinator._dialogue_frame = DialogueFrame()
    coordinator._staged_semantic_memory_proposal = None
    coordinator._canvas_jobs = _CanvasJobs(workspace or _Workspace())  # type: ignore[assignment]
    coordinator._canvas_context_lock = asyncio.Lock()
    coordinator._warm_refresh_revision = 0
    coordinator._warm_memories = ()
    coordinator._recent_live_echo_suppression_turn_ids = deque(maxlen=8)
    coordinator._last_memory_decision = None
    coordinator._memory_commit_tasks = {}
    coordinator._memory_commit_sequence = 0
    coordinator._latest_memory_commit_sequence = 0
    coordinator._failure_id_factory = lambda: "failure-semantic"
    coordinator._memory_snapshot = None
    coordinator._harness = None
    return coordinator


def _payload(
    proposal: SemanticMemoryProposal,
    *,
    turn_id: str = "turn-1",
) -> _CapturedMemoryCommit:
    return _CapturedMemoryCommit(
        sequence=1,
        turn_id=turn_id,
        occurred_at=datetime(2026, 9, 8, tzinfo=UTC),
        timezone_id="UTC",
        expected_reset_revision=0,
        child_turn_summary="Discussed a planet preference.",
        child_text="I like Jupiter.",
        child_text_source=ChildTurnTextSource.TRANSCRIPT,
        canvas_interaction=None,
        delivered_text="Jupiter is fascinating.",
        delivered_sample_count=24_000,
        terminal=DeliveredTurnTerminal.COMPLETED,
        transcript_waiter=None,
        warm_refresh_revision=1,
        semantic_memory_proposal=proposal,
    )


def _warm_reference() -> MemoryReference:
    instant = datetime(2026, 9, 7, tzinfo=UTC)
    return MemoryReference.model_validate(
        {
            "sourceKind": "episode",
            "startedAt": instant,
            "endedAt": instant,
            "timezoneIds": ["UTC"],
            "text": "The child previously discussed astronomy.",
            "sourceTurnIds": ["turn-history"],
        }
    )


async def _advance_store_to_frame_revision_one(
    memory: DevelopmentAuthoritativeMemoryStore,
) -> None:
    setup = _payload(_proposal(frame=True), turn_id="turn-setup")
    await memory.commit_delivered_turn(
        subject="child:semantic",
        expected_reset_revision=0,
        turn=_delivered_turn_from_memory_payload(
            setup,
            child_text=setup.child_text,
            child_text_source=setup.child_text_source,
        ),
        proposal=None,
        memory_id=None,
        occurred_at=setup.occurred_at,
    )
    decision = await memory.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-setup",
        proposal=setup.semantic_memory_proposal,  # type: ignore[arg-type]
    )
    assert decision.dialogue_frame.revision == 1


def test_semantic_staging_is_exact_idempotent_and_single_owner() -> None:
    coordinator = _coordinator()
    proposal = _proposal()
    with pytest.raises(CoordinatorStateError, match="active current turn"):
        coordinator.stage_semantic_memory_proposal(
            turn_id="turn-old", proposal=proposal
        )
    coordinator.stage_semantic_memory_proposal(turn_id="turn-1", proposal=proposal)
    coordinator.stage_semantic_memory_proposal(turn_id="turn-1", proposal=proposal)
    with pytest.raises(CoordinatorStateError, match="different"):
        coordinator.stage_semantic_memory_proposal(
            turn_id="turn-1", proposal=_proposal(summary="A changed proposal.")
        )
    with pytest.raises(CoordinatorStateError, match="different"):
        coordinator.stage_dialogue_frame_update(
            turn_id="turn-1",
            update=SemanticMemoryProposal.model_validate(
                {
                    "dialogueFrameUpdate": {
                        "baseRevision": 0,
                        "frame": {"activeTopics": ["Planets"]},
                    }
                }
            ).dialogue_frame_update,  # type: ignore[arg-type]
        )


def test_journal_canvas_reference_requires_exact_retained_revision() -> None:
    workspace = _Workspace({"scene-jupiter": 3})
    coordinator = _coordinator(workspace=workspace)
    coordinator.stage_semantic_memory_proposal(
        turn_id="turn-1", proposal=_proposal(scene_revision=3)
    )
    coordinator._staged_semantic_memory_proposal = None
    with pytest.raises(CoordinatorStateError, match="canonical retained"):
        coordinator.stage_semantic_memory_proposal(
            turn_id="turn-1", proposal=_proposal(scene_revision=2)
        )
    with pytest.raises(CoordinatorStateError, match="canonical retained"):
        _coordinator().stage_semantic_memory_proposal(
            turn_id="turn-1", proposal=_proposal(scene_revision=1)
        )


async def test_schedule_captures_immutable_proposal_and_clears_turn_owner() -> None:
    coordinator = _coordinator()
    coordinator._turn_occurred_at = datetime(2026, 9, 8, tzinfo=UTC)
    coordinator._turn_canvas_interaction_input = None
    coordinator._child_text = "I like Jupiter."
    coordinator._child_text_source = ChildTurnTextSource.TRANSCRIPT
    captured: list[_CapturedMemoryCommit] = []

    async def capture(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        payload: _CapturedMemoryCommit,
    ) -> None:
        captured.append(payload)

    coordinator._run_memory_commit = MethodType(capture, coordinator)  # type: ignore[method-assign]
    proposal = _proposal()
    coordinator.stage_semantic_memory_proposal(turn_id="turn-1", proposal=proposal)
    coordinator._schedule_memory_commit(
        turn_id="turn-1",
        plan=_TurnResult(segments=(), child_turn_summary="Discussed Jupiter."),
        delivered_sample_count=24_000,
        delivered_text="Jupiter is fascinating.",
        terminal=DeliveredTurnTerminal.COMPLETED,
    )
    await asyncio.gather(*coordinator._memory_commit_tasks.values())

    assert captured[0].semantic_memory_proposal == proposal
    assert coordinator._staged_semantic_memory_proposal is None
    coordinator._turn_id = "turn-2"
    assert coordinator._staged_semantic_memory_proposal is None


async def test_reset_clears_staged_semantic_proposal() -> None:
    coordinator = _coordinator()
    coordinator.stage_semantic_memory_proposal(turn_id="turn-1", proposal=_proposal())
    await coordinator.memory_reset(reset_revision=1)
    assert coordinator._staged_semantic_memory_proposal is None
    assert coordinator.dialogue_frame == DialogueFrame()


async def test_delivered_turn_precedes_semantic_transaction_and_journal_only_has_one_build() -> (
    None
):
    class _OrderingStore(DevelopmentAuthoritativeMemoryStore):
        async def apply_semantic_memory_proposal(self, **kwargs):  # type: ignore[no-untyped-def]
            snapshot = await self.load_snapshot(
                subject="child:semantic", age_band="middleChildhood"
            )
            assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-1"]
            return await super().apply_semantic_memory_proposal(**kwargs)

    memory = _OrderingStore()
    coordinator = _coordinator(memory=memory)
    coordinator._latest_memory_commit_sequence = 1
    coordinator._warm_refresh_revision = 1
    builds: list[DynamicContextBuildReason] = []

    async def build(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        reason: DynamicContextBuildReason,
    ) -> None:
        builds.append(reason)

    coordinator._refresh_canvas_context = MethodType(build, coordinator)  # type: ignore[method-assign]
    await coordinator._run_memory_commit(payload=_payload(_proposal()))

    assert len(memory._semantic_journal_events["child:semantic"]) == 1
    assert builds == [DynamicContextBuildReason.TURN_COMMITTED]


async def test_journal_only_canonical_frame_sync_forces_one_build_on_warm_failure() -> (
    None
):
    memory = _WarmSearchFailingMemory()
    await _advance_store_to_frame_revision_one(memory)
    coordinator = _coordinator(memory=memory)
    coordinator._latest_memory_commit_sequence = 1
    coordinator._warm_refresh_revision = 1
    warm_references = (_warm_reference(),)
    coordinator._warm_memories = warm_references
    builds: list[int] = []

    async def build(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        reason: DynamicContextBuildReason,
    ) -> None:
        del reason
        builds.append(_self.dialogue_frame.revision)

    coordinator._refresh_canvas_context = MethodType(build, coordinator)  # type: ignore[method-assign]
    await coordinator._run_memory_commit(payload=_payload(_proposal()))

    snapshot = await memory.load_snapshot(
        subject="child:semantic", age_band="middleChildhood"
    )
    assert {turn.turn_id for turn in snapshot.delivered_turns} == {
        "turn-setup",
        "turn-1",
    }
    assert memory._semantic_journal_sources["child:semantic"] == {
        next(iter(memory._semantic_journal_events["child:semantic"])): {
            "turn-setup",
            "turn-1",
        }
    }
    assert coordinator.dialogue_frame.revision == 1
    assert builds == [1]
    assert coordinator._warm_memories == warm_references


async def test_unchanged_canonical_frame_does_not_force_build_on_warm_failure() -> None:
    memory = _WarmSearchFailingMemory()
    coordinator = _coordinator(memory=memory)
    coordinator._latest_memory_commit_sequence = 1
    coordinator._warm_refresh_revision = 1
    warm_references = (_warm_reference(),)
    coordinator._warm_memories = warm_references
    builds: list[int] = []

    async def build(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        reason: DynamicContextBuildReason,
    ) -> None:
        del reason
        builds.append(_self.dialogue_frame.revision)

    coordinator._refresh_canvas_context = MethodType(build, coordinator)  # type: ignore[method-assign]
    await coordinator._run_memory_commit(payload=_payload(_proposal()))

    snapshot = await memory.load_snapshot(
        subject="child:semantic", age_band="middleChildhood"
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-1"]
    assert len(memory._semantic_journal_events["child:semantic"]) == 1
    assert coordinator.dialogue_frame.revision == 0
    assert builds == []
    assert coordinator._warm_memories == warm_references


async def test_canvas_reference_is_revalidated_and_failure_keeps_warm_path_alive() -> (
    None
):
    workspace = _Workspace({"scene-jupiter": 3})
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator = _coordinator(memory=memory, workspace=workspace)
    coordinator._latest_memory_commit_sequence = 1
    coordinator._warm_refresh_revision = 1
    proposal = _proposal(scene_revision=3)
    coordinator.stage_semantic_memory_proposal(turn_id="turn-1", proposal=proposal)
    workspace.scenes["scene-jupiter"] = 4
    refreshed: list[str] = []

    async def refresh(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        payload: _CapturedMemoryCommit,
        turn: object,
        force_context_refresh: bool = False,
    ) -> None:
        del turn, force_context_refresh
        refreshed.append(payload.turn_id)

    coordinator._refresh_warm_memory = MethodType(refresh, coordinator)  # type: ignore[method-assign]
    await coordinator._run_memory_commit(payload=_payload(proposal))

    snapshot = await memory.load_snapshot(
        subject="child:semantic", age_band="middleChildhood"
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-1"]
    assert memory._semantic_journal_events.get("child:semantic", {}) == {}
    assert refreshed == ["turn-1"]


async def test_frame_semantic_proposal_adopts_canonical_frame_with_one_build() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator = _coordinator(memory=memory)
    coordinator._latest_memory_commit_sequence = 1
    coordinator._warm_refresh_revision = 1
    builds: list[int] = []

    async def build(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        reason: DynamicContextBuildReason,
    ) -> None:
        del reason
        builds.append(_self.dialogue_frame.revision)

    coordinator._refresh_canvas_context = MethodType(build, coordinator)  # type: ignore[method-assign]
    await coordinator._run_memory_commit(payload=_payload(_proposal(frame=True)))

    assert coordinator.dialogue_frame.revision == 1
    assert builds == [1]


async def test_idempotent_semantic_decision_synchronizes_canonical_frame() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator = _coordinator(memory=memory)
    payload = _payload(_proposal(frame=True))
    await memory.commit_delivered_turn(
        subject="child:semantic",
        expected_reset_revision=0,
        turn=_delivered_turn_from_memory_payload(
            payload,
            child_text=payload.child_text,
            child_text_source=payload.child_text_source,
        ),
        proposal=None,
        memory_id=None,
        occurred_at=payload.occurred_at,
    )
    await memory.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-1",
        proposal=payload.semantic_memory_proposal,  # type: ignore[arg-type]
    )
    assert coordinator.dialogue_frame.revision == 0

    decision = await coordinator._apply_semantic_memory_proposal(payload=payload)

    assert decision is not None
    assert decision.status == "idempotent"
    assert coordinator.dialogue_frame.revision == 1


async def test_stale_semantic_decision_synchronizes_frame_without_journal_write() -> (
    None
):
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator = _coordinator(memory=memory)
    first = _payload(_proposal(frame=True))
    delivered = _delivered_turn_from_memory_payload(
        first,
        child_text=first.child_text,
        child_text_source=first.child_text_source,
    )
    await memory.commit_delivered_turn(
        subject="child:semantic",
        expected_reset_revision=0,
        turn=delivered,
        proposal=None,
        memory_id=None,
        occurred_at=first.occurred_at,
    )
    await memory.apply_semantic_memory_proposal(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-1",
        proposal=first.semantic_memory_proposal,  # type: ignore[arg-type]
    )
    second = _payload(
        _proposal(summary="The child prefers Saturn.", frame=True),
        turn_id="turn-2",
    )
    await memory.commit_delivered_turn(
        subject="child:semantic",
        expected_reset_revision=0,
        turn=_delivered_turn_from_memory_payload(
            second,
            child_text=second.child_text,
            child_text_source=second.child_text_source,
        ),
        proposal=None,
        memory_id=None,
        occurred_at=second.occurred_at,
    )

    decision = await coordinator._apply_semantic_memory_proposal(payload=second)

    assert decision is not None
    assert decision.status == "stale"
    assert coordinator.dialogue_frame.revision == 1
    assert len(memory._semantic_journal_events["child:semantic"]) == 1
