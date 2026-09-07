from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ava_backend.realtime.resource_governor import (
    RateLimitWindow,
    ResourceAdmissionAction,
    ResourceAdmissionReason,
    ResourceGovernor,
    ResourceGovernorState,
)
from ava_backend.realtime.tool_contract import (
    MAX_PRIMARY_TOOL_OUTPUT_UTF8_BYTES,
    MAX_SEARCH_MEMORY_TOOL_OUTPUT_UTF8_BYTES,
    MAX_TERMINAL_TOOL_OUTPUT_UTF8_BYTES,
)


class _ManualClock:
    def __init__(self) -> None:
        self.monotonic_value = 0.0
        self.wall_value = datetime(2026, 9, 7, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.monotonic_value

    def wall(self) -> datetime:
        return self.wall_value

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall_value += timedelta(seconds=seconds)


def _governor(
    clock: _ManualClock | None = None,
    *,
    max_attempts: int = 2,
) -> ResourceGovernor:
    current = clock or _ManualClock()
    return ResourceGovernor(
        max_recovery_attempts=max_attempts,
        max_recovery_seconds=30,
        fallback_wait_seconds=1,
        monotonic_clock=current.monotonic,
        wall_clock=current.wall,
    )


def _limit(
    clock: _ManualClock,
    *,
    remaining: int,
    limit: int = 50_000,
    reset_seconds: float = 2,
) -> RateLimitWindow:
    return RateLimitWindow(
        limit=limit,
        remaining=remaining,
        reset_seconds=reset_seconds,
        observed_at=clock.wall(),
    )


def test_sufficient_tpm_and_rpm_allow_one_owned_reservation() -> None:
    clock = _ManualClock()
    governor = _governor(clock)
    governor.observe_actual_usage(
        turn_id="turn-calibration", input_tokens=1_000, output_tokens=100
    )

    decision = governor.admit(
        turn_id="turn-a",
        output_token_limits=(800, 512, 512),
        tpm=_limit(clock, remaining=20_000),
        rpm=_limit(clock, remaining=10, limit=100),
    )

    assert decision.action is ResourceAdmissionAction.ALLOW
    assert decision.reason is ResourceAdmissionReason.SUFFICIENT_CAPACITY
    assert decision.state is ResourceGovernorState.HEALTHY
    snapshot = governor.snapshot(
        tpm=_limit(clock, remaining=20_000),
        rpm=_limit(clock, remaining=10, limit=100),
    )
    assert snapshot.local_reserved_tokens == decision.estimate.estimated_tokens
    assert snapshot.local_reserved_requests == 3


def test_tpm_below_logical_turn_estimate_defers() -> None:
    clock = _ManualClock()
    governor = _governor(clock)
    governor.observe_actual_usage(
        turn_id="turn-calibration", input_tokens=4_000, output_tokens=100
    )

    decision = governor.admit(
        turn_id="turn-low-tpm",
        output_token_limits=(800, 512),
        tpm=_limit(clock, remaining=5_000),
        rpm=_limit(clock, remaining=10, limit=100),
    )

    assert decision.action is ResourceAdmissionAction.DEFER
    assert decision.reason is ResourceAdmissionReason.TPM_CAPACITY
    assert decision.wait_seconds == 2


def test_exhausted_rpm_defers_independently() -> None:
    clock = _ManualClock()
    governor = _governor(clock)
    governor.observe_actual_usage(
        turn_id="turn-calibration", input_tokens=100, output_tokens=10
    )

    decision = governor.admit(
        turn_id="turn-low-rpm",
        output_token_limits=(256,),
        tpm=_limit(clock, remaining=40_000),
        rpm=_limit(clock, remaining=0, limit=100),
    )

    assert decision.action is ResourceAdmissionAction.DEFER
    assert decision.reason is ResourceAdmissionReason.RPM_CAPACITY


def test_missing_rpm_does_not_block_valid_tpm() -> None:
    clock = _ManualClock()
    governor = _governor(clock)
    governor.observe_actual_usage(
        turn_id="turn-calibration", input_tokens=100, output_tokens=10
    )

    decision = governor.admit(
        turn_id="turn-no-rpm",
        output_token_limits=(256,),
        tpm=_limit(clock, remaining=40_000),
        rpm=None,
    )

    assert decision.action is ResourceAdmissionAction.ALLOW
    assert decision.state is ResourceGovernorState.PRESSURE
    assert decision.reason is ResourceAdmissionReason.MISSING_RATE_LIMIT_SNAPSHOT


def test_missing_tpm_uses_explicit_serial_bootstrap_fallback() -> None:
    clock = _ManualClock()
    governor = _governor(clock)

    decision = governor.admit(
        turn_id="turn-bootstrap",
        output_token_limits=(800, 512, 512),
        tpm=None,
        rpm=_limit(clock, remaining=5, limit=100),
    )

    assert decision.action is ResourceAdmissionAction.ALLOW
    assert decision.state is ResourceGovernorState.PRESSURE
    assert decision.estimate.estimated_tokens is None
    assert governor.snapshot(tpm=None, rpm=None).local_reserved_tokens == 0


def test_latest_provider_snapshot_replaces_prior_capacity_without_double_subtraction() -> (
    None
):
    clock = _ManualClock()
    governor = _governor(clock)
    governor.observe_actual_usage(
        turn_id="turn-calibration", input_tokens=1_000, output_tokens=100
    )
    first = governor.admit(
        turn_id="turn-first",
        output_token_limits=(500,),
        tpm=_limit(clock, remaining=2_000),
        rpm=None,
    )
    assert first.action is ResourceAdmissionAction.ALLOW
    governor.release_turn(turn_id="turn-first")

    replacement = governor.admit(
        turn_id="turn-second",
        output_token_limits=(500,),
        tpm=_limit(clock, remaining=1_400),
        rpm=None,
    )
    assert replacement.action is ResourceAdmissionAction.DEFER

    latest = governor.admit(
        turn_id="turn-second",
        output_token_limits=(500,),
        tpm=_limit(clock, remaining=1_600),
        rpm=None,
    )
    assert latest.action is ResourceAdmissionAction.ALLOW
    assert latest.estimate.estimated_tokens == 1_600


def test_local_reservation_blocks_an_unrelated_turn_and_is_turn_scoped() -> None:
    clock = _ManualClock()
    governor = _governor(clock)
    governor.observe_actual_usage(
        turn_id="turn-calibration", input_tokens=100, output_tokens=10
    )
    assert (
        governor.admit(
            turn_id="turn-owner",
            output_token_limits=(256,),
            tpm=_limit(clock, remaining=10_000),
            rpm=None,
        ).action
        is ResourceAdmissionAction.ALLOW
    )

    blocked = governor.admit(
        turn_id="turn-other",
        output_token_limits=(256,),
        tpm=_limit(clock, remaining=10_000),
        rpm=None,
    )
    assert blocked.action is ResourceAdmissionAction.DEFER
    assert blocked.reason is ResourceAdmissionReason.RESERVATION_CAPACITY
    assert governor.release_turn(turn_id="turn-other") is False
    assert governor.snapshot(tpm=None, rpm=None).local_reserved_requests == 1


def test_same_turn_reservation_rechecks_latest_tpm_and_rpm() -> None:
    clock = _ManualClock()
    governor = _governor(clock)
    governor.observe_actual_usage(
        turn_id="turn-calibration", input_tokens=100, output_tokens=10
    )
    governor.admit(
        turn_id="turn-owner",
        output_token_limits=(100, 100),
        tpm=_limit(clock, remaining=10_000),
        rpm=_limit(clock, remaining=10, limit=100),
    )
    governor.bind_provider_response(
        turn_id="turn-owner", provider_response_id="response-primary"
    )
    governor.reconcile_response(
        turn_id="turn-owner",
        provider_response_id="response-primary",
        input_tokens=100,
        output_tokens=10,
    )

    tpm_deferred = governor.admit(
        turn_id="turn-owner",
        output_token_limits=(100,),
        tpm=_limit(clock, remaining=209),
        rpm=_limit(clock, remaining=1, limit=100),
    )
    assert tpm_deferred.action is ResourceAdmissionAction.DEFER
    assert tpm_deferred.reason is ResourceAdmissionReason.TPM_CAPACITY

    rpm_deferred = governor.admit(
        turn_id="turn-owner",
        output_token_limits=(100,),
        tpm=_limit(clock, remaining=210),
        rpm=_limit(clock, remaining=0, limit=100),
    )
    assert rpm_deferred.action is ResourceAdmissionAction.DEFER
    assert rpm_deferred.reason is ResourceAdmissionReason.RPM_CAPACITY

    allowed = governor.admit(
        turn_id="turn-owner",
        output_token_limits=(100,),
        tpm=_limit(clock, remaining=210),
        rpm=_limit(clock, remaining=1, limit=100),
    )
    assert allowed.action is ResourceAdmissionAction.ALLOW


def test_response_reconciles_then_logical_turn_completion_releases_reservation() -> (
    None
):
    clock = _ManualClock()
    governor = _governor(clock)
    governor.admit(
        turn_id="turn-owner",
        output_token_limits=(256,),
        tpm=None,
        rpm=None,
    )
    governor.bind_provider_response(
        turn_id="turn-owner", provider_response_id="response-owner"
    )
    governor.reconcile_response(
        turn_id="turn-owner",
        provider_response_id="response-owner",
        input_tokens=321,
        output_tokens=21,
    )

    snapshot = governor.snapshot(tpm=None, rpm=None)
    assert snapshot.local_reserved_requests == 0
    assert governor.latest_actual_input_tokens == 321
    assert governor.estimate(output_token_limits=(256,)).estimated_tokens == 598
    assert governor.release_turn(turn_id="turn-owner") is True
    assert governor.snapshot(tpm=None, rpm=None).local_reserved_requests == 0


def test_cancellation_releases_only_its_turn_reservation() -> None:
    clock = _ManualClock()
    governor = _governor(clock)
    governor.admit(
        turn_id="turn-cancelled",
        output_token_limits=(256,),
        tpm=None,
        rpm=None,
    )

    assert governor.release_turn(turn_id="turn-cancelled") is True
    assert governor.snapshot(tpm=None, rpm=None).local_reserved_requests == 0


def test_tool_capable_reservation_exceeds_single_inference_reservation() -> None:
    governor = _governor()
    governor.observe_actual_usage(
        turn_id="turn-calibration", input_tokens=1_000, output_tokens=100
    )

    single = governor.estimate(output_token_limits=(800,))
    tool_capable = governor.estimate(output_token_limits=(800, 512, 512))

    assert tool_capable.estimated_responses == 3
    assert single.estimated_tokens is not None
    assert tool_capable.estimated_tokens is not None
    assert tool_capable.estimated_tokens > single.estimated_tokens

    governor.admit(
        turn_id="turn-tool-capable",
        output_token_limits=(800, 512, 512),
        tpm=None,
        rpm=None,
    )
    governor.bind_provider_response(
        turn_id="turn-tool-capable", provider_response_id="response-primary"
    )
    governor.reconcile_response(
        turn_id="turn-tool-capable",
        provider_response_id="response-primary",
        input_tokens=1_000,
        output_tokens=100,
    )
    remaining = governor.snapshot(tpm=None, rpm=None)
    assert remaining.local_reserved_requests == 2
    assert remaining.local_reserved_tokens == 3_736


def test_primary_reserves_known_mechanics_without_unknown_tool_payload() -> None:
    clock = _ManualClock()
    governor = _governor(clock)
    governor.observe_actual_usage(
        turn_id="turn-calibration", input_tokens=10_000, output_tokens=500
    )

    primary = governor.admit(
        turn_id="turn-primary",
        output_token_limits=(800, 512, 512),
        tpm=_limit(clock, remaining=40_000, limit=40_000),
        rpm=None,
    )

    assert primary.action is ResourceAdmissionAction.ALLOW
    assert primary.estimate.estimated_responses == 3
    assert primary.estimate.input_growth_token_limits == (0, 0)
    # Each later response includes the preceding configured model-output
    # ceiling even though no future semantic tool payload is predicted.
    assert primary.estimate.estimated_tokens == 35_436
    assert MAX_SEARCH_MEMORY_TOOL_OUTPUT_UTF8_BYTES > 40_000
    assert MAX_PRIMARY_TOOL_OUTPUT_UTF8_BYTES >= (
        MAX_SEARCH_MEMORY_TOOL_OUTPUT_UTF8_BYTES
    )
    assert MAX_TERMINAL_TOOL_OUTPUT_UTF8_BYTES > 0


def test_actual_function_output_is_charged_to_continuation() -> None:
    governor = _governor()
    governor.observe_actual_usage(
        turn_id="turn-calibration", input_tokens=1_000, output_tokens=100
    )

    without_function_output = governor.estimate(output_token_limits=(512,))
    with_function_output = governor.estimate(
        output_token_limits=(512,),
        initial_input_growth_tokens=800,
    )

    assert with_function_output.estimated_tokens is not None
    assert without_function_output.estimated_tokens is not None
    assert (
        with_function_output.estimated_tokens
        == without_function_output.estimated_tokens + 800
    )


def test_actual_usage_rebases_remaining_reservation_upward_and_downward() -> None:
    def remaining_after(*, actual_input: int, actual_output: int) -> int:
        governor = _governor()
        governor.observe_actual_usage(
            turn_id="turn-calibration", input_tokens=1_000, output_tokens=100
        )
        governor.admit(
            turn_id="turn-owner",
            output_token_limits=(800, 512),
            input_growth_token_limits=(600,),
            tpm=None,
            rpm=None,
        )
        governor.bind_provider_response(
            turn_id="turn-owner", provider_response_id="response-primary"
        )
        governor.reconcile_response(
            turn_id="turn-owner",
            provider_response_id="response-primary",
            input_tokens=actual_input,
            output_tokens=actual_output,
        )
        snapshot = governor.snapshot(
            tpm=RateLimitWindow(
                limit=10_000,
                remaining=9_000,
                reset_seconds=60,
                observed_at=datetime(2026, 9, 7, tzinfo=UTC),
            ),
            rpm=None,
        )
        assert snapshot.provider_tpm_remaining == 9_000
        return snapshot.local_reserved_tokens

    prior_remaining_estimate = 1_000 + 100 + 800 + 600 + 512
    assert remaining_after(actual_input=2_000, actual_output=200) > (
        prior_remaining_estimate
    )
    assert remaining_after(actual_input=100, actual_output=10) < (
        prior_remaining_estimate
    )


def test_rate_limit_recovery_is_bounded_and_new_turn_is_not_poisoned() -> None:
    clock = _ManualClock()
    governor = _governor(clock, max_attempts=1)
    governor.admit(
        turn_id="turn-old",
        output_token_limits=(256,),
        tpm=None,
        rpm=None,
    )
    governor.bind_provider_response(
        turn_id="turn-old", provider_response_id="response-rejected"
    )
    assert governor.mark_rate_limited(turn_id="turn-old") is True
    governor.begin_recovery(turn_id="turn-old")
    governor.bind_provider_response(
        turn_id="turn-old", provider_response_id="response-retry"
    )
    assert governor.mark_rate_limited(turn_id="turn-old") is False
    exhausted = governor.snapshot(tpm=None, rpm=None)
    assert exhausted.state is ResourceGovernorState.PROVIDER_UNAVAILABLE
    assert exhausted.local_reserved_requests == 0

    recovered = governor.admit(
        turn_id="turn-new",
        output_token_limits=(256,),
        tpm=None,
        rpm=None,
    )
    assert recovered.action is ResourceAdmissionAction.ALLOW


def test_reset_deadline_replenishes_snapshot_without_mutating_provider_truth() -> None:
    clock = _ManualClock()
    governor = _governor(clock)
    governor.observe_actual_usage(
        turn_id="turn-calibration", input_tokens=1_000, output_tokens=100
    )
    snapshot = _limit(clock, remaining=0, limit=10_000, reset_seconds=2)
    deferred = governor.admit(
        turn_id="turn-after-reset",
        output_token_limits=(256,),
        tpm=snapshot,
        rpm=None,
    )
    assert deferred.action is ResourceAdmissionAction.DEFER

    clock.advance(2)
    allowed = governor.admit(
        turn_id="turn-after-reset",
        output_token_limits=(256,),
        tpm=snapshot,
        rpm=None,
    )
    assert allowed.action is ResourceAdmissionAction.ALLOW
    assert governor.snapshot(tpm=snapshot, rpm=None).provider_tpm_remaining == 0


def test_recovery_wait_is_capped_by_the_configured_horizon() -> None:
    clock = _ManualClock()
    governor = _governor(clock)
    governor.admit(
        turn_id="turn-recovery",
        output_token_limits=(256,),
        tpm=None,
        rpm=None,
    )
    governor.bind_provider_response(
        turn_id="turn-recovery", provider_response_id="response-limited"
    )
    assert governor.mark_rate_limited(turn_id="turn-recovery") is True

    wait_seconds = governor.recovery_wait_seconds(
        limit_kind="tokens",
        tpm=_limit(clock, remaining=0, reset_seconds=120),
        rpm=None,
    )

    assert wait_seconds == 30
