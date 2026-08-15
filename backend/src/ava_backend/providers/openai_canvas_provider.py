from __future__ import annotations

import asyncio
from hashlib import sha256
from html import escape
from typing import Any, TypedDict, cast

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from ..canvas.specialist import (
    CANVAS_SPECIALIST_PROMPT_VERSION,
    CanvasSpecialistGeneration,
    CanvasSpecialistInvalidError,
    CanvasSpecialistRequest,
    CanvasSpecialistUnavailableError,
    render_canvas_specialist_instructions,
)
from ..canvas.quality import CanvasQualityEvaluator, CanvasQualityReport
from ..config import Settings
from ..realtime.model_plan import ModelCanvasSpecialistDraft


class _StatusErrorDetails(TypedDict):
    provider_status_code: int | None
    provider_error_code: str | None
    provider_error_parameter: str | None
    provider_request_id: str | None


class OpenAICanvasSpecialist:
    """One strict Responses request for a model-authorized optional canvas."""

    def __init__(
        self,
        settings: Settings,
        *,
        safety_identifier: str,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._settings = settings
        self._safety_identifier = _required_safety_identifier(safety_identifier)
        self._client = client or AsyncOpenAI(
            api_key=settings.require_openai_api_key(),
            timeout=settings.openai_canvas_timeout_seconds,
            max_retries=settings.openai_canvas_max_retries,
        )
        self._owns_client = client is None
        self._active: dict[str, asyncio.Task[object]] = {}
        self._quality = CanvasQualityEvaluator()
        self._closed = False

    async def generate(
        self,
        request: CanvasSpecialistRequest,
    ) -> CanvasSpecialistGeneration:
        if self._closed:
            raise CanvasSpecialistUnavailableError("canvas specialist is closed")
        if request.turn_id in self._active:
            raise CanvasSpecialistInvalidError(
                "a canvas specialist request is already active for this turn"
            )

        response: object | None = None
        parsed: ModelCanvasSpecialistDraft | None = None
        quality_report: CanvasQualityReport | None = None
        total_input_tokens = 0
        total_output_tokens = 0
        total_cached_tokens = 0
        provider_request_id: str | None = None
        try:
            for attempt in range(self._settings.openai_canvas_max_quality_repairs + 1):
                task = asyncio.create_task(
                    self._request(
                        request,
                        failed_draft=parsed,
                        quality_report=quality_report,
                        attempt=attempt,
                    )
                )
                self._active[request.turn_id] = task
                response = await task
                parsed = cast(
                    ModelCanvasSpecialistDraft | None,
                    getattr(response, "output_parsed", None),
                )
                if parsed is None:
                    if _contains_refusal(response):
                        raise CanvasSpecialistInvalidError(
                            "canvas specialist refused the scene"
                        )
                    raise CanvasSpecialistInvalidError(
                        "canvas specialist returned no strict scene draft"
                    )
                usage = getattr(response, "usage", None)
                details = getattr(usage, "input_tokens_details", None)
                total_input_tokens += getattr(usage, "input_tokens", 0) or 0
                total_output_tokens += getattr(usage, "output_tokens", 0) or 0
                total_cached_tokens += getattr(details, "cached_tokens", 0) or 0
                provider_request_id = (
                    getattr(response, "_request_id", None)
                    or getattr(response, "id", None)
                    or provider_request_id
                )
                quality_report = self._quality.evaluate(
                    parsed.bind_scene_id(
                        request.current_scene.scene_id
                        if request.current_scene is not None
                        else _scene_identifier(request.turn_id)
                    )
                )
                if quality_report.accepted:
                    break
            if quality_report is None or not quality_report.accepted:
                issue_codes = ",".join(
                    issue.code
                    for issue in (quality_report.issues if quality_report else ())
                )
                raise CanvasSpecialistInvalidError(
                    "canvas specialist failed visual quality validation "
                    f"after bounded repair (issues={issue_codes or 'unknown'})"
                )
        except asyncio.CancelledError as error:
            raise CanvasSpecialistUnavailableError(
                "canvas specialist request was cancelled"
            ) from error
        except APITimeoutError as error:
            raise CanvasSpecialistUnavailableError(
                "canvas specialist exceeded its deadline"
            ) from error
        except RateLimitError as error:
            raise CanvasSpecialistUnavailableError(
                "canvas specialist was rate limited",
                **_status_error_details(error),
            ) from error
        except APIConnectionError as error:
            raise CanvasSpecialistUnavailableError(
                "canvas specialist could not connect to OpenAI"
            ) from error
        except APIStatusError as error:
            raise CanvasSpecialistUnavailableError(
                f"canvas specialist received HTTP {error.status_code} from OpenAI",
                **_status_error_details(error),
            ) from error
        except (TypeError, ValueError) as error:
            parse_details = _strict_parse_error_details(error)
            raise CanvasSpecialistInvalidError(
                "canvas specialist response failed strict parsing "
                f"(error_type={type(error).__name__}, "
                f"issue_count={parse_details['issue_count']}, "
                f"first_path={parse_details['first_path']}, "
                f"first_type={parse_details['first_type']}, "
                f"reason={parse_details['reason_code']})"
            ) from error
        finally:
            self._active.pop(request.turn_id, None)

        if parsed is None:
            raise CanvasSpecialistInvalidError(
                "canvas specialist completed without a validated draft"
            )
        return CanvasSpecialistGeneration(
            draft=parsed.bind_scene_id(
                request.current_scene.scene_id
                if request.current_scene is not None
                else _scene_identifier(request.turn_id)
            ),
            provider_request_id=provider_request_id,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cached_input_tokens=total_cached_tokens,
        )

    async def cancel(self, *, turn_id: str) -> None:
        task = self._active.get(turn_id)
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
        request: CanvasSpecialistRequest,
        *,
        failed_draft: ModelCanvasSpecialistDraft | None,
        quality_report: CanvasQualityReport | None,
        attempt: int,
    ) -> object:
        model_input = request.model_input()
        if failed_draft is not None and quality_report is not None:
            model_input += (
                '\n<quality_repair trust="validated_evaluator_feedback">\n'
                "Revise the complete candidate to fix every issue while preserving "
                "correct content and stable IDs.\n"
                f"<issues>{escape(quality_report.repair_brief())}</issues>\n"
                "<failed_candidate>"
                f"{escape(failed_draft.model_dump_json(by_alias=True, exclude_none=True))}"
                "</failed_candidate>\n"
                "</quality_repair>"
            )
        return await self._client.responses.parse(
            model=self._settings.openai_canvas_model,
            instructions=render_canvas_specialist_instructions(),
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": model_input,
                        }
                    ],
                }
            ],
            text_format=ModelCanvasSpecialistDraft,
            reasoning={"effort": self._settings.openai_canvas_reasoning_effort},
            text={"verbosity": "low"},
            max_output_tokens=self._settings.openai_canvas_max_output_tokens,
            store=False,
            prompt_cache_key=CANVAS_SPECIALIST_PROMPT_VERSION,
            safety_identifier=self._safety_identifier,
            metadata={
                "ava_protocol_version": "3",
                "ava_prompt_version": CANVAS_SPECIALIST_PROMPT_VERSION,
                "ava_provider_role": "canvas_specialist",
                "ava_quality_attempt": str(attempt),
            },
        )


def _contains_refusal(response: object) -> bool:
    for output in getattr(response, "output", ()):
        if getattr(output, "type", None) != "message":
            continue
        for content in getattr(output, "content", ()):
            if getattr(content, "type", None) == "refusal":
                return True
    return False


def _required_safety_identifier(value: str) -> str:
    normalized = value.strip()
    if not 8 <= len(normalized) <= 128 or any(
        character
        not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        for character in normalized
    ):
        raise ValueError("safety_identifier has an invalid format")
    return normalized


def _scene_identifier(turn_id: str) -> str:
    digest = sha256(turn_id.encode("utf-8")).hexdigest()[:16]
    return f"scene_{digest}"


def _status_error_details(error: APIStatusError) -> _StatusErrorDetails:
    return {
        "provider_status_code": error.status_code,
        "provider_error_code": _provider_error_code(error.body),
        "provider_error_parameter": _provider_error_parameter(error.body),
        "provider_request_id": getattr(error, "request_id", None),
    }


def _provider_error_code(body: object) -> str | None:
    if not isinstance(body, dict):
        return None
    candidate = body.get("error", body)
    if not isinstance(candidate, dict):
        return None
    for key in ("code", "type"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    return None


def _provider_error_parameter(body: object) -> str | None:
    if not isinstance(body, dict):
        return None
    candidate = body.get("error", body)
    if not isinstance(candidate, dict):
        return None
    value = candidate.get("param")
    if isinstance(value, str) and value.strip():
        return value.strip()[:120]
    return None


class _StrictParseErrorDetails(TypedDict):
    issue_count: int
    first_path: str
    first_type: str
    reason_code: str


def _strict_parse_error_details(error: Exception) -> _StrictParseErrorDetails:
    """Return bounded validation metadata without logging model output."""

    errors_method = getattr(error, "errors", None)
    if not callable(errors_method):
        return {
            "issue_count": 0,
            "first_path": "unknown",
            "first_type": "unknown",
            "reason_code": "unknown",
        }
    try:
        issues: Any = errors_method(
            include_url=False,
            include_context=False,
            include_input=False,
        )
    except TypeError:
        # Some validation libraries expose `errors()` without Pydantic's
        # keyword controls. Do not call those variants because they may embed
        # raw model output in the returned records.
        return {
            "issue_count": 0,
            "first_path": "unknown",
            "first_type": "unknown",
            "reason_code": "unknown",
        }
    if not isinstance(issues, list) or not issues:
        return {
            "issue_count": 0,
            "first_path": "unknown",
            "first_type": "unknown",
            "reason_code": "unknown",
        }
    first = issues[0]
    if not isinstance(first, dict):
        return {
            "issue_count": len(issues),
            "first_path": "unknown",
            "first_type": "unknown",
            "reason_code": "unknown",
        }
    location = first.get("loc")
    first_path = (
        ".".join(str(part) for part in location)
        if isinstance(location, tuple | list)
        else "unknown"
    )
    issue_type = first.get("type")
    message = first.get("msg")
    return {
        "issue_count": len(issues),
        "first_path": (first_path or "root")[:160],
        "first_type": (
            issue_type[:120]
            if isinstance(issue_type, str) and issue_type
            else "unknown"
        ),
        "reason_code": _safe_canvas_validation_reason(message),
    }


def _safe_canvas_validation_reason(message: object) -> str:
    """Classify a validation error without recording model-authored content."""

    if not isinstance(message, str):
        return "unknown"
    normalized = message.lower()
    categories = (
        ("hidden", "invalidHiddenState"),
        ("reset", "invalidResetInteraction"),
        ("dragwithin", "invalidDragInteraction"),
        ("interaction", "invalidInteraction"),
        ("parent", "invalidParent"),
        ("cycle", "invalidParentCycle"),
        ("unique", "duplicateIdentifier"),
    )
    for fragment, code in categories:
        if fragment in normalized:
            return code
    return "schemaValidation"
