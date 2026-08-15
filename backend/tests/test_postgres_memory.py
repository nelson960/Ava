from __future__ import annotations

import os
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from ava_backend.protocol import AgeBand
from ava_backend.realtime.coordinator import (
    AvaServerControlFrame,
    DeterministicRealtimeSessionCoordinator,
)
from ava_backend.realtime.events import (
    AVA_CLIENT_EVENT_ADAPTER,
    AvaTurnPhase,
    TurnCompletedEvent,
    TurnPhaseChangedEvent,
)
from ava_backend.realtime.fakes import (
    DETERMINISTIC_AUDIO_OFFER_SDP,
    DeterministicCanvasSpecialist,
    DeterministicRealtimePlanProvider,
    DeterministicStreamingSpeechSynthesizer,
)
from ava_backend.realtime.model_plan import model_canvas_draft_from_runtime_plan
from ava_backend.realtime.memory import DeliveredConversationTurn
from ava_backend.realtime.plan import AvaMemoryProposal, AvaTurnPlan
from ava_backend.realtime.postgres_memory import PostgresAuthoritativeMemoryStore

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"


TEST_DATABASE_URL = os.getenv("AVA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="AVA_TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)


def _turn(index: int) -> DeliveredConversationTurn:
    return DeliveredConversationTurn.model_validate(
        {
            "turnId": f"turn-{index}",
            "childText": f"I enjoy astronomy number {index}.",
            "avaDeliveredText": "The night sky is full of interesting patterns.",
            "deliveredSampleCount": 24_000,
            "childTurnSummary": f"Talked about astronomy topic {index}.",
            "terminal": "completed",
        }
    )


async def test_postgres_v3_memory_round_trip_compaction_recap_and_reset() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    await store.open()
    try:
        await store.ping()
        for index in range(1, 10):
            proposal = None
            memory_id = None
            if index == 1:
                proposal = AvaMemoryProposal.model_validate(
                    {
                        "kind": "interest",
                        "summary": "The child enjoys astronomy.",
                        "evidence": "I enjoy astronomy number 1.",
                        "confidence": 0.9,
                    }
                )
                memory_id = "memory-1"
            await store.commit_delivered_turn(
                subject=subject,
                turn=_turn(index),
                proposal=proposal,
                memory_id=memory_id,
            )

        snapshot = await store.load_snapshot(
            subject=subject,
            age_band="middleChildhood",
        )
        assert [turn.turn_id for turn in snapshot.delivered_turns] == [
            f"turn-{index}" for index in range(2, 10)
        ]
        assert snapshot.current_session_summary == ("Talked about astronomy topic 1.")
        assert [memory.id for memory in snapshot.stable_memories] == ["memory-1"]

        now = datetime.now(UTC)
        recap = await store.load_weekly_recap(
            subject=subject,
            period_start=now.date() - timedelta(days=now.weekday()),
            generated_at=now,
        )
        assert len(recap.records) == 9

        receipt = await store.reset_conversation(subject=subject)
        assert receipt.revision == 1
        empty = await store.load_snapshot(
            subject=subject,
            age_band="middleChildhood",
        )
        assert empty.delivered_turns == []
        assert empty.stable_memories == []
        assert empty.current_session_summary is None
    finally:
        await store.close()


async def test_fake_realtime_tts_delivery_pipeline_commits_to_postgres() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    suffix = uuid4().hex[:12]
    session_id = f"session-{suffix}"
    turn_id = f"turn-{suffix}"
    stream_id = f"stream-{suffix}"
    subject = f"test:{suffix}"
    plan = AvaTurnPlan.model_validate_json(
        (FIXTURE_ROOT / "ava_turn_plan_v3.json").read_text(encoding="utf-8")
    )
    coordinator = DeterministicRealtimeSessionCoordinator(
        session_id=session_id,
        turn_id=turn_id,
        request_id=f"request-{suffix}",
        stream_id=stream_id,
        subject=subject,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        realtime=DeterministicRealtimePlanProvider(
            plan,
            child_text="Please explain a plant cell.",
            canvas_brief="Draw a labeled interactive plant cell.",
        ),
        speech=DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31),
        canvas_specialist=DeterministicCanvasSpecialist(
            model_canvas_draft_from_runtime_plan(plan)
        ),
        memory=store,
        memory_id_factory=lambda current_turn_id: f"memory:{current_turn_id}",
    )
    await store.open()
    try:
        await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
        await coordinator.receive(
            AVA_CLIENT_EVENT_ADAPTER.validate_python(
                {
                    "protocolVersion": 3,
                    "sessionId": session_id,
                    "event": "clientReady",
                    "sequence": 0,
                }
            )
        )
        terminal = None
        async for frame in coordinator.frames():
            if not isinstance(frame, AvaServerControlFrame):
                continue
            if isinstance(frame.event, TurnPhaseChangedEvent) and (
                frame.event.phase == AvaTurnPhase.SPEAKING
            ):
                await coordinator.receive(
                    AVA_CLIENT_EVENT_ADAPTER.validate_python(
                        {
                            "protocolVersion": 3,
                            "sessionId": session_id,
                            "turnId": turn_id,
                            "streamId": stream_id,
                            "event": "playbackStarted",
                            "sequence": 1,
                            "renderedSampleCount": 0,
                        }
                    )
                )
                await coordinator.receive(
                    AVA_CLIENT_EVENT_ADAPTER.validate_python(
                        {
                            "protocolVersion": 3,
                            "sessionId": session_id,
                            "turnId": turn_id,
                            "streamId": stream_id,
                            "event": "playbackProgress",
                            "sequence": 2,
                            "renderedSampleCount": (coordinator.received_sample_count),
                            "bufferedSampleCount": 0,
                        }
                    )
                )
            if isinstance(frame.event, TurnCompletedEvent):
                terminal = frame.event

        assert terminal is not None
        assert terminal.delivered_text == plan.speech
        snapshot = await store.load_snapshot(
            subject=subject,
            age_band=AgeBand.MIDDLE_CHILDHOOD,
        )
        assert snapshot.delivered_turns[0].ava_delivered_text == plan.speech
        assert snapshot.delivered_turns[0].terminal == "completed"
    finally:
        await coordinator.close()
        await store.close()


async def test_postgres_serializes_concurrent_semantic_memory_proposals() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    await store.open()
    try:

        async def commit(index: int):
            return await store.commit_delivered_turn(
                subject=subject,
                turn=_turn(index),
                proposal=AvaMemoryProposal.model_validate(
                    {
                        "kind": "interest",
                        "summary": "The child enjoys astronomy.",
                        "evidence": f"I enjoy astronomy number {index}.",
                        "confidence": 0.9,
                    }
                ),
                memory_id=f"memory-{index}",
            )

        decisions = await asyncio.gather(commit(20), commit(21))
        assert sum(decision.accepted for decision in decisions) == 1
        rejected = next(decision for decision in decisions if not decision.accepted)
        assert "equivalent" in rejected.reason.lower()
        snapshot = await store.load_snapshot(
            subject=subject,
            age_band=AgeBand.MIDDLE_CHILDHOOD,
        )
        assert len(snapshot.delivered_turns) == 2
        assert len(snapshot.stable_memories) == 1
    finally:
        await store.close()
