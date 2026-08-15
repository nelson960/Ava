from __future__ import annotations

import asyncio
import logging
from builtins import anext
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass
from hashlib import sha256
from time import monotonic
from typing import Protocol, TypeAlias, cast

from ..canvas.specialist import (
    CanvasSpecialist,
    CanvasSpecialistError,
    CanvasSpecialistGeneration,
    CanvasSpecialistInvalidError,
    CanvasSpecialistRequest,
    CanvasSpecialistUnavailableError,
)
from ..canvas.session import (
    CanvasInteractionRejected,
    CanvasInteractionRejectionCode,
    CanvasSessionState,
    PreparedCanvasDraft,
)
from ..diagnostics import new_failure_id
from ..protocol import AgeBand
from .bounded_queue import BoundedAsyncQueue, QueueClosed
from .delivery import (
    DeliveredTextLedger,
    SpeechDeliveryError,
    StreamingSpeechSynthesizer,
    SynthesizedAlignmentBatch,
    SynthesizedAudioChunk,
)
from .events import (
    AVA_SERVER_EVENT_ADAPTER,
    PROTOCOL_V3,
    AlignmentBatchEvent,
    AudioChunkEvent,
    AudioMetadataEvent,
    AvaAlignmentSpan,
    AvaClientEventUnion,
    AvaFailureCode,
    AvaFailureComponent,
    AvaServerEventUnion,
    AvaTurnPhase,
    CancelTurnEvent,
    CanvasInteractionEvent,
    ClientReadyEvent,
    InteractionRejectedEvent,
    PlaybackProgressEvent,
    PlaybackStartedEvent,
    PlaybackStoppedEvent,
)
from .harness import RealtimeHarness, build_realtime_harness
from .memory import (
    AuthoritativeMemoryStore,
    AuthoritativeMemorySnapshot,
    ChildTurnTextSource,
    DeliveredConversationTurn,
    DeliveredTurnTerminal,
    MemoryProposalDecision,
)
from .plan import AvaTurnPlan, AvaTurnSegment, CanvasComplexity
from .provider_session import (
    NegotiatedRealtimeCall,
    RealtimeProviderTurnEvent,
    RealtimeProviderSession,
)
from .state_machine import RealtimeStateError, RealtimeTurnStateMachine
from .tool_contract import (
    InteractiveCanvasRequest,
    InteractiveCanvasToolOutput,
    InteractiveCanvasToolStatus,
    RealtimeCanvasToolCall,
    RealtimeRejectedCanvasToolCall,
    RealtimeSpeechTextDelta,
)
from .turn_parser import StreamingSpeechParser, StreamingTurnParseError

LOGGER = logging.getLogger("ava_backend.realtime_coordinator")


class CoordinatorStateError(RuntimeError):
    """Raised when a client command violates coordinator ownership or ordering."""


@dataclass(frozen=True, slots=True)
class AvaServerControlFrame:
    event: AvaServerEventUnion


@dataclass(frozen=True, slots=True)
class AvaServerPcmFrame:
    """Binary frame that immediately follows its audioChunk control descriptor."""

    descriptor: AudioChunkEvent
    pcm: bytes

    def __post_init__(self) -> None:
        if len(self.pcm) != self.descriptor.byte_length:
            raise ValueError("binary PCM length does not match audioChunk byteLength")


AvaServerOutboundFrame: TypeAlias = AvaServerControlFrame | AvaServerPcmFrame
MemoryIdFactory: TypeAlias = Callable[[str], str]
IdentifierFactory: TypeAlias = Callable[[], str]


@dataclass(slots=True)
class _PlanningState:
    canvas_task: asyncio.Task[CanvasSpecialistGeneration] | None = None
    canvas_started: float | None = None
    canvas_prepared: PreparedCanvasDraft | None = None
    canvas_transaction: dict[str, object] | None = None
    canvas_rejection: str | None = None
    canvas_failure_id: str | None = None
    canvas_call_id: str | None = None
    canvas_brief: str | None = None


@dataclass(frozen=True, slots=True)
class _CanvasInteractionRejection:
    scene_id: str
    scene_revision: int
    interaction_revision: int
    binding_id: str
    code: CanvasInteractionRejectionCode
    message: str


class RealtimeSessionCoordinator(Protocol):
    """Transport-independent boundary for one realtime conversation."""

    def frames(self) -> AsyncIterator[AvaServerOutboundFrame]:
        """Yield ordered JSON control and binary PCM frames."""
        ...

    async def negotiate(self, *, offer_sdp: str) -> NegotiatedRealtimeCall:
        """Negotiate the one WebRTC media session owned by this Ava session."""
        ...

    async def receive(self, event: AvaClientEventUnion) -> None:
        """Accept one validated protocol-v3 client control event."""
        ...

    async def close(self) -> None:
        """Release the session and all provider/network resources."""
        ...

    @property
    def turn_id(self) -> str:
        """Current turn identifier for safe diagnostics."""
        ...


class DeterministicRealtimeSessionCoordinator:
    """Protocol-v3 conversation using injected provider and memory seams.

    This coordinator is production-shaped but transport-free. Deterministic
    fake providers exercise it without credentials; live adapters implement the
    same ``RealtimeProviderSession`` and ``StreamingSpeechSynthesizer`` contracts.
    """

    def __init__(
        self,
        *,
        session_id: str,
        turn_id: str,
        request_id: str,
        stream_id: str,
        subject: str,
        age_band: AgeBand,
        realtime: RealtimeProviderSession,
        speech: StreamingSpeechSynthesizer,
        canvas_specialist: CanvasSpecialist,
        memory: AuthoritativeMemoryStore,
        memory_id_factory: MemoryIdFactory,
        max_audio_chunk_bytes: int = 192_000,
        canvas_state: CanvasSessionState | None = None,
        persistent: bool = False,
        turn_id_factory: IdentifierFactory | None = None,
        request_id_factory: IdentifierFactory | None = None,
        stream_id_factory: IdentifierFactory | None = None,
        failure_id_factory: IdentifierFactory = new_failure_id,
    ) -> None:
        if not 2_048 <= max_audio_chunk_bytes <= 192_000:
            raise ValueError("max_audio_chunk_bytes must be between 2048 and 192000")

        self._session_id = session_id
        self._turn_id = turn_id
        self._request_id = request_id
        self._stream_id = stream_id
        self._subject = subject
        self._age_band = age_band
        self._child_text: str | None = None
        self._child_text_source: ChildTurnTextSource | None = None
        self._realtime = realtime
        self._speech = speech
        self._canvas_specialist = canvas_specialist
        self._memory = memory
        self._memory_id_factory = memory_id_factory
        self._max_audio_chunk_bytes = max_audio_chunk_bytes
        self._canvas_state = canvas_state or CanvasSessionState()
        self._persistent = persistent
        self._turn_id_factory = turn_id_factory
        self._request_id_factory = request_id_factory
        self._stream_id_factory = stream_id_factory
        self._failure_id_factory = failure_id_factory
        if persistent and (
            turn_id_factory is None
            or request_id_factory is None
            or stream_id_factory is None
        ):
            raise ValueError(
                "persistent coordinators require turn, request, and stream id factories"
            )

        self._machine = RealtimeTurnStateMachine()
        self._controls: BoundedAsyncQueue[AvaClientEventUnion] = BoundedAsyncQueue(
            max_items=64,
            max_weight=64 * 1024,
            weigh=lambda event: len(event.model_dump_json(by_alias=True)),
        )
        self._interaction_rejections: deque[_CanvasInteractionRejection] = deque()
        self._interaction_rejection_available = asyncio.Event()
        self._interaction_rejection_capacity = asyncio.Event()
        self._interaction_rejection_capacity.set()
        self._canvas_context_lock = asyncio.Lock()
        self._memory_snapshot: AuthoritativeMemorySnapshot | None = None
        self._harness: RealtimeHarness | None = None
        self._negotiated_call: NegotiatedRealtimeCall | None = None
        self._sequence = 0
        self._revision = 0
        self._next_client_sequence = 0
        self._next_audio_chunk_index = 0
        self._started = False
        self._client_ready = False
        self._audio_metadata_sent = False
        self._playback_started = False
        self._last_rendered_sample_count = 0
        self._stopped_sample_count: int | None = None
        self._cancel_event: CancelTurnEvent | None = None
        self._cancellation_requested = asyncio.Event()
        self._terminal = False
        self._session_reusable = True
        self._closed = False
        self._last_memory_decision: MemoryProposalDecision | None = None
        self._last_canvas_generation: CanvasSpecialistGeneration | None = None
        self._canvas_cue_sent = False
        self._canvas_status = "notRequested"
        self._terminal_failure_code: str | None = None
        self._last_failure_id: str | None = None
        self._last_failure_component: str | None = None
        self._pending_memory_refresh_warning: tuple[str, str] | None = None
        self._last_error: Exception | None = None
        self._active_planning_task: asyncio.Task[AvaTurnPlan | None] | None = None
        self._active_canvas_task: asyncio.Task[CanvasSpecialistGeneration] | None = None
        self._previous_terminal_turn_id: str | None = None
        self._previous_terminal_stream_id: str | None = None

    @property
    def harness(self) -> RealtimeHarness:
        if self._harness is None:
            raise CoordinatorStateError("Realtime harness has not been built yet")
        return self._harness

    @property
    def turn_id(self) -> str:
        return self._turn_id

    @property
    def received_sample_count(self) -> int:
        return self._machine.snapshot.received_sample_count

    @property
    def last_memory_decision(self) -> MemoryProposalDecision | None:
        return self._last_memory_decision

    @property
    def last_canvas_generation(self) -> CanvasSpecialistGeneration | None:
        return self._last_canvas_generation

    @property
    def canvas_state(self) -> CanvasSessionState:
        return self._canvas_state

    @property
    def last_error(self) -> Exception | None:
        """Underlying server diagnostic; never serialize this to the client."""

        return self._last_error

    @property
    def negotiated_call(self) -> NegotiatedRealtimeCall:
        if self._negotiated_call is None:
            raise CoordinatorStateError("Realtime media has not been negotiated")
        return self._negotiated_call

    async def negotiate(self, *, offer_sdp: str) -> NegotiatedRealtimeCall:
        if self._closed:
            raise CoordinatorStateError("cannot negotiate a closed coordinator")
        if self._started:
            raise CoordinatorStateError(
                "Realtime media must be negotiated before turn streaming starts"
            )
        if self._negotiated_call is not None:
            raise CoordinatorStateError("Realtime media may be negotiated only once")
        preparation_started = monotonic()
        try:
            memory_started = monotonic()
            snapshot = await self._memory.load_snapshot(
                subject=self._subject,
                age_band=self._age_band,
            )
            self._memory_snapshot = snapshot
            LOGGER.info(
                "realtime_stage session_id=%s request_id=%s stage=memory.load "
                "elapsed_ms=%.2f",
                self._session_id,
                self._request_id,
                (monotonic() - memory_started) * 1000,
            )
            harness_started = monotonic()
            harness = build_realtime_harness(
                snapshot,
                canvas_context=self._canvas_state.render_realtime_context(),
            )
            LOGGER.info(
                "realtime_stage session_id=%s request_id=%s stage=harness.build "
                "elapsed_ms=%.2f",
                self._session_id,
                self._request_id,
                (monotonic() - harness_started) * 1000,
            )
            negotiation_started = monotonic()
            negotiated = await self._realtime.negotiate(offer_sdp=offer_sdp)
            LOGGER.info(
                "realtime_stage session_id=%s request_id=%s "
                "stage=provider.negotiate elapsed_ms=%.2f",
                self._session_id,
                self._request_id,
                (monotonic() - negotiation_started) * 1000,
            )
            prompt_started = monotonic()
            await self._realtime.prepare(harness=harness)
            LOGGER.info(
                "realtime_stage session_id=%s request_id=%s stage=prompt.install "
                "prompt_version=%s elapsed_ms=%.2f",
                self._session_id,
                self._request_id,
                harness.prompt_version,
                (monotonic() - prompt_started) * 1000,
            )
        except Exception as error:
            self._last_error = error
            raise
        self._harness = harness
        self._negotiated_call = negotiated
        LOGGER.info(
            "realtime_session_prepared session_id=%s prompt_version=%s elapsed_ms=%.2f",
            self._session_id,
            harness.prompt_version,
            (monotonic() - preparation_started) * 1000,
            extra={
                "ava_context": {
                    "sessionId": self._session_id,
                    "promptVersion": harness.prompt_version,
                    "toolContractVersion": harness.tool_contract_version,
                    "providerCallId": negotiated.provider_call_id,
                }
            },
        )
        return negotiated

    async def receive(self, event: AvaClientEventUnion) -> None:
        if self._closed:
            raise CoordinatorStateError("cannot receive on a closed coordinator")
        if event.session_id != self._session_id:
            raise CoordinatorStateError("client event sessionId does not match session")
        if event.sequence != self._next_client_sequence:
            raise CoordinatorStateError(
                f"expected client sequence {self._next_client_sequence}, "
                f"received {event.sequence}"
            )

        if isinstance(event, ClientReadyEvent):
            if self._client_ready:
                raise CoordinatorStateError("clientReady may occur only once")
            if self._started:
                raise CoordinatorStateError(
                    "clientReady must arrive before turn streaming starts"
                )
            self._client_ready = True
            self._next_client_sequence += 1
            return

        if isinstance(event, CanvasInteractionEvent):
            try:
                self._canvas_state.record_interaction(
                    scene_id=event.scene_id,
                    scene_revision=event.scene_revision,
                    interaction_revision=event.interaction_revision,
                    binding_id=event.binding_id,
                )
            except CanvasInteractionRejected as error:
                while len(self._interaction_rejections) >= 32:
                    self._interaction_rejection_capacity.clear()
                    await self._interaction_rejection_capacity.wait()
                self._interaction_rejections.append(
                    _CanvasInteractionRejection(
                        scene_id=event.scene_id,
                        scene_revision=event.scene_revision,
                        interaction_revision=event.interaction_revision,
                        binding_id=event.binding_id,
                        code=error.code,
                        message=str(error),
                    )
                )
                self._interaction_rejection_available.set()
            else:
                await self._refresh_canvas_context()
            self._next_client_sequence += 1
            return

        if self._is_late_terminal_playback_control(event):
            LOGGER.info(
                "late_terminal_playback_control_ignored session_id=%s "
                "current_turn_id=%s event_turn_id=%s event=%s sequence=%d",
                self._session_id,
                self._turn_id,
                event.turn_id,
                str(event.event),
                event.sequence,
            )
            self._next_client_sequence += 1
            return

        if self._terminal:
            raise CoordinatorStateError("cannot receive after terminal turn state")

        self._validate_turn_and_stream(event)
        should_enqueue = True
        if isinstance(event, PlaybackStartedEvent):
            if not self._audio_metadata_sent:
                raise CoordinatorStateError(
                    "playbackStarted requires prior audioMetadata"
                )
            if self._playback_started:
                raise CoordinatorStateError("playbackStarted may occur only once")
            self._playback_started = True
        elif isinstance(event, PlaybackProgressEvent):
            self._require_playback_started()
            self._validate_rendered_position(
                event.rendered_sample_count,
                buffered_sample_count=event.buffered_sample_count,
            )
            self._last_rendered_sample_count = event.rendered_sample_count
        elif isinstance(event, PlaybackStoppedEvent):
            self._require_playback_started()
            self._validate_rendered_position(event.rendered_sample_count)
            self._last_rendered_sample_count = event.rendered_sample_count
            self._stopped_sample_count = event.rendered_sample_count
        elif isinstance(event, CancelTurnEvent):
            registered = self._machine.register_cancellation(event.request_id)
            if registered:
                self._cancel_event = event
                self._cancellation_requested.set()
                await self._canvas_specialist.cancel(turn_id=self._turn_id)
            else:
                should_enqueue = False
        else:
            raise CoordinatorStateError(
                f"unsupported client control {type(event).__name__}"
            )

        if should_enqueue:
            self._controls.put_nowait(event)
        self._next_client_sequence += 1

    def _is_late_terminal_playback_control(
        self,
        event: AvaClientEventUnion,
    ) -> bool:
        if not isinstance(
            event,
            (PlaybackStartedEvent, PlaybackProgressEvent, PlaybackStoppedEvent),
        ):
            return False
        if self._terminal:
            return event.turn_id == self._turn_id and event.stream_id == self._stream_id
        return (
            self._previous_terminal_turn_id is not None
            and event.turn_id == self._previous_terminal_turn_id
            and event.stream_id == self._previous_terminal_stream_id
        )

    async def frames(self) -> AsyncIterator[AvaServerOutboundFrame]:
        if self._started:
            raise CoordinatorStateError("coordinator frames may be consumed only once")
        if self._closed:
            raise CoordinatorStateError("cannot start a closed coordinator")
        if not self._client_ready:
            raise CoordinatorStateError(
                "clientReady must be received before consuming turn frames"
            )
        if self._negotiated_call is None:
            raise CoordinatorStateError(
                "Realtime media must be negotiated before consuming turn frames"
            )
        self._started = True

        conversation = self._conversation_frames().__aiter__()
        conversation_task: asyncio.Task[AvaServerOutboundFrame] | None = (
            asyncio.create_task(_next_outbound_frame(conversation))
        )
        rejection_task: asyncio.Task[bool] | None = asyncio.create_task(
            self._interaction_rejection_available.wait()
        )
        try:
            while conversation_task is not None:
                pending: set[asyncio.Task[object]] = {
                    cast(asyncio.Task[object], conversation_task)
                }
                if rejection_task is not None:
                    pending.add(cast(asyncio.Task[object], rejection_task))
                done, _ = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # Conversation frames have priority because the task may have
                # already allocated the next sequence. Keep PCM descriptors
                # and their binary payload atomic on the wire.
                if conversation_task in done:
                    try:
                        frame = conversation_task.result()
                    except StopAsyncIteration:
                        conversation_task = None
                    else:
                        yield frame
                        if isinstance(frame, AvaServerControlFrame) and isinstance(
                            frame.event,
                            AudioChunkEvent,
                        ):
                            pcm = await anext(conversation)
                            if not isinstance(pcm, AvaServerPcmFrame):
                                raise CoordinatorStateError(
                                    "audioChunk was not followed by binary PCM"
                                )
                            yield pcm
                        # A client action can arrive while the generator is
                        # suspended at ``yield``. Emit its session warning
                        # before allocating the next turn-frame sequence.
                        if self._interaction_rejections:
                            if rejection_task is None:
                                raise CoordinatorStateError(
                                    "canvas interaction rejection reader is missing"
                                )
                            await rejection_task
                            rejection = self._pop_interaction_rejection()
                            yield self._interaction_rejected_frame(rejection)
                            rejection_task = asyncio.create_task(
                                self._interaction_rejection_available.wait()
                            )
                        conversation_task = asyncio.create_task(
                            _next_outbound_frame(conversation)
                        )

                if rejection_task is not None and rejection_task in done:
                    rejection_task.result()
                    rejection = self._pop_interaction_rejection()
                    yield self._interaction_rejected_frame(rejection)
                    rejection_task = asyncio.create_task(
                        self._interaction_rejection_available.wait()
                    )
        finally:
            tasks = tuple(
                task for task in (conversation_task, rejection_task) if task is not None
            )
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await cast(
                AsyncGenerator[AvaServerOutboundFrame, None],
                conversation,
            ).aclose()

    def _pop_interaction_rejection(self) -> _CanvasInteractionRejection:
        if not self._interaction_rejections:
            raise CoordinatorStateError(
                "canvas interaction rejection signal had no queued event"
            )
        rejection = self._interaction_rejections.popleft()
        if not self._interaction_rejections:
            self._interaction_rejection_available.clear()
        self._interaction_rejection_capacity.set()
        return rejection

    async def _conversation_frames(self) -> AsyncIterator[AvaServerOutboundFrame]:
        while not self._closed:
            async for frame in self._one_turn_frames():
                yield frame
            if not self._persistent or self._closed or not self._session_reusable:
                if self._persistent and not self._session_reusable:
                    LOGGER.warning(
                        "realtime_session_not_rearmed session_id=%s turn_id=%s "
                        "failure_id=%s component=%s code=%s",
                        self._session_id,
                        self._turn_id,
                        self._last_failure_id,
                        self._last_failure_component,
                        self._terminal_failure_code,
                    )
                return
            await self._prepare_next_turn()

    async def _one_turn_frames(self) -> AsyncIterator[AvaServerOutboundFrame]:
        parser = StreamingSpeechParser()
        ledger = DeliveredTextLedger()
        pending_binary_pcm = False
        try:
            async for frame in self._one_turn_frames_impl(
                parser=parser,
                ledger=ledger,
            ):
                if isinstance(frame, AvaServerControlFrame) and isinstance(
                    frame.event,
                    AudioChunkEvent,
                ):
                    pending_binary_pcm = True
                elif isinstance(frame, AvaServerPcmFrame):
                    if not pending_binary_pcm:
                        raise CoordinatorStateError(
                            "coordinator emitted binary PCM without its descriptor"
                        )
                    pending_binary_pcm = False
                yield frame
        except Exception as error:
            if self._terminal:
                raise
            if pending_binary_pcm:
                raise CoordinatorStateError(
                    "turn failed while a binary PCM frame was pending"
                ) from error
            await self._cancel_active_turn_work()
            self._last_error = error
            delivered_samples = self._bounded_rendered_sample_count()
            yield self._failed_frame(
                AvaFailureCode.INTERNAL,
                "Ava's turn could not be completed safely.",
                component=AvaFailureComponent.COORDINATOR,
                retryable=True,
                delivered_sample_count=delivered_samples,
                delivered_text=ledger.delivered_text(delivered_samples),
            )

    async def _one_turn_frames_impl(
        self,
        *,
        parser: StreamingSpeechParser,
        ledger: DeliveredTextLedger,
    ) -> AsyncIterator[AvaServerOutboundFrame]:
        turn_started = monotonic()
        yield self._turn_created_frame()
        yield self._phase_frame(AvaTurnPhase.CONNECTING)

        if self._harness is None:
            raise CoordinatorStateError(
                "Realtime prompt must be prepared during media negotiation"
            )

        yield self._phase_frame(AvaTurnPhase.LISTENING)
        if self._pending_memory_refresh_warning is not None:
            failure_id, message = self._pending_memory_refresh_warning
            self._pending_memory_refresh_warning = None
            yield self._memory_persistence_failed_frame(
                message,
                failure_id=failure_id,
            )
        LOGGER.info(
            "realtime_turn_stage session_id=%s turn_id=%s stage=listening "
            "elapsed_ms=%.2f",
            self._session_id,
            self._turn_id,
            (monotonic() - turn_started) * 1000,
        )

        plan: AvaTurnPlan | None = None
        synthesis_started = False
        provider_error: (
            tuple[
                AvaFailureCode,
                AvaFailureComponent,
                str,
                bool,
            ]
            | None
        ) = None
        planning_started = False
        planning_state = _PlanningState()
        planning_updates: asyncio.Queue[AvaTurnSegment | None] = asyncio.Queue(
            maxsize=8
        )
        planning_task = asyncio.create_task(
            self._produce_plan(
                parser=parser,
                updates=planning_updates,
                state=planning_state,
            )
        )
        self._active_planning_task = planning_task

        try:
            while True:
                update = await planning_updates.get()
                if update is None:
                    break
                if not planning_started:
                    planning_started = True
                    LOGGER.info(
                        "realtime_turn_stage session_id=%s turn_id=%s "
                        "stage=provider.speech_ready elapsed_ms=%.2f",
                        self._session_id,
                        self._turn_id,
                        (monotonic() - turn_started) * 1000,
                    )
                    yield self._phase_frame(AvaTurnPhase.PLANNING)

                segment = update
                if ledger.segment_count == 0:
                    LOGGER.info(
                        "realtime_turn_stage session_id=%s turn_id=%s "
                        "stage=segment.first elapsed_ms=%.2f",
                        self._session_id,
                        self._turn_id,
                        (monotonic() - turn_started) * 1000,
                    )
                yield self._segment_frame(
                    segment,
                    segment_index=ledger.segment_count,
                )
                ledger.register_segment(segment)
                if self._cancel_event is not None:
                    break
                if not synthesis_started:
                    synthesis_started = True
                    yield self._phase_frame(AvaTurnPhase.SYNTHESIZING)
                    yield self._audio_metadata_frame()

                try:
                    async for frame in self._speech_frames_for_segment(
                        segment,
                        ledger=ledger,
                    ):
                        yield frame
                        if self._cancel_event is not None and (
                            isinstance(frame, AvaServerPcmFrame)
                            or not (
                                isinstance(frame, AvaServerControlFrame)
                                and isinstance(frame.event, AudioChunkEvent)
                            )
                        ):
                            break
                except (SpeechDeliveryError, ValueError) as error:
                    self._last_error = error
                    provider_error = (
                        AvaFailureCode.SPEECH_INVALID,
                        AvaFailureComponent.SPEECH_PROVIDER,
                        "Speech PCM or alignment failed validation.",
                        False,
                    )
                    break
                except Exception as error:
                    self._last_error = error
                    provider_error = (
                        AvaFailureCode.SPEECH_UNAVAILABLE,
                        AvaFailureComponent.SPEECH_PROVIDER,
                        "Speech synthesis stream became unavailable.",
                        True,
                    )
                    break
                if self._cancel_event is not None:
                    break

            if provider_error is not None or self._cancel_event is not None:
                await self._cancel_planning_work(
                    planning_task=planning_task,
                    state=planning_state,
                )
            else:
                plan = await planning_task
                self._active_planning_task = None
                if plan is None:
                    raise StreamingTurnParseError(
                        "Realtime planning ended without a complete turn"
                    )
                LOGGER.info(
                    "realtime_plan_ready session_id=%s turn_id=%s segments=%d "
                    "canvas_decision=%s",
                    self._session_id,
                    self._turn_id,
                    len(plan.segments),
                    plan.canvas_decision.complexity,
                )
        except (StreamingTurnParseError, ValueError) as error:
            await self._cancel_planning_work(
                planning_task=planning_task,
                state=planning_state,
            )
            self._last_error = error
            provider_error = (
                AvaFailureCode.MODEL_INVALID,
                AvaFailureComponent.REALTIME_PROVIDER,
                "Realtime turn failed protocol-v3 validation.",
                False,
            )
        except Exception as error:
            await self._cancel_planning_work(
                planning_task=planning_task,
                state=planning_state,
            )
            self._last_error = error
            provider_error = (
                AvaFailureCode.MODEL_UNAVAILABLE,
                AvaFailureComponent.REALTIME_PROVIDER,
                "Realtime planning stream became unavailable.",
                True,
            )

        if self._cancel_event is not None:
            async for frame in self._cancel_frames(
                plan=plan,
                ledger=ledger,
            ):
                yield frame
            return

        if provider_error is not None:
            code, component, message, retryable = provider_error
            yield self._failed_frame(
                code,
                message,
                component=component,
                retryable=retryable,
                delivered_sample_count=self._bounded_rendered_sample_count(),
                delivered_text=ledger.delivered_text(
                    self._bounded_rendered_sample_count()
                ),
            )
            return

        if plan is None or not synthesis_started:
            await self._cancel_active_turn_work()
            yield self._failed_frame(
                AvaFailureCode.MODEL_INVALID,
                "Realtime turn completed without a valid speech segment.",
                component=AvaFailureComponent.REALTIME_PROVIDER,
                retryable=False,
                delivered_sample_count=0,
                delivered_text="",
            )
            return

        try:
            ledger.validate_complete(
                received_sample_count=self.received_sample_count,
            )
        except SpeechDeliveryError as error:
            await self._cancel_active_turn_work()
            self._last_error = error
            yield self._failed_frame(
                AvaFailureCode.SPEECH_INVALID,
                "Speech alignment did not cover the validated turn.",
                component=AvaFailureComponent.SPEECH_PROVIDER,
                retryable=False,
                delivered_sample_count=self._bounded_rendered_sample_count(),
                delivered_text=ledger.delivered_text(
                    self._bounded_rendered_sample_count()
                ),
            )
            return

        # The client can close its streaming PCM input and begin completion
        # immediately. Optional specialist work continues while audio plays.
        yield self._phase_frame(AvaTurnPhase.SPEAKING)

        canvas_transaction = planning_state.canvas_transaction
        canvas_rejection = planning_state.canvas_rejection
        canvas_failure_id = planning_state.canvas_failure_id

        if self._cancel_event is not None:
            async for frame in self._cancel_frames(
                plan=plan,
                ledger=ledger,
            ):
                yield frame
            return

        if canvas_rejection is not None:
            if canvas_failure_id is None:
                raise CoordinatorStateError(
                    "canvas rejection is missing its diagnostic failure ID"
                )
            yield self._canvas_rejected_frame(
                canvas_rejection,
                failure_id=canvas_failure_id,
            )
        elif canvas_transaction is not None:
            target_segment = (
                plan.segments[1] if len(plan.segments) > 1 else plan.segments[0]
            )
            sample_offset = ledger.sample_offset_for_segment_start(target_segment.id)
            LOGGER.info(
                "canvas_cue_emitted session_id=%s turn_id=%s scene_id=%s "
                "revision=%s sample_offset=%d",
                self._session_id,
                self._turn_id,
                canvas_transaction["sceneId"],
                canvas_transaction["revision"],
                sample_offset,
            )
            yield self._canvas_cue_frame(
                cue_id=_canvas_identifier("canvas_cue", self._turn_id),
                sample_offset=sample_offset,
                transaction=canvas_transaction,
            )
        while not self._terminal:
            try:
                control = await self._controls.get()
            except QueueClosed:
                return

            if isinstance(control, CancelTurnEvent):
                async for frame in self._cancel_frames(plan=plan, ledger=ledger):
                    yield frame
                return
            if isinstance(control, PlaybackProgressEvent):
                if (
                    control.rendered_sample_count == self.received_sample_count
                    and control.buffered_sample_count == 0
                ):
                    async for frame in self._complete_frames(
                        plan=plan,
                        ledger=ledger,
                    ):
                        yield frame
                    return

    async def _prepare_next_turn(self) -> None:
        if not self._terminal:
            raise CoordinatorStateError(
                "cannot advance a persistent session before a terminal turn"
            )
        turn_id_factory = self._turn_id_factory
        request_id_factory = self._request_id_factory
        stream_id_factory = self._stream_id_factory
        if (
            turn_id_factory is None
            or request_id_factory is None
            or stream_id_factory is None
        ):
            raise CoordinatorStateError(
                "persistent session identifier factories are unavailable"
            )

        rearm_started = monotonic()
        # OpenAI already owns the immediate conversation history for this
        # persistent call. Re-injecting just-delivered turns into session
        # instructions duplicates context and can change later tool choices.
        # Memory is committed independently; only canonical canvas interaction
        # changes require an explicit session update.
        harness = self.harness

        self._previous_terminal_turn_id = self._turn_id
        self._previous_terminal_stream_id = self._stream_id
        self._turn_id = turn_id_factory()
        self._request_id = request_id_factory()
        self._stream_id = stream_id_factory()
        self._child_text = None
        self._child_text_source = None
        self._machine = RealtimeTurnStateMachine()
        self._harness = harness
        self._sequence = 0
        self._revision = 0
        self._next_audio_chunk_index = 0
        self._audio_metadata_sent = False
        self._playback_started = False
        self._last_rendered_sample_count = 0
        self._stopped_sample_count = None
        self._cancel_event = None
        self._cancellation_requested = asyncio.Event()
        self._terminal = False
        self._canvas_cue_sent = False
        self._canvas_status = "notRequested"
        self._terminal_failure_code = None
        self._last_failure_id = None
        self._last_failure_component = None
        self._last_canvas_generation = None
        self._last_error = None
        LOGGER.info(
            "realtime_turn_rearmed session_id=%s turn_id=%s prompt_version=%s "
            "tool_contract_version=%s "
            "elapsed_ms=%.2f",
            self._session_id,
            self._turn_id,
            harness.prompt_version,
            harness.tool_contract_version,
            (monotonic() - rearm_started) * 1000,
        )

    async def _refresh_canvas_context(self) -> None:
        """Install canonical semantic canvas context for the next response.

        This is a session update only. It never starts a model response and it
        is called only for server-resolved explore/select/submitOutcome
        bindings.
        """

        async with self._canvas_context_lock:
            snapshot = self._memory_snapshot
            if snapshot is None or self._harness is None:
                raise CoordinatorStateError(
                    "canvas context cannot refresh before session negotiation"
                )
            harness = build_realtime_harness(
                snapshot,
                canvas_context=self._canvas_state.render_realtime_context(),
            )
            await self._realtime.prepare(harness=harness)
            self._harness = harness
            interaction = self._canvas_state.last_interaction
            LOGGER.info(
                "canvas_interaction_context_installed session_id=%s scene_id=%s "
                "scene_revision=%s interaction_revision=%s binding_id=%s",
                self._session_id,
                interaction.scene_id if interaction is not None else None,
                interaction.scene_revision if interaction is not None else None,
                interaction.interaction_revision if interaction is not None else None,
                interaction.binding_id if interaction is not None else None,
            )

    async def _produce_plan(
        self,
        *,
        parser: StreamingSpeechParser,
        updates: asyncio.Queue[AvaTurnSegment | None],
        state: _PlanningState,
    ) -> AvaTurnPlan | None:
        try:
            async for event in self._turn_events_until_cancelled():
                if isinstance(event, RealtimeSpeechTextDelta):
                    for segment in parser.feed(event.delta):
                        await updates.put(segment)
                    continue
                if isinstance(event, RealtimeRejectedCanvasToolCall):
                    for segment in parser.finish_response_chunk():
                        await updates.put(segment)
                    if state.canvas_call_id is not None:
                        raise StreamingTurnParseError(
                            "Realtime requested more than one canvas in one turn"
                        )
                    state.canvas_call_id = event.call_id
                    state.canvas_failure_id = self._failure_id_factory()
                    state.canvas_rejection = (
                        "The optional visual request did not pass validation."
                    )
                    self._canvas_status = "rejected"
                    LOGGER.warning(
                        "canvas_tool_arguments_rejected failure_id=%s "
                        "session_id=%s turn_id=%s call_id=%s reason=%s",
                        state.canvas_failure_id,
                        self._session_id,
                        self._turn_id,
                        event.call_id,
                        event.reason,
                    )
                    await self._realtime.complete_canvas_tool(
                        turn_id=self._turn_id,
                        call_id=event.call_id,
                        output=InteractiveCanvasToolOutput.model_validate(
                            {
                                "status": InteractiveCanvasToolStatus.REJECTED,
                                "reason": event.reason,
                            }
                        ),
                    )
                    continue
                if not isinstance(event, RealtimeCanvasToolCall):
                    raise CoordinatorStateError(
                        f"unsupported Realtime provider event {type(event).__name__}"
                    )
                for segment in parser.finish_response_chunk():
                    await updates.put(segment)
                if state.canvas_call_id is not None:
                    raise StreamingTurnParseError(
                        "Realtime requested more than one canvas in one turn"
                    )
                state.canvas_call_id = event.call_id
                await self._run_canvas_tool(event, state=state)

            if self._cancel_event is not None:
                return None
            self._commit_prepared_canvas(state)
            for segment in parser.finish():
                await updates.put(segment)
            segments = parser.emitted_segments
            child_transcript = await self._realtime.child_transcript(
                turn_id=self._turn_id
            )
            if child_transcript is None:
                self._child_text = None
                self._child_text_source = None
                child_summary = "Conversation turn."
            else:
                self._child_text = _bounded_child_transcript(child_transcript)
                self._child_text_source = ChildTurnTextSource.TRANSCRIPT
                child_summary = _bounded_child_summary(self._child_text)
            brief = None
            if state.canvas_call_id is not None:
                brief = state.canvas_brief
            return AvaTurnPlan.model_validate(
                {
                    "segments": [
                        segment.model_dump(mode="json", by_alias=True)
                        for segment in segments
                    ],
                    "memoryProposal": None,
                    "childTurnSummary": child_summary,
                    "canvasDecision": {
                        "complexity": (
                            CanvasComplexity.SPECIALIST_REQUIRED
                            if brief is not None
                            else CanvasComplexity.NONE
                        ),
                        "specialistBrief": brief,
                    },
                }
            )
        finally:
            updates.put_nowait(None)

    async def _run_canvas_tool(
        self,
        call: RealtimeCanvasToolCall,
        *,
        state: _PlanningState,
    ) -> None:
        state.canvas_started = monotonic()
        self._canvas_status = "requested"
        state.canvas_brief = call.request.brief
        request = self._canvas_specialist_request(call.request)
        state.canvas_task = asyncio.create_task(
            self._canvas_specialist.generate(request)
        )
        self._active_canvas_task = state.canvas_task
        LOGGER.info(
            "canvas_specialist_started session_id=%s turn_id=%s call_id=%s",
            self._session_id,
            self._turn_id,
            call.call_id,
        )
        try:
            try:
                self._last_canvas_generation = await state.canvas_task
            finally:
                if self._active_canvas_task is state.canvas_task:
                    self._active_canvas_task = None
            prepared = self._canvas_state.prepare_draft(
                self._last_canvas_generation.draft,
                transaction_id=_canvas_identifier("canvas", self._turn_id),
            )
            state.canvas_prepared = prepared
            transaction = prepared.transaction
        except Exception as error:
            self._last_error = error
            state.canvas_failure_id = self._failure_id_factory()
            state.canvas_rejection = _canvas_rejection_message(error)
            canvas_provider_error = (
                error if isinstance(error, CanvasSpecialistError) else None
            )
            LOGGER.warning(
                "canvas_specialist_rejected failure_id=%s session_id=%s turn_id=%s "
                "error_type=%s provider_status=%s provider_code=%s "
                "provider_request_id=%s message=%s",
                state.canvas_failure_id,
                self._session_id,
                self._turn_id,
                type(error).__name__,
                (
                    canvas_provider_error.provider_status_code
                    if canvas_provider_error is not None
                    else None
                ),
                (
                    canvas_provider_error.provider_error_code
                    if canvas_provider_error is not None
                    else None
                ),
                (
                    canvas_provider_error.provider_request_id
                    if canvas_provider_error is not None
                    else None
                ),
                str(error),
            )
            await self._realtime.complete_canvas_tool(
                turn_id=self._turn_id,
                call_id=call.call_id,
                output=InteractiveCanvasToolOutput.model_validate(
                    {
                        "status": InteractiveCanvasToolStatus.REJECTED,
                        "reason": _canvas_tool_rejection_reason(error),
                    }
                ),
            )
            return

        await self._realtime.complete_canvas_tool(
            turn_id=self._turn_id,
            call_id=call.call_id,
            output=InteractiveCanvasToolOutput.model_validate(
                {
                    "status": InteractiveCanvasToolStatus.READY,
                    "sceneId": transaction.scene_id,
                    "revision": transaction.revision,
                    "summary": _bounded_canvas_summary(call.request.brief),
                }
            ),
        )
        LOGGER.info(
            "canvas_specialist_ready session_id=%s turn_id=%s "
            "elapsed_ms=%.2f scene_id=%s revision=%d operations=%d bindings=%d",
            self._session_id,
            self._turn_id,
            (monotonic() - state.canvas_started) * 1000,
            transaction.scene_id,
            transaction.revision,
            len(transaction.operations),
            len(transaction.bindings),
        )

    def _commit_prepared_canvas(self, state: _PlanningState) -> None:
        prepared = state.canvas_prepared
        if prepared is None:
            return
        self._canvas_state.commit_prepared(prepared)
        state.canvas_transaction = prepared.transaction.model_dump(
            mode="json",
            by_alias=True,
        )

    def _canvas_specialist_request(
        self,
        request: InteractiveCanvasRequest,
    ) -> CanvasSpecialistRequest:
        return CanvasSpecialistRequest.model_validate(
            {
                "turnId": self._turn_id,
                "brief": request.brief,
                "interactionGoal": request.interaction_goal,
                "ageBand": self._age_band,
                "currentRevision": (
                    self._canvas_state.scene.revision
                    if self._canvas_state.scene is not None
                    else None
                ),
                "currentScene": (
                    self._canvas_state.draft.model_dump(
                        mode="json",
                        by_alias=True,
                    )
                    if self._canvas_state.draft is not None
                    else None
                ),
            }
        )

    async def _cancel_planning_work(
        self,
        *,
        planning_task: asyncio.Task[AvaTurnPlan | None],
        state: _PlanningState,
    ) -> None:
        await self._cancel_active_turn_work()
        # Local references may have completed before they were registered as
        # active; gathering them here still retrieves every terminal result.
        await asyncio.gather(
            planning_task,
            *(tuple((state.canvas_task,)) if state.canvas_task is not None else ()),
            return_exceptions=True,
        )

    async def _cancel_active_turn_work(self) -> None:
        planning_task = self._active_planning_task
        canvas_task = self._active_canvas_task
        if planning_task is not None:
            if not planning_task.done():
                planning_task.cancel()
                await asyncio.gather(planning_task, return_exceptions=True)
            try:
                await self._realtime.cancel(turn_id=self._turn_id)
            except Exception:
                LOGGER.exception(
                    "realtime_planning_cleanup_failed session_id=%s turn_id=%s",
                    self._session_id,
                    self._turn_id,
                )
        if canvas_task is not None and not canvas_task.done():
            try:
                await self._canvas_specialist.cancel(turn_id=self._turn_id)
            except Exception:
                LOGGER.exception(
                    "canvas_planning_cleanup_failed session_id=%s turn_id=%s",
                    self._session_id,
                    self._turn_id,
                )
            canvas_task.cancel()
        tasks = tuple(task for task in (planning_task, canvas_task) if task is not None)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._active_planning_task = None
        self._active_canvas_task = None

    async def _turn_events_until_cancelled(
        self,
    ) -> AsyncIterator[RealtimeProviderTurnEvent]:
        """Stop a provider receive immediately when the client cancels.

        While Ava is listening, OpenAI may legitimately emit no sideband event
        for an unbounded human pause. Cancellation therefore cannot be polled
        only after a provider delta arrives.
        """

        if self._harness is None:
            raise CoordinatorStateError("Realtime harness has not been built yet")
        if self._cancellation_requested.is_set():
            return

        iterator = self._realtime.turn_events(
            harness=self._harness,
            turn_id=self._turn_id,
        ).__aiter__()
        while True:
            next_event = asyncio.create_task(_next_provider_event(iterator))
            cancellation = asyncio.create_task(self._cancellation_requested.wait())
            try:
                done, _ = await asyncio.wait(
                    {next_event, cancellation},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except BaseException:
                next_event.cancel()
                cancellation.cancel()
                await asyncio.gather(
                    next_event,
                    cancellation,
                    return_exceptions=True,
                )
                raise

            if cancellation in done:
                # End the sole socket reader first. The provider retains its
                # active response ID, then cancel() drains that exact response
                # boundary so no stale deltas can enter the next turn.
                next_event.cancel()
                await asyncio.gather(next_event, return_exceptions=True)
                await self._realtime.cancel(turn_id=self._turn_id)
                return

            cancellation.cancel()
            await asyncio.gather(cancellation, return_exceptions=True)
            try:
                yield next_event.result()
            except StopAsyncIteration:
                return

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._cancel_active_turn_work()
        if not self._terminal:
            await self._realtime.cancel(turn_id=self._turn_id)
            await self._speech.cancel(turn_id=self._turn_id)
            await self._canvas_specialist.cancel(turn_id=self._turn_id)
        await self._realtime.close()
        await self._speech.close()
        await self._canvas_specialist.close()
        self._controls.close()

    async def _speech_frames_for_segment(
        self,
        segment: AvaTurnSegment,
        *,
        ledger: DeliveredTextLedger,
    ) -> AsyncIterator[AvaServerOutboundFrame]:
        segment_sample_offset = self.received_sample_count
        async for item in self._speech.synthesize(segment):
            if isinstance(item, SynthesizedAlignmentBatch):
                spans = tuple(
                    AvaAlignmentSpan.model_validate(
                        {
                            "segmentId": span.segment_id,
                            "textStart": span.text_start,
                            "textEnd": span.text_end,
                            "sampleStart": (segment_sample_offset + span.sample_start),
                            "sampleEnd": segment_sample_offset + span.sample_end,
                            "phoneme": span.phoneme,
                        }
                    )
                    for span in item.spans
                )
                ledger.add_alignment_spans(spans)
                yield self._alignment_frame(spans)
                continue

            if isinstance(item, SynthesizedAudioChunk):
                if len(item.pcm) > self._max_audio_chunk_bytes:
                    raise SpeechDeliveryError(
                        "speech PCM exceeds the configured transport chunk limit"
                    )
                descriptor_frame = self._audio_chunk_frame(item.pcm)
                yield descriptor_frame
                yield AvaServerPcmFrame(
                    descriptor=cast(AudioChunkEvent, descriptor_frame.event),
                    pcm=item.pcm,
                )
                continue

            raise SpeechDeliveryError(f"unsupported speech item {type(item).__name__}")

        for cue in segment.canvas_cues:
            yield self._canvas_cue_frame(
                cue_id=cue.id,
                sample_offset=ledger.sample_offset_for_anchor(
                    segment_id=segment.id,
                    anchor=cue.anchor,
                ),
                transaction=cue.transaction.model_dump(
                    mode="json",
                    by_alias=True,
                ),
            )

    async def _complete_frames(
        self,
        *,
        plan: AvaTurnPlan,
        ledger: DeliveredTextLedger,
    ) -> AsyncIterator[AvaServerOutboundFrame]:
        delivered_samples = self.received_sample_count
        delivered_text = ledger.delivered_text(delivered_samples)
        try:
            await self._commit_memory(
                plan=plan,
                delivered_sample_count=delivered_samples,
                delivered_text=delivered_text,
                terminal=DeliveredTurnTerminal.COMPLETED,
            )
        except Exception as error:
            self._last_error = error
            failure_id = self._failure_id_factory()
            LOGGER.exception(
                "memory_commit_failed failure_id=%s session_id=%s turn_id=%s",
                failure_id,
                self._session_id,
                self._turn_id,
                extra={
                    "ava_context": {
                        "failureId": failure_id,
                        "component": str(AvaFailureComponent.MEMORY_STORE),
                        "sessionId": self._session_id,
                        "turnId": self._turn_id,
                    }
                },
            )
            yield self._memory_persistence_failed_frame(
                "Delivered turn could not be persisted.",
                failure_id=failure_id,
            )
        yield self._terminal_frame(
            {
                "event": "turnCompleted",
                "deliveredSampleCount": delivered_samples,
                "deliveredText": delivered_text,
            }
        )

    async def _cancel_frames(
        self,
        *,
        plan: AvaTurnPlan | None,
        ledger: DeliveredTextLedger,
    ) -> AsyncIterator[AvaServerOutboundFrame]:
        cancel = self._cancel_event
        if cancel is None:
            raise CoordinatorStateError("cancellation frames require cancelTurn")
        yield self._phase_frame(AvaTurnPhase.CANCELLING)
        await self._realtime.cancel(turn_id=self._turn_id)
        await self._speech.cancel(turn_id=self._turn_id)
        await self._canvas_specialist.cancel(turn_id=self._turn_id)

        delivered_samples = self._bounded_rendered_sample_count()
        delivered_text = ledger.delivered_text(delivered_samples)
        if plan is not None:
            try:
                await self._commit_memory(
                    plan=plan,
                    delivered_sample_count=delivered_samples,
                    delivered_text=delivered_text,
                    terminal=DeliveredTurnTerminal.CANCELLED,
                )
            except Exception as error:
                self._last_error = error
                failure_id = self._failure_id_factory()
                LOGGER.exception(
                    "cancelled_memory_commit_failed failure_id=%s "
                    "session_id=%s turn_id=%s",
                    failure_id,
                    self._session_id,
                    self._turn_id,
                    extra={
                        "ava_context": {
                            "failureId": failure_id,
                            "component": str(AvaFailureComponent.MEMORY_STORE),
                            "sessionId": self._session_id,
                            "turnId": self._turn_id,
                        }
                    },
                )
                yield self._memory_persistence_failed_frame(
                    "Interrupted delivered turn could not be persisted.",
                    failure_id=failure_id,
                )

        yield self._terminal_frame(
            {
                "event": "turnCancelled",
                "deliveredSampleCount": delivered_samples,
                "deliveredText": delivered_text,
                "reason": cancel.reason,
            }
        )

    async def _commit_memory(
        self,
        *,
        plan: AvaTurnPlan,
        delivered_sample_count: int,
        delivered_text: str,
        terminal: DeliveredTurnTerminal,
    ) -> None:
        if self._child_text is None or self._child_text_source is None:
            LOGGER.info(
                "memory_commit_skipped session_id=%s turn_id=%s "
                "reason=child_transcript_unavailable",
                self._session_id,
                self._turn_id,
            )
            self._last_memory_decision = None
            return
        memory_id = (
            self._memory_id_factory(self._turn_id)
            if plan.memory_proposal is not None
            else None
        )
        turn = DeliveredConversationTurn.model_validate(
            {
                "turnId": self._turn_id,
                "childText": self._require_child_text(),
                "childTextSource": self._require_child_text_source(),
                "avaDeliveredText": delivered_text,
                "deliveredSampleCount": delivered_sample_count,
                "childTurnSummary": plan.child_turn_summary,
                "terminal": terminal,
            }
        )
        self._last_memory_decision = await self._memory.commit_delivered_turn(
            subject=self._subject,
            turn=turn,
            proposal=plan.memory_proposal,
            memory_id=memory_id,
        )

    def _require_child_text(self) -> str:
        if self._child_text is None:
            raise CoordinatorStateError(
                "provider child transcript is unavailable for memory commit"
            )
        return self._child_text

    def _require_child_text_source(self) -> ChildTurnTextSource:
        if self._child_text_source is None:
            raise CoordinatorStateError(
                "provider child text source is unavailable for memory commit"
            )
        return self._child_text_source

    def _turn_created_frame(self) -> AvaServerControlFrame:
        return self._control_frame(
            {
                "event": "turnCreated",
                "revision": 0,
                "requestId": self._request_id,
            }
        )

    def _phase_frame(self, phase: AvaTurnPhase) -> AvaServerControlFrame:
        next_revision = self._revision + 1
        frame = self._control_frame(
            {
                "event": "turnPhaseChanged",
                "revision": next_revision,
                "phase": phase,
            }
        )
        self._revision = next_revision
        return frame

    def _segment_frame(
        self,
        segment: AvaTurnSegment,
        *,
        segment_index: int,
    ) -> AvaServerControlFrame:
        return self._control_frame(
            {
                "event": "segmentReady",
                "revision": self._revision,
                "segmentIndex": segment_index,
                "segment": segment.model_dump(mode="json", by_alias=True),
            }
        )

    def _audio_metadata_frame(self) -> AvaServerControlFrame:
        frame = self._control_frame(
            {
                "event": "audioMetadata",
                "revision": self._revision,
                "streamId": self._stream_id,
                "audioFormat": "pcm_s16le",
                "sampleRateHz": 24_000,
                "channels": 1,
                "bitsPerSample": 16,
                "modelId": self._speech.model_id,
                "voiceId": self._speech.voice_id,
            }
        )
        if not isinstance(frame.event, AudioMetadataEvent):
            raise AssertionError("audio metadata frame resolved to the wrong type")
        self._audio_metadata_sent = True
        return frame

    def _alignment_frame(
        self,
        spans: tuple[AvaAlignmentSpan, ...],
    ) -> AvaServerControlFrame:
        frame = self._control_frame(
            {
                "event": "alignmentBatch",
                "revision": self._revision,
                "streamId": self._stream_id,
                "spans": [
                    span.model_dump(mode="json", by_alias=True) for span in spans
                ],
            }
        )
        if not isinstance(frame.event, AlignmentBatchEvent):
            raise AssertionError("alignment frame resolved to the wrong type")
        return frame

    def _audio_chunk_frame(self, pcm: bytes) -> AvaServerControlFrame:
        sample_count = len(pcm) // 2
        frame = self._control_frame(
            {
                "event": "audioChunk",
                "revision": self._revision,
                "streamId": self._stream_id,
                "chunkIndex": self._next_audio_chunk_index,
                "sampleOffset": self.received_sample_count,
                "sampleCount": sample_count,
                "byteLength": len(pcm),
            }
        )
        if not isinstance(frame.event, AudioChunkEvent):
            raise AssertionError("audio chunk frame resolved to the wrong type")
        self._next_audio_chunk_index += 1
        return frame

    def _canvas_cue_frame(
        self,
        *,
        cue_id: str,
        sample_offset: int,
        transaction: dict[str, object],
    ) -> AvaServerControlFrame:
        frame = self._control_frame(
            {
                "event": "canvasCue",
                "revision": self._revision,
                "cueId": cue_id,
                "sampleOffset": sample_offset,
                "transaction": transaction,
            }
        )
        self._canvas_cue_sent = True
        self._canvas_status = "delivered"
        return frame

    def _canvas_rejected_frame(
        self,
        message: str,
        *,
        failure_id: str,
    ) -> AvaServerControlFrame:
        self._canvas_status = "rejected"
        self._terminal_failure_code = str(AvaFailureCode.CANVAS_REJECTED)
        self._last_failure_id = failure_id
        self._last_failure_component = str(AvaFailureComponent.CANVAS_SPECIALIST)
        return self._control_frame(
            {
                "event": "canvasRejected",
                "revision": self._revision,
                "failure": {
                    "failureId": failure_id,
                    "component": AvaFailureComponent.CANVAS_SPECIALIST,
                    "code": "canvasRejected",
                    "message": message,
                    "retryable": False,
                },
            }
        )

    def _interaction_rejected_frame(
        self,
        rejection: _CanvasInteractionRejection,
    ) -> AvaServerControlFrame:
        frame = self._control_frame(
            {
                "event": "interactionRejected",
                "revision": self._revision,
                "sceneId": rejection.scene_id,
                "sceneRevision": rejection.scene_revision,
                "interactionRevision": rejection.interaction_revision,
                "bindingId": rejection.binding_id,
                "rejectionCode": rejection.code,
                "message": rejection.message,
            }
        )
        if not isinstance(frame.event, InteractionRejectedEvent):
            raise AssertionError("interaction rejection resolved to the wrong type")
        LOGGER.info(
            "canvas_interaction_rejected session_id=%s scene_id=%s "
            "scene_revision=%s interaction_revision=%s binding_id=%s code=%s",
            self._session_id,
            rejection.scene_id,
            rejection.scene_revision,
            rejection.interaction_revision,
            rejection.binding_id,
            rejection.code,
        )
        return frame

    def _memory_persistence_failed_frame(
        self,
        message: str,
        *,
        failure_id: str,
    ) -> AvaServerControlFrame:
        self._terminal_failure_code = str(AvaFailureCode.MEMORY_UNAVAILABLE)
        self._last_failure_id = failure_id
        self._last_failure_component = str(AvaFailureComponent.MEMORY_STORE)
        return self._control_frame(
            {
                "event": "memoryPersistenceFailed",
                "revision": self._revision,
                "failure": {
                    "failureId": failure_id,
                    "component": AvaFailureComponent.MEMORY_STORE,
                    "code": "memoryUnavailable",
                    "message": message,
                    "retryable": True,
                },
            }
        )

    def _failed_frame(
        self,
        code: AvaFailureCode,
        message: str,
        *,
        component: AvaFailureComponent,
        retryable: bool,
        delivered_sample_count: int,
        delivered_text: str,
    ) -> AvaServerControlFrame:
        failure_id = self._failure_id_factory()
        # A turnFailed event means at least one required provider or invariant
        # no longer has a proven clean boundary. End this persistent session
        # after reporting the single failure; the client may establish a fresh
        # conversation, but this coordinator must never spin on the same
        # poisoned channel.
        self._session_reusable = False
        self._terminal_failure_code = str(code)
        self._last_failure_id = failure_id
        self._last_failure_component = str(component)
        internal_error = self._last_error
        LOGGER.error(
            "realtime_turn_failed failure_id=%s component=%s session_id=%s "
            "turn_id=%s code=%s retryable=%s internal_error_type=%s",
            failure_id,
            component,
            self._session_id,
            self._turn_id,
            code,
            retryable,
            type(internal_error).__name__ if internal_error is not None else None,
            exc_info=(
                (type(internal_error), internal_error, internal_error.__traceback__)
                if internal_error is not None
                else None
            ),
            extra={
                "ava_context": {
                    "failureId": failure_id,
                    "component": str(component),
                    "sessionId": self._session_id,
                    "turnId": self._turn_id,
                    "code": str(code),
                    "retryable": retryable,
                }
            },
        )
        return self._terminal_frame(
            {
                "event": "turnFailed",
                "failure": {
                    "failureId": failure_id,
                    "component": component,
                    "code": code,
                    "message": message,
                    "retryable": retryable,
                },
                "deliveredSampleCount": delivered_sample_count,
                "deliveredText": delivered_text,
            }
        )

    def _terminal_frame(self, payload: dict[str, object]) -> AvaServerControlFrame:
        next_revision = self._revision + 1
        payload["revision"] = next_revision
        frame = self._control_frame(payload)
        self._revision = next_revision
        self._terminal = True
        failure = payload.get("failure")
        failure_code = failure.get("code") if isinstance(failure, dict) else None
        failure_id = (
            failure.get("failureId") if isinstance(failure, dict) else None
        ) or self._last_failure_id
        failure_component = (
            failure.get("component") if isinstance(failure, dict) else None
        ) or self._last_failure_component
        failure_code = failure_code or self._terminal_failure_code
        provider_response_id = getattr(
            self._realtime,
            "last_response_id",
            None,
        )
        provider_call_id = (
            self._negotiated_call.provider_call_id
            if self._negotiated_call is not None
            else None
        )
        LOGGER.info(
            "realtime_turn_terminal session_id=%s turn_id=%s terminal=%s "
            "failure_id=%s failure_component=%s failure_code=%s "
            "internal_error_type=%s provider_response_id=%s "
            "provider_call_id=%s canvas_status=%s",
            self._session_id,
            self._turn_id,
            payload.get("event"),
            failure_id,
            failure_component,
            failure_code,
            type(self._last_error).__name__ if self._last_error is not None else None,
            provider_response_id,
            provider_call_id,
            self._canvas_status,
            extra={
                "ava_context": {
                    "failureId": failure_id,
                    "component": failure_component,
                    "sessionId": self._session_id,
                    "turnId": self._turn_id,
                    "terminal": payload.get("event"),
                    "code": str(failure_code) if failure_code is not None else None,
                    "providerResponseId": provider_response_id,
                    "providerCallId": provider_call_id,
                    "canvasStatus": self._canvas_status,
                }
            },
        )
        return frame

    def _control_frame(self, payload: dict[str, object]) -> AvaServerControlFrame:
        complete = {
            "protocolVersion": PROTOCOL_V3,
            "sessionId": self._session_id,
            "turnId": self._turn_id,
            "sequence": self._sequence,
            **payload,
        }
        event = AVA_SERVER_EVENT_ADAPTER.validate_python(complete)
        try:
            self._machine.accept(event)
        except RealtimeStateError as error:
            raise CoordinatorStateError(
                f"coordinator generated an invalid server event: {error}"
            ) from error
        self._sequence += 1
        return AvaServerControlFrame(event)

    def _validate_turn_and_stream(self, event: AvaClientEventUnion) -> None:
        turn_id = getattr(event, "turn_id", None)
        if turn_id != self._turn_id:
            raise CoordinatorStateError("client event turnId does not match turn")
        stream_id = getattr(event, "stream_id", None)
        if stream_id is not None and stream_id != self._stream_id:
            raise CoordinatorStateError("client event streamId does not match stream")

    def _require_playback_started(self) -> None:
        if not self._playback_started:
            raise CoordinatorStateError("playback progress requires playbackStarted")

    def _validate_rendered_position(
        self,
        rendered_sample_count: int,
        *,
        buffered_sample_count: int = 0,
    ) -> None:
        if rendered_sample_count < self._last_rendered_sample_count:
            raise CoordinatorStateError("rendered sample count cannot move backwards")
        received = self.received_sample_count
        if rendered_sample_count > received:
            raise CoordinatorStateError(
                "rendered sample count cannot exceed received PCM"
            )
        if buffered_sample_count > received - rendered_sample_count:
            raise CoordinatorStateError(
                "rendered and buffered samples cannot exceed received PCM"
            )

    def _bounded_rendered_sample_count(self) -> int:
        candidate = (
            self._stopped_sample_count
            if self._stopped_sample_count is not None
            else self._last_rendered_sample_count
        )
        return min(candidate, self.received_sample_count)


async def _next_provider_event(
    iterator: AsyncIterator[RealtimeProviderTurnEvent],
) -> RealtimeProviderTurnEvent:
    return await iterator.__anext__()


async def _next_outbound_frame(
    iterator: AsyncIterator[AvaServerOutboundFrame],
) -> AvaServerOutboundFrame:
    return await iterator.__anext__()


def _bounded_child_transcript(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("provider child transcript cannot be empty")
    if len(normalized) > 2_000:
        raise ValueError("provider child transcript cannot exceed 2000 characters")
    return normalized


def _bounded_child_summary(value: str) -> str:
    if len(value) <= 240:
        return value
    boundary = value.rfind(" ", 0, 238)
    if boundary <= 0:
        boundary = 237
    return f"{value[:boundary].rstrip()}…"


def _bounded_canvas_summary(brief: str) -> str:
    prefix = "Interactive visual ready: "
    available = 240 - len(prefix)
    if len(brief) <= available:
        return f"{prefix}{brief}"
    boundary = brief.rfind(" ", 0, available - 1)
    if boundary <= 0:
        boundary = available - 1
    return f"{prefix}{brief[:boundary].rstrip()}…"


def _canvas_rejection_message(error: Exception) -> str:
    if isinstance(error, CanvasSpecialistUnavailableError):
        if "deadline" in str(error).lower():
            return "The optional visual took too long to prepare."
        return "The optional visual service is temporarily unavailable."
    if isinstance(error, CanvasSpecialistInvalidError):
        return "The optional visual could not be prepared from that response."
    return "The optional visual could not be safely prepared."


def _canvas_tool_rejection_reason(error: Exception) -> str:
    if isinstance(error, CanvasSpecialistUnavailableError):
        if "deadline" in str(error).lower():
            return "The visual specialist exceeded its bounded deadline."
        return "The visual specialist is temporarily unavailable."
    if isinstance(error, CanvasSpecialistInvalidError):
        return "The generated visual did not pass strict validation."
    return "The visual could not be compiled into a safe scene."


def _canvas_identifier(prefix: str, turn_id: str) -> str:
    digest = sha256(turn_id.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"
