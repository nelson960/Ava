from __future__ import annotations

import io
import wave
from types import SimpleNamespace

import pytest

from ava_backend.config import AvaEnvironment, Settings
from ava_backend.errors import SpeechProviderResponseError
from ava_backend.providers.openai_speech_provider import OpenAIAvaSpeech


def _wav_bytes(*, frames: int = 2400, sample_rate: int = 24000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\x00\x00" * frames)
    return output.getvalue()


class _SpeechResponse:
    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self.response = SimpleNamespace(headers={"x-request-id": "tts-request-123"})

    async def aread(self) -> bytes:
        return self.audio


class _Speech:
    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self.arguments: dict[str, object] = {}

    async def create(self, **arguments: object) -> _SpeechResponse:
        self.arguments = arguments
        return _SpeechResponse(self.audio)


class _Client:
    def __init__(self, audio: bytes) -> None:
        self.audio = SimpleNamespace(speech=_Speech(audio))


def _settings() -> Settings:
    return Settings(environment=AvaEnvironment.TEST)


async def test_openai_speech_provider_requests_auditable_wav() -> None:
    client = _Client(_wav_bytes())
    provider = OpenAIAvaSpeech(_settings(), client=client)

    result = await provider.synthesize(
        text="Hello there.",
        instructions="Speak warmly.",
    )

    assert result.audio_format == "wav"
    assert result.media_type == "audio/wav"
    assert result.model == "gpt-4o-mini-tts-2025-12-15"
    assert result.voice == "marin"
    assert result.provider_request_id == "tts-request-123"
    assert client.audio.speech.arguments == {
        "model": "gpt-4o-mini-tts-2025-12-15",
        "voice": "marin",
        "input": "Hello there.",
        "instructions": "Speak warmly.",
        "response_format": "wav",
        "speed": 1.0,
        "stream_format": "audio",
    }


async def test_openai_speech_provider_rejects_invalid_audio() -> None:
    provider = OpenAIAvaSpeech(_settings(), client=_Client(b"not a wav"))

    with pytest.raises(SpeechProviderResponseError):
        await provider.synthesize(
            text="Hello there.",
            instructions="Speak warmly.",
        )


async def test_openai_speech_provider_rejects_empty_inputs_before_spending() -> None:
    provider = OpenAIAvaSpeech(_settings(), client=_Client(_wav_bytes()))

    with pytest.raises(ValueError, match="text cannot be empty"):
        await provider.synthesize(text=" ", instructions="Speak warmly.")
    with pytest.raises(ValueError, match="instructions cannot be empty"):
        await provider.synthesize(text="Hello.", instructions=" ")
