from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, StringConstraints, field_validator, model_validator

from .common import NonEmptyText, ProtocolIdentifier, RealtimeProtocolModel


MAX_SPEECH_PHRASES = 12
MAX_SPEECH_PHRASE_CHARACTERS = 600
MAX_SPEECH_RESPONSE_CHARACTERS = 1_800
MAX_SPEECH_ALIGNMENT_SPANS = 512

SpeechPhraseIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=40,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]


class SpeechPhase(StrEnum):
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


class SpeechFailureCode(StrEnum):
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


class SpeechFailureComponent(StrEnum):
    REALTIME_PROVIDER = "realtimeProvider"
    SPEECH_PROVIDER = "speechProvider"
    CANVAS_SPECIALIST = "canvasSpecialist"
    MEMORY_STORE = "memoryStore"
    PLAYBACK = "playback"
    PROTOCOL = "protocol"
    COORDINATOR = "coordinator"


class SpeechFailure(RealtimeProtocolModel):
    failure_id: ProtocolIdentifier
    component: SpeechFailureComponent
    code: SpeechFailureCode
    message: Annotated[NonEmptyText, StringConstraints(max_length=240)]
    retryable: bool


class SpeechDelivery(StrEnum):
    NEUTRAL = "neutral"
    WARM = "warm"
    PLAYFUL = "playful"
    THOUGHTFUL = "thoughtful"
    ENCOURAGING = "encouraging"
    CONCERNED = "concerned"


class SpeechExpressionIntent(StrEnum):
    NEUTRAL = "neutral"
    WARM_SMILE = "warmSmile"
    CURIOUS = "curious"
    THINKING = "thinking"
    ENCOURAGING = "encouraging"
    CONCERNED = "concerned"
    SERIOUS = "serious"


class SpeechGazeIntent(StrEnum):
    CHILD = "child"
    THINKING_AWAY = "thinkingAway"
    CANVAS = "canvas"


class SpeechMotionIntent(StrEnum):
    NONE = "none"
    ACKNOWLEDGE = "acknowledge"
    PRESENT_CANVAS = "presentCanvas"
    ENCOURAGE = "encourage"


class SpeechPhraseAnchor(RealtimeProtocolModel):
    phrase: Annotated[NonEmptyText, StringConstraints(max_length=120)]
    occurrence: int = Field(ge=0, le=3)


class SpeechPhrase(RealtimeProtocolModel):
    """One complete bounded phrase released to TTS.

    The presentation hints remain because they are part of the current V4
    speech wire contract. ``canvasCues`` is retained only for byte-compatible
    V4 parsing and serialization; it is always empty and contains no legacy
    canvas schema.
    """

    id: SpeechPhraseIdentifier
    speech: Annotated[
        NonEmptyText,
        StringConstraints(max_length=MAX_SPEECH_PHRASE_CHARACTERS),
    ]
    delivery: SpeechDelivery
    expression: SpeechExpressionIntent
    gaze: SpeechGazeIntent
    motion_intent: SpeechMotionIntent
    canvas_cues: list[Any] = Field(max_length=0)

    @field_validator("canvas_cues", mode="before")
    @classmethod
    def legacy_canvas_cues_are_empty(cls, value: object) -> object:
        if not isinstance(value, list) or value:
            raise ValueError("speech phrases cannot contain legacy canvas cues")
        return value

    @model_validator(mode="after")
    def semantic_cues_are_coherent(self) -> SpeechPhrase:
        if self.gaze == SpeechGazeIntent.CANVAS:
            raise ValueError("gaze=canvas requires legacy canvas cues")
        if self.motion_intent == SpeechMotionIntent.PRESENT_CANVAS:
            raise ValueError("motionIntent=presentCanvas requires legacy canvas cues")
        return self


class SpeechAlignment(RealtimeProtocolModel):
    segment_id: SpeechPhraseIdentifier
    text_start: int = Field(ge=0, le=MAX_SPEECH_RESPONSE_CHARACTERS)
    text_end: int = Field(gt=0, le=MAX_SPEECH_RESPONSE_CHARACTERS)
    sample_start: int = Field(ge=0, le=2_147_483_647)
    sample_end: int = Field(gt=0, le=2_147_483_647)
    phoneme: Annotated[str, StringConstraints(min_length=1, max_length=16)] | None

    @model_validator(mode="after")
    def ranges_have_positive_length(self) -> SpeechAlignment:
        if self.text_end <= self.text_start:
            raise ValueError("alignment textEnd must follow textStart")
        if self.sample_end <= self.sample_start:
            raise ValueError("alignment sampleEnd must follow sampleStart")
        return self
