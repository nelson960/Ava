from __future__ import annotations

import json
from pathlib import Path

from ava_backend.canvas.specialist import CANVAS_SPECIALIST_PROMPT_VERSION
from ava_backend.canvas.quality import (
    CanvasQualityEvaluator,
    CanvasQualityIssueCode,
)
from ava_backend.realtime.model_plan import ModelCanvasDraft


EVAL_ROOT = Path(__file__).resolve().parents[1] / "evals"


def test_canvas_quality_suite_is_bounded_dynamic_and_versioned() -> None:
    payload = json.loads(
        (EVAL_ROOT / "canvas_quality_cases_v1.json").read_text(encoding="utf-8")
    )

    assert payload["schemaVersion"] == 1
    assert payload["promptVersion"] == CANVAS_SPECIALIST_PROMPT_VERSION
    assert payload["logicalCanvas"] == {
        "width": 1000,
        "height": 600,
        "safeInset": 40,
    }
    cases = payload["cases"]
    assert len(cases) == 10
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(20 <= len(case["brief"]) <= 360 for case in cases)
    allowed = {
        "explore",
        "reveal",
        "toggleVisibility",
        "select",
        "dragWithin",
        "reset",
    }
    assert all(set(case["requiredInteractionKinds"]) <= allowed for case in cases)
    assert any("dragWithin" in case["requiredInteractionKinds"] for case in cases)
    assert sum("explore" in case["requiredInteractionKinds"] for case in cases) >= 5
    assert any(case["id"] == "revise_existing_scene" for case in cases)


def _quality_draft(*, polished: bool) -> ModelCanvasDraft:
    colors = ["#102A43", "#39A9DB", "#F6AE2D"] if polished else []
    nodes: list[dict[str, object]] = [
        {
            "id": "background",
            "parentId": None,
            "kind": "rect",
            "label": "Scene background",
            "style": "secondary",
            "visualStyle": (
                {"fillColor": "#F5F9FA", "strokeColor": "#F5F9FA"} if polished else None
            ),
            "designRole": "secondaryObject",
            "hidden": False,
            "x": 45,
            "y": 45,
            "width": 910,
            "height": 510,
            "cornerRadius": 28,
        },
        {
            "id": "title",
            "parentId": None,
            "kind": "text",
            "label": "Visual title",
            "style": "label",
            "visualStyle": (
                {"fillColor": "#102A43", "strokeColor": None} if polished else None
            ),
            "designRole": "title",
            "hidden": False,
            "x": 500,
            "y": 80,
            "text": "A connected system",
        },
    ]
    for index, (x, y) in enumerate(((170, 210), (500, 300), (830, 420))):
        nodes.append(
            {
                "id": f"object_{index}",
                "parentId": None,
                "kind": "circle",
                "label": f"Object {index + 1}",
                "style": "primary" if index == 0 else "secondary",
                "visualStyle": (
                    {
                        "fillColor": colors[index],
                        "strokeColor": "#102A43",
                        "strokeWidth": 2,
                    }
                    if polished
                    else None
                ),
                "designRole": "primaryObject" if index == 0 else "secondaryObject",
                "hidden": False,
                "x": x,
                "y": y,
                "radius": 55,
            }
        )
    for index, (x, y) in enumerate(((170, 290), (500, 380), (830, 500))):
        nodes.append(
            {
                "id": f"label_{index}",
                "parentId": None,
                "kind": "text",
                "label": f"Object {index + 1} label",
                "style": "label",
                "visualStyle": (
                    {"fillColor": "#102A43", "strokeColor": None} if polished else None
                ),
                "designRole": "secondaryLabel",
                "hidden": False,
                "x": x,
                "y": y,
                "text": f"Part {index + 1}",
            }
        )
    return ModelCanvasDraft.model_validate(
        {
            "sceneId": "quality_scene",
            "title": "A connected system",
            "summary": "Three parts arranged across one interactive system.",
            "visualType": "system",
            "topology": "spatial",
            "nodes": nodes,
            "interactions": [
                {
                    "id": "select_object_one",
                    "triggerNodeId": "object_0",
                    "action": "select",
                    "targetNodeId": "object_0",
                    "bounds": None,
                }
            ],
            "entities": [
                {
                    "id": "object_one",
                    "name": "Part 1",
                    "description": "The first meaningful part of the system.",
                    "nodeIds": ["object_0", "label_0"],
                    "facts": [],
                }
            ],
        }
    )


def test_canvas_quality_gate_accepts_authored_composition() -> None:
    assert CanvasQualityEvaluator().evaluate(_quality_draft(polished=True)).accepted


def test_canvas_quality_gate_rejects_generic_unstyled_composition() -> None:
    report = CanvasQualityEvaluator().evaluate(_quality_draft(polished=False))
    assert CanvasQualityIssueCode.WEAK_VISUAL_STYLE in {
        issue.code for issue in report.issues
    }
