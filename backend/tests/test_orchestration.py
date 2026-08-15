from __future__ import annotations

from pydantic import SecretStr

from ava_backend.config import AvaEnvironment, Settings
from ava_backend.orchestration import (
    AvaOrchestrator,
    AvaPerformanceOrchestrator,
)
from ava_backend.protocol import (
    AvaPerformancePlan,
    AvaPerformanceSegment,
    AvaResponse,
    LessonTurnRequest,
)
from ava_backend.providers.base import ModelAvaResult, PerformanceModelResult


class _CapturingModel:
    def __init__(self) -> None:
        self.arguments: dict[str, object] = {}

    async def generate_response(self, **arguments: object) -> ModelAvaResult:
        self.arguments = arguments
        return ModelAvaResult(
            response=AvaResponse(
                speech="Four equal pieces make quarters.",
                expression="encouraging",
                gesture="explain",
                diagram=None,
            ),
            provider_request_id="provider-123",
            input_tokens=100,
            output_tokens=30,
        )


class _CapturingPerformanceModel:
    def __init__(self) -> None:
        self.arguments: dict[str, object] = {}

    async def generate_performance(
        self,
        **arguments: object,
    ) -> PerformanceModelResult:
        self.arguments = arguments
        return PerformanceModelResult(
            performance=AvaPerformancePlan(
                segments=[
                    AvaPerformanceSegment(
                        id="reply",
                        speech="Four equal pieces make quarters.",
                        delivery="encouraging",
                        expression="encouraging",
                        gaze="child",
                        gesture="none",
                        diagram_cue=None,
                    )
                ]
            ),
            provider_request_id="provider-v2",
            input_tokens=90,
            output_tokens=25,
        )


async def test_orchestrator_hashes_client_identity_and_returns_protocol_v1() -> None:
    model = _CapturingModel()
    settings = Settings(
        environment=AvaEnvironment.TEST,
        safety_hash_secret=SecretStr("a" * 32),
    )
    orchestrator = AvaOrchestrator(model, settings)

    outcome = await orchestrator.create_turn(
        LessonTurnRequest(
            child_name="Mia",
            client_instance_id="device-1234",
            message="What is a quarter?",
        )
    )

    assert outcome.response.protocol_version == 1
    assert outcome.response.lesson.domain == "general"
    assert outcome.included_history_messages == 0
    assert outcome.omitted_history_messages == 0
    assert model.arguments["safety_id"] != "device-1234"
    assert len(model.arguments["safety_id"]) == 64
    assert "Mia" not in model.arguments["instructions"]


async def test_performance_orchestrator_uses_bounded_v2_harness() -> None:
    model = _CapturingPerformanceModel()
    settings = Settings(
        environment=AvaEnvironment.TEST,
        safety_hash_secret=SecretStr("b" * 32),
    )
    orchestrator = AvaPerformanceOrchestrator(model, settings)

    outcome = await orchestrator.create_performance(
        LessonTurnRequest(
            child_name="Mia",
            client_instance_id="device-1234",
            message="What is a quarter?",
        )
    )

    assert outcome.performance.speech == "Four equal pieces make quarters."
    assert outcome.provider_request_id == "provider-v2"
    assert model.arguments["safety_id"] != "device-1234"
    assert '<ava_prompt version="ava-performance-v1">' in str(
        model.arguments["instructions"]
    )
    assert "AvaPerformancePlan" in str(model.arguments["instructions"])
    assert "Mia" not in str(model.arguments["model_input"])
