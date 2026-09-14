from __future__ import annotations

import pytest

from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_session import (
    SvgCandidateStatus,
    SvgCanvasCandidate,
    SvgCanvasSessionState,
    SvgCanvasStateError,
    SvgCanvasStateErrorCode,
)
from ava_backend.canvas.svg_specialist import SvgCanvasSpecialistGeneration
from ava_backend.canvas.svg_workspace import SvgCanvasWorkspace


def _generation(
    generation_id: str = "generation_1",
) -> SvgCanvasSpecialistGeneration:
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600"
 data-ava-interaction-mode="explore">
<title>Cloud &amp; rain</title>
<desc>A cloud &lt;gently&gt; releases rain.</desc>
<g id="cloud" data-ava-binding="select_cloud"
   data-ava-target="cloud" aria-label="Rain cloud">
  <ellipse cx="500" cy="220" rx="140" ry="60" fill="#dff5ff"/>
</g>
<g id="rain" data-ava-binding="show_rain"
   data-ava-target="rain" aria-label="Show the rain">
  <path d="M450 300 L430 360 M500 300 L480 360" stroke="#358acb"/>
</g>
<text id="cloud_label" x="500" y="220">Condensation</text>
<text id="rain_label" x="500" y="390">Precipitation</text>
</svg>"""
    return SvgCanvasSpecialistGeneration(
        generation_id=generation_id,
        document=SvgDocumentValidator().validate(svg),
    )


def _continuous_control_generation(
    *kinds: str,
    generation_id: str = "generation_controls",
) -> SvgCanvasSpecialistGeneration:
    tracks = "".join(
        f'<path id="track_{index}" d="M 40 {index * 80} L 360 {index * 80}"/>'
        for index, _kind in enumerate(kinds, start=1)
    )
    controls = "".join(
        (
            f'<g id="handle_{index}" aria-label="Control {index}" '
            f'data-ava-control="control_{index}" data-ava-control-kind="{kind}" '
            f'data-ava-track="track_{index}" '
            'data-ava-checkpoints="0=begin;1=end">'
            f'<circle cx="40" cy="{index * 80}" r="12"/></g>'
        )
        for index, kind in enumerate(kinds, start=1)
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300" '
        'data-ava-interaction-mode="explore">'
        "<title>Continuous controls</title>"
        "<desc>Validated local controls.</desc>"
        f"{tracks}{controls}"
        '<g id="start" data-ava-binding="begin" data-ava-target="start" '
        'aria-label="Initial state"><text x="20" y="260">Start</text></g>'
        '<g id="finish" data-ava-binding="end" data-ava-target="finish" '
        'aria-label="Final state"><text x="320" y="260">End</text></g>'
        "</svg>"
    )
    return SvgCanvasSpecialistGeneration(
        generation_id=generation_id,
        document=SvgDocumentValidator().validate(svg),
    )


def _presented_context(
    generation: SvgCanvasSpecialistGeneration,
    *,
    pending_visual: str = "",
) -> str:
    state = SvgCanvasSessionState()
    _prepare_and_schedule(state, generation)
    state.mark_presented(
        generation_id=generation.generation_id,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        first_rendered_sample=4_800,
    )
    return state.render_realtime_context(pending_visual=pending_visual)


def _prepare_and_schedule(
    state: SvgCanvasSessionState,
    generation: SvgCanvasSpecialistGeneration,
    *,
    source_turn_id: str = "turn_1",
    sample: int = 4_800,
) -> None:
    _stage_generation(state, generation, source_turn_id=source_turn_id)
    document = generation.document
    state.mark_document_prepared(
        generation_id=generation.generation_id,
        svg_sha256=document.svg_sha256,
        manifest_sha256=document.manifest_sha256,
        renderer_capability_version=str(document.manifest["rendererCapabilityVersion"]),
        validator_version=str(document.manifest["validatorVersion"]),
    )
    state.schedule_presentation(
        generation_id=generation.generation_id,
        presentation_sample_offset=sample,
    )


def _stage_generation(
    state: SvgCanvasSessionState,
    generation: SvgCanvasSpecialistGeneration,
    *,
    source_turn_id: str,
) -> SvgCanvasCandidate:
    active = state.active
    return state.stage_generation(
        generation,
        source_turn_id=source_turn_id,
        base_scene_id=active.scene_id if active is not None else None,
        base_revision=active.revision if active is not None else 0,
        workspace_epoch=state.workspace.reset_epoch,
    )


def test_preload_ack_does_not_activate_candidate() -> None:
    state = SvgCanvasSessionState()
    generation = _generation()
    candidate = _stage_generation(state, generation, source_turn_id="turn_1")

    prepared = state.mark_document_prepared(
        generation_id=generation.generation_id,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        renderer_capability_version=str(
            generation.document.manifest["rendererCapabilityVersion"]
        ),
        validator_version=str(generation.document.manifest["validatorVersion"]),
    )

    assert candidate.status is SvgCandidateStatus.DOCUMENT_READY
    assert prepared.status is SvgCandidateStatus.PREPARED
    assert state.active is None
    assert (
        state.render_realtime_context()
        == '<canvas_context trust="backend_semantic_canvas_metadata">'
        '<canvas_presentation trust="validated_current_state" visible="false"/>'
        '<canvas retained_count="0" focused_position="none"/>'
        "</canvas_context>"
    )


def test_first_rendered_frame_is_the_only_activation_boundary() -> None:
    state = SvgCanvasSessionState()
    generation = _generation()
    _prepare_and_schedule(state, generation)

    active = state.mark_presented(
        generation_id="generation_1",
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        first_rendered_sample=4_804,
    )

    assert active.revision == 1
    assert active.first_rendered_sample == 4_804
    assert state.active == active
    assert state.candidate is None


def test_presented_ack_before_scheduled_sample_fails_visibly() -> None:
    state = SvgCanvasSessionState()
    generation = _generation()
    _prepare_and_schedule(state, generation, sample=4_800)

    with pytest.raises(SvgCanvasStateError) as raised:
        state.mark_presented(
            generation_id="generation_1",
            svg_sha256=generation.document.svg_sha256,
            manifest_sha256=generation.document.manifest_sha256,
            first_rendered_sample=4_799,
        )

    assert raised.value.code is SvgCanvasStateErrorCode.INVALID_CANDIDATE_TRANSITION
    assert state.active is None


def test_discarding_unpresented_candidate_does_not_touch_active_scene() -> None:
    state = SvgCanvasSessionState()
    first = _generation()
    _prepare_and_schedule(state, first)
    active = state.mark_presented(
        generation_id=first.generation_id,
        svg_sha256=first.document.svg_sha256,
        manifest_sha256=first.document.manifest_sha256,
        first_rendered_sample=4_800,
    )
    second = _generation("generation_2")
    _stage_generation(state, second, source_turn_id="turn_2")

    discarded = state.discard_candidate(generation_id="generation_2")

    assert discarded is not None
    assert discarded.generation_id == "generation_2"
    assert state.candidate is None
    assert state.active == active


def test_hash_mismatch_never_prepares_or_activates_document() -> None:
    state = SvgCanvasSessionState()
    generation = _generation()
    _stage_generation(state, generation, source_turn_id="turn_1")

    with pytest.raises(SvgCanvasStateError) as raised:
        state.mark_document_prepared(
            generation_id="generation_1",
            svg_sha256="0" * 64,
            manifest_sha256=generation.document.manifest_sha256,
            renderer_capability_version=str(
                generation.document.manifest["rendererCapabilityVersion"]
            ),
            validator_version=str(generation.document.manifest["validatorVersion"]),
        )

    assert raised.value.code is SvgCanvasStateErrorCode.CONTENT_HASH_MISMATCH
    assert state.candidate is not None
    assert state.candidate.status is SvgCandidateStatus.DOCUMENT_READY
    assert state.active is None


def test_only_declared_binding_can_cross_control_channel() -> None:
    state = SvgCanvasSessionState()
    generation = _generation()
    _prepare_and_schedule(state, generation)
    active = state.mark_presented(
        generation_id="generation_1",
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        first_rendered_sample=4_800,
    )
    assert state.focus_revision == 0

    recorded = state.record_interaction(
        scene_id=active.scene_id,
        scene_revision=active.revision,
        focus_revision=state.focus_revision,
        interaction_revision=1,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        binding_id="select_cloud",
    )

    assert recorded.target_id == "cloud"
    assert recorded.accessibility_label == "Rain cloud"
    assert recorded.interaction_mode == "explore"
    assert recorded.step_index is None
    with pytest.raises(SvgCanvasStateError) as raised:
        state.record_interaction(
            scene_id=active.scene_id,
            scene_revision=active.revision,
            focus_revision=state.focus_revision,
            interaction_revision=2,
            svg_sha256=generation.document.svg_sha256,
            manifest_sha256=generation.document.manifest_sha256,
            binding_id="missing_binding",
        )
    assert raised.value.code is SvgCanvasStateErrorCode.BINDING_UNKNOWN


def test_interaction_focus_revision_mismatch_rejects_without_mutation() -> None:
    state = SvgCanvasSessionState()
    generation = _generation()
    _prepare_and_schedule(state, generation)
    active = state.mark_presented(
        generation_id=generation.generation_id,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        first_rendered_sample=4_800,
    )
    state.focus_retained(active, focus_revision=3)
    state.set_presentation_visible(True)

    with pytest.raises(SvgCanvasStateError) as raised:
        state.record_interaction(
            scene_id=active.scene_id,
            scene_revision=active.revision,
            focus_revision=1,
            interaction_revision=1,
            svg_sha256=generation.document.svg_sha256,
            manifest_sha256=generation.document.manifest_sha256,
            binding_id="select_cloud",
        )

    assert raised.value.code is SvgCanvasStateErrorCode.FOCUS_REVISION_MISMATCH
    assert state.last_interaction is None
    assert state.workspace.last_interaction is None

    recorded = state.record_interaction(
        scene_id=active.scene_id,
        scene_revision=active.revision,
        focus_revision=3,
        interaction_revision=1,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        binding_id="select_cloud",
    )
    assert recorded.interaction_revision == 1


def test_realtime_context_is_escaped_and_semantically_projected() -> None:
    state = SvgCanvasSessionState()
    generation = _generation()
    _prepare_and_schedule(state, generation)
    active = state.mark_presented(
        generation_id="generation_1",
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        first_rendered_sample=4_800,
    )
    state.record_interaction(
        scene_id=active.scene_id,
        scene_revision=active.revision,
        focus_revision=state.focus_revision,
        interaction_revision=1,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        binding_id="select_cloud",
    )

    context = state.render_realtime_context()

    assert 'trust="backend_semantic_canvas_metadata"' in context
    assert "<focused_scene" in context
    assert active.generation_id not in context
    assert active.scene_id not in context
    assert active.document.svg_sha256 not in context
    assert "Cloud &amp; rain" in context
    assert "&lt;gently&gt;" in context
    assert "<target>Rain cloud</target>" in context


@pytest.mark.parametrize("interaction_mode", ["none", "explore"])
def test_realtime_context_reports_no_continuous_controls(
    interaction_mode: str,
) -> None:
    binding = (
        '<g id="part" data-ava-binding="inspect" data-ava-target="part" '
        'aria-label="Inspect part"><circle cx="100" cy="100" r="20"/></g>'
        if interaction_mode == "explore"
        else ""
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 200" '
        f'data-ava-interaction-mode="{interaction_mode}">'
        "<title>Static state</title><desc>No continuous controls.</desc>"
        f"{binding}"
        "</svg>"
    )
    generation = SvgCanvasSpecialistGeneration(
        generation_id=f"generation_{interaction_mode}",
        document=SvgDocumentValidator().validate(svg),
    )

    context = _presented_context(generation)

    assert '<continuous_controls kinds="none"/>' in context


@pytest.mark.parametrize("kind", ["drag", "slider", "timeline"])
def test_realtime_context_reports_exact_continuous_control_kind(kind: str) -> None:
    context = _presented_context(_continuous_control_generation(kind))

    assert f'<continuous_controls kinds="{kind}"/>' in context


def test_realtime_context_reports_legacy_mixed_control_kinds_deterministically() -> (
    None
):
    generation = _continuous_control_generation("slider", "drag")

    context = _presented_context(generation)

    assert '<continuous_controls kinds="drag,slider"/>' in context


def test_realtime_continuous_control_context_remains_bounded() -> None:
    pending_visual = f'<pending_visual>{"x" * 1_500}</pending_visual>'

    context = _presented_context(
        _continuous_control_generation("timeline"),
        pending_visual=pending_visual,
    )

    assert '<continuous_controls kinds="timeline"/>' in context
    assert len(context) <= 2_400


def test_steps_interaction_records_typed_step_and_preserves_scene_identity() -> None:
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600"
 data-ava-interaction-mode="steps">
<title>Butterfly life cycle</title><desc>Two ordered life-cycle stages.</desc>
<g id="egg" data-ava-binding="egg" data-ava-step="1" aria-label="Egg stage"/>
<g id="caterpillar" data-ava-binding="caterpillar" data-ava-step="2"
 aria-label="Caterpillar stage"/>
</svg>"""
    generation = SvgCanvasSpecialistGeneration(
        generation_id="generation_steps",
        document=SvgDocumentValidator().validate(svg),
    )
    state = SvgCanvasSessionState()
    _prepare_and_schedule(state, generation)
    active = state.mark_presented(
        generation_id=generation.generation_id,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        first_rendered_sample=4_800,
    )
    before = (state.workspace.scene_count, active.scene_id, active.revision)

    recorded = state.record_interaction(
        scene_id=active.scene_id,
        scene_revision=active.revision,
        focus_revision=state.focus_revision,
        interaction_revision=1,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        binding_id="caterpillar",
    )

    assert recorded.interaction_mode == "steps"
    assert recorded.step_index == 2
    assert (
        state.workspace.scene_count,
        state.active.scene_id,
        state.active.revision,
    ) == before
    context = state.render_realtime_context()
    assert 'interaction_mode="steps"' in context
    assert '<last_interaction mode="steps" step_index="2">' in context
    assert "Caterpillar stage" in context


def test_presented_diagram_context_includes_important_visible_labels() -> None:
    state = SvgCanvasSessionState()
    generation = _generation()
    _prepare_and_schedule(state, generation)
    state.mark_presented(
        generation_id=generation.generation_id,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        first_rendered_sample=4_800,
    )

    context = state.render_realtime_context()

    assert "Condensation" in context
    assert "Precipitation" in context
    assert "Rain cloud" in context
    assert "M450 300" not in context
    assert "stroke" not in context


def test_reportable_interaction_enters_realtime_canvas_context() -> None:
    state = SvgCanvasSessionState()
    generation = _generation()
    _prepare_and_schedule(state, generation)
    active = state.mark_presented(
        generation_id=generation.generation_id,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        first_rendered_sample=4_800,
    )
    state.record_interaction(
        scene_id=active.scene_id,
        scene_revision=active.revision,
        focus_revision=state.focus_revision,
        interaction_revision=1,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        binding_id="select_cloud",
    )

    context = state.render_realtime_context()

    assert '<last_interaction mode="explore">' in context
    assert "<target>Rain cloud</target>" in context
    assert (
        '<last_interaction mode="explore"><target>Rain cloud</target>'
        "</last_interaction>"
    ) in context
    assert '>"<target>' not in context


def test_interaction_context_survives_session_state_replacement() -> None:
    workspace = SvgCanvasWorkspace()
    state = SvgCanvasSessionState(workspace=workspace)
    generation = _generation()
    _prepare_and_schedule(state, generation)
    active = state.mark_presented(
        generation_id=generation.generation_id,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        first_rendered_sample=4_800,
    )
    recorded = state.record_interaction(
        scene_id=active.scene_id,
        scene_revision=active.revision,
        focus_revision=state.focus_revision,
        interaction_revision=1,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        binding_id="select_cloud",
    )

    replacement = SvgCanvasSessionState(workspace=workspace)

    assert replacement.last_interaction is recorded
    replacement.set_presentation_visible(True)
    assert "<target>Rain cloud</target>" in replacement.render_realtime_context()


def test_new_canvas_revision_clears_stale_interaction_context() -> None:
    state = SvgCanvasSessionState()
    first = _generation("generation_1")
    _prepare_and_schedule(state, first)
    active = state.mark_presented(
        generation_id=first.generation_id,
        svg_sha256=first.document.svg_sha256,
        manifest_sha256=first.document.manifest_sha256,
        first_rendered_sample=4_800,
    )
    state.record_interaction(
        scene_id=active.scene_id,
        scene_revision=active.revision,
        focus_revision=state.focus_revision,
        interaction_revision=1,
        svg_sha256=first.document.svg_sha256,
        manifest_sha256=first.document.manifest_sha256,
        binding_id="select_cloud",
    )

    second = _generation("generation_2")
    _prepare_and_schedule(state, second, source_turn_id="turn_2", sample=9_600)
    revised = state.mark_presented(
        generation_id=second.generation_id,
        svg_sha256=second.document.svg_sha256,
        manifest_sha256=second.document.manifest_sha256,
        first_rendered_sample=9_600,
    )

    assert revised.scene_id == active.scene_id
    assert revised.revision == active.revision + 1
    assert state.last_interaction is None
    assert "<last_interaction>" not in state.render_realtime_context()


def test_session_state_derives_focus_from_shared_workspace() -> (
    None
):
    workspace = SvgCanvasWorkspace()
    state_a = SvgCanvasSessionState(workspace=workspace, inherit_focus=False)
    first = _generation("generation_1")
    _prepare_and_schedule(state_a, first)
    active_a = state_a.mark_presented(
        generation_id=first.generation_id,
        svg_sha256=first.document.svg_sha256,
        manifest_sha256=first.document.manifest_sha256,
        first_rendered_sample=4_800,
    )
    assert state_a.active is active_a

    # Now Session B is created with the same workspace:
    state_b = SvgCanvasSessionState(workspace=workspace, inherit_focus=False)
    # Session A is completely unaffected:
    assert state_a.active is active_a
    # Session B derives the one canonical workspace focus but remains hidden.
    assert state_b.focused_scene is active_a
    assert state_b.active is None
    assert "<focused_scene" in state_b.render_realtime_context()

    # Session B restores the scene:
    state_b.restore_focused(active_a)
    assert state_b.focused_scene is active_a
    assert state_b.presentation_visible is False
    assert state_b.active is None
    state_b.set_presentation_visible(True)
    assert state_b.active is active_a
    assert len(workspace.scenes) == 1
