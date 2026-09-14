from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from ava_backend.realtime.tool_contract import (
    InteractiveCanvasCancellationRequest,
    InteractiveCanvasRequest,
    GetCurrentTimeRequest,
    ManageCanvasPresentationRequest,
    RealtimeGetCurrentTimeToolCall,
    RealtimeSearchMemoryToolCall,
    SearchMemoryRequest,
    SearchMemoryToolOutputV4,
)


@pytest.mark.parametrize(
    ("request_type", "payload"),
    [
        (
            InteractiveCanvasRequest,
            {
                "operation": "new",
                "brief": "Show the water cycle.",
                "interactionMode": "none",
                "continuousControl": "none",
            },
        ),
        (InteractiveCanvasCancellationRequest, {}),
        (ManageCanvasPresentationRequest, {"action": "hide"}),
    ],
)
def test_canvas_completion_is_required_and_exact(
    request_type: type[object],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        request_type.model_validate(payload)  # type: ignore[attr-defined]

    for completion in ("requestComplete", "needsResultReasoning"):
        request = request_type.model_validate(  # type: ignore[attr-defined]
            {**payload, "completion": completion}
        )
        assert request.completion == completion

    with pytest.raises(ValidationError):
        request_type.model_validate(  # type: ignore[attr-defined]
            {**payload, "completion": "silentIfConvenient"}
        )

    with pytest.raises(ValidationError):
        request_type.model_validate(  # type: ignore[attr-defined]
            {**payload, "responseMode": "actionOnly"}
        )


def test_search_memory_request_is_bounded_and_uses_local_dates() -> None:
    request = SearchMemoryRequest.model_validate(
        {
            "query": "When did we discuss Saturn?",
            "fromDate": "2026-03-01",
            "toDate": "2026-03-08",
        }
    )

    assert request.from_date == date(2026, 3, 1)
    assert request.to_date == date(2026, 3, 8)
    call = RealtimeSearchMemoryToolCall(call_id="call_memory", request=request)
    assert call.request.query.endswith("Saturn?")
    with pytest.raises(ValidationError):
        SearchMemoryRequest.model_validate({"query": "x" * 501})
    with pytest.raises(ValidationError):
        SearchMemoryRequest.model_validate({"query": "Saturn", "limit": 5})


def test_get_current_time_has_no_model_owned_arguments() -> None:
    request = GetCurrentTimeRequest.model_validate({})
    call = RealtimeGetCurrentTimeToolCall(call_id="call_time", request=request)
    assert call.request == request
    with pytest.raises(ValidationError):
        GetCurrentTimeRequest.model_validate({"timezoneId": "UTC"})


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "hide"},
        {"action": "showCurrent"},
        {"action": "showNext"},
        {"action": "showPrevious"},
        {"action": "showFirst"},
        {"action": "showLast"},
        {"action": "showPosition", "position": 4},
        {"action": "showTitle", "title": "Volcano Layers"},
    ],
)
def test_canvas_presentation_uses_one_semantic_action(
    payload: dict[str, object],
) -> None:
    request = ManageCanvasPresentationRequest.model_validate(
        {**payload, "completion": "requestComplete"}
    )
    assert request.presentation_selector is not None or request.action == "hide"


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "hide", "position": 1},
        {"action": "showCurrent", "title": "Volcano Layers"},
        {"action": "showPosition"},
        {"action": "showPosition", "position": 1, "title": "Volcano Layers"},
        {"action": "showTitle"},
        {"action": "showTitle", "title": "Volcano Layers", "position": 1},
        {"operation": "show", "selector": "title", "title": "Volcano Layers"},
    ],
)
def test_canvas_presentation_rejects_redundant_or_invalid_shapes(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ManageCanvasPresentationRequest.model_validate(
            {**payload, "completion": "requestComplete"}
        )


def test_search_memory_output_is_bounded_and_status_coherent() -> None:
    no_matches = SearchMemoryToolOutputV4.model_validate(
        {"status": "noMatches", "references": []}
    )
    assert no_matches.references == []
    with pytest.raises(ValidationError):
        SearchMemoryToolOutputV4.model_validate(
            {
                "status": "available",
                "references": [],
            }
        )
    with pytest.raises(ValidationError):
        SearchMemoryToolOutputV4.model_validate(
            {
                "status": "noMatches",
                "references": [
                    {
                        "sourceKind": "episode",
                        "startedAt": None,
                        "endedAt": None,
                        "timezoneIds": [],
                        "text": "unexpected",
                    }
                ],
            }
        )
