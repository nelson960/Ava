from __future__ import annotations

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from ava_backend.app import create_app
from ava_backend.config import AvaEnvironment, Settings
from ava_backend.realtime.memory import (
    DeliveredConversationTurn,
    DevelopmentAuthoritativeMemoryStore,
)


async def _client(
    store: DevelopmentAuthoritativeMemoryStore | None,
) -> AsyncClient:
    app = create_app(
        Settings(environment=AvaEnvironment.TEST),
        authoritative_memory=store,
    )
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testclient",
    )


async def test_weekly_recap_and_reset_use_authenticated_subject() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    await store.commit_delivered_turn(
        subject="development:127.0.0.1",
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
    now = datetime.now(UTC)
    monday = now.date().fromordinal(now.date().toordinal() - now.weekday())

    async with await _client(store) as client:
        recap = await client.get(
            "/v3/memory/weekly-recap",
            params={"period_start": monday.isoformat()},
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


async def test_v3_memory_endpoints_fail_visibly_when_store_is_not_configured() -> None:
    async with await _client(None) as client:
        response = await client.get(
            "/v3/memory/weekly-recap",
            headers={"X-Ava-Protocol-Version": "3"},
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "memory_unavailable"


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
    assert payload["promptVersion"] == "ava-realtime-v4.1"
    assert payload["toolContractVersion"] == "ava-realtime-tools-v4.1"
    assert payload["canvasSpecialistVersion"] == "ava-svg-canvas-v4.2"
    assert payload["build"]["version"] == "0.1.0"
    assert payload["build"]["instanceId"].startswith("instance-")
    assert payload["build"]["sourceRevision"].startswith("sha256:")
    assert payload["build"]["sourceCurrent"] is True
    assert response.headers["cache-control"] == "no-store"
