from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from ava_backend.app import create_app
from ava_backend.canvas.svg_delivery import SvgCanvasDocumentStore
from ava_backend.canvas.svg_document import (
    SVG_MANIFEST_VERSION,
    SVG_PROTOCOL_MAX_BYTES,
    SvgDocumentValidator,
    SvgDraftSafetyValidator,
)
from ava_backend.canvas.render_profile import CanvasRenderProfile
from ava_backend.canvas.svg_session import SvgCanvasSessionState
from ava_backend.canvas.svg_workspace import (
    ActiveSvgCanvasScene,
    SvgCanvasWorkspace,
    SvgCanvasWorkspaceStore,
)
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
        self.memory_reset_revisions: list[int] = []
        self.restored_scene: ActiveSvgCanvasScene | None = None

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

    async def memory_reset(self, *, reset_revision: int) -> None:
        self.memory_reset_revisions.append(reset_revision)

    def restore_focused_canvas(self, scene: ActiveSvgCanvasScene) -> None:
        assert self.restored_scene is None
        self.restored_scene = scene


class _Factory:
    def __init__(self) -> None:
        self.coordinators: list[_Coordinator] = []
        self.render_profiles: list[CanvasRenderProfile | None] = []
        self.timezone_ids: list[str] = []

    def create(
        self,
        *,
        session_id: str,
        request_id: str,
        subject: str,
        age_band: AgeBand,
        timezone_id: str,
        canvas_render_profile: CanvasRenderProfile | None = None,
    ) -> RealtimeSessionCoordinatorV4:
        assert session_id and request_id and subject and age_band
        if canvas_render_profile is not None:
            assert canvas_render_profile.usable_aspect_ratio > 0
        self.render_profiles.append(canvas_render_profile)
        self.timezone_ids.append(timezone_id)
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


@pytest.mark.parametrize("timezone_id", ["Not/A_Real_Zone", "", " Asia/Kolkata"])
def test_v4_session_rejects_invalid_device_timezone(timezone_id: str) -> None:
    factory = _Factory()
    app = create_app(
        Settings(
            environment=AvaEnvironment.TEST,
            realtime_session_token_secret=SecretStr("v" * 32),
        ),
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
        realtime_v4_coordinator_factory=factory,
    )

    with TestClient(app, base_url="https://127.0.0.1") as client:
        response = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={
                "protocolVersion": 4,
                "ageBand": "unknown",
                "timezoneId": timezone_id,
            },
        )

    assert response.status_code == 422
    assert factory.timezone_ids == []


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
                "timezoneId": "Asia/Kolkata",
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
        assert factory.timezone_ids == ["Asia/Kolkata"]

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
                    document=SvgDraftSafetyValidator(SvgDocumentValidator()).validate(
                        generation.document.svg_text
                    ),
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


def test_focused_canvas_restore_revalidates_before_webrtc_negotiation() -> None:
    factory = _Factory()
    app = create_app(
        Settings(
            environment=AvaEnvironment.TEST,
            realtime_session_token_secret=SecretStr("h" * 32),
        ),
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
        realtime_v4_coordinator_factory=factory,
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        'data-ava-interaction-mode="explore">'
        "<title>Restored sun</title><desc>A tappable sun.</desc>"
        '<circle id="sun" data-ava-binding="sun-binding" '
        'data-ava-target="sun" aria-label="Sun" cx="50" cy="50" r="20"/>'
        "</svg>"
    )
    validated = SvgDocumentValidator().validate(svg)
    with TestClient(app, base_url="https://127.0.0.1") as client:
        created = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "unknown", "timezoneId": "UTC"},
        )
        bootstrap = created.json()
        restore_headers = {
            "Content-Type": "application/octet-stream",
            "X-Ava-Protocol-Version": "4",
            "X-Ava-Canvas-Token": bootstrap["canvasToken"],
            "X-Ava-Generation-ID": "generation-restored",
            "X-Ava-Source-Turn-ID": "turn-restored",
            "X-Ava-Scene-ID": "scene-restored",
            "X-Ava-Scene-Revision": "4",
            "X-Ava-SVG-SHA256": validated.svg_sha256,
            "X-Ava-Manifest-SHA256": validated.manifest_sha256,
        }
        restored = client.put(
            f"/v4/realtime/sessions/{bootstrap['sessionId']}/canvas-restore",
            headers=restore_headers,
            content=validated.svg_bytes,
        )
        assert restored.status_code == 204
        scene = factory.coordinators[0].restored_scene
        assert scene is not None
        assert scene.revision == 4
        assert scene.document.svg_bytes == validated.svg_bytes
        assert scene.document.manifest_bytes == validated.manifest_bytes
        assert scene.document.manifest_sha256 == validated.manifest_sha256
        assert scene.document.manifest["manifestVersion"] == SVG_MANIFEST_VERSION
        assert tuple(scene.document.bindings) == ("sun-binding",)

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

        replay = client.put(
            f"/v4/realtime/sessions/{bootstrap['sessionId']}/canvas-restore",
            headers=restore_headers,
            content=validated.svg_bytes,
        )
        assert replay.status_code == 401


def test_canvas_restore_rejects_tampered_svg_and_manifest_hash() -> None:
    factory = _Factory()
    app = create_app(
        Settings(
            environment=AvaEnvironment.TEST,
            realtime_session_token_secret=SecretStr("j" * 32),
        ),
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
        realtime_v4_coordinator_factory=factory,
    )
    document = SvgDocumentValidator().validate(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" '
        'data-ava-interaction-mode="none"><title>A</title><desc>B</desc>'
        '<circle cx="5" cy="5" r="2"/></svg>'
    )
    with TestClient(app, base_url="https://127.0.0.1") as client:
        bootstrap = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "unknown", "timezoneId": "UTC"},
        ).json()
        response = client.put(
            f"/v4/realtime/sessions/{bootstrap['sessionId']}/canvas-restore",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Canvas-Token": bootstrap["canvasToken"],
                "X-Ava-Generation-ID": "generation-tampered",
                "X-Ava-Source-Turn-ID": "turn-tampered",
                "X-Ava-Scene-ID": "scene-tampered",
                "X-Ava-Scene-Revision": "1",
                "X-Ava-SVG-SHA256": document.svg_sha256,
                "X-Ava-Manifest-SHA256": "0" * 64,
            },
            content=document.svg_bytes + b" ",
        )
    assert response.status_code == 422
    assert factory.coordinators[0].restored_scene is None


def test_canvas_restore_has_route_specific_absolute_body_bound() -> None:
    factory = _Factory()
    app = create_app(
        Settings(
            environment=AvaEnvironment.TEST,
            realtime_session_token_secret=SecretStr("k" * 32),
            request_max_bytes=16 * 1024,
        ),
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
        realtime_v4_coordinator_factory=factory,
    )
    with TestClient(app, base_url="https://127.0.0.1") as client:
        bootstrap = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "unknown", "timezoneId": "UTC"},
        ).json()
        response = client.put(
            f"/v4/realtime/sessions/{bootstrap['sessionId']}/canvas-restore",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Canvas-Token": bootstrap["canvasToken"],
                "X-Ava-Generation-ID": "generation-oversized",
                "X-Ava-Source-Turn-ID": "turn-oversized",
                "X-Ava-Scene-ID": "scene-oversized",
                "X-Ava-Scene-Revision": "1",
                "X-Ava-SVG-SHA256": "0" * 64,
                "X-Ava-Manifest-SHA256": "0" * 64,
            },
            content=b"x" * (SVG_PROTOCOL_MAX_BYTES + 1),
        )

    assert response.status_code == 413
    assert factory.coordinators[0].restored_scene is None


def test_v4_routes_are_unavailable_until_v4_factory_is_configured() -> None:
    app = create_app(
        Settings(environment=AvaEnvironment.TEST),
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
    )
    with TestClient(app, base_url="https://127.0.0.1") as client:
        response = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "unknown", "timezoneId": "UTC"},
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
        timezone_id="UTC",
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
        timezone_id="UTC",
    )
    current += timedelta(seconds=31)
    with pytest.raises(
        RealtimeSessionAuthenticationError,
        match=RealtimeSessionAuthFailure.EXPIRED,
    ):
        await registry.claim(session_id=second.session_id, token=second.token)
    assert await registry.purge_expired() == 1
    assert factory.coordinators[1].closed is True


async def test_v4_registry_memory_reset_notifies_only_matching_subject() -> None:
    factory = _Factory()
    ids = iter(("session-reset-a", "session-reset-b", "session-reset-other"))
    registry = RealtimeSessionRegistry(
        coordinator_factory=factory,
        token_codec=RealtimeSessionTokenCodec("m" * 32),
        ttl=timedelta(seconds=120),
        active_session_ttl=timedelta(minutes=30),
        max_sessions=3,
        session_id_factory=lambda: next(ids),
    )
    for subject in ("child:reset", "child:reset", "child:other"):
        await registry.create(
            request_id=f"request-{subject}-{len(factory.coordinators)}",
            subject=subject,
            age_band=AgeBand.MIDDLE_CHILDHOOD,
            timezone_id="UTC",
        )

    await registry.notify_memory_reset(
        subject="child:reset",
        reset_revision=3,
    )

    assert factory.coordinators[0].memory_reset_revisions == [3]
    assert factory.coordinators[1].memory_reset_revisions == [3]
    assert factory.coordinators[2].memory_reset_revisions == []


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
        timezone_id="UTC",
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
        timezone_id="UTC",
    )
    webrtc_admission = await registry.create(
        request_id="request-webrtc-admission",
        subject="child:admission",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        timezone_id="UTC",
    )
    unclaimed_canvas = await registry.create(
        request_id="request-unclaimed-canvas",
        subject="child:admission",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        timezone_id="UTC",
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
        timezone_id="UTC",
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


class _RealWorkspaceCoordinator(_Coordinator):
    def __init__(self, workspace: SvgCanvasWorkspace, *, inherit_focus: bool = False) -> None:
        super().__init__()
        self.workspace = workspace
        self.state = SvgCanvasSessionState(workspace=workspace, inherit_focus=inherit_focus)
        self.restored_scene: ActiveSvgCanvasScene | None = None

    def restore_focused_canvas(self, scene: ActiveSvgCanvasScene) -> None:
        self.state.restore_focused(scene)
        self.restored_scene = scene


class _RealWorkspaceFactory:
    def __init__(self, workspace_store: SvgCanvasWorkspaceStore) -> None:
        self.workspace_store = workspace_store
        self.coordinators: list[_RealWorkspaceCoordinator] = []

    def create(
        self,
        *,
        session_id: str,
        request_id: str,
        subject: str,
        age_band: AgeBand,
        timezone_id: str,
        canvas_render_profile: CanvasRenderProfile | None = None,
    ) -> RealtimeSessionCoordinatorV4:
        workspace = self.workspace_store.workspace_for(subject)
        coord = _RealWorkspaceCoordinator(workspace, inherit_focus=False)
        self.coordinators.append(coord)
        return coord


def test_canvas_restore_across_consecutive_sessions_with_shared_store() -> None:
    workspace_store = SvgCanvasWorkspaceStore()
    factory = _RealWorkspaceFactory(workspace_store)
    app = create_app(
        Settings(
            environment=AvaEnvironment.TEST,
            realtime_session_token_secret=SecretStr("r" * 32),
        ),
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
        realtime_v4_coordinator_factory=factory,
        svg_canvas_workspaces=workspace_store,
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        'data-ava-interaction-mode="explore">'
        "<title>Explore the Moon's lunar cycle</title><desc>Moon phases</desc>"
        '<circle id="moon" data-ava-binding="step-1" '
        'data-ava-target="moon" aria-label="Moon" cx="50" cy="50" r="20"/>'
        "</svg>"
    )
    validated = SvgDocumentValidator().validate(svg)
    scene_id = "scene_eb29fcfd30725883"
    scene_revision = 2
    generation_id = "generation-900d84f56b5c4da99f867d933caa85ce"
    source_turn_id = "turn-067e48290ee9405289b14de52b6d29ab"

    with TestClient(app, base_url="https://127.0.0.1") as client:
        # Session 1:
        s1 = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "middleChildhood", "timezoneId": "UTC"},
        ).json()
        headers_s1 = {
            "Content-Type": "application/octet-stream",
            "X-Ava-Protocol-Version": "4",
            "X-Ava-Canvas-Token": s1["canvasToken"],
            "X-Ava-Generation-ID": generation_id,
            "X-Ava-Source-Turn-ID": source_turn_id,
            "X-Ava-Scene-ID": scene_id,
            "X-Ava-Scene-Revision": str(scene_revision),
            "X-Ava-SVG-SHA256": validated.svg_sha256,
            "X-Ava-Manifest-SHA256": validated.manifest_sha256,
        }
        res1 = client.put(
            f"/v4/realtime/sessions/{s1['sessionId']}/canvas-restore",
            headers=headers_s1,
            content=validated.svg_bytes,
        )
        assert res1.status_code == 204
        coord_s1 = factory.coordinators[0]
        assert len(coord_s1.workspace.scenes) == 1
        assert coord_s1.state.focused_scene is not None
        assert coord_s1.state.focused_scene.scene_id == scene_id

        # Session 1 ends, Session 2 begins for the same subject on the same running server:
        s2 = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "middleChildhood", "timezoneId": "UTC"},
        ).json()
        coord_s2 = factory.coordinators[1]
        assert coord_s2.state.active is None

        headers_s2 = dict(headers_s1)
        headers_s2["X-Ava-Canvas-Token"] = s2["canvasToken"]

        # Crucial live failure reproduction: previously this returned 422
        # "The restored Canvas document is invalid." because self._scenes was not empty.
        res2 = client.put(
            f"/v4/realtime/sessions/{s2['sessionId']}/canvas-restore",
            headers=headers_s2,
            content=validated.svg_bytes,
        )
        assert res2.status_code == 204
        # Assert no duplicate scenes in retained workspace:
        assert coord_s1.workspace is coord_s2.workspace
        assert len(coord_s1.workspace.scenes) == 1
        assert coord_s2.state.focused_scene is not None
        assert coord_s2.state.focused_scene.scene_id == scene_id
        assert coord_s2.state.focused_scene.revision == scene_revision


def test_speech_grounding_safety_when_canvas_restore_fails_on_fresh_session() -> None:
    workspace_store = SvgCanvasWorkspaceStore()
    factory = _RealWorkspaceFactory(workspace_store)
    app = create_app(
        Settings(
            environment=AvaEnvironment.TEST,
            realtime_session_token_secret=SecretStr("s" * 32),
        ),
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
        realtime_v4_coordinator_factory=factory,
        svg_canvas_workspaces=workspace_store,
    )
    svg_a = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        'data-ava-interaction-mode="explore">'
        "<title>Scene A: Water Cycle</title><desc>Water cycle</desc>"
        '<circle id="water" data-ava-binding="water-binding" '
        'data-ava-target="water" aria-label="Water" cx="50" cy="50" r="20"/>'
        "</svg>"
    )
    val_a = SvgDocumentValidator().validate(svg_a)

    with TestClient(app, base_url="https://127.0.0.1") as client:
        # Session 1: populates Scene A
        s1 = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "middleChildhood", "timezoneId": "UTC"},
        ).json()
        res1 = client.put(
            f"/v4/realtime/sessions/{s1['sessionId']}/canvas-restore",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Canvas-Token": s1["canvasToken"],
                "X-Ava-Generation-ID": "gen-a",
                "X-Ava-Source-Turn-ID": "turn-a",
                "X-Ava-Scene-ID": "scene-a",
                "X-Ava-Scene-Revision": "1",
                "X-Ava-SVG-SHA256": val_a.svg_sha256,
                "X-Ava-Manifest-SHA256": val_a.manifest_sha256,
            },
            content=val_a.svg_bytes,
        )
        assert res1.status_code == 204
        coord1 = factory.coordinators[0]
        assert coord1.state.focused_scene is not None
        assert coord1.state.focused_scene.scene_id == "scene-a"

        # Session 2 begins for the same subject:
        s2 = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "middleChildhood", "timezoneId": "UTC"},
        ).json()

        coord2 = factory.coordinators[-1]
        # Session 2 has no active focus at start (inherit_focus=False):
        assert coord2.state.active is None
        # Session 1's focused scene is NOT mutated:
        assert coord1.state.focused_scene is not None
        assert coord1.state.focused_scene.scene_id == "scene-a"

        # Now client attempts to restore tampered/invalid Scene B into Session 2:
        res2 = client.put(
            f"/v4/realtime/sessions/{s2['sessionId']}/canvas-restore",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Canvas-Token": s2["canvasToken"],
                "X-Ava-Generation-ID": "gen-b",
                "X-Ava-Source-Turn-ID": "turn-b",
                "X-Ava-Scene-ID": "scene-b",
                "X-Ava-Scene-Revision": "1",
                "X-Ava-SVG-SHA256": "0" * 64,  # tampered hash
                "X-Ava-Manifest-SHA256": "0" * 64,
            },
            content=val_a.svg_bytes,
        )
        assert res2.status_code == 422

        # GROUNDING SAFETY: Restore failed, so Session 2's state.active MUST remain None!
        # It must NOT fall back to Scene A!
        assert coord2.state.active is None
        # And Session 1's state was still never touched:
        assert coord1.state.focused_scene is not None
        assert coord1.state.focused_scene.scene_id == "scene-a"


def test_canvas_restore_overlapping_sessions_with_shared_store() -> None:
    workspace_store = SvgCanvasWorkspaceStore()
    factory = _RealWorkspaceFactory(workspace_store)
    app = create_app(
        Settings(
            environment=AvaEnvironment.TEST,
            realtime_session_token_secret=SecretStr("o" * 32),
        ),
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
        realtime_v4_coordinator_factory=factory,
        svg_canvas_workspaces=workspace_store,
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        'data-ava-interaction-mode="explore">'
        "<title>Explore the Stars</title><desc>Constellations</desc>"
        '<circle id="star" data-ava-binding="step-1" '
        'data-ava-target="star" aria-label="Star" cx="50" cy="50" r="20"/>'
        "</svg>"
    )
    validated = SvgDocumentValidator().validate(svg)
    scene_id = "scene_star_123"
    scene_revision = 1
    generation_id = "generation-star-123"
    source_turn_id = "turn-star-123"

    with TestClient(app, base_url="https://127.0.0.1") as client:
        # Session A:
        s1 = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "middleChildhood", "timezoneId": "UTC"},
        ).json()
        res1 = client.put(
            f"/v4/realtime/sessions/{s1['sessionId']}/canvas-restore",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Canvas-Token": s1["canvasToken"],
                "X-Ava-Generation-ID": generation_id,
                "X-Ava-Source-Turn-ID": source_turn_id,
                "X-Ava-Scene-ID": scene_id,
                "X-Ava-Scene-Revision": str(scene_revision),
                "X-Ava-SVG-SHA256": validated.svg_sha256,
                "X-Ava-Manifest-SHA256": validated.manifest_sha256,
            },
            content=validated.svg_bytes,
        )
        assert res1.status_code == 204
        coord_a = factory.coordinators[0]
        assert coord_a.state.focused_scene is not None
        assert coord_a.state.focused_scene.scene_id == scene_id

        # Session B is created for the same subject WHILE Session A remains alive:
        s2 = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "middleChildhood", "timezoneId": "UTC"},
        ).json()
        coord_b = factory.coordinators[1]

        # REQUIREMENT 1: Session A still sees and uses Scene A
        assert coord_a.state.focused_scene is not None
        assert coord_a.state.focused_scene.scene_id == scene_id

        # REQUIREMENT 2: Session B does NOT treat Scene A as accepted focus before its own restore
        assert coord_b.state.active is None

        # REQUIREMENT 3: Session B restore of valid local scene succeeds
        res2 = client.put(
            f"/v4/realtime/sessions/{s2['sessionId']}/canvas-restore",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Canvas-Token": s2["canvasToken"],
                "X-Ava-Generation-ID": generation_id,
                "X-Ava-Source-Turn-ID": source_turn_id,
                "X-Ava-Scene-ID": scene_id,
                "X-Ava-Scene-Revision": str(scene_revision),
                "X-Ava-SVG-SHA256": validated.svg_sha256,
                "X-Ava-Manifest-SHA256": validated.manifest_sha256,
            },
            content=validated.svg_bytes,
        )
        assert res2.status_code == 204
        assert coord_b.state.focused_scene is not None
        assert coord_b.state.focused_scene.scene_id == scene_id

        # REQUIREMENT 4: No duplicate retained scene
        assert coord_a.workspace is coord_b.workspace
        assert len(coord_a.workspace.scenes) == 1

        # Close Session A:
        # Verify Session B remains correct:
        assert coord_b.state.focused_scene is not None
        assert coord_b.state.focused_scene.scene_id == scene_id
