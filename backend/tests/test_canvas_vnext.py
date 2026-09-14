from __future__ import annotations

import asyncio
from builtins import anext

import pytest

from ava_backend.canvas.svg_document import (
    SvgDocumentValidator,
    SvgDocumentValidationError,
)
from ava_backend.canvas.svg_specialist import SvgCanvasSpecialistRequest
from typing import Any

from ava_backend.canvas.svg_workspace import ActiveSvgCanvasScene
from ava_backend.providers.openai_realtime_provider import _OpenAIRealtimeEventChannel
from ava_backend.realtime.coordinator import CoordinatorStateError
from ava_backend.realtime.events_v4 import AVA_CLIENT_EVENT_ADAPTER_V4
from ava_backend.realtime.provider_session import (
    RealtimeCommittedInput,
    RealtimeContextInstallationStatus,
)
from ava_backend.realtime.speech_contract import SpeechPhase
from test_canvas_focus_v4 import VALID_SVG, VALID_SVG_2, _create_real_coordinator
from test_openai_realtime_provider import (
    _FakeRealtimeSocket,
    _harness,
    _text_response_events,
)


def control_svg(kind: str = "drag", checkpoints: str = "0=begin;1=end") -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 200" data-ava-interaction-mode="explore">
<title>Changing state</title><desc>Move the handle between two semantic states.</desc>
<path id="track" d="M 40 100 L 360 100" stroke="black"/>
<g id="handle" transform="translate(10 20) rotate(15)" aria-label="Change state" data-ava-control="state" data-ava-control-kind="{kind}" data-ava-track="track" data-ava-checkpoints="{checkpoints}"><circle cx="30" cy="80" r="12"/></g>
<g id="start" data-ava-binding="begin" data-ava-target="start" aria-label="Initial state"><text x="20" y="160">Start</text></g>
<g id="finish" data-ava-binding="end" data-ava-target="finish" aria-label="Final state"><text x="320" y="160">End</text></g>
</svg>'''


@pytest.mark.parametrize("kind", ["drag", "slider", "timeline"])
def test_controls_preserve_authored_bytes_and_resolve_existing_bindings(
    kind: str,
) -> None:
    source = control_svg(kind)
    document = SvgDocumentValidator().validate(source)
    assert document.svg_bytes == source.encode()
    control = document.controls["state"]
    assert control.kind == kind
    assert control.handle_element_id == "handle"
    assert control.track_element_id == "track"
    assert [point.binding_id for point in control.checkpoints] == ["begin", "end"]


@pytest.mark.parametrize(
    "checkpoints",
    [
        "0=unknown;1=end",
        "NaN=begin;1=end",
        "0=begin;0=end",
        "0=begin;1=begin",
        "begin,end",
    ],
)
def test_invalid_control_metadata_fails_without_repair(checkpoints: str) -> None:
    with pytest.raises(SvgDocumentValidationError):
        SvgDocumentValidator().validate(control_svg(checkpoints=checkpoints))


def test_specialist_request_carries_exact_continuous_control_without_transcript() -> None:
    request = SvgCanvasSpecialistRequest.model_validate(
        {
            "generationId": "generation-semantic",
            "sourceTurnId": "turn-semantic",
            "brief": "The resolved visual purpose.",
            "interactionMode": "none",
            "continuousControl": "none",
            "ageBand": "middleChildhood",
        }
    )
    assert "source_transcript" not in type(request).model_fields
    assert "current_scene" not in request.model_input()
    assert '<interaction mode="none" continuous_control="none"/>' in request.model_input()


@pytest.mark.asyncio
async def test_context_installation_is_single_flight_and_coalesces_intermediate_desires() -> (
    None
):
    socket = _FakeRealtimeSocket([])
    channel = _OpenAIRealtimeEventChannel(
        socket, model="test", max_output_tokens=800, turn_timeout_seconds=5
    )
    try:
        initial = asyncio.create_task(
            channel.prepare(harness=_harness(build_id="ctx-1"))
        )
        await socket.wait_sent(1)
        socket.push({"type": "session.updated"})
        await initial
        channel._active_turn_id = "turn-speaking"
        channel._active_response_id = "response-speaking"
        await channel.prepare(
            harness=_harness(build_id="ctx-2", timezone_id="Asia/Tokyo")
        )
        await channel.prepare(
            harness=_harness(build_id="ctx-3", timezone_id="Europe/London")
        )
        await channel.prepare(
            harness=_harness(build_id="ctx-4", timezone_id="Asia/Kolkata")
        )
        assert len(socket.sent) == 2
        assert channel.active_context_build_id == "ctx-1"
        socket.push({"type": "session.updated"})
        await socket.wait_sent(3)
        assert channel.active_context_build_id == "ctx-2"
        assert "Asia/Kolkata" in str(socket.sent[-1])
        assert not any("Europe/London" in str(event) for event in socket.sent)
        socket.push({"type": "session.updated"})
        await channel.wait_for_context(build_id="ctx-4")
        assert channel.active_context_build_id == "ctx-4"
    finally:
        await channel.close()


@pytest.mark.asyncio
async def test_committed_item_requests_one_response_without_transcription() -> None:
    socket = _FakeRealtimeSocket([], acknowledge_updates=True)
    channel = _OpenAIRealtimeEventChannel(
        socket, model="test", max_output_tokens=800, turn_timeout_seconds=5
    )
    await channel.prepare(harness=_harness())
    iterator = channel.stream_turn(harness=_harness(), turn_id="turn-child")
    event_task = asyncio.create_task(anext(iterator))
    socket.push({"type": "input_audio_buffer.committed", "item_id": "item-child"})
    try:
        assert await event_task == RealtimeCommittedInput("turn-child", "item-child")
        assert channel.input_item_owner(item_id="item-child") == "turn-child"
        assert channel.available_child_transcript_for_turn("turn-child") is None
        assert not any(event["type"] == "response.create" for event in socket.sent)
        await channel.request_child_response(turn_id="turn-child", item_id="item-child")
        await channel.request_child_response(turn_id="turn-child", item_id="item-child")
        assert sum(event["type"] == "response.create" for event in socket.sent) == 1
        for event in _text_response_events(
            response_id="response-child", text="Here it is."
        ):
            socket.push(event)
        assert [event async for event in iterator]
    finally:
        await iterator.aclose()
        await channel.close()


@pytest.mark.asyncio
async def test_ordinary_turn_without_focus_change_bypasses_grounding_barrier() -> None:
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    turn_id = coordinator.turn_id
    item_id = "item-ordinary"
    provider._input_item_turn_ids[item_id] = turn_id
    responses_requested: list[str] = []

    async def request_child(
        *, turn_id: str, item_id: str, permit_degraded_focus_grounding: bool = False
    ) -> None:
        responses_requested.append(item_id)

    provider.request_child_response = request_child
    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "event": "clientReady",
                "sessionId": "session-real-coord",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    coordinator._turn_created_frame()
    coordinator._phase_frame(SpeechPhase.CONNECTING)
    coordinator._phase_frame(SpeechPhase.LISTENING)

    assert coordinator.accepted_focus_revision == 0
    assert coordinator.acknowledged_focus_revision == 0

    await coordinator._synchronize_turn_context(
        RealtimeCommittedInput(turn_id, item_id)
    )

    assert coordinator._turn_controls.empty()
    assert responses_requested == [item_id]
    await coordinator.close()


@pytest.mark.asyncio
async def test_accepted_focus_newer_than_acknowledged_waits_for_provider_ack() -> None:
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    turn_id = coordinator.turn_id
    item_id = "item-focus-wait"
    provider._input_item_turn_ids[item_id] = turn_id

    scene = ActiveSvgCanvasScene(
        generation_id="generation-heart",
        source_turn_id="turn-heart",
        scene_id="scene-heart",
        revision=1,
        document=SvgDocumentValidator().validate(VALID_SVG_2),
        first_rendered_sample=99,
    )
    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "event": "clientReady",
                "sessionId": "session-real-coord",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    coordinator._turn_created_frame()
    coordinator._phase_frame(SpeechPhase.CONNECTING)
    coordinator._phase_frame(SpeechPhase.LISTENING)

    await coordinator.focus_canvas(scene, focus_revision=1)
    assert coordinator.accepted_focus_revision == 1
    assert coordinator.acknowledged_focus_revision == 0

    ack_event = asyncio.Event()
    responses_requested: list[str] = []

    original_wait_for_context = provider.wait_for_context

    async def wait_for_context(*, build_id: str) -> None:
        await ack_event.wait()
        await original_wait_for_context(build_id=build_id)

    provider.wait_for_context = wait_for_context

    async def request_child(
        *, turn_id: str, item_id: str, permit_degraded_focus_grounding: bool = False
    ) -> None:
        responses_requested.append(item_id)

    provider.request_child_response = request_child

    task = asyncio.create_task(
        coordinator._synchronize_turn_context(RealtimeCommittedInput(turn_id, item_id))
    )
    await asyncio.sleep(0.01)
    assert not responses_requested
    assert coordinator.acknowledged_focus_revision == 0

    ack_event.set()
    await task
    assert coordinator.acknowledged_focus_revision == 1
    assert responses_requested == [item_id]
    await coordinator.close()


@pytest.mark.asyncio
async def test_rapid_focus_switches_latest_wins_continuous_grounding() -> None:
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    turn_id = coordinator.turn_id
    item_id = "item-rapid-focus"
    provider._input_item_turn_ids[item_id] = turn_id

    scene_b = ActiveSvgCanvasScene(
        generation_id="gen-b",
        source_turn_id="turn-b",
        scene_id="scene-b",
        revision=1,
        document=SvgDocumentValidator().validate(VALID_SVG),
        first_rendered_sample=10,
    )
    scene_c = ActiveSvgCanvasScene(
        generation_id="gen-c",
        source_turn_id="turn-c",
        scene_id="scene-c",
        revision=1,
        document=SvgDocumentValidator().validate(VALID_SVG_2),
        first_rendered_sample=20,
    )

    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "event": "clientReady",
                "sessionId": "session-real-coord",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    coordinator._turn_created_frame()
    coordinator._phase_frame(SpeechPhase.CONNECTING)
    coordinator._phase_frame(SpeechPhase.LISTENING)

    await coordinator.focus_canvas(scene_b, focus_revision=1)
    target_build_b = coordinator._focus_build_id_by_revision[1]

    b_ack_gate = asyncio.Event()
    c_ack_gate = asyncio.Event()
    waiting_on_builds: list[str] = []
    responses_requested: list[str] = []

    original_wait_for_context = provider.wait_for_context

    async def gated_wait_for_context(*, build_id: str) -> None:
        waiting_on_builds.append(build_id)
        if build_id == target_build_b:
            await b_ack_gate.wait()
        else:
            await c_ack_gate.wait()
        await original_wait_for_context(build_id=build_id)

    provider.wait_for_context = gated_wait_for_context

    async def request_child(
        *, turn_id: str, item_id: str, permit_degraded_focus_grounding: bool = False
    ) -> None:
        responses_requested.append(item_id)

    provider.request_child_response = request_child

    sync_task = asyncio.create_task(
        coordinator._synchronize_turn_context(RealtimeCommittedInput(turn_id, item_id))
    )
    await asyncio.sleep(0.01)
    assert target_build_b in waiting_on_builds

    # Scene C accepted while waiting for B
    await coordinator.focus_canvas(scene_c, focus_revision=2)
    assert coordinator.accepted_focus_revision == 2

    # B ACK arrives. Coordinator must not trigger response, but wait for C!
    b_ack_gate.set()
    await asyncio.sleep(0.01)
    assert not responses_requested
    assert coordinator.acknowledged_focus_revision == 1

    target_build_c = coordinator._focus_build_id_by_revision[2]
    assert target_build_c in waiting_on_builds

    c_ack_gate.set()
    await sync_task

    assert coordinator.acknowledged_focus_revision == 2
    assert responses_requested == [item_id]
    await coordinator.close()


@pytest.mark.asyncio
async def test_focus_timeout_fallback_does_not_poison_session_or_mark_acknowledged() -> (
    None
):
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    coordinator._turn_context_timeout_seconds = 0.05
    turn_id = coordinator.turn_id
    item_id = "item-timeout-fallback"
    provider._input_item_turn_ids[item_id] = turn_id

    scene = ActiveSvgCanvasScene(
        generation_id="gen-timeout",
        source_turn_id="turn-timeout",
        scene_id="scene-timeout",
        revision=1,
        document=SvgDocumentValidator().validate(VALID_SVG),
        first_rendered_sample=10,
    )
    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "event": "clientReady",
                "sessionId": "session-real-coord",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    coordinator._turn_created_frame()
    coordinator._phase_frame(SpeechPhase.CONNECTING)
    coordinator._phase_frame(SpeechPhase.LISTENING)

    await coordinator.focus_canvas(scene, focus_revision=5)
    assert coordinator.accepted_focus_revision == 5
    assert coordinator.acknowledged_focus_revision == 0

    async def hanging_wait_for_context(*, build_id: str) -> None:
        await asyncio.Event().wait()

    provider.wait_for_context = hanging_wait_for_context

    degraded_flag_received: list[bool] = []

    async def request_child(
        *, turn_id: str, item_id: str, permit_degraded_focus_grounding: bool = False
    ) -> None:
        degraded_flag_received.append(permit_degraded_focus_grounding)

    provider.request_child_response = request_child

    await coordinator._synchronize_turn_context(
        RealtimeCommittedInput(turn_id, item_id)
    )

    assert degraded_flag_received == [True]
    assert coordinator.acknowledged_focus_revision == 0
    assert coordinator._session_reusable is True
    await coordinator.close()


@pytest.mark.asyncio
async def test_normal_callers_cannot_bypass_unacknowledged_context() -> None:
    socket = _FakeRealtimeSocket([], acknowledge_updates=True)
    channel = _OpenAIRealtimeEventChannel(
        socket, model="test", max_output_tokens=800, turn_timeout_seconds=5
    )
    await channel.prepare(harness=_harness(build_id="ctx-1"))
    iterator = channel.stream_turn(
        harness=_harness(build_id="ctx-1"), turn_id="turn-child"
    )
    event_task = asyncio.create_task(anext(iterator))
    socket.push({"type": "input_audio_buffer.committed", "item_id": "item-normal"})
    assert await event_task == RealtimeCommittedInput("turn-child", "item-normal")

    socket._acknowledge_updates = False
    await channel.prepare_if_idle(
        harness=_harness(build_id="ctx-2", timezone_id="America/New_York"),
        turn_id="turn-child",
    )

    committed = await channel.request_child_response(
        turn_id="turn-child",
        item_id="item-normal",
        permit_degraded_focus_grounding=False,
    )
    assert committed is False
    assert not any(event["type"] == "response.create" for event in socket.sent)

    committed = await channel.request_child_response(
        turn_id="turn-child",
        item_id="item-normal",
        permit_degraded_focus_grounding=True,
    )
    assert committed is True
    assert any(event["type"] == "response.create" for event in socket.sent)
    await iterator.aclose()
    await channel.close()


@pytest.mark.asyncio
async def test_a_b_a_refocus_rejects_delayed_interaction_then_accepts_current() -> None:
    coordinator, _provider, canvas_events, _, _, jobs = await _create_real_coordinator()
    doc_a = SvgDocumentValidator().validate(VALID_SVG)
    doc_b = SvgDocumentValidator().validate(VALID_SVG_2)
    scene_a = ActiveSvgCanvasScene(
        generation_id="gen-a",
        source_turn_id="turn-a",
        scene_id="scene-a",
        revision=1,
        document=doc_a,
        first_rendered_sample=10,
    )
    scene_b = ActiveSvgCanvasScene(
        generation_id="gen-b",
        source_turn_id="turn-b",
        scene_id="scene-b",
        revision=1,
        document=doc_b,
        first_rendered_sample=20,
    )

    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "event": "clientReady",
                "sessionId": "session-real-coord",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    coordinator._turn_created_frame()
    coordinator._phase_frame(SpeechPhase.CONNECTING)
    coordinator._phase_frame(SpeechPhase.LISTENING)

    await coordinator.focus_canvas(scene_a, focus_revision=1)
    await coordinator.focus_canvas(scene_b, focus_revision=2)
    await coordinator.focus_canvas(scene_a, focus_revision=3)
    assert jobs.state.focused_scene == scene_a
    assert coordinator.accepted_focus_revision == 3
    jobs.state.set_presentation_visible(True)

    binding_id = next(iter(doc_a.bindings.keys()))
    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "event": "canvasInteraction",
                "sessionId": "session-real-coord",
                "sequence": 1,
                "sceneId": "scene-a",
                "sceneRevision": 1,
                "focusRevision": 1,
                "interactionRevision": 1,
                "svgSha256": doc_a.svg_sha256,
                "manifestSha256": doc_a.manifest_sha256,
                "bindingId": binding_id,
            }
        )
    )
    assert jobs.state.last_interaction is None
    assert coordinator._latest_canvas_interaction_intent is None
    rejected = canvas_events._events.get_nowait()
    assert rejected["event"] == "interactionRejected"
    assert rejected["rejectionCode"] == "focusRevisionMismatch"

    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "event": "canvasInteraction",
                "sessionId": "session-real-coord",
                "sequence": 2,
                "sceneId": "scene-a",
                "sceneRevision": 1,
                "focusRevision": 3,
                "interactionRevision": 1,
                "svgSha256": doc_a.svg_sha256,
                "manifestSha256": doc_a.manifest_sha256,
                "bindingId": binding_id,
            }
        )
    )
    assert jobs.state.last_interaction is not None
    assert jobs.state.last_interaction.binding_id == binding_id
    assert coordinator._session_reusable is True
    await coordinator.close()


@pytest.mark.asyncio
async def test_superseded_committed_input_cancels_prior_sync_task() -> None:
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    turn_id = coordinator.turn_id
    item_a = "item-a"
    item_b = "item-b"
    provider._input_item_turn_ids[item_a] = turn_id
    provider._input_item_turn_ids[item_b] = turn_id
    responses_requested: list[str] = []

    async def request_child(
        *, turn_id: str, item_id: str, permit_degraded_focus_grounding: bool = False
    ) -> None:
        responses_requested.append(item_id)

    provider.request_child_response = request_child

    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "event": "clientReady",
                "sessionId": "session-real-coord",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    coordinator._turn_created_frame()
    coordinator._phase_frame(SpeechPhase.CONNECTING)
    coordinator._phase_frame(SpeechPhase.LISTENING)

    committed_a = RealtimeCommittedInput(turn_id, item_a)
    coordinator._turn_context_sync_owner = committed_a
    task_a = asyncio.create_task(coordinator._synchronize_turn_context(committed_a))
    coordinator._turn_context_sync_task = task_a

    # Now item B arrives and supersedes A
    committed_b = RealtimeCommittedInput(turn_id, item_b)
    task_a.cancel()
    await asyncio.gather(task_a, return_exceptions=True)
    coordinator._turn_context_sync_owner = committed_b
    task_b = asyncio.create_task(coordinator._synchronize_turn_context(committed_b))
    coordinator._turn_context_sync_task = task_b

    await task_b
    assert responses_requested == [item_b]
    await coordinator.close()


@pytest.mark.asyncio
async def test_pending_canvas_interaction_superseded_by_child_speech() -> None:
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    turn_id = coordinator.turn_id
    item_id = "item-speech"
    provider._input_item_turn_ids[item_id] = turn_id

    # Simulate pending canvas interaction
    coordinator._latest_canvas_interaction_intent = "dummy_interaction"  # type: ignore

    # When child speech input commits, interaction intent must be immediately cleared
    task = asyncio.create_task(
        coordinator._synchronize_turn_context(RealtimeCommittedInput(turn_id, item_id))
    )
    try:
        await asyncio.sleep(0)
        assert coordinator._latest_canvas_interaction_intent is None
        assert coordinator._canvas_interaction_response_wakeup.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await coordinator.close()


def test_specialist_revise_focused_includes_exact_svg_and_no_transcript() -> None:
    doc = SvgDocumentValidator().validate(VALID_SVG)
    request = SvgCanvasSpecialistRequest.model_validate(
        {
            "generationId": "gen-revise-1",
            "sourceTurnId": "turn-revise-1",
            "brief": "Highlight the left ventricle in blue.",
            "interactionMode": "none",
            "continuousControl": "none",
            "ageBand": "middleChildhood",
            "currentSceneId": "scene-heart-1",
            "currentRevision": 2,
            "currentSvg": doc.svg_text,
            "currentSvgSha256": doc.svg_sha256,
        }
    )
    model_input = request.model_input()
    assert 'scene_id="scene-heart-1"' in model_input
    assert 'revision="2"' in model_input
    assert f'svg_sha256="{doc.svg_sha256}"' in model_input
    assert "source_transcript" not in model_input
    assert "<transcript" not in model_input


@pytest.mark.asyncio
async def test_focus_linearization_race_prevents_stale_response() -> None:
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    turn_id = coordinator.turn_id
    item_id = "item-race-test"
    provider._input_item_turn_ids[item_id] = turn_id

    doc1 = SvgDocumentValidator().validate(VALID_SVG)
    scene_1 = ActiveSvgCanvasScene(
        scene_id="scene-heart-1",
        revision=1,
        document=doc1,
        generation_id="gen-heart-1",
        source_turn_id="turn-1",
        first_rendered_sample=1000,
    )
    doc2 = SvgDocumentValidator().validate(VALID_SVG_2)
    scene_2 = ActiveSvgCanvasScene(
        scene_id="scene-heart-2",
        revision=2,
        document=doc2,
        generation_id="gen-heart-2",
        source_turn_id="turn-1",
        first_rendered_sample=1000,
    )

    # 1. Focus revision 1 and run turn 1 to acknowledge it
    await coordinator.focus_canvas(scene_1, focus_revision=1)
    assert coordinator.submitted_focus_revision == 1
    committed_1 = RealtimeCommittedInput(turn_id, "item-turn-1")
    provider._input_item_turn_ids["item-turn-1"] = turn_id
    coordinator._turn_context_sync_owner = committed_1
    await coordinator._synchronize_turn_context(committed_1)
    assert coordinator.acknowledged_focus_revision == 1

    responses_requested: list[tuple[str, int]] = []

    async def request_child(
        *, turn_id: str, item_id: str, permit_degraded_focus_grounding: bool = False
    ) -> None:
        responses_requested.append((item_id, coordinator.acknowledged_focus_revision))

    provider.request_child_response = request_child

    # 2. Hook _focus_linearization_lock.acquire to simulate focus revision 2 arriving
    # right after the while-loop check saw accepted_focus (1) <= acknowledged (1),
    # but before the lock was acquired to commit request_child_response.
    orig_acquire = coordinator._focus_linearization_lock.acquire
    race_injected = False

    async def hooked_acquire():
        nonlocal race_injected
        if not race_injected:
            race_injected = True
            # Concurrently accept focus revision 2 right before lock is acquired by _synchronize_turn_context
            await coordinator.focus_canvas(scene_2, focus_revision=2)
        return await orig_acquire()

    coordinator._focus_linearization_lock.acquire = hooked_acquire

    committed = RealtimeCommittedInput(turn_id, item_id)
    coordinator._turn_context_sync_owner = committed
    sync_task = asyncio.create_task(coordinator._synchronize_turn_context(committed))

    await sync_task

    assert race_injected is True
    # Response must NOT have proceeded with stale focus 1!
    # It must have detected the newer focus revision 2 inside the critical section,
    # looped back, waited for revision 2 context ACK, and only then requested the response.
    assert len(responses_requested) == 1
    assert responses_requested[0] == (item_id, 2)
    assert coordinator.acknowledged_focus_revision == 2

    await coordinator.close()


@pytest.mark.asyncio
async def test_pending_context_installation_preserves_atomic_build_revision_association() -> (
    None
):
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()

    doc1 = SvgDocumentValidator().validate(VALID_SVG)
    scene_1 = ActiveSvgCanvasScene(
        scene_id="scene-heart-1",
        revision=1,
        document=doc1,
        generation_id="gen-heart-1",
        source_turn_id="turn-1",
        first_rendered_sample=1000,
    )

    # Control provider.prepare_if_idle: pause while installing revision 1's harness
    prepare_started = asyncio.Event()
    allow_prepare = asyncio.Event()
    orig_prepare = provider.prepare_if_idle

    async def gated_prepare_if_idle(*, harness: Any, turn_id: str) -> Any:
        prepare_started.set()
        await allow_prepare.wait()
        return await orig_prepare(harness=harness, turn_id=turn_id)

    provider.prepare_if_idle = gated_prepare_if_idle

    # Start focus revision 1 in background task
    focus_task_1 = asyncio.create_task(
        coordinator.focus_canvas(scene_1, focus_revision=1)
    )
    await prepare_started.wait()

    # While revision 1's harness is actively preparing/installing,
    # simulate workspace advancing to focus revision 2
    coordinator._canvas_jobs.state.workspace.focus_revision = 2

    # Now allow revision 1's prepare to finish
    allow_prepare.set()
    await focus_task_1

    # In the fixed code:
    # Build ID must be mapped to revision 1 (the revision captured at harness build time),
    # NOT revision 2!
    build_id_1 = coordinator._focus_build_id_by_revision.get(1)
    assert build_id_1 is not None
    assert coordinator._focus_build_id_by_revision.get(2) is None
    assert coordinator._submitted_focus_revision == 1

    await coordinator.close()


@pytest.mark.asyncio
async def test_pending_context_deferred_installation_preserves_atomic_build_revision_association() -> (
    None
):
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    turn_id = coordinator.turn_id

    doc1 = SvgDocumentValidator().validate(VALID_SVG)
    scene_1 = ActiveSvgCanvasScene(
        scene_id="scene-heart-1",
        revision=1,
        document=doc1,
        generation_id="gen-heart-1",
        source_turn_id="turn-1",
        first_rendered_sample=1000,
    )

    # First prepare_if_idle returns None (deferred, provider busy)
    provider.prepare_if_idle = lambda **kwargs: asyncio.sleep(0, result=None)

    await coordinator.focus_canvas(scene_1, focus_revision=1)
    assert coordinator._pending_harness is not None
    assert coordinator._pending_focus_revision == 1
    harness_build_id = coordinator._pending_harness.context.build_id

    # Advance workspace focus revision to 2 before pending harness is installed
    coordinator._canvas_jobs.state.workspace.focus_revision = 2

    # Now provider becomes idle and installs the pending harness
    provider.prepare_if_idle = lambda **kwargs: asyncio.sleep(
        0, result=RealtimeContextInstallationStatus.INSTALLED
    )
    await coordinator._install_pending_context_if_idle(turn_id=turn_id)

    # Build ID must be mapped to revision 1, NOT revision 2!
    assert coordinator._focus_build_id_by_revision.get(1) == harness_build_id
    assert coordinator._focus_build_id_by_revision.get(2) is None

    await coordinator.close()


@pytest.mark.asyncio
async def test_synchronize_turn_context_installs_pending_harness_without_nested_lock() -> (
    None
):
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    turn_id = coordinator.turn_id
    item_id = "item-child-grounding"
    provider._input_item_turn_ids[item_id] = turn_id

    doc1 = SvgDocumentValidator().validate(VALID_SVG)
    scene_1 = ActiveSvgCanvasScene(
        scene_id="scene-heart-1",
        revision=1,
        document=doc1,
        generation_id="gen-heart-1",
        source_turn_id="turn-1",
        first_rendered_sample=1000,
    )

    # 1. focus_canvas runs while provider is busy (prepare_if_idle returns None)
    orig_prepare_if_idle = provider.prepare_if_idle
    provider.prepare_if_idle = lambda **kwargs: asyncio.sleep(0, result=None)
    await coordinator.focus_canvas(scene_1, focus_revision=1)
    assert coordinator._pending_harness is not None
    assert coordinator._focus_build_id_by_revision.get(1) is None

    # 2. Provider is now ready to install pending harness when child speech arrives
    provider.prepare_if_idle = orig_prepare_if_idle
    responses_requested: list[str] = []

    async def request_child(
        *, turn_id: str, item_id: str, permit_degraded_focus_grounding: bool = False
    ) -> None:
        responses_requested.append(item_id)

    provider.request_child_response = request_child

    # 3. Child speech triggers _synchronize_turn_context.
    # It must successfully install pending harness via _install_pending_context_if_idle without deadlocking on _canvas_context_lock.
    committed = RealtimeCommittedInput(turn_id, item_id)
    coordinator._turn_context_sync_owner = committed
    await coordinator._synchronize_turn_context(committed)

    assert responses_requested == [item_id]
    assert coordinator.acknowledged_focus_revision == 1
    assert coordinator._focus_build_id_by_revision.get(1) is not None

    await coordinator.close()


@pytest.mark.asyncio
async def test_focus_canvas_equal_and_monotonic_revision_invariants() -> None:
    coordinator, _, _, _, _, _ = await _create_real_coordinator()

    doc1 = SvgDocumentValidator().validate(VALID_SVG)
    scene_a = ActiveSvgCanvasScene(
        scene_id="scene-heart-1",
        revision=1,
        document=doc1,
        generation_id="gen-heart-1",
        source_turn_id="turn-1",
        first_rendered_sample=1000,
    )
    doc2 = SvgDocumentValidator().validate(VALID_SVG_2)
    scene_b = ActiveSvgCanvasScene(
        scene_id="scene-heart-2",
        revision=2,
        document=doc2,
        generation_id="gen-heart-2",
        source_turn_id="turn-1",
        first_rendered_sample=1000,
    )

    # 1. Accept new focus at revision 1
    await coordinator.focus_canvas(scene_a, focus_revision=1)
    assert coordinator.submitted_focus_revision == 1
    assert coordinator._canvas_jobs.state.workspace.focused_scene_id == "scene-heart-1"

    # 2. Same revision and same scene -> idempotent no-op
    await coordinator.focus_canvas(scene_a, focus_revision=1)
    assert coordinator.submitted_focus_revision == 1
    assert coordinator._canvas_jobs.state.workspace.focused_scene_id == "scene-heart-1"

    # 3. Same revision and different scene -> reject inconsistent scene identity
    with pytest.raises(
        CoordinatorStateError, match="focus revision cannot change scene identity"
    ):
        await coordinator.focus_canvas(scene_b, focus_revision=1)

    # 4. Stale revision -> ignore
    await coordinator.focus_canvas(scene_b, focus_revision=0)
    assert coordinator.submitted_focus_revision == 1
    assert coordinator._canvas_jobs.state.workspace.focused_scene_id == "scene-heart-1"

    # 5. Monotonically higher revision -> accept
    await coordinator.focus_canvas(scene_b, focus_revision=2)
    assert coordinator.submitted_focus_revision == 2
    assert coordinator._canvas_jobs.state.workspace.focused_scene_id == "scene-heart-2"

    await coordinator.close()


@pytest.mark.asyncio
async def test_synchronize_turn_context_waits_for_non_focus_context_ack() -> None:
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    turn_id = coordinator.turn_id
    item_id = "item-non-focus-ack"
    provider._input_item_turn_ids[item_id] = turn_id

    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "event": "clientReady",
                "sessionId": "session-real-coord",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    coordinator._turn_created_frame()
    coordinator._phase_frame(SpeechPhase.CONNECTING)
    coordinator._phase_frame(SpeechPhase.LISTENING)

    # Focus revisions match: accepted == acknowledged == 0
    assert coordinator.accepted_focus_revision == 0
    assert coordinator.acknowledged_focus_revision == 0

    # Provider has a desired context that is deferred / not yet acknowledged
    # (reproducing ctx-24 from TURN_COMMITTED)
    provider.defer_acknowledgement = True
    context_build_b = "ctx-24"
    harness_b = _harness(build_id=context_build_b)
    status = await provider.prepare(harness=harness_b)
    assert status == RealtimeContextInstallationStatus.SUBMITTED
    assert provider.desired_context_build_id == context_build_b
    assert not provider.is_context_acknowledged(context_build_b)

    committed = RealtimeCommittedInput(turn_id, item_id)
    coordinator._turn_context_sync_owner = committed
    task = asyncio.create_task(coordinator._synchronize_turn_context(committed))

    # Yield to let the coordinator attempt synchronization
    await asyncio.sleep(0.01)
    # request_child_response must NOT have been called yet and turn must not have failed
    assert len(provider.requested_child_responses) == 0
    assert not task.done()

    # Provider delivers ACK for B
    provider.acknowledge_context(context_build_b)

    await task
    # request_child_response called exactly once
    assert provider.requested_child_responses == [(turn_id, item_id)]
    await coordinator.close()


@pytest.mark.asyncio
async def test_synchronize_turn_context_waits_for_resource_outside_focus_lock() -> None:
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    turn_id = coordinator.turn_id
    item_id = "item-resource-admission"
    provider._input_item_turn_ids[item_id] = turn_id
    attempts = 0
    wait_observed = asyncio.Event()

    async def request_child(
        *, turn_id: str, item_id: str, permit_degraded_focus_grounding: bool = False
    ) -> bool:
        nonlocal attempts
        attempts += 1
        return attempts > 1

    async def wait_for_resource(*, turn_id: str) -> bool:
        assert not coordinator._focus_linearization_lock.locked()
        wait_observed.set()
        return True

    provider.request_child_response = request_child
    provider.wait_until_response_admissible = wait_for_resource
    committed = RealtimeCommittedInput(turn_id, item_id)
    coordinator._turn_context_sync_owner = committed

    await coordinator._synchronize_turn_context(committed)

    assert wait_observed.is_set()
    assert attempts == 2
    await coordinator.close()


@pytest.mark.asyncio
async def test_synchronize_turn_context_latest_wins_across_wait() -> None:
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    turn_id = coordinator.turn_id
    item_id = "item-latest-wins"
    provider._input_item_turn_ids[item_id] = turn_id

    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "event": "clientReady",
                "sessionId": "session-real-coord",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    coordinator._turn_created_frame()
    coordinator._phase_frame(SpeechPhase.CONNECTING)
    coordinator._phase_frame(SpeechPhase.LISTENING)

    # 1. B desired
    provider.defer_acknowledgement = True
    context_build_b = "ctx-20"
    await provider.prepare(
        harness=_harness(build_id=context_build_b, timezone_id="Asia/Tokyo")
    )
    assert provider.desired_context_build_id == context_build_b

    # 2. Sync task starts waiting for B
    committed = RealtimeCommittedInput(turn_id, item_id)
    coordinator._turn_context_sync_owner = committed
    task = asyncio.create_task(coordinator._synchronize_turn_context(committed))
    await asyncio.sleep(0.01)
    assert not task.done()
    assert len(provider.requested_child_responses) == 0

    # 3. Before B is acknowledged, C is desired
    context_build_c = "ctx-21"
    await provider.prepare(
        harness=_harness(build_id=context_build_c, timezone_id="Europe/London")
    )
    assert provider.desired_context_build_id == context_build_c

    # 4. Provider delivers ACK for B
    provider.acknowledge_context(context_build_b)
    await asyncio.sleep(0.01)
    # Response must STILL NOT be sent because C is now desired and not acknowledged
    assert not task.done()
    assert len(provider.requested_child_responses) == 0

    # 5. Provider delivers ACK for C
    provider.acknowledge_context(context_build_c)
    await task
    # Response sent once
    assert provider.requested_child_responses == [(turn_id, item_id)]
    await coordinator.close()


@pytest.mark.asyncio
async def test_synchronize_turn_context_atomic_race_linearization() -> None:
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    turn_id = coordinator.turn_id
    item_id = "item-race-linearization"
    provider._input_item_turn_ids[item_id] = turn_id

    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "event": "clientReady",
                "sessionId": "session-real-coord",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    coordinator._turn_created_frame()
    coordinator._phase_frame(SpeechPhase.CONNECTING)
    coordinator._phase_frame(SpeechPhase.LISTENING)

    # 1. Context B is prepared and acknowledged
    context_build_b = "ctx-30"
    await provider.prepare(
        harness=_harness(build_id=context_build_b, timezone_id="Asia/Tokyo")
    )
    assert provider.is_context_acknowledged(context_build_b)

    # 2. Hook request_child_response: right when first called under _focus_linearization_lock,
    # schedule unacknowledged context C before checking context readiness
    original_request = provider.request_child_response
    raced = False
    context_build_c = "ctx-31"

    async def hooked_request(
        *, turn_id: str, item_id: str, permit_degraded_focus_grounding: bool = False
    ) -> bool:
        nonlocal raced
        if not raced:
            raced = True
            provider.defer_acknowledgement = True
            await provider.prepare(
                harness=_harness(build_id=context_build_c, timezone_id="Europe/London")
            )
        return await original_request(
            turn_id=turn_id,
            item_id=item_id,
            permit_degraded_focus_grounding=permit_degraded_focus_grounding,
        )

    provider.request_child_response = hooked_request

    committed = RealtimeCommittedInput(turn_id, item_id)
    coordinator._turn_context_sync_owner = committed
    task = asyncio.create_task(coordinator._synchronize_turn_context(committed))

    # Yield to let coordinator run
    await asyncio.sleep(0.01)
    # The race was triggered: hooked_request prepared C, original_request returned False,
    # coordinator released lock, looped back and is now waiting for C to be acknowledged
    assert raced
    assert not task.done()
    assert len(provider.requested_child_responses) == 0

    # Provider delivers ACK for C
    provider.acknowledge_context(context_build_c)
    await task
    # Response committed once after C was acknowledged
    assert provider.requested_child_responses == [(turn_id, item_id)]
    await coordinator.close()


@pytest.mark.asyncio
async def test_synchronize_turn_context_canvas_focus_combination() -> None:
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    turn_id = coordinator.turn_id
    item_id = "item-focus-combination"
    provider._input_item_turn_ids[item_id] = turn_id

    doc = SvgDocumentValidator().validate(VALID_SVG)
    scene = ActiveSvgCanvasScene(
        scene_id="scene-heart-1",
        revision=1,
        document=doc,
        generation_id="gen-heart-1",
        source_turn_id="turn-1",
        first_rendered_sample=1000,
    )

    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "event": "clientReady",
                "sessionId": "session-real-coord",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    coordinator._turn_created_frame()
    coordinator._phase_frame(SpeechPhase.CONNECTING)
    coordinator._phase_frame(SpeechPhase.LISTENING)

    # 1. Provider defers acknowledgements
    provider.defer_acknowledgement = True

    # 2. Focus canvas at revision 1
    await coordinator.focus_canvas(scene, focus_revision=1)
    assert coordinator.accepted_focus_revision == 1
    focus_build_id = coordinator._focus_build_id_by_revision[1]
    assert focus_build_id is not None
    assert not provider.is_context_acknowledged(focus_build_id)

    # 3. Unrelated TURN_COMMITTED creates context build C
    context_build_c = "ctx-50"
    await provider.prepare(harness=_harness(build_id=context_build_c))
    assert provider.desired_context_build_id == context_build_c
    assert not provider.is_context_acknowledged(context_build_c)

    # 4. Child speech commits
    committed = RealtimeCommittedInput(turn_id, item_id)
    coordinator._turn_context_sync_owner = committed
    task = asyncio.create_task(coordinator._synchronize_turn_context(committed))

    await asyncio.sleep(0.01)
    assert not task.done()
    assert len(provider.requested_child_responses) == 0

    # 5. Acknowledge focus context
    provider.acknowledge_context(focus_build_id)
    await asyncio.sleep(0.01)
    # Focus was acknowledged, but C is still unacknowledged -> response must not be sent yet
    assert not task.done()
    assert len(provider.requested_child_responses) == 0

    # 6. Acknowledge context C
    provider.acknowledge_context(context_build_c)
    await task
    # Response committed once
    assert provider.requested_child_responses == [(turn_id, item_id)]
    assert coordinator.acknowledged_focus_revision == 1
    assert provider.active_context_build_id == context_build_c
    await coordinator.close()


@pytest.mark.asyncio
async def test_non_focus_context_timeout_does_not_flip_focus_degraded_flag() -> None:
    coordinator, provider, _, _, _, _ = await _create_real_coordinator()
    turn_id = coordinator.turn_id
    item_id = "item-non-focus-timeout"
    provider._input_item_turn_ids[item_id] = turn_id

    await coordinator.receive(
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "event": "clientReady",
                "sessionId": "session-real-coord",
                "sequence": 0,
                "rendererCapabilityVersion": "ava-svg-renderer-v2",
                "validatorVersion": "ava-svg-validator-v2",
            }
        )
    )
    coordinator._turn_created_frame()
    coordinator._phase_frame(SpeechPhase.CONNECTING)
    coordinator._phase_frame(SpeechPhase.LISTENING)

    # Focus revisions match: accepted == acknowledged == 0
    assert coordinator.accepted_focus_revision == 0
    assert coordinator.acknowledged_focus_revision == 0

    # Provider has an unacknowledged non-focus context build
    provider.defer_acknowledgement = True
    context_build_b = "ctx-99"
    await provider.prepare(
        harness=_harness(build_id=context_build_b, timezone_id="Asia/Tokyo")
    )
    assert provider.desired_context_build_id == context_build_b
    assert not provider.is_context_acknowledged(context_build_b)

    # Make wait_for_context hang so coordinator experiences a TimeoutError
    async def hanging_wait_for_context(*, build_id: str) -> None:
        await asyncio.Event().wait()

    provider.wait_for_context = hanging_wait_for_context

    # Capture the permit_degraded_focus_grounding flag passed to request_child_response
    degraded_flags_received: list[bool] = []

    async def request_child(
        *, turn_id: str, item_id: str, permit_degraded_focus_grounding: bool = False
    ) -> bool:
        degraded_flags_received.append(permit_degraded_focus_grounding)
        return False

    provider.request_child_response = request_child

    # Set turn context timeout very short for fast test execution
    coordinator._turn_context_timeout_seconds = 0.05

    committed = RealtimeCommittedInput(turn_id, item_id)
    coordinator._turn_context_sync_owner = committed
    task = asyncio.create_task(coordinator._synchronize_turn_context(committed))

    # Allow timeout to fire and request_child to be attempted
    await asyncio.sleep(0.12)

    # The flag passed to request_child_response must be False (NOT flipped to True)
    assert len(degraded_flags_received) >= 1
    assert all(flag is False for flag in degraded_flags_received)

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await coordinator.close()
