from __future__ import annotations

import asyncio
import inspect
import json
import logging
from builtins import anext
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import Literal, TypeAlias, TypeVar, cast
from zoneinfo import ZoneInfo

from ..canvas.svg_workspace import (
    ActiveSvgCanvasScene,
    CanvasSceneSelector,
    CanvasWorkspaceManifest,
    CanvasWorkspaceManifestScene,
    RecordedSvgCanvasInteraction,
    SvgCanvasWorkspace,
    SvgCanvasWorkspaceError,
    normalize_semantic_title,
)

RetainedSceneLoader: TypeAlias = Callable[
    [CanvasWorkspaceManifestScene],
    Awaitable[ActiveSvgCanvasScene | None] | ActiveSvgCanvasScene | None,
]
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
    CanvasPresentationAcknowledgedEventV4,
    CanvasPresentationRequestedEventV4,
    CanvasPresentationChangedSource,
    CanvasPresentedEventV4,
    CanvasRetainedEventV4,
    CanvasRenderProfileUpdatedEventV4,
    CanvasRuntimeFailedEventV4,
    ClientReadyEventV4,
    TurnContextSnapshotEventV4,
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
    DeliveredTurnTerminal,
    DialogueFrame,
    DialogueFrameState,
    DialogueFrameCanvasReference,
    DialogueFrameUpdate,
    DialogueFrameUpdateDecision,
    MemoryResetRevisionMismatchError,
    MemoryProposalDecision,
    MemorySearchCandidate,
    MemoryCandidateSourceKind,
    SemanticCaptureDisposition,
    SemanticCaptureJournalDraft,
    SemanticJournalCanvasReference,
    SemanticJournalEventFamily,
    SemanticMemoryCommitDecision,
    SemanticMemoryCommitStatus,
    SemanticMemoryProposal,
    canonical_semantic_memory_proposal_fingerprint,
    resolve_semantic_journal_proposals,
)
from .provider_session import (
    NegotiatedRealtimeCall,
    RealtimeContextInstallationStatus,
    RealtimeProviderInputEvent,
    RealtimeProviderProtocolError,
    RealtimeProviderResponseIncompleteError,
    RealtimeProviderTurnEvent,
    RealtimeCommittedInput,
    RealtimeProviderSession,
    RealtimeProviderUnavailableError,
    SemanticCaptureEvidence,
    SettledSemanticToolOutcome,
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
    CANCEL_INTERACTIVE_CANVAS_TOOL_NAME,
    CANVAS_MODEL_SAFE_REJECTION_REASON,
    CanvasActionCompletion,
    GET_CURRENT_TIME_TOOL_NAME,
    InteractiveCanvasCancellationToolOutputV4,
    InteractiveCanvasOperation,
    InteractiveCanvasToolOutputV4,
    InteractiveCanvasToolStatusV4,
    GetCurrentTimeToolOutputV4,
    INTERACTIVE_CANVAS_TOOL_NAME,
    MANAGE_CANVAS_PRESENTATION_TOOL_NAME,
    ManageCanvasPresentationAction,
    ManageCanvasPresentationRequest,
    ManageCanvasPresentationStatus,
    ManageCanvasPresentationToolOutputV4,
    RealtimeGetCurrentTimeToolCall,
    RealtimeManageCanvasPresentationToolCall,
    RealtimeRejectedManageCanvasPresentationToolCall,
    RealtimeRejectedUtilityToolCall,
    RealtimeSearchMemoryToolCall,
    RealtimeUtilityToolKind,
    RejectedUtilityToolOutputV4,
    SEARCH_MEMORY_TOOL_NAME,
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


type _ProviderContextEquivalenceKey = tuple[str, str, str, str, str]


class TurnContextSynchronizationError(RuntimeError):
    """The exact device context could not be established for child reasoning."""


class CoordinatorStateError(RuntimeError):
    """Raised when a client command violates coordinator ownership or ordering."""


class CanvasFocusRevisionConflictError(CoordinatorStateError):
    """An external focus revision was reused for a different retained scene."""

    def __init__(
        self,
        *,
        incoming_scene_id: str,
        incoming_focus_revision: int,
        canonical_scene_id: str,
    ) -> None:
        super().__init__("focus revision cannot change scene identity")
        self.incoming_scene_id = incoming_scene_id
        self.incoming_focus_revision = incoming_focus_revision
        self.canonical_scene_id = canonical_scene_id


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
    action_only_succeeded: bool = False
    action_only_rejected: bool = False


@dataclass(frozen=True, slots=True)
class _TurnResult:
    segments: tuple[SpeechPhrase, ...]
    child_turn_summary: str
    action_only: bool = False


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
    settled_tool_outcomes: tuple[SettledSemanticToolOutcome, ...] = ()
    interrupted: bool = False


@dataclass(slots=True)
class _PendingPresentationCommand:
    command_id: str
    action: ManageCanvasPresentationAction
    target_scene_id: str | None
    workspace_epoch: int
    workspace_revision: int
    future: asyncio.Future[ManageCanvasPresentationToolOutputV4]
    changed: bool = True
    superseded: bool = False
    source: CanvasPresentationChangedSource = CanvasPresentationChangedSource.MODEL


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
        turn_context_timeout_seconds: float = 90.0,
        context_assembler: DynamicContextAssembler | None = None,
        current_time_factory: OccurrenceTimeFactory = _utc_now,
        retained_scene_loader: RetainedSceneLoader | None = None,
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
        self._turn_context_timeout_seconds = turn_context_timeout_seconds
        self._context_assembler = context_assembler or DynamicContextAssembler()
        self._current_time_factory = current_time_factory
        self._retained_scene_loader = retained_scene_loader
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
        self._dialogue_frame = DialogueFrame()
        self._turn_settled_tool_outcomes: list[SettledSemanticToolOutcome] = []
        self._harness: RealtimeHarness | None = None
        self._pending_harness: RealtimeHarness | None = None
        self._pending_focus_revision: int | None = None
        self._pending_context_reason: DynamicContextBuildReason | None = None
        self._context_installation_task: asyncio.Task[None] | None = None
        self._warm_memories: tuple[MemoryReference, ...] = ()
        self._context_build_sequence = 0
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
        self._memory_commit_queue: asyncio.Queue[_CapturedMemoryCommit | object] = (
            asyncio.Queue()
        )
        self._memory_commit_terminal = object()
        self._memory_commit_worker_task: asyncio.Task[None] | None = None
        self._scheduled_memory_commit_turn_ids: set[str] = set()
        self._last_enqueued_memory_sequence = 0
        self._current_memory_commit_turn_id: str | None = None
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
        self._submitted_focus_revision = 0
        self._submitted_focus_build_id: str | None = None
        self._focus_build_id_by_revision: dict[int, str] = {}
        self._acknowledged_focus_revision = 0
        self._focus_changed = asyncio.Event()
        self._focus_linearization_lock = asyncio.Lock()
        self._turn_context_request: RealtimeCommittedInput | None = None
        self._turn_context_snapshot: (
            asyncio.Future[TurnContextSnapshotEventV4] | None
        ) = None
        self._turn_context_sync_task: asyncio.Task[None] | None = None
        self._turn_context_sync_owner: RealtimeCommittedInput | None = None
        self._turn_controls: asyncio.Queue[AvaServerControlFrameV4] = asyncio.Queue(
            maxsize=16
        )
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
        self._canonical_presentation_revision = 0
        self._pending_presentation_commands: dict[str, _PendingPresentationCommand] = {}

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
    def dialogue_frame(self) -> DialogueFrame:
        return DialogueFrame.model_validate(
            self._dialogue_frame.model_dump(mode="json", by_alias=True)
        )

    def _record_tool_outcome(
        self,
        *,
        tool_name: str,
        call_id: str,
        outcome: str,
        detail: str | None = None,
        canonical_scene_id: str | None = None,
        canonical_scene_revision: int | None = None,
    ) -> None:
        self._turn_settled_tool_outcomes.append(
            SettledSemanticToolOutcome(
                tool_name=tool_name,
                call_id=call_id,
                outcome=outcome,
                detail=detail,
                canonical_scene_id=canonical_scene_id,
                canonical_scene_revision=canonical_scene_revision,
            )
        )

    def _is_canonical_canvas_reference(
        self,
        reference: DialogueFrameCanvasReference | SemanticJournalCanvasReference,
    ) -> bool:
        workspace = self._canvas_jobs.state.workspace
        scene = workspace.scene(reference.scene_id)
        manifest_scene = workspace.manifest_scene(reference.scene_id)
        canonical_revision = (
            scene.revision
            if scene is not None
            else (manifest_scene.scene_revision if manifest_scene is not None else None)
        )
        return (
            canonical_revision is not None
            and canonical_revision == reference.scene_revision
        )

    def _validate_semantic_memory_canvas_references(
        self, proposal: SemanticMemoryProposal
    ) -> None:
        references: list[
            DialogueFrameCanvasReference | SemanticJournalCanvasReference
        ] = []
        if proposal.dialogue_frame_update is not None:
            references.extend(
                proposal.dialogue_frame_update.frame.relevant_canvas_references
            )
        for event in proposal.journal_events:
            references.extend(event.canonical_canvas_references)
        for reference in references:
            if not self._is_canonical_canvas_reference(reference):
                raise CoordinatorStateError(
                    "semantic memory Canvas reference is not a canonical retained "
                    "scene revision"
                )

    @property
    def last_error(self) -> Exception | None:
        """Underlying server diagnostic; never serialize this to the client."""

        return self._last_error

    @property
    def negotiated_call(self) -> NegotiatedRealtimeCall:
        if self._negotiated_call is None:
            raise CoordinatorStateError("Realtime media has not been negotiated")
        return self._negotiated_call

    @property
    def accepted_focus_revision(self) -> int:
        return self._canvas_jobs.state.focus_revision

    @property
    def submitted_focus_revision(self) -> int:
        return self._submitted_focus_revision

    @property
    def acknowledged_focus_revision(self) -> int:
        return self._acknowledged_focus_revision

    @property
    def _focus_context_revision(self) -> int:
        return self._submitted_focus_revision

    def restore_focused_canvas(self, scene: ActiveSvgCanvasScene) -> None:
        if self._closed or self._started or self._negotiated_call is not None:
            raise CoordinatorStateError(
                "Canvas restore must complete before realtime negotiation"
            )
        self._canvas_jobs.state.restore_focused(scene)

    def synchronize_canvas_workspace(self, manifest: CanvasWorkspaceManifest) -> bool:
        if self._closed or self._started or self._negotiated_call is not None:
            raise CoordinatorStateError(
                "Canvas workspace synchronization must complete before realtime negotiation"
            )
        return self._canvas_jobs.state.workspace.adopt_manifest(manifest)

    def validate_retained_canvas_scene(self, scene: ActiveSvgCanvasScene) -> None:
        """Verify retained Flutter content against the adopted canonical manifest.

        This intentionally does not hydrate the session workspace or mutate
        focus.  Startup retains every document as an on-demand backing source;
        only the focused document is hydrated into the active runtime cache.
        """
        if self._closed or self._started or self._negotiated_call is not None:
            raise CoordinatorStateError(
                "Canvas retained scene registration must complete before realtime negotiation"
            )
        self._canvas_jobs.state.workspace.validate_retained_scene(scene)

    async def ensure_retained_scene_hydrated(
        self,
        workspace: SvgCanvasWorkspace,
        scene_id: str,
        *,
        source: str = "realtime",
    ) -> bool:
        """Hydrate an unhydrated retained scene on-demand from the backing loader.

        Checks runtime cache first. If missed, invokes retained scene loader,
        checks snapshot fence, validates against manifest, commits to runtime
        cache without mutating focus, and re-checks snapshot fence.
        """
        if workspace.scene(scene_id) is not None:
            return True

        manifest_scene = workspace.manifest_scene(scene_id)
        if manifest_scene is None:
            LOGGER.warning(
                "retained_scene_hydration_failed session_id=%s scene_id=%s source=%s reason=%s",
                self._session_id,
                scene_id,
                source,
                "manifest_missing",
            )
            return False

        if self._retained_scene_loader is None:
            LOGGER.warning(
                "retained_scene_hydration_failed session_id=%s scene_id=%s source=%s reason=%s",
                self._session_id,
                scene_id,
                source,
                "no_loader",
            )
            return False

        expected_epoch = workspace.workspace_epoch
        expected_revision = workspace.workspace_revision

        try:
            loaded = self._retained_scene_loader(manifest_scene)
            if inspect.isawaitable(loaded):
                scene = await loaded
            else:
                scene = loaded
        except Exception as exc:
            LOGGER.warning(
                "retained_scene_hydration_failed session_id=%s scene_id=%s source=%s reason=%s error=%s",
                self._session_id,
                scene_id,
                source,
                "loader_error",
                exc,
            )
            return False

        if scene is None:
            LOGGER.warning(
                "retained_scene_hydration_failed session_id=%s scene_id=%s source=%s reason=%s",
                self._session_id,
                scene_id,
                source,
                "scene_none",
            )
            return False

        if (
            workspace.workspace_epoch != expected_epoch
            or workspace.workspace_revision != expected_revision
        ):
            LOGGER.warning(
                "retained_scene_hydration_snapshot_conflict session_id=%s scene_id=%s source=%s "
                "expected_epoch=%s actual_epoch=%s expected_revision=%s actual_revision=%s",
                self._session_id,
                scene_id,
                source,
                expected_epoch,
                workspace.workspace_epoch,
                expected_revision,
                workspace.workspace_revision,
            )
            raise SvgCanvasWorkspaceError("workspaceChanged")

        workspace.validate_retained_scene(scene)
        workspace.commit_retained_scene(scene)

        if (
            workspace.workspace_epoch != expected_epoch
            or workspace.workspace_revision != expected_revision
        ):
            LOGGER.warning(
                "retained_scene_hydration_snapshot_conflict session_id=%s scene_id=%s source=%s "
                "expected_epoch=%s actual_epoch=%s expected_revision=%s actual_revision=%s",
                self._session_id,
                scene_id,
                source,
                expected_epoch,
                workspace.workspace_epoch,
                expected_revision,
                workspace.workspace_revision,
            )
            raise SvgCanvasWorkspaceError("workspaceChanged")

        LOGGER.info(
            "retained_scene_hydrated_on_demand session_id=%s scene_id=%s source=%s",
            self._session_id,
            scene_id,
            source,
        )
        return True

    async def focus_canvas(
        self, scene: ActiveSvgCanvasScene, *, focus_revision: int
    ) -> None:
        if self._closed:
            raise CoordinatorStateError("cannot focus a closed session")
        superseded_commands: list[_PendingPresentationCommand] = []
        async with self._focus_linearization_lock:
            state = self._canvas_jobs.state
            current_revision = state.focus_revision
            current_scene_id = (
                state.focused_scene.scene_id
                if state.focused_scene is not None
                else None
            )
            if focus_revision < current_revision:
                self._log_canvas_focus_result(
                    scene=scene,
                    focus_revision=focus_revision,
                    canonical_scene_id_before=current_scene_id,
                    canonical_focus_revision_before=current_revision,
                    classification="stale",
                )
                return
            state.workspace.validate_retained_focus(scene)
            if focus_revision == current_revision:
                if state.focused_scene is None:
                    raise CoordinatorStateError(
                        "positive focus revision lost canonical scene identity"
                    )
                if state.focused_scene.scene_id != scene.scene_id:
                    self._log_canvas_focus_result(
                        scene=scene,
                        focus_revision=focus_revision,
                        canonical_scene_id_before=current_scene_id,
                        canonical_focus_revision_before=current_revision,
                        classification="conflict",
                    )
                    raise CanvasFocusRevisionConflictError(
                        incoming_scene_id=scene.scene_id,
                        incoming_focus_revision=focus_revision,
                        canonical_scene_id=state.focused_scene.scene_id,
                    )
                self._log_canvas_focus_result(
                    scene=scene,
                    focus_revision=focus_revision,
                    canonical_scene_id_before=current_scene_id,
                    canonical_focus_revision_before=current_revision,
                    classification="idempotent",
                )
                return
            state.focus_retained(scene, focus_revision=focus_revision)
            for command_id, pending in tuple(
                self._pending_presentation_commands.items()
            ):
                incompatible_show = (
                    pending.action == ManageCanvasPresentationAction.SHOW
                    and pending.target_scene_id != scene.scene_id
                )
                incompatible_hide = (
                    pending.action == ManageCanvasPresentationAction.HIDE
                    and state.presentation_visible
                )
                if not incompatible_show and not incompatible_hide:
                    continue
                pending.superseded = True
                self._pending_presentation_commands.pop(command_id, None)
                superseded_commands.append(pending)
            self._focus_changed.set()
        if self._harness is not None:
            await self._refresh_canvas_context(
                reason=DynamicContextBuildReason.CANVAS_FOCUSED
            )
        self._complete_superseded_presentation_commands(
            superseded_commands,
            reason="superseded by newer client focus",
        )
        self._log_canvas_focus_result(
            scene=scene,
            focus_revision=focus_revision,
            canonical_scene_id_before=current_scene_id,
            canonical_focus_revision_before=current_revision,
            classification="accepted",
        )

    def _log_canvas_focus_result(
        self,
        *,
        scene: ActiveSvgCanvasScene,
        focus_revision: int,
        canonical_scene_id_before: str | None,
        canonical_focus_revision_before: int,
        classification: Literal["accepted", "idempotent", "stale", "conflict"],
    ) -> None:
        state = self._canvas_jobs.state
        LOGGER.info(
            "canvas_focus_submission_result session_id=%s incoming_scene_id=%s "
            "incoming_focus_revision=%s classification=%s",
            self._session_id,
            scene.scene_id,
            focus_revision,
            classification,
            extra={
                "ava_context": {
                    "sessionId": self._session_id,
                    "incomingSceneId": scene.scene_id,
                    "incomingSceneRevision": scene.revision,
                    "incomingFocusRevision": focus_revision,
                    "canonicalSceneIdBefore": canonical_scene_id_before,
                    "canonicalFocusRevisionBefore": canonical_focus_revision_before,
                    "classification": classification,
                    "canonicalSceneIdAfter": (
                        state.focused_scene.scene_id
                        if state.focused_scene is not None
                        else None
                    ),
                    "canonicalFocusRevisionAfter": state.focus_revision,
                }
            },
        )

    def _turn_context_requested_frame(
        self,
        *,
        turn_id: str,
        request_id: str,
        provider_item_id: str,
    ) -> AvaServerControlFrameV4:
        return self._control_frame(
            {
                "event": "turnContextRequested",
                "turnId": turn_id,
                "requestId": request_id,
                "providerItemId": provider_item_id,
                "revision": self._revision,
            }
        )

    async def _synchronize_turn_context(
        self, committed: RealtimeCommittedInput
    ) -> None:
        self._latest_canvas_interaction_intent = None
        self._canvas_interaction_response_wakeup.set()
        self._turn_context_sync_owner = committed
        focus_grounding_degraded = False

        try:
            while True:
                if (
                    self._closed
                    or self._cancellation_requested.is_set()
                    or self._turn_id != committed.turn_id
                    or self._turn_context_sync_owner != committed
                ):
                    return

                if self._pending_harness is not None:
                    await self._install_pending_context_if_idle(
                        turn_id=committed.turn_id
                    )

                accepted_focus = self._canvas_jobs.state.focus_revision
                if (
                    not focus_grounding_degraded
                    and accepted_focus > self._acknowledged_focus_revision
                ):
                    target_build_id = self._focus_build_id_by_revision.get(
                        accepted_focus
                    )
                    if target_build_id is None:
                        if self._pending_harness is not None:
                            await self._install_pending_context_if_idle(
                                turn_id=committed.turn_id
                            )
                        target_build_id = self._focus_build_id_by_revision.get(
                            accepted_focus
                        )

                    if target_build_id is None:
                        self._focus_changed.clear()
                        if (
                            self._canvas_jobs.state.focus_revision
                            <= self._acknowledged_focus_revision
                        ):
                            continue
                        try:
                            async with asyncio.timeout(
                                self._turn_context_timeout_seconds
                            ):
                                await self._focus_changed.wait()
                            continue
                        except TimeoutError:
                            LOGGER.warning(
                                "realtime_canvas_focus_grounding_degraded "
                                "turn_id=%s item_id=%s accepted_focus=%s acknowledged_focus=%s "
                                "reason=focus_changed_timeout",
                                committed.turn_id,
                                committed.item_id,
                                self._canvas_jobs.state.focus_revision,
                                self._acknowledged_focus_revision,
                            )
                            focus_grounding_degraded = True
                    else:
                        try:
                            LOGGER.info(
                                "realtime_response_blocked_waiting_for_context "
                                "session_id=%s turn_id=%s item_id=%s target_build_id=%s focus_revision=%s",
                                self._session_id,
                                committed.turn_id,
                                committed.item_id,
                                target_build_id,
                                accepted_focus,
                            )
                            async with asyncio.timeout(
                                self._turn_context_timeout_seconds
                            ):
                                await self._realtime.wait_for_context(
                                    build_id=target_build_id
                                )
                            self._acknowledged_focus_revision = max(
                                self._acknowledged_focus_revision,
                                accepted_focus,
                            )
                            LOGGER.info(
                                "realtime_context_acknowledged session_id=%s turn_id=%s "
                                "build_id=%s focus_revision=%s",
                                self._session_id,
                                committed.turn_id,
                                target_build_id,
                                accepted_focus,
                            )
                        except TimeoutError:
                            LOGGER.warning(
                                "realtime_canvas_focus_grounding_degraded "
                                "turn_id=%s item_id=%s build_id=%s accepted_focus=%s acknowledged_focus=%s "
                                "reason=wait_for_context_timeout",
                                committed.turn_id,
                                committed.item_id,
                                target_build_id,
                                accepted_focus,
                                self._acknowledged_focus_revision,
                            )
                            focus_grounding_degraded = True

                desired_build_id = self._realtime.desired_context_build_id
                if (
                    desired_build_id is not None
                    and not self._realtime.is_context_acknowledged(desired_build_id)
                ):
                    LOGGER.info(
                        "realtime_response_blocked_waiting_for_context "
                        "session_id=%s turn_id=%s item_id=%s desired_build_id=%s",
                        self._session_id,
                        committed.turn_id,
                        committed.item_id,
                        desired_build_id,
                    )
                    try:
                        async with asyncio.timeout(self._turn_context_timeout_seconds):
                            await self._realtime.wait_for_context(
                                build_id=desired_build_id
                            )
                        LOGGER.info(
                            "realtime_context_acknowledged session_id=%s turn_id=%s build_id=%s",
                            self._session_id,
                            committed.turn_id,
                            desired_build_id,
                        )
                    except TimeoutError:
                        LOGGER.warning(
                            "realtime_context_ack_timeout session_id=%s turn_id=%s "
                            "item_id=%s desired_build_id=%s reason=wait_for_context_timeout",
                            self._session_id,
                            committed.turn_id,
                            committed.item_id,
                            desired_build_id,
                        )

                wait_for_resource_admission = False
                async with self._focus_linearization_lock:
                    if (
                        self._closed
                        or self._cancellation_requested.is_set()
                        or self._turn_id != committed.turn_id
                        or self._turn_context_sync_owner != committed
                    ):
                        return

                    latest_accepted_focus = self._canvas_jobs.state.focus_revision
                    if not focus_grounding_degraded:
                        if latest_accepted_focus > self._acknowledged_focus_revision:
                            continue
                    else:
                        if latest_accepted_focus > accepted_focus:
                            focus_grounding_degraded = False
                            continue

                    if self._pending_harness is not None:
                        continue

                    committed_response = await self._realtime.request_child_response(
                        turn_id=committed.turn_id,
                        item_id=committed.item_id,
                        permit_degraded_focus_grounding=focus_grounding_degraded,
                    )
                    if committed_response is False:
                        wait_for_resource_admission = True
                    else:
                        LOGGER.info(
                            "realtime_child_response_committed session_id=%s turn_id=%s "
                            "item_id=%s active_context_build_id=%s",
                            self._session_id,
                            committed.turn_id,
                            committed.item_id,
                            self._realtime.active_context_build_id,
                        )
                        return
                if wait_for_resource_admission:
                    still_current = await self._realtime.wait_until_response_admissible(
                        turn_id=committed.turn_id
                    )
                    if not still_current:
                        return
        finally:
            if self._turn_context_sync_owner is committed:
                self._turn_context_sync_owner = None

    @staticmethod
    def _matches_focus(
        scene: ActiveSvgCanvasScene | None, snapshot: TurnContextSnapshotEventV4
    ) -> bool:
        identity = snapshot.focused_canvas
        if identity is None:
            return scene is None
        return scene is not None and (
            scene.scene_id == identity.scene_id
            and scene.revision == identity.scene_revision
            and scene.document.svg_sha256 == identity.svg_sha256
            and scene.document.manifest_sha256 == identity.manifest_sha256
        )

    async def memory_reset(self, *, reset_revision: int) -> None:
        if (
            isinstance(reset_revision, bool)
            or not isinstance(reset_revision, int)
            or reset_revision < 1
        ):
            raise ValueError("reset_revision must be a positive integer")
        async with self._canvas_context_lock:
            current = self._memory_reset_revision
            if current is not None and reset_revision <= current:
                return
            self._memory_reset_revision = reset_revision
            self._warm_memories = ()
            self._dialogue_frame = DialogueFrame()
            self._turn_settled_tool_outcomes = []
            await self._canvas_jobs.reset_conversation()
            self._last_memory_decision = None
            if self._harness is not None and not self._closed:
                await self._refresh_canvas_context_locked(
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
            self._dialogue_frame = snapshot.dialogue_frame
            LOGGER.info(
                "realtime_stage session_id=%s request_id=%s stage=memory.load "
                "elapsed_ms=%.2f",
                self._session_id,
                self._request_id,
                (monotonic() - memory_started) * 1000,
            )
            harness_started = monotonic()
            self._warm_memories = ()
            bootstrap_current_time_utc = self._current_time_factory()
            dynamic_context = self._context_assembler.assemble(
                build_id=self._next_context_build_id(),
                reason=DynamicContextBuildReason.SESSION_BOOTSTRAP,
                age_band=self._age_band,
                timezone_id=self._timezone_id,
                current_time_utc=bootstrap_current_time_utc,
                bootstrap_turns=tuple(snapshot.delivered_turns),
                warm_memories=self._warm_memories,
                dialogue_frame=self._dialogue_frame,
                canvas_context=self._current_canvas_context(),
                active_visual_present=(
                    self._canvas_jobs.state.presentation_visible
                    and self._canvas_jobs.state.focused_scene is not None
                ),
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
            bootstrap_focus_revision = self._canvas_jobs.state.focus_revision
            self._log_context_installation(
                harness=harness,
                reason=DynamicContextBuildReason.SESSION_BOOTSTRAP,
                status="providerAttempted",
                provider_call_id=negotiated.provider_call_id,
                pending_build_id=harness.context.build_id,
                focus_revision=bootstrap_focus_revision,
            )
            try:
                installation_status = await self._realtime.prepare(harness=harness)
            except Exception as error:
                self._log_context_installation(
                    harness=harness,
                    reason=DynamicContextBuildReason.SESSION_BOOTSTRAP,
                    status="failed",
                    provider_call_id=negotiated.provider_call_id,
                    error_type=type(error).__name__,
                    pending_build_id=harness.context.build_id,
                    focus_revision=bootstrap_focus_revision,
                )
                raise
            self._log_context_installation(
                harness=harness,
                reason=DynamicContextBuildReason.SESSION_BOOTSTRAP,
                status=str(installation_status),
                provider_call_id=negotiated.provider_call_id,
                effective_build_id=harness.context.build_id,
                focus_revision=bootstrap_focus_revision,
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

        if isinstance(event, TurnContextSnapshotEventV4):
            request = self._turn_context_request
            future = self._turn_context_snapshot
            if (
                request is None
                or event.turn_id != request.turn_id
                or event.provider_item_id != request.item_id
                or event.request_id != self._request_id
            ):
                LOGGER.info(
                    "stale_turn_context_snapshot_ignored session_id=%s turn_id=%s item_id=%s",
                    self._session_id,
                    event.turn_id,
                    event.provider_item_id,
                )
                return
            if future is None or future.done():
                return
            future.set_result(event)
            return

        if isinstance(event, ClientReadyEventV4):
            if self._client_ready:
                raise CoordinatorStateError("clientReady may occur only once")
            if self._started:
                raise CoordinatorStateError(
                    "clientReady must arrive before turn streaming starts"
                )
            self._client_ready = True
            if (
                event.scene_id is not None
                and self._canvas_jobs.state.focused_scene is not None
                and self._canvas_jobs.state.focused_scene.scene_id == event.scene_id
                and event.focus_revision is not None
                and self._canvas_jobs.state.focus_revision < event.focus_revision
            ):
                self._canvas_jobs.state.focus_revision = event.focus_revision
                self._canvas_jobs.state.workspace.focus_revision = event.focus_revision

            if event.canvas_panel_visible is not None:
                if (
                    event.canvas_panel_visible
                    and event.scene_id is not None
                    and self._canvas_jobs.state.focused_scene is not None
                    and self._canvas_jobs.state.focused_scene.scene_id == event.scene_id
                ):
                    self._canvas_jobs.state.set_presentation_visible(True)
                    self._canonical_presentation_revision += 1
                    await self._canvas_events.presentation_changed(
                        visible=True,
                        scene_id=event.scene_id,
                        focus_revision=self._canvas_jobs.state.focus_revision,
                        source=CanvasPresentationChangedSource.RESTORE.value,
                    )
                    await self._refresh_canvas_context(
                        reason=DynamicContextBuildReason.CANVAS_PRESENTATION_CHANGED
                    )
                else:
                    self._canvas_jobs.state.set_presentation_visible(False)
            return

        if isinstance(event, CanvasPresentationRequestedEventV4):
            await self._handle_client_presentation_requested(event)
            return

        if isinstance(event, CanvasPresentationAcknowledgedEventV4):
            await self._handle_client_presentation_acknowledged(event)
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
        if isinstance(event, (CanvasPresentedEventV4, CanvasRetainedEventV4)):
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
        turn_control_task: asyncio.Task[AvaServerControlFrameV4] = asyncio.create_task(
            self._turn_controls.get(),
            name="ava-v4-turn-control",
        )
        try:
            while conversation_task is not None:
                pending: set[asyncio.Task[object]] = {
                    cast(asyncio.Task[object], conversation_task),
                    cast(asyncio.Task[object], canvas_task),
                    cast(asyncio.Task[object], turn_control_task),
                }
                done, _ = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if turn_control_task in done:
                    frame = turn_control_task.result()
                    yield frame
                    turn_control_task = asyncio.create_task(
                        self._turn_controls.get(),
                        name="ava-v4-turn-control",
                    )

                # Conversation frames have priority because the task may have
                # already allocated the next sequence. Keep PCM descriptors
                # and their binary payload atomic on the wire.
                if conversation_task in done:
                    try:
                        conversation_frame = conversation_task.result()
                    except StopAsyncIteration:
                        conversation_task = None
                    else:
                        yield conversation_frame
                        if isinstance(
                            conversation_frame, AvaServerControlFrameV4
                        ) and isinstance(
                            conversation_frame.event,
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
                    payload = canvas_task.result()
                    yield AvaServerControlFrameV4(event=self._canvas_event(payload))
                    canvas_task = asyncio.create_task(
                        self._canvas_events.next_payload(),
                        name="ava-v4-canvas-control",
                    )
        finally:
            tasks = tuple(
                task
                for task in (conversation_task, canvas_task, turn_control_task)
                if task is not None
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
        except TurnContextSynchronizationError as error:
            await self._cancel_planning_work(planning_task=planning_task)
            self._last_error = error
            provider_error = (
                SpeechFailureCode.TURN_CONTEXT_FAILED,
                SpeechFailureComponent.COORDINATOR,
                str(error),
                True,
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

        if (
            plan is not None
            and plan.action_only
            and not plan.segments
            and not synthesis_started
        ):
            if not planning_started:
                yield self._phase_frame(SpeechPhase.PLANNING)
            if planning_state.canvas_generation_id is not None:
                try:
                    await self._canvas_jobs.request_presentation(
                        generation_id=planning_state.canvas_generation_id,
                        presentation_sample_offset=0,
                    )
                except Exception as error:
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
                    raise CoordinatorStateError(
                        "accepted action-only Canvas presentation could not be "
                        "scheduled"
                    ) from error
            async for frame in self._complete_frames(plan=plan, ledger=ledger):
                yield frame
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
        self._turn_settled_tool_outcomes = []
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

    async def _refresh_canvas_context_locked(
        self,
        *,
        reason: DynamicContextBuildReason,
    ) -> None:
        if self._memory_snapshot is None or self._harness is None:
            raise CoordinatorStateError(
                "canvas context cannot refresh before session negotiation"
            )
        async with self._focus_linearization_lock:
            focus_revision = self._canvas_jobs.state.focus_revision
            harness = self._build_live_harness(reason=reason)
            current_harness = self._harness
            if _provider_context_equivalence_key(
                harness
            ) == _provider_context_equivalence_key(
                current_harness
            ) and self._provider_owns_context_build(current_harness.context.build_id):
                retired = self._retire_pending_context()
                reused_build_id = current_harness.context.build_id
                self._associate_focus_revision_with_build(
                    focus_revision=focus_revision,
                    build_id=reused_build_id,
                )
                self._log_context_installation(
                    harness=harness,
                    reason=reason,
                    status="currentEquivalentSuppressed",
                    effective_build_id=reused_build_id,
                    pending_build_id=(
                        retired.context.build_id if retired is not None else None
                    ),
                    focus_revision=focus_revision,
                )
                return
            pending_harness = self._pending_harness
            if pending_harness is not None and (
                _provider_context_equivalence_key(harness)
                == _provider_context_equivalence_key(pending_harness)
            ):
                pending_build_id = pending_harness.context.build_id
                self._pending_focus_revision = focus_revision
                self._pending_context_reason = reason
                self._log_context_installation(
                    harness=harness,
                    reason=reason,
                    status="pendingEquivalentCoalesced",
                    pending_build_id=pending_build_id,
                    focus_revision=focus_revision,
                )
                self._ensure_pending_context_installation()
                return
            self._retire_pending_context()
            self._pending_harness = harness
            self._pending_focus_revision = focus_revision
            self._pending_context_reason = reason
        self._log_context_installation(
            harness=harness,
            reason=reason,
            status="providerAttempted",
            pending_build_id=harness.context.build_id,
            focus_revision=focus_revision,
        )
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
                pending_build_id=harness.context.build_id,
                focus_revision=focus_revision,
            )
            raise
        if installation_status is not None:
            self._harness = harness
            self._associate_focus_revision_with_build(
                focus_revision=focus_revision,
                build_id=harness.context.build_id,
            )
            if self._pending_harness is harness:
                self._pending_harness = None
                self._pending_focus_revision = None
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
            effective_build_id=(
                harness.context.build_id if installation_status is not None else None
            ),
            pending_build_id=(
                harness.context.build_id if installation_status is None else None
            ),
            focus_revision=focus_revision,
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
            await self._refresh_canvas_context_locked(reason=reason)

    def _build_live_harness(
        self,
        *,
        reason: DynamicContextBuildReason,
    ) -> RealtimeHarness:
        active_visual_present = (
            self._canvas_jobs.state.presentation_visible
            and self._canvas_jobs.state.focused_scene is not None
        )
        dynamic_context = self._context_assembler.assemble(
            build_id=self._next_context_build_id(),
            reason=reason,
            age_band=self._age_band,
            timezone_id=self._timezone_id,
            current_time_utc=self._current_time_factory(),
            bootstrap_turns=(),
            warm_memories=self._warm_memories,
            dialogue_frame=self._dialogue_frame,
            canvas_context=self._current_canvas_context(),
            active_visual_present=active_visual_present,
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
            "context_build_id=%s content_fingerprint_prefix=%s "
            "final_utf8_bytes=%d estimated_tokens=%d "
            "bootstrap_turn_count=%d warm_reference_count=%d "
            "canvas_included=%s included_references=%d omitted_references=%d "
            "dialogue_frame_revision=%d dialogue_frame_included=%s",
            self._session_id,
            reason,
            context.build_id,
            context.content_fingerprint[:12],
            context.final_utf8_bytes,
            context.estimated_dynamic_tokens,
            context.included_bootstrap_turns,
            context.included_memory_references,
            context.canvas_included,
            context.included_memory_references,
            context.omitted_memory_references,
            context.dialogue_frame_revision,
            context.dialogue_frame_included,
            extra={
                "ava_context": {
                    "sessionId": self._session_id,
                    "reason": str(reason),
                    "contextBuildId": context.build_id,
                    "contentFingerprintPrefix": context.content_fingerprint[:12],
                    "finalUtf8Bytes": context.final_utf8_bytes,
                    "estimatedTokens": context.estimated_dynamic_tokens,
                    "bootstrapTurnCount": context.included_bootstrap_turns,
                    "warmReferenceCount": context.included_memory_references,
                    "canvasIncluded": context.canvas_included,
                    "includedReferenceCount": context.included_memory_references,
                    "omittedReferenceCount": context.omitted_memory_references,
                    "dialogueFrameRevision": context.dialogue_frame_revision,
                    "dialogueFrameIncluded": context.dialogue_frame_included,
                }
            },
        )

    def _next_context_build_id(self) -> str:
        self._context_build_sequence += 1
        return f"ctx-{self._context_build_sequence}"

    def _provider_owns_context_build(self, build_id: str) -> bool:
        return (
            self._realtime.active_context_build_id == build_id
            or self._realtime.desired_context_build_id == build_id
            or self._realtime.is_context_acknowledged(build_id)
        )

    def _associate_focus_revision_with_build(
        self,
        *,
        focus_revision: int,
        build_id: str,
    ) -> None:
        if focus_revision >= self._submitted_focus_revision:
            self._submitted_focus_revision = focus_revision
            self._submitted_focus_build_id = build_id
        self._focus_build_id_by_revision[focus_revision] = build_id
        self._focus_changed.set()

    def _retire_pending_context(self) -> RealtimeHarness | None:
        pending = self._pending_harness
        if pending is None:
            return None
        retired_build_id = pending.context.build_id
        self._pending_harness = None
        self._pending_focus_revision = None
        self._pending_context_reason = None
        for revision, build_id in tuple(self._focus_build_id_by_revision.items()):
            if build_id == retired_build_id:
                self._focus_build_id_by_revision.pop(revision, None)
        return pending

    def _log_context_installation(
        self,
        *,
        harness: RealtimeHarness,
        reason: DynamicContextBuildReason,
        status: str,
        provider_call_id: str | None = None,
        error_type: str | None = None,
        effective_build_id: str | None = None,
        pending_build_id: str | None = None,
        focus_revision: int | None = None,
    ) -> None:
        resolved_provider_call_id = provider_call_id
        if resolved_provider_call_id is None and self._negotiated_call is not None:
            resolved_provider_call_id = self._negotiated_call.provider_call_id
        interaction = self._canvas_jobs.state.last_interaction
        LOGGER.info(
            "realtime_context_installation session_id=%s reason=%s status=%s "
            "candidate_build_id=%s effective_build_id=%s pending_build_id=%s "
            "content_fingerprint_prefix=%s final_utf8_bytes=%d "
            "estimated_tokens=%d focus_revision=%s provider_call_id=%s scene_id=%s "
            "scene_revision=%s interaction_revision=%s binding_id=%s "
            "error_type=%s",
            self._session_id,
            reason,
            status,
            harness.context.build_id,
            effective_build_id,
            pending_build_id,
            harness.context.content_fingerprint[:12],
            harness.context.final_utf8_bytes,
            harness.context.estimated_dynamic_tokens,
            focus_revision,
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
                    "candidateBuildId": harness.context.build_id,
                    "effectiveBuildId": effective_build_id,
                    "pendingBuildId": pending_build_id,
                    "contentFingerprintPrefix": (
                        harness.context.content_fingerprint[:12]
                    ),
                    "finalUtf8Bytes": harness.context.final_utf8_bytes,
                    "estimatedTokens": harness.context.estimated_dynamic_tokens,
                    "focusRevision": focus_revision,
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
            focus_revision = self._pending_focus_revision
            reason = self._pending_context_reason
            if harness is None or focus_revision is None:
                return True, None, None, None
            resolved_reason = reason or DynamicContextBuildReason.CANVAS_ACTIVATED
            self._log_context_installation(
                harness=harness,
                reason=resolved_reason,
                status="providerAttempted",
                pending_build_id=harness.context.build_id,
                focus_revision=focus_revision,
            )
            try:
                installation_status = await self._realtime.prepare_if_idle(
                    harness=harness,
                    turn_id=turn_id,
                )
            except Exception as error:
                self._log_context_installation(
                    harness=harness,
                    reason=resolved_reason,
                    status="failed",
                    error_type=type(error).__name__,
                    pending_build_id=harness.context.build_id,
                    focus_revision=focus_revision,
                )
                raise
            if installation_status is not None:
                self._harness = harness
                self._associate_focus_revision_with_build(
                    focus_revision=focus_revision,
                    build_id=harness.context.build_id,
                )
                if self._pending_harness is harness:
                    self._pending_harness = None
                    self._pending_focus_revision = None
                    self._pending_context_reason = None
            self._log_context_installation(
                harness=harness,
                reason=resolved_reason,
                status=(
                    str(installation_status)
                    if installation_status is not None
                    else "deferred"
                ),
                effective_build_id=(
                    harness.context.build_id
                    if installation_status is not None
                    else None
                ),
                pending_build_id=(
                    harness.context.build_id if installation_status is None else None
                ),
                focus_revision=focus_revision,
            )
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
            await self._install_pending_context_if_idle(turn_id=turn_id)
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
                if isinstance(event, RealtimeCommittedInput):
                    if (
                        self._turn_context_sync_task is not None
                        and not self._turn_context_sync_task.done()
                    ):
                        self._turn_context_sync_task.cancel()
                        await asyncio.gather(
                            self._turn_context_sync_task, return_exceptions=True
                        )
                    self._turn_context_sync_owner = event
                    self._turn_context_sync_task = asyncio.create_task(
                        self._synchronize_turn_context(event),
                        name=f"ava-turn-context-sync-{event.turn_id}-{event.item_id}",
                    )
                    continue
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
                    self._record_tool_outcome(
                        tool_name=event.tool_kind.value,
                        call_id=event.call_id,
                        outcome="rejected",
                        detail=event.reason,
                    )
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
                    self._record_tool_outcome(
                        tool_name=INTERACTIVE_CANVAS_TOOL_NAME,
                        call_id=event.call_id,
                        outcome="rejected",
                        detail=event.reason,
                    )
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
                if isinstance(event, RealtimeRejectedManageCanvasPresentationToolCall):
                    for segment in parser.finish_response_chunk():
                        await updates.put(segment)
                    self._claim_canvas(event.call_id, state=state)
                    self._record_tool_outcome(
                        tool_name=MANAGE_CANVAS_PRESENTATION_TOOL_NAME,
                        call_id=event.call_id,
                        outcome="rejected",
                        detail=event.reason,
                    )
                    LOGGER.warning(
                        "manage_canvas_presentation_arguments_rejected session_id=%s "
                        "turn_id=%s call_id=%s reason=%s",
                        self._session_id,
                        self._turn_id,
                        event.call_id,
                        event.reason,
                    )
                    output = ManageCanvasPresentationToolOutputV4(
                        status=ManageCanvasPresentationStatus.REJECTED,
                        operation=ManageCanvasPresentationAction.HIDE,
                        changed=False,
                        reason=event.reason,
                    )
                    await self._realtime.complete_manage_canvas_presentation_tool(
                        turn_id=self._turn_id,
                        call_id=event.call_id,
                        output=output,
                    )
                    LOGGER.info(
                        "canvas_control_completion session_id=%s turn_id=%s "
                        "function_call_id=%s operation=invalid status=%s",
                        self._session_id,
                        self._turn_id,
                        event.call_id,
                        output.status,
                        extra={
                            "ava_context": {
                                "sessionId": self._session_id,
                                "turnId": self._turn_id,
                                "providerResponseId": event.provider_response_id,
                                "functionCallId": event.call_id,
                                "operation": None,
                                "direction": None,
                                "requestedSceneId": None,
                                "status": str(output.status),
                                "changed": False,
                                "reason": event.reason,
                                "resolvedSceneId": None,
                                "sceneRevision": None,
                                "position": None,
                                "total": None,
                                "workspaceEpoch": None,
                                "workspaceRevision": None,
                                "focusRevision": (
                                    self._canvas_jobs.state.focus_revision
                                ),
                                "presentationCommandId": None,
                                "supersessionResult": "rejected",
                                "resultingContextBuildId": (
                                    self._realtime.active_context_build_id
                                ),
                                "continuationResponseId": None,
                            }
                        },
                    )
                    continue
                if isinstance(event, RealtimeManageCanvasPresentationToolCall):
                    for segment in parser.finish_response_chunk():
                        await updates.put(segment)
                    self._claim_canvas(event.call_id, state=state)
                    await self._run_manage_canvas_presentation_tool(event, state=state)
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
            if not segments and not (
                state.action_only_succeeded or state.action_only_rejected
            ):
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
                action_only=state.action_only_succeeded or state.action_only_rejected,
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
        self._record_tool_outcome(
            tool_name=SEARCH_MEMORY_TOOL_NAME,
            call_id=call.call_id,
            outcome=str(output.status),
            detail=output.reason,
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
        self._record_tool_outcome(
            tool_name=GET_CURRENT_TIME_TOOL_NAME,
            call_id=call.call_id,
            outcome="available",
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
        resolved_target_scene_id: str | None = None
        if call.request.operation == InteractiveCanvasOperation.REVISE:
            resolution = self._canvas_jobs.state.workspace.resolve_scene_selector(
                CanvasSceneSelector(
                    call.request.selector or CanvasSceneSelector.CURRENT
                ),
                position=call.request.position,
                title=call.request.title,
            )
            if resolution.scene is None or resolution.reason is not None:
                output = InteractiveCanvasToolOutputV4.model_validate(
                    {
                        "status": InteractiveCanvasToolStatusV4.REJECTED,
                        "reason": CANVAS_MODEL_SAFE_REJECTION_REASON,
                    }
                )
                await self._complete_canvas_tool(
                    turn_id=self._turn_id,
                    call_id=call.call_id,
                    output=output,
                )
                if call.request.completion == CanvasActionCompletion.REQUEST_COMPLETE:
                    state.action_only_rejected = True
                self._canvas_status = "rejected"
                self._record_tool_outcome(
                    tool_name=INTERACTIVE_CANVAS_TOOL_NAME,
                    call_id=call.call_id,
                    outcome="rejected",
                    detail=output.reason,
                )
                return

            try:
                hydrated = await self.ensure_retained_scene_hydrated(
                    self._canvas_jobs.state.workspace,
                    resolution.scene.scene_id,
                    source="realtime",
                )
            except Exception as exc:
                LOGGER.warning(
                    "revise_hydration_failed session_id=%s turn_id=%s error=%s",
                    self._session_id,
                    self._turn_id,
                    exc,
                )
                hydrated = False

            if not hydrated:
                output = InteractiveCanvasToolOutputV4.model_validate(
                    {
                        "status": InteractiveCanvasToolStatusV4.REJECTED,
                        "reason": CANVAS_MODEL_SAFE_REJECTION_REASON,
                    }
                )
                await self._complete_canvas_tool(
                    turn_id=self._turn_id,
                    call_id=call.call_id,
                    output=output,
                )
                if call.request.completion == CanvasActionCompletion.REQUEST_COMPLETE:
                    state.action_only_rejected = True
                self._canvas_status = "rejected"
                self._record_tool_outcome(
                    tool_name=INTERACTIVE_CANVAS_TOOL_NAME,
                    call_id=call.call_id,
                    outcome="rejected",
                    detail=output.reason,
                )
                return

            resolved_target_scene_id = resolution.scene.scene_id
        output = await self._canvas_jobs.accept_tool_call(
            source_turn_id=self._turn_id,
            source_transcript=bounded_source_transcript,
            call=call,
            complete_tool=self._complete_canvas_tool,
            resolved_target_scene_id=resolved_target_scene_id,
        )
        if output.status == InteractiveCanvasToolStatusV4.ACCEPTED:
            generation_id = output.generation_id
            if generation_id is None:  # guarded by the validated model
                raise CoordinatorStateError(
                    "accepted protocol-v4 canvas output lost generationId"
                )
            state.canvas_generation_id = generation_id
            if call.request.completion == CanvasActionCompletion.REQUEST_COMPLETE:
                state.action_only_succeeded = True
            self._canvas_status = "accepted"
            self._record_tool_outcome(
                tool_name=INTERACTIVE_CANVAS_TOOL_NAME,
                call_id=call.call_id,
                outcome="accepted",
                detail=output.reason,
            )
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
        if call.request.completion == CanvasActionCompletion.REQUEST_COMPLETE:
            state.action_only_rejected = True
        self._canvas_status = "rejected"
        self._record_tool_outcome(
            tool_name=INTERACTIVE_CANVAS_TOOL_NAME,
            call_id=call.call_id,
            outcome="rejected",
            detail=output.reason,
        )

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
        if call.request.completion == CanvasActionCompletion.REQUEST_COMPLETE:
            state.action_only_succeeded = True
        self._canvas_status = "cancelled"
        self._record_tool_outcome(
            tool_name=CANCEL_INTERACTIVE_CANVAS_TOOL_NAME,
            call_id=call.call_id,
            outcome="accepted",
        )
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

    async def _run_manage_canvas_presentation_tool(
        self,
        call: RealtimeManageCanvasPresentationToolCall,
        *,
        state: _PlanningState,
    ) -> None:
        output, command_id, superseded = await self._execute_model_canvas_presentation(
            call.request
        )
        required_context_build_id = self._realtime.desired_context_build_id
        while required_context_build_id is not None:
            await self._realtime.wait_for_context(build_id=required_context_build_id)
            latest_desired = self._realtime.desired_context_build_id
            if latest_desired in {None, required_context_build_id}:
                break
            required_context_build_id = latest_desired
        await self._complete_manage_canvas_presentation(
            call=call,
            state=state,
            output=output,
            presentation_command_id=command_id,
            supersession_result=(
                "superseded"
                if superseded
                else (
                    "applied"
                    if output.status == ManageCanvasPresentationStatus.ACCEPTED
                    else "rejected"
                )
            ),
        )

    async def _execute_model_canvas_presentation(
        self,
        request: ManageCanvasPresentationRequest,
    ) -> tuple[ManageCanvasPresentationToolOutputV4, str | None, bool]:
        """Translate one model semantic action into the existing selector owner."""

        return await self._execute_canvas_presentation(
            action=request.presentation_operation,
            selector=request.presentation_selector,
            position=request.position,
            title=request.title,
            source="realtime",
        )

    async def _execute_canvas_presentation(
        self,
        *,
        action: ManageCanvasPresentationAction,
        selector: CanvasSceneSelector | None,
        position: int | None,
        title: str | None,
        source: str = "realtime",
    ) -> tuple[ManageCanvasPresentationToolOutputV4, str | None, bool]:
        """Resolve semantic intent once, then await the exact Flutter command."""
        workspace = self._canvas_jobs.state.workspace
        scene_id: str | None = None

        if action == ManageCanvasPresentationAction.SHOW:
            requested_selector = CanvasSceneSelector(
                selector or CanvasSceneSelector.CURRENT
            )
            resolution = workspace.resolve_scene_selector(
                requested_selector,
                position=position,
                title=title,
            )
            if resolution.reason is not None or resolution.scene is None:
                LOGGER.warning(
                    "manage_canvas_presentation_selector_rejected session_id=%s turn_id=%s source=%s reason=%s",
                    self._session_id,
                    self._turn_id,
                    source,
                    resolution.reason,
                )
                return (
                    self._canvas_presentation_output(
                        status=ManageCanvasPresentationStatus.REJECTED,
                        operation=action,
                        changed=False,
                        scene_id=scene_id,
                        reason=resolution.reason or "selectorRejected",
                    ),
                    None,
                    False,
                )
            assert resolution.scene is not None
            LOGGER.info(
                "manage_canvas_presentation_selector_resolved "
                "session_id=%s turn_id=%s source=%s selector=%s position=%s",
                self._session_id,
                self._turn_id,
                source,
                requested_selector.value,
                resolution.position,
                extra={
                    "ava_context": {
                        "selector": requested_selector.value,
                        "requestedNormalizedTitle": (
                            normalize_semantic_title(title or "")
                            if requested_selector is CanvasSceneSelector.TITLE
                            else None
                        ),
                        "resolvedCanonicalTitle": resolution.scene.title,
                        "resolvedPosition": resolution.position,
                        "workspaceRevision": resolution.workspace_revision,
                        "contextBuildId": self._realtime.active_context_build_id,
                    }
                },
            )
            scene_id = resolution.scene.scene_id
            try:
                hydrated = await self.ensure_retained_scene_hydrated(
                    workspace,
                    scene_id,
                    source=source,
                )
            except SvgCanvasWorkspaceError as exc:
                if str(exc) == "workspaceChanged":
                    LOGGER.warning(
                        "manage_canvas_presentation_workspace_changed session_id=%s turn_id=%s source=%s scene_id=%s",
                        self._session_id,
                        self._turn_id,
                        source,
                        scene_id,
                    )
                    return (
                        self._canvas_presentation_output(
                            status=ManageCanvasPresentationStatus.REJECTED,
                            operation=action,
                            changed=False,
                            reason="workspaceChanged",
                        ),
                        None,
                        False,
                    )
                LOGGER.warning(
                    "manage_canvas_presentation_hydration_failed session_id=%s turn_id=%s source=%s scene_id=%s error=%s",
                    self._session_id,
                    self._turn_id,
                    source,
                    scene_id,
                    exc,
                )
                return (
                    self._canvas_presentation_output(
                        status=ManageCanvasPresentationStatus.REJECTED,
                        operation=action,
                        changed=False,
                        reason="retainedVisualUnavailable",
                    ),
                    None,
                    False,
                )
            if not hydrated:
                return (
                    self._canvas_presentation_output(
                        status=ManageCanvasPresentationStatus.REJECTED,
                        operation=action,
                        changed=False,
                        reason="retainedVisualUnavailable",
                    ),
                    None,
                    False,
                )

        import uuid

        command_id = f"cmd-{uuid.uuid4().hex[:12]}"
        future: asyncio.Future[ManageCanvasPresentationToolOutputV4] = (
            asyncio.get_running_loop().create_future()
        )
        pending_cmd = _PendingPresentationCommand(
            command_id=command_id,
            action=action,
            target_scene_id=scene_id,
            workspace_epoch=workspace.workspace_epoch,
            workspace_revision=workspace.workspace_revision,
            future=future,
            changed=(
                action != ManageCanvasPresentationAction.SHOW
                or self._canvas_jobs.state.focused_scene is None
                or self._canvas_jobs.state.focused_scene.scene_id != scene_id
            ),
            source=(
                CanvasPresentationChangedSource.USER
                if source == "flutter"
                else CanvasPresentationChangedSource.MODEL
            ),
        )
        self._pending_presentation_commands[command_id] = pending_cmd

        action_val: Literal["show", "hide"] = (
            "show" if action == ManageCanvasPresentationAction.SHOW else "hide"
        )
        await self._canvas_events.presentation_command(
            command_id=command_id,
            action=action_val,
            scene_id=scene_id,
        )

        try:
            async with asyncio.timeout(2.0):
                output = await future
        except TimeoutError:
            pending_cmd.superseded = True
            self._pending_presentation_commands.pop(command_id, None)
            output = ManageCanvasPresentationToolOutputV4(
                status=ManageCanvasPresentationStatus.REJECTED,
                operation=action,
                changed=False,
                reason="presentation command timed out",
            )

        return output, command_id, pending_cmd.superseded

    async def _complete_manage_canvas_presentation(
        self,
        *,
        call: RealtimeManageCanvasPresentationToolCall,
        state: _PlanningState,
        output: ManageCanvasPresentationToolOutputV4,
        presentation_command_id: str | None = None,
        supersession_result: str | None = None,
    ) -> None:
        await self._realtime.complete_manage_canvas_presentation_tool(
            turn_id=self._turn_id,
            call_id=call.call_id,
            output=output,
        )
        self._record_tool_outcome(
            tool_name=MANAGE_CANVAS_PRESENTATION_TOOL_NAME,
            call_id=call.call_id,
            outcome=str(output.status),
            detail=output.reason,
        )
        if call.request.completion == CanvasActionCompletion.REQUEST_COMPLETE:
            if output.status == ManageCanvasPresentationStatus.ACCEPTED:
                state.action_only_succeeded = True
            else:
                state.action_only_rejected = True
        LOGGER.info(
            "canvas_control_completion session_id=%s turn_id=%s "
            "function_call_id=%s action=%s status=%s",
            self._session_id,
            self._turn_id,
            call.call_id,
            call.request.action,
            output.status,
            extra={
                "ava_context": {
                    "sessionId": self._session_id,
                    "turnId": self._turn_id,
                    "providerResponseId": call.provider_response_id,
                    "functionCallId": call.call_id,
                    "action": str(call.request.action),
                    "requestedCompletion": str(call.request.completion),
                    "status": str(output.status),
                    "changed": output.changed,
                    "reason": output.reason,
                    "resolvedSceneId": None,
                    "sceneRevision": None,
                    "position": output.position,
                    "total": output.total,
                    "workspaceEpoch": None,
                    "workspaceRevision": None,
                    "focusRevision": self._canvas_jobs.state.focus_revision,
                    "presentationCommandId": presentation_command_id,
                    "supersessionResult": supersession_result,
                    "resultingContextBuildId": (self._realtime.active_context_build_id),
                    "continuationResponseId": None,
                }
            },
        )

    def _canvas_presentation_output(
        self,
        *,
        status: ManageCanvasPresentationStatus,
        operation: ManageCanvasPresentationAction,
        changed: bool,
        reason: str | None = None,
        scene_id: str | None = None,
    ) -> ManageCanvasPresentationToolOutputV4:
        state = self._canvas_jobs.state
        workspace = state.workspace
        effective_scene_id = (
            None
            if operation == ManageCanvasPresentationAction.HIDE
            else (
                scene_id
                or (
                    state.focused_scene.scene_id
                    if state.focused_scene is not None
                    else None
                )
            )
        )
        ordered_ids = workspace.ordered_scene_ids()
        position = (
            ordered_ids.index(effective_scene_id) + 1
            if effective_scene_id is not None and effective_scene_id in ordered_ids
            else None
        )
        metadata = (
            workspace.manifest_scene(effective_scene_id)
            if effective_scene_id is not None
            else None
        )
        hydrated = (
            workspace.scene(effective_scene_id)
            if effective_scene_id is not None
            else None
        )
        total = len(ordered_ids)
        return ManageCanvasPresentationToolOutputV4(
            status=status,
            operation=operation,
            changed=changed,
            visible=state.presentation_visible,
            position=position,
            total=total,
            has_previous=position is not None and position > 1,
            has_next=position is not None and position < total,
            title=(
                metadata.title
                if metadata is not None
                else (hydrated.document.title if hydrated is not None else None)
            ),
            reason=reason,
        )

    async def _handle_client_presentation_requested(
        self,
        event: CanvasPresentationRequestedEventV4,
    ) -> None:
        """Route Flutter semantic intent through the same presentation execution path."""

        asyncio.create_task(
            self._execute_canvas_presentation(
                action=ManageCanvasPresentationAction(event.operation),
                selector=event.selector,
                position=event.position,
                title=None,
                source="flutter",
            ),
            name=f"ava-client-presentation-{event.sequence}",
        )

    def _take_pending_presentation_commands(
        self,
    ) -> list[_PendingPresentationCommand]:
        commands = list(self._pending_presentation_commands.values())
        for command in commands:
            command.superseded = True
        self._pending_presentation_commands.clear()
        return commands

    def _complete_superseded_presentation_commands(
        self,
        commands: list[_PendingPresentationCommand],
        *,
        reason: str = "superseded by client physical intent",
    ) -> None:
        current_scene = self._canvas_jobs.state.focused_scene
        for command in commands:
            if command.future.done():
                continue
            command.future.set_result(
                self._canvas_presentation_output(
                    status=ManageCanvasPresentationStatus.REJECTED,
                    operation=command.action,
                    changed=False,
                    scene_id=(
                        current_scene.scene_id if current_scene is not None else None
                    ),
                    reason=reason,
                )
            )

    async def _handle_client_presentation_acknowledged(
        self,
        event: CanvasPresentationAcknowledgedEventV4,
    ) -> None:
        pending_cmd = self._pending_presentation_commands.pop(event.command_id, None)
        if pending_cmd is None or pending_cmd.superseded:
            LOGGER.info(
                "stale_or_superseded_presentation_ack_ignored session_id=%s command_id=%s",
                self._session_id,
                event.command_id,
            )
            return

        workspace = self._canvas_jobs.state.workspace
        if pending_cmd.action == ManageCanvasPresentationAction.SHOW and (
            workspace.workspace_epoch != pending_cmd.workspace_epoch
            or workspace.workspace_revision != pending_cmd.workspace_revision
        ):
            if not pending_cmd.future.done():
                pending_cmd.future.set_result(
                    self._canvas_presentation_output(
                        status=ManageCanvasPresentationStatus.REJECTED,
                        operation=pending_cmd.action,
                        changed=False,
                        reason="workspaceChanged",
                    )
                )
            return

        if not event.applied:
            if not pending_cmd.future.done():
                pending_cmd.future.set_result(
                    self._canvas_presentation_output(
                        status=ManageCanvasPresentationStatus.REJECTED,
                        operation=pending_cmd.action,
                        changed=False,
                        scene_id=pending_cmd.target_scene_id,
                        reason=event.reason or "client rejected presentation command",
                    )
                )
            return

        if pending_cmd.action == ManageCanvasPresentationAction.SHOW:
            if (
                pending_cmd.target_scene_id is None
                or event.scene_id != pending_cmd.target_scene_id
                or event.focus_revision is None
            ):
                if not pending_cmd.future.done():
                    pending_cmd.future.set_result(
                        ManageCanvasPresentationToolOutputV4(
                            status=ManageCanvasPresentationStatus.REJECTED,
                            operation=pending_cmd.action,
                            changed=False,
                            reason="client acknowledgement changed presentation identity",
                        )
                    )
                return
            state = self._canvas_jobs.state
            current_focus = state.focused_scene
            if event.focus_revision < state.focus_revision or (
                event.focus_revision == state.focus_revision
                and current_focus is not None
                and current_focus.scene_id != pending_cmd.target_scene_id
            ):
                if not pending_cmd.future.done():
                    pending_cmd.future.set_result(
                        ManageCanvasPresentationToolOutputV4(
                            status=ManageCanvasPresentationStatus.REJECTED,
                            operation=pending_cmd.action,
                            changed=False,
                            reason="superseded by newer client focus",
                        )
                    )
                return
            target_scene = state.workspace.scene(pending_cmd.target_scene_id)
            if target_scene is None:
                if not pending_cmd.future.done():
                    pending_cmd.future.set_result(
                        ManageCanvasPresentationToolOutputV4(
                            status=ManageCanvasPresentationStatus.REJECTED,
                            operation=pending_cmd.action,
                            changed=False,
                            reason="retained visual is no longer available",
                        )
                    )
                return
            state.focus_retained(target_scene, focus_revision=event.focus_revision)
            self._canvas_jobs.state.set_presentation_visible(True)
            self._canonical_presentation_revision += 1
            await self._canvas_events.presentation_changed(
                visible=True,
                scene_id=pending_cmd.target_scene_id,
                focus_revision=self._canvas_jobs.state.focus_revision,
                source=pending_cmd.source.value,
            )
        else:
            self._canvas_jobs.state.set_presentation_visible(False)
            self._canonical_presentation_revision += 1
            await self._canvas_events.presentation_changed(
                visible=False,
                scene_id=None,
                focus_revision=None,
                source=pending_cmd.source.value,
            )

        await self._refresh_canvas_context(
            reason=DynamicContextBuildReason.CANVAS_PRESENTATION_CHANGED
        )
        if not pending_cmd.future.done():
            pending_cmd.future.set_result(
                self._canvas_presentation_output(
                    status=ManageCanvasPresentationStatus.ACCEPTED,
                    operation=pending_cmd.action,
                    changed=pending_cmd.changed,
                    scene_id=pending_cmd.target_scene_id,
                )
            )

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
        sync_task = self._turn_context_sync_task
        self._turn_context_sync_task = None
        self._turn_context_sync_owner = None
        if sync_task is not None and not sync_task.done():
            sync_task.cancel()
            await asyncio.gather(sync_task, return_exceptions=True)
        for cmd in list(self._pending_presentation_commands.values()):
            cmd.superseded = True
            if not cmd.future.done():
                cmd.future.cancel()
        self._pending_presentation_commands.clear()
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
        next_event: asyncio.Task[RealtimeProviderTurnEvent] | None = None
        try:
            while True:
                if next_event is None:
                    next_event = asyncio.create_task(_next_provider_event(iterator))
                cancellation = asyncio.create_task(self._cancellation_requested.wait())
                tasks: set[asyncio.Task[object]] = {next_event, cancellation}
                sync_task = self._turn_context_sync_task
                if sync_task is not None and not sync_task.done():
                    tasks.add(cast(asyncio.Task[object], sync_task))
                try:
                    done, _ = await asyncio.wait(
                        tasks,
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

                cancellation.cancel()
                await asyncio.gather(cancellation, return_exceptions=True)

                if cancellation in done:
                    next_event.cancel()
                    await asyncio.gather(next_event, return_exceptions=True)
                    await self._realtime.cancel(turn_id=self._turn_id)
                    return

                if sync_task is not None and sync_task in done:
                    exc = sync_task.exception()
                    if exc is not None and not isinstance(exc, asyncio.CancelledError):
                        next_event.cancel()
                        await asyncio.gather(next_event, return_exceptions=True)
                        raise exc

                if next_event in done:
                    task_to_read = next_event
                    next_event = None
                    try:
                        event = task_to_read.result()
                    except StopAsyncIteration:
                        return
                    yield event
        finally:
            if next_event is not None and not next_event.done():
                next_event.cancel()
                await asyncio.gather(next_event, return_exceptions=True)

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
        if (
            self._memory_commit_worker_task is not None
            and not self._memory_commit_worker_task.done()
        ):
            self._memory_commit_queue.put_nowait(self._memory_commit_terminal)
            try:
                await asyncio.wait_for(
                    self._memory_commit_worker_task,
                    timeout=self._memory_commit_drain_timeout_seconds,
                )
            except TimeoutError:
                hung_turn_id = self._current_memory_commit_turn_id or self._session_id
                failure_id = self._failure_id_factory()
                LOGGER.warning(
                    "memory_commit_drain_timed_out failure_id=%s "
                    "session_id=%s turn_id=%s component=%s "
                    "reason_category=%s timeout_seconds=%.2f",
                    failure_id,
                    self._session_id,
                    hung_turn_id,
                    SpeechFailureComponent.MEMORY_STORE,
                    "shutdownDrainTimeout",
                    self._memory_commit_drain_timeout_seconds,
                    extra={
                        "ava_context": {
                            "failureId": failure_id,
                            "component": str(SpeechFailureComponent.MEMORY_STORE),
                            "sessionId": self._session_id,
                            "turnId": hung_turn_id,
                            "reasonCategory": "shutdownDrainTimeout",
                            "timeoutSeconds": (
                                self._memory_commit_drain_timeout_seconds
                            ),
                        }
                    },
                )
                self._memory_commit_worker_task.cancel()
                await asyncio.gather(
                    self._memory_commit_worker_task,
                    return_exceptions=True,
                )
            while not self._memory_commit_queue.empty():
                try:
                    self._memory_commit_queue.get_nowait()
                    self._memory_commit_queue.task_done()
                except (asyncio.QueueEmpty, ValueError):
                    break
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
        if self._closed:
            return
        if turn_id in self._scheduled_memory_commit_turn_ids:
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
        if sequence <= self._last_enqueued_memory_sequence:
            raise CoordinatorStateError(
                f"memory commit sequence must increase monotonically: {sequence} <= {self._last_enqueued_memory_sequence}"
            )
        self._last_enqueued_memory_sequence = sequence
        self._latest_memory_commit_sequence = sequence
        settled_tool_outcomes = tuple(self._turn_settled_tool_outcomes)
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
            settled_tool_outcomes=settled_tool_outcomes,
            interrupted=(terminal == DeliveredTurnTerminal.CANCELLED),
        )
        self._scheduled_memory_commit_turn_ids.add(turn_id)
        self._memory_commit_queue.put_nowait(payload)
        self._ensure_memory_commit_worker()

    def _ensure_memory_commit_worker(self) -> None:
        if (
            self._memory_commit_worker_task is None
            or self._memory_commit_worker_task.done()
        ):
            self._memory_commit_worker_task = asyncio.create_task(
                self._run_memory_commit_worker(),
                name=f"ava-memory-commit-worker-{self._session_id}",
            )

    async def _run_memory_commit_worker(self) -> None:
        while True:
            item = await self._memory_commit_queue.get()
            if item is self._memory_commit_terminal:
                self._memory_commit_queue.task_done()
                break
            if not isinstance(item, _CapturedMemoryCommit):
                self._memory_commit_queue.task_done()
                continue
            payload = item
            self._current_memory_commit_turn_id = payload.turn_id
            try:
                await self._run_memory_commit(payload=payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                self._scheduled_memory_commit_turn_ids.discard(payload.turn_id)
                self._current_memory_commit_turn_id = None
                self._memory_commit_queue.task_done()

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
                        "reason=child_transcript_waiter_unavailable",
                        self._session_id,
                        payload.turn_id,
                        extra={
                            "ava_context": {
                                "sessionId": self._session_id,
                                "turnId": payload.turn_id,
                                "reason": "childTranscriptWaiterUnavailable",
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
                    "reason=child_transcription_terminal_without_text",
                    self._session_id,
                    payload.turn_id,
                    extra={
                        "ava_context": {
                            "sessionId": self._session_id,
                            "turnId": payload.turn_id,
                            "reason": "childTranscriptionTerminalWithoutText",
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
            if payload.sequence == self._latest_memory_commit_sequence:
                self._last_memory_decision = decision
            await self._run_semantic_capture(
                payload=payload,
                turn=turn,
                child_text=child_text,
            )
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

    async def _run_semantic_capture(
        self,
        *,
        payload: _CapturedMemoryCommit,
        turn: DeliveredConversationTurn,
        child_text: str | None,
    ) -> None:
        if (
            self._closed
            or payload.expected_reset_revision != self._memory_reset_revision
        ):
            return

        evidence = SemanticCaptureEvidence(
            source_turn_id=payload.turn_id,
            current_frame=self._dialogue_frame.to_state(),
            child_utterance=child_text,
            spoken_response_prefix=payload.delivered_text or None,
            interrupted=payload.interrupted,
            settled_tool_outcomes=payload.settled_tool_outcomes,
        )

        try:
            capture_args = await self._realtime.capture_semantic_memory(
                evidence=evidence,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.warning(
                "semantic_capture_inference_failed session_id=%s turn_id=%s "
                "reset_revision=%d error_type=%s reason=%s",
                self._session_id,
                payload.turn_id,
                payload.expected_reset_revision,
                type(error).__name__,
                str(error),
                extra={
                    "ava_context": {
                        "sessionId": self._session_id,
                        "turnId": payload.turn_id,
                        "resetRevision": payload.expected_reset_revision,
                        "status": "failed",
                        "errorType": type(error).__name__,
                        "reason": str(error),
                    }
                },
            )
            return

        if (
            self._closed
            or payload.expected_reset_revision != self._memory_reset_revision
        ):
            return

        if capture_args.disposition == SemanticCaptureDisposition.NO_CHANGE:
            proposal = SemanticMemoryProposal(
                disposition=SemanticCaptureDisposition.NO_CHANGE,
                dialogue_frame_update=None,
                journal_events=(),
            )
        else:
            frame_state: DialogueFrameState | None = None
            if capture_args.dialogue_frame is not None:
                valid_frame_refs = tuple(
                    ref
                    for ref in capture_args.dialogue_frame.relevant_canvas_references
                    if self._is_canonical_canvas_reference(ref)
                )
                frame_state = (
                    capture_args.dialogue_frame.model_copy(
                        update={"relevant_canvas_references": valid_frame_refs}
                    )
                    if len(valid_frame_refs)
                    != len(capture_args.dialogue_frame.relevant_canvas_references)
                    else capture_args.dialogue_frame
                )

            filtered_drafts: list[SemanticCaptureJournalDraft] = []
            for draft in capture_args.journal_events:
                if draft.family == SemanticJournalEventFamily.CANVAS_REFERENCE:
                    if draft.canonical_canvas_references and all(
                        self._is_canonical_canvas_reference(ref)
                        for ref in draft.canonical_canvas_references
                    ):
                        filtered_drafts.append(draft)
                else:
                    valid_draft_refs = tuple(
                        ref
                        for ref in draft.canonical_canvas_references
                        if self._is_canonical_canvas_reference(ref)
                    )
                    if len(valid_draft_refs) != len(draft.canonical_canvas_references):
                        filtered_drafts.append(
                            draft.model_copy(
                                update={"canonical_canvas_references": valid_draft_refs}
                            )
                        )
                    else:
                        filtered_drafts.append(draft)

            if not filtered_drafts and (
                frame_state is None or frame_state == self._dialogue_frame.to_state()
            ):
                LOGGER.warning(
                    "semantic_capture_inference_failed session_id=%s turn_id=%s "
                    "reset_revision=%d error_type=%s reason=%s",
                    self._session_id,
                    payload.turn_id,
                    payload.expected_reset_revision,
                    "EmptyChangedCaptureProposalError",
                    "changed disposition produced no valid dialogue frame update or journal events",
                    extra={
                        "ava_context": {
                            "sessionId": self._session_id,
                            "turnId": payload.turn_id,
                            "resetRevision": payload.expected_reset_revision,
                            "status": "failed",
                            "errorType": "EmptyChangedCaptureProposalError",
                            "reason": "changed disposition produced no valid dialogue frame update or journal events",
                        }
                    },
                )
                return

            dialogue_frame_update: DialogueFrameUpdate | None = None
            if (
                frame_state is not None
                and frame_state != self._dialogue_frame.to_state()
            ):
                dialogue_frame_update = DialogueFrameUpdate(
                    base_revision=self._dialogue_frame.revision,
                    frame=frame_state,
                )

            resolved_events = await resolve_semantic_journal_proposals(
                store=self._memory,
                subject=self._subject,
                reset_revision=payload.expected_reset_revision,
                drafts=filtered_drafts,
            )
            proposal = SemanticMemoryProposal(
                disposition=SemanticCaptureDisposition.CHANGED,
                dialogue_frame_update=dialogue_frame_update,
                journal_events=resolved_events,
            )

        proposal_fingerprint = canonical_semantic_memory_proposal_fingerprint(proposal)
        base_revision = (
            proposal.dialogue_frame_update.base_revision
            if proposal.dialogue_frame_update is not None
            else None
        )
        started = monotonic()
        LOGGER.info(
            "semantic_memory_commit_started session_id=%s turn_id=%s "
            "reset_revision=%d disposition=%s proposal_fingerprint_prefix=%s "
            "base_frame_revision=%s proposed_journal_event_count=%d",
            self._session_id,
            payload.turn_id,
            payload.expected_reset_revision,
            proposal.disposition,
            proposal_fingerprint[:12],
            base_revision,
            len(proposal.journal_events),
        )
        try:
            self._validate_semantic_memory_canvas_references(proposal)
            decision = await self._memory.apply_semantic_memory_proposal(
                subject=self._subject,
                expected_reset_revision=payload.expected_reset_revision,
                source_turn_id=payload.turn_id,
                proposal=proposal,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            duration_ms = max(0.0, (monotonic() - started) * 1000)
            LOGGER.warning(
                "semantic_memory_commit_failed session_id=%s turn_id=%s "
                "reset_revision=%d proposal_fingerprint_prefix=%s "
                "base_frame_revision=%s proposed_journal_event_count=%d "
                "duration_ms=%.2f error_type=%s reason_category=%s",
                self._session_id,
                payload.turn_id,
                payload.expected_reset_revision,
                proposal_fingerprint[:12],
                base_revision,
                len(proposal.journal_events),
                duration_ms,
                type(error).__name__,
                _memory_failure_reason_category(error),
                extra={
                    "ava_context": {
                        "sessionId": self._session_id,
                        "turnId": payload.turn_id,
                        "resetRevision": payload.expected_reset_revision,
                        "proposalFingerprintPrefix": proposal_fingerprint[:12],
                        "baseFrameRevision": base_revision,
                        "proposedJournalEventCount": len(proposal.journal_events),
                        "status": "failed",
                        "durationMs": duration_ms,
                        "errorType": type(error).__name__,
                        "reasonCategory": _memory_failure_reason_category(error),
                    }
                },
            )
            return

        duration_ms = max(0.0, (monotonic() - started) * 1000)
        status = str(decision.status)
        event_suffix = (
            "completed"
            if decision.status == SemanticMemoryCommitStatus.APPLIED
            else status
        )
        created_count = sum(
            result.disposition == "created" for result in decision.journal_results
        )
        deduplicated_count = sum(
            result.disposition == "deduplicated" for result in decision.journal_results
        )
        log = (
            LOGGER.info
            if decision.status != SemanticMemoryCommitStatus.STALE
            else LOGGER.warning
        )
        log(
            "semantic_memory_commit_%s session_id=%s turn_id=%s "
            "reset_revision=%d proposal_fingerprint_prefix=%s "
            "base_frame_revision=%s resulting_frame_revision=%d "
            "proposed_journal_event_count=%d created_journal_event_count=%d "
            "deduplicated_journal_event_count=%d duration_ms=%.2f",
            event_suffix,
            self._session_id,
            payload.turn_id,
            payload.expected_reset_revision,
            proposal_fingerprint[:12],
            base_revision,
            decision.dialogue_frame.revision,
            len(proposal.journal_events),
            created_count,
            deduplicated_count,
            duration_ms,
            extra={
                "ava_context": {
                    "sessionId": self._session_id,
                    "turnId": payload.turn_id,
                    "resetRevision": payload.expected_reset_revision,
                    "proposalFingerprintPrefix": proposal_fingerprint[:12],
                    "baseFrameRevision": base_revision,
                    "resultingFrameRevision": decision.dialogue_frame.revision,
                    "proposedJournalEventCount": len(proposal.journal_events),
                    "createdJournalEventCount": created_count,
                    "deduplicatedJournalEventCount": deduplicated_count,
                    "status": status,
                    "durationMs": duration_ms,
                }
            },
        )

        if payload.expected_reset_revision != self._memory_reset_revision:
            return

        prior_frame = self._dialogue_frame
        self._dialogue_frame = decision.dialogue_frame

        if decision.dialogue_frame != prior_frame:
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


def _provider_context_equivalence_key(
    harness: RealtimeHarness,
) -> _ProviderContextEquivalenceKey:
    provider_neutral_update = json.dumps(
        harness.session_update(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (
        harness.prompt_version,
        harness.tool_contract_version,
        harness.reasoning_effort,
        harness.context.content_fingerprint,
        provider_neutral_update,
    )


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
