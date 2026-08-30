from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html import escape
from typing import Protocol

from ..canvas.svg_delivery import (
    StoredSvgCanvasDocument,
    StoredSvgCanvasDraft,
    SvgCanvasDocumentStore,
)
from ..canvas.render_profile import CanvasRenderProfile
from ..canvas.svg_session import (
    ActiveSvgCanvasScene,
    RecordedSvgCanvasInteraction,
    SvgCandidateStatus,
    SvgCanvasCandidate,
    SvgCanvasPresentationCue,
    SvgCanvasSessionState,
    SvgCanvasStateError,
    SvgCanvasStateErrorCode,
)
from ..canvas.svg_specialist import (
    SvgCanvasSpecialist,
    SvgCanvasSpecialistError,
    SvgCanvasSpecialistGenerationLimitError,
    SvgCanvasSpecialistInvalidError,
    SvgCanvasSpecialistRequest,
    SvgCanvasSpecialistUnavailableError,
    SvgCanvasVisibleDraft,
)
from ..canvas.svg_workspace import SvgCanvasExecutionStatus, SvgCanvasWorkspaceError
from ..protocol import AgeBand
from .events_v4 import (
    CanvasDocumentDiscardReason,
    CanvasGenerationRejectionCode,
    CanvasDocumentPreparedEventV4,
    CanvasPreparationFailedEventV4,
    CanvasRuntimeFailedEventV4,
    CanvasInteractionEventV4,
    CanvasPresentedEventV4,
)
from .tool_contract import (
    CANVAS_MODEL_SAFE_REJECTION_REASON,
    InteractiveCanvasOperation,
    InteractiveCanvasToolOutputV4,
    InteractiveCanvasToolStatusV4,
    RealtimeCanvasToolCall,
)

GenerationIdFactory = Callable[[], str]
CompleteCanvasTool = Callable[
    [str, str, InteractiveCanvasToolOutputV4], Awaitable[None]
]

LOGGER = logging.getLogger(__name__)


class SvgCanvasJobEventSink(Protocol):
    async def generation_started(
        self,
        *,
        generation_id: str,
        source_turn_id: str,
    ) -> None: ...

    async def draft_available(self, stored: StoredSvgCanvasDraft) -> None: ...

    async def document_available(self, stored: StoredSvgCanvasDocument) -> None: ...

    async def presentation_cue(
        self,
        candidate: SvgCanvasCandidate,
        cue: SvgCanvasPresentationCue,
    ) -> None: ...

    async def activated(self, active: ActiveSvgCanvasScene) -> None: ...

    async def document_discarded(
        self,
        candidate: SvgCanvasCandidate,
        reason: CanvasDocumentDiscardReason,
    ) -> None: ...

    async def active_document_discarded(
        self,
        active: ActiveSvgCanvasScene,
        reason: CanvasDocumentDiscardReason,
    ) -> None: ...

    async def generation_rejected(
        self,
        *,
        generation_id: str,
        source_turn_id: str,
        code: CanvasGenerationRejectionCode,
        message: str,
        retryable: bool,
    ) -> None: ...

    async def tool_call_rejected(
        self,
        *,
        call_id: str,
        source_turn_id: str,
        message: str,
    ) -> None: ...

    async def interaction_rejected(
        self,
        event: CanvasInteractionEventV4,
        error: SvgCanvasStateError,
    ) -> None: ...


@dataclass(slots=True)
class _AcceptedCanvasJob:
    generation_id: str
    source_turn_id: str
    request: SvgCanvasSpecialistRequest
    accepted_workspace_epoch: int
    workspace_owned: bool = False
    task: asyncio.Task[None] | None = None


@dataclass(frozen=True, slots=True)
class _CanvasGenerationTrace:
    generation_id: str
    source_turn_id: str
    operation: InteractiveCanvasOperation
    accepted_at: float


class SvgCanvasJobCoordinator:
    """Runs Sol outside the speech continuation path and owns SVG lifecycle ACKs."""

    def __init__(
        self,
        *,
        session_id: str,
        age_band: AgeBand,
        render_profile: CanvasRenderProfile | None = None,
        specialist: SvgCanvasSpecialist,
        state: SvgCanvasSessionState,
        documents: SvgCanvasDocumentStore,
        event_sink: SvgCanvasJobEventSink,
        generation_id_factory: GenerationIdFactory,
        monotonic: Callable[[], float] = time.monotonic,
        presentation_timeout_seconds: float = 30.0,
    ) -> None:
        if not 0.01 <= presentation_timeout_seconds <= 180.0:
            raise ValueError(
                "canvas presentation timeout must be between 0.01 and 180 seconds"
            )
        self._session_id = session_id
        self._age_band = age_band
        self._render_profile = (
            render_profile.model_copy(deep=True) if render_profile is not None else None
        )
        self._render_profile_revision = 0
        self._specialist = specialist
        self._state = state
        self._documents = documents
        self._event_sink = event_sink
        self._generation_id_factory = generation_id_factory
        self._monotonic = monotonic
        self._jobs: dict[str, _AcceptedCanvasJob] = {}
        self._traces: dict[str, _CanvasGenerationTrace] = {}
        self._cancelled_generations: set[str] = set()
        self._presentation_offsets: dict[str, int] = {}
        self._presentation_timeouts: dict[str, asyncio.Task[None]] = {}
        self._presentation_timeout_seconds = presentation_timeout_seconds
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def state(self) -> SvgCanvasSessionState:
        return self._state

    @property
    def render_profile_revision(self) -> int:
        return self._render_profile_revision

    async def update_render_profile(
        self,
        *,
        profile_revision: int,
        render_profile: CanvasRenderProfile,
    ) -> bool:
        """Accept a newer session profile without touching reserved work."""

        async with self._lock:
            if self._closed:
                raise RuntimeError("canvas generation is closed")
            if profile_revision <= self._render_profile_revision:
                return False
            self._render_profile = render_profile.model_copy(deep=True)
            self._render_profile_revision = profile_revision
            return True

    async def accept_tool_call(
        self,
        *,
        source_turn_id: str,
        source_transcript: str | None = None,
        call: RealtimeCanvasToolCall,
        complete_tool: CompleteCanvasTool,
    ) -> InteractiveCanvasToolOutputV4:
        rejection_message: str | None = None
        async with self._lock:
            if self._closed:
                rejection_message = "canvas generation is closed"
                output = InteractiveCanvasToolOutputV4.model_validate(
                    {
                        "status": InteractiveCanvasToolStatusV4.REJECTED,
                        "reason": CANVAS_MODEL_SAFE_REJECTION_REASON,
                    }
                )
            elif (
                call.request.operation == InteractiveCanvasOperation.REVISE_FOCUSED
                and self._state.active is None
                and not any(
                    job.workspace_owned
                    and job.generation_id not in self._cancelled_generations
                    for job in self._jobs.values()
                )
                and self._state.candidate is None
            ):
                rejection_message = "There is no current canvas to revise."
                output = InteractiveCanvasToolOutputV4.model_validate(
                    {
                        "status": InteractiveCanvasToolStatusV4.REJECTED,
                        "reason": CANVAS_MODEL_SAFE_REJECTION_REASON,
                    }
                )
            else:
                generation_id = self._generation_id_factory()
                active = (
                    self._state.active
                    if call.request.operation
                    == InteractiveCanvasOperation.REVISE_FOCUSED
                    else None
                )
                accepted_workspace_epoch = self._state.workspace.reset_epoch
                request = SvgCanvasSpecialistRequest.model_validate(
                    {
                        "generationId": generation_id,
                        "sourceTurnId": source_turn_id,
                        "sourceTranscript": source_transcript,
                        "brief": call.request.brief,
                        "interactionMode": call.request.interaction_mode,
                        "ageBand": self._age_band,
                        "renderProfile": (
                            self._render_profile.model_dump(
                                mode="json",
                                by_alias=True,
                            )
                            if self._render_profile is not None
                            else None
                        ),
                        "currentSceneId": active.scene_id if active else None,
                        "currentRevision": active.revision if active else None,
                        "currentSvg": active.document.svg_text if active else None,
                        "currentSvgSha256": (
                            active.document.svg_sha256 if active else None
                        ),
                    }
                )
                job = _AcceptedCanvasJob(
                    generation_id=generation_id,
                    source_turn_id=source_turn_id,
                    request=request,
                    accepted_workspace_epoch=accepted_workspace_epoch,
                )
                self._jobs[generation_id] = job
                self._traces[generation_id] = _CanvasGenerationTrace(
                    generation_id=generation_id,
                    source_turn_id=source_turn_id,
                    operation=call.request.operation,
                    accepted_at=self._monotonic(),
                )
                self._log(
                    "accepted",
                    generation_id=generation_id,
                    source_turn_id=source_turn_id,
                    operation=call.request.operation,
                    baseSceneId=active.scene_id if active else None,
                    baseSceneRevision=active.revision if active else 0,
                    workspaceEpoch=accepted_workspace_epoch,
                )
                output = InteractiveCanvasToolOutputV4.model_validate(
                    {
                        "status": InteractiveCanvasToolStatusV4.ACCEPTED,
                        "generationId": generation_id,
                    }
                )

        completion = asyncio.create_task(
            self._complete_tool_and_start_accepted_job(
                source_turn_id=source_turn_id,
                call=call,
                output=output,
                rejection_message=rejection_message,
                complete_tool=complete_tool,
            ),
            name=f"ava-svg-canvas-accept-{output.generation_id or call.call_id}",
        )
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError:
            # Mechanical turn cancellation must settle the exactly-once tool
            # output and transfer any accepted generation to the workspace
            # before the turn-owned planning task is allowed to finish.
            await completion
            raise
        return output

    async def _complete_tool_and_start_accepted_job(
        self,
        *,
        source_turn_id: str,
        call: RealtimeCanvasToolCall,
        output: InteractiveCanvasToolOutputV4,
        rejection_message: str | None,
        complete_tool: CompleteCanvasTool,
    ) -> None:
        try:
            await complete_tool(source_turn_id, call.call_id, output)
        except Exception:
            if output.generation_id is not None:
                async with self._lock:
                    self._jobs.pop(output.generation_id, None)
                    self._log(
                        "failed",
                        generation_id=output.generation_id,
                        source_turn_id=source_turn_id,
                        failureKind="toolCompletion",
                    )
                    self._traces.pop(output.generation_id, None)
            raise
        if output.generation_id is None:
            await self._event_sink.tool_call_rejected(
                call_id=call.call_id,
                source_turn_id=source_turn_id,
                message=(
                    rejection_message or "The optional visual request was rejected."
                ),
            )
            return

        async with self._lock:
            reserved_job = self._jobs.get(output.generation_id)
            if reserved_job is None:
                raise RuntimeError(
                    "accepted SVG canvas generation lost its reservation"
                )
            # The function output is now accepted by Realtime. From this point
            # the generation belongs to the canvas workspace, not the speech
            # turn that supplied its provenance.
            reserved_job.workspace_owned = True
            try:
                self._state.workspace.start_execution(
                    generation_id=reserved_job.generation_id,
                    source_turn_id=reserved_job.source_turn_id,
                    operation=str(call.request.operation),
                )
            except SvgCanvasWorkspaceError:
                self._jobs.pop(output.generation_id, None)
                self._traces.pop(output.generation_id, None)
                raise
            if self._closed or output.generation_id in self._cancelled_generations:
                self._jobs.pop(output.generation_id, None)
                self._cancelled_generations.discard(output.generation_id)
                self._finish_execution_if_pending(
                    output.generation_id,
                    SvgCanvasExecutionStatus.CANCELLED,
                    reason="cancelledBeforeGenerationStart",
                )
                self._log(
                    "cancelled",
                    generation_id=output.generation_id,
                    source_turn_id=source_turn_id,
                    cancellationPoint="beforeGenerationStart",
                )
                self._traces.pop(output.generation_id, None)
                return

        # A newly accepted visual operation is the only ordinary action that
        # replaces pending workspace work. Retire older workspace work before
        # starting this job so an older generation cannot stage a late candidate.
        await self._cancel_workspace_pending(
            exclude_generation_id=output.generation_id,
            discard_reason=CanvasDocumentDiscardReason.SUPERSEDED,
            rejection_message="canvas generation was superseded by a new visual request",
            terminal_status=SvgCanvasExecutionStatus.SUPERSEDED,
        )

        async with self._lock:
            reserved_job = self._jobs.get(output.generation_id)
            if reserved_job is None:
                raise RuntimeError(
                    "accepted SVG canvas generation lost its reservation"
                )
            if self._closed or output.generation_id in self._cancelled_generations:
                self._jobs.pop(output.generation_id, None)
                self._cancelled_generations.discard(output.generation_id)
                self._finish_execution_if_pending(
                    output.generation_id,
                    SvgCanvasExecutionStatus.CANCELLED,
                    reason="cancelledBeforeGenerationStart",
                )
                self._log(
                    "cancelled",
                    generation_id=output.generation_id,
                    source_turn_id=source_turn_id,
                    cancellationPoint="beforeGenerationStart",
                )
                self._traces.pop(output.generation_id, None)
                return
            try:
                # Serialize the visible start with cancellation. Once this
                # event is queued, an interrupt may follow with a rejection;
                # a rejection can never be followed by a late start.
                await self._event_sink.generation_started(
                    generation_id=output.generation_id,
                    source_turn_id=source_turn_id,
                )
                self._log(
                    "generationStarted",
                    generation_id=output.generation_id,
                    source_turn_id=source_turn_id,
                )
            except Exception:
                self._jobs.pop(output.generation_id, None)
                self._finish_execution_if_pending(
                    output.generation_id,
                    SvgCanvasExecutionStatus.FAILED,
                    reason="generationStartedDelivery",
                )
                self._log(
                    "failed",
                    generation_id=output.generation_id,
                    source_turn_id=source_turn_id,
                    failureKind="generationStartedDelivery",
                )
                self._traces.pop(output.generation_id, None)
                raise
            reserved_job.task = asyncio.create_task(
                self._run_generation(reserved_job),
                name=f"ava-svg-canvas-{reserved_job.generation_id}",
            )

    async def document_prepared(
        self,
        event: CanvasDocumentPreparedEventV4,
    ) -> SvgCanvasCandidate:
        cue: SvgCanvasPresentationCue | None = None
        async with self._lock:
            candidate = self._state.candidate_or_error(event.generation_id)
            self._require_scene_identity(
                candidate,
                scene_id=event.scene_id,
                scene_revision=event.scene_revision,
            )
            candidate = self._state.mark_document_prepared(
                generation_id=event.generation_id,
                svg_sha256=event.svg_sha256,
                manifest_sha256=event.manifest_sha256,
                renderer_capability_version=event.renderer_capability_version,
                validator_version=event.validator_version,
            )
            self._log(
                "documentPrepared",
                generation_id=candidate.generation_id,
                source_turn_id=candidate.source_turn_id,
                sceneId=candidate.scene_id,
                sceneRevision=candidate.revision,
                svgSha256=candidate.document.svg_sha256,
                manifestSha256=candidate.document.manifest_sha256,
            )
            sample_offset = self._presentation_offsets.get(event.generation_id)
            if sample_offset is not None:
                cue = self._state.schedule_presentation(
                    generation_id=event.generation_id,
                    presentation_sample_offset=sample_offset,
                )
                candidate = self._state.candidate or candidate
        if cue is not None:
            await self._event_sink.presentation_cue(candidate, cue)
            self._log(
                "presentationCueSent",
                generation_id=candidate.generation_id,
                source_turn_id=candidate.source_turn_id,
                sceneId=candidate.scene_id,
                sceneRevision=candidate.revision,
                presentationSampleOffset=cue.presentation_sample_offset,
            )
        return candidate

    async def request_presentation(
        self,
        *,
        generation_id: str,
        presentation_sample_offset: int,
    ) -> SvgCanvasPresentationCue | None:
        cue: SvgCanvasPresentationCue | None = None
        candidate: SvgCanvasCandidate | None
        async with self._lock:
            if generation_id in self._presentation_offsets:
                raise SvgCanvasStateError(
                    code=SvgCanvasStateErrorCode.INVALID_CANDIDATE_TRANSITION,
                    message="canvas presentation sample was already assigned",
                )
            candidate = self._state.candidate
            if candidate is None:
                job = self._jobs.get(generation_id)
                if job is None:
                    raise SvgCanvasStateError(
                        code=SvgCanvasStateErrorCode.GENERATION_MISMATCH,
                        message="canvas presentation generation is not pending",
                    )
                self._presentation_offsets[generation_id] = presentation_sample_offset
                self._log(
                    "presentationRequested",
                    generation_id=generation_id,
                    source_turn_id=job.source_turn_id,
                    presentationSampleOffset=presentation_sample_offset,
                )
                return None
            if candidate.generation_id != generation_id:
                raise SvgCanvasStateError(
                    code=SvgCanvasStateErrorCode.GENERATION_MISMATCH,
                    message="canvas presentation generation does not match candidate",
                )
            if candidate.status not in {
                SvgCandidateStatus.DOCUMENT_READY,
                SvgCandidateStatus.PREPARED,
            }:
                raise SvgCanvasStateError(
                    code=SvgCanvasStateErrorCode.INVALID_CANDIDATE_TRANSITION,
                    message="canvas presentation is already scheduled",
                )
            self._presentation_offsets[generation_id] = presentation_sample_offset
            self._log(
                "presentationRequested",
                generation_id=generation_id,
                source_turn_id=candidate.source_turn_id,
                presentationSampleOffset=presentation_sample_offset,
            )
            if candidate.status is SvgCandidateStatus.PREPARED:
                cue = self._state.schedule_presentation(
                    generation_id=generation_id,
                    presentation_sample_offset=presentation_sample_offset,
                )
                candidate = self._state.candidate or candidate
        if cue is not None:
            if candidate is None:  # pragma: no cover - cue requires a candidate
                raise RuntimeError("canvas cue lost its prepared candidate")
            await self._event_sink.presentation_cue(candidate, cue)
            self._log(
                "presentationCueSent",
                generation_id=candidate.generation_id,
                source_turn_id=candidate.source_turn_id,
                sceneId=candidate.scene_id,
                sceneRevision=candidate.revision,
                presentationSampleOffset=cue.presentation_sample_offset,
            )
        return cue

    async def request_interrupted_presentation(
        self,
        *,
        source_turn_id: str,
        presentation_sample_offset: int,
    ) -> SvgCanvasPresentationCue | None:
        """Anchor accepted workspace work to its source clock's last sample.

        The source turn identifies the existing rendered-sample clock only; it
        does not own or terminate the accepted canvas generation.
        """

        replacement_cue: SvgCanvasPresentationCue | None = None
        replacement_candidate: SvgCanvasCandidate | None = None
        async with self._lock:
            pending_generation_id: str | None = None
            candidate = self._state.candidate
            if candidate is not None and candidate.source_turn_id == source_turn_id:
                pending_generation_id = candidate.generation_id
            else:
                matching_jobs = [
                    job
                    for job in self._jobs.values()
                    if job.workspace_owned
                    and job.source_turn_id == source_turn_id
                    and job.generation_id not in self._cancelled_generations
                ]
                if matching_jobs:
                    pending_generation_id = max(
                        matching_jobs,
                        key=lambda job: self._traces[job.generation_id].accepted_at,
                    ).generation_id
            if pending_generation_id is None:
                return None
            existing = self._presentation_offsets.get(pending_generation_id)
            if existing is not None:
                if existing <= presentation_sample_offset:
                    return None
                self._presentation_offsets[pending_generation_id] = (
                    presentation_sample_offset
                )
                candidate = self._state.candidate
                if (
                    candidate is not None
                    and candidate.generation_id == pending_generation_id
                    and candidate.status is SvgCandidateStatus.PRESENTATION_SCHEDULED
                ):
                    replacement_cue = self._state.reschedule_presentation_earlier(
                        generation_id=pending_generation_id,
                        presentation_sample_offset=presentation_sample_offset,
                    )
                    replacement_candidate = self._state.candidate
                self._log(
                    "presentationRequested",
                    generation_id=pending_generation_id,
                    source_turn_id=source_turn_id,
                    presentationSampleOffset=presentation_sample_offset,
                    replacedUnreachableSampleOffset=existing,
                )

        if replacement_cue is not None:
            if replacement_candidate is None:  # pragma: no cover - cue requires it
                raise RuntimeError("replacement canvas cue lost its candidate")
            await self._event_sink.presentation_cue(
                replacement_candidate,
                replacement_cue,
            )
            self._log(
                "presentationCueSent",
                generation_id=replacement_candidate.generation_id,
                source_turn_id=replacement_candidate.source_turn_id,
                sceneId=replacement_candidate.scene_id,
                sceneRevision=replacement_candidate.revision,
                presentationSampleOffset=(replacement_cue.presentation_sample_offset),
                replacedUnreachableCue=True,
            )
            return replacement_cue
        if existing is not None:
            return None

        return await self.request_presentation(
            generation_id=pending_generation_id,
            presentation_sample_offset=presentation_sample_offset,
        )

    async def preparation_failed(
        self,
        event: CanvasPreparationFailedEventV4,
    ) -> SvgCanvasCandidate:
        async with self._lock:
            candidate = self._state.candidate_or_error(event.generation_id)
            self._require_scene_identity(
                candidate,
                scene_id=event.scene_id,
                scene_revision=event.scene_revision,
            )
            self._validate_candidate_hashes(
                candidate,
                svg_sha256=event.svg_sha256,
                manifest_sha256=event.manifest_sha256,
            )
            discarded = self._state.discard_candidate(generation_id=event.generation_id)
            self._presentation_offsets.pop(event.generation_id, None)
            self._cancel_presentation_timeout(event.generation_id)
            self._finish_execution_if_pending(
                event.generation_id,
                SvgCanvasExecutionStatus.FAILED,
                reason=f"clientPreparation:{event.failure_code}",
            )
        await self._documents.discard(
            session_id=self._session_id,
            generation_id=event.generation_id,
        )
        await self._documents.discard_drafts(
            session_id=self._session_id,
            generation_id=event.generation_id,
        )
        await self._event_sink.generation_rejected(
            generation_id=event.generation_id,
            source_turn_id=discarded.source_turn_id,
            code=CanvasGenerationRejectionCode.PREPARATION_FAILED,
            message="The device could not prepare the canvas document.",
            retryable=False,
        )
        self._log(
            "failed",
            generation_id=event.generation_id,
            source_turn_id=discarded.source_turn_id,
            failureKind="clientPreparation",
        )
        self._traces.pop(event.generation_id, None)
        return discarded

    async def presented(
        self,
        event: CanvasPresentedEventV4,
    ) -> ActiveSvgCanvasScene:
        async with self._lock:
            candidate = self._state.candidate_or_error(event.generation_id)
            self._require_scene_identity(
                candidate,
                scene_id=event.scene_id,
                scene_revision=event.scene_revision,
            )
            active = self._state.mark_presented(
                generation_id=event.generation_id,
                svg_sha256=event.svg_sha256,
                manifest_sha256=event.manifest_sha256,
                first_rendered_sample=event.first_rendered_sample,
            )
            self._presentation_offsets.pop(event.generation_id, None)
            self._cancel_presentation_timeout(event.generation_id)
            self._log(
                "presentationAcknowledged",
                generation_id=active.generation_id,
                source_turn_id=active.source_turn_id,
                sceneId=active.scene_id,
                sceneRevision=active.revision,
                firstRenderedSample=active.first_rendered_sample,
            )
        try:
            await self._event_sink.activated(active)
        except Exception:
            self._log(
                "failed",
                generation_id=active.generation_id,
                source_turn_id=active.source_turn_id,
                failureKind="activationDelivery",
            )
            self._traces.pop(active.generation_id, None)
            raise
        await self._documents.discard(
            session_id=self._session_id,
            generation_id=active.generation_id,
        )
        await self._documents.discard_drafts(
            session_id=self._session_id,
            generation_id=active.generation_id,
        )
        trace = self._traces.get(active.generation_id)
        accepted_to_activation_ms = (
            max(0.0, (self._monotonic() - trace.accepted_at) * 1000)
            if trace is not None
            else None
        )
        self._log(
            "activationCompleted",
            generation_id=active.generation_id,
            source_turn_id=active.source_turn_id,
            sceneId=active.scene_id,
            sceneRevision=active.revision,
            firstRenderedSample=active.first_rendered_sample,
            acceptedToActivationMs=accepted_to_activation_ms,
        )
        self._traces.pop(active.generation_id, None)
        return active

    async def runtime_failed(
        self,
        event: CanvasRuntimeFailedEventV4,
    ) -> ActiveSvgCanvasScene:
        async with self._lock:
            active = self._state.require_active_identity(
                generation_id=event.generation_id,
                scene_id=event.scene_id,
                scene_revision=event.scene_revision,
                svg_sha256=event.svg_sha256,
                manifest_sha256=event.manifest_sha256,
            )
            self._log(
                "runtimeFailed",
                generation_id=active.generation_id,
                source_turn_id=active.source_turn_id,
                sceneId=active.scene_id,
                sceneRevision=active.revision,
                failureCode=event.failure_code,
            )
        return active

    async def interaction(
        self,
        event: CanvasInteractionEventV4,
    ) -> RecordedSvgCanvasInteraction | None:
        try:
            async with self._lock:
                return self._state.record_interaction(
                    scene_id=event.scene_id,
                    scene_revision=event.scene_revision,
                    interaction_revision=event.interaction_revision,
                    svg_sha256=event.svg_sha256,
                    manifest_sha256=event.manifest_sha256,
                    binding_id=event.binding_id,
                )
        except SvgCanvasStateError as error:
            await self._event_sink.interaction_rejected(event, error)
            return None

    async def cancel_pending(self) -> tuple[str, ...]:
        """Explicitly cancel unpresented canvas-workspace work.

        An active presented scene remains intact; only a generating or prepared
        candidate is retired.
        """

        return await self._cancel_workspace_pending(
            discard_reason=CanvasDocumentDiscardReason.SUPERSEDED,
            rejection_message="canvas generation was explicitly cancelled",
            terminal_status=SvgCanvasExecutionStatus.CANCELLED,
        )

    async def _cancel_workspace_pending(
        self,
        *,
        discard_reason: CanvasDocumentDiscardReason,
        rejection_message: str,
        terminal_status: SvgCanvasExecutionStatus,
        exclude_generation_id: str | None = None,
    ) -> tuple[str, ...]:
        async with self._lock:
            jobs = [
                job
                for job in self._jobs.values()
                if job.workspace_owned
                and job.generation_id != exclude_generation_id
                and job.generation_id not in self._cancelled_generations
            ]
            self._cancelled_generations.update(job.generation_id for job in jobs)
            candidate = self._state.candidate
            if (
                candidate is not None
                and candidate.generation_id != exclude_generation_id
            ):
                candidate = self._state.discard_candidate(
                    generation_id=candidate.generation_id
                )
            else:
                candidate = None
            if candidate is not None:
                self._presentation_offsets.pop(candidate.generation_id, None)
        return await self._finalize_pending_cancellation(
            jobs=jobs,
            candidate=candidate,
            discard_reason=discard_reason,
            rejection_message=rejection_message,
            terminal_status=terminal_status,
        )

    async def _finalize_pending_cancellation(
        self,
        *,
        jobs: list[_AcceptedCanvasJob],
        candidate: SvgCanvasCandidate | None,
        discard_reason: CanvasDocumentDiscardReason,
        rejection_message: str,
        terminal_status: SvgCanvasExecutionStatus = SvgCanvasExecutionStatus.CANCELLED,
    ) -> tuple[str, ...]:
        for job in jobs:
            try:
                await self._specialist.cancel(generation_id=job.generation_id)
            except Exception:
                LOGGER.exception(
                    "canvas_specialist_cancel_failed session_id=%s "
                    "source_turn_id=%s generation_id=%s",
                    self._session_id,
                    job.source_turn_id,
                    job.generation_id,
                )
            finally:
                if job.task is not None and not job.task.done():
                    job.task.cancel()
        tasks = [job.task for job in jobs if job.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for job in jobs:
            self._finish_execution_if_pending(
                job.generation_id,
                terminal_status,
                reason=rejection_message,
            )
        if candidate is not None:
            self._finish_execution_if_pending(
                candidate.generation_id,
                terminal_status,
                reason=rejection_message,
            )
        retired: list[str] = []
        if candidate is not None:
            await self._documents.discard(
                session_id=self._session_id,
                generation_id=candidate.generation_id,
            )
            await self._documents.discard_drafts(
                session_id=self._session_id,
                generation_id=candidate.generation_id,
            )
            self._cancel_presentation_timeout(candidate.generation_id)
            await self._event_sink.document_discarded(
                candidate,
                discard_reason,
            )
            retired.append(candidate.generation_id)
        for job in jobs:
            if candidate is None or candidate.generation_id != job.generation_id:
                await self._event_sink.generation_rejected(
                    generation_id=job.generation_id,
                    source_turn_id=job.source_turn_id,
                    code=CanvasGenerationRejectionCode.CANCELLED,
                    message=rejection_message,
                    retryable=False,
                )
                retired.append(job.generation_id)
            if candidate is None or candidate.generation_id != job.generation_id:
                self._log(
                    "cancelled",
                    generation_id=job.generation_id,
                    source_turn_id=job.source_turn_id,
                    cancellationPoint="pendingGeneration",
                )
                self._traces.pop(job.generation_id, None)
        if candidate is not None:
            self._log(
                "cancelled",
                generation_id=candidate.generation_id,
                source_turn_id=candidate.source_turn_id,
                cancellationPoint="pendingCandidate",
            )
            self._traces.pop(candidate.generation_id, None)
        return tuple(retired)

    def render_realtime_context(self) -> str:
        """Render bounded semantic canvas state without transport identifiers."""

        pending_visual = self._render_pending_visual_context()
        return self._state.render_realtime_context(pending_visual=pending_visual)

    def _render_pending_visual_context(self) -> str:
        workspace_jobs = [
            job
            for job in self._jobs.values()
            if job.workspace_owned
            and job.generation_id not in self._cancelled_generations
        ]
        if workspace_jobs:
            job = max(
                workspace_jobs,
                key=lambda item: self._traces[item.generation_id].accepted_at,
            )
            trace = self._traces[job.generation_id]
            return (
                '<pending_visual state="generating" '
                f'operation="{escape(str(trace.operation))}" '
                f'interaction_mode="{escape(str(job.request.interaction_mode))}">'
                f"<purpose>{escape(job.request.brief)}</purpose>"
                "</pending_visual>"
            )
        candidate = self._state.candidate
        if candidate is None:
            return ""
        document = candidate.document
        return (
            '<pending_visual state="ready" '
            f'interaction_mode="{escape(str(document.interaction_mode))}">'
            f"<title>{escape(document.title)}</title>"
            f"<description>{escape(document.description)}</description>"
            "</pending_visual>"
        )

    async def wait_for_background_work(self) -> None:
        async with self._lock:
            tasks = [job.task for job in self._jobs.values() if job.task is not None]
        if tasks:
            await asyncio.gather(*tasks)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            jobs = list(self._jobs.values())
            self._cancelled_generations.update(job.generation_id for job in jobs)
            candidate = self._state.candidate
            if candidate is not None:
                self._state.discard_candidate(generation_id=candidate.generation_id)
                self._presentation_offsets.pop(candidate.generation_id, None)
            timeout_tasks = tuple(self._presentation_timeouts.values())
            self._presentation_timeouts.clear()
            for job in jobs:
                if job.workspace_owned:
                    self._finish_execution_if_pending(
                        job.generation_id,
                        SvgCanvasExecutionStatus.CANCELLED,
                        reason="sessionClosed",
                    )
            if candidate is not None:
                self._finish_execution_if_pending(
                    candidate.generation_id,
                    SvgCanvasExecutionStatus.CANCELLED,
                    reason="sessionClosed",
                )
        for timeout_task in timeout_tasks:
            timeout_task.cancel()
        if timeout_tasks:
            await asyncio.gather(*timeout_tasks, return_exceptions=True)
        for job in jobs:
            try:
                await self._specialist.cancel(generation_id=job.generation_id)
            except Exception:
                LOGGER.exception(
                    "canvas_specialist_close_cancel_failed session_id=%s "
                    "generation_id=%s",
                    self._session_id,
                    job.generation_id,
                )
            finally:
                if job.task is not None and not job.task.done():
                    job.task.cancel()
        tasks = [job.task for job in jobs if job.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._specialist.close()
        await self._documents.release_session(session_id=self._session_id)
        self._traces.clear()

    async def _run_generation(self, job: _AcceptedCanvasJob) -> None:
        try:
            async with self._lock:
                if (
                    job.generation_id not in self._jobs
                    or job.generation_id in self._cancelled_generations
                ):
                    return
            self._log(
                "specialistStarted",
                generation_id=job.generation_id,
                source_turn_id=job.source_turn_id,
            )
            generation = await self._specialist.generate(
                job.request,
                draft_sink=lambda draft: self._publish_visible_draft(job, draft),
            )
            async with self._lock:
                if (
                    job.generation_id not in self._jobs
                    or job.generation_id in self._cancelled_generations
                ):
                    return
                candidate = self._state.stage_generation(
                    generation,
                    source_turn_id=job.source_turn_id,
                    base_scene_id=job.request.current_scene_id,
                    base_revision=job.request.current_revision or 0,
                    workspace_epoch=job.accepted_workspace_epoch,
                )
                publication_started_at = self._monotonic()
                stored = await self._documents.publish(
                    session_id=self._session_id,
                    candidate=candidate,
                )
                self._state.workspace.advance_execution(
                    job.generation_id,
                    SvgCanvasExecutionStatus.PREPARING,
                )
                self._log(
                    "documentPublished",
                    generation_id=job.generation_id,
                    source_turn_id=job.source_turn_id,
                    duration_from=publication_started_at,
                    sceneId=candidate.scene_id,
                    sceneRevision=candidate.revision,
                    svgByteLength=len(candidate.document.svg_bytes),
                    manifestByteLength=len(candidate.document.manifest_bytes),
                    svgSha256=candidate.document.svg_sha256,
                    manifestSha256=candidate.document.manifest_sha256,
                )
            await self._event_sink.document_available(stored)
            async with self._lock:
                if self._state.candidate is not None and (
                    self._state.candidate.generation_id == job.generation_id
                ):
                    self._start_presentation_timeout(job)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            async with self._lock:
                cancelled = job.generation_id in self._cancelled_generations
                pending_candidate = self._state.candidate
                if (
                    pending_candidate is not None
                    and pending_candidate.generation_id == job.generation_id
                ):
                    self._state.discard_candidate(generation_id=job.generation_id)
                self._presentation_offsets.pop(job.generation_id, None)
                self._cancel_presentation_timeout(job.generation_id)
            if cancelled:
                return
            code, retryable = _generation_failure(error)
            self._finish_execution_if_pending(
                job.generation_id,
                SvgCanvasExecutionStatus.FAILED,
                reason=str(code),
            )
            await self._documents.discard(
                session_id=self._session_id,
                generation_id=job.generation_id,
            )
            await self._documents.discard_drafts(
                session_id=self._session_id,
                generation_id=job.generation_id,
            )
            await self._event_sink.generation_rejected(
                generation_id=job.generation_id,
                source_turn_id=job.source_turn_id,
                code=code,
                message=_bounded_failure_message(error),
                retryable=retryable,
            )
            self._log(
                "failed",
                generation_id=job.generation_id,
                source_turn_id=job.source_turn_id,
                failureKind=code,
                retryable=retryable,
            )
            self._traces.pop(job.generation_id, None)
        finally:
            async with self._lock:
                self._jobs.pop(job.generation_id, None)
                self._cancelled_generations.discard(job.generation_id)

    async def _publish_visible_draft(
        self,
        job: _AcceptedCanvasJob,
        draft: SvgCanvasVisibleDraft,
    ) -> None:
        async with self._lock:
            if (
                self._closed
                or job.generation_id not in self._jobs
                or job.generation_id in self._cancelled_generations
            ):
                return
            if draft.generation_id != job.generation_id:
                raise RuntimeError(
                    "SVG draft generation identity does not match its job"
                )
            self._state.workspace.advance_execution(
                job.generation_id,
                SvgCanvasExecutionStatus.DRAFT_VISIBLE,
            )
            stored = await self._documents.publish_draft(
                session_id=self._session_id,
                source_turn_id=job.source_turn_id,
                draft=draft,
            )
        await self._event_sink.draft_available(stored)
        self._log(
            "draftAvailable",
            generation_id=job.generation_id,
            source_turn_id=job.source_turn_id,
            draftIndex=draft.draft_index,
            attempt=draft.attempt,
            svgByteLength=len(draft.document.svg_bytes),
            svgSha256=draft.document.svg_sha256,
            authoritative=False,
            interactive=False,
        )

    def _start_presentation_timeout(self, job: _AcceptedCanvasJob) -> None:
        if job.generation_id in self._presentation_timeouts:
            raise RuntimeError("canvas presentation timeout was already started")
        self._presentation_timeouts[job.generation_id] = asyncio.create_task(
            self._expire_unpresented_candidate(job),
            name=f"ava-svg-presentation-timeout-{job.generation_id}",
        )

    async def _expire_unpresented_candidate(self, job: _AcceptedCanvasJob) -> None:
        timeout_task = asyncio.current_task()
        try:
            await asyncio.sleep(self._presentation_timeout_seconds)
            async with self._lock:
                candidate = self._state.candidate
                if (
                    self._closed
                    or candidate is None
                    or candidate.generation_id != job.generation_id
                ):
                    return
                discarded = self._state.discard_candidate(
                    generation_id=job.generation_id
                )
                self._presentation_offsets.pop(job.generation_id, None)
                self._finish_execution_if_pending(
                    job.generation_id,
                    SvgCanvasExecutionStatus.FAILED,
                    reason="presentationTimeout",
                )
            await self._documents.discard(
                session_id=self._session_id,
                generation_id=job.generation_id,
            )
            await self._documents.discard_drafts(
                session_id=self._session_id,
                generation_id=job.generation_id,
            )
            await self._event_sink.document_discarded(
                discarded,
                CanvasDocumentDiscardReason.PRESENTATION_TIMEOUT,
            )
            await self._event_sink.generation_rejected(
                generation_id=job.generation_id,
                source_turn_id=job.source_turn_id,
                code=CanvasGenerationRejectionCode.PRESENTATION_FAILED,
                message="The canvas was not presented before its deadline.",
                retryable=False,
            )
            self._log(
                "failed",
                generation_id=job.generation_id,
                source_turn_id=job.source_turn_id,
                failureKind="presentationTimeout",
            )
            self._traces.pop(job.generation_id, None)
        finally:
            async with self._lock:
                current = self._presentation_timeouts.get(job.generation_id)
                if current is timeout_task:
                    self._presentation_timeouts.pop(job.generation_id, None)

    def _cancel_presentation_timeout(self, generation_id: str) -> None:
        task = self._presentation_timeouts.pop(generation_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _finish_execution_if_pending(
        self,
        generation_id: str,
        status: SvgCanvasExecutionStatus,
        *,
        reason: str,
    ) -> None:
        execution = self._state.workspace.execution(generation_id)
        if execution is None or execution.status.terminal:
            return
        self._state.workspace.finish_execution(
            generation_id,
            status,
            reason=reason,
        )

    def _log(
        self,
        stage: str,
        *,
        generation_id: str,
        source_turn_id: str,
        duration_from: float | None = None,
        **fields: object,
    ) -> None:
        context: dict[str, object] = {
            "component": "svgCanvasCoordinator",
            "stage": stage,
            "sessionId": self._session_id,
            "generationId": generation_id,
            "sourceTurnId": source_turn_id,
        }
        trace = self._traces.get(generation_id)
        if trace is not None:
            context["operation"] = trace.operation
            context["acceptedToStageMs"] = max(
                0.0, (self._monotonic() - trace.accepted_at) * 1000
            )
        if duration_from is not None:
            context["durationMs"] = max(0.0, (self._monotonic() - duration_from) * 1000)
        context.update(
            {key: value for key, value in fields.items() if value is not None}
        )
        LOGGER.info("svg_canvas_lifecycle", extra={"ava_context": context})

    @staticmethod
    def _require_scene_identity(
        candidate: SvgCanvasCandidate,
        *,
        scene_id: str,
        scene_revision: int,
    ) -> None:
        if candidate.scene_id != scene_id or candidate.revision != scene_revision:
            raise SvgCanvasStateError(
                code=SvgCanvasStateErrorCode.SCENE_MISMATCH,
                message="canvas scene identity does not match the pending candidate",
            )

    @staticmethod
    def _validate_candidate_hashes(
        candidate: SvgCanvasCandidate,
        *,
        svg_sha256: str,
        manifest_sha256: str,
    ) -> None:
        if (
            candidate.document.svg_sha256 != svg_sha256
            or candidate.document.manifest_sha256 != manifest_sha256
        ):
            raise SvgCanvasStateError(
                code=SvgCanvasStateErrorCode.CONTENT_HASH_MISMATCH,
                message="canvas content identity does not match the pending candidate",
            )


def _generation_failure(
    error: Exception,
) -> tuple[CanvasGenerationRejectionCode, bool]:
    if isinstance(error, SvgCanvasSpecialistGenerationLimitError):
        return CanvasGenerationRejectionCode.GENERATION_LIMIT_EXCEEDED, True
    if isinstance(error, SvgCanvasSpecialistUnavailableError):
        return CanvasGenerationRejectionCode.PROVIDER_UNAVAILABLE, True
    if isinstance(error, SvgCanvasSpecialistInvalidError):
        return CanvasGenerationRejectionCode.PROVIDER_INVALID, False
    if isinstance(error, SvgCanvasSpecialistError):
        return CanvasGenerationRejectionCode.PROVIDER_INVALID, False
    return CanvasGenerationRejectionCode.PREPARATION_FAILED, False


def _bounded_failure_message(error: Exception) -> str:
    if isinstance(error, SvgCanvasSpecialistGenerationLimitError):
        return "The canvas illustrator exceeded its generation limit."
    if isinstance(error, SvgCanvasSpecialistUnavailableError):
        return "Ava could not reach the canvas illustrator."
    if isinstance(error, SvgCanvasSpecialistInvalidError):
        return "The canvas illustrator returned an invalid static SVG."
    return f"Canvas preparation failed ({type(error).__name__})."[:240]
