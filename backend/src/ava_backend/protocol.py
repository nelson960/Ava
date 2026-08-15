from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ProtocolModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )


NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class ConversationRole(StrEnum):
    CHILD = "child"
    AVA = "ava"


class AgeBand(StrEnum):
    UNKNOWN = "unknown"
    EARLY_CHILDHOOD = "earlyChildhood"
    MIDDLE_CHILDHOOD = "middleChildhood"
    PRETEEN = "preteen"


class LearningDomain(StrEnum):
    GENERAL = "general"
    MATH = "math"
    SCIENCE = "science"


class AvaExpression(StrEnum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    THINKING = "thinking"
    ENCOURAGING = "encouraging"
    SURPRISED = "surprised"


class AvaGesture(StrEnum):
    IDLE = "idle"
    NOD = "nod"
    EXPLAIN = "explain"
    POINT_AT_DASHBOARD = "pointAtDashboard"
    CELEBRATE = "celebrate"


class DiagramKind(StrEnum):
    FRACTION_CIRCLE = "fractionCircle"
    FRACTION_BAR = "fractionBar"
    NUMBER_LINE = "numberLine"


class ConversationMessage(ProtocolModel):
    role: ConversationRole
    text: Annotated[NonEmptyText, StringConstraints(max_length=1800)]


class LessonTurnRequest(ProtocolModel):
    child_name: Annotated[NonEmptyText, StringConstraints(max_length=80)]
    age_band: AgeBand = AgeBand.UNKNOWN
    client_instance_id: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=8,
                max_length=64,
                pattern=r"^[A-Za-z0-9._:-]+$",
            ),
        ]
        | None
    ) = None
    message: Annotated[NonEmptyText, StringConstraints(max_length=2000)]
    recent_messages: list[ConversationMessage] = Field(
        default_factory=list, max_length=16
    )


class LessonTurnEnvelope(ProtocolModel):
    protocol_version: Literal[1]
    request: LessonTurnRequest


class FractionCircleDiagram(ProtocolModel):
    kind: Literal[DiagramKind.FRACTION_CIRCLE] = DiagramKind.FRACTION_CIRCLE
    title: Annotated[NonEmptyText, StringConstraints(max_length=120)]
    total_parts: int = Field(ge=2, le=12)
    highlighted_parts: int = Field(ge=0, le=12)

    @model_validator(mode="after")
    def highlighted_parts_fit_total(self) -> FractionCircleDiagram:
        if self.highlighted_parts > self.total_parts:
            raise ValueError("highlightedParts cannot exceed totalParts")
        return self


class FractionBarDiagram(ProtocolModel):
    kind: Literal[DiagramKind.FRACTION_BAR] = DiagramKind.FRACTION_BAR
    title: Annotated[NonEmptyText, StringConstraints(max_length=120)]
    total_parts: int = Field(ge=2, le=12)
    highlighted_parts: int = Field(ge=0, le=12)

    @model_validator(mode="after")
    def highlighted_parts_fit_total(self) -> FractionBarDiagram:
        if self.highlighted_parts > self.total_parts:
            raise ValueError("highlightedParts cannot exceed totalParts")
        return self


class NumberLineDiagram(ProtocolModel):
    kind: Literal[DiagramKind.NUMBER_LINE] = DiagramKind.NUMBER_LINE
    title: Annotated[NonEmptyText, StringConstraints(max_length=120)]
    minimum: int = Field(ge=-100, le=99)
    maximum: int = Field(ge=-99, le=100)
    step: int = Field(ge=1, le=20)
    start: int = Field(ge=-100, le=100)
    target: int = Field(ge=-100, le=100)

    @model_validator(mode="after")
    def range_is_bounded_and_aligned(self) -> NumberLineDiagram:
        if self.minimum >= self.maximum:
            raise ValueError("minimum must be less than maximum")
        span = self.maximum - self.minimum
        if span % self.step != 0 or span // self.step > 20:
            raise ValueError("number line must contain at most 21 evenly spaced values")
        for name, value in (("start", self.start), ("target", self.target)):
            if (
                value < self.minimum
                or value > self.maximum
                or (value - self.minimum) % self.step != 0
            ):
                raise ValueError(
                    f"{name} must be an aligned value between minimum and maximum"
                )
        return self


DiagramSpec = Annotated[
    FractionCircleDiagram | FractionBarDiagram | NumberLineDiagram,
    Field(discriminator="kind"),
]


class AvaResponse(ProtocolModel):
    speech: Annotated[NonEmptyText, StringConstraints(max_length=1800)]
    expression: AvaExpression
    gesture: AvaGesture
    diagram: DiagramSpec | None


class ProtocolV1Lesson(AvaResponse):
    """Compatibility shape consumed by the current Flutter client."""

    domain: Literal[LearningDomain.GENERAL] = LearningDomain.GENERAL


class LessonTurnResponse(ProtocolModel):
    protocol_version: Literal[1] = 1
    lesson: ProtocolV1Lesson


def adapt_ava_response_to_protocol_v1(response: AvaResponse) -> LessonTurnResponse:
    """Revalidate model output before adapting it to the existing wire contract.

    Protocol v1 requires ``domain``, but ordinary conversation is not learning
    evidence. Until progress has its own explicit signal, the adapter must not
    infer a curriculum domain from generated text.
    """

    lesson = ProtocolV1Lesson.model_validate(response.model_dump())
    return LessonTurnResponse(lesson=lesson)


class AvaDelivery(StrEnum):
    """How a segment should feel, without prescribing animation mechanics."""

    NEUTRAL = "neutral"
    WARM = "warm"
    PLAYFUL = "playful"
    THOUGHTFUL = "thoughtful"
    ENCOURAGING = "encouraging"
    CONCERNED = "concerned"


class AvaGazeIntent(StrEnum):
    CHILD = "child"
    THINKING_AWAY = "thinkingAway"
    DIAGRAM = "diagram"
    GESTURE_TARGET = "gestureTarget"


class AvaExpressionIntent(StrEnum):
    NEUTRAL = "neutral"
    WARM_SMILE = "warmSmile"
    CURIOUS = "curious"
    THINKING = "thinking"
    ENCOURAGING = "encouraging"
    SURPRISED = "surprised"
    CONCERNED = "concerned"
    SERIOUS = "serious"


class AvaGestureIntent(StrEnum):
    NONE = "none"
    ACKNOWLEDGE = "acknowledge"
    EXPLAIN_SMALL = "explainSmall"
    EXPLAIN_WIDE = "explainWide"
    COUNT = "count"
    COMPARE = "compare"
    PRESENT_LEFT = "presentLeft"
    PRESENT_RIGHT = "presentRight"
    POINT_AT_DASHBOARD = "pointAtDashboard"
    REASSURE = "reassure"
    CELEBRATE_SMALL = "celebrateSmall"


class DiagramCueAction(StrEnum):
    SHOW = "show"


class DiagramCue(ProtocolModel):
    action: Literal[DiagramCueAction.SHOW] = DiagramCueAction.SHOW
    diagram: DiagramSpec


SegmentId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=40,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]


class AvaPerformanceSegment(ProtocolModel):
    """A semantic beat interpreted by Ava's deterministic local director.

    ``diagram_cue`` is deliberately required-but-nullable. This keeps the
    generated JSON schema compatible with strict Structured Outputs while
    preventing an absent field from gaining an implicit runtime meaning.
    """

    id: SegmentId
    speech: Annotated[NonEmptyText, StringConstraints(max_length=600)]
    delivery: AvaDelivery
    expression: AvaExpressionIntent
    gaze: AvaGazeIntent
    gesture: AvaGestureIntent
    diagram_cue: DiagramCue | None

    @model_validator(mode="after")
    def semantic_cues_are_coherent(self) -> AvaPerformanceSegment:
        has_diagram = self.diagram_cue is not None
        points_at_diagram = self.gesture == AvaGestureIntent.POINT_AT_DASHBOARD
        looks_at_diagram = self.gaze == AvaGazeIntent.DIAGRAM

        if has_diagram != points_at_diagram or has_diagram != looks_at_diagram:
            raise ValueError(
                "diagramCue, gaze=diagram and gesture=pointAtDashboard "
                "must occur together"
            )

        if self.gesture == AvaGestureIntent.CELEBRATE_SMALL and (
            self.delivery == AvaDelivery.CONCERNED
            or self.expression
            in {AvaExpressionIntent.CONCERNED, AvaExpressionIntent.SERIOUS}
        ):
            raise ValueError(
                "gesture=celebrateSmall cannot use a concerned or serious tone"
            )

        if self.delivery == AvaDelivery.PLAYFUL and self.expression in {
            AvaExpressionIntent.CONCERNED,
            AvaExpressionIntent.SERIOUS,
        }:
            raise ValueError(
                "delivery=playful cannot use a concerned or serious expression"
            )
        return self


class AvaPerformancePlan(ProtocolModel):
    segments: list[AvaPerformanceSegment] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def plan_is_bounded_and_unambiguous(self) -> AvaPerformancePlan:
        ids = [segment.id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("performance segment ids must be unique")

        speech = " ".join(segment.speech for segment in self.segments)
        if len(speech) > 1800:
            raise ValueError("performance speech exceeds the 1800-character limit")

        diagram_cues = sum(segment.diagram_cue is not None for segment in self.segments)
        if diagram_cues > 1:
            raise ValueError("a performance plan can contain at most one diagramCue")
        return self

    @property
    def speech(self) -> str:
        return " ".join(segment.speech for segment in self.segments)


class PerformanceTurnResponse(ProtocolModel):
    """Protocol v2 plan contract; not yet connected to the live v1 endpoint."""

    protocol_version: Literal[2] = 2
    performance: AvaPerformancePlan


class PerformanceTurnEnvelope(ProtocolModel):
    protocol_version: Literal[2]
    request: LessonTurnRequest


class PerformanceEventType(StrEnum):
    """Reserved protocol-v2 streaming control-event vocabulary."""

    TURN_ACCEPTED = "turnAccepted"
    PERFORMANCE_PLAN = "performancePlan"
    AUDIO_METADATA = "audioMetadata"
    AUDIO_CHUNK = "audioChunk"
    CHARACTER_TIMING_BATCH = "characterTimingBatch"
    SEGMENT_BOUNDARY = "segmentBoundary"
    DIAGRAM_CUE = "diagramCue"
    TURN_COMPLETED = "turnCompleted"
    TURN_CANCELLED = "turnCancelled"
    TURN_FAILED = "turnFailed"


class PerformanceFailureCode(StrEnum):
    INVALID_PLAN = "invalidPlan"
    MODEL_REFUSAL = "modelRefusal"
    MODEL_TIMEOUT = "modelTimeout"
    MODEL_UNAVAILABLE = "modelUnavailable"
    SPEECH_TIMEOUT = "speechTimeout"
    SPEECH_UNAVAILABLE = "speechUnavailable"
    STREAM_INTERRUPTED = "streamInterrupted"
    TIMING_INVALID = "timingInvalid"
    MEMORY_UNAVAILABLE = "memoryUnavailable"
    RENDERER_UNAVAILABLE = "rendererUnavailable"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internalError"


class PerformanceFailure(ProtocolModel):
    code: PerformanceFailureCode
    message: Annotated[NonEmptyText, StringConstraints(max_length=240)]
    retryable: bool


TurnId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]


class PerformanceEvent(ProtocolModel):
    protocol_version: Literal[2] = 2
    event: PerformanceEventType
    sequence: int = Field(ge=0)
    turn_id: TurnId


class TurnAcceptedEvent(PerformanceEvent):
    event: Literal[PerformanceEventType.TURN_ACCEPTED] = (
        PerformanceEventType.TURN_ACCEPTED
    )
    request_id: NonEmptyText


class PerformancePlanEvent(PerformanceEvent):
    event: Literal[PerformanceEventType.PERFORMANCE_PLAN] = (
        PerformanceEventType.PERFORMANCE_PLAN
    )
    performance: AvaPerformancePlan


class AudioMetadataEvent(PerformanceEvent):
    event: Literal[PerformanceEventType.AUDIO_METADATA] = (
        PerformanceEventType.AUDIO_METADATA
    )
    audio_format: Literal["pcm_s16le"]
    media_type: Literal["audio/x-raw"]
    sample_rate_hz: int = Field(ge=8_000, le=192_000)
    channels: Literal[1]
    bits_per_sample: Literal[16]
    model: NonEmptyText
    voice_id: NonEmptyText
    voice_label: NonEmptyText


class SegmentBoundaryEvent(PerformanceEvent):
    event: Literal[PerformanceEventType.SEGMENT_BOUNDARY] = (
        PerformanceEventType.SEGMENT_BOUNDARY
    )
    segment_id: SegmentId
    segment_index: int = Field(ge=0, le=5)


class AudioChunkEvent(PerformanceEvent):
    event: Literal[PerformanceEventType.AUDIO_CHUNK] = PerformanceEventType.AUDIO_CHUNK
    segment_id: SegmentId
    chunk_index: int = Field(ge=0)
    audio_base64: Annotated[
        NonEmptyText,
        StringConstraints(max_length=3 * 1024 * 1024),
    ]


class CharacterTiming(ProtocolModel):
    character: Annotated[str, StringConstraints(min_length=1, max_length=1)]
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def end_follows_start(self) -> CharacterTiming:
        if self.end_seconds < self.start_seconds:
            raise ValueError("character timing endSeconds cannot precede startSeconds")
        return self


class CharacterTimingBatchEvent(PerformanceEvent):
    event: Literal[PerformanceEventType.CHARACTER_TIMING_BATCH] = (
        PerformanceEventType.CHARACTER_TIMING_BATCH
    )
    segment_id: SegmentId
    chunk_index: int = Field(ge=0)
    time_base: Literal["segmentAudio"] = "segmentAudio"
    timings: list[CharacterTiming] = Field(min_length=1, max_length=10_000)


class DiagramCueEvent(PerformanceEvent):
    event: Literal[PerformanceEventType.DIAGRAM_CUE] = PerformanceEventType.DIAGRAM_CUE
    segment_id: SegmentId
    cue: DiagramCue


class TurnCompletedEvent(PerformanceEvent):
    event: Literal[PerformanceEventType.TURN_COMPLETED] = (
        PerformanceEventType.TURN_COMPLETED
    )
    audio_chunk_count: int = Field(ge=1)


class TurnFailedEvent(PerformanceEvent):
    event: Literal[PerformanceEventType.TURN_FAILED] = PerformanceEventType.TURN_FAILED
    failure: PerformanceFailure


class ErrorCode(StrEnum):
    UNAUTHORIZED = "unauthorized"
    INVALID_REQUEST = "invalid_request"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    PROVIDER_REFUSAL = "provider_refusal"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_INVALID_RESPONSE = "provider_invalid_response"
    MEMORY_UNAVAILABLE = "memory_unavailable"
    INTERNAL_ERROR = "internal_error"


class ErrorDetail(ProtocolModel):
    code: ErrorCode
    message: NonEmptyText
    request_id: NonEmptyText


class ErrorEnvelope(ProtocolModel):
    error: ErrorDetail
