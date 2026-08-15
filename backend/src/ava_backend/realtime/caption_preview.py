from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

from .delivery import (
    SynthesizedAlignmentBatch,
    SynthesizedAudioChunk,
    SynthesizedSpeechItem,
)
from .events import AvaAlignmentSpan
from .plan import AvaTurnSegment


class DevelopmentCaptionPreviewSynthesizer:
    """Development-only silent sample clock for caption and canvas testing.

    This is an explicitly selected transport exerciser, not a TTS fallback.
    It emits silence so the production PCM, delivery-prefix, caption, viseme,
    canvas-cue, interruption, and memory boundaries can be tested before the
    approved ElevenLabs voice is configured.
    """

    def __init__(
        self,
        *,
        samples_per_character: int = 1_440,
        chunk_sample_count: int = 12_000,
    ) -> None:
        if not 480 <= samples_per_character <= 3_000:
            raise ValueError("samples_per_character must be between 480 and 3000")
        if not 1_200 <= chunk_sample_count <= 96_000:
            raise ValueError("chunk_sample_count must be between 1200 and 96000")
        self._samples_per_character = samples_per_character
        self._chunk_sample_count = chunk_sample_count
        self._cancelled_turn_ids: set[str] = set()
        self._closed = False

    @property
    def model_id(self) -> str:
        return "development-caption-preview-v1"

    @property
    def voice_id(self) -> str:
        return "development-no-voice"

    async def synthesize(
        self,
        segment: AvaTurnSegment,
    ) -> AsyncIterator[SynthesizedSpeechItem]:
        if self._closed:
            raise RuntimeError("caption preview synthesizer is closed")
        matches = tuple(re.finditer(r"\S+", segment.speech))
        if not matches:
            raise ValueError("caption preview requires non-whitespace speech")
        spans = tuple(
            AvaAlignmentSpan.model_validate(
                {
                    "segmentId": segment.id,
                    "textStart": match.start(),
                    "textEnd": match.end(),
                    "sampleStart": match.start() * self._samples_per_character,
                    "sampleEnd": match.end() * self._samples_per_character,
                    "phoneme": _preview_phoneme(match.group()),
                }
            )
            for match in matches
        )
        yield SynthesizedAlignmentBatch(spans)

        remaining = len(segment.speech) * self._samples_per_character
        while remaining:
            sample_count = min(remaining, self._chunk_sample_count)
            yield SynthesizedAudioChunk(bytes(sample_count * 2))
            remaining -= sample_count
            await asyncio.sleep(0)

    async def cancel(self, *, turn_id: str) -> None:
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        self._cancelled_turn_ids.add(turn_id)

    async def close(self) -> None:
        self._closed = True


def _preview_phoneme(word: str) -> str:
    normalized = word.casefold()
    if any(character in normalized for character in "a"):
        return "AA"
    if any(character in normalized for character in "ei"):
        return "EE"
    if any(character in normalized for character in "ou"):
        return "OH"
    return "M"
