from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from xml.etree import ElementTree

import pytest

from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_session import SvgCanvasSessionState
from ava_backend.canvas.svg_workspace import (
    ActiveSvgCanvasScene,
    CanvasNavigationDirection,
    CanvasSceneSelector,
    CanvasWorkspaceManifest,
    CanvasWorkspaceManifestScene,
    RecordedSvgCanvasInteraction,
    SvgCanvasWorkspace,
    SvgCanvasExecutionStatus,
    SvgCanvasWorkspaceError,
)
from ava_backend.realtime.tool_contract import ManageCanvasPresentationRequest


def _active(index: int, *, revision: int = 1) -> ActiveSvgCanvasScene:
    document = SvgDocumentValidator().validate(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        'data-ava-interaction-mode="none">'
        f"<title>Diagram {index}</title><desc>Summary {index}</desc>"
        f'<circle id="dot-{index}" cx="50" cy="50" r="20"/></svg>'
    )
    return ActiveSvgCanvasScene(
        generation_id=f"generation-{index}-{revision}",
        source_turn_id=f"turn-{index}-{revision}",
        scene_id=f"scene-{index}",
        revision=revision,
        document=document,
        first_rendered_sample=0,
    )


def _row(
    scene: ActiveSvgCanvasScene,
    *,
    creation_sequence: int,
    modification_sequence: int | None = None,
) -> CanvasWorkspaceManifestScene:
    manifest = scene.document.manifest
    return CanvasWorkspaceManifestScene(
        scene_id=scene.scene_id,
        scene_revision=scene.revision,
        generation_id=scene.generation_id,
        source_turn_id=scene.source_turn_id,
        svg_sha256=scene.document.svg_sha256,
        manifest_sha256=scene.document.manifest_sha256,
        svg_byte_length=len(scene.document.svg_bytes),
        manifest_byte_length=len(scene.document.manifest_bytes),
        renderer_capability_version=manifest["rendererCapabilityVersion"],
        validator_version=manifest["validatorVersion"],
        manifest_version=manifest["manifestVersion"],
        creation_sequence=creation_sequence,
        modification_sequence=modification_sequence,
        title=scene.document.title,
        summary=scene.document.description,
    )


def _manifest(
    scenes: list[ActiveSvgCanvasScene],
    *,
    revision: int = 0,
    focused_index: int | None = 0,
) -> CanvasWorkspaceManifest:
    return CanvasWorkspaceManifest(
        protocol_version=4,
        workspace_epoch=7,
        workspace_revision=revision,
        next_creation_sequence=len(scenes) + 1,
        next_modification_sequence=1,
        focused_scene_id=(
            scenes[focused_index].scene_id if focused_index is not None else None
        ),
        scenes=tuple(
            _row(scene, creation_sequence=index + 1)
            for index, scene in enumerate(scenes)
        ),
    )


def _canonical_manifest(workspace: SvgCanvasWorkspace) -> CanvasWorkspaceManifest:
    return CanvasWorkspaceManifest(
        protocol_version=4,
        workspace_epoch=workspace.workspace_epoch,
        workspace_revision=workspace.workspace_revision,
        next_creation_sequence=workspace.next_creation_sequence,
        next_modification_sequence=workspace.next_modification_sequence,
        focused_scene_id=workspace.focused_scene_id,
        scenes=workspace.manifest_scenes,
    )


def _hydrate_in_order(
    workspace: SvgCanvasWorkspace,
    scenes: list[ActiveSvgCanvasScene],
    indexes: tuple[int, ...],
) -> None:
    for index in indexes:
        workspace.focus_retained(scenes[index])


def test_six_scene_manifest_preserves_durable_order_and_navigates_sequentially() -> (
    None
):
    scenes = [_active(index) for index in range(1, 7)]
    workspace = SvgCanvasWorkspace()
    assert workspace.adopt_manifest(_manifest(scenes)) is True
    workspace.restore_focused(scenes[0])

    observed = []
    for _ in range(5):
        resolution = workspace.resolve_navigation(CanvasNavigationDirection.NEXT)
        assert resolution.changed is True
        assert resolution.scene is not None
        workspace.focus_retained(scenes[resolution.position - 1])
        observed.append((resolution.position, resolution.total))

    assert workspace.ordered_scene_ids() == tuple(scene.scene_id for scene in scenes)
    assert observed == [(2, 6), (3, 6), (4, 6), (5, 6), (6, 6)]
    boundary = workspace.resolve_navigation(CanvasNavigationDirection.NEXT)
    assert boundary.changed is False
    assert boundary.reason == "endOfWorkspace"
    assert boundary.position == 6
    assert boundary.has_next is False

    workspace.focus_retained(scenes[4])
    after_manual_focus = workspace.resolve_navigation(CanvasNavigationDirection.NEXT)
    assert (after_manual_focus.position, after_manual_focus.total) == (6, 6)
    workspace.focus_retained(scenes[5])
    previous_positions = []
    for expected_position in range(5, 0, -1):
        resolution = workspace.resolve_navigation(CanvasNavigationDirection.PREVIOUS)
        previous_positions.append(resolution.position)
        workspace.focus_retained(scenes[expected_position - 1])
    assert previous_positions == [5, 4, 3, 2, 1]
    start = workspace.resolve_navigation(CanvasNavigationDirection.PREVIOUS)
    assert start.changed is False
    assert start.reason == "startOfWorkspace"


def test_focused_restore_without_manifest_never_enables_arrival_order_navigation() -> (
    None
):
    workspace = SvgCanvasWorkspace()
    workspace.restore_focused(_active(1))
    with pytest.raises(SvgCanvasWorkspaceError, match="workspaceNotSynchronized"):
        workspace.resolve_navigation(CanvasNavigationDirection.NEXT)


def test_manifest_retry_with_same_structure_revision_and_focus_is_idempotent() -> None:
    scenes = [_active(index) for index in range(1, 4)]
    workspace = SvgCanvasWorkspace()
    manifest = _manifest(scenes, revision=11)
    assert workspace.adopt_manifest(manifest) is True
    assert workspace.adopt_manifest(manifest) is False


def test_manifest_retry_with_focus_only_change_is_structurally_idempotent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scenes = [_active(index) for index in range(1, 4)]
    workspace = SvgCanvasWorkspace()
    manifest = _manifest(scenes, revision=11)
    assert workspace.adopt_manifest(manifest) is True

    focus_only_change = replace(
        manifest,
        focused_scene_id=scenes[1].scene_id,
    )
    with caplog.at_level("INFO", logger="ava_backend.canvas.svg_workspace"):
        assert workspace.adopt_manifest(focus_only_change) is False

    record = next(
        record
        for record in caplog.records
        if record.getMessage().startswith("canvas_workspace_manifest_adoption")
    )
    context = record.__dict__["ava_context"]
    assert context["component"] == "svgCanvasWorkspace"
    assert context["workspaceEpoch"] == 7
    assert context["workspaceRevision"] == 11
    assert context["incomingFocusedSceneId"] == scenes[1].scene_id
    assert context["canonicalFocusedSceneId"] is None
    assert context["structuralDigestIncoming"] == context["structuralDigestCanonical"]
    assert len(context["structuralDigestIncoming"]) == 64
    assert context["classification"] == "structurallyIdempotent"


def test_stale_manifest_focus_does_not_replace_newer_live_focus() -> None:
    scenes = [_active(index) for index in range(1, 4)]
    workspace = SvgCanvasWorkspace()
    manifest = _manifest(scenes, revision=11, focused_index=0)
    assert workspace.adopt_manifest(manifest) is True
    workspace.restore_focused(scenes[0])
    workspace.focus_retained(scenes[2])

    assert workspace.adopt_manifest(manifest) is False
    assert workspace.focused_scene_id == scenes[2].scene_id


def test_same_revision_with_different_scene_order_remains_a_conflict() -> None:
    scenes = [_active(index) for index in range(1, 4)]
    workspace = SvgCanvasWorkspace()
    manifest = _manifest(scenes, revision=11)
    assert workspace.adopt_manifest(manifest) is True

    reordered_rows = tuple(
        replace(row, creation_sequence=index)
        for index, row in enumerate(reversed(manifest.scenes), start=1)
    )
    conflicting = replace(
        manifest,
        scenes=reordered_rows,
        focused_scene_id=reordered_rows[0].scene_id,
    )
    with pytest.raises(SvgCanvasWorkspaceError, match="conflicts"):
        workspace.adopt_manifest(conflicting)


@pytest.mark.parametrize(
    "changed_row",
    (
        lambda row: replace(row, svg_sha256="0" * 64),
        lambda row: replace(row, scene_revision=row.scene_revision + 1),
    ),
    ids=("hash", "scene-revision"),
)
def test_same_revision_with_different_scene_identity_remains_a_conflict(
    changed_row: Callable[[CanvasWorkspaceManifestScene], CanvasWorkspaceManifestScene],
) -> None:
    scenes = [_active(index) for index in range(1, 4)]
    workspace = SvgCanvasWorkspace()
    manifest = _manifest(scenes, revision=11)
    assert workspace.adopt_manifest(manifest) is True
    conflicting = replace(
        manifest,
        scenes=(changed_row(manifest.scenes[0]), *manifest.scenes[1:]),
    )
    with pytest.raises(SvgCanvasWorkspaceError, match="conflicts"):
        workspace.adopt_manifest(conflicting)


def test_create_refreshes_adopted_structural_identity_from_canonical_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scenes = [_active(index) for index in range(1, 4)]
    workspace = SvgCanvasWorkspace()
    assert workspace.adopt_manifest(_manifest(scenes, revision=11)) is True

    created = workspace.commit(
        _active(4),
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=7,
    )

    assert workspace.workspace_revision == 12
    assert created.workspace_revision == 12
    assert workspace.ordered_scene_ids() == (
        "scene-1",
        "scene-2",
        "scene-3",
        "scene-4",
    )
    with caplog.at_level("INFO", logger="ava_backend.canvas.svg_workspace"):
        assert workspace.adopt_manifest(_canonical_manifest(workspace)) is False
    assert any(
        record.__dict__.get("ava_context", {}).get("classification")
        == "structurallyIdempotent"
        for record in caplog.records
    )


def test_current_revision_with_different_structure_remains_a_conflict() -> None:
    scenes = [_active(index) for index in range(1, 4)]
    workspace = SvgCanvasWorkspace()
    assert workspace.adopt_manifest(_manifest(scenes, revision=11)) is True
    workspace.commit(
        _active(4),
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=7,
    )
    current = _canonical_manifest(workspace)
    conflicting = replace(
        current,
        scenes=(
            replace(current.scenes[0], summary="Conflicting summary"),
            *current.scenes[1:],
        ),
    )

    with pytest.raises(SvgCanvasWorkspaceError, match="conflicts"):
        workspace.adopt_manifest(conflicting)


def test_reconnect_focus_hint_remains_hidden_and_cannot_roll_back_live_focus() -> None:
    scenes = [_active(index) for index in range(1, 4)]
    manifest = _manifest(scenes, revision=11, focused_index=1)
    workspace = SvgCanvasWorkspace()
    assert workspace.adopt_manifest(manifest) is True
    state = SvgCanvasSessionState(workspace=workspace, inherit_focus=False)

    restored = state.restore_focused(scenes[1])
    assert restored.scene_id == manifest.focused_scene_id
    assert state.presentation_visible is False
    assert state.presented_scene is None

    state.focus_retained(scenes[2], focus_revision=2)
    stale_focus_manifest = replace(manifest, focused_scene_id=scenes[0].scene_id)
    assert workspace.adopt_manifest(stale_focus_manifest) is False
    assert state.focused_scene == scenes[2]
    assert state.focus_revision == 2
    assert state.presentation_visible is False
    assert state.presented_scene is None


def test_lazy_hydration_validates_identity_without_reordering() -> None:
    scenes = [_active(index) for index in range(1, 7)]
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(_manifest(scenes))
    workspace.restore_focused(scenes[0])
    workspace.focus_retained(scenes[4])

    assert workspace.ordered_scene_ids() == tuple(scene.scene_id for scene in scenes)
    assert workspace.resolve_navigation(CanvasNavigationDirection.NEXT).position == 6

    wrong = replace(scenes[2], generation_id="generation-conflict")
    with pytest.raises(SvgCanvasWorkspaceError, match="conflicts"):
        workspace.focus_retained(wrong)


def test_manifest_focus_restore_accepts_b_without_presenting_it() -> None:
    scenes = [_active(index) for index in range(1, 4)]
    workspace = SvgCanvasWorkspace()
    manifest = _manifest(scenes, focused_index=1)
    workspace.adopt_manifest(manifest)
    state = SvgCanvasSessionState(workspace=workspace, inherit_focus=False)

    accepted = state.restore_focused(scenes[1])

    assert workspace.ordered_scene_ids() == ("scene-1", "scene-2", "scene-3")
    assert manifest.focused_scene_id == "scene-2"
    assert accepted.scene_id == "scene-2"
    assert state.focused_scene == accepted
    assert state.presentation_visible is False
    assert state.presented_scene is None


def test_capacity_ten_create_evicts_oldest_in_one_structural_revision() -> None:
    scenes = [_active(index) for index in range(1, 11)]
    telemetry = []
    occurred_at = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    workspace = SvgCanvasWorkspace(
        eviction_telemetry_sink=telemetry.append,
        utc_now=lambda: occurred_at,
    )
    workspace.adopt_manifest(_manifest(scenes, revision=20))
    created = workspace.commit(
        _active(11),
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=7,
    )

    mutation = workspace.last_structural_mutation
    assert mutation is not None
    assert mutation.workspace_revision == 21
    assert mutation.evicted_scene_id == "scene-1"
    assert created.workspace_revision == 21
    assert workspace.ordered_scene_ids() == tuple(
        f"scene-{index}" for index in range(2, 12)
    )
    current = _canonical_manifest(workspace)
    assert tuple(row.scene_id for row in current.scenes) == tuple(
        f"scene-{index}" for index in range(2, 12)
    )
    assert workspace.adopt_manifest(current) is False
    assert len(telemetry) == 1
    assert telemetry[0].as_context() == {
        "component": "svgCanvasWorkspace",
        "eventType": "canvasSceneEvicted",
        "sceneId": "scene-1",
        "sceneRevision": 1,
        "generationId": "generation-1-1",
        "sourceTurnId": "turn-1-1",
        "title": "Diagram 1",
        "creationSequence": 1,
        "workspaceEpoch": 7,
        "workspaceRevision": 21,
        "evictionReason": "capacityFifo",
        "occurredAt": "2026-09-06T12:00:00+00:00",
    }


def test_full_manifest_sparse_out_of_order_hydration_evicts_canonical_oldest() -> None:
    scenes = [_active(index) for index in range(1, 11)]
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(_manifest(scenes, revision=20, focused_index=None))
    _hydrate_in_order(workspace, scenes, (9, 5, 7, 0))

    created = workspace.commit(
        _active(11),
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=7,
    )

    assert workspace.ordered_scene_ids() == tuple(
        f"scene-{index}" for index in range(2, 12)
    )
    assert [scene.scene_id for scene in workspace.scenes] == [
        "scene-10",
        "scene-6",
        "scene-8",
        "scene-11",
    ]
    assert workspace.scene_count == 10
    assert workspace.workspace_revision == 21
    assert created.evicted_scene_id == "scene-1"


def test_canonical_capacity_eviction_does_not_require_evicted_scene_hydration() -> None:
    scenes = [_active(index) for index in range(1, 11)]
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(_manifest(scenes, revision=20, focused_index=None))
    _hydrate_in_order(workspace, scenes, (9, 5))

    workspace.commit(
        _active(11),
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=7,
    )

    assert [scene.scene_id for scene in workspace.scenes] == [
        "scene-10",
        "scene-6",
        "scene-11",
    ]
    assert workspace.scene("scene-1") is None


def test_canonical_capacity_eviction_removes_hydrated_scene_by_id() -> None:
    scenes = [_active(index) for index in range(1, 11)]
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(_manifest(scenes, revision=20, focused_index=None))
    _hydrate_in_order(workspace, scenes, (9, 5, 0, 7))

    workspace.commit(
        _active(11),
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=7,
    )

    assert [scene.scene_id for scene in workspace.scenes] == [
        "scene-10",
        "scene-6",
        "scene-8",
        "scene-11",
    ]
    assert workspace.scene("scene-1") is None
    assert workspace.scene("scene-10") is scenes[9]


def test_focused_canonical_capacity_eviction_clears_focus() -> None:
    scenes = [_active(index) for index in range(1, 11)]
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(_manifest(scenes, revision=20, focused_index=None))
    _hydrate_in_order(workspace, scenes, (9, 5, 7, 0))
    assert workspace.focused_scene_id == "scene-1"

    workspace.commit(
        _active(11),
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=7,
        foreground=False,
    )

    assert workspace.focused_scene_id is None
    assert workspace.scene("scene-1") is None


def test_capacity_eviction_telemetry_uses_canonical_manifest_identity() -> None:
    scenes = [_active(index) for index in range(1, 11)]
    telemetry = []
    workspace = SvgCanvasWorkspace(eviction_telemetry_sink=telemetry.append)
    workspace.adopt_manifest(_manifest(scenes, revision=20, focused_index=None))
    _hydrate_in_order(workspace, scenes, (9, 5))

    workspace.commit(
        _active(11),
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=7,
    )

    assert len(telemetry) == 1
    assert telemetry[0].scene_id == "scene-1"
    assert telemetry[0].generation_id == scenes[0].generation_id
    assert telemetry[0].scene_id != workspace.scenes[0].scene_id


def test_capacity_eviction_digest_matches_resulting_canonical_manifest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scenes = [_active(index) for index in range(1, 11)]
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(_manifest(scenes, revision=20, focused_index=None))
    _hydrate_in_order(workspace, scenes, (9, 5, 7, 0))
    workspace.commit(
        _active(11),
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=7,
    )

    resulting_manifest = _canonical_manifest(workspace)
    with caplog.at_level("INFO", logger="ava_backend.canvas.svg_workspace"):
        assert workspace.adopt_manifest(resulting_manifest) is False

    context = next(
        record.__dict__["ava_context"]
        for record in caplog.records
        if record.getMessage().startswith("canvas_workspace_manifest_adoption")
    )
    assert context["classification"] == "structurallyIdempotent"
    assert context["structuralDigestIncoming"] == context["structuralDigestCanonical"]


def test_failed_create_precondition_leaves_canonical_workspace_unchanged() -> None:
    scenes = [_active(index) for index in range(1, 11)]
    manifest = _manifest(scenes, revision=20, focused_index=None)
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(manifest)
    _hydrate_in_order(workspace, scenes, (9, 5, 7, 0))
    candidate = _active(11)
    workspace.start_execution(
        generation_id=candidate.generation_id,
        source_turn_id=candidate.source_turn_id,
        operation="new",
    )
    hydrated_before = workspace.scenes
    recency_before = tuple(
        (scene.scene_id, workspace.scene_recency(scene.scene_id))
        for scene in hydrated_before
    )
    digest_before = workspace._structural_manifest_digest

    with pytest.raises(
        SvgCanvasWorkspaceError,
        match="must be presented before activation",
    ):
        workspace.commit(
            candidate,
            base_scene_id=None,
            base_revision=0,
            workspace_epoch=7,
        )

    assert workspace.manifest_scenes == manifest.scenes
    assert workspace.scenes == hydrated_before
    assert workspace.workspace_revision == 20
    assert workspace.next_creation_sequence == 11
    assert workspace.next_modification_sequence == 1
    assert workspace._structural_manifest_digest == digest_before
    assert workspace.focused_scene_id == "scene-1"
    assert (
        tuple(
            (scene.scene_id, workspace.scene_recency(scene.scene_id))
            for scene in workspace.scenes
        )
        == recency_before
    )
    assert workspace.last_structural_mutation is None
    assert workspace.adopt_manifest(manifest) is False


def test_restart_restore_out_of_order_full_hydration_uses_manifest_eviction_order() -> (
    None
):
    scenes = [_active(index) for index in range(1, 11)]
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(_manifest(scenes, revision=20, focused_index=None))
    _hydrate_in_order(workspace, scenes, (9, 5, 7, 0, 3, 1, 8, 2, 6, 4))

    created = workspace.commit(
        _active(11),
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=7,
    )

    assert created.evicted_scene_id == "scene-1"
    assert workspace.ordered_scene_ids() == tuple(
        f"scene-{index}" for index in range(2, 12)
    )
    assert workspace.scene("scene-10") is scenes[9]
    assert workspace.scene("scene-1") is None


def test_create_after_six_scenes_appends_at_position_seven() -> None:
    scenes = [_active(index) for index in range(1, 7)]
    telemetry = []
    workspace = SvgCanvasWorkspace(eviction_telemetry_sink=telemetry.append)
    workspace.adopt_manifest(_manifest(scenes, revision=20))

    workspace.commit(
        _active(7),
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=7,
    )

    assert workspace.ordered_scene_ids() == tuple(
        f"scene-{index}" for index in range(1, 8)
    )
    assert workspace.ordered_scene_ids().index("scene-7") + 1 == 7
    assert workspace.workspace_revision == 21
    assert telemetry == []


def test_eviction_telemetry_failure_does_not_rollback_canonical_create(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scenes = [_active(index) for index in range(1, 11)]

    def failing_sink(_event: object) -> None:
        raise RuntimeError("telemetry unavailable")

    workspace = SvgCanvasWorkspace(eviction_telemetry_sink=failing_sink)
    workspace.adopt_manifest(_manifest(scenes, revision=30))
    created_scene = _active(11)
    workspace.start_execution(
        generation_id=created_scene.generation_id,
        source_turn_id=created_scene.source_turn_id,
        operation="new",
    )
    workspace.advance_execution(
        created_scene.generation_id,
        SvgCanvasExecutionStatus.VALIDATED_CANDIDATE,
        scene_id=created_scene.scene_id,
        scene_revision=created_scene.revision,
    )
    for status in (
        SvgCanvasExecutionStatus.PREPARING,
        SvgCanvasExecutionStatus.PRESENTATION_SCHEDULED,
        SvgCanvasExecutionStatus.PRESENTED,
    ):
        workspace.advance_execution(created_scene.generation_id, status)

    with caplog.at_level(
        "WARNING",
        logger="ava_backend.canvas.svg_workspace",
    ):
        created = workspace.commit(
            created_scene,
            base_scene_id=None,
            base_revision=0,
            workspace_epoch=7,
        )

    assert created.scene_id == "scene-11"
    assert workspace.workspace_revision == 31
    assert workspace.ordered_scene_ids() == tuple(
        f"scene-{index}" for index in range(2, 12)
    )
    previous = workspace.resolve_navigation(CanvasNavigationDirection.PREVIOUS)
    assert previous.scene is not None
    assert previous.scene.scene_id == "scene-10"
    execution = workspace.execution(created_scene.generation_id)
    assert execution is not None
    assert execution.status is SvgCanvasExecutionStatus.ACTIVE
    assert "canvas_scene_eviction_telemetry_failed" in caplog.text


def test_revising_older_scene_preserves_creation_and_separates_latest_semantics() -> (
    None
):
    scenes = [_active(index) for index in range(1, 6)]
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(_manifest(scenes, revision=3))
    workspace.restore_focused(scenes[1])
    revised = _active(2, revision=2)
    workspace.commit(
        revised,
        base_scene_id="scene-2",
        base_revision=1,
        workspace_epoch=7,
    )

    assert workspace.ordered_scene_ids().index("scene-2") == 1
    assert workspace.manifest_scene("scene-2").creation_sequence == 2
    assert workspace.latest_created_scene_id == "scene-5"
    assert workspace.latest_modified_scene_id == "scene-2"
    assert workspace.workspace_revision == 4
    assert workspace.adopt_manifest(_canonical_manifest(workspace)) is False


def test_focus_presentation_and_interaction_remain_non_structural() -> None:
    scenes = [_active(1), _active(2)]
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(_manifest(scenes, revision=5, focused_index=0))
    workspace.restore_focused(scenes[0])
    state = SvgCanvasSessionState(workspace=workspace)
    original_revision = workspace.workspace_revision

    state.focus_retained(scenes[1], focus_revision=9)
    state.set_presentation_visible(True)
    workspace.record_interaction(
        RecordedSvgCanvasInteraction(
            scene_id=scenes[1].scene_id,
            scene_revision=scenes[1].revision,
            interaction_revision=1,
            svg_sha256=scenes[1].document.svg_sha256,
            manifest_sha256=scenes[1].document.manifest_sha256,
            binding_id="local-binding",
            interaction_mode="none",
            target_id="local-target",
            accessibility_label="Local target",
            step_index=None,
        )
    )

    assert workspace.workspace_revision == original_revision
    assert workspace.focus_revision == 9
    assert state.presentation_visible is True
    assert workspace.adopt_manifest(_canonical_manifest(workspace)) is False


def test_legacy_manifest_keeps_modification_history_unknown() -> None:
    scenes = [_active(index) for index in range(1, 7)]
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(_manifest(scenes))

    assert [row.creation_sequence for row in workspace.manifest_scenes] == list(
        range(1, 7)
    )
    assert all(row.modification_sequence is None for row in workspace.manifest_scenes)
    assert workspace.latest_modified_scene_id is None


def test_duplicate_semantic_titles_remain_distinct_canonical_identities() -> None:
    scenes = [_active(1), _active(2)]
    base = _manifest(scenes)
    duplicate_titles = replace(
        base,
        scenes=tuple(
            replace(row, title="Heart", summary="A heart diagram")
            for row in base.scenes
        ),
    )
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(duplicate_titles)

    assert [row.scene_id for row in workspace.manifest_scenes] == [
        "scene-1",
        "scene-2",
    ]
    assert [row.title for row in workspace.manifest_scenes] == ["Heart", "Heart"]
    assert workspace.latest_created_scene_id == "scene-2"


def test_synchronized_context_exposes_exact_workspace_header_and_compact_ranks() -> (
    None
):
    scenes = [_active(1), _active(2), _active(3)]
    base = _manifest(scenes, revision=12, focused_index=1)
    manifest = replace(
        base,
        next_modification_sequence=8,
        scenes=(
            replace(base.scenes[0], modification_sequence=7),
            base.scenes[1],
            base.scenes[2],
        ),
    )
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(manifest)
    workspace.restore_focused(scenes[1])
    state = SvgCanvasSessionState(workspace=workspace)
    state.set_presentation_visible(True)

    rendered = state.render_realtime_context()
    assert (
        '<canvas_workspace retained_count="3" latest_created_position="3" latest_modified_position="1" focused_position="2"'
        in rendered
    )
    assert (
        '<scene position="1" title="Diagram 1" summary="Summary 1" created_order="1" modified_order="1"/>'
        in rendered
    )
    assert (
        '<scene position="2" title="Diagram 2" summary="Summary 2" created_order="2" modified_order="none"/>'
        in rendered
    )
    assert "scene-1" not in rendered
    assert "workspace_revision" not in rendered


def test_model_facing_directory_keeps_ten_maximum_rows_selector_safe_and_bounded() -> (
    None
):
    validator = SvgDocumentValidator()
    scenes = []
    for index in range(1, 11):
        title_prefix = f"Diagram {index} "
        title = title_prefix + ("x" * (160 - len(title_prefix)))
        summary = "x" * 800
        document = validator.validate(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
            'data-ava-interaction-mode="none">'
            f"<title>{title}</title><desc>{summary}</desc>"
            f'<circle id="dot-{index}" cx="50" cy="50" r="20"/>'
            "</svg>"
        )
        scenes.append(
            ActiveSvgCanvasScene(
                generation_id=f"maximum-generation-{index}",
                source_turn_id=f"maximum-turn-{index}",
                scene_id=f"maximum-scene-{index}",
                revision=1,
                document=document,
                first_rendered_sample=0,
            )
        )
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(_manifest(scenes, focused_index=4))
    workspace.restore_focused(scenes[4])
    state = SvgCanvasSessionState(workspace=workspace)
    state.set_presentation_visible(True)

    rendered = state.render_realtime_context()
    root = ElementTree.fromstring(rendered)
    directory = root.findall("./retained_visuals/scene")

    assert len(rendered) <= 2_400
    assert [row.attrib["position"] for row in directory] == [
        str(index) for index in range(1, 11)
    ]
    assert all(row.attrib["title"] for row in directory)
    assert all(row.attrib["summary"] for row in directory)
    assert all(row.attrib["created_order"] for row in directory)
    assert all(row.attrib["modified_order"] for row in directory)
    for row in directory:
        request = ManageCanvasPresentationRequest.model_validate(
            {
                "action": "showTitle",
                "title": row.attrib["title"],
                "completion": "requestComplete",
            }
        )
        assert request.presentation_selector is CanvasSceneSelector.TITLE
        resolution = workspace.resolve_scene_selector(
            request.presentation_selector,
            title=request.title,
        )
        assert resolution.scene is not None


def test_model_facing_directory_bounds_extreme_manifest_sequences() -> None:
    validator = SvgDocumentValidator()
    scenes = []
    for index in range(1, 11):
        document = validator.validate(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
            'data-ava-interaction-mode="none">'
            f"<title>{'T' * 160}</title><desc>{'x' * 800}</desc>"
            f'<circle id="dot-{index}" cx="50" cy="50" r="20"/>'
            "</svg>"
        )
        scenes.append(
            ActiveSvgCanvasScene(
                generation_id=f"sequence-generation-{index}",
                source_turn_id=f"sequence-turn-{index}",
                scene_id=f"sequence-scene-{index}",
                revision=1,
                document=document,
                first_rendered_sample=0,
            )
        )

    sequence_base = 10**300
    base = _manifest(scenes, focused_index=4)
    manifest = replace(
        base,
        next_creation_sequence=sequence_base + 11,
        next_modification_sequence=sequence_base + 11,
        scenes=tuple(
            replace(
                row,
                creation_sequence=sequence_base + position,
                modification_sequence=sequence_base + (11 - position),
            )
            for position, row in enumerate(base.scenes, start=1)
        ),
    )
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(manifest)
    workspace.restore_focused(scenes[4])
    state = SvgCanvasSessionState(workspace=workspace)
    state.set_presentation_visible(True)

    rendered = state.render_realtime_context()
    directory = ElementTree.fromstring(rendered).findall("./retained_visuals/scene")

    assert len(rendered) <= 2_400
    assert [row.attrib["position"] for row in directory] == [
        str(index) for index in range(1, 11)
    ]
    assert [row.attrib["title"] for row in directory] == [
        f"{'T' * (56 - len(f' [position {index}]'))} [position {index}]"
        for index in range(1, 11)
    ]
    assert str(sequence_base) not in rendered
    for position, row in enumerate(directory, start=1):
        resolution = workspace.resolve_scene_selector(
            CanvasSceneSelector.TITLE,
            title=row.attrib["title"],
        )
        assert resolution.position == position


def test_model_facing_titles_disambiguate_duplicate_canonical_titles() -> None:
    scenes = [_active(1), _active(2)]
    base = _manifest(scenes)
    manifest = replace(
        base,
        scenes=(
            replace(base.scenes[0], title="Same title"),
            replace(base.scenes[1], title="Same title"),
        ),
    )
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(manifest)

    labels = workspace.model_facing_titles()
    assert labels["scene-1"] == "Same title [position 1]"
    assert labels["scene-2"] == "Same title [position 2]"
    assert (
        workspace.resolve_scene_selector(
            CanvasSceneSelector.TITLE, title=labels["scene-1"]
        ).position
        == 1
    )
    assert (
        workspace.resolve_scene_selector(
            CanvasSceneSelector.TITLE, title=labels["scene-2"]
        ).position
        == 2
    )


def test_model_facing_titles_do_not_collide_with_a_canonical_title() -> None:
    scenes = [_active(1), _active(2), _active(3)]
    base = _manifest(scenes)
    manifest = replace(
        base,
        scenes=(
            replace(base.scenes[0], title="Same title"),
            replace(base.scenes[1], title="Same title"),
            replace(base.scenes[2], title="Same title [position 1]"),
        ),
    )
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(manifest)

    labels = workspace.model_facing_titles()
    assert len({label.casefold() for label in labels.values()}) == 3
    for position, scene in enumerate(scenes, start=1):
        assert (
            workspace.resolve_scene_selector(
                CanvasSceneSelector.TITLE,
                title=labels[scene.scene_id],
            ).position
            == position
        )


def test_semantic_selector_resolution_uses_manifest_order_and_fails_closed() -> None:
    scenes = [_active(1), _active(2), _active(3)]
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(_manifest(scenes))
    workspace.restore_focused(scenes[1])

    assert workspace.resolve_scene_selector(CanvasSceneSelector.CURRENT).position == 2
    assert (
        workspace.resolve_scene_selector(CanvasSceneSelector.CURRENT).scene
        == workspace.manifest_scenes[1]
    )
    assert workspace.resolve_scene_selector(CanvasSceneSelector.NEXT).position == 3
    assert workspace.resolve_scene_selector(CanvasSceneSelector.PREVIOUS).position == 1
    assert workspace.resolve_scene_selector(CanvasSceneSelector.FIRST).position == 1
    assert workspace.resolve_scene_selector(CanvasSceneSelector.LAST).position == 3
    assert (
        workspace.resolve_scene_selector(CanvasSceneSelector.POSITION, position=3).scene
        == workspace.manifest_scenes[2]
    )
    assert (
        workspace.resolve_scene_selector(
            CanvasSceneSelector.TITLE, title="  diagram   1 "
        ).position
        == 1
    )
    assert (
        workspace.resolve_scene_selector(
            CanvasSceneSelector.TITLE, title="unknown"
        ).reason
        == "titleNotFound"
    )

    duplicate_manifest = replace(
        _manifest(scenes),
        scenes=tuple(replace(row, title="Same") for row in _manifest(scenes).scenes),
    )
    duplicates = SvgCanvasWorkspace()
    duplicates.adopt_manifest(duplicate_manifest)
    duplicates.restore_focused(scenes[0])
    assert (
        duplicates.resolve_scene_selector(
            CanvasSceneSelector.TITLE, title="same"
        ).reason
        == "ambiguousTitle"
    )

    # Position selector lower and upper bounds enforce canonical 1-based manifest index.
    assert (
        workspace.resolve_scene_selector(CanvasSceneSelector.POSITION, position=1).scene
        == workspace.manifest_scenes[0]
    )
    assert (
        workspace.resolve_scene_selector(CanvasSceneSelector.POSITION, position=3).scene
        == workspace.manifest_scenes[2]
    )
    for invalid_position in (0, -1, 4):
        resolution = workspace.resolve_scene_selector(
            CanvasSceneSelector.POSITION, position=invalid_position
        )
        assert resolution.reason == "positionOutOfRange"
        assert resolution.scene is None
        assert workspace.focused_scene_id == scenes[1].scene_id

    # Navigation boundaries via resolve_scene_selector.
    workspace.restore_focused(scenes[0])
    start_boundary = workspace.resolve_scene_selector(CanvasSceneSelector.PREVIOUS)
    assert start_boundary.reason == "startOfWorkspace"
    assert start_boundary.scene is None
    assert workspace.focused_scene_id == scenes[0].scene_id

    workspace.restore_focused(scenes[2])
    end_boundary = workspace.resolve_scene_selector(CanvasSceneSelector.NEXT)
    assert end_boundary.reason == "endOfWorkspace"
    assert end_boundary.scene is None
    assert workspace.focused_scene_id == scenes[2].scene_id


def test_semantic_selector_rejects_unsynchronized_navigation() -> None:
    workspace = SvgCanvasWorkspace()
    workspace.restore_focused(_active(1))

    for selector in (
        CanvasSceneSelector.CURRENT,
        CanvasSceneSelector.NEXT,
        CanvasSceneSelector.PREVIOUS,
        CanvasSceneSelector.FIRST,
        CanvasSceneSelector.LAST,
        CanvasSceneSelector.POSITION,
        CanvasSceneSelector.TITLE,
    ):
        assert (
            workspace.resolve_scene_selector(
                selector, position=1, title="Diagram 1"
            ).reason
            == "workspaceNotSynchronized"
        )
