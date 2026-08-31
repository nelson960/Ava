from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from ava_backend.realtime.harness import (
    REALTIME_PROMPT_VERSION,
    build_realtime_harness,
)
from ava_backend.realtime.personality_evaluation import (
    FeatureExpectation,
    PersonalityEvalCandidate,
    PersonalityEvaluationCategory,
    evaluate_personality_candidate,
    load_personality_eval_suite,
    memory_snapshot_for_case,
)

SUITE_PATH = Path(__file__).parents[1] / "evals" / "personality_cases_v3.json"


def _speech_candidate(
    speech: str,
    *,
    memory: dict[str, object] | None = None,
) -> PersonalityEvalCandidate:
    return PersonalityEvalCandidate.model_validate(
        {
            "speech": speech,
            "canvasRequest": None,
            "postDeliveryMemoryProposal": memory,
        }
    )


def test_v3_personality_suite_covers_every_product_behavior_category() -> None:
    suite = load_personality_eval_suite(SUITE_PATH)

    assert suite.prompt_version == REALTIME_PROMPT_VERSION
    assert len(suite.cases) == 34
    assert {case.category for case in suite.cases} == set(PersonalityEvaluationCategory)
    assert len({case.id for case in suite.cases}) == len(suite.cases)
    assert any(
        case.expectations.canvas == FeatureExpectation.REQUIRED for case in suite.cases
    )
    assert any(
        case.expectations.memory == FeatureExpectation.REQUIRED for case in suite.cases
    )


def test_v4_8_suite_covers_continuity_directness_and_supported_canvas_mechanics() -> (
    None
):
    suite = load_personality_eval_suite(SUITE_PATH)
    cases = {case.id: case for case in suite.cases}

    continuation = cases["canvas_tool_continuation_coherent"]
    assert continuation.expectations.canvas == FeatureExpectation.REQUIRED
    assert any(
        "one conversational thought" in criterion
        for criterion in continuation.expectations.human_review
    )

    direct = cases["explanation_direct_without_required_acknowledgment"]
    assert "Great question" in direct.expectations.forbidden_phrases
    assert any(
        "direct answer" in criterion for criterion in direct.expectations.human_review
    )

    explore = cases["canvas_explore_supported_mechanics"]
    steps = cases["canvas_steps_supported_mechanics"]
    for case in (explore, steps):
        assert case.expectations.canvas == FeatureExpectation.REQUIRED
        assert {"zoom", "pan", "drag"}.issubset(case.expectations.forbidden_phrases)
    assert any("tap" in criterion for criterion in explore.expectations.human_review)
    assert any(
        "Back/Next" in criterion for criterion in steps.expectations.human_review
    )


def test_every_eval_case_builds_a_strict_privacy_bounded_harness() -> None:
    suite = load_personality_eval_suite(SUITE_PATH)

    for case in suite.cases:
        snapshot = memory_snapshot_for_case(case)
        harness = build_realtime_harness(snapshot)
        assert harness.prompt_version == suite.prompt_version
        assert harness.tool_choice == "auto"
        assert "date_of_birth" not in harness.context.content
        assert "child_name" not in harness.context.content


def test_candidate_evaluator_detects_personality_length_question_and_memory_failures() -> (
    None
):
    suite = load_personality_eval_suite(SUITE_PATH)
    case = next(case for case in suite.cases if case.id == "casual_goodnight")
    candidate = _speech_candidate(
        "Great question. Before you go, why not stay for one more lesson? "
        + "word " * 30,
        memory={
            "kind": "preference",
            "summary": "The child likes staying with Ava.",
            "evidence": "Goodnight, Ava.",
            "confidence": 0.95,
        },
    )

    result = evaluate_personality_candidate(case, candidate)

    assert result.passed is False
    assert any("maximum" in failure for failure in result.failures)
    assert any("questions" in failure for failure in result.failures)
    assert any("forbidden phrase" in failure for failure in result.failures)
    assert any("forbidden memory" in failure for failure in result.failures)


def test_explicit_stable_interest_candidate_passes_memory_policy() -> None:
    suite = load_personality_eval_suite(SUITE_PATH)
    case = next(case for case in suite.cases if case.id == "memory_explicit_interest")
    candidate = _speech_candidate(
        "Model airplanes have so many clever little parts.",
        memory={
            "kind": "interest",
            "summary": "The child enjoys building model airplanes.",
            "evidence": "I love building model airplanes.",
            "confidence": 0.9,
        },
    )

    result = evaluate_personality_candidate(case, candidate)

    assert result.passed is True
    assert result.failures == []


def test_explicit_canvas_request_rejects_capability_denial() -> None:
    suite = load_personality_eval_suite(SUITE_PATH)
    case = next(case for case in suite.cases if case.id == "canvas_explicit_plant_cell")
    candidate = _speech_candidate(
        "I cannot draw a diagram, but I can explain it in words."
    )

    result = evaluate_personality_candidate(case, candidate)

    assert result.passed is False
    assert "response omitted the required canvas request" in result.failures
    assert any("cannot draw" in failure for failure in result.failures)


def test_suite_loader_rejects_prompt_version_drift(tmp_path: Path) -> None:
    suite_payload = deepcopy(
        load_personality_eval_suite(SUITE_PATH).model_dump(
            mode="json",
            by_alias=True,
        )
    )
    suite_payload["promptVersion"] = "stale-prompt"
    temporary = tmp_path / "personality_cases_v3.invalid.json"
    temporary.write_text(json.dumps(suite_payload), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        load_personality_eval_suite(temporary)
