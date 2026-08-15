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

from ..canvas.protocol import AvaCanvasTransaction
from ..canvas.session import CanvasInteractionRejectionCode
from .common import (
    NonEmptyText,
    ProtocolIdentifier,
    RealtimeProtocolModel,
)
from .plan import (
    MAX_REALTIME_SPEECH_SEGMENTS,
    AvaTurnSegment,
    SegmentIdentifier,
)

PROTOCOL_V3 = 3


class AvaTurnPhase(StrEnum):
    CREATED = "created"
    CONNECTING = "connecting"
    LISTENING = "listening"
    PLANNING = "planning"
    SYNTHESIZING = "synthesizing"
    SPEAKING = "speaking"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class AvaServerEventType(StrEnum):
    TURN_CREATED = "turnCreated"
    TURN_PHASE_CHANGED = "turnPhaseChanged"
    SEGMENT_READY = "segmentReady"
    AUDIO_METADATA = "audioMetadata"
    AUDIO_CHUNK = "audioChunk"
    ALIGNMENT_BATCH = "alignmentBatch"
    CANVAS_CUE = "canvasCue"
    CANVAS_REJECTED = "canvasRejected"
    INTERACTION_REJECTED = "interactionRejected"
    MEMORY_PERSISTENCE_FAILED = "memoryPersistenceFailed"
    TURN_COMPLETED = "turnCompleted"
    TURN_CANCELLED = "turnCancelled"
    TURN_FAILED = "turnFailed"


class AvaFailureCode(StrEnum):
    AUTHENTICATION = "authentication"
    REALTIME_NEGOTIATION = "realtimeNegotiation"
    MODEL_INVALID = "modelInvalid"
    MODEL_TIMEOUT = "modelTimeout"
    MODEL_UNAVAILABLE = "modelUnavailable"
    SPEECH_INVALID = "speechInvalid"
    SPEECH_TIMEOUT = "speechTimeout"
    SPEECH_UNAVAILABLE = "speechUnavailable"
    PROTOCOL_SEQUENCE = "protocolSequence"
    CANVAS_REJECTED = "canvasRejected"
    MEMORY_UNAVAILABLE = "memoryUnavailable"
    PLAYBACK_UNAVAILABLE = "playbackUnavailable"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


class AvaFailureComponent(StrEnum):
    REALTIME_PROVIDER = "realtimeProvider"
    SPEECH_PROVIDER = "speechProvider"
    CANVAS_SPECIALIST = "canvasSpecialist"
    MEMORY_STORE = "memoryStore"
    PLAYBACK = "playback"
    PROTOCOL = "protocol"
    COORDINATOR = "coordinator"


class AvaFailure(RealtimeProtocolModel):
    failure_id: ProtocolIdentifier
    component: AvaFailureComponent
    code: AvaFailureCode
    message: Annotated[NonEmptyText, StringConstraints(max_length=240)]
    retryable: bool


class AvaServerEvent(RealtimeProtocolModel):
    protocol_version: Literal[3]
    event: AvaServerEventType
    session_id: ProtocolIdentifier
    turn_id: ProtocolIdentifier
    sequence: int = Field(ge=0, le=1_000_000)
    revision: int = Field(ge=0, le=1_000_000)


class TurnCreatedEvent(AvaServerEvent):
    event: Literal[AvaServerEventType.TURN_CREATED]
    request_id: ProtocolIdentifier


class TurnPhaseChangedEvent(AvaServerEvent):
    event: Literal[AvaServerEventType.TURN_PHASE_CHANGED]
    phase: AvaTurnPhase


class SegmentReadyEvent(AvaServerEvent):
    event: Literal[AvaServerEventType.SEGMENT_READY]
    segment_index: int = Field(
        ge=0,
        lt=MAX_REALTIME_SPEECH_SEGMENTS,
    )
    segment: AvaTurnSegment


class AudioMetadataEvent(AvaServerEvent):
    event: Literal[AvaServerEventType.AUDIO_METADATA]
    stream_id: ProtocolIdentifier
    audio_format: Literal["pcm_s16le"]
    sample_rate_hz: Literal[24000]
    channels: Literal[1]
    bits_per_sample: Literal[16]
    model_id: ProtocolIdentifier
    voice_id: ProtocolIdentifier


class AudioChunkEvent(AvaServerEvent):
    """Descriptor for the binary PCM frame that must immediately follow."""

    event: Literal[AvaServerEventType.AUDIO_CHUNK]
    stream_id: ProtocolIdentifier
    chunk_index: int = Field(ge=0, le=1_000_000)
    sample_offset: int = Field(ge=0, le=2_147_483_647)
    sample_count: int = Field(gt=0, le=96_000)
    byte_length: int = Field(gt=0, le=192_000)

    @model_validator(mode="after")
    def byte_length_matches_pcm(self) -> AudioChunkEvent:
        if self.byte_length != self.sample_count * 2:
            raise ValueError("PCM16 byteLength must equal sampleCount * 2")
        return self


class AvaAlignmentSpan(RealtimeProtocolModel):
    segment_id: SegmentIdentifier
    text_start: int = Field(ge=0, le=1_800)
    text_end: int = Field(gt=0, le=1_800)
    sample_start: int = Field(ge=0, le=2_147_483_647)
    sample_end: int = Field(gt=0, le=2_147_483_647)
    phoneme: Annotated[str, StringConstraints(min_length=1, max_length=16)] | None

    @model_validator(mode="after")
    def ranges_have_positive_length(self) -> AvaAlignmentSpan:
        if self.text_end <= self.text_start:
            raise ValueError("alignment textEnd must follow textStart")
        if self.sample_end <= self.sample_start:
            raise ValueError("alignment sampleEnd must follow sampleStart")
        return self


class AlignmentBatchEvent(AvaServerEvent):
    event: Literal[AvaServerEventType.ALIGNMENT_BATCH]
    stream_id: ProtocolIdentifier
    spans: list[AvaAlignmentSpan] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def spans_are_monotonic(self) -> AlignmentBatchEvent:
        for previous, current in zip(self.spans, self.spans[1:], strict=False):
            if current.sample_start < previous.sample_start:
                raise ValueError("alignment spans must be sample-monotonic")
        return self


class CanvasCueEvent(AvaServerEvent):
    event: Literal[AvaServerEventType.CANVAS_CUE]
    cue_id: ProtocolIdentifier
    sample_offset: int = Field(ge=0, le=2_147_483_647)
    transaction: AvaCanvasTransaction


class CanvasRejectedEvent(AvaServerEvent):
    event: Literal[AvaServerEventType.CANVAS_REJECTED]
    failure: AvaFailure

    @model_validator(mode="after")
    def failure_is_canvas_specific(self) -> CanvasRejectedEvent:
        if self.failure.code != AvaFailureCode.CANVAS_REJECTED:
            raise ValueError("canvasRejected requires failure.code=canvasRejected")
        return self


class InteractionRejectedEvent(AvaServerEvent):
    """Non-terminal rejection of a session-scoped semantic canvas action."""

    event: Literal[AvaServerEventType.INTERACTION_REJECTED]
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    interaction_revision: int = Field(ge=1, le=1_000_000)
    binding_id: ProtocolIdentifier
    rejection_code: CanvasInteractionRejectionCode
    message: Annotated[NonEmptyText, StringConstraints(max_length=240)]


class MemoryPersistenceFailedEvent(AvaServerEvent):
    """Non-terminal warning: speech completed but memory needs retry."""

    event: Literal[AvaServerEventType.MEMORY_PERSISTENCE_FAILED]
    failure: AvaFailure

    @model_validator(mode="after")
    def failure_is_memory_specific(self) -> MemoryPersistenceFailedEvent:
        if self.failure.code != AvaFailureCode.MEMORY_UNAVAILABLE:
            raise ValueError(
                "memoryPersistenceFailed requires failure.code=memoryUnavailable"
            )
        if not self.failure.retryable:
            raise ValueError("memoryPersistenceFailed must be retryable")
        return self


class TurnCompletedEvent(AvaServerEvent):
    event: Literal[AvaServerEventType.TURN_COMPLETED]
    delivered_sample_count: int = Field(ge=0, le=2_147_483_647)
    delivered_text: Annotated[str, StringConstraints(max_length=1_800)]


class TurnCancelledEvent(AvaServerEvent):
    event: Literal[AvaServerEventType.TURN_CANCELLED]
    delivered_sample_count: int = Field(ge=0, le=2_147_483_647)
    delivered_text: Annotated[str, StringConstraints(max_length=1_800)]
    reason: Annotated[NonEmptyText, StringConstraints(max_length=120)]


class TurnFailedEvent(AvaServerEvent):
    event: Literal[AvaServerEventType.TURN_FAILED]
    failure: AvaFailure
    delivered_sample_count: int = Field(ge=0, le=2_147_483_647)
    delivered_text: Annotated[str, StringConstraints(max_length=1_800)]


AvaServerEventUnion = Annotated[
    TurnCreatedEvent
    | TurnPhaseChangedEvent
    | SegmentReadyEvent
    | AudioMetadataEvent
    | AudioChunkEvent
    | AlignmentBatchEvent
    | CanvasCueEvent
    | CanvasRejectedEvent
    | InteractionRejectedEvent
    | MemoryPersistenceFailedEvent
    | TurnCompletedEvent
    | TurnCancelledEvent
    | TurnFailedEvent,
    Field(discriminator="event"),
]

AVA_SERVER_EVENT_ADAPTER: TypeAdapter[AvaServerEventUnion] = TypeAdapter(
    AvaServerEventUnion
)


def serialize_server_event(event: AvaServerEventUnion) -> str:
    """Serialize exactly the protocol object that Flutter must validate.

    Nullable fields in protocol v3 are required-by-presence. In particular,
    root canvas nodes carry ``parentId: null`` and alignment spans may carry
    ``phoneme: null``. A generic ``exclude_none`` policy therefore changes the
    wire contract after Pydantic has validated it. Round-tripping the final
    JSON here makes the production encoder itself part of the validation
    boundary.
    """

    serialized = event.model_dump_json(by_alias=True)
    try:
        decoded = AVA_SERVER_EVENT_ADAPTER.validate_json(serialized)
    except ValidationError as error:  # pragma: no cover - defensive invariant
        raise ValueError(
            "protocol-v3 server event failed post-serialization validation"
        ) from error
    if type(decoded) is not type(event) or decoded != event:
        raise ValueError("protocol-v3 server event changed during wire serialization")
    return serialized


class AvaSessionLimits(RealtimeProtocolModel):
    max_turn_seconds: int = Field(ge=5, le=180)
    max_response_characters: int = Field(ge=128, le=1_800)
    max_canvas_nodes: int = Field(ge=1, le=96)
    max_audio_chunk_bytes: int = Field(ge=2_048, le=192_000)
    playback_progress_interval_ms: int = Field(ge=50, le=2_000)


class AvaSessionBootstrap(RealtimeProtocolModel):
    protocol_version: Literal[3]
    session_id: ProtocolIdentifier
    event_url: Annotated[str, StringConstraints(max_length=2_048)]
    webrtc_url: Annotated[str, StringConstraints(max_length=2_048)]
    session_token: Annotated[NonEmptyText, StringConstraints(max_length=2_048)]
    webrtc_token: Annotated[NonEmptyText, StringConstraints(max_length=2_048)]
    expires_at: Annotated[NonEmptyText, StringConstraints(max_length=64)]
    limits: AvaSessionLimits

    @model_validator(mode="after")
    def session_channels_are_secure(
        self,
        info: ValidationInfo,
    ) -> AvaSessionBootstrap:
        allow_insecure_loopback = bool(
            isinstance(info.context, dict)
            and info.context.get("allow_insecure_loopback") is True
        )
        event = urlsplit(self.event_url)
        if (
            event.scheme not in ({"wss", "ws"} if allow_insecure_loopback else {"wss"})
            or not event.hostname
            or (event.scheme == "ws" and not _is_loopback_host(event.hostname))
            or event.username is not None
            or event.password is not None
            or event.query
            or event.fragment
        ):
            raise ValueError("eventUrl must be an absolute WSS URL")
        webrtc = urlsplit(self.webrtc_url)
        if (
            webrtc.scheme
            not in ({"https", "http"} if allow_insecure_loopback else {"https"})
            or not webrtc.hostname
            or (webrtc.scheme == "http" and not _is_loopback_host(webrtc.hostname))
            or webrtc.username is not None
            or webrtc.password is not None
            or webrtc.query
            or webrtc.fragment
        ):
            raise ValueError("webrtcUrl must be an absolute HTTPS URL")
        return self


def _is_loopback_host(host: str) -> bool:
    return host in {"localhost", "127.0.0.1", "::1"}


class AvaClientEventType(StrEnum):
    CLIENT_READY = "clientReady"
    PLAYBACK_STARTED = "playbackStarted"
    PLAYBACK_PROGRESS = "playbackProgress"
    PLAYBACK_STOPPED = "playbackStopped"
    CANVAS_INTERACTION = "canvasInteraction"
    CANCEL_TURN = "cancelTurn"


class AvaClientEvent(RealtimeProtocolModel):
    protocol_version: Literal[3]
    event: AvaClientEventType
    session_id: ProtocolIdentifier
    sequence: int = Field(ge=0, le=1_000_000)


class ClientReadyEvent(AvaClientEvent):
    event: Literal[AvaClientEventType.CLIENT_READY]


class PlaybackStartedEvent(AvaClientEvent):
    event: Literal[AvaClientEventType.PLAYBACK_STARTED]
    turn_id: ProtocolIdentifier
    stream_id: ProtocolIdentifier
    rendered_sample_count: Literal[0]


class PlaybackProgressEvent(AvaClientEvent):
    event: Literal[AvaClientEventType.PLAYBACK_PROGRESS]
    turn_id: ProtocolIdentifier
    stream_id: ProtocolIdentifier
    rendered_sample_count: int = Field(ge=0, le=2_147_483_647)
    buffered_sample_count: int = Field(ge=0, le=2_147_483_647)


class PlaybackStoppedEvent(AvaClientEvent):
    event: Literal[AvaClientEventType.PLAYBACK_STOPPED]
    turn_id: ProtocolIdentifier
    stream_id: ProtocolIdentifier
    rendered_sample_count: int = Field(ge=0, le=2_147_483_647)
    reason: Annotated[NonEmptyText, StringConstraints(max_length=120)]


class CanvasInteractionEvent(AvaClientEvent):
    event: Literal[AvaClientEventType.CANVAS_INTERACTION]
    scene_id: ProtocolIdentifier
    scene_revision: int = Field(ge=1, le=10_001)
    interaction_revision: int = Field(ge=1, le=1_000_000)
    binding_id: ProtocolIdentifier


class CancelTurnEvent(AvaClientEvent):
    event: Literal[AvaClientEventType.CANCEL_TURN]
    turn_id: ProtocolIdentifier
    request_id: ProtocolIdentifier
    reason: Annotated[NonEmptyText, StringConstraints(max_length=120)]


AvaClientEventUnion = Annotated[
    ClientReadyEvent
    | PlaybackStartedEvent
    | PlaybackProgressEvent
    | PlaybackStoppedEvent
    | CanvasInteractionEvent
    | CancelTurnEvent,
    Field(discriminator="event"),
]

AVA_CLIENT_EVENT_ADAPTER: TypeAdapter[AvaClientEventUnion] = TypeAdapter(
    AvaClientEventUnion
)
