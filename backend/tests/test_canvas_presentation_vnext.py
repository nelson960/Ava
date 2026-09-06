from __future__ import annotations

import asyncio
import logging
import unittest.mock
import xml.etree.ElementTree as ET
from builtins import anext
from dataclasses import replace
from typing import AsyncIterator

import pytest
from pydantic import ValidationError

from ava_backend.canvas.svg_delivery import SvgCanvasDocumentStore
from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_session import SvgCanvasSessionState
from ava_backend.canvas.svg_specialist import (
    SvgCanvasSpecialistGeneration,
    SvgCanvasSpecialistRequest,
)
from ava_backend.canvas.svg_workspace import (
    ActiveSvgCanvasScene,
    CanvasNavigationDirection,
    CanvasWorkspaceManifest,
    CanvasWorkspaceManifestScene,
    SvgCanvasWorkspace,
)
from ava_backend.protocol import AgeBand
from ava_backend.realtime.coordinator import DeterministicRealtimeSessionCoordinator
from ava_backend.realtime.coordinator_v4 import ProtocolV4CanvasEventSink
from ava_backend.realtime.events_v4 import (
    AVA_CLIENT_EVENT_ADAPTER_V4,
    AudioMetadataEventV4,
    AvaClientEventUnionV4,
    CanvasPresentationCommandEventV4,
    InterruptRequestedEventV4,
    TurnCancelledEventV4,
    TurnPhaseChangedEventV4,
)
from ava_backend.realtime.fakes import (
    DETERMINISTIC_AUDIO_OFFER_SDP,
    DeterministicRealtimePlanProvider,
    DeterministicStreamingSpeechSynthesizer,
)
from ava_backend.realtime.harness import RealtimeHarness
from ava_backend.realtime.memory import DevelopmentAuthoritativeMemoryStore
from ava_backend.realtime.plan import AvaTurnPlan
from ava_backend.realtime.provider_session import (
    RealtimeContextInstallationStatus,
    RealtimeProviderInputEvent,
    RealtimeProviderTurnEvent,
)
from ava_backend.realtime.svg_canvas_jobs import SvgCanvasJobCoordinator
from ava_backend.realtime.tool_contract import (
    InteractiveCanvasRequest,
    InteractiveCanvasToolStatusV4,
    ManageCanvasPresentationAction,
    ManageCanvasNavigationDirection,
    ManageCanvasPresentationRequest,
    ManageCanvasPresentationStatus,
    ManageCanvasPresentationToolOutputV4,
    RealtimeCanvasToolCall,
    RealtimeManageCanvasPresentationToolCall,
    RealtimeSpeechTextDelta,
)
from ava_backend.realtime.websocket_v4 import AvaServerControlFrameV4
from ava_backend.realtime.speech_contract import SpeechPhase

from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"


def _plan() -> AvaTurnPlan:
    return AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / "ava_turn_plan_conversation_v3.json").read_text(
            encoding="utf-8"
        )
    )


SESSION_ID = "session-pres-test"
TURN_ID = "turn-pres-test"
STREAM_ID = "stream-pres-test"

_SAMPLE_SVG_1 = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" data-ava-interaction-mode="none">
<title>Scene 1</title><desc>First scene in workspace</desc>
<rect id="rect_1" x="10" y="10" width="100" height="100"/>
</svg>"""

_SAMPLE_SVG_2 = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" data-ava-interaction-mode="none">
<title>Scene 2</title><desc>Second scene in workspace</desc>
<circle id="circle_2" cx="200" cy="200" r="50"/>
</svg>"""


def _create_scene(
    scene_id: str, svg_text: str, generation_id: str
) -> ActiveSvgCanvasScene:
    document = SvgDocumentValidator().validate(svg_text)
    return ActiveSvgCanvasScene(
        generation_id=generation_id,
        source_turn_id="turn-seed",
        scene_id=scene_id,
        revision=1,
        first_rendered_sample=0,
        document=document,
    )


def _synchronized_workspace(
    scenes: list[ActiveSvgCanvasScene], *, focused_index: int
) -> SvgCanvasWorkspace:
    rows = []
    for index, scene in enumerate(scenes):
        manifest = scene.document.manifest
        rows.append(
            CanvasWorkspaceManifestScene(
                scene_id=scene.scene_id,
                scene_revision=scene.revision,
                generation_id=scene.generation_id,
                source_turn_id=scene.source_turn_id,
                svg_sha256=scene.document.svg_sha256,
                manifest_sha256=scene.document.manifest_sha256,
                svg_byte_length=len(scene.document.svg_bytes),
                manifest_byte_length=len(scene.document.manifest_bytes),
                renderer_capability_version=manifest["rendererCapabilityVersion"],
                validator_version=manifest["validatorVersion"],
                manifest_version=manifest["manifestVersion"],
                creation_sequence=index + 1,
                modification_sequence=None,
                title=scene.document.title,
                summary=scene.document.description,
            )
        )
    workspace = SvgCanvasWorkspace()
    workspace.adopt_manifest(
        CanvasWorkspaceManifest(
            protocol_version=4,
            workspace_epoch=9,
            workspace_revision=4,
            next_creation_sequence=len(scenes) + 1,
            next_modification_sequence=1,
            focused_scene_id=scenes[focused_index].scene_id,
            scenes=tuple(rows),
        )
    )
    for scene in scenes:
        workspace.focus_retained(scene)
    workspace.focus_retained(scenes[focused_index])
    return workspace


def _client(payload: dict[str, object]) -> AvaClientEventUnionV4:
    return AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
        {
            "protocolVersion": 4,
            "sessionId": SESSION_ID,
            **payload,
        }
    )


async def _consume_turn_frames(
    coord: DeterministicRealtimeSessionCoordinator,
    on_frame=None,
) -> None:
    async for frame in coord.frames():
        if isinstance(frame, AvaServerControlFrameV4):
            if on_frame is not None:
                await on_frame(frame.event)
            if isinstance(frame.event, AudioMetadataEventV4):
                await coord.receive(
                    _client(
                        {
                            "event": "playbackStarted",
                            "sequence": coord._next_client_sequence,
                            "turnId": TURN_ID,
                            "streamId": STREAM_ID,
                            "renderedSampleCount": 0,
                        }
                    )
                )
            elif (
                isinstance(frame.event, TurnPhaseChangedEventV4)
                and frame.event.phase == SpeechPhase.SPEAKING
            ):
                await coord.receive(
                    _client(
                        {
                            "event": "playbackProgress",
                            "sequence": coord._next_client_sequence,
                            "turnId": TURN_ID,
                            "streamId": STREAM_ID,
                            "renderedSampleCount": coord.received_sample_count,
                            "bufferedSampleCount": 0,
                        }
                    )
                )


class _PresentationTestRealtimeProvider(DeterministicRealtimePlanProvider):
    async def prepare_if_idle(
        self,
        *,
        harness: RealtimeHarness,
        turn_id: str,
    ) -> RealtimeContextInstallationStatus:
        del turn_id
        return await self.prepare(harness=harness)

    async def wait_until_canvas_interaction_response_ready(
        self,
        *,
        turn_id: str,
    ) -> bool:
        del turn_id
        return True


class _ModelPresentationProvider(_PresentationTestRealtimeProvider):
    def __init__(
        self,
        plan: AvaTurnPlan,
        *,
        action: ManageCanvasPresentationAction,
        scene_id: str | None = None,
    ) -> None:
        super().__init__(plan)
        self.action = action
        self.scene_id = scene_id
        self.tool_completed = asyncio.Event()
        self.completed_output: ManageCanvasPresentationToolOutputV4 | None = None
        self.completion_count = 0

    async def _stream(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        self.turn_stream_started.set()
        yield RealtimeManageCanvasPresentationToolCall(
            call_id="call_pres_1",
            request=ManageCanvasPresentationRequest(
                action=self.action,
                sceneId=self.scene_id,
            ),
            provider_response_id="resp-presentation-tool",
        )
        await self.tool_completed.wait()
        yield RealtimeSpeechTextDelta(delta="Presentation tool processed.")

    async def complete_manage_canvas_presentation_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: ManageCanvasPresentationToolOutputV4,
    ) -> None:
        del turn_id, call_id
        self.completion_count += 1
        self.completed_output = output
        self.tool_completed.set()


class _StressPresentationProvider(_PresentationTestRealtimeProvider):
    def __init__(self, plan: AvaTurnPlan) -> None:
        super().__init__(plan)
        self.tool_completed = asyncio.Event()
        self.terminal_observed = asyncio.Event()
        self.cancel_settled = asyncio.Event()
        self.cancel_settlement_count = 0

    async def _stream(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        self.turn_stream_started.set()
        if len(self.requests) == 1:
            yield RealtimeManageCanvasPresentationToolCall(
                call_id="call_stress_show",
                request=ManageCanvasPresentationRequest(
                    action=ManageCanvasPresentationAction.SHOW,
                    sceneId="scene_2",
                ),
                provider_response_id="resp-stress-control",
            )
            await self.tool_completed.wait()
            # Model the receiver-boundary terminal fact that remains durable
            # even though another consumer has dequeued response.done.
            self.terminal_observed.set()
            yield RealtimeSpeechTextDelta(delta="The first answer is ready. ")
            await self.cancel_settled.wait()
            return
        yield RealtimeSpeechTextDelta(delta="The recovered answer is ready. ")

    async def complete_manage_canvas_presentation_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: ManageCanvasPresentationToolOutputV4,
    ) -> None:
        await super().complete_manage_canvas_presentation_tool(
            turn_id=turn_id,
            call_id=call_id,
            output=output,
        )
        self.tool_completed.set()

    async def cancel(self, *, turn_id: str) -> None:
        if turn_id in self.cancelled_turn_ids:
            return
        assert self.terminal_observed.is_set()
        self.cancel_settlement_count += 1
        if self._active_turn_id == turn_id:
            self._active_turn_id = None
            self._active_response_turn_id = None
        self.cancel_settled.set()
        await super().cancel(turn_id=turn_id)


class _DummySpecialist:
    async def generate(
        self, request: SvgCanvasSpecialistRequest, *, draft_sink: object | None = None
    ):
        raise AssertionError("Specialist should not be called in presentation tests")

    async def cancel(self, *, generation_id: str) -> None:
        pass

    async def close(self) -> None:
        pass


def _setup_coordinator(
    *,
    workspace: SvgCanvasWorkspace | None = None,
    realtime_provider: DeterministicRealtimePlanProvider | None = None,
    persistent: bool = False,
) -> tuple[
    DeterministicRealtimeSessionCoordinator,
    ProtocolV4CanvasEventSink,
    SvgCanvasSessionState,
]:
    ws = workspace or SvgCanvasWorkspace()
    state = SvgCanvasSessionState(workspace=ws)
    events = ProtocolV4CanvasEventSink()
    jobs = SvgCanvasJobCoordinator(
        session_id=SESSION_ID,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=_DummySpecialist(),
        state=state,
        documents=SvgCanvasDocumentStore(),
        event_sink=events,
        generation_id_factory=lambda: "gen-fact",
    )
    provider = realtime_provider or _PresentationTestRealtimeProvider(_plan())
    speech = DeterministicStreamingSpeechSynthesizer()
    memory = DevelopmentAuthoritativeMemoryStore()
    coord = DeterministicRealtimeSessionCoordinator(
        session_id=SESSION_ID,
        turn_id=TURN_ID,
        request_id="req-pres-1",
        stream_id=STREAM_ID,
        subject="pres-child",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=provider,
        speech=speech,
        canvas_jobs=jobs,
        canvas_events=events,
        memory=memory,
        persistent=persistent,
        turn_id_factory=(lambda: "turn-pres-next") if persistent else None,
        request_id_factory=(lambda: "req-pres-next") if persistent else None,
        stream_id_factory=(lambda: "stream-pres-next") if persistent else None,
    )
    return coord, events, state


@pytest.mark.asyncio
async def test_client_ready_with_matching_visible_canvas_sets_presentation_visible() -> (
    None
):
    ws = SvgCanvasWorkspace()
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    ws.restore_focused(scene1)

    coord, events, state = _setup_coordinator(workspace=ws)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)

    assert state.presentation_visible is False
    assert state.focused_scene == scene1
    assert state.active is None

    # ClientReady arrives with canvasPanelVisible=True for scene_1
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": True,
                "sceneId": "scene_1",
                "focusRevision": 1,
            }
        )
    )

    assert state.presentation_visible is True
    assert state.active == scene1

    # Verify presentation_changed event was sent to client
    event_payload = events._events.get_nowait()
    assert event_payload["event"] == "canvasPresentationChanged"
    assert event_payload["visible"] is True
    assert event_payload["sceneId"] == "scene_1"
    assert event_payload["source"] == "restore"

    # Verify context refresh included active visual
    assert coord.harness.context.canvas_included is True
    assert "<active_visual" in coord.harness.instructions
    assert (
        '<canvas_presentation trust="validated_current_state" visible="true"'
        in coord.harness.instructions
    )
    assert 'scene_id="scene_1" revision="1"' in coord.harness.instructions
    assert (
        '<active_visual scene_id="scene_1" revision="1"' in coord.harness.instructions
    )

    await coord.close()


@pytest.mark.asyncio
async def test_client_ready_with_hidden_canvas_leaves_presentation_invisible() -> None:
    ws = SvgCanvasWorkspace()
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    ws.restore_focused(scene1)

    coord, events, state = _setup_coordinator(workspace=ws)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)

    assert state.presentation_visible is False

    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": False,
            }
        )
    )

    assert state.presentation_visible is False
    assert state.focused_scene == scene1
    assert state.active is None
    assert events._events.empty()
    assert coord.harness.context.canvas_included is False
    assert "<active_visual" not in coord.harness.instructions
    assert "<retained_visuals" in coord.harness.instructions

    await coord.close()


@pytest.mark.asyncio
async def test_client_physical_update_to_hidden_and_reopen() -> None:
    ws = SvgCanvasWorkspace()
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    ws.restore_focused(scene1)

    coord, events, state = _setup_coordinator(workspace=ws)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": True,
                "sceneId": "scene_1",
                "focusRevision": 1,
            }
        )
    )
    _ = events._events.get_nowait()
    assert state.presentation_visible is True

    # 1. User closes the panel
    await coord.receive(
        _client(
            {
                "event": "canvasPresentationUpdated",
                "sequence": 1,
                "visible": False,
                "clientOperationId": "op_dismiss",
            }
        )
    )

    assert state.presentation_visible is False
    assert state.active is None
    event_payload = events._events.get_nowait()
    assert event_payload["event"] == "canvasPresentationChanged"
    assert event_payload["visible"] is False
    assert event_payload["source"] == "user"
    assert coord.harness.context.canvas_included is False

    # 2. User reopens the panel
    await coord.receive(
        _client(
            {
                "event": "canvasPresentationUpdated",
                "sequence": 2,
                "visible": True,
                "sceneId": "scene_1",
                "focusRevision": 1,
                "clientOperationId": "op_reopen",
            }
        )
    )

    assert state.presentation_visible is True
    assert state.active == scene1
    event_payload = events._events.get_nowait()
    assert event_payload["event"] == "canvasPresentationChanged"
    assert event_payload["visible"] is True
    assert event_payload["sceneId"] == "scene_1"
    assert event_payload["source"] == "user"
    assert coord.harness.context.canvas_included is True

    await coord.close()


@pytest.mark.asyncio
async def test_client_physical_update_defers_until_matching_focus_accepted() -> None:
    ws = SvgCanvasWorkspace()
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    scene2 = _create_scene("scene_2", _SAMPLE_SVG_2, "gen_2")
    ws.restore_focused(scene2)
    ws.restore_focused(scene1)

    coord, events, state = _setup_coordinator(workspace=ws)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": False,
                "sceneId": "scene_1",
                "focusRevision": 1,
            }
        )
    )
    assert state.presentation_visible is False
    assert state.focused_scene == scene1

    # Client sends visible=True for scene_2 at focus_revision=2, which is pending acceptance
    update_task = asyncio.create_task(
        coord.receive(
            _client(
                {
                    "event": "canvasPresentationUpdated",
                    "sequence": 1,
                    "visible": True,
                    "sceneId": "scene_2",
                    "focusRevision": 2,
                    "clientOperationId": "op_open_scene2",
                }
            )
        )
    )
    await asyncio.sleep(0.05)
    # Still pending: not visible yet, no active visual
    assert state.presentation_visible is False
    assert state.active is None

    # Focus for scene_2 is now accepted
    await coord.focus_canvas(scene2, focus_revision=2)
    await asyncio.wait_for(update_task, timeout=2.0)

    # Now presentation is visible and active scene is scene_2
    assert state.presentation_visible is True
    assert state.focused_scene == scene2
    assert state.active == scene2

    event_payload = events._events.get_nowait()
    assert event_payload["event"] == "canvasPresentationChanged"
    assert event_payload["visible"] is True
    assert event_payload["sceneId"] == "scene_2"
    assert event_payload["focusRevision"] == 2
    assert event_payload["source"] == "user"

    assert coord.harness.context.canvas_included is True

    await coord.close()


@pytest.mark.asyncio
async def test_stale_visible_waiter_is_superseded_without_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    scene2 = _create_scene("scene_2", _SAMPLE_SVG_2, "gen_2")
    ws = _synchronized_workspace([scene1, scene2], focused_index=0)
    coord, events, state = _setup_coordinator(workspace=ws)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": False,
                "sceneId": scene1.scene_id,
                "focusRevision": 1,
            }
        )
    )

    waiter = asyncio.create_task(
        coord.receive(
            _client(
                {
                    "event": "canvasPresentationUpdated",
                    "sequence": 1,
                    "visible": True,
                    "sceneId": scene2.scene_id,
                    "focusRevision": 2,
                    "clientOperationId": "op-stale-visible",
                }
            )
        )
    )
    await asyncio.sleep(0)
    with caplog.at_level(logging.INFO, logger="ava_backend.realtime_coordinator"):
        await coord.focus_canvas(scene1, focus_revision=3)
        await asyncio.wait_for(waiter, timeout=0.2)

    assert state.focus_revision == 3
    assert state.focused_scene == scene1
    assert state.presentation_visible is False
    assert events._events.empty()
    assert "presentation_visible_focus_superseded" in caplog.text
    assert "presentation_visible_focus_timeout" not in caplog.text
    await coord.close()


@pytest.mark.asyncio
async def test_visible_focus_acceptance_cannot_be_lost_before_wait_registration() -> (
    None
):
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    scene2 = _create_scene("scene_2", _SAMPLE_SVG_2, "gen_2")
    ws = _synchronized_workspace([scene1, scene2], focused_index=0)
    coord, _, state = _setup_coordinator(workspace=ws)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": False,
                "sceneId": scene1.scene_id,
                "focusRevision": 1,
            }
        )
    )

    waiter = asyncio.create_task(
        coord.receive(
            _client(
                {
                    "event": "canvasPresentationUpdated",
                    "sequence": 1,
                    "visible": True,
                    "sceneId": scene2.scene_id,
                    "focusRevision": 2,
                    "clientOperationId": "op-no-lost-wakeup",
                }
            )
        )
    )
    await coord.focus_canvas(scene2, focus_revision=2)
    await asyncio.wait_for(waiter, timeout=0.2)

    assert state.focus_revision == 2
    assert state.focused_scene == scene2
    assert state.presentation_visible is True
    await coord.close()


@pytest.mark.asyncio
async def test_stale_visible_event_cannot_reactivate_when_focus_returns_to_scene() -> (
    None
):
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    scene2 = _create_scene("scene_2", _SAMPLE_SVG_2, "gen_2")
    ws = _synchronized_workspace([scene1, scene2], focused_index=1)
    coord, events, state = _setup_coordinator(workspace=ws)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": False,
                "sceneId": scene2.scene_id,
                "focusRevision": 1,
            }
        )
    )

    stale = asyncio.create_task(
        coord.receive(
            _client(
                {
                    "event": "canvasPresentationUpdated",
                    "sequence": 1,
                    "visible": True,
                    "sceneId": scene1.scene_id,
                    "focusRevision": 2,
                    "clientOperationId": "op-old-scene-one",
                }
            )
        )
    )
    await asyncio.sleep(0)
    await coord.focus_canvas(scene2, focus_revision=3)
    await asyncio.wait_for(stale, timeout=0.2)
    await coord.focus_canvas(scene1, focus_revision=4)

    assert state.focused_scene == scene1
    assert state.focus_revision == 4
    assert state.presentation_visible is False
    assert events._events.empty()
    await coord.close()


@pytest.mark.asyncio
async def test_focus_storm_supersedes_stale_presentation_without_serial_backlog() -> (
    None
):
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    scene2 = _create_scene("scene_2", _SAMPLE_SVG_2, "gen_2")
    ws = _synchronized_workspace([scene1, scene2], focused_index=0)
    coord, _, state = _setup_coordinator(workspace=ws)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": False,
                "sceneId": scene1.scene_id,
                "focusRevision": 1,
            }
        )
    )
    stale = asyncio.create_task(
        coord.receive(
            _client(
                {
                    "event": "canvasPresentationUpdated",
                    "sequence": 1,
                    "visible": True,
                    "sceneId": scene2.scene_id,
                    "focusRevision": 2,
                    "clientOperationId": "op-storm-stale",
                }
            )
        )
    )
    await asyncio.sleep(0)
    scenes_by_id = {scene.scene_id: scene for scene in (scene1, scene2)}

    await coord.focus_canvas(scene1, focus_revision=2)
    for focus_revision in range(3, 52):
        operation = focus_revision % 4
        if operation == 0:
            resolution = ws.resolve_navigation(CanvasNavigationDirection.NEXT)
            target = (
                scenes_by_id[resolution.scene.scene_id]
                if resolution.scene is not None
                else scene2
            )
        elif operation == 1:
            resolution = ws.resolve_navigation(CanvasNavigationDirection.PREVIOUS)
            target = (
                scenes_by_id[resolution.scene.scene_id]
                if resolution.scene is not None
                else scene1
            )
        elif operation == 2:
            target = scene2
        else:
            target = scene1
        await coord.focus_canvas(target, focus_revision=focus_revision)

    await asyncio.wait_for(stale, timeout=0.2)
    assert state.focus_revision == 51
    assert state.focused_scene == scene1
    assert state.presentation_visible is False
    assert coord._presentation_focus_changed is not None

    await asyncio.wait_for(
        coord.receive(
            _client(
                {
                    "event": "canvasPresentationUpdated",
                    "sequence": 2,
                    "visible": True,
                    "sceneId": scene1.scene_id,
                    "focusRevision": 51,
                    "clientOperationId": "op-storm-final",
                }
            )
        ),
        timeout=0.2,
    )
    assert state.presentation_visible is True
    await coord.close()


@pytest.mark.asyncio
async def test_model_manage_canvas_presentation_show_and_ack(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = SvgCanvasWorkspace()
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    scene2 = _create_scene("scene_2", _SAMPLE_SVG_2, "gen_2")
    ws.restore_focused(scene2)
    ws.restore_focused(scene1)

    provider = _ModelPresentationProvider(
        _plan(),
        action=ManageCanvasPresentationAction.SHOW,
        scene_id="scene_2",
    )

    coord, events, state = _setup_coordinator(workspace=ws, realtime_provider=provider)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": False,
            }
        )
    )

    cmd_received = asyncio.Event()
    cmd_event: CanvasPresentationCommandEventV4 | None = None

    async def on_frame(event):
        nonlocal cmd_event
        if isinstance(event, CanvasPresentationCommandEventV4):
            cmd_event = event
            cmd_received.set()

    with caplog.at_level(logging.INFO, logger="ava_backend.realtime_coordinator"):
        turn_task = asyncio.create_task(_consume_turn_frames(coord, on_frame=on_frame))

        await asyncio.wait_for(cmd_received.wait(), timeout=2.0)
        assert cmd_event is not None
        assert cmd_event.action == "show"
        assert cmd_event.scene_id == "scene_2"

        # Client acknowledges command
        await coord.receive(
            _client(
                {
                    "event": "canvasPresentationAcknowledged",
                    "sequence": 1,
                    "commandId": cmd_event.command_id,
                    "applied": True,
                    "sceneId": "scene_2",
                    "focusRevision": 2,
                }
            )
        )

        await asyncio.wait_for(turn_task, timeout=5.0)

    assert provider.completed_output is not None
    assert provider.completion_count == 1
    assert provider.completed_output.status == ManageCanvasPresentationStatus.ACCEPTED
    assert provider.completed_output.action == ManageCanvasPresentationAction.SHOW
    assert provider.completed_output.scene_id == "scene_2"

    assert state.presentation_visible is True
    assert state.focused_scene == scene2
    assert state.active == scene2

    completion_records = [
        record.ava_context
        for record in caplog.records
        if record.getMessage().startswith("canvas_control_completion")
    ]
    assert len(completion_records) == 1
    completion = completion_records[0]
    assert completion["sessionId"] == SESSION_ID
    assert completion["turnId"] == TURN_ID
    assert completion["providerResponseId"] == "resp-presentation-tool"
    assert completion["functionCallId"] == "call_pres_1"
    assert completion["operation"] == "show"
    assert completion["direction"] is None
    assert completion["requestedSceneId"] == "scene_2"
    assert completion["status"] == "accepted"
    assert completion["changed"] is True
    assert completion["reason"] is None
    assert completion["resolvedSceneId"] == "scene_2"
    assert completion["sceneRevision"] == scene2.revision
    assert completion["position"] == 1
    assert completion["total"] == 2
    assert completion["workspaceEpoch"] == ws.workspace_epoch
    assert completion["workspaceRevision"] == ws.workspace_revision
    assert completion["focusRevision"] == 2
    assert completion["presentationCommandId"] == cmd_event.command_id
    assert completion["supersessionResult"] == "applied"
    assert completion["resultingContextBuildId"] is not None
    assert completion["continuationResponseId"] is None

    await coord.close()


@pytest.mark.asyncio
async def test_canvas_continuation_interrupt_focus_storm_rearms_without_backlog(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    scene2 = _create_scene("scene_2", _SAMPLE_SVG_2, "gen_2")
    workspace = _synchronized_workspace([scene1, scene2], focused_index=0)
    provider = _StressPresentationProvider(_plan())
    coord, _events, state = _setup_coordinator(
        workspace=workspace,
        realtime_provider=provider,
        persistent=True,
    )
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": False,
            }
        )
    )
    frames = coord.frames()

    async def next_control(event_type):  # type: ignore[no-untyped-def]
        while True:
            frame = await anext(frames)
            if isinstance(frame, AvaServerControlFrameV4) and isinstance(
                frame.event, event_type
            ):
                return frame.event

    try:
        command = await asyncio.wait_for(
            next_control(CanvasPresentationCommandEventV4),
            timeout=2.0,
        )
        assert command.scene_id == scene2.scene_id
        await coord.receive(
            _client(
                {
                    "event": "canvasPresentationAcknowledged",
                    "sequence": 1,
                    "commandId": command.command_id,
                    "applied": True,
                    "sceneId": scene2.scene_id,
                    "focusRevision": 1,
                }
            )
        )
        await asyncio.wait_for(provider.terminal_observed.wait(), timeout=2.0)

        # Keep the continuation active while canonical focus changes rapidly.
        for revision in range(2, 52):
            await coord.focus_canvas(
                scene1 if revision % 2 == 0 else scene2,
                focus_revision=revision,
            )

        stale_visible = asyncio.create_task(
            coord.receive(
                _client(
                    {
                        "event": "canvasPresentationUpdated",
                        "sequence": 2,
                        "visible": True,
                        "sceneId": scene1.scene_id,
                        "focusRevision": 52,
                        "clientOperationId": "op-stress-stale-visible",
                    }
                )
            )
        )
        await asyncio.sleep(0)
        with caplog.at_level(
            logging.INFO,
            logger="ava_backend.realtime_coordinator",
        ):
            await coord.focus_canvas(scene2, focus_revision=53)
            await asyncio.wait_for(stale_visible, timeout=0.2)

        first_metadata = await asyncio.wait_for(
            next_control(AudioMetadataEventV4),
            timeout=2.0,
        )
        assert first_metadata.turn_id == TURN_ID
        await coord.receive(
            _client(
                {
                    "event": "playbackStarted",
                    "sequence": 3,
                    "turnId": TURN_ID,
                    "streamId": STREAM_ID,
                    "renderedSampleCount": 0,
                }
            )
        )
        provider.emit_input_event(
            RealtimeProviderInputEvent(
                event_type="input_audio_buffer.speech_started",
                item_id="item-stress-next-child",
                audio_start_ms=900,
            )
        )
        interruption = await asyncio.wait_for(
            next_control(InterruptRequestedEventV4),
            timeout=2.0,
        )
        assert interruption.turn_id == TURN_ID
        await coord.receive(
            _client(
                {
                    "event": "playbackStopped",
                    "sequence": 4,
                    "turnId": TURN_ID,
                    "streamId": STREAM_ID,
                    "renderedSampleCount": 0,
                    "reason": "providerSpeechStarted",
                }
            )
        )
        await coord.receive(
            _client(
                {
                    "event": "cancelTurn",
                    "sequence": 5,
                    "turnId": TURN_ID,
                    "requestId": "cancel-stress-terminal-race",
                    "reason": "providerSpeechStarted",
                }
            )
        )

        cancelled = await asyncio.wait_for(
            next_control(TurnCancelledEventV4),
            timeout=2.0,
        )
        recovered_metadata = await asyncio.wait_for(
            next_control(AudioMetadataEventV4),
            timeout=2.0,
        )

        assert cancelled.turn_id == TURN_ID
        assert recovered_metadata.turn_id == "turn-pres-next"
        assert provider.cancel_settlement_count == 1
        assert provider.cancelled_turn_ids == [TURN_ID]
        assert provider.requests[-1].turn_id == "turn-pres-next"
        assert provider._active_turn_id is None
        assert provider._active_response_turn_id is None
        assert state.focused_scene == scene2
        assert state.focus_revision == 53
        assert not coord._pending_presentation_commands
        assert "presentation_visible_focus_superseded" in caplog.text
        assert "presentation_visible_focus_timeout" not in caplog.text
    finally:
        await frames.aclose()
        await coord.close()


@pytest.mark.asyncio
async def test_canvas_control_continuation_waits_for_resulting_context_ack() -> None:
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    scene2 = _create_scene("scene_2", _SAMPLE_SVG_2, "gen_2")
    ws = _synchronized_workspace([scene1, scene2], focused_index=0)
    provider = _ModelPresentationProvider(
        _plan(),
        action=ManageCanvasPresentationAction.SHOW,
        scene_id="scene_2",
    )
    coord, events, state = _setup_coordinator(
        workspace=ws,
        realtime_provider=provider,
    )
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": True,
                "sceneId": "scene_1",
                "focusRevision": 1,
            }
        )
    )
    events._events.get_nowait()
    provider.defer_acknowledgement = True
    call = RealtimeManageCanvasPresentationToolCall(
        call_id="call-context-fence",
        request=ManageCanvasPresentationRequest(
            operation=ManageCanvasPresentationAction.NAVIGATE,
            direction=ManageCanvasNavigationDirection.NEXT,
        ),
    )
    task = asyncio.create_task(
        coord._run_manage_canvas_presentation_tool(
            call, state=unittest.mock.MagicMock()
        )
    )
    command = await events.next_payload()
    await coord.receive(
        _client(
            {
                "event": "canvasPresentationAcknowledged",
                "sequence": 1,
                "commandId": command["commandId"],
                "applied": True,
                "sceneId": "scene_2",
                "focusRevision": 2,
            }
        )
    )

    await asyncio.sleep(0)
    required_build = provider.desired_context_build_id
    assert required_build is not None
    assert provider.is_context_acknowledged(required_build) is False
    assert provider.completed_output is None
    assert task.done() is False
    assert state.focused_scene == scene2

    provider.acknowledge_context(required_build)
    await task
    assert provider.completed_output is not None
    assert provider.completion_count == 1
    assert provider.completed_output.model_dump(by_alias=True, exclude_none=True) == {
        "status": "accepted",
        "operation": "navigate",
        "direction": "next",
        "changed": True,
        "visible": True,
        "sceneId": "scene_2",
        "sceneRevision": 1,
        "position": 2,
        "total": 2,
        "hasPrevious": True,
        "hasNext": False,
        "workspaceEpoch": 9,
        "workspaceRevision": 4,
    }
    await coord.close()


@pytest.mark.asyncio
async def test_canvas_control_continuation_tracks_superseding_context_ack() -> None:
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    scene2 = _create_scene("scene_2", _SAMPLE_SVG_2, "gen_2")
    ws = _synchronized_workspace([scene1, scene2], focused_index=0)
    provider = _ModelPresentationProvider(
        _plan(),
        action=ManageCanvasPresentationAction.SHOW,
        scene_id="scene_2",
    )
    coord, events, _ = _setup_coordinator(
        workspace=ws,
        realtime_provider=provider,
    )
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": True,
                "sceneId": "scene_1",
                "focusRevision": 1,
            }
        )
    )
    events._events.get_nowait()
    provider.defer_acknowledgement = True
    task = asyncio.create_task(
        coord._run_manage_canvas_presentation_tool(
            RealtimeManageCanvasPresentationToolCall(
                call_id="call-context-latest",
                request=ManageCanvasPresentationRequest(
                    operation=ManageCanvasPresentationAction.NAVIGATE,
                    direction=ManageCanvasNavigationDirection.NEXT,
                ),
            ),
            state=unittest.mock.MagicMock(),
        )
    )
    command = await events.next_payload()
    await coord.receive(
        _client(
            {
                "event": "canvasPresentationAcknowledged",
                "sequence": 1,
                "commandId": command["commandId"],
                "applied": True,
                "sceneId": "scene_2",
                "focusRevision": 2,
            }
        )
    )
    resulting_build = provider.desired_context_build_id
    assert resulting_build is not None

    superseding_context = replace(coord.harness.context, build_id="ctx-superseding")
    await provider.prepare(
        harness=replace(
            coord.harness,
            context=superseding_context,
            instructions=f"{coord.harness.instructions}\nSuperseding context test.",
        )
    )
    provider.acknowledge_context(resulting_build)
    await asyncio.sleep(0)
    assert task.done() is False
    assert provider.completed_output is None

    provider.acknowledge_context("ctx-superseding")
    await task
    assert provider.completed_output is not None
    assert provider.completion_count == 1
    assert provider.completed_output.scene_id == "scene_2"
    await coord.close()


@pytest.mark.asyncio
async def test_boundary_navigation_completes_without_context_wait() -> None:
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    scene2 = _create_scene("scene_2", _SAMPLE_SVG_2, "gen_2")
    ws = _synchronized_workspace([scene1, scene2], focused_index=1)
    provider = _ModelPresentationProvider(
        _plan(),
        action=ManageCanvasPresentationAction.SHOW,
        scene_id="scene_2",
    )
    coord, _, state = _setup_coordinator(workspace=ws, realtime_provider=provider)
    state.set_presentation_visible(True)
    provider.defer_acknowledgement = True

    await coord._run_manage_canvas_presentation_tool(
        RealtimeManageCanvasPresentationToolCall(
            call_id="call-boundary",
            request=ManageCanvasPresentationRequest(
                operation=ManageCanvasPresentationAction.NAVIGATE,
                direction=ManageCanvasNavigationDirection.NEXT,
            ),
        ),
        state=unittest.mock.MagicMock(),
    )

    assert provider.completed_output is not None
    assert provider.completion_count == 1
    assert provider.completed_output.model_dump(by_alias=True, exclude_none=True) == {
        "status": "rejected",
        "operation": "navigate",
        "direction": "next",
        "changed": False,
        "visible": True,
        "sceneId": "scene_2",
        "sceneRevision": 1,
        "position": 2,
        "total": 2,
        "hasPrevious": True,
        "hasNext": False,
        "workspaceEpoch": 9,
        "workspaceRevision": 4,
        "reason": "endOfWorkspace",
    }
    await coord.close()


@pytest.mark.asyncio
async def test_show_current_scene_returns_exact_accepted_no_op() -> None:
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    scene2 = _create_scene("scene_2", _SAMPLE_SVG_2, "gen_2")
    ws = _synchronized_workspace([scene1, scene2], focused_index=0)
    provider = _ModelPresentationProvider(
        _plan(),
        action=ManageCanvasPresentationAction.SHOW,
        scene_id="scene_1",
    )
    coord, events, _ = _setup_coordinator(workspace=ws, realtime_provider=provider)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": True,
                "sceneId": "scene_1",
                "focusRevision": 1,
            }
        )
    )
    events._events.get_nowait()
    task = asyncio.create_task(
        coord._run_manage_canvas_presentation_tool(
            RealtimeManageCanvasPresentationToolCall(
                call_id="call-show-no-op",
                request=ManageCanvasPresentationRequest(
                    operation=ManageCanvasPresentationAction.SHOW,
                    scene_id="scene_1",
                ),
            ),
            state=unittest.mock.MagicMock(),
        )
    )
    command = await events.next_payload()
    await coord.receive(
        _client(
            {
                "event": "canvasPresentationAcknowledged",
                "sequence": 1,
                "commandId": command["commandId"],
                "applied": True,
                "sceneId": "scene_1",
                "focusRevision": 1,
            }
        )
    )
    await task

    assert provider.completed_output is not None
    assert provider.completed_output.model_dump(by_alias=True, exclude_none=True) == {
        "status": "accepted",
        "operation": "show",
        "changed": False,
        "visible": True,
        "sceneId": "scene_1",
        "sceneRevision": 1,
        "position": 1,
        "total": 2,
        "hasPrevious": False,
        "hasNext": True,
        "workspaceEpoch": 9,
        "workspaceRevision": 4,
    }
    await coord.close()


@pytest.mark.asyncio
async def test_model_manage_canvas_presentation_hide_and_ack() -> None:
    ws = SvgCanvasWorkspace()
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    ws.restore_focused(scene1)

    provider = _ModelPresentationProvider(
        _plan(),
        action=ManageCanvasPresentationAction.HIDE,
    )

    coord, events, state = _setup_coordinator(workspace=ws, realtime_provider=provider)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": True,
                "sceneId": "scene_1",
                "focusRevision": 1,
            }
        )
    )
    _ = events._events.get_nowait()  # consume presentationChanged from restore

    cmd_received = asyncio.Event()
    cmd_event: CanvasPresentationCommandEventV4 | None = None

    async def on_frame(event):
        nonlocal cmd_event
        if isinstance(event, CanvasPresentationCommandEventV4):
            cmd_event = event
            cmd_received.set()

    turn_task = asyncio.create_task(_consume_turn_frames(coord, on_frame=on_frame))

    await asyncio.wait_for(cmd_received.wait(), timeout=2.0)
    assert cmd_event is not None
    assert cmd_event.action == "hide"
    assert cmd_event.scene_id is None

    # Client acknowledges command
    await coord.receive(
        _client(
            {
                "event": "canvasPresentationAcknowledged",
                "sequence": 1,
                "commandId": cmd_event.command_id,
                "applied": True,
            }
        )
    )

    await asyncio.wait_for(turn_task, timeout=5.0)

    assert provider.completed_output is not None
    assert provider.completed_output.status == ManageCanvasPresentationStatus.ACCEPTED
    assert provider.completed_output.action == ManageCanvasPresentationAction.HIDE

    assert state.presentation_visible is False
    assert state.active is None

    await coord.close()


@pytest.mark.asyncio
async def test_model_manage_canvas_presentation_unknown_scene_rejected() -> None:
    ws = SvgCanvasWorkspace()
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    ws.restore_focused(scene1)

    provider = _ModelPresentationProvider(
        _plan(),
        action=ManageCanvasPresentationAction.SHOW,
        scene_id="scene_ghost",
    )

    coord, events, state = _setup_coordinator(workspace=ws, realtime_provider=provider)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": False,
            }
        )
    )

    turn_task = asyncio.create_task(_consume_turn_frames(coord))

    await asyncio.wait_for(turn_task, timeout=5.0)

    assert provider.completed_output is not None
    assert provider.completed_output.status == ManageCanvasPresentationStatus.REJECTED
    assert "retained visuals" in provider.completed_output.reason

    assert state.presentation_visible is False

    await coord.close()


@pytest.mark.asyncio
async def test_user_physical_intent_supersedes_pending_model_presentation_command() -> (
    None
):
    ws = SvgCanvasWorkspace()
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    scene2 = _create_scene("scene_2", _SAMPLE_SVG_2, "gen_2")
    ws.restore_focused(scene2)
    ws.restore_focused(scene1)

    provider = _ModelPresentationProvider(
        _plan(),
        action=ManageCanvasPresentationAction.SHOW,
        scene_id="scene_2",
    )

    coord, events, state = _setup_coordinator(workspace=ws, realtime_provider=provider)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": True,
                "sceneId": "scene_1",
                "focusRevision": 1,
            }
        )
    )
    _ = events._events.get_nowait()

    cmd_received = asyncio.Event()
    cmd_event: CanvasPresentationCommandEventV4 | None = None

    async def on_frame(event):
        nonlocal cmd_event
        if isinstance(event, CanvasPresentationCommandEventV4):
            cmd_event = event
            cmd_received.set()

    turn_task = asyncio.create_task(_consume_turn_frames(coord, on_frame=on_frame))

    await asyncio.wait_for(cmd_received.wait(), timeout=2.0)

    # Before acking the command, user closes panel directly
    await coord.receive(
        _client(
            {
                "event": "canvasPresentationUpdated",
                "sequence": 1,
                "visible": False,
                "clientOperationId": "op_user_close",
            }
        )
    )

    # Model tool should have been rejected due to supersession
    await asyncio.wait_for(turn_task, timeout=5.0)

    assert provider.completed_output is not None
    assert provider.completed_output.status == ManageCanvasPresentationStatus.REJECTED
    assert "superseded" in provider.completed_output.reason

    assert state.presentation_visible is False

    await coord.close()


@pytest.mark.asyncio
async def test_newer_manual_focus_supersedes_pending_model_show_and_stale_ack() -> None:
    ws = SvgCanvasWorkspace()
    scene1 = _create_scene("scene_1", _SAMPLE_SVG_1, "gen_1")
    scene2 = _create_scene("scene_2", _SAMPLE_SVG_2, "gen_2")
    scene3 = _create_scene("scene_3", _SAMPLE_SVG_1, "gen_3")
    ws.restore_focused(scene3)
    ws.restore_focused(scene2)
    ws.restore_focused(scene1)

    provider = _ModelPresentationProvider(
        _plan(),
        action=ManageCanvasPresentationAction.SHOW,
        scene_id="scene_2",
    )
    coord, events, state = _setup_coordinator(workspace=ws, realtime_provider=provider)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": True,
                "sceneId": "scene_1",
                "focusRevision": 1,
            }
        )
    )
    _ = events._events.get_nowait()

    command_received = asyncio.Event()
    command_event: CanvasPresentationCommandEventV4 | None = None

    async def on_frame(event):
        nonlocal command_event
        if isinstance(event, CanvasPresentationCommandEventV4):
            command_event = event
            command_received.set()

    turn_task = asyncio.create_task(_consume_turn_frames(coord, on_frame=on_frame))
    await asyncio.wait_for(command_received.wait(), timeout=2.0)
    assert command_event is not None

    await coord.focus_canvas(scene3, focus_revision=2)
    await asyncio.wait_for(turn_task, timeout=5.0)

    assert provider.completed_output is not None
    assert provider.completed_output.model_dump(by_alias=True, exclude_none=True) == {
        "status": "rejected",
        "operation": "show",
        "changed": False,
        "visible": True,
        "sceneId": "scene_3",
        "sceneRevision": 1,
        "position": 1,
        "total": 3,
        "hasPrevious": False,
        "hasNext": True,
        "workspaceEpoch": 0,
        "workspaceRevision": 0,
        "reason": "superseded by newer client focus",
    }
    assert state.focused_scene == scene3
    assert state.presented_scene == scene3

    await coord.receive(
        _client(
            {
                "event": "canvasPresentationAcknowledged",
                "sequence": coord._next_client_sequence,
                "commandId": command_event.command_id,
                "applied": True,
                "sceneId": "scene_2",
                "focusRevision": 1,
            }
        )
    )
    assert state.focused_scene == scene3
    assert state.presented_scene == scene3

    await coord.close()


class _ReviseSpecialist:
    def __init__(self) -> None:
        self.requests: list[SvgCanvasSpecialistRequest] = []

    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
        *,
        draft_sink: object | None = None,
    ) -> SvgCanvasSpecialistGeneration:
        del draft_sink
        self.requests.append(request)
        revised_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" data-ava-interaction-mode="none">
<title>Scene 1 Revised</title><desc>Revised scene 1</desc>
<rect id="rect_1" x="20" y="20" width="120" height="120"/>
</svg>"""
        doc = SvgDocumentValidator().validate(revised_svg)
        return SvgCanvasSpecialistGeneration(
            generation_id=request.generation_id,
            document=doc,
        )

    async def cancel(self, *, generation_id: str) -> None:
        pass

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_hidden_focused_scene_supports_revise_focused() -> None:
    ws = SvgCanvasWorkspace()
    scene_a = _create_scene("scene_A", _SAMPLE_SVG_1, "gen_A")
    ws.restore_focused(scene_a)
    state = SvgCanvasSessionState(workspace=ws)
    assert state.focused_scene == scene_a
    assert state.presentation_visible is False
    assert state.active is None
    assert state.presented_scene is None

    specialist = _ReviseSpecialist()
    events = ProtocolV4CanvasEventSink()
    jobs = SvgCanvasJobCoordinator(
        session_id=SESSION_ID,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=state,
        documents=SvgCanvasDocumentStore(),
        event_sink=events,
        generation_id_factory=lambda: "gen_A_rev2",
    )

    # 1. reviseFocused requested while presentation is hidden
    async def _complete(*_):
        pass

    output = await jobs.accept_tool_call(
        source_turn_id="turn-revise",
        call=RealtimeCanvasToolCall(
            call_id="call_revise_1",
            request=InteractiveCanvasRequest.model_validate(
                {
                    "operation": "reviseFocused",
                    "brief": "Make the rectangle larger",
                    "interactionMode": "none",
                }
            ),
        ),
        complete_tool=_complete,
    )
    assert output.status == InteractiveCanvasToolStatusV4.ACCEPTED
    assert output.generation_id == "gen_A_rev2"

    await jobs.wait_for_background_work()

    # Verify specialist received Scene A details
    assert len(specialist.requests) == 1
    req = specialist.requests[0]
    assert req.current_scene_id == "scene_A"
    assert req.current_revision == 1
    assert req.current_svg == scene_a.document.svg_text

    # Verify candidate revision created against Scene A
    candidate = state.candidate
    assert candidate is not None
    assert candidate.scene_id == "scene_A"
    assert candidate.revision == 2
    assert candidate.base_scene_id == "scene_A"
    assert candidate.base_revision == 1

    # Invariants before presentation:
    # - Scene A remains focused
    # - Presentation remains hidden
    # - active / presented_scene is None
    # - No false active_visual before presentation
    assert state.focused_scene == scene_a
    assert state.presentation_visible is False
    assert state.active is None
    assert state.presented_scene is None
    context = state.render_realtime_context()
    assert "<active_visual" not in context
    assert "<retained_visuals" in context
    assert 'id="scene_A"' in context

    # Retained scene history preserved
    assert state.workspace.scene("scene_A") is not None

    # Explicitly showing presentation reveals active_visual
    state.set_presentation_visible(True)
    assert state.presentation_visible is True
    assert state.active == scene_a
    assert state.presented_scene == scene_a
    assert "<active_visual" in state.render_realtime_context()

    await jobs.close()


def test_manage_canvas_presentation_request_validation() -> None:
    with pytest.raises(
        ValidationError, match="scene_id is required when action is show"
    ):
        ManageCanvasPresentationRequest.model_validate({"action": "show"})

    with pytest.raises(
        ValidationError, match="scene_id is forbidden when action is hide"
    ):
        ManageCanvasPresentationRequest.model_validate(
            {"action": "hide", "sceneId": "scene-A"}
        )

    req_show = ManageCanvasPresentationRequest.model_validate(
        {"action": "show", "sceneId": "scene-A"}
    )
    assert req_show.action == ManageCanvasPresentationAction.SHOW
    assert req_show.scene_id == "scene-A"

    req_hide = ManageCanvasPresentationRequest.model_validate({"action": "hide"})
    assert req_hide.action == ManageCanvasPresentationAction.HIDE
    assert req_hide.scene_id is None


def test_manage_canvas_presentation_tool_completion_signatures_contract() -> None:
    import inspect
    from ava_backend.providers.openai_realtime_provider import (
        OpenAIRealtimeProviderSession,
    )
    from ava_backend.realtime.provider_session import RealtimeProviderSession

    proto_sig = inspect.signature(
        RealtimeProviderSession.complete_manage_canvas_presentation_tool
    )
    openai_sig = inspect.signature(
        OpenAIRealtimeProviderSession.complete_manage_canvas_presentation_tool
    )
    fake_sig = inspect.signature(
        DeterministicRealtimePlanProvider.complete_manage_canvas_presentation_tool
    )
    test_stub_sig = inspect.signature(
        _ModelPresentationProvider.complete_manage_canvas_presentation_tool
    )

    for name, sig in [
        ("RealtimeProviderSession", proto_sig),
        ("OpenAIRealtimeProviderSession", openai_sig),
        ("DeterministicRealtimePlanProvider", fake_sig),
        ("_ModelPresentationProvider", test_stub_sig),
    ]:
        assert "harness" not in sig.parameters, f"{name} must not accept harness"
        assert "turn_id" in sig.parameters, f"{name} must accept turn_id"
        assert "call_id" in sig.parameters, f"{name} must accept call_id"
        assert "output" in sig.parameters, f"{name} must accept output"
        for param_name in ("turn_id", "call_id", "output"):
            assert sig.parameters[param_name].kind == inspect.Parameter.KEYWORD_ONLY


@pytest.mark.asyncio
async def test_coordinator_manage_canvas_presentation_against_openai_autospec() -> None:
    import unittest.mock
    from ava_backend.providers.openai_realtime_provider import (
        OpenAIRealtimeProviderSession,
    )
    from ava_backend.realtime.tool_contract import (
        RealtimeManageCanvasPresentationToolCall,
    )

    mock_provider = unittest.mock.create_autospec(
        OpenAIRealtimeProviderSession, instance=True
    )
    coord, events, state = _setup_coordinator(realtime_provider=mock_provider)

    call = RealtimeManageCanvasPresentationToolCall(
        call_id="call-mock-1",
        request=ManageCanvasPresentationRequest(
            action=ManageCanvasPresentationAction.HIDE
        ),
    )

    planning_state = unittest.mock.MagicMock()

    async def _ack():
        await asyncio.sleep(0.05)
        for cmd in list(coord._pending_presentation_commands.values()):
            if not cmd.future.done():
                cmd.future.set_result(
                    ManageCanvasPresentationToolOutputV4(
                        status=ManageCanvasPresentationStatus.ACCEPTED,
                        action=ManageCanvasPresentationAction.HIDE,
                    )
                )

    ack_task = asyncio.create_task(_ack())
    # This must complete cleanly without raising TypeError: unexpected keyword argument 'harness'
    await coord._run_manage_canvas_presentation_tool(call, state=planning_state)
    await ack_task

    mock_provider.complete_manage_canvas_presentation_tool.assert_awaited_once()
    _, kwargs = mock_provider.complete_manage_canvas_presentation_tool.call_args
    assert "harness" not in kwargs
    assert kwargs["turn_id"] == TURN_ID
    assert kwargs["call_id"] == "call-mock-1"
    assert kwargs["output"].status == ManageCanvasPresentationStatus.ACCEPTED
    assert kwargs["output"].action == ManageCanvasPresentationAction.HIDE

    await coord.close()


@pytest.mark.asyncio
async def test_semantic_hide_and_show_retained_scene_end_to_end() -> None:
    ws = SvgCanvasWorkspace()
    heart = _create_scene("scene_heart", _SAMPLE_SVG_1, "gen_heart")
    moon = _create_scene("scene_moon", _SAMPLE_SVG_2, "gen_moon")
    ws.restore_focused(moon)
    ws.restore_focused(heart)

    # Initially Heart is visible
    provider = _ModelPresentationProvider(
        _plan(),
        action=ManageCanvasPresentationAction.HIDE,
    )
    coord, events, state = _setup_coordinator(workspace=ws, realtime_provider=provider)
    await coord.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)

    # 1. Startup: clientReady with Heart visible
    await coord.receive(
        _client(
            {
                "event": "clientReady",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
                "canvasPanelVisible": True,
                "sceneId": "scene_heart",
                "focusRevision": 1,
            }
        )
    )
    _ = events._events.get_nowait()
    assert state.presentation_visible is True
    assert state.active == heart
    assert coord.harness.context.canvas_included is True

    # Single consumer task for persistent coordinator frame stream
    hide_cmd_received = asyncio.Event()
    hide_cmd_event: CanvasPresentationCommandEventV4 | None = None
    show_cmd_received = asyncio.Event()
    show_cmd_event: CanvasPresentationCommandEventV4 | None = None

    async def on_frame(event):
        nonlocal hide_cmd_event, show_cmd_event
        if isinstance(event, CanvasPresentationCommandEventV4):
            if event.action == "hide":
                hide_cmd_event = event
                hide_cmd_received.set()
            elif event.action == "show":
                show_cmd_event = event
                show_cmd_received.set()

    consumer_task = asyncio.create_task(_consume_turn_frames(coord, on_frame=on_frame))

    # 2. Turn 1: Model calls manage_canvas_presentation(hide)
    await asyncio.wait_for(hide_cmd_received.wait(), timeout=2.0)
    assert hide_cmd_event is not None
    assert hide_cmd_event.action == "hide"

    # Client ACKs hide
    await coord.receive(
        _client(
            {
                "event": "canvasPresentationAcknowledged",
                "sequence": 1,
                "commandId": hide_cmd_event.command_id,
                "applied": True,
            }
        )
    )
    await asyncio.wait_for(provider.tool_completed.wait(), timeout=5.0)

    # Verify tool completed without TypeError and state updated
    assert provider.completed_output is not None
    assert provider.completed_output.status == ManageCanvasPresentationStatus.ACCEPTED
    assert provider.completed_output.action == ManageCanvasPresentationAction.HIDE
    assert state.presentation_visible is False
    assert state.focused_scene == heart
    assert state.active is None
    assert coord.harness.context.canvas_included is False

    # 3. Model calls manage_canvas_presentation(show, Moon)
    provider.action = ManageCanvasPresentationAction.SHOW
    provider.scene_id = "scene_moon"
    provider.tool_completed.clear()
    provider.completed_output = None

    # Trigger show tool call on coordinator
    show_call = RealtimeManageCanvasPresentationToolCall(
        call_id="call-show-moon",
        request=ManageCanvasPresentationRequest(
            action=ManageCanvasPresentationAction.SHOW, scene_id="scene_moon"
        ),
    )
    planning_state = unittest.mock.MagicMock()
    show_tool_task = asyncio.create_task(
        coord._run_manage_canvas_presentation_tool(show_call, state=planning_state)
    )

    await asyncio.wait_for(show_cmd_received.wait(), timeout=2.0)
    assert show_cmd_event is not None
    assert show_cmd_event.action == "show"
    assert show_cmd_event.scene_id == "scene_moon"

    # Client ACKs show Moon
    await coord.receive(
        _client(
            {
                "event": "canvasPresentationAcknowledged",
                "sequence": 2,
                "commandId": show_cmd_event.command_id,
                "applied": True,
                "sceneId": "scene_moon",
                "focusRevision": 2,
            }
        )
    )
    await asyncio.wait_for(show_tool_task, timeout=5.0)

    # Verify tool completed and Moon is presented
    assert provider.completed_output is not None
    assert provider.completed_output.status == ManageCanvasPresentationStatus.ACCEPTED
    assert provider.completed_output.action == ManageCanvasPresentationAction.SHOW
    assert provider.completed_output.scene_id == "scene_moon"
    assert state.presentation_visible is True
    assert state.focused_scene == moon
    assert state.active == moon
    assert coord.harness.context.canvas_included is True
    assert (
        '<active_visual scene_id="scene_moon" revision="1"'
        in coord.harness.instructions
    )
    assert (
        '<visual scene_id="scene_moon" revision="1" focused="true" presented="true"'
        in coord.harness.instructions
    )

    # 4. A later physical focus wins and immediately becomes presented because
    # canonical presentation visibility remains true.
    await coord.focus_canvas(heart, focus_revision=3)
    assert state.focused_scene == heart
    assert state.presented_scene == heart
    assert provider.active_context_build_id == coord.harness.context.build_id
    assert (
        '<active_visual scene_id="scene_heart" revision="1"'
        in coord.harness.instructions
    )

    # 5. Complete a deterministic new-scene lifecycle. The immediate tool
    # acceptance may have had only generationId; canonical identity is exposed
    # by the post-presentation context refresh.
    generation_d = SvgCanvasSpecialistGeneration(
        generation_id="generation-latest-D",
        document=SvgDocumentValidator().validate(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
            'data-ava-interaction-mode="none"><title>Latest diagram D</title>'
            "<desc>The newest retained diagram.</desc>"
            '<circle id="latest-d" cx="300" cy="300" r="80"/></svg>'
        ),
    )
    candidate_d = state.stage_generation(
        generation_d,
        source_turn_id="turn-latest-D",
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=state.workspace.reset_epoch,
    )
    state.mark_document_prepared(
        generation_id=generation_d.generation_id,
        svg_sha256=generation_d.document.svg_sha256,
        manifest_sha256=generation_d.document.manifest_sha256,
        renderer_capability_version="ava-svg-renderer-v2",
        validator_version="ava-svg-validator-v2",
    )
    state.schedule_presentation(
        generation_id=generation_d.generation_id,
        presentation_sample_offset=4_800,
    )
    await coord.receive(
        _client(
            {
                "event": "canvasPresented",
                "sequence": coord._next_client_sequence,
                "generationId": candidate_d.generation_id,
                "sceneId": candidate_d.scene_id,
                "sceneRevision": candidate_d.revision,
                "svgSha256": generation_d.document.svg_sha256,
                "manifestSha256": generation_d.document.manifest_sha256,
                "firstRenderedSample": 4_800,
            }
        )
    )
    assert state.presented_scene is not None
    assert state.presented_scene.scene_id == candidate_d.scene_id
    assert provider.active_context_build_id == coord.harness.context.build_id
    assert (
        f'<active_visual scene_id="{candidate_d.scene_id}" revision="1"'
        in coord.harness.instructions
    )
    assert f'<visual scene_id="{candidate_d.scene_id}"' in coord.harness.instructions
    assert 'recent_rank="1"' in coord.harness.instructions

    # 6. Hide, then reuse the latest canonical scene through the presentation
    # tool. No generation request is involved.
    await coord.receive(
        _client(
            {
                "event": "canvasPresentationUpdated",
                "sequence": coord._next_client_sequence,
                "visible": False,
                "clientOperationId": "op-hide-latest-D",
            }
        )
    )
    assert state.presented_scene is None
    assert provider.active_context_build_id == coord.harness.context.build_id
    assert (
        '<canvas_presentation trust="validated_current_state" visible="false"/>'
        in coord.harness.instructions
    )
    assert "<active_visual" not in coord.harness.context.content

    latest_show_call = RealtimeManageCanvasPresentationToolCall(
        call_id="call-show-latest-D",
        request=ManageCanvasPresentationRequest(
            action=ManageCanvasPresentationAction.SHOW,
            scene_id=candidate_d.scene_id,
        ),
    )
    latest_show_task = asyncio.create_task(
        coord._run_manage_canvas_presentation_tool(
            latest_show_call,
            state=unittest.mock.MagicMock(),
        )
    )
    for _ in range(20):
        if coord._pending_presentation_commands:
            break
        await asyncio.sleep(0)
    latest_command = next(iter(coord._pending_presentation_commands.values()))
    await coord.receive(
        _client(
            {
                "event": "canvasPresentationAcknowledged",
                "sequence": coord._next_client_sequence,
                "commandId": latest_command.command_id,
                "applied": True,
                "sceneId": candidate_d.scene_id,
                "focusRevision": state.focus_revision,
            }
        )
    )
    await asyncio.wait_for(latest_show_task, timeout=5.0)
    assert state.presented_scene is not None
    assert state.presented_scene.scene_id == candidate_d.scene_id

    await coord.receive(
        _client(
            {
                "event": "canvasPresentationUpdated",
                "sequence": coord._next_client_sequence,
                "visible": False,
                "clientOperationId": "op-final-hide",
            }
        )
    )
    assert state.presented_scene is None
    assert (
        '<canvas_presentation trust="validated_current_state" visible="false"/>'
        in coord.harness.instructions
    )
    assert "<active_visual" not in coord.harness.context.content

    await coord.close()
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass


def test_retained_catalog_budget_prioritizes_presented_and_latest_scene() -> None:
    workspace = SvgCanvasWorkspace()
    scenes: list[ActiveSvgCanvasScene] = []
    for index in range(10):
        marker = chr(ord("A") + index)
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
            'data-ava-interaction-mode="none">'
            f"<title>{marker * 160}</title>"
            f"<desc>{marker * 800}</desc>"
            f'<text id="label-{index}" x="100" y="100">{marker}</text>'
            "</svg>"
        )
        scene = _create_scene(f"scene_{marker}", svg, f"gen_{marker}")
        workspace.restore_focused(scene)
        scenes.append(scene)

    state = SvgCanvasSessionState(workspace=workspace)
    state.focus_retained(scenes[0], focus_revision=1)
    state.set_presentation_visible(True)

    context = state.render_realtime_context()
    root = ET.fromstring(context)
    presentation = root.find("canvas_presentation")
    assert presentation is not None
    assert presentation.attrib == {
        "trust": "validated_current_state",
        "visible": "true",
        "scene_id": "scene_A",
        "revision": "1",
    }
    retained = root.find("retained_visuals")
    assert retained is not None
    entries = retained.findall("visual")
    retained_ids = [entry.attrib["scene_id"] for entry in entries]
    assert retained_ids[:2] == ["scene_A", "scene_J"]
    assert entries[0].attrib["presented"] == "true"
    assert entries[0].attrib["focused"] == "true"
    assert entries[1].attrib["recent_rank"] == "1"
    assert len(context) <= 2_400
    assert state.render_realtime_context() == context
