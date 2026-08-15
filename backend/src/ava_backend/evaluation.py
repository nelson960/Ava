from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .protocol import (
    AgeBand,
    AvaGesture,
    AvaResponse,
    ConversationMessage,
    NonEmptyText,
    ProtocolModel,
)


class EvaluationCategory(StrEnum):
    CASUAL = "casual"
    EXPLANATION = "explanation"
    TOPIC_CHANGE = "topicChange"
    EMOTIONAL = "emotional"
    SAFETY = "safety"
    PRIVACY = "privacy"
    ADVERSARIAL = "adversarial"
    DIAGRAM = "diagram"


class DiagramExpectation(StrEnum):
    FORBIDDEN = "forbidden"
    OPTIONAL = "optional"
    REQUIRED = "required"


class ConversationEvalExpectations(ProtocolModel):
    diagram: DiagramExpectation
    maximum_words: int = Field(ge=8, le=240)
    forbidden_phrases: list[
        Annotated[NonEmptyText, StringConstraints(max_length=120)]
    ] = Field(default_factory=list, max_length=12)
    human_review: list[Annotated[NonEmptyText, StringConstraints(max_length=280)]] = (
        Field(min_length=1, max_length=8)
    )


class ConversationEvalCase(ProtocolModel):
    id: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=3,
            max_length=80,
            pattern=r"^[a-z0-9_]+$",
        ),
    ]
    category: EvaluationCategory
    age_band: AgeBand
    message: Annotated[NonEmptyText, StringConstraints(max_length=2000)]
    recent_messages: list[ConversationMessage] = Field(
        default_factory=list,
        max_length=8,
    )
    expectations: ConversationEvalExpectations


class ConversationEvalSuite(ProtocolModel):
    schema_version: Literal[1]
    prompt_version: Annotated[NonEmptyText, StringConstraints(max_length=80)]
    cases: list[ConversationEvalCase] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> ConversationEvalSuite:
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("conversation evaluation case IDs must be unique")
        return self


class DeterministicEvalResult(ProtocolModel):
    passed: bool
    failures: list[str]


def load_conversation_eval_suite(
    path: Path,
    *,
    expected_prompt_version: str,
) -> ConversationEvalSuite:
    suite = ConversationEvalSuite.model_validate_json(path.read_text(encoding="utf-8"))
    if suite.prompt_version != expected_prompt_version:
        raise ValueError(
            "evaluation suite promptVersion does not match the active prompt: "
            f"{suite.prompt_version!r} != {expected_prompt_version!r}"
        )
    return suite


def evaluate_deterministic_constraints(
    case: ConversationEvalCase,
    response: AvaResponse,
) -> DeterministicEvalResult:
    failures: list[str] = []
    word_count = len(response.speech.split())
    if word_count > case.expectations.maximum_words:
        failures.append(
            f"speech has {word_count} words; maximum is "
            f"{case.expectations.maximum_words}"
        )

    normalized_speech = response.speech.casefold()
    for phrase in case.expectations.forbidden_phrases:
        if phrase.casefold() in normalized_speech:
            failures.append(f"speech contains forbidden phrase {phrase!r}")

    diagram_expectation = case.expectations.diagram
    if diagram_expectation == DiagramExpectation.FORBIDDEN:
        if response.diagram is not None:
            failures.append("response included a diagram when diagrams are forbidden")
    elif diagram_expectation == DiagramExpectation.REQUIRED:
        if response.diagram is None:
            failures.append("response omitted a required diagram")

    if response.diagram is None and response.gesture == AvaGesture.POINT_AT_DASHBOARD:
        failures.append("pointAtDashboard gesture requires a diagram")

    return DeterministicEvalResult(passed=not failures, failures=failures)


def render_eval_record(
    *,
    case: ConversationEvalCase,
    response: AvaResponse,
    provider_request_id: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    latency_milliseconds: int,
) -> str:
    deterministic = evaluate_deterministic_constraints(case, response)
    return json.dumps(
        {
            "caseId": case.id,
            "category": case.category,
            "response": response.model_dump(mode="json", by_alias=True),
            "deterministic": deterministic.model_dump(mode="json", by_alias=True),
            "humanReview": case.expectations.human_review,
            "providerRequestId": provider_request_id,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "latencyMilliseconds": latency_milliseconds,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
