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
)
from ava_backend.realtime.context_assembler import DynamicContextBuildReason
from ava_backend.realtime.memory import (
    ChildTurnTextSource,
    DeliveredConversationTurn,
    DeliveredTurnTerminal,
    DevelopmentAuthoritativeMemoryStore,
    DialogueFrame,
    DialogueFrameUpdate,
)


def _update(
    topic: str,
    *,
    base_revision: int = 0,
    canvas: tuple[str, int] | None = None,
) -> DialogueFrameUpdate:
    frame: dict[str, object] = {"activeTopics": [topic]}
    if canvas is not None:
        frame["relevantCanvasReferences"] = [
            {
                "sceneId": canvas[0],
                "sceneRevision": canvas[1],
                "relation": "The visual currently under discussion",
            }
        ]
    return DialogueFrameUpdate.model_validate(
        {"baseRevision": base_revision, "frame": frame}
    )


class _Workspace:
    def __init__(self, scenes: dict[str, int] | None = None) -> None:
        self._scenes = scenes or {}

    def scene(self, scene_id: str):  # type: ignore[no-untyped-def]
        revision = self._scenes.get(scene_id)
        return SimpleNamespace(revision=revision) if revision is not None else None

    def manifest_scene(self, scene_id: str):  # type: ignore[no-untyped-def]
        del scene_id
        return None


class _ResettableCanvasJobs:
    def __init__(self, workspace: _Workspace) -> None:
        self.state = SimpleNamespace(workspace=workspace)
        self.reset_count = 0

    async def reset_conversation(self) -> tuple[str, ...]:
        self.reset_count += 1
        return ()


def _bare_coordinator(
    *,
    memory: DevelopmentAuthoritativeMemoryStore | None = None,
    workspace: _Workspace | None = None,
) -> DeterministicRealtimeSessionCoordinator:
    coordinator = cast(
        DeterministicRealtimeSessionCoordinator,
        object.__new__(DeterministicRealtimeSessionCoordinator),
    )
    coordinator._session_id = "session-frame"
    coordinator._turn_id = "turn-1"
    coordinator._subject = "child:frame"
    coordinator._timezone_id = "UTC"
    coordinator._terminal = False
    coordinator._started = True
    coordinator._closed = False
    coordinator._memory = memory or DevelopmentAuthoritativeMemoryStore()
    coordinator._memory_reset_revision = 0
    coordinator._dialogue_frame = DialogueFrame()
    coordinator._staged_semantic_memory_proposal = None
    coordinator._canvas_jobs = _ResettableCanvasJobs(workspace or _Workspace())  # type: ignore[assignment]
    coordinator._canvas_context_lock = asyncio.Lock()
    coordinator._warm_refresh_revision = 0
    coordinator._warm_memories = ()
    coordinator._recent_live_echo_suppression_turn_ids = deque(maxlen=8)
    coordinator._last_memory_decision = None
    coordinator._memory_commit_tasks = {}
    coordinator._memory_commit_sequence = 0
    coordinator._latest_memory_commit_sequence = 0
    coordinator._failure_id_factory = lambda: "failure-frame"
    coordinator._memory_snapshot = None
    coordinator._harness = None
    return coordinator


def test_current_turn_staging_is_idempotent_and_conflicts_are_rejected() -> None:
    coordinator = _bare_coordinator()
    update = _update("Moon")

    with pytest.raises(CoordinatorStateError, match="active current turn"):
        coordinator.stage_dialogue_frame_update(turn_id="turn-old", update=update)
    coordinator.stage_dialogue_frame_update(turn_id="turn-1", update=update)
    coordinator.stage_dialogue_frame_update(turn_id="turn-1", update=update)
    with pytest.raises(CoordinatorStateError, match="different"):
        coordinator.stage_dialogue_frame_update(
            turn_id="turn-1", update=_update("Earth")
        )


def test_canvas_references_require_exact_canonical_retained_revision() -> None:
    coordinator = _bare_coordinator(workspace=_Workspace({"scene-moon": 3}))

    coordinator.stage_dialogue_frame_update(
        turn_id="turn-1", update=_update("Moon", canvas=("scene-moon", 3))
    )
    coordinator._staged_semantic_memory_proposal = None
    with pytest.raises(CoordinatorStateError, match="canonical retained"):
        coordinator.stage_dialogue_frame_update(
            turn_id="turn-1", update=_update("Moon", canvas=("scene-moon", 2))
        )
    with pytest.raises(CoordinatorStateError, match="canonical retained"):
        coordinator.stage_dialogue_frame_update(
            turn_id="turn-1", update=_update("Moon", canvas=("scene-missing", 1))
        )


async def test_schedule_captures_exact_staged_update_without_cross_turn_leak() -> None:
    coordinator = _bare_coordinator()
    coordinator._turn_occurred_at = datetime(2026, 9, 8, tzinfo=UTC)
    coordinator._turn_canvas_interaction_input = None
    coordinator._child_text = "Tell me about the Moon."
    coordinator._child_text_source = ChildTurnTextSource.TRANSCRIPT
    captured: list[_CapturedMemoryCommit] = []

    async def capture(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        payload: _CapturedMemoryCommit,
    ) -> None:
        captured.append(payload)

    coordinator._run_memory_commit = MethodType(capture, coordinator)  # type: ignore[method-assign]
    coordinator.stage_dialogue_frame_update(turn_id="turn-1", update=_update("Moon"))
    coordinator._schedule_memory_commit(
        turn_id="turn-1",
        plan=_TurnResult(segments=(), child_turn_summary="Discussed the Moon."),
        delivered_sample_count=24_000,
        delivered_text="The Moon orbits Earth.",
        terminal=DeliveredTurnTerminal.COMPLETED,
    )
    await asyncio.gather(*coordinator._memory_commit_tasks.values())

    assert captured[0].turn_id == "turn-1"
    assert captured[0].dialogue_frame_update == _update("Moon")
    assert coordinator._staged_semantic_memory_proposal is None
    coordinator._turn_id = "turn-2"
    assert coordinator._staged_semantic_memory_proposal is None


def _payload(
    *,
    turn_id: str,
    update: DialogueFrameUpdate,
    sequence: int = 1,
) -> _CapturedMemoryCommit:
    return _CapturedMemoryCommit(
        sequence=sequence,
        turn_id=turn_id,
        occurred_at=datetime(2026, 9, 8, tzinfo=UTC),
        timezone_id="UTC",
        expected_reset_revision=0,
        child_turn_summary="Discussed current context.",
        child_text="Tell me more.",
        child_text_source=ChildTurnTextSource.TRANSCRIPT,
        canvas_interaction=None,
        delivered_text="Here is more.",
        delivered_sample_count=24_000,
        terminal=DeliveredTurnTerminal.COMPLETED,
        transcript_waiter=None,
        warm_refresh_revision=1,
        dialogue_frame_update=update,
    )


async def test_delivered_turn_precedes_frame_and_first_refresh_sees_new_frame() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator = _bare_coordinator(memory=memory)
    coordinator._latest_memory_commit_sequence = 1
    observed_revisions: list[int] = []

    async def observe_refresh(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        payload: _CapturedMemoryCommit,
        turn: object,
        force_context_refresh: bool = False,
    ) -> None:
        del payload, turn, force_context_refresh
        observed_revisions.append(coordinator.dialogue_frame.revision)

    coordinator._refresh_warm_memory = MethodType(  # type: ignore[method-assign]
        observe_refresh, coordinator
    )
    await coordinator._run_memory_commit(
        payload=_payload(turn_id="turn-1", update=_update("Moon"))
    )

    snapshot = await memory.load_snapshot(
        subject="child:frame", age_band="middleChildhood"
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-1"]
    assert snapshot.dialogue_frame.revision == 1
    assert observed_revisions == [1]


async def test_stale_frame_does_not_erase_later_delivered_turn_or_kill_refresh() -> (
    None
):
    memory = DevelopmentAuthoritativeMemoryStore()
    await memory.commit_delivered_turn(
        subject="child:frame",
        expected_reset_revision=0,
        turn=DeliveredConversationTurn.model_validate(
            {
                "turnId": "turn-a",
                "childText": "First turn.",
                "avaDeliveredText": "First response.",
                "deliveredSampleCount": 24_000,
                "childTurnSummary": "First turn.",
                "terminal": "completed",
            }
        ),
        proposal=None,
        memory_id=None,
    )
    await memory.apply_dialogue_frame_update(
        subject="child:frame",
        expected_reset_revision=0,
        source_turn_id="turn-a",
        update=_update("Moon"),
    )
    coordinator = _bare_coordinator(memory=memory)
    assert coordinator.dialogue_frame == DialogueFrame()
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
    await coordinator._run_memory_commit(
        payload=_payload(turn_id="turn-b", update=_update("Earth", base_revision=0))
    )

    snapshot = await memory.load_snapshot(
        subject="child:frame", age_band="middleChildhood"
    )
    assert {turn.turn_id for turn in snapshot.delivered_turns} == {"turn-a", "turn-b"}
    assert snapshot.dialogue_frame.active_topics == ("Moon",)
    assert coordinator.dialogue_frame.revision == 1
    assert coordinator.dialogue_frame.active_topics == ("Moon",)
    assert refreshed == ["turn-b"]


async def test_memory_reset_clears_staged_and_current_frame() -> None:
    coordinator = _bare_coordinator()
    coordinator._dialogue_frame = DialogueFrame.model_validate(
        {"revision": 2, "activeTopics": ["Moon"]}
    )
    coordinator.stage_dialogue_frame_update(
        turn_id="turn-1", update=_update("Earth", base_revision=2)
    )

    await coordinator.memory_reset(reset_revision=1)

    assert coordinator.dialogue_frame == DialogueFrame()
    assert coordinator._staged_semantic_memory_proposal is None


class _FrameFailingMemory(DevelopmentAuthoritativeMemoryStore):
    async def apply_semantic_memory_proposal(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise RuntimeError("injected DialogueFrame failure")


async def test_optional_frame_failure_preserves_turn_and_post_commit_refresh() -> None:
    memory = _FrameFailingMemory()
    coordinator = _bare_coordinator(memory=memory)
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
    await coordinator._run_memory_commit(
        payload=_payload(turn_id="turn-1", update=_update("Moon"))
    )

    snapshot = await memory.load_snapshot(
        subject="child:frame", age_band="middleChildhood"
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-1"]
    assert snapshot.dialogue_frame == DialogueFrame()
    assert refreshed == ["turn-1"]


class _WarmSearchFailingMemory(DevelopmentAuthoritativeMemoryStore):
    async def search_memory_candidates(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise RuntimeError("injected optional warm-memory failure")


def _record_context_refreshes(
    coordinator: DeterministicRealtimeSessionCoordinator,
) -> list[tuple[int, DynamicContextBuildReason]]:
    builds: list[tuple[int, DynamicContextBuildReason]] = []

    async def refresh(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        reason: DynamicContextBuildReason = DynamicContextBuildReason.CANVAS_ACTIVATED,
    ) -> None:
        builds.append((_self.dialogue_frame.revision, reason))

    coordinator._refresh_canvas_context = MethodType(refresh, coordinator)  # type: ignore[method-assign]
    return builds


async def test_successful_warm_search_requests_one_context_build() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator = _bare_coordinator(memory=memory)
    coordinator._warm_refresh_revision = 1
    builds = _record_context_refreshes(coordinator)

    await coordinator._run_memory_commit(
        payload=_payload(turn_id="turn-1", update=_update("Moon"))
    )

    assert builds == [(1, DynamicContextBuildReason.TURN_COMMITTED)]


async def test_failed_warm_search_still_builds_new_durable_frame_once() -> None:
    memory = _WarmSearchFailingMemory()
    coordinator = _bare_coordinator(memory=memory)
    coordinator._warm_refresh_revision = 1
    existing_warm_references = (object(),)
    coordinator._warm_memories = existing_warm_references  # type: ignore[assignment]
    builds = _record_context_refreshes(coordinator)

    await coordinator._run_memory_commit(
        payload=_payload(turn_id="turn-1", update=_update("Moon"))
    )

    snapshot = await memory.load_snapshot(
        subject="child:frame", age_band="middleChildhood"
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-1"]
    assert snapshot.dialogue_frame.revision == 1
    assert coordinator.dialogue_frame.revision == 1
    assert coordinator._warm_memories is existing_warm_references
    assert builds == [(1, DynamicContextBuildReason.TURN_COMMITTED)]


async def test_reset_epoch_fence_prevents_old_frame_resurrection() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    await memory.commit_delivered_turn(
        subject="child:frame",
        expected_reset_revision=0,
        turn=DeliveredConversationTurn.model_validate(
            {
                "turnId": "turn-1",
                "childText": "Old turn.",
                "avaDeliveredText": "Old response.",
                "deliveredSampleCount": 24_000,
                "childTurnSummary": "Old turn.",
                "terminal": "completed",
            }
        ),
        proposal=None,
        memory_id=None,
    )
    await memory.reset_conversation(subject="child:frame")
    coordinator = _bare_coordinator(memory=memory)
    coordinator._memory_reset_revision = 1

    decision = await coordinator._apply_dialogue_frame_update(
        payload=_payload(turn_id="turn-1", update=_update("Old topic"))
    )

    snapshot = await memory.load_snapshot(
        subject="child:frame", age_band="middleChildhood"
    )
    assert decision is None
    assert snapshot.reset_revision == 1
    assert snapshot.dialogue_frame == DialogueFrame()
