from __future__ import annotations

import pytest
from pydantic import ValidationError

from ava_backend.protocol import (
    AvaResponse,
    DiagramCue,
    FractionBarDiagram,
    FractionCircleDiagram,
    LessonTurnEnvelope,
    NumberLineDiagram,
    adapt_ava_response_to_protocol_v1,
)


def test_protocol_rejects_unknown_request_fields() -> None:
    with pytest.raises(ValidationError):
        LessonTurnEnvelope.model_validate(
            {
                "protocolVersion": 1,
                "request": {
                    "childName": "Mia",
                    "message": "Teach me fractions.",
                    "recentMessages": [],
                    "temperature": 2,
                },
            }
        )


def test_protocol_bounds_recent_history_and_child_message() -> None:
    with pytest.raises(ValidationError):
        LessonTurnEnvelope.model_validate(
            {
                "protocolVersion": 1,
                "request": {
                    "childName": "Mia",
                    "message": "x" * 2001,
                    "recentMessages": [],
                },
            }
        )

    with pytest.raises(ValidationError):
        LessonTurnEnvelope.model_validate(
            {
                "protocolVersion": 1,
                "request": {
                    "childName": "Mia",
                    "message": "Hello",
                    "recentMessages": [
                        {"role": "child", "text": "Again"} for _ in range(17)
                    ],
                },
            }
        )


def test_fraction_diagram_rejects_highlight_overflow() -> None:
    with pytest.raises(ValidationError):
        FractionCircleDiagram(
            title="Not valid",
            total_parts=4,
            highlighted_parts=5,
        )


def test_diagram_union_accepts_fraction_bars_and_bounded_number_lines() -> None:
    bar = DiagramCue.model_validate(
        {
            "action": "show",
            "diagram": {
                "kind": "fractionBar",
                "title": "Two fifths",
                "totalParts": 5,
                "highlightedParts": 2,
            },
        }
    )
    line = DiagramCue.model_validate(
        {
            "action": "show",
            "diagram": {
                "kind": "numberLine",
                "title": "Find six",
                "minimum": 0,
                "maximum": 10,
                "step": 1,
                "start": 0,
                "target": 6,
            },
        }
    )

    assert isinstance(bar.diagram, FractionBarDiagram)
    assert isinstance(line.diagram, NumberLineDiagram)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "kind": "numberLine",
            "title": "Misaligned",
            "minimum": 0,
            "maximum": 10,
            "step": 3,
            "start": 0,
            "target": 6,
        },
        {
            "kind": "numberLine",
            "title": "Too dense",
            "minimum": 0,
            "maximum": 100,
            "step": 1,
            "start": 0,
            "target": 50,
        },
    ],
)
def test_number_line_rejects_invalid_ranges(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DiagramCue.model_validate({"action": "show", "diagram": payload})


def test_ava_response_adapts_to_flutter_protocol_v1() -> None:
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

    adapted = adapt_ava_response_to_protocol_v1(response)

    assert adapted.model_dump(mode="json", by_alias=True) == {
        "protocolVersion": 1,
        "lesson": {
            "speech": "One quarter is one of four equal parts.",
            "expression": "encouraging",
            "gesture": "pointAtDashboard",
            "diagram": {
                "kind": "fractionCircle",
                "title": "One quarter",
                "totalParts": 4,
                "highlightedParts": 1,
            },
            "domain": "general",
        },
    }


def test_ava_response_rejects_model_inferred_learning_domain() -> None:
    with pytest.raises(ValidationError):
        AvaResponse.model_validate(
            {
                "speech": "Let's talk about space.",
                "domain": "science",
                "expression": "happy",
                "gesture": "nod",
                "diagram": None,
            }
        )
