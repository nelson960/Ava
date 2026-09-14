from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from ava_backend.app import create_app
from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_specialist import SVG_CANVAS_SPECIALIST_PROMPT_VERSION
from ava_backend.canvas.svg_workspace import (
    ActiveSvgCanvasScene,
    SvgCanvasWorkspaceStore,
)
from ava_backend.config import AvaEnvironment, Settings
from ava_backend.realtime.memory import (
    DeliveredConversationTurn,
    DevelopmentAuthoritativeMemoryStore,
)
from ava_backend.realtime.postgres_memory import PostgresMemoryStoreError


class _FailingResetMemoryStore(DevelopmentAuthoritativeMemoryStore):
    async def reset_conversation(self, *, subject: str):
        del subject
        raise PostgresMemoryStoreError("deterministic reset failure")


async def _client(
    store: DevelopmentAuthoritativeMemoryStore | None,
    canvas_workspaces: SvgCanvasWorkspaceStore | None = None,
) -> AsyncClient:
    app = create_app(
        Settings(environment=AvaEnvironment.TEST),
        authoritative_memory=store,
        svg_canvas_workspaces=canvas_workspaces,
    )
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testclient",
    )


async def test_weekly_recap_and_reset_use_authenticated_subject() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    await store.commit_delivered_turn(
        subject="development:127.0.0.1",
        expected_reset_revision=0,
        turn=DeliveredConversationTurn.model_validate(
            {
                "turnId": "turn-1",
                "childText": "I saw the moon.",
                "avaDeliveredText": "It looked bright tonight.",
                "deliveredSampleCount": 24_000,
                "childTurnSummary": "Talked about seeing the moon.",
                "terminal": "completed",
            }
        ),
        proposal=None,
        memory_id=None,
    )
    async with await _client(store) as client:
        recap = await client.get(
            "/v3/memory/weekly-recap",
            params={"timezoneId": "Asia/Kolkata"},
            headers={"X-Ava-Protocol-Version": "3"},
        )
        reset = await client.delete(
            "/v3/memory/conversations",
            headers={"X-Ava-Protocol-Version": "3"},
        )

    assert recap.status_code == 200
    assert recap.headers["cache-control"] == "no-store"
    assert [record["summary"] for record in recap.json()["records"]] == [
        "Talked about seeing the moon."
    ]
    assert reset.status_code == 200
    assert reset.json()["revision"] == 1


async def test_backend_reset_clears_only_authenticated_subject_workspace() -> None:
    memory = DevelopmentAuthoritativeMemoryStore()
    workspaces = SvgCanvasWorkspaceStore()
    workspace_x = workspaces.workspace_for("development:127.0.0.1")
    workspace_y = workspaces.workspace_for("child:Y")
    active_x = _active_scene("X")
    active_y = _active_scene("Y")
    initial_x_epoch = workspace_x.reset_epoch
    initial_y_epoch = workspace_y.reset_epoch
    workspace_x.commit(
        active_x,
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=workspace_x.reset_epoch,
    )
    workspace_y.commit(
        active_y,
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=workspace_y.reset_epoch,
    )

    async with await _client(memory, workspaces) as client:
        response = await client.delete(
            "/v3/memory/conversations",
            headers={"X-Ava-Protocol-Version": "3"},
        )

    assert response.status_code == 200
    assert workspace_x.reset_epoch == initial_x_epoch + 1
    assert workspace_x.current is None
    assert workspace_x.scenes == ()
    assert workspace_y.reset_epoch == initial_y_epoch
    assert workspace_y.current is active_y
    assert workspace_y.scenes == (active_y,)


async def test_failed_backend_reset_preserves_subject_workspace() -> None:
    workspaces = SvgCanvasWorkspaceStore()
    workspace = workspaces.workspace_for("development:127.0.0.1")
    active = _active_scene("X")
    initial_epoch = workspace.reset_epoch
    workspace.commit(
        active,
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=workspace.reset_epoch,
    )

    async with await _client(_FailingResetMemoryStore(), workspaces) as client:
        response = await client.delete(
            "/v3/memory/conversations",
            headers={"X-Ava-Protocol-Version": "3"},
        )

    assert response.status_code == 503
    assert workspace.reset_epoch == initial_epoch
    assert workspace.current is active
    assert workspace.scenes == (active,)


async def test_v3_memory_endpoints_fail_visibly_when_store_is_not_configured() -> None:
    async with await _client(None) as client:
        response = await client.get(
            "/v3/memory/weekly-recap",
            params={"timezoneId": "Asia/Kolkata"},
            headers={"X-Ava-Protocol-Version": "3"},
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "memory_unavailable"


async def test_weekly_recap_requires_a_valid_iana_timezone() -> None:
    async with await _client(DevelopmentAuthoritativeMemoryStore()) as client:
        missing = await client.get(
            "/v3/memory/weekly-recap",
            headers={"X-Ava-Protocol-Version": "3"},
        )
        malformed = await client.get(
            "/v3/memory/weekly-recap",
            params={"timezoneId": "IST"},
            headers={"X-Ava-Protocol-Version": "3"},
        )

    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "invalid_request"
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "invalid_request"


async def test_v3_memory_endpoints_require_protocol_header() -> None:
    async with await _client(DevelopmentAuthoritativeMemoryStore()) as client:
        response = await client.delete("/v3/memory/conversations")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "protocol_mismatch"


async def test_readiness_checks_configured_authoritative_memory() -> None:
    async with await _client(DevelopmentAuthoritativeMemoryStore()) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["protocolVersion"] == 3
    assert payload["promptVersion"] == "ava-realtime-v4.12"
    assert payload["toolContractVersion"] == "ava-realtime-tools-v4.8"
    assert payload["canvasSpecialistVersion"] == SVG_CANVAS_SPECIALIST_PROMPT_VERSION
    assert payload["build"]["version"] == "0.1.0"
    assert payload["build"]["instanceId"].startswith("instance-")
    assert payload["build"]["sourceRevision"].startswith("sha256:")
    assert payload["build"]["sourceCurrent"] is True
    assert response.headers["cache-control"] == "no-store"


def _active_scene(label: str) -> ActiveSvgCanvasScene:
    document = SvgDocumentValidator().validate(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
        'data-ava-interaction-mode="none">'
        f"<title>Scene {label}</title><desc>Subject canvas {label}.</desc>"
        "</svg>"
    )
    return ActiveSvgCanvasScene(
        generation_id=f"generation-{label}",
        source_turn_id=f"turn-{label}",
        scene_id=f"scene-{label}",
        revision=1,
        document=document,
        first_rendered_sample=4_800,
    )
