from __future__ import annotations

from types import SimpleNamespace

import pytest

from ava_backend.config import AvaEnvironment, Settings
from ava_backend.errors import ProviderRefusalError, ProviderResponseError
from ava_backend.protocol import (
    AvaPerformancePlan,
    AvaPerformanceSegment,
    AvaResponse,
)
from ava_backend.providers.openai_provider import OpenAIAvaModel


class _Responses:
    def __init__(self, response: object) -> None:
        self.response = response
        self.arguments: dict[str, object] = {}

    async def parse(self, **arguments: object) -> object:
        self.arguments = arguments
        return self.response


class _Client:
    def __init__(self, response: object) -> None:
        self.responses = _Responses(response)


def _settings() -> Settings:
    return Settings(environment=AvaEnvironment.TEST)


async def test_openai_provider_uses_structured_responses_without_storage() -> None:
    parsed = AvaResponse(
        speech="A quarter is one of four equal parts.",
        expression="encouraging",
        gesture="explain",
        diagram=None,
    )
    response = SimpleNamespace(
        output_parsed=parsed,
        output=[],
        id="resp-123",
        _request_id="request-123",
        usage=SimpleNamespace(input_tokens=80, output_tokens=20),
    )
    client = _Client(response)
    provider = OpenAIAvaModel(_settings(), client=client)

    result = await provider.generate_response(
        instructions="stable harness",
        model_input=[{"role": "user", "content": "question"}],
        safety_id="hashed-child-id",
    )

    assert result.response == parsed
    assert result.provider_request_id == "request-123"
    assert client.responses.arguments["model"] == "gpt-5.6-terra"
    assert client.responses.arguments["text_format"] is AvaResponse
    assert client.responses.arguments["store"] is False
    assert client.responses.arguments["reasoning"] == {"effort": "low"}
    assert client.responses.arguments["safety_identifier"] == "hashed-child-id"


async def test_openai_provider_generates_protocol_v2_performance_plan() -> None:
    parsed = AvaPerformancePlan(
        segments=[
            AvaPerformanceSegment(
                id="reply",
                speech="Hello! It is nice to see you.",
                delivery="warm",
                expression="warmSmile",
                gaze="child",
                gesture="none",
                diagram_cue=None,
            )
        ]
    )
    response = SimpleNamespace(
        output_parsed=parsed,
        output=[],
        id="resp-v2",
        _request_id="request-v2",
        usage=SimpleNamespace(input_tokens=70, output_tokens=18),
    )
    client = _Client(response)
    provider = OpenAIAvaModel(_settings(), client=client)

    result = await provider.generate_performance(
        instructions="performance harness",
        model_input=[{"role": "user", "content": "hello"}],
        safety_id=None,
    )

    assert result.performance == parsed
    assert result.provider_request_id == "request-v2"
    assert client.responses.arguments["text_format"] is AvaPerformancePlan
    assert client.responses.arguments["metadata"] == {
        "ava_protocol_version": "2",
        "ava_prompt_version": "ava-performance-v1",
    }
    assert client.responses.arguments["prompt_cache_key"] == "ava-performance-v1"


async def test_openai_provider_surfaces_refusal_and_missing_output() -> None:
    refusal = SimpleNamespace(
        output_parsed=None,
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="refusal")],
            )
        ],
    )
    with pytest.raises(ProviderRefusalError):
        await OpenAIAvaModel(
            _settings(),
            client=_Client(refusal),
        ).generate_response(
            instructions="harness",
            model_input=[],
            safety_id=None,
        )

    invalid = SimpleNamespace(output_parsed=None, output=[])
    with pytest.raises(ProviderResponseError):
        await OpenAIAvaModel(
            _settings(),
            client=_Client(invalid),
        ).generate_response(
            instructions="harness",
            model_input=[],
            safety_id=None,
        )
