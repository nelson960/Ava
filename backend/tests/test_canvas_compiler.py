from __future__ import annotations

import pytest

from ava_backend.canvas.quality import CanvasQualityEvaluator
from ava_backend.realtime.model_plan import (
    ModelCanvasCompiler,
    ModelCanvasDraft,
    ModelCanvasSpecialistDraft,
)


def test_compiler_compiles_declarative_solar_system_scene() -> None:
    plan = ModelCanvasSpecialistDraft.model_validate(
        {
            "title": "The Solar System",
            "summary": "The Sun at the center with Earth and Moon orbiting.",
            "nodes": [
                {
                    "id": "sun_body",
                    "parentId": None,
                    "kind": "circle",
                    "label": "The Sun",
                    "designRole": "primaryObject",
                    "hidden": False,
                    "x": 500.0,
                    "y": 300.0,
                    "radius": 55.0,
                    "style": "primary",
                    "visualStyle": {
                        "fillColor": "#FFD166",
                        "strokeColor": "#FFAA00",
                        "strokeWidth": 2.0,
                    },
                },
                {
                    "id": "earth_orbit",
                    "parentId": None,
                    "kind": "ellipse",
                    "label": "Earth Orbit",
                    "designRole": "secondaryObject",
                    "hidden": False,
                    "x": 500.0,
                    "y": 300.0,
                    "radiusX": 180.0,
                    "radiusY": 110.0,
                    "style": "outline",
                    "visualStyle": {
                        "strokeColor": "#4CC9F0",
                        "strokeWidth": 1.5,
                        "opacity": 0.4,
                    },
                },
                {
                    "id": "earth_body",
                    "parentId": None,
                    "kind": "circle",
                    "label": "Earth",
                    "designRole": "primaryObject",
                    "hidden": False,
                    "x": 680.0,
                    "y": 300.0,
                    "radius": 24.0,
                    "style": "primary",
                    "visualStyle": {
                        "fillColor": "#0077B6",
                        "strokeColor": "#48CAE4",
                    },
                },
                {
                    "id": "sun_label",
                    "parentId": None,
                    "kind": "text",
                    "label": "Sun Label",
                    "designRole": "primaryLabel",
                    "hidden": False,
                    "x": 500.0,
                    "y": 380.0,
                    "text": "The Sun",
                    "style": "label",
                },
                {
                    "id": "earth_label",
                    "parentId": None,
                    "kind": "text",
                    "label": "Earth Label",
                    "designRole": "secondaryLabel",
                    "hidden": False,
                    "x": 680.0,
                    "y": 340.0,
                    "text": "Earth",
                    "style": "label",
                },
            ],
            "entities": [
                {
                    "id": "sun",
                    "name": "The Sun",
                    "description": "The star at the center of our solar system.",
                    "facts": ["Surface: 5500 C", "Core: Nuclear Fusion"],
                    "nodeIds": ["sun_body", "sun_label"],
                },
                {
                    "id": "earth",
                    "name": "Earth",
                    "description": "Our home planet with oceans and life.",
                    "facts": ["Orbit: 365 Days", "Water: 71%"],
                    "nodeIds": ["earth_body", "earth_label"],
                },
            ],
            "interactions": [
                {
                    "id": "explore_sun",
                    "action": "explore",
                    "triggerNodeId": "sun_body",
                    "targetNodeId": "sun_body",
                },
            ],
        }
    )

    draft = plan.bind_scene_id("scene_solar")
    assert isinstance(draft, ModelCanvasDraft)
    assert draft.scene_id == "scene_solar"
    assert draft.title == "The Solar System"
    assert len(draft.nodes) == 5
    assert len(draft.entities) == 2
    assert len(draft.interactions) == 1

    # Verify quality evaluator accepts clean scene
    report = CanvasQualityEvaluator().evaluate(draft)
    assert report.accepted, f"Quality issues: {report.issues}"

    # Verify transaction compilation
    transaction = ModelCanvasCompiler().compile_canvas_draft(
        draft,
        transaction_id="tx_solar",
    )
    assert transaction.scene_id == "scene_solar"
    assert len(transaction.operations) >= len(draft.nodes)


def test_compiler_compiles_volcano_illustration_with_paths() -> None:
    plan = ModelCanvasSpecialistDraft.model_validate(
        {
            "title": "How a Volcano Erupts",
            "summary": "Magma rises from deep underground and erupts as lava and ash.",
            "nodes": [
                {
                    "id": "volcano_cone",
                    "parentId": None,
                    "kind": "path",
                    "label": "Mountain Cone",
                    "designRole": "primaryObject",
                    "hidden": False,
                    "pathData": "M 200 480 L 440 220 L 560 220 L 800 480 Z",
                    "style": "primary",
                    "visualStyle": {
                        "fillColor": "#5D4037",
                        "strokeColor": "#3E2723",
                        "strokeWidth": 2.0,
                    },
                },
                {
                    "id": "magma_chamber",
                    "parentId": None,
                    "kind": "ellipse",
                    "label": "Magma Chamber",
                    "designRole": "primaryObject",
                    "hidden": False,
                    "x": 500.0,
                    "y": 500.0,
                    "radiusX": 90.0,
                    "radiusY": 35.0,
                    "style": "primary",
                    "visualStyle": {
                        "fillColor": "#E65100",
                        "strokeColor": "#FF6D00",
                    },
                },
                {
                    "id": "conduit",
                    "parentId": None,
                    "kind": "rect",
                    "label": "Magma Conduit",
                    "designRole": "primaryObject",
                    "hidden": False,
                    "x": 485.0,
                    "y": 220.0,
                    "width": 30.0,
                    "height": 270.0,
                    "style": "primary",
                    "visualStyle": {
                        "fillColor": "#FF3D00",
                    },
                },
                {
                    "id": "ash_cloud",
                    "parentId": None,
                    "kind": "path",
                    "label": "Ash Cloud",
                    "designRole": "secondaryObject",
                    "hidden": False,
                    "pathData": "M 420 180 Q 500 80 580 180 Q 520 220 420 180 Z",
                    "style": "secondary",
                    "visualStyle": {
                        "fillColor": "#78909C",
                        "opacity": 0.85,
                    },
                },
                {
                    "id": "title_label",
                    "parentId": None,
                    "kind": "text",
                    "label": "Title",
                    "designRole": "title",
                    "hidden": False,
                    "x": 500.0,
                    "y": 80.0,
                    "text": "Volcano Eruption",
                    "style": "label",
                },
            ],
            "entities": [
                {
                    "id": "magma_chamber",
                    "name": "Magma Chamber",
                    "description": "A pool of molten rock beneath the Earth crust.",
                    "facts": ["Temperature: >1000 C", "Builds up immense pressure"],
                    "nodeIds": ["magma_chamber"],
                },
                {
                    "id": "ash_cloud",
                    "name": "Ash Cloud",
                    "description": "Hot gas, rock fragments, and volcanic glass ejected into the sky.",
                    "facts": ["Can reach miles high", "Blocks sunlight"],
                    "nodeIds": ["ash_cloud"],
                },
            ],
            "interactions": [
                {
                    "id": "explore_magma",
                    "action": "explore",
                    "triggerNodeId": "magma_chamber",
                    "targetNodeId": "magma_chamber",
                },
            ],
        }
    )

    draft = plan.bind_scene_id("scene_volcano")
    assert len(draft.nodes) == 5
    assert len(draft.entities) == 2
    assert CanvasQualityEvaluator().evaluate(draft).accepted


def test_compiler_rejects_empty_nodes_plan() -> None:
    # Compiler requires specialist to author drawing nodes
    with pytest.raises(Exception):
        ModelCanvasSpecialistDraft.model_validate(
            {
                "title": "Empty Plan",
                "summary": "A plan without any nodes.",
                "nodes": [],
            }
        )
