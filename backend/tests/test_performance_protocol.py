from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from ava_backend.protocol import (
    AvaPerformancePlan,
    PerformanceFailure,
    PerformanceTurnResponse,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "protocol"
    / "fixtures"
    / "ava_performance_plan_v2.json"
)


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_shared_protocol_v2_fixture_round_trips_strictly() -> None:
    response = PerformanceTurnResponse.model_validate(_fixture())

    assert response.performance.speech == (
        "A quarter means one of four equal parts. "
        "Here, one of the four pieces is highlighted."
    )
    assert response.model_dump(mode="json", by_alias=True) == _fixture()


def test_protocol_v2_rejects_raw_motion_and_missing_nullable_fields() -> None:
    raw_motion = _fixture()
    first_segment = raw_motion["performance"]["segments"][0]  # type: ignore[index]
    first_segment["boneRotations"] = {"head": [0, 20, 0]}  # type: ignore[index]

    with pytest.raises(ValidationError):
        PerformanceTurnResponse.model_validate(raw_motion)

    missing_diagram_field = _fixture()
    del missing_diagram_field["performance"]["segments"][0]["diagramCue"]  # type: ignore[index]

    with pytest.raises(ValidationError):
        PerformanceTurnResponse.model_validate(missing_diagram_field)


@pytest.mark.parametrize(
    ("gaze", "gesture", "has_diagram"),
    [
        ("diagram", "explainSmall", True),
        ("child", "pointAtDashboard", True),
        ("diagram", "pointAtDashboard", False),
    ],
)
def test_protocol_v2_rejects_incoherent_diagram_cues(
    gaze: str,
    gesture: str,
    has_diagram: bool,
) -> None:
    payload = _fixture()
    segment = payload["performance"]["segments"][1]  # type: ignore[index]
    segment["gaze"] = gaze  # type: ignore[index]
    segment["gesture"] = gesture  # type: ignore[index]
    if not has_diagram:
        segment["diagramCue"] = None  # type: ignore[index]

    with pytest.raises(ValidationError):
        PerformanceTurnResponse.model_validate(payload)


def test_performance_plan_rejects_duplicate_ids_and_multiple_diagrams() -> None:
    duplicate = _fixture()
    segments = duplicate["performance"]["segments"]  # type: ignore[index]
    segments[1]["id"] = "intro"  # type: ignore[index]

    with pytest.raises(ValidationError):
        PerformanceTurnResponse.model_validate(duplicate)

    multiple_diagrams = _fixture()
    segments = multiple_diagrams["performance"]["segments"]  # type: ignore[index]
    third = deepcopy(segments[1])  # type: ignore[index]
    third["id"] = "second_diagram"
    segments.append(third)  # type: ignore[union-attr]

    with pytest.raises(ValidationError):
        PerformanceTurnResponse.model_validate(multiple_diagrams)


def test_performance_plan_enforces_segment_and_total_speech_bounds() -> None:
    base_segment = {
        "id": "segment",
        "speech": "x" * 300,
        "delivery": "neutral",
        "expression": "neutral",
        "gaze": "child",
        "gesture": "none",
        "diagramCue": None,
    }
    too_many = [dict(base_segment, id=f"segment_{index}") for index in range(7)]
    with pytest.raises(ValidationError):
        AvaPerformancePlan.model_validate({"segments": too_many})

    too_long = [dict(base_segment, id=f"segment_{index}") for index in range(6)]
    with pytest.raises(ValidationError):
        AvaPerformancePlan.model_validate({"segments": too_long})


def test_protocol_v2_rejects_contradictory_emotion_and_typed_failures_are_strict() -> (
    None
):
    payload = _fixture()
    segment = payload["performance"]["segments"][0]  # type: ignore[index]
    segment["delivery"] = "playful"  # type: ignore[index]
    segment["expression"] = "serious"  # type: ignore[index]

    with pytest.raises(ValidationError):
        PerformanceTurnResponse.model_validate(payload)

    failure = PerformanceFailure.model_validate(
        {
            "code": "speechUnavailable",
            "message": "The speech provider is unavailable.",
            "retryable": True,
        }
    )
    assert failure.code == "speechUnavailable"

    with pytest.raises(ValidationError):
        PerformanceFailure.model_validate(
            {
                "code": "speechUnavailable",
                "message": "Unavailable",
                "retryable": True,
                "fallbackSpeech": "Use the device voice",
            }
        )
