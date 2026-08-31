from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest

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
from ava_backend.realtime.delivery import (
    SynthesizedAlignmentBatch,
    SynthesizedAudioChunk,
    SynthesizedSpeechItem,
)
from ava_backend.realtime.events_v4 import (
    AVA_CLIENT_EVENT_ADAPTER_V4,
    AlignmentBatchEventV4,
    AudioChunkEventV4,
    AudioMetadataEventV4,
    AvaClientEventUnionV4,
    AvaServerEventTypeV4,
    CancelTurnEventV4,
    CanvasActivatedEventV4,
    CanvasDocumentAvailableEventV4,
    CanvasPresentationCueEventV4,
    CanvasRejectedEventV4,
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
    DETERMINISTIC_AUDIO_OFFER_SDP,
    DeterministicRealtimePlanProvider,
    DeterministicStreamingSpeechSynthesizer,
)
from ava_backend.realtime.memory import DevelopmentAuthoritativeMemoryStore
from ava_backend.realtime.plan import AvaTurnPlan
from ava_backend.realtime.provider_session import (
    RealtimeProviderInputEvent,
    RealtimeProviderTurnEvent,
)
from ava_backend.realtime.speech_contract import (
    SpeechAlignment,
    SpeechPhase,
    SpeechPhrase,
)
from ava_backend.realtime.svg_canvas_jobs import SvgCanvasJobCoordinator
from ava_backend.realtime.websocket_v4 import (
    AvaServerControlFrameV4,
    AvaServerOutboundFrameV4,
    AvaServerPcmFrameV4,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"
AGE_BAND = AgeBand.MIDDLE_CHILDHOOD


def _plan(name: str = "ava_turn_plan_conversation_v3.json") -> AvaTurnPlan:
    return AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / name).read_text(encoding="utf-8")
    )


class _SvgSpecialist:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.requests: list[SvgCanvasSpecialistRequest] = []
        self.cancelled_generation_ids: list[str] = []
        self.closed = False

    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
        *,
        draft_sink: object | None = None,
    ) -> SvgCanvasSpecialistGeneration:
        del draft_sink
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        document = SvgDocumentValidator().validate(
            (FIXTURE_ROOT / "ava_svg_document_v4.svg").read_text(encoding="utf-8")
        )
        return SvgCanvasSpecialistGeneration(
            generation_id=request.generation_id,
            document=document,
        )

    async def cancel(self, *, generation_id: str) -> None:
        if generation_id not in self.cancelled_generation_ids:
            self.cancelled_generation_ids.append(generation_id)

    async def close(self) -> None:
        self.closed = True


class _ControlledSvgSpecialist(_SvgSpecialist):
    def __init__(self) -> None:
        super().__init__()
        self.generation_started = asyncio.Event()
        self.allow_generation = asyncio.Event()

    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
        *,
        draft_sink: object | None = None,
    ) -> SvgCanvasSpecialistGeneration:
        del draft_sink
        self.generation_started.set()
        await self.allow_generation.wait()
        return await super().generate(request)


class _OwnedInputRealtimeProvider(DeterministicRealtimePlanProvider):
    """Deterministic provider lifecycle with explicit item/turn ownership."""

    def __init__(
        self,
        plan: AvaTurnPlan,
        *,
        originating_item_ids: tuple[str, ...],
        originating_event_copies: int = 1,
    ) -> None:
        if not originating_item_ids:
            raise ValueError("owned-input scenarios require originating items")
        if originating_event_copies < 1:
            raise ValueError("originating_event_copies must be positive")
        super().__init__(
            plan,
            child_text=None,
            canvas_brief=(
                "Draw a labeled plant cell with a tappable nucleus label."
                if any(segment.canvas_cues for segment in plan.segments)
                else None
            ),
            chunk_sizes=(3, 11, 1, 29, 7),
        )
        self._originating_item_ids = originating_item_ids
        self._originating_event_copies = originating_event_copies
        self._input_lifecycle: asyncio.Queue[RealtimeProviderInputEvent] = (
            asyncio.Queue()
        )
        self._owner_by_item_id: dict[str, str | None] = {}
        self._transcript_by_item_id: dict[str, str] = {}
        self._transcript_events_by_item_id: dict[str, asyncio.Event] = {}
        self.input_event_observed: dict[str, asyncio.Event] = {}
        self.admissions: list[tuple[str, tuple[str, ...]]] = []

    def turn_events(
        self,
        *,
        harness: object,
        turn_id: str,
    ) -> AsyncIterator[RealtimeProviderTurnEvent]:
        turn_index = len(self.requests)
        item_id = self._originating_item_ids[turn_index]
        owner = self._owner_by_item_id.setdefault(item_id, turn_id)
        if owner != turn_id:
            raise AssertionError("originating provider item changed turn ownership")
        for _ in range(self._originating_event_copies):
            self._input_lifecycle.put_nowait(
                RealtimeProviderInputEvent(
                    event_type="input_audio_buffer.speech_started",
                    item_id=item_id,
                    audio_start_ms=100 + turn_index,
                )
            )

        async def _events_after_origin_is_observed() -> AsyncIterator[
            RealtimeProviderTurnEvent
        ]:
            await self.wait_until_input_observed(item_id=item_id)
            async for event in super(_OwnedInputRealtimeProvider, self).turn_events(
                harness=harness,
                turn_id=turn_id,
            ):
                yield event

        return _events_after_origin_is_observed()

    async def next_input_event(self) -> RealtimeProviderInputEvent:
        event = await self._input_lifecycle.get()
        self.input_event_observed.setdefault(event.item_id, asyncio.Event()).set()
        return event

    async def wait_until_input_observed(self, *, item_id: str) -> None:
        await self.input_event_observed.setdefault(item_id, asyncio.Event()).wait()

    def input_item_owner(self, *, item_id: str) -> str | None:
        if item_id not in self._owner_by_item_id:
            raise AssertionError("input ownership requested before item observation")
        return self._owner_by_item_id[item_id]

    async def admit_pending_input_items(self, *, turn_id: str) -> tuple[str, ...]:
        pending = tuple(
            item_id
            for item_id, owner in self._owner_by_item_id.items()
            if owner is None
        )
        for item_id in pending:
            self._owner_by_item_id[item_id] = turn_id
        if pending:
            self.admissions.append((turn_id, pending))
        return pending

    def emit_speech_started(self, *, item_id: str, audio_start_ms: int) -> None:
        self._owner_by_item_id.setdefault(item_id, None)
        self._input_lifecycle.put_nowait(
            RealtimeProviderInputEvent(
                event_type="input_audio_buffer.speech_started",
                item_id=item_id,
                audio_start_ms=audio_start_ms,
            )
        )

    def complete_transcription(self, *, item_id: str, transcript: str) -> None:
        if item_id not in self._owner_by_item_id:
            raise AssertionError("transcription completed before item observation")
        self._transcript_by_item_id[item_id] = transcript
        self._transcript_events_by_item_id.setdefault(item_id, asyncio.Event()).set()

    async def child_transcript(self, *, turn_id: str) -> str | None:
        return self.available_child_transcript(turn_id=turn_id)

    def available_child_transcript(self, *, turn_id: str) -> str | None:
        transcripts = [
            self._transcript_by_item_id[item_id]
            for item_id, owner in self._owner_by_item_id.items()
            if owner == turn_id and item_id in self._transcript_by_item_id
        ]
        if len(transcripts) > 1:
            raise AssertionError("one turn received multiple provider transcripts")
        return transcripts[0] if transcripts else None

    def wait_for_child_transcript(
        self,
        *,
        turn_id: str,
    ) -> Awaitable[str | None]:
        owned_item_ids = tuple(
            item_id
            for item_id, owner in self._owner_by_item_id.items()
            if owner == turn_id
        )
        if len(owned_item_ids) > 1:
            raise AssertionError("one turn received multiple provider input items")

        async def wait() -> str | None:
            if not owned_item_ids:
                return None
            item_id = owned_item_ids[0]
            transcript = self._transcript_by_item_id.get(item_id)
            if transcript is not None:
                return transcript
            terminal = self._transcript_events_by_item_id.setdefault(
                item_id,
                asyncio.Event(),
            )
            await terminal.wait()
            return self._transcript_by_item_id[item_id]

        return wait()


class _FailingSpeech(DeterministicStreamingSpeechSynthesizer):
    async def synthesize(
        self,
        segment: SpeechPhrase,
        *,
        turn_id: str,
    ) -> AsyncIterator[SynthesizedSpeechItem]:
        del segment, turn_id
        raise RuntimeError("controlled ElevenLabs outage")
        if False:  # pragma: no cover - keeps the async-generator contract explicit
            yield cast(SynthesizedSpeechItem, None)


class _GatedOwnedInputRealtimeProvider(_OwnedInputRealtimeProvider):
    def __init__(
        self,
        plan: AvaTurnPlan,
        *,
        originating_item_ids: tuple[str, ...],
    ) -> None:
        super().__init__(plan, originating_item_ids=originating_item_ids)
        self.response_stream_started = asyncio.Event()
        self.allow_response = asyncio.Event()

    async def _stream(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        self.response_stream_started.set()
        await self.allow_response.wait()
        async for event in super()._stream():
            yield event


class _GatedSpeech(DeterministicStreamingSpeechSynthesizer):
    def __init__(self) -> None:
        super().__init__(chunk_sample_count=8)
        self.phrase_started = asyncio.Event()
        self.allow_first_item = asyncio.Event()
        self.iterator_closed = asyncio.Event()

    async def synthesize(
        self,
        segment: SpeechPhrase,
        *,
        turn_id: str,
    ) -> AsyncIterator[SynthesizedSpeechItem]:
        self.phrase_started.set()
        try:
            await self.allow_first_item.wait()
            async for item in super().synthesize(segment, turn_id=turn_id):
                yield item
        finally:
            self.iterator_closed.set()


class _BoundaryGatedSpeech(DeterministicStreamingSpeechSynthesizer):
    def __init__(self) -> None:
        super().__init__(chunk_sample_count=8)
        self.segment_started: dict[str, asyncio.Event] = {}
        self.allow_segment: dict[str, asyncio.Event] = {}
        self.first_chunk_emitted: dict[str, asyncio.Event] = {}
        self.allow_second_chunk: dict[str, asyncio.Event] = {}
        self.iterator_closed: dict[str, asyncio.Event] = {}

    def _event(self, source: dict[str, asyncio.Event], key: str) -> asyncio.Event:
        return source.setdefault(key, asyncio.Event())

    async def synthesize(
        self,
        segment: SpeechPhrase,
        *,
        turn_id: str,
    ) -> AsyncIterator[SynthesizedSpeechItem]:
        del turn_id
        self.synthesized_segment_ids.append(segment.id)
        self._event(self.segment_started, segment.id).set()
        try:
            await self._event(self.allow_segment, segment.id).wait()
            spans = (
                SpeechAlignment.model_validate(
                    {
                        "segmentId": segment.id,
                        "textStart": 0,
                        "textEnd": len(segment.speech),
                        "sampleStart": 0,
                        "sampleEnd": 8,
                        "phoneme": None,
                    }
                ),
            )
            yield SynthesizedAlignmentBatch(spans)
            self._event(self.first_chunk_emitted, segment.id).set()
            yield SynthesizedAudioChunk(bytes(16))
            await self._event(self.allow_second_chunk, segment.id).wait()
            yield SynthesizedAudioChunk(bytes(16))
        finally:
            self._event(self.iterator_closed, segment.id).set()


class _DelayedCancelAdmissionQueue:
    """Hold one validated cancel after receive() sets the shared cancel event."""

    def __init__(self, delegate: object) -> None:
        self._delegate = delegate
        self._pending_cancel: CancelTurnEventV4 | None = None
        self.cancel_delayed = asyncio.Event()

    @property
    def item_count(self) -> int:
        return cast(int, getattr(self._delegate, "item_count"))

    def put_nowait(self, event: AvaClientEventUnionV4) -> None:
        if isinstance(event, CancelTurnEventV4):
            if self._pending_cancel is not None:
                raise AssertionError("only one delayed cancel is supported")
            self._pending_cancel = event
            self.cancel_delayed.set()
            return
        getattr(self._delegate, "put_nowait")(event)

    async def get(self) -> AvaClientEventUnionV4:
        return cast(AvaClientEventUnionV4, await getattr(self._delegate, "get")())

    def release_cancel(self) -> None:
        pending = self._pending_cancel
        if pending is None:
            return
        self._pending_cancel = None
        getattr(self._delegate, "put_nowait")(pending)

    def close(self) -> None:
        self.release_cancel()
        getattr(self._delegate, "close")()


async def _advance_ready_callbacks(*, rounds: int = 12) -> None:
    """Advance already-ready tasks without a sleep or wall-clock timeout."""

    loop = asyncio.get_running_loop()
    for _ in range(rounds):
        checkpoint: asyncio.Future[None] = loop.create_future()
        loop.call_soon(checkpoint.set_result, None)
        await checkpoint


class _FailingMemory(DevelopmentAuthoritativeMemoryStore):
    async def commit_delivered_turn(self, **kwargs: object):  # type: ignore[no-untyped-def]
        del kwargs
        raise RuntimeError("controlled memory outage")


class _V4ConversationScenario:
    """Test-only driver for externally observable protocol-v4 behavior.

    This intentionally drives the current production coordinator through its public
    protocol seams. Assertions never depend on the v3 adapter, coordinator fields,
    task names, or scheduling order.
    """

    def __init__(
        self,
        *,
        session_id: str,
        turn_ids: tuple[str, ...],
        plan: AvaTurnPlan | None = None,
        child_text: str | None = "Tell me something gentle.",
        speech: DeterministicStreamingSpeechSynthesizer | None = None,
        memory: DevelopmentAuthoritativeMemoryStore | None = None,
        subject: str = "child:v4-characterization",
        svg_error: Exception | None = None,
        realtime: DeterministicRealtimePlanProvider | None = None,
        svg_specialist: _SvgSpecialist | None = None,
    ) -> None:
        if not turn_ids:
            raise ValueError("a V4 scenario requires at least one turn")
        resolved_plan = plan or _plan()
        self.session_id = session_id
        self.turn_ids = turn_ids
        self.subject = subject
        self.plan = resolved_plan
        self.realtime = realtime or DeterministicRealtimePlanProvider(
            resolved_plan,
            child_text=child_text,
            canvas_brief=(
                "Draw a labeled plant cell with a tappable nucleus label."
                if any(segment.canvas_cues for segment in resolved_plan.segments)
                else None
            ),
            chunk_sizes=(3, 11, 1, 29, 7),
        )
        self.speech = speech or DeterministicStreamingSpeechSynthesizer(
            chunk_sample_count=31
        )
        self.memory = memory or DevelopmentAuthoritativeMemoryStore()
        self.svg_specialist = svg_specialist or _SvgSpecialist(error=svg_error)
        self.documents = SvgCanvasDocumentStore()
        self.svg_state = SvgCanvasSessionState()
        self.canvas_events = ProtocolV4CanvasEventSink()
        self.canvas_jobs = SvgCanvasJobCoordinator(
            session_id=session_id,
            age_band=AGE_BAND,
            specialist=self.svg_specialist,
            state=self.svg_state,
            documents=self.documents,
            event_sink=self.canvas_events,
            generation_id_factory=lambda: f"generation-{session_id}",
        )

        next_turn_ids = iter(turn_ids[1:])
        next_request_ids = iter(f"request-{turn_id}" for turn_id in turn_ids[1:])
        next_stream_ids = iter(f"stream-{turn_id}" for turn_id in turn_ids[1:])
        self.coordinator = DeterministicRealtimeSessionCoordinator(
            session_id=session_id,
            turn_id=turn_ids[0],
            request_id=f"request-{turn_ids[0]}",
            stream_id=f"stream-{turn_ids[0]}",
            subject=subject,
            age_band=AGE_BAND,
            realtime=self.realtime,
            speech=self.speech,
            canvas_jobs=self.canvas_jobs,
            canvas_events=self.canvas_events,
            memory=self.memory,
            persistent=len(turn_ids) > 1,
            turn_id_factory=lambda: next(next_turn_ids),
            request_id_factory=lambda: next(next_request_ids),
            stream_id_factory=lambda: next(next_stream_ids),
        )
        self.frames: list[AvaServerOutboundFrameV4] = []
        self.controls: list[object] = []
        self.client_controls: list[AvaClientEventUnionV4] = []
        self._client_sequence = 0
        self._frame_stream: AsyncIterator[AvaServerOutboundFrameV4] | None = None
        self.active_turn_id: str | None = None
        self.active_stream_id: str | None = None
        self.received_samples = 0
        self.playback_started_turns: set[str] = set()

    async def start(self) -> str:
        negotiated = await self.coordinator.negotiate(
            offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP
        )
        await self.send(
            {
                "event": "clientReady",
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
        self._frame_stream = self.coordinator.frames().__aiter__()
        return negotiated.provider_call_id

    async def send(self, payload: dict[str, object]) -> None:
        event = AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "sessionId": self.session_id,
                "sequence": self._client_sequence,
                **payload,
            }
        )
        self._client_sequence += 1
        self.client_controls.append(event)
        await self.coordinator.receive(event)

    async def next_frame(self) -> AvaServerOutboundFrameV4:
        if self._frame_stream is None:
            raise RuntimeError("scenario has not started")
        frame = await self._frame_stream.__anext__()
        self.frames.append(frame)
        if isinstance(frame, AvaServerControlFrameV4):
            event = frame.event
            assert event.sequence == len(self.controls)
            self.controls.append(event)
            if isinstance(event, TurnCreatedEventV4):
                self.active_turn_id = event.turn_id
                self.active_stream_id = None
                self.received_samples = 0
            elif isinstance(event, AudioMetadataEventV4):
                self.active_stream_id = event.stream_id
            elif isinstance(event, AudioChunkEventV4):
                self.received_samples += event.sample_count
        return frame

    async def read_until(
        self,
        predicate: Callable[[object], bool],
    ) -> object:
        while True:
            frame = await self.next_frame()
            if isinstance(frame, AvaServerControlFrameV4) and predicate(frame.event):
                return frame.event

    async def read_to_speaking(self) -> TurnPhaseChangedEventV4:
        while True:
            frame = await self.next_frame()
            if not isinstance(frame, AvaServerControlFrameV4):
                continue
            event = frame.event
            if isinstance(event, AudioMetadataEventV4):
                await self._start_playback(event)
            if (
                isinstance(event, TurnPhaseChangedEventV4)
                and event.phase == SpeechPhase.SPEAKING
            ):
                return event

    async def complete_turn(self) -> TurnCompletedEventV4:
        speaking = await self.read_to_speaking()
        if self.active_stream_id is None:
            raise AssertionError("speaking began without an audio stream identity")
        await self.send(
            {
                "event": "playbackProgress",
                "turnId": speaking.turn_id,
                "streamId": self.active_stream_id,
                "renderedSampleCount": self.received_samples,
                "bufferedSampleCount": 0,
            }
        )
        terminal = await self.read_until(
            lambda event: isinstance(event, TurnCompletedEventV4)
        )
        return cast(TurnCompletedEventV4, terminal)

    async def _start_playback(self, event: AudioMetadataEventV4) -> None:
        if event.turn_id in self.playback_started_turns:
            return
        self.playback_started_turns.add(event.turn_id)
        await self.send(
            {
                "event": "playbackStarted",
                "turnId": event.turn_id,
                "streamId": event.stream_id,
                "renderedSampleCount": 0,
            }
        )

    def assert_pcm_adjacency(self) -> None:
        for index, frame in enumerate(self.frames):
            if not (
                isinstance(frame, AvaServerControlFrameV4)
                and isinstance(frame.event, AudioChunkEventV4)
            ):
                continue
            assert index + 1 < len(self.frames)
            binary = self.frames[index + 1]
            assert isinstance(binary, AvaServerPcmFrameV4)
            assert binary.descriptor == frame.event
            assert len(binary.pcm) == frame.event.byte_length

    async def close(self) -> None:
        if self._frame_stream is not None:
            close = getattr(self._frame_stream, "aclose", None)
            if close is not None:
                await close()
            self._frame_stream = None
        await self.coordinator.close()


@pytest.mark.asyncio
async def test_active_session_accepts_only_newest_canvas_render_profile_revision() -> (
    None
):
    scenario = _V4ConversationScenario(
        session_id="session-v4-render-profile",
        turn_ids=("turn-v4-render-profile",),
    )
    try:
        await scenario.start()
        newest = {
            "usableAspectRatio": 1.6,
            "orientation": "landscape",
            "widthClass": "regular",
            "heightClass": "compact",
            "safeInsets": {
                "top": 0.02,
                "right": 0.03,
                "bottom": 0.04,
                "left": 0.03,
            },
            "touchEnvironment": "direct",
            "minimumInteractionTargetPoints": 44.0,
            "textScaleFactor": 1.15,
        }
        await scenario.send(
            {
                "event": "canvasRenderProfileUpdated",
                "profileRevision": 2,
                "renderProfile": newest,
            }
        )
        await scenario.send(
            {
                "event": "canvasRenderProfileUpdated",
                "profileRevision": 1,
                "renderProfile": {
                    **newest,
                    "usableAspectRatio": 0.68,
                    "orientation": "portrait",
                    "widthClass": "compact",
                    "heightClass": "regular",
                },
            }
        )

        assert scenario.canvas_jobs.render_profile_revision == 2
    finally:
        await scenario.close()


@pytest.mark.asyncio
async def test_normal_turn_freezes_v4_wire_order_identity_and_pcm_adjacency() -> None:
    scenario = _V4ConversationScenario(
        session_id="session-v4-normal",
        turn_ids=("turn-v4-normal",),
    )
    provider_call_id = await scenario.start()
    terminal = await scenario.complete_turn()

    names = [event.event for event in scenario.controls]
    assert names[:7] == [
        AvaServerEventTypeV4.TURN_CREATED,
        AvaServerEventTypeV4.TURN_PHASE_CHANGED,
        AvaServerEventTypeV4.TURN_PHASE_CHANGED,
        AvaServerEventTypeV4.TURN_PHASE_CHANGED,
        AvaServerEventTypeV4.SEGMENT_READY,
        AvaServerEventTypeV4.TURN_PHASE_CHANGED,
        AvaServerEventTypeV4.AUDIO_METADATA,
    ]
    assert names[-2:] == [
        AvaServerEventTypeV4.TURN_PHASE_CHANGED,
        AvaServerEventTypeV4.TURN_COMPLETED,
    ]
    assert provider_call_id == "rtc_fake_ava_call_0001"
    assert terminal.session_id == "session-v4-normal"
    assert terminal.turn_id == "turn-v4-normal"
    assert terminal.delivered_sample_count == scenario.received_samples
    assert terminal.delivered_text == scenario.plan.speech
    assert all(
        getattr(event, "turn_id", "turn-v4-normal") == "turn-v4-normal"
        for event in scenario.controls
    )
    scenario.assert_pcm_adjacency()
    assert all(
        "microphone" not in str(event.event).casefold() for event in scenario.controls
    )
    await scenario.close()


@pytest.mark.asyncio
async def test_speech_started_while_waiting_for_first_phrase_preempts_before_audio() -> (
    None
):
    plan = _plan()
    provider = _GatedOwnedInputRealtimeProvider(
        plan,
        originating_item_ids=("item-planning-origin", "item-planning-next"),
    )
    scenario = _V4ConversationScenario(
        session_id="session-v4-planning-preemption",
        turn_ids=("turn-v4-planning-a", "turn-v4-planning-b"),
        plan=plan,
        realtime=provider,
    )
    try:
        await scenario.start()
        for _ in range(3):
            await scenario.next_frame()
        next_frame = asyncio.create_task(scenario.next_frame())
        await provider.response_stream_started.wait()
        await provider.wait_until_input_observed(item_id="item-planning-origin")
        provider.emit_speech_started(
            item_id="item-planning-next",
            audio_start_ms=210,
        )
        provider.allow_response.set()

        frame = await next_frame

        assert isinstance(frame, AvaServerControlFrameV4)
        assert isinstance(frame.event, TurnPhaseChangedEventV4)
        assert frame.event.phase == SpeechPhase.CANCELLING
        cancelled = cast(
            TurnCancelledEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, TurnCancelledEventV4)
            ),
        )
        assert cancelled.delivered_sample_count == 0
        assert cancelled.delivered_text == ""
        assert not any(
            isinstance(event, AudioMetadataEventV4) for event in scenario.controls
        )
        created_b = cast(
            TurnCreatedEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, TurnCreatedEventV4)
                and event.turn_id == "turn-v4-planning-b"
            ),
        )
        assert created_b.turn_id == "turn-v4-planning-b"
        await scenario.read_until(
            lambda event: isinstance(event, TurnPhaseChangedEventV4)
            and event.turn_id == "turn-v4-planning-b"
            and event.phase == SpeechPhase.LISTENING
        )
        await scenario.next_frame()
        assert provider.input_item_owner(item_id="item-planning-next") == (
            "turn-v4-planning-b"
        )
    finally:
        await scenario.close()


@pytest.mark.asyncio
async def test_speech_started_after_audio_metadata_preempts_before_tts_opens() -> None:
    plan = _plan()
    provider = _OwnedInputRealtimeProvider(
        plan,
        originating_item_ids=("item-metadata-origin",),
    )
    speech = _GatedSpeech()
    scenario = _V4ConversationScenario(
        session_id="session-v4-metadata-preemption",
        turn_ids=("turn-v4-metadata-preemption",),
        plan=plan,
        realtime=provider,
        speech=speech,
    )
    try:
        await scenario.start()
        metadata = cast(
            AudioMetadataEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, AudioMetadataEventV4)
            ),
        )
        await scenario._start_playback(metadata)
        await provider.wait_until_input_observed(item_id="item-metadata-origin")
        provider.emit_speech_started(
            item_id="item-metadata-next",
            audio_start_ms=320,
        )
        speech.allow_first_item.set()

        frame = await scenario.next_frame()

        assert isinstance(frame, AvaServerControlFrameV4)
        assert isinstance(frame.event, InterruptRequestedEventV4)
        assert frame.event.stream_id == metadata.stream_id
        assert speech.cancelled_turn_ids == ["turn-v4-metadata-preemption"]
        assert not any(
            isinstance(event, AudioChunkEventV4) for event in scenario.controls
        )
    finally:
        await scenario.close()


@pytest.mark.asyncio
async def test_speech_started_during_tts_before_first_pcm_cancels_exact_phrase() -> (
    None
):
    plan = _plan()
    provider = _OwnedInputRealtimeProvider(
        plan,
        originating_item_ids=("item-tts-origin",),
    )
    speech = _GatedSpeech()
    scenario = _V4ConversationScenario(
        session_id="session-v4-tts-preemption",
        turn_ids=("turn-v4-tts-preemption",),
        plan=plan,
        realtime=provider,
        speech=speech,
    )
    try:
        await scenario.start()
        metadata = cast(
            AudioMetadataEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, AudioMetadataEventV4)
            ),
        )
        await scenario._start_playback(metadata)
        pending = asyncio.create_task(scenario.next_frame())
        await speech.phrase_started.wait()
        await provider.wait_until_input_observed(item_id="item-tts-origin")
        provider.emit_speech_started(
            item_id="item-tts-next",
            audio_start_ms=410,
        )
        speech.allow_first_item.set()

        frame = await pending

        assert isinstance(frame, AvaServerControlFrameV4)
        assert isinstance(frame.event, InterruptRequestedEventV4)
        assert speech.cancelled_turn_ids == ["turn-v4-tts-preemption"]
        assert speech.iterator_closed.is_set()
        assert not any(
            isinstance(event, AudioChunkEventV4) for event in scenario.controls
        )
    finally:
        await scenario.close()


@pytest.mark.asyncio
async def test_preemption_at_pcm_item_boundary_keeps_descriptor_binary_atomic() -> None:
    plan = _plan()
    segment_id = "speech_1"
    provider = _OwnedInputRealtimeProvider(
        plan,
        originating_item_ids=("item-pcm-origin",),
    )
    speech = _BoundaryGatedSpeech()
    scenario = _V4ConversationScenario(
        session_id="session-v4-pcm-boundary",
        turn_ids=("turn-v4-pcm-boundary",),
        plan=plan,
        realtime=provider,
        speech=speech,
    )
    try:
        await scenario.start()
        metadata = cast(
            AudioMetadataEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, AudioMetadataEventV4)
            ),
        )
        await scenario._start_playback(metadata)
        speech._event(speech.allow_segment, segment_id).set()
        await scenario.read_until(
            lambda event: isinstance(event, AlignmentBatchEventV4)
        )
        descriptor = await scenario.next_frame()
        binary = await scenario.next_frame()
        assert isinstance(descriptor, AvaServerControlFrameV4)
        assert isinstance(descriptor.event, AudioChunkEventV4)
        assert isinstance(binary, AvaServerPcmFrameV4)
        assert binary.descriptor == descriptor.event
        await speech._event(speech.first_chunk_emitted, segment_id).wait()
        await provider.wait_until_input_observed(item_id="item-pcm-origin")

        pending = asyncio.create_task(scenario.next_frame())
        provider.emit_speech_started(
            item_id="item-pcm-next",
            audio_start_ms=510,
        )
        speech._event(speech.allow_second_chunk, segment_id).set()
        frame = await pending

        assert isinstance(frame, AvaServerControlFrameV4)
        assert isinstance(frame.event, InterruptRequestedEventV4)
        assert [
            event for event in scenario.controls if isinstance(event, AudioChunkEventV4)
        ] == [descriptor.event]
        scenario.assert_pcm_adjacency()
    finally:
        await scenario.close()


@pytest.mark.asyncio
async def test_speech_started_between_phrases_prevents_next_phrase_synthesis() -> None:
    plan = _plan("ava_turn_plan_v3.json")
    first_segment_id = "speech_1"
    second_segment_id = "speech_2"
    provider = _OwnedInputRealtimeProvider(
        plan,
        originating_item_ids=("item-between-origin",),
    )
    speech = _BoundaryGatedSpeech()
    scenario = _V4ConversationScenario(
        session_id="session-v4-between-phrases",
        turn_ids=("turn-v4-between-phrases",),
        plan=plan,
        realtime=provider,
        speech=speech,
    )
    try:
        await scenario.start()
        metadata = cast(
            AudioMetadataEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, AudioMetadataEventV4)
            ),
        )
        await scenario._start_playback(metadata)
        speech._event(speech.allow_segment, first_segment_id).set()
        speech._event(speech.allow_second_chunk, first_segment_id).set()
        while True:
            frame = await scenario.next_frame()
            if isinstance(frame, AvaServerPcmFrameV4) and (
                len(
                    [
                        event
                        for event in scenario.controls
                        if isinstance(event, AudioChunkEventV4)
                    ]
                )
                == 2
            ):
                break
        await provider.wait_until_input_observed(item_id="item-between-origin")
        provider.emit_speech_started(
            item_id="item-between-next",
            audio_start_ms=610,
        )

        frame = await scenario.next_frame()

        assert isinstance(frame, AvaServerControlFrameV4)
        assert isinstance(frame.event, InterruptRequestedEventV4)
        assert second_segment_id not in speech.synthesized_segment_ids
    finally:
        await scenario.close()


@pytest.mark.asyncio
async def test_speech_started_during_second_phrase_cancels_without_later_pcm() -> None:
    plan = _plan("ava_turn_plan_v3.json")
    first_segment_id = "speech_1"
    second_segment_id = "speech_2"
    provider = _OwnedInputRealtimeProvider(
        plan,
        originating_item_ids=("item-second-origin",),
    )
    speech = _BoundaryGatedSpeech()
    scenario = _V4ConversationScenario(
        session_id="session-v4-second-phrase",
        turn_ids=("turn-v4-second-phrase",),
        plan=plan,
        realtime=provider,
        speech=speech,
    )
    try:
        await scenario.start()
        metadata = cast(
            AudioMetadataEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, AudioMetadataEventV4)
            ),
        )
        await scenario._start_playback(metadata)
        speech._event(speech.allow_segment, first_segment_id).set()
        speech._event(speech.allow_second_chunk, first_segment_id).set()
        await scenario.read_until(
            lambda event: isinstance(event, SegmentReadyEventV4)
            and event.segment_index == 1
        )
        pending = asyncio.create_task(scenario.next_frame())
        await speech._event(speech.segment_started, second_segment_id).wait()
        await provider.wait_until_input_observed(item_id="item-second-origin")
        provider.emit_speech_started(
            item_id="item-second-next",
            audio_start_ms=710,
        )
        speech._event(speech.allow_segment, second_segment_id).set()

        frame = await pending

        assert isinstance(frame, AvaServerControlFrameV4)
        assert isinstance(frame.event, InterruptRequestedEventV4)
        assert speech.cancelled_turn_ids == ["turn-v4-second-phrase"]
        assert not speech._event(
            speech.first_chunk_emitted,
            second_segment_id,
        ).is_set()
    finally:
        await scenario.close()


@pytest.mark.asyncio
async def test_speaking_provider_interrupt_waits_for_existing_control_consumer() -> (
    None
):
    provider = _OwnedInputRealtimeProvider(
        _plan(),
        originating_item_ids=("item-live-origin", "item-live-child-next"),
    )
    scenario = _V4ConversationScenario(
        session_id="session-v4-live-speaking-race",
        turn_ids=("turn-v4-live-a", "turn-v4-live-b"),
        realtime=provider,
    )
    try:
        await scenario.start()
        speaking = await scenario.read_to_speaking()
        await provider.wait_until_input_observed(item_id="item-live-origin")
        provider.emit_speech_started(
            item_id="item-live-child-next",
            audio_start_ms=910,
        )
        interrupt = cast(
            InterruptRequestedEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, InterruptRequestedEventV4)
            ),
        )

        pending = asyncio.create_task(scenario.next_frame())
        await _advance_ready_callbacks()
        assert not pending.done(), (
            "speaking arbitration terminated before Flutter supplied its "
            "authoritative cancellation"
        )

        assert scenario.active_stream_id is not None
        rendered_sample = max(1, scenario.received_samples // 2)
        await scenario.send(
            {
                "event": "playbackStopped",
                "turnId": speaking.turn_id,
                "streamId": scenario.active_stream_id,
                "renderedSampleCount": rendered_sample,
                "reason": "providerSpeechStarted",
            }
        )
        await scenario.send(
            {
                "event": "cancelTurn",
                "turnId": speaking.turn_id,
                "requestId": "cancel-live-speaking-race",
                "reason": "providerSpeechStarted",
            }
        )
        first_after_interrupt = await pending
        assert isinstance(first_after_interrupt, AvaServerControlFrameV4)
        assert isinstance(first_after_interrupt.event, TurnPhaseChangedEventV4)
        assert first_after_interrupt.event.phase == SpeechPhase.CANCELLING
        terminal = cast(
            TurnCancelledEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, TurnCancelledEventV4)
            ),
        )
        created_b = cast(
            TurnCreatedEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, TurnCreatedEventV4)
                and event.turn_id == "turn-v4-live-b"
            ),
        )
        await scenario.read_until(
            lambda event: isinstance(event, TurnPhaseChangedEventV4)
            and event.turn_id == "turn-v4-live-b"
            and event.phase == SpeechPhase.PLANNING
        )

        assert interrupt.reason == "providerSpeechStarted"
        assert terminal.delivered_sample_count == rendered_sample
        assert created_b.turn_id == "turn-v4-live-b"
        assert provider.input_item_owner(item_id="item-live-child-next") == (
            "turn-v4-live-b"
        )
        assert (
            len(
                [
                    event
                    for event in scenario.controls
                    if isinstance(event, InterruptRequestedEventV4)
                ]
            )
            == 1
        )
        assert not any(
            isinstance(event, TurnFailedEventV4) for event in scenario.controls
        )
    finally:
        await scenario.close()


@pytest.mark.asyncio
async def test_speaking_client_cancel_waits_for_delayed_queue_admission() -> None:
    scenario = _V4ConversationScenario(
        session_id="session-v4-delayed-client-cancel",
        turn_ids=("turn-v4-delayed-client-cancel",),
    )
    delayed = _DelayedCancelAdmissionQueue(scenario.coordinator._controls)
    scenario.coordinator._controls = delayed  # type: ignore[assignment]
    try:
        await scenario.start()
        speaking = await scenario.read_to_speaking()
        pending = asyncio.create_task(scenario.next_frame())
        await _advance_ready_callbacks()
        assert not pending.done()
        await scenario.send(
            {
                "event": "cancelTurn",
                "turnId": speaking.turn_id,
                "requestId": "cancel-delayed-admission",
                "reason": "userStopped",
            }
        )
        await delayed.cancel_delayed.wait()

        await _advance_ready_callbacks()
        assert not pending.done(), (
            "shared cancellation wakeup read a pending control task before "
            "CancelTurn queue admission"
        )

        delayed.release_cancel()
        first = await pending
        assert isinstance(first, AvaServerControlFrameV4)
        assert isinstance(first.event, TurnPhaseChangedEventV4)
        assert first.event.phase == SpeechPhase.CANCELLING
        terminal = cast(
            TurnCancelledEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, TurnCancelledEventV4)
            ),
        )
        assert terminal.reason == "userStopped"
        assert not any(
            isinstance(event, InterruptRequestedEventV4) for event in scenario.controls
        )
        assert not any(
            isinstance(event, TurnFailedEventV4) for event in scenario.controls
        )
    finally:
        delayed.release_cancel()
        await scenario.close()


@pytest.mark.asyncio
async def test_simultaneous_speech_started_and_playback_control_are_consumed_once() -> (
    None
):
    provider = _OwnedInputRealtimeProvider(
        _plan(),
        originating_item_ids=("item-simultaneous-origin",),
    )
    scenario = _V4ConversationScenario(
        session_id="session-v4-simultaneous-speaking",
        turn_ids=("turn-v4-simultaneous-speaking",),
        realtime=provider,
    )
    try:
        await scenario.start()
        speaking = await scenario.read_to_speaking()
        assert scenario.active_stream_id is not None
        await provider.wait_until_input_observed(item_id="item-simultaneous-origin")
        provider.emit_speech_started(
            item_id="item-simultaneous-child",
            audio_start_ms=920,
        )
        progress_sample = max(1, scenario.received_samples // 3)
        await scenario.send(
            {
                "event": "playbackProgress",
                "turnId": speaking.turn_id,
                "streamId": scenario.active_stream_id,
                "renderedSampleCount": progress_sample,
                "bufferedSampleCount": 0,
            }
        )
        interrupt = cast(
            InterruptRequestedEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, InterruptRequestedEventV4)
            ),
        )
        await scenario.send(
            {
                "event": "playbackStopped",
                "turnId": speaking.turn_id,
                "streamId": scenario.active_stream_id,
                "renderedSampleCount": progress_sample,
                "reason": "providerSpeechStarted",
            }
        )
        await scenario.send(
            {
                "event": "cancelTurn",
                "turnId": speaking.turn_id,
                "requestId": "cancel-simultaneous-speaking",
                "reason": "providerSpeechStarted",
            }
        )
        terminal = cast(
            TurnCancelledEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, TurnCancelledEventV4)
            ),
        )

        assert interrupt.reason == "providerSpeechStarted"
        assert terminal.delivered_sample_count == progress_sample
        assert scenario.coordinator._controls.item_count == 0
        assert (
            len(
                [
                    event
                    for event in scenario.controls
                    if isinstance(event, InterruptRequestedEventV4)
                ]
            )
            == 1
        )
    finally:
        await scenario.close()


@pytest.mark.asyncio
async def test_three_consecutive_turns_share_one_provider_session_and_reset_clock() -> (
    None
):
    turn_ids = ("turn-v4-one", "turn-v4-two", "turn-v4-three")
    scenario = _V4ConversationScenario(
        session_id="session-v4-three-turns",
        turn_ids=turn_ids,
    )
    provider_call_id = await scenario.start()

    terminals = [await scenario.complete_turn() for _ in turn_ids]

    assert [terminal.turn_id for terminal in terminals] == list(turn_ids)
    assert [request.turn_id for request in scenario.realtime.requests] == list(turn_ids)
    assert provider_call_id == "rtc_fake_ava_call_0001"
    assert len(scenario.realtime.negotiated_offers) == 1
    assert len(scenario.realtime.prepared_session_updates) == 1
    assert [
        event.turn_id
        for event in scenario.client_controls
        if str(event.event) == "playbackStarted"
    ] == list(turn_ids)
    assert all(
        event.rendered_sample_count == 0
        for event in scenario.client_controls
        if str(event.event) == "playbackStarted"
    )
    assert [event.sequence for event in scenario.controls] == list(
        range(len(scenario.controls))
    )
    scenario.assert_pcm_adjacency()
    await scenario.close()


@pytest.mark.asyncio
async def test_originating_input_item_cannot_interrupt_its_own_response() -> None:
    plan = _plan()
    provider = _OwnedInputRealtimeProvider(
        plan,
        originating_item_ids=("item-origin-a",),
    )
    scenario = _V4ConversationScenario(
        session_id="session-v4-origin-ownership",
        turn_ids=("turn-v4-origin-a",),
        plan=plan,
        realtime=provider,
    )
    try:
        await scenario.start()
        speaking = await scenario.read_to_speaking()
        provider.complete_transcription(
            item_id="item-origin-a",
            transcript="Draw me a gentle picture.",
        )
        assert scenario.active_stream_id is not None
        await scenario.send(
            {
                "event": "playbackProgress",
                "turnId": speaking.turn_id,
                "streamId": scenario.active_stream_id,
                "renderedSampleCount": scenario.received_samples,
                "bufferedSampleCount": 0,
            }
        )
        terminal = cast(
            TurnCompletedEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, TurnCompletedEventV4)
            ),
        )

        assert terminal.turn_id == "turn-v4-origin-a"
        assert not any(
            isinstance(event, InterruptRequestedEventV4) for event in scenario.controls
        )
        assert provider.cancelled_turn_ids == []
    finally:
        await scenario.close()


@pytest.mark.asyncio
async def test_duplicate_originating_speech_started_is_non_mutating() -> None:
    plan = _plan()
    provider = _OwnedInputRealtimeProvider(
        plan,
        originating_item_ids=("item-origin-duplicate",),
        originating_event_copies=2,
    )
    scenario = _V4ConversationScenario(
        session_id="session-v4-origin-duplicate",
        turn_ids=("turn-v4-origin-duplicate",),
        plan=plan,
        realtime=provider,
    )
    try:
        await scenario.start()
        terminal = await scenario.complete_turn()

        assert terminal.turn_id == "turn-v4-origin-duplicate"
        assert not any(
            isinstance(event, InterruptRequestedEventV4) for event in scenario.controls
        )
        assert provider.cancelled_turn_ids == []
    finally:
        await scenario.close()


@pytest.mark.asyncio
async def test_originating_item_does_not_cancel_pending_canvas_generation() -> None:
    plan = _plan("ava_turn_plan_v3.json")
    provider = _OwnedInputRealtimeProvider(
        plan,
        originating_item_ids=("item-origin-canvas",),
    )
    specialist = _ControlledSvgSpecialist()
    scenario = _V4ConversationScenario(
        session_id="session-v4-origin-canvas",
        turn_ids=("turn-v4-origin-canvas",),
        plan=plan,
        realtime=provider,
        svg_specialist=specialist,
    )
    try:
        await scenario.start()
        speaking = await scenario.read_to_speaking()
        await specialist.generation_started.wait()
        specialist.allow_generation.set()
        first_decisive_event = await scenario.read_until(
            lambda event: isinstance(
                event,
                (CanvasDocumentAvailableEventV4, InterruptRequestedEventV4),
            )
        )

        assert isinstance(first_decisive_event, CanvasDocumentAvailableEventV4)
        assert scenario.active_stream_id is not None
        await scenario.send(
            {
                "event": "playbackProgress",
                "turnId": speaking.turn_id,
                "streamId": scenario.active_stream_id,
                "renderedSampleCount": scenario.received_samples,
                "bufferedSampleCount": 0,
            }
        )
        await scenario.read_until(lambda event: isinstance(event, TurnCompletedEventV4))

        assert specialist.cancelled_generation_ids == []
        assert scenario.svg_state.candidate is not None
        assert not any(
            isinstance(event, InterruptRequestedEventV4) for event in scenario.controls
        )
    finally:
        await scenario.close()


@pytest.mark.asyncio
async def test_new_input_item_interrupts_stream_but_not_accepted_canvas(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.realtime_coordinator")
    plan = _plan("ava_turn_plan_v3.json")
    provider = _OwnedInputRealtimeProvider(
        plan,
        originating_item_ids=("item-origin-canvas-barge",),
    )
    specialist = _ControlledSvgSpecialist()
    scenario = _V4ConversationScenario(
        session_id="session-v4-canvas-barge",
        turn_ids=("turn-v4-canvas-barge", "turn-v4-after-canvas-barge"),
        plan=plan,
        realtime=provider,
        svg_specialist=specialist,
    )
    try:
        await scenario.start()
        speaking = await scenario.read_to_speaking()
        await specialist.generation_started.wait()
        provider.complete_transcription(
            item_id="item-origin-canvas-barge",
            transcript="Draw a plant cell.",
        )
        provider.emit_speech_started(item_id="item-next-canvas-barge", audio_start_ms=9)
        interrupt = cast(
            InterruptRequestedEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, InterruptRequestedEventV4)
            ),
        )
        assert scenario.active_stream_id is not None
        rendered_sample = max(1, scenario.received_samples // 2)
        await scenario.send(
            {
                "event": "playbackStopped",
                "turnId": speaking.turn_id,
                "streamId": scenario.active_stream_id,
                "renderedSampleCount": rendered_sample,
                "reason": "providerSpeechStarted",
            }
        )
        await scenario.send(
            {
                "event": "cancelTurn",
                "turnId": speaking.turn_id,
                "requestId": "cancel-v4-canvas-barge",
                "reason": "providerSpeechStarted",
            }
        )
        terminal = cast(
            TurnCancelledEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, TurnCancelledEventV4)
            ),
        )

        assert interrupt.turn_id == speaking.turn_id
        assert interrupt.stream_id == scenario.active_stream_id
        assert terminal.delivered_sample_count == rendered_sample
        assert specialist.cancelled_generation_ids == []
        specialist.allow_generation.set()
        available = await scenario.read_until(
            lambda event: isinstance(event, CanvasDocumentAvailableEventV4)
        )
        assert isinstance(available, CanvasDocumentAvailableEventV4)
        assert available.generation_id == "generation-session-v4-canvas-barge"
        assert scenario.svg_state.candidate is not None
        await scenario.send(
            {
                "event": "canvasDocumentPrepared",
                "generationId": available.generation_id,
                "sceneId": available.scene_id,
                "sceneRevision": available.scene_revision,
                "svgSha256": available.svg_sha256,
                "manifestSha256": available.manifest_sha256,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
        cue = cast(
            CanvasPresentationCueEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, CanvasPresentationCueEventV4)
            ),
        )
        assert cue.presentation_sample_offset == rendered_sample
        await scenario.send(
            {
                "event": "canvasPresented",
                "generationId": available.generation_id,
                "sceneId": available.scene_id,
                "sceneRevision": available.scene_revision,
                "svgSha256": available.svg_sha256,
                "manifestSha256": available.manifest_sha256,
                "firstRenderedSample": rendered_sample,
            }
        )
        activated = await scenario.read_until(
            lambda event: isinstance(event, CanvasActivatedEventV4)
        )
        assert isinstance(activated, CanvasActivatedEventV4)
        assert activated.generation_id == available.generation_id
        assert scenario.svg_state.active is not None
        interruption_record = next(
            record
            for record in caplog.records
            if record.getMessage().startswith("realtime_interruption_requested")
        )
        assert (
            cast(dict[str, object], interruption_record.ava_context)[
                "providerInputItemId"
            ]
            == "item-next-canvas-barge"
        )
    finally:
        await scenario.close()


@pytest.mark.asyncio
async def test_barge_in_item_is_admitted_next_and_stale_origin_cannot_mutate_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.realtime_coordinator")
    plan = _plan()
    provider = _OwnedInputRealtimeProvider(
        plan,
        originating_item_ids=("item-origin-turn-a", "item-barge-turn-b"),
    )
    memory = DevelopmentAuthoritativeMemoryStore()
    scenario = _V4ConversationScenario(
        session_id="session-v4-next-input-ownership",
        turn_ids=("turn-v4-a", "turn-v4-b"),
        plan=plan,
        realtime=provider,
        memory=memory,
        subject="child:v4-next-input-ownership",
    )
    try:
        await scenario.start()
        speaking_a = await scenario.read_to_speaking()
        provider.complete_transcription(
            item_id="item-origin-turn-a",
            transcript="Tell me about stars.",
        )
        provider.emit_speech_started(item_id="item-barge-turn-b", audio_start_ms=20)
        provider.emit_speech_started(item_id="item-barge-turn-b", audio_start_ms=20)
        interrupt = cast(
            InterruptRequestedEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, InterruptRequestedEventV4)
            ),
        )
        assert scenario.active_stream_id is not None
        rendered_sample = max(1, scenario.received_samples // 2)
        await scenario.send(
            {
                "event": "playbackStopped",
                "turnId": speaking_a.turn_id,
                "streamId": scenario.active_stream_id,
                "renderedSampleCount": rendered_sample,
                "reason": "providerSpeechStarted",
            }
        )
        await scenario.send(
            {
                "event": "cancelTurn",
                "turnId": speaking_a.turn_id,
                "requestId": "cancel-v4-owned-barge",
                "reason": "providerSpeechStarted",
            }
        )
        cancelled_a = cast(
            TurnCancelledEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, TurnCancelledEventV4)
            ),
        )

        speaking_b = await scenario.read_to_speaking()
        provider.complete_transcription(
            item_id="item-barge-turn-b",
            transcript="Actually, tell me about the moon.",
        )
        provider.emit_speech_started(item_id="item-origin-turn-a", audio_start_ms=1)
        provider.emit_speech_started(item_id="item-origin-turn-a", audio_start_ms=1)
        assert scenario.active_stream_id is not None
        await scenario.send(
            {
                "event": "playbackProgress",
                "turnId": speaking_b.turn_id,
                "streamId": scenario.active_stream_id,
                "renderedSampleCount": scenario.received_samples,
                "bufferedSampleCount": 0,
            }
        )
        completed_b = cast(
            TurnCompletedEventV4,
            await scenario.read_until(
                lambda event: isinstance(event, TurnCompletedEventV4)
            ),
        )

        interruptions = [
            event
            for event in scenario.controls
            if isinstance(event, InterruptRequestedEventV4)
        ]
        assert interruptions == [interrupt]
        assert interrupt.turn_id == "turn-v4-a"
        assert cancelled_a.delivered_sample_count == rendered_sample
        assert completed_b.turn_id == "turn-v4-b"
        assert provider.admissions == [("turn-v4-b", ("item-barge-turn-b",))]
        assert provider.input_item_owner(item_id="item-origin-turn-a") == "turn-v4-a"
        assert provider.input_item_owner(item_id="item-barge-turn-b") == "turn-v4-b"
        interruption_record = next(
            record
            for record in caplog.records
            if record.getMessage().startswith("realtime_interruption_requested")
        )
        assert (
            cast(dict[str, object], interruption_record.ava_context)[
                "providerInputItemId"
            ]
            == "item-barge-turn-b"
        )
        await _advance_ready_callbacks()
        snapshot = await memory.load_snapshot(
            subject="child:v4-next-input-ownership",
            age_band=AGE_BAND,
        )
        assert [turn.child_text for turn in snapshot.delivered_turns] == [
            "Tell me about stars.",
            "Actually, tell me about the moon.",
        ]
    finally:
        await scenario.close()


@pytest.mark.asyncio
async def test_provider_vad_during_long_playback_owns_one_exact_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.realtime_coordinator")
    scenario = _V4ConversationScenario(
        session_id="session-v4-barge-in",
        turn_ids=("turn-v4-barge-in",),
    )
    await scenario.start()
    speaking = await scenario.read_to_speaking()
    assert scenario.active_stream_id is not None
    rendered_sample = max(1, scenario.received_samples // 2)

    scenario.realtime.emit_input_event(
        RealtimeProviderInputEvent(
            event_type="input_audio_buffer.speech_started",
            item_id="item-v4-barge-in",
            audio_start_ms=321,
        )
    )
    interrupt = cast(
        InterruptRequestedEventV4,
        await scenario.read_until(
            lambda event: isinstance(event, InterruptRequestedEventV4)
        ),
    )
    assert interrupt.turn_id == speaking.turn_id
    assert interrupt.stream_id == scenario.active_stream_id
    await scenario.send(
        {
            "event": "playbackStopped",
            "turnId": interrupt.turn_id,
            "streamId": interrupt.stream_id,
            "renderedSampleCount": rendered_sample,
            "reason": "providerSpeechStarted",
        }
    )
    await scenario.send(
        {
            "event": "cancelTurn",
            "turnId": interrupt.turn_id,
            "requestId": "cancel-v4-barge-in",
            "reason": "providerSpeechStarted",
        }
    )
    terminal = cast(
        TurnCancelledEventV4,
        await scenario.read_until(
            lambda event: isinstance(event, TurnCancelledEventV4)
        ),
    )

    assert terminal.delivered_sample_count == rendered_sample
    assert terminal.delivered_text != scenario.plan.speech
    assert scenario.realtime.cancelled_turn_ids == ["turn-v4-barge-in"]
    assert scenario.speech.cancelled_turn_ids == ["turn-v4-barge-in"]
    assert [
        str(event.event)
        for event in scenario.client_controls
        if str(event.event) in {"playbackStopped", "cancelTurn"}
    ] == ["playbackStopped", "cancelTurn"]
    interruption_record = next(
        record
        for record in caplog.records
        if record.getMessage().startswith("realtime_interruption_requested")
    )
    cancellation_record = next(
        record
        for record in caplog.records
        if record.getMessage().startswith("realtime_cancellation_received")
    )
    terminal_record = next(
        record
        for record in caplog.records
        if record.getMessage().startswith("realtime_turn_terminal")
    )
    for record in (interruption_record, cancellation_record, terminal_record):
        context = cast(dict[str, object], record.ava_context)
        assert context["sessionId"] == "session-v4-barge-in"
        assert context["turnId"] == "turn-v4-barge-in"
        assert context["providerInputItemId"] == "item-v4-barge-in"
    assert (
        cast(dict[str, object], cancellation_record.ava_context)["cancellationReason"]
        == "providerSpeechStarted"
    )
    await scenario.close()


@pytest.mark.asyncio
async def test_tts_failure_is_classified_without_pcm_or_memory_commit() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    scenario = _V4ConversationScenario(
        session_id="session-v4-tts-failure",
        turn_ids=("turn-v4-tts-failure",),
        speech=_FailingSpeech(),
        memory=memory,
    )
    await scenario.start()
    failed = cast(
        TurnFailedEventV4,
        await scenario.read_until(lambda event: isinstance(event, TurnFailedEventV4)),
    )

    assert failed.failure.component == "speechProvider"
    assert failed.failure.code == "speechUnavailable"
    assert failed.turn_id == "turn-v4-tts-failure"
    assert not any(isinstance(event, AudioChunkEventV4) for event in scenario.controls)
    snapshot = await memory.load_snapshot(subject=scenario.subject, age_band=AGE_BAND)
    assert snapshot.delivered_turns == []
    await scenario.close()


@pytest.mark.asyncio
async def test_canvas_failure_is_nonterminal_while_v4_speech_completes() -> None:
    scenario = _V4ConversationScenario(
        session_id="session-v4-canvas-failure",
        turn_ids=("turn-v4-canvas-failure",),
        plan=_plan("ava_turn_plan_v3.json"),
        child_text="How does a plant cell work?",
        svg_error=RuntimeError("controlled Sol outage"),
    )
    await scenario.start()
    terminal = await scenario.complete_turn()

    assert terminal.delivered_text == scenario.plan.speech
    assert any(isinstance(event, CanvasRejectedEventV4) for event in scenario.controls)
    assert not any(isinstance(event, TurnFailedEventV4) for event in scenario.controls)
    assert len(scenario.svg_specialist.requests) == 1
    scenario.assert_pcm_adjacency()
    await scenario.close()


@pytest.mark.asyncio
async def test_memory_failure_is_nonfatal_and_does_not_poison_v4_rearm() -> None:
    scenario = _V4ConversationScenario(
        session_id="session-v4-memory-failure",
        turn_ids=("turn-v4-memory-one", "turn-v4-memory-two"),
        memory=_FailingMemory(),
    )
    await scenario.start()
    terminals = [await scenario.complete_turn(), await scenario.complete_turn()]
    await _advance_ready_callbacks()

    warnings = [
        event
        for event in scenario.controls
        if isinstance(event, MemoryPersistenceFailedEventV4)
    ]
    assert [terminal.turn_id for terminal in terminals] == [
        "turn-v4-memory-one",
        "turn-v4-memory-two",
    ]
    assert warnings == []
    assert not any(isinstance(event, TurnFailedEventV4) for event in scenario.controls)
    await scenario.close()


@pytest.mark.asyncio
async def test_session_close_and_reconnect_do_not_reuse_transient_turn_state() -> None:
    first = _V4ConversationScenario(
        session_id="session-v4-before-reconnect",
        turn_ids=("turn-v4-before-reconnect",),
    )
    await first.start()
    await first.read_until(
        lambda event: isinstance(event, TurnPhaseChangedEventV4)
        and event.phase == SpeechPhase.LISTENING
    )
    await first.close()

    assert first.realtime.closed
    assert first.speech.closed
    assert first.svg_specialist.closed

    second = _V4ConversationScenario(
        session_id="session-v4-after-reconnect",
        turn_ids=("turn-v4-after-reconnect",),
    )
    await second.start()
    terminal = await second.complete_turn()

    assert terminal.session_id == "session-v4-after-reconnect"
    assert terminal.turn_id == "turn-v4-after-reconnect"
    assert second.controls[0].sequence == 0
    assert second.client_controls[0].sequence == 0
    assert all(
        getattr(event, "turn_id", "turn-v4-after-reconnect")
        != "turn-v4-before-reconnect"
        for event in second.controls
    )
    await second.close()


@pytest.mark.asyncio
async def test_reset_between_sessions_removes_old_turn_before_new_commit() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    subject = "child:v4-reset-order"
    before = _V4ConversationScenario(
        session_id="session-v4-before-reset",
        turn_ids=("turn-v4-before-reset",),
        memory=memory,
        subject=subject,
    )
    await before.start()
    await before.complete_turn()
    await before.close()
    populated = await memory.load_snapshot(subject=subject, age_band=AGE_BAND)
    assert [turn.turn_id for turn in populated.delivered_turns] == [
        "turn-v4-before-reset"
    ]

    receipt = await memory.reset_conversation(subject=subject)
    empty = await memory.load_snapshot(subject=subject, age_band=AGE_BAND)
    assert receipt.revision == 1
    assert empty.delivered_turns == []
    assert empty.stable_memories == []
    assert empty.current_session_summary is None

    after = _V4ConversationScenario(
        session_id="session-v4-after-reset",
        turn_ids=("turn-v4-after-reset",),
        memory=memory,
        subject=subject,
    )
    await after.start()
    await after.complete_turn()
    await after.close()
    refreshed = await memory.load_snapshot(subject=subject, age_band=AGE_BAND)
    assert [turn.turn_id for turn in refreshed.delivered_turns] == [
        "turn-v4-after-reset"
    ]
