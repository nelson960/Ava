from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from .svg_document import ValidatedSvgDocument
from .svg_session import SvgCanvasCandidate

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


class SvgCanvasDocumentStore:
    """Bounded exact-byte store consumed over HTTP, never the PCM socket."""

    def __init__(
        self,
        *,
        max_documents: int = 256,
        max_documents_per_session: int = 3,
    ) -> None:
        if not 1 <= max_documents <= 10_000:
            raise ValueError("max_documents must be between 1 and 10000")
        if not 1 <= max_documents_per_session <= 8:
            raise ValueError("max_documents_per_session must be between 1 and 8")
        self._max_documents = max_documents
        self._max_documents_per_session = max_documents_per_session
        self._documents: dict[tuple[str, str], StoredSvgCanvasDocument] = {}
        self._lock = asyncio.Lock()

    @property
    def document_count(self) -> int:
        return len(self._documents)

    async def publish(
        self,
        *,
        session_id: str,
        candidate: SvgCanvasCandidate,
    ) -> StoredSvgCanvasDocument:
        _require_identifier(session_id, label="session_id")
        _require_identifier(candidate.generation_id, label="generation_id")
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

    async def release_session(self, *, session_id: str) -> int:
        async with self._lock:
            keys = [key for key in self._documents if key[0] == session_id]
            for key in keys:
                del self._documents[key]
        return len(keys)


def _require_identifier(value: str, *, label: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} is not a protocol identifier")
