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
    CanvasSceneSelector,
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
    FOCUS_REVISION_MISMATCH = "focusRevisionMismatch"
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
        return self._workspace.current

    @property
    def presented_scene(self) -> ActiveSvgCanvasScene | None:
        return self.focused_scene if self._presentation_visible else None

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

    def clear(self) -> None:
        self._workspace.clear_focus()
        self._last_interaction = None
        self.focus_revision = 0
        self._presentation_visible = False

    def restore_focused(self, scene: ActiveSvgCanvasScene) -> ActiveSvgCanvasScene:
        restored = self._workspace.restore_focused(scene)
        self._presentation_visible = False
        self._last_interaction = None
        return restored

    def hydrate_retained(self, scene: ActiveSvgCanvasScene) -> ActiveSvgCanvasScene:
        """Cache one validated retained scene without mutating focus or visibility."""
        return self._workspace.hydrate_retained(scene)

    def focus_retained(
        self,
        scene: ActiveSvgCanvasScene,
        *,
        focus_revision: int | None = None,
    ) -> ActiveSvgCanvasScene:
        focused = self._workspace.focus_retained(scene)
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
        focus_revision: int,
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
        if focus_revision != self.focus_revision:
            self._fail(
                SvgCanvasStateErrorCode.FOCUS_REVISION_MISMATCH,
                f"expected focus revision {self.focus_revision}, received {focus_revision}",
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

    def _render_retained_catalog(self) -> str:
        if not self._workspace.navigation_ready:
            return ""
        return self._render_synchronized_retained_catalog()

    def _render_synchronized_retained_catalog(self) -> str:
        """Render every retained row before allocating focused-scene detail."""

        rows = self._workspace.manifest_scenes
        if not rows:
            return ""
        titles = self._workspace.model_facing_titles()
        modification_rows = sorted(
            (row for row in rows if row.modification_sequence is not None),
            key=lambda row: row.modification_sequence or 0,
        )
        modification_orders = {
            row.scene_id: index for index, row in enumerate(modification_rows, start=1)
        }
        entries = [
            (
                f'<scene position="{position}" '
                f'title="{_bounded_escaped_text(titles[row.scene_id], source_characters=56, rendered_characters=56, quote=True)}" '
                f'summary="{_bounded_escaped_text(row.summary, source_characters=32, rendered_characters=32, quote=True)}" '
                f'created_order="{position}" '
                f'modified_order="{modification_orders.get(row.scene_id, "none")}"/>'
            )
            for position, row in enumerate(rows, start=1)
        ]
        return (
            '<retained_visuals purpose="available_to_show">'
            f"{''.join(entries)}"
            "</retained_visuals>"
        )

    def _render_workspace_header(self) -> str:
        if not self._workspace.navigation_ready or not self._workspace.manifest_scenes:
            return ""
        rows = self._workspace.manifest_scenes
        focused = self.focused_scene
        focused_id = focused.scene_id if focused else None
        position = next(
            (index + 1 for index, row in enumerate(rows) if row.scene_id == focused_id),
            None,
        )
        latest_created_id = self._workspace.latest_created_scene_id
        latest_modified_id = self._workspace.latest_modified_scene_id
        latest_created_position = next(
            (
                index + 1
                for index, row in enumerate(rows)
                if row.scene_id == latest_created_id
            ),
            None,
        )
        latest_modified_position = next(
            (
                index + 1
                for index, row in enumerate(rows)
                if row.scene_id == latest_modified_id
            ),
            None,
        )
        attributes = [
            f'retained_count="{len(rows)}"',
            f'latest_created_position="{latest_created_position or "none"}"',
            f'latest_modified_position="{latest_modified_position or "none"}"',
        ]
        if position is not None:
            attributes.extend(
                [
                    f'focused_position="{position}"',
                    f'has_previous="{"true" if position > 1 else "false"}"',
                    f'has_next="{"true" if position < len(rows) else "false"}"',
                ]
            )
        else:
            attributes.append('focused_position="none"')
        return f"<canvas_workspace {' '.join(attributes)}/>"

    def render_realtime_context(
        self,
        *,
        pending_visual: str = "",
    ) -> str:
        focused = self.focused_scene
        if not (
            self._workspace.manifest_scenes
            or focused is not None
            or pending_visual
            or self._presentation_visible
        ):
            return ""
        prefix = '<canvas_context trust="backend_semantic_canvas_metadata">'
        suffix = "</canvas_context>"
        presentation = (
            '<canvas_presentation trust="validated_current_state" '
            f'visible="{"true" if self._presentation_visible else "false"}"/>'
        )
        workspace_header = self._render_workspace_header()
        directory_xml = self._render_retained_catalog()
        base_content = (
            len(prefix)
            + len(suffix)
            + len(presentation)
            + len(workspace_header)
            + len(directory_xml)
        )
        if base_content > _MAX_REALTIME_CANVAS_CONTEXT_CHARACTERS:
            raise ValueError(
                "complete retained canvas directory exceeds its bounded limit"
            )

        remaining_chars = _MAX_REALTIME_CANVAS_CONTEXT_CHARACTERS - base_content
        optional_sections: list[str] = []
        if pending_visual and len(pending_visual) <= remaining_chars:
            optional_sections.append(pending_visual)
            remaining_chars -= len(pending_visual)

        if focused is None:
            return f"{prefix}{presentation}{workspace_header}{directory_xml}{''.join(optional_sections)}{suffix}"

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
        control_kinds = sorted(
            {control.kind.value for control in focused.document.controls.values()}
        )
        continuous_controls = ",".join(control_kinds) if control_kinds else "none"
        focused_resolution = self._workspace.resolve_scene_selector(
            CanvasSceneSelector.CURRENT
        )
        focused_position = (
            f' position="{focused_resolution.position}"'
            if focused_resolution.position is not None
            else ""
        )
        focused_title = self._workspace.model_facing_titles().get(
            focused.scene_id,
            focused.document.title,
        )
        active_prefix = (
            f"<focused_scene{focused_position} "
            f'interaction_mode="{escape(focused.document.interaction_mode.value)}">'
            "<title>"
            f"{_bounded_escaped_text(focused_title, source_characters=56, rendered_characters=56)}"
            "</title>"
        )
        active_suffix = "</focused_scene>"
        active_core_len = len(active_prefix) + len(active_suffix)
        if active_core_len > remaining_chars:
            return f"{prefix}{presentation}{workspace_header}{directory_xml}{''.join(optional_sections)}{suffix}"

        remaining_for_active = remaining_chars - active_core_len
        active_parts = [active_prefix]
        description_open = "<description>"
        description_close = "</description>"
        description_overhead = len(description_open) + len(description_close)
        if remaining_for_active > description_overhead:
            description = _bounded_escaped_text(
                focused.document.description,
                source_characters=800,
                rendered_characters=min(
                    240,
                    remaining_for_active - description_overhead,
                ),
            )
            if description:
                description_xml = f"{description_open}{description}{description_close}"
                active_parts.append(description_xml)
                remaining_for_active -= len(description_xml)
        controls_xml = f'<continuous_controls kinds="{continuous_controls}"/>'
        if len(controls_xml) <= remaining_for_active:
            active_parts.append(controls_xml)
            remaining_for_active -= len(controls_xml)
        if interaction_xml and len(interaction_xml) <= remaining_for_active:
            active_parts.append(interaction_xml)
            remaining_for_active -= len(interaction_xml)
        semantic_items = [
            f'<item kind="visibleText">{escape(label)}</item>'
            for label in focused.document.visible_text_labels
        ]
        semantic_items.extend(
            f'<item kind="namedObject">{escape(label)}</item>'
            for label in focused.document.named_objects
        )
        semantic_items.extend(
            f'<item kind="interaction" mode="{escape(focused.document.interaction_mode.value)}">'
            f"{escape(binding.accessibility_label)}</item>"
            for binding in sorted(
                focused.document.bindings.values(),
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
        active_parts.extend((semantic_xml, active_suffix))
        active_context = "".join(active_parts)
        total_content = (
            f"{prefix}{presentation}{workspace_header}{directory_xml}"
            f"{''.join(optional_sections)}{active_context}{suffix}"
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
    quote: bool = False,
) -> str:
    rendered: list[str] = []
    rendered_length = 0
    for character in value[:source_characters]:
        escaped = escape(character, quote=quote)
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
