from __future__ import annotations

from typing import Any, TypeVar, cast

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from pydantic import BaseModel

from ..config import Settings
from ..errors import (
    ProviderRefusalError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from ..prompts import PERFORMANCE_PROMPT_VERSION, PROMPT_VERSION
from ..protocol import AvaPerformancePlan, AvaResponse
from .base import ModelAvaResult, PerformanceModelResult

StructuredResponse = TypeVar("StructuredResponse", bound=BaseModel)


class OpenAIAvaModel:
    def __init__(
        self,
        settings: Settings,
        *,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or AsyncOpenAI(
            api_key=settings.require_openai_api_key(),
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )

    async def generate_response(
        self,
        *,
        instructions: str,
        model_input: list[dict[str, str]],
        safety_id: str | None,
    ) -> ModelAvaResult:
        parsed, response = await self._generate_structured(
            instructions=instructions,
            model_input=model_input,
            safety_id=safety_id,
            text_format=AvaResponse,
            protocol_version="1",
            prompt_version=PROMPT_VERSION,
        )
        usage = getattr(response, "usage", None)
        return ModelAvaResult(
            response=parsed,
            provider_request_id=_provider_request_id(response),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )

    async def generate_performance(
        self,
        *,
        instructions: str,
        model_input: list[dict[str, str]],
        safety_id: str | None,
    ) -> PerformanceModelResult:
        parsed, response = await self._generate_structured(
            instructions=instructions,
            model_input=model_input,
            safety_id=safety_id,
            text_format=AvaPerformancePlan,
            protocol_version="2",
            prompt_version=PERFORMANCE_PROMPT_VERSION,
        )
        usage = getattr(response, "usage", None)
        return PerformanceModelResult(
            performance=parsed,
            provider_request_id=_provider_request_id(response),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )

    async def _generate_structured(
        self,
        *,
        instructions: str,
        model_input: list[dict[str, str]],
        safety_id: str | None,
        text_format: type[StructuredResponse],
        protocol_version: str,
        prompt_version: str,
    ) -> tuple[StructuredResponse, object]:
        arguments: dict[str, Any] = {
            "model": self._settings.openai_model,
            "instructions": instructions,
            "input": model_input,
            "text_format": text_format,
            "reasoning": {"effort": self._settings.openai_reasoning_effort},
            "verbosity": "low",
            "max_output_tokens": self._settings.openai_max_output_tokens,
            "store": False,
            "prompt_cache_key": prompt_version,
            "metadata": {
                "ava_protocol_version": protocol_version,
                "ava_prompt_version": prompt_version,
            },
        }
        if safety_id is not None:
            arguments["safety_identifier"] = safety_id

        try:
            response = await self._client.responses.parse(**arguments)
        except APITimeoutError as error:
            raise ProviderTimeoutError() from error
        except RateLimitError as error:
            raise ProviderUnavailableError() from error
        except APIConnectionError as error:
            raise ProviderUnavailableError() from error
        except APIStatusError as error:
            raise ProviderUnavailableError() from error
        except (TypeError, ValueError) as error:
            raise ProviderResponseError() from error

        parsed = cast(StructuredResponse | None, response.output_parsed)
        if parsed is None:
            if _contains_refusal(response):
                raise ProviderRefusalError()
            raise ProviderResponseError()
        return parsed, response


def _provider_request_id(response: object) -> str | None:
    return getattr(response, "_request_id", None) or getattr(response, "id", None)


def _contains_refusal(response: object) -> bool:
    for output in getattr(response, "output", ()):
        if getattr(output, "type", None) != "message":
            continue
        for content in getattr(output, "content", ()):
            if getattr(content, "type", None) == "refusal":
                return True
    return False
