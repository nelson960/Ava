from __future__ import annotations

from dataclasses import dataclass

from .events_v4 import (
    AlignmentBatchEventV4,
    AudioChunkEventV4,
    AudioMetadataEventV4,
    AvaServerEventUnionV4,
    InterruptRequestedEventV4,
    TurnContextRequestedEventV4,
    MemoryPersistenceFailedEventV4,
    SegmentReadyEventV4,
    TurnCancelledEventV4,
    TurnCompletedEventV4,
    TurnCreatedEventV4,
    TurnFailedEventV4,
    TurnPhaseChangedEventV4,
)
from .speech_contract import SpeechPhase


class RealtimeStateError(RuntimeError):
    """Raised when a protocol-v4 event violates deterministic turn ordering."""


_NEXT_PHASES: dict[SpeechPhase, frozenset[SpeechPhase]] = {
    SpeechPhase.CREATED: frozenset({SpeechPhase.CONNECTING, SpeechPhase.CANCELLING}),
    SpeechPhase.CONNECTING: frozenset({SpeechPhase.LISTENING, SpeechPhase.CANCELLING}),
    SpeechPhase.LISTENING: frozenset({SpeechPhase.PLANNING, SpeechPhase.CANCELLING}),
    SpeechPhase.PLANNING: frozenset({SpeechPhase.SYNTHESIZING, SpeechPhase.CANCELLING}),
    SpeechPhase.SYNTHESIZING: frozenset({SpeechPhase.SPEAKING, SpeechPhase.CANCELLING}),
    SpeechPhase.SPEAKING: frozenset({SpeechPhase.CANCELLING}),
    SpeechPhase.CANCELLING: frozenset({SpeechPhase.CANCELLED}),
    SpeechPhase.COMPLETED: frozenset(),
    SpeechPhase.CANCELLED: frozenset(),
    SpeechPhase.FAILED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class RealtimeTurnSnapshot:
    session_id: str | None
    turn_id: str | None
    phase: SpeechPhase | None
    revision: int
    segment_count: int
    received_sample_count: int
    terminal: bool


class RealtimeTurnStateMachine:
    """Validates one server event stream without performing side effects."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._turn_id: str | None = None
        self._phase: SpeechPhase | None = None
        self._revision = -1
        self._next_segment_index = 0
        self._audio_stream_id: str | None = None
        self._next_audio_chunk_index = 0
        self._received_sample_count = 0
        self._cancel_request_id: str | None = None
        self._terminal = False

    @property
    def snapshot(self) -> RealtimeTurnSnapshot:
        return RealtimeTurnSnapshot(
            session_id=self._session_id,
            turn_id=self._turn_id,
            phase=self._phase,
            revision=self._revision,
            segment_count=self._next_segment_index,
            received_sample_count=self._received_sample_count,
            terminal=self._terminal,
        )

    def accept(self, event: AvaServerEventUnionV4) -> RealtimeTurnSnapshot:
        if self._terminal:
            raise RealtimeStateError("event received after terminal turn state")

        if self._session_id is None:
            if not isinstance(event, TurnCreatedEventV4):
                raise RealtimeStateError("turnCreated must be the first event")
            self._session_id = event.session_id
            self._turn_id = event.turn_id
            if event.revision != 0:
                raise RealtimeStateError("turnCreated revision must be zero")
            self._phase = SpeechPhase.CREATED
            self._revision = 0
            return self.snapshot

        event_turn_id = getattr(event, "turn_id", None)
        if event.session_id != self._session_id or event_turn_id != self._turn_id:
            raise RealtimeStateError("sessionId or turnId changed mid-turn")

        if isinstance(event, TurnPhaseChangedEventV4):
            self._accept_phase_change(event)
        elif isinstance(event, TurnCompletedEventV4):
            self._accept_completed(event)
        elif isinstance(event, TurnCancelledEventV4):
            self._accept_cancelled(event)
        elif isinstance(event, TurnFailedEventV4):
            self._accept_failed(event)
        else:
            if event.revision != self._revision:
                raise RealtimeStateError(
                    f"event revision {event.revision} does not match active "
                    f"revision {self._revision}"
                )
            self._accept_non_phase_event(event)

        return self.snapshot

    def register_cancellation(self, request_id: str) -> bool:
        """Register a cancel command, returning false for an exact replay."""

        if not request_id:
            raise RealtimeStateError("cancel requestId cannot be empty")
        if self._cancel_request_id is not None:
            if self._cancel_request_id == request_id:
                return False
            raise RealtimeStateError(
                "a different cancellation request is already registered"
            )
        if self._terminal:
            raise RealtimeStateError("cannot cancel a terminal turn")
        self._require_phase()
        self._cancel_request_id = request_id
        return True

    def _accept_phase_change(self, event: TurnPhaseChangedEventV4) -> None:
        current = self._require_phase()
        next_phase = SpeechPhase(event.phase)
        if next_phase not in _NEXT_PHASES[current]:
            raise RealtimeStateError(
                f"invalid turn phase transition {current} -> {next_phase}"
            )
        if event.revision != self._revision + 1:
            raise RealtimeStateError("phase changes must increment revision by one")
        if next_phase == SpeechPhase.SPEAKING and self._audio_stream_id is None:
            raise RealtimeStateError("speaking requires prior audioMetadata")
        self._phase = next_phase
        self._revision = event.revision

    def _accept_non_phase_event(self, event: AvaServerEventUnionV4) -> None:
        phase = self._require_phase()
        if isinstance(event, TurnContextRequestedEventV4):
            if phase != SpeechPhase.LISTENING:
                raise RealtimeStateError("turn context requires listening")
            return
        if isinstance(event, SegmentReadyEventV4):
            if phase not in {
                SpeechPhase.PLANNING,
                SpeechPhase.SYNTHESIZING,
            }:
                raise RealtimeStateError(
                    "segmentReady requires planning or synthesizing phase"
                )
            if event.segment_index != self._next_segment_index:
                raise RealtimeStateError(
                    f"expected segmentIndex {self._next_segment_index}, "
                    f"received {event.segment_index}"
                )
            self._next_segment_index += 1
            return

        if isinstance(event, AudioMetadataEventV4):
            if phase != SpeechPhase.SYNTHESIZING:
                raise RealtimeStateError("audioMetadata requires synthesizing phase")
            if self._audio_stream_id is not None:
                raise RealtimeStateError("audioMetadata may occur only once")
            self._audio_stream_id = event.stream_id
            return

        if isinstance(event, AudioChunkEventV4):
            if phase != SpeechPhase.SYNTHESIZING:
                raise RealtimeStateError("audioChunk requires synthesizing phase")
            self._require_audio_stream(event.stream_id)
            if event.chunk_index != self._next_audio_chunk_index:
                raise RealtimeStateError(
                    f"expected audio chunk {self._next_audio_chunk_index}, "
                    f"received {event.chunk_index}"
                )
            if event.sample_offset != self._received_sample_count:
                raise RealtimeStateError(
                    f"expected sampleOffset {self._received_sample_count}, "
                    f"received {event.sample_offset}"
                )
            self._next_audio_chunk_index += 1
            self._received_sample_count += event.sample_count
            return

        if isinstance(event, AlignmentBatchEventV4):
            if phase != SpeechPhase.SYNTHESIZING:
                raise RealtimeStateError("alignmentBatch requires synthesizing phase")
            self._require_audio_stream(event.stream_id)
            return

        if isinstance(event, InterruptRequestedEventV4):
            if phase not in {SpeechPhase.SYNTHESIZING, SpeechPhase.SPEAKING}:
                raise RealtimeStateError(
                    "interruptRequested requires synthesizing or speaking phase"
                )
            self._require_audio_stream(event.stream_id)
            return

        if isinstance(event, MemoryPersistenceFailedEventV4):
            if phase not in {SpeechPhase.LISTENING, SpeechPhase.SPEAKING}:
                raise RealtimeStateError(
                    "memoryPersistenceFailed requires listening or speaking phase"
                )
            return

        raise RealtimeStateError(
            f"unsupported non-phase event type {type(event).__name__}"
        )

    def _accept_completed(self, event: TurnCompletedEventV4) -> None:
        phase = self._require_phase()
        silent_action = phase == SpeechPhase.PLANNING
        if phase not in {SpeechPhase.PLANNING, SpeechPhase.SPEAKING}:
            raise RealtimeStateError(
                "turnCompleted requires speaking or owned planning-only action phase"
            )
        self._accept_terminal_revision(event.revision)
        self._validate_delivered_samples(event.delivered_sample_count)
        if silent_action:
            if (
                self._next_segment_index != 0
                or self._audio_stream_id is not None
                or self._next_audio_chunk_index != 0
                or self._received_sample_count != 0
                or event.delivered_sample_count != 0
                or event.delivered_text
            ):
                raise RealtimeStateError(
                    "planning-only completion requires empty text and zero speech/audio"
                )
        elif self._next_audio_chunk_index == 0:
            raise RealtimeStateError("turnCompleted requires received audio")
        self._phase = SpeechPhase.COMPLETED
        self._terminal = True

    def _accept_cancelled(self, event: TurnCancelledEventV4) -> None:
        if self._require_phase() != SpeechPhase.CANCELLING:
            raise RealtimeStateError("turnCancelled requires cancelling phase")
        self._accept_terminal_revision(event.revision)
        self._validate_delivered_samples(event.delivered_sample_count)
        self._phase = SpeechPhase.CANCELLED
        self._terminal = True

    def _accept_failed(self, event: TurnFailedEventV4) -> None:
        if self._require_phase() in {
            SpeechPhase.COMPLETED,
            SpeechPhase.CANCELLED,
            SpeechPhase.FAILED,
        }:
            raise RealtimeStateError("terminal turn cannot fail again")
        self._accept_terminal_revision(event.revision)
        self._validate_delivered_samples(event.delivered_sample_count)
        self._phase = SpeechPhase.FAILED
        self._terminal = True

    def _accept_terminal_revision(self, revision: int) -> None:
        if revision != self._revision + 1:
            raise RealtimeStateError("terminal events must increment revision by one")
        self._revision = revision

    def _validate_delivered_samples(self, delivered: int) -> None:
        if delivered > self._received_sample_count:
            raise RealtimeStateError("delivered samples cannot exceed received samples")

    def _require_phase(self) -> SpeechPhase:
        if self._phase is None:
            raise RealtimeStateError("turn state has not been created")
        return self._phase

    def _require_audio_stream(self, stream_id: str) -> None:
        if self._audio_stream_id is None:
            raise RealtimeStateError("audio event requires audioMetadata")
        if stream_id != self._audio_stream_id:
            raise RealtimeStateError("audio streamId changed mid-turn")
