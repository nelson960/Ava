from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from html import escape
from typing import NoReturn

from .svg_document import SvgInteractionBinding, ValidatedSvgDocument
from .svg_specialist import SvgCanvasSpecialistGeneration
from .svg_workspace import (
    ActiveSvgCanvasScene,
    RecordedSvgCanvasInteraction,
    SvgCanvasWorkspace,
    SvgCanvasWorkspaceError,
    SvgCanvasExecutionStatus,
)

_MAX_REALTIME_CANVAS_CONTEXT_CHARACTERS = 2_400


class SvgCandidateStatus(StrEnum):
    DOCUMENT_READY = "documentReady"
    PREPARED = "prepared"
    PRESENTATION_SCHEDULED = "presentationScheduled"


class SvgCanvasStateErrorCode(StrEnum):
    CANDIDATE_ALREADY_PENDING = "candidateAlreadyPending"
    GENERATION_MISMATCH = "generationMismatch"
    INVALID_CANDIDATE_TRANSITION = "invalidCandidateTransition"
    CONTENT_HASH_MISMATCH = "contentHashMismatch"
    CAPABILITY_VERSION_MISMATCH = "capabilityVersionMismatch"
    CANONICAL_SCENE_CHANGED = "canonicalSceneChanged"
    NO_ACTIVE_SCENE = "noActiveScene"
    SCENE_MISMATCH = "sceneMismatch"
    SCENE_REVISION_MISMATCH = "sceneRevisionMismatch"
    INTERACTION_REVISION_STALE = "interactionRevisionStale"
    BINDING_UNKNOWN = "bindingUnknown"


class SvgCanvasStateError(ValueError):
    def __init__(self, code: SvgCanvasStateErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SvgCanvasCandidate:
    generation_id: str
    source_turn_id: str
    scene_id: str
    revision: int
    base_scene_id: str | None
    base_revision: int
    workspace_epoch: int
    document: ValidatedSvgDocument
    status: SvgCandidateStatus = SvgCandidateStatus.DOCUMENT_READY
    presentation_sample_offset: int | None = None


@dataclass(frozen=True, slots=True)
class SvgCanvasPresentationCue:
    generation_id: str
    scene_id: str
    revision: int
    presentation_sample_offset: int
    svg_sha256: str
    manifest_sha256: str


class SvgCanvasSessionState:
    """Session-local candidate state backed by a committed canvas workspace."""

    def __init__(
        self,
        *,
        workspace: SvgCanvasWorkspace | None = None,
        inherit_focus: bool = True,
    ) -> None:
        self._candidate: SvgCanvasCandidate | None = None
        self._workspace = workspace or SvgCanvasWorkspace()
        self._focused_scene: ActiveSvgCanvasScene | None = (
            self._workspace.current if inherit_focus else None
        )
        self._last_interaction: RecordedSvgCanvasInteraction | None = (
            self._workspace.last_interaction if inherit_focus else None
        )
        self.focus_revision: int = 0
        self._presentation_visible: bool = False

    @property
    def workspace(self) -> SvgCanvasWorkspace:
        return self._workspace

    @property
    def candidate(self) -> SvgCanvasCandidate | None:
        return self._candidate

    @property
    def presentation_visible(self) -> bool:
        return self._presentation_visible

    @property
    def focused_scene(self) -> ActiveSvgCanvasScene | None:
        return self._focused_scene

    @property
    def presented_scene(self) -> ActiveSvgCanvasScene | None:
        return self._focused_scene if self._presentation_visible else None

    @property
    def active(self) -> ActiveSvgCanvasScene | None:
        return self.presented_scene

    @property
    def last_interaction(self) -> RecordedSvgCanvasInteraction | None:
        return self._last_interaction

    def set_presentation_visible(self, visible: bool) -> None:
        self._presentation_visible = visible
        if not visible:
            self._last_interaction = None

    def hide_presentation(self) -> None:
        self.set_presentation_visible(False)

    def present_focused(self) -> ActiveSvgCanvasScene | None:
        if self._focused_scene is not None:
            self.set_presentation_visible(True)
        return self.presented_scene

    def clear(self) -> None:
        self._focused_scene = None
        self._last_interaction = None
        self.focus_revision = 0
        self._presentation_visible = False

    def restore_focused(self, scene: ActiveSvgCanvasScene) -> ActiveSvgCanvasScene:
        restored = self._workspace.restore_focused(scene)
        self._focused_scene = restored
        self._presentation_visible = False
        self._last_interaction = None
        return restored

    def focus_retained(
        self,
        scene: ActiveSvgCanvasScene,
        *,
        focus_revision: int | None = None,
    ) -> ActiveSvgCanvasScene:
        focused = self._workspace.focus_retained(scene)
        self._focused_scene = focused
        if focus_revision is not None:
            self.focus_revision = focus_revision
            self._workspace.focus_revision = focus_revision
        self._last_interaction = None
        return focused

    def stage_generation(
        self,
        generation: SvgCanvasSpecialistGeneration,
        *,
        source_turn_id: str,
        base_scene_id: str | None,
        base_revision: int,
        workspace_epoch: int,
    ) -> SvgCanvasCandidate:
        if self._candidate is not None:
            self._fail(
                SvgCanvasStateErrorCode.CANDIDATE_ALREADY_PENDING,
                "an SVG canvas candidate is already pending",
            )
        if workspace_epoch != self._workspace.reset_epoch:
            self._fail(
                SvgCanvasStateErrorCode.CANONICAL_SCENE_CHANGED,
                "canvas workspace was reset after generation acceptance",
            )
        if base_scene_id is None:
            if base_revision != 0:
                self._fail(
                    SvgCanvasStateErrorCode.CANONICAL_SCENE_CHANGED,
                    "a canvas generation without a base must use revision zero",
                )
            scene_id = _scene_identifier(generation.generation_id)
            revision = 1
        else:
            base = self._workspace.scene(base_scene_id)
            if base is None or base.revision != base_revision:
                self._fail(
                    SvgCanvasStateErrorCode.CANONICAL_SCENE_CHANGED,
                    "canonical SVG canvas changed before generation completed",
                )
            scene_id = base_scene_id
            revision = base_revision + 1
        candidate = SvgCanvasCandidate(
            generation_id=generation.generation_id,
            source_turn_id=source_turn_id,
            scene_id=scene_id,
            revision=revision,
            base_scene_id=base_scene_id,
            base_revision=base_revision,
            workspace_epoch=workspace_epoch,
            document=generation.document,
        )
        self._candidate = candidate
        if self._workspace.execution(candidate.generation_id) is not None:
            try:
                self._workspace.advance_execution(
                    candidate.generation_id,
                    SvgCanvasExecutionStatus.VALIDATED_CANDIDATE,
                    scene_id=candidate.scene_id,
                    scene_revision=candidate.revision,
                )
            except SvgCanvasWorkspaceError as error:
                self._candidate = None
                self._fail(SvgCanvasStateErrorCode.CANONICAL_SCENE_CHANGED, str(error))
        return candidate

    def mark_document_prepared(
        self,
        *,
        generation_id: str,
        svg_sha256: str,
        manifest_sha256: str,
        renderer_capability_version: str,
        validator_version: str,
    ) -> SvgCanvasCandidate:
        candidate = self._require_candidate(generation_id)
        if candidate.status is not SvgCandidateStatus.DOCUMENT_READY:
            self._fail(
                SvgCanvasStateErrorCode.INVALID_CANDIDATE_TRANSITION,
                "canvasDocumentPrepared is valid only for a ready document",
            )
        self._validate_content_identity(
            candidate,
            svg_sha256=svg_sha256,
            manifest_sha256=manifest_sha256,
        )
        document = candidate.document
        manifest = document.manifest
        if (
            manifest["rendererCapabilityVersion"] != renderer_capability_version
            or manifest["validatorVersion"] != validator_version
        ):
            self._fail(
                SvgCanvasStateErrorCode.CAPABILITY_VERSION_MISMATCH,
                "prepared canvas capability versions do not match the validated document",
            )
        candidate = replace(candidate, status=SvgCandidateStatus.PREPARED)
        self._candidate = candidate
        return candidate

    def schedule_presentation(
        self,
        *,
        generation_id: str,
        presentation_sample_offset: int,
    ) -> SvgCanvasPresentationCue:
        candidate = self._require_candidate(generation_id)
        if candidate.status is not SvgCandidateStatus.PREPARED:
            self._fail(
                SvgCanvasStateErrorCode.INVALID_CANDIDATE_TRANSITION,
                "a canvas can be scheduled only after document preparation",
            )
        if presentation_sample_offset < 0:
            raise ValueError("presentation_sample_offset cannot be negative")
        candidate = replace(
            candidate,
            status=SvgCandidateStatus.PRESENTATION_SCHEDULED,
            presentation_sample_offset=presentation_sample_offset,
        )
        self._candidate = candidate
        if self._workspace.execution(generation_id) is not None:
            try:
                self._workspace.advance_execution(
                    generation_id,
                    SvgCanvasExecutionStatus.PRESENTATION_SCHEDULED,
                )
            except SvgCanvasWorkspaceError as error:
                self._fail(SvgCanvasStateErrorCode.CANONICAL_SCENE_CHANGED, str(error))
        return SvgCanvasPresentationCue(
            generation_id=candidate.generation_id,
            scene_id=candidate.scene_id,
            revision=candidate.revision,
            presentation_sample_offset=presentation_sample_offset,
            svg_sha256=candidate.document.svg_sha256,
            manifest_sha256=candidate.document.manifest_sha256,
        )

    def reschedule_presentation_earlier(
        self,
        *,
        generation_id: str,
        presentation_sample_offset: int,
    ) -> SvgCanvasPresentationCue:
        """Move an unreachable source-turn cue to its final rendered sample."""

        candidate = self._require_candidate(generation_id)
        previous_offset = candidate.presentation_sample_offset
        if (
            candidate.status is not SvgCandidateStatus.PRESENTATION_SCHEDULED
            or previous_offset is None
        ):
            self._fail(
                SvgCanvasStateErrorCode.INVALID_CANDIDATE_TRANSITION,
                "only a scheduled canvas presentation can be moved earlier",
            )
        if (
            presentation_sample_offset < 0
            or presentation_sample_offset >= previous_offset
        ):
            self._fail(
                SvgCanvasStateErrorCode.INVALID_CANDIDATE_TRANSITION,
                "replacement canvas cue must precede its unreachable source cue",
            )
        candidate = replace(
            candidate,
            presentation_sample_offset=presentation_sample_offset,
        )
        self._candidate = candidate
        return SvgCanvasPresentationCue(
            generation_id=candidate.generation_id,
            scene_id=candidate.scene_id,
            revision=candidate.revision,
            presentation_sample_offset=presentation_sample_offset,
            svg_sha256=candidate.document.svg_sha256,
            manifest_sha256=candidate.document.manifest_sha256,
        )

    def mark_presented(
        self,
        *,
        generation_id: str,
        svg_sha256: str,
        manifest_sha256: str,
        first_rendered_sample: int,
        foreground: bool = True,
        retained: bool = False,
    ) -> ActiveSvgCanvasScene:
        candidate = self._require_candidate(generation_id)
        if candidate.status is not SvgCandidateStatus.PRESENTATION_SCHEDULED:
            self._fail(
                SvgCanvasStateErrorCode.INVALID_CANDIDATE_TRANSITION,
                "canvasPresented is valid only after a presentation cue",
            )
        if first_rendered_sample < 0:
            raise ValueError("first_rendered_sample cannot be negative")
        if not retained and (
            candidate.presentation_sample_offset is None
            or first_rendered_sample < candidate.presentation_sample_offset
        ):
            self._fail(
                SvgCanvasStateErrorCode.INVALID_CANDIDATE_TRANSITION,
                "canvas cannot be presented before its scheduled sample offset",
            )
        self._validate_content_identity(
            candidate,
            svg_sha256=svg_sha256,
            manifest_sha256=manifest_sha256,
        )
        activated = ActiveSvgCanvasScene(
            generation_id=candidate.generation_id,
            source_turn_id=candidate.source_turn_id,
            scene_id=candidate.scene_id,
            revision=candidate.revision,
            document=candidate.document,
            first_rendered_sample=first_rendered_sample,
        )
        try:
            if self._workspace.execution(generation_id) is not None:
                self._workspace.advance_execution(
                    generation_id,
                    SvgCanvasExecutionStatus.PRESENTED,
                )
            activated = self._workspace.commit(
                activated,
                base_scene_id=candidate.base_scene_id,
                base_revision=candidate.base_revision,
                workspace_epoch=candidate.workspace_epoch,
                foreground=foreground,
            )
        except SvgCanvasWorkspaceError as error:
            self._fail(
                SvgCanvasStateErrorCode.CANONICAL_SCENE_CHANGED,
                str(error),
            )
        if foreground:
            self._focused_scene = activated
            self._presentation_visible = True
            self._last_interaction = None
        self._candidate = None
        return activated

    def discard_candidate(
        self,
        *,
        generation_id: str,
    ) -> SvgCanvasCandidate:
        candidate = self._require_candidate(generation_id)
        self._candidate = None
        return candidate

    def candidate_or_error(self, generation_id: str) -> SvgCanvasCandidate:
        """Return the exact pending generation or fail with its protocol code."""

        return self._require_candidate(generation_id)

    def require_active_identity(
        self,
        *,
        generation_id: str,
        scene_id: str,
        scene_revision: int,
        svg_sha256: str,
        manifest_sha256: str,
    ) -> ActiveSvgCanvasScene:
        active = self.active
        if active is None:
            self._fail(
                SvgCanvasStateErrorCode.NO_ACTIVE_SCENE,
                "active canvas identity requires an active scene",
            )
        if active.generation_id != generation_id:
            self._fail(
                SvgCanvasStateErrorCode.GENERATION_MISMATCH,
                "canvas generation does not match the active scene",
            )
        if active.scene_id != scene_id:
            self._fail(
                SvgCanvasStateErrorCode.SCENE_MISMATCH,
                "canvas scene does not match the active scene",
            )
        if active.revision != scene_revision:
            self._fail(
                SvgCanvasStateErrorCode.SCENE_REVISION_MISMATCH,
                "canvas revision does not match the active scene",
            )
        if (
            active.document.svg_sha256 != svg_sha256
            or active.document.manifest_sha256 != manifest_sha256
        ):
            self._fail(
                SvgCanvasStateErrorCode.CONTENT_HASH_MISMATCH,
                "canvas hashes do not match the active scene",
            )
        return active

    def record_interaction(
        self,
        *,
        scene_id: str,
        scene_revision: int,
        interaction_revision: int,
        svg_sha256: str,
        manifest_sha256: str,
        binding_id: str,
    ) -> RecordedSvgCanvasInteraction:
        active = self.active
        if active is None:
            self._fail(
                SvgCanvasStateErrorCode.NO_ACTIVE_SCENE,
                "canvasInteraction requires an active presented scene",
            )
        if active.scene_id != scene_id:
            self._fail(
                SvgCanvasStateErrorCode.SCENE_MISMATCH,
                f'active scene "{active.scene_id}" cannot accept "{scene_id}"',
            )
        if active.revision != scene_revision:
            self._fail(
                SvgCanvasStateErrorCode.SCENE_REVISION_MISMATCH,
                f"expected canvas revision {active.revision}, received {scene_revision}",
            )
        if (
            active.document.svg_sha256 != svg_sha256
            or active.document.manifest_sha256 != manifest_sha256
        ):
            self._fail(
                SvgCanvasStateErrorCode.CONTENT_HASH_MISMATCH,
                "canvas interaction content hashes do not match the active scene",
            )
        previous_revision = (
            self.last_interaction.interaction_revision
            if self.last_interaction is not None
            else 0
        )
        if interaction_revision <= previous_revision:
            self._fail(
                SvgCanvasStateErrorCode.INTERACTION_REVISION_STALE,
                "canvas interactionRevision must increase monotonically",
            )
        binding = active.document.bindings.get(binding_id)
        if binding is None:
            self._fail(
                SvgCanvasStateErrorCode.BINDING_UNKNOWN,
                f'canvas binding "{binding_id}" does not exist',
            )
        recorded = _recorded_interaction(
            active,
            binding,
            interaction_revision=interaction_revision,
        )
        try:
            self._last_interaction = recorded
            return self._workspace.record_interaction(recorded)
        except SvgCanvasWorkspaceError as error:
            self._fail(SvgCanvasStateErrorCode.CANONICAL_SCENE_CHANGED, str(error))

    def _render_retained_catalog(self, *, max_characters: int) -> str:
        if self._workspace.navigation_ready:
            return self._render_synchronized_retained_catalog(
                max_characters=max_characters
            )
        scenes = self._workspace.scenes
        if not scenes or max_characters <= 0:
            return ""
        retained_prefix = '<retained_visuals purpose="available_to_show">'
        retained_suffix = "</retained_visuals>"
        overhead = len(retained_prefix) + len(retained_suffix)
        if overhead >= max_characters:
            return ""

        focused = self._focused_scene
        presented = self.presented_scene
        recent_scenes = sorted(
            scenes,
            key=lambda scene: self._workspace.scene_recency(scene.scene_id),
            reverse=True,
        )
        ordered_scenes: list[ActiveSvgCanvasScene] = []
        for priority_scene in (presented, focused, recent_scenes[0]):
            if priority_scene is not None and priority_scene not in ordered_scenes:
                ordered_scenes.append(priority_scene)
        for scene in recent_scenes:
            if scene not in ordered_scenes:
                ordered_scenes.append(scene)

        entries: list[str] = []
        current_len = overhead
        for index, scene in enumerate(ordered_scenes, start=1):
            is_focused = focused is not None and scene.scene_id == focused.scene_id
            is_presented = self._presentation_visible and is_focused
            title = _bounded_escaped_text(
                scene.document.title,
                source_characters=60,
                rendered_characters=80,
            )
            summary = _bounded_escaped_text(
                scene.document.description,
                source_characters=100,
                rendered_characters=120,
            )
            recency = self._workspace.scene_recency(scene.scene_id)
            recent_rank = recent_scenes.index(scene) + 1
            entry = (
                f'<visual scene_id="{scene.scene_id}" revision="{scene.revision}" '
                f'focused="{"true" if is_focused else "false"}" '
                f'presented="{"true" if is_presented else "false"}" '
                f'order="{index}" recency="{recency}" recent_rank="{recent_rank}">'
                f"<title>{title}</title>"
                f"<summary>{summary}</summary>"
                f"</visual>"
            )
            if current_len + len(entry) > max_characters:
                break
            entries.append(entry)
            current_len += len(entry)

        if not entries:
            return ""
        return f"{retained_prefix}{''.join(entries)}{retained_suffix}"

    def _render_synchronized_retained_catalog(self, *, max_characters: int) -> str:
        rows = self._workspace.manifest_scenes
        if not rows or max_characters <= 0:
            return ""
        retained_prefix = '<retained_visuals purpose="available_to_show">'
        retained_suffix = "</retained_visuals>"
        if len(retained_prefix) + len(retained_suffix) >= max_characters:
            return ""
        focused_id = self._focused_scene.scene_id if self._focused_scene else None
        presented_id = self.presented_scene.scene_id if self.presented_scene else None
        prioritized_ids = [
            presented_id,
            focused_id,
            self._workspace.latest_created_scene_id,
            self._workspace.latest_modified_scene_id,
        ]
        ordered_rows = []
        for scene_id in prioritized_ids:
            row = self._workspace.manifest_scene(scene_id) if scene_id else None
            if row is not None and row not in ordered_rows:
                ordered_rows.append(row)
        ordered_rows.extend(row for row in rows if row not in ordered_rows)
        known_modified = sorted(
            (row for row in rows if row.modification_sequence is not None),
            key=lambda row: row.modification_sequence or 0,
        )
        modification_ranks = {
            row.scene_id: index + 1 for index, row in enumerate(known_modified)
        }
        entries: list[str] = []
        current_len = len(retained_prefix) + len(retained_suffix)
        for row in ordered_rows:
            position = next(
                index + 1
                for index, candidate in enumerate(rows)
                if candidate.scene_id == row.scene_id
            )
            modification_rank = modification_ranks.get(row.scene_id)
            modification_attribute = (
                f' modification_rank="{modification_rank}"'
                if modification_rank is not None
                else ""
            )
            entry = (
                f'<visual scene_id="{escape(row.scene_id)}" revision="{row.scene_revision}" '
                f'position="{position}" creation_rank="{position}"{modification_attribute} '
                f'focused="{"true" if row.scene_id == focused_id else "false"}" '
                f'presented="{"true" if row.scene_id == presented_id else "false"}">'
                f"<title>{_bounded_escaped_text(row.title, source_characters=60, rendered_characters=80)}</title>"
                f"<summary>{_bounded_escaped_text(row.summary, source_characters=100, rendered_characters=120)}</summary>"
                "</visual>"
            )
            if current_len + len(entry) > max_characters:
                continue
            entries.append(entry)
            current_len += len(entry)
        return (
            f"{retained_prefix}{''.join(entries)}{retained_suffix}" if entries else ""
        )

    def _render_workspace_header(self) -> str:
        if not self._workspace.navigation_ready:
            return '<canvas_workspace synchronized="false"/>'
        rows = self._workspace.manifest_scenes
        focused_id = self._focused_scene.scene_id if self._focused_scene else None
        position = next(
            (index + 1 for index, row in enumerate(rows) if row.scene_id == focused_id),
            None,
        )
        attributes = [
            'synchronized="true"',
            f'workspace_epoch="{self._workspace.workspace_epoch}"',
            f'workspace_revision="{self._workspace.workspace_revision}"',
            f'total="{len(rows)}"',
        ]
        if focused_id is not None and position is not None:
            assert self._focused_scene is not None
            focused = self._workspace.manifest_scene(focused_id)
            attributes.extend(
                [
                    f'focused_scene_id="{escape(focused_id)}"',
                    f'focused_revision="{focused.scene_revision if focused else self._focused_scene.revision}"',
                    f'focused_position="{position}"',
                    f'has_previous="{"true" if position > 1 else "false"}"',
                    f'has_next="{"true" if position < len(rows) else "false"}"',
                ]
            )
        latest_created = self._workspace.latest_created_scene_id
        latest_modified = self._workspace.latest_modified_scene_id
        if latest_created is not None:
            attributes.append(f'latest_created_scene_id="{escape(latest_created)}"')
        if latest_modified is not None:
            attributes.append(f'latest_modified_scene_id="{escape(latest_modified)}"')
        return f"<canvas_workspace {' '.join(attributes)}/>"

    def render_realtime_context(
        self,
        *,
        pending_visual: str = "",
    ) -> str:
        active = self.active
        prefix = '<canvas_context trust="untrusted_model_generated_data">'
        suffix = "</canvas_context>"
        presentation = (
            '<canvas_presentation trust="validated_current_state" visible="false"/>'
            if active is None
            else (
                '<canvas_presentation trust="validated_current_state" visible="true" '
                f'scene_id="{active.scene_id}" revision="{active.revision}"/>'
            )
        )
        workspace_header = self._render_workspace_header()
        base_overhead = (
            len(prefix)
            + len(suffix)
            + len(presentation)
            + len(workspace_header)
            + len(pending_visual)
        )
        if base_overhead > _MAX_REALTIME_CANVAS_CONTEXT_CHARACTERS:
            raise ValueError("pending canvas context exceeds its bounded limit")

        available_chars = _MAX_REALTIME_CANVAS_CONTEXT_CHARACTERS - base_overhead

        if active is None:
            catalog_xml = self._render_retained_catalog(max_characters=available_chars)
            return f"{prefix}{presentation}{workspace_header}{pending_visual}{catalog_xml}{suffix}"

        interaction = self.last_interaction
        interaction_xml = ""
        if interaction is not None:
            step_attribute = (
                f' step_index="{interaction.step_index}"'
                if interaction.step_index is not None
                else ""
            )
            interaction_xml = (
                f'<last_interaction mode="{escape(interaction.interaction_mode)}"'
                f"{step_attribute}>"
                f"<target>{escape(interaction.accessibility_label)}</target>"
                "</last_interaction>"
            )
        active_prefix = (
            f'<active_visual scene_id="{active.scene_id}" revision="{active.revision}" '
            f'interaction_mode="{escape(active.document.interaction_mode.value)}">'
            "<title>"
            f"{_bounded_escaped_text(active.document.title, source_characters=160, rendered_characters=240)}"
            "</title>"
            "<description>"
            f"{_bounded_escaped_text(active.document.description, source_characters=800, rendered_characters=600)}"
            "</description>"
            f"{interaction_xml}"
        )
        active_suffix = "</active_visual>"
        active_core_len = len(active_prefix) + len(active_suffix)

        reserved_active_semantics = 200
        catalog_budget = max(
            0, available_chars - active_core_len - reserved_active_semantics
        )
        catalog_xml = self._render_retained_catalog(max_characters=catalog_budget)

        remaining_for_active = available_chars - len(catalog_xml) - active_core_len
        semantic_items = [
            f'<item kind="visibleText">{escape(label)}</item>'
            for label in active.document.visible_text_labels
        ]
        semantic_items.extend(
            f'<item kind="namedObject">{escape(label)}</item>'
            for label in active.document.named_objects
        )
        semantic_items.extend(
            f'<item kind="interaction" mode="{escape(active.document.interaction_mode.value)}">'
            f"{escape(binding.accessibility_label)}</item>"
            for binding in sorted(
                active.document.bindings.values(),
                key=lambda item: item.binding_id,
            )
        )

        selected: list[str] = []
        semantics_prefix = "<semantics>"
        semantics_suffix = "</semantics>"
        for item in semantic_items:
            test_xml = (
                f"{semantics_prefix}{''.join((*selected, item))}{semantics_suffix}"
            )
            if len(test_xml) > remaining_for_active:
                break
            selected.append(item)
        semantic_xml = (
            f"{semantics_prefix}{''.join(selected)}</semantics>" if selected else ""
        )
        active_context = f"{active_prefix}{semantic_xml}{active_suffix}"
        total_content = (
            f"{prefix}{presentation}{workspace_header}{pending_visual}"
            f"{catalog_xml}{active_context}{suffix}"
        )
        if len(total_content) > _MAX_REALTIME_CANVAS_CONTEXT_CHARACTERS:
            raise ValueError("canvas context exceeds its bounded limit")
        return total_content

    def _require_candidate(self, generation_id: str) -> SvgCanvasCandidate:
        candidate = self._candidate
        if candidate is None or candidate.generation_id != generation_id:
            self._fail(
                SvgCanvasStateErrorCode.GENERATION_MISMATCH,
                "canvas generation does not match the pending candidate",
            )
        return candidate

    def _validate_content_identity(
        self,
        candidate: SvgCanvasCandidate,
        *,
        svg_sha256: str,
        manifest_sha256: str,
    ) -> None:
        if (
            candidate.document.svg_sha256 != svg_sha256
            or candidate.document.manifest_sha256 != manifest_sha256
        ):
            self._fail(
                SvgCanvasStateErrorCode.CONTENT_HASH_MISMATCH,
                "canvas content hashes do not match the pending candidate",
            )

    @staticmethod
    def _fail(code: SvgCanvasStateErrorCode, message: str) -> NoReturn:
        raise SvgCanvasStateError(code, message)


def _scene_identifier(generation_id: str) -> str:
    digest = sha256(generation_id.encode("utf-8")).hexdigest()[:16]
    return f"scene_{digest}"


def _bounded_escaped_text(
    value: str,
    *,
    source_characters: int,
    rendered_characters: int,
) -> str:
    rendered: list[str] = []
    rendered_length = 0
    for character in value[:source_characters]:
        escaped = escape(character, quote=False)
        if rendered_length + len(escaped) > rendered_characters:
            break
        rendered.append(escaped)
        rendered_length += len(escaped)
    return "".join(rendered)


def _recorded_interaction(
    active: ActiveSvgCanvasScene,
    binding: SvgInteractionBinding,
    *,
    interaction_revision: int,
) -> RecordedSvgCanvasInteraction:
    return RecordedSvgCanvasInteraction(
        scene_id=active.scene_id,
        scene_revision=active.revision,
        interaction_revision=interaction_revision,
        svg_sha256=active.document.svg_sha256,
        manifest_sha256=active.document.manifest_sha256,
        binding_id=binding.binding_id,
        interaction_mode=active.document.interaction_mode.value,
        target_id=binding.target_id,
        accessibility_label=binding.accessibility_label,
        step_index=binding.step_index,
    )
