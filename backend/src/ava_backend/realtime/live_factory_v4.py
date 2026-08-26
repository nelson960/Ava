from __future__ import annotations

from uuid import uuid4

from ..canvas.svg_delivery import SvgCanvasDocumentStore
from ..canvas.svg_session import SvgCanvasSessionState
from ..canvas.svg_specialist import SvgCanvasSpecialist
from ..canvas.svg_workspace import SvgCanvasWorkspaceStore
from ..config import AvaEnvironment, RealtimeDeliveryMode, Settings
from ..protocol import AgeBand
from ..providers.elevenlabs_realtime_speech import (
    ElevenLabsInputStreamingSynthesizer,
)
from ..providers.openai_realtime_provider import OpenAIRealtimeProviderSession
from ..providers.openai_svg_canvas_provider import OpenAISvgCanvasSpecialist
from ..security import safety_identifier
from .caption_preview import DevelopmentCaptionPreviewSynthesizer
from .coordinator import DeterministicRealtimeSessionCoordinator
from .coordinator_v4 import ProtocolV4CanvasEventSink
from .delivery import StreamingSpeechSynthesizer
from .memory import AuthoritativeMemoryStore
from .svg_canvas_jobs import SvgCanvasJobCoordinator
from .websocket_v4 import RealtimeSessionCoordinatorV4


class LiveRealtimeCoordinatorFactoryV4:
    """Creates one persistent v4 conversation with asynchronous Sol SVG jobs."""

    def __init__(
        self,
        *,
        settings: Settings,
        memory: AuthoritativeMemoryStore,
        documents: SvgCanvasDocumentStore,
        canvas_workspaces: SvgCanvasWorkspaceStore,
    ) -> None:
        delivery_mode = settings.realtime_delivery_mode
        if delivery_mode is RealtimeDeliveryMode.DISABLED:
            raise ValueError("protocol-v4 requires an enabled speech delivery mode")
        if delivery_mode is RealtimeDeliveryMode.CAPTION_PREVIEW:
            if settings.environment is AvaEnvironment.PRODUCTION:
                raise ValueError("captionPreview protocol-v4 cannot run in production")
        elif delivery_mode is RealtimeDeliveryMode.ELEVENLABS:
            settings.require_elevenlabs_credentials()
        else:
            raise AssertionError(f"unhandled realtime delivery mode {delivery_mode!r}")
        self._settings = settings
        self._memory = memory
        self._documents = documents
        self._canvas_workspaces = canvas_workspaces
        self._api_key = settings.require_openai_api_key()

    def create(
        self,
        *,
        session_id: str,
        request_id: str,
        subject: str,
        age_band: AgeBand,
    ) -> RealtimeSessionCoordinatorV4:
        safety_id = self._safety_identifier(subject)
        realtime = OpenAIRealtimeProviderSession(
            api_key=self._api_key,
            model=self._settings.openai_realtime_model,
            transcription_model=(self._settings.openai_realtime_transcription_model),
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
        )
        specialist: SvgCanvasSpecialist = OpenAISvgCanvasSpecialist(
            self._settings,
            session_id=session_id,
            safety_identifier=safety_id,
        )
        state = SvgCanvasSessionState(
            workspace=self._canvas_workspaces.workspace_for(subject)
        )
        canvas_events = ProtocolV4CanvasEventSink()
        jobs = SvgCanvasJobCoordinator(
            session_id=session_id,
            age_band=age_band,
            specialist=specialist,
            state=state,
            documents=self._documents,
            event_sink=canvas_events,
            generation_id_factory=lambda: f"generation-{uuid4().hex}",
        )

        suffix = uuid4().hex
        return DeterministicRealtimeSessionCoordinator(
            session_id=session_id,
            turn_id=f"turn-{suffix}",
            request_id=request_id,
            stream_id=f"stream-{suffix}",
            subject=subject,
            age_band=age_band,
            realtime=realtime,
            speech=self._create_speech_synthesizer(),
            canvas_jobs=jobs,
            canvas_events=canvas_events,
            memory=self._memory,
            persistent=True,
            turn_id_factory=lambda: f"turn-{uuid4().hex}",
            request_id_factory=lambda: f"request-{uuid4().hex}",
            stream_id_factory=lambda: f"stream-{uuid4().hex}",
        )

    def _create_speech_synthesizer(self) -> StreamingSpeechSynthesizer:
        if (
            self._settings.realtime_delivery_mode
            is RealtimeDeliveryMode.CAPTION_PREVIEW
        ):
            return DevelopmentCaptionPreviewSynthesizer()
        if self._settings.realtime_delivery_mode is RealtimeDeliveryMode.ELEVENLABS:
            return ElevenLabsInputStreamingSynthesizer(self._settings)
        raise AssertionError("protocol-v4 speech mode changed after factory validation")

    def _safety_identifier(self, subject: str) -> str:
        configured = self._settings.safety_hash_secret
        if configured is not None:
            return safety_identifier(subject, configured.get_secret_value())
        return safety_identifier(subject, "ava-development-loopback-only")
