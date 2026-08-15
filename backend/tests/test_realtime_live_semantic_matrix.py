from __future__ import annotations

from pathlib import Path

from ava_backend.realtime.harness import REALTIME_PROMPT_VERSION
from ava_backend.realtime.live_semantic_matrix import SemanticMatrixSuite


def test_live_semantic_matrix_is_versioned_bounded_and_non_keyword_based() -> None:
    suite = SemanticMatrixSuite.model_validate_json(
        (
            Path(__file__).parents[1] / "evals" / "realtime_semantic_matrix_v4.json"
        ).read_text(encoding="utf-8")
    )

    assert suite.prompt_version == REALTIME_PROMPT_VERSION
    assert 1 <= len(suite.cases) <= 8
    assert {case.expect_canvas for case in suite.cases} == {False, True}
    false_positive_case = next(
        case for case in suite.cases if case.id == "drawing_word_without_visual"
    )
    assert "drawing" in false_positive_case.input
    assert false_positive_case.expect_canvas is False
