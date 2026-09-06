from __future__ import annotations

import re
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..canvas.svg_workspace import RecordedSvgCanvasInteraction
from .harness import RealtimeHarness
from .tool_contract import (
    InteractiveCanvasCancellationToolOutputV4,
    InteractiveCanvasToolOutput,
    InteractiveCanvasToolOutputV4,
    ManageCanvasPresentationToolOutputV4,
    RealtimeUtilityToolOutputV4,
    RealtimeTurnEvent,
)


@dataclass(frozen=True, slots=True)
class RealtimeProviderInputEvent:
    """A provider input lifecycle signal observed outside turn parsing."""

    event_type: str
    item_id: str
    audio_start_ms: int | None = None


MAX_SDP_BYTES = 256 * 1024
_PROVIDER_CALL_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{7,127}$")


class RealtimeContextInstallationStatus(StrEnum):
    INSTALLED = "installed"
    DEDUPLICATED = "deduplicated"
    SUBMITTED = "submitted"


class RealtimeProviderError(RuntimeError):
    """Base error raised while consuming an established provider session."""


class RealtimeProviderProtocolError(RealtimeProviderError):
    """The provider emitted data or ordering Ava cannot safely consume."""


class RealtimeProviderUnavailableError(RealtimeProviderError):
    """The provider session could not complete the active Ava turn."""


class RealtimeProviderResponseIncompleteError(RealtimeProviderError):
    """The provider ended one response without completing its output."""

    def __init__(self, *, reason: str) -> None:
        super().__init__(f"Realtime provider response was incomplete: {reason}")
        self.reason = reason


class RealtimeNegotiationError(RuntimeError):
    """Base error for a failed provider WebRTC negotiation."""


class RealtimeNegotiationUnavailableError(RealtimeNegotiationError):
    """The provider could not be reached or could not accept a new call."""

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        error_type: str | None = None,
        provider_status_code: int | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.error_type = error_type
        self.provider_status_code = provider_status_code
        self.provider_request_id = provider_request_id


class RealtimeNegotiationResponseError(RealtimeNegotiationError):
    """The provider returned an invalid or unsuccessful SDP response."""

    def __init__(
        self,
        message: str,
        *,
        provider_status_code: int | None = None,
        provider_error_code: str | None = None,
        provider_error_parameter: str | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_status_code = provider_status_code
        self.provider_error_code = provider_error_code
        self.provider_error_parameter = provider_error_parameter
        self.provider_request_id = provider_request_id


@dataclass(frozen=True, slots=True)
class NegotiatedRealtimeCall:
    answer_sdp: str
    provider_call_id: str

    def __post_init__(self) -> None:
        validate_sdp(self.answer_sdp, description="answer")
        if not _PROVIDER_CALL_ID.fullmatch(self.provider_call_id):
            raise ValueError("provider_call_id has an invalid format")


@dataclass(frozen=True, slots=True)
class RealtimeCommittedInput:
    turn_id: str
    item_id: str


type RealtimeProviderTurnEvent = RealtimeTurnEvent | RealtimeCommittedInput


class RealtimeProviderSession(Protocol):
    """One OpenAI Realtime media/sideband session owned by Ava's backend."""

    async def negotiate(self, *, offer_sdp: str) -> NegotiatedRealtimeCall: ...

    async def request_child_response(
        self,
        *,
        turn_id: str,
        item_id: str,
        permit_degraded_focus_grounding: bool = False,
    ) -> bool: ...

    async def wait_for_context(self, *, build_id: str) -> None: ...

    @property
    def active_context_build_id(self) -> str | None: ...

    @property
    def desired_context_build_id(self) -> str | None: ...

    def is_context_acknowledged(self, build_id: str) -> bool: ...

    async def prepare(
        self,
        *,
        harness: RealtimeHarness,
    ) -> RealtimeContextInstallationStatus:
        """Install Ava's server-owned instructions and optional tools."""
        ...

    async def prepare_if_idle(
        self,
        *,
        harness: RealtimeHarness,
        turn_id: str,
    ) -> RealtimeContextInstallationStatus | None:
        """Install context only if the exact listening turn is still idle."""
        ...

    def turn_events(
        self,
        *,
        harness: RealtimeHarness,
        turn_id: str,
    ) -> AsyncIterator[RealtimeProviderTurnEvent]: ...

    async def next_input_event(self) -> RealtimeProviderInputEvent: ...

    def input_item_owner(self, *, item_id: str) -> str | None: ...

    async def admit_pending_input_items(self, *, turn_id: str) -> tuple[str, ...]: ...

    async def complete_canvas_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: (
            InteractiveCanvasToolOutput
            | InteractiveCanvasToolOutputV4
            | InteractiveCanvasCancellationToolOutputV4
        ),
    ) -> None: ...

    async def complete_manage_canvas_presentation_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: ManageCanvasPresentationToolOutputV4,
    ) -> None: ...

    async def complete_utility_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: RealtimeUtilityToolOutputV4,
        harness: RealtimeHarness,
    ) -> None: ...

    async def request_canvas_interaction_response(
        self,
        *,
        turn_id: str,
        interaction: RecordedSvgCanvasInteraction,
    ) -> bool:
        """Trigger one idle semantic response without inventing child input."""
        ...

    async def wait_until_canvas_interaction_response_ready(
        self,
        *,
        turn_id: str,
    ) -> bool:
        """Wait until the exact turn can safely own an interaction response."""
        ...

    async def child_transcript(self, *, turn_id: str) -> str | None: ...

    def available_child_transcript(self, *, turn_id: str) -> str | None:
        """Return an already-indexed owned transcript without receiving events."""
        ...

    def wait_for_child_transcript(
        self,
        *,
        turn_id: str,
    ) -> Awaitable[str | None]:
        """Wait for this turn's terminal committed-input transcription."""
        ...

    async def cancel(self, *, turn_id: str) -> None: ...

    async def close(self) -> None: ...


def validate_sdp(value: str, *, description: str) -> str:
    """Validate a bounded audio SDP without attempting to parse every RFC field."""

    if not isinstance(value, str):
        raise TypeError(f"{description} SDP must be text")
    if not value or len(value.encode("utf-8")) > MAX_SDP_BYTES:
        raise ValueError(
            f"{description} SDP must contain between 1 and {MAX_SDP_BYTES} bytes"
        )
    if "\x00" in value:
        raise ValueError(f"{description} SDP cannot contain NUL bytes")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "v=0":
        raise ValueError(f"{description} SDP must begin with v=0")
    if not any(line.startswith("m=audio ") for line in lines):
        raise ValueError(f"{description} SDP must contain an audio media section")
    if not any(line.startswith("m=application ") for line in lines):
        raise ValueError(
            f"{description} SDP must contain a Realtime data-channel section"
        )
    return value
