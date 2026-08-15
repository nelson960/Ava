from __future__ import annotations

import base64
import binascii
from collections.abc import AsyncIterator
from math import isfinite
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import Settings
from ..errors import (
    SpeechProviderResponseError,
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
)
from .base import (
    SpeechCharacterTiming,
    TimedSpeechChunk,
    TimedSpeechMetadata,
)


_ELEVENLABS_API_ORIGIN = "https://api.elevenlabs.io"
_MAX_TEXT_CHARACTERS = 1800
_MAX_AUDIO_CHUNK_BYTES = 2 * 1024 * 1024
_OUTPUT_SAMPLE_RATE_HZ = 24_000
_OUTPUT_CHANNELS: Literal[1] = 1


class _Alignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    characters: list[str] = Field(max_length=10_000)
    character_start_times_seconds: list[float] = Field(max_length=10_000)
    character_end_times_seconds: list[float] = Field(max_length=10_000)


class _StreamPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio_base64: str
    alignment: _Alignment | None
    normalized_alignment: _Alignment | None


class ElevenLabsAvaSpeech:
    """Streams candidate-B audio and provider character timing.

    Construction is deliberately strict. The selected candidate record does
    not yet contain a voice ID or model ID, so this adapter cannot be activated
    until those exact values and an API key are configured.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        api_origin: str = _ELEVENLABS_API_ORIGIN,
    ) -> None:
        api_key, voice_id, model_id = settings.require_elevenlabs_credentials()
        normalized_origin = api_origin.rstrip("/")
        if normalized_origin != _ELEVENLABS_API_ORIGIN and client is None:
            raise ValueError(
                "A non-production ElevenLabs API origin requires an injected "
                "HTTP client"
            )
        self._settings = settings
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._api_origin = normalized_origin
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.elevenlabs_timeout_seconds)
        )

    @property
    def metadata(self) -> TimedSpeechMetadata:
        return TimedSpeechMetadata(
            audio_format="pcm_s16le",
            media_type="audio/x-raw",
            sample_rate_hz=_OUTPUT_SAMPLE_RATE_HZ,
            channels=_OUTPUT_CHANNELS,
            bits_per_sample=16,
            model=self._model_id,
            voice_id=self._voice_id,
            voice_label=self._settings.elevenlabs_voice_label,
        )

    async def stream(
        self,
        *,
        text: str,
        previous_text: str | None = None,
        next_text: str | None = None,
    ) -> AsyncIterator[TimedSpeechChunk]:
        normalized_text = _bounded_text(text, "text", required=True)
        normalized_previous = _bounded_text(
            previous_text,
            "previous_text",
            required=False,
        )
        normalized_next = _bounded_text(
            next_text,
            "next_text",
            required=False,
        )
        request_body: dict[str, Any] = {
            "text": normalized_text,
            "model_id": self._model_id,
            "voice_settings": {
                "stability": self._settings.elevenlabs_stability,
                "similarity_boost": self._settings.elevenlabs_similarity_boost,
                "style": self._settings.elevenlabs_style,
                "use_speaker_boost": (self._settings.elevenlabs_use_speaker_boost),
                "speed": self._settings.elevenlabs_speed,
            },
            "apply_text_normalization": "auto",
        }
        if normalized_previous is not None:
            request_body["previous_text"] = normalized_previous
        if normalized_next is not None:
            request_body["next_text"] = normalized_next

        endpoint = (
            f"{self._api_origin}/v1/text-to-speech/"
            f"{quote(self._voice_id, safe='')}/stream/with-timestamps"
        )
        last_start = 0.0
        try:
            async with self._client.stream(
                "POST",
                endpoint,
                params={
                    "output_format": self._settings.elevenlabs_output_format,
                },
                headers={
                    "accept": "application/json",
                    "content-type": "application/json",
                    "xi-api-key": self._api_key,
                },
                json=request_body,
            ) as response:
                if response.status_code in {408, 504}:
                    raise SpeechProviderTimeoutError()
                if response.status_code == 429 or response.status_code >= 500:
                    raise SpeechProviderUnavailableError()
                if response.status_code < 200 or response.status_code >= 300:
                    raise SpeechProviderResponseError()

                request_id = response.headers.get("request-id") or response.headers.get(
                    "x-request-id"
                )
                yielded = False
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    yielded = True
                    chunk, last_start = _decode_stream_line(
                        line,
                        request_id=request_id,
                        minimum_start=last_start,
                    )
                    yield chunk
                if not yielded:
                    raise SpeechProviderResponseError()
        except SpeechProviderTimeoutError:
            raise
        except SpeechProviderUnavailableError:
            raise
        except SpeechProviderResponseError:
            raise
        except httpx.TimeoutException as error:
            raise SpeechProviderTimeoutError() from error
        except httpx.RequestError as error:
            raise SpeechProviderUnavailableError() from error
        except (ValidationError, ValueError, TypeError, binascii.Error) as error:
            raise SpeechProviderResponseError() from error


def _bounded_text(
    value: str | None,
    field: str,
    *,
    required: bool,
) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{field} cannot be empty")
        return None
    normalized = value.strip()
    if not normalized:
        if required:
            raise ValueError(f"{field} cannot be empty")
        return None
    if len(normalized) > _MAX_TEXT_CHARACTERS:
        raise ValueError(f"{field} cannot exceed {_MAX_TEXT_CHARACTERS} characters")
    return normalized


def _decode_stream_line(
    line: str,
    *,
    request_id: str | None,
    minimum_start: float,
) -> tuple[TimedSpeechChunk, float]:
    payload = _StreamPayload.model_validate_json(line)
    try:
        audio = base64.b64decode(payload.audio_base64, validate=True)
    except (ValueError, binascii.Error) as error:
        raise SpeechProviderResponseError() from error
    if not audio or len(audio) > _MAX_AUDIO_CHUNK_BYTES or len(audio) % 2 != 0:
        raise SpeechProviderResponseError()

    source_alignment = payload.normalized_alignment or payload.alignment
    alignment: tuple[SpeechCharacterTiming, ...] = ()
    next_minimum = minimum_start
    if source_alignment is not None:
        alignment, next_minimum = _validated_alignment(
            source_alignment,
            minimum_start=minimum_start,
        )
    return (
        TimedSpeechChunk(
            audio=audio,
            alignment=alignment,
            provider_request_id=request_id,
        ),
        next_minimum,
    )


def _validated_alignment(
    alignment: _Alignment,
    *,
    minimum_start: float,
) -> tuple[tuple[SpeechCharacterTiming, ...], float]:
    characters = alignment.characters
    starts = alignment.character_start_times_seconds
    ends = alignment.character_end_times_seconds
    if not characters or len(characters) != len(starts) or len(starts) != len(ends):
        raise SpeechProviderResponseError()

    result: list[SpeechCharacterTiming] = []
    previous_start = minimum_start
    for character, start, end in zip(characters, starts, ends, strict=True):
        if len(character) != 1:
            raise SpeechProviderResponseError()
        if (
            not isfinite(start)
            or not isfinite(end)
            or start < previous_start
            or end < start
        ):
            raise SpeechProviderResponseError()
        result.append(
            SpeechCharacterTiming(
                character=character,
                start_seconds=start,
                end_seconds=end,
            )
        )
        previous_start = start
    return tuple(result), previous_start
