from __future__ import annotations

import pytest

from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_session import (
    SvgCandidateStatus,
    SvgCanvasSessionState,
    SvgCanvasStateError,
    SvgCanvasStateErrorCode,
)
from ava_backend.canvas.svg_specialist import SvgCanvasSpecialistGeneration


def _generation(
    generation_id: str = "generation_1",
) -> SvgCanvasSpecialistGeneration:
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600">
<title>Cloud &amp; rain</title>
<desc>A cloud &lt;gently&gt; releases rain.</desc>
<g id="cloud" data-ava-binding="select_cloud" data-ava-action="select"
   data-ava-target="cloud" aria-label="Rain cloud">
  <ellipse cx="500" cy="220" rx="140" ry="60" fill="#dff5ff"/>
</g>
<g id="rain" data-ava-binding="show_rain" data-ava-action="reveal"
   data-ava-target="rain" aria-label="Show the rain">
  <path d="M450 300 L430 360 M500 300 L480 360" stroke="#358acb"/>
</g>
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
    state.stage_generation(generation, source_turn_id=source_turn_id)
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


def test_preload_ack_does_not_activate_candidate() -> None:
    state = SvgCanvasSessionState()
    generation = _generation()
    candidate = state.stage_generation(generation, source_turn_id="turn_1")

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
    state.stage_generation(second, source_turn_id="turn_2")

    discarded = state.discard_candidate_for_interrupted_turn(source_turn_id="turn_2")

    assert discarded is not None
    assert discarded.generation_id == "generation_2"
    assert state.candidate is None
    assert state.active == active


def test_hash_mismatch_never_prepares_or_activates_document() -> None:
    state = SvgCanvasSessionState()
    generation = _generation()
    state.stage_generation(generation, source_turn_id="turn_1")

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


def test_only_declared_server_binding_can_cross_control_channel() -> None:
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
    with pytest.raises(SvgCanvasStateError) as raised:
        state.record_interaction(
            scene_id=active.scene_id,
            scene_revision=active.revision,
            interaction_revision=2,
            svg_sha256=generation.document.svg_sha256,
            manifest_sha256=generation.document.manifest_sha256,
            binding_id="show_rain",
        )
    assert raised.value.code is SvgCanvasStateErrorCode.BINDING_LOCAL_ONLY


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
