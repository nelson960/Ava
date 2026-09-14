from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime
import logging
from types import SimpleNamespace
from typing import cast

from ava_backend.protocol import AgeBand
from ava_backend.realtime.context_assembler import (
    DynamicContextAssembler,
    DynamicContextBuildReason,
)
from ava_backend.realtime.coordinator import DeterministicRealtimeSessionCoordinator
from ava_backend.realtime.harness import RealtimeHarness, build_realtime_harness
from ava_backend.realtime.memory import DialogueFrame
from ava_backend.realtime.provider_session import (
    RealtimeCommittedInput,
    RealtimeContextInstallationStatus,
)


NOW = datetime(2026, 9, 9, 6, 30, tzinfo=UTC)
CANVAS_A = None
CANVAS_B = (
    '<active_canvas trust="validated_scene_data" scene_id="scene-b" '
    'revision="1">Scene B</active_canvas>'
)
CANVAS_C = (
    '<active_canvas trust="validated_scene_data" scene_id="scene-c" '
    'revision="1">Scene C</active_canvas>'
)


class _ContextProvider:
    def __init__(
        self,
        *,
        active_build_id: str | None = "ctx-1",
        desired_build_id: str | None = "ctx-1",
        acknowledged: set[str] | None = None,
        statuses: tuple[RealtimeContextInstallationStatus | None, ...] = (),
    ) -> None:
        self.active_context_build_id = active_build_id
        self.desired_context_build_id = desired_build_id
        self.acknowledged = set(acknowledged) if acknowledged is not None else {"ctx-1"}
        self.statuses = deque(statuses)
        self.prepare_calls: list[RealtimeHarness] = []
        self.wait_for_context_calls: list[str] = []
        self.response_requests: list[tuple[str, str, bool]] = []
        self._context_changed = asyncio.Event()
        self._prepare_called = asyncio.Event()
        self._wait_for_context_called = asyncio.Event()

    def is_context_acknowledged(self, build_id: str) -> bool:
        return build_id in self.acknowledged

    async def wait_for_context(self, *, build_id: str) -> None:
        self.wait_for_context_calls.append(build_id)
        self._wait_for_context_called.set()
        while build_id not in self.acknowledged:
            self._context_changed.clear()
            if build_id in self.acknowledged:
                return
            await self._context_changed.wait()

    def acknowledge(self, build_id: str) -> None:
        self.acknowledged.add(build_id)
        self.active_context_build_id = build_id
        self._context_changed.set()

    async def prepare_if_idle(
        self,
        *,
        harness: RealtimeHarness,
        turn_id: str,
    ) -> RealtimeContextInstallationStatus | None:
        del turn_id
        self.prepare_calls.append(harness)
        self._prepare_called.set()
        status = self.statuses.popleft() if self.statuses else None
        if status is not None:
            self.desired_context_build_id = harness.context.build_id
            if status in {
                RealtimeContextInstallationStatus.INSTALLED,
                RealtimeContextInstallationStatus.DEDUPLICATED,
            }:
                self.acknowledge(harness.context.build_id)
        return status

    async def wait_until_canvas_interaction_response_ready(
        self,
        *,
        turn_id: str,
    ) -> bool:
        del turn_id
        return False

    async def request_child_response(
        self,
        *,
        turn_id: str,
        item_id: str,
        permit_degraded_focus_grounding: bool = False,
    ) -> bool:
        self.response_requests.append(
            (turn_id, item_id, permit_degraded_focus_grounding)
        )
        return True

    async def wait_until_response_admissible(self, *, turn_id: str) -> bool:
        del turn_id
        return True

    async def wait_for_prepare_call_count(self, count: int) -> None:
        while len(self.prepare_calls) < count:
            self._prepare_called.clear()
            if len(self.prepare_calls) >= count:
                return
            await self._prepare_called.wait()

    async def wait_for_context_call(self, build_id: str) -> None:
        while build_id not in self.wait_for_context_calls:
            self._wait_for_context_called.clear()
            if build_id in self.wait_for_context_calls:
                return
            await self._wait_for_context_called.wait()


class _CanvasJobs:
    def __init__(self) -> None:
        self.context: str | None = CANVAS_A
        self.state = SimpleNamespace(
            focus_revision=0,
            presentation_visible=False,
            focused_scene=None,
            last_interaction=None,
        )

    def render_realtime_context(self) -> str:
        return self.context or ""


def _initial_harness() -> RealtimeHarness:
    context = DynamicContextAssembler().assemble(
        build_id="ctx-1",
        reason=DynamicContextBuildReason.SESSION_BOOTSTRAP,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        timezone_id="UTC",
        current_time_utc=NOW,
        dialogue_frame=DialogueFrame(),
    )
    return build_realtime_harness(dynamic_context=context)


def _coordinator(
    provider: _ContextProvider,
) -> DeterministicRealtimeSessionCoordinator:
    coordinator = cast(
        DeterministicRealtimeSessionCoordinator,
        object.__new__(DeterministicRealtimeSessionCoordinator),
    )
    coordinator._session_id = "session-economy"
    coordinator._turn_id = "turn-current"
    coordinator._age_band = AgeBand.MIDDLE_CHILDHOOD
    coordinator._timezone_id = "UTC"
    coordinator._realtime = provider  # type: ignore[assignment]
    coordinator._canvas_jobs = _CanvasJobs()  # type: ignore[assignment]
    coordinator._context_assembler = DynamicContextAssembler()
    coordinator._current_time_factory = lambda: NOW
    coordinator._memory_snapshot = SimpleNamespace()
    coordinator._dialogue_frame = DialogueFrame()
    coordinator._warm_memories = ()
    coordinator._harness = _initial_harness()
    coordinator._pending_harness = None
    coordinator._pending_focus_revision = None
    coordinator._pending_context_reason = None
    coordinator._context_build_sequence = 1
    coordinator._canvas_context_lock = asyncio.Lock()
    coordinator._focus_linearization_lock = asyncio.Lock()
    coordinator._submitted_focus_revision = 0
    coordinator._submitted_focus_build_id = "ctx-1"
    coordinator._focus_build_id_by_revision = {0: "ctx-1"}
    coordinator._acknowledged_focus_revision = 0
    coordinator._focus_changed = asyncio.Event()
    coordinator._context_installation_task = None
    coordinator._negotiated_call = None
    coordinator._closed = False
    coordinator._latest_canvas_interaction_intent = None
    coordinator._canvas_interaction_response_wakeup = asyncio.Event()
    coordinator._turn_context_sync_owner = None
    coordinator._cancellation_requested = asyncio.Event()
    coordinator._turn_context_timeout_seconds = 1.0
    return coordinator


async def _settle_installation_worker(
    coordinator: DeterministicRealtimeSessionCoordinator,
) -> None:
    await asyncio.sleep(0)
    task = coordinator._context_installation_task
    if task is not None:
        await task


async def test_current_equivalent_is_suppressed_and_real_harness_survives(
    caplog,
) -> None:  # type: ignore[no-untyped-def]
    provider = _ContextProvider()
    coordinator = _coordinator(provider)
    effective = coordinator.harness

    with caplog.at_level(logging.INFO, logger="ava_backend.realtime_coordinator"):
        await coordinator._refresh_canvas_context(
            reason=DynamicContextBuildReason.CANVAS_PRESENTATION_CHANGED
        )

    assert provider.prepare_calls == []
    assert coordinator.harness is effective
    assert "ctx-2" not in coordinator._focus_build_id_by_revision.values()
    assert "status=currentEquivalentSuppressed" in caplog.text


async def test_different_build_reason_with_same_provider_semantics_is_suppressed() -> (
    None
):
    provider = _ContextProvider()
    coordinator = _coordinator(provider)

    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_FOCUSED
    )
    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.TURN_COMMITTED
    )

    assert provider.prepare_calls == []


async def test_real_dialogue_frame_change_attempts_provider_install_once() -> None:
    provider = _ContextProvider(statuses=(RealtimeContextInstallationStatus.SUBMITTED,))
    coordinator = _coordinator(provider)
    coordinator._dialogue_frame = DialogueFrame.model_validate(
        {"revision": 1, "activeTopics": ["Saturn"]}
    )

    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.TURN_COMMITTED
    )

    assert len(provider.prepare_calls) == 1
    assert coordinator.harness.context.build_id == "ctx-2"


async def test_real_canvas_change_attempts_provider_install_once() -> None:
    provider = _ContextProvider(statuses=(RealtimeContextInstallationStatus.SUBMITTED,))
    coordinator = _coordinator(provider)
    canvas_jobs = cast(_CanvasJobs, coordinator._canvas_jobs)
    canvas_jobs.context = CANVAS_B

    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_FOCUSED
    )

    assert len(provider.prepare_calls) == 1
    assert coordinator.harness.context.build_id == "ctx-2"


async def test_focus_revision_aliases_current_real_build_without_fabricating_ack() -> (
    None
):
    provider = _ContextProvider()
    coordinator = _coordinator(provider)
    coordinator._canvas_jobs.state.focus_revision = 12

    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_FOCUSED
    )

    assert provider.prepare_calls == []
    assert coordinator._focus_build_id_by_revision[12] == "ctx-1"
    assert coordinator._submitted_focus_build_id == "ctx-1"
    assert coordinator._acknowledged_focus_revision == 0
    assert "ctx-2" not in coordinator._focus_build_id_by_revision.values()


async def test_unacknowledged_desired_build_is_reused_but_fence_still_waits() -> None:
    provider = _ContextProvider(
        active_build_id=None,
        desired_build_id="ctx-1",
        acknowledged=set(),
    )
    coordinator = _coordinator(provider)
    coordinator._canvas_jobs.state.focus_revision = 4

    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_FOCUSED
    )
    target_build_id = coordinator._focus_build_id_by_revision[4]
    waiter = asyncio.create_task(provider.wait_for_context(build_id=target_build_id))
    await asyncio.sleep(0)

    assert target_build_id == "ctx-1"
    assert waiter.done() is False
    assert coordinator._acknowledged_focus_revision == 0
    provider.acknowledge("ctx-1")
    await waiter


async def test_pending_equivalent_coalesces_on_real_pending_build() -> None:
    provider = _ContextProvider(statuses=(None,))
    coordinator = _coordinator(provider)
    canvas_jobs = cast(_CanvasJobs, coordinator._canvas_jobs)
    canvas_jobs.context = CANVAS_B
    canvas_jobs.state.focus_revision = 1
    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_FOCUSED
    )
    pending = coordinator._pending_harness
    assert pending is not None

    canvas_jobs.state.focus_revision = 2
    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_PRESENTATION_CHANGED
    )
    await _settle_installation_worker(coordinator)

    assert len(provider.prepare_calls) == 1
    assert coordinator._pending_harness is pending
    assert coordinator._pending_focus_revision == 2
    assert 2 not in coordinator._focus_build_id_by_revision
    assert coordinator._submitted_focus_revision == 0
    assert coordinator._submitted_focus_build_id == "ctx-1"
    assert coordinator._acknowledged_focus_revision == 0
    assert coordinator._focus_changed.is_set() is False
    assert "ctx-3" not in coordinator._focus_build_id_by_revision.values()

    provider.statuses.append(RealtimeContextInstallationStatus.SUBMITTED)
    (
        installed,
        status,
        installed_harness,
        _,
    ) = await coordinator._install_pending_context_if_idle(turn_id="turn-current")

    assert installed is True
    assert status == RealtimeContextInstallationStatus.SUBMITTED
    assert installed_harness is pending
    assert len(provider.prepare_calls) == 2
    assert provider.prepare_calls[0] is pending
    assert provider.prepare_calls[1] is pending
    assert coordinator._focus_build_id_by_revision[2] == pending.context.build_id
    assert coordinator._submitted_focus_revision == 2
    assert coordinator._submitted_focus_build_id == pending.context.build_id
    assert coordinator._acknowledged_focus_revision == 0
    assert coordinator._focus_changed.is_set() is True
    assert "ctx-3" not in coordinator._focus_build_id_by_revision.values()


async def test_turn_context_never_waits_for_unsubmitted_pending_build() -> None:
    provider = _ContextProvider(statuses=(None,))
    coordinator = _coordinator(provider)
    canvas_jobs = cast(_CanvasJobs, coordinator._canvas_jobs)
    canvas_jobs.context = CANVAS_B
    canvas_jobs.state.focus_revision = 1
    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_FOCUSED
    )
    pending = coordinator._pending_harness
    assert pending is not None
    pending_build_id = pending.context.build_id

    canvas_jobs.state.focus_revision = 2
    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_PRESENTATION_CHANGED
    )
    await _settle_installation_worker(coordinator)

    committed = RealtimeCommittedInput(
        turn_id="turn-current",
        item_id="item-current",
    )
    synchronization = asyncio.create_task(
        coordinator._synchronize_turn_context(committed)
    )
    await provider.wait_for_prepare_call_count(3)

    assert pending_build_id not in provider.wait_for_context_calls
    assert 2 not in coordinator._focus_build_id_by_revision
    assert coordinator._submitted_focus_build_id == "ctx-1"

    provider.statuses.append(RealtimeContextInstallationStatus.SUBMITTED)
    (
        installed,
        status,
        installed_harness,
        _,
    ) = await coordinator._install_pending_context_if_idle(turn_id="turn-current")
    assert installed is True
    assert status == RealtimeContextInstallationStatus.SUBMITTED
    assert installed_harness is pending
    assert coordinator._focus_build_id_by_revision[2] == pending_build_id
    assert coordinator._submitted_focus_build_id == pending_build_id

    await provider.wait_for_context_call(pending_build_id)
    provider.acknowledge(pending_build_id)
    await synchronization

    assert coordinator._acknowledged_focus_revision == 2
    assert provider.response_requests == [("turn-current", "item-current", False)]


async def test_a_to_b_to_a_retires_unsubmitted_b_and_aliases_real_a() -> None:
    provider = _ContextProvider(statuses=(None,))
    coordinator = _coordinator(provider)
    canvas_jobs = cast(_CanvasJobs, coordinator._canvas_jobs)
    canvas_jobs.context = CANVAS_B
    canvas_jobs.state.focus_revision = 1
    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_FOCUSED
    )
    retired_build_id = coordinator._pending_harness.context.build_id  # type: ignore[union-attr]

    canvas_jobs.state.focus_revision = 2
    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_PRESENTATION_CHANGED
    )
    assert 2 not in coordinator._focus_build_id_by_revision

    canvas_jobs.context = CANVAS_A
    canvas_jobs.state.focus_revision = 3
    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_FOCUSED
    )
    await _settle_installation_worker(coordinator)

    assert len(provider.prepare_calls) == 1
    assert coordinator._pending_harness is None
    assert coordinator.harness.context.build_id == "ctx-1"
    assert coordinator._focus_build_id_by_revision[3] == "ctx-1"
    assert retired_build_id not in coordinator._focus_build_id_by_revision.values()
    assert coordinator._submitted_focus_build_id == "ctx-1"
    assert provider.desired_context_build_id == "ctx-1"


async def test_a_to_b_to_a_after_b_submission_submits_new_a_context() -> None:
    provider = _ContextProvider(
        statuses=(
            RealtimeContextInstallationStatus.SUBMITTED,
            RealtimeContextInstallationStatus.SUBMITTED,
        )
    )
    coordinator = _coordinator(provider)
    canvas_jobs = cast(_CanvasJobs, coordinator._canvas_jobs)
    canvas_jobs.context = CANVAS_B
    canvas_jobs.state.focus_revision = 1
    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_FOCUSED
    )

    canvas_jobs.context = CANVAS_A
    canvas_jobs.state.focus_revision = 2
    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_FOCUSED
    )

    assert len(provider.prepare_calls) == 2
    assert coordinator.harness.context.build_id == "ctx-3"
    assert coordinator._focus_build_id_by_revision[2] == "ctx-3"


async def test_different_pending_context_supersedes_latest_local_candidate() -> None:
    provider = _ContextProvider(statuses=(None, None))
    coordinator = _coordinator(provider)
    canvas_jobs = cast(_CanvasJobs, coordinator._canvas_jobs)
    canvas_jobs.context = CANVAS_B
    canvas_jobs.state.focus_revision = 1
    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_FOCUSED
    )
    retired_build_id = coordinator._pending_harness.context.build_id  # type: ignore[union-attr]

    canvas_jobs.context = CANVAS_C
    canvas_jobs.state.focus_revision = 2
    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_FOCUSED
    )
    await _settle_installation_worker(coordinator)

    assert len(provider.prepare_calls) == 2
    assert coordinator._pending_harness is not None
    assert coordinator._pending_harness.context.build_id == "ctx-3"
    assert retired_build_id not in coordinator._focus_build_id_by_revision.values()


async def test_static_harness_semantic_difference_prevents_suppression() -> None:
    provider = _ContextProvider(statuses=(RealtimeContextInstallationStatus.SUBMITTED,))
    coordinator = _coordinator(provider)
    coordinator._harness = replace(
        coordinator.harness,
        instructions=coordinator.harness.instructions + "\nStatic semantic change.",
    )

    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_FOCUSED
    )

    assert len(provider.prepare_calls) == 1


async def test_suppressed_context_keeps_no_pending_helper_ready() -> None:
    provider = _ContextProvider()
    coordinator = _coordinator(provider)
    await coordinator._refresh_canvas_context(
        reason=DynamicContextBuildReason.CANVAS_INTERACTION_COMMITTED
    )

    result = await coordinator._install_pending_context_if_idle(turn_id="turn-current")

    assert result == (True, None, None, None)
    assert provider.prepare_calls == []
