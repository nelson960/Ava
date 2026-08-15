from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .context import ContextLimits, build_model_context
from .prompts import build_instructions, build_performance_instructions
from .protocol import (
    AvaPerformancePlan,
    LessonTurnRequest,
    LessonTurnResponse,
    adapt_ava_response_to_protocol_v1,
)
from .providers.base import AvaModel, AvaPerformanceModel
from .security import safety_identifier


@dataclass(frozen=True, slots=True)
class AvaTurnOutcome:
    response: LessonTurnResponse
    provider_request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    included_history_messages: int
    omitted_history_messages: int


@dataclass(frozen=True, slots=True)
class AvaPerformanceTurnOutcome:
    performance: AvaPerformancePlan
    provider_request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    included_history_messages: int
    omitted_history_messages: int


class AvaOrchestrator:
    def __init__(self, model: AvaModel, settings: Settings) -> None:
        self._model = model
        self._settings = settings
        self._instructions = build_instructions()
        self._context_limits = ContextLimits(
            max_history_messages=settings.context_history_max_messages,
            max_history_characters=settings.context_history_max_characters,
        )

    async def create_turn(self, request: LessonTurnRequest) -> AvaTurnOutcome:
        safety_id: str | None = None
        if request.client_instance_id is not None:
            configured_secret = self._settings.safety_hash_secret
            if configured_secret is not None:
                safety_id = safety_identifier(
                    request.client_instance_id,
                    configured_secret.get_secret_value(),
                )

        context = build_model_context(request, self._context_limits)
        result = await self._model.generate_response(
            instructions=self._instructions,
            model_input=context.to_provider_input(),
            safety_id=safety_id,
        )
        return AvaTurnOutcome(
            response=adapt_ava_response_to_protocol_v1(result.response),
            provider_request_id=result.provider_request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            included_history_messages=context.included_history_messages,
            omitted_history_messages=context.omitted_history_messages,
        )


class AvaPerformanceOrchestrator:
    """Builds one bounded semantic performance plan for protocol v2."""

    def __init__(self, model: AvaPerformanceModel, settings: Settings) -> None:
        self._model = model
        self._settings = settings
        self._instructions = build_performance_instructions()
        self._context_limits = ContextLimits(
            max_history_messages=settings.context_history_max_messages,
            max_history_characters=settings.context_history_max_characters,
        )

    async def create_performance(
        self,
        request: LessonTurnRequest,
    ) -> AvaPerformanceTurnOutcome:
        safety_id: str | None = None
        if request.client_instance_id is not None:
            configured_secret = self._settings.safety_hash_secret
            if configured_secret is not None:
                safety_id = safety_identifier(
                    request.client_instance_id,
                    configured_secret.get_secret_value(),
                )

        context = build_model_context(request, self._context_limits)
        result = await self._model.generate_performance(
            instructions=self._instructions,
            model_input=context.to_provider_input(),
            safety_id=safety_id,
        )
        return AvaPerformanceTurnOutcome(
            performance=AvaPerformancePlan.model_validate(
                result.performance.model_dump()
            ),
            provider_request_id=result.provider_request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            included_history_messages=context.included_history_messages,
            omitted_history_messages=context.omitted_history_messages,
        )
