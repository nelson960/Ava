from __future__ import annotations

import asyncio
from collections import deque
import logging
from types import SimpleNamespace

import pytest

from ava_backend.canvas.svg_specialist import (
    SvgCanvasSpecialistGenerationLimitError,
    SvgCanvasSpecialistInvalidError,
    SvgCanvasSpecialistRequest,
    SvgCanvasSpecialistUnavailableError,
)
from ava_backend.config import AvaEnvironment, Settings
from ava_backend.providers.openai_svg_canvas_provider import (
    OpenAISvgCanvasSpecialist,
    extract_completed_svg_output_text,
)


def _svg(*, label: str = "Cloud", interaction_mode: str = "select") -> str:
    if interaction_mode in {"select", "explore"}:
        binding = (
        '<ellipse id="cloud" data-ava-binding="select_cloud" aria-label="Cloud" '
        'cx="500" cy="250" rx="160" ry="70" fill="#dff5ff"/>\n'
        )
    elif interaction_mode == "steps":
        binding = (
            '<g id="cloud_1" data-ava-binding="cloud_1" data-ava-step="1" '
            'aria-label="Cloud begins"><circle cx="400" cy="250" r="50"/></g>\n'
            '<g id="cloud_2" data-ava-binding="cloud_2" data-ava-step="2" '
            'aria-label="Cloud grows"><circle cx="600" cy="250" r="70"/></g>\n'
        )
    else:
        binding = (
            '<ellipse id="cloud" cx="500" cy="250" rx="160" ry="70" '
            'fill="#dff5ff"/>\n'
        )
    return (
        "\n"
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
        f'data-ava-interaction-mode="{interaction_mode}">\n'
        f"<title>{label} ☁️</title>\n"
        "<desc>A soft cloud floating over a blue landscape.</desc>\n"
        f"{binding}"
        "</svg>\n"
    )


def _response(svg: str, *, request_id: str = "resp_123") -> object:
    return SimpleNamespace(
        id=request_id,
        status="completed",
        output=[
            SimpleNamespace(type="reasoning", summary=[]),
            SimpleNamespace(
                type="message",
                role="assistant",
                status="completed",
                content=[SimpleNamespace(type="output_text", text=svg)],
            ),
        ],
        usage=SimpleNamespace(
            input_tokens=120,
            output_tokens=240,
            input_tokens_details=SimpleNamespace(
                cached_tokens=12,
                cache_write_tokens=3,
            ),
            output_tokens_details=SimpleNamespace(reasoning_tokens=7),
        ),
    )


def _incomplete_response(
    *,
    reason: str,
    request_id: str = "resp_incomplete",
) -> object:
    return SimpleNamespace(
        id=request_id,
        status="incomplete",
        incomplete_details=SimpleNamespace(reason=reason),
        output=[],
        usage=SimpleNamespace(
            input_tokens=120,
            output_tokens=4096,
            input_tokens_details=SimpleNamespace(
                cached_tokens=12,
                cache_write_tokens=3,
            ),
            output_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
    )


class _ResponseStream:
    def __init__(
        self,
        response: object,
        *,
        fragments: list[str] | None = None,
        terminal_type: str = "response.completed",
    ) -> None:
        self._response = response
        self._fragments = fragments
        self._terminal_type = terminal_type
        self.close_calls = 0

    async def __aiter__(self):
        yield SimpleNamespace(
            type="response.created",
            response=SimpleNamespace(id=getattr(self._response, "id", None)),
        )
        fragments = self._fragments
        if fragments is None:
            fragments = [extract_completed_svg_output_text(self._response)]
        for fragment in fragments:
            yield SimpleNamespace(
                type="response.output_text.delta",
                delta=fragment,
            )
        yield SimpleNamespace(
            type=self._terminal_type,
            response=self._response,
        )

    async def close(self) -> None:
        self.close_calls += 1


class _TerminalWithoutEofStream:
    def __init__(
        self,
        response: object,
        *,
        fragments: list[str],
        terminal_type: str,
    ) -> None:
        self._events = deque(
            [
                *(
                    SimpleNamespace(type="response.output_text.delta", delta=fragment)
                    for fragment in fragments
                ),
                SimpleNamespace(type=terminal_type, response=response),
            ]
        )
        self._terminal_type = terminal_type
        self._terminal_yielded = False
        self._release_after_terminal = asyncio.Event()
        self.requested_after_terminal = asyncio.Event()
        self.closed = asyncio.Event()
        self.anext_calls = 0
        self.close_calls = 0

    def __aiter__(self) -> _TerminalWithoutEofStream:
        return self

    async def __anext__(self) -> object:
        self.anext_calls += 1
        if self._events:
            event = self._events.popleft()
            if event.type == self._terminal_type:
                self._terminal_yielded = True
            return event
        if not self._terminal_yielded:  # pragma: no cover - construction invariant
            raise AssertionError("terminal stream exhausted before its terminal event")
        self.requested_after_terminal.set()
        await self._release_after_terminal.wait()
        raise StopAsyncIteration

    async def close(self) -> None:
        self.close_calls += 1
        self.closed.set()
        self._release_after_terminal.set()

    def release_after_terminal_for_test(self) -> None:
        self._release_after_terminal.set()


class _SuspendedBeforeTerminalStream:
    def __init__(self) -> None:
        self.next_event_requested = asyncio.Event()
        self._release = asyncio.Event()
        self.closed = asyncio.Event()
        self.anext_calls = 0
        self.close_calls = 0

    def __aiter__(self) -> _SuspendedBeforeTerminalStream:
        return self

    async def __anext__(self) -> object:
        self.anext_calls += 1
        self.next_event_requested.set()
        await self._release.wait()
        raise StopAsyncIteration

    async def close(self) -> None:
        self.close_calls += 1
        self.closed.set()
        self._release.set()


class _Responses:
    def __init__(self, responses: list[object]) -> None:
        self._responses = iter(responses)
        self.arguments: list[dict[str, object]] = []

    async def create(self, **arguments: object) -> object:
        self.arguments.append(arguments)
        response = next(self._responses)
        if arguments.get("stream") is True:
            return _ResponseStream(response)
        return response


class _Client:
    def __init__(self, responses: list[object]) -> None:
        self.responses = _Responses(responses)


class _ClientWithStream:
    def __init__(self, stream: object) -> None:
        self.responses = self._Responses(stream)

    class _Responses:
        def __init__(self, stream: object) -> None:
            self._stream = stream

        async def create(self, **arguments: object) -> object:
            assert arguments["stream"] is True
            return self._stream


def _request(**changes: object) -> SvgCanvasSpecialistRequest:
    payload: dict[str, object] = {
        "generationId": "generation_1",
        "sourceTurnId": "turn_1",
        "brief": "Show how a cloud forms.",
        "interactionMode": "select",
        "interactionGoal": "Let the child select the cloud.",
        "ageBand": "middleChildhood",
    }
    payload.update(changes)
    return SvgCanvasSpecialistRequest.model_validate(payload)


async def _assert_task_stops_at_terminal(
    task: asyncio.Task[object],
    stream: _TerminalWithoutEofStream,
) -> None:
    requested_after_terminal = asyncio.create_task(
        stream.requested_after_terminal.wait()
    )
    try:
        done, _ = await asyncio.wait(
            {task, requested_after_terminal},
            return_when=asyncio.FIRST_COMPLETED,
        )
        incorrectly_requested_more = requested_after_terminal in done
        if incorrectly_requested_more:
            stream.release_after_terminal_for_test()
            await asyncio.wait({task})
        assert not incorrectly_requested_more, (
            "provider requested another stream event after a terminal response"
        )
    finally:
        requested_after_terminal.cancel()
        await asyncio.gather(requested_after_terminal, return_exceptions=True)


async def test_provider_uses_sol_raw_responses_and_preserves_exact_svg() -> None:
    svg = _svg(label="Café cloud")
    client = _Client([_response(svg)])
    provider = OpenAISvgCanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST, openai_canvas_max_quality_repairs=0),
        session_id="session_test_1",
        safety_identifier="ava_test_user_123",
        client=client,  # type: ignore[arg-type]
    )

    generation = await provider.generate(_request())

    assert generation.document.svg_text == svg
    assert generation.document.svg_bytes == svg.encode("utf-8")
    assert generation.provider_request_id == "resp_123"
    assert generation.input_tokens == 120
    assert generation.output_tokens == 240
    assert generation.cached_input_tokens == 12
    arguments = client.responses.arguments[0]
    assert arguments["model"] == "gpt-5.6-sol"
    assert arguments["stream"] is True
    assert "text_format" not in arguments
    assert arguments["metadata"]["ava_protocol_version"] == "4"
    assert arguments["metadata"]["ava_provider_role"] == "svg_canvas_specialist"
    instructions = arguments["instructions"]
    assert isinstance(instructions, str)
    assert (
        "Produce the smallest complete SVG that clearly teaches the requested concept."
        in instructions
    )
    assert "Target no more than 7,000 SVG characters" in instructions
    assert "Never truncate generated SVG" in instructions


async def test_absent_interaction_goal_prefers_a_static_svg() -> None:
    svg = _svg(label="Static cloud", interaction_mode="none")
    client = _Client([_response(svg)])
    provider = OpenAISvgCanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST, openai_canvas_max_quality_repairs=0),
        session_id="session_static",
        safety_identifier="ava_test_user_static",
        client=client,  # type: ignore[arg-type]
    )

    generation = await provider.generate(
        _request(interactionMode="none", interactionGoal=None)
    )

    assert generation.document.bindings == {}
    instructions = client.responses.arguments[0]["instructions"]
    assert isinstance(instructions, str)
    assert "`none` is static" in instructions


async def test_explicit_interaction_goal_allows_only_goal_serving_bindings() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
        'data-ava-interaction-mode="select">'
        "<title>Cloud</title><desc>Select the cloud to discuss rain.</desc>"
        '<g id="cloud" data-ava-binding="select_cloud" '
        'aria-label="Rain cloud">'
        '<ellipse cx="500" cy="250" rx="160" ry="70"/></g></svg>'
    )
    client = _Client([_response(svg)])
    provider = OpenAISvgCanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST, openai_canvas_max_quality_repairs=0),
        session_id="session_interactive",
        safety_identifier="ava_test_user_interactive",
        client=client,  # type: ignore[arg-type]
    )

    generation = await provider.generate(
        _request(interactionGoal="Let the child select the cloud.")
    )

    assert tuple(generation.document.bindings) == ("select_cloud",)
    instructions = client.responses.arguments[0]["instructions"]
    assert isinstance(instructions, str)
    assert "`select` uses 1-32 visible choices" in instructions


@pytest.mark.parametrize(
    ("requested_mode", "returned_mode", "goal"),
    [
        ("none", "select", None),
        ("explore", "none", "Let the child explore the cloud."),
        ("select", "explore", "Let the child select the cloud."),
        ("steps", "select", "Let the child move through cloud stages."),
    ],
)
async def test_requested_interaction_mode_must_match_validated_svg(
    requested_mode: str,
    returned_mode: str,
    goal: str | None,
) -> None:
    client = _Client([_response(_svg(interaction_mode=returned_mode))])
    provider = OpenAISvgCanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST, openai_canvas_max_quality_repairs=0),
        session_id="session_mode_mismatch",
        safety_identifier="ava_test_user_mode_mismatch",
        client=client,  # type: ignore[arg-type]
    )

    with pytest.raises(SvgCanvasSpecialistInvalidError, match="interactionModeMismatch"):
        await provider.generate(
            _request(interactionMode=requested_mode, interactionGoal=goal)
        )


async def test_interaction_mode_mismatch_uses_existing_quality_repair() -> None:
    client = _Client(
        [
            _response(_svg(interaction_mode="none"), request_id="resp_bad_mode"),
            _response(
                _svg(interaction_mode="explore"),
                request_id="resp_repaired_mode",
            ),
        ]
    )
    provider = OpenAISvgCanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST, openai_canvas_max_quality_repairs=1),
        session_id="session_mode_repair",
        safety_identifier="ava_test_user_mode_repair",
        client=client,  # type: ignore[arg-type]
    )

    generation = await provider.generate(
        _request(
            interactionMode="explore",
            interactionGoal="Let the child explore the cloud.",
        )
    )

    assert generation.document.interaction_mode == "explore"
    assert len(client.responses.arguments) == 2
    repair_input = client.responses.arguments[1]["input"]
    assert "interactionModeMismatch" in str(repair_input)


def test_exact_extractor_allows_reasoning_but_does_not_join_text() -> None:
    svg = _svg()
    response = _response(svg)

    assert extract_completed_svg_output_text(response) == svg


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(status="incomplete", output=[]),
        SimpleNamespace(status="completed", output=[]),
        SimpleNamespace(
            status="completed",
            output=[SimpleNamespace(type="tool_call")],
        ),
        SimpleNamespace(
            status="completed",
            output=[
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    status="in_progress",
                    content=[SimpleNamespace(type="output_text", text=_svg())],
                )
            ],
        ),
        SimpleNamespace(
            status="completed",
            output=[
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[
                        SimpleNamespace(type="output_text", text=_svg()),
                        SimpleNamespace(type="output_text", text="extra"),
                    ],
                )
            ],
        ),
        SimpleNamespace(
            status="completed",
            output=[
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[SimpleNamespace(type="refusal", refusal="no")],
                )
            ],
        ),
    ],
)
def test_exact_extractor_rejects_every_noncanonical_envelope(response: object) -> None:
    with pytest.raises(ValueError):
        extract_completed_svg_output_text(response)


async def test_provider_permits_one_deterministic_repair() -> None:
    invalid = _svg().replace(
        '<ellipse id="cloud"',
        '<ellipse id="cloud" foo="bar"',
    )
    valid = _svg(label="Repaired cloud")
    client = _Client([_response(invalid), _response(valid, request_id="resp_456")])
    provider = OpenAISvgCanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST, openai_canvas_max_quality_repairs=1),
        session_id="session_test_1",
        safety_identifier="ava_test_user_123",
        client=client,  # type: ignore[arg-type]
    )

    generation = await provider.generate(_request())

    assert generation.document.svg_text == valid
    assert generation.provider_request_id == "resp_456"
    assert generation.input_tokens == 240
    assert len(client.responses.arguments) == 2
    repair_input = client.responses.arguments[1]["input"][0]["content"][0]["text"]
    assert 'code="attributeForbidden"' in repair_input
    assert (
        "<validation_detail>SVG attribute &#x27;foo&#x27; is not "
        "allowed</validation_detail>"
    ) in repair_input
    assert "foo=&quot;bar&quot;" in repair_input


async def test_generation_deadline_bounds_original_and_repair_together() -> None:
    invalid = _svg().replace("<ellipse", "<script")
    blocked = asyncio.Event()

    class _ResponsesWithBlockedRepair(_Responses):
        async def create(self, **arguments: object) -> object:
            self.arguments.append(arguments)
            if len(self.arguments) == 1:
                return _ResponseStream(_response(invalid))
            blocked.set()
            await asyncio.Future()

    settings = Settings(
        environment=AvaEnvironment.TEST,
        openai_canvas_max_quality_repairs=1,
    ).model_copy(update={"openai_canvas_timeout_seconds": 0.02})
    responses = _ResponsesWithBlockedRepair([])
    provider = OpenAISvgCanvasSpecialist(
        settings,
        session_id="session_test_deadline",
        safety_identifier="ava_test_user_123",
        client=SimpleNamespace(responses=responses),  # type: ignore[arg-type]
    )

    with pytest.raises(SvgCanvasSpecialistUnavailableError, match="deadline"):
        await asyncio.wait_for(provider.generate(_request()), timeout=0.25)

    assert blocked.is_set()


async def test_provider_rejects_invalid_document_without_silent_cleanup() -> None:
    invalid = _svg().replace("<ellipse", '<ellipse onload="alert(1)"')
    provider = OpenAISvgCanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST, openai_canvas_max_quality_repairs=0),
        session_id="session_test_1",
        safety_identifier="ava_test_user_123",
        client=_Client([_response(invalid)]),  # type: ignore[arg-type]
    )

    with pytest.raises(SvgCanvasSpecialistInvalidError, match="eventHandlerForbidden"):
        await provider.generate(_request())


async def test_current_svg_revision_context_preserves_whitespace() -> None:
    current_svg = _svg()
    request = _request(
        currentSceneId="scene_1",
        currentRevision=2,
        currentSvg=current_svg,
        currentSvgSha256="a" * 64,
    )

    assert request.current_svg == current_svg


async def test_cancel_is_idempotent_and_surfaces_as_unavailable() -> None:
    started = asyncio.Event()

    class _SlowResponses:
        async def create(self, **arguments: object) -> object:
            del arguments
            started.set()
            await asyncio.Future()

    client = SimpleNamespace(responses=_SlowResponses())
    provider = OpenAISvgCanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST),
        session_id="session_test_1",
        safety_identifier="ava_test_user_123",
        client=client,  # type: ignore[arg-type]
    )
    generation_task = asyncio.create_task(provider.generate(_request()))
    await started.wait()

    await provider.cancel(generation_id="generation_1")
    await provider.cancel(generation_id="generation_1")

    with pytest.raises(SvgCanvasSpecialistUnavailableError, match="cancelled"):
        await generation_task


async def test_streaming_observability_uses_only_terminal_exact_svg(
    caplog: pytest.LogCaptureFixture,
) -> None:
    svg = _svg(label="Measured café cloud")
    response = _response(svg)
    fragments = [svg[:17], svg[17:91], svg[91:]]

    class _StreamingResponses:
        def __init__(self) -> None:
            self.arguments: list[dict[str, object]] = []

        async def create(self, **arguments: object) -> object:
            self.arguments.append(arguments)
            return _ResponseStream(response, fragments=fragments)

    responses = _StreamingResponses()
    provider = OpenAISvgCanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST, openai_canvas_max_quality_repairs=0),
        session_id="session_test_telemetry",
        safety_identifier="ava_test_user_123",
        client=SimpleNamespace(responses=responses),  # type: ignore[arg-type]
    )

    with caplog.at_level(logging.INFO, logger="ava_backend.openai_svg_canvas"):
        generation = await provider.generate(_request())

    assert generation.document.svg_text == svg
    assert generation.document.svg_bytes == svg.encode("utf-8")
    assert responses.arguments[0]["stream"] is True
    telemetry = [
        record.ava_context
        for record in caplog.records
        if hasattr(record, "ava_context")
    ]
    assert [event["stage"] for event in telemetry] == [
        "attemptStarted",
        "firstOutputDelta",
        "attemptCompleted",
        "validationStarted",
        "validationCompleted",
        "generationCompleted",
    ]
    for event in telemetry:
        assert event["sessionId"] == "session_test_telemetry"
        assert event["generationId"] == "generation_1"
        assert event["sourceTurnId"] == "turn_1"
    completed = next(
        event for event in telemetry if event["stage"] == "attemptCompleted"
    )
    assert completed["inputTokens"] == 120
    assert completed["cachedInputTokens"] == 12
    assert completed["cacheWriteInputTokens"] == 3
    assert completed["outputTokens"] == 240
    assert completed["reasoningTokens"] == 7
    assert completed["outputDeltaCharacters"] == len(svg)
    assert svg not in caplog.text


async def test_completed_terminal_stops_without_eof_and_preserves_exact_svg() -> None:
    svg = _svg(label="Terminal café cloud")
    fragments = [svg[:13], svg[13:87], svg[87:]]
    stream = _TerminalWithoutEofStream(
        _response(svg),
        fragments=fragments,
        terminal_type="response.completed",
    )
    provider = OpenAISvgCanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST, openai_canvas_max_quality_repairs=0),
        session_id="session_terminal_completed",
        safety_identifier="ava_test_user_123",
        client=_ClientWithStream(stream),  # type: ignore[arg-type]
    )
    task = asyncio.create_task(provider.generate(_request()))

    await _assert_task_stops_at_terminal(task, stream)
    generation = await task

    assert generation.document.svg_text == svg
    assert generation.document.svg_bytes == svg.encode("utf-8")
    assert stream.anext_calls == len(fragments) + 1
    assert stream.close_calls == 1
    assert stream.closed.is_set()
    assert not stream.requested_after_terminal.is_set()


@pytest.mark.parametrize(
    ("terminal_type", "response_status", "failure_code"),
    [
        ("response.failed", "failed", "responseFailed"),
        ("response.incomplete", "incomplete", "responseIncomplete"),
    ],
)
async def test_noncompleted_terminal_stops_without_eof(
    terminal_type: str,
    response_status: str,
    failure_code: str,
) -> None:
    response = _response(_svg(), request_id=f"resp_{response_status}")
    response.status = response_status
    stream = _TerminalWithoutEofStream(
        response,
        fragments=[],
        terminal_type=terminal_type,
    )
    provider = OpenAISvgCanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST, openai_canvas_max_quality_repairs=0),
        session_id=f"session_terminal_{response_status}",
        safety_identifier="ava_test_user_123",
        client=_ClientWithStream(stream),  # type: ignore[arg-type]
    )
    task = asyncio.create_task(provider.generate(_request()))

    await _assert_task_stops_at_terminal(task, stream)
    with pytest.raises(SvgCanvasSpecialistInvalidError, match=failure_code):
        await task

    assert stream.anext_calls == 1
    assert stream.close_calls == 1
    assert stream.closed.is_set()
    assert not stream.requested_after_terminal.is_set()


async def test_max_output_tokens_incomplete_does_not_start_repair() -> None:
    partial_svg = _svg()[:-8]
    response = _incomplete_response(reason="max_output_tokens")
    stream = _TerminalWithoutEofStream(
        response,
        fragments=[partial_svg],
        terminal_type="response.incomplete",
    )

    class _CountingResponses:
        def __init__(self) -> None:
            self.create_calls = 0

        async def create(self, **arguments: object) -> object:
            assert arguments["stream"] is True
            self.create_calls += 1
            if self.create_calls > 1:
                raise AssertionError("token-limit incompletion must not start repair")
            return stream

    responses = _CountingResponses()
    provider = OpenAISvgCanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST, openai_canvas_max_quality_repairs=1),
        session_id="session_generation_limit",
        safety_identifier="ava_test_user_123",
        client=SimpleNamespace(responses=responses),  # type: ignore[arg-type]
    )
    task = asyncio.create_task(provider.generate(_request()))

    await _assert_task_stops_at_terminal(task, stream)
    with pytest.raises(SvgCanvasSpecialistGenerationLimitError) as caught:
        await task

    assert not isinstance(caught.value, SvgCanvasSpecialistUnavailableError)
    assert caught.value.provider_request_id == "resp_incomplete"
    assert responses.create_calls == 1
    assert stream.anext_calls == 2
    assert stream.close_calls == 1
    assert stream.closed.is_set()
    assert not stream.requested_after_terminal.is_set()


async def test_cancellation_before_terminal_closes_suspended_stream() -> None:
    stream = _SuspendedBeforeTerminalStream()
    provider = OpenAISvgCanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST),
        session_id="session_cancel_before_terminal",
        safety_identifier="ava_test_user_123",
        client=_ClientWithStream(stream),  # type: ignore[arg-type]
    )
    task = asyncio.create_task(provider.generate(_request()))
    await stream.next_event_requested.wait()

    await provider.cancel(generation_id="generation_1")
    await provider.cancel(generation_id="generation_1")

    with pytest.raises(SvgCanvasSpecialistUnavailableError, match="cancelled"):
        await task
    assert task.done()
    assert stream.close_calls == 1
    assert stream.closed.is_set()


async def test_deadline_before_terminal_closes_suspended_stream() -> None:
    stream = _SuspendedBeforeTerminalStream()
    settings = Settings(environment=AvaEnvironment.TEST).model_copy(
        update={"openai_canvas_timeout_seconds": 0.02}
    )
    provider = OpenAISvgCanvasSpecialist(
        settings,
        session_id="session_deadline_before_terminal",
        safety_identifier="ava_test_user_123",
        client=_ClientWithStream(stream),  # type: ignore[arg-type]
    )

    with pytest.raises(SvgCanvasSpecialistUnavailableError, match="deadline"):
        await provider.generate(_request())

    assert stream.anext_calls == 1
    assert stream.close_calls == 1
    assert stream.closed.is_set()


async def test_stream_must_complete_and_match_its_terminal_output() -> None:
    svg = _svg()
    response = _response(svg)

    class _StreamingResponses:
        def __init__(self, stream: _ResponseStream) -> None:
            self._stream = stream

        async def create(self, **arguments: object) -> object:
            assert arguments["stream"] is True
            return self._stream

    for stream in (
        _ResponseStream(
            SimpleNamespace(status="incomplete", id="resp_incomplete", output=[]),
            fragments=[svg[:20]],
            terminal_type="response.incomplete",
        ),
        _ResponseStream(response, fragments=[svg, "unexpected suffix"]),
    ):
        provider = OpenAISvgCanvasSpecialist(
            Settings(
                environment=AvaEnvironment.TEST,
                openai_canvas_max_quality_repairs=0,
            ),
            session_id="session_test_stream_failure",
            safety_identifier="ava_test_user_123",
            client=SimpleNamespace(  # type: ignore[arg-type]
                responses=_StreamingResponses(stream)
            ),
        )

        with pytest.raises(SvgCanvasSpecialistInvalidError):
            await provider.generate(_request())


async def test_unexpected_stream_event_is_invalid_not_provider_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _UnexpectedEventStream:
        async def close(self) -> None:
            return None

        async def __aiter__(self):
            yield SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp_protocol_error"),
            )
            yield SimpleNamespace(type="response.unrecognized.delta")

    class _StreamingResponses:
        async def create(self, **arguments: object) -> object:
            assert arguments["stream"] is True
            return _UnexpectedEventStream()

    provider = OpenAISvgCanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST, openai_canvas_max_quality_repairs=0),
        session_id="session_test_protocol_error",
        safety_identifier="ava_test_user_123",
        client=SimpleNamespace(  # type: ignore[arg-type]
            responses=_StreamingResponses()
        ),
    )

    with caplog.at_level(logging.INFO, logger="ava_backend.openai_svg_canvas"):
        with pytest.raises(SvgCanvasSpecialistInvalidError) as caught:
            await provider.generate(_request())

    assert not isinstance(caught.value, SvgCanvasSpecialistUnavailableError)
    assert caught.value.provider_request_id == "resp_protocol_error"
    failed = next(
        record.ava_context
        for record in caplog.records
        if hasattr(record, "ava_context")
        and record.ava_context["stage"] == "generationFailed"
    )
    assert failed["failureCode"] == "unexpectedStreamEvent"
    assert failed["failureKind"] == "responseEnvelope"
    assert failed["providerRequestId"] == "resp_protocol_error"
