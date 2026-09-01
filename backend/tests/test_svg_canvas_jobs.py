from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator

import pytest

from ava_backend.canvas.svg_delivery import (
    StoredSvgCanvasDocument,
    StoredSvgCanvasDraft,
    SvgCanvasDocumentStore,
    SvgDocumentNotFoundError,
)
from ava_backend.canvas.render_profile import CanvasRenderProfile
from ava_backend.canvas.svg_document import (
    SvgDocumentValidator,
    SvgDraftSafetyValidator,
)
from ava_backend.canvas.svg_session import (
    ActiveSvgCanvasScene,
    SvgCanvasCandidate,
    SvgCanvasPresentationCue,
    SvgCanvasSessionState,
    SvgCanvasStateError,
    SvgCanvasStateErrorCode,
)
from ava_backend.canvas.svg_specialist import (
    SvgCanvasDraftSink,
    SvgCanvasSpecialistGenerationLimitError,
    SvgCanvasSpecialistGeneration,
    SvgCanvasSpecialistRequest,
    SvgCanvasSpecialistUnavailableError,
    SvgCanvasVisibleDraft,
    render_svg_canvas_specialist_instructions,
)
from ava_backend.canvas.svg_workspace import (
    SvgCanvasExecutionStatus,
    SvgCanvasWorkspace,
    SvgCanvasWorkspaceStore,
)
from ava_backend.protocol import AgeBand
from ava_backend.realtime.events_v4 import (
    CanvasDocumentDiscardReason,
    CanvasDocumentPreparedEventV4,
    CanvasGenerationRejectionCode,
    CanvasInteractionEventV4,
    CanvasPreparationFailedEventV4,
    CanvasPresentedEventV4,
    CanvasRuntimeFailedEventV4,
)
from ava_backend.realtime.svg_canvas_jobs import SvgCanvasJobCoordinator
from ava_backend.realtime.tool_contract import (
    InteractiveCanvasRequest,
    InteractiveCanvasToolOutputV4,
    InteractiveCanvasToolStatusV4,
    RealtimeCanvasToolCall,
)


def _document():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600"
 data-ava-interaction-mode="select">
<title>Cloud</title><desc>A rain cloud.</desc>
<g id="cloud" data-ava-binding="select-cloud"
 aria-label="Rain cloud"><ellipse cx="500" cy="220" rx="140" ry="60"/></g>
</svg>"""
    return SvgDocumentValidator().validate(svg)


def _draft_document():
    document = _document()
    return SvgDraftSafetyValidator(SvgDocumentValidator()).validate(document.svg_text)


class _Specialist:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.requests: list[SvgCanvasSpecialistRequest] = []
        self.cancelled: list[str] = []
        self.closed = False

    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
        *,
        draft_sink: object | None = None,
    ) -> SvgCanvasSpecialistGeneration:
        del draft_sink
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        return SvgCanvasSpecialistGeneration(
            generation_id=request.generation_id,
            document=_document(),
        )

    async def cancel(self, *, generation_id: str) -> None:
        self.cancelled.append(generation_id)

    async def close(self) -> None:
        self.closed = True


class _CancellationResistantSpecialist(_Specialist):
    """Simulates a provider coroutine that returns after task cancellation."""

    def __init__(self) -> None:
        super().__init__()
        self.task_cancelled = asyncio.Event()

    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
        *,
        draft_sink: object | None = None,
    ) -> SvgCanvasSpecialistGeneration:
        del draft_sink
        self.requests.append(request)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.task_cancelled.set()
            await self.release.wait()
        return SvgCanvasSpecialistGeneration(
            generation_id=request.generation_id,
            document=_document(),
        )


class _ProgressiveSpecialist(_Specialist):
    def __init__(self) -> None:
        super().__init__()
        self.draft_emitted = asyncio.Event()

    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
        *,
        draft_sink: SvgCanvasDraftSink | None = None,
    ) -> SvgCanvasSpecialistGeneration:
        if draft_sink is None:
            raise AssertionError("canvas coordinator must connect the draft sink")
        self.requests.append(request)
        self.started.set()
        await draft_sink(
            SvgCanvasVisibleDraft(
                generation_id=request.generation_id,
                draft_index=1,
                attempt=0,
                document=_draft_document(),
            )
        )
        self.draft_emitted.set()
        await self.release.wait()
        return SvgCanvasSpecialistGeneration(
            generation_id=request.generation_id,
            document=_document(),
        )


class _CancelFailingSpecialist(_Specialist):
    async def cancel(self, *, generation_id: str) -> None:
        self.cancelled.append(generation_id)
        raise RuntimeError("specialist cancel transport failed")


class _UnavailableSpecialist(_Specialist):
    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
        *,
        draft_sink: object | None = None,
    ) -> SvgCanvasSpecialistGeneration:
        del draft_sink
        self.requests.append(request)
        self.started.set()
        raise SvgCanvasSpecialistUnavailableError(
            "SVG canvas specialist exceeded its generation deadline"
        )


class _GenerationLimitSpecialist(_Specialist):
    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
        *,
        draft_sink: object | None = None,
    ) -> SvgCanvasSpecialistGeneration:
        del draft_sink
        self.requests.append(request)
        self.started.set()
        raise SvgCanvasSpecialistGenerationLimitError(
            "SVG canvas specialist exceeded its output generation limit"
        )


class _Sink:
    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []
        self.available: list[StoredSvgCanvasDocument] = []
        self.drafts: list[StoredSvgCanvasDraft] = []
        self.cues: list[tuple[SvgCanvasCandidate, SvgCanvasPresentationCue]] = []
        self.activations: list[ActiveSvgCanvasScene] = []
        self.discarded: list[
            tuple[SvgCanvasCandidate, CanvasDocumentDiscardReason]
        ] = []
        self.active_discarded: list[
            tuple[ActiveSvgCanvasScene, CanvasDocumentDiscardReason]
        ] = []
        self.rejected: list[tuple[str, CanvasGenerationRejectionCode]] = []
        self.rejection_details: list[
            tuple[str, CanvasGenerationRejectionCode, str, bool]
        ] = []
        self.rejected_calls: list[tuple[str, str]] = []
        self.interaction_rejections: list[SvgCanvasStateError] = []
        self.activation_failure: Exception | None = None
        self.rejection_arrived = asyncio.Event()

    async def generation_started(
        self,
        *,
        generation_id: str,
        source_turn_id: str,
    ) -> None:
        self.started.append((generation_id, source_turn_id))

    async def document_available(self, stored: StoredSvgCanvasDocument) -> None:
        self.available.append(stored)

    async def draft_available(self, stored: StoredSvgCanvasDraft) -> None:
        self.drafts.append(stored)

    async def presentation_cue(
        self,
        candidate: SvgCanvasCandidate,
        cue: SvgCanvasPresentationCue,
    ) -> None:
        self.cues.append((candidate, cue))

    async def activated(self, active: ActiveSvgCanvasScene) -> None:
        if self.activation_failure is not None:
            raise self.activation_failure
        self.activations.append(active)

    async def document_discarded(
        self,
        candidate: SvgCanvasCandidate,
        reason: CanvasDocumentDiscardReason,
    ) -> None:
        self.discarded.append((candidate, reason))

    async def active_document_discarded(
        self,
        active: ActiveSvgCanvasScene,
        reason: CanvasDocumentDiscardReason,
    ) -> None:
        self.active_discarded.append((active, reason))

    async def generation_rejected(
        self,
        *,
        generation_id: str,
        source_turn_id: str,
        code: CanvasGenerationRejectionCode,
        message: str,
        retryable: bool,
    ) -> None:
        del source_turn_id
        self.rejected.append((generation_id, code))
        self.rejection_details.append((generation_id, code, message, retryable))
        self.rejection_arrived.set()

    async def tool_call_rejected(
        self,
        *,
        call_id: str,
        source_turn_id: str,
        message: str,
    ) -> None:
        del source_turn_id
        self.rejected_calls.append((call_id, message))

    async def interaction_rejected(
        self,
        event: CanvasInteractionEventV4,
        error: SvgCanvasStateError,
    ) -> None:
        del event
        self.interaction_rejections.append(error)


class _DiscardFailingSink(_Sink):
    async def document_discarded(
        self,
        candidate: SvgCanvasCandidate,
        reason: CanvasDocumentDiscardReason,
    ) -> None:
        del candidate, reason
        raise RuntimeError("canvas discard delivery failed")


def _id_factory(values: list[str]):
    iterator: Iterator[str] = iter(values)
    return lambda: next(iterator)


def _call(
    call_id: str = "call_1",
    *,
    operation: str = "new",
    brief: str = "Show how clouds make rain.",
) -> RealtimeCanvasToolCall:
    return RealtimeCanvasToolCall(
        call_id=call_id,
        request=InteractiveCanvasRequest.model_validate(
            {
                "operation": operation,
                "brief": brief,
                "interactionMode": "select",
            }
        ),
    )


def _render_profile(*, aspect_ratio: float) -> CanvasRenderProfile:
    orientation = "portrait" if aspect_ratio < 0.95 else "landscape"
    return CanvasRenderProfile.model_validate(
        {
            "usableAspectRatio": aspect_ratio,
            "orientation": orientation,
            "widthClass": "compact" if orientation == "portrait" else "regular",
            "heightClass": "regular" if orientation == "portrait" else "compact",
            "safeInsets": {"top": 0.0, "right": 0.0, "bottom": 0.04, "left": 0.0},
            "touchEnvironment": "direct",
            "minimumInteractionTargetPoints": 44.0,
            "textScaleFactor": 1.0,
        }
    )


def _commit_scene(
    workspace: SvgCanvasWorkspace,
    *,
    scene_id: str,
    revision: int,
    generation_id: str,
    source_turn_id: str,
) -> ActiveSvgCanvasScene:
    scene = ActiveSvgCanvasScene(
        generation_id=generation_id,
        source_turn_id=source_turn_id,
        scene_id=scene_id,
        revision=revision,
        document=_document(),
        first_rendered_sample=4_800 * revision,
    )
    return workspace.commit(
        scene,
        base_scene_id=scene_id if revision > 1 else None,
        base_revision=revision - 1,
        workspace_epoch=workspace.reset_epoch,
    )


async def _accepted_coordinator(
    generation_ids: list[str] | None = None,
    *,
    state: SvgCanvasSessionState | None = None,
    session_id: str = "session-1",
    operation: str = "new",
):
    specialist = _Specialist()
    sink = _Sink()
    documents = SvgCanvasDocumentStore()
    completions: list[tuple[str, str, InteractiveCanvasToolOutputV4]] = []

    async def complete(
        turn_id: str,
        call_id: str,
        output: InteractiveCanvasToolOutputV4,
    ) -> None:
        completions.append((turn_id, call_id, output))

    coordinator = SvgCanvasJobCoordinator(
        session_id=session_id,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=state or SvgCanvasSessionState(),
        documents=documents,
        event_sink=sink,
        generation_id_factory=_id_factory(
            generation_ids or ["generation-1", "generation-2"]
        ),
    )
    output = await coordinator.accept_tool_call(
        source_turn_id="turn-1",
        call=_call(operation=operation),
        complete_tool=complete,
    )
    await specialist.started.wait()
    return coordinator, specialist, sink, completions, output, documents


async def test_render_profile_update_is_ordered_and_snapshotted_per_generation() -> (
    None
):
    specialist = _Specialist()
    sink = _Sink()
    initial_profile = _render_profile(aspect_ratio=0.68)
    newest_profile = _render_profile(aspect_ratio=1.6)

    async def complete(
        turn_id: str,
        call_id: str,
        output: InteractiveCanvasToolOutputV4,
    ) -> None:
        del turn_id, call_id, output

    coordinator = SvgCanvasJobCoordinator(
        session_id="session-render-profile",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        render_profile=initial_profile,
        specialist=specialist,
        state=SvgCanvasSessionState(),
        documents=SvgCanvasDocumentStore(),
        event_sink=sink,
        generation_id_factory=_id_factory(["generation-old", "generation-new"]),
    )
    try:
        await coordinator.accept_tool_call(
            source_turn_id="turn-old",
            call=_call("call-old"),
            complete_tool=complete,
        )
        await specialist.started.wait()
        assert specialist.requests[0].render_profile == initial_profile

        assert await coordinator.update_render_profile(
            profile_revision=2,
            render_profile=newest_profile,
        )
        assert not await coordinator.update_render_profile(
            profile_revision=1,
            render_profile=initial_profile,
        )
        assert coordinator.render_profile_revision == 2
        assert specialist.cancelled == []
        assert specialist.requests[0].render_profile == initial_profile

        await coordinator.accept_tool_call(
            source_turn_id="turn-new",
            call=_call("call-new"),
            complete_tool=complete,
        )
        for _ in range(10):
            if len(specialist.requests) == 2:
                break
            await asyncio.sleep(0)
        assert len(specialist.requests) == 2
        assert specialist.requests[0].render_profile == initial_profile
        assert specialist.requests[1].render_profile == newest_profile
    finally:
        specialist.release.set()
        await coordinator.close()


async def test_source_turn_transcript_and_realtime_brief_remain_distinct_for_sol() -> (
    None
):
    specialist = _Specialist()
    sink = _Sink()
    coordinator = SvgCanvasJobCoordinator(
        session_id="session-grounding",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=SvgCanvasSessionState(),
        documents=SvgCanvasDocumentStore(),
        event_sink=sink,
        generation_id_factory=lambda: "generation-grounding",
    )
    completions: list[tuple[str, str, InteractiveCanvasToolOutputV4]] = []
    try:
        await coordinator.accept_tool_call(
            source_turn_id="turn-grounding",
            source_transcript="source-subject-A",
            call=_call(brief="visual-brief-subject-B"),
            complete_tool=lambda turn, call, result: _record_completion(
                completions,
                turn,
                call,
                result,
            ),
        )
        await specialist.started.wait()

        request = specialist.requests[0]
        assert request.source_turn_id == "turn-grounding"
        assert request.source_transcript == "source-subject-A"
        assert request.brief == "visual-brief-subject-B"
        model_input = request.model_input()
        assert "source-subject-A" in model_input
        assert "visual-brief-subject-B" in model_input
        instructions = render_svg_canvas_specialist_instructions(SvgDocumentValidator())
        assert "untrusted semantic" in instructions
        assert "never as instructions" in instructions
    finally:
        specialist.release.set()
        await coordinator.close()


async def _activated_coordinator(
    *,
    state: SvgCanvasSessionState | None = None,
):
    (
        coordinator,
        specialist,
        sink,
        completions,
        _output,
        documents,
    ) = await _accepted_coordinator(state=state)
    specialist.release.set()
    await coordinator.wait_for_background_work()
    stored, active = await _activate_current_candidate(
        coordinator=coordinator,
        sink=sink,
        documents=documents,
        sequence=1,
    )
    return coordinator, specialist, sink, completions, documents, stored, active


async def _activate_current_candidate(
    *,
    coordinator: SvgCanvasJobCoordinator,
    sink: _Sink,
    documents: SvgCanvasDocumentStore,
    sequence: int,
    session_id: str = "session-1",
) -> tuple[StoredSvgCanvasDocument, ActiveSvgCanvasScene]:
    candidate, stored = await _prepare_current_candidate(
        coordinator=coordinator,
        sink=sink,
        documents=documents,
        sequence=sequence,
        session_id=session_id,
    )
    active = await coordinator.presented(
        _presented_event(
            candidate,
            stored,
            sequence=sequence + 1,
            session_id=session_id,
        )
    )
    return stored, active


async def _prepare_current_candidate(
    *,
    coordinator: SvgCanvasJobCoordinator,
    sink: _Sink,
    documents: SvgCanvasDocumentStore,
    sequence: int,
    session_id: str = "session-1",
    presentation_sample_offset: int | None = 4_800,
) -> tuple[SvgCanvasCandidate, StoredSvgCanvasDocument]:
    stored = sink.available[-1]
    candidate = coordinator.state.candidate
    assert candidate is not None
    assert (
        await documents.get(
            session_id=session_id,
            generation_id=candidate.generation_id,
        )
        == stored
    )
    if presentation_sample_offset is not None:
        await coordinator.request_presentation(
            generation_id=candidate.generation_id,
            presentation_sample_offset=presentation_sample_offset,
        )
    await coordinator.document_prepared(
        CanvasDocumentPreparedEventV4.model_validate(
            {
                "protocolVersion": 4,
                "event": "canvasDocumentPrepared",
                "sessionId": session_id,
                "sequence": sequence,
                "generationId": candidate.generation_id,
                "sceneId": candidate.scene_id,
                "sceneRevision": candidate.revision,
                "svgSha256": stored.document.svg_sha256,
                "manifestSha256": stored.document.manifest_sha256,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    return candidate, stored


def _presented_event(
    candidate: SvgCanvasCandidate,
    stored: StoredSvgCanvasDocument,
    *,
    sequence: int,
    session_id: str = "session-1",
) -> CanvasPresentedEventV4:
    return CanvasPresentedEventV4.model_validate(
        {
            "protocolVersion": 4,
            "event": "canvasPresented",
            "sessionId": session_id,
            "sequence": sequence,
            "generationId": candidate.generation_id,
            "sceneId": candidate.scene_id,
            "sceneRevision": candidate.revision,
            "svgSha256": stored.document.svg_sha256,
            "manifestSha256": stored.document.manifest_sha256,
            "firstRenderedSample": 4_800,
        }
    )


def _runtime_failure_event(
    active: ActiveSvgCanvasScene,
) -> CanvasRuntimeFailedEventV4:
    return CanvasRuntimeFailedEventV4.model_validate(
        {
            "protocolVersion": 4,
            "event": "canvasRuntimeFailed",
            "sessionId": "session-1",
            "sequence": 3,
            "generationId": active.generation_id,
            "sceneId": active.scene_id,
            "sceneRevision": active.revision,
            "svgSha256": active.document.svg_sha256,
            "manifestSha256": active.document.manifest_sha256,
            "failureCode": "rendererTerminated",
            "message": "The renderer process terminated.",
        }
    )


async def _commit_shared_workspace_scene(
    workspaces: SvgCanvasWorkspaceStore,
    *,
    subject: str = "child:X",
) -> ActiveSvgCanvasScene:
    state = SvgCanvasSessionState(workspace=workspaces.workspace_for(subject))
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        documents,
    ) = await _accepted_coordinator(
        ["generation-session-1"],
        state=state,
        session_id="session-canvas-1",
    )
    specialist.release.set()
    await coordinator.wait_for_background_work()
    _stored, active = await _activate_current_candidate(
        coordinator=coordinator,
        sink=sink,
        documents=documents,
        sequence=1,
        session_id="session-canvas-1",
    )
    assert documents.document_count == 0
    execution = state.workspace.execution(active.generation_id)
    assert execution is not None
    assert execution.status == SvgCanvasExecutionStatus.ACTIVE
    await coordinator.close()
    return active


async def test_new_operation_ignores_existing_current_scene() -> None:
    workspace = SvgCanvasWorkspace()
    active_a = _commit_scene(
        workspace,
        scene_id="scene-A",
        revision=1,
        generation_id="generation-A-1",
        source_turn_id="turn-A-1",
    )
    state = SvgCanvasSessionState(workspace=workspace)
    (
        coordinator,
        specialist,
        _sink,
        _completions,
        _output,
        _documents,
    ) = await _accepted_coordinator(
        ["generation-B-1"],
        state=state,
        operation="new",
    )
    try:
        request = specialist.requests[-1]
        assert request.current_scene_id is None
        assert request.current_revision is None
        assert request.current_svg is None
        assert request.current_svg_sha256 is None

        specialist.release.set()
        await coordinator.wait_for_background_work()

        candidate = state.candidate
        assert candidate is not None
        assert candidate.scene_id != active_a.scene_id
        assert candidate.revision == 1
        assert candidate.base_scene_id is None
        assert candidate.base_revision == 0
    finally:
        await coordinator.close()


async def test_revise_focused_without_current_is_rejected_before_sol() -> None:
    specialist = _Specialist()
    sink = _Sink()
    documents = SvgCanvasDocumentStore()
    completions: list[tuple[str, str, InteractiveCanvasToolOutputV4]] = []
    coordinator = SvgCanvasJobCoordinator(
        session_id="session-empty-revision",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=SvgCanvasSessionState(),
        documents=documents,
        event_sink=sink,
        generation_id_factory=lambda: "generation-must-not-be-reserved",
    )
    try:
        output = await coordinator.accept_tool_call(
            source_turn_id="turn-empty-revision",
            call=_call(operation="reviseFocused"),
            complete_tool=lambda turn, call, result: _record_completion(
                completions,
                turn,
                call,
                result,
            ),
        )

        assert output.status == InteractiveCanvasToolStatusV4.REJECTED
        assert output.generation_id is None
        assert output.reason == "visualUnavailable"
        assert completions == [("turn-empty-revision", "call_1", output)]
        assert specialist.requests == []
        assert sink.started == []
        assert sink.available == []
        assert sink.rejected_calls == [
            ("call_1", "There is no current canvas to revise.")
        ]
        assert coordinator.state.candidate is None
        assert coordinator.state.workspace.scenes == ()
        assert documents.document_count == 0
    finally:
        await coordinator.close()


async def test_consecutive_new_operations_create_distinct_scenes() -> None:
    generation_ids = ["generation-A", "generation-B", "generation-C"]
    (
        coordinator,
        specialist,
        sink,
        completions,
        _output,
        documents,
    ) = await _accepted_coordinator(generation_ids, operation="new")
    specialist.release.set()
    await coordinator.wait_for_background_work()

    actives: list[ActiveSvgCanvasScene] = []
    for index in range(3):
        if index > 0:
            await coordinator.accept_tool_call(
                source_turn_id=f"turn-{index + 1}",
                call=_call(f"call_{index + 1}", operation="new"),
                complete_tool=lambda turn, call, result: _record_completion(
                    completions,
                    turn,
                    call,
                    result,
                ),
            )
            await coordinator.wait_for_background_work()
        _stored, active = await _activate_current_candidate(
            coordinator=coordinator,
            sink=sink,
            documents=documents,
            sequence=index * 2 + 1,
        )
        actives.append(active)

    assert len({active.scene_id for active in actives}) == 3
    assert [active.revision for active in actives] == [1, 1, 1]
    assert [scene.scene_id for scene in coordinator.state.workspace.scenes] == [
        active.scene_id for active in actives
    ]
    assert coordinator.state.workspace.current is actives[-1]
    assert all(request.current_svg is None for request in specialist.requests)
    assert documents.document_count == 0
    await coordinator.close()


async def test_new_new_revise_new_semantics() -> None:
    generation_ids = [
        "generation-plant",
        "generation-solar-1",
        "generation-solar-2",
        "generation-water",
    ]
    (
        coordinator,
        specialist,
        sink,
        completions,
        _output,
        documents,
    ) = await _accepted_coordinator(generation_ids, operation="new")
    specialist.release.set()
    await coordinator.wait_for_background_work()

    _stored_plant, plant = await _activate_current_candidate(
        coordinator=coordinator,
        sink=sink,
        documents=documents,
        sequence=1,
    )
    await coordinator.accept_tool_call(
        source_turn_id="turn-solar-1",
        call=_call(
            "call_solar_1",
            operation="new",
            brief="Show the solar system.",
        ),
        complete_tool=lambda turn, call, result: _record_completion(
            completions, turn, call, result
        ),
    )
    await coordinator.wait_for_background_work()
    _stored_solar, solar_1 = await _activate_current_candidate(
        coordinator=coordinator,
        sink=sink,
        documents=documents,
        sequence=3,
    )
    await coordinator.accept_tool_call(
        source_turn_id="turn-solar-2",
        call=_call(
            "call_solar_2",
            operation="reviseFocused",
            brief="Make Earth bigger.",
        ),
        complete_tool=lambda turn, call, result: _record_completion(
            completions, turn, call, result
        ),
    )
    await coordinator.wait_for_background_work()
    _stored_solar_2, solar_2 = await _activate_current_candidate(
        coordinator=coordinator,
        sink=sink,
        documents=documents,
        sequence=5,
    )
    await coordinator.accept_tool_call(
        source_turn_id="turn-water",
        call=_call(
            "call_water",
            operation="new",
            brief="Show the water cycle.",
        ),
        complete_tool=lambda turn, call, result: _record_completion(
            completions, turn, call, result
        ),
    )
    await coordinator.wait_for_background_work()
    _stored_water, water = await _activate_current_candidate(
        coordinator=coordinator,
        sink=sink,
        documents=documents,
        sequence=7,
    )

    assert plant.scene_id != solar_1.scene_id != water.scene_id
    assert solar_2.scene_id == solar_1.scene_id
    assert solar_2.revision == 2
    assert [
        (scene.scene_id, scene.revision) for scene in coordinator.state.workspace.scenes
    ] == [
        (plant.scene_id, 1),
        (solar_1.scene_id, 2),
        (water.scene_id, 1),
    ]
    assert coordinator.state.workspace.current is water
    assert specialist.requests[0].current_svg is None
    assert specialist.requests[1].current_svg is None
    assert specialist.requests[2].current_scene_id == solar_1.scene_id
    assert specialist.requests[2].current_revision == 1
    assert specialist.requests[2].current_svg == solar_1.document.svg_text
    assert specialist.requests[2].current_svg_sha256 == solar_1.document.svg_sha256
    assert specialist.requests[3].current_svg is None
    scene_identities = tuple(coordinator.state.workspace.scenes)
    assert coordinator.state.workspace.focus(plant.scene_id) is plant
    assert coordinator.state.workspace.focused_scene_id == plant.scene_id
    assert coordinator.state.workspace.scenes == scene_identities
    assert documents.document_count == 0
    await coordinator.close()


async def test_eleven_successful_new_operations_preserve_fifo_cap() -> None:
    generation_ids = [f"generation-new-{index}" for index in range(1, 12)]
    (
        coordinator,
        specialist,
        sink,
        completions,
        _output,
        documents,
    ) = await _accepted_coordinator(generation_ids, operation="new")
    specialist.release.set()
    await coordinator.wait_for_background_work()

    activated: list[ActiveSvgCanvasScene] = []
    for index in range(1, 12):
        if index > 1:
            await coordinator.accept_tool_call(
                source_turn_id=f"turn-new-{index}",
                call=_call(f"call_new_{index}", operation="new"),
                complete_tool=lambda turn, call, result: _record_completion(
                    completions,
                    turn,
                    call,
                    result,
                ),
            )
            await coordinator.wait_for_background_work()
        _stored, active = await _activate_current_candidate(
            coordinator=coordinator,
            sink=sink,
            documents=documents,
            sequence=index * 2,
        )
        activated.append(active)
        assert coordinator.state.workspace.scene_count <= 10
        assert documents.document_count == 0

    assert coordinator.state.workspace.scene_count == 10
    assert [scene.scene_id for scene in coordinator.state.workspace.scenes] == [
        scene.scene_id for scene in activated[1:]
    ]
    assert all(scene.revision == 1 for scene in coordinator.state.workspace.scenes)
    assert coordinator.state.workspace.current is activated[-1]
    assert coordinator.state.workspace.scene(activated[0].scene_id) is None
    assert all(request.current_svg is None for request in specialist.requests)
    await coordinator.close()


async def test_realtime_tool_completes_before_sol_generation() -> None:
    (
        coordinator,
        specialist,
        sink,
        completions,
        output,
        _documents,
    ) = await _accepted_coordinator()

    assert output.status == InteractiveCanvasToolStatusV4.ACCEPTED
    assert output.generation_id == "generation-1"
    assert completions == [("turn-1", "call_1", output)]
    assert sink.started == [("generation-1", "turn-1")]
    assert sink.available == []
    assert coordinator.state.candidate is None

    specialist.release.set()
    await coordinator.wait_for_background_work()
    assert len(sink.available) == 1
    assert coordinator.state.candidate is not None


async def test_generation_deadline_rejects_only_optional_canvas_work() -> None:
    specialist = _UnavailableSpecialist()
    sink = _Sink()
    completions: list[tuple[str, str, InteractiveCanvasToolOutputV4]] = []
    coordinator = SvgCanvasJobCoordinator(
        session_id="session-timeout",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=SvgCanvasSessionState(),
        documents=SvgCanvasDocumentStore(),
        event_sink=sink,
        generation_id_factory=lambda: "generation-timeout",
    )

    output = await coordinator.accept_tool_call(
        source_turn_id="turn-speech-continues",
        call=_call(),
        complete_tool=lambda turn, call, result: _record_completion(
            completions,
            turn,
            call,
            result,
        ),
    )
    await coordinator.wait_for_background_work()

    assert output.status == InteractiveCanvasToolStatusV4.ACCEPTED
    assert completions == [("turn-speech-continues", "call_1", output)]
    assert sink.available == []
    assert sink.rejected == [
        ("generation-timeout", CanvasGenerationRejectionCode.PROVIDER_UNAVAILABLE)
    ]
    assert coordinator.state.candidate is None
    await coordinator.close()


async def test_progressive_draft_is_visible_without_candidate_authority() -> None:
    specialist = _ProgressiveSpecialist()
    sink = _Sink()
    state = SvgCanvasSessionState()
    documents = SvgCanvasDocumentStore()
    completions: list[tuple[str, str, InteractiveCanvasToolOutputV4]] = []
    coordinator = SvgCanvasJobCoordinator(
        session_id="session-progressive",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=state,
        documents=documents,
        event_sink=sink,
        generation_id_factory=lambda: "generation-progressive",
    )

    await coordinator.accept_tool_call(
        source_turn_id="turn-progressive",
        call=_call(),
        complete_tool=lambda turn, call, output: _record_completion(
            completions,
            turn,
            call,
            output,
        ),
    )
    await specialist.draft_emitted.wait()

    execution = state.workspace.execution("generation-progressive")
    assert execution is not None
    assert execution.status == SvgCanvasExecutionStatus.DRAFT_VISIBLE
    assert state.candidate is None
    assert len(sink.drafts) == 1
    stored_draft = sink.drafts[0]
    assert stored_draft.draft.document.authoritative is False
    assert stored_draft.draft.document.interactive is False
    assert not hasattr(stored_draft.draft.document, "bindings")
    assert not hasattr(stored_draft.draft.document, "manifest")
    assert "/canvas-drafts/" in stored_draft.svg_path
    assert (
        await documents.get_draft(
            session_id="session-progressive",
            generation_id="generation-progressive",
            draft_index=1,
        )
        == stored_draft
    )

    specialist.release.set()
    await coordinator.wait_for_background_work()
    execution = state.workspace.execution("generation-progressive")
    assert execution is not None
    assert execution.status == SvgCanvasExecutionStatus.PREPARING
    assert state.candidate is not None
    assert sink.available
    await coordinator.close()


async def test_unpresented_candidate_times_out_to_failed_and_preserves_active() -> None:
    workspace = SvgCanvasWorkspace()
    active = _commit_scene(
        workspace,
        scene_id="scene-existing",
        revision=1,
        generation_id="generation-existing",
        source_turn_id="turn-existing",
    )
    specialist = _Specialist()
    sink = _Sink()
    state = SvgCanvasSessionState(workspace=workspace)
    documents = SvgCanvasDocumentStore()
    completions: list[tuple[str, str, InteractiveCanvasToolOutputV4]] = []
    coordinator = SvgCanvasJobCoordinator(
        session_id="session-timeout",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=state,
        documents=documents,
        event_sink=sink,
        generation_id_factory=lambda: "generation-timeout",
        presentation_timeout_seconds=0.02,
    )
    await coordinator.accept_tool_call(
        source_turn_id="turn-timeout",
        call=_call(),
        complete_tool=lambda turn, call, output: _record_completion(
            completions,
            turn,
            call,
            output,
        ),
    )
    specialist.release.set()
    await coordinator.wait_for_background_work()
    await asyncio.wait_for(sink.rejection_arrived.wait(), timeout=0.25)

    execution = workspace.execution("generation-timeout")
    assert execution is not None
    assert execution.status == SvgCanvasExecutionStatus.FAILED
    assert execution.terminal_reason == "presentationTimeout"
    assert state.candidate is None
    assert state.active is active
    assert sink.discarded[0][1] == CanvasDocumentDiscardReason.PRESENTATION_TIMEOUT
    assert sink.rejected == [
        (
            "generation-timeout",
            CanvasGenerationRejectionCode.PRESENTATION_FAILED,
        )
    ]
    with pytest.raises(SvgDocumentNotFoundError):
        await documents.get(
            session_id="session-timeout",
            generation_id="generation-timeout",
        )
    await coordinator.close()


async def test_generation_limit_has_specific_retryable_rejection() -> None:
    specialist = _GenerationLimitSpecialist()
    sink = _Sink()
    coordinator = SvgCanvasJobCoordinator(
        session_id="session-generation-limit",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=SvgCanvasSessionState(),
        documents=SvgCanvasDocumentStore(),
        event_sink=sink,
        generation_id_factory=lambda: "generation-limit",
    )

    output = await coordinator.accept_tool_call(
        source_turn_id="turn-speech-continues",
        call=_call(),
        complete_tool=lambda turn, call, result: _record_completion(
            [],
            turn,
            call,
            result,
        ),
    )
    await coordinator.wait_for_background_work()

    assert output.status == InteractiveCanvasToolStatusV4.ACCEPTED
    assert sink.available == []
    assert sink.rejected == [
        (
            "generation-limit",
            CanvasGenerationRejectionCode.GENERATION_LIMIT_EXCEEDED,
        )
    ]
    assert sink.rejection_details == [
        (
            "generation-limit",
            CanvasGenerationRejectionCode.GENERATION_LIMIT_EXCEEDED,
            "The canvas illustrator exceeded its generation limit.",
            True,
        )
    ]
    assert sink.rejected[0][1] is not CanvasGenerationRejectionCode.PROVIDER_UNAVAILABLE
    assert coordinator.state.candidate is None
    await coordinator.close()


async def test_presentation_anchor_can_arrive_before_sol_document() -> None:
    (
        coordinator,
        specialist,
        sink,
        _completions,
        output,
        _documents,
    ) = await _accepted_coordinator()
    assert output.generation_id is not None

    assert (
        await coordinator.request_presentation(
            generation_id=output.generation_id,
            presentation_sample_offset=7_200,
        )
        is None
    )
    specialist.release.set()
    await coordinator.wait_for_background_work()
    stored = sink.available[0]
    candidate = coordinator.state.candidate
    assert candidate is not None
    await coordinator.document_prepared(
        CanvasDocumentPreparedEventV4.model_validate(
            {
                "protocolVersion": 4,
                "event": "canvasDocumentPrepared",
                "sessionId": "session-1",
                "sequence": 1,
                "generationId": candidate.generation_id,
                "sceneId": candidate.scene_id,
                "sceneRevision": candidate.revision,
                "svgSha256": stored.document.svg_sha256,
                "manifestSha256": stored.document.manifest_sha256,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )

    assert sink.cues[0][1].presentation_sample_offset == 7_200
    await coordinator.close()


async def test_new_canvas_request_supersedes_pending_workspace_generation() -> None:
    (
        coordinator,
        specialist,
        sink,
        completions,
        _output,
        _documents,
    ) = await _accepted_coordinator()

    replacement = await coordinator.accept_tool_call(
        source_turn_id="turn-2",
        call=_call("call_2", brief="Show Japan's major islands."),
        complete_tool=lambda turn, call, output: _record_completion(
            completions, turn, call, output
        ),
    )

    assert replacement.status == InteractiveCanvasToolStatusV4.ACCEPTED
    assert replacement.generation_id == "generation-2"
    assert len(completions) == 2
    assert specialist.cancelled == ["generation-1"]
    assert sink.rejected == [("generation-1", CanvasGenerationRejectionCode.CANCELLED)]
    superseded = coordinator.state.workspace.execution("generation-1")
    assert superseded is not None
    assert superseded.status == SvgCanvasExecutionStatus.SUPERSEDED
    assert "Japan&#x27;s major islands" in coordinator.render_realtime_context()
    assert "Show how clouds make rain" not in coordinator.render_realtime_context()
    await coordinator.close()


async def test_revision_request_supersedes_focused_pending_generation() -> None:
    (
        coordinator,
        specialist,
        sink,
        completions,
        _output,
        _documents,
    ) = await _accepted_coordinator()

    revision = await coordinator.accept_tool_call(
        source_turn_id="turn-2",
        call=_call(
            "call_2",
            operation="reviseFocused",
            brief="Add the major rivers to the India visual.",
        ),
        complete_tool=lambda turn, call, output: _record_completion(
            completions, turn, call, output
        ),
    )

    assert revision.status == InteractiveCanvasToolStatusV4.ACCEPTED
    assert revision.generation_id == "generation-2"
    assert specialist.cancelled == ["generation-1"]
    assert sink.rejected == [("generation-1", CanvasGenerationRejectionCode.CANCELLED)]
    context = coordinator.render_realtime_context()
    assert 'operation="reviseFocused"' in context
    assert "Add the major rivers" in context
    await coordinator.close()


async def _record_completion(
    values: list[tuple[str, str, InteractiveCanvasToolOutputV4]],
    turn_id: str,
    call_id: str,
    output: InteractiveCanvasToolOutputV4,
) -> None:
    values.append((turn_id, call_id, output))


async def test_prepare_ack_and_sample_cue_still_do_not_activate() -> None:
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        documents,
    ) = await _accepted_coordinator()
    specialist.release.set()
    await coordinator.wait_for_background_work()
    stored = sink.available[0]
    candidate = coordinator.state.candidate
    assert candidate is not None

    assert (
        await coordinator.request_presentation(
            generation_id=candidate.generation_id,
            presentation_sample_offset=4_800,
        )
        is None
    )
    prepared = CanvasDocumentPreparedEventV4.model_validate(
        {
            "protocolVersion": 4,
            "event": "canvasDocumentPrepared",
            "sessionId": "session-1",
            "sequence": 1,
            "generationId": candidate.generation_id,
            "sceneId": candidate.scene_id,
            "sceneRevision": candidate.revision,
            "svgSha256": stored.document.svg_sha256,
            "manifestSha256": stored.document.manifest_sha256,
            "rendererCapabilityVersion": "ava-svg-renderer-v2",
            "validatorVersion": "ava-svg-validator-v2",
        }
    )
    await coordinator.document_prepared(prepared)

    assert len(sink.cues) == 1
    assert coordinator.state.active is None
    await coordinator.close()


async def test_first_frame_ack_activates_and_interactions_use_manifest() -> None:
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        documents,
    ) = await _accepted_coordinator()
    specialist.release.set()
    await coordinator.wait_for_background_work()
    stored = sink.available[0]
    candidate = coordinator.state.candidate
    assert candidate is not None
    await coordinator.request_presentation(
        generation_id=candidate.generation_id,
        presentation_sample_offset=4_800,
    )
    await coordinator.document_prepared(
        CanvasDocumentPreparedEventV4.model_validate(
            {
                "protocolVersion": 4,
                "event": "canvasDocumentPrepared",
                "sessionId": "session-1",
                "sequence": 1,
                "generationId": candidate.generation_id,
                "sceneId": candidate.scene_id,
                "sceneRevision": candidate.revision,
                "svgSha256": stored.document.svg_sha256,
                "manifestSha256": stored.document.manifest_sha256,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    active = await coordinator.presented(
        CanvasPresentedEventV4.model_validate(
            {
                "protocolVersion": 4,
                "event": "canvasPresented",
                "sessionId": "session-1",
                "sequence": 2,
                "generationId": candidate.generation_id,
                "sceneId": candidate.scene_id,
                "sceneRevision": candidate.revision,
                "svgSha256": stored.document.svg_sha256,
                "manifestSha256": stored.document.manifest_sha256,
                "firstRenderedSample": 4_812,
            }
        )
    )

    recorded = await coordinator.interaction(
        CanvasInteractionEventV4.model_validate(
            {
                "protocolVersion": 4,
                "event": "canvasInteraction",
                "sessionId": "session-1",
                "sequence": 3,
                "sceneId": active.scene_id,
                "sceneRevision": active.revision,
                "interactionRevision": 1,
                "svgSha256": stored.document.svg_sha256,
                "manifestSha256": stored.document.manifest_sha256,
                "bindingId": "select-cloud",
            }
        )
    )

    assert sink.activations == [active]
    assert recorded is not None
    assert recorded.accessibility_label == "Rain cloud"


async def test_active_renderer_failure_preserves_backend_committed_scene() -> None:
    workspaces = SvgCanvasWorkspaceStore()
    state = SvgCanvasSessionState(workspace=workspaces.workspace_for("child:X"))
    (
        coordinator,
        _specialist,
        sink,
        _completions,
        documents,
        stored,
        active,
    ) = await _activated_coordinator(state=state)

    reported = await coordinator.runtime_failed(_runtime_failure_event(active))

    assert reported is active
    assert coordinator.state.active is active
    with pytest.raises(SvgDocumentNotFoundError):
        await documents.get(
            session_id="session-1",
            generation_id=active.generation_id,
        )
    assert coordinator.state.active.document is stored.document
    assert sink.active_discarded == []
    assert sink.discarded == []
    replacement = SvgCanvasSessionState(workspace=workspaces.workspace_for("child:X"))
    assert replacement.active is active
    assert replacement.render_realtime_context() == state.render_realtime_context()
    await coordinator.close()


async def test_conversation_reset_is_idempotent_without_canvas_work() -> None:
    workspace = SvgCanvasWorkspace()
    documents = SvgCanvasDocumentStore()
    coordinator = SvgCanvasJobCoordinator(
        session_id="session-reset-empty",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=_Specialist(),
        state=SvgCanvasSessionState(workspace=workspace),
        documents=documents,
        event_sink=_Sink(),
        generation_id_factory=lambda: "generation-reset-empty",
    )
    initial_epoch = workspace.reset_epoch

    await coordinator.reset_conversation()
    await coordinator.reset_conversation()

    assert workspace.current is None
    assert workspace.scenes == ()
    assert workspace.reset_epoch == initial_epoch + 2
    assert documents.document_count == 0
    assert documents.draft_count == 0
    await coordinator.close()


async def test_conversation_reset_clears_visible_canvas_workspace() -> None:
    workspace = SvgCanvasWorkspace()
    state = SvgCanvasSessionState(workspace=workspace)
    (
        coordinator,
        _specialist,
        _sink,
        _completions,
        documents,
        _stored,
        _active,
    ) = await _activated_coordinator(state=state)
    initial_epoch = workspace.reset_epoch

    await coordinator.reset_conversation()

    assert state.active is None
    assert workspace.scenes == ()
    assert workspace.reset_epoch == initial_epoch + 1
    assert documents.document_count == 0
    assert documents.draft_count == 0
    await coordinator.close()


async def test_conversation_reset_cancels_pending_sol_without_stale_completion() -> (
    None
):
    specialist = _CancellationResistantSpecialist()
    workspace = SvgCanvasWorkspace()
    state = SvgCanvasSessionState(workspace=workspace)
    documents = SvgCanvasDocumentStore()
    coordinator = SvgCanvasJobCoordinator(
        session_id="session-reset-pending",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=state,
        documents=documents,
        event_sink=_Sink(),
        generation_id_factory=lambda: "generation-reset-pending",
    )
    await coordinator.accept_tool_call(
        source_turn_id="turn-reset-pending",
        call=_call(),
        complete_tool=lambda turn, call, result: _record_completion(
            [], turn, call, result
        ),
    )
    await specialist.started.wait()

    reset = asyncio.create_task(coordinator.reset_conversation())
    await specialist.task_cancelled.wait()
    specialist.release.set()
    await reset
    await coordinator.wait_for_background_work()

    assert specialist.cancelled == ["generation-reset-pending"]
    assert state.candidate is None
    assert workspace.current is None
    assert workspace.scenes == ()
    assert documents.document_count == 0
    assert documents.draft_count == 0
    await coordinator.close()


async def test_conversation_reset_releases_unpresented_document_and_visible_draft() -> (
    None
):
    specialist = _ProgressiveSpecialist()
    workspace = SvgCanvasWorkspace()
    state = SvgCanvasSessionState(workspace=workspace)
    documents = SvgCanvasDocumentStore()
    coordinator = SvgCanvasJobCoordinator(
        session_id="session-reset-draft",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=state,
        documents=documents,
        event_sink=_Sink(),
        generation_id_factory=lambda: "generation-reset-draft",
    )
    await coordinator.accept_tool_call(
        source_turn_id="turn-reset-draft",
        call=_call(),
        complete_tool=lambda turn, call, result: _record_completion(
            [], turn, call, result
        ),
    )
    await specialist.draft_emitted.wait()
    assert documents.draft_count == 1

    specialist.release.set()
    await coordinator.wait_for_background_work()
    assert state.candidate is not None
    assert documents.document_count == 1

    await coordinator.reset_conversation()

    assert state.candidate is None
    assert workspace.current is None
    assert workspace.scenes == ()
    assert documents.document_count == 0
    assert documents.draft_count == 0
    await coordinator.close()


async def test_active_renderer_failure_preserves_canvas_semantic_context() -> None:
    (
        coordinator,
        _specialist,
        _sink,
        _completions,
        _documents,
        _stored,
        active,
    ) = await _activated_coordinator()
    context_before = coordinator.state.render_realtime_context()

    await coordinator.runtime_failed(_runtime_failure_event(active))

    assert context_before
    assert coordinator.state.render_realtime_context() == context_before
    await coordinator.close()


async def test_revision_after_renderer_failure_remains_backend_revision() -> None:
    (
        coordinator,
        specialist,
        _sink,
        completions,
        _documents,
        _stored,
        active,
    ) = await _activated_coordinator()
    await coordinator.runtime_failed(_runtime_failure_event(active))

    output = await coordinator.accept_tool_call(
        source_turn_id="turn-2",
        call=_call("call_2", operation="reviseFocused"),
        complete_tool=lambda turn, call, result: _record_completion(
            completions,
            turn,
            call,
            result,
        ),
    )
    await coordinator.wait_for_background_work()

    request = specialist.requests[-1]
    candidate = coordinator.state.candidate
    assert output.generation_id == "generation-2"
    assert request.current_scene_id == active.scene_id
    assert request.current_revision == active.revision
    assert request.current_svg == active.document.svg_text
    assert candidate is not None
    assert candidate.scene_id == active.scene_id
    assert candidate.revision == active.revision + 1
    await coordinator.close()


async def test_successful_activation_retires_delivery_cache_entry() -> None:
    (
        coordinator,
        _specialist,
        _sink,
        _completions,
        documents,
        stored,
        active,
    ) = await _activated_coordinator()

    assert coordinator.state.active is active
    assert active.document is stored.document
    assert active.document.svg_text == stored.document.svg_text
    assert coordinator.state.render_realtime_context()
    with pytest.raises(SvgDocumentNotFoundError):
        await documents.get(
            session_id="session-1",
            generation_id=active.generation_id,
        )
    await coordinator.close()


async def test_committed_workspace_survives_coordinator_replacement() -> None:
    workspaces = SvgCanvasWorkspaceStore()
    active = await _commit_shared_workspace_scene(workspaces)
    replacement_state = SvgCanvasSessionState(
        workspace=workspaces.workspace_for("child:X")
    )
    (
        replacement,
        _specialist,
        _sink,
        _completions,
        _output,
        _documents,
    ) = await _accepted_coordinator(
        ["generation-session-2"],
        state=replacement_state,
        session_id="session-canvas-2",
    )

    assert replacement_state.active is active
    assert replacement_state.active.scene_id == active.scene_id
    assert replacement_state.active.revision == 1
    assert replacement_state.active.document.svg_bytes == active.document.svg_bytes
    await replacement.close()


async def test_replacement_session_revision_uses_exact_prior_svg() -> None:
    workspaces = SvgCanvasWorkspaceStore()
    active = await _commit_shared_workspace_scene(workspaces)
    replacement_state = SvgCanvasSessionState(
        workspace=workspaces.workspace_for("child:X")
    )
    (
        replacement,
        specialist,
        _sink,
        _completions,
        _output,
        _documents,
    ) = await _accepted_coordinator(
        ["generation-session-2"],
        state=replacement_state,
        session_id="session-canvas-2",
        operation="reviseFocused",
    )

    request = specialist.requests[-1]
    assert request.current_scene_id == active.scene_id
    assert request.current_revision == 1
    assert request.current_svg == active.document.svg_text
    assert request.current_svg_sha256 == active.document.svg_sha256
    specialist.release.set()
    await replacement.wait_for_background_work()
    candidate = replacement.state.candidate
    assert candidate is not None
    assert candidate.scene_id == active.scene_id
    assert candidate.revision == 2
    await replacement.close()


async def test_different_subject_session_cannot_see_committed_workspace() -> None:
    workspaces = SvgCanvasWorkspaceStore()
    await _commit_shared_workspace_scene(workspaces, subject="child:X")
    isolated_state = SvgCanvasSessionState(
        workspace=workspaces.workspace_for("child:Y")
    )
    (
        coordinator,
        specialist,
        _sink,
        _completions,
        _output,
        _documents,
    ) = await _accepted_coordinator(
        ["generation-subject-y"],
        state=isolated_state,
        session_id="session-subject-y",
    )

    request = specialist.requests[-1]
    assert isolated_state.active is None
    assert isolated_state.render_realtime_context() == ""
    assert request.current_scene_id is None
    assert request.current_revision is None
    assert request.current_svg is None
    assert request.current_svg_sha256 is None
    specialist.release.set()
    await coordinator.wait_for_background_work()
    candidate = coordinator.state.candidate
    assert candidate is not None
    assert candidate.revision == 1
    await coordinator.close()


async def test_coordinator_close_does_not_clear_committed_workspace() -> None:
    workspaces = SvgCanvasWorkspaceStore()
    active = await _commit_shared_workspace_scene(workspaces)
    workspace = workspaces.workspace_for("child:X")

    assert workspace.current is active
    assert workspace.scenes == (active,)


async def test_accepted_revision_cannot_silently_rebase() -> None:
    workspace = SvgCanvasWorkspace()
    active_a1 = _commit_scene(
        workspace,
        scene_id="scene-A",
        revision=1,
        generation_id="generation-A-1",
        source_turn_id="turn-A-1",
    )
    state = SvgCanvasSessionState(workspace=workspace)
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        documents,
    ) = await _accepted_coordinator(
        ["generation-X"],
        state=state,
        session_id="session-X",
        operation="reviseFocused",
    )
    try:
        request = specialist.requests[-1]
        assert request.current_scene_id == active_a1.scene_id
        assert request.current_revision == active_a1.revision
        assert request.current_svg == active_a1.document.svg_text
        assert request.current_svg_sha256 == active_a1.document.svg_sha256
        active_a2 = _commit_scene(
            workspace,
            scene_id="scene-A",
            revision=2,
            generation_id="generation-Y",
            source_turn_id="turn-Y",
        )

        specialist.release.set()
        await coordinator.wait_for_background_work()

        assert state.candidate is None
        assert sink.available == []
        assert sink.rejected == [
            ("generation-X", CanvasGenerationRejectionCode.PREPARATION_FAILED)
        ]
        assert documents.document_count == 0
        assert workspace.current is active_a2
        assert workspace.scene("scene-A") is active_a2
    finally:
        await coordinator.close()
    assert documents.document_count == 0


async def test_two_coordinators_same_subject_cannot_rebase() -> None:
    workspace = SvgCanvasWorkspace()
    active_a1 = _commit_scene(
        workspace,
        scene_id="scene-A",
        revision=1,
        generation_id="generation-A-1",
        source_turn_id="turn-A-1",
    )
    state_x = SvgCanvasSessionState(workspace=workspace)
    state_y = SvgCanvasSessionState(workspace=workspace)
    (
        coordinator_x,
        specialist_x,
        sink_x,
        _cx,
        _ox,
        documents_x,
    ) = await _accepted_coordinator(
        ["generation-X"],
        state=state_x,
        session_id="session-X",
        operation="reviseFocused",
    )
    (
        coordinator_y,
        specialist_y,
        sink_y,
        _cy,
        _oy,
        documents_y,
    ) = await _accepted_coordinator(
        ["generation-Y"],
        state=state_y,
        session_id="session-Y",
        operation="reviseFocused",
    )
    try:
        for request in (specialist_x.requests[-1], specialist_y.requests[-1]):
            assert request.current_scene_id == active_a1.scene_id
            assert request.current_revision == active_a1.revision
            assert request.current_svg == active_a1.document.svg_text

        specialist_y.release.set()
        await coordinator_y.wait_for_background_work()
        _stored_y, active_y = await _activate_current_candidate(
            coordinator=coordinator_y,
            sink=sink_y,
            documents=documents_y,
            sequence=1,
            session_id="session-Y",
        )
        assert active_y.scene_id == "scene-A"
        assert active_y.revision == 2

        specialist_x.release.set()
        await coordinator_x.wait_for_background_work()

        assert state_x.candidate is None
        assert sink_x.available == []
        assert documents_x.document_count == 0
        assert workspace.current is active_y
        assert workspace.scene("scene-A") is active_y
    finally:
        await coordinator_x.close()
        await coordinator_y.close()


async def test_unrelated_current_change_does_not_invalidate_target_base() -> None:
    workspace = SvgCanvasWorkspace()
    active_a1 = _commit_scene(
        workspace,
        scene_id="scene-A",
        revision=1,
        generation_id="generation-A-1",
        source_turn_id="turn-A-1",
    )
    state = SvgCanvasSessionState(workspace=workspace)
    (
        coordinator,
        specialist,
        _sink,
        _completions,
        _output,
        documents,
    ) = await _accepted_coordinator(
        ["generation-X"],
        state=state,
        session_id="session-X",
        operation="reviseFocused",
    )
    try:
        _commit_scene(
            workspace,
            scene_id="scene-B",
            revision=1,
            generation_id="generation-B-1",
            source_turn_id="turn-B-1",
        )
        specialist.release.set()
        await coordinator.wait_for_background_work()

        candidate = state.candidate
        assert candidate is not None
        assert candidate.scene_id == active_a1.scene_id
        assert candidate.revision == 2
        assert candidate.base_scene_id == active_a1.scene_id
        assert candidate.base_revision == 1
    finally:
        await coordinator.close()
    assert documents.document_count == 0


async def test_no_base_generation_remains_distinct() -> None:
    workspace = SvgCanvasWorkspace()
    state = SvgCanvasSessionState(workspace=workspace)
    (
        coordinator,
        specialist,
        _sink,
        _completions,
        _output,
        _documents,
    ) = await _accepted_coordinator(
        ["generation-X"],
        state=state,
        session_id="session-X",
    )
    try:
        request = specialist.requests[-1]
        assert request.current_scene_id is None
        assert request.current_revision is None
        assert request.current_svg is None
        active_a1 = _commit_scene(
            workspace,
            scene_id="scene-A",
            revision=1,
            generation_id="generation-A-1",
            source_turn_id="turn-A-1",
        )

        specialist.release.set()
        await coordinator.wait_for_background_work()

        candidate = state.candidate
        assert candidate is not None
        assert candidate.scene_id != active_a1.scene_id
        assert candidate.revision == 1
        assert candidate.base_scene_id is None
        assert candidate.base_revision == 0
    finally:
        await coordinator.close()


async def test_reset_during_revision_sol_rejects_late_result() -> None:
    workspace = SvgCanvasWorkspace()
    _commit_scene(
        workspace,
        scene_id="scene-A",
        revision=1,
        generation_id="generation-A-1",
        source_turn_id="turn-A-1",
    )
    state = SvgCanvasSessionState(workspace=workspace)
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        documents,
    ) = await _accepted_coordinator(
        ["generation-X"],
        state=state,
        session_id="session-X",
        operation="reviseFocused",
    )
    try:
        workspace.clear()
        specialist.release.set()
        await coordinator.wait_for_background_work()

        assert state.candidate is None
        assert sink.available == []
        assert sink.rejected == [
            ("generation-X", CanvasGenerationRejectionCode.PREPARATION_FAILED)
        ]
        assert documents.document_count == 0
        assert workspace.scenes == ()
        assert workspace.current is None
    finally:
        await coordinator.close()


async def test_reset_during_no_base_sol_rejects_late_result() -> None:
    workspace = SvgCanvasWorkspace()
    state = SvgCanvasSessionState(workspace=workspace)
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        documents,
    ) = await _accepted_coordinator(
        ["generation-X"],
        state=state,
        session_id="session-X",
    )
    try:
        workspace.clear()
        specialist.release.set()
        await coordinator.wait_for_background_work()

        assert state.candidate is None
        assert sink.available == []
        assert sink.rejected == [
            ("generation-X", CanvasGenerationRejectionCode.PREPARATION_FAILED)
        ]
        assert documents.document_count == 0
        assert workspace.scenes == ()
        assert workspace.current is None
    finally:
        await coordinator.close()
    assert documents.document_count == 0


async def test_reset_after_candidate_staged_blocks_canvas_presented() -> None:
    workspace = SvgCanvasWorkspace()
    state = SvgCanvasSessionState(workspace=workspace)
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        documents,
    ) = await _accepted_coordinator(
        ["generation-X"],
        state=state,
        session_id="session-X",
    )
    try:
        specialist.release.set()
        await coordinator.wait_for_background_work()
        candidate, stored = await _prepare_current_candidate(
            coordinator=coordinator,
            sink=sink,
            documents=documents,
            sequence=1,
            session_id="session-X",
        )
        workspace.clear()

        with pytest.raises(SvgCanvasStateError) as raised:
            await coordinator.presented(
                _presented_event(
                    candidate,
                    stored,
                    sequence=2,
                    session_id="session-X",
                )
            )

        assert raised.value.code is SvgCanvasStateErrorCode.CANONICAL_SCENE_CHANGED
        assert workspace.scenes == ()
        assert workspace.current is None
    finally:
        await coordinator.close()
    assert documents.document_count == 0


async def test_post_reset_generation_uses_new_epoch_normally() -> None:
    workspace = SvgCanvasWorkspace()
    workspace.clear()
    state = SvgCanvasSessionState(workspace=workspace)
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        documents,
    ) = await _accepted_coordinator(
        ["generation-after-reset"],
        state=state,
        session_id="session-after-reset",
    )
    try:
        specialist.release.set()
        await coordinator.wait_for_background_work()
        candidate = state.candidate
        assert candidate is not None
        assert candidate.workspace_epoch == workspace.reset_epoch
        _stored, active = await _activate_current_candidate(
            coordinator=coordinator,
            sink=sink,
            documents=documents,
            sequence=1,
            session_id="session-after-reset",
        )

        assert active.scene_id == candidate.scene_id
        assert active.revision == 1
        assert workspace.current is active
    finally:
        await coordinator.close()


async def test_stale_after_stage_remains_commit_rejected() -> None:
    workspace = SvgCanvasWorkspace()
    _commit_scene(
        workspace,
        scene_id="scene-A",
        revision=1,
        generation_id="generation-A-1",
        source_turn_id="turn-A-1",
    )
    state = SvgCanvasSessionState(workspace=workspace)
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        documents,
    ) = await _accepted_coordinator(
        ["generation-X"],
        state=state,
        session_id="session-X",
        operation="reviseFocused",
    )
    try:
        specialist.release.set()
        await coordinator.wait_for_background_work()
        candidate, stored = await _prepare_current_candidate(
            coordinator=coordinator,
            sink=sink,
            documents=documents,
            sequence=1,
            session_id="session-X",
        )
        active_y = _commit_scene(
            workspace,
            scene_id="scene-A",
            revision=2,
            generation_id="generation-Y",
            source_turn_id="turn-Y",
        )

        with pytest.raises(SvgCanvasStateError) as raised:
            await coordinator.presented(
                _presented_event(
                    candidate,
                    stored,
                    sequence=2,
                    session_id="session-X",
                )
            )

        assert raised.value.code is SvgCanvasStateErrorCode.CANONICAL_SCENE_CHANGED
        assert workspace.current is active_y
        assert workspace.scene("scene-A") is active_y
    finally:
        await coordinator.close()
    assert documents.document_count == 0


async def test_many_successful_revisions_do_not_exhaust_delivery_cache() -> None:
    generation_ids = [f"generation-{index}" for index in range(1, 13)]
    (
        coordinator,
        specialist,
        sink,
        completions,
        _output,
        documents,
    ) = await _accepted_coordinator(generation_ids)
    specialist.release.set()
    await coordinator.wait_for_background_work()

    active: ActiveSvgCanvasScene | None = None
    for index, generation_id in enumerate(generation_ids, start=1):
        if index > 1:
            output = await coordinator.accept_tool_call(
                source_turn_id=f"turn-{index}",
                call=_call(f"call_{index}", operation="reviseFocused"),
                complete_tool=lambda turn, call, result: _record_completion(
                    completions,
                    turn,
                    call,
                    result,
                ),
            )
            assert output.generation_id == generation_id
            await coordinator.wait_for_background_work()
            assert sink.rejected == []
        _stored, active = await _activate_current_candidate(
            coordinator=coordinator,
            sink=sink,
            documents=documents,
            sequence=index * 2,
        )
        assert active.revision == index

    assert active is not None
    assert active.revision == 12
    assert coordinator.state.active is active
    assert coordinator.state.active.document.svg_text == _document().svg_text
    assert coordinator.state.active.document.svg_sha256 == _document().svg_sha256
    assert documents.document_count == 0
    assert len(sink.activations) == 12
    assert sink.rejected == []
    await coordinator.close()


async def test_delivery_document_remains_available_before_activation() -> None:
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        documents,
    ) = await _accepted_coordinator()
    specialist.release.set()
    await coordinator.wait_for_background_work()
    candidate = coordinator.state.candidate
    stored = sink.available[-1]

    assert candidate is not None
    assert coordinator.state.active is None
    assert (
        await documents.get(
            session_id="session-1",
            generation_id=candidate.generation_id,
        )
        == stored
    )
    await coordinator.close()


async def test_activation_delivery_failure_keeps_delivery_entry_until_cleanup() -> None:
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        documents,
    ) = await _accepted_coordinator()
    specialist.release.set()
    await coordinator.wait_for_background_work()
    sink.activation_failure = RuntimeError("activation delivery failed")
    stored = sink.available[-1]

    with pytest.raises(RuntimeError, match="activation delivery failed"):
        await _activate_current_candidate(
            coordinator=coordinator,
            sink=sink,
            documents=documents,
            sequence=1,
        )

    assert coordinator.state.active is not None
    assert (
        await documents.get(
            session_id="session-1",
            generation_id=stored.generation_id,
        )
        == stored
    )
    await coordinator.close()
    assert documents.document_count == 0


async def test_candidate_renderer_failure_remains_non_committing_backend() -> None:
    workspaces = SvgCanvasWorkspaceStore()
    state = SvgCanvasSessionState(workspace=workspaces.workspace_for("child:X"))
    (
        coordinator,
        _specialist,
        sink,
        completions,
        documents,
        stored,
        active,
    ) = await _activated_coordinator(state=state)
    context_before = coordinator.state.render_realtime_context()
    output = await coordinator.accept_tool_call(
        source_turn_id="turn-2",
        call=_call("call_2", operation="reviseFocused"),
        complete_tool=lambda turn, call, result: _record_completion(
            completions,
            turn,
            call,
            result,
        ),
    )
    await coordinator.wait_for_background_work()
    candidate = coordinator.state.candidate
    assert candidate is not None

    discarded = await coordinator.preparation_failed(
        CanvasPreparationFailedEventV4.model_validate(
            {
                "protocolVersion": 4,
                "event": "canvasPreparationFailed",
                "sessionId": "session-1",
                "sequence": 3,
                "generationId": candidate.generation_id,
                "sceneId": candidate.scene_id,
                "sceneRevision": candidate.revision,
                "svgSha256": candidate.document.svg_sha256,
                "manifestSha256": candidate.document.manifest_sha256,
                "failureCode": "rendererRejected",
                "message": "The renderer rejected the candidate.",
            }
        )
    )

    assert output.generation_id == discarded.generation_id
    assert coordinator.state.candidate is None
    assert coordinator.state.active is active
    with pytest.raises(SvgDocumentNotFoundError):
        await documents.get(
            session_id="session-1",
            generation_id=active.generation_id,
        )
    assert coordinator.state.active.document is stored.document
    assert coordinator.state.render_realtime_context() == context_before
    with pytest.raises(SvgDocumentNotFoundError):
        await documents.get(
            session_id="session-1",
            generation_id=discarded.generation_id,
        )
    assert sink.active_discarded == []
    assert sink.rejected[-1] == (
        discarded.generation_id,
        CanvasGenerationRejectionCode.PREPARATION_FAILED,
    )
    replacement = SvgCanvasSessionState(workspace=workspaces.workspace_for("child:X"))
    assert replacement.active is active
    assert replacement.active.document is stored.document
    assert replacement.render_realtime_context() == context_before
    await coordinator.close()


async def test_revision_reuses_scene_identity_and_supplies_exact_active_svg() -> None:
    (
        coordinator,
        specialist,
        sink,
        completions,
        _output,
        documents,
    ) = await _accepted_coordinator()
    specialist.release.set()
    await coordinator.wait_for_background_work()
    first_stored = sink.available[0]
    first_candidate = coordinator.state.candidate
    assert first_candidate is not None
    await coordinator.request_presentation(
        generation_id=first_candidate.generation_id,
        presentation_sample_offset=4_800,
    )
    await coordinator.document_prepared(
        CanvasDocumentPreparedEventV4.model_validate(
            {
                "protocolVersion": 4,
                "event": "canvasDocumentPrepared",
                "sessionId": "session-1",
                "sequence": 1,
                "generationId": first_candidate.generation_id,
                "sceneId": first_candidate.scene_id,
                "sceneRevision": first_candidate.revision,
                "svgSha256": first_stored.document.svg_sha256,
                "manifestSha256": first_stored.document.manifest_sha256,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    first_active = await coordinator.presented(
        CanvasPresentedEventV4.model_validate(
            {
                "protocolVersion": 4,
                "event": "canvasPresented",
                "sessionId": "session-1",
                "sequence": 2,
                "generationId": first_candidate.generation_id,
                "sceneId": first_candidate.scene_id,
                "sceneRevision": first_candidate.revision,
                "svgSha256": first_stored.document.svg_sha256,
                "manifestSha256": first_stored.document.manifest_sha256,
                "firstRenderedSample": 4_800,
            }
        )
    )
    with pytest.raises(SvgDocumentNotFoundError):
        await documents.get(
            session_id="session-1",
            generation_id=first_active.generation_id,
        )

    revised_output = await coordinator.accept_tool_call(
        source_turn_id="turn-2",
        call=_call("call_2", operation="reviseFocused"),
        complete_tool=lambda turn, call, output: _record_completion(
            completions,
            turn,
            call,
            output,
        ),
    )
    await coordinator.wait_for_background_work()

    revision_request = specialist.requests[-1]
    revised_candidate = coordinator.state.candidate
    assert revised_output.generation_id == "generation-2"
    assert revision_request.current_scene_id == first_active.scene_id
    assert revision_request.current_revision == first_active.revision
    assert revision_request.current_svg == first_active.document.svg_text
    assert revision_request.current_svg_sha256 == first_active.document.svg_sha256
    assert revised_candidate is not None
    assert revised_candidate.scene_id == first_active.scene_id
    assert revised_candidate.revision == first_active.revision + 1
    await coordinator.close()


async def test_canvas_lifecycle_telemetry_preserves_generation_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(
        logging.INFO,
        logger="ava_backend.realtime.svg_canvas_jobs",
    ):
        (
            coordinator,
            specialist,
            sink,
            _completions,
            _output,
            _documents,
        ) = await _accepted_coordinator()
        specialist.release.set()
        await coordinator.wait_for_background_work()
        stored = sink.available[0]
        candidate = coordinator.state.candidate
        assert candidate is not None
        await coordinator.request_presentation(
            generation_id=candidate.generation_id,
            presentation_sample_offset=4_800,
        )
        await coordinator.document_prepared(
            CanvasDocumentPreparedEventV4.model_validate(
                {
                    "protocolVersion": 4,
                    "event": "canvasDocumentPrepared",
                    "sessionId": "session-1",
                    "sequence": 1,
                    "generationId": candidate.generation_id,
                    "sceneId": candidate.scene_id,
                    "sceneRevision": candidate.revision,
                    "svgSha256": stored.document.svg_sha256,
                    "manifestSha256": stored.document.manifest_sha256,
                    "rendererCapabilityVersion": "ava-svg-renderer-v2",
                    "validatorVersion": "ava-svg-validator-v2",
                }
            )
        )
        await coordinator.presented(
            CanvasPresentedEventV4.model_validate(
                {
                    "protocolVersion": 4,
                    "event": "canvasPresented",
                    "sessionId": "session-1",
                    "sequence": 2,
                    "generationId": candidate.generation_id,
                    "sceneId": candidate.scene_id,
                    "sceneRevision": candidate.revision,
                    "svgSha256": stored.document.svg_sha256,
                    "manifestSha256": stored.document.manifest_sha256,
                    "firstRenderedSample": 4_812,
                }
            )
        )

    telemetry = [
        record.ava_context
        for record in caplog.records
        if hasattr(record, "ava_context")
    ]
    stages = [event["stage"] for event in telemetry]
    assert stages == [
        "accepted",
        "generationStarted",
        "specialistStarted",
        "documentPublished",
        "presentationRequested",
        "documentPrepared",
        "presentationCueSent",
        "presentationAcknowledged",
        "activationCompleted",
    ]
    for event in telemetry:
        assert event["sessionId"] == "session-1"
        assert event["generationId"] == "generation-1"
        assert event["sourceTurnId"] == "turn-1"
        assert event["operation"] == "new"
    published = next(
        event for event in telemetry if event["stage"] == "documentPublished"
    )
    assert published["svgByteLength"] == len(stored.document.svg_bytes)
    assert published["svgSha256"] == stored.document.svg_sha256
    activated = telemetry[-1]
    assert activated["firstRenderedSample"] == 4_812
    assert activated["acceptedToActivationMs"] >= 0
    assert "Show how clouds make rain" not in caplog.text
    await coordinator.close()


async def test_source_turn_interruption_keeps_accepted_generation_alive() -> None:
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        documents,
    ) = await _accepted_coordinator()

    cue = await coordinator.request_interrupted_presentation(
        source_turn_id="turn-1",
        presentation_sample_offset=1_337,
    )

    assert cue is None
    assert specialist.cancelled == []
    assert sink.rejected == []
    specialist.release.set()
    await coordinator.wait_for_background_work()
    assert coordinator.state.candidate is not None
    assert coordinator.state.candidate.generation_id == "generation-1"
    candidate, stored = await _prepare_current_candidate(
        coordinator=coordinator,
        sink=sink,
        documents=documents,
        sequence=1,
        presentation_sample_offset=None,
    )
    assert sink.cues[-1][1].presentation_sample_offset == 1_337
    active = await coordinator.presented(
        _presented_event(candidate, stored, sequence=2)
    )
    assert active.generation_id == "generation-1"
    await coordinator.close()


async def test_source_turn_interruption_keeps_prepared_candidate_alive() -> None:
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        documents,
    ) = await _accepted_coordinator()
    specialist.release.set()
    await coordinator.wait_for_background_work()
    candidate, _stored = await _prepare_current_candidate(
        coordinator=coordinator,
        sink=sink,
        documents=documents,
        sequence=1,
        presentation_sample_offset=None,
    )
    initial_cue = await coordinator.request_presentation(
        generation_id=candidate.generation_id,
        presentation_sample_offset=4_800,
    )

    cue = await coordinator.request_interrupted_presentation(
        source_turn_id="turn-1",
        presentation_sample_offset=927,
    )

    assert sink.discarded == []
    assert coordinator.state.candidate is not None
    assert initial_cue is not None
    assert initial_cue.presentation_sample_offset == 4_800
    assert cue is not None
    assert cue.presentation_sample_offset == 927
    assert [item[1].presentation_sample_offset for item in sink.cues] == [4_800, 927]
    assert coordinator.state.candidate.presentation_sample_offset == 927
    await coordinator.close()


async def test_no_canvas_action_keeps_pending_workspace_generation() -> None:
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        _documents,
    ) = await _accepted_coordinator()

    cue = await coordinator.request_interrupted_presentation(
        source_turn_id="turn-2",
        presentation_sample_offset=0,
    )

    assert cue is None
    assert specialist.cancelled == []
    assert sink.rejected == []
    context = coordinator.render_realtime_context()
    assert '<pending_visual state="generating"' in context
    assert "Show how clouds make rain" in context
    assert "generation-1" not in context
    assert "turn-1" not in context
    await coordinator.close()


async def test_cancelled_generation_cannot_commit_after_provider_returns() -> None:
    specialist = _CancellationResistantSpecialist()
    sink = _Sink()

    async def complete(
        turn_id: str,
        call_id: str,
        output: InteractiveCanvasToolOutputV4,
    ) -> None:
        del turn_id, call_id, output

    coordinator = SvgCanvasJobCoordinator(
        session_id="session-cancel-race",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=SvgCanvasSessionState(),
        documents=SvgCanvasDocumentStore(),
        event_sink=sink,
        generation_id_factory=lambda: "generation-cancel-race",
    )
    await coordinator.accept_tool_call(
        source_turn_id="turn-cancelled",
        call=_call(),
        complete_tool=complete,
    )
    await specialist.started.wait()

    cancellation = asyncio.create_task(coordinator.cancel_pending())
    await specialist.task_cancelled.wait()
    specialist.release.set()
    await cancellation

    assert coordinator.state.candidate is None
    assert sink.available == []
    assert sink.rejected == [
        ("generation-cancel-race", CanvasGenerationRejectionCode.CANCELLED)
    ]
    execution = coordinator.state.workspace.execution("generation-cancel-race")
    assert execution is not None
    assert execution.status == SvgCanvasExecutionStatus.CANCELLED
    await coordinator.close()


async def test_turn_task_cancellation_cannot_lose_accepted_canvas_handoff() -> None:
    specialist = _Specialist()
    specialist.release.set()
    sink = _Sink()
    complete_started = asyncio.Event()
    complete_release = asyncio.Event()

    async def complete(
        turn_id: str,
        call_id: str,
        output: InteractiveCanvasToolOutputV4,
    ) -> None:
        del turn_id, call_id, output
        complete_started.set()
        await complete_release.wait()

    coordinator = SvgCanvasJobCoordinator(
        session_id="session-cancelled-reservation",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=SvgCanvasSessionState(),
        documents=SvgCanvasDocumentStore(),
        event_sink=sink,
        generation_id_factory=lambda: "generation-cancelled-reservation",
    )
    acceptance = asyncio.create_task(
        coordinator.accept_tool_call(
            source_turn_id="turn-cancelled",
            call=_call(),
            complete_tool=complete,
        )
    )
    await complete_started.wait()

    acceptance.cancel()
    complete_release.set()
    with pytest.raises(asyncio.CancelledError):
        await acceptance
    await coordinator.wait_for_background_work()

    assert len(specialist.requests) == 1
    assert coordinator.state.candidate is not None
    assert sink.started == [("generation-cancelled-reservation", "turn-cancelled")]
    assert len(sink.available) == 1
    assert sink.rejected == []
    execution = coordinator.state.workspace.execution(
        "generation-cancelled-reservation"
    )
    assert execution is not None
    assert execution.status == SvgCanvasExecutionStatus.PREPARING
    await coordinator.close()


async def test_explicit_canvas_cancellation_cancels_pending_generation() -> None:
    (
        coordinator,
        specialist,
        sink,
        _completions,
        _output,
        _documents,
    ) = await _accepted_coordinator()

    expired = await coordinator.cancel_pending()

    assert expired == ("generation-1",)
    assert specialist.cancelled == ["generation-1"]
    assert coordinator.state.candidate is None
    assert sink.available == []
    assert sink.rejected == [("generation-1", CanvasGenerationRejectionCode.CANCELLED)]
    cancelled = coordinator.state.workspace.execution("generation-1")
    assert cancelled is not None
    assert cancelled.status == SvgCanvasExecutionStatus.CANCELLED
    assert await coordinator.cancel_pending() == ()
    assert len(sink.rejected) == 1
    await coordinator.close()


async def test_cancellation_is_terminal_before_discard_event_delivery() -> None:
    specialist = _Specialist()
    sink = _DiscardFailingSink()
    documents = SvgCanvasDocumentStore()

    async def complete(
        turn_id: str,
        call_id: str,
        output: InteractiveCanvasToolOutputV4,
    ) -> None:
        del turn_id, call_id, output

    coordinator = SvgCanvasJobCoordinator(
        session_id="session-discard-delivery-failure",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=SvgCanvasSessionState(),
        documents=documents,
        event_sink=sink,
        generation_id_factory=lambda: "generation-discard-delivery-failure",
    )
    await coordinator.accept_tool_call(
        source_turn_id="turn-discard-delivery-failure",
        call=_call(),
        complete_tool=complete,
    )
    specialist.release.set()
    await coordinator.wait_for_background_work()

    with pytest.raises(RuntimeError, match="discard delivery failed"):
        await coordinator.cancel_pending()

    execution = coordinator.state.workspace.execution(
        "generation-discard-delivery-failure"
    )
    assert execution is not None
    assert execution.status == SvgCanvasExecutionStatus.CANCELLED
    assert coordinator.state.candidate is None
    assert documents.document_count == 0
    await coordinator.close()


async def test_explicit_canvas_cancellation_preserves_active_scene() -> None:
    (
        coordinator,
        _specialist,
        sink,
        completions,
        _documents,
        _stored,
        active,
    ) = await _activated_coordinator()

    await coordinator.accept_tool_call(
        source_turn_id="turn-2",
        call=_call("call_2", brief="Show a different rain cloud."),
        complete_tool=lambda turn, call, output: _record_completion(
            completions, turn, call, output
        ),
    )
    await coordinator.wait_for_background_work()

    assert coordinator.state.candidate is not None
    assert await coordinator.cancel_pending() == ("generation-2",)
    assert coordinator.state.candidate is None
    assert coordinator.state.active is active
    assert sink.active_discarded == []
    await coordinator.close()


async def test_specialist_cancel_failure_cannot_revive_optional_canvas() -> None:
    specialist = _CancelFailingSpecialist()
    sink = _Sink()

    async def complete(
        turn_id: str,
        call_id: str,
        output: InteractiveCanvasToolOutputV4,
    ) -> None:
        del turn_id, call_id, output

    coordinator = SvgCanvasJobCoordinator(
        session_id="session-cancel-failure",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=SvgCanvasSessionState(),
        documents=SvgCanvasDocumentStore(),
        event_sink=sink,
        generation_id_factory=lambda: "generation-cancel-failure",
    )
    await coordinator.accept_tool_call(
        source_turn_id="turn-1",
        call=_call(),
        complete_tool=complete,
    )
    await specialist.started.wait()

    assert await coordinator.cancel_pending() == ("generation-cancel-failure",)
    assert coordinator.state.candidate is None
    assert sink.available == []
    assert sink.rejected == [
        ("generation-cancel-failure", CanvasGenerationRejectionCode.CANCELLED)
    ]
    await coordinator.close()


async def test_close_finishes_local_cleanup_when_specialist_cancel_fails() -> None:
    specialist = _CancelFailingSpecialist()
    sink = _Sink()

    async def complete(
        turn_id: str,
        call_id: str,
        output: InteractiveCanvasToolOutputV4,
    ) -> None:
        del turn_id, call_id, output

    coordinator = SvgCanvasJobCoordinator(
        session_id="session-close-cancel-failure",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=SvgCanvasSessionState(),
        documents=SvgCanvasDocumentStore(),
        event_sink=sink,
        generation_id_factory=lambda: "generation-close-cancel-failure",
    )
    await coordinator.accept_tool_call(
        source_turn_id="turn-1",
        call=_call(),
        complete_tool=complete,
    )
    await specialist.started.wait()

    await coordinator.close()

    assert specialist.closed is True
    assert coordinator.state.candidate is None
    execution = coordinator.state.workspace.execution("generation-close-cancel-failure")
    assert execution is not None
    assert execution.status == SvgCanvasExecutionStatus.CANCELLED
    assert execution.terminal_reason == "sessionClosed"
