from __future__ import annotations

import asyncio
from builtins import anext
import json
import logging
from collections import deque
from datetime import UTC, datetime
from types import MethodType

import httpx
import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from ava_backend.canvas.svg_workspace import RecordedSvgCanvasInteraction
from ava_backend.protocol import AgeBand
from ava_backend.providers.openai_realtime_provider import (
    OpenAIRealtimeProbeRequest,
    OpenAIRealtimeProtocolError,
    OpenAIRealtimeConnectionClosedError,
    OpenAIRealtimeProviderSession,
    OpenAIRealtimeRouterBackpressureError,
    OpenAIRealtimeUnavailableError,
    _OpenAIRealtimeEventChannel,
    _session_update,
)
from ava_backend.realtime.harness import build_realtime_harness
from ava_backend.realtime.context_assembler import (
    DynamicContextAssembler,
    DynamicContextBuildReason,
)
from ava_backend.realtime.fakes import DETERMINISTIC_AUDIO_OFFER_SDP
from ava_backend.realtime.memory import AuthoritativeMemorySnapshot
from ava_backend.realtime.provider_session import (
    RealtimeContextInstallationStatus,
    RealtimeNegotiationResponseError,
    RealtimeProviderResponseIncompleteError,
)
from ava_backend.realtime.tool_contract import (
    CANCEL_INTERACTIVE_CANVAS_TOOL_NAME,
    CANVAS_MODEL_SAFE_REJECTION_REASON,
    INTERACTIVE_CANVAS_TOOL_NAME,
    GET_CURRENT_TIME_TOOL_NAME,
    SEARCH_MEMORY_TOOL_NAME,
    GetCurrentTimeToolOutputV4,
    InteractiveCanvasToolOutput,
    InteractiveCanvasToolOutputV4,
    InteractiveCanvasCancellationToolOutputV4,
    RealtimeCancelCanvasToolCall,
    RealtimeCanvasToolCall,
    RealtimeRejectedCanvasToolCall,
    RealtimeGetCurrentTimeToolCall,
    RealtimeRejectedUtilityToolCall,
    RealtimeSearchMemoryToolCall,
    RejectedUtilityToolOutputV4,
    SearchMemoryToolOutputV4,
    RealtimeSpeechTextDelta,
)

RESPONSE_ID = "resp_test_live_0001"
CONTINUATION_RESPONSE_ID = "resp_test_live_0002"
FUNCTION_CALL_ID = "call_test_live_0001"
_TEST_CHANNELS: set[_OpenAIRealtimeEventChannel] = set()


@pytest.mark.asyncio
async def test_sdp_negotiation_failure_logs_content_free_provider_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"x-request-id": "req_realtime_negotiation_0001"},
            json={
                "error": {
                    "code": "rate_limit_exceeded",
                    "param": "model",
                    "message": "provider-response-body-must-not-be-logged",
                }
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAIRealtimeProviderSession(
            api_key="sk-test-secret-must-not-be-logged",
            model="gpt-realtime-test",
            safety_identifier="test_child_0001",
            connect_timeout_seconds=5,
            turn_timeout_seconds=30,
            max_output_tokens=512,
            http_client=client,
        )
        with caplog.at_level(
            logging.WARNING,
            logger="ava_backend.openai_realtime",
        ):
            with pytest.raises(RealtimeNegotiationResponseError):
                await provider.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)

    records = [
        record
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_negotiation_failed")
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "stage=sdp" in message
    assert "provider_status_code=429" in message
    assert "provider_error_code=rate_limit_exceeded" in message
    assert "provider_error_parameter=model" in message
    assert "provider_request_id=req_realtime_negotiation_0001" in message
    assert "provider-response-body-must-not-be-logged" not in caplog.text
    assert "sk-test-secret-must-not-be-logged" not in caplog.text
    assert DETERMINISTIC_AUDIO_OFFER_SDP not in caplog.text


def _recorded_interaction(
    *,
    binding_id: str = "explore_great_red_spot",
    target_id: str | None = None,
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
        target_id=target_id or f"target_{binding_id}",
        accessibility_label=accessibility_label,
        step_index=step_index,
    )


def _interaction_conversation_item_text(
    sent_events: list[dict[str, object]],
) -> str:
    user_items = [
        sent["item"]
        for sent in sent_events
        if sent.get("type") == "conversation.item.create"
        and isinstance(sent.get("item"), dict)
        and sent["item"].get("role") == "user"
    ]
    assert len(user_items) == 1
    item = user_items[0]
    assert isinstance(item, dict)
    assert item["type"] == "message"
    assert item["role"] == "user"
    content = item["content"]
    assert isinstance(content, list)
    assert len(content) == 1
    part = content[0]
    assert isinstance(part, dict)
    assert part["type"] == "input_text"
    text = part["text"]
    assert isinstance(text, str)
    return text


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


class _FailingSessionUpdateSocket(_FakeRealtimeSocket):
    def __init__(self, events: list[dict[str, object]]) -> None:
        super().__init__(events)
        self.fail_session_update = False

    async def send(self, message: str) -> None:
        if self.fail_session_update:
            raise RuntimeError("controlled session update failure")
        await super().send(message)


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


def _harness(  # type: ignore[no-untyped-def]
    *,
    build_id: str = "ctx-1",
    timezone_id: str = "UTC",
):
    snapshot = AuthoritativeMemorySnapshot.model_validate(
        {
            "ageBand": AgeBand.MIDDLE_CHILDHOOD,
            "resetRevision": 0,
            "currentSessionSummary": None,
            "deliveredTurns": [],
            "stableMemories": [],
        }
    )
    return build_realtime_harness(
        dynamic_context=DynamicContextAssembler().assemble(
            build_id=build_id,
            reason=DynamicContextBuildReason.SESSION_BOOTSTRAP,
            age_band=snapshot.age_band,
            timezone_id=timezone_id,
            current_time_utc=datetime(2026, 8, 31, tzinfo=UTC),
            bootstrap_turns=tuple(snapshot.delivered_turns),
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
    function_name: str = INTERACTIVE_CANVAS_TOOL_NAME,
    preamble: str = "I’ll show that clearly. ",
    response_id: str = RESPONSE_ID,
    call_id: str = FUNCTION_CALL_ID,
) -> list[dict[str, object]]:
    midpoint = len(arguments) // 2
    return [
        {
            "type": "response.created",
            "response": {"id": response_id},
        },
        {
            "type": "response.output_text.delta",
            "response_id": response_id,
            "delta": preamble,
        },
        {
            "type": "response.output_item.added",
            "response_id": response_id,
            "item": {
                "type": "function_call",
                "name": function_name,
                "call_id": call_id,
                "arguments": "",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "response_id": response_id,
            "call_id": call_id,
            "delta": arguments[:midpoint],
        },
        {
            "type": "response.function_call_arguments.delta",
            "response_id": response_id,
            "call_id": call_id,
            "delta": arguments[midpoint:],
        },
        {
            "type": "response.function_call_arguments.done",
            "response_id": response_id,
            "name": function_name,
            "call_id": call_id,
            "arguments": arguments,
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
                        "content": [{"type": "output_text", "text": preamble}],
                    },
                    {
                        "type": "function_call",
                        "name": function_name,
                        "call_id": call_id,
                        "arguments": arguments,
                    },
                ],
                "usage": _usage(10, 4),
            },
        },
    ]


def _channel(
    events: list[dict[str, object]],
    *,
    max_output_tokens: int = 800,
    provider_call_id: str | None = None,
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
        max_output_tokens=max_output_tokens,
        turn_timeout_seconds=5,
        provider_call_id=provider_call_id,
    )
    _TEST_CHANNELS.add(channel)
    return channel, socket


async def _collect_turn_events(
    channel: _OpenAIRealtimeEventChannel,
    *,
    turn_id: str,
    harness=None,  # type: ignore[no-untyped-def]
) -> list[object]:
    return [
        event
        async for event in channel.stream_turn(
            harness=harness or _harness(),
            turn_id=turn_id,
        )
    ]


async def _advance_ready_callbacks(*, rounds: int = 4) -> None:
    loop = asyncio.get_running_loop()
    for _ in range(rounds):
        checkpoint: asyncio.Future[None] = loop.create_future()
        loop.call_soon(checkpoint.set_result, None)
        await checkpoint


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


def test_available_child_transcript_peek_never_starts_or_drains_receiver() -> None:
    channel, socket = _channel([])

    assert channel.available_child_transcript_for_turn("turn-unavailable") is None
    assert channel._receiver_task is None
    assert socket.received_count == 0

    channel._input_item_turn_ids["item-indexed"] = "turn-available"
    channel._record_input_transcription(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item-indexed",
            "transcript": "Already indexed.",
        },
        failed=False,
    )

    assert (
        channel.available_child_transcript_for_turn("turn-available")
        == "Already indexed."
    )
    assert channel._receiver_task is None
    assert socket.received_count == 0


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
    assert await channel.child_transcript_for_turn("turn-b") == ("What about the moon?")
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
async def test_non_terminal_stale_response_delta_fails_ownership_validation(
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
async def test_duplicate_consumed_continuation_terminal_is_ignored_before_next_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.openai_realtime")
    arguments = (
        '{"operation":"new","brief":"Show a simple solar-system orbit.",'
        '"interactionMode":"none"}'
    )
    continuation_events = _text_response_events(
        response_id=CONTINUATION_RESPONSE_ID,
        text="The orbit loops around the sun.",
    )
    channel, _ = _channel(
        [
            *_canvas_response_events(arguments=arguments),
            *continuation_events,
            continuation_events[-1],
            *_text_response_events(
                response_id="resp_turn_b",
                text="The new turn is healthy.",
            ),
        ],
        provider_call_id="rtc_duplicate_terminal_usage",
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-canvas-a",
    ).__aiter__()

    assert isinstance(await anext(iterator), RealtimeSpeechTextDelta)
    canvas_call = await anext(iterator)
    assert isinstance(canvas_call, RealtimeCanvasToolCall)
    await channel.complete_canvas_tool(
        turn_id="turn-canvas-a",
        call_id=canvas_call.call_id,
        output=InteractiveCanvasToolOutput(
            status="rejected",
            reason="The optional visual was not available.",
        ),
    )
    assert await anext(iterator) == RealtimeSpeechTextDelta(
        delta="The orbit loops around the sun."
    )
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)

    received = await _collect_turn_events(channel, turn_id="turn-b")

    assert received == [RealtimeSpeechTextDelta(delta="The new turn is healthy.")]
    assert channel.last_response_id == "resp_turn_b"
    assert channel._response_owner_by_id[CONTINUATION_RESPONSE_ID] == "turn-canvas-a"
    assert channel._response_owner_by_id["resp_turn_b"] == "turn-b"
    assert (
        channel._consumed_terminal_response_owner_by_id[CONTINUATION_RESPONSE_ID]
        == "turn-canvas-a"
    )
    duplicate = next(
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_duplicate_terminal_ignored")
    )
    assert duplicate == {
        "component": "realtimeProvider",
        "responseId": CONTINUATION_RESPONSE_ID,
        "responseOwnerTurnId": "turn-canvas-a",
        "activeTurnId": "turn-b",
        "sidebandSequence": 12,
    }
    usage_records = [
        record.ava_context
        for record in caplog.records
        if record.getMessage() == "openai_realtime_usage"
    ]
    assert [record["responseId"] for record in usage_records] == [
        RESPONSE_ID,
        CONTINUATION_RESPONSE_ID,
        "resp_turn_b",
    ]
    assert [record["usageScope"] for record in usage_records] == [
        "response",
        "response",
        "response",
    ]
    assert all(
        record["providerCallId"] == "rtc_duplicate_terminal_usage"
        for record in usage_records
    )
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


def test_session_update_uses_text_output_and_bounded_semantic_tools() -> None:
    payload = _session_update(_harness(), "gpt-realtime-test", 800)
    session = payload["session"]

    assert session["output_modalities"] == ["text"]
    assert session["tool_choice"] == "auto"
    assert [tool["name"] for tool in session["tools"]] == [
        INTERACTIVE_CANVAS_TOOL_NAME,
        CANCEL_INTERACTIVE_CANVAS_TOOL_NAME,
        SEARCH_MEMORY_TOOL_NAME,
        GET_CURRENT_TIME_TOOL_NAME,
    ]
    turn_detection = session["audio"]["input"]["turn_detection"]
    assert session["audio"]["input"]["transcription"] == {"model": "gpt-transcribe"}
    assert turn_detection["create_response"] is True
    assert turn_detection["interrupt_response"] is True
    assert session["parallel_tool_calls"] is False
    assert session["reasoning"] == {"effort": "low"}
    assert session["truncation"] == {
        "type": "retention_ratio",
        "retention_ratio": 0.8,
        "token_limits": {"post_instructions": 80_000},
    }


def test_session_update_propagates_custom_realtime_truncation_configuration() -> None:
    payload = _session_update(
        _harness(),
        "gpt-realtime-test",
        800,
        truncation_post_instructions_tokens=64_000,
        truncation_retention_ratio=0.75,
    )

    assert payload["session"]["truncation"] == {
        "type": "retention_ratio",
        "retention_ratio": 0.75,
        "token_limits": {"post_instructions": 64_000},
    }


@pytest.mark.asyncio
async def test_logically_new_context_deduplicates_wire_update_and_becomes_active(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.openai_realtime")
    channel, socket = _channel([], provider_call_id="rtc_context_provenance")
    await channel.start_receiver()

    first_status = await channel.prepare(harness=_harness(build_id="ctx-17"))
    second_status = await channel.prepare(harness=_harness(build_id="ctx-18"))

    assert first_status == RealtimeContextInstallationStatus.INSTALLED
    assert second_status == RealtimeContextInstallationStatus.DEDUPLICATED
    assert channel.active_context_build_id == "ctx-18"
    assert [event["type"] for event in socket.sent].count("session.update") == 1

    for event in _text_response_events(
        response_id="resp_context_provenance",
        text="A content sentinel that must not enter provenance telemetry.",
    ):
        socket.push(event)
    received = await _collect_turn_events(
        channel,
        turn_id="turn-context-provenance",
        harness=_harness(build_id="ctx-18"),
    )
    assert received

    admission = next(
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_response_owner_admitted")
    )
    assert admission["providerCallId"] == "rtc_context_provenance"
    assert admission["responseId"] == "resp_context_provenance"
    assert admission["responseOwnerTurnId"] == "turn-context-provenance"
    assert admission["activeContextBuildId"] == "ctx-18"
    assert "A content sentinel" not in next(
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_response_owner_admitted")
    )


@pytest.mark.asyncio
async def test_failed_context_install_keeps_previous_active_build_identity() -> None:
    socket = _FailingSessionUpdateSocket(
        [{"type": "session.updated", "session": {"id": "sess_test"}}]
    )
    channel = _OpenAIRealtimeEventChannel(
        socket,
        model="gpt-realtime-test",
        max_output_tokens=800,
        turn_timeout_seconds=5,
        provider_call_id="rtc_failed_context",
    )
    _TEST_CHANNELS.add(channel)
    await channel.start_receiver()
    await channel.prepare(harness=_harness(build_id="ctx-21"))
    assert channel.active_context_build_id == "ctx-21"

    socket.fail_session_update = True
    with pytest.raises(OpenAIRealtimeUnavailableError, match="send failed"):
        await channel.prepare(
            harness=_harness(build_id="ctx-22", timezone_id="Asia/Kolkata")
        )

    assert channel.active_context_build_id == "ctx-21"


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
async def test_owned_transcription_wait_after_receiver_terminal_does_not_hang() -> None:
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
async def test_cancel_preserves_transcription_progress_for_its_committed_item(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.openai_realtime")
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
        ],
        provider_call_id="rtc_cancel_usage",
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
    cancel_events = [
        event for event in socket.sent if event["type"] == "response.cancel"
    ]
    assert len(cancel_events) == 1
    assert cancel_events[0]["response_id"] == RESPONSE_ID
    assert isinstance(cancel_events[0]["event_id"], str)
    assert cancel_events[0]["event_id"].startswith("ava_response_cancel_")
    cancelled_usage = next(
        record.ava_context
        for record in caplog.records
        if record.getMessage() == "openai_realtime_usage"
        and record.ava_context["responseId"] == RESPONSE_ID
    )
    assert cancelled_usage == {
        "provider": "openai",
        "model": "gpt-realtime-test",
        "usageScope": "response",
        "providerCallId": "rtc_cancel_usage",
        "responseId": RESPONSE_ID,
        "turnId": "turn-cancelled",
        "activeContextBuildId": "ctx-1",
        "inputTokens": 3,
        "cachedInputTokens": 2,
        "inputTextTokens": 3,
        "inputAudioTokens": 0,
        "outputTokens": 0,
        "outputTextTokens": 0,
        "outputAudioTokens": 0,
        "totalTokens": 3,
    }


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
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.openai_realtime")
    brief_sentinel = "CANVAS_BRIEF_USAGE_CONTENT_SENTINEL"
    speech_sentinel = "AVA_SPEECH_USAGE_CONTENT_SENTINEL"
    continuation_sentinel = "AVA_CONTINUATION_USAGE_CONTENT_SENTINEL"
    arguments = json.dumps(
        {
            "operation": operation,
            "brief": brief_sentinel,
            "interactionMode": "explore",
        },
        separators=(",", ":"),
    )
    events = [
        *_canvas_response_events(arguments=arguments, preamble=speech_sentinel),
        *_text_response_events(
            response_id=CONTINUATION_RESPONSE_ID,
            text=continuation_sentinel,
            input_tokens=6,
            output_tokens=3,
        ),
    ]
    channel, socket = _channel(events, provider_call_id="rtc_usage_canvas")

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
    assert call.request.brief == brief_sentinel
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
    assert continuation_requests[-1]["response"]["max_output_tokens"] == 512
    assert continuation_requests[-1]["response"]["conversation"] == "auto"
    assert "instructions" not in continuation_requests[-1]["response"]
    assert "input" not in continuation_requests[-1]["response"]
    assert channel.last_usage is not None
    assert channel.last_usage.total_tokens == 23
    usage_records = [
        record.ava_context
        for record in caplog.records
        if record.getMessage() == "openai_realtime_usage"
    ]
    assert usage_records == [
        {
            "provider": "openai",
            "model": "gpt-realtime-test",
            "usageScope": "response",
            "providerCallId": "rtc_usage_canvas",
            "responseId": RESPONSE_ID,
            "turnId": "turn-canvas",
            "activeContextBuildId": "ctx-1",
            "inputTokens": 10,
            "cachedInputTokens": 2,
            "inputTextTokens": 10,
            "inputAudioTokens": 0,
            "outputTokens": 4,
            "outputTextTokens": 4,
            "outputAudioTokens": 0,
            "totalTokens": 14,
        },
        {
            "provider": "openai",
            "model": "gpt-realtime-test",
            "usageScope": "response",
            "providerCallId": "rtc_usage_canvas",
            "responseId": CONTINUATION_RESPONSE_ID,
            "turnId": "turn-canvas",
            "activeContextBuildId": "ctx-1",
            "inputTokens": 6,
            "cachedInputTokens": 2,
            "inputTextTokens": 6,
            "inputAudioTokens": 0,
            "outputTokens": 3,
            "outputTextTokens": 3,
            "outputAudioTokens": 0,
            "totalTokens": 9,
        },
    ]
    for sentinel in (brief_sentinel, speech_sentinel, continuation_sentinel):
        assert sentinel not in caplog.text
        assert all(sentinel not in repr(record) for record in usage_records)


@pytest.mark.asyncio
async def test_search_memory_continuation_exposes_only_terminal_canvas_tools() -> None:
    arguments = json.dumps({"query": "Saturn rings"}, separators=(",", ":"))
    channel, socket = _channel(
        [
            *_canvas_response_events(
                arguments=arguments,
                function_name=SEARCH_MEMORY_TOOL_NAME,
                preamble="",
            ),
            *_text_response_events(
                response_id=CONTINUATION_RESPONSE_ID,
                text="You once asked about Saturn's icy rings.",
            ),
        ]
    )

    received = []
    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-memory-tool",
    ):
        received.append(event)
        if isinstance(event, RealtimeSearchMemoryToolCall):
            await channel.complete_utility_tool(
                turn_id="turn-memory-tool",
                call_id=event.call_id,
                output=SearchMemoryToolOutputV4(status="noMatches"),
                harness=_harness(),
            )

    assert [type(event) for event in received] == [
        RealtimeSearchMemoryToolCall,
        RealtimeSpeechTextDelta,
    ]
    continuation = [
        sent for sent in socket.sent if sent.get("type") == "response.create"
    ][-1]["response"]
    assert continuation["tool_choice"] == "auto"
    assert continuation["parallel_tool_calls"] is False
    assert [tool["name"] for tool in continuation["tools"]] == [
        INTERACTIVE_CANVAS_TOOL_NAME,
        CANCEL_INTERACTIVE_CANVAS_TOOL_NAME,
    ]


@pytest.mark.asyncio
async def test_current_time_then_canvas_is_the_maximum_allowed_tool_chain() -> None:
    canvas_arguments = json.dumps(
        {
            "operation": "new",
            "brief": "Show a simple morning and evening clock comparison.",
            "interactionMode": "none",
        },
        separators=(",", ":"),
    )
    channel, socket = _channel(
        [
            *_canvas_response_events(
                arguments="{}",
                function_name=GET_CURRENT_TIME_TOOL_NAME,
                preamble="",
            ),
            *_canvas_response_events(
                arguments=canvas_arguments,
                response_id=CONTINUATION_RESPONSE_ID,
                call_id="call_canvas_after_time",
                preamble="",
            ),
            *_text_response_events(
                response_id="resp_after_two_tools",
                text="Here is that clock comparison.",
            ),
        ]
    )

    received = []
    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-time-canvas",
    ):
        received.append(event)
        if isinstance(event, RealtimeGetCurrentTimeToolCall):
            await channel.complete_utility_tool(
                turn_id="turn-time-canvas",
                call_id=event.call_id,
                output=GetCurrentTimeToolOutputV4.model_validate(
                    {
                        "localIsoDateTime": "2026-08-31T12:00:00+05:30",
                        "weekday": "Monday",
                        "daypart": "afternoon",
                        "timezoneId": "Asia/Kolkata",
                    }
                ),
                harness=_harness(),
            )
        elif isinstance(event, RealtimeCanvasToolCall):
            await channel.complete_canvas_tool(
                turn_id="turn-time-canvas",
                call_id=event.call_id,
                output=InteractiveCanvasToolOutputV4.model_validate(
                    {
                        "status": "accepted",
                        "generationId": "generation-time-canvas",
                    }
                ),
            )

    assert [type(event) for event in received] == [
        RealtimeGetCurrentTimeToolCall,
        RealtimeCanvasToolCall,
        RealtimeSpeechTextDelta,
    ]
    continuations = [
        sent["response"]
        for sent in socket.sent
        if sent.get("type") == "response.create"
    ]
    assert continuations[-2]["tool_choice"] == "auto"
    assert continuations[-1]["tool_choice"] == "none"
    assert "tools" not in continuations[-1]


@pytest.mark.parametrize(
    ("function_name", "arguments"),
    [
        (SEARCH_MEMORY_TOOL_NAME, '{"query":"another lookup"}'),
        (GET_CURRENT_TIME_TOOL_NAME, "{}"),
    ],
)
async def test_canvas_terminal_continuation_rejects_later_utility_calls(
    function_name: str,
    arguments: str,
) -> None:
    channel, _ = _channel(
        [
            *_canvas_response_events(
                arguments=(
                    '{"operation":"new","brief":"Show Saturn.",'
                    '"interactionMode":"none"}'
                ),
                preamble="",
            ),
            *_canvas_response_events(
                arguments=arguments,
                function_name=function_name,
                response_id=CONTINUATION_RESPONSE_ID,
                call_id="call_disallowed_after_canvas",
                preamble="",
            ),
        ]
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-canvas-terminal",
    ).__aiter__()

    call = await iterator.__anext__()
    assert isinstance(call, RealtimeCanvasToolCall)
    await channel.complete_canvas_tool(
        turn_id="turn-canvas-terminal",
        call_id=call.call_id,
        output=InteractiveCanvasToolOutputV4.model_validate(
            {"status": "accepted", "generationId": "generation-terminal"}
        ),
    )
    with pytest.raises(OpenAIRealtimeProtocolError, match="additional function call"):
        await iterator.__anext__()
    await iterator.aclose()


@pytest.mark.asyncio
async def test_repeated_or_cross_utility_gets_one_rejection_then_no_tools() -> None:
    channel, socket = _channel(
        [
            *_canvas_response_events(
                arguments=json.dumps({"query": "Saturn"}),
                function_name=SEARCH_MEMORY_TOOL_NAME,
                preamble="",
            ),
            *_canvas_response_events(
                arguments="{}",
                function_name=GET_CURRENT_TIME_TOOL_NAME,
                response_id=CONTINUATION_RESPONSE_ID,
                call_id="call_invalid_second_utility",
                preamble="",
            ),
            *_text_response_events(
                response_id="resp_after_rejected_utility",
                text="I can answer from what is already available.",
            ),
        ]
    )

    received = []
    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-utility-rejected",
    ):
        received.append(event)
        if isinstance(event, RealtimeSearchMemoryToolCall):
            await channel.complete_utility_tool(
                turn_id="turn-utility-rejected",
                call_id=event.call_id,
                output=SearchMemoryToolOutputV4(status="noMatches"),
                harness=_harness(),
            )
        elif isinstance(event, RealtimeRejectedUtilityToolCall):
            await channel.complete_utility_tool(
                turn_id="turn-utility-rejected",
                call_id=event.call_id,
                output=RejectedUtilityToolOutputV4(reason=event.reason),
                harness=_harness(),
            )

    assert [type(event) for event in received] == [
        RealtimeSearchMemoryToolCall,
        RealtimeRejectedUtilityToolCall,
        RealtimeSpeechTextDelta,
    ]
    continuations = [
        sent["response"]
        for sent in socket.sent
        if sent.get("type") == "response.create"
    ]
    assert continuations[-1]["tool_choice"] == "none"
    assert "tools" not in continuations[-1]


@pytest.mark.asyncio
async def test_explicit_canvas_cancellation_call_gets_idempotent_tool_output() -> None:
    channel, socket = _channel(
        [
            *_canvas_response_events(
                arguments="{}",
                function_name=CANCEL_INTERACTIVE_CANVAS_TOOL_NAME,
                preamble="",
            ),
            *_text_response_events(
                response_id=CONTINUATION_RESPONSE_ID,
                text="Okay.",
            ),
        ]
    )

    received = []
    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-cancel-canvas",
    ):
        received.append(event)
        if isinstance(event, RealtimeCancelCanvasToolCall):
            await channel.complete_canvas_tool(
                turn_id="turn-cancel-canvas",
                call_id=event.call_id,
                output=InteractiveCanvasCancellationToolOutputV4.model_validate(
                    {"status": "accepted"}
                ),
            )

    assert [type(event) for event in received] == [
        RealtimeCancelCanvasToolCall,
        RealtimeSpeechTextDelta,
    ]
    function_output = next(
        sent
        for sent in socket.sent
        if isinstance(sent.get("item"), dict)
        and sent["item"].get("type") == "function_call_output"
    )
    assert json.loads(function_output["item"]["output"]) == {"status": "accepted"}


@pytest.mark.asyncio
async def test_rejected_canvas_continuation_is_outcome_owned_and_bounded() -> None:
    arguments = json.dumps(
        {
            "operation": "new",
            "brief": "visual-brief-A",
            "interactionMode": "none",
        },
        separators=(",", ":"),
    )
    channel, socket = _channel(
        [
            *_canvas_response_events(arguments=arguments, preamble=""),
            *_text_response_events(
                response_id=CONTINUATION_RESPONSE_ID,
                text="The optional visual is unavailable.",
            ),
        ],
        max_output_tokens=400,
    )

    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-canvas-rejected",
    ):
        if isinstance(event, RealtimeCanvasToolCall):
            await channel.complete_canvas_tool(
                turn_id="turn-canvas-rejected",
                call_id=event.call_id,
                output=InteractiveCanvasToolOutputV4.model_validate(
                    {
                        "status": "rejected",
                        "reason": CANVAS_MODEL_SAFE_REJECTION_REASON,
                    }
                ),
            )

    function_output = next(
        sent
        for sent in socket.sent
        if isinstance(sent.get("item"), dict)
        and sent["item"].get("type") == "function_call_output"
    )
    serialized_output = function_output["item"]["output"]
    assert isinstance(serialized_output, str)
    assert json.loads(serialized_output) == {
        "status": "rejected",
        "reason": CANVAS_MODEL_SAFE_REJECTION_REASON,
    }
    continuation = [
        sent for sent in socket.sent if sent.get("type") == "response.create"
    ][-1]["response"]
    assert continuation["tool_choice"] == "none"
    assert continuation["max_output_tokens"] == 400
    assert continuation["conversation"] == "auto"
    assert "instructions" not in continuation
    assert "input" not in continuation


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
    assert isinstance(response, dict)
    assert response["output_modalities"] == ["text"]
    assert response["tool_choice"] == "none"
    assert response["max_output_tokens"] == 256
    assert response["conversation"] == "auto"
    assert "input" not in response
    assert "instructions" not in response
    semantic_input = _interaction_conversation_item_text(socket.sent)
    assert semantic_input.startswith(
        '<canvas_interaction trust="user_action_context" mode="explore">'
    )
    assert 'mode="explore"' in semantic_input
    assert 'trust="user_action_context"' in semantic_input
    assert "<semantic_focus>Great Red Spot</semantic_focus>" in semantic_input
    for application_identity in (
        "scene_id",
        "scene_revision",
        "interaction_revision",
        "binding_id",
        "target_id",
        "svg_sha256",
        "manifest_sha256",
    ):
        assert application_identity not in semantic_input
    assert not any(
        sent.get("type") == "conversation.item.create"
        and isinstance(sent.get("item"), dict)
        and sent["item"].get("role") == "system"
        for sent in socket.sent
    )
    session_update = next(
        sent for sent in socket.sent if sent.get("type") == "session.update"
    )
    session = session_update["session"]
    assert isinstance(session, dict)
    assert session["instructions"] == _harness().instructions

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
async def test_canvas_interaction_response_owns_each_exact_semantic_context() -> None:
    interactions = (
        _recorded_interaction(
            binding_id="binding-A",
            target_id="shared-visual-target",
            accessibility_label="semantic-focus-A",
        ),
        _recorded_interaction(
            binding_id="binding-B",
            target_id="shared-visual-target",
            accessibility_label="semantic-focus-B",
            interaction_mode="select",
            interaction_revision=2,
        ),
    )
    semantic_inputs: list[str] = []

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
        assert response["conversation"] == "auto"
        assert "input" not in response
        assert "instructions" not in response
        semantic_inputs.append(_interaction_conversation_item_text(socket.sent))

        pending_event.cancel()
        await asyncio.gather(pending_event, return_exceptions=True)
        await iterator.aclose()

    assert "<semantic_focus>semantic-focus-A</semantic_focus>" in semantic_inputs[0]
    assert "semantic-focus-B" not in semantic_inputs[0]
    assert "<semantic_focus>semantic-focus-B</semantic_focus>" in semantic_inputs[1]
    assert "semantic-focus-A" not in semantic_inputs[1]
    assert 'mode="explore"' in semantic_inputs[0]
    assert 'mode="select"' in semantic_inputs[1]
    for semantic_input in semantic_inputs:
        assert "scene_id" not in semantic_input
        assert "scene_revision" not in semantic_input
        assert "interaction_revision" not in semantic_input
        assert "binding_id" not in semantic_input
        assert "target_id" not in semantic_input
        assert "binding-A" not in semantic_input
        assert "binding-B" not in semantic_input
        assert "shared-visual-target" not in semantic_input
    assert semantic_inputs[0] != semantic_inputs[1]


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
    assert response["conversation"] == "auto"
    assert "input" not in response
    assert "instructions" not in response
    semantic_input = _interaction_conversation_item_text(socket.sent)
    assert 'mode="steps"' in semantic_input
    assert 'step_index="2"' in semantic_input
    assert "<semantic_focus>Moon orbit</semantic_focus>" in semantic_input

    pending_event.cancel()
    await asyncio.gather(pending_event, return_exceptions=True)
    await iterator.aclose()


@pytest.mark.asyncio
async def test_incomplete_canvas_interaction_response_is_not_completed() -> None:
    channel, socket = _channel([])
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-incomplete-canvas-interaction",
    ).__aiter__()
    pending_event = asyncio.create_task(iterator.__anext__())
    await socket.wait_received(1)

    assert await channel.request_canvas_interaction_response(
        turn_id="turn-incomplete-canvas-interaction",
        interaction=_recorded_interaction(),
    )
    response_id = "resp_incomplete_canvas_interaction"
    socket.push({"type": "response.created", "response": {"id": response_id}})
    socket.push(
        {
            "type": "response.output_text.delta",
            "response_id": response_id,
            "delta": "Here is a partial",
        }
    )
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": response_id,
                "status": "incomplete",
                "status_details": {
                    "type": "incomplete",
                    "reason": "max_output_tokens",
                },
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "Here is a partial"}
                        ],
                    }
                ],
                "usage": _usage(10, 256),
            },
        }
    )

    assert await pending_event == RealtimeSpeechTextDelta(delta="Here is a partial")
    with pytest.raises(
        RealtimeProviderResponseIncompleteError,
        match="incomplete.*max_output_tokens",
    ):
        await iterator.__anext__()
    assert channel.last_response_id is None


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
    assert (
        len([sent for sent in socket.sent if sent.get("type") == "response.create"])
        == 1
    )

    for event in _text_response_events(
        response_id="resp_canvas_single_shot",
        text="That is the selected part.",
    ):
        socket.push(event)
    assert isinstance(await pending_event, RealtimeSpeechTextDelta)
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("function_name", "arguments"),
    [
        (
            INTERACTIVE_CANVAS_TOOL_NAME,
            '{"operation":"new","brief":"Show a cell.","interactionMode":"none"}',
        ),
        (SEARCH_MEMORY_TOOL_NAME, '{"query":"cell history"}'),
        (GET_CURRENT_TIME_TOOL_NAME, "{}"),
    ],
)
async def test_canvas_interaction_response_cannot_call_any_semantic_tool(
    function_name: str,
    arguments: str,
) -> None:
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
        arguments=arguments,
        function_name=function_name,
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
async def test_child_input_observed_first_suppresses_canvas_interaction_response() -> (
    None
):
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
    assert await channel.admit_pending_input_items(turn_id="turn-owned-child") == (
        "item_owned_child",
    )
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
async def test_readiness_predicate_consistency_across_all_gates() -> None:
    channel, socket = _channel([])
    turn_id = "turn-readiness-consistency"

    # 1. Closed provider
    channel_closed, _ = _channel([])
    await channel_closed.close()
    assert (
        channel_closed._canvas_interaction_response_readiness(turn_id)
        == "providerClosed"
    )
    assert not await channel_closed.request_canvas_interaction_response(
        turn_id=turn_id,
        interaction=_recorded_interaction(),
    )

    # 2. Turn mismatch
    assert channel._canvas_interaction_response_readiness(turn_id) == "turnMismatch"
    assert not await channel.request_canvas_interaction_response(
        turn_id=turn_id,
        interaction=_recorded_interaction(),
    )

    # 3. Not awaiting trigger (stream_turn not started)
    channel._active_turn_id = turn_id
    assert (
        channel._canvas_interaction_response_readiness(turn_id) == "notAwaitingTrigger"
    )
    assert not await channel.request_canvas_interaction_response(
        turn_id=turn_id,
        interaction=_recorded_interaction(),
    )

    # 4. Ready state
    channel._awaiting_response_trigger_turn_id = turn_id
    assert channel._canvas_interaction_response_readiness(turn_id) is None
    assert await channel.wait_until_canvas_interaction_response_ready(turn_id=turn_id)
    assert await channel.request_canvas_interaction_response(
        turn_id=turn_id,
        interaction=_recorded_interaction(),
    )


@pytest.mark.asyncio
async def test_generic_admission_never_reconstructs_response_trigger_ownership() -> (
    None
):
    turn_id = "turn-admission-ownership"
    channel, _ = _channel([])
    channel._active_turn_id = turn_id
    channel._awaiting_response_trigger_turn_id = None
    channel._input_item_turn_ids["item-prior"] = None

    channel._admit_input_item(item_id="item-prior", turn_id="turn-prior")

    assert channel._awaiting_response_trigger_turn_id is None
    assert channel._canvas_interaction_response_readiness(turn_id) == (
        "notAwaitingTrigger"
    )

    channel._response_owner_by_id["resp-prior"] = None
    channel._admit_response(response_id="resp-prior", turn_id="turn-prior")

    assert channel._awaiting_response_trigger_turn_id is None
    assert channel._canvas_interaction_response_readiness(turn_id) == (
        "notAwaitingTrigger"
    )


@pytest.mark.asyncio
async def test_readiness_waiter_closes_lost_wakeup_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel, _ = _channel([])
    turn_id = "turn-lost-wakeup"
    channel._active_turn_id = turn_id
    channel._awaiting_response_trigger_turn_id = turn_id
    channel._input_item_turn_ids["item-prior"] = None
    original = channel._canvas_interaction_response_readiness
    calls = 0

    def readiness_with_transition(
        self: _OpenAIRealtimeEventChannel,
        candidate_turn_id: str,
    ) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            self._input_item_turn_ids["item-prior"] = "turn-prior"
            self._interaction_response_readiness_changed.set()
            return "unresolvedInputOwner"
        return original(candidate_turn_id)

    monkeypatch.setattr(
        channel,
        "_canvas_interaction_response_readiness",
        MethodType(readiness_with_transition, channel),
    )

    assert await channel.wait_until_canvas_interaction_response_ready(turn_id=turn_id)
    assert calls == 2


@pytest.mark.asyncio
async def test_close_and_cancel_wake_blocked_readiness_waiters() -> None:
    close_channel, _ = _channel([])
    close_channel._active_turn_id = "turn-close-waiter"
    close_channel._awaiting_response_trigger_turn_id = "turn-close-waiter"
    close_channel._active_response_id = "resp-blocking-close"
    close_waiter = asyncio.create_task(
        close_channel.wait_until_canvas_interaction_response_ready(
            turn_id="turn-close-waiter"
        )
    )
    await _advance_ready_callbacks()
    await close_channel.close()
    assert await close_waiter is False

    cancel_channel, _ = _channel([])
    cancel_channel._active_turn_id = "turn-other"
    cancel_waiter = asyncio.create_task(
        cancel_channel.wait_until_canvas_interaction_response_ready(
            turn_id="turn-cancel-waiter"
        )
    )
    await _advance_ready_callbacks()
    await cancel_channel.cancel(turn_id="turn-cancel-waiter")
    assert await cancel_waiter is False


@pytest.mark.asyncio
async def test_unresolved_input_owner_wakes_and_triggers_interaction() -> None:
    channel, socket = _channel(
        [
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "item_unresolved_test",
                "audio_start_ms": 100,
            }
        ]
    )
    await channel.start_receiver()
    await socket.wait_received(2)
    turn_id = "turn-unresolved-input"

    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id=turn_id,
    ).__aiter__()
    pending_event = asyncio.create_task(iterator.__anext__())
    await socket.wait_sent(1)

    waiter = asyncio.create_task(
        channel.wait_until_canvas_interaction_response_ready(turn_id=turn_id)
    )
    await asyncio.sleep(0.01)
    assert not waiter.done()

    await channel.admit_pending_input_items(turn_id="turn-prior-voice")
    assert await asyncio.wait_for(waiter, timeout=1.0) is True

    assert await channel.request_canvas_interaction_response(
        turn_id=turn_id,
        interaction=_recorded_interaction(),
    )
    assert (
        sum(
            sent.get("type") == "response.create"
            and sent.get("response", {}).get("tool_choice") == "none"
            for sent in socket.sent
        )
        == 1
    )

    pending_event.cancel()
    await asyncio.gather(pending_event, return_exceptions=True)
    await iterator.aclose()


@pytest.mark.asyncio
async def test_unresolved_response_owner_wakes_and_triggers_interaction() -> None:
    channel, socket = _channel([])
    await channel.start_receiver()
    turn_id = "turn-unresolved-resp"

    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp_unresolved_123",
                "status": "completed",
                "output": [],
            },
        }
    )
    await socket.wait_received(1)
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id=turn_id,
    ).__aiter__()
    pending_event = asyncio.create_task(iterator.__anext__())
    await socket.wait_sent(1)
    assert (
        channel._canvas_interaction_response_readiness(turn_id)
        == "unresolvedResponseOwner"
    )

    waiter = asyncio.create_task(
        channel.wait_until_canvas_interaction_response_ready(turn_id=turn_id)
    )
    await asyncio.sleep(0.01)
    assert not waiter.done()

    channel._admit_response(response_id="resp_unresolved_123", turn_id="turn-prior")
    assert await asyncio.wait_for(waiter, timeout=1.0) is True

    assert await channel.request_canvas_interaction_response(
        turn_id=turn_id,
        interaction=_recorded_interaction(),
    )
    assert sum(sent.get("type") == "response.create" for sent in socket.sent) == 1

    pending_event.cancel()
    await asyncio.gather(pending_event, return_exceptions=True)
    await iterator.aclose()


@pytest.mark.asyncio
async def test_active_response_wakes_and_triggers_when_settled() -> None:
    channel, socket = _channel([])
    turn_id = "turn-active-resp-settle"
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id=turn_id,
    ).__aiter__()
    pending_event = asyncio.create_task(iterator.__anext__())
    await socket.wait_received(1)

    response_events = _text_response_events(
        response_id="resp_settle_test",
        text="Explaining something first.",
    )
    socket.push(response_events[0])
    socket.push(response_events[1])
    assert isinstance(await pending_event, RealtimeSpeechTextDelta)

    waiter = asyncio.create_task(
        channel.wait_until_canvas_interaction_response_ready(turn_id=turn_id)
    )
    await asyncio.sleep(0.01)
    assert not waiter.done()

    socket.push(response_events[2])
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()
    assert await waiter is False

    next_turn_id = "turn-next-tap"
    next_iterator = channel.stream_turn(
        harness=_harness(),
        turn_id=next_turn_id,
    ).__aiter__()
    next_pending_event = asyncio.create_task(next_iterator.__anext__())

    waiter_next = asyncio.create_task(
        channel.wait_until_canvas_interaction_response_ready(turn_id=next_turn_id)
    )
    assert await asyncio.wait_for(waiter_next, timeout=1.0) is True
    assert await channel.request_canvas_interaction_response(
        turn_id=next_turn_id,
        interaction=_recorded_interaction(),
    )
    await socket.wait_sent(2)

    next_pending_event.cancel()
    await asyncio.gather(next_pending_event, return_exceptions=True)
    await next_iterator.aclose()


@pytest.mark.asyncio
async def test_response_events_require_created_before_done() -> None:
    invalid, _ = _channel(
        [
            {
                "type": "response.done",
                "response": {
                    "id": "resp-before-created",
                    "status": "completed",
                    "output": [],
                    "usage": _usage(1, 1),
                },
            }
        ]
    )
    with pytest.raises(OpenAIRealtimeProtocolError, match="before response.created"):
        await _collect_turn_events(invalid, turn_id="turn-invalid-order")

    valid, _ = _channel(
        _text_response_events(
            response_id="resp-created-before-done",
            text="The order is valid.",
        )
    )
    assert await _collect_turn_events(valid, turn_id="turn-valid-order") == [
        RealtimeSpeechTextDelta(delta="The order is valid.")
    ]


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
async def test_canvas_validation_logs_issue_paths_without_raw_arguments(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_brief = "raw-private-visual-brief"
    channel, _socket = _channel(
        [
            *_canvas_response_events(
                arguments=json.dumps(
                    {
                        "brief": raw_brief,
                        "interactionMode": "none",
                    }
                ),
                preamble="",
            ),
            *_text_response_events(
                response_id=CONTINUATION_RESPONSE_ID,
                text="The optional visual is unavailable.",
            ),
        ]
    )
    caplog.set_level(logging.WARNING, logger="ava_backend.openai_realtime")

    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-invalid-content-free-log",
    ):
        if isinstance(event, RealtimeRejectedCanvasToolCall):
            await channel.complete_canvas_tool(
                turn_id="turn-invalid-content-free-log",
                call_id=event.call_id,
                output=InteractiveCanvasToolOutputV4.model_validate(
                    {
                        "status": "rejected",
                        "reason": CANVAS_MODEL_SAFE_REJECTION_REASON,
                    }
                ),
            )

    record = next(
        item
        for item in caplog.records
        if item.getMessage().startswith("openai_canvas_arguments_rejected")
    )
    context = record.ava_context  # type: ignore[attr-defined]
    assert context["issueCount"] == 1
    assert context["issues"] == [{"path": "operation", "code": "missing"}]
    assert raw_brief not in caplog.text
    assert raw_brief not in str(context)


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
async def test_late_exact_cancel_error_after_cancelled_done_keeps_next_turn_healthy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    channel, socket = _channel(
        [
            {
                "type": "response.created",
                "response": {"id": "resp-cancelled-a"},
            },
            {
                "type": "response.output_text.delta",
                "response_id": "resp-cancelled-a",
                "delta": "A partial answer.",
            },
        ]
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-cancelled-a",
    ).__aiter__()
    assert await iterator.__anext__() == RealtimeSpeechTextDelta(
        delta="A partial answer."
    )

    sent_before_cancel = len(socket.sent)
    cancel_task = asyncio.create_task(channel.cancel(turn_id="turn-cancelled-a"))
    await socket.wait_sent(sent_before_cancel + 1)
    cancel_event = next(
        event for event in reversed(socket.sent) if event["type"] == "response.cancel"
    )
    cancel_event_id = cancel_event["event_id"]
    assert isinstance(cancel_event_id, str)
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp-cancelled-a",
                "status": "cancelled",
                "output": [],
                "usage": _usage(3, 0),
            },
        }
    )
    await cancel_task
    await iterator.aclose()

    assert not channel._outstanding_cancel_outcomes
    assert list(channel._recently_resolved_cancel_outcomes) == [cancel_event_id]

    socket.push(
        {
            "type": "error",
            "error": {
                "code": "response_cancel_not_active",
                "event_id": cancel_event_id,
                "message": "The cancelled response is no longer active.",
            },
        }
    )
    for event in _text_response_events(
        response_id="resp-next-b",
        text="The next turn remains healthy.",
    ):
        socket.push(event)

    with caplog.at_level(logging.INFO, logger="ava_backend.openai_realtime"):
        received = await _collect_turn_events(channel, turn_id="turn-next-b")

    assert received == [RealtimeSpeechTextDelta(delta="The next turn remains healthy.")]
    assert not channel._recently_resolved_cancel_outcomes
    assert "openai_realtime_late_cancel_error_consumed" in caplog.text


@pytest.mark.asyncio
async def test_unknown_cancel_not_active_event_id_remains_fatal() -> None:
    channel, _ = _channel(
        [
            {
                "type": "error",
                "error": {
                    "code": "response_cancel_not_active",
                    "event_id": "cancel-never-owned",
                    "message": "The requested response is not active.",
                },
            }
        ]
    )

    with pytest.raises(
        OpenAIRealtimeUnavailableError,
        match="response_cancel_not_active",
    ):
        await _collect_turn_events(channel, turn_id="turn-unknown-cancel")


@pytest.mark.asyncio
async def test_exact_recent_cancel_match_consumes_only_its_protocol_record() -> None:
    channel, _ = _channel([])
    first = await channel._send_response_cancel(
        turn_id="turn-recent-1",
        response_id="resp-recent-1",
    )
    channel._resolve_outstanding_cancel_outcome(
        turn_id="turn-recent-1",
        response_id="resp-recent-1",
        terminal_status="cancelled",
    )
    second = await channel._send_response_cancel(
        turn_id="turn-recent-2",
        response_id="resp-recent-2",
    )
    channel._resolve_outstanding_cancel_outcome(
        turn_id="turn-recent-2",
        response_id="resp-recent-2",
        terminal_status="cancelled",
    )

    consumed = channel._consume_recently_resolved_cancel_outcome(
        {
            "error": {
                "code": "response_cancel_not_active",
                "event_id": first.cancel_event_id,
            }
        }
    )

    assert consumed is not None
    assert consumed.cancel_event_id == first.cancel_event_id
    assert list(channel._recently_resolved_cancel_outcomes) == [second.cancel_event_id]


@pytest.mark.asyncio
async def test_recent_cancel_outcome_ledger_is_bounded_and_eviction_is_fatal() -> None:
    channel, socket = _channel([])
    resolved_event_ids: list[str] = []
    for index in range(33):
        outcome = await channel._send_response_cancel(
            turn_id=f"turn-resolved-{index}",
            response_id=f"resp-resolved-{index}",
        )
        resolved_event_ids.append(outcome.cancel_event_id)
        channel._resolve_outstanding_cancel_outcome(
            turn_id=f"turn-resolved-{index}",
            response_id=f"resp-resolved-{index}",
            terminal_status="cancelled",
        )

    assert len(channel._recently_resolved_cancel_outcomes) == 32
    assert resolved_event_ids[0] not in channel._recently_resolved_cancel_outcomes
    assert list(channel._recently_resolved_cancel_outcomes) == resolved_event_ids[1:]

    socket.push(
        {
            "type": "error",
            "error": {
                "code": "response_cancel_not_active",
                "event_id": resolved_event_ids[0],
                "message": "The requested response is not active.",
            },
        }
    )
    with pytest.raises(
        OpenAIRealtimeUnavailableError,
        match="response_cancel_not_active",
    ):
        await _collect_turn_events(channel, turn_id="turn-after-eviction")


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

    cancel_events = [
        event for event in socket.sent if event["type"] == "response.cancel"
    ]
    assert len(cancel_events) == 1
    assert set(cancel_events[0]) == {"type", "event_id"}
    assert isinstance(cancel_events[0]["event_id"], str)
    assert cancel_events[0]["event_id"].startswith("ava_response_cancel_")
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

    cancellation = asyncio.create_task(channel.cancel(turn_id="turn-interaction-a"))
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
        RealtimeSpeechTextDelta(delta="This is the child's normal Turn B response.")
    ]
    assert channel._response_owner_by_id["resp_interaction_a"] == ("turn-interaction-a")
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
    cancellation = asyncio.create_task(channel.cancel(turn_id="turn-interaction-vad-a"))
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

    assert await channel.admit_pending_input_items(turn_id="turn-child-vad-b") == (
        "item-child-vad-b",
    )
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
    assert channel._response_owner_by_id["resp_child_vad_b"] == ("turn-child-vad-b")
    assert channel.input_item_owner(item_id="item-child-vad-b") == ("turn-child-vad-b")
    assert channel.last_response_id == "resp_child_vad_b"
    assert not channel._outstanding_cancel_outcomes


@pytest.mark.asyncio
async def test_late_cancel_error_arriving_after_next_response_created_is_consumed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.openai_realtime")
    next_turn_events = _text_response_events(
        response_id=CONTINUATION_RESPONSE_ID,
        text="The next turn remains healthy.",
    )
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
            next_turn_events[0],
            {
                "type": "error",
                "error": {
                    "code": "response_cancel_not_active",
                    "message": "The completed response is no longer active.",
                },
            },
            *next_turn_events[1:],
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
    assert not channel._outstanding_cancel_outcomes
    assert [event["type"] for event in socket.sent].count("response.cancel") == 1
    assert any(
        record.getMessage().startswith("openai_realtime_late_cancel_error_consumed")
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_late_cancel_error_survives_a_complete_later_turn() -> None:
    later_turn_events = _text_response_events(
        response_id="resp_later_turn",
        text="The later turn completes normally.",
    )
    subsequent_turn_events = _text_response_events(
        response_id="resp_subsequent_turn",
        text="The persistent session remains usable.",
    )
    channel, socket = _channel(
        [
            {
                "type": "response.done",
                "response": {
                    "id": "resp_cancelled_turn",
                    "status": "completed",
                    "output": [],
                    "usage": _usage(3, 1),
                },
            },
            *later_turn_events,
        ]
    )
    await channel.prepare(harness=_harness())
    channel._active_turn_id = "turn-cancelled"
    channel._active_response_id = "resp_cancelled_turn"

    await channel.cancel(turn_id="turn-cancelled")
    later_received = await _collect_turn_events(channel, turn_id="turn-later")

    assert later_received == [
        RealtimeSpeechTextDelta(delta="The later turn completes normally.")
    ]
    assert [
        (
            outcome.cancel_sequence,
            outcome.cancel_event_id,
            outcome.cancelled_turn_id,
            outcome.response_id,
        )
        for outcome in channel._outstanding_cancel_outcomes
    ] == [
        (
            1,
            next(
                event["event_id"]
                for event in socket.sent
                if event["type"] == "response.cancel"
            ),
            "turn-cancelled",
            "resp_cancelled_turn",
        )
    ]

    cancel_event = next(
        event for event in socket.sent if event["type"] == "response.cancel"
    )
    socket.push(
        {
            "type": "error",
            "error": {
                "event_id": cancel_event["event_id"],
                "code": "response_cancel_not_active",
                "message": "The earlier response was already complete.",
            },
        }
    )
    for event in subsequent_turn_events:
        socket.push(event)

    subsequent_received = await _collect_turn_events(
        channel,
        turn_id="turn-subsequent",
    )

    assert subsequent_received == [
        RealtimeSpeechTextDelta(delta="The persistent session remains usable.")
    ]
    assert channel.last_response_id == "resp_subsequent_turn"
    assert not channel._outstanding_cancel_outcomes
    assert [event["type"] for event in socket.sent].count("response.cancel") == 1


@pytest.mark.asyncio
async def test_multiple_cancel_outcomes_do_not_overwrite_each_other() -> None:
    channel, socket = _channel(
        [
            {
                "type": "response.done",
                "response": {
                    "id": "resp_cancel_a",
                    "status": "completed",
                    "output": [],
                    "usage": _usage(3, 1),
                },
            },
            {
                "type": "response.created",
                "response": {"id": "resp_cancel_b"},
            },
        ]
    )
    await channel.prepare(harness=_harness())
    channel._active_turn_id = "turn-cancel-a"
    channel._active_response_id = "resp_cancel_a"
    await channel.cancel(turn_id="turn-cancel-a")

    channel._active_turn_id = "turn-cancel-b"
    channel._active_response_id = "resp_cancel_b"
    cancel_b = asyncio.create_task(channel.cancel(turn_id="turn-cancel-b"))
    await socket.wait_sent(3)
    cancel_events = [
        event for event in socket.sent if event["type"] == "response.cancel"
    ]
    assert len(cancel_events) == 2
    assert cancel_events[0]["event_id"] != cancel_events[1]["event_id"]
    assert [event["response_id"] for event in cancel_events] == [
        "resp_cancel_a",
        "resp_cancel_b",
    ]
    socket.push(
        {
            "type": "error",
            "error": {
                "event_id": cancel_events[1]["event_id"],
                "code": "response_cancel_not_active",
                "message": "Turn B completed before cancellation.",
            },
        }
    )
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp_cancel_b",
                "status": "completed",
                "output": [],
                "usage": _usage(3, 1),
            },
        }
    )
    await cancel_b

    assert [
        (
            outcome.cancel_sequence,
            outcome.cancel_event_id,
            outcome.cancelled_turn_id,
            outcome.response_id,
        )
        for outcome in channel._outstanding_cancel_outcomes
    ] == [(1, cancel_events[0]["event_id"], "turn-cancel-a", "resp_cancel_a")]

    socket.push(
        {
            "type": "error",
            "error": {
                "event_id": cancel_events[0]["event_id"],
                "code": "response_cancel_not_active",
                "message": "Turn A completed before cancellation.",
            },
        }
    )
    for event in _text_response_events(
        response_id="resp_after_two_cancels",
        text="Both cancellation outcomes were attributed.",
    ):
        socket.push(event)

    received = await _collect_turn_events(channel, turn_id="turn-after-cancels")

    assert received == [
        RealtimeSpeechTextDelta(delta="Both cancellation outcomes were attributed.")
    ]
    assert channel.last_response_id == "resp_after_two_cancels"
    assert not channel._outstanding_cancel_outcomes
    assert [event["type"] for event in socket.sent].count("response.cancel") == 2


@pytest.mark.asyncio
async def test_cancel_not_active_with_unknown_event_id_is_provider_failure() -> None:
    channel, socket = _channel(
        [
            {
                "type": "response.done",
                "response": {
                    "id": "resp_cancelled_before_unknown_error",
                    "status": "completed",
                    "output": [],
                    "usage": _usage(3, 1),
                },
            }
        ]
    )
    await channel.prepare(harness=_harness())
    channel._active_turn_id = "turn-cancelled-before-unknown-error"
    channel._active_response_id = "resp_cancelled_before_unknown_error"
    await channel.cancel(turn_id="turn-cancelled-before-unknown-error")
    cancel_event = next(
        event for event in socket.sent if event["type"] == "response.cancel"
    )
    assert cancel_event["event_id"] != "unknown_client_event"
    socket.push(
        {
            "type": "error",
            "error": {
                "event_id": "unknown_client_event",
                "code": "response_cancel_not_active",
                "message": "This error does not belong to Ava's cancellation.",
            },
        }
    )

    with pytest.raises(
        OpenAIRealtimeUnavailableError,
        match="code=response_cancel_not_active",
    ):
        await anext(
            channel.stream_turn(
                harness=_harness(),
                turn_id="turn-after-unknown-error",
                text_input="Can we continue?",
                response_max_output_tokens=400,
            )
        )

    assert len(channel._outstanding_cancel_outcomes) == 1
    assert (
        channel._outstanding_cancel_outcomes[0].cancel_event_id
        == (cancel_event["event_id"])
    )


@pytest.mark.asyncio
async def test_cancel_not_active_without_ava_cancel_is_provider_failure() -> None:
    channel, _ = _channel(
        [
            {
                "type": "error",
                "error": {
                    "code": "response_cancel_not_active",
                    "message": "No response was cancelled by Ava.",
                },
            }
        ]
    )

    with pytest.raises(
        OpenAIRealtimeUnavailableError,
        match="code=response_cancel_not_active",
    ):
        await anext(
            channel.stream_turn(
                harness=_harness(),
                turn_id="turn-without-cancel",
                text_input="Can we continue?",
                response_max_output_tokens=400,
            )
        )


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
