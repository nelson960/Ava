from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from ava_backend.canvas.session import (
    CanvasInteractionRejected,
    CanvasInteractionRejectionCode,
    CanvasSessionState,
)
from ava_backend.realtime.model_plan import (
    ModelCanvasDraft,
    model_canvas_draft_from_runtime_plan,
)
from ava_backend.realtime.plan import AvaTurnPlan


FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"


def _draft() -> ModelCanvasDraft:
    plan = AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / "ava_turn_plan_v3.json").read_text(encoding="utf-8")
    )
    return model_canvas_draft_from_runtime_plan(plan)


def _explore_draft() -> ModelCanvasDraft:
    payload = _draft().model_dump(mode="json", by_alias=True)
    for node in payload["nodes"]:
        if node["id"] == "nucleus_label":
            node["hidden"] = True
    payload["interactions"] = [
        {
            "id": "explore_nucleus",
            "triggerNodeId": "nucleus",
            "action": "explore",
            "targetNodeId": "nucleus",
            "revealNodeIds": ["nucleus_label"],
            "animateNodeIds": ["nucleus_arrow"],
            "exclusive": True,
            "bounds": None,
        }
    ]
    return ModelCanvasDraft.model_validate(payload)


def test_canvas_session_compiles_atomic_revision_and_repeated_delivery() -> None:
    state = CanvasSessionState()
    original = _draft()
    first = state.apply_draft(original, transaction_id="canvas_turn_one")
    assert first is not None
    assert first.base_revision == 0
    assert first.revision == 1

    changed_payload = deepcopy(original.model_dump(mode="json", by_alias=True))
    changed_payload["nodes"][0]["label"] = "Updated cell outline"
    changed = ModelCanvasDraft.model_validate(changed_payload)
    second = state.apply_draft(changed, transaction_id="canvas_turn_two")

    assert second is not None
    assert second.base_revision == 1
    assert second.revision == 2
    assert [operation.operation for operation in second.operations] == ["update"]
    assert state.scene is not None
    assert state.scene.revision == 2
    assert state.scene.nodes[changed.nodes[0].id].label == "Updated cell outline"
    repeated = state.apply_draft(changed, transaction_id="canvas_turn_three")
    assert repeated is not None
    assert repeated.base_revision == 2
    assert repeated.revision == 3
    assert [operation.operation for operation in repeated.operations] == ["update"]
    assert state.scene.revision == 3


def test_voice_revision_preserves_explore_ids_and_updates_only_requested_content() -> (
    None
):
    state = CanvasSessionState()
    original = _explore_draft()
    state.apply_draft(original, transaction_id="canvas_explore_one")

    payload = deepcopy(original.model_dump(mode="json", by_alias=True))
    label = next(node for node in payload["nodes"] if node["id"] == "nucleus_label")
    label["text"] = "The nucleus holds the cell's instructions."
    revised = ModelCanvasDraft.model_validate(payload)
    transaction = state.apply_draft(
        revised,
        transaction_id="canvas_explore_revision",
    )

    assert [operation.operation for operation in transaction.operations] == ["update"]
    assert transaction.operations[0].node.id == "nucleus_label"
    assert transaction.bindings[0].id == "explore_nucleus_binding"
    assert transaction.bindings[0].action.kind == "explore"
    assert state.draft == revised


def test_canvas_preview_validates_without_mutating_canonical_scene() -> None:
    state = CanvasSessionState()
    draft = _draft()

    preview = state.preview_draft(draft, transaction_id="canvas_preview")
    assert preview.base_revision == 0
    assert preview.revision == 1
    assert state.scene is None
    assert state.draft is None

    committed = state.apply_draft(draft, transaction_id="canvas_preview")
    assert committed == preview
    assert state.scene is not None
    assert state.scene.revision == 1


def test_canvas_session_rejects_scene_replacement_and_local_only_reports() -> None:
    state = CanvasSessionState()
    original = _draft()
    state.apply_draft(original, transaction_id="canvas_turn_one")

    replacement = ModelCanvasDraft.model_validate(
        {
            **original.model_dump(mode="json", by_alias=True),
            "sceneId": "different_scene",
        }
    )
    with pytest.raises(ValueError, match="preserve sceneId"):
        state.apply_draft(replacement, transaction_id="canvas_turn_two")

    scene = state.scene
    assert scene is not None
    local_binding = next(iter(scene.bindings))
    with pytest.raises(CanvasInteractionRejected, match="local-only") as rejected:
        state.record_interaction(
            scene_id=scene.id,
            scene_revision=scene.revision,
            interaction_revision=1,
            binding_id=local_binding,
        )
    assert rejected.value.code == CanvasInteractionRejectionCode.BINDING_LOCAL_ONLY


def test_canvas_session_atomically_replaces_a_full_specialist_scene() -> None:
    def draft(prefix: str) -> ModelCanvasDraft:
        return ModelCanvasDraft.model_validate(
            {
                "sceneId": "backend_owned_scene",
                "nodes": [
                    {
                        "id": f"{prefix}_{index}",
                        "parentId": None,
                        "kind": "rect",
                        "label": f"{prefix} node {index}",
                        "style": "primary",
                        "designRole": "primaryObject",
                        "hidden": index == 1,
                        "x": 40 + (index % 8) * 110,
                        "y": 40 + (index // 8) * 120,
                        "width": 80,
                        "height": 70,
                        "cornerRadius": 8,
                    }
                    for index in range(32)
                ],
                "interactions": [
                    {
                        "id": f"{prefix}_reveal",
                        "triggerNodeId": f"{prefix}_0",
                        "action": "reveal",
                        "targetNodeId": f"{prefix}_1",
                        "bounds": None,
                    }
                ],
            }
        )

    state = CanvasSessionState()
    state.apply_draft(draft("old"), transaction_id="canvas_old")
    replacement = state.apply_draft(
        draft("new"),
        transaction_id="canvas_new",
    )

    assert len(replacement.operations) == 64
    assert replacement.base_revision == 1
    assert replacement.revision == 2
    assert state.scene is not None
    assert len(state.scene.nodes) == 32
    assert set(state.scene.nodes) == {f"new_{index}" for index in range(32)}


def test_canvas_session_resolves_semantic_selection_from_canonical_binding() -> None:
    payload = _draft().model_dump(mode="json", by_alias=True)
    payload["interactions"][0]["action"] = "select"
    draft = ModelCanvasDraft.model_validate(payload)
    state = CanvasSessionState()
    state.apply_draft(draft, transaction_id="canvas_select_one")
    scene = state.scene
    assert scene is not None

    interaction = state.record_interaction(
        scene_id=scene.id,
        scene_revision=scene.revision,
        interaction_revision=3,
        binding_id="toggle_nucleus_binding",
    )

    assert interaction.action == "select"
    assert interaction.target_id == "nucleus_label"
    assert interaction.target_label == "Nucleus label"
    context = state.render_realtime_context()
    assert context is not None
    assert 'scene_id="plant_cell_scene"' in context
    assert 'action="select"' in context
    assert "Nucleus label" in context

    with pytest.raises(CanvasInteractionRejected, match="monotonically") as rejected:
        state.record_interaction(
            scene_id=scene.id,
            scene_revision=scene.revision,
            interaction_revision=2,
            binding_id="toggle_nucleus_binding",
        )
    assert (
        rejected.value.code == CanvasInteractionRejectionCode.INTERACTION_REVISION_STALE
    )


def test_canvas_session_resolves_explore_to_semantic_entity_context() -> None:
    payload = _explore_draft().model_dump(mode="json", by_alias=True)
    payload["entities"] = [
        {
            "id": "nucleus_entity",
            "name": "Nucleus",
            "description": "The nucleus stores the cell's instructions.",
            "nodeIds": ["nucleus", "nucleus_label"],
            "facts": ["It helps coordinate the cell."],
        }
    ]
    state = CanvasSessionState()
    state.apply_draft(
        ModelCanvasDraft.model_validate(payload),
        transaction_id="canvas_explore_context",
    )
    scene = state.scene
    assert scene is not None

    interaction = state.record_interaction(
        scene_id=scene.id,
        scene_revision=scene.revision,
        interaction_revision=1,
        binding_id="explore_nucleus_binding",
    )

    assert interaction.action == "explore"
    assert interaction.target_id == "nucleus"
    assert (
        interaction.target_description == "The nucleus stores the cell's instructions."
    )
    context = state.render_realtime_context()
    assert context is not None
    assert 'action="explore"' in context
    assert "The nucleus stores the cell's instructions." in context
