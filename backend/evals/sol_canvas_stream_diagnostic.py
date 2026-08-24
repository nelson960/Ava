from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
import time
from types import SimpleNamespace
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_specialist import (
    SVG_CANVAS_SPECIALIST_PROMPT_VERSION,
    SvgCanvasSpecialistRequest,
    render_svg_canvas_specialist_instructions,
)
from ava_backend.config import AvaEnvironment, BackendConfigurationError, Settings
from ava_backend.providers.openai_svg_canvas_provider import OpenAISvgCanvasSpecialist


_RUN_COUNT = 5
_BRIEF = "Show a labeled water cycle with a clear loop."
_INTERACTION_GOAL = "Let the child tap each stage to reveal its label."
_SAFETY_IDENTIFIER = hashlib.sha256(
    b"ava-sol-canvas-stream-diagnostic-v1"
).hexdigest()


def _field(value: object | None, name: str) -> object | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _integer_field(value: object | None, name: str) -> int | None:
    candidate = _field(value, name)
    return candidate if isinstance(candidate, int) and candidate >= 0 else None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class _RunMetrics:
    def __init__(self, run_number: int) -> None:
        self.run_number = run_number
        self.started_at_utc = _utc_now()
        self.started_at = time.monotonic()
        self.first_delta_ms: float | None = None
        self.delta_count = 0
        self.output_characters = 0
        self.last_delta_at_utc: str | None = None
        self.last_delta_ms: float | None = None
        self.output_text_done = False
        self.output_text_done_ms: float | None = None
        self.terminal_type: str | None = None
        self.terminal_ms: float | None = None
        self.stream_ended_ms: float | None = None
        self.output_tokens: int | None = None
        self.service_tier: str | None = None
        self.response_status: str | None = None
        self.provider_response_id: str | None = None
        self.timeout = False
        self.provider_error: dict[str, object | None] | None = None
        self.event_counts: Counter[str] = Counter()
        self.non_delta_sequence: list[dict[str, object | None]] = []
        self.events_after_terminal: list[str] = []

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started_at) * 1000

    def record(self, event: object) -> None:
        event_type = _field(event, "type")
        if not isinstance(event_type, str):
            raise RuntimeError("Responses stream event is missing a string type")
        self.event_counts[event_type] += 1
        sequence_number = _field(event, "sequence_number")
        if event_type != "response.output_text.delta":
            self.non_delta_sequence.append(
                {
                    "type": event_type,
                    "sequenceNumber": (
                        sequence_number if isinstance(sequence_number, int) else None
                    ),
                    "elapsedMs": round(self.elapsed_ms(), 3),
                }
            )
        if self.terminal_type is not None:
            self.events_after_terminal.append(event_type)

        if event_type == "response.output_text.delta":
            delta = _field(event, "delta")
            if not isinstance(delta, str):
                raise RuntimeError("Responses output-text delta is not a string")
            elapsed_ms = self.elapsed_ms()
            if self.first_delta_ms is None:
                self.first_delta_ms = elapsed_ms
            self.delta_count += 1
            self.output_characters += len(delta)
            self.last_delta_at_utc = _utc_now()
            self.last_delta_ms = elapsed_ms
            return

        if event_type == "response.output_text.done":
            self.output_text_done = True
            self.output_text_done_ms = self.elapsed_ms()
            return

        if event_type not in {
            "response.completed",
            "response.failed",
            "response.incomplete",
        }:
            return

        self.terminal_type = event_type
        self.terminal_ms = self.elapsed_ms()
        response = _field(event, "response")
        self.response_status = _field(response, "status")  # type: ignore[assignment]
        self.provider_response_id = _field(response, "id")  # type: ignore[assignment]
        usage = _field(response, "usage")
        self.output_tokens = _integer_field(usage, "output_tokens")
        service_tier = _field(response, "service_tier")
        self.service_tier = service_tier if isinstance(service_tier, str) else None

    def finish(self) -> None:
        self.stream_ended_ms = self.elapsed_ms()

    def as_dict(self) -> dict[str, object]:
        total_duration_ms = self.stream_ended_ms or self.elapsed_ms()
        return {
            "run": self.run_number,
            "startedAtUtc": self.started_at_utc,
            "timeToFirstDeltaMs": _rounded(self.first_delta_ms),
            "deltaCount": self.delta_count,
            "cumulativeOutputCharacters": self.output_characters,
            "lastDeltaAtUtc": self.last_delta_at_utc,
            "lastDeltaElapsedMs": _rounded(self.last_delta_ms),
            "outputTextDoneReceived": self.output_text_done,
            "outputTextDoneElapsedMs": _rounded(self.output_text_done_ms),
            "terminalType": self.terminal_type,
            "terminalElapsedMs": _rounded(self.terminal_ms),
            "responseStatus": self.response_status,
            "responseCompleted": self.terminal_type == "response.completed",
            "responseFailed": self.terminal_type == "response.failed",
            "responseIncomplete": self.terminal_type == "response.incomplete",
            "outputTokens": self.output_tokens,
            "serviceTier": self.service_tier,
            "totalDurationMs": round(total_duration_ms, 3),
            "streamEndAfterTerminalMs": _rounded(
                None
                if self.stream_ended_ms is None or self.terminal_ms is None
                else self.stream_ended_ms - self.terminal_ms
            ),
            "timedOut": self.timeout,
            "providerError": self.provider_error,
            "providerResponseId": self.provider_response_id,
            "eventTypeCounts": dict(sorted(self.event_counts.items())),
            "nonDeltaEventSequence": self.non_delta_sequence,
            "eventsAfterTerminal": self.events_after_terminal,
        }


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


async def _consume_one(
    client: AsyncOpenAI,
    settings: Settings,
    *,
    run_number: int,
    instructions: str,
) -> _RunMetrics:
    metrics = _RunMetrics(run_number)
    request = SvgCanvasSpecialistRequest.model_validate(
        {
            "generationId": f"generation-sol-stream-probe-{run_number}",
            "sourceTurnId": f"turn-sol-stream-probe-{run_number}",
            "brief": _BRIEF,
            "interactionGoal": _INTERACTION_GOAL,
            "ageBand": "middleChildhood",
        }
    )

    async def create_and_consume() -> None:
        stream = await client.responses.create(
            model=settings.openai_canvas_model,
            instructions=instructions,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": request.model_input()}
                    ],
                }
            ],
            reasoning={"effort": settings.openai_canvas_reasoning_effort},
            text={"verbosity": "low"},
            max_output_tokens=settings.openai_canvas_max_output_tokens,
            store=False,
            stream=True,
            prompt_cache_key=SVG_CANVAS_SPECIALIST_PROMPT_VERSION,
            safety_identifier=_SAFETY_IDENTIFIER,
            metadata={
                "ava_protocol_version": "4",
                "ava_prompt_version": SVG_CANVAS_SPECIALIST_PROMPT_VERSION,
                "ava_provider_role": "svg_canvas_specialist",
                "ava_validation_attempt": "0",
            },
        )
        async for event in stream:
            metrics.record(event)
        metrics.finish()

    try:
        await asyncio.wait_for(
            create_and_consume(),
            timeout=settings.openai_canvas_timeout_seconds,
        )
    except TimeoutError:
        metrics.timeout = True
    except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as error:
        metrics.provider_error = {
            "type": type(error).__name__,
            "statusCode": getattr(error, "status_code", None),
            "requestId": getattr(error, "request_id", None),
            "code": _field(_field(error, "body"), "code"),
        }
    return metrics


async def _run(*, allow_spend: bool) -> int:
    settings = Settings()
    if not allow_spend:
        raise BackendConfigurationError(
            "Sol stream diagnostic refused: pass --allow-spend explicitly"
        )
    if not settings.openai_live_tests_enabled:
        raise BackendConfigurationError(
            "Sol stream diagnostic refused: set "
            "AVA_OPENAI_LIVE_TESTS_ENABLED=true for this command"
        )

    validator = SvgDocumentValidator()
    instructions = render_svg_canvas_specialist_instructions(validator)
    client = AsyncOpenAI(
        api_key=settings.require_openai_api_key(),
        timeout=settings.openai_canvas_timeout_seconds,
        max_retries=settings.openai_canvas_max_retries,
    )
    results: list[dict[str, object]] = []
    try:
        for run_number in range(1, _RUN_COUNT + 1):
            metrics = await _consume_one(
                client,
                settings,
                run_number=run_number,
                instructions=instructions,
            )
            result = metrics.as_dict()
            results.append(result)
            print(json.dumps({"progress": result}, sort_keys=True), flush=True)
    finally:
        await client.close()

    report: dict[str, Any] = {
        "configuration": {
            "model": settings.openai_canvas_model,
            "reasoningEffort": settings.openai_canvas_reasoning_effort,
            "textVerbosity": "low",
            "maxOutputTokens": settings.openai_canvas_max_output_tokens,
            "timeoutSeconds": settings.openai_canvas_timeout_seconds,
            "stream": True,
            "serviceTierSpecified": False,
            "promptVersion": SVG_CANVAS_SPECIALIST_PROMPT_VERSION,
            "instructionsSha256": hashlib.sha256(instructions.encode()).hexdigest(),
            "instructionsCharacters": len(instructions),
            "representativeBriefId": "waterCycleV1",
            "representativeBriefCharacters": len(_BRIEF),
            "interactionGoalPresent": True,
            "runCount": _RUN_COUNT,
        },
        "runs": results,
    }
    print(json.dumps({"finalReport": report}, indent=2, sort_keys=True))
    return 0


def _terminal_response(terminal_type: str) -> object:
    status = terminal_type.removeprefix("response.")
    output: list[object] = []
    if terminal_type == "response.completed":
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600">'
            "<title>Diagnostic</title><desc>Terminal contract check.</desc>"
            '<circle id="shape" cx="500" cy="300" r="100" fill="#abcdef"/>'
            "</svg>"
        )
        output.append(
            SimpleNamespace(
                type="message",
                role="assistant",
                status="completed",
                content=[SimpleNamespace(type="output_text", text=svg)],
            )
        )
    return SimpleNamespace(
        id=f"resp-terminal-contract-{status}",
        status=status,
        output=output,
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=20,
            input_tokens_details=SimpleNamespace(
                cached_tokens=0,
                cache_write_tokens=0,
            ),
            output_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
    )


async def _verify_terminal_contract() -> int:
    results: list[dict[str, object]] = []
    for terminal_type in (
        "response.completed",
        "response.incomplete",
        "response.failed",
    ):
        next_event_requested = asyncio.Event()
        response = _terminal_response(terminal_type)

        class _TerminalThenSuspendedStream:
            def __init__(self) -> None:
                self._terminal_pending = True
                self._release = asyncio.Event()
                self.close_calls = 0

            def __aiter__(self) -> _TerminalThenSuspendedStream:
                return self

            async def __anext__(self) -> object:
                if self._terminal_pending:
                    self._terminal_pending = False
                    return SimpleNamespace(type=terminal_type, response=response)
                next_event_requested.set()
                await self._release.wait()
                raise StopAsyncIteration

            async def close(self) -> None:
                self.close_calls += 1
                self._release.set()

        stream = _TerminalThenSuspendedStream()

        class _Responses:
            async def create(self, **arguments: object) -> object:
                if arguments.get("stream") is not True:
                    raise RuntimeError("terminal contract check requires streaming")
                return stream

        provider = OpenAISvgCanvasSpecialist(
            Settings(
                environment=AvaEnvironment.TEST,
                openai_canvas_max_quality_repairs=0,
            ),
            session_id=f"session-terminal-contract-{terminal_type}",
            safety_identifier="ava_terminal_contract_check",
            client=SimpleNamespace(responses=_Responses()),  # type: ignore[arg-type]
        )
        request = SvgCanvasSpecialistRequest.model_validate(
            {
                "generationId": f"generation-terminal-{terminal_type}",
                "sourceTurnId": f"turn-terminal-{terminal_type}",
                "brief": _BRIEF,
                "interactionGoal": _INTERACTION_GOAL,
                "ageBand": "middleChildhood",
            }
        )
        task = asyncio.create_task(provider.generate(request))
        next_event_task = asyncio.create_task(next_event_requested.wait())
        done, _ = await asyncio.wait(
            {task, next_event_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        requested_another_event = next_event_task in done
        if requested_another_event:
            await stream.close()
        await asyncio.gather(task, return_exceptions=True)
        next_event_task.cancel()
        await asyncio.gather(next_event_task, return_exceptions=True)
        results.append(
            {
                "terminalType": terminal_type,
                "requestedAnotherEventAfterTerminal": requested_another_event,
                "providerTaskDone": task.done(),
                "streamCloseCalls": stream.close_calls,
            }
        )

    print(json.dumps({"terminalContract": results}, indent=2, sort_keys=True))
    failed = any(
        result["requestedAnotherEventAfterTerminal"]
        or not result["providerTaskDone"]
        or result["streamCloseCalls"] != 1
        for result in results
    )
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run five paid, content-free diagnostics against Ava's exact Sol SVG "
            "stream request configuration."
        )
    )
    parser.add_argument("--allow-spend", action="store_true")
    parser.add_argument("--terminal-contract-only", action="store_true")
    arguments = parser.parse_args()
    if arguments.terminal_contract_only:
        raise SystemExit(asyncio.run(_verify_terminal_contract()))
    raise SystemExit(asyncio.run(_run(allow_spend=arguments.allow_spend)))


if __name__ == "__main__":
    main()
