from __future__ import annotations

from uuid import uuid4

from ..canvas.session import CanvasSessionState
from ..canvas.specialist import CanvasSpecialist
from ..config import AvaEnvironment, RealtimeDeliveryMode, Settings
from ..protocol import AgeBand
from ..providers.openai_canvas_provider import OpenAICanvasSpecialist
from ..providers.openai_realtime_provider import OpenAIRealtimeProviderSession
from ..security import safety_identifier
from .caption_preview import DevelopmentCaptionPreviewSynthesizer
from .coordinator import (
    DeterministicRealtimeSessionCoordinator,
    RealtimeSessionCoordinator,
)
from .memory import AuthoritativeMemoryStore


class LiveRealtimeCoordinatorFactory:
    """Creates one persistent protocol-v3 conversation coordinator."""

    def __init__(
        self,
        *,
        settings: Settings,
        memory: AuthoritativeMemoryStore,
    ) -> None:
        if settings.realtime_delivery_mode is RealtimeDeliveryMode.DISABLED:
            raise ValueError("live Realtime factory requires an enabled delivery mode")
        if settings.realtime_delivery_mode is RealtimeDeliveryMode.CAPTION_PREVIEW:
            if settings.environment is AvaEnvironment.PRODUCTION:
                raise ValueError("caption preview cannot run in production")
        else:
            raise ValueError(
                "ElevenLabs protocol-v3 streaming is not configured yet; "
                "use captionPreview only in development"
            )
        self._settings = settings
        self._memory = memory
        self._api_key = settings.require_openai_api_key()

    def create(
        self,
        *,
        session_id: str,
        request_id: str,
        subject: str,
        age_band: AgeBand,
    ) -> RealtimeSessionCoordinator:
        safety_id = self._safety_identifier(subject)
        canvas_specialist: CanvasSpecialist = OpenAICanvasSpecialist(
            self._settings,
            safety_identifier=safety_id,
        )
        suffix = uuid4().hex
        return DeterministicRealtimeSessionCoordinator(
            session_id=session_id,
            turn_id=f"turn-{suffix}",
            request_id=request_id,
            stream_id=f"stream-{suffix}",
            subject=subject,
            age_band=age_band,
            realtime=OpenAIRealtimeProviderSession(
                api_key=self._api_key,
                model=self._settings.openai_realtime_model,
                safety_identifier=safety_id,
                connect_timeout_seconds=(
                    self._settings.openai_realtime_connect_timeout_seconds
                ),
                turn_timeout_seconds=self._settings.realtime_max_turn_seconds,
                max_output_tokens=self._settings.openai_realtime_max_output_tokens,
                noise_reduction=self._settings.openai_realtime_noise_reduction,
                vad_threshold=self._settings.openai_realtime_vad_threshold,
                vad_prefix_padding_ms=(
                    self._settings.openai_realtime_vad_prefix_padding_ms
                ),
                vad_silence_duration_ms=(
                    self._settings.openai_realtime_vad_silence_duration_ms
                ),
            ),
            speech=DevelopmentCaptionPreviewSynthesizer(),
            canvas_specialist=canvas_specialist,
            memory=self._memory,
            memory_id_factory=lambda _turn_id: f"memory-{uuid4().hex}",
            # Canvas lifetime is exactly the coordinator/WebRTC conversation.
            # A child profile must never inherit a scene from another session.
            canvas_state=CanvasSessionState(),
            persistent=True,
            turn_id_factory=lambda: f"turn-{uuid4().hex}",
            request_id_factory=lambda: f"request-{uuid4().hex}",
            stream_id_factory=lambda: f"stream-{uuid4().hex}",
        )

    def _safety_identifier(self, subject: str) -> str:
        configured = self._settings.safety_hash_secret
        if configured is not None:
            return safety_identifier(subject, configured.get_secret_value())
        if self._settings.environment is AvaEnvironment.PRODUCTION:
            raise RuntimeError("production Realtime requires a safety hash secret")
        return safety_identifier(subject, "ava-development-loopback-only")
