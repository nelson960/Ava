from __future__ import annotations

from pydantic import SecretStr

from ava_backend.canvas.svg_delivery import SvgCanvasDocumentStore
from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_workspace import (
    ActiveSvgCanvasScene,
    CanvasWorkspaceManifest,
    CanvasWorkspaceManifestScene,
    SvgCanvasWorkspaceStore,
)
from ava_backend.config import (
    AvaEnvironment,
    RealtimeDeliveryMode,
    Settings,
)
from ava_backend.protocol import AgeBand
from ava_backend.realtime.coordinator import DeterministicRealtimeSessionCoordinator
from ava_backend.realtime.live_factory_v4 import LiveRealtimeCoordinatorFactoryV4
from ava_backend.realtime.memory import DevelopmentAuthoritativeMemoryStore


def _scene(index: int) -> ActiveSvgCanvasScene:
    document = SvgDocumentValidator().validate(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        'data-ava-interaction-mode="none">'
        f'<title>Scene {index}</title><desc>Scene {index} summary</desc>'
        f'<circle id="scene-{index}" cx="50" cy="50" r="20"/></svg>'
    )
    return ActiveSvgCanvasScene(
        generation_id=f"generation-{index}",
        source_turn_id=f"turn-{index}",
        scene_id=f"scene-{index}",
        revision=1,
        document=document,
        first_rendered_sample=0,
    )


def _manifest(scenes: list[ActiveSvgCanvasScene]) -> CanvasWorkspaceManifest:
    return CanvasWorkspaceManifest(
        protocol_version=4,
        workspace_epoch=1,
        workspace_revision=0,
        next_creation_sequence=len(scenes) + 1,
        next_modification_sequence=1,
        focused_scene_id=scenes[0].scene_id,
        scenes=tuple(
            CanvasWorkspaceManifestScene(
                scene_id=scene.scene_id,
                scene_revision=scene.revision,
                generation_id=scene.generation_id,
                source_turn_id=scene.source_turn_id,
                svg_sha256=scene.document.svg_sha256,
                manifest_sha256=scene.document.manifest_sha256,
                svg_byte_length=len(scene.document.svg_bytes),
                manifest_byte_length=len(scene.document.manifest_bytes),
                renderer_capability_version=scene.document.manifest[
                    "rendererCapabilityVersion"
                ],
                validator_version=scene.document.manifest["validatorVersion"],
                manifest_version=scene.document.manifest["manifestVersion"],
                creation_sequence=index,
                modification_sequence=None,
                title=scene.document.title,
                summary=scene.document.description,
            )
            for index, scene in enumerate(scenes, start=1)
        ),
    )


async def test_live_factory_resolves_subject_workspace_for_each_session() -> None:
    workspaces = SvgCanvasWorkspaceStore()
    factory = LiveRealtimeCoordinatorFactoryV4(
        settings=Settings(
            environment=AvaEnvironment.TEST,
            openai_api_key=SecretStr("test-openai-key"),
            realtime_delivery_mode=RealtimeDeliveryMode.CAPTION_PREVIEW,
            openai_realtime_truncation_post_instructions_tokens=64_000,
            openai_realtime_truncation_retention_ratio=0.75,
        ),
        memory=DevelopmentAuthoritativeMemoryStore(),
        documents=SvgCanvasDocumentStore(),
        canvas_workspaces=workspaces,
    )

    first = factory.create(
        session_id="session-factory-1",
        request_id="request-factory-1",
        subject="child:X",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        timezone_id="Asia/Kolkata",
    )
    second = factory.create(
        session_id="session-factory-2",
        request_id="request-factory-2",
        subject="child:X",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        timezone_id="Asia/Kolkata",
    )
    isolated = factory.create(
        session_id="session-factory-y",
        request_id="request-factory-y",
        subject="child:Y",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        timezone_id="Europe/London",
    )
    assert isinstance(first, DeterministicRealtimeSessionCoordinator)
    assert isinstance(second, DeterministicRealtimeSessionCoordinator)
    assert isinstance(isolated, DeterministicRealtimeSessionCoordinator)
    assert first._realtime._truncation_post_instructions_tokens == 64_000
    assert first._realtime._truncation_retention_ratio == 0.75
    assert first._timezone_id == "Asia/Kolkata"
    assert isolated._timezone_id == "Europe/London"

    first_workspace = first._canvas_jobs.state.workspace
    second_workspace = second._canvas_jobs.state.workspace
    isolated_workspace = isolated._canvas_jobs.state.workspace
    assert first_workspace is workspaces.workspace_for("child:X")
    assert second_workspace is first_workspace
    assert isolated_workspace is workspaces.workspace_for("child:Y")
    assert isolated_workspace is not first_workspace

    await first.close()
    await second.close()
    await isolated.close()


async def test_live_factory_hydrates_nonfocused_retained_scene_from_document_store() -> None:
    documents = SvgCanvasDocumentStore()
    factory = LiveRealtimeCoordinatorFactoryV4(
        settings=Settings(
            environment=AvaEnvironment.TEST,
            openai_api_key=SecretStr("test-openai-key"),
            realtime_delivery_mode=RealtimeDeliveryMode.CAPTION_PREVIEW,
            openai_realtime_truncation_post_instructions_tokens=64_000,
            openai_realtime_truncation_retention_ratio=0.75,
        ),
        memory=DevelopmentAuthoritativeMemoryStore(),
        documents=documents,
        canvas_workspaces=SvgCanvasWorkspaceStore(),
    )
    scenes = [_scene(1), _scene(2), _scene(3)]
    for scene in scenes:
        await documents.retain_scene(subject="child:X", scene=scene)

    coordinator = factory.create(
        session_id="session-retained-source",
        request_id="request-retained-source",
        subject="child:X",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        timezone_id="UTC",
    )
    coordinator.synchronize_canvas_workspace(_manifest(scenes))
    coordinator.restore_focused_canvas(scenes[0])
    workspace = coordinator._canvas_jobs.state.workspace
    assert workspace.scene("scene-2") is None

    assert await coordinator.ensure_retained_scene_hydrated(
        workspace,
        "scene-2",
        source="productionFactoryTest",
    )
    assert workspace.scene("scene-2") == scenes[1]
    await coordinator.close()
