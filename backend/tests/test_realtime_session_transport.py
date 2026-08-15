from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect

from ava_backend.app import create_app
from ava_backend.config import AvaEnvironment, Settings
from ava_backend.protocol import AgeBand
from ava_backend.realtime.coordinator import (
    AvaServerOutboundFrame,
    DeterministicRealtimeSessionCoordinator,
    RealtimeSessionCoordinator,
)
from ava_backend.realtime.events import AvaClientEventUnion
from ava_backend.realtime.fakes import (
    DETERMINISTIC_AUDIO_ANSWER_SDP,
    DETERMINISTIC_AUDIO_OFFER_SDP,
    DeterministicCanvasSpecialist,
    DeterministicRealtimePlanProvider,
    DeterministicStreamingSpeechSynthesizer,
)
from ava_backend.realtime.model_plan import model_canvas_draft_from_runtime_plan
from ava_backend.realtime.memory import DevelopmentAuthoritativeMemoryStore
from ava_backend.realtime.plan import AvaTurnPlan
from ava_backend.realtime.provider_session import NegotiatedRealtimeCall
from ava_backend.realtime.session import (
    AvaSessionCreateRequest,
    RealtimeCoordinatorFactory,
    RealtimeSessionAuthenticationError,
    RealtimeSessionAuthFailure,
    RealtimeSessionRegistry,
    RealtimeSessionTokenCodec,
)
from ava_backend.realtime.websocket import AvaRealtimeWebSocketBridge

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"


class _LegacyModel:
    async def generate_response(self, **_arguments: object) -> object:
        raise AssertionError("Legacy model must not run for realtime session tests")


class _ClosableCoordinator:
    def __init__(self) -> None:
        self.closed = False

    async def negotiate(self, *, offer_sdp: str) -> NegotiatedRealtimeCall:
        assert offer_sdp == DETERMINISTIC_AUDIO_OFFER_SDP
        return NegotiatedRealtimeCall(
            answer_sdp=DETERMINISTIC_AUDIO_ANSWER_SDP,
            provider_call_id="rtc_registry_fake_call",
        )

    async def receive(self, _event: AvaClientEventUnion) -> None:
        raise AssertionError("Registry test coordinator must not receive controls")

    async def frames(self) -> AsyncIterator[AvaServerOutboundFrame]:
        if False:
            yield

    async def close(self) -> None:
        self.closed = True


class _ClosableFactory:
    def __init__(self) -> None:
        self.created: list[_ClosableCoordinator] = []

    def create(
        self,
        *,
        session_id: str,
        request_id: str,
        subject: str,
        age_band: AgeBand,
    ) -> RealtimeSessionCoordinator:
        assert session_id
        assert request_id
        assert subject
        assert age_band
        coordinator = _ClosableCoordinator()
        self.created.append(coordinator)
        return coordinator


class _CoordinatorFactory(RealtimeCoordinatorFactory):
    def __init__(self) -> None:
        self.plan = AvaTurnPlan.model_validate_json(
            (FIXTURE_ROOT / "ava_turn_plan_v3.json").read_text(encoding="utf-8")
        )
        self.memory = DevelopmentAuthoritativeMemoryStore()
        self.coordinators: list[DeterministicRealtimeSessionCoordinator] = []

    def create(
        self,
        *,
        session_id: str,
        request_id: str,
        subject: str,
        age_band: AgeBand,
    ) -> RealtimeSessionCoordinator:
        coordinator = DeterministicRealtimeSessionCoordinator(
            session_id=session_id,
            turn_id=f"turn-{session_id.removeprefix('session-')}",
            request_id=request_id,
            stream_id=f"stream-{session_id.removeprefix('session-')}",
            subject=subject,
            age_band=age_band,
            realtime=DeterministicRealtimePlanProvider(
                self.plan,
                child_text="Please explain a plant cell.",
                canvas_brief="Draw a labeled interactive plant cell.",
                chunk_sizes=(2, 7, 19, 3),
            ),
            speech=DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31),
            canvas_specialist=DeterministicCanvasSpecialist(
                model_canvas_draft_from_runtime_plan(self.plan)
            ),
            memory=self.memory,
            memory_id_factory=lambda turn_id: f"memory:{turn_id}",
        )
        self.coordinators.append(coordinator)
        return coordinator


def test_shared_session_request_fixture_is_strict_v3() -> None:
    request = AvaSessionCreateRequest.model_validate_json(
        (FIXTURE_ROOT / "ava_session_request_v3.json").read_text(encoding="utf-8")
    )
    assert request.model_dump(mode="json", by_alias=True) == {
        "protocolVersion": 3,
        "ageBand": "middleChildhood",
    }


async def test_registry_tokens_expire_cannot_replay_and_close_owners() -> None:
    current = datetime(2026, 7, 30, 10, 0, tzinfo=UTC)
    factory = _ClosableFactory()
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
    negotiated = await registry.negotiate(
        session_id=first.session_id,
        token=first.webrtc_token,
        subject="child:registry",
        offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP,
    )
    assert negotiated.provider_call_id == "rtc_registry_fake_call"
    with pytest.raises(
        RealtimeSessionAuthenticationError,
        match=RealtimeSessionAuthFailure.INVALID,
    ):
        await registry.claim(
            session_id=first.session_id,
            token=first.webrtc_token,
        )
    claimed = await registry.claim(
        session_id=first.session_id,
        token=first.token,
    )
    assert claimed is factory.created[0]
    with pytest.raises(
        RealtimeSessionAuthenticationError,
        match=RealtimeSessionAuthFailure.CLAIMED,
    ):
        await registry.claim(
            session_id=first.session_id,
            token=first.token,
        )
    with pytest.raises(
        RealtimeSessionAuthenticationError,
        match=RealtimeSessionAuthFailure.INVALID,
    ):
        payload, signature = first.token.split(".", maxsplit=1)
        replacement = "A" if signature[0] != "A" else "B"
        await registry.claim(
            session_id=first.session_id,
            token=f"{payload}.{replacement}{signature[1:]}",
        )
    current += timedelta(seconds=31)
    assert await registry.purge_expired() == 0
    assert factory.created[0].closed is False
    await registry.release(first.session_id)
    assert factory.created[0].closed is True

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
        await registry.claim(
            session_id=second.session_id,
            token=second.token,
        )
    assert await registry.purge_expired() == 1
    assert factory.created[1].closed is True
    assert registry.active_session_count == 0


def test_bootstrap_and_websocket_run_one_complete_keyless_turn() -> None:
    factory = _CoordinatorFactory()
    app = _app(factory)
    with TestClient(app, base_url="https://ava.test") as client:
        response = client.post(
            "/v3/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "3"},
            json={
                "protocolVersion": 3,
                "ageBand": "middleChildhood",
            },
        )
        assert response.status_code == 201
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-ava-protocol-version"] == "3"
        bootstrap = response.json()
        assert bootstrap["protocolVersion"] == 3
        assert bootstrap["eventUrl"].startswith("wss://ava.example.test/")
        assert bootstrap["webrtcUrl"].startswith("https://ava.example.test/")
        assert bootstrap["sessionToken"]
        assert bootstrap["webrtcToken"]
        assert bootstrap["sessionToken"] != bootstrap["webrtcToken"]
        assert "name" not in response.text.lower()
        assert "dateOfBirth" not in response.text

        negotiation = client.post(
            urlsplit(bootstrap["webrtcUrl"]).path,
            headers={
                "Authorization": f"Bearer {bootstrap['webrtcToken']}",
                "Content-Type": "application/sdp",
                "X-Ava-Protocol-Version": "3",
            },
            content=DETERMINISTIC_AUDIO_OFFER_SDP,
        )
        assert negotiation.status_code == 200
        assert negotiation.headers["content-type"].startswith("application/sdp")
        assert negotiation.text == DETERMINISTIC_AUDIO_ANSWER_SDP
        replay = client.post(
            urlsplit(bootstrap["webrtcUrl"]).path,
            headers={
                "Authorization": f"Bearer {bootstrap['webrtcToken']}",
                "Content-Type": "application/sdp",
                "X-Ava-Protocol-Version": "3",
            },
            content=DETERMINISTIC_AUDIO_OFFER_SDP,
        )
        assert replay.status_code == 401

        path = urlsplit(bootstrap["eventUrl"]).path
        with client.websocket_connect(
            path,
            headers={
                "Authorization": f"Bearer {bootstrap['sessionToken']}",
                "X-Ava-Protocol-Version": "3",
            },
        ) as websocket:
            websocket.send_json(
                {
                    "protocolVersion": 3,
                    "event": "clientReady",
                    "sessionId": bootstrap["sessionId"],
                    "sequence": 0,
                }
            )
            controls: list[dict[str, object]] = []
            pending_audio: dict[str, object] | None = None
            received_samples = 0
            client_sequence = 1
            while True:
                message = websocket.receive()
                assert message["type"] == "websocket.send"
                if message.get("bytes") is not None:
                    assert pending_audio is not None
                    pcm = message["bytes"]
                    assert len(pcm) == pending_audio["byteLength"]
                    pending_audio = None
                    continue

                assert pending_audio is None
                event = json.loads(message["text"])
                controls.append(event)
                if event["event"] == "audioMetadata":
                    websocket.send_json(
                        {
                            "protocolVersion": 3,
                            "event": "playbackStarted",
                            "sessionId": bootstrap["sessionId"],
                            "sequence": client_sequence,
                            "turnId": event["turnId"],
                            "streamId": event["streamId"],
                            "renderedSampleCount": 0,
                        }
                    )
                    client_sequence += 1
                elif event["event"] == "audioChunk":
                    pending_audio = event
                    received_samples += event["sampleCount"]
                elif (
                    event["event"] == "turnPhaseChanged"
                    and event["phase"] == "speaking"
                ):
                    metadata = next(
                        control
                        for control in controls
                        if control["event"] == "audioMetadata"
                    )
                    websocket.send_json(
                        {
                            "protocolVersion": 3,
                            "event": "playbackProgress",
                            "sessionId": bootstrap["sessionId"],
                            "sequence": client_sequence,
                            "turnId": event["turnId"],
                            "streamId": metadata["streamId"],
                            "renderedSampleCount": received_samples,
                            "bufferedSampleCount": 0,
                        }
                    )
                    client_sequence += 1
                elif event["event"] == "turnCompleted":
                    assert event["deliveredSampleCount"] == received_samples
                    assert event["deliveredText"] == factory.plan.speech
                    break

    assert pending_audio is None
    assert [control["sequence"] for control in controls] == list(range(len(controls)))
    canvas_cue = next(
        control for control in controls if control["event"] == "canvasCue"
    )
    create_nodes = [
        operation["node"]
        for operation in canvas_cue["transaction"]["operations"]
        if operation["operation"] in {"create", "update"}
    ]
    assert create_nodes
    assert all("parentId" in node for node in create_nodes)
    assert factory.coordinators[0].last_memory_decision is not None


def test_websocket_rejects_bad_auth_and_non_ready_first_control() -> None:
    factory = _CoordinatorFactory()
    app = _app(factory)
    with TestClient(app, base_url="https://ava.test") as client:
        bootstrap = _bootstrap(client)
        path = urlsplit(bootstrap["eventUrl"]).path
        with pytest.raises(WebSocketDisconnect) as bad_auth:
            with client.websocket_connect(
                path,
                headers={
                    "Authorization": "Bearer invalid-token",
                    "X-Ava-Protocol-Version": "3",
                },
            ):
                pass
        assert bad_auth.value.code == 4401

        valid = _bootstrap(client)
        valid_path = urlsplit(valid["eventUrl"]).path
        with pytest.raises(WebSocketDisconnect) as not_ready:
            with client.websocket_connect(
                valid_path,
                headers={
                    "Authorization": f"Bearer {valid['sessionToken']}",
                    "X-Ava-Protocol-Version": "3",
                },
            ):
                pass
        assert not_ready.value.code == 4425

        _negotiate(client, valid)
        with client.websocket_connect(
            valid_path,
            headers={
                "Authorization": f"Bearer {valid['sessionToken']}",
                "X-Ava-Protocol-Version": "3",
            },
        ) as websocket:
            websocket.send_json(
                {
                    "protocolVersion": 3,
                    "event": "cancelTurn",
                    "sessionId": valid["sessionId"],
                    "sequence": 0,
                    "turnId": "turn-not-started",
                    "requestId": "cancel-not-started",
                    "reason": "invalidFirstControl",
                }
            )
            disconnect = websocket.receive()
        assert disconnect["type"] == "websocket.close"
        assert disconnect["code"] == 1002


def test_websocket_reports_later_state_mismatch_as_protocol_error_not_1011() -> None:
    factory = _CoordinatorFactory()
    app = _app(factory)
    with TestClient(app, base_url="https://ava.test") as client:
        bootstrap = _bootstrap(client)
        _negotiate(client, bootstrap)
        path = urlsplit(bootstrap["eventUrl"]).path
        with client.websocket_connect(
            path,
            headers={
                "Authorization": f"Bearer {bootstrap['sessionToken']}",
                "X-Ava-Protocol-Version": "3",
            },
        ) as websocket:
            websocket.send_json(
                {
                    "protocolVersion": 3,
                    "event": "clientReady",
                    "sessionId": bootstrap["sessionId"],
                    "sequence": 0,
                }
            )
            first = websocket.receive_json()
            assert first["event"] == "turnCreated"
            websocket.send_json(
                {
                    "protocolVersion": 3,
                    "event": "playbackProgress",
                    "sessionId": bootstrap["sessionId"],
                    "sequence": 1,
                    "turnId": "turn-stale-client-control",
                    "streamId": "stream-stale-client-control",
                    "renderedSampleCount": 0,
                    "bufferedSampleCount": 0,
                }
            )
            while True:
                message = websocket.receive()
                if message["type"] == "websocket.close":
                    assert message["code"] == 1002
                    break


def test_v3_session_endpoint_requires_protocol_and_configured_factory() -> None:
    settings = Settings(environment=AvaEnvironment.TEST)
    app = create_app(settings, ava_model=_LegacyModel())  # type: ignore[arg-type]
    with TestClient(app, base_url="https://ava.test") as client:
        mismatch = client.post(
            "/v3/realtime/sessions",
            json={"protocolVersion": 3, "ageBand": "unknown"},
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "protocol_mismatch"

        unavailable = client.post(
            "/v3/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "3"},
            json={"protocolVersion": 3, "ageBand": "unknown"},
        )
        assert unavailable.status_code == 503
        assert unavailable.json()["error"]["code"] == "provider_unavailable"


def test_v3_session_derives_insecure_channels_only_for_explicit_loopback() -> None:
    factory = _CoordinatorFactory()
    settings = Settings(
        environment=AvaEnvironment.TEST,
        realtime_session_token_secret=SecretStr("s" * 32),
        realtime_allow_insecure_loopback=True,
        _env_file=None,
    )
    app = create_app(
        settings,
        ava_model=_LegacyModel(),  # type: ignore[arg-type]
        realtime_coordinator_factory=factory,
    )
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        bootstrap = _bootstrap(client)

    assert str(bootstrap["eventUrl"]).startswith("ws://127.0.0.1:8080/")
    assert str(bootstrap["webrtcUrl"]).startswith("http://127.0.0.1:8080/")


def test_webrtc_endpoint_rejects_malformed_or_unauthenticated_sdp() -> None:
    factory = _CoordinatorFactory()
    app = _app(factory)
    with TestClient(app, base_url="https://ava.test") as client:
        bootstrap = _bootstrap(client)
        path = urlsplit(str(bootstrap["webrtcUrl"])).path
        missing_token = client.post(
            path,
            headers={
                "Content-Type": "application/sdp",
                "X-Ava-Protocol-Version": "3",
            },
            content=DETERMINISTIC_AUDIO_OFFER_SDP,
        )
        assert missing_token.status_code == 401
        wrong_scope = client.post(
            path,
            headers={
                "Authorization": f"Bearer {bootstrap['sessionToken']}",
                "Content-Type": "application/sdp",
                "X-Ava-Protocol-Version": "3",
            },
            content=DETERMINISTIC_AUDIO_OFFER_SDP,
        )
        assert wrong_scope.status_code == 401

        malformed = client.post(
            path,
            headers={
                "Authorization": f"Bearer {bootstrap['webrtcToken']}",
                "Content-Type": "application/sdp",
                "X-Ava-Protocol-Version": "3",
            },
            content="v=0\r\nm=video 9 RTP/AVP 96\r\n",
        )
        assert malformed.status_code == 422
        assert malformed.json()["error"]["code"] == "invalid_request"


def test_websocket_turn_deadline_closes_and_releases_session() -> None:
    factory = _CoordinatorFactory()
    app = _app(factory)
    app.state.realtime_websocket_bridge = AvaRealtimeWebSocketBridge(
        turn_timeout_seconds=0.01,
    )
    with TestClient(app, base_url="https://ava.test") as client:
        bootstrap = _bootstrap(client)
        _negotiate(client, bootstrap)
        path = urlsplit(bootstrap["eventUrl"]).path
        with client.websocket_connect(
            path,
            headers={
                "Authorization": f"Bearer {bootstrap['sessionToken']}",
                "X-Ava-Protocol-Version": "3",
            },
        ) as websocket:
            websocket.send_json(
                {
                    "protocolVersion": 3,
                    "event": "clientReady",
                    "sessionId": bootstrap["sessionId"],
                    "sequence": 0,
                }
            )
            while True:
                message = websocket.receive()
                if message["type"] == "websocket.close":
                    assert message["code"] == 1013
                    break

    assert app.state.realtime_sessions.active_session_count == 0
    assert factory.coordinators[0].last_memory_decision is None


def _app(factory: RealtimeCoordinatorFactory):
    settings = Settings(
        environment=AvaEnvironment.TEST,
        realtime_session_token_secret=SecretStr("s" * 32),
        realtime_public_event_origin="wss://ava.example.test",
    )
    return create_app(
        settings,
        ava_model=_LegacyModel(),  # type: ignore[arg-type]
        realtime_coordinator_factory=factory,
    )


def _bootstrap(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/v3/realtime/sessions",
        headers={"X-Ava-Protocol-Version": "3"},
        json={"protocolVersion": 3, "ageBand": "unknown"},
    )
    assert response.status_code == 201
    return response.json()


def _negotiate(client: TestClient, bootstrap: dict[str, object]) -> None:
    response = client.post(
        urlsplit(str(bootstrap["webrtcUrl"])).path,
        headers={
            "Authorization": f"Bearer {bootstrap['webrtcToken']}",
            "Content-Type": "application/sdp",
            "X-Ava-Protocol-Version": "3",
        },
        content=DETERMINISTIC_AUDIO_OFFER_SDP,
    )
    assert response.status_code == 200
    assert response.text == DETERMINISTIC_AUDIO_ANSWER_SDP
