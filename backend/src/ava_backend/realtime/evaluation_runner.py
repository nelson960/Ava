from __future__ import annotations

from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Annotated, Mapping

from pydantic import Field, StringConstraints, model_validator

from .common import NonEmptyText, RealtimeProtocolModel
from .harness import build_realtime_harness
from .personality_evaluation import (
    PersonalityEvalCandidate,
    PersonalityEvalSuite,
    evaluate_personality_candidate,
    memory_snapshot_for_case,
)


class PersonalityCandidateRecord(RealtimeProtocolModel):
    case_id: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=3,
            max_length=80,
            pattern=r"^[a-z0-9_]+$",
        ),
    ]
    candidate: PersonalityEvalCandidate


class PersonalityCaseReport(RealtimeProtocolModel):
    case_id: str
    category: str
    deterministic_passed: bool
    failures: list[NonEmptyText]
    human_review_pending: list[NonEmptyText]


class PersonalityEvaluationReport(RealtimeProtocolModel):
    report_version: int = Field(ge=1, le=1)
    prompt_version: str
    harness_fingerprint: str
    generated_at: datetime
    case_count: int = Field(ge=1)
    deterministic_pass_count: int = Field(ge=0)
    deterministic_failure_count: int = Field(ge=0)
    failure_counts: dict[str, int]
    promotion_ready: bool
    cases: list[PersonalityCaseReport]

    @model_validator(mode="after")
    def counts_are_consistent(self) -> PersonalityEvaluationReport:
        if self.generated_at.tzinfo is None:
            raise ValueError("evaluation generatedAt must be timezone-aware")
        if self.case_count != len(self.cases):
            raise ValueError("evaluation caseCount does not match cases")
        if (
            self.deterministic_pass_count + self.deterministic_failure_count
            != self.case_count
        ):
            raise ValueError("evaluation deterministic counts are inconsistent")
        if self.promotion_ready:
            raise ValueError(
                "automated reports cannot be promotion-ready before human review"
            )
        return self


class PersonalityRegressionGate(RealtimeProtocolModel):
    passed: bool
    regression_case_ids: list[str]
    recovered_case_ids: list[str]


def load_personality_candidates(
    path: Path,
) -> dict[str, PersonalityEvalCandidate]:
    candidates: dict[str, PersonalityEvalCandidate] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        try:
            record = PersonalityCandidateRecord.model_validate_json(raw_line)
        except ValueError as error:
            raise ValueError(
                f"invalid personality candidate at line {line_number}"
            ) from error
        if record.case_id in candidates:
            raise ValueError(f"duplicate personality candidate for {record.case_id!r}")
        candidates[record.case_id] = record.candidate
    if not candidates:
        raise ValueError("personality candidate file cannot be empty")
    return candidates


def run_personality_evaluation(
    *,
    suite: PersonalityEvalSuite,
    candidates: Mapping[str, PersonalityEvalCandidate],
    generated_at: datetime,
) -> PersonalityEvaluationReport:
    expected_ids = {case.id for case in suite.cases}
    candidate_ids = set(candidates)
    missing = sorted(expected_ids - candidate_ids)
    extra = sorted(candidate_ids - expected_ids)
    if missing or extra:
        raise ValueError(
            "personality candidate coverage mismatch: "
            f"missing={missing!r}, extra={extra!r}"
        )
    if generated_at.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")

    case_reports: list[PersonalityCaseReport] = []
    failure_counts: Counter[str] = Counter()
    harness_fingerprints: set[str] = set()
    for case in suite.cases:
        harness = build_realtime_harness(memory_snapshot_for_case(case))
        harness_fingerprints.add(_harness_fingerprint(harness))
        result = evaluate_personality_candidate(case, candidates[case.id])
        for failure in result.failures:
            failure_counts[_failure_kind(failure)] += 1
        case_reports.append(
            PersonalityCaseReport.model_validate(
                {
                    "caseId": case.id,
                    "category": case.category,
                    "deterministicPassed": result.passed,
                    "failures": result.failures,
                    "humanReviewPending": case.expectations.human_review,
                }
            )
        )
    if len(harness_fingerprints) != 1:
        raise ValueError("evaluation cases produced inconsistent harness contracts")
    pass_count = sum(report.deterministic_passed for report in case_reports)
    return PersonalityEvaluationReport.model_validate(
        {
            "reportVersion": 1,
            "promptVersion": suite.prompt_version,
            "harnessFingerprint": harness_fingerprints.pop(),
            "generatedAt": generated_at,
            "caseCount": len(case_reports),
            "deterministicPassCount": pass_count,
            "deterministicFailureCount": len(case_reports) - pass_count,
            "failureCounts": dict(sorted(failure_counts.items())),
            "promotionReady": False,
            "cases": [
                report.model_dump(mode="json", by_alias=True) for report in case_reports
            ],
        }
    )


def compare_personality_reports(
    *,
    baseline: PersonalityEvaluationReport,
    candidate: PersonalityEvaluationReport,
) -> PersonalityRegressionGate:
    baseline_cases = {
        report.case_id: report.deterministic_passed for report in baseline.cases
    }
    candidate_cases = {
        report.case_id: report.deterministic_passed for report in candidate.cases
    }
    if baseline_cases.keys() != candidate_cases.keys():
        raise ValueError("evaluation reports do not cover the same case IDs")
    regressions = sorted(
        case_id
        for case_id, was_passing in baseline_cases.items()
        if was_passing and not candidate_cases[case_id]
    )
    recoveries = sorted(
        case_id
        for case_id, was_passing in baseline_cases.items()
        if not was_passing and candidate_cases[case_id]
    )
    return PersonalityRegressionGate.model_validate(
        {
            "passed": not regressions,
            "regressionCaseIds": regressions,
            "recoveredCaseIds": recoveries,
        }
    )


def write_personality_evaluation_report(
    report: PersonalityEvaluationReport,
    path: Path,
) -> None:
    path.write_text(
        report.model_dump_json(by_alias=True, indent=2),
        encoding="utf-8",
    )


def _harness_fingerprint(harness: object) -> str:
    from .harness import RealtimeHarness

    if not isinstance(harness, RealtimeHarness):
        raise TypeError("harness must be a RealtimeHarness")
    payload = json.dumps(
        {
            "promptVersion": harness.prompt_version,
            "instructions": harness.instructions.split(
                "<ava_runtime_context",
                maxsplit=1,
            )[0],
            "tools": list(harness.tools),
            "toolChoice": harness.tool_choice,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _failure_kind(failure: str) -> str:
    prefixes = (
        ("speech has", "speechBounds"),
        ("plan has", "segmentBounds"),
        ("output contains", "forbiddenPhrase"),
        ("plan included canvas", "unexpectedCanvas"),
        ("plan omitted required canvas", "missingCanvas"),
        ("plan proposed memory", "unexpectedMemory"),
        ("plan omitted a required stable memory", "missingMemory"),
        ("memory proposal rejected", "invalidMemory"),
        ("segment ", "motionIntent"),
    )
    for prefix, kind in prefixes:
        if failure.startswith(prefix):
            return kind
    return "other"
