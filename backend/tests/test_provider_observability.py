from __future__ import annotations

from datetime import UTC, datetime

from ava_backend.realtime.provider_observability import ProviderConversationLedger


def _ledger(*, max_items: int = 16) -> ProviderConversationLedger:
    return ProviderConversationLedger(
        session_id="session-observability",
        provider_call_id="call-observability",
        max_items=max_items,
    )


def test_direct_response_items_are_owned_by_their_explicit_turn() -> None:
    ledger = _ledger()
    ledger.observe_provider_event(
        {"type": "input_audio_buffer.committed", "item_id": "input-a"},
        observed_sequence=1,
    )
    ledger.assign_input_owner(provider_item_id="input-a", local_turn_id="turn-a")
    ledger.observe_provider_event(
        {"type": "response.created", "response": {"id": "response-a"}},
        observed_sequence=2,
    )
    ledger.observe_provider_event(
        {
            "type": "response.output_item.added",
            "response_id": "response-a",
            "item": {"id": "assistant-a", "type": "message", "role": "assistant"},
        },
        observed_sequence=3,
    )
    ledger.assign_response_owner(
        provider_response_id="response-a", local_turn_id="turn-a"
    )

    assert ledger.item("input-a") is not None
    assert ledger.item("input-a").local_turn_id == "turn-a"  # type: ignore[union-attr]
    assistant = ledger.item("assistant-a")
    assert assistant is not None
    assert assistant.local_turn_id == "turn-a"
    assert assistant.provider_response_id == "response-a"
    assert assistant.logical_group_id == "turn:turn-a"
    assert assistant.item_type == "assistant_output"
    health = ledger.health_snapshot()
    assert health.observed_provider_item_count == 2
    assert health.provider_item_observation_complete is True
    # No explicit deletion was observed. Automatic provider truncation remains
    # opaque, so even a complete AVA observation stream is not an inventory.
    assert health.provider_inventory_authoritative is False
    assert health.observability_degraded is False


def test_function_output_and_continuation_share_the_turn_dependency_chain() -> None:
    ledger = _ledger()
    ledger.observe_provider_event(
        {"type": "input_audio_buffer.committed", "item_id": "input-tool"},
        observed_sequence=1,
    )
    ledger.assign_input_owner(provider_item_id="input-tool", local_turn_id="turn-tool")
    ledger.observe_provider_event(
        {"type": "response.created", "response": {"id": "response-tool"}},
        observed_sequence=2,
    )
    ledger.observe_provider_event(
        {
            "type": "response.output_item.added",
            "response_id": "response-tool",
            "item": {
                "id": "function-item",
                "type": "function_call",
                "call_id": "function-call",
            },
        },
        observed_sequence=3,
    )
    # The provider may ACK the output before the response consumer reaches the
    # response.created admission. Ownership is propagated after admission.
    ledger.observe_provider_event(
        {
            "type": "conversation.item.added",
            "item": {
                "id": "function-output",
                "type": "function_call_output",
                "call_id": "function-call",
            },
        },
        observed_sequence=4,
    )
    ledger.assign_response_owner(
        provider_response_id="response-tool", local_turn_id="turn-tool"
    )
    ledger.expect_continuation(
        local_turn_id="turn-tool", function_call_id="function-call"
    )
    ledger.observe_provider_event(
        {"type": "response.created", "response": {"id": "response-continuation"}},
        observed_sequence=5,
    )
    ledger.observe_provider_event(
        {
            "type": "response.output_item.added",
            "response_id": "response-continuation",
            "item": {
                "id": "assistant-continuation",
                "type": "message",
                "role": "assistant",
            },
        },
        observed_sequence=6,
    )
    ledger.assign_response_owner(
        provider_response_id="response-continuation",
        local_turn_id="turn-tool",
    )

    chain = {
        item.provider_item_id: item
        for item in ledger.items
        if item.local_turn_id == "turn-tool"
    }
    assert set(chain) == {
        "input-tool",
        "function-item",
        "function-output",
        "assistant-continuation",
    }
    assert {item.logical_group_id for item in chain.values()} == {"turn:turn-tool"}
    assert chain["function-output"].dependency_call_id == "function-call"
    assert chain["assistant-continuation"].dependency_call_id == "function-call"
    continuation = ledger.response("response-continuation")
    assert continuation is not None
    assert continuation.continuation_of_call_id == "function-call"
    assert ledger.health_snapshot().continuation_count == 1


def test_consecutive_and_late_items_never_cross_turn_ownership() -> None:
    ledger = _ledger()
    for index, turn_id in enumerate(("turn-a", "turn-b"), start=1):
        response_id = f"response-{index}"
        ledger.observe_provider_event(
            {"type": "response.created", "response": {"id": response_id}},
            observed_sequence=index * 10,
        )
        ledger.assign_response_owner(
            provider_response_id=response_id,
            local_turn_id=turn_id,
        )

    ledger.observe_provider_event(
        {
            "type": "response.output_item.added",
            "response_id": "response-1",
            "item": {
                "id": "late-assistant-a",
                "type": "message",
                "role": "assistant",
            },
        },
        observed_sequence=30,
    )
    ledger.observe_provider_event(
        {
            "type": "response.output_item.added",
            "response_id": "response-2",
            "item": {
                "id": "assistant-b",
                "type": "message",
                "role": "assistant",
            },
        },
        observed_sequence=31,
    )

    assert ledger.item("late-assistant-a").local_turn_id == "turn-a"  # type: ignore[union-attr]
    assert ledger.item("assistant-b").local_turn_id == "turn-b"  # type: ignore[union-attr]


def test_usage_is_exact_and_missing_cache_detail_stays_unknown() -> None:
    ledger = _ledger()
    ledger.observe_usage(
        provider_response_id="response-cached",
        local_turn_id="turn-cached",
        input_tokens=13,
        cached_input_tokens=5,
        output_tokens=7,
        total_tokens=20,
    )
    cached = ledger.health_snapshot()
    assert cached.latest_usage is not None
    assert cached.latest_usage.input_tokens == 13
    assert cached.latest_usage.cached_input_tokens == 5
    assert cached.latest_usage.uncached_input_tokens == 8
    assert cached.latest_usage.output_tokens == 7
    assert cached.latest_usage.total_tokens == 20
    assert cached.latest_usage.provider_response_id == "response-cached"
    assert cached.latest_usage.local_turn_id == "turn-cached"
    assert cached.cumulative_cached_input_tokens == 5

    ledger.observe_usage(
        provider_response_id="response-no-cache-detail",
        local_turn_id="turn-no-cache-detail",
        input_tokens=11,
        cached_input_tokens=None,
        output_tokens=3,
        total_tokens=14,
    )
    missing = ledger.health_snapshot()
    assert missing.latest_usage is not None
    assert missing.latest_usage.cached_input_tokens is None
    assert missing.latest_usage.uncached_input_tokens is None
    assert missing.cumulative_input_tokens == 24
    assert missing.cumulative_output_tokens == 10
    assert missing.cumulative_total_tokens == 34
    assert missing.cumulative_cached_input_tokens is None
    assert missing.cumulative_uncached_input_tokens is None


def test_input_transcription_usage_is_exact_and_follows_explicit_item_owner() -> None:
    ledger = _ledger()
    ledger.observe_provider_event(
        {"type": "input_audio_buffer.committed", "item_id": "input-audio"},
        observed_sequence=1,
    )
    ledger.observe_provider_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "input-audio",
            "transcript": "not retained by observability",
            "usage": {
                "type": "tokens",
                "input_tokens": 13,
                "input_token_details": {
                    "text_tokens": 0,
                    "audio_tokens": 13,
                },
                "output_tokens": 9,
                "total_tokens": 22,
            },
        },
        observed_sequence=2,
    )
    ledger.assign_input_owner(
        provider_item_id="input-audio",
        local_turn_id="turn-audio",
    )

    item = ledger.item("input-audio")
    assert item is not None
    assert item.input_transcription_usage is not None
    usage = item.input_transcription_usage
    assert usage.local_turn_id == "turn-audio"
    assert usage.observed_sequence == 2
    assert usage.usage_type == "tokens"
    assert usage.input_tokens == 13
    assert usage.input_text_tokens == 0
    assert usage.input_audio_tokens == 13
    assert usage.output_tokens == 9
    assert usage.total_tokens == 22
    assert usage.duration_seconds is None
    assert ledger.health_snapshot().latest_input_transcription_usage == usage

    ledger.observe_provider_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "input-duration-billed",
            "usage": {"type": "duration", "seconds": 1.25},
        },
        observed_sequence=3,
    )
    duration_usage = ledger.health_snapshot().latest_input_transcription_usage
    assert duration_usage is not None
    assert duration_usage.provider_item_id == "input-duration-billed"
    assert duration_usage.local_turn_id is None
    assert duration_usage.usage_type == "duration"
    assert duration_usage.duration_seconds == 1.25
    assert duration_usage.input_tokens is None
    assert duration_usage.output_tokens is None
    assert duration_usage.total_tokens is None


def test_input_owner_admission_before_item_observation_is_staged() -> None:
    ledger = _ledger()

    ledger.assign_input_owner(
        provider_item_id="input-owner-first",
        local_turn_id="turn-owner-first",
    )

    assert ledger.item("input-owner-first") is None
    assert ledger.health_snapshot().provider_item_observation_complete is True

    ledger.observe_provider_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "input-owner-first",
            "usage": {
                "type": "tokens",
                "input_tokens": 7,
                "input_token_details": {"text_tokens": 0, "audio_tokens": 7},
                "output_tokens": 2,
                "total_tokens": 9,
            },
        },
        observed_sequence=1,
    )
    ledger.observe_provider_event(
        {
            "type": "input_audio_buffer.committed",
            "item_id": "input-owner-first",
        },
        observed_sequence=2,
    )

    item = ledger.item("input-owner-first")
    assert item is not None
    assert item.local_turn_id == "turn-owner-first"
    assert item.logical_group_id == "turn:turn-owner-first"
    assert item.input_transcription_usage is not None
    assert item.input_transcription_usage.local_turn_id == "turn-owner-first"
    assert item.input_transcription_usage.observed_sequence == 1
    latest_usage = ledger.health_snapshot().latest_input_transcription_usage
    assert latest_usage is not None
    assert latest_usage.local_turn_id == "turn-owner-first"
    assert ledger.health_snapshot().provider_item_observation_complete is True


def test_conflicting_pending_input_ownership_remains_incomplete() -> None:
    ledger = _ledger()
    ledger.assign_input_owner(
        provider_item_id="input-conflict",
        local_turn_id="turn-original",
    )

    ledger.assign_input_owner(
        provider_item_id="input-conflict",
        local_turn_id="turn-conflicting",
    )
    ledger.observe_provider_event(
        {"type": "input_audio_buffer.committed", "item_id": "input-conflict"},
        observed_sequence=1,
    )

    item = ledger.item("input-conflict")
    assert item is not None
    assert item.local_turn_id == "turn-original"
    assert item.logical_group_id == "turn:turn-original"
    assert ledger.health_snapshot().provider_item_observation_complete is False


def test_pending_input_ownership_is_bounded_and_saturation_is_visible() -> None:
    ledger = ProviderConversationLedger(
        session_id="session-pending-input-bound",
        provider_call_id="call-pending-input-bound",
        max_pending_item_admissions=1,
    )
    ledger.assign_input_owner(
        provider_item_id="input-pending-1",
        local_turn_id="turn-pending-1",
    )
    ledger.assign_input_owner(
        provider_item_id="input-pending-2",
        local_turn_id="turn-pending-2",
    )

    ledger.observe_provider_event(
        {"type": "input_audio_buffer.committed", "item_id": "input-pending-1"},
        observed_sequence=1,
    )
    ledger.observe_provider_event(
        {"type": "input_audio_buffer.committed", "item_id": "input-pending-2"},
        observed_sequence=2,
    )

    first = ledger.item("input-pending-1")
    second = ledger.item("input-pending-2")
    assert first is not None and first.local_turn_id == "turn-pending-1"
    assert second is not None and second.local_turn_id is None
    assert ledger.health_snapshot().provider_item_observation_complete is False


def test_rate_limit_snapshot_tracks_tpm_rpm_unknown_and_replacement() -> None:
    ledger = _ledger()
    ledger.observe_provider_event(
        {
            "type": "rate_limits.updated",
            "rate_limits": [
                {
                    "name": "requests",
                    "limit": 120,
                    "remaining": 119,
                    "reset_seconds": 0.5,
                },
                {
                    "name": "tokens",
                    "limit": 40_000,
                    "remaining": 39_200,
                    "reset_seconds": 1.25,
                },
                {
                    "name": "future_capacity_kind",
                    "limit": 7,
                    "remaining": 6,
                    "reset_seconds": 2,
                    "future_field": "ignored-without-failure",
                },
            ],
        },
        observed_sequence=7,
    )
    first = ledger.health_snapshot()
    assert first.rpm is not None
    assert (first.rpm.name, first.rpm.limit, first.rpm.remaining) == (
        "requests",
        120,
        119,
    )
    assert first.tpm is not None
    assert (first.tpm.limit, first.tpm.remaining, first.tpm.reset_seconds) == (
        40_000,
        39_200,
        1.25,
    )
    assert [entry.name for entry in first.rate_limits] == [
        "requests",
        "tokens",
        "future_capacity_kind",
    ]

    ledger.observe_provider_event(
        {
            "type": "rate_limits.updated",
            "rate_limits": [
                {
                    "name": "tokens",
                    "limit": 40_000,
                    "remaining": 20_000,
                    "reset_seconds": 10,
                }
            ],
        },
        observed_sequence=8,
    )
    replaced = ledger.health_snapshot()
    assert len(replaced.rate_limits) == 3
    assert replaced.tpm is not None and replaced.tpm.remaining == 20_000
    assert replaced.rpm is not None and replaced.rpm.remaining == 119


def test_ledger_bound_is_visible_and_close_clears_session_ownership() -> None:
    ledger = _ledger(max_items=2)
    for sequence in range(1, 4):
        ledger.observe_provider_event(
            {
                "type": "input_audio_buffer.committed",
                "item_id": f"input-{sequence}",
            },
            observed_sequence=sequence,
        )

    saturated = ledger.health_snapshot()
    assert saturated.observed_provider_item_count == 2
    assert saturated.provider_item_observation_complete is False
    assert saturated.provider_inventory_authoritative is False
    ledger.close()
    closed = ledger.health_snapshot()
    assert closed.closed is True
    assert closed.observed_provider_item_count == 0
    assert ledger.items == ()
    assert ledger.responses == ()


def test_explicit_provider_item_deletion_updates_the_observed_item_snapshot() -> None:
    ledger = _ledger()
    ledger.observe_provider_event(
        {"type": "input_audio_buffer.committed", "item_id": "input-present"},
        observed_sequence=1,
    )
    assert ledger.health_snapshot().observed_provider_item_count == 1

    ledger.observe_provider_event(
        {"type": "conversation.item.deleted", "item_id": "input-present"},
        observed_sequence=2,
    )

    assert ledger.item("input-present") is None
    assert ledger.health_snapshot().observed_provider_item_count == 0


def test_response_continuation_and_rate_limit_collections_are_bounded() -> None:
    ledger = ProviderConversationLedger(
        session_id="session-bounds",
        provider_call_id="call-bounds",
        max_items=1,
        max_responses=1,
        max_pending_continuations=1,
        max_pending_item_admissions=1,
        max_rate_limit_entries=1,
    )
    ledger.observe_provider_event(
        {"type": "response.created", "response": {"id": "response-1"}},
        observed_sequence=1,
    )
    ledger.observe_provider_event(
        {"type": "response.created", "response": {"id": "response-2"}},
        observed_sequence=2,
    )
    ledger.expect_continuation(local_turn_id="turn-1", function_call_id="call-1")
    ledger.expect_continuation(local_turn_id="turn-2", function_call_id="call-2")
    ledger.expect_item_owner(provider_item_id="outbound-1", local_turn_id="turn-1")
    ledger.expect_item_owner(provider_item_id="outbound-2", local_turn_id="turn-2")
    ledger.observe_provider_event(
        {
            "type": "conversation.item.added",
            "item": {"id": "outbound-1", "type": "message", "role": "user"},
        },
        observed_sequence=3,
    )
    ledger.observe_provider_event(
        {
            "type": "conversation.item.added",
            "item": {"id": "outbound-2", "type": "message", "role": "user"},
        },
        observed_sequence=4,
    )
    ledger.observe_provider_event(
        {
            "type": "rate_limits.updated",
            "rate_limits": [
                {"name": "tokens", "limit": 10, "remaining": 9, "reset_seconds": 1},
                {
                    "name": "requests",
                    "limit": 10,
                    "remaining": 9,
                    "reset_seconds": 1,
                },
            ],
        },
        observed_sequence=5,
    )

    health = ledger.health_snapshot()
    assert len(ledger.responses) == 1
    assert len(ledger.items) == 1
    assert ledger.items[0].local_turn_id == "turn-1"
    assert health.provider_item_observation_complete is False
    assert health.response_count_complete is False
    assert len(health.rate_limits) == 1
    assert health.rate_limit_snapshot_truncated is True

    ledger.assign_response_owner(
        provider_response_id="response-1", local_turn_id="turn-1"
    )
    assert ledger.health_snapshot().continuation_count == 1
    ledger.settle_turn(local_turn_id="turn-2")


def test_new_session_health_starts_with_fresh_counters() -> None:
    first = _ledger()
    first.observe_usage(
        provider_response_id="response-old",
        local_turn_id="turn-old",
        input_tokens=5,
        cached_input_tokens=2,
        output_tokens=1,
        total_tokens=6,
    )
    first.close()

    second = ProviderConversationLedger(
        session_id="session-new",
        provider_call_id="call-new",
        now=lambda: datetime(2026, 9, 7, tzinfo=UTC),
        monotonic_clock=lambda: 10.0,
    )
    health = second.health_snapshot()
    assert health.session_id == "session-new"
    assert health.created_at == datetime(2026, 9, 7, tzinfo=UTC)
    assert health.uptime_seconds == 0
    assert health.response_count == 0
    assert health.observed_provider_item_count == 0
    assert health.cumulative_total_tokens == 0
    assert health.latest_usage is None
