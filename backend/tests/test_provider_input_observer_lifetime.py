from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import cast

from ava_backend.canvas.svg_delivery import SvgCanvasDocumentStore
from ava_backend.canvas.svg_session import SvgCanvasSessionState
from ava_backend.canvas.svg_specialist import (
    SvgCanvasSpecialistGeneration,
    SvgCanvasSpecialistRequest,
)
from ava_backend.protocol import AgeBand
from ava_backend.realtime.coordinator import DeterministicRealtimeSessionCoordinator
from ava_backend.realtime.coordinator_v4 import ProtocolV4CanvasEventSink
from ava_backend.realtime.events_v4 import (
    AVA_CLIENT_EVENT_ADAPTER_V4 as AVA_CLIENT_EVENT_ADAPTER,
    AudioMetadataEventV4 as AudioMetadataEvent,
    AvaClientEventUnionV4 as AvaClientEventUnion,
    TurnCancelledEventV4 as TurnCancelledEvent,
    TurnCompletedEventV4 as TurnCompletedEvent,
    TurnPhaseChangedEventV4 as TurnPhaseChangedEvent,
)
from ava_backend.realtime.fakes import (
    DETERMINISTIC_AUDIO_OFFER_SDP,
    DeterministicRealtimePlanProvider,
    DeterministicStreamingSpeechSynthesizer,
)
from ava_backend.realtime.harness import RealtimeHarness
from ava_backend.realtime.memory import DevelopmentAuthoritativeMemoryStore
from ava_backend.realtime.plan import AvaTurnPlan
from ava_backend.realtime.provider_session import (
    RealtimeProviderInputEvent,
    RealtimeProviderTurnEvent,
)
from ava_backend.realtime.tool_contract import RealtimeSpeechTextDelta
from ava_backend.realtime.speech_contract import SpeechPhase as AvaTurnPhase
from ava_backend.realtime.svg_canvas_jobs import SvgCanvasJobCoordinator
from ava_backend.realtime.websocket_v4 import (
    AvaServerControlFrameV4 as AvaServerControlFrame,
    AvaServerOutboundFrameV4 as AvaServerOutboundFrame,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"


def _plan() -> AvaTurnPlan:
    return AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / "ava_turn_plan_conversation_v3.json").read_text(
            encoding="utf-8"
        )
    )


class _NoCanvasSpecialist:
    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
    ) -> SvgCanvasSpecialistGeneration:
        del request
        raise AssertionError("observer lifetime scenarios do not request canvas")

    async def cancel(self, *, generation_id: str) -> None:
        del generation_id

    async def close(self) -> None:
        return


class _ObservedInputProvider(DeterministicRealtimePlanProvider):
    """Expose observer lifetimes without exposing coordinator task internals."""

    def __init__(self, plan: AvaTurnPlan, *, hold_cancellation: bool = False) -> None:
        super().__init__(plan, child_text="Tell me something gentle.")
        self.observer_started: asyncio.Queue[int] = asyncio.Queue()
        self.observer_cancelled: asyncio.Queue[int] = asyncio.Queue()
        self.input_consumed: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self.allow_cancellation_to_finish = asyncio.Event()
        if not hold_cancellation:
            self.allow_cancellation_to_finish.set()
        self.active_observer_count = 0
        self.maximum_active_observer_count = 0
        self.started_observer_count = 0
        self.cancelled_observer_count = 0
        self.finished_observer_count = 0

    async def next_input_event(self) -> RealtimeProviderInputEvent:
        self.started_observer_count += 1
        observer_number = self.started_observer_count
        self.active_observer_count += 1
        self.maximum_active_observer_count = max(
            self.maximum_active_observer_count,
            self.active_observer_count,
        )
        self.observer_started.put_nowait(observer_number)
        try:
            event = await super().next_input_event()
            self.input_consumed.put_nowait((observer_number, event.item_id))
            return event
        except asyncio.CancelledError:
            self.cancelled_observer_count += 1
            self.observer_cancelled.put_nowait(observer_number)
            await self.allow_cancellation_to_finish.wait()
            raise
        finally:
            self.active_observer_count -= 1
            self.finished_observer_count += 1


class _ListeningObservedInputProvider(_ObservedInputProvider):
    def __init__(self, plan: AvaTurnPlan, *, hold_cancellation: bool = False) -> None:
        super().__init__(plan, hold_cancellation=hold_cancellation)
        self.turn_stream_started = asyncio.Event()

    def turn_events(
        self,
        *,
        harness: RealtimeHarness,
        turn_id: str,
    ) -> AsyncIterator[RealtimeProviderTurnEvent]:
        del harness, turn_id
        return self._wait_for_cancellation()

    async def _wait_for_cancellation(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        self.turn_stream_started.set()
        await asyncio.Event().wait()
        if False:  # pragma: no cover - preserves the AsyncIterator contract
            yield RealtimeSpeechTextDelta(delta="unreachable")


class _EmptyThenNormalObservedInputProvider(_ObservedInputProvider):
    def __init__(self, plan: AvaTurnPlan) -> None:
        super().__init__(plan)
        self._turn_number = 0

    def turn_events(
        self,
        *,
        harness: RealtimeHarness,
        turn_id: str,
    ) -> AsyncIterator[RealtimeProviderTurnEvent]:
        self._turn_number += 1
        if self._turn_number == 1:
            return self._empty_turn()
        return super().turn_events(harness=harness, turn_id=turn_id)

    async def _empty_turn(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        if False:  # pragma: no cover - preserves the AsyncIterator contract
            yield RealtimeSpeechTextDelta(delta="unreachable")


def _client(payload: dict[str, object]) -> AvaClientEventUnion:
    return AVA_CLIENT_EVENT_ADAPTER.validate_python(
        {
            "protocolVersion": 4,
            "sessionId": "session-observer-lifetime",
            **payload,
        }
    )


def _coordinator(
    realtime: DeterministicRealtimePlanProvider,
    *,
    persistent: bool = False,
) -> DeterministicRealtimeSessionCoordinator:
    canvas_events = ProtocolV4CanvasEventSink()
    canvas_jobs = SvgCanvasJobCoordinator(
        session_id="session-observer-lifetime",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=_NoCanvasSpecialist(),
        state=SvgCanvasSessionState(),
        documents=SvgCanvasDocumentStore(),
        event_sink=canvas_events,
        generation_id_factory=lambda: "generation-observer-lifetime",
    )
    return DeterministicRealtimeSessionCoordinator(
        session_id="session-observer-lifetime",
        turn_id="turn-observer-1",
        request_id="request-observer-1",
        stream_id="stream-observer-1",
        subject="child:observer-lifetime",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=realtime,
        speech=DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31),
        canvas_jobs=canvas_jobs,
        canvas_events=canvas_events,
        memory=DevelopmentAuthoritativeMemoryStore(),
        persistent=persistent,
        turn_id_factory=(lambda: "turn-observer-2") if persistent else None,
        request_id_factory=(lambda: "request-observer-2") if persistent else None,
        stream_id_factory=(lambda: "stream-observer-2") if persistent else None,
    )


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


async def test_provider_input_observer_ends_with_normally_completed_turn() -> None:
    realtime = _ObservedInputProvider(_plan())
    coordinator = _coordinator(realtime)
    await _ready(coordinator)

    active_stream_id: str | None = None
    events: list[object] = []
    async for frame in coordinator.frames():
        if not isinstance(frame, AvaServerControlFrame):
            continue
        event = frame.event
        events.append(event)
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
                        "sequence": 1,
                        "turnId": event.turn_id,
                        "streamId": active_stream_id,
                        "renderedSampleCount": 0,
                    }
                )
            )
            await coordinator.receive(
                _client(
                    {
                        "event": "playbackProgress",
                        "sequence": 2,
                        "turnId": event.turn_id,
                        "streamId": active_stream_id,
                        "renderedSampleCount": coordinator.received_sample_count,
                        "bufferedSampleCount": 0,
                    }
                )
            )

    assert isinstance(events[-1], TurnCompletedEvent)
    assert realtime.active_observer_count == 0
    assert realtime.started_observer_count == realtime.finished_observer_count == 1
    assert realtime.cancelled_observer_count == 1
    await coordinator.close()


async def test_provider_input_observer_ends_with_explicit_turn_cancel() -> None:
    realtime = _ListeningObservedInputProvider(_plan())
    coordinator = _coordinator(realtime)
    await _ready(coordinator)

    async def collect_events() -> list[object]:
        return [
            frame.event
            async for frame in coordinator.frames()
            if isinstance(frame, AvaServerControlFrame)
        ]

    collector = asyncio.create_task(collect_events())
    assert await realtime.observer_started.get() == 1
    await realtime.turn_stream_started.wait()
    await coordinator.receive(
        _client(
            {
                "event": "cancelTurn",
                "sequence": 1,
                "turnId": "turn-observer-1",
                "requestId": "cancel-observer-1",
                "reason": "userStopped",
            }
        )
    )
    events = await collector

    assert isinstance(events[-1], TurnCancelledEvent)
    assert realtime.active_observer_count == 0
    assert realtime.started_observer_count == realtime.finished_observer_count == 1
    assert realtime.cancelled_observer_count == 1
    await coordinator.close()


async def test_session_close_awaits_suspended_provider_input_observer_once() -> None:
    realtime = _ListeningObservedInputProvider(_plan(), hold_cancellation=True)
    coordinator = _coordinator(realtime)
    await _ready(coordinator)

    async def collect_frames() -> list[AvaServerOutboundFrame]:
        return [frame async for frame in coordinator.frames()]

    collector = asyncio.create_task(collect_frames())
    assert await realtime.observer_started.get() == 1
    await realtime.turn_stream_started.wait()

    close_task = asyncio.create_task(coordinator.close())
    assert await realtime.observer_cancelled.get() == 1
    assert close_task.done() is False
    assert realtime.active_observer_count == 1

    realtime.allow_cancellation_to_finish.set()
    await close_task
    try:
        await collector
    except asyncio.CancelledError:
        pass

    assert realtime.active_observer_count == 0
    assert realtime.started_observer_count == realtime.finished_observer_count == 1
    assert realtime.cancelled_observer_count == 1
    await coordinator.close()
    assert realtime.cancelled_observer_count == 1


async def test_early_terminal_observer_cannot_consume_next_turn_input() -> None:
    realtime = _EmptyThenNormalObservedInputProvider(_plan())
    coordinator = _coordinator(realtime, persistent=True)
    await _ready(coordinator)

    terminals: list[object] = []
    active_stream_id: str | None = None
    client_sequence = 1
    emitted_next_turn_input = False
    frame_stream = cast(
        AsyncGenerator[AvaServerOutboundFrame, None],
        coordinator.frames(),
    )
    try:
        async for frame in frame_stream:
            if not isinstance(frame, AvaServerControlFrame):
                continue
            event = frame.event
            if isinstance(event, AudioMetadataEvent):
                active_stream_id = event.stream_id
            if (
                isinstance(event, TurnPhaseChangedEvent)
                and event.phase == AvaTurnPhase.SYNTHESIZING
                and event.turn_id == "turn-observer-2"
            ):
                assert await realtime.observer_started.get() == 1
                assert await realtime.observer_started.get() == 2
                realtime.emit_input_event(
                    RealtimeProviderInputEvent(
                        event_type="input_audio_buffer.speech_started",
                        item_id="item-next-turn",
                        audio_start_ms=0,
                    )
                )
                emitted_next_turn_input = True
            if (
                isinstance(event, TurnPhaseChangedEvent)
                and event.phase == AvaTurnPhase.SPEAKING
                and event.turn_id == "turn-observer-2"
            ):
                assert active_stream_id == "stream-observer-2"
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
            if isinstance(event, (TurnCancelledEvent, TurnCompletedEvent)):
                terminals.append(event)
                if len(terminals) == 2:
                    break
    finally:
        await frame_stream.aclose()
        await coordinator.close()

    assert emitted_next_turn_input is True
    assert isinstance(terminals[0], TurnCancelledEvent)
    assert isinstance(terminals[1], TurnCompletedEvent)
    assert await realtime.input_consumed.get() == (2, "item-next-turn")
    assert realtime.maximum_active_observer_count == 1
    assert realtime.active_observer_count == 0
    assert realtime.started_observer_count == realtime.finished_observer_count
