from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime
from types import MethodType, SimpleNamespace
from typing import Any, cast

import pytest

from ava_backend.realtime.context_assembler import (
    DynamicContextBuildReason,
    MemoryReference,
)
from ava_backend.realtime.coordinator import (
    CoordinatorStateError,
    DeterministicRealtimeSessionCoordinator,
    _CapturedMemoryCommit,
    _TurnResult,
    _delivered_turn_from_memory_payload,
)
from ava_backend.realtime.fakes import DeterministicRealtimePlanProvider
from ava_backend.realtime.memory import (
    AgeBand,
    ChildTurnTextSource,
    DeliveredCanvasInteraction,
    DeliveredTurnTerminal,
    DevelopmentAuthoritativeMemoryStore,
    DialogueFrame,
    DialogueFrameState,
    DialogueFrameUpdate,
    MemorySearchCandidate,
    SemanticCaptureDisposition,
    SemanticCaptureFunctionArguments,
    SemanticCaptureJournalDraft,
    SemanticJournalCanvasReference,
    SemanticJournalEventFamily,
    SemanticMemoryProposal,
)
from ava_backend.realtime.provider_session import (
    SemanticCaptureEvidence,
    SettledSemanticToolOutcome,
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


class _SearchSpyMemory(DevelopmentAuthoritativeMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.search_call_count = 0

    async def search_memory_candidates(
        self,
        *,
        subject: str,
        query: str,
        limit: int = 5,
        from_instant: datetime | None = None,
        to_instant: datetime | None = None,
    ) -> list[MemorySearchCandidate]:
        self.search_call_count += 1
        return await super().search_memory_candidates(
            subject=subject,
            query=query,
            limit=limit,
            from_instant=from_instant,
            to_instant=to_instant,
        )


def _capture_result(
    *,
    disposition: SemanticCaptureDisposition = SemanticCaptureDisposition.CHANGED,
    family: str = "userPreference",
    summary: str | None = "The child prefers Jupiter.",
    scene_revision: int | None = None,
    frame: bool = False,
    active_topics: tuple[str, ...] = ("Planets",),
) -> SemanticCaptureFunctionArguments:
    if disposition == SemanticCaptureDisposition.NO_CHANGE:
        return SemanticCaptureFunctionArguments(
            disposition=SemanticCaptureDisposition.NO_CHANGE,
        )
    dialogue_frame: DialogueFrameState | None = None
    if frame:
        dialogue_frame = DialogueFrameState.model_validate(
            {"activeTopics": list(active_topics)}
        )
    journal_events: list[SemanticCaptureJournalDraft] = []
    if summary is not None:
        event_dict: dict[str, object] = {
            "family": family,
            "semanticKey": "favorite_planet" if family != "canvasReference" else "jupiter_diagram",
            "summary": summary,
        }
        if scene_revision is not None:
            event_dict["canonicalCanvasReferences"] = [
                {
                    "sceneId": "scene-jupiter",
                    "sceneRevision": scene_revision,
                    "relation": "The discussed planet",
                }
            ]
        journal_events.append(SemanticCaptureJournalDraft.model_validate(event_dict))
    return SemanticCaptureFunctionArguments(
        disposition=SemanticCaptureDisposition.CHANGED,
        dialogue_frame=dialogue_frame,
        journal_events=tuple(journal_events),
    )


def _proposal(
    *,
    disposition: SemanticCaptureDisposition = SemanticCaptureDisposition.CHANGED,
    summary: str = "The child prefers Jupiter.",
    scene_revision: int | None = None,
    frame: bool = False,
) -> SemanticMemoryProposal:
    if disposition == SemanticCaptureDisposition.NO_CHANGE:
        return SemanticMemoryProposal(
            disposition=SemanticCaptureDisposition.NO_CHANGE,
            dialogue_frame_update=None,
            journal_events=(),
        )
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
    payload: dict[str, object] = {
        "disposition": "changed",
        "journalEvents": [event],
    }
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
    realtime: DeterministicRealtimePlanProvider | None = None,
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
    coordinator._turn_settled_tool_outcomes = []
    coordinator._canvas_jobs = _CanvasJobs(workspace or _Workspace())  # type: ignore[assignment]
    coordinator._canvas_context_lock = asyncio.Lock()
    coordinator._warm_memories = ()
    coordinator._last_memory_decision = None
    coordinator._memory_commit_queue = asyncio.Queue()
    coordinator._memory_commit_terminal = object()
    coordinator._memory_commit_worker_task = None
    coordinator._scheduled_memory_commit_turn_ids = set()
    coordinator._last_enqueued_memory_sequence = 0
    coordinator._current_memory_commit_turn_id = None
    coordinator._memory_commit_drain_timeout_seconds = 2.0
    coordinator._memory_commit_sequence = 0
    coordinator._latest_memory_commit_sequence = 0
    coordinator._failure_id_factory = lambda: "failure-semantic"
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
    sequence: int = 1,
    turn_id: str = "turn-1",
    canvas_interaction: DeliveredCanvasInteraction | None = None,
    settled_tool_outcomes: tuple[SettledSemanticToolOutcome, ...] = (),
    interrupted: bool = False,
    delivered_text: str = "Jupiter is fascinating.",
    child_text: str | None = "I like Jupiter.",
    transcript_waiter: Awaitable[str | None] | None = None,
) -> _CapturedMemoryCommit:
    resolved_child_text = (
        None if canvas_interaction is not None else child_text
    )
    resolved_child_text_source = (
        None if canvas_interaction is not None else (
            ChildTurnTextSource.TRANSCRIPT if child_text is not None else None
        )
    )
    return _CapturedMemoryCommit(
        sequence=sequence,
        turn_id=turn_id,
        occurred_at=datetime(2026, 9, 8, tzinfo=UTC),
        timezone_id="UTC",
        expected_reset_revision=0,
        child_turn_summary="Discussed a planet preference.",
        child_text=resolved_child_text,
        child_text_source=resolved_child_text_source,
        canvas_interaction=canvas_interaction,
        delivered_text=delivered_text,
        delivered_sample_count=24_000,
        terminal=DeliveredTurnTerminal.COMPLETED,
        transcript_waiter=transcript_waiter,
        settled_tool_outcomes=settled_tool_outcomes,
        interrupted=interrupted,
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
    setup = _payload(turn_id="turn-setup")
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
        proposal=_proposal(frame=True),
    )
    assert decision.dialogue_frame.revision == 1


def test_canvas_references_require_exact_canonical_retained_revision() -> None:
    workspace = _Workspace({"scene-jupiter": 3})
    coordinator = _coordinator(workspace=workspace)
    coordinator._validate_semantic_memory_canvas_references(_proposal(scene_revision=3))
    with pytest.raises(CoordinatorStateError, match="canonical retained"):
        coordinator._validate_semantic_memory_canvas_references(
            _proposal(scene_revision=2)
        )
    with pytest.raises(CoordinatorStateError, match="canonical retained"):
        _coordinator()._validate_semantic_memory_canvas_references(
            _proposal(scene_revision=1)
        )


async def test_schedule_captures_settled_tool_outcomes() -> None:
    coordinator = _coordinator()
    coordinator._turn_occurred_at = datetime(2026, 9, 8, tzinfo=UTC)
    coordinator._turn_canvas_interaction_input = None
    coordinator._child_text = "I like Jupiter."
    coordinator._child_text_source = ChildTurnTextSource.TRANSCRIPT
    coordinator._record_tool_outcome(
        tool_name="get_current_time",
        call_id="call_time_1",
        outcome="available",
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
        plan=_TurnResult(segments=(), child_turn_summary="Discussed Jupiter."),
        delivered_sample_count=24_000,
        delivered_text="Jupiter is fascinating.",
        terminal=DeliveredTurnTerminal.COMPLETED,
    )
    await coordinator._memory_commit_queue.join()
    if coordinator._memory_commit_worker_task is not None:
        coordinator._memory_commit_worker_task.cancel()

    assert captured[0].turn_id == "turn-1"
    assert len(captured[0].settled_tool_outcomes) == 1
    assert captured[0].settled_tool_outcomes[0].tool_name == "get_current_time"
    assert captured[0].settled_tool_outcomes[0].outcome == "available"
    assert not captured[0].interrupted


async def test_reset_clears_settled_tool_outcomes_and_frame() -> None:
    coordinator = _coordinator()
    coordinator._dialogue_frame = DialogueFrame.model_validate(
        {"revision": 2, "activeTopics": ["Jupiter"]}
    )
    coordinator._record_tool_outcome(
        tool_name="get_current_time", call_id="c1", outcome="available"
    )
    assert len(coordinator._turn_settled_tool_outcomes) == 1
    await coordinator.memory_reset(reset_revision=1)
    assert coordinator._turn_settled_tool_outcomes == []
    assert coordinator.dialogue_frame == DialogueFrame()


async def test_normal_speech_commit_with_no_change_disposition() -> None:
    memory = _SearchSpyMemory()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        _capture_result(disposition=SemanticCaptureDisposition.NO_CHANGE)
    ]
    coordinator = _coordinator(memory=memory, realtime=provider)
    coordinator._latest_memory_commit_sequence = 1
    builds: list[DynamicContextBuildReason] = []

    async def build(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        reason: DynamicContextBuildReason,
    ) -> None:
        del _self
        builds.append(reason)

    coordinator._refresh_canvas_context = MethodType(build, coordinator)  # type: ignore[method-assign]
    await coordinator._run_memory_commit(payload=_payload())

    snapshot = await memory.load_snapshot(
        subject="child:semantic", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-1"]
    assert memory.search_call_count == 0
    assert builds == []
    receipt = await memory.load_semantic_memory_receipt(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-1",
    )
    assert receipt is not None
    assert receipt.disposition == SemanticCaptureDisposition.NO_CHANGE
    assert receipt.event_results == ()


async def test_canvas_interaction_commit_has_no_automatic_search() -> None:
    memory = _SearchSpyMemory()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        _capture_result(disposition=SemanticCaptureDisposition.NO_CHANGE)
    ]
    coordinator = _coordinator(memory=memory, realtime=provider)
    coordinator._latest_memory_commit_sequence = 1
    interaction = DeliveredCanvasInteraction.model_validate(
        {
            "sceneId": "scene-jupiter",
            "sceneRevision": 3,
            "interactionRevision": 1,
            "interactionMode": "explore",
            "bindingId": "binding-storm",
            "targetId": "storm",
            "accessibilityLabel": "Jupiter's storm",
        }
    )
    await coordinator._run_memory_commit(
        payload=_payload(canvas_interaction=interaction)
    )

    snapshot = await memory.load_snapshot(
        subject="child:semantic", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    assert snapshot.delivered_turns[-1].canvas_interaction == interaction
    assert memory.search_call_count == 0


async def test_delivered_turn_precedes_journal_transaction_without_context_build() -> (
    None
):
    class _OrderingStore(_SearchSpyMemory):
        async def apply_semantic_memory_proposal(self, **kwargs):  # type: ignore[no-untyped-def]
            snapshot = await self.load_snapshot(
                subject="child:semantic", age_band=AgeBand.MIDDLE_CHILDHOOD
            )
            assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-1"]
            return await super().apply_semantic_memory_proposal(**kwargs)

    memory = _OrderingStore()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [_capture_result()]
    coordinator = _coordinator(memory=memory, realtime=provider)
    coordinator._latest_memory_commit_sequence = 1
    builds: list[DynamicContextBuildReason] = []

    async def build(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        reason: DynamicContextBuildReason,
    ) -> None:
        builds.append(reason)

    coordinator._refresh_canvas_context = MethodType(build, coordinator)  # type: ignore[method-assign]
    await coordinator._run_memory_commit(payload=_payload())

    assert len(memory._semantic_journal_events["child:semantic"]) == 1
    assert memory.search_call_count == 0
    assert builds == []
    receipt = await memory.load_semantic_memory_receipt(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-1",
    )
    assert receipt is not None
    assert receipt.disposition == SemanticCaptureDisposition.CHANGED
    assert len(receipt.event_results) == 1


async def test_journal_only_canonical_frame_sync_forces_one_build_without_search() -> (
    None
):
    memory = _SearchSpyMemory()
    await _advance_store_to_frame_revision_one(memory)
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [_capture_result()]
    coordinator = _coordinator(memory=memory, realtime=provider)
    coordinator._latest_memory_commit_sequence = 1
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
    await coordinator._run_memory_commit(payload=_payload())

    snapshot = await memory.load_snapshot(
        subject="child:semantic", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    assert {turn.turn_id for turn in snapshot.delivered_turns} == {
        "turn-setup",
        "turn-1",
    }
    assert coordinator.dialogue_frame.revision == 1
    assert builds == [1]
    assert memory.search_call_count == 0
    assert coordinator._warm_memories == warm_references


async def test_unchanged_canonical_frame_does_not_search_or_build() -> None:
    memory = _SearchSpyMemory()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [_capture_result()]
    coordinator = _coordinator(memory=memory, realtime=provider)
    coordinator._latest_memory_commit_sequence = 1
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
    await coordinator._run_memory_commit(payload=_payload())

    snapshot = await memory.load_snapshot(
        subject="child:semantic", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-1"]
    assert len(memory._semantic_journal_events["child:semantic"]) == 1
    assert coordinator.dialogue_frame.revision == 0
    assert builds == []
    assert memory.search_call_count == 0
    assert coordinator._warm_memories == warm_references


async def test_canvas_reference_revalidation_failure_does_not_trigger_search_or_abort_turn() -> (
    None
):
    workspace = _Workspace({"scene-jupiter": 4})
    memory = _SearchSpyMemory()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        _capture_result(family="canvasReference", scene_revision=3)
    ]
    coordinator = _coordinator(memory=memory, workspace=workspace, realtime=provider)
    coordinator._latest_memory_commit_sequence = 1
    builds: list[DynamicContextBuildReason] = []

    async def build(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        reason: DynamicContextBuildReason,
    ) -> None:
        del _self
        builds.append(reason)

    coordinator._refresh_canvas_context = MethodType(build, coordinator)  # type: ignore[method-assign]
    await coordinator._run_memory_commit(payload=_payload())

    snapshot = await memory.load_snapshot(
        subject="child:semantic", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-1"]
    assert memory._semantic_journal_events.get("child:semantic", {}) == {}
    assert memory.search_call_count == 0
    assert builds == []


async def test_frame_semantic_proposal_adopts_canonical_frame_with_one_build() -> None:
    memory = _SearchSpyMemory()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [_capture_result(frame=True)]
    coordinator = _coordinator(memory=memory, realtime=provider)
    coordinator._latest_memory_commit_sequence = 1
    builds: list[int] = []

    async def build(
        _self: DeterministicRealtimeSessionCoordinator,
        *,
        reason: DynamicContextBuildReason,
    ) -> None:
        del reason
        builds.append(_self.dialogue_frame.revision)

    coordinator._refresh_canvas_context = MethodType(build, coordinator)  # type: ignore[method-assign]
    await coordinator._run_memory_commit(payload=_payload())

    assert coordinator.dialogue_frame.revision == 1
    assert coordinator.dialogue_frame.active_topics == ("Planets",)
    assert memory.search_call_count == 0
    assert builds == [1]


async def test_explicit_no_change_capture_persists_receipt() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        _capture_result(disposition=SemanticCaptureDisposition.NO_CHANGE)
    ]
    coordinator = _coordinator(memory=memory, realtime=provider)
    coordinator._latest_memory_commit_sequence = 1

    await coordinator._run_memory_commit(payload=_payload(turn_id="turn-nc"))

    receipt = await memory.load_semantic_memory_receipt(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-nc",
    )
    assert receipt is not None
    assert receipt.disposition == SemanticCaptureDisposition.NO_CHANGE
    assert receipt.event_results == ()


async def test_capture_provider_failure_preserves_turn_and_logs_warning() -> None:
    class _FailingCaptureProvider(DeterministicRealtimePlanProvider):
        async def capture_semantic_memory(
            self, *, evidence: SemanticCaptureEvidence
        ) -> SemanticCaptureFunctionArguments:
            del evidence
            raise RuntimeError("Capture model unavailable")

    memory = DevelopmentAuthoritativeMemoryStore()
    provider = _FailingCaptureProvider()
    coordinator = _coordinator(memory=memory, realtime=provider)
    coordinator._latest_memory_commit_sequence = 1

    await coordinator._run_memory_commit(payload=_payload(turn_id="turn-fail"))

    snapshot = await memory.load_snapshot(
        subject="child:semantic", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-fail"]
    receipt = await memory.load_semantic_memory_receipt(
        subject="child:semantic",
        expected_reset_revision=0,
        source_turn_id="turn-fail",
    )
    assert receipt is None


async def test_interrupted_turn_capture_passes_spoken_prefix_and_interrupted_flag() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        _capture_result(disposition=SemanticCaptureDisposition.NO_CHANGE)
    ]
    coordinator = _coordinator(memory=memory, realtime=provider)
    coordinator._latest_memory_commit_sequence = 1

    payload = _payload(
        turn_id="turn-intr",
        delivered_text="Jupiter is a gas gi...",
        interrupted=True,
    )
    await coordinator._run_memory_commit(payload=payload)

    assert len(provider.captured_evidences) == 1
    evidence = provider.captured_evidences[0]
    assert evidence.source_turn_id == "turn-intr"
    assert evidence.interrupted is True
    assert evidence.spoken_response_prefix == "Jupiter is a gas gi..."


async def test_request_complete_zero_audio_action_only_turn_capture() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        _capture_result(disposition=SemanticCaptureDisposition.NO_CHANGE)
    ]
    coordinator = _coordinator(memory=memory, realtime=provider)
    coordinator._latest_memory_commit_sequence = 1

    outcome = SettledSemanticToolOutcome(
        tool_name="manage_canvas",
        call_id="call_canvas_1",
        outcome="presented",
        detail="canvas updated",
    )
    payload = _payload(
        turn_id="turn-act",
        delivered_text="",
        child_text="Zoom in on Jupiter.",
        settled_tool_outcomes=(outcome,),
    )
    await coordinator._run_memory_commit(payload=payload)

    assert len(provider.captured_evidences) == 1
    evidence = provider.captured_evidences[0]
    assert evidence.source_turn_id == "turn-act"
    assert evidence.spoken_response_prefix is None
    assert evidence.child_utterance == "Zoom in on Jupiter."
    assert len(evidence.settled_tool_outcomes) == 1
    assert evidence.settled_tool_outcomes[0].tool_name == "manage_canvas"
    assert evidence.settled_tool_outcomes[0].outcome == "presented"


async def test_rejected_tool_call_in_settled_outcomes() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        _capture_result(disposition=SemanticCaptureDisposition.NO_CHANGE)
    ]
    coordinator = _coordinator(memory=memory, realtime=provider)
    coordinator._latest_memory_commit_sequence = 1

    outcome = SettledSemanticToolOutcome(
        tool_name="unknown_tool",
        call_id="call_unk_1",
        outcome="rejected",
        detail="unsupported utility tool",
    )
    payload = _payload(
        turn_id="turn-rej",
        settled_tool_outcomes=(outcome,),
    )
    await coordinator._run_memory_commit(payload=payload)

    assert len(provider.captured_evidences) == 1
    evidence = provider.captured_evidences[0]
    assert len(evidence.settled_tool_outcomes) == 1
    assert evidence.settled_tool_outcomes[0].outcome == "rejected"


async def test_consecutive_captures_execute_fifo_under_lock() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        _capture_result(summary="First planet preference.", frame=True),
        _capture_result(
            summary="Second planet preference.",
            frame=True,
            active_topics=("Planets", "Moons"),
        ),
    ]
    coordinator = _coordinator(memory=memory, realtime=provider)

    waiter_1: asyncio.Future[str | None] = (
        asyncio.get_running_loop().create_future()
    )
    p1 = _payload(
        sequence=1,
        turn_id="turn-c1",
        child_text=None,
        transcript_waiter=waiter_1,
    )
    p2 = _payload(
        sequence=2,
        turn_id="turn-c2",
        child_text="Second planet preference topic.",
        transcript_waiter=None,
    )

    coordinator._scheduled_memory_commit_turn_ids.add("turn-c1")
    coordinator._memory_commit_queue.put_nowait(p1)
    coordinator._scheduled_memory_commit_turn_ids.add("turn-c2")
    coordinator._memory_commit_queue.put_nowait(p2)
    coordinator._ensure_memory_commit_worker()

    await asyncio.sleep(0.02)

    # Turn N+1 must not overtake Turn N while Turn N is blocked on transcript
    assert len(provider.captured_evidences) == 0
    snapshot_blocked = await memory.load_snapshot(
        subject="child:semantic", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    assert snapshot_blocked.delivered_turns == []

    # Unblock Turn N's transcript
    waiter_1.set_result("First planet preference topic.")
    await coordinator._memory_commit_queue.join()
    if coordinator._memory_commit_worker_task is not None:
        coordinator._memory_commit_worker_task.cancel()

    # Exact FIFO processing sequence:
    # 1. Commit N delivered turn, then Capture N (updating frame 0 -> 1)
    # 2. Commit N+1 delivered turn, then Capture N+1 (seeing frame 1, updating 1 -> 2)
    assert len(provider.captured_evidences) == 2
    ev1, ev2 = provider.captured_evidences
    assert ev1.source_turn_id == "turn-c1"
    assert ev1.current_frame.active_topics == ()
    assert ev2.source_turn_id == "turn-c2"
    assert ev2.current_frame.active_topics == ("Planets",)

    snapshot = await memory.load_snapshot(
        subject="child:semantic", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == [
        "turn-c1",
        "turn-c2",
    ]
    assert coordinator.dialogue_frame.revision == 2


async def test_stale_semantic_decision_synchronizes_frame_without_journal_write() -> (
    None
):
    memory = DevelopmentAuthoritativeMemoryStore()
    await _advance_store_to_frame_revision_one(memory)
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        _capture_result(summary="The child prefers Saturn.", frame=True)
    ]
    coordinator = _coordinator(memory=memory, realtime=provider)
    coordinator._latest_memory_commit_sequence = 1

    await coordinator._run_memory_commit(payload=_payload(turn_id="turn-stale"))

    assert coordinator.dialogue_frame.revision == 1
    assert len(memory._semantic_journal_events["child:semantic"]) == 1


async def test_superseded_journal_draft_resolves_against_store() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    p1 = DeterministicRealtimePlanProvider()
    p1.canned_capture_results = [
        _capture_result(summary="The child loves Jupiter.")
    ]
    c1 = _coordinator(memory=memory, realtime=p1)
    await c1._run_memory_commit(payload=_payload(turn_id="turn-1"))

    events = memory._semantic_journal_events["child:semantic"]
    assert len(events) == 1
    first_event_id = next(iter(events))

    p2 = DeterministicRealtimePlanProvider()
    draft = SemanticCaptureJournalDraft(
        family=SemanticJournalEventFamily.USER_PREFERENCE,
        semantic_key="favorite_planet",
        summary="The child now loves Saturn instead.",
    )
    p2.canned_capture_results = [
        SemanticCaptureFunctionArguments(
            disposition=SemanticCaptureDisposition.CHANGED,
            dialogue_frame=None,
            journal_events=(draft,),
        )
    ]
    c2 = _coordinator(memory=memory, realtime=p2)
    c2._latest_memory_commit_sequence = 1
    await c2._run_memory_commit(payload=_payload(turn_id="turn-2"))

    events = memory._semantic_journal_events["child:semantic"]
    assert len(events) == 2
    assert first_event_id in memory._semantic_journal_supersessions["child:semantic"]
    second_event = [e for e in events.values() if e.event_id != first_event_id][0]
    active = await memory.get_active_semantic_journal_event(
        subject="child:semantic",
        expected_reset_revision=0,
        identity_key=second_event.identity_key,
    )
    assert active is not None
    assert active.event_id == second_event.event_id


async def test_stale_canvas_revision_reference_discarded_semantic_content_survives() -> (
    None
):
    workspace = _Workspace({"scene-mars": 2})
    memory = DevelopmentAuthoritativeMemoryStore()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        SemanticCaptureFunctionArguments(
            disposition=SemanticCaptureDisposition.CHANGED,
            journal_events=(
                SemanticCaptureJournalDraft(
                    family=SemanticJournalEventFamily.USER_PREFERENCE,
                    semantic_key="favorite_planet",
                    summary="Child is fascinated by Mars.",
                    canonical_canvas_references=(
                        SemanticJournalCanvasReference.model_validate({
                            "sceneId": "scene-mars",
                            "sceneRevision": 1,
                            "relation": "The discussed planet",
                        }),
                    ),
                ),
            ),
        )
    ]
    coordinator = _coordinator(memory=memory, workspace=workspace, realtime=provider)
    await coordinator._run_memory_commit(payload=_payload())

    snapshot = await memory.load_snapshot(
        subject="child:semantic", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-1"]
    events = memory._semantic_journal_events.get("child:semantic", {})
    assert len(events) == 1
    event = next(iter(events.values()))
    assert event.semantic_key == "favorite_planet"
    assert event.summary == "Child is fascinated by Mars."
    assert event.canonical_canvas_references == ()


async def test_empty_changed_proposal_fails_closed_without_receipt() -> None:
    workspace = _Workspace({"scene-saturn": 2})
    memory = DevelopmentAuthoritativeMemoryStore()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        SemanticCaptureFunctionArguments(
            disposition=SemanticCaptureDisposition.CHANGED,
            journal_events=(
                SemanticCaptureJournalDraft(
                    family=SemanticJournalEventFamily.CANVAS_REFERENCE,
                    semantic_key="saturn_rings",
                    summary="Saturn ring diagram.",
                    canonical_canvas_references=(
                        SemanticJournalCanvasReference.model_validate({
                            "sceneId": "scene-saturn",
                            "sceneRevision": 1,
                            "relation": "Ring view",
                        }),
                    ),
                ),
            ),
        )
    ]
    coordinator = _coordinator(memory=memory, workspace=workspace, realtime=provider)
    await coordinator._run_memory_commit(payload=_payload())

    snapshot = await memory.load_snapshot(
        subject="child:semantic", age_band=AgeBand.MIDDLE_CHILDHOOD
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == ["turn-1"]
    assert memory._semantic_journal_events.get("child:semantic", {}) == {}
    receipts = memory._semantic_memory_receipts.get("child:semantic", {})
    assert "turn-1" not in receipts


async def test_dialogue_frame_revision_not_incremented_when_frame_unchanged() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        SemanticCaptureFunctionArguments(
            disposition=SemanticCaptureDisposition.CHANGED,
            dialogue_frame=DialogueFrameState(),
            journal_events=(
                SemanticCaptureJournalDraft(
                    family=SemanticJournalEventFamily.USER_PREFERENCE,
                    semantic_key="favorite_color",
                    summary="Child loves cobalt blue.",
                ),
            ),
        )
    ]
    coordinator = _coordinator(memory=memory, realtime=provider)
    assert coordinator.dialogue_frame.revision == 0

    await coordinator._run_memory_commit(payload=_payload())

    # DialogueFrame revision must remain 0 since frame was identical to current state
    assert coordinator.dialogue_frame.revision == 0

    # Journal event must be durably committed
    events = memory._semantic_journal_events.get("child:semantic", {})
    assert len(events) == 1
    event = next(iter(events.values()))
    assert event.semantic_key == "favorite_color"
    assert event.summary == "Child loves cobalt blue."

    # Receipt records resulting_frame_revision = 0
    receipts = memory._semantic_memory_receipts.get("child:semantic", {})
    assert "turn-1" in receipts
    assert receipts["turn-1"].resulting_frame_revision == 0


async def test_dialogue_frame_revision_incremented_when_frame_evolves() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        SemanticCaptureFunctionArguments(
            disposition=SemanticCaptureDisposition.CHANGED,
            dialogue_frame=DialogueFrameState(active_topics=("astronomy",)),
            journal_events=(
                SemanticCaptureJournalDraft(
                    family=SemanticJournalEventFamily.USER_PREFERENCE,
                    semantic_key="favorite_color",
                    summary="Child loves cobalt blue.",
                ),
            ),
        )
    ]
    coordinator = _coordinator(memory=memory, realtime=provider)
    assert coordinator.dialogue_frame.revision == 0

    await coordinator._run_memory_commit(payload=_payload())

    # DialogueFrame revision must increment to 1
    assert coordinator.dialogue_frame.revision == 1
    assert coordinator.dialogue_frame.active_topics == ("astronomy",)

    # Journal event must be durably committed
    events = memory._semantic_journal_events.get("child:semantic", {})
    assert len(events) == 1

    # Receipt records resulting_frame_revision = 1
    receipts = memory._semantic_memory_receipts.get("child:semantic", {})
    assert "turn-1" in receipts
    assert receipts["turn-1"].resulting_frame_revision == 1


async def test_dialogue_frame_identical_with_no_journal_events_fails_closed() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    provider = DeterministicRealtimePlanProvider()
    provider.canned_capture_results = [
        SemanticCaptureFunctionArguments(
            disposition=SemanticCaptureDisposition.CHANGED,
            dialogue_frame=DialogueFrameState(),
            journal_events=(),
        )
    ]
    coordinator = _coordinator(memory=memory, realtime=provider)
    assert coordinator.dialogue_frame.revision == 0

    await coordinator._run_memory_commit(payload=_payload())

    # Frame revision unchanged
    assert coordinator.dialogue_frame.revision == 0

    # No journal events committed
    assert memory._semantic_journal_events.get("child:semantic", {}) == {}

    # EmptyChangedCaptureProposalError fails closed without creating receipt
    receipts = memory._semantic_memory_receipts.get("child:semantic", {})
    assert "turn-1" not in receipts


async def test_semantic_capture_changed_becomes_empty_fails_closed() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    provider = DeterministicRealtimePlanProvider()
    capture_args = SemanticCaptureFunctionArguments.model_validate(
        {
            "disposition": "changed",
            "dialogueFrame": {
                "activeGoals": ["x" * 200],
            },
            "journalEvents": [],
        }
    )
    provider.canned_capture_results = [capture_args]
    coordinator = _coordinator(memory=memory, realtime=provider)
    assert coordinator.dialogue_frame.revision == 0

    await coordinator._run_memory_commit(payload=_payload())

    assert coordinator.dialogue_frame.revision == 0
    assert coordinator.dialogue_frame.active_goals == ()
    assert memory._semantic_journal_events.get("child:semantic", {}) == {}
    receipts = memory._semantic_memory_receipts.get("child:semantic", {})
    assert "turn-1" not in receipts


async def test_semantic_capture_valid_journal_survives_overlong_frame_item() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    provider = DeterministicRealtimePlanProvider()
    capture_args = SemanticCaptureFunctionArguments.model_validate(
        {
            "disposition": "changed",
            "dialogueFrame": {
                "activeGoals": ["x" * 200],
            },
            "journalEvents": [
                {
                    "family": "userPreference",
                    "semanticKey": "astronomy_interest",
                    "summary": "Child is fascinated by Saturn's rings.",
                }
            ],
        }
    )
    provider.canned_capture_results = [capture_args]
    coordinator = _coordinator(memory=memory, realtime=provider)
    assert coordinator.dialogue_frame.revision == 0

    await coordinator._run_memory_commit(payload=_payload())

    assert coordinator.dialogue_frame.revision == 0
    assert coordinator.dialogue_frame.active_goals == ()

    events = memory._semantic_journal_events.get("child:semantic", {})
    assert len(events) == 1
    event = next(iter(events.values()))
    assert event.semantic_key == "astronomy_interest"
    assert event.summary == "Child is fascinated by Saturn's rings."

    receipts = memory._semantic_memory_receipts.get("child:semantic", {})
    assert "turn-1" in receipts
    assert receipts["turn-1"].resulting_frame_revision == 0

