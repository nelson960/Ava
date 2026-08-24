from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from ava_backend.app import create_app
from ava_backend.auth import AuthenticatedClient
from ava_backend.config import (
    AvaEnvironment,
    BackendConfigurationError,
    RealtimeDeliveryMode,
    Settings,
)


class _Authenticator:
    async def authenticate(self, _request: object) -> AuthenticatedClient:
        return AuthenticatedClient(subject="client:test")


def _production_speech_configuration() -> dict[str, object]:
    return {
        "realtime_delivery_mode": RealtimeDeliveryMode.ELEVENLABS,
        "elevenlabs_api_key": SecretStr("elevenlabs-production-test-key"),
        "elevenlabs_voice_id": "voiceCandidateB123",
        "elevenlabs_model_id": "eleven_flash_v2_5",
        "elevenlabs_commercial_rights_confirmed": True,
    }


def test_development_configuration_does_not_hide_missing_model_key() -> None:
    settings = Settings(
        environment=AvaEnvironment.DEVELOPMENT,
        _env_file=None,
    )

    assert settings.openai_realtime_model == "gpt-realtime-2.1-mini"
    assert settings.openai_realtime_transcription_model == "gpt-transcribe"
    assert settings.openai_realtime_noise_reduction == "near_field"
    assert settings.openai_realtime_vad_threshold == 0.45
    assert settings.openai_realtime_vad_prefix_padding_ms == 500
    assert settings.openai_realtime_vad_silence_duration_ms == 450
    assert settings.openai_canvas_timeout_seconds == 60
    assert settings.openai_live_tests_enabled is False
    assert settings.openai_tts_model == "gpt-4o-mini-tts-2025-12-15"
    assert settings.openai_tts_voice == "marin"
    with pytest.raises(BackendConfigurationError, match="AVA_OPENAI_API_KEY"):
        settings.require_openai_api_key()


def test_canvas_specialist_model_is_sol_only() -> None:
    with pytest.raises(ValidationError, match="gpt-5.6-sol"):
        Settings(openai_canvas_model="gpt-5.6-luna")  # type: ignore[arg-type]


def test_production_requires_safety_secret() -> None:
    with pytest.raises(ValidationError, match="AVA_SAFETY_HASH_SECRET"):
        Settings(
            environment=AvaEnvironment.PRODUCTION,
            openai_api_key=SecretStr("sk-production-test-value"),
            **_production_speech_configuration(),
        )


def test_production_requires_authenticator_even_with_provider_injected() -> None:
    settings = Settings(
        environment=AvaEnvironment.PRODUCTION,
        openai_api_key=SecretStr("sk-production-test-value"),
        safety_hash_secret=SecretStr("x" * 32),
        realtime_session_token_secret=SecretStr("r" * 32),
        realtime_public_event_origin="wss://ava.example.test",
        database_url=SecretStr("postgresql://ava:secret@db.example.test/ava"),
        **_production_speech_configuration(),
    )

    with pytest.raises(BackendConfigurationError, match="RequestAuthenticator"):
        create_app(settings)

    app = create_app(
        settings,
        authenticator=_Authenticator(),
    )
    assert app.docs_url is None


def test_postgres_memory_url_is_required_and_strict_in_production() -> None:
    with pytest.raises(ValidationError, match="AVA_DATABASE_URL"):
        Settings(
            _env_file=None,
            environment=AvaEnvironment.PRODUCTION,
            openai_api_key=SecretStr("sk-production-test-value"),
            safety_hash_secret=SecretStr("x" * 32),
            realtime_session_token_secret=SecretStr("r" * 32),
            realtime_public_event_origin="wss://ava.example.test",
            **_production_speech_configuration(),
        )

    settings = Settings(
        database_url=SecretStr("postgresql://ava:secret@localhost:55432/ava")
    )
    assert settings.require_postgres_memory_url().endswith("/ava")

    supabase = Settings(
        database_url=SecretStr(
            "postgresql://postgres:secret@db.example.supabase.co/postgres"
            "?sslmode=require"
        )
    )
    assert "sslmode=require" in supabase.require_postgres_memory_url()

    invalid = Settings(
        database_url=SecretStr(
            "postgresql://postgres:secret@db.example.supabase.co/postgres"
            "?application_name=unsafe"
        )
    )
    with pytest.raises(BackendConfigurationError, match="sslmode"):
        invalid.require_postgres_memory_url()
