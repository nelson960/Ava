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
    assert state.render_realtime_context() == ""


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


def test_interruption_discards_unpresented_candidate_not_active_scene() -> None:
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

    discarded = state.discard_candidate_for_interrupted_turn(source_turn_id="turn_2")

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

    recorded = state.record_interaction(
        scene_id=active.scene_id,
        scene_revision=active.revision,
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
            interaction_revision=2,
            svg_sha256=generation.document.svg_sha256,
            manifest_sha256=generation.document.manifest_sha256,
            binding_id="missing_binding",
        )
    assert raised.value.code is SvgCanvasStateErrorCode.BINDING_UNKNOWN


def test_realtime_context_is_escaped_and_explicitly_untrusted() -> None:
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
        interaction_revision=1,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        binding_id="select_cloud",
    )

    context = state.render_realtime_context()

    assert 'trust="untrusted_model_generated_data"' in context
    assert "Cloud &amp; rain" in context
    assert "&lt;gently&gt;" in context
    assert "<target>Rain cloud</target>" in context


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
        interaction_revision=1,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        binding_id="caterpillar",
    )

    assert recorded.interaction_mode == "steps"
    assert recorded.step_index == 2
    assert (state.workspace.scene_count, state.active.scene_id, state.active.revision) == before
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
        interaction_revision=1,
        svg_sha256=generation.document.svg_sha256,
        manifest_sha256=generation.document.manifest_sha256,
        binding_id="select_cloud",
    )

    replacement = SvgCanvasSessionState(workspace=workspace)

    assert replacement.last_interaction is recorded
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
