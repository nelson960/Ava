from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from html import escape
from typing import TypedDict, cast

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from ..canvas.svg_document import (
    SvgDocumentValidationError,
    SvgDocumentValidator,
)
from ..canvas.svg_specialist import (
    SVG_CANVAS_SPECIALIST_PROMPT_VERSION,
    SvgCanvasSpecialistGeneration,
    SvgCanvasSpecialistGenerationLimitError,
    SvgCanvasSpecialistInvalidError,
    SvgCanvasSpecialistRequest,
    SvgCanvasSpecialistUnavailableError,
    render_svg_canvas_specialist_instructions,
)
from ..config import Settings


LOGGER = logging.getLogger("ava_backend.openai_svg_canvas")

_EXPECTED_NONTERMINAL_STREAM_EVENTS = frozenset(
    {
        "response.created",
        "response.queued",
        "response.in_progress",
        "response.output_item.added",
        "response.output_item.done",
        "response.content_part.added",
        "response.content_part.done",
        "response.reasoning_summary_part.added",
        "response.reasoning_summary_part.done",
        "response.reasoning_summary_text.delta",
        "response.reasoning_summary_text.done",
        "response.reasoning_text.delta",
        "response.reasoning_text.done",
        "response.output_text.done",
        "response.output_text.annotation.added",
        "response.refusal.delta",
        "response.refusal.done",
    }
)


class _StatusErrorDetails(TypedDict):
    provider_status_code: int | None
    provider_error_code: str | None
    provider_error_parameter: str | None
    provider_request_id: str | None


class _ResponseShapeError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.provider_request_id = provider_request_id


class _CanvasGenerationDeadlineExceeded(TimeoutError):
    """The generation-wide original/validation/repair budget expired."""


@dataclass(frozen=True, slots=True)
class _StreamedResponse:
    response: object
    output_text_deltas: str


class OpenAISvgCanvasSpecialist:
    """Sol returns exact SVG text; Ava validates without rewriting it."""

    def __init__(
        self,
        settings: Settings,
        *,
        session_id: str,
        safety_identifier: str,
        client: AsyncOpenAI | None = None,
        validator: SvgDocumentValidator | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._session_id = _required_runtime_id(session_id, label="session_id")
        self._safety_identifier = _required_safety_identifier(safety_identifier)
        self._client = client or AsyncOpenAI(
            api_key=settings.require_openai_api_key(),
            timeout=settings.openai_canvas_timeout_seconds,
            max_retries=settings.openai_canvas_max_retries,
        )
        self._owns_client = client is None
        self._validator = validator or SvgDocumentValidator()
        self._monotonic = monotonic
        self._active: dict[str, asyncio.Task[object]] = {}
        self._closed = False

    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
    ) -> SvgCanvasSpecialistGeneration:
        if self._closed:
            raise SvgCanvasSpecialistUnavailableError("SVG canvas specialist is closed")
        if request.generation_id in self._active:
            raise SvgCanvasSpecialistInvalidError(
                "an SVG canvas specialist request is already active for this generation"
            )
        generation_task = asyncio.current_task()
        if generation_task is None:  # pragma: no cover - async runtime invariant
            raise RuntimeError("SVG canvas generation requires an owning task")
        self._active[request.generation_id] = generation_task

        total_input_tokens = 0
        total_output_tokens = 0
        total_cached_tokens = 0
        total_cache_write_tokens = 0
        total_reasoning_tokens = 0
        provider_request_id: str | None = None
        failed_svg: str | None = None
        failure_code: str | None = None
        failure_detail: str | None = None
        generation_started_at = self._monotonic()
        generation_deadline = (
            generation_started_at + self._settings.openai_canvas_timeout_seconds
        )
        try:
            for attempt in range(self._settings.openai_canvas_max_quality_repairs + 1):
                if attempt > 0:
                    self._log(
                        "repairStarted",
                        request=request,
                        attempt=attempt,
                        elapsed_from=generation_started_at,
                        failureCode=failure_code,
                    )
                task = asyncio.create_task(
                    self._request(
                        request,
                        attempt=attempt,
                        failed_svg=failed_svg,
                        failure_code=failure_code,
                        failure_detail=failure_detail,
                    )
                )
                try:
                    remaining_seconds = generation_deadline - self._monotonic()
                    if remaining_seconds <= 0:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        raise _CanvasGenerationDeadlineExceeded
                    try:
                        streamed = await asyncio.wait_for(
                            task,
                            timeout=remaining_seconds,
                        )
                    except TimeoutError as error:
                        raise _CanvasGenerationDeadlineExceeded from error
                except _ResponseShapeError as error:
                    provider_request_id = (
                        error.provider_request_id or provider_request_id
                    )
                    if attempt > 0:
                        self._log(
                            "repairCompleted",
                            request=request,
                            attempt=attempt,
                            elapsed_from=generation_started_at,
                            result="rejected",
                            failureCode=error.code,
                            providerRequestId=provider_request_id,
                        )
                    self._log(
                        "generationFailed",
                        request=request,
                        attempt=attempt,
                        elapsed_from=generation_started_at,
                        failureCode=error.code,
                        failureKind="responseEnvelope",
                        providerRequestId=provider_request_id,
                    )
                    raise SvgCanvasSpecialistInvalidError(
                        "SVG canvas specialist returned an invalid Responses stream "
                        f"(reason={error.code})",
                        provider_request_id=_as_optional_string(provider_request_id),
                    ) from error
                response = streamed.response
                provider_request_id = (
                    _as_optional_string(
                        _field(response, "_request_id") or _field(response, "id")
                    )
                    or provider_request_id
                )
                usage = _field(response, "usage")
                details = _field(usage, "input_tokens_details")
                output_details = _field(usage, "output_tokens_details")
                total_input_tokens += _integer_field(usage, "input_tokens")
                total_output_tokens += _integer_field(usage, "output_tokens")
                total_cached_tokens += _integer_field(details, "cached_tokens")
                total_cache_write_tokens += _integer_field(
                    details, "cache_write_tokens"
                )
                total_reasoning_tokens += _integer_field(
                    output_details, "reasoning_tokens"
                )
                validation_started_at = self._monotonic()
                self._log(
                    "validationStarted",
                    request=request,
                    attempt=attempt,
                    elapsed_from=generation_started_at,
                    providerRequestId=provider_request_id,
                )
                try:
                    svg_text = extract_completed_svg_output_text(response)
                    if streamed.output_text_deltas != svg_text:
                        raise _ResponseShapeError(
                            "streamOutputMismatch",
                            "Responses text deltas do not match terminal output text",
                        )
                    document = self._validator.validate(svg_text)
                    if document.interaction_mode.value != request.interaction_mode:
                        raise SvgDocumentValidationError(
                            "interactionModeMismatch",
                            "validated SVG interaction mode does not match the request",
                        )
                except _ResponseShapeError as error:
                    self._log(
                        "validationCompleted",
                        request=request,
                        attempt=attempt,
                        elapsed_from=validation_started_at,
                        result="rejected",
                        failureCode=error.code,
                        providerRequestId=provider_request_id,
                    )
                    if attempt > 0:
                        self._log(
                            "repairCompleted",
                            request=request,
                            attempt=attempt,
                            elapsed_from=generation_started_at,
                            result="rejected",
                            failureCode=error.code,
                        )
                    self._log(
                        "generationFailed",
                        request=request,
                        attempt=attempt,
                        elapsed_from=generation_started_at,
                        failureCode=error.code,
                        failureKind="responseEnvelope",
                    )
                    raise SvgCanvasSpecialistInvalidError(
                        "SVG canvas specialist returned an invalid Responses envelope "
                        f"(reason={error.code})",
                        provider_request_id=_as_optional_string(provider_request_id),
                    ) from error
                except SvgDocumentValidationError as error:
                    failure_code = error.code
                    failure_detail = str(error)
                    failed_svg = svg_text
                    self._log(
                        "validationCompleted",
                        request=request,
                        attempt=attempt,
                        elapsed_from=validation_started_at,
                        result="rejected",
                        failureCode=error.code,
                        providerRequestId=provider_request_id,
                        svgByteLength=len(svg_text.encode("utf-8")),
                    )
                    if attempt > 0:
                        self._log(
                            "repairCompleted",
                            request=request,
                            attempt=attempt,
                            elapsed_from=generation_started_at,
                            result="rejected",
                            failureCode=error.code,
                        )
                    if attempt < self._settings.openai_canvas_max_quality_repairs:
                        continue
                    self._log(
                        "generationFailed",
                        request=request,
                        attempt=attempt,
                        elapsed_from=generation_started_at,
                        failureCode=error.code,
                        failureKind="validation",
                    )
                    raise SvgCanvasSpecialistInvalidError(
                        "SVG canvas specialist returned a rejected static document "
                        f"(reason={error.code})",
                        provider_request_id=_as_optional_string(provider_request_id),
                    ) from error
                self._log(
                    "validationCompleted",
                    request=request,
                    attempt=attempt,
                    elapsed_from=validation_started_at,
                    result="accepted",
                    providerRequestId=provider_request_id,
                    svgByteLength=len(document.svg_bytes),
                    svgSha256=document.svg_sha256,
                    manifestByteLength=len(document.manifest_bytes),
                    manifestSha256=document.manifest_sha256,
                )
                if attempt > 0:
                    self._log(
                        "repairCompleted",
                        request=request,
                        attempt=attempt,
                        elapsed_from=generation_started_at,
                        result="accepted",
                    )
                self._log(
                    "generationCompleted",
                    request=request,
                    attempt=attempt,
                    elapsed_from=generation_started_at,
                    providerRequestId=provider_request_id,
                    attemptCount=attempt + 1,
                    inputTokens=total_input_tokens,
                    cachedInputTokens=total_cached_tokens,
                    cacheWriteInputTokens=total_cache_write_tokens,
                    outputTokens=total_output_tokens,
                    reasoningTokens=total_reasoning_tokens,
                    svgByteLength=len(document.svg_bytes),
                    svgSha256=document.svg_sha256,
                    manifestByteLength=len(document.manifest_bytes),
                    manifestSha256=document.manifest_sha256,
                )
                return SvgCanvasSpecialistGeneration(
                    generation_id=request.generation_id,
                    document=document,
                    provider_request_id=_as_optional_string(provider_request_id),
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    cached_input_tokens=total_cached_tokens,
                )
        except SvgCanvasSpecialistGenerationLimitError as error:
            self._log(
                "generationFailed",
                request=request,
                elapsed_from=generation_started_at,
                failureCode="generationLimitExceeded",
                failureKind="generationLimit",
                providerRequestId=error.provider_request_id,
            )
            raise
        except _CanvasGenerationDeadlineExceeded as error:
            self._log(
                "generationFailed",
                request=request,
                elapsed_from=generation_started_at,
                failureKind="timeout",
                timeoutScope="generation",
            )
            raise SvgCanvasSpecialistUnavailableError(
                "SVG canvas specialist exceeded its generation deadline"
            ) from error
        except asyncio.CancelledError as error:
            self._log(
                "generationFailed",
                request=request,
                elapsed_from=generation_started_at,
                failureKind="cancelled",
            )
            raise SvgCanvasSpecialistUnavailableError(
                "SVG canvas specialist request was cancelled"
            ) from error
        except APITimeoutError as error:
            self._log(
                "generationFailed",
                request=request,
                elapsed_from=generation_started_at,
                failureKind="timeout",
            )
            raise SvgCanvasSpecialistUnavailableError(
                "SVG canvas specialist exceeded its deadline"
            ) from error
        except RateLimitError as error:
            self._log(
                "generationFailed",
                request=request,
                elapsed_from=generation_started_at,
                failureKind="rateLimit",
                providerRequestId=getattr(error, "request_id", None),
            )
            raise SvgCanvasSpecialistUnavailableError(
                "SVG canvas specialist was rate limited",
                **_status_error_details(error),
            ) from error
        except APIConnectionError as error:
            self._log(
                "generationFailed",
                request=request,
                elapsed_from=generation_started_at,
                failureKind="connection",
            )
            raise SvgCanvasSpecialistUnavailableError(
                "SVG canvas specialist could not connect to OpenAI"
            ) from error
        except APIStatusError as error:
            self._log(
                "generationFailed",
                request=request,
                elapsed_from=generation_started_at,
                failureKind="providerStatus",
                providerStatusCode=error.status_code,
                providerRequestId=getattr(error, "request_id", None),
            )
            raise SvgCanvasSpecialistUnavailableError(
                f"SVG canvas specialist received HTTP {error.status_code} from OpenAI",
                **_status_error_details(error),
            ) from error
        finally:
            self._active.pop(request.generation_id, None)

        raise SvgCanvasSpecialistInvalidError(
            "SVG canvas specialist completed without a validated document"
        )

    async def cancel(self, *, generation_id: str) -> None:
        task = self._active.get(generation_id)
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = tuple(self._active.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._active.clear()
        if self._owns_client:
            await self._client.close()

    async def _request(
        self,
        request: SvgCanvasSpecialistRequest,
        *,
        attempt: int,
        failed_svg: str | None,
        failure_code: str | None,
        failure_detail: str | None,
    ) -> _StreamedResponse:
        model_input = request.model_input()
        if failure_code is not None:
            failed_document = (
                f"<failed_svg>{escape(failed_svg)}</failed_svg>\n"
                if failed_svg is not None
                else ""
            )
            model_input += (
                '\n<validation_repair trust="deterministic_validator_feedback" '
                f'code="{escape(failure_code)}">\n'
                "Return a complete replacement SVG that fixes this exact rejection. "
                "Do not discuss the failure.\n"
                f"<validation_detail>{escape(failure_detail or '')}"
                "</validation_detail>\n"
                f"{failed_document}"
                "</validation_repair>"
            )
        attempt_started_at = self._monotonic()
        self._log(
            "attemptStarted",
            request=request,
            attempt=attempt,
            elapsed_from=attempt_started_at,
            isRepair=attempt > 0,
        )
        stream = await self._client.responses.create(
            model=self._settings.openai_canvas_model,
            instructions=render_svg_canvas_specialist_instructions(self._validator),
            input=[
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": model_input}],
                }
            ],
            reasoning={"effort": self._settings.openai_canvas_reasoning_effort},
            text={"verbosity": "low"},
            max_output_tokens=self._settings.openai_canvas_max_output_tokens,
            store=False,
            stream=True,
            prompt_cache_key=SVG_CANVAS_SPECIALIST_PROMPT_VERSION,
            safety_identifier=self._safety_identifier,
            metadata={
                "ava_protocol_version": "4",
                "ava_prompt_version": SVG_CANVAS_SPECIALIST_PROMPT_VERSION,
                "ava_provider_role": "svg_canvas_specialist",
                "ava_validation_attempt": str(attempt),
            },
        )
        if not hasattr(stream, "__aiter__"):
            raise _ResponseShapeError(
                "responseStreamMissing",
                "Responses streaming request did not return an asynchronous stream",
            )
        close_stream = getattr(stream, "close", None)
        if not callable(close_stream):
            raise _ResponseShapeError(
                "responseStreamCloseMissing",
                "Responses streaming request did not expose async cleanup",
            )
        try:
            return await self._consume_stream(
                stream,  # type: ignore[arg-type]
                request=request,
                attempt=attempt,
                attempt_started_at=attempt_started_at,
            )
        finally:
            await cast(Callable[[], Awaitable[None]], close_stream)()

    async def _consume_stream(
        self,
        stream: AsyncIterator[object],
        *,
        request: SvgCanvasSpecialistRequest,
        attempt: int,
        attempt_started_at: float,
    ) -> _StreamedResponse:
        response: object | None = None
        terminal_type: str | None = None
        provider_request_id: str | None = None
        deltas: list[str] = []
        first_delta_recorded = False
        async for event in stream:
            event_type = _field(event, "type")
            if not isinstance(event_type, str):
                raise _ResponseShapeError(
                    "streamEventTypeMissing",
                    "Responses stream event is missing its type",
                    provider_request_id=provider_request_id,
                )
            if terminal_type is not None:
                raise _ResponseShapeError(
                    "eventAfterTerminal",
                    "Responses stream emitted an event after its terminal event",
                    provider_request_id=provider_request_id,
                )
            if event_type == "response.output_text.delta":
                delta = _field(event, "delta")
                if not isinstance(delta, str):
                    raise _ResponseShapeError(
                        "outputDeltaInvalid",
                        "Responses output text delta must contain text",
                        provider_request_id=provider_request_id,
                    )
                deltas.append(delta)
                if delta and not first_delta_recorded:
                    first_delta_recorded = True
                    self._log(
                        "firstOutputDelta",
                        request=request,
                        attempt=attempt,
                        elapsed_from=attempt_started_at,
                        providerRequestId=provider_request_id,
                    )
                continue
            if event_type in {
                "response.completed",
                "response.failed",
                "response.incomplete",
            }:
                terminal_type = event_type
                response = _field(event, "response")
                if response is None:
                    raise _ResponseShapeError(
                        "terminalResponseMissing",
                        "Responses terminal event is missing its response",
                        provider_request_id=provider_request_id,
                    )
                provider_request_id = (
                    _as_optional_string(
                        _field(response, "_request_id") or _field(response, "id")
                    )
                    or provider_request_id
                )
                break
            if event_type == "error":
                terminal_type = event_type
                continue
            if event_type not in _EXPECTED_NONTERMINAL_STREAM_EVENTS:
                raise _ResponseShapeError(
                    "unexpectedStreamEvent",
                    f"Responses stream emitted unsupported event {event_type!r}",
                    provider_request_id=provider_request_id,
                )
            if event_type == "response.created":
                created_response = _field(event, "response")
                provider_request_id = (
                    _as_optional_string(
                        _field(created_response, "_request_id")
                        or _field(created_response, "id")
                    )
                    or provider_request_id
                )

        if terminal_type is None:
            raise _ResponseShapeError(
                "terminalEventMissing",
                "Responses stream ended without a terminal event",
                provider_request_id=provider_request_id,
            )
        if terminal_type == "error":
            self._log(
                "attemptCompleted",
                request=request,
                attempt=attempt,
                elapsed_from=attempt_started_at,
                providerRequestId=provider_request_id,
                status="error",
                outputDeltaCharacters=sum(len(delta) for delta in deltas),
            )
            raise _ResponseShapeError(
                "responseStreamError",
                "Responses stream emitted an error event",
                provider_request_id=provider_request_id,
            )
        if response is None:  # pragma: no cover - guarded above
            raise RuntimeError("Responses terminal event lost its response")

        usage = _field(response, "usage")
        input_details = _field(usage, "input_tokens_details")
        output_details = _field(usage, "output_tokens_details")
        status = _as_optional_string(_field(response, "status"))
        self._log(
            "attemptCompleted",
            request=request,
            attempt=attempt,
            elapsed_from=attempt_started_at,
            providerRequestId=provider_request_id,
            status=status,
            inputTokens=_integer_field(usage, "input_tokens"),
            cachedInputTokens=_integer_field(input_details, "cached_tokens"),
            cacheWriteInputTokens=_integer_field(input_details, "cache_write_tokens"),
            outputTokens=_integer_field(usage, "output_tokens"),
            reasoningTokens=_integer_field(output_details, "reasoning_tokens"),
            outputDeltaCharacters=sum(len(delta) for delta in deltas),
        )
        if terminal_type == "response.incomplete":
            incomplete_details = _field(response, "incomplete_details")
            incomplete_reason = _as_optional_string(
                _field(incomplete_details, "reason")
            )
            if incomplete_reason == "max_output_tokens":
                raise SvgCanvasSpecialistGenerationLimitError(
                    "SVG canvas specialist exhausted its output generation limit",
                    provider_request_id=provider_request_id,
                )
        if terminal_type != "response.completed":
            code = (
                "responseFailed"
                if terminal_type == "response.failed"
                else "responseIncomplete"
            )
            raise _ResponseShapeError(
                code,
                f"Responses stream terminated with {terminal_type}",
                provider_request_id=provider_request_id,
            )
        return _StreamedResponse(
            response=response,
            output_text_deltas="".join(deltas),
        )

    def _log(
        self,
        stage: str,
        *,
        request: SvgCanvasSpecialistRequest,
        attempt: int | None = None,
        elapsed_from: float | None = None,
        **fields: object,
    ) -> None:
        context: dict[str, object] = {
            "component": "svgCanvasSpecialist",
            "stage": stage,
            "sessionId": self._session_id,
            "generationId": request.generation_id,
            "sourceTurnId": request.source_turn_id,
        }
        if attempt is not None:
            context["attemptNumber"] = attempt + 1
        if elapsed_from is not None:
            context["durationMs"] = max(0.0, (self._monotonic() - elapsed_from) * 1000)
        context.update(
            {key: value for key, value in fields.items() if value is not None}
        )
        LOGGER.info(
            "svg_canvas_latency",
            extra={"ava_context": context},
        )


def extract_completed_svg_output_text(response: object) -> str:
    """Extract one exact output_text part without joining or normalizing content."""

    if _field(response, "status") != "completed":
        raise _ResponseShapeError(
            "responseNotCompleted",
            "Responses result must have status=completed",
        )
    output = _field(response, "output")
    if not isinstance(output, list | tuple):
        raise _ResponseShapeError("outputMissing", "Responses output must be a list")

    messages: list[object] = []
    for item in output:
        item_type = _field(item, "type")
        if item_type == "reasoning":
            continue
        if item_type != "message":
            raise _ResponseShapeError(
                "unexpectedOutputItem",
                "Responses output contains an unexpected item",
            )
        messages.append(item)
    if len(messages) != 1:
        raise _ResponseShapeError(
            "messageCountInvalid",
            "Responses output must contain exactly one assistant message",
        )

    message = messages[0]
    if _field(message, "role") != "assistant":
        raise _ResponseShapeError(
            "messageRoleInvalid",
            "Responses message must have role=assistant",
        )
    if _field(message, "status") != "completed":
        raise _ResponseShapeError(
            "messageNotCompleted",
            "Responses assistant message must have status=completed",
        )
    content = _field(message, "content")
    if not isinstance(content, list | tuple) or len(content) != 1:
        raise _ResponseShapeError(
            "contentCountInvalid",
            "Responses assistant message must contain exactly one content part",
        )
    part = content[0]
    if _field(part, "type") == "refusal":
        raise _ResponseShapeError("responseRefused", "Responses assistant refused")
    if _field(part, "type") != "output_text":
        raise _ResponseShapeError(
            "contentTypeInvalid",
            "Responses content part must have type=output_text",
        )
    text = _field(part, "text")
    if not isinstance(text, str):
        raise _ResponseShapeError(
            "outputTextMissing",
            "Responses output_text part must contain text",
        )
    return text


def _field(value: object, name: str) -> object | None:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _integer_field(value: object, name: str) -> int:
    candidate = _field(value, name)
    return candidate if isinstance(candidate, int) and candidate >= 0 else 0


def _as_optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _required_safety_identifier(value: str) -> str:
    normalized = value.strip()
    if not 8 <= len(normalized) <= 128 or any(
        character
        not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        for character in normalized
    ):
        raise ValueError("safety_identifier has an invalid format")
    return normalized


def _required_runtime_id(value: str, *, label: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or normalized[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        or len(normalized) > 128
        or any(
            character
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
            for character in normalized
        )
    ):
        raise ValueError(f"{label} has an invalid format")
    return normalized


def _status_error_details(error: APIStatusError) -> _StatusErrorDetails:
    return {
        "provider_status_code": error.status_code,
        "provider_error_code": _provider_error_field(error.body, "code", "type"),
        "provider_error_parameter": _provider_error_field(error.body, "param"),
        "provider_request_id": getattr(error, "request_id", None),
    }


def _provider_error_field(body: object, *names: str) -> str | None:
    if not isinstance(body, dict):
        return None
    candidate = body.get("error", body)
    if not isinstance(candidate, dict):
        return None
    for name in names:
        value = candidate.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    return None
