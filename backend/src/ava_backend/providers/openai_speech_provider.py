from __future__ import annotations

import io
import wave
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from ..config import Settings
from ..errors import (
    SpeechProviderResponseError,
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
)
from .base import SpeechAudioResult


class OpenAIAvaSpeech:
    def __init__(
        self,
        settings: Settings,
        *,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or AsyncOpenAI(
            api_key=settings.require_openai_api_key(),
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )

    async def synthesize(
        self,
        *,
        text: str,
        instructions: str,
    ) -> SpeechAudioResult:
        normalized_text = text.strip()
        normalized_instructions = instructions.strip()
        if not normalized_text:
            raise ValueError("speech text cannot be empty")
        if len(normalized_text) > 4096:
            raise ValueError("speech text cannot exceed 4096 characters")
        if not normalized_instructions:
            raise ValueError("speech instructions cannot be empty")

        arguments: dict[str, Any] = {
            "model": self._settings.openai_tts_model,
            "voice": self._settings.openai_tts_voice,
            "input": normalized_text,
            "instructions": normalized_instructions,
            "response_format": "wav",
            "speed": self._settings.openai_tts_speed,
            "stream_format": "audio",
        }
        try:
            response = await self._client.audio.speech.create(**arguments)
            audio = await response.aread()
        except APITimeoutError as error:
            raise SpeechProviderTimeoutError() from error
        except (RateLimitError, APIConnectionError, APIStatusError) as error:
            raise SpeechProviderUnavailableError() from error
        except (AttributeError, TypeError, ValueError, OSError) as error:
            raise SpeechProviderResponseError() from error

        _validate_wav(audio)
        raw_response = getattr(response, "response", None)
        headers = getattr(raw_response, "headers", {})
        return SpeechAudioResult(
            audio=audio,
            audio_format="wav",
            media_type="audio/wav",
            model=self._settings.openai_tts_model,
            voice=self._settings.openai_tts_voice,
            provider_request_id=headers.get("x-request-id"),
        )


def _validate_wav(audio: bytes) -> None:
    if not audio:
        raise SpeechProviderResponseError()
    try:
        with wave.open(io.BytesIO(audio), "rb") as reader:
            if reader.getnchannels() not in {1, 2}:
                raise SpeechProviderResponseError()
            if reader.getframerate() <= 0 or reader.getnframes() <= 0:
                raise SpeechProviderResponseError()
            if reader.getsampwidth() not in {1, 2, 3, 4}:
                raise SpeechProviderResponseError()
    except (EOFError, wave.Error) as error:
        raise SpeechProviderResponseError() from error
