from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ava_backend.canvas.svg_document import SvgDocumentValidator
from ava_backend.realtime.events_v4 import (
    AVA_CLIENT_EVENT_ADAPTER_V4,
    AVA_SERVER_EVENT_ADAPTER_V4,
    AvaSessionBootstrapV4,
    CanvasActivatedEventV4,
    CanvasDocumentAvailableEventV4,
    CanvasDraftAvailableEventV4,
    CanvasGenerationRejectionCode,
    CanvasGenerationStartedEventV4,
    CanvasDocumentPreparedEventV4,
    CanvasInteractionEventV4,
    CanvasPresentedEventV4,
    CanvasRenderProfileUpdatedEventV4,
    CanvasRejectedEventV4,
    InterruptRequestedEventV4,
    serialize_server_event_v4,
)
from ava_backend.realtime.speech_contract import SpeechPhase
from ava_backend.realtime.state_machine import (
    RealtimeStateError,
    RealtimeTurnStateMachine,
)

FIXTURES = Path(__file__).parents[2] / "protocol" / "fixtures"


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _speech_turn_payloads() -> list[dict[str, object]]:
    fixture_lines = (
        (FIXTURES / "ava_realtime_speech_events_v4.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    fixture = [json.loads(line) for line in fixture_lines]
    segment = fixture[2]
    metadata = fixture[3]
    alignment = fixture[4]
    chunk = fixture[5]
    warning = fixture[7]
    completed = fixture[8]
    return [
        {**fixture[0], "sequence": 0},
        {**fixture[1], "sequence": 1, "revision": 1, "phase": "connecting"},
        {**fixture[1], "sequence": 2, "revision": 2, "phase": "listening"},
        {**fixture[1], "sequence": 3, "revision": 3, "phase": "planning"},
        {**segment, "sequence": 4, "revision": 3},
        {**fixture[1], "sequence": 5, "revision": 4, "phase": "synthesizing"},
        {**metadata, "sequence": 6, "revision": 4},
        {**alignment, "sequence": 7, "revision": 4},
        {**chunk, "sequence": 8, "revision": 4},
        {**fixture[1], "sequence": 9, "revision": 5, "phase": "speaking"},
        {**warning, "sequence": 10, "revision": 5},
        {
            **completed,
            "sequence": 11,
            "revision": 6,
            "deliveredSampleCount": 4,
        },
    ]


def test_shared_capability_fixture_matches_backend_validator_exactly() -> None:
    assert _load("ava_svg_capabilities_v4.json") == (
        SvgDocumentValidator().capability_manifest()
    )


def test_session_bootstrap_fixture_locks_independent_canvas_lane() -> None:
    payload = _load("ava_session_bootstrap_v4.json")

    bootstrap = AvaSessionBootstrapV4.model_validate(payload)

    assert bootstrap.protocol_version == 4
    assert bootstrap.canvas_token == "canvas-token-value"
    assert bootstrap.canvas_document_base_url.endswith("/canvas-documents")
    assert bootstrap.canvas_document_base_url != bootstrap.event_url


def test_loopback_bootstrap_requires_explicit_test_context() -> None:
    payload = _load("ava_session_bootstrap_v4.json")
    assert isinstance(payload, dict)
    payload["eventUrl"] = "ws://127.0.0.1:8000/v4/realtime/sessions/session-1234/events"
    payload["webrtcUrl"] = (
        "http://127.0.0.1:8000/v4/realtime/sessions/session-1234/webrtc"
    )
    payload["canvasDocumentBaseUrl"] = (
        "http://127.0.0.1:8000/v4/realtime/sessions/session-1234/canvas-documents"
    )

    with pytest.raises(ValidationError):
        AvaSessionBootstrapV4.model_validate(payload)
    assert AvaSessionBootstrapV4.model_validate(
        payload,
        context={"allow_insecure_loopback": True},
    )


def test_server_canvas_lifecycle_fixtures_round_trip() -> None:
    payloads = _load("ava_realtime_canvas_events_v4.json")
    assert isinstance(payloads, list)

    events = [AVA_SERVER_EVENT_ADAPTER_V4.validate_python(item) for item in payloads]

    assert isinstance(events[0], CanvasDocumentAvailableEventV4)
    assert isinstance(events[2], CanvasActivatedEventV4)
    for event, payload in zip(events, payloads, strict=True):
        assert json.loads(serialize_server_event_v4(event)) == payload


def test_shared_generation_started_fixture_round_trips() -> None:
    payload = _load("ava_realtime_canvas_generation_v4.json")

    event = AVA_SERVER_EVENT_ADAPTER_V4.validate_python(payload)

    assert isinstance(event, CanvasGenerationStartedEventV4)
    assert event.operation == "new"
    assert json.loads(serialize_server_event_v4(event)) == payload


def test_canvas_generation_started_requires_valid_operation() -> None:
    base = _load("ava_realtime_canvas_generation_v4.json")

    # 10. new parses correctly
    event_new = AVA_SERVER_EVENT_ADAPTER_V4.validate_python({**base, "operation": "new"})
    assert isinstance(event_new, CanvasGenerationStartedEventV4)
    assert event_new.operation == "new"

    # 11. reviseFocused parses correctly
    event_revise = AVA_SERVER_EVENT_ADAPTER_V4.validate_python({**base, "operation": "reviseFocused"})
    assert isinstance(event_revise, CanvasGenerationStartedEventV4)
    assert event_revise.operation == "reviseFocused"

    # 9. requires operation (missing rejected)
    missing = {k: v for k, v in base.items() if k != "operation"}
    with pytest.raises(ValidationError):
        AVA_SERVER_EVENT_ADAPTER_V4.validate_python(missing)

    # 12. invalid operation rejected
    with pytest.raises(ValidationError):
        AVA_SERVER_EVENT_ADAPTER_V4.validate_python({**base, "operation": "invalidOp"})


def test_shared_visible_draft_fixture_has_no_scene_or_manifest_authority() -> None:
    payload = _load("ava_realtime_canvas_draft_v4.json")

    event = AVA_SERVER_EVENT_ADAPTER_V4.validate_python(payload)

    assert isinstance(event, CanvasDraftAvailableEventV4)
    assert event.authoritative is False
    assert event.interactive is False
    assert not hasattr(event, "scene_id")
    assert not hasattr(event, "manifest_sha256")
    assert json.loads(serialize_server_event_v4(event)) == payload


def test_client_canvas_lifecycle_fixtures_are_session_scoped() -> None:
    payloads = _load("ava_realtime_canvas_client_events_v4.json")
    assert isinstance(payloads, list)

    events = [AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(item) for item in payloads]

    assert isinstance(events[1], CanvasDocumentPreparedEventV4)
    assert isinstance(events[2], CanvasPresentedEventV4)
    assert isinstance(events[3], CanvasInteractionEventV4)
    assert all("turnId" not in payload for payload in payloads[1:])


def test_canvas_render_profile_update_is_strict_and_session_scoped() -> None:
    profile: dict[str, object] = {
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
        "minimumInteractionTargetPoints": 44.0,
        "textScaleFactor": 1.15,
    }
    payload: dict[str, object] = {
        "protocolVersion": 4,
        "event": "canvasRenderProfileUpdated",
        "sessionId": "session-1234",
        "sequence": 4,
        "profileRevision": 7,
        "renderProfile": profile,
    }

    event = AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(payload)

    assert isinstance(event, CanvasRenderProfileUpdatedEventV4)
    assert event.profile_revision == 7
    assert event.render_profile.usable_aspect_ratio == 1.6
    with pytest.raises(ValidationError):
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
            {
                **payload,
                "profileRevision": 6,
                "renderProfile": {
                    **profile,
                    "orientation": "portrait",
                },
            }
        )


def test_canvas_prepared_cannot_be_confused_with_presented() -> None:
    payloads = _load("ava_realtime_canvas_client_events_v4.json")
    assert isinstance(payloads, list)
    prepared = payloads[1]
    assert isinstance(prepared, dict)
    prepared["event"] = "canvasPresented"

    with pytest.raises(ValidationError, match="firstRenderedSample"):
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(prepared)


def test_canvas_controls_reject_turn_id_and_unknown_content_identity() -> None:
    payloads = _load("ava_realtime_canvas_client_events_v4.json")
    assert isinstance(payloads, list)
    interaction = payloads[3]
    assert isinstance(interaction, dict)
    interaction["turnId"] = "turn-1234"
    interaction["svgSha256"] = "not-a-hash"

    with pytest.raises(ValidationError):
        AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(interaction)


def test_canvas_rejection_identifies_exactly_one_call_or_generation() -> None:
    base = {
        "protocolVersion": 4,
        "event": "canvasRejected",
        "sessionId": "session-1234",
        "sequence": 3,
        "revision": 1,
        "sourceTurnId": "turn-1234",
        "rejectionCode": "providerInvalid",
        "message": "The optional drawing request was rejected.",
        "retryable": False,
    }
    by_call = AVA_SERVER_EVENT_ADAPTER_V4.validate_python(
        {**base, "callId": "call-1234"}
    )
    assert isinstance(by_call, CanvasRejectedEventV4)
    assert json.loads(serialize_server_event_v4(by_call)) == {
        **base,
        "callId": "call-1234",
    }
    for invalid in (base, {**base, "callId": "call-1234", "generationId": "gen-1"}):
        with pytest.raises(ValidationError):
            AVA_SERVER_EVENT_ADAPTER_V4.validate_python(invalid)


def test_canvas_generation_limit_is_a_structured_v4_rejection() -> None:
    event = AVA_SERVER_EVENT_ADAPTER_V4.validate_python(
        {
            "protocolVersion": 4,
            "event": "canvasRejected",
            "sessionId": "session-1234",
            "sequence": 4,
            "revision": 1,
            "generationId": "generation-1234",
            "sourceTurnId": "turn-1234",
            "rejectionCode": "generationLimitExceeded",
            "message": "The canvas illustrator exceeded its generation limit.",
            "retryable": True,
        }
    )

    assert isinstance(event, CanvasRejectedEventV4)
    assert (
        event.rejection_code == CanvasGenerationRejectionCode.GENERATION_LIMIT_EXCEEDED
    )


def test_v4_segment_rejects_legacy_typed_canvas_cues() -> None:
    payload = next(
        payload
        for payload in _speech_turn_payloads()
        if payload["event"] == "segmentReady"
    )
    segment = dict(payload["segment"])  # type: ignore[arg-type]
    segment["canvasCues"] = [{"legacy": "typed"}]
    payload["segment"] = segment

    with pytest.raises(ValidationError, match="legacy canvas cues"):
        AVA_SERVER_EVENT_ADAPTER_V4.validate_python(payload)


def test_v4_interrupt_request_is_turn_and_stream_bound() -> None:
    payload = {
        "protocolVersion": 4,
        "event": "interruptRequested",
        "sessionId": "session-1234",
        "turnId": "turn-1234",
        "sequence": 14,
        "revision": 5,
        "streamId": "stream-1234",
        "reason": "providerSpeechStarted",
    }
    event = AVA_SERVER_EVENT_ADAPTER_V4.validate_python(payload)

    assert isinstance(event, InterruptRequestedEventV4)
    assert json.loads(serialize_server_event_v4(event)) == payload


def test_v4_turn_state_machine_accepts_synthesizing_interrupt_after_metadata() -> None:
    payloads = _speech_turn_payloads()
    machine = RealtimeTurnStateMachine()
    for payload in payloads[:7]:
        machine.accept(AVA_SERVER_EVENT_ADAPTER_V4.validate_python(payload))
    interrupt = AVA_SERVER_EVENT_ADAPTER_V4.validate_python(
        {
            "protocolVersion": 4,
            "event": "interruptRequested",
            "sessionId": "session-1234",
            "turnId": "turn-1234",
            "sequence": 7,
            "revision": 4,
            "streamId": "stream-1234",
            "reason": "providerSpeechStarted",
        }
    )

    snapshot = machine.accept(interrupt)

    assert snapshot.phase == SpeechPhase.SYNTHESIZING
    assert snapshot.received_sample_count == 0


def test_v4_turn_state_machine_rejects_synthesizing_interrupt_without_metadata() -> (
    None
):
    payloads = _speech_turn_payloads()
    machine = RealtimeTurnStateMachine()
    for payload in payloads[:6]:
        machine.accept(AVA_SERVER_EVENT_ADAPTER_V4.validate_python(payload))
    interrupt = AVA_SERVER_EVENT_ADAPTER_V4.validate_python(
        {
            "protocolVersion": 4,
            "event": "interruptRequested",
            "sessionId": "session-1234",
            "turnId": "turn-1234",
            "sequence": 6,
            "revision": 4,
            "streamId": "stream-1234",
            "reason": "providerSpeechStarted",
        }
    )

    with pytest.raises(RealtimeStateError, match="audioMetadata"):
        machine.accept(interrupt)


def test_v4_turn_state_machine_accepts_speech_and_isolates_memory_warning() -> None:
    machine = RealtimeTurnStateMachine()
    events = [
        AVA_SERVER_EVENT_ADAPTER_V4.validate_python(payload)
        for payload in _speech_turn_payloads()
    ]
    for event in events:
        machine.accept(event)

    assert machine.snapshot.phase == SpeechPhase.COMPLETED
    assert machine.snapshot.terminal is True
    assert machine.snapshot.segment_count == 1
    assert machine.snapshot.received_sample_count == 4
    with pytest.raises(RealtimeStateError, match="terminal"):
        machine.accept(events[-1])


def test_v4_turn_state_machine_accepts_owned_planning_only_completion() -> None:
    payloads = _speech_turn_payloads()
    machine = RealtimeTurnStateMachine()
    for payload in payloads[:4]:
        machine.accept(AVA_SERVER_EVENT_ADAPTER_V4.validate_python(payload))

    snapshot = machine.accept(
        AVA_SERVER_EVENT_ADAPTER_V4.validate_python(
            {
                **payloads[-1],
                "sequence": 4,
                "revision": 4,
                "deliveredSampleCount": 0,
                "deliveredText": "",
            }
        )
    )

    assert snapshot.phase == SpeechPhase.COMPLETED
    assert snapshot.received_sample_count == 0
    assert snapshot.segment_count == 0
    assert snapshot.terminal is True


def test_v4_turn_state_machine_rejects_arbitrary_empty_completion() -> None:
    payloads = _speech_turn_payloads()
    machine = RealtimeTurnStateMachine()
    for payload in payloads[:3]:
        machine.accept(AVA_SERVER_EVENT_ADAPTER_V4.validate_python(payload))

    with pytest.raises(RealtimeStateError, match="planning"):
        machine.accept(
            AVA_SERVER_EVENT_ADAPTER_V4.validate_python(
                {
                    **payloads[-1],
                    "sequence": 3,
                    "revision": 3,
                    "deliveredSampleCount": 0,
                    "deliveredText": "",
                }
            )
        )


def test_v4_turn_state_machine_rejects_late_pcm_and_stale_turn_identity() -> None:
    payloads = _speech_turn_payloads()
    machine = RealtimeTurnStateMachine()
    for payload in payloads[:10]:
        machine.accept(AVA_SERVER_EVENT_ADAPTER_V4.validate_python(payload))

    late_pcm = {
        **payloads[8],
        "sequence": 10,
        "revision": 5,
        "chunkIndex": 1,
        "sampleOffset": 4,
    }
    with pytest.raises(RealtimeStateError, match="synthesizing"):
        machine.accept(AVA_SERVER_EVENT_ADAPTER_V4.validate_python(late_pcm))

    stale_turn = {**payloads[9], "turnId": "turn-stale", "revision": 6}
    with pytest.raises(RealtimeStateError, match="turnId"):
        machine.accept(AVA_SERVER_EVENT_ADAPTER_V4.validate_python(stale_turn))


def test_v4_cancellation_registration_is_idempotent_and_owned() -> None:
    machine = RealtimeTurnStateMachine()
    machine.accept(
        AVA_SERVER_EVENT_ADAPTER_V4.validate_python(_speech_turn_payloads()[0])
    )

    assert machine.register_cancellation("cancel-1234") is True
    assert machine.register_cancellation("cancel-1234") is False
    with pytest.raises(RealtimeStateError, match="different cancellation"):
        machine.register_cancellation("cancel-5678")
