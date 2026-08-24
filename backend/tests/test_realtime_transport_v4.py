from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from ava_backend.app import create_app
from ava_backend.canvas.svg_delivery import SvgCanvasDocumentStore
from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.svg_session import SvgCanvasSessionState
from ava_backend.canvas.svg_specialist import SvgCanvasSpecialistGeneration
from ava_backend.config import AvaEnvironment, Settings
from ava_backend.protocol import AgeBand
from ava_backend.realtime.events_v4 import AvaClientEventUnionV4
from ava_backend.realtime.fakes import (
    DETERMINISTIC_AUDIO_ANSWER_SDP,
    DETERMINISTIC_AUDIO_OFFER_SDP,
)
from ava_backend.realtime.memory import DevelopmentAuthoritativeMemoryStore
from ava_backend.realtime.provider_session import NegotiatedRealtimeCall
from ava_backend.realtime.session import (
    RealtimeSessionAuthenticationError,
    RealtimeSessionAuthFailure,
    RealtimeSessionRegistry,
    RealtimeSessionTokenCodec,
)
from ava_backend.realtime.websocket_v4 import (
    AvaServerOutboundFrameV4,
    RealtimeSessionCoordinatorV4,
)


class _Coordinator:
    def __init__(self) -> None:
        self.closed = False
        self.received: list[AvaClientEventUnionV4] = []

    @property
    def turn_id(self) -> str:
        return "turn-v4"

    async def negotiate(self, *, offer_sdp: str) -> NegotiatedRealtimeCall:
        assert offer_sdp == DETERMINISTIC_AUDIO_OFFER_SDP
        return NegotiatedRealtimeCall(
            answer_sdp=DETERMINISTIC_AUDIO_ANSWER_SDP,
            provider_call_id="rtc_v4_call",
        )

    async def receive(self, event: AvaClientEventUnionV4) -> None:
        self.received.append(event)

    async def frames(self) -> AsyncIterator[AvaServerOutboundFrameV4]:
        if False:
            yield

    async def close(self) -> None:
        self.closed = True


class _Factory:
    def __init__(self) -> None:
        self.coordinators: list[_Coordinator] = []

    def create(
        self,
        *,
        session_id: str,
        request_id: str,
        subject: str,
        age_band: AgeBand,
    ) -> RealtimeSessionCoordinatorV4:
        assert session_id and request_id and subject and age_band
        coordinator = _Coordinator()
        self.coordinators.append(coordinator)
        return coordinator


def test_v4_bootstrap_and_independent_exact_document_fetch() -> None:
    factory = _Factory()
    documents = SvgCanvasDocumentStore()
    app = create_app(
        Settings(
            environment=AvaEnvironment.TEST,
            realtime_session_token_secret=SecretStr("v" * 32),
            realtime_public_event_origin="wss://ava.example.test",
        ),
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
        realtime_v4_coordinator_factory=factory,
        svg_document_store=documents,
    )
    with TestClient(app, base_url="https://127.0.0.1") as client:
        created = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "middleChildhood"},
        )
        assert created.status_code == 201
        bootstrap = created.json()
        assert bootstrap["protocolVersion"] == 4
        assert bootstrap["canvasToken"]
        assert bootstrap["canvasDocumentBaseUrl"].endswith("/canvas-documents")
        assert bootstrap["rendererCapabilityVersion"] == "ava-svg-renderer-v1"

        negotiated = client.post(
            f"/v4/realtime/sessions/{bootstrap['sessionId']}/webrtc",
            headers={
                "Authorization": f"Bearer {bootstrap['webrtcToken']}",
                "Content-Type": "application/sdp",
                "X-Ava-Protocol-Version": "4",
            },
            content=DETERMINISTIC_AUDIO_OFFER_SDP,
        )
        assert negotiated.status_code == 200
        assert negotiated.text == DETERMINISTIC_AUDIO_ANSWER_SDP

        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600">'
            "<title>Exact cloud</title><desc>A cloud.</desc>"
            '<ellipse cx="500" cy="250" rx="120" ry="60"/>'
            "</svg>\n"
        )
        generation = SvgCanvasSpecialistGeneration(
            generation_id="generation-v4",
            document=SvgDocumentValidator().validate(svg),
        )
        state = SvgCanvasSessionState()
        candidate = state.stage_generation(generation, source_turn_id="turn-v4")
        assert client.portal is not None
        client.portal.call(
            lambda: documents.publish(
                session_id=bootstrap["sessionId"],
                candidate=candidate,
            )
        )

        base = f"/v4/realtime/sessions/{bootstrap['sessionId']}/canvas-documents/generation-v4"
        headers = {
            "X-Ava-Protocol-Version": "4",
            "X-Ava-Canvas-Token": bootstrap["canvasToken"],
        }
        svg_response = client.get(f"{base}/svg", headers=headers)
        manifest_response = client.get(f"{base}/manifest", headers=headers)

        assert svg_response.status_code == 200
        assert svg_response.content == generation.document.svg_bytes
        assert svg_response.headers["content-type"].startswith("image/svg+xml")
        assert svg_response.headers["x-content-sha256"] == (
            generation.document.svg_sha256
        )
        assert manifest_response.status_code == 200
        assert manifest_response.content == generation.document.manifest_bytes
        assert manifest_response.headers["x-content-sha256"] == (
            generation.document.manifest_sha256
        )

        wrong_scope = client.get(
            f"{base}/svg",
            headers={
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Canvas-Token": bootstrap["sessionToken"],
            },
        )
        assert wrong_scope.status_code == 401


def test_v4_routes_are_unavailable_until_v4_factory_is_configured() -> None:
    app = create_app(
        Settings(environment=AvaEnvironment.TEST),
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
    )
    with TestClient(app, base_url="https://127.0.0.1") as client:
        response = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "unknown"},
        )

    assert response.status_code == 503


async def test_v4_registry_tokens_expire_cannot_replay_and_close_owners() -> None:
    current = datetime(2026, 7, 30, 10, 0, tzinfo=UTC)
    factory = _Factory()
    ids = iter(("session-registry-1", "session-registry-2"))
    registry = RealtimeSessionRegistry(
        coordinator_factory=factory,
        token_codec=RealtimeSessionTokenCodec("r" * 32),
        ttl=timedelta(seconds=30),
        max_sessions=2,
        now=lambda: current,
        session_id_factory=lambda: next(ids),
    )
    first = await registry.create(
        request_id="request-registry-1",
        subject="child:registry",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    await registry.negotiate(
        session_id=first.session_id,
        token=first.webrtc_token,
        subject="child:registry",
        offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP,
    )
    with pytest.raises(
        RealtimeSessionAuthenticationError,
        match=RealtimeSessionAuthFailure.INVALID,
    ):
        await registry.claim(
            session_id=first.session_id,
            token=first.webrtc_token,
        )
    assert (
        await registry.claim(
            session_id=first.session_id,
            token=first.token,
        )
        is factory.coordinators[0]
    )
    with pytest.raises(
        RealtimeSessionAuthenticationError,
        match=RealtimeSessionAuthFailure.CLAIMED,
    ):
        await registry.claim(session_id=first.session_id, token=first.token)

    current += timedelta(seconds=31)
    assert await registry.purge_expired() == 0
    await registry.release(first.session_id)
    assert factory.coordinators[0].closed is True

    second = await registry.create(
        request_id="request-registry-2",
        subject="child:registry",
        age_band=AgeBand.PRETEEN,
    )
    current += timedelta(seconds=31)
    with pytest.raises(
        RealtimeSessionAuthenticationError,
        match=RealtimeSessionAuthFailure.EXPIRED,
    ):
        await registry.claim(session_id=second.session_id, token=second.token)
    assert await registry.purge_expired() == 1
    assert factory.coordinators[1].closed is True
