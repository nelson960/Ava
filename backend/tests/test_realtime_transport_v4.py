from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from ava_backend.app import create_app
from ava_backend.canvas.svg_delivery import SvgCanvasDocumentStore
from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.canvas.render_profile import CanvasRenderProfile
from ava_backend.canvas.svg_session import SvgCanvasSessionState
from ava_backend.canvas.svg_specialist import (
    SvgCanvasSpecialistGeneration,
    SvgCanvasVisibleDraft,
)
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
        self.render_profiles: list[CanvasRenderProfile | None] = []

    def create(
        self,
        *,
        session_id: str,
        request_id: str,
        subject: str,
        age_band: AgeBand,
        canvas_render_profile: CanvasRenderProfile | None = None,
    ) -> RealtimeSessionCoordinatorV4:
        assert session_id and request_id and subject and age_band
        if canvas_render_profile is not None:
            assert canvas_render_profile.usable_aspect_ratio > 0
        self.render_profiles.append(canvas_render_profile)
        coordinator = _Coordinator()
        self.coordinators.append(coordinator)
        return coordinator


class _RejectingCanvasRegistry:
    def __init__(self, failure: RealtimeSessionAuthFailure) -> None:
        self._failure = failure

    async def authorize_canvas_document(
        self,
        *,
        session_id: str,
        token: str,
        subject: str,
    ) -> None:
        assert session_id and token and subject
        raise RealtimeSessionAuthenticationError(self._failure)


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
            json={
                "protocolVersion": 4,
                "ageBand": "middleChildhood",
                "canvasRenderProfile": {
                    "usableAspectRatio": 1.6,
                    "orientation": "landscape",
                    "widthClass": "regular",
                    "heightClass": "compact",
                    "safeInsets": {
                        "top": 0.02,
                        "right": 0.03,
                        "bottom": 0.04,
                        "left": 0.03,
                    },
                    "touchEnvironment": "direct",
                    "minimumInteractionTargetPoints": 44,
                    "textScaleFactor": 1.15,
                },
            },
        )
        assert created.status_code == 201
        bootstrap = created.json()
        assert bootstrap["protocolVersion"] == 4
        assert bootstrap["canvasToken"]
        assert bootstrap["canvasDocumentBaseUrl"].endswith("/canvas-documents")
        assert bootstrap["rendererCapabilityVersion"] == "ava-svg-renderer-v2"
        assert bootstrap["limits"]["maxSvgBytes"] == 384 * 1024
        assert bootstrap["limits"]["maxManifestBytes"] == 64 * 1024
        render_profile = factory.render_profiles[0]
        assert render_profile is not None
        assert render_profile.usable_aspect_ratio == 1.6
        assert render_profile.safe_insets.bottom == 0.04

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
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
            'data-ava-interaction-mode="none">'
            "<title>Exact cloud</title><desc>A cloud.</desc>"
            '<ellipse cx="500" cy="250" rx="120" ry="60"/>'
            "</svg>\n"
        )
        generation = SvgCanvasSpecialistGeneration(
            generation_id="generation-v4",
            document=SvgDocumentValidator().validate(svg),
        )
        state = SvgCanvasSessionState()
        candidate = state.stage_generation(
            generation,
            source_turn_id="turn-v4",
            base_scene_id=None,
            base_revision=0,
            workspace_epoch=state.workspace.reset_epoch,
        )
        assert client.portal is not None
        client.portal.call(
            lambda: documents.publish(
                session_id=bootstrap["sessionId"],
                candidate=candidate,
            )
        )
        client.portal.call(
            lambda: documents.publish_draft(
                session_id=bootstrap["sessionId"],
                source_turn_id="turn-v4",
                draft=SvgCanvasVisibleDraft(
                    generation_id="generation-v4",
                    draft_index=1,
                    attempt=0,
                    document=generation.document,
                ),
            )
        )

        base = f"/v4/realtime/sessions/{bootstrap['sessionId']}/canvas-documents/generation-v4"
        headers = {
            "X-Ava-Protocol-Version": "4",
            "X-Ava-Canvas-Token": bootstrap["canvasToken"],
        }
        svg_response = client.get(f"{base}/svg", headers=headers)
        manifest_response = client.get(f"{base}/manifest", headers=headers)
        draft_response = client.get(
            f"/v4/realtime/sessions/{bootstrap['sessionId']}/canvas-drafts/"
            "generation-v4/1/svg",
            headers=headers,
        )

        assert svg_response.status_code == 200
        assert svg_response.content == generation.document.svg_bytes
        assert svg_response.headers["content-type"].startswith("image/svg+xml")
        assert svg_response.headers["x-content-sha256"] == (
            generation.document.svg_sha256
        )
        assert svg_response.headers["x-ava-canvas-authority"] == "candidate"
        assert manifest_response.status_code == 200
        assert manifest_response.content == generation.document.manifest_bytes
        assert manifest_response.headers["x-content-sha256"] == (
            generation.document.manifest_sha256
        )
        assert draft_response.status_code == 200
        assert draft_response.content == generation.document.svg_bytes
        assert draft_response.headers["x-ava-canvas-authority"] == "draft"
        assert "manifest" not in draft_response.headers

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
        active_session_ttl=timedelta(minutes=30),
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


async def test_claimed_v4_session_keeps_canvas_authorization_until_release() -> None:
    current = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    factory = _Factory()
    registry = RealtimeSessionRegistry(
        coordinator_factory=factory,
        token_codec=RealtimeSessionTokenCodec("c" * 32),
        ttl=timedelta(seconds=120),
        active_session_ttl=timedelta(minutes=30),
        max_sessions=1,
        now=lambda: current,
        session_id_factory=lambda: "session-canvas-lifetime",
    )
    created = await registry.create(
        request_id="request-canvas-lifetime",
        subject="child:canvas-lifetime",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    await registry.negotiate(
        session_id=created.session_id,
        token=created.webrtc_token,
        subject="child:canvas-lifetime",
        offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP,
    )
    await registry.claim(session_id=created.session_id, token=created.token)

    await registry.authorize_canvas_document(
        session_id=created.session_id,
        token=created.canvas_token,
        subject="child:canvas-lifetime",
    )

    current += timedelta(seconds=121)
    await registry.authorize_canvas_document(
        session_id=created.session_id,
        token=created.canvas_token,
        subject="child:canvas-lifetime",
    )

    await registry.release(created.session_id)
    with pytest.raises(
        RealtimeSessionAuthenticationError,
        match=RealtimeSessionAuthFailure.UNKNOWN,
    ):
        await registry.authorize_canvas_document(
            session_id=created.session_id,
            token=created.canvas_token,
            subject="child:canvas-lifetime",
        )


async def test_v4_event_and_webrtc_admission_remain_short_lived() -> None:
    current = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    factory = _Factory()
    ids = iter(
        (
            "session-expired-event-admission",
            "session-expired-webrtc-admission",
            "session-expired-unclaimed-canvas",
        )
    )
    registry = RealtimeSessionRegistry(
        coordinator_factory=factory,
        token_codec=RealtimeSessionTokenCodec("a" * 32),
        ttl=timedelta(seconds=120),
        active_session_ttl=timedelta(minutes=30),
        max_sessions=3,
        now=lambda: current,
        session_id_factory=lambda: next(ids),
    )
    event_admission = await registry.create(
        request_id="request-event-admission",
        subject="child:admission",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    webrtc_admission = await registry.create(
        request_id="request-webrtc-admission",
        subject="child:admission",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    unclaimed_canvas = await registry.create(
        request_id="request-unclaimed-canvas",
        subject="child:admission",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    await registry.negotiate(
        session_id=unclaimed_canvas.session_id,
        token=unclaimed_canvas.webrtc_token,
        subject="child:admission",
        offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP,
    )

    current += timedelta(seconds=121)

    with pytest.raises(
        RealtimeSessionAuthenticationError,
        match=RealtimeSessionAuthFailure.EXPIRED,
    ):
        await registry.claim(
            session_id=event_admission.session_id,
            token=event_admission.token,
        )
    with pytest.raises(
        RealtimeSessionAuthenticationError,
        match=RealtimeSessionAuthFailure.EXPIRED,
    ):
        await registry.negotiate(
            session_id=webrtc_admission.session_id,
            token=webrtc_admission.webrtc_token,
            subject="child:admission",
            offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP,
        )
    with pytest.raises(
        RealtimeSessionAuthenticationError,
        match=RealtimeSessionAuthFailure.EXPIRED,
    ):
        await registry.authorize_canvas_document(
            session_id=unclaimed_canvas.session_id,
            token=unclaimed_canvas.canvas_token,
            subject="child:admission",
        )


async def test_claimed_v4_canvas_authorization_expires_at_active_deadline() -> None:
    current = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    factory = _Factory()
    registry = RealtimeSessionRegistry(
        coordinator_factory=factory,
        token_codec=RealtimeSessionTokenCodec("d" * 32),
        ttl=timedelta(seconds=120),
        active_session_ttl=timedelta(minutes=30),
        max_sessions=1,
        now=lambda: current,
        session_id_factory=lambda: "session-active-deadline",
    )
    created = await registry.create(
        request_id="request-active-deadline",
        subject="child:active-deadline",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
    )
    await registry.negotiate(
        session_id=created.session_id,
        token=created.webrtc_token,
        subject="child:active-deadline",
        offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP,
    )
    current += timedelta(seconds=119)
    await registry.claim(session_id=created.session_id, token=created.token)

    current += timedelta(minutes=30)
    await registry.authorize_canvas_document(
        session_id=created.session_id,
        token=created.canvas_token,
        subject="child:active-deadline",
    )

    current += timedelta(microseconds=1)
    with pytest.raises(
        RealtimeSessionAuthenticationError,
        match=RealtimeSessionAuthFailure.EXPIRED,
    ):
        await registry.authorize_canvas_document(
            session_id=created.session_id,
            token=created.canvas_token,
            subject="child:active-deadline",
        )


@pytest.mark.parametrize(
    "failure",
    (
        RealtimeSessionAuthFailure.INVALID,
        RealtimeSessionAuthFailure.EXPIRED,
        RealtimeSessionAuthFailure.UNKNOWN,
        RealtimeSessionAuthFailure.NOT_READY,
    ),
)
def test_canvas_document_authorization_logs_content_free_failure(
    failure: RealtimeSessionAuthFailure,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(
        Settings(environment=AvaEnvironment.TEST),
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
        realtime_v4_coordinator_factory=_Factory(),
    )
    app.state.realtime_v4_sessions = _RejectingCanvasRegistry(failure)
    credential = "canvas-credential-must-not-be-logged"

    with TestClient(app, base_url="https://127.0.0.1") as client:
        with caplog.at_level(logging.WARNING, logger="ava_backend"):
            response = client.get(
                "/v4/realtime/sessions/session-diagnostic/"
                "canvas-documents/generation-diagnostic/svg",
                headers={
                    "X-Ava-Protocol-Version": "4",
                    "X-Ava-Canvas-Token": credential,
                },
            )

    assert response.status_code == 401
    assert "canvas_document_authorization_failed" in caplog.text
    assert f"failure={failure.value}" in caplog.text
    assert credential not in caplog.text
