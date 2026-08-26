from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from ..canvas.svg_delivery import (
    StoredSvgCanvasDocument,
    SvgCanvasDocumentStore,
)
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
)
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
        specialist: SvgCanvasSpecialist,
        state: SvgCanvasSessionState,
        documents: SvgCanvasDocumentStore,
        event_sink: SvgCanvasJobEventSink,
        generation_id_factory: GenerationIdFactory,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_id = session_id
        self._age_band = age_band
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
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def state(self) -> SvgCanvasSessionState:
        return self._state

    async def accept_tool_call(
        self,
        *,
        source_turn_id: str,
        call: RealtimeCanvasToolCall,
        complete_tool: CompleteCanvasTool,
    ) -> InteractiveCanvasToolOutputV4:
        async with self._lock:
            if self._closed:
                output = InteractiveCanvasToolOutputV4.model_validate(
                    {
                        "status": InteractiveCanvasToolStatusV4.REJECTED,
                        "reason": "canvas generation is closed",
                    }
                )
            elif self._jobs or self._state.candidate is not None:
                output = InteractiveCanvasToolOutputV4.model_validate(
                    {
                        "status": InteractiveCanvasToolStatusV4.REJECTED,
                        "reason": "another canvas generation is already pending",
                    }
                )
            elif (
                call.request.operation == InteractiveCanvasOperation.REVISE_FOCUSED
                and self._state.active is None
            ):
                output = InteractiveCanvasToolOutputV4.model_validate(
                    {
                        "status": InteractiveCanvasToolStatusV4.REJECTED,
                        "reason": "There is no current canvas to revise.",
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
                        "brief": call.request.brief,
                        "interactionMode": call.request.interaction_mode,
                        "interactionGoal": call.request.interaction_goal,
                        "ageBand": self._age_band,
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
                message=output.reason or "The optional visual request was rejected.",
            )
            return output

        async with self._lock:
            reserved_job = self._jobs.get(output.generation_id)
            if reserved_job is None:
                raise RuntimeError(
                    "accepted SVG canvas generation lost its reservation"
                )
            if self._closed or output.generation_id in self._cancelled_generations:
                self._jobs.pop(output.generation_id, None)
                self._cancelled_generations.discard(output.generation_id)
                self._log(
                    "cancelled",
                    generation_id=output.generation_id,
                    source_turn_id=source_turn_id,
                    cancellationPoint="beforeGenerationStart",
                )
                self._traces.pop(output.generation_id, None)
                return output
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
        return output

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
        await self._documents.discard(
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

    async def interrupt_turn(self, *, source_turn_id: str) -> None:
        await self._cancel_pending_turn(
            source_turn_id=source_turn_id,
            discard_reason=CanvasDocumentDiscardReason.INTERRUPTED_BEFORE_PRESENTATION,
            rejection_message="canvas generation was cancelled by interruption",
        )

    async def expire_turn(self, *, source_turn_id: str) -> tuple[str, ...]:
        """Retire unpresented work when explicitly superseding its source."""

        return await self._cancel_pending_turn(
            source_turn_id=source_turn_id,
            discard_reason=CanvasDocumentDiscardReason.SUPERSEDED,
            rejection_message="canvas generation was explicitly superseded",
        )

    async def _cancel_pending_turn(
        self,
        *,
        source_turn_id: str,
        discard_reason: CanvasDocumentDiscardReason,
        rejection_message: str,
    ) -> tuple[str, ...]:
        async with self._lock:
            jobs = [
                job
                for job in self._jobs.values()
                if job.source_turn_id == source_turn_id
            ]
            self._cancelled_generations.update(job.generation_id for job in jobs)
            candidate = self._state.discard_candidate_for_interrupted_turn(
                source_turn_id=source_turn_id
            )
            if candidate is not None:
                self._presentation_offsets.pop(candidate.generation_id, None)
        for job in jobs:
            try:
                await self._specialist.cancel(generation_id=job.generation_id)
            except Exception:
                LOGGER.exception(
                    "canvas_specialist_cancel_failed session_id=%s "
                    "source_turn_id=%s generation_id=%s",
                    self._session_id,
                    source_turn_id,
                    job.generation_id,
                )
            finally:
                if job.task is not None and not job.task.done():
                    job.task.cancel()
        tasks = [job.task for job in jobs if job.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        retired: list[str] = []
        if candidate is not None:
            await self._documents.discard(
                session_id=self._session_id,
                generation_id=candidate.generation_id,
            )
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
            generation = await self._specialist.generate(job.request)
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
            await self._documents.discard(
                session_id=self._session_id,
                generation_id=job.generation_id,
            )
            if cancelled:
                return
            code, retryable = _generation_failure(error)
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
