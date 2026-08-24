from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    model_validator,
)

from ..canvas.svg_session import SvgCanvasStateErrorCode
from .common import NonEmptyText, ProtocolIdentifier, RealtimeProtocolModel
from .speech_contract import (
    MAX_SPEECH_ALIGNMENT_SPANS,
    MAX_SPEECH_PHRASES,
    MAX_SPEECH_RESPONSE_CHARACTERS,
    SpeechAlignment,
    SpeechFailure,
    SpeechPhase,
    SpeechPhrase,
)

PROTOCOL_V4 = 4
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class AvaServerEventTypeV4(StrEnum):
    TURN_CREATED = "turnCreated"
    TURN_PHASE_CHANGED = "turnPhaseChanged"
    SEGMENT_READY = "segmentReady"
    AUDIO_METADATA = "audioMetadata"
    AUDIO_CHUNK = "audioChunk"
    ALIGNMENT_BATCH = "alignmentBatch"
    INTERRUPT_REQUESTED = "interruptRequested"
    CANVAS_GENERATION_STARTED = "canvasGenerationStarted"
    CANVAS_DOCUMENT_AVAILABLE = "canvasDocumentAvailable"
    CANVAS_PRESENTATION_CUE = "canvasPresentationCue"
    CANVAS_ACTIVATED = "canvasActivated"
    CANVAS_DOCUMENT_DISCARDED = "canvasDocumentDiscarded"
    CANVAS_REJECTED = "canvasRejected"
    INTERACTION_REJECTED = "interactionRejected"
    MEMORY_PERSISTENCE_FAILED = "memoryPersistenceFailed"
    TURN_COMPLETED = "turnCompleted"
    TURN_CANCELLED = "turnCancelled"
    TURN_FAILED = "turnFailed"


class AvaServerEventV4(RealtimeProtocolModel):
    protocol_version: Literal[4]
    event: AvaServerEventTypeV4
    session_id: ProtocolIdentifier
    sequence: int = Field(ge=0, le=1_000_000)
    revision: int = Field(ge=0, le=1_000_000)


class AvaTurnServerEventV4(AvaServerEventV4):
    turn_id: ProtocolIdentifier


class TurnCreatedEventV4(AvaTurnServerEventV4):
    event: Literal[AvaServerEventTypeV4.TURN_CREATED]
    request_id: ProtocolIdentifier


class TurnPhaseChangedEventV4(AvaTurnServerEventV4):
    event: Literal[AvaServerEventTypeV4.TURN_PHASE_CHANGED]
    phase: SpeechPhase


class SegmentReadyEventV4(AvaTurnServerEventV4):
    event: Literal[AvaServerEventTypeV4.SEGMENT_READY]
    segment_index: int = Field(ge=0, lt=MAX_SPEECH_PHRASES)
    segment: SpeechPhrase


class AudioMetadataEventV4(AvaTurnServerEventV4):
    event: Literal[AvaServerEventTypeV4.AUDIO_METADATA]
    stream_id: ProtocolIdentifier
    audio_format: Literal["pcm_s16le"]
    sample_rate_hz: Literal[24000]
    channels: Literal[1]
    bits_per_sample: Literal[16]
    model_id: ProtocolIdentifier
    voice_id: ProtocolIdentifier


class AudioChunkEventV4(AvaTurnServerEventV4):
    """Descriptor for the binary PCM frame that must immediately follow."""

    event: Literal[AvaServerEventTypeV4.AUDIO_CHUNK]
    stream_id: ProtocolIdentifier
    chunk_index: int = Field(ge=0, le=1_000_000)
    sample_offset: int = Field(ge=0, le=2_147_483_647)
    sample_count: int = Field(gt=0, le=96_000)
    byte_length: int = Field(gt=0, le=192_000)

    @model_validator(mode="after")
    def byte_length_matches_pcm(self) -> AudioChunkEventV4:
        if self.byte_length != self.sample_count * 2:
            raise ValueError("PCM16 byteLength must equal sampleCount * 2")
        return self


class AlignmentBatchEventV4(AvaTurnServerEventV4):
    event: Literal[AvaServerEventTypeV4.ALIGNMENT_BATCH]
    stream_id: ProtocolIdentifier
    spans: list[SpeechAlignment] = Field(
        min_length=1,
        max_length=MAX_SPEECH_ALIGNMENT_SPANS,
    )

    @model_validator(mode="after")
    def spans_are_monotonic(self) -> AlignmentBatchEventV4:
        for previous, current in zip(self.spans, self.spans[1:], strict=False):
            if current.sample_start < previous.sample_start:
                raise ValueError("alignment spans must be sample-monotonic")
        return self


class InterruptRequestedEventV4(AvaTurnServerEventV4):
    """Provider VAD requests interruption of the active PCM stream."""

    event: Literal[AvaServerEventTypeV4.INTERRUPT_REQUESTED]
    stream_id: ProtocolIdentifier
    reason: Annotated[NonEmptyText, StringConstraints(max_length=120)]


class CanvasGenerationStartedEventV4(AvaServerEventV4):
    """Session control announcing accepted Sol work before a document exists."""

    event: Literal[AvaServerEventTypeV4.CANVAS_GENERATION_STARTED]
    generation_id: ProtocolIdentifier
    source_turn_id: ProtocolIdentifier


class CanvasDocumentAvailableEventV4(AvaServerEventV4):
    event: Literal[AvaServerEventTypeV4.CANVAS_DOCUMENT_AVAILABLE]
    generation_id: ProtocolIdentifier
    source_turn_id: ProtocolIdentifier
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    svg_path: Annotated[str, StringConstraints(pattern=r"^/[A-Za-z0-9_./:-]+$")]
    manifest_path: Annotated[str, StringConstraints(pattern=r"^/[A-Za-z0-9_./:-]+$")]
    svg_sha256: Sha256Digest
    manifest_sha256: Sha256Digest
    svg_byte_length: int = Field(gt=0, le=262_144)
    manifest_byte_length: int = Field(gt=0, le=65_536)
    renderer_capability_version: Literal["ava-svg-renderer-v1"]
    validator_version: Literal["ava-svg-validator-v1"]
    manifest_version: Literal["ava-svg-manifest-v1"]


class CanvasPresentationCueEventV4(AvaServerEventV4):
    event: Literal[AvaServerEventTypeV4.CANVAS_PRESENTATION_CUE]
    generation_id: ProtocolIdentifier
    source_turn_id: ProtocolIdentifier
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    presentation_sample_offset: int = Field(ge=0, le=2_147_483_647)
    svg_sha256: Sha256Digest
    manifest_sha256: Sha256Digest


class CanvasActivatedEventV4(AvaServerEventV4):
    event: Literal[AvaServerEventTypeV4.CANVAS_ACTIVATED]
    generation_id: ProtocolIdentifier
    source_turn_id: ProtocolIdentifier
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    first_rendered_sample: int = Field(ge=0, le=2_147_483_647)
    svg_sha256: Sha256Digest
    manifest_sha256: Sha256Digest


class CanvasDocumentDiscardReason(StrEnum):
    INTERRUPTED_BEFORE_PRESENTATION = "interruptedBeforePresentation"
    SUPERSEDED = "superseded"
    SESSION_CLOSED = "sessionClosed"
    RENDERER_FAILURE = "rendererFailure"


class CanvasDocumentDiscardedEventV4(AvaServerEventV4):
    event: Literal[AvaServerEventTypeV4.CANVAS_DOCUMENT_DISCARDED]
    generation_id: ProtocolIdentifier
    source_turn_id: ProtocolIdentifier
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    reason: CanvasDocumentDiscardReason


class CanvasGenerationRejectionCode(StrEnum):
    PROVIDER_UNAVAILABLE = "providerUnavailable"
    PROVIDER_INVALID = "providerInvalid"
    GENERATION_LIMIT_EXCEEDED = "generationLimitExceeded"
    STATIC_VALIDATION_FAILED = "staticValidationFailed"
    PREPARATION_FAILED = "preparationFailed"
    CANCELLED = "cancelled"


class CanvasRejectedEventV4(AvaServerEventV4):
    event: Literal[AvaServerEventTypeV4.CANVAS_REJECTED]
    generation_id: ProtocolIdentifier | None = None
    call_id: ProtocolIdentifier | None = None
    source_turn_id: ProtocolIdentifier
    rejection_code: CanvasGenerationRejectionCode
    message: Annotated[NonEmptyText, StringConstraints(max_length=240)]
    retryable: bool

    @model_validator(mode="after")
    def identifies_call_or_generation(self) -> CanvasRejectedEventV4:
        if (self.generation_id is None) == (self.call_id is None):
            raise ValueError(
                "canvasRejected requires exactly one of generationId or callId"
            )
        return self


class InteractionRejectedEventV4(AvaServerEventV4):
    """Visible, non-terminal rejection of a session-scoped interaction."""

    event: Literal[AvaServerEventTypeV4.INTERACTION_REJECTED]
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    interaction_revision: int = Field(ge=1, le=1_000_000)
    binding_id: ProtocolIdentifier
    rejection_code: SvgCanvasStateErrorCode
    message: Annotated[NonEmptyText, StringConstraints(max_length=240)]


class MemoryPersistenceFailedEventV4(AvaTurnServerEventV4):
    event: Literal[AvaServerEventTypeV4.MEMORY_PERSISTENCE_FAILED]
    failure: SpeechFailure


class TurnCompletedEventV4(AvaTurnServerEventV4):
    event: Literal[AvaServerEventTypeV4.TURN_COMPLETED]
    delivered_sample_count: int = Field(ge=0, le=2_147_483_647)
    delivered_text: Annotated[
        str,
        StringConstraints(max_length=MAX_SPEECH_RESPONSE_CHARACTERS),
    ]


class TurnCancelledEventV4(AvaTurnServerEventV4):
    event: Literal[AvaServerEventTypeV4.TURN_CANCELLED]
    delivered_sample_count: int = Field(ge=0, le=2_147_483_647)
    delivered_text: Annotated[
        str,
        StringConstraints(max_length=MAX_SPEECH_RESPONSE_CHARACTERS),
    ]
    reason: Annotated[NonEmptyText, StringConstraints(max_length=120)]


class TurnFailedEventV4(AvaTurnServerEventV4):
    event: Literal[AvaServerEventTypeV4.TURN_FAILED]
    failure: SpeechFailure
    delivered_sample_count: int = Field(ge=0, le=2_147_483_647)
    delivered_text: Annotated[
        str,
        StringConstraints(max_length=MAX_SPEECH_RESPONSE_CHARACTERS),
    ]


AvaServerEventUnionV4 = Annotated[
    TurnCreatedEventV4
    | TurnPhaseChangedEventV4
    | SegmentReadyEventV4
    | AudioMetadataEventV4
    | AudioChunkEventV4
    | AlignmentBatchEventV4
    | InterruptRequestedEventV4
    | CanvasGenerationStartedEventV4
    | CanvasDocumentAvailableEventV4
    | CanvasPresentationCueEventV4
    | CanvasActivatedEventV4
    | CanvasDocumentDiscardedEventV4
    | CanvasRejectedEventV4
    | InteractionRejectedEventV4
    | MemoryPersistenceFailedEventV4
    | TurnCompletedEventV4
    | TurnCancelledEventV4
    | TurnFailedEventV4,
    Field(discriminator="event"),
]
AVA_SERVER_EVENT_ADAPTER_V4: TypeAdapter[AvaServerEventUnionV4] = TypeAdapter(
    AvaServerEventUnionV4
)


def serialize_server_event_v4(event: AvaServerEventUnionV4) -> str:
    serialized = event.model_dump_json(
        by_alias=True,
        exclude_none=isinstance(event, CanvasRejectedEventV4),
    )
    try:
        decoded = AVA_SERVER_EVENT_ADAPTER_V4.validate_json(serialized)
    except ValidationError as error:  # pragma: no cover
        raise ValueError(
            "protocol-v4 server event failed post-serialization validation"
        ) from error
    if type(decoded) is not type(event) or decoded != event:
        raise ValueError("protocol-v4 server event changed during wire serialization")
    return serialized


class AvaClientEventTypeV4(StrEnum):
    CLIENT_READY = "clientReady"
    PLAYBACK_STARTED = "playbackStarted"
    PLAYBACK_PROGRESS = "playbackProgress"
    PLAYBACK_STOPPED = "playbackStopped"
    CANVAS_DOCUMENT_PREPARED = "canvasDocumentPrepared"
    CANVAS_PREPARATION_FAILED = "canvasPreparationFailed"
    CANVAS_RUNTIME_FAILED = "canvasRuntimeFailed"
    CANVAS_PRESENTED = "canvasPresented"
    CANVAS_INTERACTION = "canvasInteraction"
    CANCEL_TURN = "cancelTurn"


class AvaClientEventV4(RealtimeProtocolModel):
    protocol_version: Literal[4]
    event: AvaClientEventTypeV4
    session_id: ProtocolIdentifier
    sequence: int = Field(ge=0, le=1_000_000)


class ClientReadyEventV4(AvaClientEventV4):
    event: Literal[AvaClientEventTypeV4.CLIENT_READY]
    renderer_capability_version: Literal["ava-svg-renderer-v1"]
    validator_version: Literal["ava-svg-validator-v1"]


class PlaybackStartedEventV4(AvaClientEventV4):
    event: Literal[AvaClientEventTypeV4.PLAYBACK_STARTED]
    turn_id: ProtocolIdentifier
    stream_id: ProtocolIdentifier
    rendered_sample_count: Literal[0]


class PlaybackProgressEventV4(AvaClientEventV4):
    event: Literal[AvaClientEventTypeV4.PLAYBACK_PROGRESS]
    turn_id: ProtocolIdentifier
    stream_id: ProtocolIdentifier
    rendered_sample_count: int = Field(ge=0, le=2_147_483_647)
    buffered_sample_count: int = Field(ge=0, le=2_147_483_647)


class PlaybackStoppedEventV4(AvaClientEventV4):
    event: Literal[AvaClientEventTypeV4.PLAYBACK_STOPPED]
    turn_id: ProtocolIdentifier
    stream_id: ProtocolIdentifier
    rendered_sample_count: int = Field(ge=0, le=2_147_483_647)
    reason: Annotated[NonEmptyText, StringConstraints(max_length=120)]


class CanvasDocumentPreparedEventV4(AvaClientEventV4):
    event: Literal[AvaClientEventTypeV4.CANVAS_DOCUMENT_PREPARED]
    generation_id: ProtocolIdentifier
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    svg_sha256: Sha256Digest
    manifest_sha256: Sha256Digest
    renderer_capability_version: Literal["ava-svg-renderer-v1"]
    validator_version: Literal["ava-svg-validator-v1"]


class CanvasPreparationFailureCode(StrEnum):
    FETCH_FAILED = "fetchFailed"
    CONTENT_IDENTITY_MISMATCH = "contentIdentityMismatch"
    RENDERER_REJECTED = "rendererRejected"
    RENDERER_TERMINATED = "rendererTerminated"
    PREPARATION_TIMEOUT = "preparationTimeout"


class CanvasPreparationFailedEventV4(AvaClientEventV4):
    event: Literal[AvaClientEventTypeV4.CANVAS_PREPARATION_FAILED]
    generation_id: ProtocolIdentifier
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    svg_sha256: Sha256Digest
    manifest_sha256: Sha256Digest
    failure_code: CanvasPreparationFailureCode
    message: Annotated[NonEmptyText, StringConstraints(max_length=240)]


class CanvasRuntimeFailureCode(StrEnum):
    RENDERER_REJECTED = "rendererRejected"
    RENDERER_TERMINATED = "rendererTerminated"


class CanvasRuntimeFailedEventV4(AvaClientEventV4):
    event: Literal[AvaClientEventTypeV4.CANVAS_RUNTIME_FAILED]
    generation_id: ProtocolIdentifier
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    svg_sha256: Sha256Digest
    manifest_sha256: Sha256Digest
    failure_code: CanvasRuntimeFailureCode
    message: Annotated[NonEmptyText, StringConstraints(max_length=240)]


class CanvasPresentedEventV4(AvaClientEventV4):
    event: Literal[AvaClientEventTypeV4.CANVAS_PRESENTED]
    generation_id: ProtocolIdentifier
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    svg_sha256: Sha256Digest
    manifest_sha256: Sha256Digest
    first_rendered_sample: int = Field(ge=0, le=2_147_483_647)


class CanvasInteractionEventV4(AvaClientEventV4):
    event: Literal[AvaClientEventTypeV4.CANVAS_INTERACTION]
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    interaction_revision: int = Field(ge=1, le=1_000_000)
    svg_sha256: Sha256Digest
    manifest_sha256: Sha256Digest
    binding_id: ProtocolIdentifier


class CancelTurnEventV4(AvaClientEventV4):
    event: Literal[AvaClientEventTypeV4.CANCEL_TURN]
    turn_id: ProtocolIdentifier
    request_id: ProtocolIdentifier
    reason: Annotated[NonEmptyText, StringConstraints(max_length=120)]


AvaClientEventUnionV4 = Annotated[
    ClientReadyEventV4
    | PlaybackStartedEventV4
    | PlaybackProgressEventV4
    | PlaybackStoppedEventV4
    | CanvasDocumentPreparedEventV4
    | CanvasPreparationFailedEventV4
    | CanvasRuntimeFailedEventV4
    | CanvasPresentedEventV4
    | CanvasInteractionEventV4
    | CancelTurnEventV4,
    Field(discriminator="event"),
]
AVA_CLIENT_EVENT_ADAPTER_V4: TypeAdapter[AvaClientEventUnionV4] = TypeAdapter(
    AvaClientEventUnionV4
)


class AvaSessionLimitsV4(RealtimeProtocolModel):
    max_turn_seconds: int = Field(ge=5, le=180)
    max_response_characters: int = Field(
        ge=128,
        le=MAX_SPEECH_RESPONSE_CHARACTERS,
    )
    max_svg_bytes: int = Field(gt=0, le=262_144)
    max_manifest_bytes: int = Field(gt=0, le=65_536)
    max_audio_chunk_bytes: int = Field(ge=2_048, le=192_000)
    playback_progress_interval_ms: int = Field(ge=50, le=2_000)


class AvaSessionBootstrapV4(RealtimeProtocolModel):
    protocol_version: Literal[4]
    session_id: ProtocolIdentifier
    event_url: Annotated[str, StringConstraints(max_length=2_048)]
    webrtc_url: Annotated[str, StringConstraints(max_length=2_048)]
    canvas_document_base_url: Annotated[str, StringConstraints(max_length=2_048)]
    session_token: Annotated[NonEmptyText, StringConstraints(max_length=2_048)]
    webrtc_token: Annotated[NonEmptyText, StringConstraints(max_length=2_048)]
    canvas_token: Annotated[NonEmptyText, StringConstraints(max_length=2_048)]
    expires_at: Annotated[NonEmptyText, StringConstraints(max_length=64)]
    renderer_capability_version: Literal["ava-svg-renderer-v1"]
    validator_version: Literal["ava-svg-validator-v1"]
    manifest_version: Literal["ava-svg-manifest-v1"]
    limits: AvaSessionLimitsV4

    @model_validator(mode="after")
    def session_channels_are_secure(
        self,
        info: ValidationInfo,
    ) -> AvaSessionBootstrapV4:
        allow_insecure_loopback = bool(
            isinstance(info.context, dict)
            and info.context.get("allow_insecure_loopback") is True
        )
        self._validate_url(
            self.event_url,
            secure_scheme="wss",
            insecure_loopback_scheme="ws",
            label="eventUrl",
            allow_insecure_loopback=allow_insecure_loopback,
        )
        for value, label in (
            (self.webrtc_url, "webrtcUrl"),
            (self.canvas_document_base_url, "canvasDocumentBaseUrl"),
        ):
            self._validate_url(
                value,
                secure_scheme="https",
                insecure_loopback_scheme="http",
                label=label,
                allow_insecure_loopback=allow_insecure_loopback,
            )
        return self

    @staticmethod
    def _validate_url(
        value: str,
        *,
        secure_scheme: str,
        insecure_loopback_scheme: str,
        label: str,
        allow_insecure_loopback: bool,
    ) -> None:
        parsed = urlsplit(value)
        allowed = {secure_scheme}
        if allow_insecure_loopback:
            allowed.add(insecure_loopback_scheme)
        if (
            parsed.scheme not in allowed
            or not parsed.hostname
            or (
                parsed.scheme == insecure_loopback_scheme
                and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            )
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"{label} must use a secure absolute URL")
