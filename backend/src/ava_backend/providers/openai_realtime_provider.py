from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Protocol, TypeVar
from urllib.parse import quote, urlsplit
from uuid import uuid4
from xml.sax.saxutils import escape

import httpx
from websockets.exceptions import ConnectionClosed
from pydantic import ValidationError
from websockets.asyncio.client import connect as connect_websocket

from ..canvas.svg_workspace import RecordedSvgCanvasInteraction
from ..config import (
    DEFAULT_OPENAI_REALTIME_TRUNCATION_POST_INSTRUCTIONS_TOKENS,
    DEFAULT_OPENAI_REALTIME_TRUNCATION_RETENTION_RATIO,
)
from ..realtime.harness import RealtimeHarness
from ..realtime.provider_session import (
    MAX_SDP_BYTES,
    NegotiatedRealtimeCall,
    RealtimeContextInstallationStatus,
    RealtimeProviderError,
    RealtimeProviderProtocolError,
    RealtimeProviderResponseIncompleteError,
    RealtimeProviderUnavailableError,
    RealtimeNegotiationResponseError,
    RealtimeNegotiationUnavailableError,
    RealtimeProviderInputEvent,
    RealtimeProviderTurnEvent,
    validate_sdp,
)
from ..realtime.tool_contract import (
    CANCEL_INTERACTIVE_CANVAS_TOOL_NAME,
    GET_CURRENT_TIME_TOOL_NAME,
    INTERACTIVE_CANVAS_TOOL_NAME,
    SEARCH_MEMORY_TOOL_NAME,
    GetCurrentTimeRequest,
    InteractiveCanvasCancellationRequest,
    InteractiveCanvasCancellationToolOutputV4,
    InteractiveCanvasRequest,
    InteractiveCanvasToolOutput,
    InteractiveCanvasToolOutputV4,
    RealtimeGetCurrentTimeToolCall,
    RealtimeRejectedUtilityToolCall,
    RealtimeSearchMemoryToolCall,
    RealtimeUtilityToolKind,
    RealtimeUtilityToolOutputV4,
    RealtimeCancelCanvasToolCall,
    RealtimeCanvasToolCall,
    RealtimeRejectedCanvasToolCall,
    RealtimeSpeechTextDelta,
    RejectedUtilityToolOutputV4,
    SearchMemoryRequest,
)
from ..realtime.turn_parser import StreamingSpeechParser, StreamingTurnParseError

LOGGER = logging.getLogger("ava_backend.openai_realtime")

DEFAULT_OPENAI_API_ORIGIN = "https://api.openai.com"
DEFAULT_OPENAI_REALTIME_WEBSOCKET = "wss://api.openai.com/v1/realtime"
MAX_PROVIDER_EVENT_BYTES = 512 * 1024
MAX_PROVIDER_EVENTS_PER_TURN = 4096
MAX_PROVIDER_ROUTER_EVENTS = 4096
MAX_FUNCTION_ARGUMENT_BYTES = 256 * 1024
MAX_PROVIDER_ERROR_BYTES = 64 * 1024
_SIDEBAND_SEQUENCE_FIELD = "__ava_sideband_sequence"
_CANVAS_INTERACTION_RESPONSE_MAX_OUTPUT_TOKENS = 256
_CANVAS_TOOL_CONTINUATION_PREFERRED_MAX_OUTPUT_TOKENS = 512
_MAX_CONSUMED_TERMINAL_RESPONSE_IDS = 64
_CANVAS_TOOL_NAMES = frozenset(
    {INTERACTIVE_CANVAS_TOOL_NAME, CANCEL_INTERACTIVE_CANVAS_TOOL_NAME}
)
_UTILITY_TOOL_NAMES = frozenset({SEARCH_MEMORY_TOOL_NAME, GET_CURRENT_TIME_TOOL_NAME})
_SEMANTIC_TOOL_NAMES = _CANVAS_TOOL_NAMES | _UTILITY_TOOL_NAMES


class OpenAIRealtimeProviderError(RealtimeProviderError):
    """Base error for an invalid or unavailable OpenAI Realtime session."""


class OpenAIRealtimeProtocolError(
    RealtimeProviderProtocolError,
    OpenAIRealtimeProviderError,
):
    """OpenAI emitted an event sequence that Ava cannot safely consume."""


class OpenAIRealtimeUnavailableError(
    RealtimeProviderUnavailableError,
    OpenAIRealtimeProviderError,
):
    """The OpenAI Realtime connection could not complete the requested turn."""


class OpenAIRealtimeConnectionClosedError(OpenAIRealtimeUnavailableError):
    """The sideband socket closed, retaining safe close metadata."""

    def __init__(
        self,
        message: str,
        *,
        local_close: bool | None,
        close_code: int | None,
        close_reason: str | None,
    ) -> None:
        super().__init__(message)
        self.local_close = local_close
        self.close_code = close_code
        self.close_reason = close_reason


class OpenAIRealtimeRouterBackpressureError(OpenAIRealtimeProviderError):
    """A bounded application mailbox could not retain provider events."""


class _OpenAIRealtimeReceiveTimeout(OpenAIRealtimeUnavailableError):
    """No provider event arrived within a deliberately bounded drain window."""


@dataclass(frozen=True, slots=True)
class OpenAIRealtimeUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_input_tokens: int | None
    input_text_tokens: int | None
    input_audio_tokens: int | None
    output_text_tokens: int | None
    output_audio_tokens: int | None


@dataclass(frozen=True, slots=True)
class OpenAIRealtimeValidationIssue:
    path: tuple[str | int, ...]
    code: str


@dataclass(frozen=True, slots=True)
class _InputTranscriptionOutcome:
    turn_id: str | None
    transcript: str | None
    failed: bool


@dataclass(frozen=True, slots=True)
class _OutstandingCancelOutcome:
    cancel_event_id: str
    cancel_sequence: int
    cancelled_turn_id: str
    response_id: str | None


@dataclass(frozen=True, slots=True)
class _ResolvedCancelOutcome:
    cancel_event_id: str
    cancelled_turn_id: str
    response_id: str


_MAX_OUTSTANDING_CANCEL_OUTCOMES = 64
_MAX_RECENTLY_RESOLVED_CANCEL_OUTCOMES = 32


class OpenAIRealtimeOutputValidationError(OpenAIRealtimeProtocolError):
    """A billed live response failed Ava's speech/tool boundary."""

    def __init__(
        self,
        *,
        usage: OpenAIRealtimeUsage,
        response_id: str,
        validation_issue_count: int,
        validation_issues: tuple[OpenAIRealtimeValidationIssue, ...],
    ) -> None:
        super().__init__("OpenAI live probe output failed Ava's speech/tool validation")
        self.usage = usage
        self.response_id = response_id
        self.validation_issue_count = validation_issue_count
        self.validation_issues = validation_issues


@dataclass(frozen=True, slots=True)
class OpenAIRealtimeProbeResult:
    speech: str
    canvas_request: InteractiveCanvasRequest | None
    canvas_selected: bool
    canvas_rejection: str | None
    model: str
    prompt_version: str
    response_id: str
    first_delta_ms: float
    completed_ms: float
    usage: OpenAIRealtimeUsage


@dataclass(frozen=True, slots=True)
class OpenAIRealtimeProbeFailureResult:
    turn_id: str
    response_id: str
    usage: OpenAIRealtimeUsage
    validation_issues: tuple[OpenAIRealtimeValidationIssue, ...]


type OpenAIRealtimeProbeOutcome = (
    OpenAIRealtimeProbeResult | OpenAIRealtimeProbeFailureResult
)


@dataclass(frozen=True, slots=True)
class OpenAIRealtimeProbeRequest:
    turn_id: str
    text_input: str
    max_output_tokens: int

    def __post_init__(self) -> None:
        if not self.turn_id.strip():
            raise ValueError("probe turn_id cannot be empty")
        if not 1 <= len(self.text_input.strip()) <= 2_000:
            raise ValueError("probe text_input must contain 1-2000 characters")
        if not 256 <= self.max_output_tokens <= 4_096:
            raise ValueError("probe max_output_tokens must be between 256 and 4096")


class RealtimeWebSocket(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


RealtimeWebSocketConnector = Callable[
    [str, Mapping[str, str], float, int],
    Awaitable[RealtimeWebSocket],
]


async def _default_websocket_connector(
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
    max_event_bytes: int,
) -> RealtimeWebSocket:
    return await connect_websocket(
        url,
        additional_headers=headers,
        open_timeout=timeout_seconds,
        close_timeout=min(timeout_seconds, 10),
        ping_interval=20,
        ping_timeout=20,
        max_size=max_event_bytes,
        max_queue=32,
        compression=None,
    )


class _OpenAIRealtimeEventChannel:
    def __init__(
        self,
        socket: RealtimeWebSocket,
        *,
        model: str,
        max_output_tokens: int,
        turn_timeout_seconds: float,
        transcription_model: str = "gpt-transcribe",
        max_event_bytes: int = MAX_PROVIDER_EVENT_BYTES,
        max_events_per_turn: int = MAX_PROVIDER_EVENTS_PER_TURN,
        max_router_events: int = MAX_PROVIDER_ROUTER_EVENTS,
        max_argument_bytes: int = MAX_FUNCTION_ARGUMENT_BYTES,
        noise_reduction: str = "near_field",
        vad_threshold: float = 0.45,
        vad_prefix_padding_ms: int = 500,
        vad_silence_duration_ms: int = 450,
        truncation_post_instructions_tokens: int = (
            DEFAULT_OPENAI_REALTIME_TRUNCATION_POST_INSTRUCTIONS_TOKENS
        ),
        truncation_retention_ratio: float = (
            DEFAULT_OPENAI_REALTIME_TRUNCATION_RETENTION_RATIO
        ),
        provider_call_id: str | None = None,
    ) -> None:
        self._socket = socket
        self._provider_call_id = provider_call_id
        self._model = model
        self._transcription_model = _required_name(
            transcription_model,
            "OpenAI Realtime transcription model",
        )
        self._max_output_tokens = max_output_tokens
        self._turn_timeout_seconds = turn_timeout_seconds
        self._max_event_bytes = max_event_bytes
        self._max_events_per_turn = max_events_per_turn
        if not 8 <= max_router_events <= 16_384:
            raise ValueError("max_router_events must be between 8 and 16384")
        self._max_router_events = max_router_events
        self._max_argument_bytes = max_argument_bytes
        self._noise_reduction = _validated_noise_reduction(noise_reduction)
        self._vad_threshold = _validated_vad_threshold(vad_threshold)
        self._vad_prefix_padding_ms = _validated_vad_prefix_padding_ms(
            vad_prefix_padding_ms
        )
        self._vad_silence_duration_ms = _validated_vad_silence_duration_ms(
            vad_silence_duration_ms
        )
        self._truncation_post_instructions_tokens = (
            _validated_truncation_post_instructions_tokens(
                truncation_post_instructions_tokens
            )
        )
        self._truncation_retention_ratio = _validated_truncation_retention_ratio(
            truncation_retention_ratio
        )
        self._received_event_count = 0
        self._sideband_event_sequence = 0
        self._pending_events: deque[dict[str, object]] = deque()
        self._router_events: asyncio.Queue[dict[str, object] | object] = asyncio.Queue(
            maxsize=max_router_events
        )
        self._input_events: asyncio.Queue[RealtimeProviderInputEvent | object] = (
            asyncio.Queue(maxsize=max_router_events)
        )
        self._receiver_task: asyncio.Task[None] | None = None
        self._receiver_started = False
        self._receiver_error: BaseException | None = None
        self._receiver_terminal = object()
        self._closed = False
        self._active_turn_id: str | None = None
        self._active_response_id: str | None = None
        self._awaiting_response_trigger_turn_id: str | None = None
        self._interaction_response_turn_id: str | None = None
        self._interaction_response_readiness_changed = asyncio.Event()
        self._settled_turn_ids: set[str] = set()
        self._response_owner_by_id: dict[str, str | None] = {}
        self._consumed_terminal_response_owner_by_id: dict[str, str] = {}
        self._outstanding_cancel_outcomes: list[_OutstandingCancelOutcome] = []
        self._recently_resolved_cancel_outcomes: OrderedDict[
            str, _ResolvedCancelOutcome
        ] = OrderedDict()
        self._next_cancel_sequence = 1
        self._prepared_session_update: str | None = None
        self._installed_context_build_id: str | None = None
        self._cancelled_turn_ids: set[str] = set()
        self._pending_canvas_call_id: str | None = None
        self._completed_canvas_call_ids: set[str] = set()
        self._pending_utility_call_id: str | None = None
        self._completed_utility_call_ids: set[str] = set()
        self.last_usage: OpenAIRealtimeUsage | None = None
        self.last_response_id: str | None = None
        self.last_first_delta_ms: float | None = None
        self.last_completed_ms: float | None = None
        self._child_transcripts_by_turn: dict[str, str] = {}
        self._child_transcription_events_by_turn: dict[str, asyncio.Event] = {}
        self._input_item_turn_ids: dict[str, str | None] = {}
        self._input_transcription_outcomes: dict[str, _InputTranscriptionOutcome] = {}
        self._input_terminal_observed_at: dict[str, float] = {}

    async def start_receiver(self) -> None:
        """Start the sole raw sideband reader for this provider session."""

        if self._closed:
            raise OpenAIRealtimeProtocolError(
                "cannot start a receiver on a closed OpenAI Realtime channel"
            )
        if self._receiver_started:
            return
        self._receiver_started = True
        self._receiver_task = asyncio.create_task(
            self._receive_session_events(),
            name="ava-openai-realtime-sideband-reader",
        )
        # Let the task begin before negotiation returns. This keeps the
        # provider session readable even while no turn consumer is active.
        await asyncio.sleep(0)

    async def next_input_event(self) -> RealtimeProviderInputEvent:
        """Return the next indexed provider input lifecycle event."""

        await self.start_receiver()
        if self._receiver_error is not None and self._input_events.empty():
            raise self._receiver_error
        value = await self._input_events.get()
        if value is self._receiver_terminal:
            if self._receiver_error is not None:
                raise self._receiver_error
            raise OpenAIRealtimeUnavailableError(
                "OpenAI Realtime sideband receiver stopped unexpectedly"
            )
        if not isinstance(value, RealtimeProviderInputEvent):
            raise OpenAIRealtimeProtocolError(
                "OpenAI Realtime router produced an invalid input event"
            )
        return value

    def input_item_owner(self, *, item_id: str) -> str | None:
        """Return the explicitly admitted Ava turn for one observed input item."""

        if not item_id.strip():
            raise ValueError("item_id cannot be empty")
        if item_id not in self._input_item_turn_ids:
            raise OpenAIRealtimeProtocolError(
                "OpenAI input item ownership was requested before observation"
            )
        return self._input_item_turn_ids[item_id]

    async def admit_pending_input_items(self, *, turn_id: str) -> tuple[str, ...]:
        """Explicitly associate pending provider input with an Ava turn."""

        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        pending = tuple(
            item_id
            for item_id, owner in self._input_item_turn_ids.items()
            if owner is None
        )
        for item_id in pending:
            self._admit_input_item(item_id=item_id, turn_id=turn_id)
        return pending

    async def _receive_session_events(self) -> None:
        """Own every raw recv for the negotiated sideband lifetime."""

        try:
            while not self._closed:
                raw = await self._socket.recv()
                event = self._decode_event(raw)
                self._sideband_event_sequence += 1
                event[_SIDEBAND_SEQUENCE_FIELD] = self._sideband_event_sequence
                self._index_provider_identity(event)
                event_type = _event_type(event)
                if event_type in _RESPONSE_DIAGNOSTIC_EVENT_TYPES:
                    response_id = _response_id_from_event(event)
                    _log_response_diagnostic(
                        "openai_realtime_response_event_observed",
                        event=event,
                        response_owner_turn_id=self._response_owner_by_id.get(
                            response_id or ""
                        ),
                        active_turn_id=self._active_turn_id,
                        active_response_id=self._active_response_id,
                    )
                try:
                    self._router_events.put_nowait(event)
                except asyncio.QueueFull as error:
                    raise OpenAIRealtimeRouterBackpressureError(
                        "OpenAI Realtime session router capacity was exhausted"
                    ) from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._receiver_error = _as_realtime_receive_error(error)
            LOGGER.warning(
                "openai_realtime_sideband_reader_stopped error_type=%s",
                type(error).__name__,
                extra={
                    "ava_context": {
                        "component": "realtimeProvider",
                        "errorType": type(error).__name__,
                        **_connection_error_context(error),
                    }
                },
            )
        finally:
            self._put_receiver_terminal()

    def _put_receiver_terminal(self) -> None:
        for event in self._child_transcription_events_by_turn.values():
            event.set()
        for queue in (self._router_events, self._input_events):
            try:
                queue.put_nowait(self._receiver_terminal)
            except asyncio.QueueFull:
                # The receiver has already recorded the explicit capacity
                # failure. Consumers will observe it after retained events.
                pass

    def _decode_event(self, raw: str | bytes) -> dict[str, object]:
        if isinstance(raw, bytes):
            raise OpenAIRealtimeProtocolError(
                "OpenAI Realtime sideband emitted an unexpected binary event"
            )
        if len(raw.encode("utf-8")) > self._max_event_bytes:
            raise OpenAIRealtimeProtocolError(
                "OpenAI Realtime event exceeded Ava's byte limit"
            )
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as error:
            raise OpenAIRealtimeProtocolError(
                "OpenAI Realtime emitted malformed JSON"
            ) from error
        if not isinstance(decoded, dict):
            raise OpenAIRealtimeProtocolError(
                "OpenAI Realtime event must be a JSON object"
            )
        return decoded

    def _index_provider_identity(self, event: Mapping[str, object]) -> None:
        event_type = _event_type(event)
        if event_type in _INPUT_LIFECYCLE_EVENT_TYPES:
            self._index_input_lifecycle(event)
        response_id = _response_id_from_event(event)
        if response_id is not None and event_type.startswith("response."):
            if event_type == "response.created":
                self._awaiting_response_trigger_turn_id = None
                self._interaction_response_readiness_changed.set()
            if response_id not in self._response_owner_by_id:
                # The session receiver observes provider identity before a
                # turn consumer necessarily reaches the event. Keep the
                # response unowned until its explicit response.created
                # admission (or cancellation admission).
                self._response_owner_by_id[response_id] = None
                _retain_latest(self._response_owner_by_id, maximum=64)

    def _index_input_lifecycle(self, event: Mapping[str, object]) -> None:
        event_type = _event_type(event)
        item_id_value = event.get("item_id")
        if event_type == "input_audio_buffer.speech_stopped" and item_id_value is None:
            return
        item_id = _required_string(event, "item_id")
        if event_type in {
            "input_audio_buffer.speech_started",
            "input_audio_buffer.committed",
        }:
            self._awaiting_response_trigger_turn_id = None
            self._interaction_response_readiness_changed.set()
        if (
            event_type in _INPUT_TRANSCRIPTION_PROGRESS_EVENT_TYPES
            or event_type
            in {
                "conversation.item.input_audio_transcription.completed",
                "conversation.item.input_audio_transcription.failed",
            }
        ) and item_id not in self._input_item_turn_ids:
            raise OpenAIRealtimeProtocolError(
                "OpenAI input transcription progress has no committed item owner"
            )
        if item_id not in self._input_item_turn_ids:
            self._input_item_turn_ids[item_id] = None
            _retain_latest(self._input_item_turn_ids, maximum=64)
        if event_type in {
            "input_audio_buffer.speech_stopped",
            "input_audio_buffer.committed",
        }:
            self._input_terminal_observed_at[item_id] = monotonic()
            _retain_latest(self._input_terminal_observed_at, maximum=64)
        if event_type in _INPUT_TRANSCRIPTION_PROGRESS_EVENT_TYPES:
            self._validate_input_transcription_progress(event)
        elif event_type == "conversation.item.input_audio_transcription.completed":
            self._record_input_transcription(event, failed=False)
        elif event_type == "conversation.item.input_audio_transcription.failed":
            self._record_input_transcription(event, failed=True)
        elif event_type == "input_audio_buffer.committed":
            self._queue_input_event(event)
        elif event_type == "input_audio_buffer.speech_started":
            self._queue_input_event(event)
        elif event_type == "input_audio_buffer.speech_stopped":
            self._queue_input_event(event)

    def _queue_input_event(self, event: Mapping[str, object]) -> None:
        event_type = _event_type(event)
        item_id = _required_string(event, "item_id")
        audio_start_ms = event.get("audio_start_ms")
        if audio_start_ms is not None and (
            isinstance(audio_start_ms, bool)
            or not isinstance(audio_start_ms, int)
            or audio_start_ms < 0
        ):
            raise OpenAIRealtimeProtocolError(
                "OpenAI speech_started audio_start_ms is invalid"
            )
        try:
            self._input_events.put_nowait(
                RealtimeProviderInputEvent(
                    event_type=event_type,
                    item_id=item_id,
                    audio_start_ms=audio_start_ms,
                )
            )
        except asyncio.QueueFull as error:
            raise OpenAIRealtimeRouterBackpressureError(
                "OpenAI Realtime input-event router capacity was exhausted"
            ) from error

    async def expect_session_created(self) -> None:
        await self.start_receiver()
        event = await self._receive_event(
            timeout_seconds=self._turn_timeout_seconds,
        )
        if event.get("type") != "session.created":
            raise OpenAIRealtimeProtocolError(
                "OpenAI Realtime did not begin with session.created"
            )

    async def stream_turn(
        self,
        *,
        harness: RealtimeHarness,
        turn_id: str,
        text_input: str | None = None,
        response_max_output_tokens: int | None = None,
    ) -> AsyncIterator[RealtimeProviderTurnEvent]:
        if self._closed:
            raise OpenAIRealtimeProtocolError(
                "cannot start a turn on a closed OpenAI Realtime channel"
            )
        if self._active_turn_id is not None:
            raise OpenAIRealtimeProtocolError(
                "OpenAI Realtime channel already owns an active turn"
            )
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        if response_max_output_tokens is not None and not (
            256 <= response_max_output_tokens <= self._max_output_tokens
        ):
            raise ValueError(
                "response_max_output_tokens must be between 256 and the "
                "configured Realtime maximum"
            )
        normalized_text = None
        if text_input is not None:
            normalized_text = text_input.strip()
            if not 1 <= len(normalized_text) <= 2_000:
                raise ValueError("live probe input must contain 1-2000 characters")

        self._active_turn_id = turn_id
        self._active_response_id = None
        self._awaiting_response_trigger_turn_id = turn_id
        self._interaction_response_turn_id = None
        self._settled_turn_ids.discard(turn_id)
        self._interaction_response_readiness_changed.set()
        self.last_usage = None
        self.last_response_id = None
        self.last_first_delta_ms = None
        self.last_completed_ms = None
        self._received_event_count = 0
        await self.start_receiver()

        response_completed = False
        try:
            # Production negotiation installs the initial harness before the
            # microphone is enabled. Later dynamic projections use the
            # coordinator's readiness-gated installation path; a turn reader
            # must never reinstall an older harness over that safe update.
            if self._prepared_session_update is None:
                await self.prepare(harness=harness)

            trigger_started = monotonic()
            if normalized_text is not None:
                await self._send_json(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": normalized_text,
                                }
                            ],
                        },
                    }
                )
                await self._send_json(
                    {
                        "type": "response.create",
                        "response": {
                            "output_modalities": ["text"],
                            "tool_choice": harness.tool_choice,
                            "parallel_tool_calls": False,
                            "max_output_tokens": (
                                response_max_output_tokens or self._max_output_tokens
                            ),
                        },
                    }
                )

            async for event in self._stream_semantic_turn(
                trigger_started=trigger_started,
                response_expected=normalized_text is not None,
                turn_id=turn_id,
            ):
                yield event
            response_completed = True
        finally:
            # An interrupted/erroring async generator deliberately retains the
            # turn boundary until cancel() drains the provider response. In
            # particular, response.created may already be queued on the wire
            # even when we have not observed its response ID yet.
            if response_completed:
                self._settled_turn_ids.add(turn_id)
                self._active_turn_id = None
                self._active_response_id = None
                self._awaiting_response_trigger_turn_id = None
                self._interaction_response_readiness_changed.set()

    async def complete_canvas_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: (
            InteractiveCanvasToolOutput
            | InteractiveCanvasToolOutputV4
            | InteractiveCanvasCancellationToolOutputV4
        ),
    ) -> None:
        if self._closed:
            raise OpenAIRealtimeProtocolError(
                "cannot complete a tool on a closed Realtime channel"
            )
        if turn_id != self._active_turn_id:
            raise OpenAIRealtimeProtocolError(
                "canvas tool completion does not match the active turn"
            )
        if call_id in self._completed_canvas_call_ids:
            raise OpenAIRealtimeProtocolError("canvas tool call was completed twice")
        if call_id != self._pending_canvas_call_id:
            raise OpenAIRealtimeProtocolError(
                "canvas tool completion does not match the pending call"
            )
        await self._send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output.model_dump_json(by_alias=True, exclude_none=True),
                },
            }
        )
        await self._send_json(
            {
                "type": "response.create",
                "response": {
                    "conversation": "auto",
                    "output_modalities": ["text"],
                    "tool_choice": "none",
                    "max_output_tokens": min(
                        _CANVAS_TOOL_CONTINUATION_PREFERRED_MAX_OUTPUT_TOKENS,
                        self._max_output_tokens,
                    ),
                },
            }
        )
        _log_response_diagnostic(
            "openai_realtime_canvas_continuation_requested",
            event_type="response.create",
            consumer_turn_id=turn_id,
            consumer_phase="canvasContinuation",
            active_turn_id=self._active_turn_id,
            active_response_id=self._active_response_id,
            call_id=call_id,
        )
        self._completed_canvas_call_ids.add(call_id)
        self._pending_canvas_call_id = None
        self._interaction_response_readiness_changed.set()

    async def complete_utility_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: RealtimeUtilityToolOutputV4,
        harness: RealtimeHarness,
    ) -> None:
        if self._closed:
            raise OpenAIRealtimeProtocolError(
                "cannot complete a tool on a closed Realtime channel"
            )
        if turn_id != self._active_turn_id:
            raise OpenAIRealtimeProtocolError(
                "utility tool completion does not match the active turn"
            )
        if call_id in self._completed_utility_call_ids:
            raise OpenAIRealtimeProtocolError("utility tool call was completed twice")
        if call_id != self._pending_utility_call_id:
            raise OpenAIRealtimeProtocolError(
                "utility tool completion does not match the pending call"
            )
        await self._send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output.model_dump_json(by_alias=True, exclude_none=True),
                },
            }
        )
        rejected = isinstance(output, RejectedUtilityToolOutputV4)
        response: dict[str, object] = {
            "conversation": "auto",
            "output_modalities": ["text"],
            "tool_choice": "none" if rejected else "auto",
            "parallel_tool_calls": False,
            "max_output_tokens": min(
                _CANVAS_TOOL_CONTINUATION_PREFERRED_MAX_OUTPUT_TOKENS,
                self._max_output_tokens,
            ),
        }
        if not rejected:
            response["tools"] = [
                tool
                for tool in harness.tools
                if tool.get("name")
                in {
                    INTERACTIVE_CANVAS_TOOL_NAME,
                    CANCEL_INTERACTIVE_CANVAS_TOOL_NAME,
                }
            ]
        await self._send_json({"type": "response.create", "response": response})
        self._completed_utility_call_ids.add(call_id)
        self._pending_utility_call_id = None
        self._interaction_response_readiness_changed.set()

    def _canvas_interaction_response_readiness(
        self,
        turn_id: str,
    ) -> str | None:
        if self._closed:
            return "providerClosed"
        if turn_id in self._cancelled_turn_ids or turn_id in self._settled_turn_ids:
            return "turnSettledOrCancelled"
        if self._active_turn_id != turn_id:
            return "turnMismatch"
        if any(owner is None for owner in self._input_item_turn_ids.values()):
            return "unresolvedInputOwner"
        if any(owner is None for owner in self._response_owner_by_id.values()):
            return "unresolvedResponseOwner"
        if any(owner == turn_id for owner in self._input_item_turn_ids.values()):
            return "turnAlreadyOwnsInput"
        if self._active_response_id is not None:
            return "activeResponse"
        if self._pending_canvas_call_id is not None:
            return "pendingCanvasCall"
        if self._pending_utility_call_id is not None:
            return "pendingUtilityCall"
        if self._awaiting_response_trigger_turn_id != turn_id:
            return "notAwaitingTrigger"
        return None

    async def request_canvas_interaction_response(
        self,
        *,
        turn_id: str,
        interaction: RecordedSvgCanvasInteraction,
    ) -> bool:
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        unready_reason = self._canvas_interaction_response_readiness(turn_id)
        if unready_reason is not None:
            LOGGER.info(
                "openai_canvas_interaction_response_not_ready turn_id=%s reason=%s",
                turn_id,
                unready_reason,
                extra={
                    "ava_context": {
                        "turnId": turn_id,
                        "unreadyReason": unready_reason,
                    }
                },
            )
            return False
        semantic_context = _canvas_interaction_context_text(interaction)
        self._awaiting_response_trigger_turn_id = None
        self._interaction_response_turn_id = turn_id
        self._interaction_response_readiness_changed.set()
        await self._send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": semantic_context,
                        }
                    ],
                },
            }
        )
        await self._send_json(
            {
                "type": "response.create",
                "response": {
                    "conversation": "auto",
                    "output_modalities": ["text"],
                    "tool_choice": "none",
                    "max_output_tokens": (
                        _CANVAS_INTERACTION_RESPONSE_MAX_OUTPUT_TOKENS
                    ),
                },
            }
        )
        _log_response_diagnostic(
            "openai_realtime_canvas_interaction_response_requested",
            event_type="response.create",
            consumer_turn_id=turn_id,
            consumer_phase="canvasInteraction",
            active_turn_id=self._active_turn_id,
            active_response_id=self._active_response_id,
        )
        return True

    async def wait_until_canvas_interaction_response_ready(
        self,
        *,
        turn_id: str,
    ) -> bool:
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        while True:
            readiness = self._canvas_interaction_response_readiness(turn_id)
            if readiness is None:
                return True
            if readiness in {"providerClosed", "turnSettledOrCancelled"}:
                return False
            self._interaction_response_readiness_changed.clear()
            readiness = self._canvas_interaction_response_readiness(turn_id)
            if readiness is None:
                return True
            if readiness in {"providerClosed", "turnSettledOrCancelled"}:
                return False
            await self._interaction_response_readiness_changed.wait()

    @property
    def active_context_build_id(self) -> str | None:
        return self._installed_context_build_id

    async def prepare(
        self,
        *,
        harness: RealtimeHarness,
    ) -> RealtimeContextInstallationStatus:
        """Prepare the sideband before Flutter is told to unmute the mic."""

        payload = _session_update(
            harness,
            self._model,
            self._max_output_tokens,
            transcription_model=self._transcription_model,
            noise_reduction=self._noise_reduction,
            vad_threshold=self._vad_threshold,
            vad_prefix_padding_ms=self._vad_prefix_padding_ms,
            vad_silence_duration_ms=self._vad_silence_duration_ms,
            truncation_post_instructions_tokens=(
                self._truncation_post_instructions_tokens
            ),
            truncation_retention_ratio=self._truncation_retention_ratio,
        )
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if serialized == self._prepared_session_update:
            self._installed_context_build_id = harness.context.build_id
            LOGGER.info(
                "openai_realtime_prompt_deduplicated prompt_version=%s "
                "tool_contract_version=%s",
                harness.prompt_version,
                harness.tool_contract_version,
            )
            return RealtimeContextInstallationStatus.DEDUPLICATED
        initial_prepare = self._prepared_session_update is None
        await self._send_json(payload)
        # The first update must be acknowledged before Flutter is allowed to
        # unmute. On later turns, the live WebRTC input can already have queued
        # VAD/conversation events. Waiting for session.updated here would
        # consume one of those events outside the turn reader and previously
        # killed the persistent session after a single utterance. The next
        # stream_turn owns the queue and validates session.updated or an error
        # in order with the rest of the provider events.
        if initial_prepare:
            await self._wait_for_session_updated()
        self._prepared_session_update = serialized
        self._installed_context_build_id = harness.context.build_id
        LOGGER.info(
            "openai_realtime_prompt_installed prompt_version=%s "
            "tool_contract_version=%s acknowledged=%s "
            "truncation_post_instructions_tokens=%d "
            "truncation_retention_ratio=%.2f",
            harness.prompt_version,
            harness.tool_contract_version,
            initial_prepare,
            self._truncation_post_instructions_tokens,
            self._truncation_retention_ratio,
            extra={
                "ava_context": {
                    "promptVersion": harness.prompt_version,
                    "toolContractVersion": harness.tool_contract_version,
                    "providerAcknowledged": initial_prepare,
                    "truncationPostInstructionsTokens": (
                        self._truncation_post_instructions_tokens
                    ),
                    "truncationRetentionRatio": self._truncation_retention_ratio,
                }
            },
        )
        return RealtimeContextInstallationStatus.INSTALLED

    async def prepare_if_idle(
        self,
        *,
        harness: RealtimeHarness,
        turn_id: str,
    ) -> RealtimeContextInstallationStatus | None:
        if self._canvas_interaction_response_readiness(turn_id) is not None:
            return None
        previous_build_id = self._installed_context_build_id
        status = await self.prepare(harness=harness)
        if self._canvas_interaction_response_readiness(turn_id) is None:
            return status
        self._installed_context_build_id = previous_build_id
        return None

    async def cancel(self, *, turn_id: str) -> None:
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        if turn_id in self._cancelled_turn_ids:
            return
        if self._awaiting_response_trigger_turn_id == turn_id:
            self._awaiting_response_trigger_turn_id = None
            self._interaction_response_readiness_changed.set()
        cancelled_pending_utility = (
            not self._closed
            and self._active_turn_id == turn_id
            and self._pending_utility_call_id is not None
        )
        if cancelled_pending_utility:
            call_id = self._pending_utility_call_id
            await self._send_json(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": RejectedUtilityToolOutputV4(
                            reason="The child interrupted this utility request."
                        ).model_dump_json(by_alias=True),
                    },
                }
            )
            self._pending_utility_call_id = None
            self._active_response_id = None
            self._active_turn_id = None
            self._cancelled_turn_ids.add(turn_id)
            self._interaction_response_readiness_changed.set()
            return
        cancelled_pending_canvas = (
            not self._closed
            and self._active_turn_id == turn_id
            and self._pending_canvas_call_id is not None
        )
        if cancelled_pending_canvas:
            call_id = self._pending_canvas_call_id
            await self._send_json(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": InteractiveCanvasToolOutput.model_validate(
                            {
                                "status": "rejected",
                                "reason": (
                                    "The child interrupted this visual request."
                                ),
                            }
                        ).model_dump_json(by_alias=True, exclude_none=True),
                    },
                }
            )
            self._pending_canvas_call_id = None
            # The function-call response has already reached response.done and
            # no continuation is created for an interrupted visual request.
            self._active_response_id = None
            self._active_turn_id = None
            self._cancelled_turn_ids.add(turn_id)
            self._interaction_response_readiness_changed.set()
            return
        if self._closed or self._active_turn_id != turn_id:
            self._cancelled_turn_ids.add(turn_id)
            self._interaction_response_readiness_changed.set()
            return
        await self._cancel_and_drain_active_response()
        self._cancelled_turn_ids.add(turn_id)
        self._interaction_response_readiness_changed.set()

    async def _cancel_and_drain_active_response(self) -> None:
        """Restore the persistent socket to an exact response boundary."""

        cancelled_turn_id = self._active_turn_id
        if cancelled_turn_id is None:
            raise OpenAIRealtimeProtocolError(
                "OpenAI cancellation requires an active Ava turn"
            )
        response_id = self._active_response_id
        cancel_not_active_before_response_created = False
        pending_interaction_create = (
            response_id is None
            and self._interaction_response_turn_id == cancelled_turn_id
        )
        if response_id is not None:
            self._admit_response(
                response_id=response_id,
                turn_id=cancelled_turn_id,
            )
        active_cancel_outcome = await self._send_response_cancel(
            turn_id=cancelled_turn_id,
            response_id=response_id,
        )
        deferred_events: list[dict[str, object]] = []
        while True:
            event = await self._receive_event(
                timeout_seconds=self._turn_timeout_seconds,
            )
            event_type = _event_type(event)
            if event_type == "error":
                if _provider_error_code(event) == "response_cancel_not_active":
                    outcome = self._consume_correlated_cancel_outcome(event)
                    if outcome is None:
                        resolved = self._consume_recently_resolved_cancel_outcome(event)
                        if resolved is None:
                            raise _provider_error(event)
                        LOGGER.info(
                            "openai_realtime_late_cancel_error_consumed "
                            "cancel_event_id=%s cancelled_turn_id=%s "
                            "cancelled_response_id=%s consumer_turn_id=%s",
                            resolved.cancel_event_id,
                            resolved.cancelled_turn_id,
                            resolved.response_id,
                            cancelled_turn_id,
                        )
                        continue
                    if outcome.cancel_event_id != active_cancel_outcome.cancel_event_id:
                        LOGGER.info(
                            "openai_realtime_late_cancel_error_consumed "
                            "cancel_event_id=%s cancel_sequence=%d "
                            "cancelled_turn_id=%s "
                            "cancelled_response_id=%s consumer_turn_id=%s",
                            outcome.cancel_event_id,
                            outcome.cancel_sequence,
                            outcome.cancelled_turn_id,
                            outcome.response_id,
                            cancelled_turn_id,
                        )
                        continue
                    if response_id is not None:
                        _log_response_diagnostic(
                            "openai_realtime_cancel_not_active_waiting_for_terminal",
                            event=event,
                            response_id=response_id,
                            response_owner_turn_id=self._response_owner_by_id.get(
                                response_id
                            ),
                            consumer_turn_id=self._active_turn_id,
                            consumer_phase="cancellation",
                            active_turn_id=self._active_turn_id,
                            active_response_id=self._active_response_id,
                        )
                        # The response ID has already been admitted to this
                        # turn. A cancel-not-active error can race ahead of its
                        # in-transit response.done, but it cannot transfer or
                        # settle that response's ownership boundary.
                        continue
                    if pending_interaction_create:
                        cancel_not_active_before_response_created = True
                        _log_response_diagnostic(
                            "openai_realtime_cancel_not_active_pending_create",
                            event=event,
                            response_owner_turn_id=cancelled_turn_id,
                            consumer_turn_id=cancelled_turn_id,
                            consumer_phase="cancellation",
                            active_turn_id=self._active_turn_id,
                            active_response_id=self._active_response_id,
                        )
                        continue
                    LOGGER.info(
                        "openai_realtime_cancel_already_settled turn_id=%s "
                        "response_id=%s",
                        self._active_turn_id,
                        response_id,
                    )
                    self._pending_events.extend(deferred_events)
                    self._active_response_id = None
                    self._active_turn_id = None
                    return
                raise _provider_error(event)
            if event_type == "response.created":
                candidate = _nested_string(event, "response", "id")
                if response_id is not None and candidate != response_id:
                    raise OpenAIRealtimeProtocolError(
                        "OpenAI created a different response while cancelling"
                    )
                response_id = candidate
                self._admit_response(
                    response_id=candidate,
                    turn_id=cancelled_turn_id,
                )
                self._active_response_id = candidate
                self._bind_outstanding_cancel_outcome_response(
                    turn_id=cancelled_turn_id,
                    response_id=candidate,
                )
                pending_interaction_create = False
                if cancel_not_active_before_response_created:
                    active_cancel_outcome = await self._send_response_cancel(
                        turn_id=cancelled_turn_id,
                        response_id=candidate,
                    )
                continue
            if event_type == "response.done":
                completed_response_id = _nested_string(event, "response", "id")
                if response_id is not None and completed_response_id != response_id:
                    raise OpenAIRealtimeProtocolError(
                        "OpenAI completed a different response while cancelling"
                    )
                if completed_response_id is None:
                    raise OpenAIRealtimeProtocolError(
                        "OpenAI completed response without a response ID"
                    )
                owner = self._response_owner_by_id.get(completed_response_id)
                if owner != cancelled_turn_id:
                    raise OpenAIRealtimeProtocolError(
                        "OpenAI completed response belongs to a different Ava turn"
                    )
                status = _nested_string(event, "response", "status")
                self._mark_response_terminal_consumed(
                    event,
                    response_id=completed_response_id,
                    turn_id=cancelled_turn_id,
                    consumer_phase="cancellation",
                )
                _log_response_diagnostic(
                    "openai_realtime_response_drained",
                    event=event,
                    response_id=completed_response_id,
                    response_owner_turn_id=owner,
                    consumer_turn_id=self._active_turn_id,
                    consumer_phase="cancellation",
                    active_turn_id=self._active_turn_id,
                    active_response_id=self._active_response_id,
                    status=status,
                )
                self._pending_events.extend(deferred_events)
                # A completed response may race the cancellation error. Keep
                # its outcome provenance until that error is consumed,
                # including across later semantic responses.
                if status != "completed":
                    self._resolve_outstanding_cancel_outcome(
                        turn_id=cancelled_turn_id,
                        response_id=completed_response_id,
                        terminal_status=status,
                    )
                if self._interaction_response_turn_id == cancelled_turn_id:
                    self._interaction_response_turn_id = None
                self._active_response_id = None
                self._active_turn_id = None
                return
            event_response_id = event.get("response_id")
            if event_response_id is not None:
                if not isinstance(event_response_id, str) or not event_response_id:
                    raise OpenAIRealtimeProtocolError(
                        "OpenAI emitted an invalid response ID while cancelling"
                    )
                if response_id is not None and event_response_id != response_id:
                    raise OpenAIRealtimeProtocolError(
                        "OpenAI emitted a different response while cancelling"
                    )
                response_id = event_response_id
                self._active_response_id = event_response_id
                self._bind_outstanding_cancel_outcome_response(
                    turn_id=cancelled_turn_id,
                    response_id=event_response_id,
                )
            if event_type in _PRE_RESPONSE_EVENT_TYPES:
                deferred_events.append(event)
                continue
            if event_type in _INPUT_LIFECYCLE_EVENT_TYPES:
                # Input transcription is asynchronous and may belong to the
                # just-cancelled turn or to the child's barge-in. Preserve the
                # provider ordering and let the next turn boundary bind each
                # committed item by item_id.
                deferred_events.append(event)
                continue
            if (
                event_type not in _IN_TURN_IGNORED_EVENT_TYPES
                and not event_type.startswith("response.")
            ):
                raise OpenAIRealtimeProtocolError(
                    f"unexpected OpenAI event while cancelling: {event_type}"
                )

    def available_child_transcript_for_turn(self, turn_id: str) -> str | None:
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        return self._child_transcripts_by_turn.get(turn_id)

    async def child_transcript_for_turn(self, turn_id: str) -> str | None:
        await self.start_receiver()
        transcript = self._child_transcripts_by_turn.get(turn_id)
        if transcript is not None:
            return transcript
        # Transcription is explicitly asynchronous to the model response. A
        # short drain after response.done lets committed-turn memory observe a
        # transcript that arrived just behind the response without turning STT
        # into a speech or playback gate. Non-transcription events are retained
        # in exact order for the next turn reader.
        while True:
            try:
                event = await self._receive_event(timeout_seconds=0.05)
            except _OpenAIRealtimeReceiveTimeout:
                return self._child_transcripts_by_turn.get(turn_id)
            event_type = _event_type(event)
            if event_type in _INPUT_TRANSCRIPTION_PROGRESS_EVENT_TYPES:
                self._validate_input_transcription_progress(event)
                continue
            if event_type == "conversation.item.input_audio_transcription.completed":
                self._record_input_transcription(event, failed=False)
            elif event_type == "conversation.item.input_audio_transcription.failed":
                self._record_input_transcription(event, failed=True)
            elif event_type in _PRE_RESPONSE_EVENT_TYPES:
                continue
            else:
                self._pending_events.appendleft(event)
                return self._child_transcripts_by_turn.get(turn_id)
            transcript = self._child_transcripts_by_turn.get(turn_id)
            if transcript is not None:
                return transcript

    async def wait_for_child_transcript_for_turn(self, turn_id: str) -> str | None:
        """Wait for the terminal transcription of one explicitly owned input item."""

        await self.start_receiver()
        transcript = self._child_transcripts_by_turn.get(turn_id)
        if transcript is not None:
            return transcript
        owned_item_ids = tuple(
            item_id
            for item_id, owner_turn_id in self._input_item_turn_ids.items()
            if owner_turn_id == turn_id
        )
        if not owned_item_ids:
            return None
        if len(owned_item_ids) != 1:
            raise OpenAIRealtimeProtocolError(
                "one Ava turn cannot own multiple committed input items"
            )
        item_id = owned_item_ids[0]
        outcome = self._input_transcription_outcomes.get(item_id)
        if outcome is not None:
            return outcome.transcript

        terminal = self._child_transcription_events_by_turn.setdefault(
            turn_id,
            asyncio.Event(),
        )
        # Receiver termination is a one-time broadcast. If it happened before
        # this turn registered its waiter, make that already-terminal state
        # observable here. If termination races after registration,
        # _put_receiver_terminal() owns the wake instead.
        receiver_task = self._receiver_task
        if receiver_task is not None and receiver_task.done():
            terminal.set()
        try:
            await terminal.wait()
        finally:
            if self._child_transcription_events_by_turn.get(turn_id) is terminal:
                self._child_transcription_events_by_turn.pop(turn_id, None)
        outcome = self._input_transcription_outcomes.get(item_id)
        if outcome is not None:
            return outcome.transcript
        if self._receiver_error is not None:
            raise self._receiver_error
        if self._closed:
            return None
        raise OpenAIRealtimeProtocolError(
            "OpenAI input transcription waiter woke without a terminal outcome"
        )

    def _bind_committed_input_item(
        self,
        event: Mapping[str, object],
        *,
        turn_id: str,
    ) -> None:
        item_id = _required_string(event, "item_id")
        existing = self._input_item_turn_ids.get(item_id)
        if existing is not None:
            if existing == turn_id:
                return
            # A committed item can remain in the routed mailbox after the
            # turn that admitted it has already been cancelled. It is a
            # known duplicate/late event, never a request to reassign that
            # provider item to the newer turn.
            LOGGER.info(
                "openai_late_committed_item_ignored item_id=%s owner_turn_id=%s "
                "consumer_turn_id=%s",
                item_id,
                existing,
                turn_id,
            )
            return
        originating_item_id = next(
            (
                candidate_item_id
                for candidate_item_id, owner_turn_id in self._input_item_turn_ids.items()
                if owner_turn_id == turn_id
            ),
            None,
        )
        if originating_item_id is not None and originating_item_id != item_id:
            # A turn owns exactly one committed provider input item. A later
            # item observed while its response is still active is a pending
            # barge-in for the next turn, not additional input for this turn.
            LOGGER.info(
                "openai_input_item_deferred active_turn_id=%s "
                "originating_item_id=%s pending_item_id=%s",
                turn_id,
                originating_item_id,
                item_id,
            )
            return
        self._admit_input_item(item_id=item_id, turn_id=turn_id)

    def _admit_input_item(self, *, item_id: str, turn_id: str) -> None:
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        existing = self._input_item_turn_ids.get(item_id)
        if item_id not in self._input_item_turn_ids:
            raise OpenAIRealtimeProtocolError(
                "OpenAI input item cannot be admitted before it is observed"
            )
        if existing is not None and existing != turn_id:
            raise OpenAIRealtimeProtocolError(
                "OpenAI input item changed Ava turn ownership"
            )
        self._input_item_turn_ids[item_id] = turn_id
        self._interaction_response_readiness_changed.set()
        outcome = self._input_transcription_outcomes.get(item_id)
        if outcome is not None:
            if outcome.turn_id is not None and outcome.turn_id != turn_id:
                raise OpenAIRealtimeProtocolError(
                    "OpenAI input transcription changed committed item ownership"
                )
            self._input_transcription_outcomes[item_id] = _InputTranscriptionOutcome(
                turn_id=turn_id,
                transcript=outcome.transcript,
                failed=outcome.failed,
            )
            if outcome.transcript is not None:
                self._child_transcripts_by_turn[turn_id] = outcome.transcript
            _retain_latest(self._child_transcripts_by_turn, maximum=64)
            terminal = self._child_transcription_events_by_turn.get(turn_id)
            if terminal is not None:
                terminal.set()
        _retain_latest(self._input_item_turn_ids, maximum=64)

    def _validate_response_owner(
        self,
        event: Mapping[str, object],
        *,
        turn_id: str,
        consumer_phase: str,
    ) -> None:
        response_id = _response_id_from_event(event)
        if response_id is None:
            raise OpenAIRealtimeProtocolError(
                "OpenAI response event omitted its response ID"
            )
        owner = self._response_owner_by_id.get(response_id)
        if owner is None and response_id not in self._response_owner_by_id:
            raise OpenAIRealtimeProtocolError(
                "OpenAI response event has no registered response owner"
            )
        if owner is None:
            if _event_type(event) != "response.created":
                raise OpenAIRealtimeProtocolError(
                    "OpenAI response event arrived before response.created"
                )
            self._admit_response(response_id=response_id, turn_id=turn_id)
            _log_response_diagnostic(
                "openai_realtime_response_owner_admitted",
                event=event,
                response_owner_turn_id=turn_id,
                consumer_turn_id=turn_id,
                consumer_phase=consumer_phase,
                active_turn_id=self._active_turn_id,
                active_response_id=self._active_response_id,
                provider_call_id=self._provider_call_id,
                active_context_build_id=self._installed_context_build_id,
            )
            return
        if owner != turn_id:
            _log_response_diagnostic(
                "openai_realtime_response_owner_mismatch",
                level=logging.ERROR,
                event=event,
                response_owner_turn_id=owner,
                consumer_turn_id=turn_id,
                consumer_phase=consumer_phase,
                active_turn_id=self._active_turn_id,
                active_response_id=self._active_response_id,
            )
            raise OpenAIRealtimeProtocolError(
                "OpenAI response event belongs to a different Ava turn"
            )

    def _admit_response(self, *, response_id: str, turn_id: str) -> None:
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        if response_id not in self._response_owner_by_id:
            raise OpenAIRealtimeProtocolError(
                "OpenAI response cannot be admitted before it is observed"
            )
        owner = self._response_owner_by_id[response_id]
        if owner is not None and owner != turn_id:
            raise OpenAIRealtimeProtocolError(
                "OpenAI response changed Ava turn ownership"
            )
        self._response_owner_by_id[response_id] = turn_id
        self._interaction_response_readiness_changed.set()

    def _mark_response_terminal_consumed(
        self,
        event: Mapping[str, object],
        *,
        response_id: str,
        turn_id: str,
        consumer_phase: str,
        usage: OpenAIRealtimeUsage | None = None,
    ) -> None:
        owner = self._response_owner_by_id.get(response_id)
        if owner is None or owner != turn_id:
            raise OpenAIRealtimeProtocolError(
                "OpenAI terminal response owner changed before consumption"
            )
        if response_id in self._consumed_terminal_response_owner_by_id:
            raise OpenAIRealtimeProtocolError(
                "OpenAI terminal response was consumed more than once"
            )
        reported_usage = usage or _parse_usage(event)
        self._consumed_terminal_response_owner_by_id[response_id] = owner
        _retain_latest(
            self._consumed_terminal_response_owner_by_id,
            maximum=_MAX_CONSUMED_TERMINAL_RESPONSE_IDS,
        )
        self._interaction_response_readiness_changed.set()
        _log_response_diagnostic(
            "openai_realtime_response_terminal_consumed",
            event=event,
            response_owner_turn_id=owner,
            consumer_turn_id=turn_id,
            consumer_phase=consumer_phase,
            active_turn_id=self._active_turn_id,
            active_response_id=self._active_response_id,
        )
        LOGGER.info(
            "openai_realtime_usage",
            extra={
                "ava_context": {
                    "provider": "openai",
                    "model": self._model,
                    "usageScope": "response",
                    "providerCallId": self._provider_call_id,
                    "responseId": response_id,
                    "turnId": owner,
                    "activeContextBuildId": self._installed_context_build_id,
                    "inputTokens": reported_usage.input_tokens,
                    "cachedInputTokens": reported_usage.cached_input_tokens,
                    "inputTextTokens": reported_usage.input_text_tokens,
                    "inputAudioTokens": reported_usage.input_audio_tokens,
                    "outputTokens": reported_usage.output_tokens,
                    "outputTextTokens": reported_usage.output_text_tokens,
                    "outputAudioTokens": reported_usage.output_audio_tokens,
                    "totalTokens": reported_usage.total_tokens,
                }
            },
        )

    def _validate_input_transcription_progress(
        self,
        event: Mapping[str, object],
    ) -> None:
        event_type = _event_type(event)
        item_id = _required_string(event, "item_id")
        if item_id not in self._input_item_turn_ids:
            raise OpenAIRealtimeProtocolError(
                "OpenAI input transcription progress has no committed item owner"
            )
        if event_type == "conversation.item.input_audio_transcription.delta":
            _required_string(event, "delta", allow_empty=True)
            return
        if event_type == "conversation.item.input_audio_transcription.segment":
            _required_string(event, "id")
            _required_string(event, "speaker")
            _required_string(event, "text", allow_empty=True)
            return
        raise OpenAIRealtimeProtocolError(
            f"unsupported OpenAI input transcription progress event: {event_type}"
        )

    def _record_input_transcription(
        self,
        event: Mapping[str, object],
        *,
        failed: bool,
    ) -> None:
        item_id = _required_string(event, "item_id")
        previous = self._input_transcription_outcomes.get(item_id)
        turn_id = self._input_item_turn_ids.get(item_id)
        if item_id not in self._input_item_turn_ids:
            if previous is None:
                raise OpenAIRealtimeProtocolError(
                    "OpenAI input transcription has no committed item owner"
                )
            turn_id = previous.turn_id
        elif previous is not None and previous.turn_id != turn_id:
            raise OpenAIRealtimeProtocolError(
                "OpenAI input transcription changed committed item ownership"
            )

        if failed:
            outcome = _InputTranscriptionOutcome(
                turn_id=turn_id,
                transcript=None,
                failed=True,
            )
        else:
            transcript = _required_string(event, "transcript", allow_empty=True)
            clean_text = transcript.strip()
            outcome = _InputTranscriptionOutcome(
                turn_id=turn_id,
                transcript=clean_text or None,
                failed=False,
            )

        if previous is not None:
            if previous != outcome:
                raise OpenAIRealtimeProtocolError(
                    "OpenAI input transcription changed after its terminal event"
                )
            LOGGER.info(
                "openai_child_transcription_duplicate turn_id=%s item_id=%s "
                "failed=%s has_text=%s",
                turn_id,
                item_id,
                failed,
                outcome.transcript is not None,
            )
            return

        self._input_transcription_outcomes[item_id] = outcome
        _retain_latest(self._input_transcription_outcomes, maximum=64)
        if turn_id is not None:
            terminal = self._child_transcription_events_by_turn.get(turn_id)
            if terminal is not None:
                terminal.set()

        if failed:
            LOGGER.warning(
                "openai_child_transcription_failed turn_id=%s item_id=%s",
                turn_id,
                item_id,
            )
            return

        if outcome.transcript is None:
            LOGGER.info(
                "openai_child_transcription_empty turn_id=%s item_id=%s",
                turn_id,
                item_id,
            )
            return

        if turn_id is not None:
            self._child_transcripts_by_turn[turn_id] = outcome.transcript
            _retain_latest(self._child_transcripts_by_turn, maximum=64)
        LOGGER.info(
            "openai_child_transcript_received turn_id=%s item_id=%s characters=%d",
            turn_id,
            item_id,
            len(outcome.transcript or ""),
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._awaiting_response_trigger_turn_id = None
        self._interaction_response_turn_id = None
        self._interaction_response_readiness_changed.set()
        try:
            await self._socket.close(code=1000, reason="Ava session closed")
        finally:
            task = self._receiver_task
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    async def _wait_for_session_updated(self) -> None:
        while True:
            event = await self._receive_event(
                timeout_seconds=self._turn_timeout_seconds,
            )
            event_type = _event_type(event)
            if event_type == "session.updated":
                return
            if event_type == "error":
                raise _provider_error(event)
            if event_type not in _PRE_RESPONSE_EVENT_TYPES:
                raise OpenAIRealtimeProtocolError(
                    f"unexpected OpenAI event before session.updated: {event_type}"
                )

    async def _stream_semantic_turn(
        self,
        *,
        trigger_started: float,
        response_expected: bool,
        turn_id: str,
    ) -> AsyncIterator[RealtimeProviderTurnEvent]:
        continuation = False
        canvas_called = False
        utility_called = False
        tool_steps_used = 0
        utility_chain_rejected = False
        accumulated_usage: OpenAIRealtimeUsage | None = None

        while True:
            response_id: str | None = None
            call_id: str | None = None
            function_name: str | None = None
            argument_parts: list[str] = []
            final_arguments: str | None = None
            function_item_count = 0
            response_created_at: float | None = None
            first_text_delta_observed = False

            while True:
                event = await self._receive_event(
                    timeout_seconds=(
                        self._turn_timeout_seconds if response_expected else None
                    )
                )
                event_type = _event_type(event)
                if event_type.startswith("response."):
                    self._validate_response_owner(
                        event,
                        turn_id=turn_id,
                        consumer_phase=(
                            "canvasContinuation" if continuation else "turnResponse"
                        ),
                    )
                if event_type == "error":
                    if _provider_error_code(event) == "response_cancel_not_active":
                        outcome = self._consume_correlated_cancel_outcome(event)
                        if outcome is None:
                            resolved = self._consume_recently_resolved_cancel_outcome(
                                event
                            )
                            if resolved is None:
                                raise _provider_error(event)
                            LOGGER.info(
                                "openai_realtime_late_cancel_error_consumed "
                                "cancel_event_id=%s cancelled_turn_id=%s "
                                "cancelled_response_id=%s consumer_turn_id=%s",
                                resolved.cancel_event_id,
                                resolved.cancelled_turn_id,
                                resolved.response_id,
                                turn_id,
                            )
                            continue
                        LOGGER.info(
                            "openai_realtime_late_cancel_error_consumed "
                            "cancel_event_id=%s cancel_sequence=%d "
                            "cancelled_turn_id=%s "
                            "cancelled_response_id=%s consumer_turn_id=%s",
                            outcome.cancel_event_id,
                            outcome.cancel_sequence,
                            outcome.cancelled_turn_id,
                            outcome.response_id,
                            turn_id,
                        )
                        continue
                    raise _provider_error(event)

                if event_type == "input_audio_buffer.committed":
                    self._bind_committed_input_item(
                        event,
                        turn_id=turn_id,
                    )

                if event_type in _INPUT_TRANSCRIPTION_PROGRESS_EVENT_TYPES:
                    self._validate_input_transcription_progress(event)
                    continue

                if (
                    event_type
                    == "conversation.item.input_audio_transcription.completed"
                ):
                    self._record_input_transcription(event, failed=False)
                    continue

                if event_type == "conversation.item.input_audio_transcription.failed":
                    self._record_input_transcription(event, failed=True)
                    continue

                if event_type == "response.created":
                    response_created_at = monotonic()
                    self._awaiting_response_trigger_turn_id = None
                    self._interaction_response_readiness_changed.set()
                    if not response_expected:
                        trigger_started = monotonic()
                    response_expected = True
                    candidate = _nested_string(event, "response", "id")
                    if response_id is not None and candidate != response_id:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI response ID changed after response.created"
                        )
                    response_id = candidate
                    self._active_response_id = candidate
                    if (
                        not continuation
                        and self._interaction_response_turn_id != turn_id
                    ):
                        input_terminal_at = max(
                            (
                                observed_at
                                for item_id, observed_at in (
                                    self._input_terminal_observed_at.items()
                                )
                                if self._input_item_turn_ids.get(item_id) == turn_id
                            ),
                            default=None,
                        )
                        if input_terminal_at is not None:
                            elapsed_ms = max(
                                0.0,
                                (response_created_at - input_terminal_at) * 1000,
                            )
                            LOGGER.info(
                                "openai_realtime_latency turn_id=%s response_id=%s "
                                "stage=input_terminal_to_response_created "
                                "elapsed_ms=%.2f",
                                turn_id,
                                candidate,
                                elapsed_ms,
                                extra={
                                    "ava_context": {
                                        "turnId": turn_id,
                                        "providerResponseId": candidate,
                                        "stage": ("inputTerminalToResponseCreated"),
                                        "elapsedMs": elapsed_ms,
                                    }
                                },
                            )
                    continue

                if event_type in (
                    "response.text.delta",
                    "response.output_text.delta",
                ):
                    candidate_response_id = _required_string(event, "response_id")
                    if response_id is not None and candidate_response_id != response_id:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI response ID changed while streaming speech"
                        )
                    response_id = candidate_response_id
                    self._active_response_id = candidate_response_id
                    delta = _required_string(event, "delta", allow_empty=True)
                    if delta:
                        if self.last_first_delta_ms is None:
                            self.last_first_delta_ms = (
                                monotonic() - trigger_started
                            ) * 1000
                        if (
                            not first_text_delta_observed
                            and response_created_at is not None
                        ):
                            first_text_delta_observed = True
                            created_to_delta_ms = max(
                                0.0,
                                (monotonic() - response_created_at) * 1000,
                            )
                            LOGGER.info(
                                "openai_realtime_latency turn_id=%s "
                                "response_id=%s "
                                "stage=response_created_to_first_text_delta "
                                "elapsed_ms=%.2f",
                                turn_id,
                                candidate_response_id,
                                created_to_delta_ms,
                                extra={
                                    "ava_context": {
                                        "turnId": turn_id,
                                        "providerResponseId": candidate_response_id,
                                        "stage": ("responseCreatedToFirstTextDelta"),
                                        "elapsedMs": created_to_delta_ms,
                                    }
                                },
                            )
                        yield RealtimeSpeechTextDelta(delta=delta)
                    continue

                if event_type == "response.output_item.added":
                    item = _mapping(event.get("item"), "response output item")
                    if item.get("type") != "function_call":
                        continue
                    if self._interaction_response_turn_id == turn_id:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI canvas interaction response attempted a tool call"
                        )
                    function_item_count += 1
                    if function_item_count > 1 or canvas_called or tool_steps_used >= 2:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI emitted an unexpected additional function call"
                        )
                    function_name = _required_string(item, "name")
                    if function_name not in _SEMANTIC_TOOL_NAMES:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI selected an unavailable function"
                        )
                    if utility_chain_rejected:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI selected a function after a rejected utility chain"
                        )
                    if utility_called and function_name not in _CANVAS_TOOL_NAMES:
                        # Retain exact ownership so Ava can return one bounded
                        # rejected function output and terminate tool capability.
                        pass
                    call_id = _required_string(item, "call_id")
                    continue

                if event_type == "response.function_call_arguments.delta":
                    if self._interaction_response_turn_id == turn_id:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI canvas interaction response attempted a tool call"
                        )
                    delta = _required_string(event, "delta", allow_empty=True)
                    candidate_call_id = _required_string(event, "call_id")
                    candidate_response_id = _required_string(event, "response_id")
                    if call_id is not None and candidate_call_id != call_id:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI function-call ID changed while streaming"
                        )
                    if response_id is not None and candidate_response_id != response_id:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI response ID changed while streaming tool arguments"
                        )
                    call_id = candidate_call_id
                    response_id = candidate_response_id
                    self._active_response_id = candidate_response_id
                    argument_parts.append(delta)
                    if (
                        len("".join(argument_parts).encode("utf-8"))
                        > self._max_argument_bytes
                    ):
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI function arguments exceeded Ava's byte limit"
                        )
                    if self.last_first_delta_ms is None and delta:
                        self.last_first_delta_ms = (
                            monotonic() - trigger_started
                        ) * 1000
                    continue

                if event_type == "response.function_call_arguments.done":
                    if self._interaction_response_turn_id == turn_id:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI canvas interaction response attempted a tool call"
                        )
                    candidate_name = _required_string(event, "name")
                    candidate_call_id = _required_string(event, "call_id")
                    candidate_response_id = _required_string(event, "response_id")
                    candidate_arguments = _required_string(event, "arguments")
                    if candidate_name not in _SEMANTIC_TOOL_NAMES:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI completed an unavailable function"
                        )
                    if call_id is not None and candidate_call_id != call_id:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI function-call ID changed at completion"
                        )
                    if response_id is not None and candidate_response_id != response_id:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI response ID changed at tool completion"
                        )
                    streamed = "".join(argument_parts)
                    if streamed and streamed != candidate_arguments:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI final function arguments differ from streamed deltas"
                        )
                    call_id = candidate_call_id
                    response_id = candidate_response_id
                    function_name = candidate_name
                    final_arguments = candidate_arguments
                    continue

                if event_type == "response.done":
                    try:
                        response, completed_response_id = (
                            _validate_semantic_response_done(
                                event,
                                expected_response_id=response_id,
                            )
                        )
                    except RealtimeProviderResponseIncompleteError:
                        completed_response_id = _required_string(
                            _mapping(event.get("response"), "response.done response"),
                            "id",
                        )
                        self._mark_response_terminal_consumed(
                            event,
                            response_id=completed_response_id,
                            turn_id=turn_id,
                            consumer_phase=(
                                "canvasContinuation" if continuation else "turnResponse"
                            ),
                        )
                        self._active_response_id = None
                        if self._interaction_response_turn_id == turn_id:
                            self._interaction_response_turn_id = None
                        self._settled_turn_ids.add(turn_id)
                        self._active_turn_id = None
                        self._awaiting_response_trigger_turn_id = None
                        self._interaction_response_readiness_changed.set()
                        raise
                    usage = _parse_usage(event)
                    accumulated_usage = _merge_usage(accumulated_usage, usage)
                    self._active_response_id = None
                    self._interaction_response_readiness_changed.set()

                    function_items = [
                        _mapping(item, "response output item")
                        for item in _response_output(response)
                        if isinstance(item, dict)
                        and item.get("type") == "function_call"
                    ]
                    if len(function_items) > 1:
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI response contains multiple function calls"
                        )
                    if function_items:
                        if self._interaction_response_turn_id == turn_id:
                            raise OpenAIRealtimeProtocolError(
                                "OpenAI canvas interaction response attempted a tool call"
                            )
                        if canvas_called or tool_steps_used >= 2:
                            raise OpenAIRealtimeProtocolError(
                                "OpenAI continuation exceeded Ava's semantic tool chain"
                            )
                        item = function_items[0]
                        completed_name = _required_string(item, "name")
                        completed_call_id = _required_string(item, "call_id")
                        completed_arguments = _required_string(item, "arguments")
                        if completed_name not in _SEMANTIC_TOOL_NAMES:
                            raise OpenAIRealtimeProtocolError(
                                "OpenAI response completed an unavailable function"
                            )
                        if (
                            function_name is not None
                            and completed_name != function_name
                        ):
                            raise OpenAIRealtimeProtocolError(
                                "OpenAI function name changed at response completion"
                            )
                        if call_id is not None and completed_call_id != call_id:
                            raise OpenAIRealtimeProtocolError(
                                "OpenAI function-call ID changed at response completion"
                            )
                        streamed = "".join(argument_parts)
                        expected_arguments = final_arguments or streamed
                        if (
                            expected_arguments
                            and expected_arguments != completed_arguments
                        ):
                            raise OpenAIRealtimeProtocolError(
                                "OpenAI response arguments differ from streamed arguments"
                            )
                        _log_response_diagnostic(
                            "openai_realtime_function_response_consumed",
                            event=event,
                            response_id=completed_response_id,
                            response_owner_turn_id=self._response_owner_by_id.get(
                                completed_response_id
                            ),
                            consumer_turn_id=turn_id,
                            consumer_phase="turnResponse",
                            active_turn_id=self._active_turn_id,
                            active_response_id=self._active_response_id,
                            call_id=completed_call_id,
                        )
                        self._mark_response_terminal_consumed(
                            event,
                            response_id=completed_response_id,
                            turn_id=turn_id,
                            consumer_phase="turnResponse",
                            usage=usage,
                        )
                        if completed_name in _CANVAS_TOOL_NAMES:
                            self._pending_canvas_call_id = completed_call_id
                            canvas_called = True
                            tool_steps_used += 1
                            try:
                                if completed_name == INTERACTIVE_CANVAS_TOOL_NAME:
                                    canvas_request = (
                                        InteractiveCanvasRequest.model_validate_json(
                                            completed_arguments
                                        )
                                    )
                                else:
                                    InteractiveCanvasCancellationRequest.model_validate_json(
                                        completed_arguments
                                    )
                            except ValidationError as error:
                                LOGGER.warning(
                                    "openai_canvas_arguments_rejected call_id=%s "
                                    "validation_issue_count=%d",
                                    completed_call_id,
                                    error.error_count(),
                                    extra={
                                        "ava_context": {
                                            "callId": completed_call_id,
                                            "issueCount": error.error_count(),
                                            "issues": _content_free_validation_issues(
                                                error
                                            ),
                                        }
                                    },
                                )
                                yield RealtimeRejectedCanvasToolCall(
                                    call_id=completed_call_id,
                                    reason=(
                                        "The canvas request exceeded Ava's bounded "
                                        "tool contract."
                                    ),
                                )
                            else:
                                if completed_name == INTERACTIVE_CANVAS_TOOL_NAME:
                                    yield RealtimeCanvasToolCall(
                                        call_id=completed_call_id,
                                        request=canvas_request,
                                    )
                                else:
                                    yield RealtimeCancelCanvasToolCall(
                                        call_id=completed_call_id
                                    )
                            if completed_call_id not in self._completed_canvas_call_ids:
                                raise OpenAIRealtimeProtocolError(
                                    "Ava did not return the canvas function output"
                                )
                            self._completed_canvas_call_ids.discard(completed_call_id)
                        else:
                            utility_kind = RealtimeUtilityToolKind(completed_name)
                            repeated_or_cross_utility = utility_called
                            utility_rejected = repeated_or_cross_utility
                            utility_request: SearchMemoryRequest | GetCurrentTimeRequest
                            if not utility_rejected:
                                try:
                                    if completed_name == SEARCH_MEMORY_TOOL_NAME:
                                        utility_request = (
                                            SearchMemoryRequest.model_validate_json(
                                                completed_arguments
                                            )
                                        )
                                    else:
                                        utility_request = (
                                            GetCurrentTimeRequest.model_validate_json(
                                                completed_arguments
                                            )
                                        )
                                except ValidationError as error:
                                    utility_rejected = True
                                    LOGGER.warning(
                                        "openai_utility_arguments_rejected "
                                        "tool_kind=%s call_id=%s "
                                        "validation_issue_count=%d",
                                        utility_kind,
                                        completed_call_id,
                                        error.error_count(),
                                        extra={
                                            "ava_context": {
                                                "toolKind": str(utility_kind),
                                                "callId": completed_call_id,
                                                "issueCount": error.error_count(),
                                                "issues": (
                                                    _content_free_validation_issues(
                                                        error
                                                    )
                                                ),
                                            }
                                        },
                                    )
                            self._pending_utility_call_id = completed_call_id
                            utility_called = True
                            tool_steps_used += 1
                            if utility_rejected:
                                yield RealtimeRejectedUtilityToolCall(
                                    call_id=completed_call_id,
                                    tool_kind=utility_kind,
                                    reason=(
                                        "The utility request is unavailable in this "
                                        "tool position or exceeded its bounded contract."
                                    ),
                                )
                                utility_chain_rejected = True
                            elif completed_name == SEARCH_MEMORY_TOOL_NAME:
                                if not isinstance(utility_request, SearchMemoryRequest):
                                    raise AssertionError(
                                        "search memory request parsing changed"
                                    )
                                yield RealtimeSearchMemoryToolCall(
                                    call_id=completed_call_id,
                                    request=utility_request,
                                )
                            else:
                                if not isinstance(
                                    utility_request, GetCurrentTimeRequest
                                ):
                                    raise AssertionError(
                                        "current time request parsing changed"
                                    )
                                yield RealtimeGetCurrentTimeToolCall(
                                    call_id=completed_call_id,
                                    request=utility_request,
                                )
                            if (
                                completed_call_id
                                not in self._completed_utility_call_ids
                            ):
                                raise OpenAIRealtimeProtocolError(
                                    "Ava did not return the utility function output"
                                )
                            self._completed_utility_call_ids.discard(completed_call_id)
                        continuation = True
                        break

                    if (
                        function_item_count
                        or function_name is not None
                        or call_id is not None
                        or final_arguments is not None
                        or argument_parts
                    ):
                        raise OpenAIRealtimeProtocolError(
                            "OpenAI streamed a function call that was absent from "
                            "the completed response"
                        )

                    self._mark_response_terminal_consumed(
                        event,
                        response_id=completed_response_id,
                        turn_id=turn_id,
                        consumer_phase=(
                            "canvasContinuation" if continuation else "turnResponse"
                        ),
                        usage=usage,
                    )
                    self.last_response_id = completed_response_id
                    self.last_usage = accumulated_usage
                    self.last_completed_ms = (monotonic() - trigger_started) * 1000
                    if self._interaction_response_turn_id == turn_id:
                        self._interaction_response_turn_id = None
                    return

                if event_type in _RESPONSE_TRIGGER_EVENT_TYPES:
                    response_expected = True
                    trigger_started = monotonic()
                    continue

                if event_type not in _IN_TURN_IGNORED_EVENT_TYPES:
                    raise OpenAIRealtimeProtocolError(
                        f"unexpected OpenAI event during Ava turn: {event_type}"
                    )

    async def _send_response_cancel(
        self,
        *,
        turn_id: str,
        response_id: str | None,
    ) -> _OutstandingCancelOutcome:
        if len(self._outstanding_cancel_outcomes) >= _MAX_OUTSTANDING_CANCEL_OUTCOMES:
            raise OpenAIRealtimeProtocolError(
                "OpenAI cancellation outcome provenance exhausted its bounded capacity"
            )
        cancel_event_id = f"ava_response_cancel_{uuid4().hex}"
        cancel_event: dict[str, object] = {
            "type": "response.cancel",
            "event_id": cancel_event_id,
        }
        if response_id is not None:
            cancel_event["response_id"] = response_id
        await self._send_json(cancel_event)
        outcome = _OutstandingCancelOutcome(
            cancel_event_id=cancel_event_id,
            cancel_sequence=self._next_cancel_sequence,
            cancelled_turn_id=turn_id,
            response_id=response_id,
        )
        self._next_cancel_sequence += 1
        self._outstanding_cancel_outcomes.append(outcome)
        return outcome

    def _bind_outstanding_cancel_outcome_response(
        self,
        *,
        turn_id: str,
        response_id: str,
    ) -> None:
        for index in range(len(self._outstanding_cancel_outcomes) - 1, -1, -1):
            outcome = self._outstanding_cancel_outcomes[index]
            if outcome.cancelled_turn_id != turn_id:
                continue
            if outcome.response_id == response_id:
                return
            if outcome.response_id is not None:
                raise OpenAIRealtimeProtocolError(
                    "OpenAI cancellation response ID changed before terminal status"
                )
            self._outstanding_cancel_outcomes[index] = _OutstandingCancelOutcome(
                cancel_event_id=outcome.cancel_event_id,
                cancel_sequence=outcome.cancel_sequence,
                cancelled_turn_id=turn_id,
                response_id=response_id,
            )
            return

    def _consume_correlated_cancel_outcome(
        self,
        event: Mapping[str, object],
    ) -> _OutstandingCancelOutcome | None:
        error = _mapping(event.get("error"), "provider error")
        if "event_id" not in error:
            if len(self._outstanding_cancel_outcomes) != 1:
                return None
            return self._outstanding_cancel_outcomes.pop()
        cancel_event_id = error.get("event_id")
        if not isinstance(cancel_event_id, str) or not cancel_event_id:
            return None
        for index, outcome in enumerate(self._outstanding_cancel_outcomes):
            if outcome.cancel_event_id != cancel_event_id:
                continue
            return self._outstanding_cancel_outcomes.pop(index)
        return None

    def _resolve_outstanding_cancel_outcome(
        self,
        *,
        turn_id: str,
        response_id: str,
        terminal_status: str,
    ) -> None:
        for index, outcome in enumerate(self._outstanding_cancel_outcomes):
            if outcome.cancelled_turn_id != turn_id:
                continue
            if outcome.response_id not in {None, response_id}:
                continue
            self._outstanding_cancel_outcomes.pop(index)
            self._recently_resolved_cancel_outcomes[outcome.cancel_event_id] = (
                _ResolvedCancelOutcome(
                    cancel_event_id=outcome.cancel_event_id,
                    cancelled_turn_id=outcome.cancelled_turn_id,
                    response_id=response_id,
                )
            )
            if (
                len(self._recently_resolved_cancel_outcomes)
                > _MAX_RECENTLY_RESOLVED_CANCEL_OUTCOMES
            ):
                self._recently_resolved_cancel_outcomes.popitem(last=False)
            LOGGER.info(
                "openai_realtime_cancel_outcome_resolved cancel_event_id=%s "
                "cancel_sequence=%d "
                "cancelled_turn_id=%s cancelled_response_id=%s "
                "terminal_status=%s",
                outcome.cancel_event_id,
                outcome.cancel_sequence,
                outcome.cancelled_turn_id,
                response_id,
                terminal_status,
            )
            return

    def _consume_recently_resolved_cancel_outcome(
        self,
        event: Mapping[str, object],
    ) -> _ResolvedCancelOutcome | None:
        error = _mapping(event.get("error"), "provider error")
        cancel_event_id = error.get("event_id")
        if not isinstance(cancel_event_id, str) or not cancel_event_id:
            return None
        return self._recently_resolved_cancel_outcomes.pop(cancel_event_id, None)

    async def _send_json(self, payload: Mapping[str, object]) -> None:
        if self._closed:
            raise OpenAIRealtimeProtocolError(
                "cannot send to a closed OpenAI Realtime channel"
            )
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(serialized.encode("utf-8")) > self._max_event_bytes:
            raise OpenAIRealtimeProtocolError(
                "Ava's OpenAI client event exceeded the provider event limit"
            )
        try:
            await self._socket.send(serialized)
        except ConnectionClosed as error:
            raise _as_realtime_receive_error(error) from error
        except Exception as error:
            raise OpenAIRealtimeUnavailableError(
                "OpenAI Realtime send failed"
            ) from error

    async def _receive_event(
        self,
        *,
        timeout_seconds: float | None,
    ) -> dict[str, object]:
        await self.start_receiver()
        while True:
            self._received_event_count += 1
            if self._received_event_count > self._max_events_per_turn:
                raise OpenAIRealtimeProtocolError(
                    "OpenAI Realtime exceeded Ava's event-count limit"
                )
            if self._pending_events:
                event = self._pending_events.popleft()
            else:
                if self._receiver_error is not None and self._router_events.empty():
                    raise self._receiver_error
                try:
                    if timeout_seconds is None:
                        value = await self._router_events.get()
                    else:
                        async with asyncio.timeout(timeout_seconds):
                            value = await self._router_events.get()
                except TimeoutError as error:
                    raise _OpenAIRealtimeReceiveTimeout(
                        "OpenAI Realtime active-response event receive timed out"
                    ) from error
                if value is self._receiver_terminal:
                    if self._receiver_error is not None:
                        raise self._receiver_error
                    raise OpenAIRealtimeUnavailableError(
                        "OpenAI Realtime sideband receiver stopped unexpectedly"
                    )
                if not isinstance(value, dict):
                    raise OpenAIRealtimeProtocolError(
                        "OpenAI Realtime router produced an invalid provider event"
                    )
                event = value
            if self._is_duplicate_consumed_terminal_response(event):
                continue
            return event

    def _is_duplicate_consumed_terminal_response(
        self,
        event: Mapping[str, object],
    ) -> bool:
        if _event_type(event) != "response.done":
            return False
        response_id = _response_id_from_event(event)
        if response_id is None:
            return False
        owner = self._consumed_terminal_response_owner_by_id.get(response_id)
        if owner is None:
            return False
        sequence = _sideband_sequence(event)
        LOGGER.info(
            "openai_realtime_duplicate_terminal_ignored response_id=%s "
            "response_owner_turn_id=%s active_turn_id=%s sideband_sequence=%s",
            response_id,
            owner,
            self._active_turn_id,
            sequence,
            extra={
                "ava_context": {
                    "component": "realtimeProvider",
                    "responseId": response_id,
                    "responseOwnerTurnId": owner,
                    "activeTurnId": self._active_turn_id,
                    "sidebandSequence": sequence,
                }
            },
        )
        return True


class OpenAIRealtimeProviderSession:
    """Production WebRTC negotiation and server-side sideband implementation."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        transcription_model: str = "gpt-transcribe",
        safety_identifier: str,
        connect_timeout_seconds: float,
        turn_timeout_seconds: float,
        max_output_tokens: int,
        noise_reduction: str = "near_field",
        vad_threshold: float = 0.45,
        vad_prefix_padding_ms: int = 500,
        vad_silence_duration_ms: int = 450,
        truncation_post_instructions_tokens: int = (
            DEFAULT_OPENAI_REALTIME_TRUNCATION_POST_INSTRUCTIONS_TOKENS
        ),
        truncation_retention_ratio: float = (
            DEFAULT_OPENAI_REALTIME_TRUNCATION_RETENTION_RATIO
        ),
        http_client: httpx.AsyncClient | None = None,
        websocket_connector: RealtimeWebSocketConnector = (
            _default_websocket_connector
        ),
        api_origin: str = DEFAULT_OPENAI_API_ORIGIN,
        realtime_websocket_url: str = DEFAULT_OPENAI_REALTIME_WEBSOCKET,
    ) -> None:
        self._api_key = _required_secret(api_key, "OpenAI API key")
        self._model = _required_name(model, "OpenAI Realtime model")
        self._transcription_model = _required_name(
            transcription_model,
            "OpenAI Realtime transcription model",
        )
        self._safety_identifier = _required_safety_identifier(safety_identifier)
        if not 3 <= connect_timeout_seconds <= 60:
            raise ValueError("connect_timeout_seconds must be between 3 and 60")
        if not 5 <= turn_timeout_seconds <= 180:
            raise ValueError("turn_timeout_seconds must be between 5 and 180")
        if not 256 <= max_output_tokens <= 4096:
            raise ValueError("max_output_tokens must be between 256 and 4096")
        self._connect_timeout_seconds = connect_timeout_seconds
        self._turn_timeout_seconds = turn_timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._noise_reduction = _validated_noise_reduction(noise_reduction)
        self._vad_threshold = _validated_vad_threshold(vad_threshold)
        self._vad_prefix_padding_ms = _validated_vad_prefix_padding_ms(
            vad_prefix_padding_ms
        )
        self._vad_silence_duration_ms = _validated_vad_silence_duration_ms(
            vad_silence_duration_ms
        )
        self._truncation_post_instructions_tokens = (
            _validated_truncation_post_instructions_tokens(
                truncation_post_instructions_tokens
            )
        )
        self._truncation_retention_ratio = _validated_truncation_retention_ratio(
            truncation_retention_ratio
        )
        self._api_origin = _validated_origin(api_origin, scheme="https")
        self._realtime_websocket_url = _validated_websocket_url(realtime_websocket_url)
        self._http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect_timeout_seconds),
            follow_redirects=False,
        )
        self._owns_http_client = http_client is None
        self._websocket_connector = websocket_connector
        self._call_id: str | None = None
        self._channel: _OpenAIRealtimeEventChannel | None = None
        self._closed = False

    @property
    def last_usage(self) -> OpenAIRealtimeUsage | None:
        return self._channel.last_usage if self._channel is not None else None

    @property
    def last_response_id(self) -> str | None:
        return self._channel.last_response_id if self._channel is not None else None

    async def negotiate(self, *, offer_sdp: str) -> NegotiatedRealtimeCall:
        if self._closed:
            raise RealtimeNegotiationResponseError(
                "cannot negotiate a closed OpenAI Realtime provider session"
            )
        if self._call_id is not None:
            raise RealtimeNegotiationResponseError(
                "OpenAI Realtime provider session may negotiate only once"
            )
        validate_sdp(offer_sdp, description="offer")
        session = {
            "type": "realtime",
            "model": self._model,
            "output_modalities": ["text"],
            "max_output_tokens": self._max_output_tokens,
        }
        headers = self._headers(accept="application/sdp")
        files = {
            "sdp": (None, offer_sdp, "application/sdp"),
            "session": (
                None,
                json.dumps(session, separators=(",", ":")),
                "application/json",
            ),
        }
        sdp_started = monotonic()
        provider_status_code: int | None = None
        provider_error_code: str | None = None
        provider_error_parameter: str | None = None
        provider_request_id: str | None = None
        try:
            async with self._http_client.stream(
                "POST",
                f"{self._api_origin}/v1/realtime/calls",
                headers=headers,
                files=files,
                timeout=self._connect_timeout_seconds,
            ) as response:
                provider_status_code = response.status_code
                provider_request_id = response.headers.get("x-request-id")
                if response.status_code != 201:
                    error_body = await _read_bounded_error_response(response)
                    provider_error_code, provider_error_parameter = (
                        _provider_http_error_details(error_body)
                    )
                    raise RealtimeNegotiationResponseError(
                        "OpenAI Realtime negotiation failed with HTTP status "
                        f"{response.status_code}",
                        provider_status_code=response.status_code,
                        provider_error_code=provider_error_code,
                        provider_error_parameter=provider_error_parameter,
                        provider_request_id=provider_request_id,
                    )
                location = response.headers.get("location")
                if location is None:
                    raise RealtimeNegotiationResponseError(
                        "OpenAI Realtime negotiation omitted the Location header"
                    )
                content_type = response.headers.get("content-type", "")
                media_type = content_type.split(";", maxsplit=1)[0].strip().lower()
                if media_type not in {"application/sdp", "text/plain"}:
                    raise RealtimeNegotiationResponseError(
                        "OpenAI Realtime negotiation returned a non-SDP response"
                    )
                answer_sdp = (
                    await _read_bounded_response(response, MAX_SDP_BYTES)
                ).decode("utf-8")
                call_id = _call_id_from_location(location)
        except RealtimeNegotiationResponseError as error:
            _log_sdp_negotiation_failure(
                provider_status_code=(
                    error.provider_status_code or provider_status_code
                ),
                provider_error_code=error.provider_error_code or provider_error_code,
                provider_error_parameter=(
                    error.provider_error_parameter or provider_error_parameter
                ),
                provider_request_id=error.provider_request_id or provider_request_id,
            )
            raise
        except (UnicodeDecodeError, httpx.HTTPError, OSError) as error:
            raise RealtimeNegotiationUnavailableError(
                "OpenAI Realtime negotiation could not reach the provider",
                stage="sdp",
                error_type=type(error).__name__,
            ) from error
        LOGGER.info(
            "openai_realtime_stage stage=sdp call_id=%s elapsed_ms=%.2f",
            call_id,
            (monotonic() - sdp_started) * 1000,
        )

        sideband_started = monotonic()
        try:
            socket = await self._websocket_connector(
                f"{self._realtime_websocket_url}?call_id={quote(call_id)}",
                self._sideband_headers(),
                self._connect_timeout_seconds,
                MAX_PROVIDER_EVENT_BYTES,
            )
            channel = _OpenAIRealtimeEventChannel(
                socket,
                model=self._model,
                max_output_tokens=self._max_output_tokens,
                turn_timeout_seconds=self._turn_timeout_seconds,
                transcription_model=self._transcription_model,
                noise_reduction=self._noise_reduction,
                vad_threshold=self._vad_threshold,
                vad_prefix_padding_ms=self._vad_prefix_padding_ms,
                vad_silence_duration_ms=self._vad_silence_duration_ms,
                truncation_post_instructions_tokens=(
                    self._truncation_post_instructions_tokens
                ),
                truncation_retention_ratio=self._truncation_retention_ratio,
                provider_call_id=call_id,
            )
            await channel.start_receiver()
        except Exception as error:
            status_code, request_id = _websocket_handshake_error_metadata(error)
            LOGGER.warning(
                "openai_realtime_sideband_attachment_failed call_id=%s "
                "error_type=%s provider_status_code=%s provider_request_id=%s",
                call_id,
                type(error).__name__,
                status_code,
                request_id,
            )
            await self._hangup_after_failed_attachment(call_id)
            if isinstance(error, OpenAIRealtimeProviderError):
                raise RealtimeNegotiationResponseError(
                    "OpenAI Realtime sideband returned an invalid session"
                ) from error
            raise RealtimeNegotiationUnavailableError(
                "OpenAI Realtime sideband could not be attached",
                stage="sideband",
                error_type=type(error).__name__,
                provider_status_code=status_code,
                provider_request_id=request_id,
            ) from error
        LOGGER.info(
            "openai_realtime_stage stage=sideband call_id=%s elapsed_ms=%.2f",
            call_id,
            (monotonic() - sideband_started) * 1000,
        )

        try:
            negotiated = NegotiatedRealtimeCall(
                answer_sdp=answer_sdp,
                provider_call_id=call_id,
            )
        except ValueError:
            _log_sdp_negotiation_failure(
                provider_status_code=provider_status_code,
                provider_error_code=provider_error_code,
                provider_error_parameter=provider_error_parameter,
                provider_request_id=provider_request_id,
            )
            raise
        self._call_id = call_id
        self._channel = channel
        return negotiated

    def turn_events(
        self,
        *,
        harness: RealtimeHarness,
        turn_id: str,
    ) -> AsyncIterator[RealtimeProviderTurnEvent]:
        return self._require_channel().stream_turn(
            harness=harness,
            turn_id=turn_id,
        )

    async def next_input_event(self) -> RealtimeProviderInputEvent:
        return await self._require_channel().next_input_event()

    def input_item_owner(self, *, item_id: str) -> str | None:
        return self._require_channel().input_item_owner(item_id=item_id)

    async def admit_pending_input_items(self, *, turn_id: str) -> tuple[str, ...]:
        return await self._require_channel().admit_pending_input_items(turn_id=turn_id)

    @property
    def active_context_build_id(self) -> str | None:
        return self._require_channel().active_context_build_id

    async def prepare(
        self,
        *,
        harness: RealtimeHarness,
    ) -> RealtimeContextInstallationStatus:
        return await self._require_channel().prepare(harness=harness)

    async def prepare_if_idle(
        self,
        *,
        harness: RealtimeHarness,
        turn_id: str,
    ) -> RealtimeContextInstallationStatus | None:
        return await self._require_channel().prepare_if_idle(
            harness=harness,
            turn_id=turn_id,
        )

    async def complete_canvas_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: (
            InteractiveCanvasToolOutput
            | InteractiveCanvasToolOutputV4
            | InteractiveCanvasCancellationToolOutputV4
        ),
    ) -> None:
        await self._require_channel().complete_canvas_tool(
            turn_id=turn_id,
            call_id=call_id,
            output=output,
        )

    async def complete_utility_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: RealtimeUtilityToolOutputV4,
        harness: RealtimeHarness,
    ) -> None:
        await self._require_channel().complete_utility_tool(
            turn_id=turn_id,
            call_id=call_id,
            output=output,
            harness=harness,
        )

    async def request_canvas_interaction_response(
        self,
        *,
        turn_id: str,
        interaction: RecordedSvgCanvasInteraction,
    ) -> bool:
        return await self._require_channel().request_canvas_interaction_response(
            turn_id=turn_id,
            interaction=interaction,
        )

    async def wait_until_canvas_interaction_response_ready(
        self,
        *,
        turn_id: str,
    ) -> bool:
        return (
            await self._require_channel().wait_until_canvas_interaction_response_ready(
                turn_id=turn_id
            )
        )

    async def child_transcript(self, *, turn_id: str) -> str | None:
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        channel = self._channel
        if channel is not None:
            return await channel.child_transcript_for_turn(turn_id)
        return None

    def available_child_transcript(self, *, turn_id: str) -> str | None:
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        channel = self._channel
        if channel is not None:
            return channel.available_child_transcript_for_turn(turn_id)
        return None

    def wait_for_child_transcript(
        self,
        *,
        turn_id: str,
    ) -> Awaitable[str | None]:
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        return self._require_channel().wait_for_child_transcript_for_turn(turn_id)

    async def cancel(self, *, turn_id: str) -> None:
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        if self._closed:
            return
        channel = self._channel
        if channel is None:
            # A bootstrap can expire before Flutter completes SDP negotiation.
            # There is no provider response to cancel in that lifecycle state.
            return
        await channel.cancel(turn_id=turn_id)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[Exception] = []
        if self._call_id is not None:
            try:
                await self._hangup(self._call_id)
            except Exception as error:
                errors.append(error)
        if self._channel is not None:
            try:
                await self._channel.close()
            except Exception as error:
                errors.append(error)
        if self._owns_http_client:
            try:
                await self._http_client.aclose()
            except Exception as error:
                errors.append(error)
        if errors:
            raise OpenAIRealtimeUnavailableError(
                "OpenAI Realtime session cleanup did not complete"
            ) from errors[0]

    def _require_channel(self) -> _OpenAIRealtimeEventChannel:
        if self._closed:
            raise OpenAIRealtimeProtocolError(
                "OpenAI Realtime provider session is closed"
            )
        if self._channel is None:
            raise OpenAIRealtimeProtocolError(
                "OpenAI Realtime media must be negotiated before the turn starts"
            )
        return self._channel

    def _headers(self, *, accept: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Safety-Identifier": self._safety_identifier,
        }
        if accept is not None:
            headers["Accept"] = accept
        return headers

    def _sideband_headers(self) -> dict[str, str]:
        # The safety identifier is bound when /v1/realtime/calls creates the
        # session. OpenAI's sideband contract authenticates the attachment
        # socket with the standard bearer key only.
        return {"Authorization": f"Bearer {self._api_key}"}

    async def _hangup_after_failed_attachment(self, call_id: str) -> None:
        try:
            await self._hangup(call_id)
        except Exception:
            LOGGER.exception(
                "openai_realtime_attachment_cleanup_failed call_id=%s",
                call_id,
            )

    async def _hangup(self, call_id: str) -> None:
        try:
            response = await self._http_client.post(
                f"{self._api_origin}/v1/realtime/calls/{quote(call_id)}/hangup",
                headers=self._headers(accept="*/*"),
                timeout=self._connect_timeout_seconds,
            )
        except (httpx.HTTPError, OSError) as error:
            raise OpenAIRealtimeUnavailableError(
                "OpenAI Realtime hangup request failed"
            ) from error
        if response.status_code not in {200, 404}:
            raise OpenAIRealtimeProtocolError(
                f"OpenAI Realtime hangup failed with HTTP status {response.status_code}"
            )


class OpenAIRealtimeLiveProbe:
    """One explicitly authorized, server-to-server text probe of the live model."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        safety_identifier: str,
        connect_timeout_seconds: float,
        turn_timeout_seconds: float,
        max_output_tokens: int,
        truncation_post_instructions_tokens: int = (
            DEFAULT_OPENAI_REALTIME_TRUNCATION_POST_INSTRUCTIONS_TOKENS
        ),
        truncation_retention_ratio: float = (
            DEFAULT_OPENAI_REALTIME_TRUNCATION_RETENTION_RATIO
        ),
        websocket_connector: RealtimeWebSocketConnector = (
            _default_websocket_connector
        ),
        realtime_websocket_url: str = DEFAULT_OPENAI_REALTIME_WEBSOCKET,
    ) -> None:
        self._api_key = _required_secret(api_key, "OpenAI API key")
        self._model = _required_name(model, "OpenAI Realtime model")
        self._safety_identifier = _required_safety_identifier(safety_identifier)
        if not 3 <= connect_timeout_seconds <= 60:
            raise ValueError("connect_timeout_seconds must be between 3 and 60")
        if not 5 <= turn_timeout_seconds <= 180:
            raise ValueError("turn_timeout_seconds must be between 5 and 180")
        if not 256 <= max_output_tokens <= 4096:
            raise ValueError("max_output_tokens must be between 256 and 4096")
        self._connect_timeout_seconds = connect_timeout_seconds
        self._turn_timeout_seconds = turn_timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._truncation_post_instructions_tokens = (
            _validated_truncation_post_instructions_tokens(
                truncation_post_instructions_tokens
            )
        )
        self._truncation_retention_ratio = _validated_truncation_retention_ratio(
            truncation_retention_ratio
        )
        self._websocket_connector = websocket_connector
        self._realtime_websocket_url = _validated_websocket_url(realtime_websocket_url)

    async def run(
        self,
        *,
        harness: RealtimeHarness,
        turn_id: str,
        text_input: str,
        response_max_output_tokens: int,
    ) -> OpenAIRealtimeProbeResult:
        outcome = (
            await self.run_many(
                harness=harness,
                requests=(
                    OpenAIRealtimeProbeRequest(
                        turn_id=turn_id,
                        text_input=text_input,
                        max_output_tokens=response_max_output_tokens,
                    ),
                ),
            )
        )[0]
        if isinstance(outcome, OpenAIRealtimeProbeFailureResult):
            raise OpenAIRealtimeOutputValidationError(
                usage=outcome.usage,
                response_id=outcome.response_id,
                validation_issue_count=len(outcome.validation_issues),
                validation_issues=outcome.validation_issues,
            )
        return outcome

    async def run_many(
        self,
        *,
        harness: RealtimeHarness,
        requests: tuple[OpenAIRealtimeProbeRequest, ...],
    ) -> tuple[OpenAIRealtimeProbeOutcome, ...]:
        """Run a bounded semantic matrix in one persistent Realtime session."""

        if not 1 <= len(requests) <= 8:
            raise ValueError("live semantic matrix must contain 1-8 requests")
        if len({request.turn_id for request in requests}) != len(requests):
            raise ValueError("live semantic matrix turn IDs must be unique")
        url = f"{self._realtime_websocket_url}?model={quote(self._model)}"
        socket = await self._websocket_connector(
            url,
            {
                "Authorization": f"Bearer {self._api_key}",
                "OpenAI-Safety-Identifier": self._safety_identifier,
            },
            self._connect_timeout_seconds,
            MAX_PROVIDER_EVENT_BYTES,
        )
        channel = _OpenAIRealtimeEventChannel(
            socket,
            model=self._model,
            max_output_tokens=self._max_output_tokens,
            turn_timeout_seconds=self._turn_timeout_seconds,
            truncation_post_instructions_tokens=(
                self._truncation_post_instructions_tokens
            ),
            truncation_retention_ratio=self._truncation_retention_ratio,
        )
        try:
            await channel.expect_session_created()
            outcomes: list[OpenAIRealtimeProbeOutcome] = []
            for request in requests:
                try:
                    outcome: OpenAIRealtimeProbeOutcome = await self._run_request(
                        channel=channel,
                        harness=harness,
                        request=request,
                    )
                except OpenAIRealtimeOutputValidationError as error:
                    outcome = OpenAIRealtimeProbeFailureResult(
                        turn_id=request.turn_id,
                        response_id=error.response_id,
                        usage=error.usage,
                        validation_issues=error.validation_issues,
                    )
                outcomes.append(outcome)
            return tuple(outcomes)
        finally:
            await channel.close()

    async def _run_request(
        self,
        *,
        channel: _OpenAIRealtimeEventChannel,
        harness: RealtimeHarness,
        request: OpenAIRealtimeProbeRequest,
    ) -> OpenAIRealtimeProbeResult:
        parser = StreamingSpeechParser()
        canvas_request: InteractiveCanvasRequest | None = None
        canvas_selected = False
        canvas_rejection: str | None = None
        async for event in channel.stream_turn(
            harness=harness,
            turn_id=request.turn_id,
            text_input=request.text_input,
            response_max_output_tokens=request.max_output_tokens,
        ):
            if isinstance(event, RealtimeSpeechTextDelta):
                parser.feed(event.delta)
                continue
            if isinstance(event, RealtimeCanvasToolCall):
                parser.finish_response_chunk()
                if canvas_selected:
                    raise OpenAIRealtimeProtocolError(
                        "OpenAI live probe requested multiple canvases"
                    )
                canvas_selected = True
                canvas_request = event.request
                await channel.complete_canvas_tool(
                    turn_id=request.turn_id,
                    call_id=event.call_id,
                    output=InteractiveCanvasToolOutput.model_validate(
                        {
                            "status": "rejected",
                            "reason": (
                                "Live semantic-selection probe does not render a scene."
                            ),
                        }
                    ),
                )
                continue
            if isinstance(event, RealtimeCancelCanvasToolCall):
                parser.finish_response_chunk()
                if canvas_selected:
                    raise OpenAIRealtimeProtocolError(
                        "OpenAI live probe requested multiple canvases"
                    )
                canvas_selected = True
                await channel.complete_canvas_tool(
                    turn_id=request.turn_id,
                    call_id=event.call_id,
                    output=InteractiveCanvasCancellationToolOutputV4.model_validate(
                        {"status": "accepted"}
                    ),
                )
                continue
            if isinstance(event, RealtimeRejectedCanvasToolCall):
                parser.finish_response_chunk()
                if canvas_selected:
                    raise OpenAIRealtimeProtocolError(
                        "OpenAI live probe requested multiple canvases"
                    )
                canvas_selected = True
                canvas_rejection = event.reason
                await channel.complete_canvas_tool(
                    turn_id=request.turn_id,
                    call_id=event.call_id,
                    output=InteractiveCanvasToolOutput.model_validate(
                        {
                            "status": "rejected",
                            "reason": event.reason,
                        }
                    ),
                )
                continue
            raise OpenAIRealtimeProtocolError(
                "OpenAI live probe received an unsupported event"
            )
        try:
            parser.finish()
        except StreamingTurnParseError as error:
            if channel.last_usage is None or channel.last_response_id is None:
                raise OpenAIRealtimeProtocolError(
                    "OpenAI live probe failed speech validation without "
                    "billed response metadata"
                ) from error
            raise OpenAIRealtimeOutputValidationError(
                usage=channel.last_usage,
                response_id=channel.last_response_id,
                validation_issue_count=1,
                validation_issues=(
                    OpenAIRealtimeValidationIssue(path=(), code="streaming_contract"),
                ),
            ) from error
        if (
            channel.last_usage is None
            or channel.last_response_id is None
            or channel.last_first_delta_ms is None
            or channel.last_completed_ms is None
        ):
            raise OpenAIRealtimeProtocolError(
                "OpenAI live probe completed without usage or timing metadata"
            )
        return OpenAIRealtimeProbeResult(
            speech=" ".join(segment.speech for segment in parser.emitted_segments),
            canvas_request=canvas_request,
            canvas_selected=canvas_selected,
            canvas_rejection=canvas_rejection,
            model=self._model,
            prompt_version=harness.prompt_version,
            response_id=channel.last_response_id,
            first_delta_ms=channel.last_first_delta_ms,
            completed_ms=channel.last_completed_ms,
            usage=channel.last_usage,
        )


def _canvas_interaction_context_text(
    interaction: RecordedSvgCanvasInteraction,
) -> str:
    if interaction.interaction_mode not in {"explore", "select", "steps"}:
        raise ValueError("canvas interaction response has an unsupported mode")
    if (interaction.interaction_mode == "steps") != (
        interaction.step_index is not None
    ):
        raise ValueError("canvas interaction response has invalid step identity")

    escaped_attribute = {'"': "&quot;"}
    step_attribute = (
        f' step_index="{interaction.step_index}"'
        if interaction.step_index is not None
        else ""
    )
    return (
        '<canvas_interaction trust="user_action_context" '
        f'mode="{escape(interaction.interaction_mode, escaped_attribute)}"'
        f"{step_attribute}>"
        "<semantic_focus>"
        f"{escape(interaction.accessibility_label)}"
        "</semantic_focus>"
        "</canvas_interaction>"
    )


def _content_free_validation_issues(
    error: ValidationError,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for issue in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = issue.get("loc", ())
        path = ".".join(str(part) for part in location)
        code = issue.get("type")
        issues.append(
            {
                "path": path or "$",
                "code": str(code or "validation_error"),
            }
        )
    return issues


def _session_update(
    harness: RealtimeHarness,
    model: str,
    max_output_tokens: int,
    *,
    transcription_model: str = "gpt-transcribe",
    noise_reduction: str = "near_field",
    vad_threshold: float = 0.45,
    vad_prefix_padding_ms: int = 500,
    vad_silence_duration_ms: int = 450,
    truncation_post_instructions_tokens: int = (
        DEFAULT_OPENAI_REALTIME_TRUNCATION_POST_INSTRUCTIONS_TOKENS
    ),
    truncation_retention_ratio: float = (
        DEFAULT_OPENAI_REALTIME_TRUNCATION_RETENTION_RATIO
    ),
) -> dict[str, object]:
    provider_neutral = harness.session_update()
    raw_session = provider_neutral.get("session")
    if not isinstance(raw_session, dict):
        raise OpenAIRealtimeProtocolError(
            "Realtime harness produced an invalid session update"
        )
    session = dict(raw_session)
    session.update(
        {
            "type": "realtime",
            "model": model,
            "output_modalities": ["text"],
            "max_output_tokens": max_output_tokens,
            "parallel_tool_calls": False,
            "reasoning": {"effort": harness.reasoning_effort},
            "truncation": {
                "type": "retention_ratio",
                "retention_ratio": _validated_truncation_retention_ratio(
                    truncation_retention_ratio
                ),
                "token_limits": {
                    "post_instructions": (
                        _validated_truncation_post_instructions_tokens(
                            truncation_post_instructions_tokens
                        )
                    )
                },
            },
            "audio": {
                "input": {
                    "transcription": {
                        "model": _required_name(
                            transcription_model,
                            "OpenAI Realtime transcription model",
                        )
                    },
                    "noise_reduction": {
                        "type": _validated_noise_reduction(noise_reduction)
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": _validated_vad_threshold(vad_threshold),
                        "prefix_padding_ms": _validated_vad_prefix_padding_ms(
                            vad_prefix_padding_ms
                        ),
                        "silence_duration_ms": _validated_vad_silence_duration_ms(
                            vad_silence_duration_ms
                        ),
                        "create_response": True,
                        "interrupt_response": True,
                    },
                }
            },
        }
    )
    return {"type": "session.update", "session": session}


def _validated_noise_reduction(value: str) -> str:
    if value not in {"near_field", "far_field"}:
        raise ValueError("noise_reduction must be near_field or far_field")
    return value


def _validated_vad_threshold(value: float) -> float:
    if isinstance(value, bool) or not 0 <= value <= 1:
        raise ValueError("vad_threshold must be between 0 and 1")
    return float(value)


def _validated_vad_prefix_padding_ms(value: int) -> int:
    if isinstance(value, bool) or not 0 <= value <= 5_000:
        raise ValueError("vad_prefix_padding_ms must be between 0 and 5000")
    return value


def _validated_vad_silence_duration_ms(value: int) -> int:
    if isinstance(value, bool) or not 100 <= value <= 2_000:
        raise ValueError("vad_silence_duration_ms must be between 100 and 2000")
    return value


def _validated_truncation_post_instructions_tokens(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1_024 <= value <= 1_000_000
    ):
        raise ValueError(
            "truncation_post_instructions_tokens must be between 1024 and 1000000"
        )
    return value


def _validated_truncation_retention_ratio(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("truncation_retention_ratio must be a number")
    normalized = float(value)
    if not 0 < normalized < 1:
        raise ValueError("truncation_retention_ratio must be between 0 and 1")
    return normalized


def _validate_semantic_response_done(
    event: Mapping[str, object],
    *,
    expected_response_id: str | None,
) -> tuple[dict[str, object], str]:
    response = _mapping(event.get("response"), "response.done response")
    response_id = _required_string(response, "id")
    if expected_response_id is not None and response_id != expected_response_id:
        raise OpenAIRealtimeProtocolError(
            f"OpenAI response ID changed at response completion: expected {expected_response_id}, got {response_id}"
        )
    status = response.get("status")
    if status == "failed":
        status_details = response.get("status_details")
        details = f", details={status_details}" if status_details is not None else ""
        raise OpenAIRealtimeUnavailableError(
            f"OpenAI Realtime response failed (status={status}{details})"
        )
    if status == "incomplete":
        _response_output(response)
        status_details = _mapping(
            response.get("status_details"),
            "incomplete response status_details",
        )
        status_type = status_details.get("type")
        if status_type not in {None, "incomplete"}:
            raise OpenAIRealtimeProtocolError(
                "OpenAI incomplete response reported conflicting status details"
            )
        reason = _required_string(status_details, "reason")
        if reason not in {"max_output_tokens", "content_filter"}:
            raise OpenAIRealtimeProtocolError(
                "OpenAI incomplete response reported an unsupported reason"
            )
        raise RealtimeProviderResponseIncompleteError(reason=reason)
    if status not in {"completed", "cancelled"}:
        raise OpenAIRealtimeProtocolError(
            f"OpenAI Realtime response reported unsupported status: {status}"
        )
    _response_output(response)
    return response, response_id


def _response_output(response: Mapping[str, object]) -> list[object]:
    output = response.get("output")
    if output is None and response.get("status") == "cancelled":
        return []
    if not isinstance(output, list):
        raise OpenAIRealtimeProtocolError(
            "OpenAI Realtime response omitted its output items"
        )
    return output


def _merge_usage(
    accumulated: OpenAIRealtimeUsage | None,
    current: OpenAIRealtimeUsage,
) -> OpenAIRealtimeUsage:
    if accumulated is None:
        return current

    def optional_sum(left: int | None, right: int | None) -> int | None:
        if left is None or right is None:
            return None
        return left + right

    return OpenAIRealtimeUsage(
        input_tokens=accumulated.input_tokens + current.input_tokens,
        output_tokens=accumulated.output_tokens + current.output_tokens,
        total_tokens=accumulated.total_tokens + current.total_tokens,
        cached_input_tokens=optional_sum(
            accumulated.cached_input_tokens,
            current.cached_input_tokens,
        ),
        input_text_tokens=optional_sum(
            accumulated.input_text_tokens,
            current.input_text_tokens,
        ),
        input_audio_tokens=optional_sum(
            accumulated.input_audio_tokens,
            current.input_audio_tokens,
        ),
        output_text_tokens=optional_sum(
            accumulated.output_text_tokens,
            current.output_text_tokens,
        ),
        output_audio_tokens=optional_sum(
            accumulated.output_audio_tokens,
            current.output_audio_tokens,
        ),
    )


def _parse_usage(event: Mapping[str, object]) -> OpenAIRealtimeUsage:
    response = _mapping(event.get("response"), "response.done response")
    usage = _mapping(response.get("usage"), "response.done usage")
    input_details = _optional_mapping(usage.get("input_token_details"))
    output_details = _optional_mapping(usage.get("output_token_details"))
    return OpenAIRealtimeUsage(
        input_tokens=_non_negative_integer(usage, "input_tokens"),
        output_tokens=_non_negative_integer(usage, "output_tokens"),
        total_tokens=_non_negative_integer(usage, "total_tokens"),
        cached_input_tokens=_optional_non_negative_integer(
            input_details,
            "cached_tokens",
        ),
        input_text_tokens=_optional_non_negative_integer(
            input_details,
            "text_tokens",
        ),
        input_audio_tokens=_optional_non_negative_integer(
            input_details,
            "audio_tokens",
        ),
        output_text_tokens=_optional_non_negative_integer(
            output_details,
            "text_tokens",
        ),
        output_audio_tokens=_optional_non_negative_integer(
            output_details,
            "audio_tokens",
        ),
    )


def _provider_error(event: Mapping[str, object]) -> OpenAIRealtimeProviderError:
    error = _optional_mapping(event.get("error"))
    code = error.get("code") if isinstance(error.get("code"), str) else "unknown"
    message = error.get("message") if isinstance(error.get("message"), str) else ""
    param = error.get("param") if isinstance(error.get("param"), str) else None
    details = f"code={code}"
    if param:
        details += f", param={param}"
    if message:
        details += f": {message}"
    return OpenAIRealtimeUnavailableError(f"OpenAI Realtime provider error ({details})")


def _provider_error_code(event: Mapping[str, object]) -> str:
    error = _optional_mapping(event.get("error"))
    code = error.get("code")
    return code if isinstance(code, str) and code else "unknown"


async def _read_bounded_response(
    response: httpx.Response,
    max_bytes: int,
) -> bytes:
    chunks: list[bytes] = []
    byte_count = 0
    async for chunk in response.aiter_bytes():
        byte_count += len(chunk)
        if byte_count > max_bytes:
            raise RealtimeNegotiationResponseError(
                "OpenAI Realtime SDP answer exceeded Ava's byte limit"
            )
        chunks.append(chunk)
    if byte_count == 0:
        raise RealtimeNegotiationResponseError("OpenAI Realtime SDP answer was empty")
    return b"".join(chunks)


async def _read_bounded_error_response(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    byte_count = 0
    async for chunk in response.aiter_bytes():
        byte_count += len(chunk)
        if byte_count > MAX_PROVIDER_ERROR_BYTES:
            raise RealtimeNegotiationResponseError(
                "OpenAI Realtime error response exceeded Ava's byte limit",
                provider_status_code=response.status_code,
                provider_request_id=response.headers.get("x-request-id"),
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _provider_http_error_details(body: bytes) -> tuple[str | None, str | None]:
    try:
        decoded = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(decoded, dict):
        return None, None
    candidate = decoded.get("error", decoded)
    if not isinstance(candidate, dict):
        return None, None
    raw_code = candidate.get("code") or candidate.get("type")
    code = (
        raw_code.strip()[:120]
        if isinstance(raw_code, str) and raw_code.strip()
        else None
    )
    raw_parameter = candidate.get("param")
    parameter = (
        raw_parameter.strip()[:120]
        if isinstance(raw_parameter, str) and raw_parameter.strip()
        else None
    )
    return code, parameter


def _log_sdp_negotiation_failure(
    *,
    provider_status_code: int | None,
    provider_error_code: str | None,
    provider_error_parameter: str | None,
    provider_request_id: str | None,
) -> None:
    LOGGER.warning(
        "openai_realtime_negotiation_failed stage=sdp "
        "provider_status_code=%s provider_error_code=%s "
        "provider_error_parameter=%s provider_request_id=%s",
        provider_status_code,
        provider_error_code,
        provider_error_parameter,
        provider_request_id,
    )


def _call_id_from_location(location: str) -> str:
    parsed = urlsplit(location)
    if parsed.username is not None or parsed.password is not None:
        raise RealtimeNegotiationResponseError(
            "OpenAI Realtime Location cannot contain credentials"
        )
    if parsed.query or parsed.fragment:
        raise RealtimeNegotiationResponseError(
            "OpenAI Realtime Location cannot contain query or fragment data"
        )
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 4 or parts[:3] != ["v1", "realtime", "calls"]:
        raise RealtimeNegotiationResponseError(
            "OpenAI Realtime Location has an invalid call path"
        )
    return parts[-1]


def _websocket_handshake_error_metadata(
    error: Exception,
) -> tuple[int | None, str | None]:
    """Extract non-sensitive diagnostics from a WebSocket handshake failure."""

    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if not isinstance(status_code, int):
        status_code = None
    headers = getattr(response, "headers", None)
    request_id: str | None = None
    if headers is not None:
        candidate = headers.get("x-request-id")
        if isinstance(candidate, str) and candidate.strip():
            request_id = candidate.strip()[:256]
    return status_code, request_id


def _event_type(event: Mapping[str, object]) -> str:
    return _required_string(event, "type")


def _required_string(
    value: Mapping[str, object],
    key: str,
    *,
    allow_empty: bool = False,
) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or (not allow_empty and not candidate):
        raise OpenAIRealtimeProtocolError(f"OpenAI Realtime event has invalid {key}")
    return candidate


def _nested_string(
    value: Mapping[str, object],
    parent: str,
    child: str,
) -> str:
    return _required_string(_mapping(value.get(parent), parent), child)


def _mapping(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise OpenAIRealtimeProtocolError(
            f"OpenAI Realtime {description} must be an object"
        )
    return value


def _optional_mapping(value: object) -> dict[str, object]:
    if value is None:
        return {}
    return _mapping(value, "usage details")


def _non_negative_integer(value: Mapping[str, object], key: str) -> int:
    candidate = value.get(key)
    if type(candidate) is not int or candidate < 0:
        raise OpenAIRealtimeProtocolError(f"OpenAI Realtime usage has invalid {key}")
    return candidate


def _optional_non_negative_integer(
    value: Mapping[str, object],
    key: str,
) -> int | None:
    candidate = value.get(key)
    if candidate is None:
        return None
    if type(candidate) is not int or candidate < 0:
        raise OpenAIRealtimeProtocolError(f"OpenAI Realtime usage has invalid {key}")
    return candidate


def _required_secret(value: str, description: str) -> str:
    normalized = value.strip()
    if not normalized or normalized.lower() in {
        "replace-me",
        "changeme",
        "your-api-key",
    }:
        raise ValueError(f"{description} is missing or still a placeholder")
    return normalized


def _required_name(value: str, description: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError(f"{description} must contain 1-128 characters")
    if any(
        character
        not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
        for character in normalized
    ):
        raise ValueError(f"{description} contains unsupported characters")
    return normalized


def _required_safety_identifier(value: str) -> str:
    normalized = value.strip()
    if not 8 <= len(normalized) <= 128 or any(
        character
        not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        for character in normalized
    ):
        raise ValueError("safety_identifier has an invalid format")
    return normalized


def _validated_origin(value: str, *, scheme: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != scheme
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"provider origin must be an absolute {scheme} origin")
    return normalized


def _validated_websocket_url(value: str) -> str:
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "wss"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Realtime WebSocket URL must be an absolute WSS URL")
    return normalized


_RetainedValue = TypeVar("_RetainedValue")


def _retain_latest(
    values: dict[str, _RetainedValue],
    *,
    maximum: int,
) -> None:
    while len(values) > maximum:
        oldest = next(iter(values))
        del values[oldest]


def _response_id_from_event(event: Mapping[str, object]) -> str | None:
    event_type = event.get("type")
    if event_type in {"response.created", "response.done"}:
        response = event.get("response")
        if isinstance(response, dict):
            value = response.get("id")
            if isinstance(value, str) and value:
                return value
            return None
    value = event.get("response_id")
    return value if isinstance(value, str) and value else None


def _sideband_sequence(event: Mapping[str, object]) -> int | None:
    value = event.get(_SIDEBAND_SEQUENCE_FIELD)
    return value if type(value) is int and value > 0 else None


def _log_response_diagnostic(
    message: str,
    *,
    level: int = logging.INFO,
    event: Mapping[str, object] | None = None,
    event_type: str | None = None,
    response_id: str | None = None,
    response_owner_turn_id: str | None = None,
    consumer_turn_id: str | None = None,
    consumer_phase: str | None = None,
    active_turn_id: str | None = None,
    active_response_id: str | None = None,
    call_id: str | None = None,
    provider_call_id: str | None = None,
    active_context_build_id: str | None = None,
    status: str | None = None,
) -> None:
    if event is not None:
        event_type = event_type or _event_type(event)
        response_id = response_id or _response_id_from_event(event)
    sequence = _sideband_sequence(event or {})
    context: dict[str, object] = {
        "component": "realtimeProvider",
        "sidebandSequence": sequence,
        "eventType": event_type,
        "responseId": response_id,
        "responseOwnerTurnId": response_owner_turn_id,
        "consumerTurnId": consumer_turn_id,
        "consumerPhase": consumer_phase,
        "activeTurnId": active_turn_id,
        "activeResponseId": active_response_id,
    }
    if call_id is not None:
        context["callId"] = call_id
    if provider_call_id is not None:
        context["providerCallId"] = provider_call_id
    if active_context_build_id is not None:
        context["activeContextBuildId"] = active_context_build_id
    if status is not None:
        context["status"] = status
    LOGGER.log(
        level,
        "%s sideband_sequence=%s event_type=%s response_id=%s "
        "response_owner_turn_id=%s consumer_turn_id=%s consumer_phase=%s "
        "active_turn_id=%s active_response_id=%s provider_call_id=%s "
        "active_context_build_id=%s",
        message,
        sequence,
        event_type,
        response_id,
        response_owner_turn_id,
        consumer_turn_id,
        consumer_phase,
        active_turn_id,
        active_response_id,
        provider_call_id,
        active_context_build_id,
        extra={"ava_context": context},
    )


def _safe_close_reason(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = " ".join(value.split())
    return normalized[:120] or None


def _connection_error_context(error: BaseException) -> dict[str, object]:
    if not isinstance(error, ConnectionClosed):
        return {}
    received = getattr(error, "rcvd", None)
    sent = getattr(error, "sent", None)
    close = received or sent
    local_close = None
    if sent is not None and received is None:
        local_close = True
    elif received is not None and sent is None:
        local_close = False
    return {
        "localClose": local_close,
        "closeCode": getattr(close, "code", None),
        "closeReason": _safe_close_reason(getattr(close, "reason", None)),
    }


def _as_realtime_receive_error(error: BaseException) -> BaseException:
    if isinstance(error, OpenAIRealtimeProviderError):
        return error
    if isinstance(error, ConnectionClosed):
        context = _connection_error_context(error)
        local_close = context["localClose"]
        close_code = context["closeCode"]
        close_reason = context["closeReason"]
        if not isinstance(local_close, (bool, type(None))):
            raise AssertionError("invalid local close diagnostic")
        if not isinstance(close_code, (int, type(None))):
            raise AssertionError("invalid close code diagnostic")
        if not isinstance(close_reason, (str, type(None))):
            raise AssertionError("invalid close reason diagnostic")
        return OpenAIRealtimeConnectionClosedError(
            "OpenAI Realtime sideband connection closed",
            local_close=local_close,
            close_code=close_code,
            close_reason=close_reason,
        )
    return OpenAIRealtimeUnavailableError(
        "OpenAI Realtime connection closed unexpectedly"
    )


_PRE_RESPONSE_EVENT_TYPES = frozenset(
    {
        "conversation.created",
        "rate_limits.updated",
    }
)

_RESPONSE_DIAGNOSTIC_EVENT_TYPES = frozenset(
    {
        "response.created",
        "response.output_item.added",
        "response.function_call_arguments.done",
        "response.done",
    }
)

_INPUT_TRANSCRIPTION_PROGRESS_EVENT_TYPES = frozenset(
    {
        "conversation.item.input_audio_transcription.delta",
        "conversation.item.input_audio_transcription.segment",
    }
)

_INPUT_LIFECYCLE_EVENT_TYPES = frozenset(
    {
        *_INPUT_TRANSCRIPTION_PROGRESS_EVENT_TYPES,
        "conversation.item.input_audio_transcription.completed",
        "conversation.item.input_audio_transcription.failed",
        "input_audio_buffer.committed",
        "input_audio_buffer.speech_started",
        "input_audio_buffer.speech_stopped",
    }
)

_IN_TURN_IGNORED_EVENT_TYPES = frozenset(
    {
        *_INPUT_TRANSCRIPTION_PROGRESS_EVENT_TYPES,
        "conversation.created",
        "conversation.item.added",
        "conversation.item.created",
        "conversation.item.deleted",
        "conversation.item.done",
        "conversation.item.input_audio_transcription.completed",
        "conversation.item.input_audio_transcription.failed",
        "conversation.item.truncated",
        "input_audio_buffer.committed",
        "input_audio_buffer.speech_started",
        "input_audio_buffer.speech_stopped",
        "rate_limits.updated",
        "response.content_part.added",
        "response.content_part.done",
        "response.output_item.done",
        "response.output_text.done",
        "response.text.done",
        "session.updated",
    }
)

_RESPONSE_TRIGGER_EVENT_TYPES = frozenset(
    {
        "input_audio_buffer.committed",
        "input_audio_buffer.speech_stopped",
    }
)
