from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from .svg_document import (
    SVG_DEFAULT_MAX_BYTES,
    SVG_PROTOCOL_MAX_BYTES,
    ValidatedSvgDocument,
)
from .svg_session import SvgCanvasCandidate
from .svg_specialist import SvgCanvasVisibleDraft
from .svg_workspace import ActiveSvgCanvasScene, CanvasWorkspaceManifestScene

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,63}$")


class SvgDocumentStoreError(RuntimeError):
    """Base failure for the bounded independent SVG document lane."""


class SvgDocumentStoreCapacityError(SvgDocumentStoreError):
    """The store cannot accept another document without silent eviction."""


class SvgDocumentNotFoundError(SvgDocumentStoreError):
    """The requested session/generation pair has no published document."""


@dataclass(frozen=True, slots=True)
class StoredSvgCanvasDocument:
    session_id: str
    generation_id: str
    source_turn_id: str
    scene_id: str
    scene_revision: int
    document: ValidatedSvgDocument

    @property
    def svg_path(self) -> str:
        return (
            f"/v4/realtime/sessions/{self.session_id}/canvas-documents/"
            f"{self.generation_id}/svg"
        )

    @property
    def manifest_path(self) -> str:
        return (
            f"/v4/realtime/sessions/{self.session_id}/canvas-documents/"
            f"{self.generation_id}/manifest"
        )


@dataclass(frozen=True, slots=True)
class StoredSvgCanvasDraft:
    """Fetchable non-authoritative snapshot; it carries no scene or manifest."""

    session_id: str
    generation_id: str
    source_turn_id: str
    draft_index: int
    draft: SvgCanvasVisibleDraft

    @property
    def svg_path(self) -> str:
        return (
            f"/v4/realtime/sessions/{self.session_id}/canvas-drafts/"
            f"{self.generation_id}/{self.draft_index}/svg"
        )


@dataclass(frozen=True, slots=True)
class StoredRetainedSvgCanvasScene:
    """Validated retained content supplied by the durable Flutter workspace."""

    subject: str
    scene: ActiveSvgCanvasScene


def _retained_scene_matches_manifest(
    scene: ActiveSvgCanvasScene,
    manifest: CanvasWorkspaceManifestScene,
) -> bool:
    document = scene.document
    return (
        scene.scene_id == manifest.scene_id
        and scene.revision == manifest.scene_revision
        and scene.generation_id == manifest.generation_id
        and scene.source_turn_id == manifest.source_turn_id
        and document.svg_sha256 == manifest.svg_sha256
        and document.manifest_sha256 == manifest.manifest_sha256
        and len(document.svg_bytes) == manifest.svg_byte_length
        and len(document.manifest_bytes) == manifest.manifest_byte_length
        and document.manifest["rendererCapabilityVersion"]
        == manifest.renderer_capability_version
        and document.manifest["validatorVersion"] == manifest.validator_version
        and document.manifest["manifestVersion"] == manifest.manifest_version
        and document.title == manifest.title
        and document.description == manifest.summary
    )


class SvgCanvasDocumentStore:
    """Bounded exact-byte transport store and retained-scene source.

    Flutter remains the durable workspace owner.  At session startup it submits
    the exact persisted retained documents here after the manifest is adopted;
    this store is the backend's validated, session-independent read-through
    source for on-demand workspace hydration.
    """

    def __init__(
        self,
        *,
        max_documents: int = 256,
        max_documents_per_session: int = 3,
        max_retained_scenes_per_subject: int = 10,
        max_drafts: int = 1_024,
        max_drafts_per_generation: int = 8,
        max_document_bytes: int = SVG_DEFAULT_MAX_BYTES,
    ) -> None:
        if not 1 <= max_documents <= 10_000:
            raise ValueError("max_documents must be between 1 and 10000")
        if not 1 <= max_documents_per_session <= 8:
            raise ValueError("max_documents_per_session must be between 1 and 8")
        if not 1 <= max_retained_scenes_per_subject <= 10:
            raise ValueError(
                "max_retained_scenes_per_subject must be between 1 and 10"
            )
        if not 1 <= max_drafts <= 10_000:
            raise ValueError("max_drafts must be between 1 and 10000")
        if not 1 <= max_drafts_per_generation <= 32:
            raise ValueError("max_drafts_per_generation must be between 1 and 32")
        if not 1 <= max_document_bytes <= SVG_PROTOCOL_MAX_BYTES:
            raise ValueError("max_document_bytes exceeds the protocol ceiling")
        self._max_documents = max_documents
        self._max_documents_per_session = max_documents_per_session
        self._max_retained_scenes_per_subject = max_retained_scenes_per_subject
        self._max_drafts = max_drafts
        self._max_drafts_per_generation = max_drafts_per_generation
        self._max_document_bytes = max_document_bytes
        self._documents: dict[tuple[str, str], StoredSvgCanvasDocument] = {}
        self._drafts: dict[tuple[str, str, int], StoredSvgCanvasDraft] = {}
        self._retained_scenes: dict[tuple[str, str], StoredRetainedSvgCanvasScene] = {}
        self._lock = asyncio.Lock()

    @property
    def document_count(self) -> int:
        return len(self._documents)

    @property
    def draft_count(self) -> int:
        return len(self._drafts)

    @property
    def retained_scene_count(self) -> int:
        return len(self._retained_scenes)

    async def retain_scene(
        self,
        *,
        subject: str,
        scene: ActiveSvgCanvasScene,
    ) -> StoredRetainedSvgCanvasScene:
        """Record one exact validated Flutter-retained scene by subject/identity."""
        if not subject.strip():
            raise ValueError("retained canvas subject cannot be empty")
        _require_identifier(scene.scene_id, label="scene_id")
        _require_identifier(scene.generation_id, label="generation_id")
        if len(scene.document.svg_bytes) > self._max_document_bytes:
            raise SvgDocumentStoreCapacityError(
                "retained SVG document exceeds the configured byte limit"
            )
        key = (subject, scene.scene_id)
        stored = StoredRetainedSvgCanvasScene(subject=subject, scene=scene)
        async with self._lock:
            existing = self._retained_scenes.get(key)
            if existing is not None:
                if existing == stored:
                    return existing
                if existing.scene.revision == scene.revision:
                    raise SvgDocumentStoreError(
                        "retained SVG scene revision was reused for different content"
                    )
                if existing.scene.revision > scene.revision:
                    return existing
            subject_count = sum(
                1 for stored_subject, _ in self._retained_scenes if stored_subject == subject
            )
            if (
                existing is None
                and subject_count >= self._max_retained_scenes_per_subject
            ):
                raise SvgDocumentStoreCapacityError(
                    "retained SVG scene capacity is exhausted for this subject"
                )
            if existing is None and len(self._retained_scenes) >= self._max_documents:
                raise SvgDocumentStoreCapacityError(
                    "global retained SVG scene capacity is exhausted"
                )
            self._retained_scenes[key] = stored
        return stored

    async def load_retained_scene(
        self,
        *,
        subject: str,
        manifest_scene: CanvasWorkspaceManifestScene,
    ) -> ActiveSvgCanvasScene | None:
        """Return a retained document only when it exactly matches the manifest."""
        async with self._lock:
            stored = self._retained_scenes.get((subject, manifest_scene.scene_id))
        if stored is None or not _retained_scene_matches_manifest(
            stored.scene, manifest_scene
        ):
            return None
        return stored.scene

    async def clear_subject(self, *, subject: str) -> int:
        async with self._lock:
            keys = [key for key in self._retained_scenes if key[0] == subject]
            for key in keys:
                del self._retained_scenes[key]
        return len(keys)

    async def publish(
        self,
        *,
        session_id: str,
        candidate: SvgCanvasCandidate,
    ) -> StoredSvgCanvasDocument:
        _require_identifier(session_id, label="session_id")
        _require_identifier(candidate.generation_id, label="generation_id")
        if len(candidate.document.svg_bytes) > self._max_document_bytes:
            raise SvgDocumentStoreCapacityError(
                "SVG document exceeds the configured store byte limit"
            )
        key = (session_id, candidate.generation_id)
        stored = StoredSvgCanvasDocument(
            session_id=session_id,
            generation_id=candidate.generation_id,
            source_turn_id=candidate.source_turn_id,
            scene_id=candidate.scene_id,
            scene_revision=candidate.revision,
            document=candidate.document,
        )
        async with self._lock:
            existing = self._documents.get(key)
            if existing is not None:
                if existing == stored:
                    return existing
                raise SvgDocumentStoreError(
                    "generation id was reused for different SVG content"
                )
            session_count = sum(
                1
                for stored_session, _ in self._documents
                if stored_session == session_id
            )
            if len(self._documents) >= self._max_documents:
                raise SvgDocumentStoreCapacityError(
                    "global SVG document capacity is exhausted"
                )
            if session_count >= self._max_documents_per_session:
                raise SvgDocumentStoreCapacityError(
                    "session SVG document capacity is exhausted"
                )
            self._documents[key] = stored
        return stored

    async def publish_draft(
        self,
        *,
        session_id: str,
        source_turn_id: str,
        draft: SvgCanvasVisibleDraft,
    ) -> StoredSvgCanvasDraft:
        _require_identifier(session_id, label="session_id")
        _require_identifier(draft.generation_id, label="generation_id")
        if len(draft.document.svg_bytes) > self._max_document_bytes:
            raise SvgDocumentStoreCapacityError(
                "SVG draft exceeds the configured store byte limit"
            )
        key = (session_id, draft.generation_id, draft.draft_index)
        stored = StoredSvgCanvasDraft(
            session_id=session_id,
            generation_id=draft.generation_id,
            source_turn_id=source_turn_id,
            draft_index=draft.draft_index,
            draft=draft,
        )
        async with self._lock:
            existing = self._drafts.get(key)
            if existing is not None:
                if existing == stored:
                    return existing
                raise SvgDocumentStoreError(
                    "draft identity was reused for different SVG content"
                )
            generation_count = sum(
                1
                for stored_session, stored_generation, _ in self._drafts
                if stored_session == session_id
                and stored_generation == draft.generation_id
            )
            if len(self._drafts) >= self._max_drafts:
                raise SvgDocumentStoreCapacityError(
                    "global SVG draft capacity is exhausted"
                )
            if generation_count >= self._max_drafts_per_generation:
                raise SvgDocumentStoreCapacityError(
                    "generation SVG draft capacity is exhausted"
                )
            self._drafts[key] = stored
        return stored

    async def get_draft(
        self,
        *,
        session_id: str,
        generation_id: str,
        draft_index: int,
    ) -> StoredSvgCanvasDraft:
        _require_identifier(session_id, label="session_id")
        _require_identifier(generation_id, label="generation_id")
        if draft_index < 1:
            raise ValueError("draft_index must be positive")
        async with self._lock:
            stored = self._drafts.get((session_id, generation_id, draft_index))
        if stored is None:
            raise SvgDocumentNotFoundError(
                "SVG canvas draft does not exist for this session and generation"
            )
        return stored

    async def get(
        self,
        *,
        session_id: str,
        generation_id: str,
    ) -> StoredSvgCanvasDocument:
        _require_identifier(session_id, label="session_id")
        _require_identifier(generation_id, label="generation_id")
        async with self._lock:
            stored = self._documents.get((session_id, generation_id))
        if stored is None:
            raise SvgDocumentNotFoundError(
                "SVG canvas document does not exist for this session and generation"
            )
        return stored

    async def discard(self, *, session_id: str, generation_id: str) -> bool:
        async with self._lock:
            return self._documents.pop((session_id, generation_id), None) is not None

    async def discard_drafts(self, *, session_id: str, generation_id: str) -> int:
        async with self._lock:
            keys = [
                key
                for key in self._drafts
                if key[0] == session_id and key[1] == generation_id
            ]
            for key in keys:
                del self._drafts[key]
        return len(keys)

    async def release_session(self, *, session_id: str) -> int:
        async with self._lock:
            keys = [key for key in self._documents if key[0] == session_id]
            for document_key in keys:
                del self._documents[document_key]
            draft_keys = [key for key in self._drafts if key[0] == session_id]
            for draft_key in draft_keys:
                del self._drafts[draft_key]
        return len(keys) + len(draft_keys)


def _require_identifier(value: str, *, label: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} is not a protocol identifier")
