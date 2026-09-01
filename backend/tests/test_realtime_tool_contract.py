from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from ava_backend.realtime.tool_contract import (
    GetCurrentTimeRequest,
    RealtimeGetCurrentTimeToolCall,
    RealtimeSearchMemoryToolCall,
    SearchMemoryRequest,
    SearchMemoryToolOutputV4,
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
