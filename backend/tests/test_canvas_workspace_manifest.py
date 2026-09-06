from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_session import SvgCanvasSessionState
from ava_backend.canvas.svg_workspace import (
    ActiveSvgCanvasScene,
    CanvasNavigationDirection,
    CanvasWorkspaceManifest,
    CanvasWorkspaceManifestScene,
    SvgCanvasWorkspace,
    SvgCanvasExecutionStatus,
    SvgCanvasWorkspaceError,
)


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


def test_structural_commit_advances_workspace_revision_normally() -> None:
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
        '<canvas_workspace synchronized="true" workspace_epoch="7" '
        'workspace_revision="12" total="3" focused_scene_id="scene-2" '
        'focused_revision="1" focused_position="2" has_previous="true" '
        'has_next="true" latest_created_scene_id="scene-3" '
        'latest_modified_scene_id="scene-1"/>'
    ) in rendered
    assert (
        '<visual scene_id="scene-1" revision="1" position="1" '
        'creation_rank="1" modification_rank="1" focused="false" '
        'presented="false">'
    ) in rendered
    assert 'scene_id="scene-2" revision="1" position="2" creation_rank="2"' in rendered
