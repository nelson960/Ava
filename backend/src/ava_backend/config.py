from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_OPENAI_REALTIME_TRUNCATION_POST_INSTRUCTIONS_TOKENS = 80_000
DEFAULT_OPENAI_REALTIME_TRUNCATION_RETENTION_RATIO = 0.8


class AvaEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class RealtimeDeliveryMode(StrEnum):
    """Explicit speech delivery selected for protocol-v3 sessions."""

    DISABLED = "disabled"
    CAPTION_PREVIEW = "captionPreview"
    ELEVENLABS = "elevenlabs"


class BackendConfigurationError(RuntimeError):
    """Raised when the server cannot safely start with its configuration."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AVA_",
        env_file=(Path("backend/.env"), Path(".env")),
        env_file_encoding="utf-8",
        extra="forbid",
        case_sensitive=False,
    )

    environment: AvaEnvironment = AvaEnvironment.DEVELOPMENT
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.6-terra"
    openai_reasoning_effort: Literal["none", "low", "medium"] = "low"
    openai_timeout_seconds: float = Field(default=20, ge=3, le=60)
    openai_max_retries: int = Field(default=2, ge=0, le=3)
    openai_max_output_tokens: int = Field(default=1200, ge=256, le=4096)
    openai_realtime_model: str = "gpt-realtime-2.1-mini"
    openai_realtime_transcription_model: str = "gpt-transcribe"
    openai_realtime_connect_timeout_seconds: float = Field(
        default=15,
        ge=3,
        le=60,
    )
    openai_realtime_max_output_tokens: int = Field(
        default=1200,
        ge=256,
        le=4096,
    )
    openai_realtime_noise_reduction: Literal["near_field", "far_field"] = "near_field"
    openai_realtime_vad_threshold: float = Field(default=0.45, ge=0, le=1)
    openai_realtime_vad_prefix_padding_ms: int = Field(
        default=500,
        ge=0,
        le=5_000,
    )
    openai_realtime_vad_silence_duration_ms: int = Field(
        default=450,
        ge=100,
        le=2_000,
    )
    openai_realtime_truncation_post_instructions_tokens: int = Field(
        default=DEFAULT_OPENAI_REALTIME_TRUNCATION_POST_INSTRUCTIONS_TOKENS,
        ge=1_024,
        le=1_000_000,
        strict=True,
    )
    openai_realtime_truncation_retention_ratio: float = Field(
        default=DEFAULT_OPENAI_REALTIME_TRUNCATION_RETENTION_RATIO,
        gt=0,
        lt=1,
    )
    openai_canvas_model: Literal["gpt-5.6-sol"] = "gpt-5.6-sol"
    openai_canvas_reasoning_effort: Literal["none", "low"] = "none"
    openai_canvas_timeout_seconds: float = Field(
        default=90,
        ge=3,
        le=180,
        description="Maximum duration of the primary Sol generation attempt.",
    )
    openai_canvas_first_progress_timeout_seconds: float = Field(
        default=40,
        ge=1,
        le=60,
    )
    openai_canvas_stream_stall_timeout_seconds: float = Field(
        default=15,
        ge=1,
        le=60,
    )
    openai_canvas_overall_timeout_seconds: float = Field(
        default=150,
        ge=10,
        le=300,
    )
    openai_canvas_repair_timeout_seconds: float = Field(
        default=45,
        ge=3,
        le=120,
    )
    openai_canvas_max_retries: Literal[0] = 0
    openai_canvas_max_quality_repairs: Literal[0, 1] = 1
    openai_canvas_max_output_tokens: int = Field(
        default=20_000,
        ge=4_096,
        le=32_768,
    )
    openai_canvas_max_accumulated_output_bytes: int = Field(
        default=512 * 1024,
        ge=256 * 1024,
        le=1024 * 1024,
    )
    canvas_max_svg_bytes: int = Field(
        default=384 * 1024,
        ge=128 * 1024,
        le=512 * 1024,
    )
    canvas_presentation_timeout_seconds: float = Field(
        default=30,
        ge=1,
        le=180,
    )
    openai_live_tests_enabled: bool = False
    openai_tts_model: str = "gpt-4o-mini-tts-2025-12-15"
    openai_tts_voice: Literal[
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "fable",
        "nova",
        "onyx",
        "sage",
        "shimmer",
        "verse",
        "marin",
        "cedar",
    ] = "marin"
    openai_tts_speed: float = Field(default=1.0, ge=0.25, le=4.0)
    elevenlabs_api_key: SecretStr | None = None
    elevenlabs_voice_id: str | None = None
    elevenlabs_voice_label: str = "Eve marry"
    elevenlabs_model_id: str | None = None
    elevenlabs_commercial_rights_confirmed: bool = False
    elevenlabs_output_format: Literal["pcm_24000"] = "pcm_24000"
    elevenlabs_timeout_seconds: float = Field(default=20, ge=3, le=60)
    elevenlabs_stability: float = Field(default=0.5, ge=0, le=1)
    elevenlabs_similarity_boost: float = Field(default=0.75, ge=0, le=1)
    elevenlabs_style: float = Field(default=0, ge=0, le=1)
    elevenlabs_use_speaker_boost: bool = True
    elevenlabs_speed: float = Field(default=1.0, ge=0.7, le=1.2)
    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None
    supabase_timeout_seconds: float = Field(default=10, ge=3, le=30)
    database_url: SecretStr | None = None
    memory_pool_min_size: int = Field(default=1, ge=0, le=32)
    memory_pool_max_size: int = Field(default=4, ge=1, le=32)
    memory_pool_timeout_seconds: float = Field(default=5, ge=1, le=30)
    context_history_max_messages: int = Field(default=12, ge=0, le=16)
    context_history_max_characters: int = Field(default=8000, ge=0, le=32_000)
    safety_hash_secret: SecretStr | None = None
    realtime_session_token_secret: SecretStr | None = None
    realtime_public_event_origin: str | None = None
    realtime_session_ttl_seconds: int = Field(default=120, ge=30, le=600)
    realtime_max_sessions: int = Field(default=128, ge=1, le=10_000)
    realtime_max_turn_seconds: int = Field(default=90, ge=5, le=180)
    realtime_max_session_seconds: int = Field(default=1800, ge=60, le=7200)
    realtime_playback_progress_interval_ms: int = Field(
        default=250,
        ge=50,
        le=2_000,
    )
    realtime_delivery_mode: RealtimeDeliveryMode = RealtimeDeliveryMode.DISABLED
    realtime_allow_insecure_loopback: bool = False
    development_log_path: Path | None = None
    request_max_bytes: int = Field(default=256 * 1024, ge=16 * 1024, le=1024 * 1024)
    development_allowed_hosts: str = "127.0.0.1,::1,localhost,testclient"

    @field_validator(
        "openai_model",
        "openai_realtime_model",
        "openai_realtime_transcription_model",
        "openai_canvas_model",
        "openai_tts_model",
        "elevenlabs_voice_label",
    )
    @classmethod
    def validate_required_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Configured provider names cannot be empty")
        return normalized

    @field_validator("elevenlabs_voice_id")
    @classmethod
    def validate_elevenlabs_voice_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if not 8 <= len(normalized) <= 128 or any(
            character
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
            for character in normalized
        ):
            raise ValueError("AVA_ELEVENLABS_VOICE_ID has an invalid format")
        return normalized

    @field_validator("elevenlabs_model_id")
    @classmethod
    def validate_elevenlabs_model_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        if self.environment is not AvaEnvironment.PRODUCTION:
            return self
        if self.realtime_delivery_mode is RealtimeDeliveryMode.CAPTION_PREVIEW:
            raise ValueError(
                "AVA_REALTIME_DELIVERY_MODE=captionPreview is development-only"
            )
        if self.realtime_delivery_mode is not RealtimeDeliveryMode.ELEVENLABS:
            raise ValueError(
                "AVA_REALTIME_DELIVERY_MODE=elevenlabs is required in production"
            )
        if self.realtime_allow_insecure_loopback:
            raise ValueError(
                "AVA_REALTIME_ALLOW_INSECURE_LOOPBACK cannot be enabled in production"
            )
        self.require_openai_api_key()
        secret = self.safety_hash_secret
        if secret is None or not secret.get_secret_value().strip():
            raise ValueError("AVA_SAFETY_HASH_SECRET is required in production")
        if len(secret.get_secret_value()) < 32:
            raise ValueError(
                "AVA_SAFETY_HASH_SECRET must contain at least 32 characters"
            )
        realtime_secret = self.realtime_session_token_secret
        if (
            realtime_secret is None
            or len(realtime_secret.get_secret_value().strip()) < 32
        ):
            raise ValueError(
                "AVA_REALTIME_SESSION_TOKEN_SECRET must contain at least "
                "32 characters in production"
            )
        if self.realtime_public_event_origin is None:
            raise ValueError(
                "AVA_REALTIME_PUBLIC_EVENT_ORIGIN is required in production"
            )
        try:
            self.require_postgres_memory_url()
        except BackendConfigurationError as error:
            raise ValueError(str(error)) from error
        try:
            self.require_elevenlabs_credentials()
        except BackendConfigurationError as error:
            raise ValueError(str(error)) from error
        return self

    @model_validator(mode="after")
    def validate_memory_pool(self) -> Settings:
        if self.memory_pool_min_size > self.memory_pool_max_size:
            raise ValueError(
                "AVA_MEMORY_POOL_MIN_SIZE cannot exceed AVA_MEMORY_POOL_MAX_SIZE"
            )
        return self

    @model_validator(mode="after")
    def validate_canvas_lifecycle_budgets(self) -> Settings:
        if self.openai_canvas_overall_timeout_seconds < (
            self.openai_canvas_timeout_seconds
        ):
            raise ValueError(
                "AVA_OPENAI_CANVAS_OVERALL_TIMEOUT_SECONDS cannot be shorter than "
                "AVA_OPENAI_CANVAS_TIMEOUT_SECONDS"
            )
        if self.openai_canvas_max_accumulated_output_bytes < self.canvas_max_svg_bytes:
            raise ValueError(
                "AVA_OPENAI_CANVAS_MAX_ACCUMULATED_OUTPUT_BYTES cannot be smaller "
                "than AVA_CANVAS_MAX_SVG_BYTES"
            )
        return self

    @field_validator("realtime_public_event_origin")
    @classmethod
    def validate_realtime_public_event_origin(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "wss"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "AVA_REALTIME_PUBLIC_EVENT_ORIGIN must be an absolute WSS "
                "origin or path prefix without credentials, query, or fragment"
            )
        return normalized

    def require_openai_api_key(self) -> str:
        key = self.openai_api_key
        if key is None or not key.get_secret_value().strip():
            raise BackendConfigurationError(
                "AVA_OPENAI_API_KEY is required to start Ava's OpenAI providers"
            )
        raw = key.get_secret_value().strip()
        if raw.lower() in {"replace-me", "changeme", "your-api-key"}:
            raise BackendConfigurationError(
                "AVA_OPENAI_API_KEY still contains a placeholder value"
            )
        return raw

    def require_elevenlabs_credentials(self) -> tuple[str, str, str]:
        key = self.elevenlabs_api_key
        if key is None or not key.get_secret_value().strip():
            raise BackendConfigurationError(
                "AVA_ELEVENLABS_API_KEY is required to start Ava's selected "
                "timed-speech provider"
            )
        raw_key = key.get_secret_value().strip()
        if raw_key.lower() in {"replace-me", "changeme", "your-api-key"}:
            raise BackendConfigurationError(
                "AVA_ELEVENLABS_API_KEY still contains a placeholder value"
            )
        voice_id = self.elevenlabs_voice_id
        if voice_id is None:
            raise BackendConfigurationError(
                "AVA_ELEVENLABS_VOICE_ID is required for the selected Ava "
                "voice candidate"
            )
        model_id = self.elevenlabs_model_id
        if model_id is None:
            raise BackendConfigurationError(
                "AVA_ELEVENLABS_MODEL_ID is required; export the exact model "
                "used to create the approved voice candidate"
            )
        if not self.elevenlabs_commercial_rights_confirmed:
            raise BackendConfigurationError(
                "AVA_ELEVENLABS_COMMERCIAL_RIGHTS_CONFIRMED=true is required "
                "after documenting commercial rights for the selected voice"
            )
        return raw_key, voice_id, model_id

    def require_supabase_memory_credentials(self) -> tuple[str, str]:
        raw_url = (self.supabase_url or "").strip()
        parsed = urlsplit(raw_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise BackendConfigurationError(
                "AVA_SUPABASE_URL must be the HTTPS project origin without a "
                "path, query, or fragment"
            )
        key = self.supabase_service_role_key
        if key is None or not key.get_secret_value().strip():
            raise BackendConfigurationError(
                "AVA_SUPABASE_SERVICE_ROLE_KEY is required to start durable "
                "conversation memory"
            )
        raw_key = key.get_secret_value().strip()
        if raw_key.lower() in {"replace-me", "changeme", "your-api-key"}:
            raise BackendConfigurationError(
                "AVA_SUPABASE_SERVICE_ROLE_KEY still contains a placeholder value"
            )
        return raw_url.rstrip("/"), raw_key

    def require_postgres_memory_url(self) -> str:
        value = self.database_url
        raw_url = value.get_secret_value().strip() if value is not None else ""
        parsed = urlsplit(raw_url)
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or not parsed.hostname
            or parsed.username is None
            or not parsed.password
            or not parsed.path
            or parsed.path == "/"
            or parsed.fragment
        ):
            raise BackendConfigurationError(
                "AVA_DATABASE_URL must be a PostgreSQL connection URL with "
                "host, user, password, and database name and without a fragment"
            )
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if len(query) != len({key for key, _value in query}) or any(
            key != "sslmode"
            or value not in {"prefer", "require", "verify-ca", "verify-full"}
            for key, value in query
        ):
            raise BackendConfigurationError(
                "AVA_DATABASE_URL permits only one standard sslmode query parameter"
            )
        return raw_url

    @property
    def allowed_development_hosts(self) -> tuple[str, ...]:
        hosts = tuple(
            part.strip()
            for part in self.development_allowed_hosts.split(",")
            if part.strip()
        )
        if not hosts:
            raise BackendConfigurationError(
                "AVA_DEVELOPMENT_ALLOWED_HOSTS must contain at least one host"
            )
        return hosts
