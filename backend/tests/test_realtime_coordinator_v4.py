from __future__ import annotations

from pathlib import Path

from ava_backend.canvas.svg_delivery import SvgCanvasDocumentStore
from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_session import SvgCanvasSessionState
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
    ) -> SvgCanvasSpecialistGeneration:
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
    async for frame in coordinator.frames():
        frames.append(frame)
        if not isinstance(frame, AvaServerControlFrameV4):
            continue
        event = frame.event
        controls.append(event)
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

    assert activated
    assert [event.sequence for event in controls] == list(range(len(controls)))
    assert controls[-1].event == AvaServerEventTypeV4.TURN_COMPLETED
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
    assert len(realtime.prepared_session_updates) == 2

    for index, frame in enumerate(frames):
        if isinstance(frame, AvaServerControlFrameV4) and isinstance(
            frame.event, AudioChunkEventV4
        ):
            binary = frames[index + 1]
            assert isinstance(binary, AvaServerPcmFrameV4)
            assert binary.descriptor == frame.event

    await coordinator.close()
    assert documents.document_count == 0
