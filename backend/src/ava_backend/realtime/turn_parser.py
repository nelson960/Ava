from __future__ import annotations

import re

from pydantic import ValidationError

from .speech_contract import (
    MAX_SPEECH_PHRASE_CHARACTERS,
    MAX_SPEECH_RESPONSE_CHARACTERS,
    SpeechDelivery,
    SpeechExpressionIntent,
    SpeechGazeIntent,
    SpeechMotionIntent,
    SpeechPhrase,
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
        max_total_characters: int = MAX_SPEECH_RESPONSE_CHARACTERS,
        max_segment_characters: int = MAX_SPEECH_PHRASE_CHARACTERS,
    ) -> None:
        if not 1 <= max_segment_characters <= max_total_characters:
            raise ValueError("invalid streaming speech character limits")
        self._max_total_characters = max_total_characters
        self._max_segment_characters = max_segment_characters
        self._buffer = ""
        self._received_characters = 0
        self._segments: list[SpeechPhrase] = []
        self._finished = False

    @property
    def emitted_segments(self) -> tuple[SpeechPhrase, ...]:
        return tuple(self._segments)

    def feed(self, delta: str) -> tuple[SpeechPhrase, ...]:
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

    def finish(self, *, allow_empty: bool = False) -> tuple[SpeechPhrase, ...]:
        if self._finished:
            raise StreamingTurnParseError("speech parser was already finished")
        self._finished = True
        emitted = list(self._release(ending=True))
        if not self._segments and not allow_empty:
            raise StreamingTurnParseError(
                "Realtime response completed without child-facing speech"
            )
        return tuple(emitted)

    def finish_response_chunk(self) -> tuple[SpeechPhrase, ...]:
        """Release text before a tool boundary without ending the whole turn."""

        if self._finished:
            raise StreamingTurnParseError("cannot finish a chunk on a finished parser")
        return self._release(ending=True)

    def _release(self, *, ending: bool) -> tuple[SpeechPhrase, ...]:
        emitted: list[SpeechPhrase] = []
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
            try:
                segment = SpeechPhrase.model_validate(
                    {
                        "id": f"speech_{len(self._segments) + 1}",
                        "speech": speech,
                        "delivery": SpeechDelivery.NEUTRAL,
                        "expression": SpeechExpressionIntent.NEUTRAL,
                        "gaze": SpeechGazeIntent.CHILD,
                        "motionIntent": SpeechMotionIntent.NONE,
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
