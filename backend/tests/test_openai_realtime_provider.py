from __future__ import annotations

import asyncio
import json
from collections import deque

import pytest

from ava_backend.protocol import AgeBand
from ava_backend.providers.openai_realtime_provider import (
    OpenAIRealtimeProbeRequest,
    OpenAIRealtimeProtocolError,
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


class _FakeRealtimeSocket:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = deque(json.dumps(event) for event in events)
        self.sent: list[dict[str, object]] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        if not self.events:
            raise AssertionError("fake OpenAI socket exhausted its event fixture")
        return self.events.popleft()

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
    return (
        _OpenAIRealtimeEventChannel(
            socket,
            model="gpt-realtime-test",
            max_output_tokens=800,
            turn_timeout_seconds=5,
        ),
        socket,
    )


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


def test_session_update_uses_text_output_and_one_optional_semantic_tool() -> None:
    payload = _session_update(_harness(), "gpt-realtime-test", 800)
    session = payload["session"]

    assert session["output_modalities"] == ["text"]
    assert session["tool_choice"] == "auto"
    assert session["parallel_tool_calls"] is False
    assert [tool["name"] for tool in session["tools"]] == [INTERACTIVE_CANVAS_TOOL_NAME]
    turn_detection = session["audio"]["input"]["turn_detection"]
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
async def test_canvas_call_gets_one_output_then_tool_disabled_continuation() -> None:
    arguments = json.dumps(
        {
            "brief": "Show a labeled water cycle with a clear loop.",
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
async def test_invalid_canvas_arguments_are_rejected_without_killing_speech() -> None:
    channel, socket = _channel(
        [
            *_canvas_response_events(arguments='{"brief":42}', preamble="One moment. "),
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
    arguments = '{"brief":"Show the moon phases in order."}'
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
    arguments = '{"brief":"Show a simple solar-system orbit."}'
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
