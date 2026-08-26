from __future__ import annotations

from dataclasses import dataclass

from .svg_document import ValidatedSvgDocument


@dataclass(frozen=True, slots=True)
class ActiveSvgCanvasScene:
    generation_id: str
    source_turn_id: str
    scene_id: str
    revision: int
    document: ValidatedSvgDocument
    first_rendered_sample: int


@dataclass(frozen=True, slots=True)
class RecordedSvgCanvasInteraction:
    scene_id: str
    scene_revision: int
    interaction_revision: int
    svg_sha256: str
    manifest_sha256: str
    binding_id: str
    action: str
    target_id: str
    accessibility_label: str
    declared_value: str | None


class SvgCanvasWorkspaceError(ValueError):
    """A committed canvas update conflicts with canonical workspace state."""


class SvgCanvasWorkspace:
    """Subject-scoped process-lifetime owner of committed SVG scenes."""

    maximum_scene_count = 10

    def __init__(self) -> None:
        self._scenes: list[ActiveSvgCanvasScene] = []
        self._current_scene_id: str | None = None
        self._last_interaction: RecordedSvgCanvasInteraction | None = None
        self._reset_epoch = 0

    @property
    def reset_epoch(self) -> int:
        return self._reset_epoch

    @property
    def scenes(self) -> tuple[ActiveSvgCanvasScene, ...]:
        return tuple(self._scenes)

    @property
    def scene_count(self) -> int:
        return len(self._scenes)

    @property
    def current(self) -> ActiveSvgCanvasScene | None:
        scene_id = self._current_scene_id
        return self.scene(scene_id) if scene_id is not None else None

    @property
    def last_interaction(self) -> RecordedSvgCanvasInteraction | None:
        return self._last_interaction

    def scene(self, scene_id: str) -> ActiveSvgCanvasScene | None:
        return next(
            (scene for scene in self._scenes if scene.scene_id == scene_id),
            None,
        )

    def commit(
        self,
        scene: ActiveSvgCanvasScene,
        *,
        base_scene_id: str | None,
        base_revision: int,
        workspace_epoch: int,
    ) -> ActiveSvgCanvasScene:
        if workspace_epoch != self._reset_epoch:
            raise SvgCanvasWorkspaceError(
                "canonical SVG canvas workspace was reset after generation acceptance"
            )
        existing_index = next(
            (
                index
                for index, existing in enumerate(self._scenes)
                if existing.scene_id == scene.scene_id
            ),
            None,
        )
        if existing_index is None:
            if scene.revision != 1 or base_scene_id is not None or base_revision != 0:
                raise SvgCanvasWorkspaceError(
                    "canonical SVG canvas has no matching revision base"
                )
            self._scenes.append(scene)
            if len(self._scenes) > self.maximum_scene_count:
                evicted = self._scenes.pop(0)
                if self._current_scene_id == evicted.scene_id:
                    self._current_scene_id = None
            self._current_scene_id = scene.scene_id
            self._last_interaction = None
            return scene

        existing = self._scenes[existing_index]
        if scene.revision == existing.revision:
            if not _same_committed_scene_identity(existing, scene):
                raise SvgCanvasWorkspaceError(
                    "canonical SVG canvas scene revision has conflicting "
                    "committed identity"
                )
            if not _valid_observation_base(
                scene,
                base_scene_id=base_scene_id,
                base_revision=base_revision,
            ):
                raise SvgCanvasWorkspaceError(
                    "canonical SVG canvas observation has invalid base identity"
                )
            return existing
        if (
            base_scene_id != scene.scene_id
            or existing.revision != base_revision
            or scene.revision != base_revision + 1
        ):
            raise SvgCanvasWorkspaceError(
                "canonical SVG canvas changed before presentation completed"
            )
        self._scenes[existing_index] = scene
        self._current_scene_id = scene.scene_id
        self._last_interaction = None
        return scene

    def record_interaction(
        self,
        interaction: RecordedSvgCanvasInteraction,
    ) -> RecordedSvgCanvasInteraction:
        current = self.current
        if current is None or (
            interaction.scene_id != current.scene_id
            or interaction.scene_revision != current.revision
            or interaction.svg_sha256 != current.document.svg_sha256
            or interaction.manifest_sha256 != current.document.manifest_sha256
        ):
            raise SvgCanvasWorkspaceError(
                "canvas interaction does not match the canonical SVG canvas"
            )
        self._last_interaction = interaction
        return interaction

    def clear(self) -> None:
        self._scenes.clear()
        self._current_scene_id = None
        self._last_interaction = None
        self._reset_epoch += 1


class SvgCanvasWorkspaceStore:
    """Resolves one isolated in-memory canvas workspace per subject."""

    def __init__(self) -> None:
        self._workspaces: dict[str, SvgCanvasWorkspace] = {}

    def workspace_for(self, subject: str) -> SvgCanvasWorkspace:
        if not subject:
            raise ValueError("canvas workspace subject cannot be empty")
        workspace = self._workspaces.get(subject)
        if workspace is None:
            workspace = SvgCanvasWorkspace()
            self._workspaces[subject] = workspace
        return workspace

    def clear_subject(self, subject: str) -> None:
        workspace = self._workspaces.get(subject)
        if workspace is not None:
            workspace.clear()


def _same_committed_scene_identity(
    existing: ActiveSvgCanvasScene,
    observed: ActiveSvgCanvasScene,
) -> bool:
    return (
        existing.scene_id == observed.scene_id
        and existing.revision == observed.revision
        and existing.generation_id == observed.generation_id
        and existing.source_turn_id == observed.source_turn_id
        and existing.document.svg_sha256 == observed.document.svg_sha256
        and existing.document.manifest_sha256 == observed.document.manifest_sha256
        and existing.first_rendered_sample == observed.first_rendered_sample
    )


def _valid_observation_base(
    scene: ActiveSvgCanvasScene,
    *,
    base_scene_id: str | None,
    base_revision: int,
) -> bool:
    if scene.revision == 1:
        return base_scene_id is None and base_revision == 0
    return base_scene_id == scene.scene_id and base_revision == scene.revision - 1
