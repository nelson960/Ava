from __future__ import annotations

import pytest

from ava_backend.canvas.svg_delivery import (
    SvgCanvasDocumentStore,
    SvgDocumentNotFoundError,
    SvgDocumentStoreCapacityError,
    SvgDocumentStoreError,
)
from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_session import SvgCanvasSessionState
from ava_backend.canvas.svg_specialist import (
    SvgCanvasSpecialistGeneration,
    SvgCanvasVisibleDraft,
)


def _candidate(generation_id: str = "generation-1"):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
        'data-ava-interaction-mode="none">'
        "<title>Exact cloud</title><desc>A cloud.</desc>"
        '<ellipse cx="500" cy="250" rx="120" ry="60" fill="#fff"/>'
        "</svg>\n"
    )
    generation = SvgCanvasSpecialistGeneration(
        generation_id=generation_id,
        document=SvgDocumentValidator().validate(svg),
    )
    state = SvgCanvasSessionState()
    return state.stage_generation(
        generation,
        source_turn_id="turn-1",
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=state.workspace.reset_epoch,
    )


async def test_store_serves_exact_svg_and_canonical_manifest_on_separate_paths() -> (
    None
):
    store = SvgCanvasDocumentStore()
    candidate = _candidate()

    stored = await store.publish(session_id="session-1", candidate=candidate)
    fetched = await store.get(session_id="session-1", generation_id="generation-1")

    assert fetched == stored
    assert fetched.document.svg_bytes == candidate.document.svg_bytes
    assert fetched.document.manifest_bytes == candidate.document.manifest_bytes
    assert fetched.svg_path.endswith("/generation-1/svg")
    assert fetched.manifest_path.endswith("/generation-1/manifest")
    assert "/events" not in fetched.svg_path


async def test_store_keeps_draft_separate_from_candidate_manifest_authority() -> None:
    store = SvgCanvasDocumentStore()
    candidate = _candidate()
    draft = SvgCanvasVisibleDraft(
        generation_id=candidate.generation_id,
        draft_index=1,
        attempt=0,
        document=candidate.document,
    )

    stored = await store.publish_draft(
        session_id="session-1",
        source_turn_id="turn-1",
        draft=draft,
    )

    assert (
        await store.get_draft(
            session_id="session-1",
            generation_id="generation-1",
            draft_index=1,
        )
        == stored
    )
    assert "/canvas-drafts/" in stored.svg_path
    assert not hasattr(stored, "scene_id")
    assert not hasattr(stored, "manifest_path")
    assert store.document_count == 0
    assert store.draft_count == 1


async def test_publish_is_idempotent_only_for_identical_generation_content() -> None:
    store = SvgCanvasDocumentStore()
    candidate = _candidate()

    first = await store.publish(session_id="session-1", candidate=candidate)
    second = await store.publish(session_id="session-1", candidate=candidate)

    assert second is first


async def test_generation_id_reuse_with_different_document_fails_loudly() -> None:
    store = SvgCanvasDocumentStore()
    first = _candidate()
    second_state = SvgCanvasSessionState()
    second_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
        'data-ava-interaction-mode="none">'
        "<title>Different</title><desc>A different cloud.</desc>"
        '<circle cx="500" cy="250" r="80"/>'
        "</svg>"
    )
    second = second_state.stage_generation(
        SvgCanvasSpecialistGeneration(
            generation_id="generation-1",
            document=SvgDocumentValidator().validate(second_svg),
        ),
        source_turn_id="turn-1",
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=second_state.workspace.reset_epoch,
    )
    await store.publish(session_id="session-1", candidate=first)

    with pytest.raises(SvgDocumentStoreError, match="reused"):
        await store.publish(session_id="session-1", candidate=second)


async def test_capacity_never_silently_evicts_a_document() -> None:
    store = SvgCanvasDocumentStore(max_documents=2, max_documents_per_session=1)
    first = _candidate("generation-1")
    second = _candidate("generation-2")
    await store.publish(session_id="session-1", candidate=first)

    with pytest.raises(SvgDocumentStoreCapacityError, match="session"):
        await store.publish(session_id="session-1", candidate=second)
    assert await store.get(session_id="session-1", generation_id="generation-1")


async def test_discard_and_session_release_remove_only_exact_scope() -> None:
    store = SvgCanvasDocumentStore()
    await store.publish(session_id="session-1", candidate=_candidate("generation-1"))
    await store.publish(session_id="session-2", candidate=_candidate("generation-2"))

    assert await store.discard(session_id="session-1", generation_id="generation-1")
    with pytest.raises(SvgDocumentNotFoundError):
        await store.get(session_id="session-1", generation_id="generation-1")
    assert await store.release_session(session_id="session-2") == 1
    assert store.document_count == 0
