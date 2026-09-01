from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
import re
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from .common import NonEmptyText, RealtimeProtocolModel


INTERACTIVE_CANVAS_TOOL_NAME = "request_interactive_canvas"
CANCEL_INTERACTIVE_CANVAS_TOOL_NAME = "cancel_interactive_canvas"
SEARCH_MEMORY_TOOL_NAME = "search_memory"
GET_CURRENT_TIME_TOOL_NAME = "get_current_time"
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


class SearchMemoryRequest(RealtimeProtocolModel):
    query: Annotated[NonEmptyText, StringConstraints(max_length=500)] = Field(
        description=(
            "A concise historical detail to find when it is not already present "
            "in the live conversation or supplied memory references."
        )
    )
    from_date: date | None = Field(
        default=None,
        description="Optional inclusive local calendar date in the child's timezone.",
    )
    to_date: date | None = Field(
        default=None,
        description="Optional inclusive local calendar date in the child's timezone.",
    )


class GetCurrentTimeRequest(RealtimeProtocolModel):
    """The application owns timezone and current-time inputs."""


class RealtimeUtilityToolKind(StrEnum):
    SEARCH_MEMORY = SEARCH_MEMORY_TOOL_NAME
    GET_CURRENT_TIME = GET_CURRENT_TIME_TOOL_NAME


class SearchMemoryToolStatus(StrEnum):
    AVAILABLE = "available"
    NO_MATCHES = "noMatches"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"


class SearchMemoryReferenceV4(RealtimeProtocolModel):
    source_kind: Annotated[NonEmptyText, StringConstraints(max_length=32)]
    started_at: datetime | None = None
    ended_at: datetime | None = None
    timezone_ids: list[Annotated[NonEmptyText, StringConstraints(max_length=64)]] = (
        Field(max_length=32)
    )
    text: Annotated[NonEmptyText, StringConstraints(max_length=1_200)]

    @model_validator(mode="after")
    def time_range_is_atomic(self) -> SearchMemoryReferenceV4:
        if (self.started_at is None) != (self.ended_at is None):
            raise ValueError("memory tool reference time range must be complete")
        if self.started_at is not None:
            if self.started_at.tzinfo is None or self.ended_at is None:
                raise ValueError("memory tool reference times must be timezone-aware")
            if self.ended_at.tzinfo is None or self.ended_at < self.started_at:
                raise ValueError("memory tool reference time range is invalid")
        return self


class SearchMemoryToolOutputV4(RealtimeProtocolModel):
    status: SearchMemoryToolStatus
    references: list[SearchMemoryReferenceV4] = Field(
        default_factory=list, max_length=5
    )
    reason: Annotated[str, StringConstraints(max_length=160)] | None = None

    @model_validator(mode="after")
    def result_shape_matches_status(self) -> SearchMemoryToolOutputV4:
        if self.status == SearchMemoryToolStatus.AVAILABLE:
            if not self.references or self.reason is not None:
                raise ValueError("available memory output requires references only")
        elif self.status == SearchMemoryToolStatus.NO_MATCHES:
            if self.references or self.reason is not None:
                raise ValueError(
                    "noMatches memory output cannot contain data or reason"
                )
        elif self.references or not self.reason:
            raise ValueError("failed memory output requires a reason and no references")
        return self


class CurrentTimeDaypart(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    NIGHT = "night"


class GetCurrentTimeToolOutputV4(RealtimeProtocolModel):
    local_iso_date_time: Annotated[NonEmptyText, StringConstraints(max_length=64)]
    weekday: Annotated[NonEmptyText, StringConstraints(max_length=16)]
    daypart: CurrentTimeDaypart
    timezone_id: Annotated[NonEmptyText, StringConstraints(max_length=64)]


class RejectedUtilityToolOutputV4(RealtimeProtocolModel):
    status: Annotated[str, StringConstraints(pattern="^rejected$")] = "rejected"
    reason: Annotated[NonEmptyText, StringConstraints(max_length=160)]


type RealtimeUtilityToolOutputV4 = (
    SearchMemoryToolOutputV4 | GetCurrentTimeToolOutputV4 | RejectedUtilityToolOutputV4
)


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


@dataclass(frozen=True, slots=True)
class RealtimeSearchMemoryToolCall:
    call_id: str
    request: SearchMemoryRequest

    def __post_init__(self) -> None:
        if not _CALL_ID.fullmatch(self.call_id):
            raise ValueError("Realtime memory call_id has an invalid format")


@dataclass(frozen=True, slots=True)
class RealtimeGetCurrentTimeToolCall:
    call_id: str
    request: GetCurrentTimeRequest

    def __post_init__(self) -> None:
        if not _CALL_ID.fullmatch(self.call_id):
            raise ValueError("Realtime current-time call_id has an invalid format")


@dataclass(frozen=True, slots=True)
class RealtimeRejectedUtilityToolCall:
    call_id: str
    tool_kind: RealtimeUtilityToolKind
    reason: str

    def __post_init__(self) -> None:
        if not _CALL_ID.fullmatch(self.call_id):
            raise ValueError("Realtime utility call_id has an invalid format")
        if not self.reason or len(self.reason) > 160:
            raise ValueError("Realtime utility rejection reason is invalid")


type RealtimeTurnEvent = (
    RealtimeSpeechTextDelta
    | RealtimeCanvasToolCall
    | RealtimeCancelCanvasToolCall
    | RealtimeRejectedCanvasToolCall
    | RealtimeSearchMemoryToolCall
    | RealtimeGetCurrentTimeToolCall
    | RealtimeRejectedUtilityToolCall
)
