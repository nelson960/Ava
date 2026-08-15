from __future__ import annotations

import json
from collections.abc import AsyncIterator

from httpx import ASGITransport, AsyncClient

from ava_backend.app import create_app
from ava_backend.config import AvaEnvironment, Settings
from ava_backend.errors import ProviderTimeoutError
from ava_backend.protocol import (
    AvaPerformancePlan,
    AvaPerformanceSegment,
    AvaResponse,
    FractionCircleDiagram,
)
from ava_backend.providers.base import (
    ModelAvaResult,
    PerformanceModelResult,
    TimedSpeechChunk,
    TimedSpeechMetadata,
)


class _AvaModel:
    async def generate_response(self, **_arguments: object) -> ModelAvaResult:
        return ModelAvaResult(
            response=AvaResponse(
                speech="One quarter is one of four equal parts.",
                expression="encouraging",
                gesture="pointAtDashboard",
                diagram=FractionCircleDiagram(
                    title="One quarter",
                    total_parts=4,
                    highlighted_parts=1,
                ),
            ),
            provider_request_id="openai-request-123",
        )


class _TimeoutModel:
    async def generate_response(self, **_arguments: object) -> ModelAvaResult:
        raise ProviderTimeoutError()


class _PerformanceModel:
    async def generate_performance(
        self,
        **_arguments: object,
    ) -> PerformanceModelResult:
        return PerformanceModelResult(
            performance=AvaPerformancePlan(
                segments=[
                    AvaPerformanceSegment(
                        id="reply",
                        speech="Hello there.",
                        delivery="warm",
                        expression="warmSmile",
                        gaze="child",
                        gesture="none",
                        diagram_cue=None,
                    )
                ]
            )
        )


class _TimedSpeech:
    @property
    def metadata(self) -> TimedSpeechMetadata:
        return TimedSpeechMetadata(
            audio_format="pcm_s16le",
            media_type="audio/x-raw",
            sample_rate_hz=24_000,
            channels=1,
            bits_per_sample=16,
            model="test-model",
            voice_id="test-voice",
            voice_label="Test voice",
        )

    async def stream(
        self,
        *,
        text: str,
        previous_text: str | None = None,
        next_text: str | None = None,
    ) -> AsyncIterator[TimedSpeechChunk]:
        yield TimedSpeechChunk(audio=b"mp3", alignment=())


def _client(
    model: object = None,
    *,
    performance_model: object | None = None,
    timed_speech: object | None = None,
    max_bytes: int = 256 * 1024,
) -> AsyncClient:
    settings = Settings(
        environment=AvaEnvironment.TEST,
        request_max_bytes=max_bytes,
    )
    app = create_app(
        settings,
        ava_model=model or _AvaModel(),  # type: ignore[arg-type]
        ava_performance_model=performance_model,  # type: ignore[arg-type]
        ava_timed_speech=timed_speech,  # type: ignore[arg-type]
    )
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testclient",
    )


def _payload() -> dict[str, object]:
    return {
        "protocolVersion": 1,
        "request": {
            "childName": "Mia",
            "message": "What is one quarter?",
            "recentMessages": [
                {"role": "ava", "text": "What would you like to learn?"}
            ],
        },
    }


def _performance_payload() -> dict[str, object]:
    payload = _payload()
    payload["protocolVersion"] = 2
    return payload


async def test_lesson_endpoint_matches_flutter_contract() -> None:
    async with _client() as client:
        response = await client.post(
            "/v1/lesson-turns",
            headers={"X-Ava-Protocol-Version": "1"},
            json=_payload(),
        )

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    assert response.json() == {
        "protocolVersion": 1,
        "lesson": {
            "speech": "One quarter is one of four equal parts.",
            "domain": "general",
            "expression": "encouraging",
            "gesture": "pointAtDashboard",
            "diagram": {
                "kind": "fractionCircle",
                "title": "One quarter",
                "totalParts": 4,
                "highlightedParts": 1,
            },
        },
    }


async def test_endpoint_rejects_header_mismatch_and_unknown_fields() -> None:
    async with _client() as client:
        missing_header = await client.post("/v1/lesson-turns", json=_payload())
    assert missing_header.status_code == 409
    assert missing_header.json()["error"]["code"] == "protocol_mismatch"

    invalid = _payload()
    invalid["request"] = {**invalid["request"], "rawSvg": "<svg/>"}
    async with _client() as client:
        unknown_field = await client.post(
            "/v1/lesson-turns",
            headers={"X-Ava-Protocol-Version": "1"},
            json=invalid,
        )
    assert unknown_field.status_code == 422
    assert unknown_field.json()["error"]["code"] == "invalid_request"


async def test_provider_errors_are_traceable_without_provider_body() -> None:
    async with _client(_TimeoutModel()) as client:
        response = await client.post(
            "/v1/lesson-turns",
            headers={"X-Ava-Protocol-Version": "1"},
            json=_payload(),
        )

    assert response.status_code == 504
    assert response.json()["error"] == {
        "code": "provider_timeout",
        "message": "Ava's response provider timed out.",
        "requestId": response.headers["x-request-id"],
    }


async def test_content_length_limit_rejects_oversized_request_before_model() -> None:
    async with _client(max_bytes=16 * 1024) as client:
        response = await client.post(
            "/v1/lesson-turns",
            headers={
                "X-Ava-Protocol-Version": "1",
                "Content-Length": str(17 * 1024),
            },
            json=_payload(),
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_v2_performance_endpoint_streams_strict_ndjson() -> None:
    async with _client(
        performance_model=_PerformanceModel(),
        timed_speech=_TimedSpeech(),
    ) as client:
        response = await client.post(
            "/v2/performance-turns",
            headers={"X-Ava-Protocol-Version": "2"},
            json=_performance_payload(),
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["x-ava-protocol-version"] == "2"
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert [event["sequence"] for event in events] == list(range(len(events)))
    assert [event["event"] for event in events] == [
        "turnAccepted",
        "performancePlan",
        "audioMetadata",
        "segmentBoundary",
        "audioChunk",
        "turnCompleted",
    ]
    assert all(event["protocolVersion"] == 2 for event in events)
    assert events[0]["requestId"] == response.headers["x-request-id"]


async def test_v2_endpoint_fails_loudly_when_voice_is_not_configured() -> None:
    async with _client(performance_model=_PerformanceModel()) as client:
        response = await client.post(
            "/v2/performance-turns",
            headers={"X-Ava-Protocol-Version": "2"},
            json=_performance_payload(),
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"
    assert (
        "timed-speech provider is not configured" in response.json()["error"]["message"]
    )
