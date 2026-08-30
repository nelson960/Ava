from __future__ import annotations

import asyncio
from builtins import anext
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ava_backend.canvas.svg_delivery import SvgCanvasDocumentStore
from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_session import SvgCanvasSessionState
from ava_backend.canvas.svg_workspace import (
    RecordedSvgCanvasInteraction,
    SvgCanvasWorkspace,
)
from ava_backend.canvas.svg_specialist import (
    SvgCanvasSpecialistGeneration,
    SvgCanvasSpecialistRequest,
)
from ava_backend.protocol import AgeBand
from ava_backend.realtime.coordinator import (
    CoordinatorStateError,
    DeterministicRealtimeSessionCoordinator,
)
from ava_backend.realtime.coordinator_v4 import ProtocolV4CanvasEventSink
from ava_backend.realtime.events_v4 import (
    AVA_CLIENT_EVENT_ADAPTER_V4,
    AudioChunkEventV4,
    AudioMetadataEventV4,
    AvaClientEventUnionV4,
    CanvasInteractionEventV4,
    InterruptRequestedEventV4,
    MemoryPersistenceFailedEventV4,
    SegmentReadyEventV4,
    TurnCancelledEventV4,
    TurnCompletedEventV4,
    TurnCreatedEventV4,
    TurnFailedEventV4,
    TurnPhaseChangedEventV4,
)
from ava_backend.realtime.fakes import (
    DETERMINISTIC_AUDIO_ANSWER_SDP,
    DETERMINISTIC_AUDIO_OFFER_SDP,
    DeterministicRealtimePlanProvider,
    DeterministicStreamingSpeechSynthesizer,
)
from ava_backend.realtime.memory import DevelopmentAuthoritativeMemoryStore
from ava_backend.realtime.plan import AvaTurnPlan
from ava_backend.realtime.provider_session import (
    NegotiatedRealtimeCall,
    RealtimeProviderInputEvent,
    RealtimeProviderProtocolError,
    RealtimeProviderResponseIncompleteError,
    RealtimeProviderTurnEvent,
    RealtimeProviderUnavailableError,
)
from ava_backend.realtime.speech_contract import (
    SpeechFailureCode,
    SpeechFailureComponent,
    SpeechPhase,
)
from ava_backend.realtime.svg_canvas_jobs import SvgCanvasJobCoordinator
from ava_backend.realtime.tool_contract import (
    InteractiveCanvasToolOutputV4,
    RealtimeSpeechTextDelta,
)
from ava_backend.realtime.websocket_v4 import (
    AvaServerControlFrameV4,
    AvaServerPcmFrameV4,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"
SESSION_ID = "session-coordinator"
TURN_ID = "turn-coordinator"
STREAM_ID = "stream-coordinator"


def _plan() -> AvaTurnPlan:
    return AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / "ava_turn_plan_conversation_v3.json").read_text(
            encoding="utf-8"
        )
    )


def _speech_plan(segment_count: int) -> AvaTurnPlan:
    payload = _plan().model_dump(mode="json", by_alias=True)
    template = payload["segments"][0]
    payload["segments"] = [
        {
            **template,
            "id": f"speech_{index + 1}",
            "speech": f"Part {index + 1}.",
        }
        for index in range(segment_count)
    ]
    return AvaTurnPlan.model_validate(payload)


class _NoCanvasSpecialist:
    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
        *,
        draft_sink: object | None = None,
    ) -> SvgCanvasSpecialistGeneration:
        del request, draft_sink
        raise AssertionError("speech-only coordinator test requested canvas")

    async def cancel(self, *, generation_id: str) -> None:
        del generation_id

    async def close(self) -> None:
        return


class _BlockingCanvasInteractionJobs:
    def __init__(self, delegate: SvgCanvasJobCoordinator) -> None:
        self._delegate = delegate
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def interaction(self, event: CanvasInteractionEventV4):  # type: ignore[no-untyped-def]
        self.started.set()
        await self.release.wait()
        return await self._delegate.interaction(event)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._delegate, name)


class _CapturingCanvasGroundingJobs:
    def __init__(self, delegate: SvgCanvasJobCoordinator) -> None:
        self._delegate = delegate
        self.accepted: list[tuple[str, str | None, str]] = []

    async def accept_tool_call(
        self,
        *,
        source_turn_id: str,
        source_transcript: str | None,
        call,  # type: ignore[no-untyped-def]
        complete_tool,  # type: ignore[no-untyped-def]
    ):  # type: ignore[no-untyped-def]
        self.accepted.append((source_turn_id, source_transcript, call.request.brief))
        return await self._delegate.accept_tool_call(
            source_turn_id=source_turn_id,
            source_transcript=source_transcript,
            call=call,
            complete_tool=complete_tool,
        )

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._delegate, name)


class _BlockingInteractionContextProvider(DeterministicRealtimePlanProvider):
    def __init__(self, plan: AvaTurnPlan) -> None:
        super().__init__(plan, wait_for_canvas_interaction_response=True)
        self.context_refresh_started = asyncio.Event()
        self.allow_context_refresh = asyncio.Event()
        self.interaction_response_requested = asyncio.Event()

    async def prepare(self, *, harness):  # type: ignore[no-untyped-def]
        if self.prepared_session_updates:
            self.context_refresh_started.set()
            await self.allow_context_refresh.wait()
        await super().prepare(harness=harness)

    async def request_canvas_interaction_response(
        self,
        *,
        turn_id: str,
        interaction: RecordedSvgCanvasInteraction,
    ) -> bool:
        triggered = await super().request_canvas_interaction_response(
            turn_id=turn_id,
            interaction=interaction,
        )
        if triggered:
            self.interaction_response_requested.set()
        return triggered


class _InteractionThenChildRealtimeProvider(DeterministicRealtimePlanProvider):
    """Wait for an interaction response on turn one, then serve child turn two."""

    def __init__(self, plan: AvaTurnPlan) -> None:
        super().__init__(
            plan,
            child_text=None,
            wait_for_canvas_interaction_response=True,
        )
        self.input_observed = asyncio.Event()

    async def next_input_event(self) -> RealtimeProviderInputEvent:
        event = await super().next_input_event()
        self.input_observed.set()
        return event

    def serve_future_child_turns(self) -> None:
        self._wait_for_canvas_interaction_response = False


class _IncompleteInteractionRealtimeProvider(DeterministicRealtimePlanProvider):
    def __init__(self, plan: AvaTurnPlan) -> None:
        super().__init__(
            plan,
            child_text=None,
            wait_for_canvas_interaction_response=True,
        )

    async def _stream(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        self.turn_stream_started.set()
        await self._canvas_interaction_response_requested.wait()
        raise RealtimeProviderResponseIncompleteError(reason="max_output_tokens")
        yield  # pragma: no cover - makes this an async generator


class _IncompleteAfterSpeechRealtimeProvider(DeterministicRealtimePlanProvider):
    def __init__(self, plan: AvaTurnPlan) -> None:
        super().__init__(plan)
        self.allow_incomplete = asyncio.Event()

    async def _stream(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        self.turn_stream_started.set()
        yield RealtimeSpeechTextDelta(delta=f"{self._speech_segments[0]} ")
        await self.allow_incomplete.wait()
        raise RealtimeProviderResponseIncompleteError(reason="max_output_tokens")
        yield  # pragma: no cover - makes this an async generator


def _canvas_runtime(
    *,
    state: SvgCanvasSessionState | None = None,
) -> tuple[SvgCanvasJobCoordinator, ProtocolV4CanvasEventSink]:
    events = ProtocolV4CanvasEventSink()
    jobs = SvgCanvasJobCoordinator(
        session_id=SESSION_ID,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=_NoCanvasSpecialist(),
        state=state or SvgCanvasSessionState(),
        documents=SvgCanvasDocumentStore(),
        event_sink=events,
        generation_id_factory=lambda: "generation-coordinator",
    )
    return jobs, events


def _active_labeled_canvas_state(
    workspace: SvgCanvasWorkspace | None = None,
) -> SvgCanvasSessionState:
    document = SvgDocumentValidator().validate(
        """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600"
 data-ava-interaction-mode="explore">
<title>Water cycle</title><desc>A labeled cycle of water moving through nature.</desc>
<text id="evaporation_label" x="180" y="420">Evaporation</text>
<g id="condensation" data-ava-binding="explore_condensation"
 data-ava-target="condensation" aria-label="Explore condensation">
  <text id="condensation_label" x="500" y="140">Condensation</text>
</g>
<text id="precipitation_label" x="800" y="420">Precipitation</text>
</svg>"""
    )
    generation = SvgCanvasSpecialistGeneration(
        generation_id="generation-active-context",
        document=document,
    )
    state = SvgCanvasSessionState(workspace=workspace)
    state.stage_generation(
        generation,
        source_turn_id="turn-canvas-source",
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=state.workspace.reset_epoch,
    )
    state.mark_document_prepared(
        generation_id=generation.generation_id,
        svg_sha256=document.svg_sha256,
        manifest_sha256=document.manifest_sha256,
        renderer_capability_version=str(document.manifest["rendererCapabilityVersion"]),
        validator_version=str(document.manifest["validatorVersion"]),
    )
    state.schedule_presentation(
        generation_id=generation.generation_id,
        presentation_sample_offset=4_800,
    )
    state.mark_presented(
        generation_id=generation.generation_id,
        svg_sha256=document.svg_sha256,
        manifest_sha256=document.manifest_sha256,
        first_rendered_sample=4_800,
    )
    return state


def _active_multi_binding_canvas_state() -> SvgCanvasSessionState:
    document = SvgDocumentValidator().validate(
        """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600"
 data-ava-interaction-mode="explore">
<title>Plant cell</title><desc>Three labeled plant-cell parts.</desc>
<g id="nucleus" data-ava-binding="explore_nucleus"
 data-ava-target="nucleus" aria-label="Explore nucleus">
  <text x="120" y="180">Nucleus</text>
</g>
<g id="wall" data-ava-binding="explore_wall"
 data-ava-target="wall" aria-label="Explore cell wall">
  <text x="420" y="180">Cell wall</text>
</g>
<g id="chloroplast" data-ava-binding="explore_chloroplast"
 data-ava-target="chloroplast" aria-label="Explore chloroplast">
  <text x="720" y="180">Chloroplast</text>
</g>
</svg>"""
    )
    generation = SvgCanvasSpecialistGeneration(
        generation_id="generation-active-multi-context",
        document=document,
    )
    state = SvgCanvasSessionState()
    state.stage_generation(
        generation,
        source_turn_id="turn-canvas-source",
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=state.workspace.reset_epoch,
    )
    state.mark_document_prepared(
        generation_id=generation.generation_id,
        svg_sha256=document.svg_sha256,
        manifest_sha256=document.manifest_sha256,
        renderer_capability_version=str(document.manifest["rendererCapabilityVersion"]),
        validator_version=str(document.manifest["validatorVersion"]),
    )
    state.schedule_presentation(
        generation_id=generation.generation_id,
        presentation_sample_offset=0,
    )
    state.mark_presented(
        generation_id=generation.generation_id,
        svg_sha256=document.svg_sha256,
        manifest_sha256=document.manifest_sha256,
        first_rendered_sample=0,
    )
    return state


async def test_replacement_session_initial_context_contains_committed_canvas() -> None:
    workspace = SvgCanvasWorkspace()
    first_state = _active_labeled_canvas_state(workspace)
    expected_context = first_state.render_realtime_context()
    replacement_state = SvgCanvasSessionState(workspace=workspace)
    coordinator, realtime, _speech, _memory = _coordinator(
        canvas_state=replacement_state,
    )

    await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)

    assert expected_context
    assert replacement_state.render_realtime_context() == expected_context
    assert expected_context in coordinator.harness.instructions
    assert isinstance(realtime, DeterministicRealtimePlanProvider)
    session = realtime.prepared_session_updates[0]["session"]
    assert isinstance(session, dict)
    instructions = session["instructions"]
    assert isinstance(instructions, str)
    assert expected_context in instructions
    await coordinator.close()


async def test_provider_owned_source_turn_transcript_grounds_exact_canvas_request() -> (
    None
):
    payload = _plan().model_dump(mode="json", by_alias=True)
    payload["canvasDecision"] = {
        "complexity": "specialistRequired",
        "specialistBrief": "visual-brief-subject-B",
    }
    plan = AvaTurnPlan.model_validate(payload)
    provider = DeterministicRealtimePlanProvider(
        plan,
        child_text="source-subject-A",
        canvas_brief="visual-brief-subject-B",
    )
    provider.emit_input_event(
        RealtimeProviderInputEvent(
            event_type="input_audio_buffer.speech_started",
            item_id="item-source-grounding-A",
            audio_start_ms=0,
        ),
        owner_turn_id=TURN_ID,
    )
    coordinator, _, _, _ = _coordinator(plan=plan, realtime=provider)
    capturing_jobs = _CapturingCanvasGroundingJobs(coordinator._canvas_jobs)
    coordinator._canvas_jobs = capturing_jobs  # type: ignore[assignment]
    await _ready(coordinator)
    try:
        events = await _complete_delivered_turn(coordinator)

        assert isinstance(events[-1], TurnCompletedEventV4)
        assert provider.input_item_owner(item_id="item-source-grounding-A") == TURN_ID
        assert capturing_jobs.accepted == [
            (TURN_ID, "source-subject-A", "visual-brief-subject-B")
        ]
    finally:
        await coordinator.close()


def _client(payload: dict[str, object]) -> AvaClientEventUnionV4:
    return AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
        {
            "protocolVersion": 4,
            "sessionId": SESSION_ID,
            **payload,
        }
    )


def _coordinator(
    *,
    plan: AvaTurnPlan | None = None,
    realtime: object | None = None,
    memory: DevelopmentAuthoritativeMemoryStore | None = None,
    persistent: bool = False,
    unexpected_rearm: bool = False,
    occurrence_time_factory: Callable[[], datetime] | None = None,
    canvas_state: SvgCanvasSessionState | None = None,
) -> tuple[
    DeterministicRealtimeSessionCoordinator,
    DeterministicRealtimePlanProvider | object,
    DeterministicStreamingSpeechSynthesizer,
    DevelopmentAuthoritativeMemoryStore,
]:
    resolved_plan = plan or _plan()
    resolved_realtime = realtime or DeterministicRealtimePlanProvider(
        resolved_plan,
        child_text="Tell me something gentle.",
        chunk_sizes=(3, 11, 1, 29, 7),
    )
    resolved_memory = memory or DevelopmentAuthoritativeMemoryStore()
    speech = DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31)
    jobs, events = _canvas_runtime(state=canvas_state)

    def next_id() -> str:
        if unexpected_rearm:
            raise AssertionError("failed provider session must not allocate a new turn")
        return "turn-coordinator-2"

    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id=SESSION_ID,
        turn_id=TURN_ID,
        request_id="request-coordinator",
        stream_id=STREAM_ID,
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=resolved_realtime,  # type: ignore[arg-type]
        speech=speech,
        canvas_jobs=jobs,
        canvas_events=events,
        memory=resolved_memory,
        persistent=persistent,
        turn_id_factory=next_id if persistent else None,
        request_id_factory=(lambda: "request-coordinator-2") if persistent else None,
        stream_id_factory=(lambda: "stream-coordinator-2") if persistent else None,
        occurrence_time_factory=(
            occurrence_time_factory or (lambda: datetime.now(UTC))
        ),
    )
    return coordinator, resolved_realtime, speech, resolved_memory


async def _ready(coordinator: DeterministicRealtimeSessionCoordinator) -> None:
    await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coordinator.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )


async def _advance_ready_callbacks(*, rounds: int = 12) -> None:
    """Advance already-ready tasks without sleeps or wall-clock timeouts."""

    loop = asyncio.get_running_loop()
    for _ in range(rounds):
        checkpoint: asyncio.Future[None] = loop.create_future()
        loop.call_soon(checkpoint.set_result, None)
        await checkpoint


async def test_coordinator_streams_seven_phrases_pcm_and_memory_directly_as_v4() -> (
    None
):
    plan = _speech_plan(7)
    coordinator, realtime, speech, memory = _coordinator(plan=plan)
    await _ready(coordinator)

    frames = []
    controls = []
    async for frame in coordinator.frames():
        frames.append(frame)
        if not isinstance(frame, AvaServerControlFrameV4):
            continue
        controls.append(frame.event)
        if (
            isinstance(frame.event, TurnPhaseChangedEventV4)
            and frame.event.phase == SpeechPhase.SPEAKING
        ):
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": 1,
                        "turnId": TURN_ID,
                        "streamId": STREAM_ID,
                        "renderedSampleCount": 0,
                    }
                )
            )
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackProgress",
                        "sequence": 2,
                        "turnId": TURN_ID,
                        "streamId": STREAM_ID,
                        "renderedSampleCount": coordinator.received_sample_count,
                        "bufferedSampleCount": 0,
                    }
                )
            )

    segments = [event for event in controls if isinstance(event, SegmentReadyEventV4)]
    assert [event.segment.speech for event in segments] == [
        f"Part {index}." for index in range(1, 8)
    ]
    assert speech.synthesized_segment_ids == [
        f"speech_{index}" for index in range(1, 8)
    ]
    assert isinstance(controls[-1], TurnCompletedEventV4)
    assert controls[-1].delivered_text == plan.speech
    assert [event.sequence for event in controls] == list(range(len(controls)))
    for index, frame in enumerate(frames):
        if isinstance(frame, AvaServerControlFrameV4) and isinstance(
            frame.event, AudioChunkEventV4
        ):
            assert isinstance(frames[index + 1], AvaServerPcmFrameV4)
            assert frames[index + 1].descriptor == frame.event
    snapshot = await memory.load_snapshot(
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == [TURN_ID]
    assert isinstance(realtime, DeterministicRealtimePlanProvider)
    await coordinator.close()


class _MalformedRealtimeProvider:
    def __init__(self) -> None:
        self.cancelled_turn_ids: list[str] = []
        self.closed = False

    async def negotiate(self, *, offer_sdp: str) -> NegotiatedRealtimeCall:
        del offer_sdp
        return NegotiatedRealtimeCall(
            answer_sdp=DETERMINISTIC_AUDIO_ANSWER_SDP,
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

    async def next_input_event(self) -> RealtimeProviderInputEvent:
        await asyncio.Event().wait()
        raise AssertionError("unreachable suspended provider input observer")

    async def complete_canvas_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: InteractiveCanvasToolOutputV4,
    ) -> None:
        del turn_id, call_id, output
        raise AssertionError("malformed speech cannot request canvas")

    def input_item_owner(self, *, item_id: str) -> str | None:
        del item_id
        raise AssertionError("malformed provider does not emit input events")

    async def child_transcript(self, *, turn_id: str) -> str:
        del turn_id
        return "Hello Ava."

    async def cancel(self, *, turn_id: str) -> None:
        self.cancelled_turn_ids.append(turn_id)

    async def close(self) -> None:
        self.closed = True


class _FailingRealtimeProvider(_MalformedRealtimeProvider):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    async def turn_events(
        self,
        *,
        harness: object,
        turn_id: str,
    ) -> AsyncIterator[RealtimeProviderTurnEvent]:
        del harness, turn_id
        raise self._error
        if False:  # pragma: no cover - preserves the async-iterator contract
            yield RealtimeSpeechTextDelta(delta="unreachable")


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_retryable"),
    [
        (
            RealtimeProviderProtocolError("provider event was malformed"),
            SpeechFailureCode.MODEL_INVALID,
            False,
        ),
        (
            RealtimeProviderUnavailableError("provider socket closed"),
            SpeechFailureCode.MODEL_UNAVAILABLE,
            True,
        ),
    ],
)
async def test_provider_failure_taxonomy_is_preserved_without_broad_mapping(
    error: Exception,
    expected_code: SpeechFailureCode,
    expected_retryable: bool,
) -> None:
    provider = _FailingRealtimeProvider(error)
    coordinator, _, speech, memory = _coordinator(realtime=provider)
    await _ready(coordinator)

    events = [
        frame.event
        async for frame in coordinator.frames()
        if isinstance(frame, AvaServerControlFrameV4)
    ]

    assert isinstance(events[-1], TurnFailedEventV4)
    assert events[-1].failure.code == expected_code
    assert events[-1].failure.component == SpeechFailureComponent.REALTIME_PROVIDER
    assert events[-1].failure.retryable is expected_retryable
    assert speech.synthesized_segment_ids == []
    snapshot = await memory.load_snapshot(
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert snapshot.delivered_turns == []
    assert provider.cancelled_turn_ids == [TURN_ID]
    await coordinator.close()


async def test_malformed_provider_text_fails_validation_before_tts() -> None:
    provider = _MalformedRealtimeProvider()
    coordinator, _, speech, _ = _coordinator(realtime=provider)
    await _ready(coordinator)

    events = [
        frame.event
        async for frame in coordinator.frames()
        if isinstance(frame, AvaServerControlFrameV4)
    ]

    assert isinstance(events[-1], TurnFailedEventV4)
    assert events[-1].failure.code == SpeechFailureCode.MODEL_INVALID
    assert events[-1].delivered_sample_count == 0
    assert speech.synthesized_segment_ids == []
    await coordinator.close()


class _FailingCommitMemoryStore(DevelopmentAuthoritativeMemoryStore):
    async def commit_delivered_turn(self, **kwargs: object):  # type: ignore[no-untyped-def]
        del kwargs
        raise RuntimeError("database unavailable")


class _ObservedMemoryStore(DevelopmentAuthoritativeMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.committed = asyncio.Event()
        self.committed_turn_ids: list[str] = []
        self.committed_by_turn: dict[str, asyncio.Event] = {}

    async def commit_delivered_turn(self, **kwargs: object):  # type: ignore[no-untyped-def]
        turn = kwargs["turn"]
        result = await super().commit_delivered_turn(**kwargs)  # type: ignore[arg-type]
        self.committed_turn_ids.append(turn.turn_id)  # type: ignore[union-attr]
        self.committed_by_turn.setdefault(  # type: ignore[union-attr]
            turn.turn_id,  # type: ignore[union-attr]
            asyncio.Event(),
        ).set()
        self.committed.set()
        return result

    async def wait_for_turn(self, turn_id: str) -> None:
        event = self.committed_by_turn.setdefault(turn_id, asyncio.Event())
        await event.wait()


class _LateTranscriptRealtimeProvider(DeterministicRealtimePlanProvider):
    def __init__(self, plan: AvaTurnPlan, *, transcript: str) -> None:
        super().__init__(plan, child_text=None)
        self.item_id = "item-late-transcript-a"
        self._input_item_turn_ids[self.item_id] = TURN_ID
        self._late_transcript = transcript
        self._completed_transcript: str | None = None
        self._transcription_terminal = asyncio.Event()
        self.transcript_wait_requests = 0

    def wait_for_child_transcript(
        self,
        *,
        turn_id: str,
    ) -> Awaitable[str | None]:
        assert turn_id == TURN_ID
        assert self.input_item_owner(item_id=self.item_id) == turn_id
        self.transcript_wait_requests += 1

        async def wait_for_terminal() -> str | None:
            await self._transcription_terminal.wait()
            return self._completed_transcript

        return wait_for_terminal()

    def complete_transcription(self) -> None:
        if self._completed_transcript is not None:
            assert self._completed_transcript == self._late_transcript
            return
        self._completed_transcript = self._late_transcript
        self._transcription_terminal.set()


class _CrossTurnTranscriptRealtimeProvider(DeterministicRealtimePlanProvider):
    def __init__(self, plan: AvaTurnPlan) -> None:
        super().__init__(plan, child_text=None)
        self.turn_a = TURN_ID
        self.turn_b = "turn-coordinator-2"
        self.item_a = "item-chronology-a"
        self.item_b = "item-chronology-b"
        self.child_a = "First, tell me about the moon."
        self.child_b = "Then, tell me about the stars."
        self._input_item_turn_ids.update(
            {self.item_a: self.turn_a, self.item_b: self.turn_b}
        )
        self._transcript_a: str | None = None
        self._transcription_a_terminal = asyncio.Event()

    async def child_transcript(self, *, turn_id: str) -> str | None:
        if turn_id == self.turn_a:
            return self._transcript_a
        if turn_id == self.turn_b:
            return self.child_b
        raise AssertionError(f"unexpected turn transcript request: {turn_id}")

    def wait_for_child_transcript(
        self,
        *,
        turn_id: str,
    ) -> Awaitable[str | None]:
        assert turn_id == self.turn_a
        assert self.input_item_owner(item_id=self.item_a) == turn_id

        async def wait_for_terminal() -> str | None:
            await self._transcription_a_terminal.wait()
            return self._transcript_a

        return wait_for_terminal()

    def complete_turn_a_transcription(self) -> None:
        self._transcript_a = self.child_a
        self._transcription_a_terminal.set()


async def _complete_delivered_turn(
    coordinator: DeterministicRealtimeSessionCoordinator,
    *,
    first_client_sequence: int = 1,
) -> list[object]:
    events: list[object] = []
    async for frame in coordinator.frames():
        if not isinstance(frame, AvaServerControlFrameV4):
            continue
        events.append(frame.event)
        if isinstance(frame.event, AudioMetadataEventV4):
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": first_client_sequence,
                        "turnId": TURN_ID,
                        "streamId": STREAM_ID,
                        "renderedSampleCount": 0,
                    }
                )
            )
        if (
            isinstance(frame.event, TurnPhaseChangedEventV4)
            and frame.event.phase == SpeechPhase.SPEAKING
        ):
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackProgress",
                        "sequence": first_client_sequence + 1,
                        "turnId": TURN_ID,
                        "streamId": STREAM_ID,
                        "renderedSampleCount": coordinator.received_sample_count,
                        "bufferedSampleCount": 0,
                    }
                )
            )
    return events


async def _complete_first_persistent_turn_and_rearm(
    coordinator: DeterministicRealtimeSessionCoordinator,
) -> None:
    frames = coordinator.frames()
    try:
        async for frame in frames:
            if not isinstance(frame, AvaServerControlFrameV4):
                continue
            event = frame.event
            if isinstance(event, AudioMetadataEventV4):
                await coordinator.receive(
                    _client(
                        {
                            "event": "playbackStarted",
                            "sequence": 2,
                            "turnId": TURN_ID,
                            "streamId": STREAM_ID,
                            "renderedSampleCount": 0,
                        }
                    )
                )
            if (
                isinstance(event, TurnPhaseChangedEventV4)
                and event.phase == SpeechPhase.SPEAKING
            ):
                await coordinator.receive(
                    _client(
                        {
                            "event": "playbackProgress",
                            "sequence": 3,
                            "turnId": TURN_ID,
                            "streamId": STREAM_ID,
                            "renderedSampleCount": coordinator.received_sample_count,
                            "bufferedSampleCount": 0,
                        }
                    )
                )
            if isinstance(event, TurnCreatedEventV4) and event.turn_id != TURN_ID:
                return
        raise AssertionError("persistent coordinator did not rearm the next turn")
    finally:
        await frames.aclose()


async def test_late_owned_transcription_commits_delivered_turn_exactly_once() -> None:
    plan = _plan()
    provider = _LateTranscriptRealtimeProvider(
        plan,
        transcript="Please tell me something gentle.",
    )
    memory = _ObservedMemoryStore()
    coordinator, _, _, _ = _coordinator(
        plan=plan,
        realtime=provider,
        memory=memory,
    )
    await _ready(coordinator)

    events = await _complete_delivered_turn(coordinator)

    assert isinstance(events[-1], TurnCompletedEventV4)
    assert events[-1].delivered_text == plan.speech
    assert provider.input_item_owner(item_id=provider.item_id) == TURN_ID
    assert provider.transcript_wait_requests == 1
    before_transcription = await memory.load_snapshot(
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert before_transcription.delivered_turns == []

    provider.complete_transcription()
    await memory.committed.wait()
    provider.complete_transcription()

    snapshot = await memory.load_snapshot(
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert memory.committed_turn_ids == [TURN_ID]
    assert [turn.turn_id for turn in snapshot.delivered_turns] == [TURN_ID]
    assert snapshot.delivered_turns[0].child_text == (
        "Please tell me something gentle."
    )
    assert snapshot.delivered_turns[0].ava_delivered_text == plan.speech
    await coordinator.close()


async def test_committed_turn_is_loaded_into_fresh_session_harness() -> None:
    plan = _plan()
    child_text = "Can you remember this gentle question?"
    provider_a = _LateTranscriptRealtimeProvider(plan, transcript=child_text)
    memory = _ObservedMemoryStore()
    coordinator_a, _, _, _ = _coordinator(
        plan=plan,
        realtime=provider_a,
        memory=memory,
    )
    await _ready(coordinator_a)
    await _complete_delivered_turn(coordinator_a)
    assert provider_a.transcript_wait_requests == 1

    provider_a.complete_transcription()
    await memory.committed.wait()
    await coordinator_a.close()

    provider_b = DeterministicRealtimePlanProvider(
        plan,
        child_text="This is a fresh session.",
    )
    coordinator_b, _, _, _ = _coordinator(
        plan=plan,
        realtime=provider_b,
        memory=memory,
    )
    await _ready(coordinator_b)

    context = coordinator_b.harness.context.content
    assert child_text in context
    assert plan.speech in context
    assert coordinator_b.harness.context.included_delivered_turns == 1
    await coordinator_b.close()


async def test_existing_labeled_canvas_part_can_be_discussed_without_new_tool_call() -> (
    None
):
    plan = _plan()
    provider = DeterministicRealtimePlanProvider(
        plan,
        child_text="What does condensation mean in that diagram?",
    )
    coordinator, _, _, _ = _coordinator(
        plan=plan,
        realtime=provider,
        canvas_state=_active_labeled_canvas_state(),
    )
    await _ready(coordinator)

    events = await _complete_delivered_turn(coordinator)

    assert isinstance(events[-1], TurnCompletedEventV4)
    assert "Condensation" in coordinator.harness.instructions
    assert provider.completed_canvas_outputs == []
    assert len(provider.requests) == 1
    await coordinator.close()


async def test_valid_canvas_interaction_refreshes_context_before_one_response() -> None:
    state = _active_labeled_canvas_state()
    active = state.active
    assert active is not None
    provider = DeterministicRealtimePlanProvider(
        _plan(),
        wait_for_canvas_interaction_response=True,
    )
    coordinator, _, _, _ = _coordinator(
        realtime=provider,
        canvas_state=state,
    )
    await _ready(coordinator)
    delivery = asyncio.create_task(
        _complete_delivered_turn(coordinator, first_client_sequence=2)
    )
    await provider.turn_stream_started.wait()

    await coordinator.receive(
        _client(
            {
                "event": "canvasInteraction",
                "sequence": 1,
                "sceneId": active.scene_id,
                "sceneRevision": active.revision,
                "interactionRevision": 1,
                "svgSha256": active.document.svg_sha256,
                "manifestSha256": active.document.manifest_sha256,
                "bindingId": "explore_condensation",
            }
        )
    )
    await delivery

    assert provider.canvas_interaction_response_turn_ids == [TURN_ID]
    assert provider.canvas_interaction_response_interactions == [state.last_interaction]
    assert len(provider.canvas_interaction_response_session_updates) == 1
    session = provider.canvas_interaction_response_session_updates[0]["session"]
    assert isinstance(session, dict)
    instructions = session["instructions"]
    assert isinstance(instructions, str)
    assert '<canvas_context trust="untrusted_model_generated_data"' in instructions
    assert 'interaction_mode="explore"' in instructions
    assert '<last_interaction mode="explore">' in instructions
    assert "<target>Explore condensation</target>" in instructions
    await coordinator.close()


async def test_rapid_canvas_interactions_coalesce_optional_speech_to_latest() -> None:
    state = _active_multi_binding_canvas_state()
    active = state.active
    assert active is not None
    provider = _BlockingInteractionContextProvider(_plan())
    coordinator, _, _, _ = _coordinator(
        realtime=provider,
        canvas_state=state,
    )
    await _ready(coordinator)
    delivery = asyncio.create_task(
        _complete_delivered_turn(coordinator, first_client_sequence=4)
    )
    await provider.turn_stream_started.wait()
    first = asyncio.create_task(
        coordinator.receive(
            _client(
                {
                    "event": "canvasInteraction",
                    "sequence": 1,
                    "sceneId": active.scene_id,
                    "sceneRevision": active.revision,
                    "interactionRevision": 1,
                    "svgSha256": active.document.svg_sha256,
                    "manifestSha256": active.document.manifest_sha256,
                    "bindingId": "explore_nucleus",
                }
            )
        )
    )
    try:
        await provider.context_refresh_started.wait()
        assert first.done(), "canonical admission waited for optional response work"
        await first
        await coordinator.receive(
            _client(
                {
                    "event": "canvasInteraction",
                    "sequence": 2,
                    "sceneId": active.scene_id,
                    "sceneRevision": active.revision,
                    "interactionRevision": 2,
                    "svgSha256": active.document.svg_sha256,
                    "manifestSha256": active.document.manifest_sha256,
                    "bindingId": "explore_wall",
                }
            )
        )
        await coordinator.receive(
            _client(
                {
                    "event": "canvasInteraction",
                    "sequence": 3,
                    "sceneId": active.scene_id,
                    "sceneRevision": active.revision,
                    "interactionRevision": 3,
                    "svgSha256": active.document.svg_sha256,
                    "manifestSha256": active.document.manifest_sha256,
                    "bindingId": "explore_chloroplast",
                }
            )
        )
        provider.allow_context_refresh.set()
        await provider.interaction_response_requested.wait()
        await delivery

        assert state.last_interaction is not None
        assert state.last_interaction.interaction_revision == 3
        assert state.last_interaction.binding_id == "explore_chloroplast"
        assert provider.canvas_interaction_response_turn_ids == [TURN_ID]
        assert provider.canvas_interaction_response_interactions == [
            state.last_interaction
        ]
        installed = provider.canvas_interaction_response_session_updates[0]["session"]
        assert isinstance(installed, dict)
        instructions = installed["instructions"]
        assert isinstance(instructions, str)
        assert "<target>Explore chloroplast</target>" in instructions
        assert "<target>Explore nucleus</target>" not in instructions
        assert "<target>Explore cell wall</target>" not in instructions
    finally:
        provider.allow_context_refresh.set()
        await asyncio.gather(first, return_exceptions=True)
        if not delivery.done():
            delivery.cancel()
            await asyncio.gather(delivery, return_exceptions=True)
        await coordinator.close()


async def test_child_speech_supersedes_interaction_while_context_refresh_is_pending() -> (
    None
):
    state = _active_labeled_canvas_state()
    active = state.active
    assert active is not None
    provider = _BlockingInteractionContextProvider(_plan())
    coordinator, _, _, _ = _coordinator(
        realtime=provider,
        canvas_state=state,
    )
    await _ready(coordinator)
    frames = coordinator.frames()
    try:
        for _ in range(3):
            await anext(frames)
        await coordinator.receive(
            _client(
                {
                    "event": "canvasInteraction",
                    "sequence": 1,
                    "sceneId": active.scene_id,
                    "sceneRevision": active.revision,
                    "interactionRevision": 1,
                    "svgSha256": active.document.svg_sha256,
                    "manifestSha256": active.document.manifest_sha256,
                    "bindingId": "explore_condensation",
                }
            )
        )
        pending_frame = asyncio.create_task(anext(frames))
        await provider.turn_stream_started.wait()
        await provider.context_refresh_started.wait()
        provider.emit_input_event(
            RealtimeProviderInputEvent(
                event_type="input_audio_buffer.speech_started",
                item_id="item-child-wins",
                audio_start_ms=800,
            )
        )
        provider.allow_context_refresh.set()

        first_frame = await pending_frame
        controls: list[object] = []
        if isinstance(first_frame, AvaServerControlFrameV4):
            controls.append(first_frame.event)
        async for frame in frames:
            if not isinstance(frame, AvaServerControlFrameV4):
                continue
            controls.append(frame.event)
            if isinstance(frame.event, TurnCancelledEventV4):
                break

        assert state.last_interaction is not None
        assert state.last_interaction.binding_id == "explore_condensation"
        assert provider.canvas_interaction_response_turn_ids == []
        assert provider.canvas_interaction_response_interactions == []
        assert not any(
            isinstance(event, InterruptRequestedEventV4) for event in controls
        )
        terminal = controls[-1]
        assert isinstance(terminal, TurnCancelledEventV4)
        assert terminal.reason == "providerSpeechStarted"
        assert terminal.delivered_sample_count == 0
    finally:
        provider.allow_context_refresh.set()
        await frames.aclose()
        await coordinator.close()


async def test_tap_during_interaction_speech_rearms_only_latest_reaction() -> None:
    state = _active_multi_binding_canvas_state()
    active = state.active
    assert active is not None
    provider = DeterministicRealtimePlanProvider(
        _plan(),
        child_text=None,
        wait_for_canvas_interaction_response=True,
    )
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator, _, _, _ = _coordinator(
        realtime=provider,
        memory=memory,
        persistent=True,
        canvas_state=state,
    )
    await _ready(coordinator)
    frames = coordinator.frames()
    try:
        await coordinator.receive(
            _client(
                {
                    "event": "canvasInteraction",
                    "sequence": 1,
                    "sceneId": active.scene_id,
                    "sceneRevision": active.revision,
                    "interactionRevision": 1,
                    "svgSha256": active.document.svg_sha256,
                    "manifestSha256": active.document.manifest_sha256,
                    "bindingId": "explore_nucleus",
                }
            )
        )

        first_metadata: AudioMetadataEventV4 | None = None
        async for frame in frames:
            if not isinstance(frame, AvaServerControlFrameV4):
                continue
            if isinstance(frame.event, AudioMetadataEventV4):
                first_metadata = frame.event
                break
        assert first_metadata is not None
        await coordinator.receive(
            _client(
                {
                    "event": "playbackStarted",
                    "sequence": 2,
                    "turnId": TURN_ID,
                    "streamId": STREAM_ID,
                    "renderedSampleCount": 0,
                }
            )
        )
        speaking_reached = False
        async for frame in frames:
            if isinstance(frame, AvaServerControlFrameV4) and isinstance(
                frame.event, TurnPhaseChangedEventV4
            ):
                if frame.event.phase == SpeechPhase.SPEAKING:
                    speaking_reached = True
                    break
        assert speaking_reached
        await coordinator.receive(
            _client(
                {
                    "event": "canvasInteraction",
                    "sequence": 3,
                    "sceneId": active.scene_id,
                    "sceneRevision": active.revision,
                    "interactionRevision": 2,
                    "svgSha256": active.document.svg_sha256,
                    "manifestSha256": active.document.manifest_sha256,
                    "bindingId": "explore_wall",
                }
            )
        )

        interrupted: InterruptRequestedEventV4 | None = None
        async for frame in frames:
            if isinstance(frame, AvaServerControlFrameV4) and isinstance(
                frame.event, InterruptRequestedEventV4
            ):
                interrupted = frame.event
                break
        assert interrupted is not None
        assert interrupted.stream_id == STREAM_ID
        assert interrupted.reason == "canvasInteractionSuperseded"
        interruptions = [interrupted]
        rendered_before_cancel = coordinator.received_sample_count // 2
        await coordinator.receive(
            _client(
                {
                    "event": "playbackStopped",
                    "sequence": 4,
                    "turnId": TURN_ID,
                    "streamId": STREAM_ID,
                    "renderedSampleCount": rendered_before_cancel,
                    "reason": "canvasInteractionSuperseded",
                }
            )
        )
        await coordinator.receive(
            _client(
                {
                    "event": "cancelTurn",
                    "sequence": 5,
                    "turnId": TURN_ID,
                    "requestId": "cancel-canvas-interaction-a",
                    "reason": "canvasInteractionSuperseded",
                }
            )
        )

        replacement_metadata: AudioMetadataEventV4 | None = None
        cancelled: TurnCancelledEventV4 | None = None
        async for frame in frames:
            if not isinstance(frame, AvaServerControlFrameV4):
                continue
            if isinstance(frame.event, InterruptRequestedEventV4):
                interruptions.append(frame.event)
                continue
            if isinstance(frame.event, TurnCancelledEventV4):
                cancelled = frame.event
                continue
            if (
                isinstance(frame.event, AudioMetadataEventV4)
                and frame.event.turn_id == "turn-coordinator-2"
            ):
                replacement_metadata = frame.event
                break

        assert replacement_metadata is not None
        assert cancelled is not None
        assert len(interruptions) == 1
        assert state.last_interaction is not None
        assert state.last_interaction.interaction_revision == 2
        assert state.last_interaction.binding_id == "explore_wall"
        assert provider.canvas_interaction_response_turn_ids == [
            TURN_ID,
            "turn-coordinator-2",
        ]
        assert [
            interaction.binding_id
            for interaction in provider.canvas_interaction_response_interactions
        ] == ["explore_nucleus", "explore_wall"]
        assert [
            interaction.interaction_revision
            for interaction in provider.canvas_interaction_response_interactions
        ] == [1, 2]
        snapshot = await memory.load_snapshot(
            subject="child:coordinator",
            age_band=AgeBand.MIDDLE_CHILDHOOD,
        )
        assert len(snapshot.delivered_turns) == 1
        persisted = snapshot.delivered_turns[0]
        assert persisted.input_kind == "canvasInteraction"
        assert persisted.canvas_interaction is not None
        assert persisted.canvas_interaction.binding_id == "explore_nucleus"
        assert persisted.canvas_interaction.interaction_revision == 1
        assert persisted.terminal == "cancelled"
        assert persisted.delivered_sample_count == rendered_before_cancel
        assert persisted.ava_delivered_text == cancelled.delivered_text
        latest_session = provider.canvas_interaction_response_session_updates[-1][
            "session"
        ]
        assert isinstance(latest_session, dict)
        instructions = latest_session["instructions"]
        assert isinstance(instructions, str)
        assert "<target>Explore cell wall</target>" in instructions
        assert "<target>Explore nucleus</target>" not in instructions
    finally:
        await frames.aclose()
        await coordinator.close()


async def test_child_speech_during_canvas_fade_does_not_repeat_interruption() -> None:
    state = _active_multi_binding_canvas_state()
    active = state.active
    assert active is not None
    provider = _InteractionThenChildRealtimeProvider(_plan())
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator, _, _, _ = _coordinator(
        realtime=provider,
        memory=memory,
        persistent=True,
        canvas_state=state,
    )
    await _ready(coordinator)
    frames = coordinator.frames()
    try:
        await coordinator.receive(
            _client(
                {
                    "event": "canvasInteraction",
                    "sequence": 1,
                    "sceneId": active.scene_id,
                    "sceneRevision": active.revision,
                    "interactionRevision": 1,
                    "svgSha256": active.document.svg_sha256,
                    "manifestSha256": active.document.manifest_sha256,
                    "bindingId": "explore_nucleus",
                }
            )
        )

        first_metadata: AudioMetadataEventV4 | None = None
        async for frame in frames:
            if isinstance(frame, AvaServerControlFrameV4) and isinstance(
                frame.event, AudioMetadataEventV4
            ):
                first_metadata = frame.event
                break
        assert first_metadata is not None
        await coordinator.receive(
            _client(
                {
                    "event": "playbackStarted",
                    "sequence": 2,
                    "turnId": TURN_ID,
                    "streamId": STREAM_ID,
                    "renderedSampleCount": 0,
                }
            )
        )
        speaking_reached = False
        async for frame in frames:
            if isinstance(frame, AvaServerControlFrameV4) and isinstance(
                frame.event, TurnPhaseChangedEventV4
            ):
                if frame.event.phase == SpeechPhase.SPEAKING:
                    speaking_reached = True
                    break
        assert speaking_reached
        await coordinator.receive(
            _client(
                {
                    "event": "canvasInteraction",
                    "sequence": 3,
                    "sceneId": active.scene_id,
                    "sceneRevision": active.revision,
                    "interactionRevision": 2,
                    "svgSha256": active.document.svg_sha256,
                    "manifestSha256": active.document.manifest_sha256,
                    "bindingId": "explore_wall",
                }
            )
        )

        first_interrupt: InterruptRequestedEventV4 | None = None
        async for frame in frames:
            if isinstance(frame, AvaServerControlFrameV4) and isinstance(
                frame.event, InterruptRequestedEventV4
            ):
                first_interrupt = frame.event
                break
        assert first_interrupt is not None
        assert first_interrupt.reason == "canvasInteractionSuperseded"

        provider.emit_input_event(
            RealtimeProviderInputEvent(
                event_type="input_audio_buffer.speech_started",
                item_id="item-child-during-canvas-fade",
                audio_start_ms=925,
            )
        )
        provider.serve_future_child_turns()
        pending = asyncio.create_task(anext(frames))
        await provider.input_observed.wait()
        await _advance_ready_callbacks()
        assert not pending.done(), (
            "child speech during an existing fade emitted a second interruption "
            "instead of waiting for authoritative client cancellation"
        )

        await coordinator.receive(
            _client(
                {
                    "event": "playbackStopped",
                    "sequence": 4,
                    "turnId": TURN_ID,
                    "streamId": STREAM_ID,
                    "renderedSampleCount": 0,
                    "reason": "canvasInteractionSuperseded",
                }
            )
        )
        await coordinator.receive(
            _client(
                {
                    "event": "cancelTurn",
                    "sequence": 5,
                    "turnId": TURN_ID,
                    "requestId": "cancel-canvas-then-child",
                    "reason": "canvasInteractionSuperseded",
                }
            )
        )

        first_after_cancel = await pending
        controls: list[object] = []
        if isinstance(first_after_cancel, AvaServerControlFrameV4):
            controls.append(first_after_cancel.event)
        replacement_metadata: AudioMetadataEventV4 | None = None
        async for frame in frames:
            if not isinstance(frame, AvaServerControlFrameV4):
                continue
            controls.append(frame.event)
            if (
                isinstance(frame.event, AudioMetadataEventV4)
                and frame.event.turn_id == "turn-coordinator-2"
            ):
                replacement_metadata = frame.event
                break

        assert replacement_metadata is not None
        assert (
            provider.input_item_owner(item_id="item-child-during-canvas-fade")
            == "turn-coordinator-2"
        )
        assert state.last_interaction is not None
        assert state.last_interaction.interaction_revision == 2
        assert state.last_interaction.binding_id == "explore_wall"
        assert provider.canvas_interaction_response_turn_ids == [TURN_ID]
        assert not any(
            isinstance(event, InterruptRequestedEventV4) for event in controls
        )
        assert not any(isinstance(event, TurnFailedEventV4) for event in controls)
        snapshot = await memory.load_snapshot(
            subject="child:coordinator",
            age_band=AgeBand.MIDDLE_CHILDHOOD,
        )
        assert len(snapshot.delivered_turns) == 1
        persisted = snapshot.delivered_turns[0]
        assert persisted.canvas_interaction is not None
        assert persisted.canvas_interaction.binding_id == "explore_nucleus"
        assert persisted.delivered_sample_count == 0
        assert persisted.ava_delivered_text == ""
        assert persisted.terminal == "cancelled"
    finally:
        await frames.aclose()
        await coordinator.close()


async def test_interaction_context_refresh_spanning_rearm_never_attaches_to_new_turn() -> (
    None
):
    state = _active_labeled_canvas_state()
    active = state.active
    assert active is not None
    provider = DeterministicRealtimePlanProvider(_plan())
    coordinator, _, _, _ = _coordinator(
        realtime=provider,
        persistent=True,
        canvas_state=state,
    )
    blocking_jobs = _BlockingCanvasInteractionJobs(coordinator._canvas_jobs)
    coordinator._canvas_jobs = blocking_jobs  # type: ignore[assignment]
    await _ready(coordinator)

    interaction = asyncio.create_task(
        coordinator.receive(
            _client(
                {
                    "event": "canvasInteraction",
                    "sequence": 1,
                    "sceneId": active.scene_id,
                    "sceneRevision": active.revision,
                    "interactionRevision": 1,
                    "svgSha256": active.document.svg_sha256,
                    "manifestSha256": active.document.manifest_sha256,
                    "bindingId": "explore_condensation",
                }
            )
        )
    )
    await blocking_jobs.started.wait()

    await _complete_first_persistent_turn_and_rearm(coordinator)
    assert coordinator._turn_id == "turn-coordinator-2"
    blocking_jobs.release.set()
    await interaction

    assert state.last_interaction is not None
    assert state.last_interaction.binding_id == "explore_condensation"
    assert provider.canvas_interaction_response_turn_ids == []
    assert provider.canvas_interaction_response_interactions == []
    instructions = provider.prepared_session_updates[-1]["session"]
    assert isinstance(instructions, dict)
    installed = instructions["instructions"]
    assert isinstance(installed, str)
    assert '<last_interaction mode="explore">' in installed
    await coordinator.close()


async def test_rejected_canvas_interaction_does_not_request_response() -> None:
    state = _active_labeled_canvas_state()
    active = state.active
    assert active is not None
    provider = DeterministicRealtimePlanProvider(_plan())
    coordinator, _, _, _ = _coordinator(
        realtime=provider,
        canvas_state=state,
    )
    await _ready(coordinator)

    await coordinator.receive(
        _client(
            {
                "event": "canvasInteraction",
                "sequence": 1,
                "sceneId": active.scene_id,
                "sceneRevision": active.revision,
                "interactionRevision": 1,
                "svgSha256": active.document.svg_sha256,
                "manifestSha256": active.document.manifest_sha256,
                "bindingId": "unknown_binding",
            }
        )
    )

    emitted = await coordinator._canvas_events.next_payload()
    assert emitted["event"] == "interactionRejected"
    assert emitted["bindingId"] == "unknown_binding"
    assert provider.canvas_interaction_response_turn_ids == []
    await coordinator.close()


async def test_idle_interaction_response_persists_typed_input_without_child_speech() -> (
    None
):
    state = _active_labeled_canvas_state()
    active = state.active
    assert active is not None
    provider = DeterministicRealtimePlanProvider(
        _plan(),
        child_text=None,
        wait_for_canvas_interaction_response=True,
    )
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator, _, speech, _ = _coordinator(
        realtime=provider,
        memory=memory,
        canvas_state=state,
    )
    await _ready(coordinator)
    delivery = asyncio.create_task(
        _complete_delivered_turn(coordinator, first_client_sequence=2)
    )
    await provider.turn_stream_started.wait()

    await coordinator.receive(
        _client(
            {
                "event": "canvasInteraction",
                "sequence": 1,
                "sceneId": active.scene_id,
                "sceneRevision": active.revision,
                "interactionRevision": 1,
                "svgSha256": active.document.svg_sha256,
                "manifestSha256": active.document.manifest_sha256,
                "bindingId": "explore_condensation",
            }
        )
    )
    events = await delivery

    assert isinstance(events[-1], TurnCompletedEventV4)
    assert events[-1].delivered_text == _plan().speech
    assert speech.synthesized_segment_ids
    assert provider.canvas_interaction_response_turn_ids == [TURN_ID]
    snapshot = await memory.load_snapshot(
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert len(snapshot.delivered_turns) == 1
    persisted = snapshot.delivered_turns[0]
    assert persisted.input_kind == "canvasInteraction"
    assert persisted.child_text is None
    assert persisted.child_text_source is None
    assert persisted.canvas_interaction is not None
    assert state.last_interaction is not None
    assert persisted.canvas_interaction.scene_id == state.last_interaction.scene_id
    assert (
        persisted.canvas_interaction.interaction_revision
        == state.last_interaction.interaction_revision
    )
    assert persisted.canvas_interaction.binding_id == state.last_interaction.binding_id
    assert persisted.ava_delivered_text == _plan().speech
    await coordinator.close()


async def test_incomplete_interaction_response_is_cancelled_not_completed() -> None:
    state = _active_labeled_canvas_state()
    active = state.active
    assert active is not None
    provider = _IncompleteInteractionRealtimeProvider(_plan())
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator, _, _, _ = _coordinator(
        realtime=provider,
        memory=memory,
        canvas_state=state,
    )
    await _ready(coordinator)
    delivery = asyncio.create_task(
        _complete_delivered_turn(coordinator, first_client_sequence=2)
    )
    await provider.turn_stream_started.wait()

    await coordinator.receive(
        _client(
            {
                "event": "canvasInteraction",
                "sequence": 1,
                "sceneId": active.scene_id,
                "sceneRevision": active.revision,
                "interactionRevision": 1,
                "svgSha256": active.document.svg_sha256,
                "manifestSha256": active.document.manifest_sha256,
                "bindingId": "explore_condensation",
            }
        )
    )
    events = await delivery

    terminal = events[-1]
    assert isinstance(terminal, TurnCancelledEventV4)
    assert terminal.reason == "providerResponseIncomplete:max_output_tokens"
    assert not any(isinstance(event, TurnCompletedEventV4) for event in events)
    snapshot = await memory.load_snapshot(
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert len(snapshot.delivered_turns) == 1
    persisted = snapshot.delivered_turns[0]
    assert persisted.input_kind == "canvasInteraction"
    assert persisted.terminal == "cancelled"
    assert persisted.ava_delivered_text == ""
    assert terminal.delivered_sample_count == 0
    assert not any(isinstance(event, AudioMetadataEventV4) for event in events)
    assert not any(isinstance(event, InterruptRequestedEventV4) for event in events)
    assert provider.cancelled_turn_ids == []
    await coordinator.close()


async def test_provider_incomplete_after_playback_waits_for_exact_stopped_position() -> (
    None
):
    provider = _IncompleteAfterSpeechRealtimeProvider(_plan())
    coordinator, _, speech, _ = _coordinator(
        realtime=provider,
        persistent=True,
    )
    await _ready(coordinator)
    frames = coordinator.frames()
    client_sequence = 1
    periodic_rendered_sample_count: int | None = None
    interruption: InterruptRequestedEventV4 | None = None
    try:
        while interruption is None:
            frame = await anext(frames)
            if isinstance(frame, AvaServerPcmFrameV4):
                if periodic_rendered_sample_count is None:
                    periodic_rendered_sample_count = coordinator.received_sample_count
                    await coordinator.receive(
                        _client(
                            {
                                "event": "playbackProgress",
                                "sequence": client_sequence,
                                "turnId": TURN_ID,
                                "streamId": STREAM_ID,
                                "renderedSampleCount": periodic_rendered_sample_count,
                                "bufferedSampleCount": 0,
                            }
                        )
                    )
                    client_sequence += 1
                    provider.allow_incomplete.set()
                continue
            if not isinstance(frame, AvaServerControlFrameV4):
                continue
            event = frame.event
            if isinstance(event, AudioMetadataEventV4):
                await coordinator.receive(
                    _client(
                        {
                            "event": "playbackStarted",
                            "sequence": client_sequence,
                            "turnId": TURN_ID,
                            "streamId": STREAM_ID,
                            "renderedSampleCount": 0,
                        }
                    )
                )
                client_sequence += 1
            elif isinstance(event, InterruptRequestedEventV4):
                interruption = event
            elif isinstance(event, TurnCancelledEventV4):
                pytest.fail(
                    "provider-incomplete playback terminated before the exact "
                    "playbackStopped position"
                )

        assert periodic_rendered_sample_count is not None
        assert interruption.reason == "providerResponseIncomplete:max_output_tokens"
        assert interruption.stream_id == STREAM_ID
        assert provider.cancelled_turn_ids == []

        waiting_for_stop = asyncio.create_task(anext(frames))
        await _advance_ready_callbacks()
        assert not waiting_for_stop.done(), (
            "turnCancelled was emitted before Flutter supplied playbackStopped"
        )

        stopped_rendered_sample_count = coordinator.received_sample_count
        assert stopped_rendered_sample_count > periodic_rendered_sample_count
        await coordinator.receive(
            _client(
                {
                    "event": "playbackStopped",
                    "sequence": client_sequence,
                    "turnId": TURN_ID,
                    "streamId": STREAM_ID,
                    "renderedSampleCount": stopped_rendered_sample_count,
                    "reason": "providerResponseIncomplete:max_output_tokens",
                }
            )
        )
        client_sequence += 1
        await _advance_ready_callbacks()
        assert not waiting_for_stop.done(), (
            "turnCancelled was emitted before Flutter supplied cancelTurn"
        )
        await coordinator.receive(
            _client(
                {
                    "event": "cancelTurn",
                    "sequence": client_sequence,
                    "turnId": TURN_ID,
                    "requestId": "cancel-provider-incomplete-playback",
                    "reason": "providerResponseIncomplete:max_output_tokens",
                }
            )
        )

        events: list[object] = []
        first_after_stop = await waiting_for_stop
        if isinstance(first_after_stop, AvaServerControlFrameV4):
            events.append(first_after_stop.event)
        rearmed: TurnCreatedEventV4 | None = None
        async for frame in frames:
            if not isinstance(frame, AvaServerControlFrameV4):
                continue
            events.append(frame.event)
            if (
                isinstance(frame.event, TurnCreatedEventV4)
                and frame.event.turn_id != TURN_ID
            ):
                rearmed = frame.event
                break

        terminal = next(
            event for event in events if isinstance(event, TurnCancelledEventV4)
        )
        assert terminal.reason == "providerResponseIncomplete:max_output_tokens"
        assert terminal.delivered_sample_count == stopped_rendered_sample_count
        assert rearmed is not None
        assert rearmed.turn_id == "turn-coordinator-2"
        assert provider.cancelled_turn_ids == []
        assert speech.cancelled_turn_ids == [TURN_ID]
    finally:
        provider.allow_incomplete.set()
        await frames.aclose()
        await coordinator.close()


async def test_provider_incomplete_after_metadata_without_playback_cancels_at_zero() -> (
    None
):
    provider = _IncompleteAfterSpeechRealtimeProvider(_plan())
    coordinator, _, speech, _ = _coordinator(realtime=provider)
    await _ready(coordinator)
    frames = coordinator.frames()
    events: list[object] = []
    try:
        async for frame in frames:
            if not isinstance(frame, AvaServerControlFrameV4):
                continue
            event = frame.event
            events.append(event)
            if isinstance(event, AudioMetadataEventV4):
                provider.allow_incomplete.set()
            if isinstance(event, TurnCancelledEventV4):
                break

        terminal = events[-1]
        assert isinstance(terminal, TurnCancelledEventV4)
        assert terminal.reason == "providerResponseIncomplete:max_output_tokens"
        assert terminal.delivered_sample_count == 0
        assert any(isinstance(event, AudioMetadataEventV4) for event in events)
        assert not any(isinstance(event, InterruptRequestedEventV4) for event in events)
        assert provider.cancelled_turn_ids == []
        assert speech.cancelled_turn_ids == [TURN_ID]
    finally:
        provider.allow_incomplete.set()
        await frames.aclose()
        await coordinator.close()


async def test_provider_incomplete_after_playback_stopped_uses_final_position() -> None:
    provider = _IncompleteAfterSpeechRealtimeProvider(_plan())
    coordinator, _, speech, _ = _coordinator(realtime=provider)
    await _ready(coordinator)
    frames = coordinator.frames()
    client_sequence = 1
    stopped_rendered_sample_count: int | None = None
    events: list[object] = []
    try:
        async for frame in frames:
            if isinstance(frame, AvaServerPcmFrameV4):
                if stopped_rendered_sample_count is None:
                    stopped_rendered_sample_count = coordinator.received_sample_count
                    await coordinator.receive(
                        _client(
                            {
                                "event": "playbackStopped",
                                "sequence": client_sequence,
                                "turnId": TURN_ID,
                                "streamId": STREAM_ID,
                                "renderedSampleCount": stopped_rendered_sample_count,
                                "reason": "providerResponseIncomplete:max_output_tokens",
                            }
                        )
                    )
                    client_sequence += 1
                    provider.allow_incomplete.set()
                continue
            if not isinstance(frame, AvaServerControlFrameV4):
                continue
            event = frame.event
            events.append(event)
            if isinstance(event, AudioMetadataEventV4):
                await coordinator.receive(
                    _client(
                        {
                            "event": "playbackStarted",
                            "sequence": client_sequence,
                            "turnId": TURN_ID,
                            "streamId": STREAM_ID,
                            "renderedSampleCount": 0,
                        }
                    )
                )
                client_sequence += 1
            if isinstance(event, TurnCancelledEventV4):
                break

        terminal = events[-1]
        assert isinstance(terminal, TurnCancelledEventV4)
        assert stopped_rendered_sample_count is not None
        assert terminal.reason == "providerResponseIncomplete:max_output_tokens"
        assert terminal.delivered_sample_count == stopped_rendered_sample_count
        assert not any(isinstance(event, InterruptRequestedEventV4) for event in events)
        assert provider.cancelled_turn_ids == []
        assert speech.cancelled_turn_ids == [TURN_ID]
    finally:
        provider.allow_incomplete.set()
        await frames.aclose()
        await coordinator.close()


async def test_late_turn_a_commit_preserves_cross_turn_memory_chronology() -> None:
    plan = _plan()
    provider = _CrossTurnTranscriptRealtimeProvider(plan)
    memory = _ObservedMemoryStore()
    occurrence_times = iter(
        (
            datetime(2026, 8, 25, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 8, 25, 10, 0, 1, tzinfo=UTC),
        )
    )
    coordinator, _, _, _ = _coordinator(
        plan=plan,
        realtime=provider,
        memory=memory,
        persistent=True,
        occurrence_time_factory=lambda: next(occurrence_times),
    )
    await _ready(coordinator)

    frames = coordinator.frames()
    client_sequence = 1
    active_stream_id: str | None = None
    completed_turn_ids: list[str] = []
    while len(completed_turn_ids) < 2:
        frame = await anext(frames)
        if not isinstance(frame, AvaServerControlFrameV4):
            continue
        event = frame.event
        if isinstance(event, AudioMetadataEventV4):
            active_stream_id = event.stream_id
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": client_sequence,
                        "turnId": event.turn_id,
                        "streamId": event.stream_id,
                        "renderedSampleCount": 0,
                    }
                )
            )
            client_sequence += 1
        if (
            isinstance(event, TurnPhaseChangedEventV4)
            and event.phase == SpeechPhase.SPEAKING
        ):
            assert active_stream_id is not None
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
        if isinstance(event, TurnCompletedEventV4):
            completed_turn_ids.append(event.turn_id)

    assert completed_turn_ids == [provider.turn_a, provider.turn_b]
    assert memory.committed_turn_ids == [provider.turn_b]

    provider.complete_turn_a_transcription()
    await memory.wait_for_turn(provider.turn_a)
    assert memory.committed_turn_ids == [provider.turn_b, provider.turn_a]
    await frames.aclose()
    await coordinator.close()

    fresh_provider = DeterministicRealtimePlanProvider(
        plan,
        child_text="Start a fresh conversation.",
    )
    fresh, _, _, _ = _coordinator(
        plan=plan,
        realtime=fresh_provider,
        memory=memory,
    )
    await _ready(fresh)

    snapshot = await memory.load_snapshot(
        subject="child:coordinator",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == [
        provider.turn_a,
        provider.turn_b,
    ]
    context = fresh.harness.context.content
    assert context.index(provider.child_a) < context.index(provider.child_b)
    await fresh.close()


async def test_memory_failure_warns_then_speech_completes() -> None:
    plan = _plan()
    coordinator, _, _, _ = _coordinator(
        plan=plan,
        memory=_FailingCommitMemoryStore(),
    )
    await _ready(coordinator)

    events = []
    async for frame in coordinator.frames():
        if not isinstance(frame, AvaServerControlFrameV4):
            continue
        events.append(frame.event)
        if isinstance(frame.event, AudioMetadataEventV4):
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackStarted",
                        "sequence": 1,
                        "turnId": TURN_ID,
                        "streamId": STREAM_ID,
                        "renderedSampleCount": 0,
                    }
                )
            )
        if (
            isinstance(frame.event, TurnPhaseChangedEventV4)
            and frame.event.phase == SpeechPhase.SPEAKING
        ):
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackProgress",
                        "sequence": 2,
                        "turnId": TURN_ID,
                        "streamId": STREAM_ID,
                        "renderedSampleCount": coordinator.received_sample_count,
                        "bufferedSampleCount": 0,
                    }
                )
            )

    warning = next(
        event for event in events if isinstance(event, MemoryPersistenceFailedEventV4)
    )
    assert warning.failure.code == SpeechFailureCode.MEMORY_UNAVAILABLE
    assert isinstance(events[-1], TurnCompletedEventV4)
    assert events[-1].delivered_text == plan.speech
    await coordinator.close()


async def test_client_ready_and_sequence_contract_fails_loudly() -> None:
    coordinator, _, _, _ = _coordinator()

    with pytest.raises(CoordinatorStateError, match="clientReady"):
        _ = [frame async for frame in coordinator.frames()]
    with pytest.raises(CoordinatorStateError, match="expected client sequence"):
        await coordinator.receive(
            _client(
                {
                    "event": "clientReady",
                    "sequence": 1,
                    "rendererCapabilityVersion": "ava-svg-renderer-v2",
                    "validatorVersion": "ava-svg-validator-v2",
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
                    "rendererCapabilityVersion": "ava-svg-renderer-v2",
                    "validatorVersion": "ava-svg-validator-v2",
                }
            )
        )
    await coordinator.close()


class _EmptyTurnRealtimeProvider(DeterministicRealtimePlanProvider):
    def turn_events(
        self,
        *,
        harness: object,
        turn_id: str,
    ) -> AsyncIterator[RealtimeProviderTurnEvent]:
        del harness, turn_id
        return self._empty()

    async def _empty(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        if False:  # pragma: no cover - preserves the async-iterator contract
            yield RealtimeSpeechTextDelta(delta="unreachable")


async def test_empty_provider_turn_cancels_cleanly_without_failure() -> None:
    provider = _EmptyTurnRealtimeProvider(_plan())
    coordinator, _, _, _ = _coordinator(realtime=provider)
    await _ready(coordinator)

    events = [
        frame.event
        async for frame in coordinator.frames()
        if isinstance(frame, AvaServerControlFrameV4)
    ]

    assert any(
        isinstance(event, TurnPhaseChangedEventV4)
        and event.phase == SpeechPhase.CANCELLING
        for event in events
    )
    assert isinstance(events[-1], TurnCancelledEventV4)
    assert not any(isinstance(event, TurnFailedEventV4) for event in events)
    await coordinator.close()
