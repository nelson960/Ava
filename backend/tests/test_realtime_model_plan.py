from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from ava_backend.realtime.model_plan import (
    ModelCanvasDraft,
    ModelCanvasInteraction,
    ModelCanvasNode,
    ModelCanvasSpecialistDraft,
    compile_model_canvas_draft,
    model_canvas_draft_from_runtime_plan,
)
from ava_backend.realtime.plan import AvaTurnPlan

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"


def _specialist_contract(node_id: str) -> dict[str, object]:
    return {
        "title": "Test visual",
        "summary": "A bounded interactive visual used by the contract tests.",
        "visualType": "freeform",
        "topology": "spatial",
        "entities": [
            {
                "id": "test_entity",
                "name": "Test entity",
                "description": "The main concept represented by this visual.",
                "nodeIds": [node_id],
                "facts": [],
            }
        ],
    }


def _runtime_plan() -> AvaTurnPlan:
    return AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / "ava_turn_plan_v3.json").read_text(encoding="utf-8")
    )


def test_specialist_draft_compiles_into_strict_runtime_transaction() -> None:
    transaction = compile_model_canvas_draft(
        model_canvas_draft_from_runtime_plan(_runtime_plan()),
        transaction_id="canvas_test",
    )

    assert transaction.scene_id == "plant_cell_scene"
    assert transaction.base_revision == 0
    assert transaction.revision == 1
    create_operations = [
        operation
        for operation in transaction.operations
        if operation.operation == "create"
    ]
    animation_operations = [
        operation
        for operation in transaction.operations
        if operation.operation == "animate"
    ]
    assert [operation.node.id for operation in create_operations] == [
        "cell",
        "nucleus",
        "nucleus_label",
        "nucleus_arrow",
    ]
    assert [operation.node_id for operation in animation_operations] == [
        "nucleus",
        "nucleus_label",
    ]
    assert transaction.bindings[0].action.kind == "toggleVisibility"
    assert transaction.bindings[0].action.target_node_id == "nucleus_label"


def test_compact_node_rejects_missing_and_irrelevant_geometry() -> None:
    adapter = TypeAdapter(ModelCanvasNode)
    base = {
        "id": "earth",
        "parentId": None,
        "kind": "circle",
        "label": "Earth",
        "style": "primary",
        "hidden": False,
        "x": 600,
        "y": 300,
    }
    with pytest.raises(ValidationError):
        adapter.validate_python(base)

    with pytest.raises(ValidationError):
        adapter.validate_python({**base, "radius": 80, "text": "Earth"})


def test_reveal_interaction_requires_a_hidden_local_target() -> None:
    raw = model_canvas_draft_from_runtime_plan(_runtime_plan()).model_dump(
        mode="json",
        by_alias=True,
    )
    interaction = raw["interactions"][0]
    interaction["action"] = "reveal"
    target_id = interaction["targetNodeId"]
    target = next(node for node in raw["nodes"] if node["id"] == target_id)
    target["hidden"] = False

    with pytest.raises(ValidationError, match="reveal interaction target"):
        ModelCanvasDraft.model_validate(raw)


@pytest.mark.parametrize(
    ("payload", "invalid_field"),
    [
        (
            {
                "id": "reveal_fact",
                "triggerNodeId": "fact_button",
                "action": "reveal",
                "targetNodeId": None,
                "bounds": None,
            },
            "targetNodeId",
        ),
        (
            {
                "id": "drag_drop",
                "triggerNodeId": "drop",
                "action": "dragWithin",
                "targetNodeId": "drop",
                "bounds": None,
            },
            "bounds",
        ),
        (
            {
                "id": "reset_scene",
                "triggerNodeId": "reset_button",
                "action": "reset",
                "targetNodeId": "drop",
                "bounds": None,
            },
            "targetNodeId",
        ),
    ],
)
def test_interaction_action_schema_rejects_invalid_payload_combinations(
    payload: dict[str, object],
    invalid_field: str,
) -> None:
    with pytest.raises(ValidationError) as error:
        TypeAdapter(ModelCanvasInteraction).validate_python(payload)

    assert invalid_field in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "id": "explore_fact",
            "triggerNodeId": "stage",
            "action": "explore",
            "targetNodeId": "stage",
            "revealNodeIds": ["fact_card", "fact_text"],
            "animateNodeIds": ["stage_arrow"],
            "exclusive": True,
            "bounds": None,
        },
        {
            "id": "reveal_fact",
            "triggerNodeId": "fact_button",
            "action": "reveal",
            "targetNodeId": "fact",
            "bounds": None,
        },
        {
            "id": "toggle_fact",
            "triggerNodeId": "fact_button",
            "action": "toggleVisibility",
            "targetNodeId": "fact",
            "bounds": None,
        },
        {
            "id": "select_fact",
            "triggerNodeId": "fact_button",
            "action": "select",
            "targetNodeId": "fact",
            "bounds": None,
        },
        {
            "id": "drag_drop",
            "triggerNodeId": "drop",
            "action": "dragWithin",
            "targetNodeId": "drop",
            "bounds": {"left": 100, "top": 100, "right": 900, "bottom": 500},
        },
        {
            "id": "reset_scene",
            "triggerNodeId": "reset_button",
            "action": "reset",
            "targetNodeId": None,
            "bounds": None,
        },
    ],
)
def test_interaction_action_schema_accepts_every_supported_shape(
    payload: dict[str, object],
) -> None:
    assert TypeAdapter(ModelCanvasInteraction).validate_python(payload)


def test_interaction_schema_exposes_action_specific_shapes_to_provider() -> None:
    schema = ModelCanvasSpecialistDraft.model_json_schema()
    interaction_union = schema["$defs"]["ModelCanvasInteraction"]

    assert "anyOf" in interaction_union
    assert "oneOf" not in interaction_union
    assert len(interaction_union["anyOf"]) == 6

    explore_schema = schema["$defs"]["ModelCanvasExploreInteraction"]
    assert set(explore_schema["required"]) == {
        "id",
        "triggerNodeId",
        "action",
        "targetNodeId",
        "revealNodeIds",
        "animateNodeIds",
        "exclusive",
        "bounds",
    }

    reset_schema = schema["$defs"]["ModelCanvasResetInteraction"]
    assert set(reset_schema["required"]) == {
        "id",
        "triggerNodeId",
        "action",
        "targetNodeId",
        "bounds",
    }
    assert reset_schema["properties"]["targetNodeId"]["type"] == "null"
    assert reset_schema["properties"]["bounds"]["type"] == "null"

    assert set(
        schema["$defs"]["ModelCanvasTextNode"]["properties"]["designRole"]["enum"]
    ) == {
        "title",
        "primaryLabel",
        "secondaryLabel",
        "explanatoryNote",
        "interactiveControl",
    }
    assert (
        schema["$defs"]["ModelCanvasArrowNode"]["properties"]["designRole"]["const"]
        == "connector"
    )
    assert set(
        schema["$defs"]["ModelCanvasCircleNode"]["properties"]["designRole"]["enum"]
    ) == {"primaryObject", "secondaryObject", "interactiveControl"}

    specialist_node_refs = {
        option["$ref"]
        for option in schema["$defs"]["ModelCanvasSpecialistNode"]["anyOf"]
    }
    assert "#/$defs/ModelCanvasGroupNode" in specialist_node_refs
    assert len(specialist_node_refs) == 10
    for reference in specialist_node_refs:
        definition = reference.rsplit("/", maxsplit=1)[-1]
        assert "parentId" in schema["$defs"][definition]["properties"]


def test_specialist_compiles_compound_explore_as_one_local_binding() -> None:
    specialist = ModelCanvasSpecialistDraft.model_validate(
        {
            **_specialist_contract("stage"),
            "nodes": [
                {
                    "id": "stage",
                    "parentId": None,
                    "kind": "circle",
                    "label": "Explore this stage",
                    "style": "primary",
                    "designRole": "primaryObject",
                    "hidden": False,
                    "x": 260,
                    "y": 260,
                    "radius": 58,
                },
                {
                    "id": "stage_arrow",
                    "parentId": None,
                    "kind": "arrow",
                    "label": "Flow to the next stage",
                    "style": "outline",
                    "designRole": "connector",
                    "hidden": False,
                    "x": 320,
                    "y": 260,
                    "x2": 520,
                    "y2": 260,
                },
                {
                    "id": "fact_card",
                    "parentId": None,
                    "kind": "rect",
                    "label": "Explanation card",
                    "style": "secondary",
                    "designRole": "secondaryObject",
                    "hidden": True,
                    "x": 560,
                    "y": 200,
                    "width": 300,
                    "height": 120,
                    "cornerRadius": 24,
                },
                {
                    "id": "fact_text",
                    "parentId": None,
                    "kind": "text",
                    "label": "Stage explanation",
                    "style": "label",
                    "designRole": "explanatoryNote",
                    "hidden": True,
                    "x": 710,
                    "y": 270,
                    "text": "This stage changes the flow.",
                },
            ],
            "interactions": [
                {
                    "id": "explore_stage",
                    "triggerNodeId": "stage",
                    "action": "explore",
                    "targetNodeId": "stage",
                    "revealNodeIds": ["fact_card", "fact_text"],
                    "animateNodeIds": ["stage_arrow"],
                    "exclusive": True,
                    "bounds": None,
                }
            ],
        }
    )

    transaction = compile_model_canvas_draft(
        specialist.bind_scene_id("explore_scene"),
        transaction_id="explore_canvas",
    )

    assert transaction.bindings[0].action.kind == "explore"
    assert transaction.bindings[0].action.reveal_node_ids == [
        "fact_card",
        "fact_text",
    ]
    assert transaction.bindings[0].action.animate_node_ids == ["stage_arrow"]
    animations = [
        operation
        for operation in transaction.operations
        if operation.operation == "animate"
    ]
    assert [animation.delay_ms for animation in animations] == [0]


def test_specialist_quality_accepts_multiple_non_crossing_connectors() -> None:
    specialist = ModelCanvasSpecialistDraft.model_validate(
        {
            **_specialist_contract("stage_one"),
            "nodes": [
                {
                    "id": "stage_one",
                    "parentId": None,
                    "kind": "circle",
                    "label": "First stage",
                    "style": "primary",
                    "designRole": "primaryObject",
                    "hidden": False,
                    "x": 150,
                    "y": 300,
                    "radius": 50,
                },
                {
                    "id": "stage_two",
                    "parentId": None,
                    "kind": "circle",
                    "label": "Second stage",
                    "style": "secondary",
                    "designRole": "secondaryObject",
                    "hidden": False,
                    "x": 500,
                    "y": 300,
                    "radius": 50,
                },
                {
                    "id": "stage_three",
                    "parentId": None,
                    "kind": "circle",
                    "label": "Third stage",
                    "style": "secondary",
                    "designRole": "secondaryObject",
                    "hidden": False,
                    "x": 850,
                    "y": 300,
                    "radius": 50,
                },
                {
                    "id": "first_flow",
                    "parentId": None,
                    "kind": "arrow",
                    "label": "Flow from first to second stage",
                    "style": "outline",
                    "designRole": "connector",
                    "hidden": False,
                    "x": 200,
                    "y": 300,
                    "x2": 450,
                    "y2": 300,
                },
                {
                    "id": "second_flow",
                    "parentId": None,
                    "kind": "arrow",
                    "label": "Flow from second to third stage",
                    "style": "outline",
                    "designRole": "connector",
                    "hidden": False,
                    "x": 550,
                    "y": 300,
                    "x2": 800,
                    "y2": 300,
                },
            ],
            "interactions": [
                {
                    "id": "select_first_stage",
                    "triggerNodeId": "stage_one",
                    "action": "select",
                    "targetNodeId": "stage_one",
                    "bounds": None,
                }
            ],
        }
    )

    transaction = compile_model_canvas_draft(
        specialist.bind_scene_id("three_stage_scene"),
        transaction_id="three_stage_canvas",
    )

    assert (
        len(
            [
                operation
                for operation in transaction.operations
                if operation.operation == "create"
            ]
        )
        == 5
    )
    assert (
        len(
            [
                operation
                for operation in transaction.operations
                if operation.operation == "animate"
            ]
        )
        == 1
    )
    assert len(transaction.bindings) == 1


def test_specialist_compiler_orders_groups_before_children() -> None:
    draft = ModelCanvasDraft.model_validate(
        {
            "sceneId": "ordered_scene",
            "nodes": [
                {
                    "id": "label",
                    "parentId": "stage",
                    "kind": "text",
                    "label": "Water label",
                    "style": "label",
                    "designRole": "primaryLabel",
                    "hidden": False,
                    "x": 120,
                    "y": 80,
                    "text": "Water",
                },
                {
                    "id": "stage",
                    "parentId": None,
                    "kind": "group",
                    "label": "Water-cycle stage",
                    "style": "primary",
                    "designRole": "primaryObject",
                    "hidden": False,
                },
            ],
            "interactions": [],
        }
    )

    transaction = compile_model_canvas_draft(draft, transaction_id="ordered_canvas")
    assert [
        operation.node.id
        for operation in transaction.operations
        if operation.operation == "create"
    ] == [
        "stage",
        "label",
    ]


def test_specialist_draft_rejects_parent_cycles() -> None:
    with pytest.raises(ValidationError, match="cannot contain cycles"):
        ModelCanvasDraft.model_validate(
            {
                "sceneId": "cyclic_scene",
                "nodes": [
                    {
                        "id": "first",
                        "parentId": "second",
                        "kind": "group",
                        "label": "First group",
                        "style": "primary",
                        "designRole": "primaryObject",
                        "hidden": False,
                    },
                    {
                        "id": "second",
                        "parentId": "first",
                        "kind": "group",
                        "label": "Second group",
                        "style": "secondary",
                        "designRole": "secondaryObject",
                        "hidden": False,
                    },
                ],
                "interactions": [],
            }
        )


def test_specialist_draft_rejects_non_group_parent() -> None:
    with pytest.raises(ValidationError, match="parent must reference a group"):
        ModelCanvasDraft.model_validate(
            {
                "sceneId": "invalid_parent_scene",
                "nodes": [
                    {
                        "id": "sun",
                        "parentId": None,
                        "kind": "circle",
                        "label": "Sun",
                        "style": "primary",
                        "designRole": "primaryObject",
                        "hidden": False,
                        "x": 100,
                        "y": 100,
                        "radius": 40,
                    },
                    {
                        "id": "sun_label",
                        "parentId": "sun",
                        "kind": "text",
                        "label": "Sun label",
                        "style": "label",
                        "designRole": "primaryLabel",
                        "hidden": False,
                        "x": 100,
                        "y": 160,
                        "text": "Sun",
                    },
                ],
                "interactions": [],
            }
        )


def test_specialist_compiles_drag_and_reset_as_typed_local_interactions() -> None:
    specialist = ModelCanvasSpecialistDraft.model_validate(
        {
            **_specialist_contract("draggable_drop"),
            "nodes": [
                {
                    "id": "draggable_drop",
                    "parentId": None,
                    "kind": "circle",
                    "label": "Drag the water drop",
                    "style": "primary",
                    "designRole": "primaryObject",
                    "hidden": False,
                    "x": 260,
                    "y": 260,
                    "radius": 42,
                },
                {
                    "id": "reset_control",
                    "parentId": None,
                    "kind": "rect",
                    "label": "Reset the scene",
                    "style": "secondary",
                    "designRole": "interactiveControl",
                    "hidden": False,
                    "x": 810,
                    "y": 490,
                    "width": 110,
                    "height": 52,
                    "cornerRadius": 18,
                },
            ],
            "interactions": [
                {
                    "id": "drag_drop",
                    "triggerNodeId": "draggable_drop",
                    "action": "dragWithin",
                    "targetNodeId": "draggable_drop",
                    "bounds": {
                        "left": 100,
                        "top": 100,
                        "right": 760,
                        "bottom": 500,
                    },
                },
                {
                    "id": "reset_scene",
                    "triggerNodeId": "reset_control",
                    "action": "reset",
                    "targetNodeId": None,
                    "bounds": None,
                },
            ],
        }
    )

    transaction = compile_model_canvas_draft(
        specialist.bind_scene_id("interactive_scene"),
        transaction_id="interactive_canvas",
    )

    assert [binding.event for binding in transaction.bindings] == ["drag", "tap"]
    assert [binding.action.kind for binding in transaction.bindings] == [
        "dragWithin",
        "reset",
    ]


def test_specialist_aesthetic_layout_is_renderer_owned() -> None:
    overlapping_labels = ModelCanvasSpecialistDraft.model_validate(
        {
            **_specialist_contract("first_label"),
            "nodes": [
                {
                    "id": "first_label",
                    "parentId": None,
                    "kind": "text",
                    "label": "First label",
                    "style": "label",
                    "designRole": "primaryLabel",
                    "hidden": False,
                    "x": 500,
                    "y": 250,
                    "text": "Evaporation",
                },
                {
                    "id": "second_label",
                    "parentId": None,
                    "kind": "text",
                    "label": "Second label",
                    "style": "label",
                    "designRole": "primaryLabel",
                    "hidden": False,
                    "x": 510,
                    "y": 255,
                    "text": "Condensation",
                },
            ],
            "interactions": [
                {
                    "id": "select_first",
                    "triggerNodeId": "first_label",
                    "action": "select",
                    "targetNodeId": "first_label",
                    "bounds": None,
                }
            ],
        }
    )
    tiny_trigger = ModelCanvasSpecialistDraft.model_validate(
        {
            **_specialist_contract("tiny_dot"),
            "nodes": [
                {
                    "id": "tiny_dot",
                    "parentId": None,
                    "kind": "circle",
                    "label": "Tiny dot",
                    "style": "primary",
                    "designRole": "primaryObject",
                    "hidden": False,
                    "x": 500,
                    "y": 300,
                    "radius": 10,
                }
            ],
            "interactions": [
                {
                    "id": "select_dot",
                    "triggerNodeId": "tiny_dot",
                    "action": "select",
                    "targetNodeId": "tiny_dot",
                    "bounds": None,
                }
            ],
        }
    )

    assert overlapping_labels
    assert tiny_trigger


def test_specialist_visual_bounds_are_not_provider_parse_failures() -> None:
    payload = {
        **_specialist_contract("edge_object"),
        "nodes": [
            {
                "id": "edge_object",
                "parentId": None,
                "kind": "circle",
                "label": "Object near the canvas edge",
                "style": "primary",
                "designRole": "primaryObject",
                "hidden": False,
                "x": 60,
                "y": 60,
                "radius": 30,
            }
        ],
        "interactions": [
            {
                "id": "select_edge_object",
                "triggerNodeId": "edge_object",
                "action": "select",
                "targetNodeId": "edge_object",
                "bounds": None,
            }
        ],
    }

    assert ModelCanvasSpecialistDraft.model_validate(payload)

    payload["nodes"][0]["x"] = 20
    assert ModelCanvasSpecialistDraft.model_validate(payload)
