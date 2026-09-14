from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from html import escape
import hashlib
import json
from collections.abc import Callable

from .svg_document import ValidatedSvgDocument

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ActiveSvgCanvasScene:
    generation_id: str
    source_turn_id: str
    scene_id: str
    revision: int
    document: ValidatedSvgDocument
    first_rendered_sample: int
    workspace_epoch: int | None = None
    workspace_revision: int | None = None
    creation_sequence: int | None = None
    modification_sequence: int | None = None
    evicted_scene_id: str | None = None


@dataclass(frozen=True, slots=True)
class CanvasWorkspaceManifestScene:
    scene_id: str
    scene_revision: int
    generation_id: str
    source_turn_id: str
    svg_sha256: str
    manifest_sha256: str
    svg_byte_length: int
    manifest_byte_length: int
    renderer_capability_version: str
    validator_version: str
    manifest_version: str
    creation_sequence: int
    modification_sequence: int | None
    title: str
    summary: str


@dataclass(frozen=True, slots=True)
class CanvasWorkspaceManifest:
    protocol_version: int
    workspace_epoch: int
    workspace_revision: int
    next_creation_sequence: int
    next_modification_sequence: int
    focused_scene_id: str | None
    scenes: tuple[CanvasWorkspaceManifestScene, ...]


@dataclass(frozen=True, slots=True)
class CanvasWorkspaceStructuralMutation:
    operation: str
    workspace_epoch: int
    workspace_revision: int
    scene_id: str
    scene_revision: int
    creation_sequence: int
    modification_sequence: int
    evicted_scene_id: str | None = None


@dataclass(frozen=True, slots=True)
class CanvasSceneEvictedTelemetryEvent:
    event_type: str
    scene_id: str
    scene_revision: int
    generation_id: str | None
    source_turn_id: str | None
    title: str
    creation_sequence: int
    modification_sequence: int | None
    workspace_epoch: int
    workspace_revision: int
    eviction_reason: str
    occurred_at: datetime

    def as_context(self) -> dict[str, object]:
        return {
            "component": "svgCanvasWorkspace",
            "eventType": self.event_type,
            "sceneId": self.scene_id,
            "sceneRevision": self.scene_revision,
            **(
                {"generationId": self.generation_id}
                if self.generation_id is not None
                else {}
            ),
            **(
                {"sourceTurnId": self.source_turn_id}
                if self.source_turn_id is not None
                else {}
            ),
            "title": self.title,
            "creationSequence": self.creation_sequence,
            **(
                {"modificationSequence": self.modification_sequence}
                if self.modification_sequence is not None
                else {}
            ),
            "workspaceEpoch": self.workspace_epoch,
            "workspaceRevision": self.workspace_revision,
            "evictionReason": self.eviction_reason,
            "occurredAt": self.occurred_at.isoformat(),
        }


CanvasSceneEvictionTelemetrySink = Callable[[CanvasSceneEvictedTelemetryEvent], None]


def normalize_semantic_title(value: str) -> str:
    return " ".join(value.split()).casefold()


_MODEL_FACING_TITLE_MAX_RENDERED_CHARACTERS = 56


def _bounded_model_facing_text(value: str, *, max_rendered_characters: int) -> str:
    """Keep a semantic label small without returning an invalid XML value."""

    rendered: list[str] = []
    rendered_length = 0
    for character in value:
        escaped = escape(character, quote=True)
        if rendered_length + len(escaped) > max_rendered_characters:
            break
        rendered.append(character)
        rendered_length += len(escaped)
    return "".join(rendered)


def _log_canvas_scene_evicted(event: CanvasSceneEvictedTelemetryEvent) -> None:
    LOGGER.info("canvas_scene_evicted", extra={"ava_context": event.as_context()})


class CanvasNavigationDirection(StrEnum):
    NEXT = "next"
    PREVIOUS = "previous"
    FIRST = "first"
    LAST = "last"


class CanvasSceneSelector(StrEnum):
    CURRENT = "current"
    NEXT = "next"
    PREVIOUS = "previous"
    FIRST = "first"
    LAST = "last"
    POSITION = "position"
    TITLE = "title"


@dataclass(frozen=True, slots=True)
class CanvasNavigationResolution:
    changed: bool
    reason: str | None
    scene: CanvasWorkspaceManifestScene | None
    position: int | None
    total: int
    has_previous: bool
    has_next: bool


@dataclass(frozen=True, slots=True)
class CanvasSceneResolution:
    """One semantic selector resolved against one canonical workspace snapshot."""

    reason: str | None
    scene: CanvasWorkspaceManifestScene | None
    position: int | None
    total: int
    has_previous: bool
    has_next: bool
    workspace_epoch: int
    workspace_revision: int


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
    focus_revision: int = 0
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

    def __init__(
        self,
        *,
        eviction_telemetry_sink: CanvasSceneEvictionTelemetrySink = (
            _log_canvas_scene_evicted
        ),
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._scenes: list[ActiveSvgCanvasScene] = []
        self._focused_scene: ActiveSvgCanvasScene | None = None
        self.focus_revision = 0
        self._last_interaction: RecordedSvgCanvasInteraction | None = None
        self._reset_epoch = 0
        self._executions: dict[str, SvgCanvasExecution] = {}
        self._scene_recency: dict[str, int] = {}
        self._next_scene_recency = 0
        self._workspace_epoch = 0
        self._workspace_revision = 0
        self._next_creation_sequence = 1
        self._next_modification_sequence = 1
        self._manifest_scenes: list[CanvasWorkspaceManifestScene] = []
        self._structural_manifest_digest: str | None = None
        self._navigation_ready = False
        self._last_structural_mutation: CanvasWorkspaceStructuralMutation | None = None
        self._eviction_telemetry_sink = eviction_telemetry_sink
        self._utc_now = utc_now

    @property
    def reset_epoch(self) -> int:
        return self._workspace_epoch

    @property
    def workspace_epoch(self) -> int:
        return self._workspace_epoch

    @property
    def workspace_revision(self) -> int:
        return self._workspace_revision

    @property
    def next_creation_sequence(self) -> int:
        return self._next_creation_sequence

    @property
    def next_modification_sequence(self) -> int:
        return self._next_modification_sequence

    @property
    def navigation_ready(self) -> bool:
        return self._navigation_ready

    @property
    def manifest_scenes(self) -> tuple[CanvasWorkspaceManifestScene, ...]:
        return tuple(self._manifest_scenes)

    def model_facing_titles(self) -> dict[str, str]:
        """Return the selector-safe labels for the current retained directory.

        The label is deliberately derived only from durable manifest data.  A
        truncated or duplicate canonical title receives its bounded retained
        position as a suffix,
        so the model never sees a title which the presentation adapter cannot
        map back to exactly one retained manifest row.
        """

        base_titles = {
            row.scene_id: _bounded_model_facing_text(
                row.title,
                max_rendered_characters=_MODEL_FACING_TITLE_MAX_RENDERED_CHARACTERS,
            )
            for row in self._manifest_scenes
        }
        normalized_counts: dict[str, int] = {}
        for title in base_titles.values():
            normalized = normalize_semantic_title(title)
            normalized_counts[normalized] = normalized_counts.get(normalized, 0) + 1

        positions = {
            row.scene_id: position
            for position, row in enumerate(self._manifest_scenes, start=1)
        }
        labels: dict[str, str] = {}
        suffixed: set[str] = set()
        for row in self._manifest_scenes:
            base = base_titles[row.scene_id]
            needs_suffix = (
                base != row.title
                or normalized_counts[normalize_semantic_title(base)] > 1
            )
            if not needs_suffix:
                labels[row.scene_id] = base
                continue
            suffix = f" [position {positions[row.scene_id]}]"
            labels[row.scene_id] = (
                _bounded_model_facing_text(
                    row.title,
                    max_rendered_characters=(
                        _MODEL_FACING_TITLE_MAX_RENDERED_CHARACTERS - len(suffix)
                    ),
                )
                + suffix
            )
            suffixed.add(row.scene_id)

        # A canonical title can itself equal another row's generated label.
        # Add the same stable retained-position suffix to every remaining collision
        # so an emitted label always identifies exactly one retained row.
        label_counts: dict[str, int] = {}
        for label in labels.values():
            normalized = normalize_semantic_title(label)
            label_counts[normalized] = label_counts.get(normalized, 0) + 1
        for row in self._manifest_scenes:
            label = labels[row.scene_id]
            if (
                label_counts[normalize_semantic_title(label)] == 1
                or row.scene_id in suffixed
            ):
                continue
            suffix = f" [position {positions[row.scene_id]}]"
            labels[row.scene_id] = (
                _bounded_model_facing_text(
                    row.title,
                    max_rendered_characters=(
                        _MODEL_FACING_TITLE_MAX_RENDERED_CHARACTERS - len(suffix)
                    ),
                )
                + suffix
            )
        return labels

    @property
    def last_structural_mutation(self) -> CanvasWorkspaceStructuralMutation | None:
        return self._last_structural_mutation

    @property
    def scenes(self) -> tuple[ActiveSvgCanvasScene, ...]:
        return tuple(self._scenes)

    @property
    def scene_count(self) -> int:
        return (
            len(self._manifest_scenes) if self._navigation_ready else len(self._scenes)
        )

    @property
    def current(self) -> ActiveSvgCanvasScene | None:
        return self._focused_scene

    @property
    def last_interaction(self) -> RecordedSvgCanvasInteraction | None:
        return self._last_interaction

    @property
    def focused_scene_id(self) -> str | None:
        return self._focused_scene.scene_id if self._focused_scene is not None else None

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

    def manifest_scene(self, scene_id: str) -> CanvasWorkspaceManifestScene | None:
        return next(
            (scene for scene in self._manifest_scenes if scene.scene_id == scene_id),
            None,
        )

    def ordered_scene_ids(self) -> tuple[str, ...]:
        if self._navigation_ready:
            return tuple(scene.scene_id for scene in self._manifest_scenes)
        return tuple(scene.scene_id for scene in self._scenes)

    def adopt_manifest(self, manifest: CanvasWorkspaceManifest) -> bool:
        """Adopt an authenticated recovery snapshot without hydrating SVG bytes.

        Returns ``False`` for a structurally identical idempotent retry. Focus
        remains an independent recovery hint and is not adopted here.
        """

        _validate_workspace_manifest(
            manifest, maximum_scene_count=self.maximum_scene_count
        )
        structural_digest = _structural_manifest_digest(manifest)
        if self._structural_manifest_digest is not None:
            if (
                manifest.workspace_epoch == self._workspace_epoch
                and manifest.workspace_revision == self._workspace_revision
            ):
                if structural_digest == self._structural_manifest_digest:
                    self._log_manifest_adoption(
                        manifest=manifest,
                        incoming_structural_digest=structural_digest,
                        classification="structurallyIdempotent",
                    )
                    return False
                self._log_manifest_adoption(
                    manifest=manifest,
                    incoming_structural_digest=structural_digest,
                    classification="structuralConflict",
                )
                raise SvgCanvasWorkspaceError(
                    "canvas workspace revision conflicts with previously adopted contents"
                )
            raise SvgCanvasWorkspaceError(
                "canvas workspace manifest cannot replace an adopted structural identity"
            )
        if self._scenes:
            raise SvgCanvasWorkspaceError(
                "canvas workspace manifest must be adopted before content hydration"
            )
        self._workspace_epoch = manifest.workspace_epoch
        self._reset_epoch = manifest.workspace_epoch
        self._workspace_revision = manifest.workspace_revision
        self._next_creation_sequence = manifest.next_creation_sequence
        self._next_modification_sequence = manifest.next_modification_sequence
        self._manifest_scenes = list(manifest.scenes)
        self._structural_manifest_digest = structural_digest
        self._navigation_ready = True
        return True

    def _log_manifest_adoption(
        self,
        *,
        manifest: CanvasWorkspaceManifest,
        incoming_structural_digest: str,
        classification: str,
    ) -> None:
        LOGGER.info(
            "canvas_workspace_manifest_adoption classification=%s "
            "workspace_epoch=%d workspace_revision=%d "
            "incoming_focused_scene_id=%s canonical_focused_scene_id=%s "
            "structural_digest_incoming=%s structural_digest_canonical=%s",
            classification,
            manifest.workspace_epoch,
            manifest.workspace_revision,
            manifest.focused_scene_id,
            self.focused_scene_id,
            incoming_structural_digest,
            self._structural_manifest_digest,
            extra={
                "ava_context": {
                    "component": "svgCanvasWorkspace",
                    "workspaceEpoch": manifest.workspace_epoch,
                    "workspaceRevision": manifest.workspace_revision,
                    "incomingFocusedSceneId": manifest.focused_scene_id,
                    "canonicalFocusedSceneId": self.focused_scene_id,
                    "structuralDigestIncoming": incoming_structural_digest,
                    "structuralDigestCanonical": self._structural_manifest_digest,
                    "classification": classification,
                }
            },
        )

    def resolve_navigation(
        self, direction: CanvasNavigationDirection
    ) -> CanvasNavigationResolution:
        if not self._navigation_ready:
            raise SvgCanvasWorkspaceError("workspaceNotSynchronized")
        direction = CanvasNavigationDirection(direction)
        total = len(self._manifest_scenes)
        if total == 0:
            return CanvasNavigationResolution(
                False, "emptyWorkspace", None, None, 0, False, False
            )
        focused_id = self.focused_scene_id
        current_index = next(
            (
                i
                for i, row in enumerate(self._manifest_scenes)
                if row.scene_id == focused_id
            ),
            None,
        )
        if current_index is None:
            return CanvasNavigationResolution(
                False, "noFocusedScene", None, None, total, False, total > 1
            )
        if direction is CanvasNavigationDirection.NEXT:
            target_index = current_index + 1
            boundary_reason = "endOfWorkspace"
        elif direction is CanvasNavigationDirection.PREVIOUS:
            target_index = current_index - 1
            boundary_reason = "startOfWorkspace"
        elif direction is CanvasNavigationDirection.FIRST:
            target_index = 0
            boundary_reason = None
        else:
            target_index = total - 1
            boundary_reason = None
        if target_index < 0 or target_index >= total:
            row = self._manifest_scenes[current_index]
            return CanvasNavigationResolution(
                False,
                boundary_reason,
                row,
                current_index + 1,
                total,
                current_index > 0,
                current_index < total - 1,
            )
        row = self._manifest_scenes[target_index]
        return CanvasNavigationResolution(
            target_index != current_index,
            None,
            row,
            target_index + 1,
            total,
            target_index > 0,
            target_index < total - 1,
        )

    def resolve_scene_selector(
        self,
        selector: CanvasSceneSelector,
        *,
        position: int | None = None,
        title: str | None = None,
    ) -> CanvasSceneResolution:
        """Resolve model/UI semantic intent without exposing identity to either."""

        selector = CanvasSceneSelector(selector)
        if selector is CanvasSceneSelector.CURRENT:
            if not self._navigation_ready:
                return self._selector_failure("workspaceNotSynchronized")
            focused = self.current
            if focused is None:
                return self._selector_failure("noFocusedScene")
            row = self.manifest_scene(focused.scene_id)
            if row is None:
                return self._selector_failure("noFocusedScene")
            return self._selector_success(row)
        if not self._navigation_ready:
            return self._selector_failure("workspaceNotSynchronized")
        if selector in {
            CanvasSceneSelector.NEXT,
            CanvasSceneSelector.PREVIOUS,
            CanvasSceneSelector.FIRST,
            CanvasSceneSelector.LAST,
        }:
            navigation = self.resolve_navigation(CanvasNavigationDirection(selector))
            if navigation.reason is not None:
                return self._selector_failure(navigation.reason)
            assert navigation.scene is not None
            return self._selector_success(navigation.scene)
        if selector is CanvasSceneSelector.POSITION:
            if (
                position is None
                or position < 1
                or position > len(self._manifest_scenes)
            ):
                return self._selector_failure("positionOutOfRange")
            return self._selector_success(self._manifest_scenes[position - 1])
        assert selector is CanvasSceneSelector.TITLE
        normalized = normalize_semantic_title(title or "")
        model_titles = self.model_facing_titles()
        matches = [
            row
            for row in self._manifest_scenes
            if normalize_semantic_title(model_titles[row.scene_id]) == normalized
        ]
        if not matches:
            matches = [
                row
                for row in self._manifest_scenes
                if normalize_semantic_title(row.title) == normalized
            ]
        if not matches:
            return self._selector_failure("titleNotFound")
        if len(matches) != 1:
            return self._selector_failure("ambiguousTitle")
        return self._selector_success(matches[0])

    def _selector_success(
        self, row: CanvasWorkspaceManifestScene
    ) -> CanvasSceneResolution:
        position = self._manifest_scenes.index(row) + 1
        return CanvasSceneResolution(
            None,
            row,
            position,
            len(self._manifest_scenes),
            position > 1,
            position < len(self._manifest_scenes),
            self._workspace_epoch,
            self._workspace_revision,
        )

    def _selector_failure(self, reason: str) -> CanvasSceneResolution:
        return CanvasSceneResolution(
            reason,
            None,
            None,
            self.scene_count,
            False,
            False,
            self._workspace_epoch,
            self._workspace_revision,
        )

    @property
    def latest_created_scene_id(self) -> str | None:
        if not self._manifest_scenes:
            return None
        return max(
            self._manifest_scenes, key=lambda row: row.creation_sequence
        ).scene_id

    @property
    def latest_modified_scene_id(self) -> str | None:
        known = [
            row
            for row in self._manifest_scenes
            if row.modification_sequence is not None
        ]
        if not known:
            return None
        return max(known, key=lambda row: row.modification_sequence or 0).scene_id

    def scene_recency(self, scene_id: str) -> int:
        try:
            return self._scene_recency[scene_id]
        except KeyError as error:
            raise SvgCanvasWorkspaceError(
                "canvas scene recency requires a retained scene"
            ) from error

    def clear_focus(self) -> None:
        self._focused_scene = None
        self._last_interaction = None

    def focus(self, scene_id: str) -> ActiveSvgCanvasScene:
        scene = self.scene(scene_id)
        if scene is None:
            raise SvgCanvasWorkspaceError("focused SVG canvas scene does not exist")
        self._focused_scene = scene
        self._last_interaction = None
        return scene

    def restore_focused(self, scene: ActiveSvgCanvasScene) -> ActiveSvgCanvasScene:
        """Hydrate one independently validated scene before realtime negotiation."""

        return self.focus_retained(scene)

    def validate_retained_scene(self, scene: ActiveSvgCanvasScene) -> None:
        """Validate one retained scene against workspace manifest and cache integrity."""

        if scene.revision < 1 or scene.revision > 10_001:
            raise SvgCanvasWorkspaceError("restored canvas revision is invalid")

        if self._navigation_ready:
            expected = self.manifest_scene(scene.scene_id)
            if expected is None:
                raise SvgCanvasWorkspaceError(
                    "focused SVG canvas scene is absent from synchronized workspace"
                )
            if not _scene_matches_manifest(scene, expected):
                raise SvgCanvasWorkspaceError(
                    "focused SVG canvas content conflicts with synchronized workspace metadata"
                )

        existing = (
            self.current
            if self.current is not None
            and _same_committed_scene_identity(self.current, scene)
            else self.scene(scene.scene_id)
        )
        if existing is not None:
            if (
                existing.revision != scene.revision
                or existing.generation_id != scene.generation_id
                or existing.source_turn_id != scene.source_turn_id
                or existing.document.svg_sha256 != scene.document.svg_sha256
                or existing.document.manifest_sha256 != scene.document.manifest_sha256
            ):
                raise SvgCanvasWorkspaceError(
                    "conflicting canvas scene revision or identity cannot be focused"
                )

    def validate_retained_focus(self, scene: ActiveSvgCanvasScene) -> None:
        """Validate one retained scene for runtime focus without mutating workspace."""

        self.validate_retained_scene(scene)

    def commit_retained_scene(
        self, scene: ActiveSvgCanvasScene
    ) -> ActiveSvgCanvasScene:
        """Synchronously commit a pre-validated retained scene into cache without mutating focus."""

        existing = self.scene(scene.scene_id)
        if existing is not None:
            return existing

        if len(self._scenes) >= self.maximum_scene_count:
            evict_index = 0
            if (
                self._focused_scene is not None
                and self._scenes[0].scene_id == self._focused_scene.scene_id
                and len(self._scenes) > 1
            ):
                evict_index = 1
            evicted = self._scenes.pop(evict_index)
            self._scene_recency.pop(evicted.scene_id, None)
        self._scenes.append(scene)
        self._record_scene_recency(scene.scene_id)
        return scene

    def hydrate_retained(self, scene: ActiveSvgCanvasScene) -> ActiveSvgCanvasScene:
        """Validate and cache one retained scene without changing focus."""

        self.validate_retained_scene(scene)
        return self.commit_retained_scene(scene)

    def commit_retained_focus(
        self, scene: ActiveSvgCanvasScene
    ) -> ActiveSvgCanvasScene:
        """Synchronously and infallibly commit a pre-validated retained scene focus."""

        existing = self.commit_retained_scene(scene)
        self._focused_scene = existing
        self._last_interaction = None
        return existing

    def focus_retained(self, scene: ActiveSvgCanvasScene) -> ActiveSvgCanvasScene:
        """Focus or hydrate one independently validated retained scene at runtime."""

        self.validate_retained_focus(scene)
        return self.commit_retained_focus(scene)

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
            focus_revision=self.focus_revision,
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
            focus_revision=execution.focus_revision,
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
            focus_revision=execution.focus_revision,
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
        foreground: bool = True,
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
            self._validate_active_execution_ready(scene.generation_id)
            creation_sequence = self._next_creation_sequence
            modification_sequence = self._next_modification_sequence
            resulting_workspace_revision = self._workspace_revision + 1
            if self._navigation_ready:
                if self.manifest_scene(scene.scene_id) is not None:
                    raise SvgCanvasWorkspaceError(
                        "canonical SVG canvas scene already exists but is not hydrated"
                    )
                scene = replace(
                    scene,
                    workspace_epoch=self._workspace_epoch,
                    workspace_revision=resulting_workspace_revision,
                    creation_sequence=creation_sequence,
                    modification_sequence=modification_sequence,
                )
                evicted_manifest_scene = (
                    self._manifest_scenes[0]
                    if len(self._manifest_scenes) >= self.maximum_scene_count
                    else None
                )
                evicted_scene_id = (
                    evicted_manifest_scene.scene_id
                    if evicted_manifest_scene is not None
                    else None
                )
                evicted_scene = (
                    self.scene(evicted_scene_id)
                    if evicted_scene_id is not None
                    else None
                )
                scene = replace(scene, evicted_scene_id=evicted_scene_id)
                retained_manifest_scenes = (
                    self._manifest_scenes[1:]
                    if evicted_manifest_scene is not None
                    else list(self._manifest_scenes)
                )
                resulting_manifest_scenes = [
                    *retained_manifest_scenes,
                    _manifest_scene_from_active(
                        scene,
                        creation_sequence=creation_sequence,
                        modification_sequence=modification_sequence,
                    ),
                ]
                resulting_scenes = [
                    retained
                    for retained in self._scenes
                    if retained.scene_id != evicted_scene_id
                ]
                resulting_scenes.append(scene)
                resulting_scene_recency = dict(self._scene_recency)
                if evicted_scene_id is not None:
                    resulting_scene_recency.pop(evicted_scene_id, None)
                resulting_next_scene_recency = self._next_scene_recency + 1
                resulting_scene_recency[scene.scene_id] = resulting_next_scene_recency
                evicted_focused_scene = (
                    evicted_scene_id is not None
                    and self.focused_scene_id == evicted_scene_id
                )
                resulting_focused_scene = (
                    None if evicted_focused_scene else self._focused_scene
                )
                if foreground:
                    resulting_focused_scene = scene
                resulting_manifest = CanvasWorkspaceManifest(
                    protocol_version=4,
                    workspace_epoch=self._workspace_epoch,
                    workspace_revision=resulting_workspace_revision,
                    next_creation_sequence=creation_sequence + 1,
                    next_modification_sequence=modification_sequence + 1,
                    focused_scene_id=(
                        resulting_focused_scene.scene_id
                        if resulting_focused_scene is not None
                        else None
                    ),
                    scenes=tuple(resulting_manifest_scenes),
                )
                _validate_workspace_manifest(
                    resulting_manifest,
                    maximum_scene_count=self.maximum_scene_count,
                )
                resulting_structural_digest = _structural_manifest_digest(
                    resulting_manifest
                )

                self._next_creation_sequence = creation_sequence + 1
                self._next_modification_sequence = modification_sequence + 1
                self._next_scene_recency = resulting_next_scene_recency
                self._scene_recency = resulting_scene_recency
                self._scenes = resulting_scenes
                self._manifest_scenes = resulting_manifest_scenes
                self._focused_scene = resulting_focused_scene
                self._workspace_revision = resulting_workspace_revision
                self._structural_manifest_digest = resulting_structural_digest
                self._last_structural_mutation = CanvasWorkspaceStructuralMutation(
                    operation="create",
                    workspace_epoch=self._workspace_epoch,
                    workspace_revision=self._workspace_revision,
                    scene_id=scene.scene_id,
                    scene_revision=scene.revision,
                    creation_sequence=creation_sequence,
                    modification_sequence=modification_sequence,
                    evicted_scene_id=evicted_scene_id,
                )
                if foreground or evicted_focused_scene:
                    self._last_interaction = None
                if evicted_manifest_scene is not None:
                    self._emit_capacity_eviction(
                        scene_id=evicted_manifest_scene.scene_id,
                        hydrated_scene=evicted_scene,
                        manifest_scene=evicted_manifest_scene,
                    )
                self._finish_active_execution(scene.generation_id)
                return scene

            self._next_creation_sequence += 1
            self._next_modification_sequence += 1
            self._scenes.append(scene)
            self._record_scene_recency(scene.scene_id)
            legacy_evicted_scene_id: str | None = None
            legacy_evicted_scene: ActiveSvgCanvasScene | None = None
            if len(self._scenes) > self.maximum_scene_count:
                legacy_evicted_scene = self._scenes.pop(0)
                legacy_evicted_scene_id = legacy_evicted_scene.scene_id
                self._scene_recency.pop(legacy_evicted_scene.scene_id, None)
                if self.focused_scene_id == legacy_evicted_scene.scene_id:
                    self._focused_scene = None
            self._workspace_revision = resulting_workspace_revision
            self._last_structural_mutation = CanvasWorkspaceStructuralMutation(
                operation="create",
                workspace_epoch=self._workspace_epoch,
                workspace_revision=self._workspace_revision,
                scene_id=scene.scene_id,
                scene_revision=scene.revision,
                creation_sequence=creation_sequence,
                modification_sequence=modification_sequence,
                evicted_scene_id=legacy_evicted_scene_id,
            )
            if legacy_evicted_scene_id is not None:
                self._emit_capacity_eviction(
                    scene_id=legacy_evicted_scene_id,
                    hydrated_scene=legacy_evicted_scene,
                    manifest_scene=None,
                )
            if foreground:
                self._focused_scene = scene
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
        self._record_scene_recency(scene.scene_id)
        modification_sequence = self._next_modification_sequence
        self._next_modification_sequence += 1
        creation_sequence = existing_index + 1
        if self._navigation_ready:
            manifest_index = next(
                (
                    i
                    for i, row in enumerate(self._manifest_scenes)
                    if row.scene_id == scene.scene_id
                ),
                None,
            )
            if manifest_index is None:
                raise SvgCanvasWorkspaceError(
                    "revised scene is absent from canonical workspace"
                )
            previous_metadata = self._manifest_scenes[manifest_index]
            creation_sequence = previous_metadata.creation_sequence
            self._manifest_scenes[manifest_index] = _manifest_scene_from_active(
                scene,
                creation_sequence=previous_metadata.creation_sequence,
                modification_sequence=modification_sequence,
            )
        if self._navigation_ready:
            scene = replace(
                scene,
                workspace_epoch=self._workspace_epoch,
                workspace_revision=self._workspace_revision + 1,
                creation_sequence=creation_sequence,
                modification_sequence=modification_sequence,
            )
        self._scenes[existing_index] = scene
        self._workspace_revision += 1
        self._last_structural_mutation = CanvasWorkspaceStructuralMutation(
            operation="revise",
            workspace_epoch=self._workspace_epoch,
            workspace_revision=self._workspace_revision,
            scene_id=scene.scene_id,
            scene_revision=scene.revision,
            creation_sequence=creation_sequence,
            modification_sequence=modification_sequence,
        )
        self._refresh_structural_manifest_digest()
        if foreground:
            self._focused_scene = scene
        self._last_interaction = None
        self._finish_active_execution(scene.generation_id)
        return scene

    def _emit_capacity_eviction(
        self,
        *,
        scene_id: str,
        hydrated_scene: ActiveSvgCanvasScene | None,
        manifest_scene: CanvasWorkspaceManifestScene | None,
    ) -> None:
        if manifest_scene is not None:
            event = CanvasSceneEvictedTelemetryEvent(
                event_type="canvasSceneEvicted",
                scene_id=manifest_scene.scene_id,
                scene_revision=manifest_scene.scene_revision,
                generation_id=manifest_scene.generation_id,
                source_turn_id=manifest_scene.source_turn_id,
                title=manifest_scene.title,
                creation_sequence=manifest_scene.creation_sequence,
                modification_sequence=manifest_scene.modification_sequence,
                workspace_epoch=self._workspace_epoch,
                workspace_revision=self._workspace_revision,
                eviction_reason="capacityFifo",
                occurred_at=self._utc_now(),
            )
        elif hydrated_scene is not None:
            event = CanvasSceneEvictedTelemetryEvent(
                event_type="canvasSceneEvicted",
                scene_id=hydrated_scene.scene_id,
                scene_revision=hydrated_scene.revision,
                generation_id=hydrated_scene.generation_id,
                source_turn_id=hydrated_scene.source_turn_id,
                title=hydrated_scene.document.title[:160],
                creation_sequence=1,
                modification_sequence=None,
                workspace_epoch=self._workspace_epoch,
                workspace_revision=self._workspace_revision,
                eviction_reason="capacityFifo",
                occurred_at=self._utc_now(),
            )
        else:
            raise SvgCanvasWorkspaceError(
                f"evicted canvas scene {scene_id} lost its canonical identity"
            )
        try:
            self._eviction_telemetry_sink(event)
        except Exception as error:
            LOGGER.warning(
                "canvas_scene_eviction_telemetry_failed "
                "scene_id=%s workspace_epoch=%d workspace_revision=%d "
                "failure_type=%s",
                event.scene_id,
                event.workspace_epoch,
                event.workspace_revision,
                type(error).__name__,
            )

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
        self._scene_recency.clear()
        self._next_scene_recency = 0
        self._manifest_scenes.clear()
        self._structural_manifest_digest = None
        self._navigation_ready = False
        self._workspace_epoch += 1
        self._workspace_revision = 0
        self._next_creation_sequence = 1
        self._next_modification_sequence = 1
        self._last_structural_mutation = None
        self._focused_scene = None
        self._last_interaction = None
        for generation_id, execution in tuple(self._executions.items()):
            if not execution.status.terminal:
                self.finish_execution(
                    generation_id,
                    SvgCanvasExecutionStatus.CANCELLED,
                    reason="workspaceReset",
                )
        self._reset_epoch = self._workspace_epoch

    def _refresh_structural_manifest_digest(self) -> None:
        if self._structural_manifest_digest is None:
            return
        self._structural_manifest_digest = _structural_manifest_digest(
            CanvasWorkspaceManifest(
                protocol_version=4,
                workspace_epoch=self._workspace_epoch,
                workspace_revision=self._workspace_revision,
                next_creation_sequence=self._next_creation_sequence,
                next_modification_sequence=self._next_modification_sequence,
                focused_scene_id=self.focused_scene_id,
                scenes=tuple(self._manifest_scenes),
            )
        )

    def _finish_active_execution(self, generation_id: str) -> None:
        self._validate_active_execution_ready(generation_id)
        execution = self._executions.get(generation_id)
        if execution is None:
            return
        self.finish_execution(generation_id, SvgCanvasExecutionStatus.ACTIVE)

    def _validate_active_execution_ready(self, generation_id: str) -> None:
        execution = self._executions.get(generation_id)
        if (
            execution is not None
            and execution.status is not SvgCanvasExecutionStatus.PRESENTED
        ):
            raise SvgCanvasWorkspaceError(
                "canvas execution must be presented before activation"
            )

    def _record_scene_recency(self, scene_id: str) -> None:
        self._next_scene_recency += 1
        self._scene_recency[scene_id] = self._next_scene_recency

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


def _validate_workspace_manifest(
    manifest: CanvasWorkspaceManifest,
    *,
    maximum_scene_count: int,
) -> None:
    if manifest.protocol_version != 4:
        raise SvgCanvasWorkspaceError("canvas workspace protocolVersion must be 4")
    for name, value in (
        ("workspaceEpoch", manifest.workspace_epoch),
        ("workspaceRevision", manifest.workspace_revision),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SvgCanvasWorkspaceError(f"canvas workspace {name} is invalid")
    if not 0 <= len(manifest.scenes) <= maximum_scene_count:
        raise SvgCanvasWorkspaceError("canvas workspace scene count exceeds its bound")
    scene_ids: set[str] = set()
    structural_identities: set[tuple[str, int]] = set()
    creation_sequences: set[int] = set()
    ordered_creation_sequences: list[int] = []
    modification_sequences: set[int] = set()
    for row in manifest.scenes:
        if row.scene_id in scene_ids:
            raise SvgCanvasWorkspaceError("canvas workspace scene IDs must be unique")
        scene_ids.add(row.scene_id)
        structural_identity = (row.scene_id, row.scene_revision)
        if structural_identity in structural_identities:
            raise SvgCanvasWorkspaceError(
                "canvas workspace structural identities must be unique"
            )
        structural_identities.add(structural_identity)
        if not 1 <= row.scene_revision <= 10_001:
            raise SvgCanvasWorkspaceError("canvas workspace scene revision is invalid")
        if row.creation_sequence < 1 or row.creation_sequence in creation_sequences:
            raise SvgCanvasWorkspaceError(
                "canvas workspace creationSequence is invalid"
            )
        creation_sequences.add(row.creation_sequence)
        ordered_creation_sequences.append(row.creation_sequence)
        if row.modification_sequence is not None:
            if (
                row.modification_sequence < 1
                or row.modification_sequence in modification_sequences
            ):
                raise SvgCanvasWorkspaceError(
                    "canvas workspace modificationSequence is invalid"
                )
            modification_sequences.add(row.modification_sequence)
        for identifier in (row.scene_id, row.generation_id, row.source_turn_id):
            if not identifier or len(identifier) > 96:
                raise SvgCanvasWorkspaceError("canvas workspace identifier is invalid")
        for digest in (row.svg_sha256, row.manifest_sha256):
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise SvgCanvasWorkspaceError(
                    "canvas workspace content hash is invalid"
                )
        if not 1 <= row.svg_byte_length <= 512 * 1024:
            raise SvgCanvasWorkspaceError("canvas workspace SVG byte length is invalid")
        if not 1 <= row.manifest_byte_length <= 64 * 1024:
            raise SvgCanvasWorkspaceError(
                "canvas workspace manifest byte length is invalid"
            )
        if len(row.title) > 160 or len(row.summary) > 800:
            raise SvgCanvasWorkspaceError(
                "canvas workspace semantic metadata is too long"
            )
        if not all(
            1 <= len(version) <= 64
            for version in (
                row.renderer_capability_version,
                row.validator_version,
                row.manifest_version,
            )
        ):
            raise SvgCanvasWorkspaceError(
                "canvas workspace version metadata is invalid"
            )
    max_creation = max(creation_sequences, default=0)
    max_modification = max(modification_sequences, default=0)
    if ordered_creation_sequences != sorted(ordered_creation_sequences):
        raise SvgCanvasWorkspaceError(
            "canvas workspace order must follow creationSequence"
        )
    if manifest.next_creation_sequence <= max_creation:
        raise SvgCanvasWorkspaceError(
            "nextCreationSequence does not exceed its high-water mark"
        )
    if manifest.next_modification_sequence <= max_modification:
        raise SvgCanvasWorkspaceError(
            "nextModificationSequence does not exceed its high-water mark"
        )
    if (
        manifest.focused_scene_id is not None
        and manifest.focused_scene_id not in scene_ids
    ):
        raise SvgCanvasWorkspaceError("focusedSceneId is absent from canvas workspace")


def _structural_manifest_digest(manifest: CanvasWorkspaceManifest) -> str:
    normalized = {
        "protocolVersion": manifest.protocol_version,
        "workspaceEpoch": manifest.workspace_epoch,
        "workspaceRevision": manifest.workspace_revision,
        "nextCreationSequence": manifest.next_creation_sequence,
        "nextModificationSequence": manifest.next_modification_sequence,
        "scenes": [
            {
                "sceneId": row.scene_id,
                "sceneRevision": row.scene_revision,
                "generationId": row.generation_id,
                "sourceTurnId": row.source_turn_id,
                "svgSha256": row.svg_sha256,
                "manifestSha256": row.manifest_sha256,
                "svgByteLength": row.svg_byte_length,
                "manifestByteLength": row.manifest_byte_length,
                "rendererCapabilityVersion": row.renderer_capability_version,
                "validatorVersion": row.validator_version,
                "manifestVersion": row.manifest_version,
                "creationSequence": row.creation_sequence,
                "modificationSequence": row.modification_sequence,
                "title": row.title,
                "summary": row.summary,
            }
            for row in manifest.scenes
        ],
    }
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scene_matches_manifest(
    scene: ActiveSvgCanvasScene,
    expected: CanvasWorkspaceManifestScene,
) -> bool:
    manifest = scene.document.manifest
    return (
        scene.scene_id == expected.scene_id
        and scene.revision == expected.scene_revision
        and scene.generation_id == expected.generation_id
        and scene.source_turn_id == expected.source_turn_id
        and scene.document.svg_sha256 == expected.svg_sha256
        and scene.document.manifest_sha256 == expected.manifest_sha256
        and len(scene.document.svg_bytes) == expected.svg_byte_length
        and len(scene.document.manifest_bytes) == expected.manifest_byte_length
        and manifest["rendererCapabilityVersion"]
        == expected.renderer_capability_version
        and manifest["validatorVersion"] == expected.validator_version
        and manifest["manifestVersion"] == expected.manifest_version
    )


def _manifest_scene_from_active(
    scene: ActiveSvgCanvasScene,
    *,
    creation_sequence: int,
    modification_sequence: int | None,
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
        renderer_capability_version=str(manifest["rendererCapabilityVersion"]),
        validator_version=str(manifest["validatorVersion"]),
        manifest_version=str(manifest["manifestVersion"]),
        creation_sequence=creation_sequence,
        modification_sequence=modification_sequence,
        title=scene.document.title[:160],
        summary=scene.document.description[:800],
    )
