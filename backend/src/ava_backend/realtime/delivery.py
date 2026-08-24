from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from .speech_contract import (
    MAX_SPEECH_ALIGNMENT_SPANS,
    SpeechAlignment,
    SpeechPhrase,
    SpeechPhraseAnchor,
)


class SpeechDeliveryError(RuntimeError):
    """Raised when PCM or alignment cannot satisfy the delivery contract."""


@dataclass(frozen=True, slots=True)
class SynthesizedAudioChunk:
    """One provider PCM16 mono chunk without transport metadata."""

    pcm: bytes

    def __post_init__(self) -> None:
        if not self.pcm:
            raise ValueError("synthesized PCM chunk cannot be empty")
        if len(self.pcm) % 2:
            raise ValueError("synthesized PCM16 chunk must have an even byte length")
        if len(self.pcm) > 192_000:
            raise ValueError("synthesized PCM chunk cannot exceed 192000 bytes")


@dataclass(frozen=True, slots=True)
class SynthesizedAlignmentBatch:
    """Segment-relative text/sample alignment supplied by streaming TTS."""

    spans: tuple[SpeechAlignment, ...]

    def __post_init__(self) -> None:
        if not self.spans:
            raise ValueError("synthesized alignment batch cannot be empty")
        if len(self.spans) > MAX_SPEECH_ALIGNMENT_SPANS:
            raise ValueError("synthesized alignment batch cannot exceed 512 spans")
        for previous, current in zip(self.spans, self.spans[1:], strict=False):
            if current.sample_start < previous.sample_start:
                raise ValueError("synthesized alignment spans must be sample-monotonic")


SynthesizedSpeechItem: TypeAlias = SynthesizedAudioChunk | SynthesizedAlignmentBatch


class StreamingSpeechSynthesizer(Protocol):
    """Provider-neutral boundary for one input-streaming speech connection."""

    @property
    def model_id(self) -> str: ...

    @property
    def voice_id(self) -> str: ...

    def synthesize(
        self,
        segment: SpeechPhrase,
        *,
        turn_id: str,
    ) -> AsyncIterator[SynthesizedSpeechItem]: ...

    async def cancel(self, *, turn_id: str) -> None: ...

    async def close(self) -> None: ...


class DeliveredTextLedger:
    """Maps the rendered PCM sample clock to the text the child actually heard."""

    def __init__(self) -> None:
        self._segments: list[SpeechPhrase] = []
        self._segment_by_id: dict[str, SpeechPhrase] = {}
        self._spans: defaultdict[str, list[SpeechAlignment]] = defaultdict(list)

    @property
    def segment_count(self) -> int:
        return len(self._segments)

    def register_segment(self, segment: SpeechPhrase) -> None:
        if segment.id in self._segment_by_id:
            raise SpeechDeliveryError(
                f"delivery segment {segment.id!r} was registered twice"
            )
        self._segments.append(segment)
        self._segment_by_id[segment.id] = segment

    def add_alignment_spans(
        self,
        spans: tuple[SpeechAlignment, ...],
    ) -> None:
        if not spans:
            raise SpeechDeliveryError("alignment batch cannot be empty")
        for span in spans:
            segment = self._segment_by_id.get(span.segment_id)
            if segment is None:
                raise SpeechDeliveryError(
                    f"alignment references unknown segment {span.segment_id!r}"
                )
            if span.text_end > len(segment.speech):
                raise SpeechDeliveryError(
                    f"alignment for {span.segment_id!r} exceeds segment text"
                )
            existing = self._spans[span.segment_id]
            if existing:
                previous = existing[-1]
                if span.sample_start < previous.sample_end:
                    raise SpeechDeliveryError(
                        f"alignment samples overlap for segment {span.segment_id!r}"
                    )
                if span.text_start < previous.text_end:
                    raise SpeechDeliveryError(
                        f"alignment text overlaps for segment {span.segment_id!r}"
                    )
            existing.append(span)

    def validate_complete(self, *, received_sample_count: int) -> None:
        if received_sample_count <= 0:
            raise SpeechDeliveryError("completed speech requires received PCM samples")
        previous_sample_end = 0
        for segment in self._segments:
            spans = self._spans.get(segment.id, [])
            if not spans:
                raise SpeechDeliveryError(
                    f"segment {segment.id!r} has no text/audio alignment"
                )
            if spans[0].sample_start < previous_sample_end:
                raise SpeechDeliveryError(
                    f"segment {segment.id!r} alignment overlaps prior speech"
                )
            if spans[-1].text_end != len(segment.speech):
                raise SpeechDeliveryError(
                    f"segment {segment.id!r} alignment does not cover all speech"
                )
            if spans[-1].sample_end > received_sample_count:
                raise SpeechDeliveryError(
                    f"segment {segment.id!r} alignment exceeds received PCM"
                )
            previous_sample_end = spans[-1].sample_end

    def delivered_text(self, rendered_sample_count: int) -> str:
        if rendered_sample_count < 0:
            raise ValueError("rendered_sample_count cannot be negative")
        delivered_segments: list[str] = []
        for segment in self._segments:
            delivered_end = 0
            for span in self._spans.get(segment.id, ()):
                if span.sample_end > rendered_sample_count:
                    break
                delivered_end = span.text_end
            if delivered_end == 0:
                break
            delivered_segments.append(segment.speech[:delivered_end].rstrip())
            if delivered_end < len(segment.speech):
                break
        return " ".join(delivered_segments)

    def sample_offset_for_anchor(
        self,
        *,
        segment_id: str,
        anchor: SpeechPhraseAnchor,
    ) -> int:
        segment = self._segment_by_id.get(segment_id)
        if segment is None:
            raise SpeechDeliveryError(
                f"canvas anchor references unknown segment {segment_id!r}"
            )
        text_offset = _substring_offset(
            segment.speech,
            phrase=anchor.phrase,
            occurrence=anchor.occurrence,
        )
        for span in self._spans.get(segment_id, ()):
            if span.text_start <= text_offset < span.text_end:
                return span.sample_start
            if span.text_start > text_offset:
                return span.sample_start
        raise SpeechDeliveryError(
            f"canvas anchor {anchor.phrase!r} has no resolved speech alignment"
        )

    def sample_offset_for_segment_start(self, segment_id: str) -> int:
        if segment_id not in self._segment_by_id:
            raise SpeechDeliveryError(
                f"canvas cue references unknown segment {segment_id!r}"
            )
        spans = self._spans.get(segment_id, ())
        if not spans:
            raise SpeechDeliveryError(
                f"canvas cue segment {segment_id!r} has no speech alignment"
            )
        return spans[0].sample_start


def _substring_offset(text: str, *, phrase: str, occurrence: int) -> int:
    cursor = 0
    found = -1
    for _ in range(occurrence + 1):
        found = text.find(phrase, cursor)
        if found < 0:
            raise SpeechDeliveryError(
                f"anchor phrase {phrase!r} occurrence {occurrence} is absent"
            )
        cursor = found + max(1, len(phrase))
    return found
