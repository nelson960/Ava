from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import MethodType, SimpleNamespace
from typing import Any, cast

from ava_backend.realtime.tool_contract import (
    GetCurrentTimeRequest,
    GetCurrentTimeToolOutputV4,
)

from ava_backend.realtime.coordinator import (
    DeterministicRealtimeSessionCoordinator,
    _CapturedMemoryCommit,
    _TurnResult,
)
from ava_backend.realtime.context_assembler import DynamicContextBuildReason
from ava_backend.realtime.fakes import DeterministicRealtimePlanProvider
from ava_backend.realtime.memory import (
    AgeBand,
    ChildTurnTextSource,
    DeliveredConversationTurn,
    DeliveredTurnTerminal,
    DevelopmentAuthoritativeMemoryStore,
    DialogueFrame,
    DialogueFrameState,
    DialogueFrameUpdate,
    SemanticCaptureDisposition,
    SemanticCaptureFunctionArguments,
)


def _capture_result(
    topic: str,
    *,
    canvas: tuple[str, int] | None = None,
) -> SemanticCaptureFunctionArguments:
    frame: dict[str, object] = {"activeTopics": [topic]}
    if canvas is not None:
        frame["relevantCanvasReferences"] = [
            {
                "sceneId": canvas[0],
                "sceneRevision": canvas[1],
                "relation": "The visual currently under discussion",
            }
        ]
    return SemanticCaptureFunctionArguments(
        disposition=SemanticCaptureDisposition.CHANGED,
        dialogue_frame=DialogueFrameState.model_validate(frame),
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
    realtime: DeterministicRealtimePlanProvider | None = None,
    start_memory_worker: bool = False,
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
    coordinator._turn_tool_exchanges = []
    coordinator._canvas_jobs = _ResettableCanvasJobs(workspace or _Workspace())  # type: ignore[assignment]
    coordinator._canvas_context_lock = asyncio.Lock()
    coordinator._memory_commit_queue = asyncio.Queue()
    coordinator._memory_commit_terminal = object()
    if start_memory_worker:
        coordinator._memory_commit_worker_task = asyncio.create_task(
            coordinator._run_memory_commit_worker()
        )
    else:
        coordinator._memory_commit_worker_task = None
    coordinator._last_enqueued_memory_sequence = 0
    coordinator._scheduled_memory_commit_turn_ids = set()
    coordinator._memory_commit_sequence = 0
    coordinator._current_memory_commit_turn_id = None
    coordinator._memory_commit_drain_timeout_seconds = 5.0
    coordinator._failure_id_factory = lambda: "failure-frame"
    coordinator._memory_snapshot = None
    coordinator._harness = None
    coordinator._pending_harness = None
    coordinator._latest_canvas_interaction_intent = None
    coordinator._context_installation_task = None
    coordinator._realtime = realtime or DeterministicRealtimePlanProvider()
    coordinator._turn_id_factory = lambda: "turn-next"
    coordinator._request_id_factory = lambda: "req-next"
    coordinator._stream_id_factory = lambda: "stream-next"
    coordinator._stream_id = "stream-1"
    return coordinator


def _payload(
    *,
    turn_id: str,
    sequence: int = 1,
    terminal: DeliveredTurnTerminal = DeliveredTurnTerminal.COMPLETED,
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
        terminal=terminal,
        transcript_waiter=None,
        interrupted=(terminal == DeliveredTurnTerminal.CANCELLED),
    )


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


async def test_post_delivery_capture_updates_dialogue_frame_and_refreshes_context() -> (
    None
):
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [_capture_result("Moon")]
    coordinator = _bare_coordinator(realtime=provider)
    builds = _record_context_refreshes(coordinator)

    await coordinator._run_memory_commit(payload=_payload(turn_id="turn-1"))

    assert coordinator.dialogue_frame.active_topics == ("Moon",)
    assert coordinator.dialogue_frame.revision == 1
    assert len(provider.captured_evidences) == 1
    assert provider.captured_evidences[0].source_turn_id == "turn-1"
    assert provider.captured_evidences[0].child_utterance == "Tell me more."
    assert provider.captured_evidences[0].spoken_response_prefix == "Here is more."
    assert not provider.captured_evidences[0].interrupted
    assert builds == [(1, DynamicContextBuildReason.TURN_COMMITTED)]


async def test_canvas_references_require_exact_canonical_retained_revision() -> None:
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        _capture_result("Moon", canvas=("scene-moon", 2))
    ]
    coordinator = _bare_coordinator(
        workspace=_Workspace({"scene-moon": 3}), realtime=provider
    )
    builds = _record_context_refreshes(coordinator)

    await coordinator._run_memory_commit(payload=_payload(turn_id="turn-1"))

    # Canonicalization drops the invalid reference without losing semantic content.
    assert coordinator.dialogue_frame.active_topics == ("Moon",)
    assert coordinator.dialogue_frame.relevant_canvas_references == ()
    assert coordinator.dialogue_frame.revision == 1
    assert builds == [(1, DynamicContextBuildReason.TURN_COMMITTED)]


async def test_schedule_captures_settled_outcomes_without_cross_turn_leak() -> None:
    coordinator = _bare_coordinator(start_memory_worker=True)
    coordinator._turn_occurred_at = datetime(2026, 9, 8, tzinfo=UTC)
    coordinator._turn_canvas_interaction_input = None
    coordinator._child_text = "Tell me about the Moon."
    coordinator._child_text_source = ChildTurnTextSource.TRANSCRIPT
    coordinator._record_tool_exchange(
        tool_name="get_current_time",
        request=GetCurrentTimeRequest(),
        result=GetCurrentTimeToolOutputV4(
            localIsoDateTime="2026-09-08T00:00:00+00:00",
            weekday="Tuesday",
            daypart="night",
            timezoneId="UTC",
        ),
    )
    captured: list[_CapturedMemoryCommit] = []

    async def capture(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        payload: _CapturedMemoryCommit,
    ) -> None:
        captured.append(payload)

    coordinator._run_memory_commit = MethodType(capture, coordinator)  # type: ignore[method-assign]
    coordinator._schedule_memory_commit(
        turn_id="turn-1",
        plan=_TurnResult(segments=(), child_turn_summary="Discussed the Moon."),
        delivered_sample_count=24_000,
        delivered_text="The Moon orbits Earth.",
        terminal=DeliveredTurnTerminal.COMPLETED,
    )
    await coordinator._memory_commit_queue.join()

    assert captured[0].turn_id == "turn-1"
    assert len(captured[0].tool_exchanges) == 1
    assert captured[0].tool_exchanges[0].tool == "get_current_time"
    assert not captured[0].interrupted
    # Settled tool outcomes are preserved per turn and cleared on transition
    coordinator._harness = cast(
        Any, SimpleNamespace(prompt_version="1", tool_contract_version="1")
    )
    coordinator._terminal = True
    await coordinator._prepare_next_turn()
    assert coordinator._turn_tool_exchanges == []
    coordinator._memory_commit_queue.put_nowait(coordinator._memory_commit_terminal)
    if coordinator._memory_commit_worker_task is not None:
        await coordinator._memory_commit_worker_task


async def test_delivered_turn_precedes_frame_and_first_refresh_sees_new_frame() -> None:
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [_capture_result("Moon")]
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator = _bare_coordinator(memory=memory, realtime=provider)
    observed_revisions: list[int] = []

    async def observe_refresh(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        reason: DynamicContextBuildReason,
    ) -> None:
        assert reason == DynamicContextBuildReason.TURN_COMMITTED
        observed_revisions.append(coordinator.dialogue_frame.revision)

    coordinator._refresh_canvas_context = MethodType(  # type: ignore[method-assign]
        observe_refresh, coordinator
    )
    await coordinator._run_memory_commit(payload=_payload(turn_id="turn-1"))

    snapshot = await memory.load_snapshot(
        subject="child:frame", age_band=AgeBand.MIDDLE_CHILDHOOD
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
    )
    await memory.apply_dialogue_frame_update(
        subject="child:frame",
        expected_reset_revision=0,
        source_turn_id="turn-a",
        update=DialogueFrameUpdate.model_validate(
            {"baseRevision": 0, "frame": {"activeTopics": ["Moon"]}}
        ),
    )

    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [_capture_result("Earth")]
    coordinator = _bare_coordinator(memory=memory, realtime=provider)
    assert coordinator.dialogue_frame == DialogueFrame()
    refreshed: list[str] = []

    async def refresh(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        reason: DynamicContextBuildReason,
    ) -> None:
        assert reason == DynamicContextBuildReason.TURN_COMMITTED
        del _self
        refreshed.append(str(reason))

    coordinator._refresh_canvas_context = MethodType(refresh, coordinator)  # type: ignore[method-assign]
    await coordinator._run_memory_commit(payload=_payload(turn_id="turn-b"))

    snapshot = await memory.load_snapshot(
        subject="child:frame", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    assert {turn.turn_id for turn in snapshot.delivered_turns} == {"turn-a", "turn-b"}
    assert snapshot.dialogue_frame.active_topics == ("Moon",)
    assert coordinator.dialogue_frame.revision == 1
    assert coordinator.dialogue_frame.active_topics == ("Moon",)
    assert refreshed == [str(DynamicContextBuildReason.TURN_COMMITTED)]


async def test_memory_reset_clears_settled_outcomes_and_current_frame() -> None:
    coordinator = _bare_coordinator()
    coordinator._dialogue_frame = DialogueFrame.model_validate(
        {"revision": 2, "activeTopics": ["Moon"]}
    )
    coordinator._record_tool_exchange(
        tool_name="get_current_time",
        request=GetCurrentTimeRequest(),
        result=GetCurrentTimeToolOutputV4(
            localIsoDateTime="2026-09-08T00:00:00+00:00",
            weekday="Tuesday",
            daypart="night",
            timezoneId="UTC",
        ),
    )

    await coordinator.memory_reset(reset_revision=1)

    assert coordinator.dialogue_frame == DialogueFrame()
    assert coordinator._turn_tool_exchanges == []


class _FrameFailingMemory(DevelopmentAuthoritativeMemoryStore):
    async def apply_semantic_memory_proposal(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise RuntimeError("injected DialogueFrame failure")


async def test_optional_frame_failure_preserves_turn_without_context_refresh() -> None:
    memory = _FrameFailingMemory()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [_capture_result("Moon")]
    coordinator = _bare_coordinator(memory=memory, realtime=provider)
    refreshed: list[str] = []

    async def refresh(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        reason: DynamicContextBuildReason,
    ) -> None:
        del _self, reason
        refreshed.append("unexpected")

    coordinator._refresh_canvas_context = MethodType(refresh, coordinator)  # type: ignore[method-assign]
    await coordinator._run_memory_commit(payload=_payload(turn_id="turn-1"))

    snapshot = await memory.load_snapshot(
        subject="child:frame", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-1"]
    assert snapshot.dialogue_frame == DialogueFrame()
    assert refreshed == []


async def test_frame_change_requests_one_context_build() -> None:
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [_capture_result("Moon")]
    coordinator = _bare_coordinator(realtime=provider)
    builds = _record_context_refreshes(coordinator)

    await coordinator._run_memory_commit(payload=_payload(turn_id="turn-1"))

    assert builds == [(1, DynamicContextBuildReason.TURN_COMMITTED)]


async def test_frame_change_preserves_bootstrap_references_and_builds_once() -> None:
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [_capture_result("Moon")]
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator = _bare_coordinator(memory=memory, realtime=provider)
    builds = _record_context_refreshes(coordinator)

    await coordinator._run_memory_commit(payload=_payload(turn_id="turn-1"))

    snapshot = await memory.load_snapshot(
        subject="child:frame", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-1"]
    assert snapshot.dialogue_frame.revision == 1
    assert coordinator.dialogue_frame.revision == 1
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
    )
    await memory.reset_conversation(subject="child:frame")
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [_capture_result("Old topic")]
    coordinator = _bare_coordinator(memory=memory, realtime=provider)
    coordinator._memory_reset_revision = 1

    await coordinator._run_memory_commit(payload=_payload(turn_id="turn-1"))

    snapshot = await memory.load_snapshot(
        subject="child:frame", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    assert snapshot.reset_revision == 1
    assert snapshot.dialogue_frame == DialogueFrame()
    assert coordinator.dialogue_frame == DialogueFrame()
