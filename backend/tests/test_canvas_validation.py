from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from ava_backend.canvas.protocol import (
    CANVAS_RENDERER_CAPABILITY_MANIFEST,
    AvaCanvasTransaction,
    CanvasRendererCapabilityManifest,
)
from ava_backend.canvas.validation import (
    CanvasPathValidationError,
    CanvasSceneValidator,
    CanvasTransactionRejected,
    validate_canvas_path,
)

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "protocol"
    / "fixtures"
    / "ava_canvas_gallery_v3.json"
)


def _gallery() -> list[dict[str, object]]:
    decoded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(decoded, list)
    return decoded


def test_shared_renderer_capability_manifest_matches_backend_limits() -> None:
    payload = json.loads(
        (FIXTURE.parent / "ava_canvas_capabilities_v3.json").read_text(encoding="utf-8")
    )
    parsed = CanvasRendererCapabilityManifest.model_validate(payload)
    assert parsed.model_dump(mode="json", by_alias=True) == payload
    assert (
        CANVAS_RENDERER_CAPABILITY_MANIFEST.model_dump(mode="json", by_alias=True)
        == payload
    )


def _transaction(index: int) -> AvaCanvasTransaction:
    payload = _gallery()[index]["transaction"]
    return AvaCanvasTransaction.model_validate(payload)


def test_shared_gallery_uses_one_scene_grammar_and_validates_atomically() -> None:
    entries = _gallery()
    assert [entry["name"] for entry in entries] == [
        "free_form_plant_cell",
        "solar_relationship",
        "triangle_construction",
        "force_arrows",
        "event_timeline",
        "bar_line_comparison",
        "custom_curved_path",
        "interactive_drag_toggle_reveal",
    ]

    kinds: set[str] = set()
    for entry in entries:
        transaction = AvaCanvasTransaction.model_validate(entry["transaction"])
        scene = CanvasSceneValidator().apply(transaction)
        assert scene.revision == 1
        assert scene.nodes
        kinds.update(node.kind for node in scene.nodes.values())

    assert kinds == {
        "group",
        "text",
        "line",
        "arrow",
        "circle",
        "ellipse",
        "rect",
        "polygon",
        "polyline",
        "path",
    }


def test_advanced_explore_fixture_is_strict_and_renderer_safe() -> None:
    payload = json.loads(
        (FIXTURE.parent / "ava_canvas_explore_v3.json").read_text(encoding="utf-8")
    )
    transaction = AvaCanvasTransaction.model_validate(payload)
    scene = CanvasSceneValidator().apply(transaction)

    assert scene.revision == 1
    assert len(scene.nodes) == 19
    assert {binding.action.kind for binding in scene.bindings.values()} == {
        "explore",
        "reset",
    }
    delays = [
        operation.delay_ms
        for operation in transaction.operations
        if operation.operation == "animate"
    ]
    assert delays == sorted(delays)


def test_path_parser_accepts_bounded_svg_grammar_and_rejects_bad_groups() -> None:
    validate_canvas_path(
        "M 10 20 L 30 40 H 50 V 60 "
        "C 70 80 90 100 110 120 S 130 140 150 160 "
        "Q 170 180 190 200 T 210 220 A 20 30 0 0 1 250 260 Z"
    )

    for invalid in (
        "L 10 20",
        "M 10",
        "M 0 0 A 10 10 0 2 0 20 20",
        "M 0 0 C 1 2 3",
        "M 0,,0",
    ):
        with pytest.raises(CanvasPathValidationError):
            validate_canvas_path(invalid)


def test_scene_rejects_stale_and_broken_transactions_without_mutation() -> None:
    validator = CanvasSceneValidator()
    original = validator.apply(_transaction(0))
    original_ids = tuple(original.nodes)

    stale_json = deepcopy(_gallery()[0]["transaction"])
    stale_json["id"] = "canvas.gallery.plant.stale"
    stale_json["baseRevision"] = 1
    stale_json["revision"] = 2
    stale_json["operations"] = [
        {
            "operation": "delete",
            "nodeId": "missing_node",
        }
    ]
    stale = AvaCanvasTransaction.model_validate(stale_json)
    with pytest.raises(CanvasTransactionRejected) as rejected:
        validator.apply(stale, original)
    assert rejected.value.code == "unknownNode"
    assert tuple(original.nodes) == original_ids
    assert original.revision == 1

    wrong_revision_json = deepcopy(stale_json)
    wrong_revision_json["baseRevision"] = 2
    wrong_revision_json["revision"] = 3
    wrong_revision = AvaCanvasTransaction.model_validate(wrong_revision_json)
    with pytest.raises(CanvasTransactionRejected) as revision_rejected:
        validator.apply(wrong_revision, original)
    assert revision_rejected.value.code == "revisionMismatch"


def test_interaction_declarations_reference_compatible_nodes_and_state() -> None:
    scene = CanvasSceneValidator().apply(_transaction(7))
    assert len(scene.bindings) == 6
    assert set(scene.state_variables) == {
        "answer_seen",
        "marker_value",
        "detail_level",
    }

    invalid_json = deepcopy(_gallery()[7]["transaction"])
    invalid_json["bindings"][0]["action"]["targetNodeId"] = "missing_node"
    invalid = AvaCanvasTransaction.model_validate(invalid_json)
    with pytest.raises(CanvasTransactionRejected) as rejected:
        CanvasSceneValidator().apply(invalid)
    assert rejected.value.code == "invalidBinding"
