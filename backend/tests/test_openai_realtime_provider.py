from __future__ import annotations

from ava_backend.realtime.provider_session import RealtimeCommittedInput

import asyncio
from builtins import anext
import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from types import MethodType
from uuid import UUID

import httpx
import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from ava_backend.canvas.svg_workspace import RecordedSvgCanvasInteraction
from ava_backend.protocol import AgeBand
from ava_backend.providers import openai_realtime_provider
from ava_backend.providers.openai_realtime_provider import (
    MAX_OPENAI_REALTIME_CLIENT_ITEM_ID_CHARS,
    RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME,
    _SEMANTIC_CAPTURE_MAX_OUTPUT_TOKENS,
    OpenAIRealtimeConnectionClosedError,
    OpenAIRealtimeProbeRequest,
    OpenAIRealtimeProtocolError,
    OpenAIRealtimeProviderError,
    OpenAIRealtimeProviderSession,
    OpenAIRealtimeRouterBackpressureError,
    OpenAIRealtimeUnavailableError,
    _OpenAIRealtimeEventChannel,
    _OpenAIRealtimeReceiveTimeout,
    _session_update,
)
from ava_backend.realtime.harness import build_realtime_harness
from ava_backend.realtime.context_assembler import (
    DynamicContextAssembler,
    DynamicContextBuildReason,
)
from ava_backend.realtime.fakes import DETERMINISTIC_AUDIO_OFFER_SDP
from ava_backend.realtime.memory import (
    MAX_SEMANTIC_MEMORY_PROPOSAL_UTF8_BYTES,
    AuthoritativeMemorySnapshot,
    DialogueFrameState,
    SemanticCaptureDisposition,
    SemanticCaptureFunctionArguments,
    SemanticCaptureJournalDraft,
    SemanticJournalEventFamily,
)
from ava_backend.realtime.provider_session import (
    RealtimeContextInstallationStatus,
    RealtimeNegotiationResponseError,
    RealtimeProviderInputEvent,
    RealtimeProviderResponseIncompleteError,
    SemanticCaptureEvidence,
)
from ava_backend.realtime.tool_contract import (
    CANCEL_INTERACTIVE_CANVAS_TOOL_NAME,
    CANVAS_MODEL_SAFE_REJECTION_REASON,
    INTERACTIVE_CANVAS_TOOL_NAME,
    MANAGE_CANVAS_PRESENTATION_TOOL_NAME,
    GET_CURRENT_TIME_TOOL_NAME,
    SEARCH_MEMORY_TOOL_NAME,
    GetCurrentTimeToolOutputV4,
    InteractiveCanvasToolOutputV4,
    InteractiveCanvasCancellationToolOutputV4,
    ManageCanvasPresentationStatus,
    ManageCanvasPresentationToolOutputV4,
    RealtimeCancelCanvasToolCall,
    RealtimeCanvasToolCall,
    RealtimeRejectedCanvasToolCall,
    RealtimeGetCurrentTimeToolCall,
    RealtimeManageCanvasPresentationToolCall,
    RealtimeRejectedManageCanvasPresentationToolCall,
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
            session_id="session-negotiation-test",
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
    def __init__(
        self, events: list[dict[str, object]], *, acknowledge_updates: bool = False
    ) -> None:
        self.acknowledge_updates = acknowledge_updates
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
        if self.acknowledge_updates and self.sent[-1]["type"] == "session.update":
            self.events.appendleft(
                json.dumps({"type": "session.updated", "session": {"id": "sess_test"}})
            )
            self._available.set()
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


def _rate_limited_response_events(
    *,
    response_id: str,
    limit_kind: str = "tokens",
) -> list[dict[str, object]]:
    return [
        {"type": "response.created", "response": {"id": response_id}},
        {
            "type": "response.done",
            "response": {
                "id": response_id,
                "status": "failed",
                "status_details": {
                    "type": "failed",
                    "error": {
                        "type": limit_kind,
                        "code": "rate_limit_exceeded",
                    },
                },
                "output": [],
            },
        },
    ]


def _top_level_rate_limit_error(
    *,
    event_id: str | None,
    limit_kind: str = "tokens",
) -> dict[str, object]:
    error: dict[str, object] = {
        "type": limit_kind,
        "code": "rate_limit_exceeded",
        "message": "content-free deterministic rate limit",
    }
    if event_id is not None:
        error["event_id"] = event_id
    return {"type": "error", "error": error}


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


@pytest.mark.asyncio
async def test_canvas_control_call_retains_response_and_continuation_correlation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.openai_realtime")
    channel, _ = _channel(
        [
            *_canvas_response_events(
                arguments='{"action":"hide","completion":"needsResultReasoning"}',
                function_name=MANAGE_CANVAS_PRESENTATION_TOOL_NAME,
                preamble="I’ll close it. ",
            ),
            *_text_response_events(
                response_id=CONTINUATION_RESPONSE_ID,
                text="It’s closed.",
            ),
        ]
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-control-correlation",
    ).__aiter__()

    assert await anext(iterator) == RealtimeSpeechTextDelta(delta="I’ll close it. ")
    call = await anext(iterator)
    assert isinstance(call, RealtimeManageCanvasPresentationToolCall)
    assert call.provider_response_id == RESPONSE_ID
    await channel.complete_manage_canvas_presentation_tool(
        turn_id="turn-control-correlation",
        call_id=call.call_id,
        output=ManageCanvasPresentationToolOutputV4(
            status=ManageCanvasPresentationStatus.ACCEPTED,
            operation="hide",
            changed=True,
            visible=False,
        ),
    )
    assert await anext(iterator) == RealtimeSpeechTextDelta(delta="It’s closed.")
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)

    correlation = next(
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("canvas_control_continuation_started")
    )
    assert correlation == {
        "turnId": "turn-control-correlation",
        "functionCallId": FUNCTION_CALL_ID,
        "continuationResponseId": CONTINUATION_RESPONSE_ID,
    }
    await channel.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["new", "revise"])
async def test_successful_canvas_action_only_stops_after_one_function_output(
    operation: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.openai_realtime")
    arguments = json.dumps(
        {
            "operation": operation,
            **({"targetSceneId": "scene-retained"} if operation == "revise" else {}),
            "brief": "Show the water cycle.",
            "interactionMode": "none",
            "continuousControl": "none",
            "completion": "requestComplete",
        },
        separators=(",", ":"),
    )
    channel, socket = _channel(
        _canvas_response_events(arguments=arguments, preamble="")
    )

    received = []
    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-canvas-action-only",
    ):
        received.append(event)
        if isinstance(event, RealtimeCanvasToolCall):
            output = InteractiveCanvasToolOutputV4.model_validate(
                {"status": "accepted", "generationId": "generation-action-only"}
            )
            await channel.complete_canvas_tool(
                turn_id="turn-canvas-action-only",
                call_id=event.call_id,
                output=output,
            )
            with pytest.raises(OpenAIRealtimeProtocolError, match="completed twice"):
                await channel.complete_canvas_tool(
                    turn_id="turn-canvas-action-only",
                    call_id=event.call_id,
                    output=output,
                )

    assert [type(event) for event in received] == [RealtimeCanvasToolCall]
    function_outputs = [
        sent
        for sent in socket.sent
        if isinstance(sent.get("item"), dict)
        and sent["item"].get("type") == "function_call_output"
    ]
    assert len(function_outputs) == 1
    assert not [sent for sent in socket.sent if sent.get("type") == "response.create"]
    disposition = next(
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_canvas_disposition_resolved")
    )
    assert disposition["consumerTurnId"] == "turn-canvas-action-only"
    assert disposition["responseId"] == RESPONSE_ID
    assert disposition["callId"] == FUNCTION_CALL_ID
    assert disposition["toolKind"] == INTERACTIVE_CANVAS_TOOL_NAME
    assert disposition["requestedCompletion"] == "requestComplete"
    assert disposition["toolResultStatus"] == "accepted"
    assert disposition["continuationCreated"] is False


@pytest.mark.asyncio
async def test_successful_manage_canvas_action_only_stops_without_narration() -> None:
    channel, socket = _channel(
        _canvas_response_events(
            arguments='{"action":"hide","completion":"requestComplete"}',
            function_name=MANAGE_CANVAS_PRESENTATION_TOOL_NAME,
            preamble="",
        )
    )

    received = []
    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-manage-action-only",
    ):
        received.append(event)
        if isinstance(event, RealtimeManageCanvasPresentationToolCall):
            await channel.complete_manage_canvas_presentation_tool(
                turn_id="turn-manage-action-only",
                call_id=event.call_id,
                output=ManageCanvasPresentationToolOutputV4(
                    status=ManageCanvasPresentationStatus.ACCEPTED,
                    operation="hide",
                    changed=True,
                    visible=False,
                ),
            )

    assert [type(event) for event in received] == [
        RealtimeManageCanvasPresentationToolCall
    ]
    assert (
        len(
            [
                sent
                for sent in socket.sent
                if isinstance(sent.get("item"), dict)
                and sent["item"].get("type") == "function_call_output"
            ]
        )
        == 1
    )
    assert not [sent for sent in socket.sent if sent.get("type") == "response.create"]


@pytest.mark.asyncio
async def test_canvas_boundary_noop_action_only_stops_without_continuation() -> None:
    channel, socket = _channel(
        _canvas_response_events(
            arguments='{"action":"hide","completion":"requestComplete"}',
            function_name=MANAGE_CANVAS_PRESENTATION_TOOL_NAME,
            preamble="",
        )
    )

    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-manage-boundary-action-only",
    ):
        assert isinstance(event, RealtimeManageCanvasPresentationToolCall)
        await channel.complete_manage_canvas_presentation_tool(
            turn_id="turn-manage-boundary-action-only",
            call_id=event.call_id,
            output=ManageCanvasPresentationToolOutputV4(
                status=ManageCanvasPresentationStatus.ACCEPTED,
                operation="hide",
                changed=False,
                visible=False,
            ),
        )

    function_outputs = [
        sent
        for sent in socket.sent
        if isinstance(sent.get("item"), dict)
        and sent["item"].get("type") == "function_call_output"
    ]
    assert len(function_outputs) == 1
    assert not [sent for sent in socket.sent if sent.get("type") == "response.create"]


@pytest.mark.asyncio
async def test_successful_canvas_cancellation_action_only_stops_without_narration() -> (
    None
):
    channel, socket = _channel(
        _canvas_response_events(
            arguments='{"completion":"requestComplete"}',
            function_name=CANCEL_INTERACTIVE_CANVAS_TOOL_NAME,
            preamble="",
        )
    )

    received = []
    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-cancel-action-only",
    ):
        received.append(event)
        if isinstance(event, RealtimeCancelCanvasToolCall):
            await channel.complete_canvas_tool(
                turn_id="turn-cancel-action-only",
                call_id=event.call_id,
                output=InteractiveCanvasCancellationToolOutputV4.model_validate(
                    {"status": "accepted"}
                ),
            )

    assert [type(event) for event in received] == [RealtimeCancelCanvasToolCall]
    function_outputs = [
        sent
        for sent in socket.sent
        if isinstance(sent.get("item"), dict)
        and sent["item"].get("type") == "function_call_output"
    ]
    assert len(function_outputs) == 1
    assert not [sent for sent in socket.sent if sent.get("type") == "response.create"]


@pytest.mark.asyncio
async def test_failed_canvas_action_only_stops_after_exact_function_output(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.openai_realtime")
    arguments = (
        '{"operation":"new","brief":"Show the water cycle.",'
        '"interactionMode":"none","continuousControl":"none",'
        '"completion":"requestComplete"}'
    )
    channel, socket = _channel(
        _canvas_response_events(arguments=arguments, preamble="")
    )

    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-failed-action-only",
    ):
        if isinstance(event, RealtimeCanvasToolCall):
            await channel.complete_canvas_tool(
                turn_id="turn-failed-action-only",
                call_id=event.call_id,
                output=InteractiveCanvasToolOutputV4.model_validate(
                    {
                        "status": "rejected",
                        "reason": CANVAS_MODEL_SAFE_REJECTION_REASON,
                    }
                ),
            )

    response_creates = [
        sent for sent in socket.sent if sent.get("type") == "response.create"
    ]
    assert response_creates == []
    function_outputs = [
        sent
        for sent in socket.sent
        if isinstance(sent.get("item"), dict)
        and sent["item"].get("type") == "function_call_output"
    ]
    assert len(function_outputs) == 1
    disposition = next(
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_canvas_disposition_resolved")
    )
    assert disposition["requestedCompletion"] == "requestComplete"
    assert disposition["toolResultStatus"] == "rejected"
    assert disposition["continuationCreated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        (
            '{"operation":"new","brief":"Show the water cycle.",'
            '"interactionMode":"none","continuousControl":"none"}'
        ),
        (
            '{"operation":"new","brief":"Show the water cycle.",'
            '"interactionMode":"none","continuousControl":"none",'
            '"completion":"eventually"}'
        ),
    ],
)
async def test_missing_or_invalid_canvas_completion_is_rejected_without_fallback(
    arguments: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.openai_realtime")
    channel, socket = _channel(
        _canvas_response_events(arguments=arguments, preamble="")
    )

    received = []
    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-invalid-response-mode",
    ):
        received.append(event)
        assert isinstance(event, RealtimeRejectedCanvasToolCall)
        await channel.complete_canvas_tool(
            turn_id="turn-invalid-response-mode",
            call_id=event.call_id,
            output=InteractiveCanvasToolOutputV4.model_validate(
                {
                    "status": "rejected",
                    "reason": CANVAS_MODEL_SAFE_REJECTION_REASON,
                }
            ),
        )

    assert [type(event) for event in received] == [RealtimeRejectedCanvasToolCall]
    function_outputs = [
        sent
        for sent in socket.sent
        if isinstance(sent.get("item"), dict)
        and sent["item"].get("type") == "function_call_output"
    ]
    assert len(function_outputs) == 1
    assert not [sent for sent in socket.sent if sent.get("type") == "response.create"]
    disposition = next(
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_canvas_disposition_resolved")
    )
    assert disposition["requestedCompletion"] is None
    assert disposition["toolResultStatus"] == "rejected"
    assert disposition["continuationCreated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("function_name", "arguments"),
    [
        (CANCEL_INTERACTIVE_CANVAS_TOOL_NAME, "{}"),
        (MANAGE_CANVAS_PRESENTATION_TOOL_NAME, '{"action":"hide"}'),
    ],
)
async def test_missing_completion_rejects_other_canvas_actions_without_fallback(
    function_name: str,
    arguments: str,
) -> None:
    channel, socket = _channel(
        _canvas_response_events(
            arguments=arguments,
            function_name=function_name,
            preamble="",
        )
    )

    received = []
    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-other-invalid-response-mode",
    ):
        received.append(event)
        if isinstance(event, RealtimeRejectedManageCanvasPresentationToolCall):
            await channel.complete_manage_canvas_presentation_tool(
                turn_id="turn-other-invalid-response-mode",
                call_id=event.call_id,
                output=ManageCanvasPresentationToolOutputV4(
                    status=ManageCanvasPresentationStatus.REJECTED,
                    operation="hide",
                    reason=CANVAS_MODEL_SAFE_REJECTION_REASON,
                ),
            )
        else:
            assert isinstance(event, RealtimeRejectedCanvasToolCall)
            await channel.complete_canvas_tool(
                turn_id="turn-other-invalid-response-mode",
                call_id=event.call_id,
                output=InteractiveCanvasCancellationToolOutputV4.model_validate(
                    {
                        "status": "rejected",
                        "reason": CANVAS_MODEL_SAFE_REJECTION_REASON,
                    }
                ),
            )

    assert len(received) == 1
    assert not any(
        isinstance(
            event,
            (
                RealtimeCanvasToolCall,
                RealtimeCancelCanvasToolCall,
                RealtimeManageCanvasPresentationToolCall,
            ),
        )
        for event in received
    )
    function_outputs = [
        sent
        for sent in socket.sent
        if isinstance(sent.get("item"), dict)
        and sent["item"].get("type") == "function_call_output"
    ]
    assert len(function_outputs) == 1
    assert not [sent for sent in socket.sent if sent.get("type") == "response.create"]


@pytest.mark.asyncio
async def test_canvas_action_only_settles_before_same_channel_speech_turn() -> None:
    action_response_id = "resp_action_only_a"
    action_call_id = "call_action_only_a"
    speech_response_id = "resp_speech_b"
    arguments = (
        '{"operation":"new","brief":"Show the water cycle.",'
        '"interactionMode":"none","continuousControl":"none",'
        '"completion":"requestComplete"}'
    )
    channel, socket = _channel(
        [
            *_canvas_response_events(
                arguments=arguments,
                preamble="",
                response_id=action_response_id,
                call_id=action_call_id,
            ),
            {
                "type": "input_audio_buffer.committed",
                "item_id": "item_speech_b",
            },
            *_text_response_events(
                response_id=speech_response_id,
                text="Here is an ordinary next-turn answer.",
            ),
        ]
    )

    turn_a = []
    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-action-only-a",
        text_input="Draw the water cycle.",
    ):
        turn_a.append(event)
        if isinstance(event, RealtimeCanvasToolCall):
            await channel.complete_canvas_tool(
                turn_id="turn-action-only-a",
                call_id=event.call_id,
                output=InteractiveCanvasToolOutputV4.model_validate(
                    {"status": "accepted", "generationId": "generation-a"}
                ),
            )

    assert [type(event) for event in turn_a] == [RealtimeCanvasToolCall]
    assert channel._active_turn_id is None
    assert channel._active_response_id is None
    assert channel._pending_canvas_call_id is None
    assert channel._pending_canvas_completion is None
    assert channel._completed_canvas_call_ids == set()
    assert channel._completed_canvas_tools == {}
    assert channel._active_response_request is None
    assert channel._resource_deferred_turn_id is None
    assert channel._response_create_pending is False
    assert channel._awaiting_response_trigger_turn_id is None
    resource = channel.session_health.resource_governor
    assert resource is not None
    assert resource.local_reserved_requests == 0
    assert channel.session_health.continuation_count == 0
    assert channel.session_health.pending_response_count == 0

    turn_b = await _collect_turn_events(channel, turn_id="turn-speech-b")

    assert turn_b == [
        RealtimeSpeechTextDelta(delta="Here is an ordinary next-turn answer.")
    ]
    assert channel.conversation_ledger.response(action_response_id).local_turn_id == (
        "turn-action-only-a"
    )
    assert channel.conversation_ledger.response(speech_response_id).local_turn_id == (
        "turn-speech-b"
    )
    assert channel._input_item_turn_ids["item_speech_b"] == "turn-speech-b"
    response_creates = [
        event for event in socket.sent if event.get("type") == "response.create"
    ]
    assert len(response_creates) == 2
    assert not any(
        event["response"].get("tool_choice") == "none" for event in response_creates
    )


@pytest.mark.asyncio
async def test_canvas_action_only_does_not_contaminate_same_channel_continue_call() -> (
    None
):
    action_call_id = "call_action_only_first"
    continue_call_id = "call_continue_second"
    action_arguments = (
        '{"operation":"new","brief":"Show a cloud.",'
        '"interactionMode":"none","continuousControl":"none",'
        '"completion":"requestComplete"}'
    )
    continue_arguments = (
        '{"operation":"new","brief":"Show rain.",'
        '"interactionMode":"none","continuousControl":"none",'
        '"completion":"needsResultReasoning"}'
    )
    channel, socket = _channel(
        [
            *_canvas_response_events(
                arguments=action_arguments,
                preamble="",
                response_id="resp_action_only_first",
                call_id=action_call_id,
            ),
            *_canvas_response_events(
                arguments=continue_arguments,
                preamble="",
                response_id="resp_continue_second",
                call_id=continue_call_id,
            ),
            *_text_response_events(
                response_id="resp_continue_narration",
                text="The rain picture is on its way.",
            ),
        ]
    )

    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-action-only-first",
        text_input="Draw a cloud.",
    ):
        if isinstance(event, RealtimeCanvasToolCall):
            await channel.complete_canvas_tool(
                turn_id="turn-action-only-first",
                call_id=event.call_id,
                output=InteractiveCanvasToolOutputV4.model_validate(
                    {"status": "accepted", "generationId": "generation-first"}
                ),
            )

    turn_b = []
    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-continue-second",
        text_input="Now draw rain.",
    ):
        turn_b.append(event)
        if isinstance(event, RealtimeCanvasToolCall):
            with pytest.raises(
                OpenAIRealtimeProtocolError,
                match="does not match the pending call",
            ):
                await channel.complete_canvas_tool(
                    turn_id="turn-continue-second",
                    call_id=action_call_id,
                    output=InteractiveCanvasToolOutputV4.model_validate(
                        {
                            "status": "accepted",
                            "generationId": "generation-stale",
                        }
                    ),
                )
            await channel.complete_canvas_tool(
                turn_id="turn-continue-second",
                call_id=event.call_id,
                output=InteractiveCanvasToolOutputV4.model_validate(
                    {"status": "accepted", "generationId": "generation-second"}
                ),
            )

    assert [type(event) for event in turn_b] == [
        RealtimeCanvasToolCall,
        RealtimeSpeechTextDelta,
    ]
    assert turn_b[-1] == RealtimeSpeechTextDelta(
        delta="The rain picture is on its way."
    )
    assert channel._completed_canvas_call_ids == set()
    assert channel._completed_canvas_tools == {}
    assert channel._pending_canvas_completion is None
    assert channel._pending_canvas_control_completion is None
    response_creates = [
        event for event in socket.sent if event.get("type") == "response.create"
    ]
    assert len(response_creates) == 3
    assert (
        sum(
            event["response"].get("tool_choice") == "none" for event in response_creates
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("function_name", "arguments"),
    [
        (
            INTERACTIVE_CANVAS_TOOL_NAME,
            '{"operation":"new","brief":"Show a cloud.",'
            '"interactionMode":"none","continuousControl":"none",'
            '"completion":"requestComplete"}',
        ),
        (
            INTERACTIVE_CANVAS_TOOL_NAME,
            '{"operation":"revise","targetSceneId":"scene-retained","brief":"Add rain.",'
            '"interactionMode":"none","continuousControl":"none",'
            '"completion":"requestComplete"}',
        ),
        (
            CANCEL_INTERACTIVE_CANVAS_TOOL_NAME,
            '{"completion":"requestComplete"}',
        ),
    ],
)
async def test_aborted_canvas_completion_discards_local_disposition(
    function_name: str,
    arguments: str,
) -> None:
    channel, socket = _channel(
        _canvas_response_events(
            function_name=function_name,
            arguments=arguments,
            preamble="",
            response_id="resp_aborted_canvas",
            call_id="call_aborted_canvas",
        )
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-aborted-canvas",
    ).__aiter__()
    call = await anext(iterator)
    if isinstance(call, RealtimeCanvasToolCall):
        output: (
            InteractiveCanvasToolOutputV4 | InteractiveCanvasCancellationToolOutputV4
        ) = InteractiveCanvasToolOutputV4.model_validate(
            {"status": "accepted", "generationId": "generation-aborted"}
        )
    else:
        assert isinstance(call, RealtimeCancelCanvasToolCall)
        output = InteractiveCanvasCancellationToolOutputV4.model_validate(
            {"status": "accepted"}
        )
    await channel.complete_canvas_tool(
        turn_id="turn-aborted-canvas",
        call_id=call.call_id,
        output=output,
    )
    assert set(channel._completed_canvas_tools) == {"call_aborted_canvas"}
    assert channel._completed_canvas_call_ids == {"call_aborted_canvas"}
    assert channel._completed_canvas_control_tools == {}
    assert channel._completed_canvas_control_call_ids == set()

    await iterator.aclose()

    assert channel._completed_canvas_tools == {}
    assert channel._completed_canvas_call_ids == set()
    assert channel._pending_canvas_completion is None

    sent_before_cancel = len(socket.sent)
    cancellation = asyncio.create_task(channel.cancel(turn_id="turn-aborted-canvas"))
    await socket.wait_sent(sent_before_cancel + 1)
    cancel_event = socket.sent[-1]
    assert cancel_event["type"] == "response.cancel"
    socket.push(
        {
            "type": "error",
            "error": {
                "event_id": cancel_event["event_id"],
                "code": "response_cancel_not_active",
                "message": "The completed response is no longer active.",
            },
        }
    )
    await cancellation
    assert channel._active_turn_id is None
    assert channel._active_response_id is None


@pytest.mark.asyncio
async def test_aborted_canvas_control_completion_is_cleared_on_channel_close() -> None:
    channel, socket = _channel(
        _canvas_response_events(
            function_name=MANAGE_CANVAS_PRESENTATION_TOOL_NAME,
            arguments='{"action":"hide","completion":"requestComplete"}',
            preamble="",
            response_id="resp_aborted_control",
            call_id="call_aborted_control",
        )
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-aborted-control",
    ).__aiter__()
    call = await anext(iterator)
    assert isinstance(call, RealtimeManageCanvasPresentationToolCall)
    await channel.complete_manage_canvas_presentation_tool(
        turn_id="turn-aborted-control",
        call_id=call.call_id,
        output=ManageCanvasPresentationToolOutputV4(
            status=ManageCanvasPresentationStatus.ACCEPTED,
            operation="hide",
            changed=True,
            visible=False,
        ),
    )
    assert set(channel._completed_canvas_control_tools) == {"call_aborted_control"}
    assert channel._completed_canvas_control_call_ids == {"call_aborted_control"}
    assert channel._completed_canvas_tools == {}
    assert channel._completed_canvas_call_ids == set()
    assert (
        len(
            [
                event
                for event in socket.sent
                if isinstance(event.get("item"), dict)
                and event["item"].get("type") == "function_call_output"
            ]
        )
        == 1
    )

    await iterator.aclose()
    await channel.close()

    assert channel._completed_canvas_control_tools == {}
    assert channel._completed_canvas_control_call_ids == set()
    assert channel._pending_canvas_control_completion is None


@pytest.mark.asyncio
async def test_repeated_aborted_canvas_completions_do_not_accumulate() -> None:
    channel, socket = _channel([])

    for index in range(3):
        for event in _canvas_response_events(
            arguments=(
                '{"operation":"new","brief":"Show one cloud.",'
                '"interactionMode":"none","continuousControl":"none",'
                '"completion":"requestComplete"}'
            ),
            preamble="",
            response_id=f"resp_aborted_repeat_{index}",
            call_id=f"call_aborted_repeat_{index}",
        ):
            socket.push(event)
        turn_id = f"turn-aborted-repeat-{index}"
        iterator = channel.stream_turn(
            harness=_harness(),
            turn_id=turn_id,
        ).__aiter__()
        call = await anext(iterator)
        assert isinstance(call, RealtimeCanvasToolCall)
        await channel.complete_canvas_tool(
            turn_id=turn_id,
            call_id=call.call_id,
            output=InteractiveCanvasToolOutputV4.model_validate(
                {
                    "status": "accepted",
                    "generationId": f"generation-aborted-repeat-{index}",
                }
            ),
        )
        await iterator.aclose()
        assert channel._completed_canvas_tools == {}
        assert channel._completed_canvas_call_ids == set()

        sent_before_cancel = len(socket.sent)
        cancellation = asyncio.create_task(channel.cancel(turn_id=turn_id))
        await socket.wait_sent(sent_before_cancel + 1)
        cancel_event = socket.sent[-1]
        socket.push(
            {
                "type": "error",
                "error": {
                    "event_id": cancel_event["event_id"],
                    "code": "response_cancel_not_active",
                    "message": "The completed response is no longer active.",
                },
            }
        )
        await cancellation

    assert (
        len(
            [
                event
                for event in socket.sent
                if isinstance(event.get("item"), dict)
                and event["item"].get("type") == "function_call_output"
            ]
        )
        == 3
    )
    assert channel._completed_canvas_tools == {}
    assert channel._completed_canvas_call_ids == set()


def _channel(
    events: list[dict[str, object]],
    *,
    max_output_tokens: int = 800,
    provider_call_id: str | None = None,
    resource_capacity_waiter: (
        Callable[[float, asyncio.Event], Awaitable[bool]] | None
    ) = None,
    rate_limit_max_recovery_attempts: int = 2,
) -> tuple[
    _OpenAIRealtimeEventChannel,
    _FakeRealtimeSocket,
]:
    socket = _FakeRealtimeSocket(events, acknowledge_updates=True)
    channel = _OpenAIRealtimeEventChannel(
        socket,
        model="gpt-realtime-test",
        max_output_tokens=max_output_tokens,
        turn_timeout_seconds=5,
        provider_call_id=provider_call_id,
        session_id="session-provider-test",
        resource_capacity_waiter=(
            resource_capacity_waiter
            or openai_realtime_provider._default_resource_capacity_waiter
        ),
        rate_limit_max_recovery_attempts=rate_limit_max_recovery_attempts,
    )
    _TEST_CHANNELS.add(channel)
    return channel, socket


async def _collect_turn_events(
    channel: _OpenAIRealtimeEventChannel,
    *,
    turn_id: str,
    harness=None,  # type: ignore[no-untyped-def]
) -> list[object]:
    received: list[object] = []
    async for event in channel.stream_turn(
        harness=harness or _harness(), turn_id=turn_id
    ):
        if isinstance(event, RealtimeCommittedInput):
            await channel.request_child_response(
                turn_id=event.turn_id, item_id=event.item_id
            )
        else:
            received.append(event)
    return received


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
    await socket.wait_received(2)

    assert await channel.child_transcript_for_turn("turn-a") is None
    admitted = await channel.admit_pending_input_items(turn_id="turn-b")

    assert admitted == ("item-barge-in",)
    assert await channel.child_transcript_for_turn("turn-b") == "What happens next?"
    assert await channel.child_transcript_for_turn("turn-a") is None
    await channel.close()


@pytest.mark.asyncio
async def test_speech_started_input_admission_precedes_item_observation_cleanly() -> (
    None
):
    channel, socket = _channel(
        [
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "item-owner-before-commit",
                "audio_start_ms": 120,
            }
        ]
    )
    await channel.start_receiver()
    await socket.wait_received(1)

    assert await channel.admit_pending_input_items(
        turn_id="turn-owner-before-commit"
    ) == ("item-owner-before-commit",)
    assert (
        channel.input_item_owner(item_id="item-owner-before-commit")
        == "turn-owner-before-commit"
    )
    assert channel.conversation_ledger.item("item-owner-before-commit") is None
    assert channel.session_health.provider_item_observation_complete is True

    socket.push(
        {
            "type": "input_audio_buffer.committed",
            "item_id": "item-owner-before-commit",
        }
    )
    socket.push(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item-owner-before-commit",
            "transcript": "Where do rainbows come from?",
            "usage": {
                "type": "tokens",
                "input_tokens": 8,
                "input_token_details": {"text_tokens": 0, "audio_tokens": 8},
                "output_tokens": 3,
                "total_tokens": 11,
            },
        }
    )
    await socket.wait_received(3)

    observed_started = await channel.next_input_event()
    observed_committed = await channel.next_input_event()
    assert observed_started == RealtimeProviderInputEvent(
        event_type="input_audio_buffer.speech_started",
        item_id="item-owner-before-commit",
        audio_start_ms=120,
    )
    assert observed_committed == RealtimeProviderInputEvent(
        event_type="input_audio_buffer.committed",
        item_id="item-owner-before-commit",
    )
    item = channel.conversation_ledger.item("item-owner-before-commit")
    assert item is not None
    assert item.local_turn_id == "turn-owner-before-commit"
    assert item.logical_group_id == "turn:turn-owner-before-commit"
    assert item.input_transcription_usage is not None
    assert item.input_transcription_usage.local_turn_id == "turn-owner-before-commit"
    latest_transcription_usage = channel.session_health.latest_input_transcription_usage
    assert latest_transcription_usage is not None
    assert latest_transcription_usage.local_turn_id == "turn-owner-before-commit"
    assert channel.session_health.provider_item_observation_complete is True
    assert channel._receiver_error is None
    assert channel._receiver_task is not None
    assert channel._receiver_task.done() is False
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
        '"interactionMode":"none","continuousControl":"none",'
        '"completion":"needsResultReasoning"}'
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
        output=InteractiveCanvasToolOutputV4(
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
        MANAGE_CANVAS_PRESENTATION_TOOL_NAME,
        SEARCH_MEMORY_TOOL_NAME,
        GET_CURRENT_TIME_TOOL_NAME,
    ]
    canvas_tool = session["tools"][0]
    parameters = canvas_tool["parameters"]
    assert parameters["required"] == [
        "operation",
        "brief",
        "interactionMode",
        "continuousControl",
        "completion",
    ]
    assert parameters["properties"]["interactionMode"]["$ref"] == (
        "#/$defs/InteractiveCanvasMode"
    )
    assert parameters["$defs"]["InteractiveCanvasMode"]["enum"] == [
        "none",
        "explore",
        "select",
        "steps",
    ]
    assert parameters["properties"]["continuousControl"]["$ref"] == (
        "#/$defs/InteractiveCanvasContinuousControl"
    )
    assert parameters["$defs"]["InteractiveCanvasContinuousControl"]["enum"] == [
        "none",
        "drag",
        "slider",
        "timeline",
    ]
    turn_detection = session["audio"]["input"]["turn_detection"]
    assert session["audio"]["input"]["transcription"] == {"model": "gpt-transcribe"}
    assert turn_detection["create_response"] is False
    assert turn_detection["interrupt_response"] is False
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
    assert cancel_events == []
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
        "sessionId": "session-provider-test",
        "responseId": RESPONSE_ID,
        "turnId": "turn-cancelled",
        "activeContextBuildId": "ctx-1",
        "inputTokens": 3,
        "cachedInputTokens": 2,
        "uncachedInputTokens": 1,
        "inputTextTokens": 3,
        "inputAudioTokens": 0,
        "outputTokens": 0,
        "outputTextTokens": 0,
        "outputAudioTokens": 0,
        "totalTokens": 3,
        "observedProviderItemCount": 1,
        "responseCount": 2,
        "responseCountComplete": True,
        "functionCallCount": 0,
        "continuationCount": 0,
    }


@pytest.mark.asyncio
async def test_done_dequeued_before_cancel_settles_without_second_terminal() -> None:
    channel, socket = _channel([])
    await channel.prepare(harness=_harness())
    turn_id = "turn-done-before-cancel"
    response_id = "resp-done-before-cancel"
    socket.push({"type": "response.created", "response": {"id": response_id}})
    created = await channel._receive_event(timeout_seconds=1)
    channel._validate_response_owner(
        created,
        turn_id=turn_id,
        consumer_phase="turnResponse",
    )
    channel._active_turn_id = turn_id
    channel._active_response_id = response_id

    destructive_consumer = asyncio.create_task(
        channel._receive_event(timeout_seconds=1)
    )
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": response_id,
                "status": "completed",
                "output": [],
                "usage": _usage(3, 1),
            },
        }
    )
    terminal = await destructive_consumer
    assert terminal["type"] == "response.done"
    assert channel._response_lifecycle_by_id[response_id].terminal_observed

    sent_before_cancel = len(socket.sent)
    await asyncio.wait_for(channel.cancel(turn_id=turn_id), timeout=0.2)

    assert len(socket.sent) == sent_before_cancel
    lifecycle = channel._response_lifecycle_by_id[response_id]
    assert lifecycle.terminal_consumed
    assert lifecycle.settled
    assert channel._active_response_id is None
    assert channel._active_turn_id is None
    assert turn_id in channel._cancelled_turn_ids


@pytest.mark.asyncio
async def test_cancel_not_active_after_terminal_dequeued_by_other_consumer_settles(
    caplog: pytest.LogCaptureFixture,
) -> None:
    channel, socket = _channel([])
    await channel.prepare(harness=_harness())
    turn_id = "turn-done-before-cancel"
    response_id = "resp-done-before-cancel"
    socket.push({"type": "response.created", "response": {"id": response_id}})
    created = await channel._receive_event(timeout_seconds=1)
    channel._validate_response_owner(
        created,
        turn_id=turn_id,
        consumer_phase="turnResponse",
    )
    channel._active_turn_id = turn_id
    channel._active_response_id = response_id

    destructive_consumer = asyncio.create_task(
        channel._receive_event(timeout_seconds=1)
    )
    await asyncio.sleep(0)
    cancellation = asyncio.create_task(channel.cancel(turn_id=turn_id))
    await socket.wait_sent(2)
    cancel_event = socket.sent[-1]
    assert cancel_event["type"] == "response.cancel"

    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": response_id,
                "status": "completed",
                "output": [],
                "usage": _usage(3, 1),
            },
        }
    )
    terminal = await destructive_consumer
    assert terminal["type"] == "response.done"
    socket.push(
        {
            "type": "error",
            "error": {
                "event_id": cancel_event["event_id"],
                "code": "response_cancel_not_active",
                "message": "The completed response is no longer active.",
            },
        }
    )

    with caplog.at_level(logging.INFO, logger="ava_backend.openai_realtime"):
        await asyncio.wait_for(cancellation, timeout=0.2)

    lifecycle = channel._response_lifecycle_by_id[response_id]
    assert lifecycle.terminal_observed
    assert lifecycle.terminal_consumed
    assert lifecycle.settled
    assert lifecycle.terminal_sideband_sequence is not None
    assert channel._consumed_terminal_response_owner_by_id[response_id] == turn_id
    assert channel._active_response_id is None
    assert channel._active_turn_id is None
    assert turn_id in channel._cancelled_turn_ids
    assert (
        sum(
            record.getMessage().startswith("openai_realtime_cancellation_lifecycle")
            for record in caplog.records
        )
        == 1
    )

    for event in _text_response_events(
        response_id="resp-after-lost-terminal",
        text="The next turn responds.",
    ):
        socket.push(event)
    received = await _collect_turn_events(channel, turn_id="turn-after-lost-terminal")
    assert received == [RealtimeSpeechTextDelta(delta="The next turn responds.")]


@pytest.mark.asyncio
async def test_simultaneous_done_and_cancel_never_leaks_response_ownership() -> None:
    channel, socket = _channel([])
    await channel.prepare(harness=_harness())

    for index in range(30):
        turn_id = f"turn-race-{index}"
        response_id = f"resp-race-{index}"
        socket.push({"type": "response.created", "response": {"id": response_id}})
        created = await channel._receive_event(timeout_seconds=1)
        channel._validate_response_owner(
            created,
            turn_id=turn_id,
            consumer_phase="turnResponse",
        )
        channel._active_turn_id = turn_id
        channel._active_response_id = response_id
        terminal = {
            "type": "response.done",
            "response": {
                "id": response_id,
                "status": "cancelled",
                "output": [],
                "usage": _usage(3, 0),
            },
        }

        if index % 2 == 0:
            received_count = socket.received_count
            socket.push(terminal)
            await socket.wait_received(received_count + 1)
            await asyncio.sleep(0)
            await asyncio.wait_for(channel.cancel(turn_id=turn_id), timeout=0.2)
        else:
            sent_count = len(socket.sent)
            cancellation = asyncio.create_task(channel.cancel(turn_id=turn_id))
            await socket.wait_sent(sent_count + 1)
            socket.push(terminal)
            await asyncio.wait_for(cancellation, timeout=0.2)

        lifecycle = channel._response_lifecycle_by_id[response_id]
        assert lifecycle.terminal_observed
        assert lifecycle.terminal_consumed
        assert lifecycle.settled
        assert channel._consumed_terminal_response_owner_by_id[response_id] == turn_id
        assert channel._active_response_id is None
        assert channel._active_turn_id is None


@pytest.mark.asyncio
async def test_cancel_not_active_before_terminal_waits_for_legitimate_terminal() -> (
    None
):
    channel, socket = _channel([])
    await channel.prepare(harness=_harness())
    turn_id = "turn-cancel-error-first"
    response_id = "resp-cancel-error-first"
    socket.push({"type": "response.created", "response": {"id": response_id}})
    created = await channel._receive_event(timeout_seconds=1)
    channel._validate_response_owner(
        created,
        turn_id=turn_id,
        consumer_phase="turnResponse",
    )
    channel._active_turn_id = turn_id
    channel._active_response_id = response_id

    sent_count = len(socket.sent)
    cancellation = asyncio.create_task(channel.cancel(turn_id=turn_id))
    await socket.wait_sent(sent_count + 1)
    cancel_event = socket.sent[-1]
    received_count = socket.received_count
    socket.push(
        {
            "type": "error",
            "error": {
                "event_id": cancel_event["event_id"],
                "code": "response_cancel_not_active",
                "message": "The response terminal is still in transit.",
            },
        }
    )
    await socket.wait_received(received_count + 1)
    await asyncio.sleep(0)
    assert not cancellation.done()

    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": response_id,
                "status": "completed",
                "output": [],
                "usage": _usage(3, 1),
            },
        }
    )
    await asyncio.wait_for(cancellation, timeout=0.2)
    lifecycle = channel._response_lifecycle_by_id[response_id]
    assert lifecycle.terminal_observed
    assert lifecycle.terminal_consumed
    assert lifecycle.settled


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
@pytest.mark.parametrize("operation", ["new", "revise"])
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
            **({"targetSceneId": "scene-retained"} if operation == "revise" else {}),
            "brief": brief_sentinel,
            "interactionMode": "explore",
            "continuousControl": "none",
            "completion": "needsResultReasoning",
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
                output=InteractiveCanvasToolOutputV4.model_validate(
                    {
                        "status": "accepted",
                        "generationId": "generation-private",
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
    disposition = next(
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_canvas_disposition_resolved")
    )
    assert disposition["requestedCompletion"] == "needsResultReasoning"
    assert disposition["toolResultStatus"] == "ready"
    assert disposition["continuationCreated"] is True
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
            "sessionId": "session-provider-test",
            "responseId": RESPONSE_ID,
            "turnId": "turn-canvas",
            "activeContextBuildId": "ctx-1",
            "inputTokens": 10,
            "cachedInputTokens": 2,
            "uncachedInputTokens": 8,
            "inputTextTokens": 10,
            "inputAudioTokens": 0,
            "outputTokens": 4,
            "outputTextTokens": 4,
            "outputAudioTokens": 0,
            "totalTokens": 14,
            "observedProviderItemCount": 0,
            "responseCount": 2,
            "responseCountComplete": True,
            "functionCallCount": 0,
            "continuationCount": 0,
        },
        {
            "provider": "openai",
            "model": "gpt-realtime-test",
            "usageScope": "response",
            "providerCallId": "rtc_usage_canvas",
            "sessionId": "session-provider-test",
            "responseId": CONTINUATION_RESPONSE_ID,
            "turnId": "turn-canvas",
            "activeContextBuildId": "ctx-1",
            "inputTokens": 6,
            "cachedInputTokens": 2,
            "uncachedInputTokens": 4,
            "inputTextTokens": 6,
            "inputAudioTokens": 0,
            "outputTokens": 3,
            "outputTextTokens": 3,
            "outputAudioTokens": 0,
            "totalTokens": 9,
            "observedProviderItemCount": 0,
            "responseCount": 2,
            "responseCountComplete": True,
            "functionCallCount": 0,
            "continuationCount": 1,
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
            "continuousControl": "none",
            "completion": "needsResultReasoning",
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
                    '"interactionMode":"none","continuousControl":"none",'
                    '"completion":"needsResultReasoning"}'
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
                arguments='{"completion":"needsResultReasoning"}',
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
                arguments='{"completion":"needsResultReasoning"}',
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
async def test_rejected_canvas_continuation_is_outcome_owned_and_bounded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.openai_realtime")
    arguments = json.dumps(
        {
            "operation": "new",
            "brief": "visual-brief-A",
            "interactionMode": "none",
            "continuousControl": "none",
            "completion": "needsResultReasoning",
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
    disposition = next(
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_canvas_disposition_resolved")
    )
    assert disposition["requestedCompletion"] == "needsResultReasoning"
    assert disposition["toolResultStatus"] == "rejected"
    assert disposition["continuationCreated"] is True


@pytest.mark.asyncio
async def test_accepted_canvas_continuation_supplies_drawing_instructions() -> None:
    arguments = (
        '{"operation":"new","brief":"Show a water cycle.","interactionMode":"none",'
        '"continuousControl":"none","completion":"needsResultReasoning"}'
    )
    channel, socket = _channel(
        [
            *_canvas_response_events(arguments=arguments, preamble=""),
            *_text_response_events(
                response_id=CONTINUATION_RESPONSE_ID,
                text="I am drawing the water cycle for you now.",
            ),
        ]
    )

    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-canvas-accepted",
    ):
        if isinstance(event, RealtimeCanvasToolCall):
            await channel.complete_canvas_tool(
                turn_id="turn-canvas-accepted",
                call_id=event.call_id,
                output=InteractiveCanvasToolOutputV4.model_validate(
                    {
                        "status": "accepted",
                        "generationId": "gen-test-1",
                    }
                ),
            )

    continuation = [
        sent for sent in socket.sent if sent.get("type") == "response.create"
    ][-1]["response"]
    assert continuation["tool_choice"] == "none"
    assert "instructions" not in continuation


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
            '{"operation":"new","brief":"Show a cell.","interactionMode":"none",'
            '"continuousControl":"none","completion":"needsResultReasoning"}',
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
    await socket.wait_received(1)
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
    channel._observe_response_lifecycle(
        {"type": "response.created", "response": {"id": "resp-prior"}}
    )
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
    await socket.wait_received(1)
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
        '{"brief":"Show a water cycle.","completion":"needsResultReasoning"}',
        '{"operation":"revise","brief":"Show a water cycle.",'
        '"completion":"needsResultReasoning"}',
        '{"operation":"new","brief":42,"completion":"needsResultReasoning"}',
        '{"operation":"new","brief":"Show a slider.",'
        '"interactionMode":"slider","continuousControl":"slider",'
        '"completion":"needsResultReasoning"}',
        '{"operation":"new","brief":"Show ordered steps.",'
        '"interactionMode":"steps","continuousControl":"slider",'
        '"completion":"needsResultReasoning"}',
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
                output=InteractiveCanvasToolOutputV4(
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
                        "continuousControl": "none",
                        "completion": "needsResultReasoning",
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
async def test_canvas_presentation_validation_logs_content_free_argument_shape(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_title = "private provider title"
    channel, _socket = _channel(
        _canvas_response_events(
            arguments=json.dumps(
                {
                    "action": "hide",
                    "title": raw_title,
                    "completion": "requestComplete",
                }
            ),
            function_name=MANAGE_CANVAS_PRESENTATION_TOOL_NAME,
            preamble="",
        )
    )
    caplog.set_level(logging.WARNING, logger="ava_backend.openai_realtime")

    async for event in channel.stream_turn(
        harness=_harness(),
        turn_id="turn-invalid-presentation-shape",
    ):
        assert isinstance(event, RealtimeRejectedManageCanvasPresentationToolCall)
        await channel.complete_manage_canvas_presentation_tool(
            turn_id="turn-invalid-presentation-shape",
            call_id=event.call_id,
            output=ManageCanvasPresentationToolOutputV4(
                status=ManageCanvasPresentationStatus.REJECTED,
                operation="hide",
                reason=CANVAS_MODEL_SAFE_REJECTION_REASON,
            ),
        )

    record = next(
        item
        for item in caplog.records
        if item.getMessage().startswith("openai_canvas_presentation_arguments_rejected")
    )
    context = record.ava_context  # type: ignore[attr-defined]
    assert context["argumentShape"] == {
        "rootShape": "object",
        "action": "hide",
        "hasPosition": False,
        "hasTitle": True,
        "hasCompletion": True,
    }
    assert context["issues"] == [{"path": "$", "code": "value_error"}]
    assert raw_title not in caplog.text
    assert raw_title not in str(context)


@pytest.mark.asyncio
async def test_canvas_function_must_be_completed_exactly_once() -> None:
    arguments = (
        '{"operation":"new","brief":"Show the moon phases in order.",'
        '"interactionMode":"none","continuousControl":"none",'
        '"completion":"needsResultReasoning"}'
    )
    channel, _ = _channel(_canvas_response_events(arguments=arguments))
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-once",
    ).__aiter__()

    assert isinstance(await iterator.__anext__(), RealtimeSpeechTextDelta)
    call = await iterator.__anext__()
    assert isinstance(call, RealtimeCanvasToolCall)
    output = InteractiveCanvasToolOutputV4(
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
        '"interactionMode":"none","continuousControl":"none",'
        '"completion":"needsResultReasoning"}'
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
        '"interactionMode":"none","continuousControl":"none",'
        '"completion":"needsResultReasoning"}'
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
        output=InteractiveCanvasToolOutputV4(
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

    assert not any(
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith(
            "openai_realtime_cancel_not_active_waiting_for_terminal"
        )
    )
    settled = next(
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_cancellation_lifecycle")
    )
    assert settled["responseId"] == CONTINUATION_RESPONSE_ID
    assert settled["ownerTurnId"] == "turn-canvas-a"
    assert settled["terminalObserved"] is True
    assert settled["terminalConsumed"] is True
    assert settled["settled"] is True
    assert settled["terminalSidebandSequence"] == 12
    assert settled["cancelOutcome"] == "cancelNotActiveAfterTerminal"


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
    channel, socket = _channel([])
    await channel.prepare(harness=_harness())
    socket.push({"type": "response.created", "response": {"id": RESPONSE_ID}})
    created = await channel._receive_event(timeout_seconds=1)
    channel._validate_response_owner(
        created,
        turn_id="turn-completed-before-cancel",
        consumer_phase="turnResponse",
    )
    channel._active_turn_id = "turn-completed-before-cancel"
    channel._active_response_id = RESPONSE_ID

    sent_count = len(socket.sent)
    cancellation = asyncio.create_task(
        channel.cancel(turn_id="turn-completed-before-cancel")
    )
    await socket.wait_sent(sent_count + 1)
    cancel_event = socket.sent[-1]
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": RESPONSE_ID,
                "status": "completed",
                "output": [],
                "usage": _usage(3, 1),
            },
        }
    )
    await cancellation
    socket.push(next_turn_events[0])
    socket.push(
        {
            "type": "error",
            "error": {
                "event_id": cancel_event["event_id"],
                "code": "response_cancel_not_active",
                "message": "The completed response is no longer active.",
            },
        }
    )
    for event in next_turn_events[1:]:
        socket.push(event)
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
    channel, socket = _channel([])
    await channel.prepare(harness=_harness())
    socket.push({"type": "response.created", "response": {"id": "resp_cancelled_turn"}})
    created = await channel._receive_event(timeout_seconds=1)
    channel._validate_response_owner(
        created,
        turn_id="turn-cancelled",
        consumer_phase="turnResponse",
    )
    channel._active_turn_id = "turn-cancelled"
    channel._active_response_id = "resp_cancelled_turn"

    sent_count = len(socket.sent)
    cancellation = asyncio.create_task(channel.cancel(turn_id="turn-cancelled"))
    await socket.wait_sent(sent_count + 1)
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp_cancelled_turn",
                "status": "completed",
                "output": [],
                "usage": _usage(3, 1),
            },
        }
    )
    await cancellation
    for event in later_turn_events:
        socket.push(event)
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
    channel, socket = _channel([])
    await channel.prepare(harness=_harness())
    socket.push({"type": "response.created", "response": {"id": "resp_cancel_a"}})
    created_a = await channel._receive_event(timeout_seconds=1)
    channel._validate_response_owner(
        created_a,
        turn_id="turn-cancel-a",
        consumer_phase="turnResponse",
    )
    channel._active_turn_id = "turn-cancel-a"
    channel._active_response_id = "resp_cancel_a"
    sent_count = len(socket.sent)
    cancel_a = asyncio.create_task(channel.cancel(turn_id="turn-cancel-a"))
    await socket.wait_sent(sent_count + 1)
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp_cancel_a",
                "status": "completed",
                "output": [],
                "usage": _usage(3, 1),
            },
        }
    )
    await cancel_a

    socket.push({"type": "response.created", "response": {"id": "resp_cancel_b"}})
    created_b = await channel._receive_event(timeout_seconds=1)
    channel._validate_response_owner(
        created_b,
        turn_id="turn-cancel-b",
        consumer_phase="turnResponse",
    )
    channel._active_turn_id = "turn-cancel-b"
    channel._active_response_id = "resp_cancel_b"
    sent_count = len(socket.sent)
    cancel_b = asyncio.create_task(channel.cancel(turn_id="turn-cancel-b"))
    await socket.wait_sent(sent_count + 1)
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
    channel, socket = _channel([])
    await channel.prepare(harness=_harness())
    response_id = "resp_cancelled_before_unknown_error"
    turn_id = "turn-cancelled-before-unknown-error"
    socket.push({"type": "response.created", "response": {"id": response_id}})
    created = await channel._receive_event(timeout_seconds=1)
    channel._validate_response_owner(
        created,
        turn_id=turn_id,
        consumer_phase="turnResponse",
    )
    channel._active_turn_id = turn_id
    channel._active_response_id = response_id
    sent_count = len(socket.sent)
    cancellation = asyncio.create_task(channel.cancel(turn_id=turn_id))
    await socket.wait_sent(sent_count + 1)
    cancel_event = next(
        event for event in socket.sent if event["type"] == "response.cancel"
    )
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": response_id,
                "status": "completed",
                "output": [],
                "usage": _usage(3, 1),
            },
        }
    )
    await cancellation
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


@pytest.mark.asyncio
async def test_accepted_canvas_tool_completion_contains_no_response_instructions() -> (
    None
):
    channel, socket = _channel(
        [
            *_canvas_response_events(
                arguments=(
                    '{"operation":"new","brief":"Draw a star.",'
                    '"interactionMode":"none","continuousControl":"none",'
                    '"completion":"needsResultReasoning"}'
                ),
                preamble="",
            ),
            *_text_response_events(
                response_id=CONTINUATION_RESPONSE_ID,
                text="The drawing has started.",
            ),
        ]
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-canvas-p2",
    ).__aiter__()

    call = await iterator.__anext__()
    assert isinstance(call, RealtimeCanvasToolCall)
    await channel.complete_canvas_tool(
        turn_id="turn-canvas-p2",
        call_id=call.call_id,
        output=InteractiveCanvasToolOutputV4.model_validate(
            {"status": "accepted", "generationId": "generation-p2"}
        ),
    )
    assert await anext(iterator) == RealtimeSpeechTextDelta(
        delta="The drawing has started."
    )
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)

    response_create_events = [
        msg for msg in socket.sent if msg.get("type") == "response.create"
    ]
    assert len(response_create_events) == 1
    response_payload = response_create_events[0]["response"]
    assert isinstance(response_payload, dict)
    assert response_payload["conversation"] == "auto"
    assert response_payload["output_modalities"] == ["text"]
    assert response_payload["tool_choice"] == "none"
    assert "max_output_tokens" in response_payload
    # P2: response-level instructions MUST NOT be present
    assert "instructions" not in response_payload

    # Harness prompt layer must still retain the drawing behavior
    harness = _harness()
    prompt_text = harness.instructions
    assert "Ava can create and revise validated lower-panel visuals" in prompt_text
    assert "deny drawing capability" in prompt_text


@pytest.mark.asyncio
async def test_prepare_if_idle_not_ready_before_send() -> None:
    """prepare_if_idle returns None before sending if channel is not idle/ready."""
    channel, socket = _channel([])
    await channel.prepare(harness=_harness())
    initial_sent_count = len(socket.sent)

    # Channel has no active turn awaiting trigger, so readiness is not ready
    new_harness = _harness(build_id="ctx-2")
    status = await channel.prepare_if_idle(harness=new_harness, turn_id="turn-inactive")

    assert status == RealtimeContextInstallationStatus.DEDUPLICATED
    # Identical context does not require another wire update.
    assert len(socket.sent) == initial_sent_count


@pytest.mark.asyncio
async def test_prepare_if_idle_readiness_changes_after_send() -> None:
    """prepare_if_idle must return non-None once update is sent, even if readiness changes."""

    class _MutatingSocket(_FakeRealtimeSocket):
        def __init__(self, events: list[dict[str, object]]) -> None:
            super().__init__(events)
            self.channel_ref: _OpenAIRealtimeEventChannel | None = None

        async def send(self, message: str) -> None:
            parsed = json.loads(message)
            if parsed.get("type") == "session.update" and self.channel_ref is not None:
                if self.channel_ref._prepared_session_update is not None:
                    # Mutate readiness to not-ready (e.g. response becomes active)
                    self.channel_ref._active_response_id = "resp_racing"
            await super().send(message)

    socket = _MutatingSocket(
        [{"type": "session.updated", "session": {"id": "sess_test"}}]
    )
    channel = _OpenAIRealtimeEventChannel(
        socket,
        model="gpt-realtime-test",
        max_output_tokens=800,
        turn_timeout_seconds=5,
    )
    socket.channel_ref = channel
    _TEST_CHANNELS.add(channel)

    # Initial prepare
    await channel.prepare(harness=_harness())
    initial_sent_count = len(socket.sent)

    # Set up channel so turn-active is initially idle/ready
    turn_id = "turn-active"
    channel._active_turn_id = turn_id
    channel._awaiting_response_trigger_turn_id = turn_id

    # Precondition: channel readiness is initially None (ready)
    assert channel._canvas_interaction_response_readiness(turn_id) is None

    new_harness = _harness(build_id="ctx-2", timezone_id="America/New_York")
    status = await channel.prepare_if_idle(harness=new_harness, turn_id=turn_id)

    # session.update was sent onto the wire
    assert len(socket.sent) == initial_sent_count + 1
    session_update_msg = socket.sent[-1]
    assert session_update_msg.get("type") == "session.update"

    # Point of no return: once sent, prepare_if_idle must NOT return None
    assert status == RealtimeContextInstallationStatus.SUBMITTED
    assert channel._installed_context_build_id != new_harness.context.build_id
    socket.push({"type": "session.updated", "session": {"id": "sess_test"}})
    await channel.wait_for_context(build_id=new_harness.context.build_id)
    assert channel._installed_context_build_id == new_harness.context.build_id


@pytest.mark.asyncio
async def test_prepare_context_unacknowledged_until_session_updated() -> None:
    socket = _FakeRealtimeSocket([], acknowledge_updates=True)
    channel = _OpenAIRealtimeEventChannel(
        socket,
        model="gpt-realtime-test",
        max_output_tokens=800,
        turn_timeout_seconds=5,
    )
    _TEST_CHANNELS.add(channel)
    await channel.start_receiver()

    # 1. Initial prepare succeeds and is immediately installed/acknowledged
    status_a = await channel.prepare(harness=_harness(build_id="ctx-1"))
    assert status_a == RealtimeContextInstallationStatus.INSTALLED
    assert channel.desired_context_build_id == "ctx-1"
    assert channel.active_context_build_id == "ctx-1"
    assert channel.is_context_acknowledged("ctx-1") is True

    # 2. Prepare context B (non-initial) with unacknowledged socket updates
    socket.acknowledge_updates = False
    status_b = await channel.prepare(
        harness=_harness(build_id="ctx-2", timezone_id="America/New_York")
    )

    # Before session.updated:
    assert status_b == RealtimeContextInstallationStatus.SUBMITTED
    assert channel.desired_context_build_id == "ctx-2"
    assert channel._inflight_context is not None
    assert channel._inflight_context[2] == "ctx-2"
    assert channel.active_context_build_id == "ctx-1"
    assert channel.is_context_acknowledged("ctx-2") is False

    # 3. Deliver session.updated for B
    socket.push({"type": "session.updated", "session": {"id": "sess_b"}})
    await channel.wait_for_context(build_id="ctx-2")

    # After session.updated:
    assert channel.is_context_acknowledged("ctx-2") is True
    assert channel.active_context_build_id == "ctx-2"
    await channel.close()


@pytest.mark.asyncio
async def test_context_coalescing_and_acknowledgement() -> None:
    socket = _FakeRealtimeSocket([], acknowledge_updates=True)
    channel = _OpenAIRealtimeEventChannel(
        socket,
        model="gpt-realtime-test",
        max_output_tokens=800,
        turn_timeout_seconds=5,
    )
    _TEST_CHANNELS.add(channel)
    await channel.start_receiver()

    # Initial prepare A
    await channel.prepare(harness=_harness(build_id="ctx-1"))
    assert channel.is_context_acknowledged("ctx-1") is True

    # Prepare B (inflight) without auto-ack
    socket.acknowledge_updates = False
    status_b = await channel.prepare(
        harness=_harness(build_id="ctx-2", timezone_id="America/New_York")
    )
    assert status_b == RealtimeContextInstallationStatus.SUBMITTED
    assert channel._inflight_context[2] == "ctx-2"

    # Prepare C while B is inflight (supersedes B)
    status_c = await channel.prepare(
        harness=_harness(build_id="ctx-3", timezone_id="America/Chicago")
    )
    assert status_c == RealtimeContextInstallationStatus.SUBMITTED
    assert channel.desired_context_build_id == "ctx-3"
    # B remains inflight on the wire until B ACK arrives
    assert channel._inflight_context[2] == "ctx-2"

    # Now deliver B ACK
    socket.push({"type": "session.updated", "session": {"id": "sess_b"}})
    await asyncio.sleep(0.01)

    # B was acknowledged, but C is the latest desired context and is NOT yet acknowledged
    assert channel.is_context_acknowledged("ctx-2") is True
    assert channel.is_context_acknowledged("ctx-3") is False
    # C was now sent onto the wire
    assert channel._inflight_context is not None
    assert channel._inflight_context[2] == "ctx-3"

    # Deliver C ACK
    socket.push({"type": "session.updated", "session": {"id": "sess_c"}})
    await channel.wait_for_context(build_id="ctx-3")
    assert channel.is_context_acknowledged("ctx-3") is True
    assert channel.active_context_build_id == "ctx-3"
    await channel.close()


@pytest.mark.asyncio
async def test_request_child_response_pending_create_returns_false_and_does_not_commit() -> (
    None
):
    socket = _FakeRealtimeSocket([], acknowledge_updates=True)
    channel = _OpenAIRealtimeEventChannel(
        socket, model="test", max_output_tokens=800, turn_timeout_seconds=5
    )
    _TEST_CHANNELS.add(channel)
    await channel.prepare(harness=_harness(build_id="ctx-1"))
    channel._active_turn_id = "turn-child"
    channel._input_item_turn_ids["item-pending"] = "turn-child"

    # Simulate response_create_pending is already True
    channel._response_create_pending = True

    committed = await channel.request_child_response(
        turn_id="turn-child",
        item_id="item-pending",
    )
    # A. response_create_pending -> no send, no committed item, False
    assert committed is False
    assert "item-pending" not in channel._committed_response_items
    assert not any(event.get("type") == "response.create" for event in socket.sent)
    await channel.close()


@pytest.mark.asyncio
async def test_request_child_response_active_response_returns_false_and_does_not_commit() -> (
    None
):
    socket = _FakeRealtimeSocket([], acknowledge_updates=True)
    channel = _OpenAIRealtimeEventChannel(
        socket, model="test", max_output_tokens=800, turn_timeout_seconds=5
    )
    _TEST_CHANNELS.add(channel)
    await channel.prepare(harness=_harness(build_id="ctx-1"))
    channel._active_turn_id = "turn-child"
    channel._input_item_turn_ids["item-active"] = "turn-child"

    # Simulate active response already exists
    channel._active_response_id = "resp-active-123"

    committed = await channel.request_child_response(
        turn_id="turn-child",
        item_id="item-active",
    )
    # B. active response -> no send, no committed item, False
    assert committed is False
    assert "item-active" not in channel._committed_response_items
    assert not any(event.get("type") == "response.create" for event in socket.sent)
    await channel.close()


@pytest.mark.asyncio
async def test_request_child_response_successful_readiness_commits_exactly_once() -> (
    None
):
    socket = _FakeRealtimeSocket([], acknowledge_updates=True)
    channel = _OpenAIRealtimeEventChannel(
        socket, model="test", max_output_tokens=800, turn_timeout_seconds=5
    )
    _TEST_CHANNELS.add(channel)
    await channel.prepare(harness=_harness(build_id="ctx-1"))
    channel._active_turn_id = "turn-child"
    channel._input_item_turn_ids["item-success"] = "turn-child"

    # C. successful readiness -> exactly one send, item committed, True
    committed = await channel.request_child_response(
        turn_id="turn-child",
        item_id="item-success",
    )
    assert committed is True
    assert "item-success" in channel._committed_response_items
    response_create_events = [
        e for e in socket.sent if e.get("type") == "response.create"
    ]
    assert len(response_create_events) == 1

    # Calling again with the same item_id is idempotent: returns True, does not duplicate send
    committed_again = await channel.request_child_response(
        turn_id="turn-child",
        item_id="item-success",
    )
    assert committed_again is True
    response_create_events_after = [
        e for e in socket.sent if e.get("type") == "response.create"
    ]
    assert len(response_create_events_after) == 1
    await channel.close()


@pytest.mark.asyncio
async def test_client_conversation_item_ids_are_unique_and_provider_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_uuids = iter([UUID(hex="1" * 32), UUID(hex="2" * 32)])
    monkeypatch.setattr(
        openai_realtime_provider,
        "uuid4",
        lambda: next(generated_uuids),
    )
    channel, socket = _channel([])
    item = {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "test input"}],
    }

    first_item_id = await channel._create_conversation_item(
        turn_id="turn-client-item-1",
        item=item,
    )
    second_item_id = await channel._create_conversation_item(
        turn_id="turn-client-item-2",
        item=item,
    )

    assert first_item_id
    assert first_item_id.startswith("ava_")
    assert len(first_item_id) == MAX_OPENAI_REALTIME_CLIENT_ITEM_ID_CHARS
    assert len(second_item_id) <= MAX_OPENAI_REALTIME_CLIENT_ITEM_ID_CHARS
    assert first_item_id != second_item_id
    created_item_ids = [
        event["item"]["id"]
        for event in socket.sent
        if event.get("type") == "conversation.item.create"
    ]
    assert created_item_ids == ["ava_" + "1" * 28, "ava_" + "2" * 28]
    await channel.close()


@pytest.mark.asyncio
async def test_provider_ledger_and_health_follow_the_owned_tool_continuation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ava_backend.openai_realtime")
    arguments = json.dumps(
        {
            "operation": "new",
            "brief": "Show the water cycle.",
            "interactionMode": "none",
            "continuousControl": "none",
            "completion": "needsResultReasoning",
        },
        separators=(",", ":"),
    )
    initial_response = _canvas_response_events(arguments=arguments)
    function_added = initial_response[2]["item"]
    assert isinstance(function_added, dict)
    function_added["id"] = "item-function-call"
    done_response = initial_response[-1]["response"]
    assert isinstance(done_response, dict)
    output = done_response["output"]
    assert isinstance(output, list)
    assert isinstance(output[0], dict)
    assert isinstance(output[1], dict)
    output[0]["id"] = "item-assistant-preamble"
    output[1]["id"] = "item-function-call"

    channel, socket = _channel(
        [
            {
                "type": "rate_limits.updated",
                "rate_limits": [
                    {
                        "name": "tokens",
                        "limit": 50_000,
                        "remaining": 49_000,
                        "reset_seconds": 1.5,
                    },
                    {
                        "name": "requests",
                        "limit": 300,
                        "remaining": 299,
                        "reset_seconds": 0.2,
                    },
                    {
                        "name": "future_limit",
                        "limit": 1,
                        "remaining": 1,
                        "reset_seconds": 0,
                    },
                ],
            },
            {
                "type": "input_audio_buffer.committed",
                "item_id": "item-child-input",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-child-input",
                "transcript": "",
                "usage": {
                    "type": "tokens",
                    "input_tokens": 8,
                    "input_token_details": {
                        "text_tokens": 0,
                        "audio_tokens": 8,
                    },
                    "output_tokens": 2,
                    "total_tokens": 10,
                },
            },
            *initial_response,
        ],
        provider_call_id="rtc-ledger-health",
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-ledger-health",
    ).__aiter__()

    committed = await anext(iterator)
    assert committed == RealtimeCommittedInput(
        turn_id="turn-ledger-health",
        item_id="item-child-input",
    )
    assert await channel.request_child_response(
        turn_id=committed.turn_id,
        item_id=committed.item_id,
    )
    assert await anext(iterator) == RealtimeSpeechTextDelta(
        delta="I’ll show that clearly. "
    )
    call = await anext(iterator)
    assert isinstance(call, RealtimeCanvasToolCall)
    await channel.complete_canvas_tool(
        turn_id="turn-ledger-health",
        call_id=call.call_id,
        output=InteractiveCanvasToolOutputV4.model_validate(
            {
                "status": "accepted",
                "generationId": "generation-private",
            }
        ),
    )
    function_output_request = next(
        event
        for event in reversed(socket.sent)
        if event.get("type") == "conversation.item.create"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "function_call_output"
    )
    function_output_item = function_output_request["item"]
    assert isinstance(function_output_item, dict)
    function_output_item_id = function_output_item["id"]
    assert isinstance(function_output_item_id, str)
    assert function_output_item_id.startswith("ava_")
    assert len(function_output_item_id) <= MAX_OPENAI_REALTIME_CLIENT_ITEM_ID_CHARS
    socket.push(
        {
            "type": "conversation.item.added",
            "item": {
                "id": function_output_item_id,
                "type": "function_call_output",
                "call_id": FUNCTION_CALL_ID,
                "output": '{"status":"ready"}',
            },
        }
    )
    socket.push(
        {
            "type": "response.created",
            "response": {"id": CONTINUATION_RESPONSE_ID},
        }
    )
    socket.push(
        {
            "type": "response.output_item.added",
            "response_id": CONTINUATION_RESPONSE_ID,
            "item": {
                "id": "item-assistant-continuation",
                "type": "message",
                "role": "assistant",
            },
        }
    )
    socket.push(
        {
            "type": "response.output_text.delta",
            "response_id": CONTINUATION_RESPONSE_ID,
            "item_id": "item-assistant-continuation",
            "delta": "Here it is.",
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
                        "id": "item-assistant-continuation",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Here it is."}],
                    }
                ],
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "total_tokens": 15,
                    "input_token_details": {
                        "cached_tokens": 5,
                        "text_tokens": 12,
                        "audio_tokens": 0,
                    },
                    "output_token_details": {
                        "text_tokens": 3,
                        "audio_tokens": 0,
                    },
                },
            },
        }
    )

    assert await anext(iterator) == RealtimeSpeechTextDelta(delta="Here it is.")
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)

    items = {item.provider_item_id: item for item in channel.conversation_ledger.items}
    assert set(items) == {
        "item-child-input",
        "item-assistant-preamble",
        "item-function-call",
        function_output_item_id,
        "item-assistant-continuation",
    }
    assert {item.local_turn_id for item in items.values()} == {"turn-ledger-health"}
    assert {item.logical_group_id for item in items.values()} == {
        "turn:turn-ledger-health"
    }
    assert items[function_output_item_id].dependency_call_id == FUNCTION_CALL_ID
    assert items["item-assistant-continuation"].dependency_call_id == FUNCTION_CALL_ID
    transcription_usage = items["item-child-input"].input_transcription_usage
    assert transcription_usage is not None
    assert transcription_usage.local_turn_id == "turn-ledger-health"
    assert transcription_usage.input_tokens == 8
    assert transcription_usage.input_audio_tokens == 8
    assert transcription_usage.output_tokens == 2

    health = channel.session_health
    assert health.session_id == "session-provider-test"
    assert health.provider_call_id == "rtc-ledger-health"
    assert health.observed_provider_item_count == 5
    assert health.provider_item_observation_complete is True
    assert health.provider_inventory_authoritative is False
    assert health.observability_degraded is False
    assert health.response_count == 2
    assert health.function_call_count == 1
    assert health.continuation_count == 1
    assert health.pending_response_count == 0
    assert health.latest_usage is not None
    assert health.latest_usage.provider_response_id == CONTINUATION_RESPONSE_ID
    assert health.latest_usage.local_turn_id == "turn-ledger-health"
    assert health.latest_usage.input_tokens == 12
    assert health.latest_usage.cached_input_tokens == 5
    assert health.latest_usage.uncached_input_tokens == 7
    assert health.latest_usage.output_tokens == 3
    continuation = channel.conversation_ledger.response(CONTINUATION_RESPONSE_ID)
    assert continuation is not None
    assert continuation.usage == health.latest_usage
    assert health.tpm is not None and health.tpm.remaining == 49_000
    assert health.rpm is not None and health.rpm.remaining == 299
    assert [entry.name for entry in health.rate_limits] == [
        "tokens",
        "requests",
        "future_limit",
    ]

    await channel.close()
    assert channel.session_health.closed is True
    assert channel.session_health.observed_provider_item_count == 0
    closing_health = [
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_session_health")
        and record.ava_context["reason"] == "sessionClosed"
    ]
    assert len(closing_health) == 1
    assert closing_health[0]["closed"] is True
    assert closing_health[0]["observedProviderItemCount"] == 0
    assert closing_health[0]["providerInventoryAuthoritative"] is False


@pytest.mark.asyncio
async def test_provider_usage_without_cache_details_remains_unknown() -> None:
    events = _text_response_events(
        response_id="response-without-cache-details",
        text="Cache details are optional.",
        input_tokens=9,
        output_tokens=4,
    )
    done = events[-1]["response"]
    assert isinstance(done, dict)
    usage = done["usage"]
    assert isinstance(usage, dict)
    usage.pop("input_token_details")
    channel, _ = _channel(events)

    received = await _collect_turn_events(channel, turn_id="turn-without-cache-details")

    assert received == [RealtimeSpeechTextDelta(delta="Cache details are optional.")]
    health = channel.session_health
    assert health.latest_usage is not None
    assert health.latest_usage.input_tokens == 9
    assert health.latest_usage.cached_input_tokens is None
    assert health.latest_usage.uncached_input_tokens is None
    assert health.latest_usage.output_tokens == 4
    assert health.latest_usage.total_tokens == 13
    assert health.cumulative_cached_input_tokens is None
    assert health.cumulative_uncached_input_tokens is None


@pytest.mark.asyncio
async def test_receiver_isolates_observability_failure_and_routes_the_event(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="ava_backend.openai_realtime")
    channel, socket = _channel([], provider_call_id="rtc-observation-failure")
    ledger = channel.conversation_ledger
    original_observer = ledger.observe_provider_event
    observation_count = 0

    def fail_first_observation(
        event: Mapping[str, object],
        *,
        observed_sequence: int,
    ) -> None:
        nonlocal observation_count
        observation_count += 1
        if observation_count == 1:
            raise RuntimeError("provider-payload-sentinel-must-not-be-logged")
        original_observer(event, observed_sequence=observed_sequence)

    monkeypatch.setattr(ledger, "observe_provider_event", fail_first_observation)
    socket.push(
        {
            "type": "input_audio_buffer.speech_started",
            "item_id": "item-observation-failure",
            "audio_start_ms": 12,
        }
    )

    observed_input = await channel.next_input_event()
    routed = await channel._receive_event(timeout_seconds=1)
    socket.push(
        {
            "type": "input_audio_buffer.committed",
            "item_id": "item-after-observation-failure",
        }
    )
    observed_after_failure = await channel.next_input_event()
    routed_after_failure = await channel._receive_event(timeout_seconds=1)

    assert observed_input == RealtimeProviderInputEvent(
        event_type="input_audio_buffer.speech_started",
        item_id="item-observation-failure",
        audio_start_ms=12,
    )
    assert routed["type"] == "input_audio_buffer.speech_started"
    assert observed_after_failure == RealtimeProviderInputEvent(
        event_type="input_audio_buffer.committed",
        item_id="item-after-observation-failure",
    )
    assert routed_after_failure["type"] == "input_audio_buffer.committed"
    assert channel._receiver_error is None
    assert channel._receiver_task is not None
    assert channel._receiver_task.done() is False
    health = channel.session_health
    assert health.observability_degraded is True
    assert health.provider_item_observation_complete is False
    assert health.response_count_complete is False
    assert health.observed_provider_item_count == 1
    warnings = [
        record
        for record in caplog.records
        if record.getMessage().startswith("openai_realtime_observability_degraded")
    ]
    assert len(warnings) == 1
    assert warnings[0].ava_context["operation"] == "observeProviderEvent"
    assert warnings[0].ava_context["errorType"] == "RuntimeError"
    assert "provider-payload-sentinel-must-not-be-logged" not in caplog.text
    await channel.close()


@pytest.mark.asyncio
async def test_response_admission_observability_failure_cannot_own_or_fail_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_id = "response-observation-admission-failure"
    channel, _ = _channel(
        _text_response_events(
            response_id=response_id,
            text="The provider lifecycle still completes.",
        )
    )

    def fail_response_observation(
        *,
        provider_response_id: str,
        local_turn_id: str,
    ) -> None:
        del provider_response_id, local_turn_id
        raise RuntimeError("controlled observability admission failure")

    monkeypatch.setattr(
        channel.conversation_ledger,
        "assign_response_owner",
        fail_response_observation,
    )

    received = await _collect_turn_events(
        channel,
        turn_id="turn-observation-admission-failure",
    )

    assert received == [
        RealtimeSpeechTextDelta(delta="The provider lifecycle still completes.")
    ]
    assert channel._response_owner_by_id[response_id] == (
        "turn-observation-admission-failure"
    )
    observed_response = channel.conversation_ledger.response(response_id)
    assert observed_response is not None
    assert observed_response.local_turn_id is None
    assert channel._receiver_error is None
    assert channel.session_health.observability_degraded is True
    await channel.close()


@pytest.mark.asyncio
async def test_low_tpm_defers_primary_response_until_provider_capacity_update() -> None:
    socket_ref: _FakeRealtimeSocket | None = None

    async def update_capacity(
        wait_seconds: float,
        changed: asyncio.Event,
    ) -> bool:
        assert 0 < wait_seconds <= 2
        assert socket_ref is not None
        assert not any(
            event.get("type") == "response.create" for event in socket_ref.sent
        )
        previous_received = socket_ref.received_count
        socket_ref.push(
            {
                "type": "rate_limits.updated",
                "rate_limits": [
                    {
                        "name": "tokens",
                        "limit": 50_000,
                        "remaining": 40_000,
                        "reset_seconds": 60,
                    }
                ],
            }
        )
        await socket_ref.wait_received(previous_received + 1)
        assert changed.is_set()
        return True

    channel, socket = _channel(
        [
            {
                "type": "rate_limits.updated",
                "rate_limits": [
                    {
                        "name": "tokens",
                        "limit": 50_000,
                        "remaining": 100,
                        "reset_seconds": 2,
                    }
                ],
            },
            {"type": "input_audio_buffer.committed", "item_id": "item-low-tpm"},
        ],
        resource_capacity_waiter=update_capacity,
    )
    socket_ref = socket
    channel._resource_governor.observe_actual_usage(
        turn_id="turn-calibration",
        input_tokens=2_000,
        output_tokens=100,
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-low-tpm",
    ).__aiter__()
    committed = await anext(iterator)
    assert committed == RealtimeCommittedInput(
        turn_id="turn-low-tpm", item_id="item-low-tpm"
    )

    assert not await channel.request_child_response(
        turn_id="turn-low-tpm", item_id="item-low-tpm"
    )
    assert await channel.wait_until_response_admissible(turn_id="turn-low-tpm")
    assert await channel.request_child_response(
        turn_id="turn-low-tpm", item_id="item-low-tpm"
    )
    assert (
        len([event for event in socket.sent if event.get("type") == "response.create"])
        == 1
    )
    resource = channel.session_health.resource_governor
    assert resource is not None
    assert resource.resource_deferral_count == 1
    await iterator.aclose()


@pytest.mark.asyncio
async def test_structured_rate_limit_retries_only_response_generation() -> None:
    async def reset_elapsed(_: float, __: asyncio.Event) -> bool:
        return False

    channel, socket = _channel(
        [
            *_rate_limited_response_events(response_id="response-rate-limited"),
            *_text_response_events(
                response_id="response-recovered",
                text="Recovered safely.",
                input_tokens=900,
                output_tokens=4,
            ),
        ],
        resource_capacity_waiter=reset_elapsed,
    )

    events = [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id="turn-rate-limited",
            text_input="Please answer once.",
            response_max_output_tokens=400,
        )
    ]

    assert events == [RealtimeSpeechTextDelta(delta="Recovered safely.")]
    user_items = [
        event
        for event in socket.sent
        if event.get("type") == "conversation.item.create"
        and isinstance(event.get("item"), dict)
        and event["item"].get("role") == "user"
    ]
    assert len(user_items) == 1
    assert (
        len([event for event in socket.sent if event.get("type") == "response.create"])
        == 2
    )
    recovered = channel.conversation_ledger.response("response-recovered")
    assert recovered is not None
    assert recovered.local_turn_id == "turn-rate-limited"
    resource = channel.session_health.resource_governor
    assert resource is not None
    assert resource.rate_limit_recovery_count == 1
    assert resource.local_reserved_requests == 0
    assert channel._semantic_response_count == 1


@pytest.mark.asyncio
async def test_three_actual_semantic_responses_are_allowed_and_fourth_is_bounded() -> (
    None
):
    channel, socket = _channel([])
    turn_id = "turn-semantic-chain-limit"
    channel._active_turn_id = turn_id

    for response_number in range(1, 4):
        created, decision = await channel._create_response_locked(
            turn_id=turn_id,
            response_kind=f"testResponse{response_number}",
            response={"max_output_tokens": 64},
            output_token_limits=(64,),
        )
        assert created is True
        assert decision is not None
        assert decision.estimate.output_token_limits == (64,)
        assert channel._semantic_response_count == response_number

        provider_response_id = f"response-{response_number}"
        channel._resource_governor.bind_provider_response(
            turn_id=turn_id,
            provider_response_id=provider_response_id,
        )
        channel._resource_governor.reconcile_response(
            turn_id=turn_id,
            provider_response_id=provider_response_id,
            input_tokens=100 + response_number,
            output_tokens=10,
        )
        channel._response_create_pending = False
        channel._active_response_request = None
        resource = channel.session_health.resource_governor
        assert resource is not None
        assert resource.local_reserved_requests == 0
        assert resource.local_reserved_tokens == 0

    with pytest.raises(
        OpenAIRealtimeProtocolError,
        match="logical turn exceeded Ava's semantic response chain",
    ):
        await channel._create_response_locked(
            turn_id=turn_id,
            response_kind="testResponse4",
            response={"max_output_tokens": 64},
            output_token_limits=(64,),
        )

    assert channel._semantic_response_count == 3
    assert (
        len([event for event in socket.sent if event.get("type") == "response.create"])
        == 3
    )
    channel._resource_governor.release_turn(turn_id=turn_id)


@pytest.mark.parametrize("limit_kind", ["tokens", "requests"])
@pytest.mark.asyncio
async def test_rate_limit_fresh_insufficient_snapshot_waits_for_sufficient_truth(
    limit_kind: str,
) -> None:
    wait_calls: asyncio.Queue[tuple[float, asyncio.Event]] = asyncio.Queue()
    wait_results: asyncio.Queue[bool] = asyncio.Queue()

    async def controlled_waiter(
        wait_seconds: float,
        changed: asyncio.Event,
    ) -> bool:
        await wait_calls.put((wait_seconds, changed))
        return await wait_results.get()

    channel, socket = _channel(
        _rate_limited_response_events(
            response_id=f"response-{limit_kind}-limited",
            limit_kind=limit_kind,
        ),
        resource_capacity_waiter=controlled_waiter,
    )

    async def collect_turn() -> list[object]:
        return [
            event
            async for event in channel.stream_turn(
                harness=_harness(),
                turn_id=f"turn-{limit_kind}-fresh-snapshot",
                text_input="Use the exact response once.",
                response_max_output_tokens=400,
            )
        ]

    turn = asyncio.create_task(collect_turn())

    _, first_changed = await wait_calls.get()
    response_creates = [
        event for event in socket.sent if event.get("type") == "response.create"
    ]
    assert len(response_creates) == 1
    previous_received = socket.received_count
    socket.push(
        {
            "type": "rate_limits.updated",
            "rate_limits": [
                {
                    "name": limit_kind,
                    "limit": 500_000 if limit_kind == "tokens" else 100,
                    "remaining": 0,
                    "reset_seconds": 60,
                }
            ],
        }
    )
    await socket.wait_received(previous_received + 1)
    assert first_changed.is_set()
    await wait_results.put(True)

    _, second_changed = await wait_calls.get()
    assert (
        len([event for event in socket.sent if event.get("type") == "response.create"])
        == 1
    )
    previous_received = socket.received_count
    socket.push(
        {
            "type": "rate_limits.updated",
            "rate_limits": [
                {
                    "name": limit_kind,
                    "limit": 500_000 if limit_kind == "tokens" else 100,
                    "remaining": 500_000 if limit_kind == "tokens" else 10,
                    "reset_seconds": 60,
                }
            ],
        }
    )
    await socket.wait_received(previous_received + 1)
    assert second_changed.is_set()
    await wait_results.put(True)
    await socket.wait_sent(4)
    for event in _text_response_events(
        response_id=f"response-{limit_kind}-recovered",
        text="Recovered from fresh provider truth.",
    ):
        socket.push(event)

    assert await turn == [
        RealtimeSpeechTextDelta(delta="Recovered from fresh provider truth.")
    ]
    assert (
        len([event for event in socket.sent if event.get("type") == "response.create"])
        == 2
    )


@pytest.mark.asyncio
async def test_correlated_top_level_rate_limit_retries_with_new_client_event_id() -> (
    None
):
    async def reset_elapsed(_: float, __: asyncio.Event) -> bool:
        return False

    channel, socket = _channel([], resource_capacity_waiter=reset_elapsed)

    async def collect_turn() -> list[object]:
        return [
            event
            async for event in channel.stream_turn(
                harness=_harness(),
                turn_id="turn-correlated-top-level",
                text_input="Send this input only once.",
                response_max_output_tokens=400,
            )
        ]

    turn = asyncio.create_task(collect_turn())
    await socket.wait_sent(2)
    first_create = next(
        event for event in socket.sent if event.get("type") == "response.create"
    )
    first_event_id = first_create.get("event_id")
    assert isinstance(first_event_id, str)
    sent_before_error = len(socket.sent)
    socket.push(_top_level_rate_limit_error(event_id=first_event_id))
    await socket.wait_sent(sent_before_error + 1)
    response_creates = [
        event for event in socket.sent if event.get("type") == "response.create"
    ]
    assert len(response_creates) == 2
    retry_event_id = response_creates[1].get("event_id")
    assert isinstance(retry_event_id, str)
    assert retry_event_id != first_event_id
    assert len(first_event_id) == len(retry_event_id) == 32

    # A late error for the rejected attempt is explicitly stale and cannot own
    # the new attempt.
    socket.push(_top_level_rate_limit_error(event_id=first_event_id))
    for event in _text_response_events(
        response_id="response-correlated-top-level-recovered",
        text="Recovered once.",
    ):
        socket.push(event)

    assert await turn == [RealtimeSpeechTextDelta(delta="Recovered once.")]
    user_items = [
        event
        for event in socket.sent
        if event.get("type") == "conversation.item.create"
        and isinstance(event.get("item"), dict)
        and event["item"].get("role") == "user"
    ]
    assert len(user_items) == 1
    assert channel._semantic_response_count == 1


@pytest.mark.asyncio
async def test_correlated_top_level_rate_limit_after_function_output_retries_only_continuation() -> (
    None
):
    async def reset_elapsed(_: float, __: asyncio.Event) -> bool:
        return False

    arguments = json.dumps(
        {
            "operation": "new",
            "brief": "Show one triangle.",
            "interactionMode": "none",
            "continuousControl": "none",
            "completion": "needsResultReasoning",
        },
        separators=(",", ":"),
    )
    channel, socket = _channel(
        _canvas_response_events(arguments=arguments),
        resource_capacity_waiter=reset_elapsed,
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-correlated-continuation",
        text_input="Draw one triangle.",
        response_max_output_tokens=400,
    ).__aiter__()
    assert isinstance(await anext(iterator), RealtimeSpeechTextDelta)
    call = await anext(iterator)
    assert isinstance(call, RealtimeCanvasToolCall)
    await channel.complete_canvas_tool(
        turn_id="turn-correlated-continuation",
        call_id=call.call_id,
        output=InteractiveCanvasToolOutputV4.model_validate(
            {"status": "accepted", "generationId": "generation-correlated"}
        ),
    )
    pending = asyncio.create_task(anext(iterator))
    await socket.wait_sent(5)
    response_creates = [
        event for event in socket.sent if event.get("type") == "response.create"
    ]
    assert len(response_creates) == 2
    continuation_event_id = response_creates[-1].get("event_id")
    assert isinstance(continuation_event_id, str)
    sent_before_error = len(socket.sent)
    socket.push(_top_level_rate_limit_error(event_id=continuation_event_id))
    await socket.wait_sent(sent_before_error + 1)
    for event in _text_response_events(
        response_id="response-correlated-continuation-recovered",
        text="Continuation recovered once.",
    ):
        socket.push(event)
    assert await pending == RealtimeSpeechTextDelta(
        delta="Continuation recovered once."
    )
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
    function_outputs = [
        event
        for event in socket.sent
        if event.get("type") == "conversation.item.create"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "function_call_output"
    ]
    assert len(function_outputs) == 1
    assert (
        len([event for event in socket.sent if event.get("type") == "response.create"])
        == 3
    )
    assert channel._semantic_response_count == 2


@pytest.mark.parametrize("error_event_id", [None, "mismatched-response-create"])
@pytest.mark.asyncio
async def test_uncorrelated_top_level_rate_limit_does_not_retry_or_wedge_session(
    error_event_id: str | None,
) -> None:
    channel, socket = _channel([])

    async def collect_old_turn() -> list[object]:
        return [
            event
            async for event in channel.stream_turn(
                harness=_harness(),
                turn_id="turn-uncorrelated-rate-limit",
                text_input="Do not replay this input.",
                response_max_output_tokens=400,
            )
        ]

    old_turn = asyncio.create_task(collect_old_turn())
    await socket.wait_sent(2)
    socket.push(_top_level_rate_limit_error(event_id=error_event_id))
    with pytest.raises(
        RealtimeProviderResponseIncompleteError,
        match="uncorrelated_rate_limit",
    ):
        await old_turn
    assert (
        len([event for event in socket.sent if event.get("type") == "response.create"])
        == 1
    )
    resource = channel.session_health.resource_governor
    assert resource is not None
    assert resource.local_reserved_requests == 0
    assert resource.deferred_turn_id is None
    assert channel._active_response_request is None

    for event in _text_response_events(
        response_id=f"response-after-uncorrelated-{error_event_id}",
        text="The later turn remains usable.",
    ):
        socket.push(event)
    later = [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id=f"turn-after-uncorrelated-{error_event_id}",
            text_input="Start a later turn.",
            response_max_output_tokens=400,
        )
    ]
    assert later == [RealtimeSpeechTextDelta(delta="The later turn remains usable.")]


@pytest.mark.parametrize("limit_kind", ["tokens", "requests"])
@pytest.mark.asyncio
async def test_continuation_rechecks_latest_provider_capacity_without_replay(
    limit_kind: str,
) -> None:
    waiter_entered = asyncio.Event()
    permit_retry = asyncio.Event()

    async def controlled_waiter(_: float, __: asyncio.Event) -> bool:
        waiter_entered.set()
        await permit_retry.wait()
        return True

    arguments = json.dumps(
        {
            "operation": "new",
            "brief": "Show one triangle.",
            "interactionMode": "none",
            "continuousControl": "none",
            "completion": "needsResultReasoning",
        },
        separators=(",", ":"),
    )
    channel, socket = _channel(
        _canvas_response_events(arguments=arguments),
        resource_capacity_waiter=controlled_waiter,
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id=f"turn-{limit_kind}-continuation-capacity",
        text_input="Draw one triangle.",
        response_max_output_tokens=400,
    ).__aiter__()
    assert isinstance(await anext(iterator), RealtimeSpeechTextDelta)
    call = await anext(iterator)
    assert isinstance(call, RealtimeCanvasToolCall)

    previous_received = socket.received_count
    socket.push(
        {
            "type": "rate_limits.updated",
            "rate_limits": [
                {
                    "name": limit_kind,
                    "limit": 50_000 if limit_kind == "tokens" else 100,
                    "remaining": 1 if limit_kind == "tokens" else 0,
                    "reset_seconds": 60,
                }
            ],
        }
    )
    await socket.wait_received(previous_received + 1)
    await channel.complete_canvas_tool(
        turn_id=f"turn-{limit_kind}-continuation-capacity",
        call_id=call.call_id,
        output=InteractiveCanvasToolOutputV4.model_validate(
            {"status": "accepted", "generationId": "generation-capacity"}
        ),
    )
    continuation = asyncio.create_task(anext(iterator))
    await waiter_entered.wait()
    function_outputs = [
        event
        for event in socket.sent
        if event.get("type") == "conversation.item.create"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "function_call_output"
    ]
    assert len(function_outputs) == 1
    assert (
        len([event for event in socket.sent if event.get("type") == "response.create"])
        == 1
    )

    previous_received = socket.received_count
    socket.push(
        {
            "type": "rate_limits.updated",
            "rate_limits": [
                {
                    "name": limit_kind,
                    "limit": 50_000 if limit_kind == "tokens" else 100,
                    "remaining": 50_000 if limit_kind == "tokens" else 10,
                    "reset_seconds": 60,
                }
            ],
        }
    )
    await socket.wait_received(previous_received + 1)
    permit_retry.set()
    await socket.wait_sent(5)
    assert (
        len([event for event in socket.sent if event.get("type") == "response.create"])
        == 2
    )
    for event in _text_response_events(
        response_id=f"response-{limit_kind}-continuation",
        text="Continuation admitted once.",
    ):
        socket.push(event)
    assert await continuation == RealtimeSpeechTextDelta(
        delta="Continuation admitted once."
    )
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
    assert (
        len(
            [
                event
                for event in socket.sent
                if event.get("type") == "conversation.item.create"
                and isinstance(event.get("item"), dict)
                and event["item"].get("type") == "function_call_output"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_search_memory_exact_output_defers_continuation_without_duplicate_output() -> (
    None
):
    waiter_entered = asyncio.Event()

    async def wait_for_snapshot(_: float, changed: asyncio.Event) -> bool:
        waiter_entered.set()
        await changed.wait()
        return True

    channel, socket = _channel(
        _canvas_response_events(
            arguments=json.dumps(
                {"query": "a bounded historical detail"}, separators=(",", ":")
            ),
            function_name=SEARCH_MEMORY_TOOL_NAME,
            preamble="",
        ),
        resource_capacity_waiter=wait_for_snapshot,
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-search-output-capacity",
        text_input="Remember our earlier conversation.",
        response_max_output_tokens=400,
    ).__aiter__()
    call = await anext(iterator)
    assert isinstance(call, RealtimeSearchMemoryToolCall)

    output = SearchMemoryToolOutputV4.model_validate(
        {
            "status": "available",
            "references": [
                {
                    "sourceKind": "rawEvent",
                    "timezoneIds": ["UTC"],
                    "text": f"memory-reference-{index}-" + ("x" * 1_175),
                }
                for index in range(5)
            ],
        }
    )
    serialized_output = output.model_dump_json(by_alias=True, exclude_none=True)
    output_utf8_bytes = len(serialized_output.encode("utf-8"))
    assert output_utf8_bytes > 5_000

    previous_received = socket.received_count
    socket.push(
        {
            "type": "rate_limits.updated",
            "rate_limits": [
                {
                    "name": "tokens",
                    "limit": 40_000,
                    "remaining": 1_000,
                    "reset_seconds": 60,
                }
            ],
        }
    )
    await socket.wait_received(previous_received + 1)
    completion = asyncio.create_task(
        channel.complete_utility_tool(
            turn_id="turn-search-output-capacity",
            call_id=call.call_id,
            output=output,
            harness=_harness(),
        )
    )
    await waiter_entered.wait()

    function_outputs = [
        event
        for event in socket.sent
        if event.get("type") == "conversation.item.create"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "function_call_output"
    ]
    assert len(function_outputs) == 1
    assert function_outputs[0]["item"]["output"] == serialized_output
    assert (
        len([event for event in socket.sent if event.get("type") == "response.create"])
        == 1
    )
    resource = channel.session_health.resource_governor
    assert resource is not None
    expected_input = 10 + 4 + output_utf8_bytes
    assert resource.estimated_logical_turn_responses == 1
    assert resource.estimated_logical_turn_tokens == expected_input + 512

    previous_received = socket.received_count
    socket.push(
        {
            "type": "rate_limits.updated",
            "rate_limits": [
                {
                    "name": "tokens",
                    "limit": 40_000,
                    "remaining": 40_000,
                    "reset_seconds": 60,
                }
            ],
        }
    )
    await socket.wait_received(previous_received + 1)
    await completion
    assert (
        len([event for event in socket.sent if event.get("type") == "response.create"])
        == 2
    )
    assert (
        len(
            [
                event
                for event in socket.sent
                if event.get("type") == "conversation.item.create"
                and isinstance(event.get("item"), dict)
                and event["item"].get("type") == "function_call_output"
            ]
        )
        == 1
    )

    for event in _text_response_events(
        response_id="response-search-output-capacity",
        text="I found the bounded reference.",
    ):
        socket.push(event)
    assert await anext(iterator) == RealtimeSpeechTextDelta(
        delta="I found the bounded reference."
    )
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)


@pytest.mark.asyncio
async def test_rate_limited_canvas_continuation_does_not_duplicate_tool_work() -> None:
    async def reset_elapsed(_: float, __: asyncio.Event) -> bool:
        return False

    arguments = json.dumps(
        {
            "operation": "new",
            "brief": "Show one triangle.",
            "interactionMode": "none",
            "continuousControl": "none",
            "completion": "needsResultReasoning",
        },
        separators=(",", ":"),
    )
    initial = _canvas_response_events(arguments=arguments)
    channel, socket = _channel(
        [
            *initial,
            *_rate_limited_response_events(response_id="response-continuation-limited"),
            *_text_response_events(
                response_id="response-continuation-recovered",
                text="Here it is.",
                input_tokens=1_200,
                output_tokens=4,
            ),
        ],
        resource_capacity_waiter=reset_elapsed,
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-tool-rate-limit",
        text_input="Draw a triangle.",
        response_max_output_tokens=400,
    ).__aiter__()

    assert await anext(iterator) == RealtimeSpeechTextDelta(
        delta="I’ll show that clearly. "
    )
    call = await anext(iterator)
    assert isinstance(call, RealtimeCanvasToolCall)
    await channel.complete_canvas_tool(
        turn_id="turn-tool-rate-limit",
        call_id=call.call_id,
        output=InteractiveCanvasToolOutputV4.model_validate(
            {"status": "accepted", "generationId": "generation-rate-limit"}
        ),
    )
    assert await anext(iterator) == RealtimeSpeechTextDelta(delta="Here it is.")
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)

    function_outputs = [
        event
        for event in socket.sent
        if event.get("type") == "conversation.item.create"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "function_call_output"
    ]
    assert len(function_outputs) == 1
    assert (
        len([event for event in socket.sent if event.get("type") == "response.create"])
        == 3
    )
    recovered = channel.conversation_ledger.response("response-continuation-recovered")
    assert recovered is not None
    assert recovered.local_turn_id == "turn-tool-rate-limit"
    assert recovered.continuation_of_call_id == FUNCTION_CALL_ID


@pytest.mark.asyncio
async def test_interruption_during_rate_limit_wait_prevents_stale_retry() -> None:
    waiter_entered = asyncio.Event()

    async def blocked_waiter(_: float, changed: asyncio.Event) -> bool:
        waiter_entered.set()
        await changed.wait()
        return True

    channel, socket = _channel(
        _rate_limited_response_events(response_id="response-interrupted"),
        resource_capacity_waiter=blocked_waiter,
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-interrupted-recovery",
        text_input="Do not replay me.",
        response_max_output_tokens=400,
    ).__aiter__()
    pending = asyncio.create_task(anext(iterator))
    await waiter_entered.wait()

    await channel.cancel(turn_id="turn-interrupted-recovery")
    with pytest.raises(StopAsyncIteration):
        await pending

    assert (
        len([event for event in socket.sent if event.get("type") == "response.create"])
        == 1
    )
    assert (
        len(
            [
                event
                for event in socket.sent
                if event.get("type") == "conversation.item.create"
            ]
        )
        == 1
    )
    resource = channel.session_health.resource_governor
    assert resource is not None
    assert resource.local_reserved_requests == 0
    assert resource.deferred_turn_id is None


@pytest.mark.asyncio
async def test_cancellation_during_primary_admission_defer_releases_reservation() -> (
    None
):
    waiter_entered = asyncio.Event()

    async def blocked_waiter(_: float, changed: asyncio.Event) -> bool:
        waiter_entered.set()
        await changed.wait()
        return True

    channel, socket = _channel(
        [
            {
                "type": "rate_limits.updated",
                "rate_limits": [
                    {
                        "name": "tokens",
                        "limit": 50_000,
                        "remaining": 0,
                        "reset_seconds": 2,
                    }
                ],
            },
            {
                "type": "input_audio_buffer.committed",
                "item_id": "item-deferred-cancel",
            },
        ],
        resource_capacity_waiter=blocked_waiter,
    )
    channel._resource_governor.observe_actual_usage(
        turn_id="turn-calibration",
        input_tokens=1_000,
        output_tokens=100,
    )
    iterator = channel.stream_turn(
        harness=_harness(), turn_id="turn-deferred-cancel"
    ).__aiter__()
    committed = await anext(iterator)
    assert isinstance(committed, RealtimeCommittedInput)
    assert not await channel.request_child_response(
        turn_id=committed.turn_id,
        item_id=committed.item_id,
    )
    waiting = asyncio.create_task(
        channel.wait_until_response_admissible(turn_id=committed.turn_id)
    )
    await waiter_entered.wait()

    await channel.cancel(turn_id=committed.turn_id)
    assert await waiting is False
    assert not any(event.get("type") == "response.create" for event in socket.sent)
    resource = channel.session_health.resource_governor
    assert resource is not None
    assert resource.local_reserved_requests == 0
    assert resource.deferred_turn_id is None
    await iterator.aclose()


@pytest.mark.asyncio
async def test_repeated_rate_limits_exhaust_bound_without_wedging_next_turn() -> None:
    async def reset_elapsed(_: float, __: asyncio.Event) -> bool:
        return False

    channel, socket = _channel(
        [
            *_rate_limited_response_events(response_id="response-limited-1"),
            *_rate_limited_response_events(response_id="response-limited-2"),
        ],
        resource_capacity_waiter=reset_elapsed,
        rate_limit_max_recovery_attempts=1,
    )
    iterator = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-exhausted",
        text_input="Try once.",
        response_max_output_tokens=400,
    ).__aiter__()

    with pytest.raises(
        RealtimeProviderResponseIncompleteError,
        match="rate_limit_recovery_exhausted",
    ):
        await anext(iterator)
    exhausted = channel.session_health.resource_governor
    assert exhausted is not None
    assert exhausted.state.value == "providerUnavailable"
    assert exhausted.local_reserved_requests == 0
    assert channel._active_response_request is None
    assert channel._resource_deferred_turn_id is None

    for event in _text_response_events(
        response_id="response-next-turn",
        text="The next turn works.",
    ):
        socket.push(event)
    next_events = [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id="turn-after-exhaustion",
            text_input="New turn.",
            response_max_output_tokens=400,
        )
    ]
    assert next_events == [RealtimeSpeechTextDelta(delta="The next turn works.")]


def _semantic_capture_response_events(
    *,
    response_id: str = "resp-capture-1",
    source_turn_id: str = "turn-cap-1",
    arguments: str = '{"disposition":"noChange"}',
    input_tokens: int = 100,
    output_tokens: int = 20,
) -> list[dict[str, object]]:
    return [
        {
            "type": "response.created",
            "response": {
                "id": response_id,
                "metadata": {
                    "purpose": "semantic_capture",
                    "source_turn_id": source_turn_id,
                },
            },
        },
        {
            "type": "response.output_item.added",
            "response_id": response_id,
            "item": {
                "type": "function_call",
                "name": RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME,
                "call_id": f"call_{response_id}",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "response_id": response_id,
            "call_id": f"call_{response_id}",
            "delta": arguments,
        },
        {
            "type": "response.function_call_arguments.done",
            "response_id": response_id,
            "name": RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME,
            "call_id": f"call_{response_id}",
            "arguments": arguments,
        },
        {
            "type": "response.output_item.done",
            "response_id": response_id,
            "item": {
                "type": "function_call",
                "name": RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME,
                "call_id": f"call_{response_id}",
                "arguments": arguments,
            },
        },
        {
            "type": "response.done",
            "response": {
                "id": response_id,
                "status": "completed",
                "usage": _usage(input_tokens, output_tokens),
            },
        },
    ]


@pytest.mark.asyncio
async def test_semantic_capture_payload_is_hermetic() -> None:
    channel, socket = _channel([])
    evidence = SemanticCaptureEvidence(
        source_turn_id="turn-hermetic-1",
        current_frame=DialogueFrameState(),
        child_utterance="I like Jupiter.",
        spoken_response_prefix="Jupiter is a giant gas planet.",
        interrupted=False,
    )
    capture_task = asyncio.create_task(
        channel.capture_semantic_memory(evidence=evidence)
    )
    await socket.wait_sent(1)
    sent_create = socket.sent[-1]

    assert sent_create["type"] == "response.create"
    response_spec = sent_create["response"]
    assert isinstance(response_spec, dict)
    assert response_spec["conversation"] == "none"
    assert response_spec["input"] == []
    assert response_spec["output_modalities"] == ["text"]
    assert "modalities" not in response_spec
    governor = channel._resource_governor
    assert governor._capture_reservation is not None
    governor_capture_limit = governor._capture_reservation.estimate.output_token_limits[
        0
    ]
    assert (
        response_spec["max_output_tokens"]
        == governor_capture_limit
        == _SEMANTIC_CAPTURE_MAX_OUTPUT_TOKENS
        == MAX_SEMANTIC_MEMORY_PROPOSAL_UTF8_BYTES
        == 4096
    )
    assert response_spec["tool_choice"] == {
        "type": "function",
        "name": RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME,
    }
    assert response_spec["metadata"] == {
        "source_turn_id": "turn-hermetic-1",
        "purpose": "semantic_capture",
    }
    tools = response_spec["tools"]
    assert isinstance(tools, list) and len(tools) == 1
    assert tools[0]["type"] == "function"
    assert tools[0]["name"] == RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME

    for event in _semantic_capture_response_events(
        response_id="resp-cap-hermetic-1",
        source_turn_id="turn-hermetic-1",
    ):
        socket.push(event)

    result = await capture_task
    assert isinstance(result, SemanticCaptureFunctionArguments)
    assert result.disposition == SemanticCaptureDisposition.NO_CHANGE

    assert channel._active_semantic_capture is None
    assert len(channel._capture_response_id_to_registration) == 0
    governor = channel.session_health.resource_governor
    assert governor is not None
    assert governor.local_reserved_requests == 0


@pytest.mark.asyncio
async def test_event_demux_routes_normal_and_capture_events_independently() -> None:
    channel, socket = _channel([], max_output_tokens=2000)
    stream = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-normal-1",
        text_input="Hello Ava!",
        response_max_output_tokens=400,
    )

    evidence = SemanticCaptureEvidence(
        source_turn_id="turn-cap-demux-1",
        current_frame=DialogueFrameState(),
        child_utterance="Tell me about Mars.",
    )
    capture_task = asyncio.create_task(
        channel.capture_semantic_memory(evidence=evidence)
    )
    await socket.wait_sent(1)

    # Interleave normal router events, rate limits, and capture events
    socket.push(
        {
            "type": "input_audio_buffer.speech_started",
            "audio_start_ms": 100,
            "item_id": "item_speech_1",
        }
    )
    socket.push(
        {
            "type": "response.created",
            "response": {
                "id": "resp-cap-demux-1",
                "metadata": {
                    "purpose": "semantic_capture",
                    "source_turn_id": "turn-cap-demux-1",
                },
            },
        }
    )
    socket.push(
        {
            "type": "response.created",
            "response": {"id": "resp-normal-1"},
        }
    )
    socket.push(
        {
            "type": "rate_limits.updated",
            "rate_limits": [
                {
                    "name": "tokens",
                    "limit": 1000,
                    "remaining": 950,
                    "reset_seconds": 0.5,
                }
            ],
        }
    )
    socket.push(
        {
            "type": "response.output_text.delta",
            "response_id": "resp-normal-1",
            "delta": "Hello child!",
        }
    )
    socket.push(
        {
            "type": "response.output_item.added",
            "response_id": "resp-cap-demux-1",
            "item": {
                "type": "function_call",
                "name": RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME,
                "call_id": "call-cap-demux-1",
            },
        }
    )
    socket.push(
        {
            "type": "response.function_call_arguments.done",
            "response_id": "resp-cap-demux-1",
            "name": RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME,
            "call_id": "call-cap-demux-1",
            "arguments": '{"disposition":"noChange"}',
        }
    )
    socket.push(
        {
            "type": "response.output_item.done",
            "response_id": "resp-cap-demux-1",
            "item": {
                "type": "function_call",
                "name": RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME,
                "call_id": "call-cap-demux-1",
                "arguments": '{"disposition":"noChange"}',
            },
        }
    )
    socket.push(
        {
            "type": "response.output_text.done",
            "response_id": "resp-normal-1",
            "text": "Hello child!",
        }
    )
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp-normal-1",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Hello child!"}],
                    }
                ],
                "usage": _usage(10, 4),
            },
        }
    )
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp-cap-demux-1",
                "status": "completed",
                "usage": _usage(50, 10),
            },
        }
    )

    normal_events: list[object] = []
    async for event in stream:
        normal_events.append(event)

    assert normal_events == [RealtimeSpeechTextDelta(delta="Hello child!")]
    input_event = await channel.next_input_event()
    assert input_event == RealtimeProviderInputEvent(
        event_type="input_audio_buffer.speech_started",
        item_id="item_speech_1",
        audio_start_ms=100,
    )

    capture_res = await capture_task
    assert capture_res.disposition == SemanticCaptureDisposition.NO_CHANGE

    assert channel._active_semantic_capture is None
    assert len(channel._capture_response_id_to_registration) == 0


@pytest.mark.asyncio
async def test_rapid_next_turn_independent_of_open_semantic_capture() -> None:
    channel, socket = _channel([], max_output_tokens=2000)

    # Turn 1 semantic capture starts and stays open
    evidence = SemanticCaptureEvidence(
        source_turn_id="turn-open-1",
        current_frame=DialogueFrameState(),
    )
    capture_task = asyncio.create_task(
        channel.capture_semantic_memory(evidence=evidence)
    )
    await socket.wait_sent(1)
    socket.push(
        {
            "type": "response.created",
            "response": {
                "id": "resp-cap-open-1",
                "metadata": {
                    "purpose": "semantic_capture",
                    "source_turn_id": "turn-open-1",
                },
            },
        }
    )

    # Turn 2 child turn starts while capture is still pending
    for ev in _text_response_events(
        response_id="resp-child-2",
        text="Turn 2 reply.",
    ):
        socket.push(ev)

    t2_events = [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id="turn-child-2",
            text_input="Turn 2 text",
            response_max_output_tokens=400,
        )
    ]
    assert t2_events == [RealtimeSpeechTextDelta(delta="Turn 2 reply.")]

    # Now finish the open capture response
    socket.push(
        {
            "type": "response.output_item.added",
            "response_id": "resp-cap-open-1",
            "item": {
                "type": "function_call",
                "name": RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME,
                "call_id": "call-open-1",
            },
        }
    )
    socket.push(
        {
            "type": "response.function_call_arguments.done",
            "response_id": "resp-cap-open-1",
            "name": RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME,
            "call_id": "call-open-1",
            "arguments": '{"disposition":"noChange"}',
        }
    )
    socket.push(
        {
            "type": "response.output_item.done",
            "response_id": "resp-cap-open-1",
            "item": {
                "type": "function_call",
                "name": RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME,
                "call_id": "call-open-1",
                "arguments": '{"disposition":"noChange"}',
            },
        }
    )
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp-cap-open-1",
                "status": "completed",
                "usage": _usage(50, 10),
            },
        }
    )

    capture_res = await capture_task
    assert capture_res.disposition == SemanticCaptureDisposition.NO_CHANGE
    assert channel._active_semantic_capture is None


@pytest.mark.asyncio
async def test_capture_correlated_error_routes_to_capture_and_raises() -> None:
    channel, socket = _channel([])
    evidence = SemanticCaptureEvidence(
        source_turn_id="turn-err-1",
        current_frame=DialogueFrameState(),
    )
    capture_task = asyncio.create_task(
        channel.capture_semantic_memory(evidence=evidence)
    )
    await socket.wait_sent(1)
    sent_create = socket.sent[-1]
    client_event_id = sent_create.get("event_id")
    assert isinstance(client_event_id, str)

    # Inject correlated error referencing event_id
    socket.push(
        {
            "type": "error",
            "event_id": client_event_id,
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_parameters",
                "message": "Capture parameters invalid",
                "event_id": client_event_id,
            },
        }
    )

    with pytest.raises(OpenAIRealtimeProviderError) as exc_info:
        await capture_task

    assert "invalid_parameters" in str(exc_info.value)
    assert channel._active_semantic_capture is None
    assert len(channel._capture_response_id_to_registration) == 0
    governor = channel.session_health.resource_governor
    assert governor is not None
    assert governor.local_reserved_requests == 0


@pytest.mark.asyncio
async def test_capture_route_cleanup_and_governor_release_on_timeout_and_cancel() -> (
    None
):
    # 1. Test timeout cleanup: reservation remains active until session close
    channel_timeout, socket_timeout = _channel([], max_output_tokens=100)
    channel_timeout._turn_timeout_seconds = 0.05
    evidence_to = SemanticCaptureEvidence(
        source_turn_id="turn-timeout-1",
        current_frame=DialogueFrameState(),
    )
    with pytest.raises(_OpenAIRealtimeReceiveTimeout):
        await channel_timeout.capture_semantic_memory(evidence=evidence_to)

    assert channel_timeout._active_semantic_capture is None
    assert len(channel_timeout._capture_response_id_to_registration) == 0
    gov_to = channel_timeout.session_health.resource_governor
    assert gov_to is not None
    assert gov_to.local_reserved_requests == 1
    await channel_timeout.close()
    assert channel_timeout.session_health.resource_governor.local_reserved_requests == 0

    # 2. Test cancellation cleanup: reservation remains active until session close
    channel_cancel, socket_cancel = _channel([])
    evidence_cancel = SemanticCaptureEvidence(
        source_turn_id="turn-cancel-1",
        current_frame=DialogueFrameState(),
    )
    cancel_task = asyncio.create_task(
        channel_cancel.capture_semantic_memory(evidence=evidence_cancel)
    )
    await socket_cancel.wait_sent(1)

    cancel_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancel_task

    assert channel_cancel._active_semantic_capture is None
    assert len(channel_cancel._capture_response_id_to_registration) == 0
    gov_cancel = channel_cancel.session_health.resource_governor
    assert gov_cancel is not None
    assert gov_cancel.local_reserved_requests == 1
    await channel_cancel.close()
    assert channel_cancel.session_health.resource_governor.local_reserved_requests == 0


@pytest.mark.asyncio
async def test_capture_tolerates_private_message_text_before_function_call_and_isolates_child_turn() -> (
    None
):
    channel, socket = _channel([], max_output_tokens=2000)

    evidence = SemanticCaptureEvidence(
        source_turn_id="turn-live-seq-1",
        current_frame=DialogueFrameState(),
        child_utterance="I like stars.",
    )
    capture_task = asyncio.create_task(
        channel.capture_semantic_memory(evidence=evidence)
    )
    await socket.wait_sent(1)

    stream_b = channel.stream_turn(
        harness=_harness(),
        turn_id="turn-child-live-b",
        text_input="What is a supernova?",
        response_max_output_tokens=400,
    )

    # Capture A emits response.created
    socket.push(
        {
            "type": "response.created",
            "response": {
                "id": "resp-cap-live-A",
                "metadata": {
                    "purpose": "semantic_capture",
                    "source_turn_id": "turn-live-seq-1",
                },
            },
        }
    )

    # Capture A emits private message item and text (the live failure trigger)
    socket.push(
        {
            "type": "response.output_item.added",
            "response_id": "resp-cap-live-A",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [],
            },
        }
    )
    socket.push(
        {
            "type": "response.output_text.delta",
            "response_id": "resp-cap-live-A",
            "delta": "I am thinking about stars...",
        }
    )
    socket.push(
        {
            "type": "response.output_text.done",
            "response_id": "resp-cap-live-A",
            "text": "I am thinking about stars...",
        }
    )
    socket.push(
        {
            "type": "response.output_item.done",
            "response_id": "resp-cap-live-A",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "I am thinking about stars..."}
                ],
            },
        }
    )

    # Interleave normal child response B events
    socket.push(
        {
            "type": "response.created",
            "response": {"id": "resp-child-live-B"},
        }
    )
    socket.push(
        {
            "type": "response.output_text.delta",
            "response_id": "resp-child-live-B",
            "delta": "A supernova is an explosion.",
        }
    )

    await asyncio.sleep(0)
    # Verify route ownership of A is still maintained while A is generating
    assert "resp-cap-live-A" in channel._capture_response_id_to_registration

    # THEN LATER Capture A emits valid function call
    socket.push(
        {
            "type": "response.output_item.added",
            "response_id": "resp-cap-live-A",
            "item": {
                "type": "function_call",
                "name": RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME,
                "call_id": "call-cap-live-A",
            },
        }
    )
    socket.push(
        {
            "type": "response.function_call_arguments.delta",
            "response_id": "resp-cap-live-A",
            "call_id": "call-cap-live-A",
            "delta": '{"disposition":"noChange"}',
        }
    )
    socket.push(
        {
            "type": "response.function_call_arguments.done",
            "response_id": "resp-cap-live-A",
            "name": RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME,
            "call_id": "call-cap-live-A",
            "arguments": '{"disposition":"noChange"}',
        }
    )
    socket.push(
        {
            "type": "response.output_item.done",
            "response_id": "resp-cap-live-A",
            "item": {
                "type": "function_call",
                "name": RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME,
                "call_id": "call-cap-live-A",
                "arguments": '{"disposition":"noChange"}',
            },
        }
    )
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp-cap-live-A",
                "status": "completed",
                "usage": _usage(50, 15),
            },
        }
    )

    # Normal child response B finishes
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp-child-live-B",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "A supernova is an explosion.",
                            }
                        ],
                    }
                ],
                "usage": _usage(20, 6),
            },
        }
    )

    b_events = [event async for event in stream_b]
    assert b_events == [RealtimeSpeechTextDelta(delta="A supernova is an explosion.")]

    capture_res = await capture_task
    assert isinstance(capture_res, SemanticCaptureFunctionArguments)
    assert capture_res.disposition == SemanticCaptureDisposition.NO_CHANGE

    assert channel._active_semantic_capture is None
    assert "resp-cap-live-A" not in channel._capture_response_id_to_registration
    assert len(channel._capture_response_id_to_registration) == 0


@pytest.mark.asyncio
async def test_invalid_capture_only_text_fails_at_terminal_and_preserves_next_turn() -> (
    None
):
    channel, socket = _channel([], max_output_tokens=2000)
    evidence = SemanticCaptureEvidence(
        source_turn_id="turn-invalid-text-1",
        current_frame=DialogueFrameState(),
    )
    capture_task = asyncio.create_task(
        channel.capture_semantic_memory(evidence=evidence)
    )
    await socket.wait_sent(1)

    socket.push(
        {
            "type": "response.created",
            "response": {
                "id": "resp-cap-text-only",
                "metadata": {
                    "purpose": "semantic_capture",
                    "source_turn_id": "turn-invalid-text-1",
                },
            },
        }
    )
    socket.push(
        {
            "type": "response.output_item.added",
            "response_id": "resp-cap-text-only",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [],
            },
        }
    )
    socket.push(
        {
            "type": "response.output_text.delta",
            "response_id": "resp-cap-text-only",
            "delta": "Just text, no tool call.",
        }
    )
    socket.push(
        {
            "type": "response.output_text.done",
            "response_id": "resp-cap-text-only",
            "text": "Just text, no tool call.",
        }
    )

    await asyncio.sleep(0)
    # Route remains quarantined, not failed yet
    assert "resp-cap-text-only" in channel._capture_response_id_to_registration
    assert not capture_task.done()

    # Now emit response.done
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp-cap-text-only",
                "status": "completed",
                "usage": _usage(30, 5),
            },
        }
    )

    # Capture fails only after response.done
    with pytest.raises(OpenAIRealtimeProtocolError) as exc_info:
        await capture_task
    assert "did not invoke record_semantic_turn_capture" in str(exc_info.value)

    # Mapping cleaned afterward
    assert "resp-cap-text-only" not in channel._capture_response_id_to_registration
    assert len(channel._capture_response_id_to_registration) == 0

    # Next child turn remains healthy, no owner mismatch
    for ev in _text_response_events(
        response_id="resp-child-next",
        text="Next turn reply.",
    ):
        socket.push(ev)

    events = [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id="turn-child-next",
            text_input="Next input",
            response_max_output_tokens=400,
        )
    ]
    assert events == [RealtimeSpeechTextDelta(delta="Next turn reply.")]


@pytest.mark.asyncio
async def test_malformed_function_capture_fails_at_terminal_and_child_turn_unaffected() -> (
    None
):
    channel, socket = _channel([], max_output_tokens=2000)
    evidence = SemanticCaptureEvidence(
        source_turn_id="turn-malformed-1",
        current_frame=DialogueFrameState(),
    )
    capture_task = asyncio.create_task(
        channel.capture_semantic_memory(evidence=evidence)
    )
    await socket.wait_sent(1)

    socket.push(
        {
            "type": "response.created",
            "response": {
                "id": "resp-cap-malformed",
                "metadata": {
                    "purpose": "semantic_capture",
                    "source_turn_id": "turn-malformed-1",
                },
            },
        }
    )
    socket.push(
        {
            "type": "response.output_item.added",
            "response_id": "resp-cap-malformed",
            "item": {
                "type": "function_call",
                "name": "unexpected_wrong_tool",
                "call_id": "call-wrong-1",
            },
        }
    )
    socket.push(
        {
            "type": "response.function_call_arguments.delta",
            "response_id": "resp-cap-malformed",
            "call_id": "call-wrong-1",
            "delta": '{"foo": "bar"}',
        }
    )
    socket.push(
        {
            "type": "response.function_call_arguments.done",
            "response_id": "resp-cap-malformed",
            "name": "unexpected_wrong_tool",
            "call_id": "call-wrong-1",
            "arguments": '{"foo": "bar"}',
        }
    )
    socket.push(
        {
            "type": "response.output_item.done",
            "response_id": "resp-cap-malformed",
            "item": {
                "type": "function_call",
                "name": "unexpected_wrong_tool",
                "call_id": "call-wrong-1",
                "arguments": '{"foo": "bar"}',
            },
        }
    )

    await asyncio.sleep(0)
    assert "resp-cap-malformed" in channel._capture_response_id_to_registration
    assert not capture_task.done()

    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp-cap-malformed",
                "status": "completed",
                "usage": _usage(40, 10),
            },
        }
    )

    with pytest.raises(OpenAIRealtimeProtocolError) as exc_info:
        await capture_task
    assert "unexpected tool 'unexpected_wrong_tool'" in str(exc_info.value)
    assert len(channel._capture_response_id_to_registration) == 0

    for ev in _text_response_events(
        response_id="resp-child-after-malformed",
        text="Normal response.",
    ):
        socket.push(ev)

    events = [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id="turn-child-after-malformed",
            text_input="Child input",
            response_max_output_tokens=400,
        )
    ]
    assert events == [RealtimeSpeechTextDelta(delta="Normal response.")]


@pytest.mark.asyncio
async def test_timeout_abandoned_capture_response_quarantined_until_terminal() -> None:
    channel, socket = _channel([], max_output_tokens=2000)
    evidence = SemanticCaptureEvidence(
        source_turn_id="turn-abandoned-1",
        current_frame=DialogueFrameState(),
    )
    capture_task = asyncio.create_task(
        channel.capture_semantic_memory(evidence=evidence)
    )
    await socket.wait_sent(1)

    socket.push(
        {
            "type": "response.created",
            "response": {
                "id": "resp-cap-abandoned-1",
                "metadata": {
                    "purpose": "semantic_capture",
                    "source_turn_id": "turn-abandoned-1",
                },
            },
        }
    )

    # Let response.created be processed by capture task
    await asyncio.sleep(0)
    assert "resp-cap-abandoned-1" in channel._capture_response_id_to_registration

    # Cause local capture abandonment (e.g. cancellation/timeout)
    capture_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await capture_task

    # Crucial: route mapping MUST remain present and marked abandoned
    assert "resp-cap-abandoned-1" in channel._capture_response_id_to_registration
    assert (
        channel._capture_response_id_to_registration["resp-cap-abandoned-1"].abandoned
        is True
    )

    # Emit more capture response events
    socket.push(
        {
            "type": "response.output_text.delta",
            "response_id": "resp-cap-abandoned-1",
            "delta": "Post-timeout private text.",
        }
    )

    # Interleave normal child response concurrently
    for ev in _text_response_events(
        response_id="resp-child-interleaved-1",
        text="Child speaks during abandoned capture.",
    ):
        socket.push(ev)

    child_events = [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id="turn-child-interleaved-1",
            text_input="Child question",
            response_max_output_tokens=400,
        )
    ]
    assert child_events == [
        RealtimeSpeechTextDelta(delta="Child speaks during abandoned capture.")
    ]

    # Mapping is still present before terminal
    assert "resp-cap-abandoned-1" in channel._capture_response_id_to_registration

    # Finally emit capture response terminal
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp-cap-abandoned-1",
                "status": "completed",
                "usage": _usage(50, 10),
            },
        }
    )

    await asyncio.sleep(0)

    # Mapping is removed at terminal
    assert "resp-cap-abandoned-1" not in channel._capture_response_id_to_registration
    assert len(channel._capture_response_id_to_registration) == 0
    gov = channel.session_health.resource_governor
    assert gov is not None
    assert gov.local_reserved_requests == 0


@pytest.mark.asyncio
async def test_late_response_created_after_abandonment_is_quarantined() -> None:
    channel, socket = _channel([], max_output_tokens=2000)
    evidence = SemanticCaptureEvidence(
        source_turn_id="turn-late-created-1",
        current_frame=DialogueFrameState(),
    )
    capture_task = asyncio.create_task(
        channel.capture_semantic_memory(evidence=evidence)
    )
    await socket.wait_sent(1)

    # Cancel before response.created ever arrives
    capture_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await capture_task

    # Now late response.created arrives
    socket.push(
        {
            "type": "response.created",
            "response": {
                "id": "resp-late-created-1",
                "metadata": {
                    "purpose": "semantic_capture",
                    "source_turn_id": "turn-late-created-1",
                },
            },
        }
    )
    await asyncio.sleep(0)

    # It was quarantined and not leaked to _router_events
    assert "resp-late-created-1" in channel._capture_response_id_to_registration

    # Normal child turn streams without issue
    for ev in _text_response_events(
        response_id="resp-child-normal-after-late",
        text="Normal child turn.",
    ):
        socket.push(ev)

    child_events = [
        event
        async for event in channel.stream_turn(
            harness=_harness(),
            turn_id="turn-child-normal-after-late",
            text_input="Child question",
            response_max_output_tokens=400,
        )
    ]
    assert child_events == [RealtimeSpeechTextDelta(delta="Normal child turn.")]

    # Emit terminal for the late capture
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp-late-created-1",
                "status": "completed",
                "usage": _usage(50, 10),
            },
        }
    )
    await asyncio.sleep(0)
    assert "resp-late-created-1" not in channel._capture_response_id_to_registration


@pytest.mark.asyncio
async def test_capture_resource_governor_lifecycle_for_valid_invalid_and_abandoned() -> (
    None
):
    channel, socket = _channel([], max_output_tokens=2000)

    # Case 1: Valid capture keeps reservation until terminal and reconciles usage
    evidence1 = SemanticCaptureEvidence(
        source_turn_id="turn-gov-valid-1",
        current_frame=DialogueFrameState(),
    )
    task1 = asyncio.create_task(channel.capture_semantic_memory(evidence=evidence1))
    await socket.wait_sent(1)
    gov1 = channel.session_health.resource_governor
    assert gov1 is not None and gov1.local_reserved_requests == 1

    for ev in _semantic_capture_response_events(
        response_id="resp-gov-valid-1",
        source_turn_id="turn-gov-valid-1",
        input_tokens=120,
        output_tokens=30,
    ):
        socket.push(ev)

    res1 = await task1
    assert res1.disposition == SemanticCaptureDisposition.NO_CHANGE
    gov1_done = channel.session_health.resource_governor
    assert gov1_done is not None and gov1_done.local_reserved_requests == 0
    assert channel._resource_governor.latest_actual_input_tokens == 120
    assert channel._resource_governor.latest_actual_output_tokens == 30

    # Case 2: Semantically invalid capture (only text) retains reservation until response.done
    evidence2 = SemanticCaptureEvidence(
        source_turn_id="turn-gov-invalid-2",
        current_frame=DialogueFrameState(),
    )
    task2 = asyncio.create_task(channel.capture_semantic_memory(evidence=evidence2))
    await socket.wait_sent(2)
    gov2 = channel.session_health.resource_governor
    assert gov2 is not None and gov2.local_reserved_requests == 1

    socket.push(
        {
            "type": "response.created",
            "response": {
                "id": "resp-gov-invalid-2",
                "metadata": {
                    "purpose": "semantic_capture",
                    "source_turn_id": "turn-gov-invalid-2",
                },
            },
        }
    )
    socket.push(
        {
            "type": "response.output_text.delta",
            "response_id": "resp-gov-invalid-2",
            "delta": "invalid prose only",
        }
    )
    await asyncio.sleep(0)
    gov2_mid = channel.session_health.resource_governor
    assert gov2_mid is not None and gov2_mid.local_reserved_requests == 1

    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp-gov-invalid-2",
                "status": "completed",
                "usage": _usage(80, 25),
            },
        }
    )
    with pytest.raises(OpenAIRealtimeProtocolError):
        await task2

    gov2_done = channel.session_health.resource_governor
    assert gov2_done is not None and gov2_done.local_reserved_requests == 0
    assert channel._resource_governor.latest_actual_input_tokens == 80
    assert channel._resource_governor.latest_actual_output_tokens == 25

    # Case 3: Abandoned capture holds reservation until background receiver sees terminal
    evidence3 = SemanticCaptureEvidence(
        source_turn_id="turn-gov-abandoned-3",
        current_frame=DialogueFrameState(),
    )
    task3 = asyncio.create_task(channel.capture_semantic_memory(evidence=evidence3))
    await socket.wait_sent(3)
    gov3 = channel.session_health.resource_governor
    assert gov3 is not None and gov3.local_reserved_requests == 1

    socket.push(
        {
            "type": "response.created",
            "response": {
                "id": "resp-gov-abandoned-3",
                "metadata": {
                    "purpose": "semantic_capture",
                    "source_turn_id": "turn-gov-abandoned-3",
                },
            },
        }
    )
    await asyncio.sleep(0)

    task3.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task3

    gov3_mid = channel.session_health.resource_governor
    assert gov3_mid is not None and gov3_mid.local_reserved_requests == 1

    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp-gov-abandoned-3",
                "status": "completed",
                "usage": _usage(60, 15),
            },
        }
    )
    await asyncio.sleep(0)

    gov3_done = channel.session_health.resource_governor
    assert gov3_done is not None and gov3_done.local_reserved_requests == 0
    assert channel._resource_governor.latest_actual_input_tokens == 60
    assert channel._resource_governor.latest_actual_output_tokens == 15


@pytest.mark.asyncio
async def test_capture_abandoned_before_response_created_keeps_reservation_active() -> (
    None
):
    channel, socket = _channel([], max_output_tokens=1000)
    evidence = SemanticCaptureEvidence(
        source_turn_id="turn-abandoned-pre-created-1",
        current_frame=DialogueFrameState(),
    )
    task = asyncio.create_task(channel.capture_semantic_memory(evidence=evidence))
    await socket.wait_sent(1)

    # Abandon the task locally before response.created arrives
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Registration must be quarantined with abandoned=True
    assert channel._active_semantic_capture is None
    quarantined = channel._quarantined_capture_by_source_turn_id.get(
        "turn-abandoned-pre-created-1"
    )
    assert quarantined is not None
    assert quarantined.abandoned is True
    assert (
        quarantined.client_event_id in channel._quarantined_capture_by_client_event_id
    )

    # The ResourceGovernor reservation must remain ACTIVE (not released early)
    gov = channel.session_health.resource_governor
    assert gov is not None
    assert gov.local_reserved_requests == 1


@pytest.mark.asyncio
async def test_capture_abandoned_before_response_created_late_response_created_and_done() -> (
    None
):
    channel, socket = _channel([], max_output_tokens=1000)
    evidence = SemanticCaptureEvidence(
        source_turn_id="turn-abandoned-late-done-1",
        current_frame=DialogueFrameState(),
    )
    task = asyncio.create_task(channel.capture_semantic_memory(evidence=evidence))
    await socket.wait_sent(1)

    # Cancel before response.created arrives
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    gov = channel.session_health.resource_governor
    assert gov is not None
    assert gov.local_reserved_requests == 1

    # Late response.created arrives from provider
    socket.push(
        {
            "type": "response.created",
            "response": {
                "id": "resp-late-created-done-1",
                "metadata": {
                    "purpose": "semantic_capture",
                    "source_turn_id": "turn-abandoned-late-done-1",
                },
            },
        }
    )
    await asyncio.sleep(0)

    # Quarantined capture moves to response_id mapping, reservation is still held
    assert (
        "turn-abandoned-late-done-1"
        not in channel._quarantined_capture_by_source_turn_id
    )
    assert "resp-late-created-done-1" in channel._capture_response_id_to_registration
    assert gov.local_reserved_requests == 1

    # Late response.done arrives with token usage
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp-late-created-done-1",
                "status": "completed",
                "usage": _usage(120, 35),
            },
        }
    )
    await asyncio.sleep(0)

    # Terminal settles the reservation, reconciles usage, and cleans up registration
    gov_done = channel.session_health.resource_governor
    assert gov_done is not None and gov_done.local_reserved_requests == 0
    assert channel._resource_governor.latest_actual_input_tokens == 120
    assert channel._resource_governor.latest_actual_output_tokens == 35
    assert (
        "resp-late-created-done-1" not in channel._capture_response_id_to_registration
    )


@pytest.mark.asyncio
async def test_capture_abandoned_before_response_created_then_channel_close() -> None:
    channel, socket = _channel([], max_output_tokens=1000)
    evidence = SemanticCaptureEvidence(
        source_turn_id="turn-abandoned-close-1",
        current_frame=DialogueFrameState(),
    )
    task = asyncio.create_task(channel.capture_semantic_memory(evidence=evidence))
    await socket.wait_sent(1)

    # Cancel before response.created arrives
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    gov = channel.session_health.resource_governor
    assert gov is not None
    assert gov.local_reserved_requests == 1

    # Closing the channel/session releases all orphaned/quarantined reservations
    await channel.close()
    assert channel.session_health.resource_governor.local_reserved_requests == 0
    assert len(channel._quarantined_capture_by_source_turn_id) == 0
    assert len(channel._quarantined_capture_by_client_event_id) == 0


@pytest.mark.asyncio
async def test_capture_abandoned_before_response_created_correlated_provider_error() -> (
    None
):
    channel, socket = _channel([], max_output_tokens=1000)
    evidence = SemanticCaptureEvidence(
        source_turn_id="turn-abandoned-error-1",
        current_frame=DialogueFrameState(),
    )
    task = asyncio.create_task(channel.capture_semantic_memory(evidence=evidence))
    await socket.wait_sent(1)

    sent_create = socket.sent[-1]
    client_event_id = sent_create.get("event_id")
    assert isinstance(client_event_id, str)

    # Cancel before response.created arrives
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    gov = channel.session_health.resource_governor
    assert gov is not None
    assert gov.local_reserved_requests == 1
    assert client_event_id in channel._quarantined_capture_by_client_event_id

    # Provider emits correlated error referencing client_event_id proving no response will be created
    socket.push(
        {
            "type": "error",
            "event_id": client_event_id,
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_value",
                "message": "Capture request rejected before response creation",
                "event_id": client_event_id,
            },
        }
    )
    await asyncio.sleep(0)

    # Error settles and releases the reservation
    gov_done = channel.session_health.resource_governor
    assert gov_done is not None and gov_done.local_reserved_requests == 0
    assert client_event_id not in channel._quarantined_capture_by_client_event_id
    assert (
        "turn-abandoned-error-1" not in channel._quarantined_capture_by_source_turn_id
    )


async def _execute_capture_with_raw_arguments(
    arguments: str,
    source_turn_id: str,
) -> SemanticCaptureFunctionArguments:
    channel, socket = _channel([])
    evidence = SemanticCaptureEvidence(
        source_turn_id=source_turn_id,
        current_frame=DialogueFrameState(),
    )
    capture_task = asyncio.create_task(
        channel.capture_semantic_memory(evidence=evidence)
    )
    await socket.wait_sent(1)
    for event in _semantic_capture_response_events(
        response_id=f"resp-{source_turn_id}",
        source_turn_id=source_turn_id,
        arguments=arguments,
    ):
        socket.push(event)
    return await capture_task


@pytest.mark.asyncio
async def test_raw_wire_capture_journal_events_null_with_changed_disposition() -> None:
    raw_json = json.dumps(
        {
            "disposition": "changed",
            "dialogueFrame": {
                "activeTopics": ["astronomy"],
            },
            "journalEvents": None,
        }
    )
    args = await _execute_capture_with_raw_arguments(
        raw_json, source_turn_id="turn-raw-a"
    )
    assert args.disposition == SemanticCaptureDisposition.CHANGED
    assert args.journal_events == ()
    assert args.dialogue_frame is not None
    assert args.dialogue_frame.active_topics == ("astronomy",)


@pytest.mark.asyncio
async def test_raw_wire_capture_canonical_canvas_references_null_on_draft() -> None:
    raw_json = json.dumps(
        {
            "disposition": "changed",
            "journalEvents": [
                {
                    "family": "userPreference",
                    "semanticKey": "favorite_color",
                    "summary": "Loves blue.",
                    "canonicalCanvasReferences": None,
                }
            ],
        }
    )
    args = await _execute_capture_with_raw_arguments(
        raw_json, source_turn_id="turn-raw-b"
    )
    assert args.disposition == SemanticCaptureDisposition.CHANGED
    assert len(args.journal_events) == 1
    draft = args.journal_events[0]
    assert draft.canonical_canvas_references == ()
    assert draft.semantic_key == "favorite_color"
    assert draft.summary == "Loves blue."


@pytest.mark.asyncio
async def test_raw_wire_capture_key_canonicalization_and_collision_detection() -> None:
    raw_json = json.dumps(
        {
            "disposition": "changed",
            "journalEvents": [
                {
                    "family": "userPreference",
                    "semanticKey": "volcanicFeaturesInOtherWorlds",
                    "summary": "Interested in cryovolcanoes on Titan.",
                }
            ],
        }
    )
    args = await _execute_capture_with_raw_arguments(
        raw_json, source_turn_id="turn-raw-c1"
    )
    assert args.journal_events[0].semantic_key == "volcanic_features_in_other_worlds"

    raw_collision = json.dumps(
        {
            "disposition": "changed",
            "journalEvents": [
                {
                    "family": "userPreference",
                    "semanticKey": "favorite_color",
                    "summary": "Loves cobalt blue.",
                },
                {
                    "family": "userPreference",
                    "semanticKey": "favoriteColor",
                    "summary": "Loves cerulean blue.",
                },
            ],
        }
    )
    with pytest.raises(
        OpenAIRealtimeProtocolError, match="duplicate semantic identities"
    ):
        await _execute_capture_with_raw_arguments(
            raw_collision, source_turn_id="turn-raw-c2"
        )


@pytest.mark.asyncio
async def test_raw_wire_capture_dialogue_frame_list_bound_clamping_preserves_priority() -> (
    None
):
    referents = [f"referent_{i}" for i in range(6)]
    entities = [f"entity_{i}" for i in range(9)]
    raw_json = json.dumps(
        {
            "disposition": "changed",
            "dialogueFrame": {
                "currentReferents": referents,
                "recentEntities": entities,
            },
        }
    )
    args = await _execute_capture_with_raw_arguments(
        raw_json, source_turn_id="turn-raw-d"
    )
    assert args.dialogue_frame is not None
    assert args.dialogue_frame.current_referents == tuple(referents[:4])
    assert args.dialogue_frame.recent_entities == tuple(entities[:8])


@pytest.mark.asyncio
async def test_raw_wire_capture_malformed_canvas_reference_discarded() -> None:
    raw_json = json.dumps(
        {
            "disposition": "changed",
            "dialogueFrame": {
                "relevantCanvasReferences": [
                    {
                        "sceneId": "scene-solar-system",
                        "sceneRevision": 1,
                        "relation": "The active solar system map",
                    },
                    {
                        "sceneId": "scene-corrupted",
                        "sceneRevision": 1,
                        "relation": "corrupted item",
                        "continue_placeholder": "corrupted_token",
                    },
                    {
                        "corruptField": "not_a_valid_ref",
                    },
                ],
            },
            "journalEvents": [
                {
                    "family": "userPreference",
                    "semanticKey": "planet_choice",
                    "summary": "Prefers Mars.",
                    "canonicalCanvasReferences": [
                        {
                            "sceneId": "scene-mars",
                            "sceneRevision": 2,
                            "relation": "Mars scene",
                        },
                        {
                            "sceneId": "scene-mars-corrupted",
                            "sceneRevision": 2,
                            "relation": "broken",
                            "extra_noise": 123,
                        },
                        {
                            "garbage": True,
                        },
                    ],
                }
            ],
        }
    )
    args = await _execute_capture_with_raw_arguments(
        raw_json, source_turn_id="turn-raw-e"
    )
    assert args.dialogue_frame is not None
    assert len(args.dialogue_frame.relevant_canvas_references) == 1
    ref = args.dialogue_frame.relevant_canvas_references[0]
    assert ref.scene_id == "scene-solar-system"
    assert ref.scene_revision == 1
    assert ref.relation == "The active solar system map"

    assert len(args.journal_events) == 1
    assert len(args.journal_events[0].canonical_canvas_references) == 1
    draft_ref = args.journal_events[0].canonical_canvas_references[0]
    assert draft_ref.scene_id == "scene-mars"
    assert draft_ref.scene_revision == 2


@pytest.mark.asyncio
async def test_capture_incomplete_response_diagnostic_includes_reason() -> None:
    channel, socket = _channel([])
    evidence = SemanticCaptureEvidence(
        source_turn_id="turn-inc-1",
        current_frame=DialogueFrameState(),
    )
    capture_task = asyncio.create_task(
        channel.capture_semantic_memory(evidence=evidence)
    )
    await socket.wait_sent(1)

    socket.push(
        {
            "type": "response.created",
            "response": {
                "id": "resp-cap-inc-1",
                "metadata": {
                    "purpose": "semantic_capture",
                    "source_turn_id": "turn-inc-1",
                },
            },
        }
    )
    socket.push(
        {
            "type": "response.done",
            "response": {
                "id": "resp-cap-inc-1",
                "status": "incomplete",
                "status_details": {
                    "reason": "max_output_tokens",
                },
            },
        }
    )

    with pytest.raises(RealtimeProviderResponseIncompleteError) as exc_info:
        await capture_task

    assert exc_info.value.reason == "max_output_tokens"
    assert "max_output_tokens" in str(exc_info.value)


@pytest.mark.asyncio
async def test_raw_wire_capture_all_dialogue_frame_collection_bounds_clamped_to_prefix() -> (
    None
):
    # Test A: every bounded text collection with one item beyond its canonical count
    raw_payload = {
        "disposition": "changed",
        "dialogueFrame": {
            "activeTopics": [f"topic_{i}" for i in range(5)],
            "activeGoals": [f"goal_{i}" for i in range(5)],
            "unresolvedQuestions": [f"question_{i}" for i in range(5)],
            "currentReferents": [f"referent_{i}" for i in range(5)],
            "recentEntities": [f"entity_{i}" for i in range(9)],
            "avaCommitments": [f"commitment_{i}" for i in range(5)],
            "explicitUserPreferences": [f"user_pref_{i}" for i in range(5)],
            "temporaryInteractionPreferences": [f"temp_pref_{i}" for i in range(5)],
            "recentToolReferences": [f"tool_ref_{i}" for i in range(5)],
        },
    }
    args = await _execute_capture_with_raw_arguments(
        json.dumps(raw_payload), source_turn_id="turn-raw-all-collections"
    )
    assert args.dialogue_frame is not None
    assert args.dialogue_frame.active_topics == tuple(f"topic_{i}" for i in range(4))
    assert args.dialogue_frame.active_goals == tuple(f"goal_{i}" for i in range(4))
    assert args.dialogue_frame.unresolved_questions == tuple(
        f"question_{i}" for i in range(4)
    )
    assert args.dialogue_frame.current_referents == tuple(
        f"referent_{i}" for i in range(4)
    )
    assert args.dialogue_frame.recent_entities == tuple(f"entity_{i}" for i in range(8))
    assert args.dialogue_frame.ava_commitments == tuple(
        f"commitment_{i}" for i in range(4)
    )
    assert args.dialogue_frame.explicit_user_preferences == tuple(
        f"user_pref_{i}" for i in range(4)
    )
    assert args.dialogue_frame.temporary_interaction_preferences == tuple(
        f"temp_pref_{i}" for i in range(4)
    )
    assert args.dialogue_frame.recent_tool_references == tuple(
        f"tool_ref_{i}" for i in range(4)
    )
    assert args.dialogue_frame.relevant_canvas_references == ()


@pytest.mark.asyncio
async def test_raw_wire_capture_overlong_dialogue_frame_item_discarded() -> None:
    # Test B: overlong item discarded, short items survive unchanged, no truncated version
    overlong_item = "x" * 161
    raw_payload = {
        "disposition": "changed",
        "dialogueFrame": {
            "activeGoals": [
                "Understand orbital resonance",
                overlong_item,
                "Explore Europa's ice surface",
            ],
        },
    }
    args = await _execute_capture_with_raw_arguments(
        json.dumps(raw_payload), source_turn_id="turn-raw-overlong-discard"
    )
    assert args.dialogue_frame is not None
    assert args.dialogue_frame.active_goals == (
        "Understand orbital resonance",
        "Explore Europa's ice surface",
    )
    for goal in args.dialogue_frame.active_goals:
        assert not goal.startswith("x" * 10)
        assert len(goal) <= 160


@pytest.mark.asyncio
async def test_raw_wire_capture_exact_text_boundary_preserved() -> None:
    # Test C: item exactly at the 160-char bound survives byte-for-byte unchanged
    exact_160 = "a" * 160
    raw_payload = {
        "disposition": "changed",
        "dialogueFrame": {
            "activeTopics": [exact_160],
        },
    }
    args = await _execute_capture_with_raw_arguments(
        json.dumps(raw_payload), source_turn_id="turn-raw-exact-boundary"
    )
    assert args.dialogue_frame is not None
    assert len(args.dialogue_frame.active_topics) == 1
    assert args.dialogue_frame.active_topics[0] == exact_160
    assert len(args.dialogue_frame.active_topics[0]) == 160


@pytest.mark.asyncio
async def test_raw_wire_capture_relevant_canvas_references_clamped_to_prefix() -> None:
    # Test D: 6 canvas references clamped to prefix of 4, no IDs or relations rewritten
    refs = [
        {
            "sceneId": f"scene-{i}",
            "sceneRevision": i + 1,
            "relation": f"Showing relation {i}",
        }
        for i in range(6)
    ]
    raw_payload = {
        "disposition": "changed",
        "dialogueFrame": {
            "relevantCanvasReferences": refs,
        },
    }
    args = await _execute_capture_with_raw_arguments(
        json.dumps(raw_payload), source_turn_id="turn-raw-canvas-clamp"
    )
    assert args.dialogue_frame is not None
    assert len(args.dialogue_frame.relevant_canvas_references) == 4
    for i in range(4):
        ref = args.dialogue_frame.relevant_canvas_references[i]
        assert ref.scene_id == f"scene-{i}"
        assert ref.scene_revision == i + 1
        assert ref.relation == f"Showing relation {i}"


@pytest.mark.asyncio
async def test_raw_wire_capture_live_regression_shape() -> None:
    # Test G: live failure shape (5 activeGoals, overlong activeGoal)
    live_count_payload = {
        "disposition": "changed",
        "dialogueFrame": {
            "activeGoals": [
                "Explain the lunar phases to the child",
                "Describe how tides correlate with the Moon",
                "Answer questions about gravity in space",
                "Keep responses encouraging and interactive",
                "Prepare transition to the planetary map",
            ],
        },
    }
    args_count = await _execute_capture_with_raw_arguments(
        json.dumps(live_count_payload), source_turn_id="turn-raw-live-count"
    )
    assert args_count.dialogue_frame is not None
    assert len(args_count.dialogue_frame.active_goals) == 4
    assert (
        args_count.dialogue_frame.active_goals[0]
        == "Explain the lunar phases to the child"
    )
    assert (
        args_count.dialogue_frame.active_goals[3]
        == "Keep responses encouraging and interactive"
    )

    overlong_semantic_goal = (
        "Discuss whether future astronauts could set up permanent habitats on the Moon "
        "and explore deep subterranean lava tubes that offer natural radiation protection "
        "and thermal insulation from extreme surface cycles."
    )
    assert len(overlong_semantic_goal) > 160

    live_overlong_payload = {
        "disposition": "changed",
        "dialogueFrame": {
            "activeGoals": [
                "Explain the lunar phases to the child",
                "Describe how tides correlate with the Moon",
                "Answer questions about gravity in space",
                overlong_semantic_goal,
            ],
        },
    }
    args_overlong = await _execute_capture_with_raw_arguments(
        json.dumps(live_overlong_payload), source_turn_id="turn-raw-live-overlong"
    )
    assert args_overlong.dialogue_frame is not None
    assert len(args_overlong.dialogue_frame.active_goals) == 3
    assert args_overlong.dialogue_frame.active_goals == (
        "Explain the lunar phases to the child",
        "Describe how tides correlate with the Moon",
        "Answer questions about gravity in space",
    )
