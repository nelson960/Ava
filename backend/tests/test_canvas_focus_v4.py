from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ava_backend.app import create_app
from ava_backend.canvas.svg_delivery import SvgCanvasDocumentStore
from ava_backend.canvas.svg_document import (
    SVG_PROTOCOL_MAX_BYTES,
    SvgDocumentValidator,
    SvgValidationLimits,
)
from ava_backend.canvas.svg_session import SvgCanvasSessionState
from ava_backend.canvas.svg_specialist import (
    SvgCanvasSpecialistGeneration,
    SvgCanvasSpecialistRequest,
)
from ava_backend.canvas.svg_workspace import (
    ActiveSvgCanvasScene,
    SvgCanvasWorkspace,
    SvgCanvasWorkspaceError,
)
from ava_backend.config import AvaEnvironment, Settings
from ava_backend.protocol import AgeBand
from ava_backend.realtime.coordinator import DeterministicRealtimeSessionCoordinator
from ava_backend.realtime.coordinator_v4 import (
    ProtocolV4CanvasEventSink,
)
from ava_backend.realtime.events_v4 import (
    AVA_CLIENT_EVENT_ADAPTER_V4,
)
from ava_backend.realtime.fakes import (
    DETERMINISTIC_AUDIO_OFFER_SDP,
    DeterministicRealtimePlanProvider,
    DeterministicStreamingSpeechSynthesizer,
)
from ava_backend.realtime.memory import DevelopmentAuthoritativeMemoryStore
from ava_backend.realtime.plan import AvaTurnPlan
from ava_backend.realtime.svg_canvas_jobs import SvgCanvasJobCoordinator
from ava_backend.realtime.tool_contract import (
    InteractiveCanvasRequest,
    InteractiveCanvasToolStatusV4,
    RealtimeCanvasToolCall,
)
from ava_backend.auth import AuthenticatedClient, RequestAuthenticator
from ava_backend.realtime.session import (
    NegotiatedRealtimeCall,
    RealtimeSessionAuthenticationError,
    RealtimeSessionAuthFailure,
    RealtimeSessionRegistry,
    RealtimeSessionTokenCodec,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"

VALID_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" data-ava-background="#dff5ff" data-ava-interaction-mode="select">
  <title>Café cloud ☁️</title>
  <desc>A cloud turns water vapour into rain.</desc>
  <g id="cloud" data-ava-binding="select-cloud" data-ava-target="cloud" aria-label="Rain cloud">
    <ellipse cx="500" cy="220" rx="140" ry="60" fill="#ffffff"/>
  </g>
</svg>"""

VALID_SVG_2 = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" data-ava-background="#dff5ff" data-ava-interaction-mode="select">
  <title>Café cloud 2 ☁️</title>
  <desc>A second cloud turns water vapour into rain.</desc>
  <g id="cloud2" data-ava-binding="select-cloud-2" data-ava-target="cloud2" aria-label="Second rain cloud">
    <ellipse cx="600" cy="220" rx="160" ry="70" fill="#ffffff"/>
  </g>
</svg>"""


def test_canonical_identity_presentation_sample_decoupling() -> None:
    workspace = SvgCanvasWorkspace()
    validator = SvgDocumentValidator(SvgValidationLimits())
    doc1 = validator.validate(VALID_SVG)

    # Existing live scene with sample-clock offset 500000
    scene_existing = ActiveSvgCanvasScene(
        generation_id="gen-1",
        source_turn_id="turn-1",
        scene_id="scene-1",
        revision=1,
        document=doc1,
        first_rendered_sample=500_000,
    )
    workspace.restore_focused(scene_existing)
    assert workspace.focused_scene_id == "scene-1"

    # Retained history focus of exact same canonical scene with sample-clock offset 0
    scene_retained = ActiveSvgCanvasScene(
        generation_id="gen-1",
        source_turn_id="turn-1",
        scene_id="scene-1",
        revision=1,
        document=doc1,
        first_rendered_sample=0,
    )

    # Focus must succeed
    focused = workspace.focus_retained(scene_retained)
    assert focused.scene_id == "scene-1"
    assert focused.revision == 1

    # Same-revision observation commit path must also succeed
    committed = workspace.commit(
        scene_retained,
        base_scene_id=None,
        base_revision=0,
        workspace_epoch=workspace.reset_epoch,
    )
    assert committed.scene_id == "scene-1"
    assert committed.revision == 1


def test_svg_workspace_focus_retained_basic() -> None:
    workspace = SvgCanvasWorkspace()
    validator = SvgDocumentValidator(SvgValidationLimits())
    doc1 = validator.validate(VALID_SVG)
    scene1 = ActiveSvgCanvasScene(
        generation_id="gen-1",
        source_turn_id="turn-1",
        scene_id="scene-1",
        revision=1,
        document=doc1,
        first_rendered_sample=0,
    )

    focused1 = workspace.focus_retained(scene1)
    assert focused1.scene_id == "scene-1"
    assert workspace.current == scene1
    assert workspace.focused_scene_id == "scene-1"
    assert workspace.scene_count == 1

    doc2 = validator.validate(VALID_SVG_2)
    scene2 = ActiveSvgCanvasScene(
        generation_id="gen-2",
        source_turn_id="turn-2",
        scene_id="scene-2",
        revision=1,
        document=doc2,
        first_rendered_sample=0,
    )
    focused2 = workspace.focus_retained(scene2)
    assert focused2.scene_id == "scene-2"
    assert workspace.current == scene2
    assert workspace.focused_scene_id == "scene-2"
    assert workspace.scene_count == 2

    # Switch focus back to scene-1
    refocused1 = workspace.focus_retained(scene1)
    assert refocused1.scene_id == "scene-1"
    assert workspace.current == scene1
    assert workspace.scene_count == 2


def test_svg_workspace_focus_retained_rejects_active_execution() -> None:
    workspace = SvgCanvasWorkspace()
    validator = SvgDocumentValidator(SvgValidationLimits())
    doc1 = validator.validate(VALID_SVG)
    scene1 = ActiveSvgCanvasScene(
        generation_id="gen-1",
        source_turn_id="turn-1",
        scene_id="scene-1",
        revision=1,
        document=doc1,
        first_rendered_sample=0,
    )
    workspace.focus_retained(scene1)

    workspace.start_execution(
        generation_id="gen-pending",
        source_turn_id="turn-2",
        operation="newScene",
    )

    doc2 = validator.validate(VALID_SVG_2)
    scene2 = ActiveSvgCanvasScene(
        generation_id="gen-2",
        source_turn_id="turn-2",
        scene_id="scene-2",
        revision=1,
        document=doc2,
        first_rendered_sample=0,
    )
    with pytest.raises(SvgCanvasWorkspaceError, match="canvas generation is active"):
        workspace.focus_retained(scene2)


def test_svg_workspace_focus_retained_rejects_conflicting_identity() -> None:
    workspace = SvgCanvasWorkspace()
    validator = SvgDocumentValidator(SvgValidationLimits())
    doc1 = validator.validate(VALID_SVG)
    scene1 = ActiveSvgCanvasScene(
        generation_id="gen-1",
        source_turn_id="turn-1",
        scene_id="scene-1",
        revision=1,
        document=doc1,
        first_rendered_sample=0,
    )
    workspace.focus_retained(scene1)

    # Same scene_id but different revision
    scene1_conflicting = ActiveSvgCanvasScene(
        generation_id="gen-1",
        source_turn_id="turn-1",
        scene_id="scene-1",
        revision=2,
        document=doc1,
        first_rendered_sample=0,
    )
    with pytest.raises(SvgCanvasWorkspaceError, match="conflicting"):
        workspace.focus_retained(scene1_conflicting)


def test_svg_workspace_focus_retained_enforces_10_scene_bound() -> None:
    workspace = SvgCanvasWorkspace()
    validator = SvgDocumentValidator(SvgValidationLimits())
    doc = validator.validate(VALID_SVG)

    for i in range(12):
        scene = ActiveSvgCanvasScene(
            generation_id=f"gen-{i}",
            source_turn_id=f"turn-{i}",
            scene_id=f"scene-{i}",
            revision=1,
            document=doc,
            first_rendered_sample=0,
        )
        workspace.focus_retained(scene)

    assert workspace.scene_count == 10
    assert workspace.scene("scene-0") is None
    assert workspace.scene("scene-1") is None
    assert workspace.scene("scene-11") is not None
    assert workspace.focused_scene_id == "scene-11"


class _StubCoordinator:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.focused_scene: ActiveSvgCanvasScene | None = None
        self.restored_scene: ActiveSvgCanvasScene | None = None
        self.turn_id = f"turn-{session_id}"

    def restore_focused_canvas(self, scene: ActiveSvgCanvasScene) -> None:
        self.restored_scene = scene

    async def focus_canvas(self, scene: ActiveSvgCanvasScene) -> None:
        self.focused_scene = scene

    async def negotiate(self, *, offer_sdp: str) -> NegotiatedRealtimeCall:
        return NegotiatedRealtimeCall(
            provider_call_id=f"call-{self.session_id}",
            answer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP,
        )

    async def close(self) -> None:
        pass


class _StubFactory:
    def __init__(self) -> None:
        self.coordinators: list[_StubCoordinator] = []

    def create(
        self,
        *,
        session_id: str,
        request_id: str,
        subject: str,
        age_band: AgeBand,
        timezone_id: str,
        canvas_render_profile: object = None,
    ) -> _StubCoordinator:
        coord = _StubCoordinator(session_id)
        self.coordinators.append(coord)
        return coord


class _DynamicSubjectAuthenticator(RequestAuthenticator):
    def __init__(self, default_subject: str = "child:default") -> None:
        self.current_subject = default_subject

    async def authenticate(self, request: Request) -> AuthenticatedClient:
        return AuthenticatedClient(
            subject=self.current_subject,
        )


def test_canvas_focus_http_route_success() -> None:
    factory = _StubFactory()
    validator = SvgDocumentValidator(SvgValidationLimits())
    validated = validator.validate(VALID_SVG)
    app = create_app(
        Settings(
            environment=AvaEnvironment.TEST,
            realtime_session_token_secret=SecretStr("k" * 32),
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
            f"/v4/realtime/sessions/{bootstrap['sessionId']}/canvas-focus",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Canvas-Token": bootstrap["canvasToken"],
                "X-Ava-Generation-ID": "gen-focus-1",
                "X-Ava-Source-Turn-ID": "turn-focus-1",
                "X-Ava-Scene-ID": "scene-focus-1",
                "X-Ava-Scene-Revision": "1",
                "X-Ava-SVG-SHA256": validated.svg_sha256,
                "X-Ava-Manifest-SHA256": validated.manifest_sha256,
            },
            content=validated.svg_bytes,
        )
        assert response.status_code == 204
        assert factory.coordinators[0].focused_scene is not None
        assert factory.coordinators[0].focused_scene.scene_id == "scene-focus-1"


# Contract A: Valid canvas token from DIFFERENT session is rejected
def test_canvas_focus_rejects_token_from_different_session() -> None:
    factory = _StubFactory()
    validator = SvgDocumentValidator(SvgValidationLimits())
    validated = validator.validate(VALID_SVG)
    app = create_app(
        Settings(
            environment=AvaEnvironment.TEST,
            realtime_session_token_secret=SecretStr("k" * 32),
        ),
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
        realtime_v4_coordinator_factory=factory,
    )
    with TestClient(app, base_url="https://127.0.0.1") as client:
        b1 = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "unknown", "timezoneId": "UTC"},
        ).json()
        b2 = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "unknown", "timezoneId": "UTC"},
        ).json()

        response = client.put(
            f"/v4/realtime/sessions/{b1['sessionId']}/canvas-focus",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Canvas-Token": b2["canvasToken"],
                "X-Ava-Generation-ID": "gen-1",
                "X-Ava-Source-Turn-ID": "turn-1",
                "X-Ava-Scene-ID": "scene-1",
                "X-Ava-Scene-Revision": "1",
                "X-Ava-SVG-SHA256": validated.svg_sha256,
                "X-Ava-Manifest-SHA256": validated.manifest_sha256,
            },
            content=validated.svg_bytes,
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


# Contract B: Valid token/session with wrong subject is rejected
def test_canvas_focus_rejects_wrong_subject() -> None:
    factory = _StubFactory()
    validator = SvgDocumentValidator(SvgValidationLimits())
    validated = validator.validate(VALID_SVG)
    authenticator = _DynamicSubjectAuthenticator("child:owner-subject")
    app = create_app(
        Settings(
            environment=AvaEnvironment.TEST,
            realtime_session_token_secret=SecretStr("k" * 32),
        ),
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
        realtime_v4_coordinator_factory=factory,
        authenticator=authenticator,
    )
    with TestClient(app, base_url="https://127.0.0.1") as client:
        bootstrap = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "unknown", "timezoneId": "UTC"},
        ).json()

        # Switch client authentication to a different subject
        authenticator.current_subject = "child:intruder-subject"

        response = client.put(
            f"/v4/realtime/sessions/{bootstrap['sessionId']}/canvas-focus",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Canvas-Token": bootstrap["canvasToken"],
                "X-Ava-Generation-ID": "gen-1",
                "X-Ava-Source-Turn-ID": "turn-1",
                "X-Ava-Scene-ID": "scene-1",
                "X-Ava-Scene-Revision": "1",
                "X-Ava-SVG-SHA256": validated.svg_sha256,
                "X-Ava-Manifest-SHA256": validated.manifest_sha256,
            },
            content=validated.svg_bytes,
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


# Contract C: Manifest SHA mismatch is rejected
def test_canvas_focus_rejects_manifest_sha_mismatch() -> None:
    factory = _StubFactory()
    validator = SvgDocumentValidator(SvgValidationLimits())
    validated = validator.validate(VALID_SVG)
    app = create_app(
        Settings(
            environment=AvaEnvironment.TEST,
            realtime_session_token_secret=SecretStr("k" * 32),
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
            f"/v4/realtime/sessions/{bootstrap['sessionId']}/canvas-focus",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Canvas-Token": bootstrap["canvasToken"],
                "X-Ava-Generation-ID": "gen-1",
                "X-Ava-Source-Turn-ID": "turn-1",
                "X-Ava-Scene-ID": "scene-1",
                "X-Ava-Scene-Revision": "1",
                "X-Ava-SVG-SHA256": validated.svg_sha256,
                "X-Ava-Manifest-SHA256": "0" * 64,
            },
            content=validated.svg_bytes,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"


# Contract D: Existing SVG SHA mismatch test stays
def test_canvas_focus_rejects_svg_sha_mismatch() -> None:
    factory = _StubFactory()
    validator = SvgDocumentValidator(SvgValidationLimits())
    validated = validator.validate(VALID_SVG)
    app = create_app(
        Settings(
            environment=AvaEnvironment.TEST,
            realtime_session_token_secret=SecretStr("k" * 32),
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
            f"/v4/realtime/sessions/{bootstrap['sessionId']}/canvas-focus",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Canvas-Token": bootstrap["canvasToken"],
                "X-Ava-Generation-ID": "gen-1",
                "X-Ava-Source-Turn-ID": "turn-1",
                "X-Ava-Scene-ID": "scene-1",
                "X-Ava-Scene-Revision": "1",
                "X-Ava-SVG-SHA256": "0" * 64,
                "X-Ava-Manifest-SHA256": validated.manifest_sha256,
            },
            content=validated.svg_bytes,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"


# Contract E & F: HTTP boundary limits
def test_canvas_focus_http_boundary_limits() -> None:
    factory = _StubFactory()
    validator = SvgDocumentValidator(SvgValidationLimits())
    validated = validator.validate(VALID_SVG)
    settings = Settings(
        environment=AvaEnvironment.TEST,
        realtime_session_token_secret=SecretStr("k" * 32),
        request_max_bytes=65_536,
    )
    app = create_app(
        settings,
        authoritative_memory=DevelopmentAuthoritativeMemoryStore(),
        realtime_v4_coordinator_factory=factory,
    )
    with TestClient(app, base_url="https://127.0.0.1") as client:
        bootstrap = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4"},
            json={"protocolVersion": 4, "ageBand": "unknown", "timezoneId": "UTC"},
        ).json()

        # Contract F: Normal endpoint body > 64 KiB returns 413
        normal_resp = client.post(
            "/v4/realtime/sessions",
            headers={"X-Ava-Protocol-Version": "4", "Content-Type": "application/json"},
            content=b" " * (settings.request_max_bytes + 1),
        )
        assert normal_resp.status_code == 413
        assert normal_resp.json()["error"]["code"] == "payload_too_large"

        # Contract E: Canvas focus body > 512 KiB returns 413
        large_body = b"<svg>" + (b" " * (SVG_PROTOCOL_MAX_BYTES + 1))
        over_canvas_resp = client.put(
            f"/v4/realtime/sessions/{bootstrap['sessionId']}/canvas-focus",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Canvas-Token": bootstrap["canvasToken"],
                "X-Ava-Generation-ID": "gen-1",
                "X-Ava-Source-Turn-ID": "turn-1",
                "X-Ava-Scene-ID": "scene-1",
                "X-Ava-Scene-Revision": "1",
                "X-Ava-SVG-SHA256": validated.svg_sha256,
                "X-Ava-Manifest-SHA256": validated.manifest_sha256,
            },
            content=large_body,
        )
        assert over_canvas_resp.status_code == 413
        assert over_canvas_resp.json()["error"]["code"] == "payload_too_large"

        # Body between 64 KiB and 512 KiB passes boundary limit
        allowed_100k = b"<svg>" + (b" " * 100_000)
        within_canvas_resp = client.put(
            f"/v4/realtime/sessions/{bootstrap['sessionId']}/canvas-focus",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Canvas-Token": bootstrap["canvasToken"],
                "X-Ava-Generation-ID": "gen-1",
                "X-Ava-Source-Turn-ID": "turn-1",
                "X-Ava-Scene-ID": "scene-1",
                "X-Ava-Scene-Revision": "1",
                "X-Ava-SVG-SHA256": validated.svg_sha256,
                "X-Ava-Manifest-SHA256": validated.manifest_sha256,
            },
            content=allowed_100k,
        )
        assert within_canvas_resp.status_code == 422


class _FakeSpecialist:
    def __init__(self) -> None:
        self.requests: list[SvgCanvasSpecialistRequest] = []
        self.cancelled: list[str] = []
        self.closed = False

    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
        *,
        draft_sink: object | None = None,
    ) -> SvgCanvasSpecialistGeneration:
        del draft_sink
        self.requests.append(request)
        target_rev = (request.current_revision or 0) + 1
        svg_text = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" data-ava-background="#dff5ff" data-ava-interaction-mode="select">
  <title>Cloud rev {target_rev}</title>
  <desc>A cloud revised to revision {target_rev}.</desc>
  <g id="cloud" data-ava-binding="select-cloud" data-ava-target="cloud" aria-label="Rain cloud">
    <ellipse cx="500" cy="220" rx="140" ry="60" fill="#ffffff"/>
  </g>
</svg>"""
        document = SvgDocumentValidator().validate(svg_text)
        return SvgCanvasSpecialistGeneration(
            generation_id=request.generation_id,
            document=document,
        )

    async def cancel(self, *, generation_id: str) -> None:
        self.cancelled.append(generation_id)

    async def close(self) -> None:
        self.closed = True


class _RealtimeFocusPlanProvider(DeterministicRealtimePlanProvider):
    async def prepare_if_idle(
        self,
        *,
        harness: Any,
        turn_id: str,
    ) -> Any:
        return await self.prepare(harness=harness)

    async def wait_until_canvas_interaction_response_ready(
        self,
        *,
        turn_id: str,
    ) -> bool:
        return True


async def _create_real_coordinator(
    session_id: str = "session-real-coord",
    realtime_provider: _RealtimeFocusPlanProvider | None = None,
) -> tuple[
    DeterministicRealtimeSessionCoordinator,
    _RealtimeFocusPlanProvider,
    ProtocolV4CanvasEventSink,
    DevelopmentAuthoritativeMemoryStore,
    _FakeSpecialist,
    SvgCanvasJobCoordinator,
]:
    plan = AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / "ava_turn_plan_conversation_v3.json").read_text(
            encoding="utf-8"
        )
    )
    realtime = realtime_provider or _RealtimeFocusPlanProvider(plan)
    state = SvgCanvasSessionState()
    documents = SvgCanvasDocumentStore()
    canvas_events = ProtocolV4CanvasEventSink()
    specialist = _FakeSpecialist()
    jobs = SvgCanvasJobCoordinator(
        session_id=session_id,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=specialist,
        state=state,
        documents=documents,
        event_sink=canvas_events,
        generation_id_factory=lambda: "gen-candidate-b4",
    )
    memory = DevelopmentAuthoritativeMemoryStore()
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id=session_id,
        turn_id="turn-real-coord",
        request_id="request-real-coord",
        stream_id="stream-real-coord",
        subject="child:real-coord",
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=realtime,
        speech=DeterministicStreamingSpeechSynthesizer(),
        canvas_jobs=jobs,
        canvas_events=canvas_events,
        memory=memory,
    )
    await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
    return coordinator, realtime, canvas_events, memory, specialist, jobs


# Contract G: invoke REAL coordinator.focus_canvas(...) path and prove dynamic context refresh occurs with CANVAS_FOCUSED
@pytest.mark.asyncio
async def test_real_coordinator_focus_canvas_refreshes_dynamic_context_canvas_focused() -> (
    None
):
    (
        coordinator,
        realtime,
        _events,
        _memory,
        _specialist,
        jobs,
    ) = await _create_real_coordinator()
    try:
        validator = SvgDocumentValidator(SvgValidationLimits())
        doc = validator.validate(VALID_SVG)
        scene = ActiveSvgCanvasScene(
            generation_id="gen-focus-g",
            source_turn_id="turn-focus-g",
            scene_id="scene-g",
            revision=1,
            document=doc,
            first_rendered_sample=0,
        )

        initial_updates = len(realtime.prepared_session_updates)
        assert initial_updates >= 1

        await coordinator.focus_canvas(scene)

        assert len(realtime.prepared_session_updates) == initial_updates + 1
        latest_instructions = realtime.prepared_session_updates[-1]["session"][
            "instructions"
        ]
        assert "Café cloud ☁️" in latest_instructions
        assert jobs.state.workspace.focused_scene_id == "scene-g"
        assert jobs.state.active == scene
    finally:
        await coordinator.close()


# Contract H: prove canvas-focus emits no canvasActivated, canvasPresented, fake turn, or memory commit
@pytest.mark.asyncio
async def test_real_coordinator_focus_canvas_emits_no_presentation_or_turn_or_memory() -> (
    None
):
    (
        coordinator,
        _realtime,
        canvas_events,
        memory,
        _specialist,
        _jobs,
    ) = await _create_real_coordinator()
    try:
        validator = SvgDocumentValidator(SvgValidationLimits())
        doc = validator.validate(VALID_SVG)
        scene = ActiveSvgCanvasScene(
            generation_id="gen-focus-h",
            source_turn_id="turn-focus-h",
            scene_id="scene-h",
            revision=1,
            document=doc,
            first_rendered_sample=0,
        )

        initial_turn_id = coordinator._turn_id
        initial_committed = len(memory._committed_turn_ids[coordinator._subject])

        await coordinator.focus_canvas(scene)

        assert coordinator._turn_id == initial_turn_id
        assert (
            len(memory._committed_turn_ids[coordinator._subject]) == initial_committed
        )
        assert canvas_events._events.empty()
    finally:
        await coordinator.close()


# Contract I: after runtime focus to retained Scene A, a typed Canvas interaction against A succeeds
@pytest.mark.asyncio
async def test_real_coordinator_interaction_against_retained_scene_succeeds() -> None:
    (
        coordinator,
        _realtime,
        canvas_events,
        _memory,
        _specialist,
        jobs,
    ) = await _create_real_coordinator()
    try:
        validator = SvgDocumentValidator(SvgValidationLimits())
        doc = validator.validate(VALID_SVG)
        scene = ActiveSvgCanvasScene(
            generation_id="gen-focus-i",
            source_turn_id="turn-focus-i",
            scene_id="scene-i",
            revision=1,
            document=doc,
            first_rendered_sample=0,
        )

        await coordinator.focus_canvas(scene)

        client_interaction = AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                "protocolVersion": 4,
                "sessionId": coordinator._session_id,
                "sequence": 0,
                "event": "canvasInteraction",
                "sceneId": scene.scene_id,
                "sceneRevision": scene.revision,
                "interactionRevision": 1,
                "svgSha256": scene.document.svg_sha256,
                "manifestSha256": scene.document.manifest_sha256,
                "bindingId": "select-cloud",
            }
        )
        await coordinator.receive(client_interaction)

        last_interaction = jobs.state.last_interaction
        assert last_interaction is not None
        assert last_interaction.scene_id == "scene-i"
        assert last_interaction.binding_id == "select-cloud"
        assert last_interaction.interaction_mode == "select"
        assert canvas_events._events.empty()
    finally:
        await coordinator.close()


# Contract J: after runtime focus to retained B revision 3, reviseFocused uses B3 as base and produces/stages B revision 4
@pytest.mark.asyncio
async def test_real_coordinator_revise_focused_uses_retained_base_and_stages_rev4() -> (
    None
):
    (
        coordinator,
        _realtime,
        _events,
        _memory,
        specialist,
        jobs,
    ) = await _create_real_coordinator()
    try:
        validator = SvgDocumentValidator(SvgValidationLimits())
        doc_b = validator.validate(VALID_SVG_2)
        scene_b3 = ActiveSvgCanvasScene(
            generation_id="gen-b3",
            source_turn_id="turn-b3",
            scene_id="scene-b",
            revision=3,
            document=doc_b,
            first_rendered_sample=0,
        )

        await coordinator.focus_canvas(scene_b3)
        assert jobs.state.workspace.focused_scene_id == "scene-b"
        assert jobs.state.active == scene_b3

        tool_call = RealtimeCanvasToolCall(
            call_id="call-revise-1",
            request=InteractiveCanvasRequest.model_validate(
                {
                    "operation": "reviseFocused",
                    "brief": "Add rain droplets falling from the cloud.",
                    "interactionMode": "select",
                }
            ),
        )

        completions: list[tuple[str, str, Any]] = []

        async def _record_completion(turn: str, call: str, out: Any) -> None:
            completions.append((turn, call, out))

        output = await jobs.accept_tool_call(
            source_turn_id="turn-real-coord",
            call=tool_call,
            complete_tool=_record_completion,
        )

        assert output.status == InteractiveCanvasToolStatusV4.ACCEPTED
        assert output.generation_id == "gen-candidate-b4"

        await jobs.wait_for_background_work()

        assert len(specialist.requests) == 1
        spec_req = specialist.requests[0]
        assert spec_req.current_scene_id == "scene-b"
        assert spec_req.current_revision == 3
        assert spec_req.current_svg == doc_b.svg_text
        assert spec_req.current_svg_sha256 == doc_b.svg_sha256

        candidate = jobs.state.candidate
        assert candidate is not None
        assert candidate.scene_id == "scene-b"
        assert candidate.revision == 4
    finally:
        await coordinator.close()


@pytest.mark.asyncio
async def test_claimed_canvas_focus_succeeds_after_bootstrap_ttl_and_fails_after_active_deadline() -> None:
    current_time = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)
    factory = _StubFactory()
    codec = RealtimeSessionTokenCodec("k" * 32)
    registry = RealtimeSessionRegistry(
        coordinator_factory=factory,
        token_codec=codec,
        ttl=timedelta(seconds=120),
        active_session_ttl=timedelta(seconds=3600),
        max_sessions=10,
        now=lambda: current_time,
    )
    validator = SvgDocumentValidator(SvgValidationLimits())
    validated = validator.validate(VALID_SVG)

    # 1. Create session
    created = await registry.create(
        request_id="req-1",
        subject="child:owner",
        age_band=AgeBand.UNKNOWN,
        timezone_id="UTC",
    )

    # 2. Negotiate WebRTC + Claim events (activates the session)
    current_time += timedelta(seconds=10)
    await registry.negotiate(
        session_id=created.session_id,
        token=created.webrtc_token,
        subject="child:owner",
        offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP,
    )
    await registry.claim(
        session_id=created.session_id,
        token=created.token,
    )

    # Prove startup canvas-restore remains pre-negotiation / one-shot (rejects on claimed session)
    with pytest.raises(RealtimeSessionAuthenticationError) as exc_restore:
        await registry.restore_focused_canvas(
            session_id=created.session_id,
            token=created.canvas_token,
            subject="child:owner",
            generation_id="gen-restore-1",
            source_turn_id="turn-restore-1",
            scene_id="scene-restore-1",
            scene_revision=1,
            expected_svg_sha256=validated.svg_sha256,
            expected_manifest_sha256=validated.manifest_sha256,
            svg_bytes=validated.svg_bytes,
        )
    assert exc_restore.value.failure == RealtimeSessionAuthFailure.CLAIMED

    # 3. Advance past bootstrap record.expires_at (120s from creation, now is t0 + 130s)
    current_time += timedelta(seconds=120)

    # 4 & 5. Canvas focus succeeds after bootstrap TTL because active_deadline is valid
    focused = await registry.focus_canvas(
        session_id=created.session_id,
        token=created.canvas_token,
        subject="child:owner",
        generation_id="gen-focus-1",
        source_turn_id="turn-focus-1",
        scene_id="scene-focus-1",
        scene_revision=1,
        expected_svg_sha256=validated.svg_sha256,
        expected_manifest_sha256=validated.manifest_sha256,
        svg_bytes=validated.svg_bytes,
    )
    assert focused.scene_id == "scene-focus-1"
    assert factory.coordinators[0].focused_scene is not None

    # 6 & 7. Advance past active_deadline (active_session_ttl is 3600s from claim at t0+10s = t0+3610s)
    current_time += timedelta(seconds=3600)
    with pytest.raises(RealtimeSessionAuthenticationError) as exc_focus:
        await registry.focus_canvas(
            session_id=created.session_id,
            token=created.canvas_token,
            subject="child:owner",
            generation_id="gen-focus-2",
            source_turn_id="turn-focus-2",
            scene_id="scene-focus-1",
            scene_revision=1,
            expected_svg_sha256=validated.svg_sha256,
            expected_manifest_sha256=validated.manifest_sha256,
            svg_bytes=validated.svg_bytes,
        )
    assert exc_focus.value.failure == RealtimeSessionAuthFailure.EXPIRED


@pytest.mark.asyncio
async def test_focus_canvas_fails_atomically_when_provider_installation_throws() -> None:
    plan = AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / "ava_turn_plan_conversation_v3.json").read_text(
            encoding="utf-8"
        )
    )

    class _FailingRealtimeProvider(_RealtimeFocusPlanProvider):
        def __init__(self, plan: Any) -> None:
            super().__init__(plan)
            self.fail_prepare = False

        async def prepare_if_idle(self, *, harness: Any, turn_id: str) -> Any:
            if self.fail_prepare:
                raise RuntimeError("Deterministic Realtime context installation failure")
            return await super().prepare_if_idle(harness=harness, turn_id=turn_id)

    provider = _FailingRealtimeProvider(plan)
    coordinator, _realtime, canvas_events, memory, _specialist, jobs = (
        await _create_real_coordinator(realtime_provider=provider)
    )
    try:
        validator = SvgDocumentValidator(SvgValidationLimits())
        doc_a = validator.validate(VALID_SVG)
        scene_a = ActiveSvgCanvasScene(
            generation_id="gen-a",
            source_turn_id="turn-a",
            scene_id="scene-a",
            revision=1,
            document=doc_a,
            first_rendered_sample=0,
        )
        doc_b = validator.validate(VALID_SVG_2)
        scene_b = ActiveSvgCanvasScene(
            generation_id="gen-b",
            source_turn_id="turn-b",
            scene_id="scene-b",
            revision=1,
            document=doc_b,
            first_rendered_sample=0,
        )

        # Initial backend focus: Scene A
        await coordinator.focus_canvas(scene_a)
        assert jobs.state.workspace.focused_scene_id == "scene-a"
        assert jobs.state.active == scene_a

        # Now make Realtime context installation throw on Scene B
        provider.fail_prepare = True

        with pytest.raises(RuntimeError, match="Deterministic Realtime context installation failure"):
            await coordinator.focus_canvas(scene_b)

        # Expected:
        # - coordinator.focus_canvas(B) fails;
        # - workspace.focused_scene_id remains A;
        # - workspace.active/current remains A;
        # - no fake canvasActivated;
        # - no canvasPresented;
        # - no memory commit;
        # - no fake turn;
        # - B is NOT semantically committed.
        assert jobs.state.workspace.focused_scene_id == "scene-a"
        assert jobs.state.active == scene_a
        assert jobs.state.workspace.current == scene_a
        assert jobs.state.workspace.scene("scene-b") is None
        assert canvas_events._events.empty()
        assert coordinator._turn_id == "turn-real-coord"
        assert len(memory._committed_turn_ids[coordinator._subject]) == 0
    finally:
        await coordinator.close()


@pytest.mark.asyncio
async def test_focus_canvas_retries_and_commits_only_after_installation_confirmed() -> None:
    plan = AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / "ava_turn_plan_conversation_v3.json").read_text(
            encoding="utf-8"
        )
    )

    class _DeferredThenReadyProvider(_RealtimeFocusPlanProvider):
        def __init__(self, plan: Any) -> None:
            super().__init__(plan)
            self.attempts = 0
            self.workspace_ref: SvgCanvasWorkspace | None = None
            self.workspace_focus_on_first_attempt: str | None = None

        async def prepare_if_idle(self, *, harness: Any, turn_id: str) -> Any:
            self.attempts += 1
            if self.attempts == 1:
                if self.workspace_ref is not None:
                    self.workspace_focus_on_first_attempt = self.workspace_ref.focused_scene_id
                return None
            return await super().prepare_if_idle(harness=harness, turn_id=turn_id)

    provider = _DeferredThenReadyProvider(plan)
    coordinator, _realtime, _canvas_events, _memory, _specialist, jobs = (
        await _create_real_coordinator(realtime_provider=provider)
    )
    provider.workspace_ref = jobs.state.workspace
    try:
        validator = SvgDocumentValidator(SvgValidationLimits())
        doc_a = validator.validate(VALID_SVG)
        scene_a = ActiveSvgCanvasScene(
            generation_id="gen-a",
            source_turn_id="turn-a",
            scene_id="scene-a",
            revision=1,
            document=doc_a,
            first_rendered_sample=0,
        )
        doc_b = validator.validate(VALID_SVG_2)
        scene_b = ActiveSvgCanvasScene(
            generation_id="gen-b",
            source_turn_id="turn-b",
            scene_id="scene-b",
            revision=1,
            document=doc_b,
            first_rendered_sample=0,
        )

        # Initial backend focus: Scene A (attempts count reset)
        await coordinator.focus_canvas(scene_a)
        assert jobs.state.workspace.focused_scene_id == "scene-a"
        provider.attempts = 0  # reset for scene B focus

        # Focus B: first prepare_if_idle returns None, then retry succeeds
        await coordinator.focus_canvas(scene_b)

        # Assertions before installation confirmation:
        # workspace focus was == "scene-a"
        assert provider.workspace_focus_on_first_attempt == "scene-a"
        assert provider.attempts >= 2

        # Assertions after successful confirmation:
        # workspace focus == "scene-b"
        assert jobs.state.workspace.focused_scene_id == "scene-b"
        assert jobs.state.active == scene_b
    finally:
        await coordinator.close()


@pytest.mark.asyncio
async def test_registry_focus_canvas_does_not_hold_registry_lock_during_coordinator_await() -> None:
    current_time = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)
    block_focus = asyncio.Event()
    focus_entered = asyncio.Event()

    class _BlockingCoordinator(_StubCoordinator):
        async def focus_canvas(self, scene: ActiveSvgCanvasScene) -> None:
            focus_entered.set()
            await block_focus.wait()
            self.focused_scene = scene

    class _BlockingFactory:
        def __init__(self) -> None:
            self.coordinators: list[_StubCoordinator] = []

        def create(self, **kwargs: Any) -> _StubCoordinator:
            coord = _BlockingCoordinator(kwargs["session_id"])
            self.coordinators.append(coord)
            return coord

    factory = _BlockingFactory()
    codec = RealtimeSessionTokenCodec("k" * 32)
    registry = RealtimeSessionRegistry(
        coordinator_factory=factory,  # type: ignore[arg-type]
        token_codec=codec,
        ttl=timedelta(seconds=120),
        active_session_ttl=timedelta(seconds=3600),
        max_sessions=10,
        now=lambda: current_time,
    )
    validator = SvgDocumentValidator(SvgValidationLimits())
    validated = validator.validate(VALID_SVG)

    # Session A
    session_a = await registry.create(
        request_id="req-a",
        subject="child:owner-a",
        age_band=AgeBand.UNKNOWN,
        timezone_id="UTC",
    )
    await registry.negotiate(
        session_id=session_a.session_id,
        token=session_a.webrtc_token,
        subject="child:owner-a",
        offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP,
    )
    await registry.claim(
        session_id=session_a.session_id,
        token=session_a.token,
    )

    # Session B
    session_b = await registry.create(
        request_id="req-b",
        subject="child:owner-b",
        age_band=AgeBand.UNKNOWN,
        timezone_id="UTC",
    )
    await registry.negotiate(
        session_id=session_b.session_id,
        token=session_b.webrtc_token,
        subject="child:owner-b",
        offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP,
    )
    await registry.claim(
        session_id=session_b.session_id,
        token=session_b.token,
    )

    # Start focus on Session A in background task
    task_a = asyncio.create_task(
        registry.focus_canvas(
            session_id=session_a.session_id,
            token=session_a.canvas_token,
            subject="child:owner-a",
            generation_id="gen-focus-a",
            source_turn_id="turn-focus-a",
            scene_id="scene-focus-a",
            scene_revision=1,
            expected_svg_sha256=validated.svg_sha256,
            expected_manifest_sha256=validated.manifest_sha256,
            svg_bytes=validated.svg_bytes,
        )
    )

    # Wait until Session A enters coordinator.focus_canvas
    await asyncio.wait_for(focus_entered.wait(), timeout=2.0)

    # WITHOUT releasing block_focus, perform an operation on Session B that acquires registry lock
    try:
        await asyncio.wait_for(
            registry.authorize_canvas_document(
                session_id=session_b.session_id,
                token=session_b.canvas_token,
                subject="child:owner-b",
            ),
            timeout=1.0,
        )
    finally:
        block_focus.set()
        await task_a


@pytest.mark.asyncio
async def test_registry_focus_canvas_fails_if_session_released_during_coordinator_focus() -> None:
    current_time = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)
    block_focus = asyncio.Event()
    focus_entered = asyncio.Event()

    class _BlockingCoordinator(_StubCoordinator):
        async def focus_canvas(self, scene: ActiveSvgCanvasScene) -> None:
            focus_entered.set()
            await block_focus.wait()
            self.focused_scene = scene

    class _BlockingFactory:
        def __init__(self) -> None:
            self.coordinators: list[_StubCoordinator] = []

        def create(self, **kwargs: Any) -> _StubCoordinator:
            coord = _BlockingCoordinator(kwargs["session_id"])
            self.coordinators.append(coord)
            return coord

    factory = _BlockingFactory()
    codec = RealtimeSessionTokenCodec("k" * 32)
    registry = RealtimeSessionRegistry(
        coordinator_factory=factory,  # type: ignore[arg-type]
        token_codec=codec,
        ttl=timedelta(seconds=120),
        active_session_ttl=timedelta(seconds=3600),
        max_sessions=10,
        now=lambda: current_time,
    )
    validator = SvgDocumentValidator(SvgValidationLimits())
    validated = validator.validate(VALID_SVG)

    session_a = await registry.create(
        request_id="req-a",
        subject="child:owner-a",
        age_band=AgeBand.UNKNOWN,
        timezone_id="UTC",
    )
    await registry.negotiate(
        session_id=session_a.session_id,
        token=session_a.webrtc_token,
        subject="child:owner-a",
        offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP,
    )
    await registry.claim(
        session_id=session_a.session_id,
        token=session_a.token,
    )

    task_a = asyncio.create_task(
        registry.focus_canvas(
            session_id=session_a.session_id,
            token=session_a.canvas_token,
            subject="child:owner-a",
            generation_id="gen-focus-a",
            source_turn_id="turn-focus-a",
            scene_id="scene-focus-a",
            scene_revision=1,
            expected_svg_sha256=validated.svg_sha256,
            expected_manifest_sha256=validated.manifest_sha256,
            svg_bytes=validated.svg_bytes,
        )
    )

    await asyncio.wait_for(focus_entered.wait(), timeout=2.0)

    # Release session A while coordinator is outside the lock
    await registry.release(session_a.session_id)

    block_focus.set()
    with pytest.raises(RealtimeSessionAuthenticationError) as exc_info:
        await task_a
    assert exc_info.value.failure == RealtimeSessionAuthFailure.UNKNOWN


@pytest.mark.asyncio
async def test_post_install_infallible_commit_cannot_produce_split_brain() -> None:
    """Prove no failure window exists between provider confirmation and canonical commit."""
    (
        coordinator,
        _realtime,
        _canvas_events,
        _memory,
        _specialist,
        jobs,
    ) = await _create_real_coordinator()
    try:
        validator = SvgDocumentValidator(SvgValidationLimits())
        doc_a = validator.validate(VALID_SVG)
        scene_a = ActiveSvgCanvasScene(
            generation_id="gen-a",
            source_turn_id="turn-a",
            scene_id="scene-a",
            revision=1,
            document=doc_a,
            first_rendered_sample=0,
        )
        doc_b = validator.validate(VALID_SVG_2)
        scene_b = ActiveSvgCanvasScene(
            generation_id="gen-b",
            source_turn_id="turn-b",
            scene_id="scene-b",
            revision=1,
            document=doc_b,
            first_rendered_sample=0,
        )

        await coordinator.focus_canvas(scene_a)
        assert jobs.state.workspace.focused_scene_id == "scene-a"

        # Now focus B: provider installation succeeds
        await coordinator.focus_canvas(scene_b)

        # Invariant: Once provider has B, workspace MUST have B and live harness MUST have B
        assert jobs.state.workspace.focused_scene_id == "scene-b"
        assert jobs.state.active == scene_b
        assert jobs.state.workspace.current == scene_b
        assert coordinator.harness is not None
        assert "Café cloud ☁️" not in coordinator.harness.instructions
    finally:
        await coordinator.close()


@pytest.mark.asyncio
async def test_concurrent_memory_reset_serialized_with_canvas_focus() -> None:
    """Prove memory_reset and focus_canvas are serialized by canvas_context_lock."""
    plan = AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / "ava_turn_plan_conversation_v3.json").read_text(
            encoding="utf-8"
        )
    )

    prepare_started = asyncio.Event()
    unblock_prepare = asyncio.Event()

    class _ControllableProvider(_RealtimeFocusPlanProvider):
        async def prepare_if_idle(self, *, harness: Any, turn_id: str) -> Any:
            prepare_started.set()
            await unblock_prepare.wait()
            return await super().prepare_if_idle(harness=harness, turn_id=turn_id)

    provider = _ControllableProvider(plan)
    (
        coordinator,
        _realtime,
        _canvas_events,
        _memory,
        _specialist,
        jobs,
    ) = await _create_real_coordinator(realtime_provider=provider)
    try:
        validator = SvgDocumentValidator(SvgValidationLimits())
        doc_a = validator.validate(VALID_SVG)
        scene_a = ActiveSvgCanvasScene(
            generation_id="gen-a",
            source_turn_id="turn-a",
            scene_id="scene-a",
            revision=1,
            document=doc_a,
            first_rendered_sample=0,
        )
        doc_b = validator.validate(VALID_SVG_2)
        scene_b = ActiveSvgCanvasScene(
            generation_id="gen-b",
            source_turn_id="turn-b",
            scene_id="scene-b",
            revision=1,
            document=doc_b,
            first_rendered_sample=0,
        )

        unblock_prepare.set()
        await coordinator.focus_canvas(scene_a)
        assert jobs.state.workspace.focused_scene_id == "scene-a"

        # Now start focus B with controllable prepare
        unblock_prepare.clear()
        focus_task = asyncio.create_task(coordinator.focus_canvas(scene_b))
        await prepare_started.wait()

        # While focus_canvas is inside canvas_context_lock awaiting prepare_if_idle,
        # trigger memory_reset concurrently
        reset_task = asyncio.create_task(coordinator.memory_reset(reset_revision=2))

        # Yield to event loop to allow reset_task to try to acquire the lock
        await asyncio.sleep(0.01)

        # Unblock prepare_if_idle: focus commits B, then memory_reset runs
        unblock_prepare.set()
        await focus_task
        await reset_task

        # Both operations completed cleanly without split-brain:
        # focus_canvas committed B, then memory_reset ran its authoritative reset,
        # clearing workspace artifacts and updating dynamic context.
        # Invariant: workspace is NOT left pointing to Scene A.
        assert jobs.state.workspace.scene("scene-a") is None
        assert jobs.state.workspace.focused_scene_id is None
        assert coordinator.harness is not None
        assert "Café cloud ☁️" not in coordinator.harness.instructions
    finally:
        await coordinator.close()


@pytest.mark.asyncio
async def test_registry_focus_canvas_does_not_fail_if_deadline_passes_during_coordinator_work() -> None:
    current_time = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)

    class _SlowCoordinator(_StubCoordinator):
        def __init__(self, session_id: str, clock_advance_fn: Any) -> None:
            super().__init__(session_id)
            self._advance = clock_advance_fn

        async def focus_canvas(self, scene: ActiveSvgCanvasScene) -> None:
            # Advance time past active_deadline while coordinator is working
            self._advance(timedelta(seconds=4000))
            self.focused_scene = scene

    class _SlowFactory:
        def __init__(self, clock_advance_fn: Any) -> None:
            self._advance = clock_advance_fn
            self.coordinators: list[_StubCoordinator] = []

        def create(self, **kwargs: Any) -> _StubCoordinator:
            coord = _SlowCoordinator(kwargs["session_id"], self._advance)
            self.coordinators.append(coord)
            return coord

    def _advance_time(delta: timedelta) -> None:
        nonlocal current_time
        current_time += delta

    factory = _SlowFactory(_advance_time)
    codec = RealtimeSessionTokenCodec("k" * 32)
    registry = RealtimeSessionRegistry(
        coordinator_factory=factory,  # type: ignore[arg-type]
        token_codec=codec,
        ttl=timedelta(seconds=120),
        active_session_ttl=timedelta(seconds=3600),
        max_sessions=10,
        now=lambda: current_time,
    )
    validator = SvgDocumentValidator(SvgValidationLimits())
    validated = validator.validate(VALID_SVG)

    session_a = await registry.create(
        request_id="req-a",
        subject="child:owner-a",
        age_band=AgeBand.UNKNOWN,
        timezone_id="UTC",
    )
    await registry.negotiate(
        session_id=session_a.session_id,
        token=session_a.webrtc_token,
        subject="child:owner-a",
        offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP,
    )
    await registry.claim(
        session_id=session_a.session_id,
        token=session_a.token,
    )

    # Focus was authorized when requested; deadline passed while coordinator was committing.
    # Registry must NOT convert this committed focus on a live session into an HTTP failure!
    focused = await registry.focus_canvas(
        session_id=session_a.session_id,
        token=session_a.canvas_token,
        subject="child:owner-a",
        generation_id="gen-focus-a",
        source_turn_id="turn-focus-a",
        scene_id="scene-focus-a",
        scene_revision=1,
        expected_svg_sha256=validated.svg_sha256,
        expected_manifest_sha256=validated.manifest_sha256,
        svg_bytes=validated.svg_bytes,
    )
    assert focused.scene_id == "scene-focus-a"
    assert factory.coordinators[0].focused_scene is not None


@pytest.mark.asyncio
async def test_focus_canvas_converges_to_b_when_readiness_changes_after_provider_send() -> None:
    """Prove that if provider send succeeds, coordinator converges canonical workspace to B."""
    plan = AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / "ava_turn_plan_conversation_v3.json").read_text(
            encoding="utf-8"
        )
    )

    class _ReadinessRacingProvider(_RealtimeFocusPlanProvider):
        def __init__(self, plan: Any) -> None:
            super().__init__(plan)
            self.send_count = 0

        async def prepare(self, *, harness: Any) -> Any:
            self.send_count += 1
            # Simulate state change during/immediately after send (e.g. readiness changes to unready)
            self._active_response_id = "resp_racing"
            return await super().prepare(harness=harness)

    provider = _ReadinessRacingProvider(plan)
    (
        coordinator,
        _realtime,
        _canvas_events,
        _memory,
        _specialist,
        jobs,
    ) = await _create_real_coordinator(realtime_provider=provider)
    try:
        validator = SvgDocumentValidator(SvgValidationLimits())
        doc_a = validator.validate(VALID_SVG)
        scene_a = ActiveSvgCanvasScene(
            generation_id="gen-a",
            source_turn_id="turn-a",
            scene_id="scene-a",
            revision=1,
            document=doc_a,
            first_rendered_sample=0,
        )
        doc_b = validator.validate(VALID_SVG_2)
        scene_b = ActiveSvgCanvasScene(
            generation_id="gen-b",
            source_turn_id="turn-b",
            scene_id="scene-b",
            revision=1,
            document=doc_b,
            first_rendered_sample=0,
        )

        # Baseline: initial focus A
        provider._active_response_id = None
        await coordinator.focus_canvas(scene_a)
        assert jobs.state.workspace.focused_scene_id == "scene-a"

        # Now focus B: provider.prepare sends B and mutates readiness during send
        provider._active_response_id = None
        await coordinator.focus_canvas(scene_b)

        # Provider send succeeded
        assert provider.send_count >= 2
        # Invariant: canonical workspace MUST converge to Scene B, not stay on A
        assert jobs.state.workspace.focused_scene_id == "scene-b"
        assert jobs.state.active == scene_b
        assert jobs.state.workspace.current == scene_b
        assert coordinator.harness is not None
        assert "Café cloud ☁️" not in coordinator.harness.instructions
    finally:
        await coordinator.close()
