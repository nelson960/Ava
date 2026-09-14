from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
import re
from typing import Annotated

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from ..canvas.svg_workspace import CanvasSceneSelector
from .common import NonEmptyText, RealtimeProtocolModel, _to_camel


INTERACTIVE_CANVAS_TOOL_NAME = "request_interactive_canvas"
CANCEL_INTERACTIVE_CANVAS_TOOL_NAME = "cancel_interactive_canvas"
MANAGE_CANVAS_PRESENTATION_TOOL_NAME = "manage_canvas_presentation"
SEARCH_MEMORY_TOOL_NAME = "search_memory"
GET_CURRENT_TIME_TOOL_NAME = "get_current_time"
RECORD_SEMANTIC_TURN_CAPTURE_TOOL_NAME = "record_semantic_turn_capture"
INTERACTIVE_CANVAS_BRIEF_MAX_CHARACTERS = 1000
CANVAS_MODEL_SAFE_REJECTION_REASON = "visualUnavailable"
SEARCH_MEMORY_MAX_REFERENCES = 5
# One optional utility followed by one terminal Canvas action.
MAX_SEMANTIC_TOOL_EXCHANGES = 2
SEARCH_MEMORY_SOURCE_KIND_MAX_CHARACTERS = 32
SEARCH_MEMORY_MAX_TIMEZONE_IDS = 32
SEARCH_MEMORY_TIMEZONE_ID_MAX_CHARACTERS = 64
SEARCH_MEMORY_REFERENCE_TEXT_MAX_CHARACTERS = 1_200
SEARCH_MEMORY_REASON_MAX_CHARACTERS = 160
CURRENT_TIME_ISO_MAX_CHARACTERS = 64
CURRENT_TIME_WEEKDAY_MAX_CHARACTERS = 16
CURRENT_TIME_TIMEZONE_MAX_CHARACTERS = 64
UTILITY_REJECTION_REASON_MAX_CHARACTERS = 160
CANVAS_TOOL_OUTPUT_REASON_MAX_CHARACTERS = 240
_CALL_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class InteractiveCanvasOperation(StrEnum):
    NEW = "new"
    REVISE = "revise"


class InteractiveCanvasMode(StrEnum):
    NONE = "none"
    EXPLORE = "explore"
    SELECT = "select"
    STEPS = "steps"


class InteractiveCanvasContinuousControl(StrEnum):
    NONE = "none"
    DRAG = "drag"
    SLIDER = "slider"
    TIMELINE = "timeline"


class CanvasActionCompletion(StrEnum):
    REQUEST_COMPLETE = "requestComplete"
    NEEDS_RESULT_REASONING = "needsResultReasoning"


class InteractiveCanvasRequest(RealtimeProtocolModel):
    operation: InteractiveCanvasOperation = Field(
        description="new creates a visual; revise semantically targets a retained scene."
    )
    selector: CanvasSceneSelector | None = Field(
        default=None,
        description="Optional semantic revise target; omitted means the focused scene.",
    )
    position: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="One-based retained position; required only with selector=position.",
    )
    title: Annotated[NonEmptyText, StringConstraints(max_length=120)] | None = Field(
        default=None,
        description="Exact semantic title; required only with selector=title.",
    )
    brief: Annotated[
        NonEmptyText,
        StringConstraints(max_length=INTERACTIVE_CANVAS_BRIEF_MAX_CHARACTERS),
    ] = Field(
        description=(
            "The visual purpose, objects, relationships, flow, and labels; no "
            "coordinates, markup, styling, code, or identifiers."
        )
    )
    interaction_mode: InteractiveCanvasMode = Field(
        description=(
            "Semantic mode; exactly none|explore|select|steps. Never use drag, "
            "slider, or timeline here."
        )
    )
    continuous_control: InteractiveCanvasContinuousControl = Field(
        description=(
            "Separate control axis; exactly none|drag|slider|timeline. drag, slider, "
            "and timeline require interactionMode=explore."
        )
    )
    completion: CanvasActionCompletion = Field(
        description=(
            "Required. requestComplete stops after the result; needsResultReasoning "
            "authorizes one inference that requires the result."
        ),
    )

    @model_validator(mode="after")
    def validate_interaction_control_combination(self) -> InteractiveCanvasRequest:
        if self.operation == InteractiveCanvasOperation.NEW:
            if any(
                value is not None
                for value in (self.selector, self.position, self.title)
            ):
                raise ValueError(
                    "selector, position, and title are forbidden when operation is new"
                )
        else:
            selector = CanvasSceneSelector(self.selector or CanvasSceneSelector.CURRENT)
            if selector not in {
                CanvasSceneSelector.CURRENT,
                CanvasSceneSelector.POSITION,
                CanvasSceneSelector.TITLE,
            }:
                raise ValueError("revise selector must be current, position, or title")
            if (selector is CanvasSceneSelector.POSITION) != (
                self.position is not None
            ):
                raise ValueError("position is required only with selector=position")
            if (selector is CanvasSceneSelector.TITLE) != (self.title is not None):
                raise ValueError("title is required only with selector=title")
            if (
                selector is not CanvasSceneSelector.POSITION
                and self.position is not None
            ):
                raise ValueError("position is required only with selector=position")
            if selector is not CanvasSceneSelector.TITLE and self.title is not None:
                raise ValueError("title is required only with selector=title")
        if (
            self.interaction_mode != InteractiveCanvasMode.EXPLORE
            and self.continuous_control != InteractiveCanvasContinuousControl.NONE
        ):
            raise ValueError(
                "continuousControl must be none unless interactionMode is explore"
            )
        return self


class InteractiveCanvasCancellationRequest(RealtimeProtocolModel):
    """Explicit semantic request to stop unpresented canvas work."""

    completion: CanvasActionCompletion = Field(
        description=(
            "Required. requestComplete stops after the result; needsResultReasoning "
            "authorizes one inference that requires the result."
        ),
    )


class ManageCanvasPresentationAction(StrEnum):
    SHOW = "show"
    HIDE = "hide"


class ManageCanvasPresentationIntent(StrEnum):
    HIDE = "hide"
    SHOW_CURRENT = "showCurrent"
    SHOW_NEXT = "showNext"
    SHOW_PREVIOUS = "showPrevious"
    SHOW_FIRST = "showFirst"
    SHOW_LAST = "showLast"
    SHOW_POSITION = "showPosition"
    SHOW_TITLE = "showTitle"


class ManageCanvasPresentationStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ManageCanvasPresentationRequest(RealtimeProtocolModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        validate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )

    action: ManageCanvasPresentationIntent = Field(
        description="One semantic Canvas presentation action. hide has no target.",
    )
    position: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="One-based retained position; required only with action=showPosition.",
    )
    title: Annotated[NonEmptyText, StringConstraints(max_length=160)] | None = Field(
        default=None,
        description="Exact selector-safe title from the retained directory; required only with action=showTitle.",
    )
    completion: CanvasActionCompletion = Field(
        description=(
            "Required. requestComplete stops after the result; needsResultReasoning "
            "authorizes one inference that requires the result."
        ),
    )

    @model_validator(mode="after")
    def validate_action_fields(self) -> ManageCanvasPresentationRequest:
        requires_position = self.action == ManageCanvasPresentationIntent.SHOW_POSITION
        requires_title = self.action == ManageCanvasPresentationIntent.SHOW_TITLE
        if requires_position != (self.position is not None):
            raise ValueError("position is required only with action=showPosition")
        if requires_title != (self.title is not None):
            raise ValueError("title is required only with action=showTitle")
        return self

    @property
    def presentation_operation(self) -> ManageCanvasPresentationAction:
        return (
            ManageCanvasPresentationAction.HIDE
            if self.action == ManageCanvasPresentationIntent.HIDE
            else ManageCanvasPresentationAction.SHOW
        )

    @property
    def presentation_selector(self) -> CanvasSceneSelector | None:
        selectors = {
            ManageCanvasPresentationIntent.SHOW_CURRENT: CanvasSceneSelector.CURRENT,
            ManageCanvasPresentationIntent.SHOW_NEXT: CanvasSceneSelector.NEXT,
            ManageCanvasPresentationIntent.SHOW_PREVIOUS: CanvasSceneSelector.PREVIOUS,
            ManageCanvasPresentationIntent.SHOW_FIRST: CanvasSceneSelector.FIRST,
            ManageCanvasPresentationIntent.SHOW_LAST: CanvasSceneSelector.LAST,
            ManageCanvasPresentationIntent.SHOW_POSITION: CanvasSceneSelector.POSITION,
            ManageCanvasPresentationIntent.SHOW_TITLE: CanvasSceneSelector.TITLE,
        }
        return selectors.get(self.action)


class MemorySearchOrder(StrEnum):
    BEST_MATCH = "bestMatch"
    NEWEST = "newest"
    OLDEST = "oldest"


class SearchMemoryRequest(RealtimeProtocolModel):
    order: MemorySearchOrder = Field(
        default=MemorySearchOrder.BEST_MATCH,
        description="Semantic historical order.",
    )
    query: Annotated[NonEmptyText, StringConstraints(max_length=500)] = Field(
        description=(
            "Historical detail absent from live conversation and supplied references."
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
    source_kind: Annotated[
        NonEmptyText,
        StringConstraints(max_length=SEARCH_MEMORY_SOURCE_KIND_MAX_CHARACTERS),
    ]
    started_at: datetime | None = None
    ended_at: datetime | None = None
    timezone_ids: list[
        Annotated[
            NonEmptyText,
            StringConstraints(max_length=SEARCH_MEMORY_TIMEZONE_ID_MAX_CHARACTERS),
        ]
    ] = Field(max_length=SEARCH_MEMORY_MAX_TIMEZONE_IDS)
    text: Annotated[
        NonEmptyText,
        StringConstraints(max_length=SEARCH_MEMORY_REFERENCE_TEXT_MAX_CHARACTERS),
    ]

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
        default_factory=list, max_length=SEARCH_MEMORY_MAX_REFERENCES
    )
    reason: (
        Annotated[
            str,
            StringConstraints(max_length=SEARCH_MEMORY_REASON_MAX_CHARACTERS),
        ]
        | None
    ) = None

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
    local_iso_date_time: Annotated[
        NonEmptyText,
        StringConstraints(max_length=CURRENT_TIME_ISO_MAX_CHARACTERS),
    ]
    weekday: Annotated[
        NonEmptyText,
        StringConstraints(max_length=CURRENT_TIME_WEEKDAY_MAX_CHARACTERS),
    ]
    daypart: CurrentTimeDaypart
    timezone_id: Annotated[
        NonEmptyText,
        StringConstraints(max_length=CURRENT_TIME_TIMEZONE_MAX_CHARACTERS),
    ]


class RejectedUtilityToolOutputV4(RealtimeProtocolModel):
    status: Annotated[str, StringConstraints(pattern="^rejected$")] = "rejected"
    reason: Annotated[
        NonEmptyText,
        StringConstraints(max_length=UTILITY_REJECTION_REASON_MAX_CHARACTERS),
    ]


type RealtimeUtilityToolOutputV4 = (
    SearchMemoryToolOutputV4 | GetCurrentTimeToolOutputV4 | RejectedUtilityToolOutputV4
)


class InteractiveCanvasToolStatusV4(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class InteractiveCanvasToolOutputV4(RealtimeProtocolModel):
    """Immediate function result; SVG generation is always asynchronous.

    ``generation_id`` is retained only while the backend schedules the accepted
    job.  It is deliberately excluded from the provider-visible function result.
    """

    status: InteractiveCanvasToolStatusV4
    generation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        exclude=True,
    )
    reason: (
        Annotated[
            str,
            StringConstraints(max_length=CANVAS_TOOL_OUTPUT_REASON_MAX_CHARACTERS),
        ]
        | None
    ) = None

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
    reason: (
        Annotated[
            str,
            StringConstraints(max_length=CANVAS_TOOL_OUTPUT_REASON_MAX_CHARACTERS),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def status_fields_are_atomic(self) -> InteractiveCanvasCancellationToolOutputV4:
        if self.status == InteractiveCanvasToolStatusV4.ACCEPTED:
            if self.reason is not None:
                raise ValueError("accepted canvas cancellation cannot include reason")
        elif not self.reason:
            raise ValueError("rejected canvas cancellation requires reason")
        return self


class ManageCanvasPresentationToolOutputV4(RealtimeProtocolModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        validate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )

    status: ManageCanvasPresentationStatus
    operation: ManageCanvasPresentationAction
    changed: bool = False
    visible: bool | None = None
    position: int | None = Field(default=None, ge=1, le=10)
    total: int | None = Field(default=None, ge=0, le=10)
    has_previous: bool | None = None
    has_next: bool | None = None
    title: Annotated[str, StringConstraints(max_length=160)] | None = None
    reason: (
        Annotated[
            str,
            StringConstraints(max_length=CANVAS_TOOL_OUTPUT_REASON_MAX_CHARACTERS),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_atomic_status(self) -> ManageCanvasPresentationToolOutputV4:
        if self.status == ManageCanvasPresentationStatus.ACCEPTED:
            if self.reason is not None:
                raise ValueError(
                    "accepted canvas presentation output cannot include reason"
                )
        else:
            if not self.reason:
                raise ValueError("rejected canvas presentation output requires reason")
        return self


def _serialized_tool_output_utf8_bytes(output: RealtimeProtocolModel) -> int:
    return len(output.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8"))


def _maximum_search_memory_output_utf8_bytes() -> int:
    # U+0001 forces JSON's longest six-byte character escape, so this concrete
    # valid model measures the contract's conservative UTF-8 serialization bound
    # without introducing a tokenizer or an unrelated policy constant.
    escaped = "\u0001"
    reference = SearchMemoryReferenceV4.model_validate(
        {
            "sourceKind": escaped * SEARCH_MEMORY_SOURCE_KIND_MAX_CHARACTERS,
            "startedAt": datetime.max.replace(tzinfo=UTC),
            "endedAt": datetime.max.replace(tzinfo=UTC),
            "timezoneIds": [
                escaped * SEARCH_MEMORY_TIMEZONE_ID_MAX_CHARACTERS
                for _ in range(SEARCH_MEMORY_MAX_TIMEZONE_IDS)
            ],
            "text": escaped * SEARCH_MEMORY_REFERENCE_TEXT_MAX_CHARACTERS,
        }
    )
    return _serialized_tool_output_utf8_bytes(
        SearchMemoryToolOutputV4(
            status=SearchMemoryToolStatus.AVAILABLE,
            references=[reference] * SEARCH_MEMORY_MAX_REFERENCES,
        )
    )


MAX_SEARCH_MEMORY_TOOL_OUTPUT_UTF8_BYTES = _maximum_search_memory_output_utf8_bytes()
MAX_TERMINAL_TOOL_OUTPUT_UTF8_BYTES = max(
    _serialized_tool_output_utf8_bytes(
        InteractiveCanvasToolOutputV4(
            status=InteractiveCanvasToolStatusV4.REJECTED,
            reason="\u0001" * CANVAS_TOOL_OUTPUT_REASON_MAX_CHARACTERS,
        )
    ),
    _serialized_tool_output_utf8_bytes(
        InteractiveCanvasCancellationToolOutputV4(
            status=InteractiveCanvasToolStatusV4.REJECTED,
            reason="\u0001" * CANVAS_TOOL_OUTPUT_REASON_MAX_CHARACTERS,
        )
    ),
    _serialized_tool_output_utf8_bytes(
        ManageCanvasPresentationToolOutputV4(
            status=ManageCanvasPresentationStatus.REJECTED,
            operation=ManageCanvasPresentationAction.HIDE,
            reason="\u0001" * CANVAS_TOOL_OUTPUT_REASON_MAX_CHARACTERS,
        )
    ),
)
MAX_PRIMARY_TOOL_OUTPUT_UTF8_BYTES = max(
    MAX_SEARCH_MEMORY_TOOL_OUTPUT_UTF8_BYTES,
    MAX_TERMINAL_TOOL_OUTPUT_UTF8_BYTES,
    _serialized_tool_output_utf8_bytes(
        GetCurrentTimeToolOutputV4.model_validate(
            {
                "localIsoDateTime": "\u0001" * CURRENT_TIME_ISO_MAX_CHARACTERS,
                "weekday": "\u0001" * CURRENT_TIME_WEEKDAY_MAX_CHARACTERS,
                "daypart": CurrentTimeDaypart.AFTERNOON,
                "timezoneId": "\u0001" * CURRENT_TIME_TIMEZONE_MAX_CHARACTERS,
            }
        )
    ),
    _serialized_tool_output_utf8_bytes(
        RejectedUtilityToolOutputV4(
            reason="\u0001" * UTILITY_REJECTION_REASON_MAX_CHARACTERS
        )
    ),
)


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
    request: InteractiveCanvasCancellationRequest

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
class RealtimeManageCanvasPresentationToolCall:
    call_id: str
    request: ManageCanvasPresentationRequest
    provider_response_id: str | None = None

    def __post_init__(self) -> None:
        if not _CALL_ID.fullmatch(self.call_id):
            raise ValueError("Realtime canvas call_id has an invalid format")
        if self.provider_response_id is not None and not self.provider_response_id:
            raise ValueError("Realtime provider response ID cannot be empty")


@dataclass(frozen=True, slots=True)
class RealtimeRejectedManageCanvasPresentationToolCall:
    call_id: str
    reason: str
    provider_response_id: str | None = None

    def __post_init__(self) -> None:
        if not _CALL_ID.fullmatch(self.call_id):
            raise ValueError("Realtime canvas call_id has an invalid format")
        if not self.reason or len(self.reason) > 240:
            raise ValueError("Realtime canvas rejection reason is invalid")
        if self.provider_response_id is not None and not self.provider_response_id:
            raise ValueError("Realtime provider response ID cannot be empty")


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
    | RealtimeManageCanvasPresentationToolCall
    | RealtimeRejectedManageCanvasPresentationToolCall
    | RealtimeSearchMemoryToolCall
    | RealtimeGetCurrentTimeToolCall
    | RealtimeRejectedUtilityToolCall
)
