from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from .common import NonEmptyText, RealtimeProtocolModel


INTERACTIVE_CANVAS_TOOL_NAME = "request_interactive_canvas"
CANCEL_INTERACTIVE_CANVAS_TOOL_NAME = "cancel_interactive_canvas"
INTERACTIVE_CANVAS_BRIEF_MAX_CHARACTERS = 1000
CANVAS_MODEL_SAFE_REJECTION_REASON = "visualUnavailable"
_CALL_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class InteractiveCanvasOperation(StrEnum):
    NEW = "new"
    REVISE_FOCUSED = "reviseFocused"


class InteractiveCanvasMode(StrEnum):
    NONE = "none"
    EXPLORE = "explore"
    SELECT = "select"
    STEPS = "steps"


class InteractiveCanvasRequest(RealtimeProtocolModel):
    operation: InteractiveCanvasOperation = Field(
        description=(
            "Use new for a separate visual or different concept. Use "
            "reviseFocused only to modify the current focused visual."
        )
    )
    brief: Annotated[
        NonEmptyText,
        StringConstraints(max_length=INTERACTIVE_CANVAS_BRIEF_MAX_CHARACTERS),
    ] = Field(
        description=(
            "Describe the visual's conversational purpose, essential objects, "
            "relationships, flow, and labels. Do not repeat full context or "
            "provide coordinates, SVG, styles, code, scene IDs, or revisions."
        )
    )
    interaction_mode: InteractiveCanvasMode = Field(
        description=(
            "Use none for a static visual, explore for tapping parts to learn, "
            "select for choosing one visible option, or steps for ordered "
            "Back/Next progression."
        )
    )


class InteractiveCanvasCancellationRequest(RealtimeProtocolModel):
    """Explicit semantic request to stop unpresented canvas work."""


class InteractiveCanvasToolStatus(StrEnum):
    READY = "ready"
    REJECTED = "rejected"


class InteractiveCanvasToolStatusV4(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class InteractiveCanvasToolOutputV4(RealtimeProtocolModel):
    """Immediate function result; SVG generation is always asynchronous."""

    status: InteractiveCanvasToolStatusV4
    generation_id: str | None = Field(default=None, min_length=1, max_length=64)
    reason: Annotated[str, StringConstraints(max_length=240)] | None = None

    @model_validator(mode="after")
    def status_fields_are_atomic(self) -> InteractiveCanvasToolOutputV4:
        if self.status == InteractiveCanvasToolStatusV4.ACCEPTED:
            if self.generation_id is None:
                raise ValueError("accepted canvas output requires generationId")
            if self.reason is not None:
                raise ValueError("accepted canvas output cannot include reason")
        else:
            if not self.reason:
                raise ValueError("rejected canvas output requires reason")
            if self.generation_id is not None:
                raise ValueError("rejected canvas output cannot include generationId")
        return self


class InteractiveCanvasCancellationToolOutputV4(RealtimeProtocolModel):
    """Immediate result for an explicit cancellation of pending canvas work."""

    status: InteractiveCanvasToolStatusV4
    reason: Annotated[str, StringConstraints(max_length=240)] | None = None

    @model_validator(mode="after")
    def status_fields_are_atomic(self) -> InteractiveCanvasCancellationToolOutputV4:
        if self.status == InteractiveCanvasToolStatusV4.ACCEPTED:
            if self.reason is not None:
                raise ValueError("accepted canvas cancellation cannot include reason")
        elif not self.reason:
            raise ValueError("rejected canvas cancellation requires reason")
        return self


class InteractiveCanvasToolOutput(RealtimeProtocolModel):
    status: InteractiveCanvasToolStatus
    scene_id: str | None = Field(default=None, min_length=1, max_length=80)
    revision: int | None = Field(default=None, ge=1, le=10_001)
    summary: Annotated[str, StringConstraints(max_length=240)] | None = None
    reason: Annotated[str, StringConstraints(max_length=240)] | None = None

    @model_validator(mode="after")
    def status_fields_are_atomic(self) -> InteractiveCanvasToolOutput:
        if self.status == InteractiveCanvasToolStatus.READY:
            if self.scene_id is None or self.revision is None or not self.summary:
                raise ValueError(
                    "ready canvas output requires sceneId, revision, and summary"
                )
            if self.reason is not None:
                raise ValueError("ready canvas output cannot include reason")
        else:
            if not self.reason:
                raise ValueError("rejected canvas output requires reason")
            if any(
                value is not None
                for value in (self.scene_id, self.revision, self.summary)
            ):
                raise ValueError("rejected canvas output cannot include scene fields")
        return self


@dataclass(frozen=True, slots=True)
class RealtimeSpeechTextDelta:
    delta: str

    def __post_init__(self) -> None:
        if not isinstance(self.delta, str):
            raise TypeError("Realtime speech delta must be text")


@dataclass(frozen=True, slots=True)
class RealtimeCanvasToolCall:
    call_id: str
    request: InteractiveCanvasRequest

    def __post_init__(self) -> None:
        if not _CALL_ID.fullmatch(self.call_id):
            raise ValueError("Realtime canvas call_id has an invalid format")


@dataclass(frozen=True, slots=True)
class RealtimeCancelCanvasToolCall:
    call_id: str

    def __post_init__(self) -> None:
        if not _CALL_ID.fullmatch(self.call_id):
            raise ValueError("Realtime canvas call_id has an invalid format")


@dataclass(frozen=True, slots=True)
class RealtimeRejectedCanvasToolCall:
    call_id: str
    reason: str

    def __post_init__(self) -> None:
        if not _CALL_ID.fullmatch(self.call_id):
            raise ValueError("Realtime canvas call_id has an invalid format")
        if not self.reason or len(self.reason) > 240:
            raise ValueError("Realtime canvas rejection reason is invalid")


type RealtimeTurnEvent = (
    RealtimeSpeechTextDelta
    | RealtimeCanvasToolCall
    | RealtimeCancelCanvasToolCall
    | RealtimeRejectedCanvasToolCall
)
