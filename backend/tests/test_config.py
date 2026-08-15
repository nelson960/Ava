from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from ava_backend.app import create_app
from ava_backend.auth import AuthenticatedClient
from ava_backend.config import (
    AvaEnvironment,
    BackendConfigurationError,
    Settings,
)


class _Authenticator:
    async def authenticate(self, _request: object) -> AuthenticatedClient:
        return AuthenticatedClient(subject="client:test")


class _Model:
    async def generate_response(self, **_arguments: object) -> object:
        raise AssertionError("not called during app construction")


def test_development_configuration_does_not_hide_missing_model_key() -> None:
    settings = Settings(
        environment=AvaEnvironment.DEVELOPMENT,
        _env_file=None,
    )

    assert settings.openai_realtime_model == "gpt-realtime-2.1-mini"
    assert settings.openai_realtime_noise_reduction == "near_field"
    assert settings.openai_realtime_vad_threshold == 0.45
    assert settings.openai_realtime_vad_prefix_padding_ms == 500
    assert settings.openai_realtime_vad_silence_duration_ms == 450
    assert settings.openai_canvas_timeout_seconds == 25
    assert settings.openai_live_tests_enabled is False
    assert settings.openai_tts_model == "gpt-4o-mini-tts-2025-12-15"
    assert settings.openai_tts_voice == "marin"
    with pytest.raises(BackendConfigurationError, match="AVA_OPENAI_API_KEY"):
        create_app(settings)


def test_production_requires_safety_secret() -> None:
    with pytest.raises(ValidationError, match="AVA_SAFETY_HASH_SECRET"):
        Settings(
            environment=AvaEnvironment.PRODUCTION,
            openai_api_key=SecretStr("sk-production-test-value"),
        )


def test_production_requires_authenticator_even_with_provider_injected() -> None:
    settings = Settings(
        environment=AvaEnvironment.PRODUCTION,
        openai_api_key=SecretStr("sk-production-test-value"),
        safety_hash_secret=SecretStr("x" * 32),
        realtime_session_token_secret=SecretStr("r" * 32),
        realtime_public_event_origin="wss://ava.example.test",
        database_url=SecretStr("postgresql://ava:secret@db.example.test/ava"),
    )

    with pytest.raises(BackendConfigurationError, match="RequestAuthenticator"):
        create_app(settings, ava_model=_Model())

    app = create_app(
        settings,
        ava_model=_Model(),
        authenticator=_Authenticator(),
    )
    assert app.docs_url is None


def test_postgres_memory_url_is_required_and_strict_in_production() -> None:
    with pytest.raises(ValidationError, match="AVA_DATABASE_URL"):
        Settings(
            environment=AvaEnvironment.PRODUCTION,
            openai_api_key=SecretStr("sk-production-test-value"),
            safety_hash_secret=SecretStr("x" * 32),
            realtime_session_token_secret=SecretStr("r" * 32),
            realtime_public_event_origin="wss://ava.example.test",
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
