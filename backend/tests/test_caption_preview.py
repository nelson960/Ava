from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from ava_backend.app import create_app
from ava_backend.config import AvaEnvironment, RealtimeDeliveryMode, Settings
from ava_backend.realtime.caption_preview import (
    DevelopmentCaptionPreviewSynthesizer,
)
from ava_backend.realtime.delivery import (
    SynthesizedAlignmentBatch,
    SynthesizedAudioChunk,
)
from ava_backend.realtime.memory import DevelopmentAuthoritativeMemoryStore
from ava_backend.realtime.plan import AvaTurnSegment


class _LegacyModel:
    async def generate_response(self, **_arguments: object) -> object:
        raise AssertionError("legacy model must not run during app construction")


async def test_caption_preview_emits_bounded_silence_and_complete_alignment() -> None:
    segment = AvaTurnSegment.model_validate(
        {
            "id": "preview",
            "speech": "Hello, Ava preview.",
            "delivery": "warm",
            "expression": "warmSmile",
            "gaze": "child",
            "motionIntent": "none",
            "canvasCues": [],
        }
    )
    synthesizer = DevelopmentCaptionPreviewSynthesizer(
        samples_per_character=480,
        chunk_sample_count=1_200,
    )

    items = [item async for item in synthesizer.synthesize(segment)]

    alignment = items[0]
    assert isinstance(alignment, SynthesizedAlignmentBatch)
    assert alignment.spans[-1].text_end == len(segment.speech)
    audio = [item for item in items[1:] if isinstance(item, SynthesizedAudioChunk)]
    assert sum(len(item.pcm) // 2 for item in audio) == len(segment.speech) * 480
    assert all(set(item.pcm) <= {0} for item in audio)
    assert synthesizer.voice_id == "development-no-voice"


def test_caption_preview_is_explicit_and_cannot_enter_production() -> None:
    with pytest.raises(ValidationError, match="development-only"):
        Settings(
            environment=AvaEnvironment.PRODUCTION,
            openai_api_key=SecretStr("sk-production-test-value"),
            safety_hash_secret=SecretStr("x" * 32),
            realtime_session_token_secret=SecretStr("r" * 32),
            realtime_public_event_origin="wss://ava.example.test",
            database_url=SecretStr("postgresql://ava:secret@db.example.test/ava"),
            realtime_delivery_mode=RealtimeDeliveryMode.CAPTION_PREVIEW,
            _env_file=None,
        )

    settings = Settings(
        environment=AvaEnvironment.TEST,
        openai_api_key=SecretStr("sk-test-value"),
        realtime_delivery_mode=RealtimeDeliveryMode.CAPTION_PREVIEW,
        _env_file=None,
    )
    app = create_app(
        settings,
        ava_model=_LegacyModel(),  # type: ignore[arg-type]
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
    )
    assert app.state.realtime_sessions is not None
