from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ava_backend.canvas.svg_delivery import SvgCanvasDocumentStore
from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_session import SvgCanvasSessionState
from ava_backend.canvas.svg_specialist import (
    SvgCanvasSpecialistGeneration,
    SvgCanvasSpecialistRequest,
)
from ava_backend.canvas.svg_workspace import ActiveSvgCanvasScene
from ava_backend.protocol import AgeBand
from ava_backend.realtime.context_assembler import (
    DynamicContextAssembler,
    DynamicContextBuildReason,
)
from ava_backend.realtime.coordinator import DeterministicRealtimeSessionCoordinator
from ava_backend.realtime.coordinator_v4 import (
    CanvasEventBackpressureError,
    ProtocolV4CanvasEventSink,
)
from ava_backend.realtime.events_v4 import (
    AVA_CLIENT_EVENT_ADAPTER_V4,
    AudioChunkEventV4,
    AvaClientEventUnionV4,
    AvaServerEventTypeV4,
    CanvasActivatedEventV4,
    CanvasDocumentAvailableEventV4,
    CanvasPresentationCueEventV4,
    TurnPhaseChangedEventV4,
)
from ava_backend.realtime.fakes import (
    DETERMINISTIC_AUDIO_OFFER_SDP,
    DeterministicRealtimePlanProvider,
    DeterministicStreamingSpeechSynthesizer,
)
from ava_backend.realtime.harness import build_realtime_harness
from ava_backend.realtime.memory import DevelopmentAuthoritativeMemoryStore
from ava_backend.realtime.plan import AvaTurnPlan
from ava_backend.realtime.svg_canvas_jobs import SvgCanvasJobCoordinator
from ava_backend.realtime.tool_contract import InteractiveCanvasToolOutputV4
from ava_backend.realtime.websocket_v4 import (
    AvaServerControlFrameV4,
    AvaServerPcmFrameV4,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"


class _SvgSpecialist:
    def __init__(self) -> None:
        self.requests: list[SvgCanvasSpecialistRequest] = []
        self.cancelled: list[str] = []
        self.closed = False

    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
        *,
        draft_sink: object | None = None,
    ) -> SvgCanvasSpecialistGeneration:
        del draft_sink
        self.requests.append(request)
        document = SvgDocumentValidator().validate(
            (FIXTURE_ROOT / "ava_svg_document_v4.svg").read_text(encoding="utf-8")
        )
        return SvgCanvasSpecialistGeneration(
            generation_id=request.generation_id,
            document=document,
        )

    async def cancel(self, *, generation_id: str) -> None:
        self.cancelled.append(generation_id)

    async def close(self) -> None:
        self.closed = True


def _client(
    sequence: int,
    payload: dict[str, object],
) -> AvaClientEventUnionV4:
    return AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
        {
            "protocolVersion": 4,
            "sessionId": "session-v4-adapter",
            "sequence": sequence,
            **payload,
        }
    )


async def test_canvas_event_backpressure_fails_instead_of_blocking_lifecycle() -> None:
    canvas_events = ProtocolV4CanvasEventSink(maximum_events=8)
    for index in range(8):
        await canvas_events.generation_started(
            generation_id=f"generation-{index}",
            source_turn_id=f"turn-{index}",
        )

    with pytest.raises(CanvasEventBackpressureError, match="capacity was exhausted"):
        await canvas_events.generation_started(
            generation_id="generation-overflow",
            source_turn_id="turn-overflow",
        )


async def test_fake_provider_settled_turn_terminates_deferred_readiness_wait() -> None:
    plan = AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / "ava_turn_plan_conversation_v3.json").read_text(
            encoding="utf-8"
        )
    )
    realtime = DeterministicRealtimePlanProvider(plan)

    events = [
        event
        async for event in realtime.turn_events(
            harness=build_realtime_harness(
                dynamic_context=DynamicContextAssembler().assemble(
                    build_id="ctx-1",
                    reason=DynamicContextBuildReason.SESSION_BOOTSTRAP,
                    age_band=AgeBand.MIDDLE_CHILDHOOD,
                    timezone_id="UTC",
                    current_time_utc=datetime(2026, 9, 1, tzinfo=UTC),
                )
            ),
            turn_id="turn-settled-fake",
        )
    ]

    assert events
    assert not await asyncio.wait_for(
        realtime.wait_until_canvas_interaction_response_ready(
            turn_id="turn-settled-fake"
        ),
        timeout=0.5,
    )


async def test_restored_focused_canvas_is_in_initial_provider_context() -> None:
    plan = AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / "ava_turn_plan_conversation_v3.json").read_text(
            encoding="utf-8"
        )
    )
    realtime = DeterministicRealtimePlanProvider(plan)
    state = SvgCanvasSessionState()
    documents = SvgCanvasDocumentStore()
    canvas_events = ProtocolV4CanvasEventSink()
    jobs = SvgCanvasJobCoordinator(
        session_id="session-restored-context",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=_SvgSpecialist(),
        state=state,
        documents=documents,
        event_sink=canvas_events,
        generation_id_factory=lambda: "generation-unused",
    )
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id="session-restored-context",
        turn_id="turn-restored-context",
        request_id="request-restored-context",
        stream_id="stream-restored-context",
        subject="child:restored-context",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=realtime,
        speech=DeterministicStreamingSpeechSynthesizer(),
        canvas_jobs=jobs,
        canvas_events=canvas_events,
        memory=DevelopmentAuthoritativeMemoryStore(),
    )
    document = SvgDocumentValidator().validate(
        (FIXTURE_ROOT / "ava_svg_document_v4.svg").read_text(encoding="utf-8")
    )
    restored = ActiveSvgCanvasScene(
        generation_id="generation-restored-context",
        source_turn_id="turn-original-context",
        scene_id="scene-restored-context",
        revision=4,
        document=document,
        first_rendered_sample=0,
    )
    try:
        coordinator.restore_focused_canvas(restored)
        await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)

        assert state.active is restored
        assert len(realtime.prepared_session_updates) == 1
        instructions = realtime.prepared_session_updates[0]["session"]["instructions"]
        assert isinstance(instructions, str)
        assert state.render_realtime_context() in instructions
    finally:
        await coordinator.close()


async def test_v4_coordinator_runs_exact_svg_lifecycle_without_pcm_interleaving() -> (
    None
):
    plan = AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / "ava_turn_plan_v3.json").read_text(encoding="utf-8")
    )
    realtime = DeterministicRealtimePlanProvider(
        plan,
        child_text="How does a plant cell work?",
        canvas_brief="Draw a labeled plant cell with a tappable nucleus label.",
    )
    speech = DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31)
    svg_specialist = _SvgSpecialist()
    documents = SvgCanvasDocumentStore()
    state = SvgCanvasSessionState()
    canvas_events = ProtocolV4CanvasEventSink()
    jobs = SvgCanvasJobCoordinator(
        session_id="session-v4-adapter",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=svg_specialist,
        state=state,
        documents=documents,
        event_sink=canvas_events,
        generation_id_factory=lambda: "generation-v4-adapter",
    )

    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id="session-v4-adapter",
        turn_id="turn-v4-adapter",
        request_id="request-v4-adapter",
        stream_id="stream-v4-adapter",
        subject="child:v4-adapter",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=realtime,
        speech=speech,
        canvas_jobs=jobs,
        canvas_events=canvas_events,
        memory=DevelopmentAuthoritativeMemoryStore(),
        persistent=True,
        turn_id_factory=lambda: "turn-v4-adapter-2",
        request_id_factory=lambda: "request-v4-adapter-2",
        stream_id_factory=lambda: "stream-v4-adapter-2",
    )
    await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coordinator.receive(
        _client(
            0,
            {
                "event": "clientReady",
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            },
        )
    )

    frames = []
    controls = []
    next_client_sequence = 1
    speaking = False
    activated = False
    first_turn_completed = False
    rearmed = False
    frame_stream = coordinator.frames()
    try:
        async for frame in frame_stream:
            frames.append(frame)
            if not isinstance(frame, AvaServerControlFrameV4):
                continue
            event = frame.event
            controls.append(event)
            if (
                event.event == AvaServerEventTypeV4.TURN_CREATED
                and event.turn_id == "turn-v4-adapter-2"
            ):
                rearmed = True
            if isinstance(event, TurnPhaseChangedEventV4) and event.phase == "speaking":
                speaking = True
            if isinstance(event, CanvasDocumentAvailableEventV4):
                await coordinator.receive(
                    _client(
                        next_client_sequence,
                        {
                            "event": "canvasDocumentPrepared",
                            "generationId": event.generation_id,
                            "sceneId": event.scene_id,
                            "sceneRevision": event.scene_revision,
                            "svgSha256": event.svg_sha256,
                            "manifestSha256": event.manifest_sha256,
                            "rendererCapabilityVersion": (
                                event.renderer_capability_version
                            ),
                            "validatorVersion": event.validator_version,
                        },
                    )
                )
                next_client_sequence += 1
            if isinstance(event, CanvasPresentationCueEventV4):
                assert speaking
                await coordinator.receive(
                    _client(
                        next_client_sequence,
                        {
                            "event": "canvasPresented",
                            "generationId": event.generation_id,
                            "sceneId": event.scene_id,
                            "sceneRevision": event.scene_revision,
                            "svgSha256": event.svg_sha256,
                            "manifestSha256": event.manifest_sha256,
                            "firstRenderedSample": event.presentation_sample_offset,
                        },
                    )
                )
                next_client_sequence += 1
            if isinstance(event, CanvasActivatedEventV4):
                activated = True
                await coordinator.receive(
                    _client(
                        next_client_sequence,
                        {
                            "event": "playbackStarted",
                            "turnId": "turn-v4-adapter",
                            "streamId": "stream-v4-adapter",
                            "renderedSampleCount": 0,
                        },
                    )
                )
                next_client_sequence += 1
                await coordinator.receive(
                    _client(
                        next_client_sequence,
                        {
                            "event": "playbackProgress",
                            "turnId": "turn-v4-adapter",
                            "streamId": "stream-v4-adapter",
                            "renderedSampleCount": coordinator.received_sample_count,
                            "bufferedSampleCount": 0,
                        },
                    )
                )
                next_client_sequence += 1
            if (
                event.event == AvaServerEventTypeV4.TURN_COMPLETED
                and event.turn_id == "turn-v4-adapter"
            ):
                first_turn_completed = True
                assert len(realtime.prepared_session_updates) == 1
                assert coordinator._pending_harness is not None
            if (
                rearmed
                and len(realtime.prepared_session_updates) == 2
                and coordinator._context_installation_task is None
            ):
                break

        assert first_turn_completed
        assert rearmed
        assert activated
        assert len(realtime.prepared_session_updates) == 2
        assert coordinator._pending_harness is None
        assert coordinator._context_installation_task is None

        final_instructions = realtime.prepared_session_updates[-1]["session"][
            "instructions"
        ]
        assert isinstance(final_instructions, str)
        assert state.render_realtime_context() in final_instructions

        first_terminal_index = next(
            index
            for index, event in enumerate(controls)
            if event.event == AvaServerEventTypeV4.TURN_COMPLETED
            and event.turn_id == "turn-v4-adapter"
        )
        first_session_controls = controls[: first_terminal_index + 1]
        assert [event.sequence for event in first_session_controls] == list(
            range(len(first_session_controls))
        )
        assert first_session_controls[-1].event == AvaServerEventTypeV4.TURN_COMPLETED
        assert len(svg_specialist.requests) == 1
        assert isinstance(
            realtime.completed_canvas_outputs[0], InteractiveCanvasToolOutputV4
        )
        assert state.active is not None
        assert state.active.first_rendered_sample >= 0
        assert state.active.document.svg_text == (
            FIXTURE_ROOT / "ava_svg_document_v4.svg"
        ).read_text(encoding="utf-8")
        assert documents.document_count == 0

        for index, frame in enumerate(frames):
            if isinstance(frame, AvaServerControlFrameV4) and isinstance(
                frame.event, AudioChunkEventV4
            ):
                binary = frames[index + 1]
                assert isinstance(binary, AvaServerPcmFrameV4)
                assert binary.descriptor == frame.event
    finally:
        await frame_stream.aclose()
        await coordinator.close()

    assert coordinator._context_installation_task is None
    assert documents.document_count == 0
