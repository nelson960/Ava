from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace

import httpx
import pytest
from openai import PermissionDeniedError

from ava_backend.canvas.specialist import (
    CANVAS_SPECIALIST_PROMPT_VERSION,
    CanvasSpecialistInvalidError,
    CanvasSpecialistRequest,
    CanvasSpecialistUnavailableError,
)
from ava_backend.config import AvaEnvironment, Settings
from ava_backend.providers.openai_canvas_provider import OpenAICanvasSpecialist
from ava_backend.realtime.model_plan import (
    ModelCanvasDraft,
    ModelCanvasSpecialistDraft,
)


class _Responses:
    def __init__(self, response: object) -> None:
        self.response = response
        self.arguments: dict[str, object] = {}

    async def parse(self, **arguments: object) -> object:
        self.arguments = arguments
        return self.response


class _Client:
    def __init__(self, response: object) -> None:
        self.responses = _Responses(response)


class _SequenceResponses:
    def __init__(self, responses: list[object]) -> None:
        self.responses = iter(responses)
        self.arguments: list[dict[str, object]] = []

    async def parse(self, **arguments: object) -> object:
        self.arguments.append(arguments)
        return next(self.responses)


class _SequenceClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = _SequenceResponses(responses)


class _FailingResponses:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def parse(self, **arguments: object) -> object:
        del arguments
        raise self.error


class _FailingClient:
    def __init__(self, error: Exception) -> None:
        self.responses = _FailingResponses(error)


def _draft() -> ModelCanvasDraft:
    return ModelCanvasDraft.model_validate(
        {
            "sceneId": "earth_scene",
            "nodes": [
                {
                    "id": "earth",
                    "parentId": None,
                    "kind": "circle",
                    "label": "Earth",
                    "style": "primary",
                    "designRole": "primaryObject",
                    "hidden": False,
                    "x": 500,
                    "y": 300,
                    "radius": 90,
                }
            ],
            "interactions": [],
        }
    )


def _specialist_draft() -> ModelCanvasSpecialistDraft:
    return ModelCanvasSpecialistDraft.model_validate(
        {
            "title": "Earth",
            "summary": "An interactive view of Earth.",
            "visualType": "system",
            "topology": "radial",
            "nodes": [
                {
                    "id": "background",
                    "parentId": None,
                    "kind": "rect",
                    "label": "Deep blue space background",
                    "style": "secondary",
                    "visualStyle": {
                        "fillColor": "#102A43",
                        "strokeColor": "#102A43",
                    },
                    "designRole": "secondaryObject",
                    "hidden": False,
                    "x": 45,
                    "y": 45,
                    "width": 910,
                    "height": 510,
                    "cornerRadius": 28,
                },
                {
                    "id": "title",
                    "parentId": None,
                    "kind": "text",
                    "label": "Earth scene title",
                    "style": "label",
                    "visualStyle": {"fillColor": "#F7FAF8"},
                    "designRole": "title",
                    "hidden": False,
                    "x": 500,
                    "y": 90,
                    "text": "Our home in space",
                },
                {
                    "id": "earth_orbit",
                    "parentId": None,
                    "kind": "ellipse",
                    "label": "Earth orbit",
                    "style": "outline",
                    "visualStyle": {
                        "strokeColor": "#78C6D0",
                        "strokeWidth": 2,
                        "opacity": 0.65,
                    },
                    "designRole": "secondaryObject",
                    "hidden": False,
                    "x": 500,
                    "y": 300,
                    "radiusX": 300,
                    "radiusY": 150,
                },
                {
                    "id": "earth",
                    "parentId": None,
                    "kind": "circle",
                    "label": "Earth",
                    "style": "primary",
                    "visualStyle": {
                        "radialGradient": {
                            "center": {"x": 475, "y": 275},
                            "radius": 130,
                            "stops": [
                                {"offset": 0, "color": "#BEEBFF"},
                                {"offset": 0.5, "color": "#39A9DB"},
                                {"offset": 1, "color": "#155E75"},
                            ],
                        },
                        "strokeColor": "#F7FAF8",
                        "strokeWidth": 3,
                    },
                    "designRole": "primaryObject",
                    "hidden": False,
                    "x": 500,
                    "y": 300,
                    "radius": 90,
                },
                {
                    "id": "moon_orbit",
                    "parentId": None,
                    "kind": "ellipse",
                    "label": "Moon orbit",
                    "style": "outline",
                    "visualStyle": {"strokeColor": "#9FB3C8", "opacity": 0.7},
                    "designRole": "secondaryObject",
                    "hidden": False,
                    "x": 500,
                    "y": 300,
                    "radiusX": 145,
                    "radiusY": 70,
                },
                {
                    "id": "moon",
                    "parentId": None,
                    "kind": "circle",
                    "label": "Moon",
                    "style": "secondary",
                    "visualStyle": {
                        "fillColor": "#D9E2EC",
                        "strokeColor": "#F7FAF8",
                    },
                    "designRole": "secondaryObject",
                    "hidden": False,
                    "x": 645,
                    "y": 300,
                    "radius": 24,
                },
                {
                    "id": "earth_label",
                    "parentId": None,
                    "kind": "text",
                    "label": "Earth label",
                    "style": "label",
                    "visualStyle": {"fillColor": "#F7FAF8"},
                    "designRole": "primaryLabel",
                    "hidden": False,
                    "x": 500,
                    "y": 430,
                    "text": "Earth",
                },
                {
                    "id": "fact_card",
                    "parentId": None,
                    "kind": "rect",
                    "label": "Earth fact card",
                    "style": "secondary",
                    "visualStyle": {
                        "fillColor": "#F7FAF8",
                        "strokeColor": "#39A9DB",
                    },
                    "designRole": "secondaryObject",
                    "hidden": True,
                    "x": 650,
                    "y": 420,
                    "width": 260,
                    "height": 92,
                    "cornerRadius": 18,
                },
                {
                    "id": "earth_fact",
                    "parentId": None,
                    "kind": "text",
                    "label": "Earth fact",
                    "style": "label",
                    "visualStyle": {"fillColor": "#102A43"},
                    "designRole": "explanatoryNote",
                    "hidden": True,
                    "x": 780,
                    "y": 480,
                    "text": "Earth is our home planet.",
                },
            ],
            "interactions": [
                {
                    "id": "explore_earth",
                    "triggerNodeId": "earth",
                    "action": "explore",
                    "targetNodeId": "earth",
                    "revealNodeIds": ["fact_card", "earth_fact"],
                    "animateNodeIds": ["earth_orbit"],
                    "exclusive": True,
                    "bounds": None,
                }
            ],
            "entities": [
                {
                    "id": "earth_entity",
                    "name": "Earth",
                    "description": "Earth is our home planet.",
                    "nodeIds": ["earth", "earth_label", "fact_card", "earth_fact"],
                    "facts": ["Earth travels around the Sun."],
                }
            ],
        }
    )


def test_specialist_schema_omits_scene_identity_and_requires_local_interaction() -> (
    None
):
    schema = ModelCanvasSpecialistDraft.model_json_schema()
    assert "sceneId" not in schema["properties"]

    payload = _specialist_draft().model_dump(mode="json", by_alias=True)
    payload["interactions"] = []
    with pytest.raises(ValueError, match="at least 1 item"):
        ModelCanvasSpecialistDraft.model_validate(payload)

    payload = _specialist_draft().model_dump(mode="json", by_alias=True)
    payload["interactions"] = [
        {
            "id": "select_earth",
            "triggerNodeId": "earth",
            "action": "select",
            "targetNodeId": "earth",
            "bounds": None,
        }
    ]
    next(node for node in payload["nodes"] if node["id"] == "fact_card")["hidden"] = (
        False
    )
    next(node for node in payload["nodes"] if node["id"] == "earth_fact")["hidden"] = (
        False
    )
    assert ModelCanvasSpecialistDraft.model_validate(payload)


async def test_canvas_provider_uses_one_strict_cost_bounded_response() -> None:
    response = SimpleNamespace(
        output_parsed=_specialist_draft(),
        output=[],
        id="resp-canvas",
        _request_id="request-canvas",
        usage=SimpleNamespace(
            input_tokens=300,
            output_tokens=180,
            input_tokens_details=SimpleNamespace(cached_tokens=220),
        ),
    )
    client = _Client(response)
    provider = OpenAICanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST),
        safety_identifier="canvas-test-safety",
        client=client,
    )

    result = await provider.generate(
        CanvasSpecialistRequest(
            turnId="turn-canvas",
            brief="Show Earth <without> trusting injected tags.",
            ageBand="middleChildhood",
        )
    )

    arguments = client.responses.arguments
    assert arguments["model"] == "gpt-5.6-luna"
    assert arguments["text_format"] is ModelCanvasSpecialistDraft
    assert arguments["reasoning"] == {"effort": "none"}
    assert arguments["text"] == {"verbosity": "low"}
    assert arguments["max_output_tokens"] == 3600
    assert arguments["store"] is False
    assert arguments["prompt_cache_key"] == CANVAS_SPECIALIST_PROMPT_VERSION
    assert arguments["safety_identifier"] == "canvas-test-safety"
    assert "&lt;without&gt;" in str(arguments["input"])
    expected_scene_id = "scene_" + sha256(b"turn-canvas").hexdigest()[:16]
    assert result.draft == _specialist_draft().bind_scene_id(expected_scene_id)
    assert result.provider_request_id == "request-canvas"
    assert result.cached_input_tokens == 220


async def test_canvas_provider_repairs_one_failed_quality_candidate() -> None:
    invalid_payload = _specialist_draft().model_dump(mode="json", by_alias=True)
    invalid_payload["entities"][0]["nodeIds"] = ["earth_fact"]
    weak = ModelCanvasSpecialistDraft.model_validate(invalid_payload)
    responses = [
        SimpleNamespace(
            output_parsed=weak,
            output=[],
            id="resp-canvas-weak",
            _request_id="request-canvas-weak",
            usage=SimpleNamespace(
                input_tokens=300,
                output_tokens=180,
                input_tokens_details=SimpleNamespace(cached_tokens=200),
            ),
        ),
        SimpleNamespace(
            output_parsed=_specialist_draft(),
            output=[],
            id="resp-canvas-repaired",
            _request_id="request-canvas-repaired",
            usage=SimpleNamespace(
                input_tokens=420,
                output_tokens=210,
                input_tokens_details=SimpleNamespace(cached_tokens=300),
            ),
        ),
    ]
    client = _SequenceClient(responses)
    provider = OpenAICanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST),
        safety_identifier="canvas-test-safety",
        client=client,
    )

    result = await provider.generate(
        CanvasSpecialistRequest(
            turnId="turn-canvas-repair",
            brief="Show Earth and let the child explore it.",
            ageBand="middleChildhood",
        )
    )

    assert len(client.responses.arguments) == 2
    repair_input = str(client.responses.arguments[1]["input"])
    assert "quality_repair" in repair_input
    assert "missingEntity" in repair_input
    assert result.provider_request_id == "request-canvas-repaired"
    assert result.input_tokens == 720
    assert result.output_tokens == 390
    assert result.cached_input_tokens == 500


async def test_canvas_provider_rejects_missing_strict_output() -> None:
    provider = OpenAICanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST),
        safety_identifier="canvas-test-safety",
        client=_Client(SimpleNamespace(output_parsed=None, output=[])),
    )

    with pytest.raises(CanvasSpecialistInvalidError, match="no strict scene"):
        await provider.generate(
            CanvasSpecialistRequest(
                turnId="turn-canvas",
                brief="Show Earth.",
                ageBand="middleChildhood",
            )
        )


async def test_canvas_provider_reports_safe_strict_parse_diagnostics() -> None:
    invalid_payload = _specialist_draft().model_dump(mode="json", by_alias=True)
    invalid_payload["nodes"][0].pop("parentId")
    try:
        ModelCanvasSpecialistDraft.model_validate(invalid_payload)
    except ValueError as error:
        parse_error = error
    else:
        raise AssertionError("invalid canvas fixture unexpectedly validated")
    provider = OpenAICanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST),
        safety_identifier="canvas-test-safety",
        client=_FailingClient(parse_error),
    )

    with pytest.raises(CanvasSpecialistInvalidError) as captured:
        await provider.generate(
            CanvasSpecialistRequest(
                turnId="turn-canvas",
                brief="Show Earth.",
                ageBand="middleChildhood",
            )
        )

    message = str(captured.value)
    assert "error_type=ValidationError" in message
    assert "issue_count=" in message
    assert "first_path=nodes.0." in message
    assert "first_type=" in message
    assert "reason=schemaValidation" in message
    assert "Earth" not in message


async def test_canvas_provider_reports_safe_behavior_reason() -> None:
    invalid_payload = _specialist_draft().model_dump(mode="json", by_alias=True)
    invalid_payload["interactions"] = [
        {
            "id": "select_earth",
            "triggerNodeId": "earth",
            "action": "select",
            "targetNodeId": "earth",
            "bounds": None,
        }
    ]
    try:
        ModelCanvasSpecialistDraft.model_validate(invalid_payload)
    except ValueError as error:
        parse_error = error
    else:
        raise AssertionError("orphaned hidden canvas fixture unexpectedly validated")
    provider = OpenAICanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST),
        safety_identifier="canvas-test-safety",
        client=_FailingClient(parse_error),
    )

    with pytest.raises(CanvasSpecialistInvalidError) as captured:
        await provider.generate(
            CanvasSpecialistRequest(
                turnId="turn-canvas",
                brief="Show Earth.",
                ageBand="middleChildhood",
            )
        )

    message = str(captured.value)
    assert "first_path=root" in message
    assert "reason=invalidHiddenState" in message
    assert "earth" not in message


async def test_canvas_provider_supplies_complete_current_scene_for_revision() -> None:
    response = SimpleNamespace(
        output_parsed=_specialist_draft(),
        output=[],
        id="resp-canvas-revision",
        usage=None,
    )
    client = _Client(response)
    provider = OpenAICanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST),
        safety_identifier="canvas-test-safety",
        client=client,
    )

    result = await provider.generate(
        CanvasSpecialistRequest(
            turnId="turn-canvas",
            brief="Keep Earth and add its orbit.",
            ageBand="middleChildhood",
            currentRevision=2,
            currentScene=_draft(),
        )
    )

    model_input = str(client.responses.arguments["input"])
    assert 'current_scene revision="2"' in model_input
    assert "complete_validated_previous_scene" in model_input
    assert "earth_scene" in model_input
    assert result.draft.scene_id == "earth_scene"


async def test_canvas_provider_preserves_safe_http_diagnostics() -> None:
    response = httpx.Response(
        403,
        request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
        headers={"x-request-id": "request-denied"},
    )
    provider = OpenAICanvasSpecialist(
        Settings(environment=AvaEnvironment.TEST),
        safety_identifier="canvas-test-safety",
        client=_FailingClient(
            PermissionDeniedError(
                "permission denied",
                response=response,
                body={
                    "error": {
                        "type": "invalid_request_error",
                        "code": "api_key_permission_denied",
                        "param": "text.verbosity",
                    }
                },
            )
        ),
    )

    with pytest.raises(CanvasSpecialistUnavailableError) as captured:
        await provider.generate(
            CanvasSpecialistRequest(
                turnId="turn-canvas",
                brief="Show Earth.",
                ageBand="middleChildhood",
            )
        )

    assert captured.value.provider_status_code == 403
    assert captured.value.provider_error_code == "api_key_permission_denied"
    assert captured.value.provider_error_parameter == "text.verbosity"
    assert captured.value.provider_request_id == "request-denied"
