from __future__ import annotations

from dataclasses import dataclass

from .events import (
    AlignmentBatchEvent,
    AudioChunkEvent,
    AudioMetadataEvent,
    AvaServerEventUnion,
    AvaTurnPhase,
    CanvasCueEvent,
    CanvasRejectedEvent,
    InteractionRejectedEvent,
    MemoryPersistenceFailedEvent,
    SegmentReadyEvent,
    TurnCancelledEvent,
    TurnCompletedEvent,
    TurnCreatedEvent,
    TurnFailedEvent,
    TurnPhaseChangedEvent,
)


class RealtimeStateError(RuntimeError):
    """Raised when a protocol-v3 event violates deterministic turn ordering."""


_NEXT_PHASES: dict[AvaTurnPhase, frozenset[AvaTurnPhase]] = {
    AvaTurnPhase.CREATED: frozenset({AvaTurnPhase.CONNECTING, AvaTurnPhase.CANCELLING}),
    AvaTurnPhase.CONNECTING: frozenset(
        {AvaTurnPhase.LISTENING, AvaTurnPhase.CANCELLING}
    ),
    AvaTurnPhase.LISTENING: frozenset({AvaTurnPhase.PLANNING, AvaTurnPhase.CANCELLING}),
    AvaTurnPhase.PLANNING: frozenset(
        {AvaTurnPhase.SYNTHESIZING, AvaTurnPhase.CANCELLING}
    ),
    AvaTurnPhase.SYNTHESIZING: frozenset(
        {AvaTurnPhase.SPEAKING, AvaTurnPhase.CANCELLING}
    ),
    AvaTurnPhase.SPEAKING: frozenset({AvaTurnPhase.CANCELLING}),
    AvaTurnPhase.CANCELLING: frozenset({AvaTurnPhase.CANCELLED}),
    AvaTurnPhase.COMPLETED: frozenset(),
    AvaTurnPhase.CANCELLED: frozenset(),
    AvaTurnPhase.FAILED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class RealtimeTurnSnapshot:
    session_id: str | None
    turn_id: str | None
    phase: AvaTurnPhase | None
    revision: int
    next_sequence: int
    segment_count: int
    received_sample_count: int
    terminal: bool


class RealtimeTurnStateMachine:
    """Validates one server event stream without performing side effects."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._turn_id: str | None = None
        self._phase: AvaTurnPhase | None = None
        self._revision = -1
        self._next_sequence = 0
        self._next_segment_index = 0
        self._audio_stream_id: str | None = None
        self._next_audio_chunk_index = 0
        self._received_sample_count = 0
        self._cue_ids: set[str] = set()
        self._cancel_request_id: str | None = None
        self._terminal = False

    @property
    def snapshot(self) -> RealtimeTurnSnapshot:
        return RealtimeTurnSnapshot(
            session_id=self._session_id,
            turn_id=self._turn_id,
            phase=self._phase,
            revision=self._revision,
            next_sequence=self._next_sequence,
            segment_count=self._next_segment_index,
            received_sample_count=self._received_sample_count,
            terminal=self._terminal,
        )

    def accept(self, event: AvaServerEventUnion) -> RealtimeTurnSnapshot:
        if isinstance(event, InteractionRejectedEvent):
            return self._accept_session_interaction_rejection(event)
        if self._terminal:
            raise RealtimeStateError("event received after terminal turn state")
        if event.sequence != self._next_sequence:
            raise RealtimeStateError(
                f"expected event sequence {self._next_sequence}, "
                f"received {event.sequence}"
            )

        if self._session_id is None:
            if not isinstance(event, TurnCreatedEvent):
                raise RealtimeStateError("turnCreated must be the first event")
            self._session_id = event.session_id
            self._turn_id = event.turn_id
            if event.revision != 0:
                raise RealtimeStateError("turnCreated revision must be zero")
            self._phase = AvaTurnPhase.CREATED
            self._revision = 0
            self._next_sequence += 1
            return self.snapshot

        if event.session_id != self._session_id or event.turn_id != self._turn_id:
            raise RealtimeStateError("sessionId or turnId changed mid-turn")

        if isinstance(event, TurnPhaseChangedEvent):
            self._accept_phase_change(event)
        elif isinstance(event, TurnCompletedEvent):
            self._accept_completed(event)
        elif isinstance(event, TurnCancelledEvent):
            self._accept_cancelled(event)
        elif isinstance(event, TurnFailedEvent):
            self._accept_failed(event)
        else:
            if event.revision != self._revision:
                raise RealtimeStateError(
                    f"event revision {event.revision} does not match active "
                    f"revision {self._revision}"
                )
            self._accept_non_phase_event(event)

        self._next_sequence += 1
        return self.snapshot

    def _accept_session_interaction_rejection(
        self,
        event: InteractionRejectedEvent,
    ) -> RealtimeTurnSnapshot:
        """Advance ordering without making a canvas warning turn-terminal."""

        if self._session_id is None or self._turn_id is None:
            raise RealtimeStateError("interactionRejected cannot precede turnCreated")
        if event.sequence != self._next_sequence:
            raise RealtimeStateError(
                f"expected event sequence {self._next_sequence}, "
                f"received {event.sequence}"
            )
        if event.session_id != self._session_id or event.turn_id != self._turn_id:
            raise RealtimeStateError("sessionId or turnId changed mid-turn")
        if event.revision != self._revision:
            raise RealtimeStateError(
                "interactionRejected revision does not match active revision"
            )
        self._next_sequence += 1
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

    def _accept_phase_change(self, event: TurnPhaseChangedEvent) -> None:
        current = self._require_phase()
        next_phase = AvaTurnPhase(event.phase)
        if next_phase not in _NEXT_PHASES[current]:
            raise RealtimeStateError(
                f"invalid turn phase transition {current} -> {next_phase}"
            )
        if event.revision != self._revision + 1:
            raise RealtimeStateError("phase changes must increment revision by one")
        if next_phase == AvaTurnPhase.SPEAKING and self._audio_stream_id is None:
            raise RealtimeStateError("speaking requires prior audioMetadata")
        self._phase = next_phase
        self._revision = event.revision

    def _accept_non_phase_event(self, event: AvaServerEventUnion) -> None:
        phase = self._require_phase()
        if isinstance(event, SegmentReadyEvent):
            if phase not in {
                AvaTurnPhase.PLANNING,
                AvaTurnPhase.SYNTHESIZING,
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

        if isinstance(event, AudioMetadataEvent):
            if phase != AvaTurnPhase.SYNTHESIZING:
                raise RealtimeStateError("audioMetadata requires synthesizing phase")
            if self._audio_stream_id is not None:
                raise RealtimeStateError("audioMetadata may occur only once")
            self._audio_stream_id = event.stream_id
            return

        if isinstance(event, AudioChunkEvent):
            if phase != AvaTurnPhase.SYNTHESIZING:
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

        if isinstance(event, AlignmentBatchEvent):
            if phase != AvaTurnPhase.SYNTHESIZING:
                raise RealtimeStateError("alignmentBatch requires synthesizing phase")
            self._require_audio_stream(event.stream_id)
            return

        if isinstance(event, CanvasCueEvent):
            if phase not in {
                AvaTurnPhase.SYNTHESIZING,
                AvaTurnPhase.SPEAKING,
            }:
                raise RealtimeStateError(
                    "canvasCue requires synthesizing or speaking phase"
                )
            if event.cue_id in self._cue_ids:
                raise RealtimeStateError("canvas cue ids must not repeat")
            if event.sample_offset > self._received_sample_count:
                raise RealtimeStateError(
                    "canvas cue sampleOffset cannot exceed received PCM"
                )
            self._cue_ids.add(event.cue_id)
            return

        if isinstance(event, CanvasRejectedEvent):
            if phase not in {
                AvaTurnPhase.SYNTHESIZING,
                AvaTurnPhase.SPEAKING,
            }:
                raise RealtimeStateError(
                    "canvasRejected requires synthesizing or speaking phase"
                )
            return

        if isinstance(event, MemoryPersistenceFailedEvent):
            if phase not in {AvaTurnPhase.LISTENING, AvaTurnPhase.SPEAKING}:
                raise RealtimeStateError(
                    "memoryPersistenceFailed requires listening or speaking phase"
                )
            return

        raise RealtimeStateError(
            f"unsupported non-phase event type {type(event).__name__}"
        )

    def _accept_completed(self, event: TurnCompletedEvent) -> None:
        if self._require_phase() != AvaTurnPhase.SPEAKING:
            raise RealtimeStateError("turnCompleted requires speaking phase")
        self._accept_terminal_revision(event.revision)
        self._validate_delivered_samples(event.delivered_sample_count)
        if self._next_audio_chunk_index == 0:
            raise RealtimeStateError("turnCompleted requires received audio")
        self._phase = AvaTurnPhase.COMPLETED
        self._terminal = True

    def _accept_cancelled(self, event: TurnCancelledEvent) -> None:
        if self._require_phase() != AvaTurnPhase.CANCELLING:
            raise RealtimeStateError("turnCancelled requires cancelling phase")
        self._accept_terminal_revision(event.revision)
        self._validate_delivered_samples(event.delivered_sample_count)
        self._phase = AvaTurnPhase.CANCELLED
        self._terminal = True

    def _accept_failed(self, event: TurnFailedEvent) -> None:
        if self._require_phase() in {
            AvaTurnPhase.COMPLETED,
            AvaTurnPhase.CANCELLED,
            AvaTurnPhase.FAILED,
        }:
            raise RealtimeStateError("terminal turn cannot fail again")
        self._accept_terminal_revision(event.revision)
        self._validate_delivered_samples(event.delivered_sample_count)
        self._phase = AvaTurnPhase.FAILED
        self._terminal = True

    def _accept_terminal_revision(self, revision: int) -> None:
        if revision != self._revision + 1:
            raise RealtimeStateError("terminal events must increment revision by one")
        self._revision = revision

    def _validate_delivered_samples(self, delivered: int) -> None:
        if delivered > self._received_sample_count:
            raise RealtimeStateError("delivered samples cannot exceed received samples")

    def _require_phase(self) -> AvaTurnPhase:
        if self._phase is None:
            raise RealtimeStateError("turn state has not been created")
        return self._phase

    def _require_audio_stream(self, stream_id: str) -> None:
        if self._audio_stream_id is None:
            raise RealtimeStateError("audio event requires audioMetadata")
        if stream_id != self._audio_stream_id:
            raise RealtimeStateError("audio streamId changed mid-turn")
