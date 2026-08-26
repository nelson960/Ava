from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from html import escape
from typing import NoReturn

from .svg_document import SvgInteractionBinding, ValidatedSvgDocument
from .svg_specialist import SvgCanvasSpecialistGeneration

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
    BINDING_LOCAL_ONLY = "bindingLocalOnly"


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
    document: ValidatedSvgDocument
    status: SvgCandidateStatus = SvgCandidateStatus.DOCUMENT_READY
    presentation_sample_offset: int | None = None


@dataclass(frozen=True, slots=True)
class ActiveSvgCanvasScene:
    generation_id: str
    source_turn_id: str
    scene_id: str
    revision: int
    document: ValidatedSvgDocument
    first_rendered_sample: int


@dataclass(frozen=True, slots=True)
class SvgCanvasPresentationCue:
    generation_id: str
    scene_id: str
    revision: int
    presentation_sample_offset: int
    svg_sha256: str
    manifest_sha256: str


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


class SvgCanvasSessionState:
    """Canonical SVG state for one persistent Realtime conversation session."""

    def __init__(self) -> None:
        self._candidate: SvgCanvasCandidate | None = None
        self._active: ActiveSvgCanvasScene | None = None
        self._last_interaction: RecordedSvgCanvasInteraction | None = None

    @property
    def candidate(self) -> SvgCanvasCandidate | None:
        return self._candidate

    @property
    def active(self) -> ActiveSvgCanvasScene | None:
        return self._active

    @property
    def last_interaction(self) -> RecordedSvgCanvasInteraction | None:
        return self._last_interaction

    def stage_generation(
        self,
        generation: SvgCanvasSpecialistGeneration,
        *,
        source_turn_id: str,
    ) -> SvgCanvasCandidate:
        if self._candidate is not None:
            self._fail(
                SvgCanvasStateErrorCode.CANDIDATE_ALREADY_PENDING,
                "an SVG canvas candidate is already pending",
            )
        active = self._active
        scene_id = (
            active.scene_id
            if active is not None
            else _scene_identifier(generation.generation_id)
        )
        candidate = SvgCanvasCandidate(
            generation_id=generation.generation_id,
            source_turn_id=source_turn_id,
            scene_id=scene_id,
            revision=(active.revision + 1 if active is not None else 1),
            base_scene_id=(active.scene_id if active is not None else None),
            base_revision=(active.revision if active is not None else 0),
            document=generation.document,
        )
        self._candidate = candidate
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
        active = self._active
        active_scene_id = active.scene_id if active is not None else None
        active_revision = active.revision if active is not None else 0
        if (
            active_scene_id != candidate.base_scene_id
            or active_revision != candidate.base_revision
        ):
            self._fail(
                SvgCanvasStateErrorCode.CANONICAL_SCENE_CHANGED,
                "canonical SVG canvas changed before presentation completed",
            )
        activated = ActiveSvgCanvasScene(
            generation_id=candidate.generation_id,
            source_turn_id=candidate.source_turn_id,
            scene_id=candidate.scene_id,
            revision=candidate.revision,
            document=candidate.document,
            first_rendered_sample=first_rendered_sample,
        )
        self._active = activated
        self._candidate = None
        self._last_interaction = None
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

    def discard_candidate_for_interrupted_turn(
        self,
        *,
        source_turn_id: str,
    ) -> SvgCanvasCandidate | None:
        candidate = self._candidate
        if candidate is None or candidate.source_turn_id != source_turn_id:
            return None
        self._candidate = None
        return candidate

    def require_active_identity(
        self,
        *,
        generation_id: str,
        scene_id: str,
        scene_revision: int,
        svg_sha256: str,
        manifest_sha256: str,
    ) -> ActiveSvgCanvasScene:
        active = self._active
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
        active = self._active
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
            self._last_interaction.interaction_revision
            if self._last_interaction is not None
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
        if not binding.is_server_reportable:
            self._fail(
                SvgCanvasStateErrorCode.BINDING_LOCAL_ONLY,
                f'canvas binding "{binding_id}" is local-only and must not be sent',
            )
        recorded = _recorded_interaction(
            active,
            binding,
            interaction_revision=interaction_revision,
        )
        self._last_interaction = recorded
        return recorded

    def render_realtime_context(self) -> str:
        active = self._active
        if active is None:
            return ""
        interaction = self._last_interaction
        interaction_xml = ""
        if interaction is not None:
            interaction_xml = (
                "<last_interaction>"
                f"<action>{escape(interaction.action)}</action>"
                f"<target>{escape(interaction.accessibility_label)}</target>"
                "</last_interaction>"
            )
        prefix = (
            '<canvas_context trust="untrusted_model_generated_data" '
            f'scene_id="{escape(active.scene_id)}" revision="{active.revision}" '
            f'svg_sha256="{active.document.svg_sha256}">'
            f"<title>{escape(active.document.title)}</title>"
            f"<description>{escape(active.document.description)}</description>"
            f"{interaction_xml}"
        )
        suffix = "</canvas_context>"
        semantic_items = [
            f'<item kind="visibleText">{escape(label)}</item>'
            for label in active.document.visible_text_labels
        ]
        semantic_items.extend(
            f'<item kind="namedObject">{escape(label)}</item>'
            for label in active.document.named_objects
        )
        semantic_items.extend(
            f'<item kind="interaction" action="{escape(binding.action)}">'
            f"{escape(binding.accessibility_label)}</item>"
            for binding in sorted(
                active.document.bindings.values(),
                key=lambda item: item.binding_id,
            )
            if binding.is_server_reportable
        )
        selected: list[str] = []
        for item in semantic_items:
            semantic_xml = (
                f"<semantics>{''.join((*selected, item))}</semantics>"
            )
            if (
                len(prefix) + len(semantic_xml) + len(suffix)
                > _MAX_REALTIME_CANVAS_CONTEXT_CHARACTERS
            ):
                break
            selected.append(item)
        semantic_xml = (
            f"<semantics>{''.join(selected)}</semantics>" if selected else ""
        )
        return f"{prefix}{semantic_xml}{suffix}"

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
        action=binding.action,
        target_id=binding.target_id,
        accessibility_label=binding.accessibility_label,
        declared_value=binding.declared_value,
    )
