from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from ava_backend.canvas.svg_delivery import SvgCanvasDocumentStore
from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_session import SvgCandidateStatus, SvgCanvasSessionState
from ava_backend.canvas.svg_specialist import (
    SvgCanvasSpecialistGeneration,
    SvgCanvasSpecialistRequest,
)
from ava_backend.protocol import AgeBand
from ava_backend.realtime.coordinator import DeterministicRealtimeSessionCoordinator
from ava_backend.realtime.coordinator_v4 import ProtocolV4CanvasEventSink
from ava_backend.realtime.events_v4 import (
    AVA_CLIENT_EVENT_ADAPTER_V4,
    AudioChunkEventV4,
    AudioMetadataEventV4,
    CanvasDocumentPreparedEventV4,
    CanvasPresentationCommandEventV4,
    SegmentReadyEventV4,
    TurnCancelledEventV4,
    TurnCompletedEventV4,
    TurnFailedEventV4,
    TurnPhaseChangedEventV4,
)
from ava_backend.realtime.fakes import (
    DETERMINISTIC_AUDIO_OFFER_SDP,
    DeterministicRealtimePlanProvider,
    DeterministicStreamingSpeechSynthesizer,
)
from ava_backend.realtime.memory import DevelopmentAuthoritativeMemoryStore
from ava_backend.realtime.plan import AvaTurnPlan
from ava_backend.realtime.provider_session import RealtimeProviderTurnEvent
from ava_backend.realtime.speech_contract import SpeechPhase
from ava_backend.realtime.svg_canvas_jobs import SvgCanvasJobCoordinator
from ava_backend.realtime.tool_contract import (
    InteractiveCanvasRequest,
    ManageCanvasPresentationRequest,
    RealtimeCanvasToolCall,
    RealtimeManageCanvasPresentationToolCall,
)
from ava_backend.realtime.websocket_v4 import (
    AvaServerControlFrameV4,
    AvaServerPcmFrameV4,
)


FIXTURES = Path(__file__).parents[2] / "protocol" / "fixtures"
SESSION_ID = "session-action-only"
TURN_ID = "turn-action-only"


class _ActionOnlyCanvasProvider(DeterministicRealtimePlanProvider):
    def __init__(self, plan: AvaTurnPlan) -> None:
        super().__init__(plan, child_text="Please draw the water cycle.")

    async def _stream(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        self.turn_stream_started.set()
        self._pending_call_id = "call-action-only"
        yield RealtimeCanvasToolCall(
            call_id=self._pending_call_id,
            request=InteractiveCanvasRequest.model_validate(
                {
                    "operation": "new",
                    "brief": "Show the water cycle.",
                    "interactionMode": "none",
                    "continuousControl": "none",
                    "completion": "requestComplete",
                }
            ),
        )
        if len(self.completed_canvas_outputs) != 1:
            raise RuntimeError(
                "action-only canvas output was not completed exactly once"
            )
        self._pending_call_id = None


class _ActionOnlyManageProvider(DeterministicRealtimePlanProvider):
    async def _stream(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        self.turn_stream_started.set()
        yield RealtimeManageCanvasPresentationToolCall(
            call_id="call-manage-action-only",
            request=ManageCanvasPresentationRequest.model_validate(
                {"action": "hide", "completion": "requestComplete"}
            ),
            provider_response_id="response-manage-action-only",
        )
        if len(self.completed_manage_canvas_presentation_outputs) != 1:
            raise RuntimeError("action-only presentation output was not completed once")


class _ActionOnlyCanvasRejectedProvider(DeterministicRealtimePlanProvider):
    def __init__(self, plan: AvaTurnPlan) -> None:
        super().__init__(plan, child_text="Please revise missing scene.")

    async def _stream(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        self.turn_stream_started.set()
        self._pending_call_id = "call-action-only-rej"
        yield RealtimeCanvasToolCall(
            call_id=self._pending_call_id,
            request=InteractiveCanvasRequest.model_validate(
                {
                    "operation": "revise",
                    "selector": "title",
                    "title": "Missing scene",
                    "brief": "Revise the missing scene.",
                    "interactionMode": "none",
                    "continuousControl": "none",
                    "completion": "requestComplete",
                }
            ),
        )
        if len(self.completed_canvas_outputs) != 1:
            raise RuntimeError(
                "action-only canvas output was not completed exactly once"
            )
        self._pending_call_id = None


class _ActionOnlyManageRejectedProvider(DeterministicRealtimePlanProvider):
    async def _stream(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        self.turn_stream_started.set()
        yield RealtimeManageCanvasPresentationToolCall(
            call_id="call-manage-action-only-rej",
            request=ManageCanvasPresentationRequest.model_validate(
                {
                    "action": "showTitle",
                    "title": "Missing scene",
                    "completion": "requestComplete",
                }
            ),
            provider_response_id="response-manage-action-only-rej",
        )
        if len(self.completed_manage_canvas_presentation_outputs) != 1:
            raise RuntimeError("action-only presentation output was not completed once")


class _BlockingCanvasSpecialist:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
        *,
        draft_sink: object | None = None,
    ) -> SvgCanvasSpecialistGeneration:
        del draft_sink
        self.started.set()
        await self.release.wait()
        return SvgCanvasSpecialistGeneration(
            generation_id=request.generation_id,
            document=SvgDocumentValidator().validate(
                """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600"
 data-ava-interaction-mode="none">
<title>Water cycle</title><desc>Water moving through a simple cycle.</desc>
<circle id="cycle" cx="500" cy="300" r="180"/>
</svg>"""
            ),
        )

    async def cancel(self, *, generation_id: str) -> None:
        del generation_id

    async def close(self) -> None:
        return


class _FailingActionOnlyPresentationJobs:
    def __init__(self, delegate: SvgCanvasJobCoordinator) -> None:
        self.delegate = delegate
        self.request_failures = 0
        self.interrupted_recoveries = 0

    def __getattr__(self, name: str) -> object:
        return getattr(self.delegate, name)

    async def request_presentation(
        self,
        *,
        generation_id: str,
        presentation_sample_offset: int,
    ) -> None:
        del generation_id, presentation_sample_offset
        self.request_failures += 1
        raise RuntimeError("controlled action-only presentation scheduling failure")

    async def request_interrupted_presentation(
        self,
        *,
        source_turn_id: str,
        presentation_sample_offset: int,
    ) -> object:
        self.interrupted_recoveries += 1
        return await self.delegate.request_interrupted_presentation(
            source_turn_id=source_turn_id,
            presentation_sample_offset=presentation_sample_offset,
        )


def _plan() -> AvaTurnPlan:
    return AvaTurnPlan.model_validate_json(
        (FIXTURES / "ava_turn_plan_conversation_v3.json").read_text(encoding="utf-8")
    )


@pytest.mark.asyncio
async def test_action_only_canvas_turn_completes_without_audio_and_persists_child() -> (
    None
):
    provider = _ActionOnlyCanvasProvider(_plan())
    specialist = _BlockingCanvasSpecialist()
    memory = DevelopmentAuthoritativeMemoryStore()
    state = SvgCanvasSessionState()
    canvas_events = ProtocolV4CanvasEventSink()
    jobs = SvgCanvasJobCoordinator(
        session_id=SESSION_ID,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=state,
        documents=SvgCanvasDocumentStore(),
        event_sink=canvas_events,
        generation_id_factory=lambda: "generation-action-only",
    )
    speech = DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31)
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id=SESSION_ID,
        turn_id=TURN_ID,
        request_id="request-action-only",
        stream_id="stream-action-only",
        subject="child:action-only",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=provider,
        speech=speech,
        canvas_jobs=jobs,
        canvas_events=canvas_events,
        memory=memory,
    )
    await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "sessionId": SESSION_ID,
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )

    frames = [frame async for frame in coordinator.frames()]
    await specialist.started.wait()
    await coordinator._memory_commit_queue.join()

    turn_events = [
        frame.event for frame in frames if isinstance(frame, AvaServerControlFrameV4)
    ]
    assert [
        event.phase
        for event in turn_events
        if isinstance(event, TurnPhaseChangedEventV4)
    ] == [SpeechPhase.CONNECTING, SpeechPhase.LISTENING, SpeechPhase.PLANNING]
    completed = [
        event for event in turn_events if isinstance(event, TurnCompletedEventV4)
    ]
    assert len(completed) == 1
    assert completed[0].delivered_text == ""
    assert completed[0].delivered_sample_count == 0
    assert not any(isinstance(event, SegmentReadyEventV4) for event in turn_events)
    assert not any(isinstance(event, AudioMetadataEventV4) for event in turn_events)
    assert not any(isinstance(event, AudioChunkEventV4) for event in turn_events)
    assert not any(isinstance(frame, AvaServerPcmFrameV4) for frame in frames)
    assert speech.synthesized_segment_ids == []
    assert len(provider.completed_canvas_outputs) == 1
    execution = state.workspace.execution("generation-action-only")
    assert execution is not None
    assert jobs._presentation_offsets == {"generation-action-only": 0}

    snapshot = await memory.load_snapshot(
        subject="child:action-only",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert len(snapshot.delivered_turns) == 1
    persisted = snapshot.delivered_turns[0]
    assert persisted.child_text == "Please draw the water cycle."
    assert persisted.ava_delivered_text == ""
    assert persisted.delivered_sample_count == 0
    assert persisted.terminal == "completed"

    # Ordinary speech interruption owns the new turn, not accepted workspace work.
    assert (
        await jobs.request_interrupted_presentation(
            source_turn_id=TURN_ID,
            presentation_sample_offset=0,
        )
        is None
    )
    assert state.workspace.execution("generation-action-only") is execution
    assert not execution.status.terminal

    specialist.release.set()
    await jobs.wait_for_background_work()
    await coordinator.close()


@pytest.mark.asyncio
async def test_action_only_manage_canvas_turn_completes_without_audio() -> None:
    provider = _ActionOnlyManageProvider(
        _plan(),
        child_text="Please close the picture.",
    )
    state = SvgCanvasSessionState()
    canvas_events = ProtocolV4CanvasEventSink()
    jobs = SvgCanvasJobCoordinator(
        session_id=SESSION_ID,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=_BlockingCanvasSpecialist(),
        state=state,
        documents=SvgCanvasDocumentStore(),
        event_sink=canvas_events,
        generation_id_factory=lambda: "unused-manage-generation",
    )
    speech = DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31)
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id=SESSION_ID,
        turn_id=TURN_ID,
        request_id="request-manage-action-only",
        stream_id="stream-manage-action-only",
        subject="child:manage-action-only",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=provider,
        speech=speech,
        canvas_jobs=jobs,
        canvas_events=canvas_events,
        memory=DevelopmentAuthoritativeMemoryStore(),
    )
    await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "sessionId": SESSION_ID,
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )

    frames: list[object] = []
    async for frame in coordinator.frames():
        frames.append(frame)
        if isinstance(frame, AvaServerControlFrameV4) and isinstance(
            frame.event,
            CanvasPresentationCommandEventV4,
        ):
            await coordinator.receive(
                AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
                    {
                        "protocolVersion": 4,
                        "sessionId": SESSION_ID,
                        "event": "canvasPresentationAcknowledged",
                        "sequence": 1,
                        "commandId": frame.event.command_id,
                        "applied": True,
                    }
                )
            )

    turn_events = [
        frame.event for frame in frames if isinstance(frame, AvaServerControlFrameV4)
    ]
    completed = [
        event for event in turn_events if isinstance(event, TurnCompletedEventV4)
    ]
    assert len(completed) == 1
    assert completed[0].delivered_text == ""
    assert completed[0].delivered_sample_count == 0
    assert len(provider.completed_manage_canvas_presentation_outputs) == 1
    assert speech.synthesized_segment_ids == []
    assert not any(isinstance(frame, AvaServerPcmFrameV4) for frame in frames)
    await coordinator.close()


@pytest.mark.asyncio
async def test_action_only_presentation_failure_is_visible_and_reanchors_job() -> None:
    provider = _ActionOnlyCanvasProvider(_plan())
    specialist = _BlockingCanvasSpecialist()
    state = SvgCanvasSessionState()
    canvas_events = ProtocolV4CanvasEventSink()
    jobs = SvgCanvasJobCoordinator(
        session_id=SESSION_ID,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=state,
        documents=SvgCanvasDocumentStore(),
        event_sink=canvas_events,
        generation_id_factory=lambda: "generation-presentation-failure",
    )
    failing_jobs = _FailingActionOnlyPresentationJobs(jobs)
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id=SESSION_ID,
        turn_id=TURN_ID,
        request_id="request-presentation-failure",
        stream_id="stream-presentation-failure",
        subject="child:presentation-failure",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=provider,
        speech=DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31),
        canvas_jobs=failing_jobs,  # type: ignore[arg-type]
        canvas_events=canvas_events,
        memory=DevelopmentAuthoritativeMemoryStore(),
    )
    await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "sessionId": SESSION_ID,
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )

    frames = [frame async for frame in coordinator.frames()]
    turn_events = [
        frame.event for frame in frames if isinstance(frame, AvaServerControlFrameV4)
    ]
    assert not any(isinstance(event, TurnCompletedEventV4) for event in turn_events)
    assert sum(isinstance(event, TurnFailedEventV4) for event in turn_events) == 1
    assert failing_jobs.request_failures == 1
    assert failing_jobs.interrupted_recoveries == 1
    assert jobs._presentation_offsets == {"generation-presentation-failure": 0}
    execution = state.workspace.execution("generation-presentation-failure")
    assert execution is not None
    assert not execution.status.terminal

    specialist.release.set()
    await jobs.wait_for_background_work()
    candidate = state.candidate
    assert candidate is not None
    scheduled = await jobs.document_prepared(
        CanvasDocumentPreparedEventV4.model_validate(
            {
                "protocolVersion": 4,
                "event": "canvasDocumentPrepared",
                "sessionId": SESSION_ID,
                "sequence": 1,
                "generationId": candidate.generation_id,
                "sceneId": candidate.scene_id,
                "sceneRevision": candidate.revision,
                "svgSha256": candidate.document.svg_sha256,
                "manifestSha256": candidate.document.manifest_sha256,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    assert scheduled.status == SvgCandidateStatus.PRESENTATION_SCHEDULED
    assert scheduled.presentation_sample_offset == 0
    await coordinator.close()


@pytest.mark.asyncio
async def test_action_only_manage_canvas_rejected_turn_completes_cleanly() -> None:
    provider = _ActionOnlyManageRejectedProvider(
        _plan(),
        child_text="Please show missing picture.",
    )
    state = SvgCanvasSessionState()
    canvas_events = ProtocolV4CanvasEventSink()
    jobs = SvgCanvasJobCoordinator(
        session_id=SESSION_ID,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=_BlockingCanvasSpecialist(),
        state=state,
        documents=SvgCanvasDocumentStore(),
        event_sink=canvas_events,
        generation_id_factory=lambda: "unused-manage-generation",
    )
    speech = DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31)
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id=SESSION_ID,
        turn_id=TURN_ID,
        request_id="request-manage-action-only-rej",
        stream_id="stream-manage-action-only-rej",
        subject="child:manage-action-only-rej",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=provider,
        speech=speech,
        canvas_jobs=jobs,
        canvas_events=canvas_events,
        memory=memory,
    )
    await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "sessionId": SESSION_ID,
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )

    frames = [frame async for frame in coordinator.frames()]
    await coordinator._memory_commit_queue.join()

    turn_events = [
        frame.event for frame in frames if isinstance(frame, AvaServerControlFrameV4)
    ]
    completed = [
        event for event in turn_events if isinstance(event, TurnCompletedEventV4)
    ]
    assert len(completed) == 1
    assert completed[0].delivered_text == ""
    assert completed[0].delivered_sample_count == 0
    assert not any(isinstance(event, TurnCancelledEventV4) for event in turn_events)
    assert not any(isinstance(event, TurnFailedEventV4) for event in turn_events)
    assert len(provider.completed_manage_canvas_presentation_outputs) == 1
    assert (
        str(provider.completed_manage_canvas_presentation_outputs[0].status)
        == "rejected"
    )
    assert speech.synthesized_segment_ids == []
    assert not any(isinstance(frame, AvaServerPcmFrameV4) for frame in frames)

    snapshot = await memory.load_snapshot(
        subject="child:manage-action-only-rej",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert len(snapshot.delivered_turns) == 1
    persisted = snapshot.delivered_turns[0]
    assert persisted.child_text == "Please show missing picture."
    assert persisted.ava_delivered_text == ""
    assert persisted.delivered_sample_count == 0
    assert persisted.terminal == "completed"

    await coordinator.close()


@pytest.mark.asyncio
async def test_action_only_canvas_specialist_rejected_turn_completes_cleanly() -> None:
    provider = _ActionOnlyCanvasRejectedProvider(_plan())
    specialist = _BlockingCanvasSpecialist()
    memory = DevelopmentAuthoritativeMemoryStore()
    state = SvgCanvasSessionState()
    canvas_events = ProtocolV4CanvasEventSink()
    jobs = SvgCanvasJobCoordinator(
        session_id=SESSION_ID,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=state,
        documents=SvgCanvasDocumentStore(),
        event_sink=canvas_events,
        generation_id_factory=lambda: "generation-action-only-rej",
    )
    speech = DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31)
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id=SESSION_ID,
        turn_id=TURN_ID,
        request_id="request-action-only-rej",
        stream_id="stream-action-only-rej",
        subject="child:action-only-rej",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=provider,
        speech=speech,
        canvas_jobs=jobs,
        canvas_events=canvas_events,
        memory=memory,
    )
    await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "sessionId": SESSION_ID,
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )

    frames = [frame async for frame in coordinator.frames()]
    await coordinator._memory_commit_queue.join()

    turn_events = [
        frame.event for frame in frames if isinstance(frame, AvaServerControlFrameV4)
    ]
    completed = [
        event for event in turn_events if isinstance(event, TurnCompletedEventV4)
    ]
    assert len(completed) == 1
    assert completed[0].delivered_text == ""
    assert completed[0].delivered_sample_count == 0
    assert not any(isinstance(event, TurnCancelledEventV4) for event in turn_events)
    assert not any(isinstance(event, TurnFailedEventV4) for event in turn_events)
    assert len(provider.completed_canvas_outputs) == 1
    assert str(provider.completed_canvas_outputs[0].status) == "rejected"
    assert speech.synthesized_segment_ids == []
    assert not any(isinstance(frame, AvaServerPcmFrameV4) for frame in frames)

    snapshot = await memory.load_snapshot(
        subject="child:action-only-rej",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert len(snapshot.delivered_turns) == 1
    persisted = snapshot.delivered_turns[0]
    assert persisted.child_text == "Please revise missing scene."
    assert persisted.ava_delivered_text == ""
    assert persisted.delivered_sample_count == 0
    assert persisted.terminal == "completed"

    await coordinator.close()


@pytest.mark.asyncio
async def test_action_only_canvas_turn_client_cancellation_emits_interrupted_by_speech() -> (
    None
):
    provider = _ActionOnlyCanvasProvider(_plan())
    specialist = _BlockingCanvasSpecialist()
    memory = DevelopmentAuthoritativeMemoryStore()
    state = SvgCanvasSessionState()
    canvas_events = ProtocolV4CanvasEventSink()
    jobs = SvgCanvasJobCoordinator(
        session_id=SESSION_ID,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=state,
        documents=SvgCanvasDocumentStore(),
        event_sink=canvas_events,
        generation_id_factory=lambda: "generation-action-only-cancel",
    )
    speech = DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31)
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id=SESSION_ID,
        turn_id=TURN_ID,
        request_id="request-action-only-cancel",
        stream_id="stream-action-only-cancel",
        subject="child:action-only-cancel",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=provider,
        speech=speech,
        canvas_jobs=jobs,
        canvas_events=canvas_events,
        memory=memory,
    )
    await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "sessionId": SESSION_ID,
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )

    frames: list[object] = []

    async def _receive_cancel_when_ready() -> None:
        await provider.turn_stream_started.wait()
        await coordinator.receive(
            AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
                {
                    "protocolVersion": 4,
                    "sessionId": SESSION_ID,
                    "event": "cancelTurn",
                    "sequence": 1,
                    "turnId": TURN_ID,
                    "requestId": "request-action-only-cancel",
                    "reason": "interrupted_by_speech",
                }
            )
        )

    cancel_task = asyncio.create_task(_receive_cancel_when_ready())
    async for frame in coordinator.frames():
        frames.append(frame)
    await cancel_task

    turn_events = [
        frame.event for frame in frames if isinstance(frame, AvaServerControlFrameV4)
    ]
    cancelled = [
        event for event in turn_events if isinstance(event, TurnCancelledEventV4)
    ]
    assert len(cancelled) == 1
    assert cancelled[0].reason == "interrupted_by_speech"
    assert not any(isinstance(event, TurnCompletedEventV4) for event in turn_events)

    specialist.release.set()
    await jobs.wait_for_background_work()
    await coordinator.close()
