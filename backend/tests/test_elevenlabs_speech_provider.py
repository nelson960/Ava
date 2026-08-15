from __future__ import annotations

import base64
import json

import httpx
import pytest
from pydantic import SecretStr

from ava_backend.config import (
    AvaEnvironment,
    BackendConfigurationError,
    Settings,
)
from ava_backend.errors import SpeechProviderResponseError
from ava_backend.providers.elevenlabs_speech_provider import (
    ElevenLabsAvaSpeech,
)


def _settings(**overrides: object) -> Settings:
    return Settings(
        environment=AvaEnvironment.TEST,
        elevenlabs_api_key=SecretStr("elevenlabs-test-key"),
        elevenlabs_voice_id="voiceCandidateB123",
        elevenlabs_model_id="eleven_multilingual_v2",
        elevenlabs_commercial_rights_confirmed=True,
        **overrides,
    )


def _line(
    audio: bytes,
    *,
    characters: list[str] | None = None,
    starts: list[float] | None = None,
    ends: list[float] | None = None,
) -> str:
    alignment = None
    if characters is not None:
        alignment = {
            "characters": characters,
            "character_start_times_seconds": starts,
            "character_end_times_seconds": ends,
        }
    return json.dumps(
        {
            "audio_base64": base64.b64encode(audio).decode("ascii"),
            "alignment": None,
            "normalized_alignment": alignment,
        }
    )


async def test_streams_candidate_b_audio_with_strict_character_timing() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        content = "\n".join(
            (
                _line(
                    b"first!",
                    characters=["H", "i"],
                    starts=[0.0, 0.08],
                    ends=[0.08, 0.16],
                ),
                _line(b"second"),
            )
        )
        return httpx.Response(
            200,
            headers={"request-id": "eleven-request-123"},
            content=content,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ElevenLabsAvaSpeech(
        _settings(),
        client=client,
        api_origin="https://elevenlabs.test",
    )
    chunks = [
        chunk
        async for chunk in provider.stream(
            text="Hi.",
            previous_text="Hello.",
            next_text="How are you?",
        )
    ]
    await client.aclose()

    assert provider.metadata.model == "eleven_multilingual_v2"
    assert provider.metadata.voice_label == "Eve marry"
    assert provider.metadata.sample_rate_hz == 24_000
    assert [chunk.audio for chunk in chunks] == [b"first!", b"second"]
    assert chunks[0].provider_request_id == "eleven-request-123"
    assert [
        (cue.character, cue.start_seconds, cue.end_seconds)
        for cue in chunks[0].alignment
    ] == [
        ("H", 0.0, 0.08),
        ("i", 0.08, 0.16),
    ]
    assert captured["url"] == (
        "https://elevenlabs.test/v1/text-to-speech/"
        "voiceCandidateB123/stream/with-timestamps?output_format=pcm_24000"
    )
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["xi-api-key"] == "elevenlabs-test-key"
    assert captured["body"] == {
        "text": "Hi.",
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": 1.0,
        },
        "apply_text_normalization": "auto",
        "previous_text": "Hello.",
        "next_text": "How are you?",
    }


@pytest.mark.parametrize(
    "content",
    [
        "",
        '{"audio_base64":"not-base64","alignment":null,"normalized_alignment":null}',
        _line(
            b"audio",
            characters=["a", "b"],
            starts=[0.2, 0.1],
            ends=[0.3, 0.2],
        ),
    ],
)
async def test_rejects_empty_invalid_or_non_monotonic_streams(
    content: str,
) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=content)
        )
    )
    provider = ElevenLabsAvaSpeech(
        _settings(),
        client=client,
        api_origin="https://elevenlabs.test",
    )

    with pytest.raises(SpeechProviderResponseError):
        _ = [chunk async for chunk in provider.stream(text="Hello.")]
    await client.aclose()


def test_requires_exact_candidate_credentials_before_activation() -> None:
    with pytest.raises(BackendConfigurationError, match="API_KEY"):
        ElevenLabsAvaSpeech(Settings(environment=AvaEnvironment.TEST))
    with pytest.raises(BackendConfigurationError, match="VOICE_ID"):
        ElevenLabsAvaSpeech(
            Settings(
                environment=AvaEnvironment.TEST,
                elevenlabs_api_key=SecretStr("elevenlabs-test-key"),
            )
        )
    with pytest.raises(BackendConfigurationError, match="MODEL_ID"):
        ElevenLabsAvaSpeech(
            Settings(
                environment=AvaEnvironment.TEST,
                elevenlabs_api_key=SecretStr("elevenlabs-test-key"),
                elevenlabs_voice_id="voiceCandidateB123",
            )
        )
    with pytest.raises(BackendConfigurationError, match="COMMERCIAL_RIGHTS"):
        ElevenLabsAvaSpeech(
            Settings(
                environment=AvaEnvironment.TEST,
                elevenlabs_api_key=SecretStr("elevenlabs-test-key"),
                elevenlabs_voice_id="voiceCandidateB123",
                elevenlabs_model_id="eleven_multilingual_v2",
            )
        )
