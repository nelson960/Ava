from __future__ import annotations

import json
import random
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from ava_backend.realtime.bounded_queue import (
    BoundedAsyncQueue,
    QueueCapacityExceeded,
    QueueClosed,
)
from ava_backend.realtime.events import (
    AVA_CLIENT_EVENT_ADAPTER,
    AVA_SERVER_EVENT_ADAPTER,
    AvaSessionBootstrap,
    AvaTurnPhase,
    serialize_server_event,
)
from ava_backend.realtime.fakes import (
    DeterministicArgumentDeltaStream,
    DeterministicServerEventStream,
)
from ava_backend.realtime.plan import MAX_REALTIME_SPEECH_SEGMENTS, AvaTurnPlan
from ava_backend.realtime.state_machine import (
    RealtimeStateError,
    RealtimeTurnStateMachine,
)
from ava_backend.realtime.turn_parser import (
    StreamingTurnParseError,
    StreamingSpeechParser,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"


def _json(name: str) -> object:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _plan_payload() -> dict[str, object]:
    payload = _json("ava_turn_plan_v3.json")
    assert isinstance(payload, dict)
    return payload


def _speech_segment_payloads(count: int) -> list[dict[str, object]]:
    payload = _json("ava_turn_plan_conversation_v3.json")
    assert isinstance(payload, dict)
    raw_segments = payload["segments"]
    assert isinstance(raw_segments, list)
    template = raw_segments[0]
    assert isinstance(template, dict)
    return [
        {
            **deepcopy(template),
            "id": f"part_{index + 1}",
            "speech": f"Part {index + 1}.",
        }
        for index in range(count)
    ]


def _event_payloads() -> list[dict[str, object]]:
    payload = _json("ava_realtime_events_v3.json")
    assert isinstance(payload, list)
    assert all(isinstance(item, dict) for item in payload)
    return payload


def test_shared_protocol_v3_fixtures_round_trip_strictly() -> None:
    conversation_payload = _json("ava_turn_plan_conversation_v3.json")
    conversation = AvaTurnPlan.model_validate(conversation_payload)
    assert conversation.model_dump(mode="json", by_alias=True) == conversation_payload
    assert len(conversation.segments) == 1
    assert conversation.segments[0].canvas_cues == []

    plan_payload = _plan_payload()
    plan = AvaTurnPlan.model_validate(plan_payload)
    assert plan.model_dump(mode="json", by_alias=True) == plan_payload
    assert plan.speech.startswith("A plant cell")
    assert plan.segments[1].canvas_cues[0].transaction.revision == 1

    bootstrap_payload = _json("ava_session_bootstrap_v3.json")
    bootstrap = AvaSessionBootstrap.model_validate(bootstrap_payload)
    assert bootstrap.model_dump(mode="json", by_alias=True) == bootstrap_payload

    event_payloads = _event_payloads()
    events = [
        AVA_SERVER_EVENT_ADAPTER.validate_python(payload) for payload in event_payloads
    ]
    assert [
        event.model_dump(mode="json", by_alias=True) for event in events
    ] == event_payloads

    client_payloads = _json("ava_realtime_client_events_v3.json")
    assert isinstance(client_payloads, list)
    client_events = [
        AVA_CLIENT_EVENT_ADAPTER.validate_python(payload) for payload in client_payloads
    ]
    assert [
        event.model_dump(mode="json", by_alias=True) for event in client_events
    ] == client_payloads


def test_python_and_dart_wire_contracts_require_explicit_fields() -> None:
    missing_occurrence = _plan_payload()
    anchor = missing_occurrence["segments"][1]["canvasCues"][0]["anchor"]  # type: ignore[index]
    del anchor["occurrence"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="occurrence"):
        AvaTurnPlan.model_validate(missing_occurrence)

    missing_transform_scale = _plan_payload()
    node = missing_transform_scale["segments"][1]["canvasCues"][0]["transaction"][  # type: ignore[index]
        "operations"
    ][0]["node"]
    del node["transform"]["scale"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="scale"):
        AvaTurnPlan.model_validate(missing_transform_scale)

    event = deepcopy(_event_payloads()[0])
    del event["protocolVersion"]
    with pytest.raises(ValidationError, match="protocolVersion"):
        AVA_SERVER_EVENT_ADAPTER.validate_python(event)

    snake_case = deepcopy(_event_payloads()[0])
    snake_case["protocol_version"] = snake_case.pop("protocolVersion")
    with pytest.raises(ValidationError):
        AVA_SERVER_EVENT_ADAPTER.validate_python(snake_case)

    schema = AvaTurnPlan.model_json_schema()
    assert "segments" in schema["required"]


def test_production_server_wire_preserves_required_nullable_fields() -> None:
    payloads = _event_payloads()
    canvas_payload = next(item for item in payloads if item["event"] == "canvasCue")
    canvas_event = AVA_SERVER_EVENT_ADAPTER.validate_python(canvas_payload)
    canvas_wire = json.loads(serialize_server_event(canvas_event))
    create_operation = canvas_wire["transaction"]["operations"][0]
    assert "parentId" in create_operation["node"]
    assert create_operation["node"]["parentId"] is None

    alignment_payload = deepcopy(
        next(item for item in payloads if item["event"] == "alignmentBatch")
    )
    alignment_payload["spans"][0]["phoneme"] = None  # type: ignore[index]
    alignment_event = AVA_SERVER_EVENT_ADAPTER.validate_python(alignment_payload)
    alignment_wire = json.loads(serialize_server_event(alignment_event))
    assert "phoneme" in alignment_wire["spans"][0]
    assert alignment_wire["spans"][0]["phoneme"] is None


def test_bootstrap_allows_insecure_channels_only_for_explicit_loopback() -> None:
    payload = _json("ava_session_bootstrap_v3.json")
    assert isinstance(payload, dict)
    payload["eventUrl"] = "ws://127.0.0.1:8080/v3/realtime/sessions/session-1234/events"
    payload["webrtcUrl"] = (
        "http://127.0.0.1:8080/v3/realtime/sessions/session-1234/webrtc"
    )

    with pytest.raises(ValidationError):
        AvaSessionBootstrap.model_validate(payload)

    accepted = AvaSessionBootstrap.model_validate(
        payload,
        context={"allow_insecure_loopback": True},
    )
    assert accepted.event_url.startswith("ws://127.0.0.1")

    payload["eventUrl"] = "ws://ava.example.test/v3/realtime/events"
    with pytest.raises(ValidationError):
        AvaSessionBootstrap.model_validate(
            payload,
            context={"allow_insecure_loopback": True},
        )


def test_v3_plan_rejects_raw_svg_unknown_motion_and_bad_anchor() -> None:
    raw_svg = _plan_payload()
    cue = raw_svg["segments"][1]["canvasCues"][0]  # type: ignore[index]
    cue["rawSvg"] = "<svg onload='bad()'/>"  # type: ignore[index]
    with pytest.raises(ValidationError):
        AvaTurnPlan.model_validate(raw_svg)

    raw_motion = _plan_payload()
    segment = raw_motion["segments"][0]  # type: ignore[index]
    segment["boneRotations"] = {"head": [0, 1, 0]}  # type: ignore[index]
    with pytest.raises(ValidationError):
        AvaTurnPlan.model_validate(raw_motion)

    bad_anchor = _plan_payload()
    anchor = bad_anchor["segments"][1]["canvasCues"][0]["anchor"]  # type: ignore[index]
    anchor["phrase"] = "a phrase that is absent"  # type: ignore[index]
    with pytest.raises(ValidationError, match="anchor phrase"):
        AvaTurnPlan.model_validate(bad_anchor)


def test_v3_plan_rejects_unknown_enum_and_oversized_speech() -> None:
    unknown_enum = _plan_payload()
    unknown_enum["segments"][0]["delivery"] = "dramatic"  # type: ignore[index]
    with pytest.raises(ValidationError, match="delivery"):
        AvaTurnPlan.model_validate(unknown_enum)

    oversized = _plan_payload()
    oversized["segments"][0]["speech"] = "a" * 601  # type: ignore[index]
    with pytest.raises(ValidationError, match="speech"):
        AvaTurnPlan.model_validate(oversized)


def test_v3_speech_segment_limit_matches_plan_and_server_event() -> None:
    plan_payload = _json("ava_turn_plan_conversation_v3.json")
    assert isinstance(plan_payload, dict)
    plan_payload["segments"] = _speech_segment_payloads(MAX_REALTIME_SPEECH_SEGMENTS)
    assert len(AvaTurnPlan.model_validate(plan_payload).segments) == (
        MAX_REALTIME_SPEECH_SEGMENTS
    )

    oversized_plan = deepcopy(plan_payload)
    oversized_plan["segments"] = _speech_segment_payloads(
        MAX_REALTIME_SPEECH_SEGMENTS + 1
    )
    with pytest.raises(ValidationError, match="segments"):
        AvaTurnPlan.model_validate(oversized_plan)

    segment_ready = next(
        deepcopy(payload)
        for payload in _event_payloads()
        if payload["event"] == "segmentReady"
    )
    segment_ready["segmentIndex"] = MAX_REALTIME_SPEECH_SEGMENTS - 1
    accepted = AVA_SERVER_EVENT_ADAPTER.validate_python(segment_ready)
    assert accepted.segment_index == MAX_REALTIME_SPEECH_SEGMENTS - 1

    segment_ready["segmentIndex"] = MAX_REALTIME_SPEECH_SEGMENTS
    with pytest.raises(ValidationError, match="segmentIndex"):
        AVA_SERVER_EVENT_ADAPTER.validate_python(segment_ready)


def test_v3_canvas_rejects_unsafe_path_and_non_contiguous_revision() -> None:
    unsafe = _plan_payload()
    transaction = unsafe["segments"][1]["canvasCues"][0]["transaction"]  # type: ignore[index]
    transaction["operations"].append(  # type: ignore[union-attr]
        {
            "operation": "create",
            "node": {
                "id": "unsafe_path",
                "parentId": None,
                "label": "Unsafe path",
                "visible": True,
                "transform": {
                    "translateX": 0.0,
                    "translateY": 0.0,
                    "scale": 1.0,
                    "rotationDegrees": 0.0,
                },
                "style": {
                    "fill": "transparent",
                    "stroke": "ink",
                    "strokeWidth": 2.0,
                    "opacity": 1.0,
                    "strokeCap": "round",
                },
                "kind": "path",
                "pathData": "M 0 0 <script>",
            },
        }
    )
    with pytest.raises(ValidationError, match="pathData"):
        AvaTurnPlan.model_validate(unsafe)

    skipped_revision = _plan_payload()
    transaction = skipped_revision["segments"][1]["canvasCues"][0][  # type: ignore[index]
        "transaction"
    ]
    transaction["revision"] = 2  # type: ignore[index]
    with pytest.raises(ValidationError, match="baseRevision"):
        AvaTurnPlan.model_validate(skipped_revision)


@pytest.mark.asyncio
async def test_streaming_speech_parser_emits_complete_phrases_at_any_fragmentation() -> (
    None
):
    speech = "Hello there. Water moves in a loop!"
    stream = DeterministicArgumentDeltaStream(speech, (1,))
    parser = StreamingSpeechParser()
    emitted = []
    async for delta in stream.stream():
        emitted.extend(parser.feed(delta))
    emitted.extend(parser.finish())

    assert [segment.id for segment in emitted] == ["speech_1", "speech_2"]
    assert [segment.speech for segment in emitted] == [
        "Hello there.",
        "Water moves in a loop!",
    ]
    assert tuple(emitted) == parser.emitted_segments


@pytest.mark.asyncio
async def test_streaming_speech_parser_is_deterministic_across_chunk_patterns() -> None:
    speech = "First thought. Second thought? Final thought!"
    random_source = random.Random(20260729)
    expected = ["First thought.", "Second thought?", "Final thought!"]
    for _ in range(40):
        parser = StreamingSpeechParser()
        emitted = []
        sizes = tuple(random_source.randint(1, 17) for _ in range(8))
        stream = DeterministicArgumentDeltaStream(speech, sizes)
        async for delta in stream.stream():
            emitted.extend(parser.feed(delta))
        emitted.extend(parser.finish())
        assert [segment.speech for segment in emitted] == expected


def test_streaming_speech_parser_safely_splits_one_long_phrase() -> None:
    speech = ("gentle words " * 58).strip()
    parser = StreamingSpeechParser(max_total_characters=1_000)
    emitted = list(parser.feed(speech))
    emitted.extend(parser.finish())

    assert len(emitted) == 2
    assert " ".join(segment.speech for segment in emitted) == speech
    assert all(len(segment.speech) <= 600 for segment in emitted)


def test_streaming_speech_parser_preserves_tool_response_boundaries() -> None:
    parser = StreamingSpeechParser()
    assert parser.feed("I’ll draw that for you") == ()
    bridge = parser.finish_response_chunk()
    continuation = parser.feed("Here is the finished picture.")
    ending = parser.finish()

    assert [segment.speech for segment in (*bridge, *continuation, *ending)] == [
        "I’ll draw that for you",
        "Here is the finished picture.",
    ]


def test_streaming_speech_parser_allows_twelve_bounded_short_phrases() -> None:
    parser = StreamingSpeechParser()
    emitted = list(parser.feed(" ".join(f"Sentence {index}." for index in range(12))))
    emitted.extend(parser.finish())

    assert len(emitted) == 12

    parser = StreamingSpeechParser()
    with pytest.raises(StreamingTurnParseError, match="12-segment"):
        parser.feed(" ".join(f"Sentence {index}." for index in range(13)) + " ")


def test_streaming_speech_parser_fails_loudly_on_invalid_streams() -> None:
    with pytest.raises(StreamingTurnParseError, match="control"):
        StreamingSpeechParser().feed("hello\u0000")

    parser = StreamingSpeechParser(max_total_characters=10, max_segment_characters=10)
    with pytest.raises(StreamingTurnParseError, match="1800"):
        parser.feed("eleven chars")

    with pytest.raises(StreamingTurnParseError, match="without child-facing speech"):
        StreamingSpeechParser().finish()

    parser = StreamingSpeechParser()
    parser.feed("Done.")
    parser.finish()
    with pytest.raises(StreamingTurnParseError, match="finished"):
        parser.feed("Again.")


def test_state_machine_accepts_shared_event_sequence() -> None:
    machine = RealtimeTurnStateMachine()
    for payload in _event_payloads():
        machine.accept(AVA_SERVER_EVENT_ADAPTER.validate_python(payload))

    snapshot = machine.snapshot
    assert snapshot.phase == AvaTurnPhase.COMPLETED
    assert snapshot.terminal is True
    assert snapshot.segment_count == 2
    assert snapshot.received_sample_count == 8


def test_state_machine_rejects_duplicate_stale_and_post_terminal_events() -> None:
    payloads = _event_payloads()
    machine = RealtimeTurnStateMachine()
    machine.accept(AVA_SERVER_EVENT_ADAPTER.validate_python(payloads[0]))
    with pytest.raises(RealtimeStateError, match="sequence"):
        machine.accept(AVA_SERVER_EVENT_ADAPTER.validate_python(payloads[0]))

    stale = deepcopy(payloads[1])
    stale["revision"] = 0
    with pytest.raises(RealtimeStateError, match="increment revision"):
        machine.accept(AVA_SERVER_EVENT_ADAPTER.validate_python(stale))

    machine = RealtimeTurnStateMachine()
    events = [AVA_SERVER_EVENT_ADAPTER.validate_python(payload) for payload in payloads]
    for event in events:
        machine.accept(event)
    with pytest.raises(RealtimeStateError, match="terminal"):
        machine.accept(events[-1])


def test_session_scoped_interaction_rejection_is_non_terminal() -> None:
    payloads = _event_payloads()
    machine = RealtimeTurnStateMachine()
    for payload in payloads:
        machine.accept(AVA_SERVER_EVENT_ADAPTER.validate_python(payload))

    terminal = machine.snapshot
    rejection = AVA_SERVER_EVENT_ADAPTER.validate_python(
        {
            "protocolVersion": 3,
            "event": "interactionRejected",
            "sessionId": "session-1234",
            "turnId": "turn-1234",
            "sequence": terminal.next_sequence,
            "revision": terminal.revision,
            "sceneId": "plant_cell_scene",
            "sceneRevision": 1,
            "interactionRevision": 7,
            "bindingId": "select_nucleus",
            "rejectionCode": "interactionRevisionStale",
            "message": "Canvas interaction revision is stale.",
        }
    )

    snapshot = machine.accept(rejection)
    assert snapshot.terminal is True
    assert snapshot.phase == AvaTurnPhase.COMPLETED
    assert snapshot.next_sequence == terminal.next_sequence + 1


def test_speaking_is_a_hard_end_of_synthesis_boundary_for_pcm() -> None:
    payloads = _event_payloads()
    machine = RealtimeTurnStateMachine()
    for payload in payloads[:13]:
        machine.accept(AVA_SERVER_EVENT_ADAPTER.validate_python(payload))
    assert machine.snapshot.phase == AvaTurnPhase.SPEAKING

    late_audio = deepcopy(payloads[11])
    late_audio.update(
        {
            "sequence": 13,
            "revision": 5,
            "chunkIndex": 2,
            "sampleOffset": 8,
        }
    )
    with pytest.raises(RealtimeStateError, match="synthesizing"):
        machine.accept(AVA_SERVER_EVENT_ADAPTER.validate_python(late_audio))


def test_canvas_cue_cannot_target_audio_that_has_not_arrived() -> None:
    payloads = deepcopy(_event_payloads())
    cue = payloads[10]
    cue["sampleOffset"] = 5
    machine = RealtimeTurnStateMachine()
    for payload in payloads[:10]:
        machine.accept(AVA_SERVER_EVENT_ADAPTER.validate_python(payload))
    with pytest.raises(RealtimeStateError, match="received PCM"):
        machine.accept(AVA_SERVER_EVENT_ADAPTER.validate_python(cue))


@pytest.mark.parametrize(
    ("prefix_length", "delivered_samples"),
    (
        (1, 0),  # created
        (2, 0),  # connecting
        (4, 0),  # planning
        (8, 0),  # TTS metadata received
        (12, 4),  # playback started
    ),
)
def test_state_machine_accepts_cancellation_from_every_active_stage(
    prefix_length: int,
    delivered_samples: int,
) -> None:
    payloads = _event_payloads()
    cancellation_payloads = deepcopy(payloads[:prefix_length])
    current = cancellation_payloads[-1]
    sequence = int(current["sequence"]) + 1
    revision = int(current["revision"]) + 1
    cancellation_payloads.append(
        {
            "protocolVersion": 3,
            "event": "turnPhaseChanged",
            "sessionId": "session-1234",
            "turnId": "turn-1234",
            "sequence": sequence,
            "revision": revision,
            "phase": "cancelling",
        }
    )
    cancellation_payloads.append(
        {
            "protocolVersion": 3,
            "event": "turnCancelled",
            "sessionId": "session-1234",
            "turnId": "turn-1234",
            "sequence": sequence + 1,
            "revision": revision + 1,
            "deliveredSampleCount": delivered_samples,
            "deliveredText": "A plant" if delivered_samples else "",
            "reason": "childStartedSpeaking",
        }
    )

    machine = RealtimeTurnStateMachine()
    for payload in cancellation_payloads:
        machine.accept(AVA_SERVER_EVENT_ADAPTER.validate_python(payload))
    assert machine.snapshot.phase == AvaTurnPhase.CANCELLED
    assert machine.snapshot.terminal is True


def test_cancel_request_registration_is_idempotent_by_request_id() -> None:
    machine = RealtimeTurnStateMachine()
    machine.accept(AVA_SERVER_EVENT_ADAPTER.validate_python(_event_payloads()[0]))
    assert machine.register_cancellation("cancel-1234") is True
    assert machine.register_cancellation("cancel-1234") is False
    with pytest.raises(RealtimeStateError, match="different cancellation"):
        machine.register_cancellation("cancel-5678")


def test_state_machine_keeps_speech_complete_when_persistence_fails() -> None:
    payloads = deepcopy(_event_payloads()[:13])
    payloads.append(
        {
            "protocolVersion": 3,
            "event": "memoryPersistenceFailed",
            "sessionId": "session-1234",
            "turnId": "turn-1234",
            "sequence": 13,
            "revision": 5,
            "failure": {
                "failureId": "failure-protocol-memory",
                "component": "memoryStore",
                "code": "memoryUnavailable",
                "message": "Delivered turn could not be persisted.",
                "retryable": True,
            },
        }
    )
    payloads.append(
        {
            "protocolVersion": 3,
            "event": "turnCompleted",
            "sessionId": "session-1234",
            "turnId": "turn-1234",
            "sequence": 14,
            "revision": 6,
            "deliveredSampleCount": 8,
            "deliveredText": "A plant",
        }
    )

    machine = RealtimeTurnStateMachine()
    for payload in payloads:
        machine.accept(AVA_SERVER_EVENT_ADAPTER.validate_python(payload))
    assert machine.snapshot.phase == AvaTurnPhase.COMPLETED
    assert machine.snapshot.received_sample_count == 8


def test_audio_metadata_and_binary_descriptor_fail_loudly() -> None:
    metadata = next(
        deepcopy(payload)
        for payload in _event_payloads()
        if payload["event"] == "audioMetadata"
    )
    metadata["sampleRateHz"] = 44_100
    with pytest.raises(ValidationError, match="sampleRateHz"):
        AVA_SERVER_EVENT_ADAPTER.validate_python(metadata)

    descriptor = next(
        deepcopy(payload)
        for payload in _event_payloads()
        if payload["event"] == "audioChunk"
    )
    descriptor["byteLength"] = 7
    with pytest.raises(ValidationError, match="byteLength"):
        AVA_SERVER_EVENT_ADAPTER.validate_python(descriptor)


@pytest.mark.asyncio
async def test_bounded_queue_and_fake_event_stream_are_deterministic() -> None:
    queue: BoundedAsyncQueue[bytes] = BoundedAsyncQueue(
        max_items=2,
        max_weight=4,
        weigh=len,
    )
    queue.put_nowait(b"ab")
    queue.put_nowait(b"cd")
    with pytest.raises(QueueCapacityExceeded):
        queue.put_nowait(b"e")
    assert await queue.get() == b"ab"
    queue.close()
    assert await queue.get() == b"cd"
    with pytest.raises(QueueClosed):
        await queue.get()

    events = [
        AVA_SERVER_EVENT_ADAPTER.validate_python(payload)
        for payload in _event_payloads()[:3]
    ]
    stream = DeterministicServerEventStream(events)
    received = [event async for event in stream.stream()]
    assert received == events
