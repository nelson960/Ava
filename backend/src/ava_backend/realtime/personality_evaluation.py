from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from ..protocol import AgeBand
from .common import NonEmptyText, RealtimeProtocolModel
from .harness import REALTIME_PROMPT_VERSION
from .memory import (
    AvaMemoryProposal,
    AuthoritativeMemorySnapshot,
    evaluate_memory_proposal,
)
from .tool_contract import InteractiveCanvasRequest
from .turn_parser import StreamingSpeechParser, StreamingTurnParseError


class PersonalityEvaluationCategory(StrEnum):
    CASUAL = "casual"
    EXPLANATION = "explanation"
    TOPIC_CHANGE = "topicChange"
    EMOTIONAL = "emotional"
    RELATIONSHIP = "relationship"
    SAFETY = "safety"
    PRIVACY = "privacy"
    ADVERSARIAL = "adversarial"
    CANVAS = "canvas"
    MEMORY = "memory"


class FeatureExpectation(StrEnum):
    FORBIDDEN = "forbidden"
    OPTIONAL = "optional"
    REQUIRED = "required"


class PersonalityEvalTurn(RealtimeProtocolModel):
    child: Annotated[NonEmptyText, StringConstraints(max_length=2_000)]
    ava_delivered: Annotated[NonEmptyText, StringConstraints(max_length=1_800)]


class PersonalityEvalMemory(RealtimeProtocolModel):
    kind: Literal["interest", "preference"]
    summary: Annotated[NonEmptyText, StringConstraints(max_length=160)]
    evidence: Annotated[NonEmptyText, StringConstraints(max_length=160)]


class PersonalityEvalContext(RealtimeProtocolModel):
    current_session_summary: (
        Annotated[str, StringConstraints(min_length=1, max_length=1_200)] | None
    ) = None
    delivered_turns: list[PersonalityEvalTurn] = Field(
        default_factory=list,
        max_length=4,
    )
    stable_memories: list[PersonalityEvalMemory] = Field(
        default_factory=list,
        max_length=4,
    )


class PersonalityEvalExpectations(RealtimeProtocolModel):
    canvas: FeatureExpectation
    memory: FeatureExpectation
    maximum_words: int = Field(ge=4, le=260)
    maximum_questions: int = Field(ge=0, le=2)
    maximum_segments: int = Field(ge=1, le=6)
    forbidden_phrases: list[
        Annotated[NonEmptyText, StringConstraints(max_length=120)]
    ] = Field(default_factory=list, max_length=20)
    human_review: list[Annotated[NonEmptyText, StringConstraints(max_length=280)]] = (
        Field(min_length=1, max_length=8)
    )


class PersonalityEvalCase(RealtimeProtocolModel):
    id: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=3,
            max_length=80,
            pattern=r"^[a-z0-9_]+$",
        ),
    ]
    category: PersonalityEvaluationCategory
    age_band: AgeBand
    child_text: Annotated[NonEmptyText, StringConstraints(max_length=2_000)]
    context: PersonalityEvalContext = Field(
        default_factory=PersonalityEvalContext,
    )
    expectations: PersonalityEvalExpectations


class PersonalityEvalSuite(RealtimeProtocolModel):
    schema_version: Literal[3]
    prompt_version: Annotated[NonEmptyText, StringConstraints(max_length=80)]
    cases: list[PersonalityEvalCase] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> PersonalityEvalSuite:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("personality evaluation case IDs must be unique")
        return self


class PersonalityEvalResult(RealtimeProtocolModel):
    passed: bool
    failures: list[str]


class PersonalityEvalCandidate(RealtimeProtocolModel):
    """One semantic live response plus an independently extracted memory result."""

    speech: Annotated[NonEmptyText, StringConstraints(max_length=1_800)]
    canvas_request: InteractiveCanvasRequest | None = None
    post_delivery_memory_proposal: AvaMemoryProposal | None = None


def load_personality_eval_suite(
    path: Path,
    *,
    expected_prompt_version: str = REALTIME_PROMPT_VERSION,
) -> PersonalityEvalSuite:
    suite = PersonalityEvalSuite.model_validate_json(path.read_text(encoding="utf-8"))
    if suite.prompt_version != expected_prompt_version:
        raise ValueError(
            "personality suite promptVersion does not match the active "
            f"prompt: {suite.prompt_version!r} != {expected_prompt_version!r}"
        )
    return suite


def evaluate_personality_candidate(
    case: PersonalityEvalCase,
    candidate: PersonalityEvalCandidate,
) -> PersonalityEvalResult:
    failures: list[str] = []
    speech = candidate.speech
    word_count = len(speech.split())
    if word_count > case.expectations.maximum_words:
        failures.append(
            f"speech has {word_count} words; maximum is "
            f"{case.expectations.maximum_words}"
        )
    question_count = speech.count("?")
    if question_count > case.expectations.maximum_questions:
        failures.append(
            f"speech has {question_count} questions; maximum is "
            f"{case.expectations.maximum_questions}"
        )
    parser = StreamingSpeechParser()
    try:
        parser.feed(speech)
        parser.finish()
    except StreamingTurnParseError as error:
        failures.append(f"speech failed the streaming contract: {error}")
    else:
        if len(parser.emitted_segments) > case.expectations.maximum_segments:
            failures.append(
                f"speech has {len(parser.emitted_segments)} segments; maximum is "
                f"{case.expectations.maximum_segments}"
            )

    searchable_output = " ".join(
        (
            speech,
            (
                candidate.post_delivery_memory_proposal.summary
                if candidate.post_delivery_memory_proposal
                else ""
            ),
            (
                candidate.post_delivery_memory_proposal.evidence
                if candidate.post_delivery_memory_proposal
                else ""
            ),
        )
    ).casefold()
    for phrase in case.expectations.forbidden_phrases:
        if phrase.casefold() in searchable_output:
            failures.append(f"output contains forbidden phrase {phrase!r}")

    has_canvas = candidate.canvas_request is not None
    if case.expectations.canvas == FeatureExpectation.FORBIDDEN and has_canvas:
        failures.append("response requested a canvas when canvas is forbidden")
    if case.expectations.canvas == FeatureExpectation.REQUIRED and not has_canvas:
        failures.append("response omitted the required canvas request")

    if (
        case.expectations.memory == FeatureExpectation.FORBIDDEN
        and candidate.post_delivery_memory_proposal is not None
    ):
        failures.append("post-delivery extractor proposed forbidden memory")
    if (
        case.expectations.memory == FeatureExpectation.REQUIRED
        and candidate.post_delivery_memory_proposal is None
    ):
        failures.append("post-delivery extractor omitted required stable memory")
    if candidate.post_delivery_memory_proposal is not None:
        decision = evaluate_memory_proposal(
            proposal=candidate.post_delivery_memory_proposal,
            child_text=case.child_text,
            source_turn_id="eval-turn",
            memory_id="eval-memory",
        )
        if not decision.accepted:
            failures.append(f"memory proposal rejected: {decision.reason}")

    return PersonalityEvalResult.model_validate(
        {"passed": not failures, "failures": failures}
    )


def memory_snapshot_for_case(
    case: PersonalityEvalCase,
) -> AuthoritativeMemorySnapshot:
    return AuthoritativeMemorySnapshot.model_validate(
        {
            "ageBand": case.age_band,
            "currentSessionSummary": case.context.current_session_summary,
            "deliveredTurns": [
                {
                    "turnId": f"eval-prior-turn-{index}",
                    "childText": turn.child,
                    "avaDeliveredText": turn.ava_delivered,
                    "deliveredSampleCount": 1,
                    "childTurnSummary": "Earlier delivered conversation.",
                    "terminal": "completed",
                }
                for index, turn in enumerate(case.context.delivered_turns, start=1)
            ],
            "stableMemories": [
                {
                    "id": f"eval-prior-memory-{index}",
                    "kind": memory.kind,
                    "summary": memory.summary,
                    "evidence": memory.evidence,
                    "sourceTurnId": f"eval-memory-source-{index}",
                }
                for index, memory in enumerate(case.context.stable_memories, start=1)
            ],
        }
    )


def render_personality_eval_record(
    *,
    case: PersonalityEvalCase,
    candidate: PersonalityEvalCandidate,
) -> str:
    result = evaluate_personality_candidate(case, candidate)
    return json.dumps(
        {
            "caseId": case.id,
            "category": case.category,
            "promptVersion": REALTIME_PROMPT_VERSION,
            "candidate": candidate.model_dump(mode="json", by_alias=True),
            "deterministic": result.model_dump(mode="json", by_alias=True),
            "humanReview": case.expectations.human_review,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
