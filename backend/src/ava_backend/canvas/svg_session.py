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

    def __init__(self, *, workspace: SvgCanvasWorkspace | None = None) -> None:
        self._candidate: SvgCanvasCandidate | None = None
        self._workspace = workspace or SvgCanvasWorkspace()

    @property
    def workspace(self) -> SvgCanvasWorkspace:
        return self._workspace

    @property
    def candidate(self) -> SvgCanvasCandidate | None:
        return self._candidate

    @property
    def active(self) -> ActiveSvgCanvasScene | None:
        return self._workspace.current

    @property
    def last_interaction(self) -> RecordedSvgCanvasInteraction | None:
        return self._workspace.last_interaction

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
    ) -> ActiveSvgCanvasScene:
        candidate = self._require_candidate(generation_id)
        if candidate.status is not SvgCandidateStatus.PRESENTATION_SCHEDULED:
            self._fail(
                SvgCanvasStateErrorCode.INVALID_CANDIDATE_TRANSITION,
                "canvasPresented is valid only after a presentation cue",
            )
        if first_rendered_sample < 0:
            raise ValueError("first_rendered_sample cannot be negative")
        if (
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
            self._workspace.commit(
                activated,
                base_scene_id=candidate.base_scene_id,
                base_revision=candidate.base_revision,
                workspace_epoch=candidate.workspace_epoch,
            )
        except SvgCanvasWorkspaceError as error:
            self._fail(
                SvgCanvasStateErrorCode.CANONICAL_SCENE_CHANGED,
                str(error),
            )
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
            return self._workspace.record_interaction(recorded)
        except SvgCanvasWorkspaceError as error:
            self._fail(SvgCanvasStateErrorCode.CANONICAL_SCENE_CHANGED, str(error))

    def render_realtime_context(self, *, pending_visual: str = "") -> str:
        active = self.active
        if active is None and not pending_visual:
            return ""
        prefix = '<canvas_context trust="untrusted_model_generated_data">'
        suffix = "</canvas_context>"
        if active is None:
            if len(prefix) + len(pending_visual) + len(suffix) > (
                _MAX_REALTIME_CANVAS_CONTEXT_CHARACTERS
            ):
                raise ValueError("pending canvas context exceeds its bounded limit")
            return f"{prefix}{pending_visual}{suffix}"
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
            f'<active_visual interaction_mode="{escape(active.document.interaction_mode.value)}">'
            f"<title>{escape(active.document.title)}</title>"
            f"<description>{escape(active.document.description)}</description>"
            f"{interaction_xml}"
        )
        active_suffix = "</active_visual>"
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
        for item in semantic_items:
            semantic_xml = f"<semantics>{''.join((*selected, item))}</semantics>"
            if (
                len(prefix)
                + len(pending_visual)
                + len(active_prefix)
                + len(semantic_xml)
                + len(active_suffix)
                + len(suffix)
                > _MAX_REALTIME_CANVAS_CONTEXT_CHARACTERS
            ):
                break
            selected.append(item)
        semantic_xml = f"<semantics>{''.join(selected)}</semantics>" if selected else ""
        active_context = f"{active_prefix}{semantic_xml}{active_suffix}"
        if len(prefix) + len(pending_visual) + len(active_context) + len(suffix) > (
            _MAX_REALTIME_CANVAS_CONTEXT_CHARACTERS
        ):
            raise ValueError("canvas context exceeds its bounded limit")
        return f"{prefix}{pending_visual}{active_context}{suffix}"

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
