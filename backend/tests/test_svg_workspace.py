from __future__ import annotations

import pytest

from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_workspace import (
    ActiveSvgCanvasScene,
    SvgCanvasWorkspace,
    SvgCanvasWorkspaceError,
    SvgCanvasWorkspaceStore,
)


def _scene(scene: str, revision: int) -> ActiveSvgCanvasScene:
    document = SvgDocumentValidator().validate(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600">'
        f"<title>Scene {scene} revision {revision}</title>"
        f"<desc>Exact committed scene {scene} revision {revision}.</desc>"
        f'<text id="label-{scene}" x="100" y="100">{scene}{revision}</text>'
        "</svg>"
    )
    return ActiveSvgCanvasScene(
        generation_id=f"generation-{scene}-{revision}",
        source_turn_id=f"turn-{scene}-{revision}",
        scene_id=f"scene-{scene}",
        revision=revision,
        document=document,
        first_rendered_sample=4_800,
    )


def _commit_new(
    workspace: SvgCanvasWorkspace,
    scene: str,
) -> ActiveSvgCanvasScene:
    committed = _scene(scene, 1)
    return workspace.commit(
        committed,
        base_scene_id=None,
        base_revision=0,
    )


def _commit_revision(
    workspace: SvgCanvasWorkspace,
    scene: str,
    revision: int,
) -> ActiveSvgCanvasScene:
    committed = _scene(scene, revision)
    return workspace.commit(
        committed,
        base_scene_id=committed.scene_id,
        base_revision=revision - 1,
    )


def test_backend_workspace_max_ten_fifo_and_revision_replacement() -> None:
    workspace = SvgCanvasWorkspace()
    for scene in "ABCDEFGHIJ":
        _commit_new(workspace, scene)

    revised_a = _commit_revision(workspace, "A", 2)

    assert workspace.scene_count == 10
    assert [(scene.scene_id, scene.revision) for scene in workspace.scenes] == [
        ("scene-A", 2),
        *((f"scene-{scene}", 1) for scene in "BCDEFGHIJ"),
    ]
    assert workspace.current is revised_a

    committed_k = _commit_new(workspace, "K")

    assert workspace.scene_count == 10
    assert [scene.scene_id for scene in workspace.scenes] == [
        *(f"scene-{scene}" for scene in "BCDEFGHIJ"),
        "scene-K",
    ]
    assert workspace.current is committed_k


def test_revision_does_not_refresh_backend_fifo() -> None:
    workspace = SvgCanvasWorkspace()
    for scene in "ABC":
        _commit_new(workspace, scene)

    _commit_revision(workspace, "A", 2)

    assert [scene.scene_id for scene in workspace.scenes] == [
        "scene-A",
        "scene-B",
        "scene-C",
    ]


def test_stale_revision_cannot_overwrite_shared_workspace() -> None:
    workspace = SvgCanvasWorkspace()
    _commit_new(workspace, "A")
    active_a2 = _commit_revision(workspace, "A", 2)
    stale_a2 = _scene("A", 2)

    with pytest.raises(SvgCanvasWorkspaceError, match="canonical"):
        workspace.commit(
            stale_a2,
            base_scene_id="scene-A",
            base_revision=1,
        )

    assert workspace.current is active_a2
    assert workspace.scene("scene-A") is active_a2


def test_exact_commit_observation_is_idempotent_and_views_are_read_only() -> None:
    workspace = SvgCanvasWorkspace()
    active = _commit_new(workspace, "A")

    observed = workspace.commit(
        active,
        base_scene_id=None,
        base_revision=0,
    )

    assert observed is active
    assert workspace.scenes == (active,)
    assert isinstance(workspace.scenes, tuple)


def test_different_subjects_have_isolated_workspaces() -> None:
    store = SvgCanvasWorkspaceStore()
    workspace_x = store.workspace_for("child:X")
    workspace_y = store.workspace_for("child:Y")
    active_x = _commit_new(workspace_x, "A")

    assert workspace_x.current is active_x
    assert workspace_y.current is None
    assert workspace_y.scenes == ()
    assert workspace_x is store.workspace_for("child:X")
    assert workspace_y is store.workspace_for("child:Y")


def test_clear_subject_removes_only_authenticated_subject_workspace() -> None:
    store = SvgCanvasWorkspaceStore()
    workspace_x = store.workspace_for("child:X")
    workspace_y = store.workspace_for("child:Y")
    _commit_new(workspace_x, "A")
    active_y = _commit_new(workspace_y, "B")

    store.clear_subject("child:X")

    assert workspace_x.current is None
    assert workspace_x.scenes == ()
    assert workspace_y.current is active_y
    assert workspace_y.scenes == (active_y,)
