from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path

import pytest

from ava_backend.canvas.svg_delivery import SvgCanvasDocumentStore
from ava_backend.canvas.svg_session import SvgCanvasSessionState
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
    MemoryPersistenceFailedEventV4,
    SegmentReadyEventV4,
    TurnCancelledEventV4,
    TurnCompletedEventV4,
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
    RealtimeProviderProtocolError,
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
    ) -> SvgCanvasSpecialistGeneration:
        del request
        raise AssertionError("speech-only coordinator test requested canvas")

    async def cancel(self, *, generation_id: str) -> None:
        del generation_id

    async def close(self) -> None:
        return


def _canvas_runtime() -> tuple[SvgCanvasJobCoordinator, ProtocolV4CanvasEventSink]:
    events = ProtocolV4CanvasEventSink()
    jobs = SvgCanvasJobCoordinator(
        session_id=SESSION_ID,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=_NoCanvasSpecialist(),
        state=SvgCanvasSessionState(),
        documents=SvgCanvasDocumentStore(),
        event_sink=events,
        generation_id_factory=lambda: "generation-coordinator",
    )
    return jobs, events


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
    jobs, events = _canvas_runtime()

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
    )
    return coordinator, resolved_realtime, speech, resolved_memory


async def _ready(coordinator: DeterministicRealtimeSessionCoordinator) -> None:
    await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coordinator.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v1",
                "validatorVersion": "ava-svg-validator-v1",
            }
        )
    )


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

    async def commit_delivered_turn(self, **kwargs: object):  # type: ignore[no-untyped-def]
        turn = kwargs["turn"]
        result = await super().commit_delivered_turn(**kwargs)  # type: ignore[arg-type]
        self.committed_turn_ids.append(turn.turn_id)  # type: ignore[union-attr]
        self.committed.set()
        return result


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


async def _complete_delivered_turn(
    coordinator: DeterministicRealtimeSessionCoordinator,
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
    return events


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
                    "rendererCapabilityVersion": "ava-svg-renderer-v1",
                    "validatorVersion": "ava-svg-validator-v1",
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
                    "rendererCapabilityVersion": "ava-svg-renderer-v1",
                    "validatorVersion": "ava-svg-validator-v1",
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
