from __future__ import annotations

from pydantic import SecretStr

from ava_backend.canvas.svg_delivery import SvgCanvasDocumentStore
from ava_backend.canvas.svg_workspace import SvgCanvasWorkspaceStore
from ava_backend.config import (
    AvaEnvironment,
    RealtimeDeliveryMode,
    Settings,
)
from ava_backend.protocol import AgeBand
from ava_backend.realtime.coordinator import DeterministicRealtimeSessionCoordinator
from ava_backend.realtime.live_factory_v4 import LiveRealtimeCoordinatorFactoryV4
from ava_backend.realtime.memory import DevelopmentAuthoritativeMemoryStore


async def test_live_factory_resolves_subject_workspace_for_each_session() -> None:
    workspaces = SvgCanvasWorkspaceStore()
    factory = LiveRealtimeCoordinatorFactoryV4(
        settings=Settings(
            environment=AvaEnvironment.TEST,
            openai_api_key=SecretStr("test-openai-key"),
            realtime_delivery_mode=RealtimeDeliveryMode.CAPTION_PREVIEW,
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
    )
    second = factory.create(
        session_id="session-factory-2",
        request_id="request-factory-2",
        subject="child:X",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    isolated = factory.create(
        session_id="session-factory-y",
        request_id="request-factory-y",
        subject="child:Y",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    assert isinstance(first, DeterministicRealtimeSessionCoordinator)
    assert isinstance(second, DeterministicRealtimeSessionCoordinator)
    assert isinstance(isolated, DeterministicRealtimeSessionCoordinator)

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
