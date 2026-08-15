from __future__ import annotations

import re

from pydantic import ValidationError

from .plan import (
    AvaDelivery,
    AvaExpressionIntent,
    AvaGazeIntent,
    AvaMotionIntent,
    AvaTurnSegment,
    MAX_REALTIME_SPEECH_SEGMENTS,
)


_SENTENCE_BOUNDARY = re.compile(r"[.!?](?:[\"'’])?(?=\s)")


class StreamingTurnParseError(ValueError):
    """Raised when streamed assistant speech violates Ava's bounded contract."""


class StreamingSpeechParser:
    """Convert assistant text deltas into complete, speakable TTS segments.

    The parser never relies on JSON property order. It releases complete
    sentences immediately and safely splits unusually long sentences at a
    whitespace boundary.
    """

    def __init__(
        self,
        *,
        max_total_characters: int = 1_800,
        max_segment_characters: int = 600,
        max_segments: int = MAX_REALTIME_SPEECH_SEGMENTS,
    ) -> None:
        if not 1 <= max_segment_characters <= max_total_characters:
            raise ValueError("invalid streaming speech character limits")
        if not 1 <= max_segments <= MAX_REALTIME_SPEECH_SEGMENTS:
            raise ValueError(
                f"max_segments must be between 1 and {MAX_REALTIME_SPEECH_SEGMENTS}"
            )
        self._max_total_characters = max_total_characters
        self._max_segment_characters = max_segment_characters
        self._max_segments = max_segments
        self._buffer = ""
        self._received_characters = 0
        self._segments: list[AvaTurnSegment] = []
        self._finished = False

    @property
    def emitted_segments(self) -> tuple[AvaTurnSegment, ...]:
        return tuple(self._segments)

    def feed(self, delta: str) -> tuple[AvaTurnSegment, ...]:
        if self._finished:
            raise StreamingTurnParseError("cannot feed a finished speech parser")
        if not isinstance(delta, str):
            raise TypeError("assistant text delta must be a string")
        if not delta:
            return ()
        if any(
            ord(character) < 32 and character not in "\n\r\t" for character in delta
        ):
            raise StreamingTurnParseError(
                "assistant speech contains control characters"
            )
        self._received_characters += len(delta)
        if self._received_characters > self._max_total_characters:
            raise StreamingTurnParseError("assistant speech exceeded 1800 characters")
        self._buffer += delta
        return self._release(ending=False)

    def finish(self) -> tuple[AvaTurnSegment, ...]:
        if self._finished:
            raise StreamingTurnParseError("speech parser was already finished")
        self._finished = True
        emitted = list(self._release(ending=True))
        if not self._segments:
            raise StreamingTurnParseError(
                "Realtime response completed without child-facing speech"
            )
        return tuple(emitted)

    def finish_response_chunk(self) -> tuple[AvaTurnSegment, ...]:
        """Release text before a tool boundary without ending the whole turn."""

        if self._finished:
            raise StreamingTurnParseError("cannot finish a chunk on a finished parser")
        return self._release(ending=True)

    def _release(self, *, ending: bool) -> tuple[AvaTurnSegment, ...]:
        emitted: list[AvaTurnSegment] = []
        while True:
            self._buffer = self._buffer.lstrip()
            if not self._buffer:
                break

            split_at = self._sentence_split()
            if split_at is None and len(self._buffer) > self._max_segment_characters:
                split_at = self._long_text_split()
            if split_at is None and ending:
                split_at = len(self._buffer)
            if split_at is None:
                break

            speech = self._buffer[:split_at].strip()
            self._buffer = self._buffer[split_at:]
            if not speech:
                continue
            if len(speech) > self._max_segment_characters:
                raise StreamingTurnParseError(
                    "assistant speech segment exceeded 600 characters"
                )
            if len(self._segments) >= self._max_segments:
                raise StreamingTurnParseError(
                    f"assistant speech exceeded the {self._max_segments}-segment limit"
                )
            try:
                segment = AvaTurnSegment.model_validate(
                    {
                        "id": f"speech_{len(self._segments) + 1}",
                        "speech": speech,
                        "delivery": AvaDelivery.NEUTRAL,
                        "expression": AvaExpressionIntent.NEUTRAL,
                        "gaze": AvaGazeIntent.CHILD,
                        "motionIntent": AvaMotionIntent.NONE,
                        "canvasCues": [],
                    }
                )
            except ValidationError as error:
                raise StreamingTurnParseError(
                    f"assistant speech failed validation: {error}"
                ) from error
            self._segments.append(segment)
            emitted.append(segment)
        return tuple(emitted)

    def _sentence_split(self) -> int | None:
        for match in _SENTENCE_BOUNDARY.finditer(self._buffer):
            end = match.end()
            if end <= self._max_segment_characters:
                return end
            break
        return None

    def _long_text_split(self) -> int:
        boundary = self._buffer.rfind(" ", 0, self._max_segment_characters + 1)
        if boundary <= 0:
            raise StreamingTurnParseError(
                "assistant speech exceeds 600 characters without a safe boundary"
            )
        return boundary
