from __future__ import annotations

import asyncio
import base64
import binascii
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Protocol
from urllib.parse import quote, urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from websockets.asyncio.client import connect as connect_websocket

from ..config import Settings
from ..realtime.delivery import (
    SynthesizedAlignmentBatch,
    SynthesizedAudioChunk,
    SynthesizedSpeechItem,
)
from ..realtime.speech_contract import (
    MAX_SPEECH_ALIGNMENT_SPANS,
    MAX_SPEECH_RESPONSE_CHARACTERS,
    SpeechAlignment,
    SpeechPhrase,
)

_ELEVENLABS_WEBSOCKET_ORIGIN = "wss://api.elevenlabs.io"
_SAMPLE_RATE_HZ = 24_000
_MAX_PROVIDER_EVENT_BYTES = 4 * 1024 * 1024
_MAX_PCM_CHUNK_BYTES = 192_000


class ElevenLabsInputStreamingError(ValueError):
    """The ElevenLabs input-streaming contract failed validation."""


class ElevenLabsInputStreamingUnavailableError(RuntimeError):
    """The ElevenLabs input-streaming connection became unavailable."""


class ElevenLabsWebSocket(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


ElevenLabsWebSocketConnector = Callable[
    [str, Mapping[str, str], float, int],
    Awaitable[ElevenLabsWebSocket],
]


async def _default_websocket_connector(
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
    max_event_bytes: int,
) -> ElevenLabsWebSocket:
    return await connect_websocket(
        url,
        additional_headers=headers,
        open_timeout=timeout_seconds,
        close_timeout=min(timeout_seconds, 10),
        ping_interval=20,
        ping_timeout=20,
        max_size=max_event_bytes,
        max_queue=16,
        compression=None,
    )


class _Alignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chars: list[str] = Field(
        min_length=1,
        max_length=MAX_SPEECH_RESPONSE_CHARACTERS,
    )
    char_start_times_ms: list[int] = Field(
        min_length=1,
        max_length=MAX_SPEECH_RESPONSE_CHARACTERS,
    )
    char_durations_ms: list[int] = Field(
        min_length=1,
        max_length=MAX_SPEECH_RESPONSE_CHARACTERS,
    )


class _ServerMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio: str | None = None
    alignment: _Alignment | None = None
    normalized_alignment: _Alignment | None = None
    is_final: bool


class ElevenLabsInputStreamingSynthesizer:
    """Phrase-bounded ElevenLabs WebSocket input streaming with PCM alignment.

    Realtime releases only complete speakable phrases to this adapter. Each
    phrase gets one bounded provider stream so cancellation can close the exact
    in-flight synthesis without reviving stale audio.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        websocket_connector: ElevenLabsWebSocketConnector = (
            _default_websocket_connector
        ),
        websocket_origin: str = _ELEVENLABS_WEBSOCKET_ORIGIN,
    ) -> None:
        api_key, voice_id, model_id = settings.require_elevenlabs_credentials()
        origin = websocket_origin.rstrip("/")
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "wss"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("ElevenLabs WebSocket origin must be credential-free WSS")
        if (
            origin != _ELEVENLABS_WEBSOCKET_ORIGIN
            and websocket_connector is _default_websocket_connector
        ):
            raise ValueError(
                "A non-production ElevenLabs WebSocket origin requires an "
                "injected connector"
            )
        if settings.elevenlabs_output_format != "pcm_24000":
            raise ValueError("realtime speech requires ElevenLabs pcm_24000 output")
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._settings = settings
        self._origin = origin
        self._connector = websocket_connector
        self._active_socket: ElevenLabsWebSocket | None = None
        self._active_turn_id: str | None = None
        self._active_lock = asyncio.Lock()
        self._closed = False

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def voice_id(self) -> str:
        return self._voice_id

    async def synthesize(
        self,
        segment: SpeechPhrase,
        *,
        turn_id: str,
    ) -> AsyncIterator[SynthesizedSpeechItem]:
        if not turn_id.strip():
            raise ElevenLabsInputStreamingError("turn_id cannot be empty")
        if self._closed:
            raise ElevenLabsInputStreamingError("speech synthesizer is closed")
        speech = segment.speech
        if not speech or len(speech) > MAX_SPEECH_RESPONSE_CHARACTERS:
            raise ElevenLabsInputStreamingError(
                "speech phrase must contain 1-1800 characters"
            )
        query = urlencode(
            {
                "model_id": self._model_id,
                "output_format": self._settings.elevenlabs_output_format,
                "sync_alignment": "true",
                "auto_mode": "true",
                "apply_text_normalization": "auto",
            }
        )
        url = (
            f"{self._origin}/v1/text-to-speech/"
            f"{quote(self._voice_id, safe='')}/stream-input?{query}"
        )
        async with self._active_lock:
            if self._closed:
                raise ElevenLabsInputStreamingError("speech synthesizer is closed")
            if self._active_turn_id is not None:
                raise ElevenLabsInputStreamingError(
                    "ElevenLabs phrase synthesis cannot overlap"
                )
            self._active_turn_id = turn_id

        socket: ElevenLabsWebSocket | None = None
        audio_sample_cursor = 0
        text_cursor = 0
        yielded_audio = False
        saw_final = False
        try:
            socket = await self._connector(
                url,
                {"xi-api-key": self._api_key},
                self._settings.elevenlabs_timeout_seconds,
                _MAX_PROVIDER_EVENT_BYTES,
            )
            async with self._active_lock:
                request_is_current = (
                    not self._closed and self._active_turn_id == turn_id
                )
                if request_is_current:
                    self._active_socket = socket
            if not request_is_current:
                await socket.close(
                    code=1000,
                    reason="Ava speech request no longer owns this turn",
                )
                socket = None
                return
            await socket.send(
                json.dumps(
                    {
                        "text": " ",
                        "voice_settings": {
                            "stability": self._settings.elevenlabs_stability,
                            "similarity_boost": (
                                self._settings.elevenlabs_similarity_boost
                            ),
                            "style": self._settings.elevenlabs_style,
                            "use_speaker_boost": (
                                self._settings.elevenlabs_use_speaker_boost
                            ),
                            "speed": self._settings.elevenlabs_speed,
                        },
                    },
                    separators=(",", ":"),
                )
            )
            await socket.send(
                json.dumps(
                    {"text": speech, "flush": True},
                    separators=(",", ":"),
                )
            )
            await socket.send('{"text":""}')

            while True:
                raw = await asyncio.wait_for(
                    socket.recv(),
                    timeout=self._settings.elevenlabs_timeout_seconds,
                )
                if not isinstance(raw, str):
                    raise ElevenLabsInputStreamingError(
                        "ElevenLabs returned a non-text control frame"
                    )
                try:
                    message = _ServerMessage.model_validate_json(raw)
                except ValidationError as error:
                    raise ElevenLabsInputStreamingError(
                        "ElevenLabs returned an invalid stream message"
                    ) from error
                if message.is_final:
                    if message.audio is not None or message.alignment is not None:
                        raise ElevenLabsInputStreamingError(
                            "ElevenLabs final message carried unexpected content"
                        )
                    saw_final = True
                    break
                if message.audio is None:
                    raise ElevenLabsInputStreamingError(
                        "ElevenLabs audio message omitted audio"
                    )
                try:
                    pcm = base64.b64decode(message.audio, validate=True)
                except (ValueError, binascii.Error) as error:
                    raise ElevenLabsInputStreamingError(
                        "ElevenLabs returned invalid base64 audio"
                    ) from error
                if not pcm or len(pcm) % 2:
                    raise ElevenLabsInputStreamingError(
                        "ElevenLabs returned invalid PCM16 audio"
                    )
                alignment = message.alignment
                if alignment is not None:
                    spans, text_cursor = _alignment_spans(
                        alignment,
                        segment=segment,
                        text_cursor=text_cursor,
                        sample_base=audio_sample_cursor,
                        audio_sample_count=len(pcm) // 2,
                    )
                    for offset in range(0, len(spans), MAX_SPEECH_ALIGNMENT_SPANS):
                        yield SynthesizedAlignmentBatch(
                            spans[offset : offset + MAX_SPEECH_ALIGNMENT_SPANS]
                        )
                audio_sample_cursor += len(pcm) // 2
                for offset in range(0, len(pcm), _MAX_PCM_CHUNK_BYTES):
                    yield SynthesizedAudioChunk(
                        pcm[offset : offset + _MAX_PCM_CHUNK_BYTES]
                    )
                yielded_audio = True

            if not saw_final or not yielded_audio:
                raise ElevenLabsInputStreamingError(
                    "ElevenLabs ended without final PCM audio"
                )
            if text_cursor != len(speech):
                raise ElevenLabsInputStreamingError(
                    "ElevenLabs alignment did not cover the complete phrase"
                )
        except TimeoutError as error:
            raise ElevenLabsInputStreamingUnavailableError(
                "ElevenLabs input stream timed out"
            ) from error
        finally:
            async with self._active_lock:
                if self._active_turn_id == turn_id and self._active_socket is socket:
                    self._active_socket = None
                    self._active_turn_id = None
            if socket is not None:
                await socket.close(code=1000, reason="Ava phrase complete")

    async def cancel(self, *, turn_id: str) -> None:
        if not turn_id.strip():
            raise ElevenLabsInputStreamingError("turn_id cannot be empty")
        async with self._active_lock:
            if self._active_turn_id != turn_id:
                return
            socket = self._active_socket
            self._active_socket = None
            self._active_turn_id = None
        if socket is not None:
            await socket.close(code=1000, reason="Ava turn interrupted")

    async def close(self) -> None:
        async with self._active_lock:
            if self._closed:
                return
            self._closed = True
            socket = self._active_socket
            self._active_socket = None
            self._active_turn_id = None
        if socket is not None:
            await socket.close(code=1000, reason="Ava speech session closed")


def _alignment_spans(
    alignment: _Alignment,
    *,
    segment: SpeechPhrase,
    text_cursor: int,
    sample_base: int,
    audio_sample_count: int,
) -> tuple[tuple[SpeechAlignment, ...], int]:
    if not (
        len(alignment.chars)
        == len(alignment.char_start_times_ms)
        == len(alignment.char_durations_ms)
    ):
        raise ElevenLabsInputStreamingError(
            "ElevenLabs alignment arrays have different lengths"
        )
    text = "".join(alignment.chars)
    if segment.speech[text_cursor : text_cursor + len(text)] != text:
        raise ElevenLabsInputStreamingError(
            "ElevenLabs alignment text changed the authored phrase"
        )
    spans: list[SpeechAlignment] = []
    previous_end = sample_base
    maximum_end = sample_base + audio_sample_count
    for index, (character, start_ms, duration_ms) in enumerate(
        zip(
            alignment.chars,
            alignment.char_start_times_ms,
            alignment.char_durations_ms,
            strict=True,
        )
    ):
        if len(character) != 1 or start_ms < 0 or duration_ms <= 0:
            raise ElevenLabsInputStreamingError(
                "ElevenLabs alignment contains an invalid character timing"
            )
        sample_start = sample_base + round(start_ms * _SAMPLE_RATE_HZ / 1000)
        sample_end = sample_start + round(duration_ms * _SAMPLE_RATE_HZ / 1000)
        if (
            sample_start < previous_end
            or sample_end <= sample_start
            or sample_end > maximum_end
        ):
            raise ElevenLabsInputStreamingError(
                "ElevenLabs alignment exceeds or overlaps its PCM chunk"
            )
        spans.append(
            SpeechAlignment.model_validate(
                {
                    "segmentId": segment.id,
                    "textStart": text_cursor + index,
                    "textEnd": text_cursor + index + 1,
                    "sampleStart": sample_start,
                    "sampleEnd": sample_end,
                    "phoneme": None,
                }
            )
        )
        previous_end = sample_end
    return tuple(spans), text_cursor + len(text)
