from __future__ import annotations

import asyncio
import json
import logging
from collections import deque

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from ava_backend.canvas.svg_workspace import RecordedSvgCanvasInteraction
from ava_backend.protocol import AgeBand
from ava_backend.providers.openai_realtime_provider import (
    OpenAIRealtimeProbeRequest,
    OpenAIRealtimeProtocolError,
    OpenAIRealtimeConnectionClosedError,
    OpenAIRealtimeRouterBackpressureError,
    OpenAIRealtimeUnavailableError,
    _OpenAIRealtimeEventChannel,
    _session_update,
)
from ava_backend.realtime.harness import build_realtime_harness
from ava_backend.realtime.memory import AuthoritativeMemorySnapshot
from ava_backend.realtime.tool_contract import (
    INTERACTIVE_CANVAS_TOOL_NAME,
    InteractiveCanvasToolOutput,
    RealtimeCanvasToolCall,
    RealtimeRejectedCanvasToolCall,
    RealtimeSpeechTextDelta,
)

RESPONSE_ID = "resp_test_live_0001"
CONTINUATION_RESPONSE_ID = "resp_test_live_0002"
FUNCTION_CALL_ID = "call_test_live_0001"
_TEST_CHANNELS: set[_OpenAIRealtimeEventChannel] = set()


def _recorded_interaction(
    *,
    binding_id: str = "explore_great_red_spot",
    accessibility_label: str = "Great Red Spot",
    interaction_mode: str = "explore",
    interaction_revision: int = 1,
    step_index: int | None = None,
) -> RecordedSvgCanvasInteraction:
    return RecordedSvgCanvasInteraction(
        scene_id="scene_jupiter",
        scene_revision=3,
        interaction_revision=interaction_revision,
        svg_sha256="a" * 64,
        manifest_sha256="b" * 64,
        binding_id=binding_id,
        interaction_mode=interaction_mode,
        target_id=f"target_{binding_id}",
        accessibility_label=accessibility_label,
        step_index=step_index,
    )


@pytest.fixture(autouse=True)
async def _close_test_sideband_channels():  # type: ignore[no-untyped-def]
    """A sole-reader task must never escape a deterministic provider test."""

    yield
    channels = tuple(_TEST_CHANNELS)
    _TEST_CHANNELS.clear()
    if channels:
        await asyncio.gather(*(channel.close() for channel in channels))


class _FakeRealtimeSocket:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = deque(json.dumps(event) for event in events)
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self.received_count = 0
        self._received = asyncio.Event()
        self._sent = asyncio.Event()
        self._available = asyncio.Event()
        if self.events:
            self._available.set()

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))
        self._sent.set()

    async def recv(self) -> str:
        if not self.events:
            # A persistent provider socket remains open between turns.  An
            # empty fixture therefore means "no event yet", not transport
            # failure; the channel closes/cancels the sole receiver at test
            # teardown.
            await self._available.wait()
        self.received_count += 1
        self._received.set()
        event = self.events.popleft()
        if not self.events:
            self._available.clear()
        return event

    def push(self, event: dict[str, object]) -> None:
        self.events.append(json.dumps(event))
        self._available.set()

    async def wait_received(self, count: int) -> None:
        while self.received_count < count:
            self._received.clear()
            if self.received_count >= count:
                return
            await self._received.wait()

    async def wait_sent(self, count: int) -> None:
        while len(self.sent) < count:
            self._sent.clear()
            if len(self.sent) >= count:
                return
            await self._sent.wait()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        assert code == 1000
        assert reason
        self.closed = True


class _DelayedRealtimeSocket(_FakeRealtimeSocket):
    def __init__(
        self,
        events: list[dict[str, object]],
        *,
        delayed_receive_index: int,
        delay_seconds: float,
        hang_when_empty: bool = False,
    ) -> None:
        super().__init__(events)
        self._delayed_receive_index = delayed_receive_index
        self._delay_seconds = delay_seconds
        self._hang_when_empty = hang_when_empty
        self._receive_index = 0

    async def recv(self) -> str:
        receive_index = self._receive_index
        self._receive_index += 1
        if receive_index == self._delayed_receive_index:
            await asyncio.sleep(self._delay_seconds)
        if not self.events and self._hang_when_empty:
            await asyncio.Event().wait()
        return await super().recv()


class _ClosingRealtimeSocket(_FakeRealtimeSocket):
    async def recv(self) -> str:
        raise ConnectionClosedError(Close(1011, "keepalive ping timeout"), None)


class _ControlledClosingRealtimeSocket(_FakeRealtimeSocket):
    def __init__(self, events: list[dict[str, object]]) -> None:
        super().__init__(events)
        self.receiver_waiting = asyncio.Event()
        self.terminate_receiver = asyncio.Event()

    async def recv(self) -> str:
        if self.events:
            return await super().recv()
        self.receiver_waiting.set()
        await self.terminate_receiver.wait()
        raise ConnectionClosedError(Close(1011, "controlled receiver close"), None)


def _harness():  # type: ignore[no-untyped-def]
    return build_realtime_harness(
        AuthoritativeMemorySnapshot.model_validate(
            {
                "ageBand": AgeBand.MIDDLE_CHILDHOOD,
                "currentSessionSummary": None,
                "deliveredTurns": [],
                "stableMemories": [],
            }
        )
    )


def _usage(input_tokens: int, output_tokens: int) -> dict[str, object]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "input_token_details": {
            "cached_tokens": 2,
            "text_tokens": input_tokens,
            "audio_tokens": 0,
        },
        "output_token_details": {
            "text_tokens": output_tokens,
            "audio_tokens": 0,
        },
    }


def _text_response_events(
    *,
    response_id: str,
    text: str,
    input_tokens: int = 10,
    output_tokens: int = 4,
) -> list[dict[str, object]]:
    return [
        {
            "type": "response.created",
            "response": {"id": response_id},
        },
        {
            "type": "response.output_text.delta",
            "response_id": response_id,
            "delta": text,
        },
        {
            "type": "response.done",
            "response": {
                "id": response_id,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                ],
                "usage": _usage(input_tokens, output_tokens),
            },
        },
    ]


def _canvas_response_events(
    *,
    arguments: str,
    preamble: str = "I’ll show that clearly. ",
) -> list[dict[str, object]]:
    midpoint = len(arguments) // 2
    return [
        {
            "type": "response.created",
            "response": {"id": RESPONSE_ID},
        },
        {
            "type": "response.output_text.delta",
            "response_id": RESPONSE_ID,
            "delta": preamble,
        },
        {
            "type": "response.output_item.added",
            "response_id": RESPONSE_ID,
            "item": {
                "type": "function_call",
                "name": INTERACTIVE_CANVAS_TOOL_NAME,
                "call_id": FUNCTION_CALL_ID,
                "arguments": "",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "response_id": RESPONSE_ID,
            "call_id": FUNCTION_CALL_ID,
            "delta": arguments[:midpoint],
        },
        {
            "type": "response.function_call_arguments.delta",
            "response_id": RESPONSE_ID,
            "call_id": FUNCTION_CALL_ID,
            "delta": arguments[midpoint:],
        },
        {
            "type": "response.function_call_arguments.done",
            "response_id": RESPONSE_ID,
            "name": INTERACTIVE_CANVAS_TOOL_NAME,
            "call_id": FUNCTION_CALL_ID,
            "arguments": arguments,
        },
        {
            "type": "response.done",
            "response": {
                "id": RESPONSE_ID,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": preamble}],
                    },
                    {
                        "type": "function_call",
                        "name": INTERACTIVE_CANVAS_TOOL_NAME,
                        "call_id": FUNCTION_CALL_ID,
                        "arguments": arguments,
                    },
                ],
                "usage": _usage(10, 4),
            },
        },
    ]


def _channel(
    events: list[dict[str, object]],
) -> tuple[
    _OpenAIRealtimeEventChannel,
    _FakeRealtimeSocket,
]:
    socket = _FakeRealtimeSocket(
        [{"type": "session.updated", "session": {"id": "sess_test"}}, *events]
    )
    channel = _OpenAIRealtimeEventChannel(
        socket,
        model="gpt-realtime-test",
        max_output_tokens=800,
        turn_timeout_seconds=5,
    )
    _TEST_CHANNELS.add(channel)
    return channel, socket


async def _collect_turn_events(
    channel: _OpenAIRealtimeEventChannel,
    *,
    turn_id: str,
) -> list[object]:
    return [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id=turn_id,
        )
    ]


@pytest.mark.asyncio
async def test_session_receiver_retains_more_than_websocket_queue_during_playback() -> (
    None
):
    events = [
        {"type": "session.updated", "session": {"id": "sess_test"}},
        *({"type": "rate_limits.updated"} for _ in range(40)),
    ]
    socket = _FakeRealtimeSocket(events)
    channel = _OpenAIRealtimeEventChannel(
        socket,
        model="gpt-realtime-test",
        max_output_tokens=800,
        turn_timeout_seconds=5,
    )

    await channel.start_receiver()
    await socket.wait_received(41)

    assert channel._receiver_error is None
    assert channel._router_events.qsize() == 41
    await channel.close()


@pytest.mark.asyncio
async def test_session_receiver_continues_across_idle_keepalive_windows() -> None:
    socket = _FakeRealtimeSocket(
        [{"type": "session.updated", "session": {"id": "sess_test"}}]
    )
    channel = _OpenAIRealtimeEventChannel(
        socket,
        model="gpt-realtime-test",
        max_output_tokens=800,
        turn_timeout_seconds=5,
    )

    await channel.start_receiver()
    await socket.wait_received(1)
    for index in range(3):
        socket.push({"type": "rate_limits.updated", "sequence": index})
        await socket.wait_received(index + 2)

    assert channel._receiver_error is None
    assert channel._receiver_task is not None
    assert not channel._receiver_task.done()
    await channel.close()


@pytest.mark.asyncio
async def test_pending_input_is_admitted_by_item_id_at_next_turn_boundary() -> None:
    channel, socket = _channel(
        [
            {
                "type": "input_audio_buffer.committed",
                "item_id": "item-barge-in",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-barge-in",
                "transcript": "What happens next?",
            },
        ]
    )
    await channel.start_receiver()
    await socket.wait_received(3)

    assert await channel.child_transcript_for_turn("turn-a") is None
    admitted = await channel.admit_pending_input_items(turn_id="turn-b")

    assert admitted == ("item-barge-in",)
    assert await channel.child_transcript_for_turn("turn-b") == "What happens next?"
    assert await channel.child_transcript_for_turn("turn-a") is None
    await channel.close()


@pytest.mark.asyncio
async def test_originating_and_barge_in_items_keep_distinct_turn_ownership() -> None:
    channel, _ = _channel(
        [
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "item-origin-a",
                "audio_start_ms": 100,
            },
            {
                "type": "input_audio_buffer.committed",
                "item_id": "item-origin-a",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-origin-a",
                "transcript": "Tell me about stars.",
            },
            {
                "type": "response.created",
                "response": {"id": RESPONSE_ID},
            },
            {
                "type": "response.output_text.delta",
                "response_id": RESPONSE_ID,
                "delta": "Stars are glowing suns.",
            },
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "item-barge-b",
                "audio_start_ms": 200,
            },
            {
                "type": "input_audio_buffer.committed",
                "item_id": "item-barge-b",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-barge-b",
                "transcript": "What about the moon?",
            },
            {
                "type": "response.done",
                "response": {
                    "id": RESPONSE_ID,
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Stars are glowing suns.",
                                }
                            ],
                        }
                    ],
                    "usage": _usage(10, 4),
                },
            },
        ]
    )

    received = await _collect_turn_events(channel, turn_id="turn-a")

    assert received == [RealtimeSpeechTextDelta(delta="Stars are glowing suns.")]
    assert channel.input_item_owner(item_id="item-origin-a") == "turn-a"
    assert channel.input_item_owner(item_id="item-barge-b") is None
    assert await channel.child_transcript_for_turn("turn-a") == "Tell me about stars."

    admitted = await channel.admit_pending_input_items(turn_id="turn-b")

    assert admitted == ("item-barge-b",)
    assert channel.input_item_owner(item_id="item-barge-b") == "turn-b"
    assert await channel.child_transcript_for_turn("turn-b") == (
        "What about the moon?"
    )
    assert await channel.child_transcript_for_turn("turn-a") == "Tell me about stars."
    await channel.close()


@pytest.mark.asyncio
async def test_duplicate_transcription_events_are_resolved_by_item_id() -> None:
    channel, _ = _channel(
        [
            {
                "type": "input_audio_buffer.committed",
                "item_id": "item-delayed",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-delayed",
                "transcript": "Tell me about rain.",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-delayed",
                "transcript": "Tell me about rain.",
            },
            *_text_response_events(response_id=RESPONSE_ID, text="Rain falls."),
        ]
    )

    await _collect_turn_events(channel, turn_id="turn-item-id")
    assert await channel.child_transcript_for_turn("turn-item-id") == (
        "Tell me about rain."
    )
    await channel.close()


@pytest.mark.asyncio
async def test_late_response_event_cannot_mutate_new_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.openai_realtime")
    channel, _ = _channel(
        [
            *_text_response_events(response_id=RESPONSE_ID, text="First."),
            {
                "type": "response.output_text.delta",
                "response_id": RESPONSE_ID,
                "delta": "late",
            },
            *_text_response_events(
                response_id=CONTINUATION_RESPONSE_ID,
                text="Second.",
            ),
        ]
    )
    await _collect_turn_events(channel, turn_id="turn-a")

    with pytest.raises(
        OpenAIRealtimeProtocolError,
        match="different Ava turn",
    ):
        await _collect_turn_events(channel, turn_id="turn-b")
    mismatch = next(
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_response_owner_mismatch")
    )
    assert mismatch == {
        "component": "realtimeProvider",
        "sidebandSequence": 5,
        "eventType": "response.output_text.delta",
        "responseId": RESPONSE_ID,
        "responseOwnerTurnId": "turn-a",
        "consumerTurnId": "turn-b",
        "consumerPhase": "turnResponse",
        "activeTurnId": "turn-b",
        "activeResponseId": None,
    }
    await channel.close()


@pytest.mark.asyncio
async def test_router_capacity_exhaustion_is_explicit_backpressure_failure() -> None:
    socket = _FakeRealtimeSocket(
        [{"type": "rate_limits.updated", "index": index} for index in range(9)]
    )
    channel = _OpenAIRealtimeEventChannel(
        socket,
        model="gpt-realtime-test",
        max_output_tokens=800,
        turn_timeout_seconds=5,
        max_router_events=8,
    )
    await channel.start_receiver()
    await socket.wait_received(9)
    assert isinstance(channel._receiver_error, OpenAIRealtimeRouterBackpressureError)
    assert not isinstance(channel._receiver_error, OpenAIRealtimeUnavailableError)

    for _ in range(8):
        await channel._receive_event(timeout_seconds=None)
    with pytest.raises(OpenAIRealtimeRouterBackpressureError):
        await channel._receive_event(timeout_seconds=None)
    await channel.close()


@pytest.mark.asyncio
async def test_transport_close_retains_close_code_reason_and_is_not_protocol_error() -> (
    None
):
    socket = _ClosingRealtimeSocket([])
    channel = _OpenAIRealtimeEventChannel(
        socket,
        model="gpt-realtime-test",
        max_output_tokens=800,
        turn_timeout_seconds=5,
    )
    await channel.start_receiver()

    with pytest.raises(OpenAIRealtimeConnectionClosedError) as caught:
        await channel._receive_event(timeout_seconds=None)
    assert caught.value.local_close is False
    assert caught.value.close_code == 1011
    assert caught.value.close_reason == "keepalive ping timeout"
    await channel.close()


def test_session_update_uses_text_output_and_one_optional_semantic_tool() -> None:
    payload = _session_update(_harness(), "gpt-realtime-test", 800)
    session = payload["session"]

    assert session["output_modalities"] == ["text"]
    assert session["tool_choice"] == "auto"
    assert [tool["name"] for tool in session["tools"]] == [INTERACTIVE_CANVAS_TOOL_NAME]
    turn_detection = session["audio"]["input"]["turn_detection"]
    assert session["audio"]["input"]["transcription"] == {"model": "gpt-transcribe"}
    assert turn_detection["create_response"] is True
    assert turn_detection["interrupt_response"] is True


@pytest.mark.asyncio
async def test_text_only_turn_streams_speech_without_function_output() -> None:
    channel, socket = _channel(
        _text_response_events(response_id=RESPONSE_ID, text="Hello there.")
    )
    events = [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id="turn-text",
        )
    ]

    assert events == [RealtimeSpeechTextDelta(delta="Hello there.")]
    assert channel.last_response_id == RESPONSE_ID
    assert channel.last_usage is not None
    assert channel.last_usage.total_tokens == 14
    assert not any(
        sent.get("item", {}).get("type") == "function_call_output"
        for sent in socket.sent
        if isinstance(sent.get("item"), dict)
    )


@pytest.mark.asyncio
async def test_one_channel_runs_multiple_turns_without_reinstalling_prompt() -> None:
    channel, socket = _channel(
        [
            *_text_response_events(
                response_id=RESPONSE_ID,
                text="Hello there.",
            ),
            *_text_response_events(
                response_id=CONTINUATION_RESPONSE_ID,
                text="The moon reflects sunlight.",
            ),
        ]
    )
    first = [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id="turn-one",
            text_input="Hello Ava.",
            response_max_output_tokens=400,
        )
    ]
    second = [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id="turn-two",
            text_input="Why does the moon shine?",
            response_max_output_tokens=400,
        )
    ]

    assert first == [RealtimeSpeechTextDelta(delta="Hello there.")]
    assert second == [RealtimeSpeechTextDelta(delta="The moon reflects sunlight.")]
    assert len([sent for sent in socket.sent if sent["type"] == "session.update"]) == 1
    assert len([sent for sent in socket.sent if sent["type"] == "response.create"]) == 2


@pytest.mark.asyncio
async def test_late_input_transcript_is_attributed_by_committed_item_id() -> None:
    channel, _ = _channel(
        [
            {"type": "input_audio_buffer.committed", "item_id": "item-child-one"},
            *_text_response_events(
                response_id=RESPONSE_ID,
                text="First answer.",
            ),
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-child-one",
                "transcript": "First child question?",
            },
            {"type": "input_audio_buffer.committed", "item_id": "item-child-two"},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-child-two",
                "transcript": "Second child question?",
            },
            *_text_response_events(
                response_id=CONTINUATION_RESPONSE_ID,
                text="Second answer.",
            ),
        ]
    )

    await _collect_turn_events(channel, turn_id="turn-one")
    assert await channel.child_transcript_for_turn("turn-one") == (
        "First child question?"
    )
    assert await channel.child_transcript_for_turn("turn-two") is None

    await _collect_turn_events(channel, turn_id="turn-two")

    assert await channel.child_transcript_for_turn("turn-one") == (
        "First child question?"
    )
    assert await channel.child_transcript_for_turn("turn-two") == (
        "Second child question?"
    )


@pytest.mark.asyncio
async def test_input_transcription_delta_is_accepted_for_committed_item() -> None:
    channel, _ = _channel(
        [
            {"type": "input_audio_buffer.committed", "item_id": "item-child"},
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": "item-child",
                "delta": "Hello",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-child",
                "transcript": "Hello Ava.",
            },
            *_text_response_events(
                response_id=RESPONSE_ID,
                text="Hello there.",
            ),
        ]
    )

    received = await _collect_turn_events(channel, turn_id="turn-delta")

    assert received == [RealtimeSpeechTextDelta(delta="Hello there.")]
    assert await channel.child_transcript_for_turn("turn-delta") == "Hello Ava."


@pytest.mark.asyncio
async def test_empty_completed_input_transcription_does_not_invalidate_speech() -> None:
    channel, _ = _channel(
        [
            {"type": "input_audio_buffer.committed", "item_id": "item-empty"},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-empty",
                "transcript": "",
            },
            *_text_response_events(
                response_id=RESPONSE_ID,
                text="I can still answer the audio the model understood.",
            ),
            {"type": "session.updated", "session": {"id": "sess_test"}},
        ]
    )

    received = await _collect_turn_events(channel, turn_id="turn-empty")

    assert received == [
        RealtimeSpeechTextDelta(
            delta="I can still answer the audio the model understood."
        )
    ]
    assert await channel.child_transcript_for_turn("turn-empty") is None


@pytest.mark.asyncio
async def test_duplicate_completed_input_transcription_is_idempotent_by_item() -> None:
    completed = {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": "item-duplicate",
        "transcript": "Hello Ava.",
    }
    channel, _ = _channel(
        [
            {
                "type": "input_audio_buffer.committed",
                "item_id": "item-duplicate",
            },
            completed,
            completed,
            *_text_response_events(
                response_id=RESPONSE_ID,
                text="Hello there.",
            ),
        ]
    )

    received = await _collect_turn_events(channel, turn_id="turn-duplicate")

    assert received == [RealtimeSpeechTextDelta(delta="Hello there.")]
    assert await channel.child_transcript_for_turn("turn-duplicate") == "Hello Ava."


@pytest.mark.asyncio
async def test_conflicting_duplicate_input_transcription_fails_loudly() -> None:
    channel, _ = _channel(
        [
            {
                "type": "input_audio_buffer.committed",
                "item_id": "item-conflict",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-conflict",
                "transcript": "Hello Ava.",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-conflict",
                "transcript": "Different text.",
            },
        ]
    )

    with pytest.raises(
        OpenAIRealtimeProtocolError,
        match="changed after its terminal event",
    ):
        await _collect_turn_events(channel, turn_id="turn-conflict")


@pytest.mark.asyncio
async def test_transcription_progress_drain_reaches_late_completed_event() -> None:
    channel, _ = _channel(
        [
            {"type": "input_audio_buffer.committed", "item_id": "item-child"},
            *_text_response_events(
                response_id=RESPONSE_ID,
                text="I heard you.",
            ),
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": "item-child",
                "delta": "Late",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-child",
                "transcript": "Late transcript.",
            },
        ]
    )

    await _collect_turn_events(channel, turn_id="turn-late-delta")

    assert await channel.child_transcript_for_turn("turn-late-delta") == (
        "Late transcript."
    )


@pytest.mark.asyncio
async def test_owned_transcription_terminal_wakes_deferred_memory_waiter() -> None:
    channel, socket = _channel(
        [
            {"type": "input_audio_buffer.committed", "item_id": "item-memory"},
            *_text_response_events(
                response_id=RESPONSE_ID,
                text="I will remember the delivered reply.",
            ),
        ]
    )
    await _collect_turn_events(channel, turn_id="turn-memory")
    assert channel.input_item_owner(item_id="item-memory") == "turn-memory"

    transcript = asyncio.create_task(
        channel.wait_for_child_transcript_for_turn("turn-memory")
    )
    socket.push(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item-memory",
            "transcript": "Please remember this question.",
        }
    )

    assert await transcript == "Please remember this question."
    assert await channel.wait_for_child_transcript_for_turn("turn-memory") == (
        "Please remember this question."
    )


@pytest.mark.asyncio
async def test_owned_transcription_wait_after_receiver_terminal_does_not_hang() -> (
    None
):
    socket = _ControlledClosingRealtimeSocket(
        [{"type": "input_audio_buffer.committed", "item_id": "item-terminal"}]
    )
    channel = _OpenAIRealtimeEventChannel(
        socket,
        model="gpt-realtime-test",
        max_output_tokens=800,
        turn_timeout_seconds=5,
    )
    _TEST_CHANNELS.add(channel)
    await channel.start_receiver()
    await socket.wait_received(1)
    await channel.admit_pending_input_items(turn_id="turn-terminal")
    assert channel.input_item_owner(item_id="item-terminal") == "turn-terminal"

    await socket.receiver_waiting.wait()
    socket.terminate_receiver.set()
    assert channel._receiver_task is not None
    await channel._receiver_task

    with pytest.raises(OpenAIRealtimeConnectionClosedError):
        await asyncio.wait_for(
            channel.wait_for_child_transcript_for_turn("turn-terminal"),
            timeout=0.05,
        )


@pytest.mark.asyncio
async def test_receiver_terminal_immediately_after_wait_registration_wakes_waiter() -> (
    None
):
    socket = _ControlledClosingRealtimeSocket(
        [{"type": "input_audio_buffer.committed", "item_id": "item-racing"}]
    )
    channel = _OpenAIRealtimeEventChannel(
        socket,
        model="gpt-realtime-test",
        max_output_tokens=800,
        turn_timeout_seconds=5,
    )
    _TEST_CHANNELS.add(channel)
    await channel.start_receiver()
    await socket.wait_received(1)
    await channel.admit_pending_input_items(turn_id="turn-racing")
    await socket.receiver_waiting.wait()

    waiter = asyncio.create_task(
        channel.wait_for_child_transcript_for_turn("turn-racing")
    )
    await asyncio.sleep(0)
    assert "turn-racing" in channel._child_transcription_events_by_turn
    socket.terminate_receiver.set()

    with pytest.raises(OpenAIRealtimeConnectionClosedError):
        await waiter


@pytest.mark.asyncio
async def test_transcription_segment_is_accepted_for_committed_item() -> None:
    channel, _ = _channel(
        [
            {"type": "input_audio_buffer.committed", "item_id": "item-child"},
            {
                "type": "conversation.item.input_audio_transcription.segment",
                "item_id": "item-child",
                "id": "segment-child-1",
                "speaker": "speaker-1",
                "text": "Hello Ava.",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-child",
                "transcript": "Hello Ava.",
            },
            *_text_response_events(
                response_id=RESPONSE_ID,
                text="Hello there.",
            ),
        ]
    )

    await _collect_turn_events(channel, turn_id="turn-segment")

    assert await channel.child_transcript_for_turn("turn-segment") == "Hello Ava."


@pytest.mark.asyncio
async def test_transcription_delta_without_committed_owner_fails_loudly() -> None:
    channel, _ = _channel(
        [
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": "item-unowned",
                "delta": "Hello",
            }
        ]
    )

    with pytest.raises(
        OpenAIRealtimeProtocolError,
        match="progress has no committed item owner",
    ):
        await _collect_turn_events(channel, turn_id="turn-unowned-delta")


@pytest.mark.asyncio
async def test_cancel_preserves_transcription_progress_for_its_committed_item() -> None:
    channel, socket = _channel(
        [
            {
                "type": "input_audio_buffer.committed",
                "item_id": "item-cancelled",
            },
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": "item-cancelled",
                "delta": "Interrupted",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-cancelled",
                "transcript": "Interrupted question.",
            },
            {
                "type": "response.done",
                "response": {
                    "id": RESPONSE_ID,
                    "status": "cancelled",
                    "output": [],
                    "usage": _usage(3, 0),
                },
            },
            *_text_response_events(
                response_id=CONTINUATION_RESPONSE_ID,
                text="We can continue.",
            ),
        ]
    )
    await channel.prepare(harness=_harness())
    channel._active_turn_id = "turn-cancelled"
    channel._active_response_id = RESPONSE_ID
    channel._bind_committed_input_item(
        {"type": "input_audio_buffer.committed", "item_id": "item-cancelled"},
        turn_id="turn-cancelled",
    )

    await channel.cancel(turn_id="turn-cancelled")
    received = await _collect_turn_events(channel, turn_id="turn-after-cancel")

    assert received == [RealtimeSpeechTextDelta(delta="We can continue.")]
    assert await channel.child_transcript_for_turn("turn-cancelled") == (
        "Interrupted question."
    )
    assert [event["type"] for event in socket.sent].count("response.cancel") == 1


@pytest.mark.asyncio
async def test_cancel_drain_attributes_known_and_barge_in_items_by_item_id() -> None:
    channel, _ = _channel(
        [
            {
                "type": "input_audio_buffer.committed",
                "item_id": "item-before-cancel",
            },
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": "item-before-cancel",
                "delta": "Earlier",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-before-cancel",
                "transcript": "Earlier question.",
            },
            {
                "type": "input_audio_buffer.committed",
                "item_id": "item-barge-in",
            },
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": "item-barge-in",
                "delta": "Interrupted",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-barge-in",
                "transcript": "Interrupted question.",
            },
            {
                "type": "response.done",
                "response": {
                    "id": RESPONSE_ID,
                    "status": "cancelled",
                    "output": [],
                    "usage": _usage(3, 0),
                },
            },
            *_text_response_events(
                response_id=CONTINUATION_RESPONSE_ID,
                text="The next turn remains separate.",
            ),
        ]
    )
    await channel.prepare(harness=_harness())
    channel._active_turn_id = "turn-cancelled"
    channel._active_response_id = RESPONSE_ID
    channel._bind_committed_input_item(
        {
            "type": "input_audio_buffer.committed",
            "item_id": "item-before-cancel",
        },
        turn_id="turn-cancelled",
    )

    await channel.cancel(turn_id="turn-cancelled")
    received = await _collect_turn_events(channel, turn_id="turn-after-cancel")

    assert received == [
        RealtimeSpeechTextDelta(delta="The next turn remains separate.")
    ]
    assert channel._child_transcripts_by_turn == {
        "turn-cancelled": "Earlier question.",
        "turn-after-cancel": "Interrupted question.",
    }


@pytest.mark.asyncio
async def test_silent_live_listening_does_not_consume_response_deadline() -> None:
    socket = _DelayedRealtimeSocket(
        [
            {"type": "session.updated", "session": {"id": "sess_test"}},
            *_text_response_events(
                response_id=RESPONSE_ID,
                text="I heard you after the quiet pause.",
            ),
        ],
        delayed_receive_index=1,
        delay_seconds=0.03,
    )
    channel = _OpenAIRealtimeEventChannel(
        socket,
        model="gpt-realtime-test",
        max_output_tokens=800,
        turn_timeout_seconds=0.01,
    )

    received = await asyncio.wait_for(
        _collect_turn_events(channel, turn_id="turn-after-silence"),
        timeout=0.2,
    )

    assert received == [
        RealtimeSpeechTextDelta(delta="I heard you after the quiet pause.")
    ]
    await channel.close()


@pytest.mark.asyncio
async def test_active_response_still_has_a_bounded_event_deadline() -> None:
    socket = _DelayedRealtimeSocket(
        [
            {"type": "session.updated", "session": {"id": "sess_test"}},
            {"type": "input_audio_buffer.speech_stopped"},
        ],
        delayed_receive_index=-1,
        delay_seconds=0,
        hang_when_empty=True,
    )
    channel = _OpenAIRealtimeEventChannel(
        socket,
        model="gpt-realtime-test",
        max_output_tokens=800,
        turn_timeout_seconds=0.01,
    )

    with pytest.raises(
        OpenAIRealtimeUnavailableError,
        match="active-response event receive timed out",
    ):
        await asyncio.wait_for(
            _collect_turn_events(channel, turn_id="turn-response-timeout"),
            timeout=0.2,
        )
    await channel.close()


def test_live_matrix_request_is_strictly_bounded() -> None:
    request = OpenAIRealtimeProbeRequest(
        turn_id="turn-matrix",
        text_input="Hello Ava.",
        max_output_tokens=400,
    )
    assert request.max_output_tokens == 400
    with pytest.raises(ValueError, match="1-2000"):
        OpenAIRealtimeProbeRequest(
            turn_id="turn-empty",
            text_input=" ",
            max_output_tokens=400,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["new", "reviseFocused"])
async def test_canvas_call_gets_one_output_then_tool_disabled_continuation(
    operation: str,
) -> None:
    arguments = json.dumps(
        {
            "operation": operation,
            "brief": "Show a labeled water cycle with a clear loop.",
            "interactionMode": "explore",
            "interactionGoal": "Tap the cloud to reveal precipitation.",
        },
        separators=(",", ":"),
    )
    events = [
        *_canvas_response_events(arguments=arguments),
        *_text_response_events(
            response_id=CONTINUATION_RESPONSE_ID,
            text="Tap the cloud when you’re ready.",
            input_tokens=6,
            output_tokens=3,
        ),
    ]
    channel, socket = _channel(events)

    received = []
    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-canvas",
    ):
        received.append(event)
        if isinstance(event, RealtimeCanvasToolCall):
            await channel.complete_canvas_tool(
                turn_id="turn-canvas",
                call_id=event.call_id,
                output=InteractiveCanvasToolOutput.model_validate(
                    {
                        "status": "ready",
                        "sceneId": "water_cycle",
                        "revision": 1,
                        "summary": ("A labeled interactive water-cycle loop is ready."),
                    }
                ),
            )

    assert [type(event) for event in received] == [
        RealtimeSpeechTextDelta,
        RealtimeCanvasToolCall,
        RealtimeSpeechTextDelta,
    ]
    call = received[1]
    assert isinstance(call, RealtimeCanvasToolCall)
    assert call.request.operation == operation
    assert call.request.brief.startswith("Show a labeled water cycle")
    function_outputs = [
        sent
        for sent in socket.sent
        if isinstance(sent.get("item"), dict)
        and sent["item"].get("type") == "function_call_output"
    ]
    assert len(function_outputs) == 1
    assert function_outputs[0]["item"]["call_id"] == FUNCTION_CALL_ID
    continuation_requests = [
        sent for sent in socket.sent if sent.get("type") == "response.create"
    ]
    assert continuation_requests[-1]["response"]["tool_choice"] == "none"
    assert channel.last_usage is not None
    assert channel.last_usage.total_tokens == 23


@pytest.mark.asyncio
async def test_idle_canvas_interaction_can_trigger_existing_semantic_turn() -> None:
    channel, socket = _channel([])
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-canvas-interaction",
    ).__aiter__()
    pending_event = asyncio.create_task(iterator.__anext__())
    await socket.wait_received(1)

    accepted = await channel.request_canvas_interaction_response(
        turn_id="turn-canvas-interaction",
        interaction=_recorded_interaction(),
    )

    assert accepted is True
    interaction_requests = [
        sent for sent in socket.sent if sent.get("type") == "response.create"
    ]
    assert len(interaction_requests) == 1
    response = interaction_requests[0]["response"]
    assert response["output_modalities"] == ["text"]
    assert response["tool_choice"] == "none"
    assert response["max_output_tokens"] == 128
    assert "no Markdown" in response["instructions"]
    assert 'scene_id="scene_jupiter"' in response["instructions"]
    assert 'scene_revision="3"' in response["instructions"]
    assert 'interaction_revision="1"' in response["instructions"]
    assert 'mode="explore"' in response["instructions"]
    assert 'binding_id="explore_great_red_spot"' in response["instructions"]
    assert 'target_id="target_explore_great_red_spot"' in response["instructions"]
    assert "<target>Great Red Spot</target>" in response["instructions"]
    assert "latest validated canvas interaction" not in response["instructions"]
    assert not any(
        sent.get("type") == "conversation.item.create"
        and isinstance(sent.get("item"), dict)
        and sent["item"].get("role") == "user"
        for sent in socket.sent
    )

    for event in _text_response_events(
        response_id="resp_canvas_interaction",
        text="The nucleus holds the cell's DNA.",
    ):
        socket.push(event)
    assert await pending_event == RealtimeSpeechTextDelta(
        delta="The nucleus holds the cell's DNA."
    )
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()


@pytest.mark.asyncio
async def test_canvas_interaction_response_owns_each_exact_target_instruction() -> None:
    interactions = (
        _recorded_interaction(),
        _recorded_interaction(
            binding_id="select_europa",
            accessibility_label="Europa",
            interaction_mode="select",
            interaction_revision=2,
        ),
    )
    instructions: list[str] = []

    for index, interaction in enumerate(interactions, start=1):
        channel, socket = _channel([])
        turn_id = f"turn-exact-target-{index}"
        iterator = channel.stream_turn(
            harness=_harness(),
            turn_id=turn_id,
        ).__aiter__()
        pending_event = asyncio.create_task(iterator.__anext__())
        await socket.wait_received(1)

        assert await channel.request_canvas_interaction_response(
            turn_id=turn_id,
            interaction=interaction,
        )
        response_create = next(
            sent for sent in socket.sent if sent.get("type") == "response.create"
        )
        response = response_create["response"]
        assert isinstance(response, dict)
        instruction = response["instructions"]
        assert isinstance(instruction, str)
        instructions.append(instruction)

        pending_event.cancel()
        await asyncio.gather(pending_event, return_exceptions=True)
        await iterator.aclose()

    assert "<target>Great Red Spot</target>" in instructions[0]
    assert "Europa" not in instructions[0]
    assert "<target>Europa</target>" in instructions[1]
    assert "Great Red Spot" not in instructions[1]
    assert 'interaction_revision="1"' in instructions[0]
    assert 'interaction_revision="2"' in instructions[1]
    assert instructions[0] != instructions[1]


@pytest.mark.asyncio
async def test_steps_interaction_response_owns_exact_step() -> None:
    channel, socket = _channel([])
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-exact-step",
    ).__aiter__()
    pending_event = asyncio.create_task(iterator.__anext__())
    await socket.wait_received(1)

    assert await channel.request_canvas_interaction_response(
        turn_id="turn-exact-step",
        interaction=_recorded_interaction(
            binding_id="step_moon_orbit",
            accessibility_label="Moon orbit",
            interaction_mode="steps",
            step_index=2,
        ),
    )
    response_create = next(
        sent for sent in socket.sent if sent.get("type") == "response.create"
    )
    response = response_create["response"]
    assert isinstance(response, dict)
    instruction = response["instructions"]
    assert isinstance(instruction, str)
    assert 'mode="steps"' in instruction
    assert 'step_index="2"' in instruction
    assert "<target>Moon orbit</target>" in instruction
    assert "Back" not in instruction
    assert "Next" not in instruction

    pending_event.cancel()
    await asyncio.gather(pending_event, return_exceptions=True)
    await iterator.aclose()


@pytest.mark.asyncio
async def test_canvas_interaction_trigger_is_single_shot() -> None:
    channel, socket = _channel([])
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-canvas-single-shot",
    ).__aiter__()
    pending_event = asyncio.create_task(iterator.__anext__())
    await socket.wait_received(1)

    assert await channel.request_canvas_interaction_response(
        turn_id="turn-canvas-single-shot",
        interaction=_recorded_interaction(),
    )
    assert not await channel.request_canvas_interaction_response(
        turn_id="turn-canvas-single-shot",
        interaction=_recorded_interaction(),
    )
    assert len(
        [sent for sent in socket.sent if sent.get("type") == "response.create"]
    ) == 1

    for event in _text_response_events(
        response_id="resp_canvas_single_shot",
        text="That is the selected part.",
    ):
        socket.push(event)
    assert isinstance(await pending_event, RealtimeSpeechTextDelta)
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()


@pytest.mark.asyncio
async def test_canvas_interaction_response_cannot_call_canvas_tool() -> None:
    channel, socket = _channel([])
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-canvas-no-tools",
    ).__aiter__()
    pending_event = asyncio.create_task(iterator.__anext__())
    await socket.wait_received(1)
    assert await channel.request_canvas_interaction_response(
        turn_id="turn-canvas-no-tools",
        interaction=_recorded_interaction(),
    )

    for event in _canvas_response_events(
        arguments=(
            '{"operation":"new","brief":"Show a cell.",'
            '"interactionMode":"none","interactionGoal":null}'
        ),
        preamble="",
    ):
        socket.push(event)

    with pytest.raises(
        OpenAIRealtimeProtocolError,
        match="interaction response attempted a tool call",
    ):
        await pending_event
    await channel.cancel(turn_id="turn-canvas-no-tools")


@pytest.mark.asyncio
async def test_child_input_observed_first_suppresses_canvas_interaction_response() -> None:
    channel, socket = _channel([])
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-child-wins",
    ).__aiter__()
    pending_event = asyncio.create_task(iterator.__anext__())
    await socket.wait_received(1)
    socket.push(
        {
            "type": "input_audio_buffer.speech_started",
            "item_id": "item_child_wins",
            "audio_start_ms": 120,
        }
    )
    await socket.wait_received(2)

    assert not await channel.request_canvas_interaction_response(
        turn_id="turn-child-wins",
        interaction=_recorded_interaction(),
    )
    assert not any(sent.get("type") == "response.create" for sent in socket.sent)

    pending_event.cancel()
    await asyncio.gather(pending_event, return_exceptions=True)
    await iterator.aclose()


@pytest.mark.asyncio
async def test_already_owned_child_input_suppresses_interaction_response() -> None:
    channel, socket = _channel(
        [
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "item_owned_child",
                "audio_start_ms": 120,
            }
        ]
    )
    await channel.start_receiver()
    await socket.wait_received(2)
    assert await channel.admit_pending_input_items(
        turn_id="turn-owned-child"
    ) == ("item_owned_child",)
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-owned-child",
    ).__aiter__()
    pending_event = asyncio.create_task(iterator.__anext__())
    await socket.wait_sent(1)

    assert not await channel.request_canvas_interaction_response(
        turn_id="turn-owned-child",
        interaction=_recorded_interaction(),
    )
    assert not any(sent.get("type") == "response.create" for sent in socket.sent)

    pending_event.cancel()
    await asyncio.gather(pending_event, return_exceptions=True)
    await iterator.aclose()


@pytest.mark.asyncio
async def test_active_normal_response_suppresses_canvas_interaction_response() -> None:
    channel, socket = _channel([])
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-normal-response-active",
    ).__aiter__()
    pending_event = asyncio.create_task(iterator.__anext__())
    await socket.wait_received(1)
    response_events = _text_response_events(
        response_id="resp_normal_active",
        text="I heard you.",
    )
    socket.push(response_events[0])
    socket.push(response_events[1])
    assert isinstance(await pending_event, RealtimeSpeechTextDelta)

    assert not await channel.request_canvas_interaction_response(
        turn_id="turn-normal-response-active",
        interaction=_recorded_interaction(),
    )
    assert not any(sent.get("type") == "response.create" for sent in socket.sent)

    socket.push(response_events[2])
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        '{"brief":"Show a water cycle."}',
        '{"operation":"revise","brief":"Show a water cycle."}',
        '{"operation":"new","brief":42}',
    ],
)
async def test_invalid_canvas_arguments_are_rejected_without_killing_speech(
    arguments: str,
) -> None:
    channel, socket = _channel(
        [
            *_canvas_response_events(arguments=arguments, preamble="One moment. "),
            *_text_response_events(
                response_id=CONTINUATION_RESPONSE_ID,
                text="I can still explain it with words.",
            ),
        ]
    )

    received = []
    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-invalid",
    ):
        received.append(event)
        if isinstance(event, RealtimeRejectedCanvasToolCall):
            await channel.complete_canvas_tool(
                turn_id="turn-invalid",
                call_id=event.call_id,
                output=InteractiveCanvasToolOutput(
                    status="rejected",
                    reason=event.reason,
                ),
            )

    assert [type(event) for event in received] == [
        RealtimeSpeechTextDelta,
        RealtimeRejectedCanvasToolCall,
        RealtimeSpeechTextDelta,
    ]
    function_outputs = [
        sent
        for sent in socket.sent
        if isinstance(sent.get("item"), dict)
        and sent["item"].get("type") == "function_call_output"
    ]
    assert len(function_outputs) == 1
    assert json.loads(function_outputs[0]["item"]["output"])["status"] == "rejected"


@pytest.mark.asyncio
async def test_canvas_function_must_be_completed_exactly_once() -> None:
    arguments = (
        '{"operation":"new","brief":"Show the moon phases in order.",'
        '"interactionMode":"none"}'
    )
    channel, _ = _channel(_canvas_response_events(arguments=arguments))
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-once",
    ).__aiter__()

    assert isinstance(await iterator.__anext__(), RealtimeSpeechTextDelta)
    call = await iterator.__anext__()
    assert isinstance(call, RealtimeCanvasToolCall)
    output = InteractiveCanvasToolOutput(
        status="rejected",
        reason="The scene did not pass validation.",
    )
    await channel.complete_canvas_tool(
        turn_id="turn-once",
        call_id=call.call_id,
        output=output,
    )
    with pytest.raises(OpenAIRealtimeProtocolError, match="completed twice"):
        await channel.complete_canvas_tool(
            turn_id="turn-once",
            call_id=call.call_id,
            output=output,
        )
    await iterator.aclose()


@pytest.mark.asyncio
async def test_cancelling_pending_canvas_returns_rejection_without_continuation() -> (
    None
):
    arguments = (
        '{"operation":"new","brief":"Show a simple solar-system orbit.",'
        '"interactionMode":"none"}'
    )
    channel, socket = _channel(_canvas_response_events(arguments=arguments))
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-cancel",
    ).__aiter__()

    await iterator.__anext__()
    assert isinstance(await iterator.__anext__(), RealtimeCanvasToolCall)
    await channel.cancel(turn_id="turn-cancel")
    await iterator.aclose()

    outputs = [
        sent
        for sent in socket.sent
        if isinstance(sent.get("item"), dict)
        and sent["item"].get("type") == "function_call_output"
    ]
    assert len(outputs) == 1
    assert json.loads(outputs[0]["item"]["output"])["status"] == "rejected"
    assert not any(
        sent.get("type") == "response.create"
        and sent.get("response", {}).get("tool_choice") == "none"
        for sent in socket.sent
        if isinstance(sent.get("response"), dict)
    )


@pytest.mark.asyncio
async def test_cancelled_canvas_continuation_cannot_escape_into_next_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.openai_realtime")
    arguments = (
        '{"operation":"new","brief":"Show a simple solar-system orbit.",'
        '"interactionMode":"none"}'
    )
    channel, socket = _channel(_canvas_response_events(arguments=arguments))
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-canvas-a",
    ).__aiter__()

    assert isinstance(await iterator.__anext__(), RealtimeSpeechTextDelta)
    call = await iterator.__anext__()
    assert isinstance(call, RealtimeCanvasToolCall)
    await channel.complete_canvas_tool(
        turn_id="turn-canvas-a",
        call_id=call.call_id,
        output=InteractiveCanvasToolOutput(
            status="rejected",
            reason="The optional visual was not available.",
        ),
    )

    socket.push(
        {
            "type": "response.created",
            "response": {"id": CONTINUATION_RESPONSE_ID},
        }
    )
    socket.push(
        {
            "type": "response.output_text.delta",
            "response_id": CONTINUATION_RESPONSE_ID,
            "delta": "I can still explain it with words.",
        }
    )
    assert await iterator.__anext__() == RealtimeSpeechTextDelta(
        delta="I can still explain it with words."
    )
    await iterator.aclose()

    # The provider can reject response.cancel after R2 has completed internally
    # while its authoritative response.done is still in transit on the sideband.
    # That terminal event still belongs to Turn A and must be drained by Turn A.
    socket.push(
        {
            "type": "error",
            "error": {
                "code": "response_cancel_not_active",
                "message": "The continuation completed before cancellation.",
            },
        }
    )
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": CONTINUATION_RESPONSE_ID,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "I can still explain it with words.",
                            }
                        ],
                    }
                ],
                "usage": _usage(5, 3),
            },
        }
    )
    socket.push(
        {
            "type": "response.created",
            "response": {"id": "resp_turn_b"},
        }
    )
    socket.push(
        {
            "type": "response.output_text.delta",
            "response_id": "resp_turn_b",
            "delta": "Turn B remains healthy.",
        }
    )
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp_turn_b",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Turn B remains healthy.",
                            }
                        ],
                    }
                ],
                "usage": _usage(4, 2),
            },
        }
    )

    await channel.cancel(turn_id="turn-canvas-a")
    received = await _collect_turn_events(channel, turn_id="turn-b")

    assert received == [RealtimeSpeechTextDelta(delta="Turn B remains healthy.")]
    assert channel.last_response_id == "resp_turn_b"

    observed = [
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_response_event_observed")
    ]
    assert [
        (
            context["sidebandSequence"],
            context["eventType"],
            context["responseId"],
        )
        for context in observed
    ] == [
        (2, "response.created", RESPONSE_ID),
        (4, "response.output_item.added", RESPONSE_ID),
        (7, "response.function_call_arguments.done", RESPONSE_ID),
        (8, "response.done", RESPONSE_ID),
        (9, "response.created", CONTINUATION_RESPONSE_ID),
        (12, "response.done", CONTINUATION_RESPONSE_ID),
        (13, "response.created", "resp_turn_b"),
        (15, "response.done", "resp_turn_b"),
    ]

    owner_admissions = [
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_response_owner_admitted")
    ]
    assert [
        (
            context["sidebandSequence"],
            context["responseId"],
            context["responseOwnerTurnId"],
            context["consumerTurnId"],
            context["consumerPhase"],
        )
        for context in owner_admissions
    ] == [
        (2, RESPONSE_ID, "turn-canvas-a", "turn-canvas-a", "turnResponse"),
        (
            9,
            CONTINUATION_RESPONSE_ID,
            "turn-canvas-a",
            "turn-canvas-a",
            "canvasContinuation",
        ),
        (13, "resp_turn_b", "turn-b", "turn-b", "turnResponse"),
    ]

    cancel_wait = next(
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith(
            "openai_realtime_cancel_not_active_waiting_for_terminal"
        )
    )
    assert cancel_wait == {
        "component": "realtimeProvider",
        "sidebandSequence": 11,
        "eventType": "error",
        "responseId": CONTINUATION_RESPONSE_ID,
        "responseOwnerTurnId": "turn-canvas-a",
        "consumerTurnId": "turn-canvas-a",
        "consumerPhase": "cancellation",
        "activeTurnId": "turn-canvas-a",
        "activeResponseId": CONTINUATION_RESPONSE_ID,
    }
    drained = next(
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_response_drained")
    )
    assert drained["sidebandSequence"] == 12
    assert drained["responseId"] == CONTINUATION_RESPONSE_ID
    assert drained["responseOwnerTurnId"] == "turn-canvas-a"
    assert drained["consumerTurnId"] == "turn-canvas-a"


@pytest.mark.asyncio
async def test_cancel_before_response_id_is_known_settles_idempotently() -> None:
    channel, socket = _channel(
        [
            {
                "type": "error",
                "error": {
                    "code": "response_cancel_not_active",
                    "message": "Cancellation raced response completion.",
                },
            }
        ]
    )
    await channel.prepare(harness=_harness())
    channel._active_turn_id = "turn-cancel-before-created"

    await channel.cancel(turn_id="turn-cancel-before-created")
    await channel.cancel(turn_id="turn-cancel-before-created")

    assert [event["type"] for event in socket.sent].count("response.cancel") == 1
    assert channel._active_turn_id is None
    assert channel._active_response_id is None


@pytest.mark.asyncio
async def test_cancellation_during_pending_interaction_response_create_does_not_leak() -> (
    None
):
    channel, socket = _channel([])
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-interaction-a",
    ).__aiter__()
    pending_event = asyncio.create_task(iterator.__anext__())
    await socket.wait_sent(1)
    assert await channel.request_canvas_interaction_response(
        turn_id="turn-interaction-a",
        interaction=_recorded_interaction(),
    )
    pending_event.cancel()
    await asyncio.gather(pending_event, return_exceptions=True)
    await iterator.aclose()

    cancellation = asyncio.create_task(
        channel.cancel(turn_id="turn-interaction-a")
    )
    await socket.wait_sent(3)
    socket.push(
        {
            "type": "error",
            "error": {
                "code": "response_cancel_not_active",
                "message": "The requested response is not active yet.",
            },
        }
    )
    for event in _text_response_events(
        response_id="resp_interaction_a",
        text="This stale interaction speech belongs only to Turn A.",
    ):
        socket.push(event)
    await cancellation

    for event in _text_response_events(
        response_id="resp_child_b",
        text="This is the child's normal Turn B response.",
    ):
        socket.push(event)
    received = await _collect_turn_events(channel, turn_id="turn-child-b")

    assert received == [
        RealtimeSpeechTextDelta(
            delta="This is the child's normal Turn B response."
        )
    ]
    assert channel._response_owner_by_id["resp_interaction_a"] == (
        "turn-interaction-a"
    )
    assert channel._response_owner_by_id["resp_child_b"] == "turn-child-b"
    assert [event["type"] for event in socket.sent].count("response.cancel") == 2
    assert channel.last_response_id == "resp_child_b"


@pytest.mark.asyncio
async def test_child_vad_cancels_pending_interaction_before_next_child_response() -> (
    None
):
    channel, socket = _channel([])
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-interaction-vad-a",
    ).__aiter__()
    pending_event = asyncio.create_task(iterator.__anext__())
    await socket.wait_sent(1)
    assert await channel.request_canvas_interaction_response(
        turn_id="turn-interaction-vad-a",
        interaction=_recorded_interaction(),
    )

    socket.push(
        {
            "type": "input_audio_buffer.speech_started",
            "item_id": "item-child-vad-b",
            "audio_start_ms": 240,
        }
    )
    observed_input = await channel.next_input_event()
    assert observed_input.event_type == "input_audio_buffer.speech_started"
    assert observed_input.item_id == "item-child-vad-b"

    pending_event.cancel()
    await asyncio.gather(pending_event, return_exceptions=True)
    await iterator.aclose()
    cancellation = asyncio.create_task(
        channel.cancel(turn_id="turn-interaction-vad-a")
    )
    await socket.wait_sent(3)
    socket.push(
        {
            "type": "response.created",
            "response": {"id": "resp_interaction_vad_a"},
        }
    )
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp_interaction_vad_a",
                "status": "cancelled",
                "output": [],
                "usage": _usage(3, 0),
            },
        }
    )
    socket.push(
        {
            "type": "input_audio_buffer.speech_stopped",
            "item_id": "item-child-vad-b",
            "audio_end_ms": 860,
        }
    )
    socket.push(
        {
            "type": "input_audio_buffer.committed",
            "item_id": "item-child-vad-b",
        }
    )
    for event in _text_response_events(
        response_id="resp_child_vad_b",
        text="The child's response wins after the interruption.",
    ):
        socket.push(event)
    await socket.wait_received(9)
    await cancellation

    assert await channel.admit_pending_input_items(
        turn_id="turn-child-vad-b"
    ) == ("item-child-vad-b",)
    received = await _collect_turn_events(
        channel,
        turn_id="turn-child-vad-b",
    )

    assert received == [
        RealtimeSpeechTextDelta(
            delta="The child's response wins after the interruption."
        )
    ]
    assert channel._response_owner_by_id["resp_interaction_vad_a"] == (
        "turn-interaction-vad-a"
    )
    assert channel._response_owner_by_id["resp_child_vad_b"] == (
        "turn-child-vad-b"
    )
    assert channel.input_item_owner(item_id="item-child-vad-b") == (
        "turn-child-vad-b"
    )
    assert channel.last_response_id == "resp_child_vad_b"


@pytest.mark.asyncio
async def test_late_cancel_error_cannot_poison_the_next_turn() -> None:
    channel, socket = _channel(
        [
            {
                "type": "response.done",
                "response": {
                    "id": RESPONSE_ID,
                    "status": "completed",
                    "output": [],
                    "usage": _usage(3, 1),
                },
            },
            {
                "type": "error",
                "error": {
                    "code": "response_cancel_not_active",
                    "message": "The completed response is no longer active.",
                },
            },
            *_text_response_events(
                response_id=CONTINUATION_RESPONSE_ID,
                text="The next turn remains healthy.",
            ),
        ]
    )
    await channel.prepare(harness=_harness())
    channel._active_turn_id = "turn-completed-before-cancel"
    channel._active_response_id = RESPONSE_ID

    await channel.cancel(turn_id="turn-completed-before-cancel")
    received = [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id="turn-after-cancel-race",
            text_input="Can we continue?",
            response_max_output_tokens=400,
        )
    ]

    assert received == [RealtimeSpeechTextDelta(delta="The next turn remains healthy.")]
    assert channel.last_response_id == CONTINUATION_RESPONSE_ID
    assert [event["type"] for event in socket.sent].count("response.cancel") == 1


@pytest.mark.asyncio
async def test_live_response_text_delta_and_text_done_are_accepted() -> None:
    channel, _ = _channel(
        [
            {
                "type": "response.created",
                "response": {"id": RESPONSE_ID},
            },
            {
                "type": "response.output_item.added",
                "response_id": RESPONSE_ID,
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                },
            },
            {
                "type": "response.content_part.added",
                "response_id": RESPONSE_ID,
                "part": {"type": "text", "text": ""},
            },
            {
                "type": "response.text.delta",
                "response_id": RESPONSE_ID,
                "delta": "Live OpenAI delta.",
            },
            {
                "type": "response.text.done",
                "response_id": RESPONSE_ID,
                "text": "Live OpenAI delta.",
            },
            {
                "type": "response.content_part.done",
                "response_id": RESPONSE_ID,
            },
            {
                "type": "response.output_item.done",
                "response_id": RESPONSE_ID,
            },
            {
                "type": "response.done",
                "response": {
                    "id": RESPONSE_ID,
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Live OpenAI delta."}],
                        }
                    ],
                    "usage": _usage(10, 4),
                },
            },
        ]
    )
    events = [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id="turn-live-text",
        )
    ]

    assert events == [RealtimeSpeechTextDelta(delta="Live OpenAI delta.")]
    assert channel.last_response_id == RESPONSE_ID


@pytest.mark.asyncio
async def test_response_cancelled_by_turn_detected_completes_cleanly() -> None:
    channel, socket = _channel(
        [
            {
                "type": "response.created",
                "response": {"id": RESPONSE_ID, "status": "in_progress"},
            },
            {
                "type": "response.text.delta",
                "response_id": RESPONSE_ID,
                "delta": "Partial text before",
            },
            {
                "type": "response.done",
                "response": {
                    "id": RESPONSE_ID,
                    "status": "cancelled",
                    "status_details": {"type": "cancelled", "reason": "turn_detected"},
                    "output": [],
                    "usage": _usage(5, 2),
                },
            },
        ]
    )
    events = [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id="turn-cancelled",
        )
    ]

    assert events == [RealtimeSpeechTextDelta(delta="Partial text before")]
    assert channel.last_response_id == RESPONSE_ID
    assert channel.last_usage is not None
    assert channel.last_usage.total_tokens == 7
