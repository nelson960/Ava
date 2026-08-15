from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from ava_backend.canvas.specialist import (
    CanvasSpecialistInvalidError,
    CanvasSpecialistUnavailableError,
)
from ava_backend.protocol import AgeBand
from ava_backend.canvas.session import CanvasSessionState
from ava_backend.realtime.coordinator import (
    AvaServerControlFrame,
    AvaServerOutboundFrame,
    AvaServerPcmFrame,
    CoordinatorStateError,
    DeterministicRealtimeSessionCoordinator,
    _canvas_rejection_message,
    _canvas_tool_rejection_reason,
)
from ava_backend.realtime.delivery import SynthesizedSpeechItem
from ava_backend.realtime.events import (
    AVA_CLIENT_EVENT_ADAPTER,
    AlignmentBatchEvent,
    AudioChunkEvent,
    AudioMetadataEvent,
    AvaClientEventUnion,
    AvaFailureCode,
    AvaFailureComponent,
    AvaTurnPhase,
    CanvasCueEvent,
    CanvasRejectedEvent,
    InteractionRejectedEvent,
    MemoryPersistenceFailedEvent,
    SegmentReadyEvent,
    TurnCancelledEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnPhaseChangedEvent,
)
from ava_backend.realtime.fakes import (
    DETERMINISTIC_AUDIO_OFFER_SDP,
    DeterministicCanvasSpecialist,
    DeterministicRealtimePlanProvider,
    DeterministicStreamingSpeechSynthesizer,
)
from ava_backend.realtime.model_plan import (
    ModelCanvasDraft,
    model_canvas_draft_from_runtime_plan,
)
from ava_backend.realtime.memory import DevelopmentAuthoritativeMemoryStore
from ava_backend.realtime.plan import AvaTurnPlan, AvaTurnSegment
from ava_backend.realtime.provider_session import (
    NegotiatedRealtimeCall,
    RealtimeProviderTurnEvent,
)
from ava_backend.realtime.tool_contract import (
    INTERACTIVE_CANVAS_TOOL_NAME,
    InteractiveCanvasToolOutput,
    RealtimeSpeechTextDelta,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"


def _plan(name: str = "ava_turn_plan_v3.json") -> AvaTurnPlan:
    return AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / name).read_text(encoding="utf-8")
    )


def test_canvas_rejection_messages_distinguish_timeout_from_validation() -> None:
    timeout = CanvasSpecialistUnavailableError(
        "canvas specialist exceeded its deadline"
    )
    invalid = CanvasSpecialistInvalidError(
        "canvas specialist response failed strict parsing"
    )

    assert _canvas_rejection_message(timeout) == (
        "The optional visual took too long to prepare."
    )
    assert "deadline" in _canvas_tool_rejection_reason(timeout)
    assert _canvas_rejection_message(invalid) == (
        "The optional visual could not be prepared from that response."
    )
    assert "validation" in _canvas_tool_rejection_reason(invalid)


def _speech_only_plan(segment_count: int) -> AvaTurnPlan:
    base = _plan("ava_turn_plan_conversation_v3.json").model_dump(
        mode="json",
        by_alias=True,
    )
    template = base["segments"][0]
    base["segments"] = [
        {
            **template,
            "id": f"speech_{index + 1}",
            "speech": f"Part {index + 1}.",
        }
        for index in range(segment_count)
    ]
    return AvaTurnPlan.model_validate(base)


def _client(payload: dict[str, object]) -> AvaClientEventUnion:
    return AVA_CLIENT_EVENT_ADAPTER.validate_python(
        {
            "protocolVersion": 3,
            "sessionId": "session-coordinator",
            **payload,
        }
    )


def _coordinator(
    plan: AvaTurnPlan,
    *,
    child_text: str | None,
    memory: DevelopmentAuthoritativeMemoryStore | None = None,
    specialist_error: Exception | None = None,
    specialist_draft: ModelCanvasDraft | None = None,
    canvas_state: CanvasSessionState | None = None,
    rejected_canvas_reason: str | None = None,
) -> tuple[
    DeterministicRealtimeSessionCoordinator,
    DeterministicRealtimePlanProvider,
    DeterministicStreamingSpeechSynthesizer,
    DeterministicCanvasSpecialist,
    DevelopmentAuthoritativeMemoryStore,
]:
    resolved_memory = memory or DevelopmentAuthoritativeMemoryStore()
    realtime = DeterministicRealtimePlanProvider(
        plan,
        child_text=child_text,
        canvas_brief=(
            "Draw a labeled plant cell with a tappable nucleus label."
            if any(segment.canvas_cues for segment in plan.segments)
            else None
        ),
        rejected_canvas_reason=rejected_canvas_reason,
        chunk_sizes=(3, 11, 1, 29, 7),
    )
    speech = DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31)
    specialist = DeterministicCanvasSpecialist(
        specialist_draft or model_canvas_draft_from_runtime_plan(_plan()),
        error=specialist_error,
    )
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id="session-coordinator",
        turn_id="turn-coordinator",
        request_id="request-coordinator",
        stream_id="stream-coordinator",
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=realtime,
        speech=speech,
        canvas_specialist=specialist,
        memory=resolved_memory,
        memory_id_factory=lambda turn_id: f"memory:{turn_id}",
        canvas_state=canvas_state,
    )
    return coordinator, realtime, speech, specialist, resolved_memory


async def _ready(
    coordinator: DeterministicRealtimeSessionCoordinator,
) -> None:
    await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coordinator.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
            }
        )
    )


async def test_coordinator_streams_incremental_plan_pcm_canvas_and_memory() -> None:
    plan = _plan()
    coordinator, realtime, speech, specialist, memory = _coordinator(
        plan,
        child_text="The child asked how a plant cell works.",
    )
    await _ready(coordinator)

    frames: list[AvaServerOutboundFrame] = []
    control_events = []
    async for frame in coordinator.frames():
        frames.append(frame)
        if not isinstance(frame, AvaServerControlFrame):
            continue
        control_events.append(frame.event)
        if (
            isinstance(frame.event, TurnPhaseChangedEvent)
            and frame.event.phase == AvaTurnPhase.SPEAKING
        ):
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": 1,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": 0,
                    }
                )
            )
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackProgress",
                        "sequence": 2,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": coordinator.received_sample_count,
                        "bufferedSampleCount": 0,
                    }
                )
            )

    assert isinstance(control_events[-1], TurnCompletedEvent)
    assert control_events[-1].delivered_text == plan.speech
    assert realtime.requests[0].child_text == (
        "The child asked how a plant cell works."
    )
    session = realtime.requests[0].session_update["session"]
    assert isinstance(session, dict)
    assert session["tool_choice"] == "auto"
    assert [tool["name"] for tool in session["tools"]] == [
        INTERACTIVE_CANVAS_TOOL_NAME,
    ]
    assert speech.synthesized_segment_ids == ["speech_1", "speech_2"]

    event_names = [event.event for event in control_events]
    first_audio_index = event_names.index("audioChunk")
    second_segment_index = [
        index for index, name in enumerate(event_names) if name == "segmentReady"
    ][1]
    assert first_audio_index < second_segment_index
    assert "canvasCue" in event_names
    assert len(specialist.requests) == 1
    assert specialist.requests[0].brief.startswith(
        "Draw a labeled plant cell with a tappable nucleus label."
    )
    assert len(realtime.completed_canvas_outputs) == 1
    assert realtime.completed_canvas_outputs[0].status == "ready"
    assert coordinator.last_canvas_generation is not None

    for index, frame in enumerate(frames):
        if isinstance(frame, AvaServerControlFrame) and isinstance(
            frame.event, AudioChunkEvent
        ):
            binary = frames[index + 1]
            assert isinstance(binary, AvaServerPcmFrame)
            assert binary.descriptor == frame.event
            assert len(binary.pcm) == frame.event.byte_length

    snapshot = await memory.load_snapshot(
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert snapshot.delivered_turns[0].ava_delivered_text == plan.speech
    assert snapshot.delivered_turns[0].terminal == "completed"
    assert snapshot.stable_memories == []
    await coordinator.close()
    assert coordinator.last_memory_decision is not None
    assert coordinator.last_memory_decision.accepted is False
    assert coordinator.last_memory_decision.reason == "No stable memory was proposed."
    await coordinator.close()
    assert realtime.cancelled_turn_ids == []
    assert speech.cancelled_turn_ids == []


async def test_coordinator_streams_seventh_segment_without_protocol_failure() -> None:
    plan = _speech_only_plan(7)
    coordinator, _realtime, speech, _specialist, _memory = _coordinator(
        plan,
        child_text="Please give me a seven-part answer.",
    )
    await _ready(coordinator)

    control_events = []
    async for frame in coordinator.frames():
        if not isinstance(frame, AvaServerControlFrame):
            continue
        control_events.append(frame.event)
        if (
            isinstance(frame.event, TurnPhaseChangedEvent)
            and frame.event.phase == AvaTurnPhase.SPEAKING
        ):
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": 1,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": 0,
                    }
                )
            )
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackProgress",
                        "sequence": 2,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": coordinator.received_sample_count,
                        "bufferedSampleCount": 0,
                    }
                )
            )

    segment_events = [
        event for event in control_events if isinstance(event, SegmentReadyEvent)
    ]
    assert [event.segment_index for event in segment_events] == list(range(7))
    assert speech.synthesized_segment_ids == [
        f"speech_{index}" for index in range(1, 8)
    ]
    assert isinstance(control_events[-1], TurnCompletedEvent)
    assert control_events[-1].delivered_text == plan.speech
    await coordinator.close()


class _BlockedFirstSegmentSpeech(DeterministicStreamingSpeechSynthesizer):
    def __init__(self) -> None:
        super().__init__(chunk_sample_count=31)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def synthesize(
        self,
        segment: AvaTurnSegment,
    ) -> AsyncIterator[SynthesizedSpeechItem]:
        self.started.set()
        await self.release.wait()
        async for item in super().synthesize(segment):
            yield item


async def test_canvas_specialist_starts_while_first_segment_tts_is_in_flight() -> None:
    plan = _plan()
    memory = DevelopmentAuthoritativeMemoryStore()
    realtime = DeterministicRealtimePlanProvider(
        plan,
        child_text="Show me how a plant cell works.",
        canvas_brief="Draw a labeled plant cell with a tappable nucleus.",
        chunk_sizes=(1, 2, 3, 5, 8),
    )
    speech = _BlockedFirstSegmentSpeech()
    specialist = DeterministicCanvasSpecialist(
        model_canvas_draft_from_runtime_plan(plan)
    )
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id="session-overlap",
        turn_id="turn-overlap",
        request_id="request-overlap",
        stream_id="stream-overlap",
        subject="child:overlap",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=realtime,
        speech=speech,
        canvas_specialist=specialist,
        memory=memory,
        memory_id_factory=lambda turn_id: f"memory:{turn_id}",
    )
    await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER.validate_python(
            {
                "protocolVersion": 3,
                "sessionId": "session-overlap",
                "event": "clientReady",
                "sequence": 0,
            }
        )
    )

    async def consume() -> None:
        async for frame in coordinator.frames():
            if not isinstance(frame, AvaServerControlFrame):
                continue
            if (
                isinstance(frame.event, TurnPhaseChangedEvent)
                and frame.event.phase == AvaTurnPhase.SPEAKING
            ):
                await coordinator.receive(
                    AVA_CLIENT_EVENT_ADAPTER.validate_python(
                        {
                            "protocolVersion": 3,
                            "sessionId": "session-overlap",
                            "event": "playbackStarted",
                            "sequence": 1,
                            "turnId": "turn-overlap",
                            "streamId": "stream-overlap",
                            "renderedSampleCount": 0,
                        }
                    )
                )
                await coordinator.receive(
                    AVA_CLIENT_EVENT_ADAPTER.validate_python(
                        {
                            "protocolVersion": 3,
                            "sessionId": "session-overlap",
                            "event": "playbackProgress",
                            "sequence": 2,
                            "turnId": "turn-overlap",
                            "streamId": "stream-overlap",
                            "renderedSampleCount": coordinator.received_sample_count,
                            "bufferedSampleCount": 0,
                        }
                    )
                )

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(speech.started.wait(), timeout=1)
    await asyncio.wait_for(specialist.request_started.wait(), timeout=1)
    assert speech.release.is_set() is False
    assert specialist.requests[0].brief.startswith(
        "Draw a labeled plant cell with a tappable nucleus."
    )
    speech.release.set()
    await asyncio.wait_for(consumer, timeout=2)
    await coordinator.close()


async def test_persistent_coordinator_runs_two_isolated_turns_on_one_call() -> None:
    plan = _plan("ava_turn_plan_conversation_v3.json")
    memory = DevelopmentAuthoritativeMemoryStore()
    realtime = DeterministicRealtimePlanProvider(
        plan,
        child_text="Tell me something gentle.",
        chunk_sizes=(3, 11, 7),
    )
    speech = DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31)
    specialist = DeterministicCanvasSpecialist(
        model_canvas_draft_from_runtime_plan(_plan())
    )
    turn_ids = iter(("turn-persistent-2", "turn-persistent-3"))
    request_ids = iter(("request-persistent-2", "request-persistent-3"))
    stream_ids = iter(("stream-persistent-2", "stream-persistent-3"))
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id="session-coordinator",
        turn_id="turn-persistent-1",
        request_id="request-persistent-1",
        stream_id="stream-persistent-1",
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=realtime,
        speech=speech,
        canvas_specialist=specialist,
        memory=memory,
        memory_id_factory=lambda turn_id: f"memory:{turn_id}",
        persistent=True,
        turn_id_factory=lambda: next(turn_ids),
        request_id_factory=lambda: next(request_ids),
        stream_id_factory=lambda: next(stream_ids),
    )
    await _ready(coordinator)

    terminal_turn_ids: list[str] = []
    client_sequence = 1
    active_stream_id: str | None = None
    previous_terminal_stream_id: str | None = None
    late_terminal_control_sent = False
    frame_stream = coordinator.frames()
    async for frame in frame_stream:
        if not isinstance(frame, AvaServerControlFrame):
            continue
        event = frame.event
        if (
            isinstance(event, TurnPhaseChangedEvent)
            and event.phase == AvaTurnPhase.LISTENING
            and terminal_turn_ids
            and not late_terminal_control_sent
        ):
            assert previous_terminal_stream_id is not None
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": client_sequence,
                        "turnId": terminal_turn_ids[-1],
                        "streamId": previous_terminal_stream_id,
                        "renderedSampleCount": 0,
                    }
                )
            )
            client_sequence += 1
            late_terminal_control_sent = True
        if isinstance(event, AudioMetadataEvent):
            active_stream_id = event.stream_id
        if (
            isinstance(event, TurnPhaseChangedEvent)
            and event.phase == AvaTurnPhase.SPEAKING
        ):
            assert active_stream_id is not None
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": client_sequence,
                        "turnId": event.turn_id,
                        "streamId": active_stream_id,
                        "renderedSampleCount": 0,
                    }
                )
            )
            client_sequence += 1
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackProgress",
                        "sequence": client_sequence,
                        "turnId": event.turn_id,
                        "streamId": active_stream_id,
                        "renderedSampleCount": coordinator.received_sample_count,
                        "bufferedSampleCount": 0,
                    }
                )
            )
            client_sequence += 1
        if isinstance(event, TurnCompletedEvent):
            terminal_turn_ids.append(event.turn_id)
            if len(terminal_turn_ids) == 1:
                previous_terminal_stream_id = active_stream_id
            if len(terminal_turn_ids) == 2:
                break
    await frame_stream.aclose()

    assert terminal_turn_ids == ["turn-persistent-1", "turn-persistent-2"]
    assert late_terminal_control_sent is True
    assert len(realtime.negotiated_offers) == 1
    assert [request.turn_id for request in realtime.requests] == terminal_turn_ids
    assert len(realtime.prepared_session_updates) == 1
    snapshot = await memory.load_snapshot(
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == terminal_turn_ids
    await coordinator.close()


class _ListeningRealtimeProvider(DeterministicRealtimePlanProvider):
    """Realtime fake that remains silent until its iterator is cancelled."""

    def __init__(self, plan: AvaTurnPlan) -> None:
        super().__init__(plan, child_text=None)
        self.stream_started = asyncio.Event()

    def turn_events(
        self,
        *,
        harness: object,
        turn_id: str,
    ) -> AsyncIterator[RealtimeProviderTurnEvent]:
        del harness, turn_id
        return self._wait_for_cancellation()

    async def _wait_for_cancellation(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        self.stream_started.set()
        await asyncio.Event().wait()
        if False:  # pragma: no cover - preserves the AsyncIterator contract
            yield RealtimeSpeechTextDelta(delta="unreachable")


async def test_cancel_while_listening_interrupts_silent_provider_immediately() -> None:
    plan = _plan("ava_turn_plan_conversation_v3.json")
    realtime = _ListeningRealtimeProvider(plan)
    speech = DeterministicStreamingSpeechSynthesizer()
    specialist = DeterministicCanvasSpecialist(
        model_canvas_draft_from_runtime_plan(_plan())
    )
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id="session-coordinator",
        turn_id="turn-coordinator",
        request_id="request-coordinator",
        stream_id="stream-coordinator",
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=realtime,
        speech=speech,
        canvas_specialist=specialist,
        memory=memory,
        memory_id_factory=lambda turn_id: f"memory:{turn_id}",
    )
    await _ready(coordinator)

    async def collect_events() -> list[object]:
        return [
            frame.event
            async for frame in coordinator.frames()
            if isinstance(frame, AvaServerControlFrame)
        ]

    collector = asyncio.create_task(collect_events())
    await asyncio.wait_for(realtime.stream_started.wait(), timeout=1)
    await coordinator.receive(
        _client(
            {
                "event": "cancelTurn",
                "sequence": 1,
                "turnId": "turn-coordinator",
                "requestId": "cancel-listening",
                "reason": "userStopped",
            }
        )
    )
    events = await asyncio.wait_for(collector, timeout=1)

    assert isinstance(events[-1], TurnCancelledEvent)
    assert events[-1].delivered_sample_count == 0
    assert events[-1].delivered_text == ""
    assert realtime.cancelled_turn_ids == ["turn-coordinator"]
    assert speech.cancelled_turn_ids == ["turn-coordinator"]
    assert specialist.cancelled_turn_ids == ["turn-coordinator"]


async def test_canvas_selection_enters_next_turn_and_revises_same_session() -> None:
    plan = _plan()
    canvas_state = CanvasSessionState()
    first_payload = model_canvas_draft_from_runtime_plan(plan).model_dump(
        mode="json",
        by_alias=True,
    )
    first_payload["interactions"][0]["action"] = "select"
    first_draft = ModelCanvasDraft.model_validate(first_payload)
    realtime = DeterministicRealtimePlanProvider(
        plan,
        child_text="Make the nucleus explanation clearer.",
        canvas_brief="Draw a labeled plant cell with a tappable nucleus label.",
        chunk_sizes=(3, 11, 1, 29, 7),
    )
    speech = DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31)
    specialist = DeterministicCanvasSpecialist(first_draft)
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id="session-coordinator",
        turn_id="turn-coordinator",
        request_id="request-coordinator",
        stream_id="stream-coordinator",
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=realtime,
        speech=speech,
        canvas_specialist=specialist,
        memory=memory,
        memory_id_factory=lambda turn_id: f"memory:{turn_id}",
        canvas_state=canvas_state,
        persistent=True,
        turn_id_factory=lambda: "turn-coordinator-two",
        request_id_factory=lambda: "request-coordinator-two",
        stream_id_factory=lambda: "stream-coordinator-two",
    )
    await _ready(coordinator)

    client_sequence = 1
    terminal_count = 0
    transactions = []
    frame_stream = coordinator.frames()
    try:
        async for frame in frame_stream:
            if not isinstance(frame, AvaServerControlFrame):
                continue
            event = frame.event
            if (
                isinstance(event, TurnPhaseChangedEvent)
                and event.phase == AvaTurnPhase.SPEAKING
            ):
                await coordinator.receive(
                    _client(
                        {
                            "event": "playbackStarted",
                            "sequence": client_sequence,
                            "turnId": event.turn_id,
                            "streamId": (
                                "stream-coordinator"
                                if terminal_count == 0
                                else "stream-coordinator-two"
                            ),
                            "renderedSampleCount": 0,
                        }
                    )
                )
                client_sequence += 1
            if isinstance(event, CanvasCueEvent):
                transactions.append(event.transaction)
                if terminal_count == 0:
                    await coordinator.receive(
                        _client(
                            {
                                "event": "canvasInteraction",
                                "sequence": client_sequence,
                                "sceneId": "plant_cell_scene",
                                "sceneRevision": 1,
                                "interactionRevision": 1,
                                "bindingId": "toggle_nucleus_binding",
                            }
                        )
                    )
                    client_sequence += 1
                await coordinator.receive(
                    _client(
                        {
                            "event": "playbackProgress",
                            "sequence": client_sequence,
                            "turnId": event.turn_id,
                            "streamId": (
                                "stream-coordinator"
                                if terminal_count == 0
                                else "stream-coordinator-two"
                            ),
                            "renderedSampleCount": coordinator.received_sample_count,
                            "bufferedSampleCount": 0,
                        }
                    )
                )
                client_sequence += 1
            if isinstance(event, TurnCompletedEvent):
                terminal_count += 1
                if terminal_count == 2:
                    break
    finally:
        await frame_stream.aclose()
        await coordinator.close()

    assert canvas_state.last_interaction is not None
    assert canvas_state.last_interaction.target_label == "Nucleus label"
    assert len(transactions) == 2
    assert transactions[1].base_revision == 1
    assert transactions[1].revision == 2
    assert specialist.requests[1].current_revision == 1
    assert specialist.requests[1].current_scene == first_draft
    assert (
        "<last_semantic_interaction"
        in realtime.requests[1].session_update["session"]["instructions"]
    )


async def test_stale_canvas_interaction_is_rejected_without_failing_turn() -> None:
    plan = _plan()
    coordinator, _, _, _, _ = _coordinator(
        plan,
        child_text="Show me the plant cell.",
    )
    await _ready(coordinator)

    events = []
    async for frame in coordinator.frames():
        if not isinstance(frame, AvaServerControlFrame):
            continue
        event = frame.event
        events.append(event)
        if (
            isinstance(event, TurnPhaseChangedEvent)
            and event.phase == AvaTurnPhase.SPEAKING
        ):
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": 1,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": 0,
                    }
                )
            )
        if isinstance(event, CanvasCueEvent):
            await coordinator.receive(
                _client(
                    {
                        "event": "canvasInteraction",
                        "sequence": 2,
                        "sceneId": "plant_cell_scene",
                        "sceneRevision": 999,
                        "interactionRevision": 1,
                        "bindingId": "toggle_nucleus_binding",
                    }
                )
            )
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackProgress",
                        "sequence": 3,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": coordinator.received_sample_count,
                        "bufferedSampleCount": 0,
                    }
                )
            )

    rejection = next(
        event for event in events if isinstance(event, InteractionRejectedEvent)
    )
    assert rejection.rejection_code == "sceneRevisionMismatch"
    assert rejection.scene_id == "plant_cell_scene"
    assert isinstance(events[-1], TurnCompletedEvent)
    assert coordinator.last_error is None


async def test_missing_provider_transcript_does_not_invent_authoritative_memory() -> (
    None
):
    plan = _plan("ava_turn_plan_conversation_v3.json")
    coordinator, _, _, specialist, memory = _coordinator(plan, child_text=None)
    await _ready(coordinator)

    async for frame in coordinator.frames():
        if not isinstance(frame, AvaServerControlFrame):
            continue
        if (
            isinstance(frame.event, TurnPhaseChangedEvent)
            and frame.event.phase == AvaTurnPhase.SPEAKING
        ):
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": 1,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": 0,
                    }
                )
            )
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackProgress",
                        "sequence": 2,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": coordinator.received_sample_count,
                        "bufferedSampleCount": 0,
                    }
                )
            )

    snapshot = await memory.load_snapshot(
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert snapshot.delivered_turns == []
    assert snapshot.stable_memories == []
    assert specialist.requests == []


async def test_invalid_optional_canvas_is_reported_while_speech_completes() -> None:
    plan = _plan()
    coordinator, _, speech, specialist, memory = _coordinator(
        plan,
        child_text="Please explain a plant cell.",
        specialist_error=RuntimeError("synthetic specialist failure"),
    )
    await _ready(coordinator)

    control_events = []
    async for frame in coordinator.frames():
        if not isinstance(frame, AvaServerControlFrame):
            continue
        control_events.append(frame.event)
        if (
            isinstance(frame.event, TurnPhaseChangedEvent)
            and frame.event.phase == AvaTurnPhase.SPEAKING
        ):
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": 1,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": 0,
                    }
                )
            )
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackProgress",
                        "sequence": 2,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": coordinator.received_sample_count,
                        "bufferedSampleCount": 0,
                    }
                )
            )

    event_names = [event.event for event in control_events]
    assert "canvasRejected" in event_names
    assert "canvasCue" not in event_names
    rejection = next(
        event for event in control_events if event.event == "canvasRejected"
    )
    assert isinstance(rejection, CanvasRejectedEvent)
    assert rejection.failure.failure_id.startswith("failure-")
    assert rejection.failure.component == AvaFailureComponent.CANVAS_SPECIALIST
    assert isinstance(control_events[-1], TurnCompletedEvent)
    assert speech.synthesized_segment_ids == ["speech_1", "speech_2"]
    assert len(specialist.requests) == 1
    snapshot = await memory.load_snapshot(
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert snapshot.delivered_turns[0].ava_delivered_text == plan.speech


async def test_invalid_canvas_tool_arguments_do_not_fail_the_spoken_turn() -> None:
    plan = _plan()
    coordinator, realtime, speech, specialist, _ = _coordinator(
        plan,
        child_text="Please explain a plant cell.",
        rejected_canvas_reason="The request exceeded the bounded contract.",
    )
    await _ready(coordinator)

    control_events = []
    async for frame in coordinator.frames():
        if not isinstance(frame, AvaServerControlFrame):
            continue
        control_events.append(frame.event)
        if (
            isinstance(frame.event, TurnPhaseChangedEvent)
            and frame.event.phase == AvaTurnPhase.SPEAKING
        ):
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": 1,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": 0,
                    }
                )
            )
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackProgress",
                        "sequence": 2,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": coordinator.received_sample_count,
                        "bufferedSampleCount": 0,
                    }
                )
            )

    assert any(isinstance(event, CanvasRejectedEvent) for event in control_events)
    assert isinstance(control_events[-1], TurnCompletedEvent)
    assert specialist.requests == []
    assert speech.synthesized_segment_ids == ["speech_1", "speech_2"]
    assert len(realtime.completed_canvas_outputs) == 1
    assert realtime.completed_canvas_outputs[0].status == "rejected"


async def test_interruption_commits_only_alignment_derived_delivered_prefix() -> None:
    plan = _plan("ava_turn_plan_conversation_v3.json")
    coordinator, realtime, speech, _, memory = _coordinator(
        plan,
        child_text="I have a new story idea.",
    )
    await _ready(coordinator)

    first_word_sample_end: int | None = None
    first_word_text_end: int | None = None
    terminal: TurnCancelledEvent | None = None
    async for frame in coordinator.frames():
        if not isinstance(frame, AvaServerControlFrame):
            continue
        if (
            isinstance(frame.event, AlignmentBatchEvent)
            and first_word_sample_end is None
        ):
            first_word_sample_end = frame.event.spans[0].sample_end
            first_word_text_end = frame.event.spans[0].text_end
        if (
            isinstance(frame.event, TurnPhaseChangedEvent)
            and frame.event.phase == AvaTurnPhase.SPEAKING
        ):
            assert first_word_sample_end is not None
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": 1,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": 0,
                    }
                )
            )
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackProgress",
                        "sequence": 2,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": first_word_sample_end,
                        "bufferedSampleCount": (
                            coordinator.received_sample_count - first_word_sample_end
                        ),
                    }
                )
            )
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStopped",
                        "sequence": 3,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": first_word_sample_end,
                        "reason": "childStartedSpeaking",
                    }
                )
            )
            await coordinator.receive(
                _client(
                    {
                        "event": "cancelTurn",
                        "sequence": 4,
                        "turnId": "turn-coordinator",
                        "requestId": "cancel-coordinator",
                        "reason": "childStartedSpeaking",
                    }
                )
            )
        if isinstance(frame.event, TurnCancelledEvent):
            terminal = frame.event

    assert terminal is not None
    assert first_word_text_end is not None
    expected_prefix = plan.speech[:first_word_text_end]
    assert terminal.delivered_text == expected_prefix
    assert plan.speech != expected_prefix
    assert realtime.cancelled_turn_ids == ["turn-coordinator"]
    assert speech.cancelled_turn_ids == ["turn-coordinator"]

    snapshot = await memory.load_snapshot(
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert snapshot.delivered_turns[0].ava_delivered_text == expected_prefix
    assert snapshot.delivered_turns[0].terminal == "cancelled"


class _MalformedRealtimeProvider:
    def __init__(self) -> None:
        self.cancelled_turn_ids: list[str] = []
        self.closed = False

    async def negotiate(self, *, offer_sdp: str) -> NegotiatedRealtimeCall:
        assert offer_sdp == DETERMINISTIC_AUDIO_OFFER_SDP
        return NegotiatedRealtimeCall(
            answer_sdp=(
                "v=0\r\n"
                "o=- 2000 2 IN IP4 127.0.0.1\r\n"
                "s=Malformed provider answer\r\n"
                "t=0 0\r\n"
                "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
                "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
            ),
            provider_call_id="rtc_malformed_provider",
        )

    async def prepare(self, *, harness: object) -> None:
        del harness

    async def turn_events(
        self,
        *,
        harness: object,
        turn_id: str,
    ) -> AsyncIterator[RealtimeProviderTurnEvent]:
        del harness, turn_id
        yield RealtimeSpeechTextDelta(delta="Invalid\u0000speech")

    async def complete_canvas_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: InteractiveCanvasToolOutput,
    ) -> None:
        del turn_id, call_id, output
        raise AssertionError("malformed speech cannot request a canvas")

    async def child_transcript(self, *, turn_id: str) -> str:
        assert turn_id == "turn-coordinator"
        return "Hello Ava."

    async def cancel(self, *, turn_id: str) -> None:
        self.cancelled_turn_ids.append(turn_id)

    async def close(self) -> None:
        self.closed = True


async def test_malformed_realtime_speech_fails_without_tts_or_memory() -> None:
    plan = _plan("ava_turn_plan_conversation_v3.json")
    _, _, speech, specialist, memory = _coordinator(
        plan,
        child_text="Hello Ava.",
    )
    malformed = _MalformedRealtimeProvider()
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id="session-coordinator",
        turn_id="turn-coordinator",
        request_id="request-coordinator",
        stream_id="stream-coordinator",
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=malformed,
        speech=speech,
        canvas_specialist=specialist,
        memory=memory,
        memory_id_factory=lambda turn_id: f"memory:{turn_id}",
    )
    await _ready(coordinator)

    events = [
        frame.event
        async for frame in coordinator.frames()
        if isinstance(frame, AvaServerControlFrame)
    ]

    assert isinstance(events[-1], TurnFailedEvent)
    assert events[-1].failure.code == AvaFailureCode.MODEL_INVALID
    assert events[-1].failure.failure_id.startswith("failure-")
    assert events[-1].failure.component == AvaFailureComponent.REALTIME_PROVIDER
    assert events[-1].delivered_sample_count == 0
    assert coordinator.last_error is not None
    assert speech.synthesized_segment_ids == []
    snapshot = await memory.load_snapshot(
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert snapshot.delivered_turns == []


async def test_persistent_provider_failure_is_not_rearmed() -> None:
    plan = _plan("ava_turn_plan_conversation_v3.json")
    _, _, speech, specialist, memory = _coordinator(
        plan,
        child_text="Hello Ava.",
    )
    malformed = _MalformedRealtimeProvider()

    def unexpected_rearm() -> str:
        raise AssertionError("a failed provider session must not allocate another turn")

    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id="session-coordinator",
        turn_id="turn-coordinator",
        request_id="request-coordinator",
        stream_id="stream-coordinator",
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=malformed,
        speech=speech,
        canvas_specialist=specialist,
        memory=memory,
        memory_id_factory=lambda turn_id: f"memory:{turn_id}",
        persistent=True,
        turn_id_factory=unexpected_rearm,
        request_id_factory=unexpected_rearm,
        stream_id_factory=unexpected_rearm,
    )
    await _ready(coordinator)

    async def collect_events() -> list[object]:
        return [
            frame.event
            async for frame in coordinator.frames()
            if isinstance(frame, AvaServerControlFrame)
        ]

    events = await asyncio.wait_for(
        collect_events(),
        timeout=1,
    )

    failures = [event for event in events if isinstance(event, TurnFailedEvent)]
    assert len(failures) == 1
    assert failures[0].failure.component == AvaFailureComponent.REALTIME_PROVIDER
    assert malformed.cancelled_turn_ids == ["turn-coordinator"]
    await coordinator.close()


class _FailingCommitMemoryStore(DevelopmentAuthoritativeMemoryStore):
    async def commit_delivered_turn(self, **kwargs: object):  # type: ignore[no-untyped-def]
        del kwargs
        raise RuntimeError("database unavailable")


class _FailingRefreshMemoryStore(DevelopmentAuthoritativeMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.load_count = 0

    async def load_snapshot(self, **kwargs: object):  # type: ignore[no-untyped-def]
        self.load_count += 1
        if self.load_count == 2:
            raise RuntimeError("memory refresh unavailable")
        return await super().load_snapshot(**kwargs)  # type: ignore[arg-type]


async def test_persistent_turn_does_not_reload_memory_into_provider_history() -> None:
    plan = _plan("ava_turn_plan_conversation_v3.json")
    memory = _FailingRefreshMemoryStore()
    realtime = DeterministicRealtimePlanProvider(
        plan,
        child_text="Tell me something gentle.",
        chunk_sizes=(3, 11, 7),
    )
    speech = DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31)
    specialist = DeterministicCanvasSpecialist(
        model_canvas_draft_from_runtime_plan(_plan())
    )
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id="session-coordinator",
        turn_id="turn-refresh-1",
        request_id="request-refresh-1",
        stream_id="stream-refresh-1",
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=realtime,
        speech=speech,
        canvas_specialist=specialist,
        memory=memory,
        memory_id_factory=lambda turn_id: f"memory:{turn_id}",
        persistent=True,
        turn_id_factory=lambda: "turn-refresh-2",
        request_id_factory=lambda: "request-refresh-2",
        stream_id_factory=lambda: "stream-refresh-2",
    )
    await _ready(coordinator)

    completed: list[str] = []
    warnings: list[MemoryPersistenceFailedEvent] = []
    client_sequence = 1
    active_stream_id: str | None = None
    frame_stream = coordinator.frames()
    async for frame in frame_stream:
        if not isinstance(frame, AvaServerControlFrame):
            continue
        event = frame.event
        if isinstance(event, MemoryPersistenceFailedEvent):
            warnings.append(event)
        if isinstance(event, AudioMetadataEvent):
            active_stream_id = event.stream_id
        if (
            isinstance(event, TurnPhaseChangedEvent)
            and event.phase == AvaTurnPhase.SPEAKING
        ):
            assert active_stream_id is not None
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": client_sequence,
                        "turnId": event.turn_id,
                        "streamId": active_stream_id,
                        "renderedSampleCount": 0,
                    }
                )
            )
            client_sequence += 1
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackProgress",
                        "sequence": client_sequence,
                        "turnId": event.turn_id,
                        "streamId": active_stream_id,
                        "renderedSampleCount": coordinator.received_sample_count,
                        "bufferedSampleCount": 0,
                    }
                )
            )
            client_sequence += 1
        if isinstance(event, TurnCompletedEvent):
            completed.append(event.turn_id)
            if len(completed) == 2:
                break
    await frame_stream.aclose()

    assert completed == ["turn-refresh-1", "turn-refresh-2"]
    assert warnings == []
    assert memory.load_count == 1
    assert len(realtime.negotiated_offers) == 1
    await coordinator.close()


async def test_persistence_failure_warns_then_completes_with_delivered_text() -> None:
    plan = _plan("ava_turn_plan_conversation_v3.json")
    memory = _FailingCommitMemoryStore()
    coordinator, _, _, _, _ = _coordinator(
        plan,
        child_text="Tell me something kind.",
        memory=memory,
    )
    await _ready(coordinator)

    warning: MemoryPersistenceFailedEvent | None = None
    terminal: TurnCompletedEvent | None = None
    async for frame in coordinator.frames():
        if not isinstance(frame, AvaServerControlFrame):
            continue
        if (
            isinstance(frame.event, TurnPhaseChangedEvent)
            and frame.event.phase == AvaTurnPhase.SPEAKING
        ):
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": 1,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": 0,
                    }
                )
            )
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackProgress",
                        "sequence": 2,
                        "turnId": "turn-coordinator",
                        "streamId": "stream-coordinator",
                        "renderedSampleCount": coordinator.received_sample_count,
                        "bufferedSampleCount": 0,
                    }
                )
            )
        if isinstance(frame.event, MemoryPersistenceFailedEvent):
            warning = frame.event
        if isinstance(frame.event, TurnCompletedEvent):
            terminal = frame.event

    assert warning is not None
    assert warning.failure.code == AvaFailureCode.MEMORY_UNAVAILABLE
    assert warning.failure.failure_id.startswith("failure-")
    assert warning.failure.component == AvaFailureComponent.MEMORY_STORE
    assert warning.failure.retryable is True
    assert terminal is not None
    assert terminal.delivered_text == plan.speech
    assert terminal.delivered_sample_count > 0
    assert isinstance(coordinator.last_error, RuntimeError)


async def test_client_ready_and_sequence_contract_fails_loudly() -> None:
    coordinator, _, _, _, _ = _coordinator(
        _plan("ava_turn_plan_conversation_v3.json"),
        child_text="Hello Ava.",
    )

    with pytest.raises(CoordinatorStateError, match="clientReady"):
        _ = [frame async for frame in coordinator.frames()]
    with pytest.raises(CoordinatorStateError, match="expected client sequence"):
        await coordinator.receive(
            _client(
                {
                    "event": "clientReady",
                    "sequence": 1,
                }
            )
        )

    await _ready(coordinator)
    with pytest.raises(CoordinatorStateError, match="only once"):
        await coordinator.receive(
            _client(
                {
                    "event": "clientReady",
                    "sequence": 1,
                }
            )
        )
