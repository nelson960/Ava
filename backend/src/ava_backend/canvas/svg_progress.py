from __future__ import annotations

import xml.etree.ElementTree as ET

from .svg_document import SvgDocumentValidationError, SvgDocumentValidator
from .svg_specialist import SvgCanvasVisibleDraft

_VISIBLE_GEOMETRY = frozenset(
    {"circle", "ellipse", "line", "path", "polygon", "polyline", "rect", "text", "use"}
)


class ProgressiveSvgDraftBuilder:
    """Projects complete, validated root-child prefixes from streamed Sol SVG."""

    def __init__(
        self,
        *,
        generation_id: str,
        attempt: int,
        validator: SvgDocumentValidator,
        first_draft_index: int,
        maximum_drafts: int = 8,
        minimum_growth_characters: int = 128,
    ) -> None:
        if first_draft_index < 1:
            raise ValueError("first_draft_index must be positive")
        if not 1 <= maximum_drafts <= 32:
            raise ValueError("maximum_drafts must be between 1 and 32")
        self._generation_id = generation_id
        self._attempt = attempt
        self._validator = validator
        self._next_draft_index = first_draft_index
        self._maximum_drafts = maximum_drafts
        self._minimum_growth_characters = minimum_growth_characters
        self._emitted = 0
        self._last_emitted_characters = 0
        self._text = ""

    @property
    def next_draft_index(self) -> int:
        return self._next_draft_index

    def feed(self, delta: str) -> SvgCanvasVisibleDraft | None:
        if not delta or self._emitted >= self._maximum_drafts:
            self._text += delta
            return None
        self._text += delta
        complete_document = self._text.rstrip().endswith("</svg>")
        if (
            len(self._text) - self._last_emitted_characters
            < self._minimum_growth_characters
            and not complete_document
        ):
            return None
        stripped = self._text.lstrip()
        if not stripped.startswith("<svg"):
            return None

        leading_characters = len(self._text) - len(stripped)
        boundaries = [
            index + 1
            for index, character in enumerate(self._text)
            if character == ">" and index + 1 > self._last_emitted_characters
        ]
        for boundary in reversed(boundaries):
            prefix = self._text[:boundary]
            candidate = (
                prefix if prefix.rstrip().endswith("</svg>") else f"{prefix}</svg>"
            )
            try:
                document = self._validator.validate(candidate)
            except SvgDocumentValidationError:
                continue
            if not _contains_visible_geometry(document.svg_text):
                continue
            if boundary <= leading_characters:
                continue
            draft = SvgCanvasVisibleDraft(
                generation_id=self._generation_id,
                draft_index=self._next_draft_index,
                attempt=self._attempt,
                document=document,
            )
            self._next_draft_index += 1
            self._emitted += 1
            self._last_emitted_characters = boundary
            return draft
        return None


def _contains_visible_geometry(svg_text: str) -> bool:
    root = ET.fromstring(svg_text)
    return any(
        element.tag.rsplit("}", 1)[-1] in _VISIBLE_GEOMETRY for element in root.iter()
    )
