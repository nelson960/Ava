from __future__ import annotations

import asyncio
import logging
from builtins import anext
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import TypeAlias, TypeVar, cast
from zoneinfo import ZoneInfo

from ..canvas.svg_workspace import ActiveSvgCanvasScene, RecordedSvgCanvasInteraction
from ..diagnostics import new_failure_id
from ..protocol import AgeBand
from .bounded_queue import BoundedAsyncQueue, QueueClosed
from .context_assembler import (
    DynamicContextAssembler,
    DynamicContextBuildReason,
    MemoryReference,
)
from .coordinator_v4 import ProtocolV4CanvasEventSink
from .delivery import (
    DeliveredTextLedger,
    SpeechDeliveryError,
    StreamingSpeechSynthesizer,
    SynthesizedAlignmentBatch,
    SynthesizedAudioChunk,
    SynthesizedSpeechItem,
)
from .events_v4 import (
    AVA_SERVER_EVENT_ADAPTER_V4,
    PROTOCOL_V4,
    AlignmentBatchEventV4,
    AudioChunkEventV4,
    AudioMetadataEventV4,
    AvaClientEventUnionV4,
    AvaServerEventUnionV4,
    CancelTurnEventV4,
    CanvasDocumentPreparedEventV4,
    CanvasInteractionEventV4,
    CanvasPreparationFailedEventV4,
    CanvasPresentedEventV4,
    CanvasRenderProfileUpdatedEventV4,
    CanvasRuntimeFailedEventV4,
    ClientReadyEventV4,
    PlaybackProgressEventV4,
    PlaybackStartedEventV4,
    PlaybackStoppedEventV4,
)
from .harness import RealtimeHarness, build_realtime_harness
from .memory import (
    MAX_CHILD_TURN_SUMMARY_CHARACTERS,
    AuthoritativeMemoryStore,
    AuthoritativeMemorySnapshot,
    ChildTurnTextSource,
    DeliveredCanvasInteraction,
    DeliveredConversationTurn,
    DeliveredInputKind,
    DeliveredTurnTerminal,
    MemoryResetRevisionMismatchError,
    MemoryProposalDecision,
    MemorySearchCandidate,
    MemoryCandidateSourceKind,
)
from .provider_session import (
    NegotiatedRealtimeCall,
    RealtimeContextInstallationStatus,
    RealtimeProviderInputEvent,
    RealtimeProviderProtocolError,
    RealtimeProviderResponseIncompleteError,
    RealtimeProviderTurnEvent,
    RealtimeProviderSession,
    RealtimeProviderUnavailableError,
)
from .state_machine import RealtimeStateError, RealtimeTurnStateMachine
from .speech_contract import (
    SpeechAlignment,
    SpeechFailureCode,
    SpeechFailureComponent,
    SpeechPhase,
    SpeechPhrase,
)
from .svg_canvas_jobs import SvgCanvasJobCoordinator
from .tool_contract import (
    CANVAS_MODEL_SAFE_REJECTION_REASON,
    InteractiveCanvasCancellationToolOutputV4,
    InteractiveCanvasToolOutputV4,
    InteractiveCanvasToolStatusV4,
    GetCurrentTimeToolOutputV4,
    RealtimeGetCurrentTimeToolCall,
    RealtimeRejectedUtilityToolCall,
    RealtimeSearchMemoryToolCall,
    RealtimeUtilityToolKind,
    RejectedUtilityToolOutputV4,
    SearchMemoryReferenceV4,
    SearchMemoryToolOutputV4,
    SearchMemoryToolStatus,
    RealtimeCancelCanvasToolCall,
    RealtimeCanvasToolCall,
    RealtimeRejectedCanvasToolCall,
    RealtimeSpeechTextDelta,
)
from .turn_parser import StreamingSpeechParser, StreamingTurnParseError
from .timezone import validate_iana_timezone_id
from .websocket_v4 import (
    AvaServerControlFrameV4,
    AvaServerOutboundFrameV4,
    AvaServerPcmFrameV4,
)

LOGGER = logging.getLogger("ava_backend.realtime_coordinator")
RECENT_LIVE_ECHO_SUPPRESSION_TURNS = 8


class CoordinatorStateError(RuntimeError):
    """Raised when a client command violates coordinator ownership or ordering."""


IdentifierFactory: TypeAlias = Callable[[], str]
OccurrenceTimeFactory: TypeAlias = Callable[[], datetime]
_WorkResult = TypeVar("_WorkResult")


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class _PlanningState:
    canvas_started: float | None = None
    canvas_call_id: str | None = None
    canvas_generation_id: str | None = None
    first_text_delta_at: float | None = None
    tool_steps_used: int = 0
    utility_tool_kind: RealtimeUtilityToolKind | None = None
    canvas_terminal_used: bool = False


@dataclass(frozen=True, slots=True)
class _TurnResult:
    segments: tuple[SpeechPhrase, ...]
    child_turn_summary: str


@dataclass(frozen=True, slots=True)
class _CapturedMemoryCommit:
    sequence: int
    turn_id: str
    occurred_at: datetime
    timezone_id: str
    expected_reset_revision: int
    child_turn_summary: str | None
    child_text: str | None
    child_text_source: ChildTurnTextSource | None
    canvas_interaction: DeliveredCanvasInteraction | None
    delivered_text: str
    delivered_sample_count: int
    terminal: DeliveredTurnTerminal
    transcript_waiter: Awaitable[str | None] | None
    warm_refresh_revision: int


class DeterministicRealtimeSessionCoordinator:
    """Protocol-v4 conversation using injected provider and memory seams.

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
        timezone_id: str = "UTC",
        realtime: RealtimeProviderSession,
        speech: StreamingSpeechSynthesizer,
        canvas_jobs: SvgCanvasJobCoordinator,
        canvas_events: ProtocolV4CanvasEventSink,
        memory: AuthoritativeMemoryStore,
        max_audio_chunk_bytes: int = 192_000,
        persistent: bool = False,
        turn_id_factory: IdentifierFactory | None = None,
        request_id_factory: IdentifierFactory | None = None,
        stream_id_factory: IdentifierFactory | None = None,
        failure_id_factory: IdentifierFactory = new_failure_id,
        occurrence_time_factory: OccurrenceTimeFactory = _utc_now,
        memory_commit_drain_timeout_seconds: float = 2.0,
        context_assembler: DynamicContextAssembler | None = None,
        current_time_factory: OccurrenceTimeFactory = _utc_now,
    ) -> None:
        if not 2_048 <= max_audio_chunk_bytes <= 192_000:
            raise ValueError("max_audio_chunk_bytes must be between 2048 and 192000")
        if not 0.01 <= memory_commit_drain_timeout_seconds <= 10:
            raise ValueError(
                "memory_commit_drain_timeout_seconds must be between 0.01 and 10"
            )

        self._session_id = session_id
        self._turn_id = turn_id
        self._request_id = request_id
        self._stream_id = stream_id
        self._subject = subject
        self._age_band = age_band
        self._timezone_id = validate_iana_timezone_id(timezone_id)
        self._child_text: str | None = None
        self._child_text_source: ChildTurnTextSource | None = None
        self._realtime = realtime
        self._speech = speech
        self._canvas_jobs = canvas_jobs
        self._canvas_events = canvas_events
        self._memory = memory
        self._max_audio_chunk_bytes = max_audio_chunk_bytes
        self._persistent = persistent
        self._turn_id_factory = turn_id_factory
        self._request_id_factory = request_id_factory
        self._stream_id_factory = stream_id_factory
        self._failure_id_factory = failure_id_factory
        self._occurrence_time_factory = occurrence_time_factory
        self._memory_commit_drain_timeout_seconds = memory_commit_drain_timeout_seconds
        self._context_assembler = context_assembler or DynamicContextAssembler()
        self._current_time_factory = current_time_factory
        if persistent and (
            turn_id_factory is None
            or request_id_factory is None
            or stream_id_factory is None
        ):
            raise ValueError(
                "persistent coordinators require turn, request, and stream id factories"
            )

        self._machine = RealtimeTurnStateMachine()
        self._controls: BoundedAsyncQueue[AvaClientEventUnionV4] = BoundedAsyncQueue(
            max_items=64,
            max_weight=64 * 1024,
            weigh=lambda event: len(event.model_dump_json(by_alias=True)),
        )
        self._canvas_context_lock = asyncio.Lock()
        self._memory_snapshot: AuthoritativeMemorySnapshot | None = None
        self._memory_reset_revision: int | None = None
        self._harness: RealtimeHarness | None = None
        self._pending_harness: RealtimeHarness | None = None
        self._pending_context_reason: DynamicContextBuildReason | None = None
        self._context_installation_task: asyncio.Task[None] | None = None
        self._warm_memories: tuple[MemoryReference, ...] = ()
        self._warm_refresh_revision = 0
        self._context_build_sequence = 0
        self._recent_live_echo_suppression_turn_ids: deque[str] = deque(
            maxlen=RECENT_LIVE_ECHO_SUPPRESSION_TURNS
        )
        self._negotiated_call: NegotiatedRealtimeCall | None = None
        self._sequence = 0
        self._revision = 0
        self._canvas_revision = 0
        self._next_client_sequence = 0
        self._next_audio_chunk_index = 0
        self._started = False
        self._client_ready = False
        self._audio_metadata_sent = False
        self._playback_started = False
        self._last_rendered_sample_count = 0
        self._playback_progress_changed = asyncio.Event()
        self._stopped_sample_count: int | None = None
        self._cancel_event: CancelTurnEventV4 | None = None
        self._cancellation_requested = asyncio.Event()
        self._terminal = False
        self._session_reusable = True
        self._closed = False
        self._last_memory_decision: MemoryProposalDecision | None = None
        self._canvas_status = "notRequested"
        self._terminal_failure_code: str | None = None
        self._last_failure_id: str | None = None
        self._last_failure_component: str | None = None
        self._memory_commit_tasks: dict[str, asyncio.Task[None]] = {}
        self._memory_commit_sequence = 0
        self._latest_memory_commit_sequence = 0
        self._turn_occurred_at: datetime | None = None
        self._last_error: Exception | None = None
        self._active_planning_task: asyncio.Task[_TurnResult | None] | None = None
        self._active_provider_input_task: (
            asyncio.Task[RealtimeProviderInputEvent] | None
        ) = None
        self._previous_terminal_turn_id: str | None = None
        self._previous_terminal_stream_id: str | None = None
        self._pending_provider_input_item_ids: set[str] = set()
        self._interrupt_signalled_item_ids: set[str] = set()
        self._interruption_provider_item_id: str | None = None
        self._interruption_provider_audio_start_ms: int | None = None
        self._provider_speech_started_observed_at: float | None = None
        self._preemption_requested_at: float | None = None
        self._latest_canvas_interaction_intent: RecordedSvgCanvasInteraction | None = (
            None
        )
        self._canvas_interaction_response_task: asyncio.Task[None] | None = None
        self._canvas_interaction_response_wakeup = asyncio.Event()
        self._canvas_interaction_response_turn_id: str | None = None
        self._turn_canvas_interaction_input: RecordedSvgCanvasInteraction | None = None
        self._server_preemption_reason: str | None = None

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
    def last_error(self) -> Exception | None:
        """Underlying server diagnostic; never serialize this to the client."""

        return self._last_error

    @property
    def negotiated_call(self) -> NegotiatedRealtimeCall:
        if self._negotiated_call is None:
            raise CoordinatorStateError("Realtime media has not been negotiated")
        return self._negotiated_call

    def restore_focused_canvas(self, scene: ActiveSvgCanvasScene) -> None:
        if self._closed or self._started or self._negotiated_call is not None:
            raise CoordinatorStateError(
                "Canvas restore must complete before realtime negotiation"
            )
        self._canvas_jobs.state.workspace.restore_focused(scene)

    async def memory_reset(self, *, reset_revision: int) -> None:
        if (
            isinstance(reset_revision, bool)
            or not isinstance(reset_revision, int)
            or reset_revision < 1
        ):
            raise ValueError("reset_revision must be a positive integer")
        current = self._memory_reset_revision
        if current is not None and reset_revision <= current:
            return
        self._memory_reset_revision = reset_revision
        await self._canvas_jobs.reset_conversation()
        self._warm_refresh_revision += 1
        self._warm_memories = ()
        self._recent_live_echo_suppression_turn_ids.clear()
        self._last_memory_decision = None
        # Durable reset clears application memory projections. It does not mutate
        # conversation items already resident in the provider's ephemeral call.
        if self._harness is not None and not self._closed:
            await self._refresh_canvas_context(
                reason=DynamicContextBuildReason.MEMORY_COMPACTED
            )

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
            self._memory_reset_revision = snapshot.reset_revision
            LOGGER.info(
                "realtime_stage session_id=%s request_id=%s stage=memory.load "
                "elapsed_ms=%.2f",
                self._session_id,
                self._request_id,
                (monotonic() - memory_started) * 1000,
            )
            harness_started = monotonic()
            self._warm_memories = _stable_memory_references(snapshot)
            bootstrap_current_time_utc = self._current_time_factory()
            dynamic_context = self._context_assembler.assemble(
                build_id=self._next_context_build_id(),
                reason=DynamicContextBuildReason.SESSION_BOOTSTRAP,
                age_band=self._age_band,
                timezone_id=self._timezone_id,
                current_time_utc=bootstrap_current_time_utc,
                bootstrap_turns=tuple(snapshot.delivered_turns),
                warm_memories=self._warm_memories,
                canvas_context=self._current_canvas_context(),
            )
            harness = build_realtime_harness(
                dynamic_context=dynamic_context,
            )
            self._log_dynamic_context_build(
                DynamicContextBuildReason.SESSION_BOOTSTRAP,
                harness,
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
            try:
                installation_status = await self._realtime.prepare(harness=harness)
            except Exception as error:
                self._log_context_installation(
                    harness=harness,
                    reason=DynamicContextBuildReason.SESSION_BOOTSTRAP,
                    status="failed",
                    provider_call_id=negotiated.provider_call_id,
                    error_type=type(error).__name__,
                )
                raise
            self._log_context_installation(
                harness=harness,
                reason=DynamicContextBuildReason.SESSION_BOOTSTRAP,
                status=str(installation_status),
                provider_call_id=negotiated.provider_call_id,
            )
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
        bootstrap_speech_turn_ids = [
            turn.turn_id
            for turn in snapshot.delivered_turns
            if turn.input_kind == DeliveredInputKind.SPEECH
        ]
        if dynamic_context.included_bootstrap_turns:
            self._recent_live_echo_suppression_turn_ids.extend(
                bootstrap_speech_turn_ids[-dynamic_context.included_bootstrap_turns :]
            )
        self._negotiated_call = negotiated
        LOGGER.info(
            "realtime_session_prepared session_id=%s timezone_id=%s "
            "prompt_version=%s elapsed_ms=%.2f",
            self._session_id,
            self._timezone_id,
            harness.prompt_version,
            (monotonic() - preparation_started) * 1000,
            extra={
                "ava_context": {
                    "sessionId": self._session_id,
                    "timezoneId": self._timezone_id,
                    "promptVersion": harness.prompt_version,
                    "toolContractVersion": harness.tool_contract_version,
                    "providerCallId": negotiated.provider_call_id,
                }
            },
        )
        return negotiated

    async def receive(self, event: AvaClientEventUnionV4) -> None:
        if self._closed:
            raise CoordinatorStateError("cannot receive on a closed coordinator")
        if event.session_id != self._session_id:
            raise CoordinatorStateError("client event sessionId does not match session")
        if event.sequence != self._next_client_sequence:
            raise CoordinatorStateError(
                f"expected client sequence {self._next_client_sequence}, "
                f"received {event.sequence}"
            )
        self._next_client_sequence += 1

        if isinstance(event, ClientReadyEventV4):
            if self._client_ready:
                raise CoordinatorStateError("clientReady may occur only once")
            if self._started:
                raise CoordinatorStateError(
                    "clientReady must arrive before turn streaming starts"
                )
            self._client_ready = True
            return

        if isinstance(event, CanvasRenderProfileUpdatedEventV4):
            if not self._client_ready:
                raise CoordinatorStateError(
                    "canvas render-profile update requires clientReady"
                )
            await self._canvas_jobs.update_render_profile(
                profile_revision=event.profile_revision,
                render_profile=event.render_profile,
            )
            return

        if isinstance(event, CanvasDocumentPreparedEventV4):
            await self._canvas_jobs.document_prepared(event)
            return
        if isinstance(event, CanvasPreparationFailedEventV4):
            await self._canvas_jobs.preparation_failed(event)
            return
        if isinstance(event, CanvasPresentedEventV4):
            await self._canvas_jobs.presented(event)
            await self._refresh_canvas_context()
            return
        if isinstance(event, CanvasRuntimeFailedEventV4):
            await self._canvas_jobs.runtime_failed(event)
            await self._refresh_canvas_context()
            return
        if isinstance(event, CanvasInteractionEventV4):
            interaction = await self._canvas_jobs.interaction(event)
            if interaction is not None:
                self._latest_canvas_interaction_intent = interaction
                self._canvas_interaction_response_wakeup.set()
                self._ensure_canvas_interaction_response_worker()
                if self._canvas_interaction_response_turn_id == self._turn_id or (
                    self._machine.snapshot.phase
                    in {SpeechPhase.SYNTHESIZING, SpeechPhase.SPEAKING}
                ):
                    self._request_server_preemption(
                        reason="canvasInteractionSuperseded"
                    )
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
            return

        if self._terminal:
            raise CoordinatorStateError("cannot receive after terminal turn state")

        self._validate_turn_and_stream(event)
        should_enqueue = True
        if isinstance(event, PlaybackStartedEventV4):
            if not self._audio_metadata_sent:
                raise CoordinatorStateError(
                    "playbackStarted requires prior audioMetadata"
                )
            if self._playback_started:
                raise CoordinatorStateError("playbackStarted may occur only once")
            self._playback_started = True
        elif isinstance(event, PlaybackProgressEventV4):
            self._require_playback_started()
            self._validate_rendered_position(
                event.rendered_sample_count,
                buffered_sample_count=event.buffered_sample_count,
            )
            self._last_rendered_sample_count = event.rendered_sample_count
            self._playback_progress_changed.set()
            should_enqueue = False
        elif isinstance(event, PlaybackStoppedEventV4):
            self._require_playback_started()
            self._validate_rendered_position(event.rendered_sample_count)
            self._last_rendered_sample_count = event.rendered_sample_count
            self._stopped_sample_count = event.rendered_sample_count
        elif isinstance(event, CancelTurnEventV4):
            registered = self._machine.register_cancellation(event.request_id)
            if registered:
                self._cancel_event = event
                self._cancellation_requested.set()
                LOGGER.info(
                    "realtime_cancellation_received session_id=%s turn_id=%s "
                    "request_id=%s reason=%s provider_input_item_id=%s",
                    self._session_id,
                    self._turn_id,
                    event.request_id,
                    event.reason,
                    self._interruption_provider_item_id,
                    extra={
                        "ava_context": {
                            "sessionId": self._session_id,
                            "turnId": self._turn_id,
                            "requestId": event.request_id,
                            "cancellationReason": event.reason,
                            "providerInputItemId": (
                                self._interruption_provider_item_id
                            ),
                        }
                    },
                )
            else:
                should_enqueue = False
        else:
            raise CoordinatorStateError(
                f"unsupported client control {type(event).__name__}"
            )

        if should_enqueue:
            self._controls.put_nowait(event)

    def _is_late_terminal_playback_control(
        self,
        event: AvaClientEventUnionV4,
    ) -> bool:
        if not isinstance(
            event,
            (
                PlaybackStartedEventV4,
                PlaybackProgressEventV4,
                PlaybackStoppedEventV4,
            ),
        ):
            return False
        if self._terminal:
            return event.turn_id == self._turn_id and event.stream_id == self._stream_id
        return (
            self._previous_terminal_turn_id is not None
            and event.turn_id == self._previous_terminal_turn_id
            and event.stream_id == self._previous_terminal_stream_id
        )

    async def frames(self) -> AsyncIterator[AvaServerOutboundFrameV4]:
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
        conversation_task: asyncio.Task[AvaServerOutboundFrameV4] | None = (
            asyncio.create_task(_next_outbound_frame(conversation))
        )
        canvas_task: asyncio.Task[dict[str, object]] = asyncio.create_task(
            self._canvas_events.next_payload(),
            name="ava-v4-canvas-control",
        )
        try:
            while conversation_task is not None:
                pending: set[asyncio.Task[object]] = {
                    cast(asyncio.Task[object], conversation_task),
                    cast(asyncio.Task[object], canvas_task),
                }
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
                        if isinstance(frame, AvaServerControlFrameV4) and isinstance(
                            frame.event,
                            AudioChunkEventV4,
                        ):
                            pcm = await anext(conversation)
                            if not isinstance(pcm, AvaServerPcmFrameV4):
                                raise CoordinatorStateError(
                                    "audioChunk was not followed by binary PCM"
                                )
                            yield pcm
                        conversation_task = asyncio.create_task(
                            _next_outbound_frame(conversation)
                        )

                if canvas_task in done:
                    yield AvaServerControlFrameV4(
                        event=self._canvas_event(canvas_task.result())
                    )
                    canvas_task = asyncio.create_task(
                        self._canvas_events.next_payload(),
                        name="ava-v4-canvas-control",
                    )
        finally:
            tasks = tuple(
                task for task in (conversation_task, canvas_task) if task is not None
            )
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await cast(
                AsyncGenerator[AvaServerOutboundFrameV4, None],
                conversation,
            ).aclose()

    async def _conversation_frames(self) -> AsyncIterator[AvaServerOutboundFrameV4]:
        while not self._closed:
            turn_frames = self._one_turn_frames()
            try:
                async for frame in turn_frames:
                    yield frame
            finally:
                await cast(
                    AsyncGenerator[AvaServerOutboundFrameV4, None],
                    turn_frames,
                ).aclose()
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

    async def _one_turn_frames(self) -> AsyncIterator[AvaServerOutboundFrameV4]:
        parser = StreamingSpeechParser()
        ledger = DeliveredTextLedger()
        pending_binary_pcm = False
        turn_frames = self._one_turn_frames_impl(
            parser=parser,
            ledger=ledger,
        )
        try:
            async for frame in turn_frames:
                if isinstance(frame, AvaServerControlFrameV4) and isinstance(
                    frame.event,
                    AudioChunkEventV4,
                ):
                    pending_binary_pcm = True
                elif isinstance(frame, AvaServerPcmFrameV4):
                    if not pending_binary_pcm:
                        raise CoordinatorStateError(
                            "coordinator emitted binary PCM without its descriptor"
                        )
                    pending_binary_pcm = False
                yield frame
        except RealtimeProviderProtocolError as error:
            if self._terminal:
                raise
            if pending_binary_pcm:
                raise CoordinatorStateError(
                    "turn failed while a binary PCM frame was pending"
                ) from error
            await self._cancel_active_turn_work()
            self._last_error = error
            delivered_samples = self._bounded_rendered_sample_count()
            delivered_text = ledger.delivered_text(delivered_samples)
            self._schedule_failed_interaction_memory(
                delivered_sample_count=delivered_samples,
                delivered_text=delivered_text,
            )
            yield self._failed_frame(
                SpeechFailureCode.MODEL_INVALID,
                _format_failure_message(
                    "Realtime turn failed provider validation",
                    error,
                ),
                component=SpeechFailureComponent.REALTIME_PROVIDER,
                retryable=False,
                delivered_sample_count=delivered_samples,
                delivered_text=delivered_text,
            )
        except RealtimeProviderUnavailableError as error:
            if self._terminal:
                raise
            if pending_binary_pcm:
                raise CoordinatorStateError(
                    "turn failed while a binary PCM frame was pending"
                ) from error
            await self._cancel_active_turn_work()
            self._last_error = error
            delivered_samples = self._bounded_rendered_sample_count()
            delivered_text = ledger.delivered_text(delivered_samples)
            self._schedule_failed_interaction_memory(
                delivered_sample_count=delivered_samples,
                delivered_text=delivered_text,
            )
            yield self._failed_frame(
                SpeechFailureCode.MODEL_UNAVAILABLE,
                _format_failure_message(
                    "Realtime planning stream became unavailable",
                    error,
                ),
                component=SpeechFailureComponent.REALTIME_PROVIDER,
                retryable=True,
                delivered_sample_count=delivered_samples,
                delivered_text=delivered_text,
            )
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
            delivered_text = ledger.delivered_text(delivered_samples)
            self._schedule_failed_interaction_memory(
                delivered_sample_count=delivered_samples,
                delivered_text=delivered_text,
            )
            yield self._failed_frame(
                SpeechFailureCode.INTERNAL,
                "Ava's turn could not be completed safely.",
                component=SpeechFailureComponent.COORDINATOR,
                retryable=True,
                delivered_sample_count=delivered_samples,
                delivered_text=delivered_text,
            )
        finally:
            await cast(
                AsyncGenerator[AvaServerOutboundFrameV4, None],
                turn_frames,
            ).aclose()
            # The provider-input observer is turn-owned. It starts before
            # planning, so every turn exit must join it -- including an empty
            # provider response that returns before the playback-loop cleanup.
            await self._cancel_provider_input_observer()

    async def _one_turn_frames_impl(
        self,
        *,
        parser: StreamingSpeechParser,
        ledger: DeliveredTextLedger,
    ) -> AsyncIterator[AvaServerOutboundFrameV4]:
        turn_started = monotonic()
        occurred_at = self._occurrence_time_factory()
        if occurred_at.tzinfo is None:
            raise CoordinatorStateError("turn occurrence time must be timezone-aware")
        self._turn_occurred_at = occurred_at
        yield self._turn_created_frame()
        yield self._phase_frame(SpeechPhase.CONNECTING)

        if self._harness is None:
            raise CoordinatorStateError(
                "Realtime prompt must be prepared during media negotiation"
            )

        yield self._phase_frame(SpeechPhase.LISTENING)
        LOGGER.info(
            "realtime_turn_stage session_id=%s turn_id=%s stage=listening "
            "elapsed_ms=%.2f",
            self._session_id,
            self._turn_id,
            (monotonic() - turn_started) * 1000,
        )

        # Provider input observed while the previous turn was being
        # cancelled/playback-drained remains unowned until this explicit
        # turn-admission boundary. It must never be assigned merely because
        # this turn later became current.
        admit_pending = getattr(
            self._realtime,
            "admit_pending_input_items",
            None,
        )
        if callable(admit_pending):
            await admit_pending(turn_id=self._turn_id)
        self._pending_provider_input_item_ids.clear()
        self._interrupt_signalled_item_ids.clear()
        self._interruption_provider_item_id = None
        self._interruption_provider_audio_start_ms = None
        self._provider_speech_started_observed_at = None
        self._preemption_requested_at = None

        # Keep a provider-input observer alive before planning/synthesis. The
        # sideband reader is session-owned, while this turn observer owns only
        # the indexed input lifecycle; it must not wait until playback starts
        # and miss a VAD signal that arrived during synthesis.
        self._start_provider_input_observer()

        plan: _TurnResult | None = None
        synthesis_started = False
        provider_error: (
            tuple[
                SpeechFailureCode,
                SpeechFailureComponent,
                str,
                bool,
            ]
            | None
        ) = None
        planning_started = False
        planning_state = _PlanningState()
        planning_updates: asyncio.Queue[SpeechPhrase | None] = asyncio.Queue(maxsize=8)
        planning_task = asyncio.create_task(
            self._produce_plan(
                parser=parser,
                updates=planning_updates,
                state=planning_state,
            )
        )
        self._active_planning_task = planning_task
        provider_preempted = False
        provider_incomplete_reason: str | None = None

        try:
            while True:
                update_task = asyncio.create_task(
                    planning_updates.get(),
                    name="ava-planning-phrase-boundary",
                )
                provider_preempted = await self._provider_input_preempts_work(
                    update_task
                )
                if provider_preempted:
                    update_task.cancel()
                    await asyncio.gather(update_task, return_exceptions=True)
                    break
                update = update_task.result()
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
                    yield self._phase_frame(SpeechPhase.PLANNING)

                segment = update
                if ledger.segment_count == 0:
                    first_delta_at = planning_state.first_text_delta_at
                    if first_delta_at is not None:
                        elapsed_ms = max(
                            0.0,
                            (monotonic() - first_delta_at) * 1000,
                        )
                        LOGGER.info(
                            "realtime_latency session_id=%s turn_id=%s "
                            "stage=first_text_delta_to_first_speech_phrase "
                            "elapsed_ms=%.2f",
                            self._session_id,
                            self._turn_id,
                            elapsed_ms,
                            extra={
                                "ava_context": {
                                    "sessionId": self._session_id,
                                    "turnId": self._turn_id,
                                    "stage": "firstTextDeltaToFirstSpeechPhrase",
                                    "elapsedMs": elapsed_ms,
                                }
                            },
                        )
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
                    yield self._phase_frame(SpeechPhase.SYNTHESIZING)
                    yield self._audio_metadata_frame()

                try:
                    async for frame in self._speech_frames_for_segment(
                        segment,
                        ledger=ledger,
                    ):
                        yield frame
                        if self._cancel_event is not None and (
                            isinstance(frame, AvaServerPcmFrameV4)
                            or not (
                                isinstance(frame, AvaServerControlFrameV4)
                                and isinstance(frame.event, AudioChunkEventV4)
                            )
                        ):
                            break
                except (SpeechDeliveryError, ValueError) as error:
                    self._last_error = error
                    provider_error = (
                        SpeechFailureCode.SPEECH_INVALID,
                        SpeechFailureComponent.SPEECH_PROVIDER,
                        _format_failure_message(
                            "Speech PCM or alignment failed validation",
                            error,
                        ),
                        False,
                    )
                    break
                except Exception as error:
                    self._last_error = error
                    provider_error = (
                        SpeechFailureCode.SPEECH_UNAVAILABLE,
                        SpeechFailureComponent.SPEECH_PROVIDER,
                        _format_failure_message(
                            "Speech synthesis stream became unavailable",
                            error,
                        ),
                        True,
                    )
                    break
                if self._cancel_event is not None:
                    break
                if self._provider_preemption_pending:
                    provider_preempted = True
                    break
                if self._server_preemption_reason is not None:
                    break

            if (
                provider_error is not None
                or self._cancel_event is not None
                or provider_preempted
                or self._server_preemption_reason is not None
            ):
                await self._cancel_planning_work(
                    planning_task=planning_task,
                )
            else:
                plan = await planning_task
                self._active_planning_task = None
                if plan is not None:
                    LOGGER.info(
                        "realtime_plan_ready session_id=%s turn_id=%s segments=%d",
                        self._session_id,
                        self._turn_id,
                        len(plan.segments),
                    )
        except RealtimeProviderResponseIncompleteError as error:
            await self._cancel_provider_input_observer()
            await asyncio.gather(planning_task, return_exceptions=True)
            self._active_planning_task = None
            provider_incomplete_reason = error.reason
        except (
            RealtimeProviderProtocolError,
            StreamingTurnParseError,
            ValueError,
        ) as error:
            await self._cancel_planning_work(
                planning_task=planning_task,
            )
            self._last_error = error
            provider_error = (
                SpeechFailureCode.MODEL_INVALID,
                SpeechFailureComponent.REALTIME_PROVIDER,
                _format_failure_message(
                    "Realtime turn failed provider validation",
                    error,
                ),
                False,
            )
        except RealtimeProviderUnavailableError as error:
            await self._cancel_planning_work(
                planning_task=planning_task,
            )
            self._last_error = error
            provider_error = (
                SpeechFailureCode.MODEL_UNAVAILABLE,
                SpeechFailureComponent.REALTIME_PROVIDER,
                _format_failure_message(
                    "Realtime planning stream became unavailable",
                    error,
                ),
                True,
            )
        except Exception as error:
            await self._cancel_planning_work(
                planning_task=planning_task,
            )
            self._last_error = error
            provider_error = (
                SpeechFailureCode.INTERNAL,
                SpeechFailureComponent.COORDINATOR,
                _format_failure_message(
                    "Realtime planning coordinator encountered an unexpected "
                    "local failure",
                    error,
                ),
                False,
            )

        if provider_preempted:
            if self._audio_metadata_sent:
                yield self._provider_interrupt_requested_frame()
                async for frame in self._await_client_cancellation_after_interruption(
                    plan=plan,
                    ledger=ledger,
                ):
                    yield frame
            else:
                async for frame in self._cancel_frames(
                    plan=plan,
                    ledger=ledger,
                    reason="providerSpeechStarted",
                ):
                    yield frame
            return

        server_preemption_reason = self._server_preemption_reason
        if server_preemption_reason is not None:
            if self._audio_metadata_sent:
                yield self._interrupt_requested_frame(
                    stream_id=self._stream_id,
                    reason=server_preemption_reason,
                )
                async for frame in self._await_client_cancellation_after_interruption(
                    plan=plan,
                    ledger=ledger,
                ):
                    yield frame
            else:
                async for frame in self._cancel_frames(
                    plan=plan,
                    ledger=ledger,
                    reason=server_preemption_reason,
                ):
                    yield frame
            return

        if self._cancel_event is not None:
            async for frame in self._cancel_frames(
                plan=plan,
                ledger=ledger,
            ):
                yield frame
            return

        if provider_incomplete_reason is not None:
            cancellation_reason = (
                f"providerResponseIncomplete:{provider_incomplete_reason}"
            )
            if self._stopped_sample_count is not None:
                async for frame in self._cancel_frames(
                    plan=plan,
                    ledger=ledger,
                    reason=cancellation_reason,
                    provider_response_terminal=True,
                ):
                    yield frame
            elif self._playback_started:
                yield self._interrupt_requested_frame(
                    stream_id=self._stream_id,
                    reason=cancellation_reason,
                )
                async for frame in self._await_client_cancellation_after_interruption(
                    plan=plan,
                    ledger=ledger,
                    reason=cancellation_reason,
                    provider_response_terminal=True,
                ):
                    yield frame
            else:
                async for frame in self._cancel_frames(
                    plan=plan,
                    ledger=ledger,
                    reason=cancellation_reason,
                    provider_response_terminal=True,
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
            if self._cancel_event is None:
                self._cancel_event = CancelTurnEventV4.model_validate(
                    {
                        "protocolVersion": 4,
                        "sessionId": self._session_id,
                        "event": "cancelTurn",
                        "sequence": self._next_client_sequence,
                        "turnId": self._turn_id,
                        "requestId": self._request_id,
                        "reason": "interrupted_by_speech",
                    }
                )
            async for frame in self._cancel_frames(
                plan=plan,
                ledger=ledger,
            ):
                yield frame
            return

        try:
            ledger.validate_complete(
                received_sample_count=self.received_sample_count,
            )
        except SpeechDeliveryError as error:
            await self._cancel_active_turn_work()
            self._last_error = error
            yield self._failed_frame(
                SpeechFailureCode.SPEECH_INVALID,
                "Speech alignment did not cover the validated turn.",
                component=SpeechFailureComponent.SPEECH_PROVIDER,
                retryable=False,
                delivered_sample_count=self._bounded_rendered_sample_count(),
                delivered_text=ledger.delivered_text(
                    self._bounded_rendered_sample_count()
                ),
            )
            return

        # The client can close its streaming PCM input and begin completion
        # immediately. Optional specialist work continues while audio plays.
        yield self._phase_frame(SpeechPhase.SPEAKING)

        if self._cancel_event is not None:
            async for frame in self._cancel_frames(
                plan=plan,
                ledger=ledger,
            ):
                yield frame
            return

        if planning_state.canvas_generation_id is not None:
            target_segment = (
                plan.segments[1] if len(plan.segments) > 1 else plan.segments[0]
            )
            sample_offset = ledger.sample_offset_for_segment_start(target_segment.id)
            try:
                await self._canvas_jobs.request_presentation(
                    generation_id=planning_state.canvas_generation_id,
                    presentation_sample_offset=sample_offset,
                )
            except Exception as error:
                # Optional canvas scheduling cannot invalidate already-valid speech.
                self._last_error = error
                failure_id = self._failure_id_factory()
                LOGGER.exception(
                    "canvas_presentation_schedule_failed failure_id=%s "
                    "session_id=%s turn_id=%s generation_id=%s",
                    failure_id,
                    self._session_id,
                    self._turn_id,
                    planning_state.canvas_generation_id,
                )
        control_task: asyncio.Task[AvaClientEventUnionV4] | None = asyncio.create_task(
            self._controls.get(),
            name="ava-turn-playback-control",
        )
        preemption_task: asyncio.Task[bool] | None = asyncio.create_task(
            self._cancellation_requested.wait(),
            name="ava-turn-server-preemption",
        )
        progress_task: asyncio.Task[bool] | None = asyncio.create_task(
            self._playback_progress_changed.wait(),
            name="ava-turn-playback-progress-state",
        )
        waiting_for_client_cancellation = False
        try:
            while not self._terminal:
                input_task = self._active_provider_input_task
                wait_tasks = {
                    task
                    for task in (
                        control_task,
                        input_task,
                        preemption_task,
                        progress_task,
                    )
                    if task is not None
                }
                done, _ = await asyncio.wait(
                    wait_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if input_task is not None and input_task in done:
                    input_event = input_task.result()
                    self._active_provider_input_task = None
                    if self._classify_provider_input(input_event):
                        self._cancellation_requested.set()
                        if not waiting_for_client_cancellation:
                            waiting_for_client_cancellation = True
                            yield self._provider_interrupt_requested_frame()
                    else:
                        self._start_provider_input_observer()
                    continue

                if preemption_task is not None and preemption_task in done:
                    preemption_task.result()
                    preemption_task = None
                    reason = self._server_preemption_reason
                    if reason is not None and not waiting_for_client_cancellation:
                        waiting_for_client_cancellation = True
                        yield self._interrupt_requested_frame(
                            stream_id=self._stream_id,
                            reason=reason,
                        )
                    elif reason is None and self._cancel_event is not None:
                        waiting_for_client_cancellation = True
                    elif not waiting_for_client_cancellation:
                        raise CoordinatorStateError(
                            "speaking cancellation signal has no owning input, "
                            "server preemption, or client cancellation"
                        )
                    if control_task is not None and not control_task.done():
                        continue

                while control_task is not None and (
                    control_task.done() or self._controls.item_count > 0
                ):
                    try:
                        control = await control_task
                    except QueueClosed:
                        return

                    if isinstance(control, CancelTurnEventV4):
                        async for frame in self._cancel_frames(
                            plan=plan, ledger=ledger
                        ):
                            yield frame
                        return
                    control_task = asyncio.create_task(
                        self._controls.get(),
                        name="ava-turn-playback-control",
                    )

                if progress_task is not None and progress_task.done():
                    progress_task.result()
                    self._playback_progress_changed.clear()
                    if (
                        not waiting_for_client_cancellation
                        and not self._cancellation_requested.is_set()
                        and self._last_rendered_sample_count
                        == self.received_sample_count
                    ):
                        async for frame in self._complete_frames(
                            plan=plan,
                            ledger=ledger,
                        ):
                            yield frame
                        return
                    progress_task = asyncio.create_task(
                        self._playback_progress_changed.wait(),
                        name="ava-turn-playback-progress-state",
                    )
        finally:
            for task in (control_task, preemption_task, progress_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(
                    task
                    for task in (control_task, preemption_task, progress_task)
                    if task is not None
                ),
                return_exceptions=True,
            )
            await self._cancel_provider_input_observer()

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
        self._memory_commit_sequence += 1
        self._latest_memory_commit_sequence = self._memory_commit_sequence
        self._last_memory_decision = None
        self._turn_id = turn_id_factory()
        self._request_id = request_id_factory()
        self._stream_id = stream_id_factory()
        self._child_text = None
        self._child_text_source = None
        self._turn_occurred_at = None
        self._machine = RealtimeTurnStateMachine()
        self._harness = harness
        self._revision = 0
        self._next_audio_chunk_index = 0
        self._audio_metadata_sent = False
        self._playback_started = False
        self._last_rendered_sample_count = 0
        self._playback_progress_changed = asyncio.Event()
        self._stopped_sample_count = None
        self._cancel_event = None
        self._cancellation_requested = asyncio.Event()
        self._server_preemption_reason = None
        self._canvas_interaction_response_turn_id = None
        self._turn_canvas_interaction_input = None
        self._interruption_provider_item_id = None
        self._interruption_provider_audio_start_ms = None
        self._provider_speech_started_observed_at = None
        self._preemption_requested_at = None
        self._terminal = False
        self._canvas_status = "notRequested"
        self._terminal_failure_code = None
        self._last_failure_id = None
        self._last_failure_component = None
        self._last_error = None
        self._ensure_pending_context_installation(replace=True)
        if self._latest_canvas_interaction_intent is not None:
            self._canvas_interaction_response_wakeup.set()
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

    async def _refresh_canvas_context(
        self,
        *,
        reason: DynamicContextBuildReason = DynamicContextBuildReason.CANVAS_ACTIVATED,
    ) -> None:
        """Install canonical semantic canvas context for the next response.

        This is a session update only. The validated-interaction caller may
        separately request one idle response after the update is installed.
        """

        async with self._canvas_context_lock:
            if self._memory_snapshot is None or self._harness is None:
                raise CoordinatorStateError(
                    "canvas context cannot refresh before session negotiation"
                )
            harness = self._build_live_harness(reason=reason)
            self._pending_harness = harness
            self._pending_context_reason = reason
            try:
                installation_status = await self._realtime.prepare_if_idle(
                    harness=harness,
                    turn_id=self._turn_id,
                )
            except Exception as error:
                self._log_context_installation(
                    harness=harness,
                    reason=reason,
                    status="failed",
                    error_type=type(error).__name__,
                )
                raise
            if installation_status is not None:
                self._harness = harness
                self._pending_harness = None
                self._pending_context_reason = None
            else:
                self._ensure_pending_context_installation()
            self._log_context_installation(
                harness=harness,
                reason=reason,
                status=(
                    str(installation_status)
                    if installation_status is not None
                    else "deferred"
                ),
            )

    def _build_live_harness(
        self,
        *,
        reason: DynamicContextBuildReason,
    ) -> RealtimeHarness:
        dynamic_context = self._context_assembler.assemble(
            build_id=self._next_context_build_id(),
            reason=reason,
            age_band=self._age_band,
            timezone_id=self._timezone_id,
            current_time_utc=self._current_time_factory(),
            bootstrap_turns=(),
            warm_memories=self._warm_memories,
            canvas_context=self._current_canvas_context(),
        )
        harness = build_realtime_harness(dynamic_context=dynamic_context)
        self._log_dynamic_context_build(reason, harness)
        return harness

    def _log_dynamic_context_build(
        self,
        reason: DynamicContextBuildReason,
        harness: RealtimeHarness,
    ) -> None:
        context = harness.context
        LOGGER.info(
            "realtime_dynamic_context_built session_id=%s reason=%s "
            "context_build_id=%s final_utf8_bytes=%d estimated_tokens=%d "
            "bootstrap_turn_count=%d warm_reference_count=%d "
            "canvas_included=%s included_references=%d omitted_references=%d",
            self._session_id,
            reason,
            context.build_id,
            context.final_utf8_bytes,
            context.estimated_dynamic_tokens,
            context.included_bootstrap_turns,
            context.included_memory_references,
            context.canvas_included,
            context.included_memory_references,
            context.omitted_memory_references,
            extra={
                "ava_context": {
                    "sessionId": self._session_id,
                    "reason": str(reason),
                    "contextBuildId": context.build_id,
                    "finalUtf8Bytes": context.final_utf8_bytes,
                    "estimatedTokens": context.estimated_dynamic_tokens,
                    "bootstrapTurnCount": context.included_bootstrap_turns,
                    "warmReferenceCount": context.included_memory_references,
                    "canvasIncluded": context.canvas_included,
                    "includedReferenceCount": context.included_memory_references,
                    "omittedReferenceCount": context.omitted_memory_references,
                }
            },
        )

    def _next_context_build_id(self) -> str:
        self._context_build_sequence += 1
        return f"ctx-{self._context_build_sequence}"

    def _log_context_installation(
        self,
        *,
        harness: RealtimeHarness,
        reason: DynamicContextBuildReason,
        status: str,
        provider_call_id: str | None = None,
        error_type: str | None = None,
    ) -> None:
        resolved_provider_call_id = provider_call_id
        if resolved_provider_call_id is None and self._negotiated_call is not None:
            resolved_provider_call_id = self._negotiated_call.provider_call_id
        interaction = self._canvas_jobs.state.last_interaction
        LOGGER.info(
            "realtime_context_installation session_id=%s reason=%s status=%s "
            "context_build_id=%s provider_call_id=%s scene_id=%s "
            "scene_revision=%s interaction_revision=%s binding_id=%s "
            "error_type=%s",
            self._session_id,
            reason,
            status,
            harness.context.build_id,
            resolved_provider_call_id,
            interaction.scene_id if interaction is not None else None,
            interaction.scene_revision if interaction is not None else None,
            interaction.interaction_revision if interaction is not None else None,
            interaction.binding_id if interaction is not None else None,
            error_type,
            extra={
                "ava_context": {
                    "sessionId": self._session_id,
                    "reason": str(reason),
                    "status": status,
                    "contextBuildId": harness.context.build_id,
                    "providerCallId": resolved_provider_call_id,
                    "sceneId": interaction.scene_id if interaction else None,
                    "sceneRevision": (
                        interaction.scene_revision if interaction else None
                    ),
                    "interactionRevision": (
                        interaction.interaction_revision if interaction else None
                    ),
                    "bindingId": interaction.binding_id if interaction else None,
                    "errorType": error_type,
                }
            },
        )

    def _current_canvas_context(self) -> str | None:
        context = self._canvas_jobs.render_realtime_context()
        return context or None

    async def _install_pending_context_if_idle(
        self,
        *,
        turn_id: str,
    ) -> tuple[
        bool,
        RealtimeContextInstallationStatus | None,
        RealtimeHarness | None,
        DynamicContextBuildReason | None,
    ]:
        async with self._canvas_context_lock:
            harness = self._pending_harness
            reason = self._pending_context_reason
            if harness is None:
                return True, None, None, None
            try:
                installation_status = await self._realtime.prepare_if_idle(
                    harness=harness,
                    turn_id=turn_id,
                )
            except Exception as error:
                self._log_context_installation(
                    harness=harness,
                    reason=reason or DynamicContextBuildReason.CANVAS_ACTIVATED,
                    status="failed",
                    error_type=type(error).__name__,
                )
                raise
            if installation_status is not None:
                self._harness = harness
                self._pending_harness = None
                self._pending_context_reason = None
            return (
                installation_status is not None,
                installation_status,
                harness,
                reason,
            )

    def _ensure_pending_context_installation(self, *, replace: bool = False) -> None:
        if self._closed or self._pending_harness is None:
            return
        existing = self._context_installation_task
        if existing is not None and not existing.done():
            if not replace:
                return
            existing.cancel()
        turn_id = self._turn_id
        task = asyncio.create_task(
            self._run_pending_context_installation(turn_id=turn_id),
            name=f"ava-context-install-{turn_id}",
        )
        self._context_installation_task = task

    async def _run_pending_context_installation(self, *, turn_id: str) -> None:
        task = asyncio.current_task()
        try:
            ready = await self._realtime.wait_until_canvas_interaction_response_ready(
                turn_id=turn_id
            )
            if not ready or self._closed or self._turn_id != turn_id:
                return
            (
                _,
                installation_status,
                harness,
                reason,
            ) = await self._install_pending_context_if_idle(turn_id=turn_id)
            if harness is not None:
                self._log_context_installation(
                    harness=harness,
                    reason=reason or DynamicContextBuildReason.CANVAS_ACTIVATED,
                    status=(
                        str(installation_status)
                        if installation_status is not None
                        else "deferred"
                    ),
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.warning(
                "realtime_context_installation_failed session_id=%s turn_id=%s "
                "error_type=%s",
                self._session_id,
                turn_id,
                type(error).__name__,
            )
        finally:
            if self._context_installation_task is task:
                self._context_installation_task = None

    def _ensure_canvas_interaction_response_worker(self) -> None:
        task = self._canvas_interaction_response_task
        if task is not None and not task.done():
            return
        self._canvas_interaction_response_task = asyncio.create_task(
            self._run_canvas_interaction_response_worker(),
            name="ava-canvas-interaction-response",
        )

    async def _run_canvas_interaction_response_worker(self) -> None:
        while not self._closed:
            await self._canvas_interaction_response_wakeup.wait()
            self._canvas_interaction_response_wakeup.clear()
            while not self._closed:
                interaction = self._latest_canvas_interaction_intent
                if interaction is None:
                    break
                try:
                    await self._refresh_canvas_context(
                        reason=(DynamicContextBuildReason.CANVAS_INTERACTION_COMMITTED)
                    )
                    if not self._is_latest_canvas_interaction(interaction):
                        continue
                    turn_id = await self._interaction_response_ready_turn(interaction)
                    if turn_id is None:
                        break
                    if not self._is_latest_canvas_interaction(interaction):
                        continue
                    installed, _, _, _ = await self._install_pending_context_if_idle(
                        turn_id=turn_id
                    )
                    if not installed:
                        if self._is_latest_canvas_interaction(interaction):
                            await self._realtime.wait_until_canvas_interaction_response_ready(
                                turn_id=turn_id
                            )
                        continue
                    self._canvas_interaction_response_turn_id = turn_id
                    if self._turn_id != turn_id or self._terminal:
                        self._canvas_interaction_response_turn_id = None
                        continue
                    if self._turn_canvas_interaction_input is not None:
                        raise CoordinatorStateError(
                            "Realtime turn already owns a canvas interaction input"
                        )
                    self._turn_canvas_interaction_input = interaction
                    triggered = (
                        await self._realtime.request_canvas_interaction_response(
                            turn_id=turn_id,
                            interaction=interaction,
                        )
                    )
                    superseded = not self._is_latest_canvas_interaction(interaction)
                    if not triggered:
                        if self._turn_canvas_interaction_input == interaction:
                            self._turn_canvas_interaction_input = None
                        self._canvas_interaction_response_turn_id = None
                        if superseded:
                            break
                        continue
                    if superseded:
                        self._request_server_preemption(
                            reason="canvasInteractionSuperseded"
                        )
                    else:
                        self._latest_canvas_interaction_intent = None
                    LOGGER.info(
                        "canvas_interaction_response_decided session_id=%s "
                        "turn_id=%s scene_id=%s scene_revision=%s "
                        "interaction_revision=%s triggered=%s superseded=%s",
                        self._session_id,
                        turn_id,
                        interaction.scene_id,
                        interaction.scene_revision,
                        interaction.interaction_revision,
                        triggered,
                        superseded,
                    )
                    break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOGGER.exception(
                        "canvas_interaction_response_failed session_id=%s "
                        "scene_id=%s scene_revision=%s interaction_revision=%s",
                        self._session_id,
                        interaction.scene_id,
                        interaction.scene_revision,
                        interaction.interaction_revision,
                    )
                    if self._is_latest_canvas_interaction(interaction):
                        self._latest_canvas_interaction_intent = None
                    self._canvas_interaction_response_turn_id = None
                    break

    async def _interaction_response_ready_turn(
        self,
        interaction: RecordedSvgCanvasInteraction,
    ) -> str | None:
        while not self._closed and self._is_latest_canvas_interaction(interaction):
            if self._terminal:
                self._canvas_interaction_response_wakeup.clear()
                await self._canvas_interaction_response_wakeup.wait()
                self._canvas_interaction_response_wakeup.clear()
                continue
            turn_id = self._turn_id
            readiness = asyncio.create_task(
                self._realtime.wait_until_canvas_interaction_response_ready(
                    turn_id=turn_id
                ),
                name="ava-canvas-interaction-response-ready",
            )
            changed = asyncio.create_task(
                self._canvas_interaction_response_wakeup.wait(),
                name="ava-canvas-interaction-response-changed",
            )
            try:
                done, _ = await asyncio.wait(
                    {readiness, changed},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if changed in done:
                    self._canvas_interaction_response_wakeup.clear()
                    readiness.cancel()
                    await asyncio.gather(readiness, return_exceptions=True)
                    continue
                changed.cancel()
                await asyncio.gather(changed, return_exceptions=True)
                if (
                    readiness.result()
                    and self._turn_id == turn_id
                    and not self._terminal
                ):
                    return turn_id
                continue
            finally:
                for task in (readiness, changed):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(readiness, changed, return_exceptions=True)
        return None

    def _is_latest_canvas_interaction(
        self,
        interaction: RecordedSvgCanvasInteraction,
    ) -> bool:
        latest = self._latest_canvas_interaction_intent
        return latest is not None and (
            latest.scene_id,
            latest.scene_revision,
            latest.interaction_revision,
        ) == (
            interaction.scene_id,
            interaction.scene_revision,
            interaction.interaction_revision,
        )

    def _request_server_preemption(self, *, reason: str) -> None:
        if self._terminal:
            return
        if self._server_preemption_reason is None:
            self._server_preemption_reason = reason
            self._preemption_requested_at = monotonic()
        self._cancellation_requested.set()

    async def _produce_plan(
        self,
        *,
        parser: StreamingSpeechParser,
        updates: asyncio.Queue[SpeechPhrase | None],
        state: _PlanningState,
    ) -> _TurnResult | None:
        try:
            async for event in self._turn_events_until_cancelled():
                if isinstance(event, RealtimeSpeechTextDelta):
                    if event.delta and state.first_text_delta_at is None:
                        state.first_text_delta_at = monotonic()
                    for segment in parser.feed(event.delta):
                        await updates.put(segment)
                    continue
                if isinstance(event, RealtimeRejectedUtilityToolCall):
                    for segment in parser.finish_response_chunk():
                        await updates.put(segment)
                    self._claim_rejected_utility(event, state=state)
                    await self._realtime.complete_utility_tool(
                        turn_id=self._turn_id,
                        call_id=event.call_id,
                        output=RejectedUtilityToolOutputV4(reason=event.reason),
                        harness=self.harness,
                    )
                    continue
                if isinstance(event, RealtimeSearchMemoryToolCall):
                    for segment in parser.finish_response_chunk():
                        await updates.put(segment)
                    self._claim_utility(
                        RealtimeUtilityToolKind.SEARCH_MEMORY,
                        state=state,
                    )
                    await self._run_search_memory_tool(event)
                    continue
                if isinstance(event, RealtimeGetCurrentTimeToolCall):
                    for segment in parser.finish_response_chunk():
                        await updates.put(segment)
                    self._claim_utility(
                        RealtimeUtilityToolKind.GET_CURRENT_TIME,
                        state=state,
                    )
                    await self._run_get_current_time_tool(event)
                    continue
                if isinstance(event, RealtimeRejectedCanvasToolCall):
                    for segment in parser.finish_response_chunk():
                        await updates.put(segment)
                    self._claim_canvas(event.call_id, state=state)
                    self._canvas_status = "rejected"
                    LOGGER.warning(
                        "canvas_tool_arguments_rejected session_id=%s "
                        "turn_id=%s call_id=%s reason=%s",
                        self._session_id,
                        self._turn_id,
                        event.call_id,
                        event.reason,
                    )
                    await self._realtime.complete_canvas_tool(
                        turn_id=self._turn_id,
                        call_id=event.call_id,
                        output=InteractiveCanvasToolOutputV4.model_validate(
                            {
                                "status": InteractiveCanvasToolStatusV4.REJECTED,
                                "reason": CANVAS_MODEL_SAFE_REJECTION_REASON,
                            }
                        ),
                    )
                    await self._canvas_events.tool_call_rejected(
                        call_id=event.call_id,
                        source_turn_id=self._turn_id,
                        message=event.reason,
                    )
                    continue
                if isinstance(event, RealtimeCancelCanvasToolCall):
                    for segment in parser.finish_response_chunk():
                        await updates.put(segment)
                    self._claim_canvas(event.call_id, state=state)
                    await self._run_canvas_cancellation_tool(event, state=state)
                    continue
                if not isinstance(event, RealtimeCanvasToolCall):
                    raise CoordinatorStateError(
                        f"unsupported Realtime provider event {type(event).__name__}"
                    )
                for segment in parser.finish_response_chunk():
                    await updates.put(segment)
                self._claim_canvas(event.call_id, state=state)
                await self._run_canvas_tool(event, state=state)

            if self._cancel_event is not None:
                return None
            for segment in parser.finish(allow_empty=True):
                await updates.put(segment)
            segments = parser.emitted_segments
            if not segments:
                return None
            child_transcript = self._realtime.available_child_transcript(
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
            return _TurnResult(
                segments=segments,
                child_turn_summary=child_summary,
            )
        finally:
            updates.put_nowait(None)

    def _claim_utility(
        self,
        tool_kind: RealtimeUtilityToolKind,
        *,
        state: _PlanningState,
    ) -> None:
        if (
            state.tool_steps_used != 0
            or state.utility_tool_kind is not None
            or state.canvas_terminal_used
        ):
            raise StreamingTurnParseError(
                "Realtime utility tool violated the bounded turn chain"
            )
        state.tool_steps_used = 1
        state.utility_tool_kind = tool_kind

    def _claim_rejected_utility(
        self,
        call: RealtimeRejectedUtilityToolCall,
        *,
        state: _PlanningState,
    ) -> None:
        if state.canvas_terminal_used or state.tool_steps_used >= 2:
            raise StreamingTurnParseError(
                "Realtime rejected utility exceeded the bounded turn chain"
            )
        state.tool_steps_used += 1
        if state.utility_tool_kind is None:
            state.utility_tool_kind = call.tool_kind

    def _claim_canvas(self, call_id: str, *, state: _PlanningState) -> None:
        if (
            state.canvas_terminal_used
            or state.canvas_call_id is not None
            or state.tool_steps_used >= 2
        ):
            raise StreamingTurnParseError(
                "Realtime requested a canvas outside the bounded turn chain"
            )
        if state.tool_steps_used == 1 and state.utility_tool_kind is None:
            raise StreamingTurnParseError(
                "Realtime canvas continuation lost utility ownership"
            )
        state.canvas_call_id = call_id
        state.canvas_terminal_used = True
        state.tool_steps_used += 1

    async def _run_search_memory_tool(
        self,
        call: RealtimeSearchMemoryToolCall,
    ) -> None:
        output: SearchMemoryToolOutputV4
        candidates: list[MemorySearchCandidate] = []
        try:
            lower, upper = _local_date_search_bounds(
                from_date=call.request.from_date,
                to_date=call.request.to_date,
                timezone_id=self._timezone_id,
            )
            candidates = await self._memory.search_memory_candidates(
                subject=self._subject,
                query=call.request.query,
                limit=5,
                from_instant=lower,
                to_instant=upper,
            )
        except ValueError:
            output = SearchMemoryToolOutputV4(
                status=SearchMemoryToolStatus.REJECTED,
                reason="The historical-memory query or date range was invalid.",
            )
        except Exception as error:
            LOGGER.warning(
                "realtime_semantic_tool_failed session_id=%s turn_id=%s "
                "tool_kind=%s step=1 error_type=%s",
                self._session_id,
                self._turn_id,
                RealtimeUtilityToolKind.SEARCH_MEMORY,
                type(error).__name__,
            )
            output = SearchMemoryToolOutputV4(
                status=SearchMemoryToolStatus.UNAVAILABLE,
                reason="Historical memory is temporarily unavailable.",
            )
        else:
            references = [_search_tool_reference(candidate) for candidate in candidates]
            output = SearchMemoryToolOutputV4(
                status=(
                    SearchMemoryToolStatus.AVAILABLE
                    if references
                    else SearchMemoryToolStatus.NO_MATCHES
                ),
                references=references,
            )
        await self._realtime.complete_utility_tool(
            turn_id=self._turn_id,
            call_id=call.call_id,
            output=output,
            harness=self.harness,
        )
        result_source_kinds = [str(candidate.source_kind) for candidate in candidates]
        result_source_ids = [candidate.source_id for candidate in candidates]
        LOGGER.info(
            "realtime_semantic_tool_completed session_id=%s turn_id=%s "
            "tool_call_id=%s tool_kind=%s step=1 query_char_count=%d "
            "has_from_date=%s has_to_date=%s raw_candidate_count=%d "
            "result_count=%d result_source_kinds=%s result_source_ids=%s status=%s",
            self._session_id,
            self._turn_id,
            call.call_id,
            RealtimeUtilityToolKind.SEARCH_MEMORY,
            len(call.request.query),
            call.request.from_date is not None,
            call.request.to_date is not None,
            len(candidates),
            len(output.references),
            result_source_kinds,
            result_source_ids,
            output.status,
            extra={
                "ava_context": {
                    "sessionId": self._session_id,
                    "turnId": self._turn_id,
                    "toolCallId": call.call_id,
                    "toolKind": str(RealtimeUtilityToolKind.SEARCH_MEMORY),
                    "step": 1,
                    "queryCharCount": len(call.request.query),
                    "hasFromDate": call.request.from_date is not None,
                    "hasToDate": call.request.to_date is not None,
                    "rawCandidateCount": len(candidates),
                    "resultCount": len(output.references),
                    "resultSourceKinds": result_source_kinds,
                    "resultSourceIds": result_source_ids,
                    "status": str(output.status),
                }
            },
        )

    async def _run_get_current_time_tool(
        self,
        call: RealtimeGetCurrentTimeToolCall,
    ) -> None:
        now = self._current_time_factory()
        if now.tzinfo is None:
            raise CoordinatorStateError(
                "current time factory returned a timezone-naive datetime"
            )
        local = now.astimezone(ZoneInfo(self._timezone_id))
        output = GetCurrentTimeToolOutputV4.model_validate(
            {
                "localIsoDateTime": local.isoformat(),
                "weekday": local.strftime("%A"),
                "daypart": _clock_daypart(local.hour),
                "timezoneId": self._timezone_id,
            }
        )
        await self._realtime.complete_utility_tool(
            turn_id=self._turn_id,
            call_id=call.call_id,
            output=output,
            harness=self.harness,
        )
        LOGGER.info(
            "realtime_semantic_tool_completed session_id=%s turn_id=%s "
            "tool_kind=%s step=1 result_count=1",
            self._session_id,
            self._turn_id,
            RealtimeUtilityToolKind.GET_CURRENT_TIME,
        )

    async def _run_canvas_tool(
        self,
        call: RealtimeCanvasToolCall,
        *,
        state: _PlanningState,
    ) -> None:
        state.canvas_started = monotonic()
        self._canvas_status = "requested"
        source_transcript = self._realtime.available_child_transcript(
            turn_id=self._turn_id,
        )
        bounded_source_transcript = (
            _bounded_child_transcript(source_transcript)
            if source_transcript is not None
            else None
        )
        LOGGER.info(
            "canvas_source_grounding_resolved session_id=%s turn_id=%s "
            "transcript_available=%s",
            self._session_id,
            self._turn_id,
            bounded_source_transcript is not None,
        )
        output = await self._canvas_jobs.accept_tool_call(
            source_turn_id=self._turn_id,
            source_transcript=bounded_source_transcript,
            call=call,
            complete_tool=self._complete_canvas_tool,
        )
        if output.status == InteractiveCanvasToolStatusV4.ACCEPTED:
            generation_id = output.generation_id
            if generation_id is None:  # guarded by the validated model
                raise CoordinatorStateError(
                    "accepted protocol-v4 canvas output lost generationId"
                )
            state.canvas_generation_id = generation_id
            self._canvas_status = "accepted"
            LOGGER.info(
                "canvas_specialist_accepted_async session_id=%s turn_id=%s "
                "call_id=%s generation_id=%s elapsed_ms=%.2f",
                self._session_id,
                self._turn_id,
                call.call_id,
                generation_id,
                (monotonic() - state.canvas_started) * 1000,
            )
            await self._refresh_canvas_context()
            return
        self._canvas_status = "rejected"

    async def _run_canvas_cancellation_tool(
        self,
        call: RealtimeCancelCanvasToolCall,
        *,
        state: _PlanningState,
    ) -> None:
        state.canvas_started = monotonic()
        retired = await self._canvas_jobs.cancel_pending()
        await self._realtime.complete_canvas_tool(
            turn_id=self._turn_id,
            call_id=call.call_id,
            output=InteractiveCanvasCancellationToolOutputV4.model_validate(
                {"status": InteractiveCanvasToolStatusV4.ACCEPTED}
            ),
        )
        self._canvas_status = "cancelled"
        LOGGER.info(
            "canvas_pending_work_cancelled session_id=%s turn_id=%s call_id=%s "
            "retired_count=%d elapsed_ms=%.2f",
            self._session_id,
            self._turn_id,
            call.call_id,
            len(retired),
            (monotonic() - state.canvas_started) * 1000,
        )
        await self._refresh_canvas_context()

    async def _complete_canvas_tool(
        self,
        turn_id: str,
        call_id: str,
        output: InteractiveCanvasToolOutputV4,
    ) -> None:
        await self._realtime.complete_canvas_tool(
            turn_id=turn_id,
            call_id=call_id,
            output=output,
        )

    async def _cancel_planning_work(
        self,
        *,
        planning_task: asyncio.Task[_TurnResult | None],
    ) -> None:
        await self._cancel_active_turn_work()
        # Local references may have completed before they were registered as
        # active; gathering them here still retrieves every terminal result.
        await asyncio.gather(planning_task, return_exceptions=True)

    async def _cancel_active_turn_work(self) -> None:
        planning_task = self._active_planning_task
        await self._cancel_provider_input_observer()
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
        await self._schedule_interrupted_canvas_presentation()
        self._active_planning_task = None

    async def _cancel_provider_input_observer(self) -> None:
        task = self._active_provider_input_task
        if task is None:
            return

        # Claim the task before awaiting it so concurrent turn/session cleanup
        # is idempotent and cannot cancel the same observer twice.
        self._active_provider_input_task = None
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    @property
    def _provider_preemption_pending(self) -> bool:
        return (
            self._interruption_provider_item_id is not None
            and self._cancel_event is None
            and self._cancellation_requested.is_set()
        )

    def _start_provider_input_observer(self) -> None:
        if self._active_provider_input_task is not None:
            raise CoordinatorStateError(
                "provider input observer already owns this turn boundary"
            )
        self._active_provider_input_task = asyncio.create_task(
            self._realtime.next_input_event(),
            name="ava-provider-input-observer",
        )

    async def _provider_input_preempts_work(
        self,
        work_task: asyncio.Task[_WorkResult],
    ) -> bool:
        """Give an explicitly unowned speech-start priority over pending work."""

        while True:
            input_task = self._active_provider_input_task
            if input_task is None:
                await work_task
                return False
            try:
                done, _ = await asyncio.wait(
                    {work_task, input_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if input_task not in done:
                    return False
                self._active_provider_input_task = None
                input_event = input_task.result()
                if self._classify_provider_input(input_event):
                    self._cancellation_requested.set()
                    return True
                self._start_provider_input_observer()
                if work_task in done:
                    return False
            except BaseException:
                if not work_task.done():
                    work_task.cancel()
                await asyncio.gather(work_task, return_exceptions=True)
                raise

    def _classify_provider_input(
        self,
        input_event: RealtimeProviderInputEvent,
    ) -> bool:
        self._pending_provider_input_item_ids.add(input_event.item_id)
        if input_event.event_type != "input_audio_buffer.speech_started":
            return False
        owner_turn_id = self._realtime.input_item_owner(item_id=input_event.item_id)
        if owner_turn_id == self._turn_id:
            LOGGER.info(
                "realtime_originating_input_ignored session_id=%s turn_id=%s "
                "provider_input_item_id=%s",
                self._session_id,
                self._turn_id,
                input_event.item_id,
            )
            return False
        if owner_turn_id is not None:
            LOGGER.info(
                "realtime_stale_input_ignored session_id=%s turn_id=%s "
                "owner_turn_id=%s provider_input_item_id=%s",
                self._session_id,
                self._turn_id,
                owner_turn_id,
                input_event.item_id,
            )
            return False
        if input_event.item_id in self._interrupt_signalled_item_ids:
            return False
        self._interrupt_signalled_item_ids.add(input_event.item_id)
        if self._interruption_provider_item_id is None:
            self._interruption_provider_item_id = input_event.item_id
            self._interruption_provider_audio_start_ms = input_event.audio_start_ms
            self._provider_speech_started_observed_at = monotonic()
            self._preemption_requested_at = self._provider_speech_started_observed_at
        self._latest_canvas_interaction_intent = None
        self._canvas_interaction_response_wakeup.set()
        LOGGER.info(
            "realtime_child_intent_observed session_id=%s turn_id=%s "
            "provider_input_item_id=%s audio_start_ms=%s",
            self._session_id,
            self._turn_id,
            input_event.item_id,
            input_event.audio_start_ms,
            extra={
                "ava_context": {
                    "sessionId": self._session_id,
                    "turnId": self._turn_id,
                    "providerInputItemId": input_event.item_id,
                    "providerAudioStartMs": input_event.audio_start_ms,
                }
            },
        )
        return True

    def _provider_interrupt_requested_frame(self) -> AvaServerControlFrameV4:
        observed_at = self._provider_speech_started_observed_at
        elapsed_ms = (
            max(0.0, (monotonic() - observed_at) * 1000)
            if observed_at is not None
            else None
        )
        LOGGER.info(
            "realtime_interruption_requested session_id=%s turn_id=%s "
            "stream_id=%s provider_input_item_id=%s audio_start_ms=%s",
            self._session_id,
            self._turn_id,
            self._stream_id,
            self._interruption_provider_item_id,
            self._interruption_provider_audio_start_ms,
            extra={
                "ava_context": {
                    "sessionId": self._session_id,
                    "turnId": self._turn_id,
                    "streamId": self._stream_id,
                    "providerInputItemId": self._interruption_provider_item_id,
                    "providerAudioStartMs": (
                        self._interruption_provider_audio_start_ms
                    ),
                    "interruptionReason": "providerSpeechStarted",
                }
            },
        )
        LOGGER.info(
            "realtime_latency session_id=%s turn_id=%s "
            "stage=provider_speech_started_to_interrupt_requested "
            "elapsed_ms=%s provider_input_item_id=%s",
            self._session_id,
            self._turn_id,
            f"{elapsed_ms:.2f}" if elapsed_ms is not None else None,
            self._interruption_provider_item_id,
            extra={
                "ava_context": {
                    "sessionId": self._session_id,
                    "turnId": self._turn_id,
                    "stage": "providerSpeechStartedToInterruptRequested",
                    "elapsedMs": elapsed_ms,
                    "providerInputItemId": self._interruption_provider_item_id,
                }
            },
        )
        return self._interrupt_requested_frame(
            stream_id=self._stream_id,
            reason="providerSpeechStarted",
        )

    async def _await_client_cancellation_after_interruption(
        self,
        *,
        plan: _TurnResult | None,
        ledger: DeliveredTextLedger,
        reason: str | None = None,
        provider_response_terminal: bool = False,
    ) -> AsyncIterator[AvaServerOutboundFrameV4]:
        while True:
            try:
                control = await self._controls.get()
            except QueueClosed:
                return
            if not isinstance(control, CancelTurnEventV4):
                continue
            if self._stopped_sample_count is None:
                raise CoordinatorStateError(
                    "client cancellation after interruptRequested requires "
                    "playbackStopped"
                )
            async for frame in self._cancel_frames(
                plan=plan,
                ledger=ledger,
                reason=reason,
                provider_response_terminal=provider_response_terminal,
            ):
                yield frame
            return

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
        context_installation_task = self._context_installation_task
        self._context_installation_task = None
        if (
            context_installation_task is not None
            and not context_installation_task.done()
        ):
            context_installation_task.cancel()
        if context_installation_task is not None:
            await asyncio.gather(
                context_installation_task,
                return_exceptions=True,
            )
        interaction_task = self._canvas_interaction_response_task
        self._canvas_interaction_response_task = None
        self._canvas_interaction_response_wakeup.set()
        if interaction_task is not None and not interaction_task.done():
            interaction_task.cancel()
        if interaction_task is not None:
            await asyncio.gather(interaction_task, return_exceptions=True)
        await self._cancel_active_turn_work()
        memory_tasks_by_turn = dict(self._memory_commit_tasks)
        if memory_tasks_by_turn:
            _, pending = await asyncio.wait(
                tuple(memory_tasks_by_turn.values()),
                timeout=self._memory_commit_drain_timeout_seconds,
            )
            if pending:
                pending_turns = {
                    task: turn_id
                    for turn_id, task in memory_tasks_by_turn.items()
                    if task in pending
                }
                for task, turn_id in pending_turns.items():
                    failure_id = self._failure_id_factory()
                    LOGGER.warning(
                        "memory_commit_drain_timed_out failure_id=%s "
                        "session_id=%s turn_id=%s component=%s "
                        "reason_category=%s timeout_seconds=%.2f",
                        failure_id,
                        self._session_id,
                        turn_id,
                        SpeechFailureComponent.MEMORY_STORE,
                        "shutdownDrainTimeout",
                        self._memory_commit_drain_timeout_seconds,
                        extra={
                            "ava_context": {
                                "failureId": failure_id,
                                "component": str(SpeechFailureComponent.MEMORY_STORE),
                                "sessionId": self._session_id,
                                "turnId": turn_id,
                                "reasonCategory": "shutdownDrainTimeout",
                                "timeoutSeconds": (
                                    self._memory_commit_drain_timeout_seconds
                                ),
                            }
                        },
                    )
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        self._memory_commit_tasks.clear()
        self._recent_live_echo_suppression_turn_ids.clear()
        if not self._terminal:
            await self._realtime.cancel(turn_id=self._turn_id)
            await self._speech.cancel(turn_id=self._turn_id)
        await self._realtime.close()
        await self._speech.close()
        await self._canvas_jobs.close()
        self._controls.close()

    async def _speech_frames_for_segment(
        self,
        segment: SpeechPhrase,
        *,
        ledger: DeliveredTextLedger,
    ) -> AsyncIterator[AvaServerOutboundFrameV4]:
        segment_sample_offset = self.received_sample_count
        turn_id = self._turn_id
        async for item in self._speech_items_until_cancelled(
            segment,
            turn_id=turn_id,
        ):
            if isinstance(item, SynthesizedAlignmentBatch):
                spans = tuple(
                    SpeechAlignment.model_validate(
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
                yield AvaServerPcmFrameV4(
                    descriptor=cast(AudioChunkEventV4, descriptor_frame.event),
                    pcm=item.pcm,
                )
                continue

            raise SpeechDeliveryError(f"unsupported speech item {type(item).__name__}")

    async def _speech_items_until_cancelled(
        self,
        segment: SpeechPhrase,
        *,
        turn_id: str,
    ) -> AsyncIterator[SynthesizedSpeechItem]:
        if self._cancellation_requested.is_set():
            return
        speech_items = self._speech.synthesize(
            segment,
            turn_id=turn_id,
        ).__aiter__()
        try:
            while True:
                if self._cancellation_requested.is_set():
                    await self._speech.cancel(turn_id=turn_id)
                    return
                next_item = asyncio.create_task(_next_speech_item(speech_items))
                cancellation = asyncio.create_task(self._cancellation_requested.wait())
                try:
                    while True:
                        input_task = self._active_provider_input_task
                        wait_tasks: set[asyncio.Task[object]] = {
                            cast(asyncio.Task[object], next_item),
                            cast(asyncio.Task[object], cancellation),
                        }
                        if input_task is not None:
                            wait_tasks.add(cast(asyncio.Task[object], input_task))
                        done, _ = await asyncio.wait(
                            wait_tasks,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if input_task is not None and input_task in done:
                            self._active_provider_input_task = None
                            input_event = input_task.result()
                            if self._classify_provider_input(input_event):
                                self._cancellation_requested.set()
                                try:
                                    await self._speech.cancel(turn_id=turn_id)
                                finally:
                                    requested_at = self._preemption_requested_at
                                    elapsed_ms = (
                                        max(
                                            0.0,
                                            (monotonic() - requested_at) * 1000,
                                        )
                                        if requested_at is not None
                                        else None
                                    )
                                    LOGGER.info(
                                        "realtime_latency session_id=%s turn_id=%s "
                                        "stage=preemption_to_elevenlabs_cancelled "
                                        "elapsed_ms=%s",
                                        self._session_id,
                                        turn_id,
                                        (
                                            f"{elapsed_ms:.2f}"
                                            if elapsed_ms is not None
                                            else None
                                        ),
                                        extra={
                                            "ava_context": {
                                                "sessionId": self._session_id,
                                                "turnId": turn_id,
                                                "stage": (
                                                    "preemptionToElevenLabsCancelled"
                                                ),
                                                "elapsedMs": elapsed_ms,
                                            }
                                        },
                                    )
                                    next_item.cancel()
                                    cancellation.cancel()
                                    await asyncio.gather(
                                        next_item,
                                        cancellation,
                                        return_exceptions=True,
                                    )
                                return
                            self._start_provider_input_observer()
                            if next_item not in done and cancellation not in done:
                                continue
                        break
                except BaseException:
                    next_item.cancel()
                    cancellation.cancel()
                    await asyncio.gather(
                        next_item,
                        cancellation,
                        return_exceptions=True,
                    )
                    raise

                if cancellation in done:
                    try:
                        await self._speech.cancel(turn_id=turn_id)
                    finally:
                        next_item.cancel()
                        await asyncio.gather(next_item, return_exceptions=True)
                    return

                cancellation.cancel()
                await asyncio.gather(cancellation, return_exceptions=True)
                try:
                    yield next_item.result()
                except StopAsyncIteration:
                    return
        finally:
            close_iterator = getattr(speech_items, "aclose", None)
            if close_iterator is not None:
                await close_iterator()

    async def _complete_frames(
        self,
        *,
        plan: _TurnResult,
        ledger: DeliveredTextLedger,
    ) -> AsyncIterator[AvaServerOutboundFrameV4]:
        delivered_samples = self.received_sample_count
        delivered_text = ledger.delivered_text(delivered_samples)
        self._schedule_memory_commit(
            turn_id=self._turn_id,
            plan=plan,
            delivered_sample_count=delivered_samples,
            delivered_text=delivered_text,
            terminal=DeliveredTurnTerminal.COMPLETED,
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
        plan: _TurnResult | None,
        ledger: DeliveredTextLedger,
        reason: str | None = None,
        provider_response_terminal: bool = False,
    ) -> AsyncIterator[AvaServerOutboundFrameV4]:
        cancel = self._cancel_event
        cancellation_reason = reason or (cancel.reason if cancel is not None else None)
        if cancellation_reason is None:
            raise CoordinatorStateError(
                "cancellation frames require a client cancel or provider preemption"
            )
        yield self._phase_frame(SpeechPhase.CANCELLING)
        if not provider_response_terminal:
            await self._realtime.cancel(turn_id=self._turn_id)
        await self._speech.cancel(turn_id=self._turn_id)
        await self._schedule_interrupted_canvas_presentation()

        delivered_samples = self._bounded_rendered_sample_count()
        delivered_text = ledger.delivered_text(delivered_samples)
        if plan is not None or self._turn_canvas_interaction_input is not None:
            self._schedule_memory_commit(
                turn_id=self._turn_id,
                plan=plan,
                delivered_sample_count=delivered_samples,
                delivered_text=delivered_text,
                terminal=DeliveredTurnTerminal.CANCELLED,
            )

        yield self._terminal_frame(
            {
                "event": "turnCancelled",
                "deliveredSampleCount": delivered_samples,
                "deliveredText": delivered_text,
                "reason": cancellation_reason,
            }
        )

    def _schedule_memory_commit(
        self,
        *,
        turn_id: str,
        plan: _TurnResult | None,
        delivered_sample_count: int,
        delivered_text: str,
        terminal: DeliveredTurnTerminal,
    ) -> None:
        if turn_id in self._memory_commit_tasks:
            raise CoordinatorStateError(
                "one turn cannot own multiple durable memory commit tasks"
            )
        occurred_at = self._require_turn_occurred_at()
        expected_reset_revision = self._require_memory_reset_revision()
        interaction = self._turn_canvas_interaction_input
        canvas_interaction: DeliveredCanvasInteraction | None = None
        if interaction is not None:
            canvas_interaction = DeliveredCanvasInteraction.model_validate(
                {
                    "sceneId": interaction.scene_id,
                    "sceneRevision": interaction.scene_revision,
                    "interactionRevision": interaction.interaction_revision,
                    "interactionMode": interaction.interaction_mode,
                    "bindingId": interaction.binding_id,
                    "targetId": interaction.target_id,
                    "accessibilityLabel": interaction.accessibility_label,
                    "stepIndex": interaction.step_index,
                }
            )
        elif plan is None:
            return

        child_text = self._child_text
        child_text_source = self._child_text_source
        transcript_waiter: Awaitable[str | None] | None = None
        if canvas_interaction is None and (
            child_text is None or child_text_source is None
        ):
            child_transcript = self._realtime.available_child_transcript(
                turn_id=turn_id
            )
            if child_transcript is not None:
                child_text = _bounded_child_transcript(child_transcript)
                child_text_source = ChildTurnTextSource.TRANSCRIPT
            else:
                wait_for_child_transcript = getattr(
                    self._realtime,
                    "wait_for_child_transcript",
                    None,
                )
                if callable(wait_for_child_transcript):
                    transcript_waiter = cast(
                        Callable[..., Awaitable[str | None]],
                        wait_for_child_transcript,
                    )(turn_id=turn_id)

        self._memory_commit_sequence += 1
        sequence = self._memory_commit_sequence
        self._latest_memory_commit_sequence = sequence
        self._warm_refresh_revision += 1
        warm_refresh_revision = self._warm_refresh_revision
        payload = _CapturedMemoryCommit(
            sequence=sequence,
            turn_id=turn_id,
            occurred_at=occurred_at,
            timezone_id=self._timezone_id,
            expected_reset_revision=expected_reset_revision,
            child_turn_summary=(plan.child_turn_summary if plan is not None else None),
            child_text=child_text,
            child_text_source=child_text_source,
            canvas_interaction=canvas_interaction,
            delivered_sample_count=delivered_sample_count,
            delivered_text=delivered_text,
            terminal=terminal,
            transcript_waiter=transcript_waiter,
            warm_refresh_revision=warm_refresh_revision,
        )
        self._memory_commit_tasks[turn_id] = asyncio.create_task(
            self._run_memory_commit(payload=payload),
            name=f"ava-memory-commit-{turn_id}",
        )

    def _schedule_failed_interaction_memory(
        self,
        *,
        delivered_sample_count: int,
        delivered_text: str,
    ) -> None:
        if self._turn_canvas_interaction_input is None:
            return
        self._schedule_memory_commit(
            turn_id=self._turn_id,
            plan=None,
            delivered_sample_count=delivered_sample_count,
            delivered_text=delivered_text,
            terminal=DeliveredTurnTerminal.FAILED,
        )

    async def _run_memory_commit(
        self,
        *,
        payload: _CapturedMemoryCommit,
    ) -> None:
        try:
            child_text = payload.child_text
            child_text_source = payload.child_text_source
            if payload.canvas_interaction is None and (
                child_text is None or child_text_source is None
            ):
                transcript_waiter = payload.transcript_waiter
                if transcript_waiter is None:
                    LOGGER.info(
                        "memory_commit_skipped session_id=%s turn_id=%s "
                        "reason=child_transcript_waiter_unavailable "
                        "warm_refresh_revision=%d",
                        self._session_id,
                        payload.turn_id,
                        payload.warm_refresh_revision,
                        extra={
                            "ava_context": {
                                "sessionId": self._session_id,
                                "turnId": payload.turn_id,
                                "reason": "childTranscriptWaiterUnavailable",
                                "warmRefreshRevision": (payload.warm_refresh_revision),
                            }
                        },
                    )
                    return
                child_transcript = await transcript_waiter
                if child_transcript is not None:
                    child_text = _bounded_child_transcript(child_transcript)
                    child_text_source = ChildTurnTextSource.TRANSCRIPT
            if payload.canvas_interaction is None and (
                child_text is None or child_text_source is None
            ):
                LOGGER.info(
                    "memory_commit_skipped session_id=%s turn_id=%s "
                    "reason=child_transcription_terminal_without_text "
                    "warm_refresh_revision=%d",
                    self._session_id,
                    payload.turn_id,
                    payload.warm_refresh_revision,
                    extra={
                        "ava_context": {
                            "sessionId": self._session_id,
                            "turnId": payload.turn_id,
                            "reason": "childTranscriptionTerminalWithoutText",
                            "warmRefreshRevision": payload.warm_refresh_revision,
                        }
                    },
                )
                return

            turn = _delivered_turn_from_memory_payload(
                payload,
                child_text=child_text,
                child_text_source=child_text_source,
            )
            commit_started = monotonic()
            decision = await self._memory.commit_delivered_turn(
                subject=self._subject,
                expected_reset_revision=payload.expected_reset_revision,
                turn=turn,
                proposal=None,
                memory_id=None,
                occurred_at=payload.occurred_at,
                timezone_id=payload.timezone_id,
            )
            if payload.expected_reset_revision != self._memory_reset_revision:
                return
            commit_duration_ms = max(0.0, (monotonic() - commit_started) * 1000)
            LOGGER.info(
                "memory_commit_completed session_id=%s turn_id=%s "
                "reset_revision=%d input_kind=%s duration_ms=%.2f",
                self._session_id,
                payload.turn_id,
                payload.expected_reset_revision,
                turn.input_kind,
                commit_duration_ms,
                extra={
                    "ava_context": {
                        "sessionId": self._session_id,
                        "turnId": payload.turn_id,
                        "resetRevision": payload.expected_reset_revision,
                        "inputKind": str(turn.input_kind),
                        "durationMs": commit_duration_ms,
                    }
                },
            )
            self._recent_live_echo_suppression_turn_ids.append(payload.turn_id)
            if payload.sequence == self._latest_memory_commit_sequence:
                self._last_memory_decision = decision
            await self._refresh_warm_memory(payload=payload, turn=turn)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failure_id = self._failure_id_factory()
            reason_category = _memory_failure_reason_category(error)
            log = (
                LOGGER.warning
                if isinstance(error, MemoryResetRevisionMismatchError)
                else LOGGER.error
            )
            log(
                "memory_commit_failed failure_id=%s session_id=%s turn_id=%s "
                "component=%s reason_category=%s",
                failure_id,
                self._session_id,
                payload.turn_id,
                SpeechFailureComponent.MEMORY_STORE,
                reason_category,
                extra={
                    "ava_context": {
                        "failureId": failure_id,
                        "component": str(SpeechFailureComponent.MEMORY_STORE),
                        "sessionId": self._session_id,
                        "turnId": payload.turn_id,
                        "reasonCategory": reason_category,
                    }
                },
            )
        finally:
            task = asyncio.current_task()
            if self._memory_commit_tasks.get(payload.turn_id) is task:
                self._memory_commit_tasks.pop(payload.turn_id, None)

    async def _refresh_warm_memory(
        self,
        *,
        payload: _CapturedMemoryCommit,
        turn: DeliveredConversationTurn,
    ) -> None:
        revision = payload.warm_refresh_revision
        LOGGER.info(
            "memory_warm_refresh_started session_id=%s turn_id=%s revision=%d",
            self._session_id,
            payload.turn_id,
            revision,
        )
        try:
            candidates = await self._memory.search_memory_candidates(
                subject=self._subject,
                query=_warm_memory_query(turn),
                limit=4,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.warning(
                "memory_warm_refresh_failed session_id=%s turn_id=%s "
                "revision=%d reason_category=%s",
                self._session_id,
                payload.turn_id,
                revision,
                _memory_failure_reason_category(error),
            )
            return
        if (
            payload.expected_reset_revision != self._memory_reset_revision
            or revision != self._warm_refresh_revision
        ):
            return
        recent_live_turn_ids = set(self._recent_live_echo_suppression_turn_ids)
        surviving_candidates = [
            candidate
            for candidate in candidates
            if not recent_live_turn_ids.intersection(candidate.source_turn_ids)
        ]
        selected_candidates = surviving_candidates[:4]
        references = tuple(
            MemoryReference.from_candidate(candidate)
            for candidate in selected_candidates
        )
        self._warm_memories = references
        raw_candidate_count = len(candidates)
        recent_suppressed_count = raw_candidate_count - len(surviving_candidates)
        selected_source_kinds = [
            str(candidate.source_kind) for candidate in selected_candidates
        ]
        selected_source_ids = [candidate.source_id for candidate in selected_candidates]
        LOGGER.info(
            "memory_warm_refresh_completed session_id=%s turn_id=%s "
            "revision=%d raw_candidate_count=%d recent_suppressed_count=%d "
            "selected_count=%d selected_source_kinds=%s selected_source_ids=%s",
            self._session_id,
            payload.turn_id,
            revision,
            raw_candidate_count,
            recent_suppressed_count,
            len(selected_candidates),
            selected_source_kinds,
            selected_source_ids,
            extra={
                "ava_context": {
                    "sessionId": self._session_id,
                    "turnId": payload.turn_id,
                    "sourceTurnId": payload.turn_id,
                    "revision": revision,
                    "rawCandidateCount": raw_candidate_count,
                    "recentSuppressedCount": recent_suppressed_count,
                    "selectedCount": len(selected_candidates),
                    "selectedSourceKinds": selected_source_kinds,
                    "selectedSourceIds": selected_source_ids,
                }
            },
        )
        reason = (
            DynamicContextBuildReason.CANVAS_INTERACTION_COMMITTED
            if turn.canvas_interaction is not None
            else DynamicContextBuildReason.TURN_COMMITTED
        )
        try:
            await self._refresh_canvas_context(reason=reason)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.warning(
                "realtime_context_installation_failed session_id=%s turn_id=%s "
                "reason=%s error_type=%s",
                self._session_id,
                payload.turn_id,
                reason,
                type(error).__name__,
            )

    async def _schedule_interrupted_canvas_presentation(self) -> None:
        if self._closed:
            return
        try:
            await self._canvas_jobs.request_interrupted_presentation(
                source_turn_id=self._turn_id,
                presentation_sample_offset=self._bounded_rendered_sample_count(),
            )
        except Exception:
            LOGGER.exception(
                "canvas_interrupted_presentation_schedule_failed "
                "session_id=%s turn_id=%s",
                self._session_id,
                self._turn_id,
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

    def _require_turn_occurred_at(self) -> datetime:
        if self._turn_occurred_at is None:
            raise CoordinatorStateError("turn occurrence time is unavailable")
        return self._turn_occurred_at

    def _require_memory_reset_revision(self) -> int:
        if self._memory_reset_revision is None:
            raise CoordinatorStateError(
                "memory reset revision is unavailable before session negotiation"
            )
        return self._memory_reset_revision

    def _turn_created_frame(self) -> AvaServerControlFrameV4:
        return self._control_frame(
            {
                "event": "turnCreated",
                "revision": 0,
                "requestId": self._request_id,
            }
        )

    def _phase_frame(self, phase: SpeechPhase) -> AvaServerControlFrameV4:
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

    def _interrupt_requested_frame(
        self,
        *,
        stream_id: str | None,
        reason: str,
    ) -> AvaServerControlFrameV4:
        if stream_id is None:
            raise CoordinatorStateError(
                "provider speech_started cannot interrupt without an active "
                "playback stream"
            )
        return self._control_frame(
            {
                "event": "interruptRequested",
                "revision": self._revision,
                "streamId": stream_id,
                "reason": reason,
            }
        )

    def _segment_frame(
        self,
        segment: SpeechPhrase,
        *,
        segment_index: int,
    ) -> AvaServerControlFrameV4:
        return self._control_frame(
            {
                "event": "segmentReady",
                "revision": self._revision,
                "segmentIndex": segment_index,
                "segment": segment.model_dump(mode="json", by_alias=True),
            }
        )

    def _audio_metadata_frame(self) -> AvaServerControlFrameV4:
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
        if not isinstance(frame.event, AudioMetadataEventV4):
            raise AssertionError("audio metadata frame resolved to the wrong type")
        self._audio_metadata_sent = True
        return frame

    def _alignment_frame(
        self,
        spans: tuple[SpeechAlignment, ...],
    ) -> AvaServerControlFrameV4:
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
        if not isinstance(frame.event, AlignmentBatchEventV4):
            raise AssertionError("alignment frame resolved to the wrong type")
        return frame

    def _audio_chunk_frame(self, pcm: bytes) -> AvaServerControlFrameV4:
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
        if not isinstance(frame.event, AudioChunkEventV4):
            raise AssertionError("audio chunk frame resolved to the wrong type")
        self._next_audio_chunk_index += 1
        return frame

    def _failed_frame(
        self,
        code: SpeechFailureCode,
        message: str,
        *,
        component: SpeechFailureComponent,
        retryable: bool,
        delivered_sample_count: int,
        delivered_text: str,
    ) -> AvaServerControlFrameV4:
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

    def _terminal_frame(self, payload: dict[str, object]) -> AvaServerControlFrameV4:
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
        cancellation_reason = payload.get("reason")
        if cancellation_reason is None and self._cancel_event is not None:
            cancellation_reason = self._cancel_event.reason
        LOGGER.info(
            "realtime_turn_terminal session_id=%s turn_id=%s terminal=%s "
            "failure_id=%s failure_component=%s failure_code=%s "
            "internal_error_type=%s provider_response_id=%s "
            "provider_call_id=%s provider_input_item_id=%s "
            "cancellation_reason=%s canvas_status=%s",
            self._session_id,
            self._turn_id,
            payload.get("event"),
            failure_id,
            failure_component,
            failure_code,
            type(self._last_error).__name__ if self._last_error is not None else None,
            provider_response_id,
            provider_call_id,
            self._interruption_provider_item_id,
            cancellation_reason,
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
                    "providerInputItemId": self._interruption_provider_item_id,
                    "cancellationReason": cancellation_reason,
                    "canvasStatus": self._canvas_status,
                }
            },
        )
        return frame

    def _control_frame(self, payload: dict[str, object]) -> AvaServerControlFrameV4:
        complete = {
            "protocolVersion": PROTOCOL_V4,
            "sessionId": self._session_id,
            "turnId": self._turn_id,
            "sequence": self._sequence,
            **payload,
        }
        event = AVA_SERVER_EVENT_ADAPTER_V4.validate_python(complete)
        try:
            self._machine.accept(event)
        except RealtimeStateError as error:
            raise CoordinatorStateError(
                f"coordinator generated an invalid server event: {error}"
            ) from error
        self._sequence += 1
        return AvaServerControlFrameV4(event)

    def _canvas_event(self, payload: dict[str, object]) -> AvaServerEventUnionV4:
        self._canvas_revision += 1
        complete = {
            "protocolVersion": PROTOCOL_V4,
            "sessionId": self._session_id,
            "sequence": self._sequence,
            "revision": self._canvas_revision,
            **payload,
        }
        event = AVA_SERVER_EVENT_ADAPTER_V4.validate_python(complete)
        self._sequence += 1
        return event

    def _validate_turn_and_stream(self, event: AvaClientEventUnionV4) -> None:
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


def _delivered_turn_from_memory_payload(
    payload: _CapturedMemoryCommit,
    *,
    child_text: str | None,
    child_text_source: ChildTurnTextSource | None,
) -> DeliveredConversationTurn:
    interaction = payload.canvas_interaction
    if interaction is not None:
        return DeliveredConversationTurn.model_validate(
            {
                "turnId": payload.turn_id,
                "inputKind": "canvasInteraction",
                "childText": None,
                "childTextSource": None,
                "canvasInteraction": interaction.model_dump(
                    mode="json",
                    by_alias=True,
                ),
                "avaDeliveredText": payload.delivered_text,
                "deliveredSampleCount": payload.delivered_sample_count,
                "childTurnSummary": _canvas_interaction_summary(interaction),
                "terminal": payload.terminal,
            }
        )
    if child_text is None or child_text_source is None:
        raise CoordinatorStateError("speech memory payload lost child text")
    if payload.child_turn_summary is None:
        raise CoordinatorStateError("speech memory payload lost its bounded summary")
    return DeliveredConversationTurn.model_validate(
        {
            "turnId": payload.turn_id,
            "childText": child_text,
            "childTextSource": child_text_source,
            "avaDeliveredText": payload.delivered_text,
            "deliveredSampleCount": payload.delivered_sample_count,
            "childTurnSummary": payload.child_turn_summary,
            "terminal": payload.terminal,
        }
    )


def _canvas_interaction_summary(
    interaction: RecordedSvgCanvasInteraction | DeliveredCanvasInteraction,
) -> str:
    mode = str(interaction.interaction_mode)
    if mode == "explore":
        prefix = "Explored “"
    elif mode == "select":
        prefix = "Selected “"
    elif mode == "steps":
        if interaction.step_index is None:
            raise ValueError("steps interaction memory requires an exact step index")
        prefix = f"Viewed step {interaction.step_index}: “"
    else:
        raise ValueError("static Canvas content cannot produce interaction memory")

    suffix = "”."
    label = " ".join(interaction.accessibility_label.split())
    available = MAX_CHILD_TURN_SUMMARY_CHARACTERS - len(prefix) - len(suffix)
    if len(label) > available:
        label = f"{label[: available - 1].rstrip()}…"
    summary = f"{prefix}{label}{suffix}"
    if len(summary) > MAX_CHILD_TURN_SUMMARY_CHARACTERS:
        raise AssertionError("Canvas interaction memory summary exceeded its boundary")
    return summary


def _stable_memory_references(
    snapshot: AuthoritativeMemorySnapshot,
) -> tuple[MemoryReference, ...]:
    return tuple(
        MemoryReference.model_validate(
            {
                "sourceKind": MemoryCandidateSourceKind.STABLE_MEMORY,
                "startedAt": None,
                "endedAt": None,
                "timezoneIds": [],
                "text": memory.summary,
                "sourceTurnIds": [memory.source_turn_id],
            }
        )
        for memory in snapshot.stable_memories[-4:]
    )


def _warm_memory_query(turn: DeliveredConversationTurn) -> str:
    fields = [turn.child_turn_summary]
    if turn.child_text is not None:
        fields.append(turn.child_text)
    elif turn.canvas_interaction is not None:
        fields.append(turn.canvas_interaction.accessibility_label)
    normalized = " ".join(" ".join(fields).split())
    if not normalized:
        raise CoordinatorStateError("warm memory seed lost semantic turn material")
    return normalized[:500]


def _local_date_search_bounds(
    *,
    from_date: date | None,
    to_date: date | None,
    timezone_id: str,
) -> tuple[datetime | None, datetime | None]:
    if from_date is not None and to_date is not None and from_date > to_date:
        raise ValueError("fromDate cannot be after toDate")
    zone = ZoneInfo(validate_iana_timezone_id(timezone_id))
    lower = (
        datetime.combine(from_date, time.min, tzinfo=zone).astimezone(UTC)
        if from_date is not None
        else None
    )
    upper = (
        datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=zone).astimezone(
            UTC
        )
        if to_date is not None
        else None
    )
    return lower, upper


def _search_tool_reference(
    candidate: MemorySearchCandidate,
) -> SearchMemoryReferenceV4:
    return SearchMemoryReferenceV4.model_validate(
        {
            "sourceKind": candidate.source_kind,
            "startedAt": candidate.started_at,
            "endedAt": candidate.ended_at,
            "timezoneIds": candidate.timezone_ids,
            "text": candidate.reference_text,
        }
    )


def _clock_daypart(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _memory_failure_reason_category(error: Exception) -> str:
    if isinstance(error, MemoryResetRevisionMismatchError):
        return "staleResetRevision"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, (ValueError, CoordinatorStateError)):
        return "validation"
    return "storeFailure"


async def _next_provider_event(
    iterator: AsyncIterator[RealtimeProviderTurnEvent],
) -> RealtimeProviderTurnEvent:
    return await iterator.__anext__()


async def _next_outbound_frame(
    iterator: AsyncIterator[AvaServerOutboundFrameV4],
) -> AvaServerOutboundFrameV4:
    return await iterator.__anext__()


async def _next_speech_item(
    iterator: AsyncIterator[SynthesizedSpeechItem],
) -> SynthesizedSpeechItem:
    return await iterator.__anext__()


def _bounded_child_transcript(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("provider child transcript cannot be empty")
    if len(normalized) > 2_000:
        raise ValueError("provider child transcript cannot exceed 2000 characters")
    return normalized


def _bounded_child_summary(value: str) -> str:
    if len(value) <= MAX_CHILD_TURN_SUMMARY_CHARACTERS:
        return value
    boundary = value.rfind(" ", 0, 238)
    if boundary <= 0:
        boundary = 237
    return f"{value[:boundary].rstrip()}…"


def _format_failure_message(
    base_message: str,
    error: Exception | None = None,
) -> str:
    if error is None:
        return base_message[:240]
    error_str = str(error).strip()
    error_type = type(error).__name__
    if not error_str or error_str == error_type:
        detail = error_type
    elif error_str.startswith(error_type):
        detail = error_str
    else:
        detail = f"{error_type}: {error_str}"
    detail = " ".join(detail.split())
    combined = f"{base_message} ({detail})"
    if len(combined) <= 240:
        return combined
    available = 240 - len(base_message) - 6
    if available > 20:
        return f"{base_message} ({detail[:available]}...)"
    return base_message[:240]
