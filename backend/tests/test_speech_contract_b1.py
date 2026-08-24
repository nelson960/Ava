from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ava_backend.realtime.events_v4 import (
    AVA_SERVER_EVENT_ADAPTER_V4,
    SegmentReadyEventV4,
    serialize_server_event_v4,
)
from ava_backend.realtime.plan import AvaTurnPlan, AvaTurnSegment
from ava_backend.realtime.speech_contract import SpeechFailureCode, SpeechPhrase
from ava_backend.realtime.turn_parser import StreamingSpeechParser


ROOT = Path(__file__).resolve().parents[2]
SPEECH_WIRE_FIXTURE = (
    ROOT / "protocol" / "fixtures" / "ava_realtime_speech_events_v4.jsonl"
)
PRODUCTION_ROOT = ROOT / "backend" / "src" / "ava_backend"


def _speech_wire_lines() -> list[str]:
    return SPEECH_WIRE_FIXTURE.read_text(encoding="utf-8").splitlines()


def _segment_payload() -> dict[str, object]:
    return {
        "protocolVersion": 4,
        "event": "segmentReady",
        "sessionId": "session-1234",
        "sequence": 4,
        "revision": 3,
        "turnId": "turn-1234",
        "segmentIndex": 0,
        "segment": {
            "id": "answer",
            "speech": "A gentle answer.",
            "delivery": "warm",
            "expression": "curious",
            "gaze": "child",
            "motionIntent": "acknowledge",
            "canvasCues": [],
        },
    }


def test_v4_speech_events_are_byte_and_field_stable() -> None:
    for expected_json in _speech_wire_lines():
        event = AVA_SERVER_EVENT_ADAPTER_V4.validate_json(expected_json)

        assert serialize_server_event_v4(event) == expected_json
        assert json.loads(serialize_server_event_v4(event)) == json.loads(expected_json)


def test_streaming_phrase_output_is_frozen_across_fragmentation() -> None:
    text = "Hello there. Water moves in a loop!"
    expected = [
        {
            "id": "speech_1",
            "speech": "Hello there.",
            "delivery": "neutral",
            "expression": "neutral",
            "gaze": "child",
            "motionIntent": "none",
            "canvasCues": [],
        },
        {
            "id": "speech_2",
            "speech": "Water moves in a loop!",
            "delivery": "neutral",
            "expression": "neutral",
            "gaze": "child",
            "motionIntent": "none",
            "canvasCues": [],
        },
    ]

    for chunk_size in (1, 2, 7, len(text)):
        parser = StreamingSpeechParser()
        phrases = []
        for offset in range(0, len(text), chunk_size):
            phrases.extend(parser.feed(text[offset : offset + chunk_size]))
        phrases.extend(parser.finish())

        assert [
            phrase.model_dump(mode="json", by_alias=True) for phrase in phrases
        ] == expected


def test_v4_failure_code_values_are_frozen() -> None:
    assert {str(code) for code in SpeechFailureCode} == {
        "authentication",
        "realtimeNegotiation",
        "modelInvalid",
        "modelTimeout",
        "modelUnavailable",
        "speechInvalid",
        "speechTimeout",
        "speechUnavailable",
        "protocolSequence",
        "canvasRejected",
        "memoryUnavailable",
        "playbackUnavailable",
        "cancelled",
        "internal",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"segmentIndex": 12}, "less than 12"),
        ({"segment": {"speech": ""}}, "at least 1 character"),
        ({"segment": {"delivery": "dramatic"}}, "delivery"),
        ({"segment": {"canvasCues": [{"legacy": "typed"}]}}, "legacy canvas"),
    ],
)
def test_v4_rejects_malformed_or_legacy_canvas_speech(
    mutation: dict[str, object],
    message: str,
) -> None:
    payload = _segment_payload()
    for key, value in mutation.items():
        if key == "segment":
            segment = payload["segment"]
            assert isinstance(segment, dict)
            assert isinstance(value, dict)
            segment.update(value)
        else:
            payload[key] = value

    with pytest.raises(ValidationError, match=message):
        AVA_SERVER_EVENT_ADAPTER_V4.validate_python(payload)


def test_v4_segment_schema_is_not_a_legacy_canvas_segment() -> None:
    segment_type = SegmentReadyEventV4.model_fields["segment"].annotation
    assert getattr(segment_type, "__module__", "") == (
        "ava_backend.realtime.speech_contract"
    )
    assert getattr(segment_type, "__name__", "") == "SpeechPhrase"

    event = AVA_SERVER_EVENT_ADAPTER_V4.validate_python(_segment_payload())
    assert isinstance(event, SegmentReadyEventV4)
    assert type(event.segment) is SpeechPhrase
    assert not isinstance(event.segment, AvaTurnSegment)

    plan = AvaTurnPlan.model_validate(
        {
            "segments": [_segment_payload()["segment"]],
            "memoryProposal": None,
            "childTurnSummary": "Conversation turn.",
            "canvasDecision": {"complexity": "none", "specialistBrief": None},
        }
    )
    assert type(plan.segments[0]) is SpeechPhrase
    assert not isinstance(plan.segments[0], AvaTurnSegment)


def test_shared_speech_modules_do_not_import_v3_schema_modules() -> None:
    modules = (
        PRODUCTION_ROOT / "realtime" / "speech_contract.py",
        PRODUCTION_ROOT / "realtime" / "events_v4.py",
        PRODUCTION_ROOT / "realtime" / "delivery.py",
        PRODUCTION_ROOT / "realtime" / "turn_parser.py",
        PRODUCTION_ROOT / "realtime" / "caption_preview.py",
        PRODUCTION_ROOT / "providers" / "elevenlabs_realtime_speech.py",
    )
    forbidden = {
        "ava_backend.realtime.events",
        "ava_backend.realtime.plan",
        "ava_backend.realtime.model_plan",
        "ava_backend.canvas.protocol",
    }
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        relative_imports = {
            "." * node.level + (node.module or "")
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert imports.isdisjoint(forbidden), path
        assert relative_imports.isdisjoint(
            {".events", ".plan", ".model_plan", "..canvas.protocol"}
        ), path
