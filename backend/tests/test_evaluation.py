from __future__ import annotations

import json
from pathlib import Path

import pytest

from ava_backend.eval_runner import _parse_arguments, _select_cases
from ava_backend.evaluation import (
    EvaluationCategory,
    evaluate_deterministic_constraints,
    load_conversation_eval_suite,
    render_eval_record,
)
from ava_backend.prompts import PROMPT_VERSION
from ava_backend.protocol import AvaResponse, FractionCircleDiagram

SUITE_PATH = Path(__file__).parents[1] / "evals" / "conversation_cases.json"


def test_conversation_eval_suite_covers_all_required_behavior_categories() -> None:
    suite = load_conversation_eval_suite(
        SUITE_PATH,
        expected_prompt_version=PROMPT_VERSION,
    )

    assert len(suite.cases) == 40
    assert {case.category for case in suite.cases} == set(EvaluationCategory)
    assert len({case.id for case in suite.cases}) == len(suite.cases)


def test_eval_suite_rejects_prompt_version_drift() -> None:
    with pytest.raises(ValueError, match="does not match the active prompt"):
        load_conversation_eval_suite(
            SUITE_PATH,
            expected_prompt_version="different-prompt-version",
        )


def test_deterministic_eval_detects_length_diagram_and_gesture_failures() -> None:
    suite = load_conversation_eval_suite(
        SUITE_PATH,
        expected_prompt_version=PROMPT_VERSION,
    )
    casual_case = next(case for case in suite.cases if case.id == "casual_greeting")
    response = AvaResponse(
        speech="Great question! " + "word " * 50,
        expression="happy",
        gesture="pointAtDashboard",
        diagram=None,
    )

    result = evaluate_deterministic_constraints(casual_case, response)

    assert result.passed is False
    assert any("maximum" in failure for failure in result.failures)
    assert any("forbidden phrase" in failure for failure in result.failures)
    assert any("requires a diagram" in failure for failure in result.failures)

    forbidden_diagram = response.model_copy(
        update={
            "gesture": "explain",
            "diagram": FractionCircleDiagram(
                title="Unwanted",
                total_parts=2,
                highlighted_parts=1,
            ),
        }
    )
    result = evaluate_deterministic_constraints(casual_case, forbidden_diagram)
    assert any("diagrams are forbidden" in failure for failure in result.failures)

    required_case = next(
        case for case in suite.cases if case.id == "diagram_fraction_required"
    )
    missing_diagram = AvaResponse(
        speech="One quarter is one of four equal parts.",
        expression="encouraging",
        gesture="explain",
        diagram=None,
    )
    result = evaluate_deterministic_constraints(required_case, missing_diagram)
    assert any("omitted a required diagram" in failure for failure in result.failures)


def test_eval_record_contains_machine_checks_and_human_rubric() -> None:
    suite = load_conversation_eval_suite(
        SUITE_PATH,
        expected_prompt_version=PROMPT_VERSION,
    )
    diagram_case = next(
        case for case in suite.cases if case.id == "diagram_fraction_required"
    )
    response = AvaResponse(
        speech="One quarter is one of four equal parts.",
        expression="encouraging",
        gesture="pointAtDashboard",
        diagram=FractionCircleDiagram(
            title="One quarter",
            total_parts=4,
            highlighted_parts=1,
        ),
    )

    record = json.loads(
        render_eval_record(
            case=diagram_case,
            response=response,
            provider_request_id="request-123",
            input_tokens=100,
            output_tokens=25,
            latency_milliseconds=420,
        )
    )

    assert record["deterministic"] == {"passed": True, "failures": []}
    assert record["humanReview"] == diagram_case.expectations.human_review
    assert record["response"]["diagram"]["totalParts"] == 4


def test_live_runner_requires_explicit_paid_case_selection() -> None:
    with pytest.raises(SystemExit):
        _parse_arguments([])

    arguments = _parse_arguments(["--case", "casual_greeting"])
    suite = load_conversation_eval_suite(
        SUITE_PATH,
        expected_prompt_version=PROMPT_VERSION,
    )
    selected = _select_cases(suite.cases, requested_ids=arguments.case_ids)
    assert [case.id for case in selected] == ["casual_greeting"]

    with pytest.raises(ValueError, match="unknown evaluation case IDs"):
        _select_cases(suite.cases, requested_ids=["missing_case"])
    with pytest.raises(ValueError, match="spend credits twice"):
        _select_cases(
            suite.cases,
            requested_ids=["casual_greeting", "casual_greeting"],
        )
