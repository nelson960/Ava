from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from .evaluation_runner import (
    PersonalityEvaluationReport,
    compare_personality_reports,
    load_personality_candidates,
    run_personality_evaluation,
    write_personality_evaluation_report,
)
from .personality_evaluation import load_personality_eval_suite


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate Ava semantic speech/tool candidates without changing prompts."
        )
    )
    parser.add_argument("--suite", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--baseline", type=Path)
    arguments = parser.parse_args()

    suite = load_personality_eval_suite(arguments.suite)
    candidates = load_personality_candidates(arguments.candidates)
    report = run_personality_evaluation(
        suite=suite,
        candidates=candidates,
        generated_at=datetime.now(UTC),
    )
    write_personality_evaluation_report(report, arguments.output)

    if arguments.baseline is None:
        return 0 if report.deterministic_failure_count == 0 else 1
    baseline = PersonalityEvaluationReport.model_validate_json(
        arguments.baseline.read_text(encoding="utf-8")
    )
    gate = compare_personality_reports(baseline=baseline, candidate=report)
    return 0 if gate.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
