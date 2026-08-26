from __future__ import annotations

import os
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from ava_backend.canvas.svg_delivery import SvgCanvasDocumentStore
from ava_backend.canvas.svg_session import SvgCanvasSessionState
from ava_backend.canvas.svg_specialist import (
    SvgCanvasSpecialistGeneration,
    SvgCanvasSpecialistRequest,
)
from ava_backend.protocol import AgeBand
from ava_backend.realtime.coordinator import DeterministicRealtimeSessionCoordinator
from ava_backend.realtime.coordinator_v4 import ProtocolV4CanvasEventSink
from ava_backend.realtime.events_v4 import (
    AVA_CLIENT_EVENT_ADAPTER_V4,
    TurnCompletedEventV4,
    TurnPhaseChangedEventV4,
)
from ava_backend.realtime.fakes import (
    DETERMINISTIC_AUDIO_OFFER_SDP,
    DeterministicRealtimePlanProvider,
    DeterministicStreamingSpeechSynthesizer,
)
from ava_backend.realtime.memory import DeliveredConversationTurn
from ava_backend.realtime.memory import AvaMemoryProposal
from ava_backend.realtime.plan import AvaTurnPlan
from ava_backend.realtime.postgres_memory import PostgresAuthoritativeMemoryStore
from ava_backend.realtime.speech_contract import SpeechPhase
from ava_backend.realtime.svg_canvas_jobs import SvgCanvasJobCoordinator
from ava_backend.realtime.websocket_v4 import AvaServerControlFrameV4

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"


TEST_DATABASE_URL = os.getenv("AVA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="AVA_TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)


class _NoCanvasSpecialist:
    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
    ) -> SvgCanvasSpecialistGeneration:
        del request
        raise AssertionError("PostgreSQL speech pipeline does not request canvas")

    async def cancel(self, *, generation_id: str) -> None:
        del generation_id

    async def close(self) -> None:
        return


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
        (FIXTURE_ROOT / "ava_turn_plan_conversation_v3.json").read_text(
            encoding="utf-8"
        )
    )
    canvas_events = ProtocolV4CanvasEventSink()
    canvas_jobs = SvgCanvasJobCoordinator(
        session_id=session_id,
        age_band=AgeBand.MIDDLE_CHILDHOOD,
        specialist=_NoCanvasSpecialist(),
        state=SvgCanvasSessionState(),
        documents=SvgCanvasDocumentStore(),
        event_sink=canvas_events,
        generation_id_factory=lambda: f"generation-{suffix}",
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
        ),
        speech=DeterministicStreamingSpeechSynthesizer(chunk_sample_count=31),
        canvas_jobs=canvas_jobs,
        canvas_events=canvas_events,
        memory=store,
    )
    await store.open()
    try:
        await coordinator.negotiate(offer_sdp=DETERMINISTIC_AUDIO_OFFER_SDP)
        await coordinator.receive(
            AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
                {
                    "protocolVersion": 4,
                    "sessionId": session_id,
                    "event": "clientReady",
                    "sequence": 0,
                    "rendererCapabilityVersion": "ava-svg-renderer-v2",
                    "validatorVersion": "ava-svg-validator-v2",
                }
            )
        )
        terminal = None
        async for frame in coordinator.frames():
            if not isinstance(frame, AvaServerControlFrameV4):
                continue
            if isinstance(frame.event, TurnPhaseChangedEventV4) and (
                frame.event.phase == SpeechPhase.SPEAKING
            ):
                await coordinator.receive(
                    AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
                        {
                            "protocolVersion": 4,
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
                    AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(
                        {
                            "protocolVersion": 4,
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
            if isinstance(frame.event, TurnCompletedEventV4):
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
