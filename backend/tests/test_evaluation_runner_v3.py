from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from ava_backend.realtime.evaluation_runner import (
    compare_personality_reports,
    load_personality_candidates,
    run_personality_evaluation,
)
from ava_backend.realtime.personality_evaluation import (
    PersonalityEvalCandidate,
    load_personality_eval_suite,
)

SUITE_PATH = Path(__file__).parents[1] / "evals" / "personality_cases_v3.json"


def _candidate(speech: str = "Okay.") -> PersonalityEvalCandidate:
    return PersonalityEvalCandidate.model_validate(
        {
            "speech": speech,
            "canvasRequest": None,
            "postDeliveryMemoryProposal": None,
        }
    )


def test_evaluation_runner_requires_complete_exact_candidate_coverage() -> None:
    suite = load_personality_eval_suite(SUITE_PATH)
    with pytest.raises(ValueError, match="coverage mismatch"):
        run_personality_evaluation(
            suite=suite,
            candidates={suite.cases[0].id: _candidate()},
            generated_at=datetime(2026, 8, 1, tzinfo=UTC),
        )


def test_evaluation_report_is_fingerprinted_and_never_auto_promotes() -> None:
    suite = load_personality_eval_suite(SUITE_PATH)
    candidates = {case.id: _candidate() for case in suite.cases}
    report = run_personality_evaluation(
        suite=suite,
        candidates=candidates,
        generated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert report.case_count == len(suite.cases)
    assert report.deterministic_failure_count > 0
    assert len(report.harness_fingerprint) == 64
    assert report.promotion_ready is False
    assert all(case.human_review_pending for case in report.cases)


def test_regression_gate_blocks_new_failures_and_records_recoveries() -> None:
    suite = load_personality_eval_suite(SUITE_PATH)
    candidates = {case.id: _candidate() for case in suite.cases}
    baseline = run_personality_evaluation(
        suite=suite,
        candidates=candidates,
        generated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    candidate_payload = deepcopy(baseline.model_dump(mode="json", by_alias=True))
    passing = next(
        case for case in candidate_payload["cases"] if case["deterministicPassed"]
    )
    passing["deterministicPassed"] = False
    passing["failures"] = ["synthetic regression"]
    candidate_payload["deterministicPassCount"] -= 1
    candidate_payload["deterministicFailureCount"] += 1
    candidate = baseline.model_validate(candidate_payload)

    gate = compare_personality_reports(baseline=baseline, candidate=candidate)
    assert gate.passed is False
    assert gate.regression_case_ids == [passing["caseId"]]


def test_candidate_jsonl_loader_rejects_duplicates(tmp_path: Path) -> None:
    record = {
        "caseId": "casual_greeting",
        "candidate": _candidate().model_dump(mode="json", by_alias=True),
    }
    path = tmp_path / "candidates.jsonl"
    path.write_text(
        json.dumps(record) + "\n" + json.dumps(record) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_personality_candidates(path)
