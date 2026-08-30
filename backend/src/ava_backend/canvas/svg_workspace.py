from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

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
    interaction_mode: str
    target_id: str
    accessibility_label: str
    step_index: int | None


class SvgCanvasWorkspaceError(ValueError):
    """A committed canvas update conflicts with canonical workspace state."""


class SvgCanvasExecutionStatus(StrEnum):
    GENERATING = "generating"
    DRAFT_VISIBLE = "draftVisible"
    VALIDATED_CANDIDATE = "validatedCandidate"
    PREPARING = "preparing"
    PRESENTATION_SCHEDULED = "presentationScheduled"
    PRESENTED = "presented"
    ACTIVE = "active"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"

    @property
    def terminal(self) -> bool:
        return self in {
            SvgCanvasExecutionStatus.ACTIVE,
            SvgCanvasExecutionStatus.FAILED,
            SvgCanvasExecutionStatus.CANCELLED,
            SvgCanvasExecutionStatus.SUPERSEDED,
        }


@dataclass(frozen=True, slots=True)
class SvgCanvasExecution:
    generation_id: str
    source_turn_id: str
    operation: str
    status: SvgCanvasExecutionStatus
    scene_id: str | None = None
    scene_revision: int | None = None
    terminal_reason: str | None = None


_EXECUTION_PROGRESS = {
    SvgCanvasExecutionStatus.GENERATING: 0,
    SvgCanvasExecutionStatus.DRAFT_VISIBLE: 1,
    SvgCanvasExecutionStatus.VALIDATED_CANDIDATE: 2,
    SvgCanvasExecutionStatus.PREPARING: 3,
    SvgCanvasExecutionStatus.PRESENTATION_SCHEDULED: 4,
    SvgCanvasExecutionStatus.PRESENTED: 5,
}


class SvgCanvasWorkspace:
    """Subject-scoped process-lifetime owner of committed SVG scenes."""

    maximum_scene_count = 10
    maximum_execution_history = 64

    def __init__(self) -> None:
        self._scenes: list[ActiveSvgCanvasScene] = []
        self._current_scene_id: str | None = None
        self._last_interaction: RecordedSvgCanvasInteraction | None = None
        self._reset_epoch = 0
        self._executions: dict[str, SvgCanvasExecution] = {}

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

    @property
    def focused_scene_id(self) -> str | None:
        return self._current_scene_id

    @property
    def executions(self) -> tuple[SvgCanvasExecution, ...]:
        return tuple(self._executions.values())

    def execution(self, generation_id: str) -> SvgCanvasExecution | None:
        return self._executions.get(generation_id)

    def scene(self, scene_id: str) -> ActiveSvgCanvasScene | None:
        return next(
            (scene for scene in self._scenes if scene.scene_id == scene_id),
            None,
        )

    def focus(self, scene_id: str) -> ActiveSvgCanvasScene:
        scene = self.scene(scene_id)
        if scene is None:
            raise SvgCanvasWorkspaceError("focused SVG canvas scene does not exist")
        self._current_scene_id = scene_id
        self._last_interaction = None
        return scene

    def start_execution(
        self,
        *,
        generation_id: str,
        source_turn_id: str,
        operation: str,
    ) -> SvgCanvasExecution:
        if generation_id in self._executions:
            raise SvgCanvasWorkspaceError("canvas generation identity was reused")
        self._prune_execution_history()
        if len(self._executions) >= self.maximum_execution_history:
            raise SvgCanvasWorkspaceError(
                "canvas execution history is full of non-terminal work"
            )
        execution = SvgCanvasExecution(
            generation_id=generation_id,
            source_turn_id=source_turn_id,
            operation=operation,
            status=SvgCanvasExecutionStatus.GENERATING,
        )
        self._executions[generation_id] = execution
        return execution

    def advance_execution(
        self,
        generation_id: str,
        status: SvgCanvasExecutionStatus,
        *,
        scene_id: str | None = None,
        scene_revision: int | None = None,
    ) -> SvgCanvasExecution:
        execution = self._require_execution(generation_id)
        if execution.status.terminal:
            raise SvgCanvasWorkspaceError("terminal canvas execution cannot advance")
        if status.terminal or status not in _EXECUTION_PROGRESS:
            raise SvgCanvasWorkspaceError("canvas execution advance requires a phase")
        current_order = _EXECUTION_PROGRESS[execution.status]
        requested_order = _EXECUTION_PROGRESS[status]
        if requested_order < current_order:
            raise SvgCanvasWorkspaceError("canvas execution phase cannot move backward")
        skips_optional_draft = (
            execution.status is SvgCanvasExecutionStatus.GENERATING
            and status is SvgCanvasExecutionStatus.VALIDATED_CANDIDATE
        )
        if requested_order > current_order + 1 and not skips_optional_draft:
            raise SvgCanvasWorkspaceError(
                "canvas execution phase cannot skip trust stages"
            )
        updated = SvgCanvasExecution(
            generation_id=execution.generation_id,
            source_turn_id=execution.source_turn_id,
            operation=execution.operation,
            status=status,
            scene_id=scene_id if scene_id is not None else execution.scene_id,
            scene_revision=(
                scene_revision
                if scene_revision is not None
                else execution.scene_revision
            ),
        )
        self._executions[generation_id] = updated
        return updated

    def finish_execution(
        self,
        generation_id: str,
        status: SvgCanvasExecutionStatus,
        *,
        reason: str | None = None,
    ) -> SvgCanvasExecution:
        if not status.terminal:
            raise SvgCanvasWorkspaceError(
                "canvas execution terminal status is required"
            )
        execution = self._require_execution(generation_id)
        if execution.status.terminal:
            if execution.status is status:
                return execution
            raise SvgCanvasWorkspaceError(
                "canvas execution already has a terminal status"
            )
        updated = SvgCanvasExecution(
            generation_id=execution.generation_id,
            source_turn_id=execution.source_turn_id,
            operation=execution.operation,
            status=status,
            scene_id=execution.scene_id,
            scene_revision=execution.scene_revision,
            terminal_reason=reason,
        )
        self._executions[generation_id] = updated
        return updated

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
            self._finish_active_execution(scene.generation_id)
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
        self._finish_active_execution(scene.generation_id)
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
        for generation_id, execution in tuple(self._executions.items()):
            if not execution.status.terminal:
                self.finish_execution(
                    generation_id,
                    SvgCanvasExecutionStatus.CANCELLED,
                    reason="workspaceReset",
                )
        self._reset_epoch += 1

    def _finish_active_execution(self, generation_id: str) -> None:
        execution = self._executions.get(generation_id)
        if execution is None:
            return
        if execution.status is not SvgCanvasExecutionStatus.PRESENTED:
            raise SvgCanvasWorkspaceError(
                "canvas execution must be presented before activation"
            )
        self.finish_execution(generation_id, SvgCanvasExecutionStatus.ACTIVE)

    def _require_execution(self, generation_id: str) -> SvgCanvasExecution:
        execution = self._executions.get(generation_id)
        if execution is None:
            raise SvgCanvasWorkspaceError("canvas execution does not exist")
        return execution

    def _prune_execution_history(self) -> None:
        while len(self._executions) >= self.maximum_execution_history:
            terminal_id = next(
                (
                    generation_id
                    for generation_id, execution in self._executions.items()
                    if execution.status.terminal
                ),
                None,
            )
            if terminal_id is None:
                return
            del self._executions[terminal_id]


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
