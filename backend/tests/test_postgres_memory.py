from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, errors, sql
from psycopg.conninfo import make_conninfo

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
from ava_backend.realtime.memory import (
    DeliveredConversationTurn,
    DialogueFrame,
    DialogueFrameSourceTurnMissingError,
    DialogueFrameUpdate,
    DialogueFrameUpdateConflictError,
    MAX_WEEKLY_RECORDS,
    MemoryCandidateSourceKind,
    MemoryResetRevisionMismatchError,
    SemanticJournalConflictError,
    SemanticMemoryProposal,
    current_local_week_utc_bounds,
)
from ava_backend.realtime.plan import AvaTurnPlan
from ava_backend.realtime.postgres_memory import PostgresAuthoritativeMemoryStore
from ava_backend.realtime.postgres_memory import PostgresMemoryStoreError
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
        *,
        draft_sink: object | None = None,
    ) -> SvgCanvasSpecialistGeneration:
        del request, draft_sink
        raise AssertionError("PostgreSQL speech pipeline does not request canvas")

    async def cancel(self, *, generation_id: str) -> None:
        del generation_id

    async def close(self) -> None:
        return


class _PausingFencePostgresStore(PostgresAuthoritativeMemoryStore):
    def __init__(self, database_url: str) -> None:
        super().__init__(database_url)
        self.before_subject_lock = asyncio.Event()
        self.allow_subject_lock = asyncio.Event()

    async def _lock_subject(self, connection, subject: str) -> int:  # type: ignore[no-untyped-def]
        self.before_subject_lock.set()
        await self.allow_subject_lock.wait()
        return await super()._lock_subject(connection, subject)


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


def _interaction_turn(index: int) -> DeliveredConversationTurn:
    return DeliveredConversationTurn.model_validate(
        {
            "turnId": f"turn-interaction-{index}",
            "inputKind": "canvasInteraction",
            "childText": None,
            "childTextSource": None,
            "canvasInteraction": {
                "sceneId": "scene-A",
                "sceneRevision": 2,
                "interactionRevision": index,
                "interactionMode": "explore",
                "bindingId": f"binding-{index}",
                "targetId": f"target-{index}",
                "accessibilityLabel": f"target-{index} label",
                "stepIndex": None,
            },
            "avaDeliveredText": "A bounded heard response.",
            "deliveredSampleCount": 24_000,
            "childTurnSummary": "Canvas interaction.",
            "terminal": "completed",
        }
    )


async def test_postgres_v8_schema_includes_semantic_journal() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    await store.open()
    connection = await AsyncConnection.connect(TEST_DATABASE_URL)
    try:
        cursor = await connection.execute(
            """
            select
              (select version from ava_private.schema_migrations
                where name = 'realtime_memory_v8'),
              (select version from ava_private.schema_migrations
                where name = 'semantic_capture_receipt_disposition'),
              to_regclass('ava_private.weekly_records'),
              to_regclass('ava_private.dialogue_frame_receipts'),
              to_regclass('ava_private.semantic_journal_events'),
              to_regclass('ava_private.semantic_journal_event_sources'),
              to_regclass('ava_private.semantic_journal_supersessions'),
              to_regclass('ava_private.semantic_memory_receipts'),
              exists (
                select 1 from information_schema.columns
                where table_schema = 'ava_private'
                  and table_name = 'memory_state'
                  and column_name = 'dialogue_frame_payload'
              ),
              exists (
                select 1 from information_schema.columns
                where table_schema = 'ava_private'
                  and table_name = 'semantic_memory_receipts'
                  and column_name = 'disposition'
              ),
              exists (
                select 1
                from pg_catalog.pg_tables
                where schemaname = 'ava_private'
                  and tablename like '%recap%'
              )
            """
        )
        assert await cursor.fetchone() == (
            1,
            1,
            None,
            "ava_private.dialogue_frame_receipts",
            "ava_private.semantic_journal_events",
            "ava_private.semantic_journal_event_sources",
            "ava_private.semantic_journal_supersessions",
            "ava_private.semantic_memory_receipts",
            True,
            True,
            False,
        )
    finally:
        await connection.close()
        await store.close()


async def test_postgres_startup_rejects_schema_without_disposition_registration() -> (
    None
):
    assert TEST_DATABASE_URL is not None
    connection = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    try:
        await connection.execute(
            "delete from ava_private.schema_migrations where name = 'semantic_capture_receipt_disposition'"
        )
        with pytest.raises(
            PostgresMemoryStoreError, match="semantic_capture_receipt_disposition"
        ):
            await store.open()
    finally:
        await store.close()
        await connection.execute(
            """
            insert into ava_private.schema_migrations (name, version)
            values ('semantic_capture_receipt_disposition', 1)
            on conflict (name) do update
            set version = excluded.version,
                applied_at = now()
            """
        )
        await connection.close()


async def test_postgres_memory_round_trip_does_not_compact_at_event_nine() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    await store.open()
    try:
        await store.ping()
        for index in range(1, 10):
            await store.commit_delivered_turn(
                subject=subject,
                expected_reset_revision=0,
                turn=_turn(index),
                timezone_id="Asia/Kolkata",
            )

        snapshot = await store.load_snapshot(
            subject=subject,
            age_band="middleChildhood",
        )
        assert [turn.turn_id for turn in snapshot.delivered_turns] == [
            f"turn-{index}" for index in range(2, 10)
        ]
        assert snapshot.current_session_summary is None
        assert snapshot.stable_memories == []
        connection = await AsyncConnection.connect(TEST_DATABASE_URL)
        try:
            counts_cursor = await connection.execute(
                """
                select
                  (select count(*) from ava_private.delivered_turns where subject = %s),
                  (select count(*) from ava_private.conversation_episodes where subject = %s),
                  (select count(*) from ava_private.delivered_turns
                    where subject = %s and timezone_id = 'Asia/Kolkata')
                """,
                (subject, subject, subject),
            )
            assert await counts_cursor.fetchone() == (9, 0, 9)
        finally:
            await connection.close()

        now = datetime.now(UTC)
        recap = await store.load_weekly_recap(
            subject=subject,
            timezone_id="Asia/Kolkata",
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


async def test_postgres_dialogue_frame_cas_receipts_and_reset() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    update = DialogueFrameUpdate.model_validate(
        {"baseRevision": 0, "frame": {"activeTopics": ["Astronomy"]}}
    )
    await store.open()
    try:
        with pytest.raises(DialogueFrameSourceTurnMissingError):
            await store.apply_dialogue_frame_update(
                subject=subject,
                expected_reset_revision=0,
                source_turn_id="turn-1",
                update=update,
            )
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(1),
        )
        applied = await store.apply_dialogue_frame_update(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-1",
            update=update,
        )
        retry = await store.apply_dialogue_frame_update(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-1",
            update=update,
        )
        with pytest.raises(DialogueFrameUpdateConflictError):
            await store.apply_dialogue_frame_update(
                subject=subject,
                expected_reset_revision=0,
                source_turn_id="turn-1",
                update=DialogueFrameUpdate.model_validate(
                    {"baseRevision": 0, "frame": {"activeTopics": ["Biology"]}}
                ),
            )

        assert applied.status == "applied"
        assert retry.status == "idempotent"
        snapshot = await store.load_snapshot(
            subject=subject, age_band="middleChildhood"
        )
        assert snapshot.dialogue_frame.revision == 1
        assert snapshot.dialogue_frame.active_topics == ("Astronomy",)

        receipt = await store.reset_conversation(subject=subject)
        reset_snapshot = await store.load_snapshot(
            subject=subject, age_band="middleChildhood"
        )
        assert receipt.revision == 1
        assert reset_snapshot.dialogue_frame == DialogueFrame()
        with pytest.raises(MemoryResetRevisionMismatchError):
            await store.apply_dialogue_frame_update(
                subject=subject,
                expected_reset_revision=0,
                source_turn_id="turn-1",
                update=update,
            )
    finally:
        await store.close()


async def test_postgres_weekly_recap_projects_newest_64_delivered_turns() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    generated_at = datetime.now(UTC)
    _, _, week_start, _ = current_local_week_utc_bounds(
        timezone_id="Asia/Kolkata",
        generated_at=generated_at,
    )
    await store.open()
    try:
        for index in range(1, 71):
            await store.commit_delivered_turn(
                subject=subject,
                expected_reset_revision=0,
                turn=_turn(index),
                occurred_at=week_start + timedelta(minutes=index),
                timezone_id="Europe/London",
            )

        recap = await store.load_weekly_recap(
            subject=subject,
            timezone_id="Asia/Kolkata",
            generated_at=generated_at,
        )

        assert len(recap.records) == MAX_WEEKLY_RECORDS
        assert [record.turn_id for record in recap.records] == [
            f"turn-{index}" for index in range(70, 6, -1)
        ]
        assert [record.summary for record in recap.records] == [
            f"Talked about astronomy topic {index}." for index in range(70, 6, -1)
        ]
        assert [record.occurred_at for record in recap.records] == [
            week_start + timedelta(minutes=index) for index in range(70, 6, -1)
        ]
    finally:
        await store.close()


async def test_postgres_typed_canvas_interaction_round_trip_and_reset() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    interaction_turn = _interaction_turn(1)
    await store.open()
    try:
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=interaction_turn,
        )
        snapshot = await store.load_snapshot(
            subject=subject,
            age_band="middleChildhood",
        )

        assert len(snapshot.delivered_turns) == 1
        stored = snapshot.delivered_turns[0]
        assert stored.turn_sequence == 1
        assert stored.reset_revision == 0
        assert (
            DeliveredConversationTurn.model_validate(
                {
                    name: value
                    for name, value in stored.model_dump(by_alias=True).items()
                    if name
                    not in {"resetRevision", "turnSequence", "occurredAt", "timezoneId"}
                }
            )
            == interaction_turn
        )
        assert snapshot.stable_memories == []

        await store.reset_conversation(subject=subject)
        reset_snapshot = await store.load_snapshot(
            subject=subject,
            age_band="middleChildhood",
        )
        assert reset_snapshot.delivered_turns == []
    finally:
        await store.close()


@pytest.mark.parametrize("query", ["???", "!@#"])
async def test_postgres_search_rejects_non_lexical_queries_before_sql(
    query: str,
) -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    await store.open()
    try:
        with pytest.raises(ValueError, match="must contain searchable text"):
            await store.search_memory_candidates(
                subject=f"test:{uuid4()}",
                query=query,
            )
    finally:
        await store.close()


async def test_postgres_episode_search_excerpt_contains_late_matching_line() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    now = datetime.now(UTC)
    digest_lines = [
        (
            "Late cobalt nebula wisps were discussed with Ava."
            if index == 25
            else f"Earlier ordinary astronomy topic {index} " + "x" * 90
        )
        for index in range(1, 33)
    ]
    digest = "\n".join(digest_lines)
    assert "cobalt" not in digest[:1_200]
    connection = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
    await store.open()
    try:
        await connection.execute(
            "insert into ava_private.memory_state (subject) values (%s)",
            (subject,),
        )
        await connection.execute(
            """
            insert into ava_private.conversation_episodes (
              subject, episode_id, started_at, ended_at, timezone_id,
              digest, source_turn_ids, source_event_count, created_at
            ) values (%s, 'episode-late-match', %s, %s, 'UTC', %s, %s, 32, %s)
            """,
            (
                subject,
                now - timedelta(hours=1),
                now,
                digest,
                [f"turn-late-{index}" for index in range(1, 33)],
                now,
            ),
        )

        candidates = await store.search_memory_candidates(
            subject=subject,
            query="cobalt nebula",
        )

        assert len(candidates) == 1
        assert candidates[0].source_kind == "episode"
        assert "cobalt nebula" in candidates[0].reference_text.casefold()
        assert len(candidates[0].reference_text) <= 1_200
        assert candidates[0].source_turn_ids[-1] == "turn-late-32"
        assert candidates[0].started_at == now - timedelta(hours=1)
        assert candidates[0].ended_at == now
        assert "<b>" not in candidates[0].reference_text
    finally:
        await connection.execute(
            "delete from ava_private.memory_state where subject = %s",
            (subject,),
        )
        await connection.close()
        await store.close()


async def test_postgres_legacy_episode_history_survives_new_commits() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    now = datetime.now(UTC)
    await store.open()
    connection = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
    try:
        await connection.execute(
            "insert into ava_private.memory_state (subject) values (%s)",
            (subject,),
        )
        for index in range(130):
            ended_at = (
                now - timedelta(days=181)
                if index == 0
                else now - timedelta(hours=129 - index)
            )
            await connection.execute(
                """
                insert into ava_private.conversation_episodes (
                  subject, episode_id, started_at, ended_at, timezone_id,
                  digest, source_turn_ids, source_event_count, created_at
                ) values (%s, %s, %s, %s, 'UTC', %s, %s, 1, %s)
                """,
                (
                    subject,
                    f"episode-retention-{index}",
                    ended_at,
                    ended_at,
                    f"retention episode {index}",
                    [f"turn-retention-{index}"],
                    now,
                ),
            )

        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(999),
            occurred_at=now,
            timezone_id="UTC",
        )

        cursor = await connection.execute(
            """
            select count(*), min(ended_at)
            from ava_private.conversation_episodes
            where subject = %s
            """,
            (subject,),
        )
        count, oldest = await cursor.fetchone()
        assert count == 130
        assert oldest == now - timedelta(days=181)
        evidence = await store.search_memory_candidates(
            subject=subject, query="retention", to_instant=now - timedelta(days=180)
        )
        assert len(evidence) == 1
        assert evidence[0].source_kind == "episode"
    finally:
        await connection.execute(
            "delete from ava_private.memory_state where subject = %s",
            (subject,),
        )
        await connection.close()
        await store.close()


async def test_postgres_rejects_ambiguous_hybrid_delivered_input() -> None:
    assert TEST_DATABASE_URL is not None
    subject = f"test:{uuid4()}"
    turn_id = f"turn-{uuid4().hex[:12]}"
    connection = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
    try:
        await connection.execute(
            """
            insert into ava_private.memory_state (subject)
            values (%s)
            """,
            (subject,),
        )
        await connection.execute(
            """
            insert into ava_private.turn_receipts (subject, turn_id)
            values (%s, %s)
            """,
            (subject, turn_id),
        )
        with pytest.raises(errors.CheckViolation):
            await connection.execute(
                """
                insert into ava_private.delivered_turns (
                  subject, turn_id, input_kind, child_text, child_text_source,
                  ava_delivered_text, delivered_sample_count,
                  child_turn_summary, terminal, timezone_id,
                  canvas_scene_id, canvas_scene_revision,
                  canvas_interaction_revision, canvas_interaction_mode,
                  canvas_binding_id, canvas_target_id,
                  canvas_accessibility_label, canvas_step_index, reset_revision, turn_sequence
                ) values (
                  %s, %s, 'canvasInteraction', %s, 'transcript',
                  '', 0, 'Canvas interaction.', 'completed', 'UTC',
                  'scene-A', 1, 1, 'explore', 'binding-A', 'target-A',
                  'target-A label', null, 0, 1
                )
                """,
                (subject, turn_id, "fabricated child speech"),
            )
    finally:
        await connection.execute(
            "delete from ava_private.memory_state where subject = %s",
            (subject,),
        )
        await connection.close()


async def test_postgres_rejects_null_or_blank_canvas_interaction_fields() -> None:
    assert TEST_DATABASE_URL is not None
    subject = f"test:{uuid4()}"
    connection = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
    invalid_inputs = (
        ("scene-A", None, 1, "explore", "binding-A", "target-A", "Target A", None),
        ("scene-A", 1, None, "explore", "binding-A", "target-A", "Target A", None),
        ("scene-A", 1, 1, None, "binding-A", "target-A", "Target A", None),
        ("scene-A", 1, 1, "explore", "binding-A", "target-A", None, None),
        ("   ", 1, 1, "explore", "binding-A", "target-A", "Target A", None),
        ("scene-A", 1, 1, "explore", "   ", "target-A", "Target A", None),
        ("scene-A", 1, 1, "explore", "binding-A", "   ", "Target A", None),
        ("scene-A", 1, 1, "explore", "binding-A", "target-A", "   ", None),
        ("scene-A", 1, 1, "steps", "binding-A", "target-A", "Target A", None),
        ("scene-A", 1, 1, "steps", "binding-A", "target-A", "Target A", 13),
        ("scene-A", 1, 1, "explore", "binding-A", "target-A", "Target A", 1),
        ("scene-A", 1, 1, "select", "binding-A", "target-A", "Target A", 1),
    )
    try:
        await connection.execute(
            "insert into ava_private.memory_state (subject) values (%s)",
            (subject,),
        )
        for index, values in enumerate(invalid_inputs):
            turn_id = f"turn-invalid-canvas-{index}"
            await connection.execute(
                """
                insert into ava_private.turn_receipts (subject, turn_id)
                values (%s, %s)
                """,
                (subject, turn_id),
            )
            with pytest.raises(errors.CheckViolation):
                await connection.execute(
                    """
                    insert into ava_private.delivered_turns (
                      subject, turn_id, input_kind, child_text, child_text_source,
                      ava_delivered_text, delivered_sample_count,
                      child_turn_summary, terminal, timezone_id,
                      canvas_scene_id, canvas_scene_revision,
                      canvas_interaction_revision, canvas_interaction_mode,
                      canvas_binding_id, canvas_target_id,
                      canvas_accessibility_label, canvas_step_index, reset_revision, turn_sequence
                    ) values (
                      %s, %s, 'canvasInteraction', null, null,
                      '', 0, 'Canvas interaction.', 'completed', 'UTC',
                      %s, %s, %s, %s, %s, %s, %s, %s, 0, 1
                    )
                    """,
                    (subject, turn_id, *values),
                )
    finally:
        await connection.execute(
            "delete from ava_private.memory_state where subject = %s",
            (subject,),
        )
        await connection.close()


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
        await coordinator.close()
        snapshot = await store.load_snapshot(
            subject=subject,
            age_band=AgeBand.MIDDLE_CHILDHOOD,
        )
        assert snapshot.delivered_turns[0].ava_delivered_text == plan.speech
        assert snapshot.delivered_turns[0].terminal == "completed"
    finally:
        await coordinator.close()
        await store.close()


async def test_postgres_serializes_concurrent_delivered_turn_sequences() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    await store.open()
    try:

        async def commit(index: int):
            return await store.commit_delivered_turn(
                subject=subject,
                expected_reset_revision=0,
                turn=_turn(index),
            )

        await asyncio.gather(commit(20), commit(21))
        snapshot = await store.load_snapshot(
            subject=subject,
            age_band=AgeBand.MIDDLE_CHILDHOOD,
        )
        assert len(snapshot.delivered_turns) == 2
        assert snapshot.stable_memories == []
        assert [turn.turn_sequence for turn in snapshot.delivered_turns] == [1, 2]
        assert {turn.turn_id for turn in snapshot.delivered_turns} == {
            "turn-20",
            "turn-21",
        }
    finally:
        await store.close()


async def test_postgres_reset_wins_against_in_flight_delivered_commit() -> None:
    assert TEST_DATABASE_URL is not None
    store = _PausingFencePostgresStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    connection = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
    commit: asyncio.Task[object] | None = None
    await store.open()
    try:
        await connection.execute(
            "insert into ava_private.memory_state (subject) values (%s)",
            (subject,),
        )
        commit = asyncio.create_task(
            store.commit_delivered_turn(
                subject=subject,
                expected_reset_revision=0,
                turn=_turn(90),
            )
        )
        await store.before_subject_lock.wait()

        receipt = await store.reset_conversation(subject=subject)
        assert receipt.revision == 1
        store.allow_subject_lock.set()

        with pytest.raises(MemoryResetRevisionMismatchError):
            await commit

        snapshot = await store.load_snapshot(
            subject=subject,
            age_band=AgeBand.MIDDLE_CHILDHOOD,
        )
        assert snapshot.reset_revision == 1
        assert snapshot.delivered_turns == []
        counts_cursor = await connection.execute(
            """
            select
              (select count(*) from ava_private.turn_receipts where subject = %s)
                as receipt_count,
              (select count(*) from ava_private.delivered_turns where subject = %s)
                as turn_count
            """,
            (subject, subject),
        )
        counts = await counts_cursor.fetchone()
        assert counts == (0, 0)
    finally:
        store.allow_subject_lock.set()
        if commit is not None:
            await asyncio.gather(commit, return_exceptions=True)
        await connection.execute(
            "delete from ava_private.reset_log where subject = %s",
            (subject,),
        )
        await connection.execute(
            "delete from ava_private.memory_state where subject = %s",
            (subject,),
        )
        await connection.close()
        await store.close()


def _semantic_proposal(
    summary: str,
    *,
    base_revision: int | None = None,
    supersedes: list[str] | None = None,
) -> SemanticMemoryProposal:
    payload: dict[str, object] = {
        "disposition": "changed",
        "journalEvents": [
            {
                "family": "userPreference",
                "semanticKey": "favorite_planet",
                "summary": summary,
                "supersedesEventIds": supersedes or [],
            }
        ],
    }
    if base_revision is not None:
        payload["dialogueFrameUpdate"] = {
            "baseRevision": base_revision,
            "frame": {"activeTopics": ["Planets"]},
        }
    return SemanticMemoryProposal.model_validate(payload)


async def _semantic_journal_rows(subject: str) -> list[tuple[object, ...]]:
    assert TEST_DATABASE_URL is not None
    connection = await AsyncConnection.connect(TEST_DATABASE_URL)
    try:
        cursor = await connection.execute(
            """
            select event.event_id, event.summary,
              array(
                select source.source_turn_id
                from ava_private.semantic_journal_event_sources as source
                where source.subject = event.subject
                  and source.event_id = event.event_id
                order by source.source_turn_id
              ) as source_turn_ids,
              array(
                select edge.superseding_event_id
                from ava_private.semantic_journal_supersessions as edge
                where edge.subject = event.subject
                  and edge.superseded_event_id = event.event_id
                order by edge.superseding_event_id
              ) as superseded_by_event_ids
            from ava_private.semantic_journal_events as event
            where event.subject = %s
            order by event.created_at, event.event_id
            """,
            (subject,),
        )
        return await cursor.fetchall()
    finally:
        await connection.close()


async def test_postgres_semantic_memory_atomic_apply_and_exact_idempotency() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    await store.open()
    try:
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(1),
        )
        proposal = _semantic_proposal("The child prefers Jupiter.", base_revision=0)
        applied = await store.apply_semantic_memory_proposal(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-1",
            proposal=proposal,
        )
        retry = await store.apply_semantic_memory_proposal(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-1",
            proposal=proposal,
        )
        m1_retry = await store.apply_dialogue_frame_update(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-1",
            update=proposal.dialogue_frame_update,  # type: ignore[arg-type]
        )
        snapshot = await store.load_snapshot(
            subject=subject, age_band=AgeBand.MIDDLE_CHILDHOOD
        )
        journal = await _semantic_journal_rows(subject)

        assert applied.status == "applied"
        assert retry.status == "idempotent"
        assert m1_retry.status == "idempotent"
        assert snapshot.dialogue_frame.revision == 1
        assert len(journal) == 1
        assert journal[0][2] == ["turn-1"]
    finally:
        connection = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
        await connection.execute(
            "delete from ava_private.memory_state where subject = %s", (subject,)
        )
        await connection.close()
        await store.close()


async def test_postgres_semantic_memory_supersession_and_reset_cascade() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    await store.open()
    try:
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(1),
        )
        first = await store.apply_semantic_memory_proposal(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-1",
            proposal=_semantic_proposal("The child prefers Jupiter."),
        )
        first_id = first.journal_results[0].event.event_id
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(2),
        )
        await store.apply_semantic_memory_proposal(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-2",
            proposal=_semantic_proposal(
                "The child prefers Saturn.", supersedes=[first_id]
            ),
        )
        journal = await _semantic_journal_rows(subject)
        assert len(journal) == 2
        assert journal[0][3] == [journal[1][0]]

        receipt = await store.reset_conversation(subject=subject)
        assert receipt.revision == 1
        assert await _semantic_journal_rows(subject) == []
        snapshot = await store.load_snapshot(
            subject=subject, age_band=AgeBand.MIDDLE_CHILDHOOD
        )
        assert snapshot.dialogue_frame == DialogueFrame()
    finally:
        connection = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
        await connection.execute(
            "delete from ava_private.reset_log where subject = %s", (subject,)
        )
        await connection.execute(
            "delete from ava_private.memory_state where subject = %s", (subject,)
        )
        await connection.close()
        await store.close()


async def test_postgres_semantic_journal_search_parity_supersession_and_reset() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    await store.open()
    try:
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(1),
        )
        first = await store.apply_semantic_memory_proposal(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-1",
            proposal=_semantic_proposal("The child prefers volcanic Jupiter moons."),
        )
        first_id = first.journal_results[0].event.event_id

        # 1. Search retrieves active journal record
        initial_search = await store.search_memory_candidates(
            subject=subject,
            query="volcanic moons",
        )
        assert len(initial_search) == 1
        assert initial_search[0].source_id == first_id
        assert (
            initial_search[0].source_kind == MemoryCandidateSourceKind.SEMANTIC_JOURNAL
        )
        assert initial_search[0].source_kind == "semanticJournal"
        assert "volcanic Jupiter moons" in initial_search[0].reference_text
        assert initial_search[0].source_turn_ids == ["turn-1"]
        assert initial_search[0].started_at is not None
        assert initial_search[0].ended_at == initial_search[0].started_at
        assert initial_search[0].timezone_ids == ["UTC"]

        # 2. Supersede with second event
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(2),
        )
        second = await store.apply_semantic_memory_proposal(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-2",
            proposal=_semantic_proposal(
                "The child prefers Saturn icy ring systems.",
                supersedes=[first_id],
            ),
        )
        second_id = second.journal_results[0].event.event_id

        # Superseded first event is excluded from search
        superseded_search = await store.search_memory_candidates(
            subject=subject,
            query="volcanic moons",
        )
        assert not any(c.source_id == first_id for c in superseded_search)

        # Second active event is retrieved
        second_search = await store.search_memory_candidates(
            subject=subject,
            query="Saturn icy",
        )
        assert len(second_search) == 1
        assert second_search[0].source_id == second_id
        assert (
            second_search[0].source_kind == MemoryCandidateSourceKind.SEMANTIC_JOURNAL
        )

        # 3. Reset conversation isolates old records
        receipt = await store.reset_conversation(subject=subject)
        assert receipt.revision == 1

        post_reset_search = await store.search_memory_candidates(
            subject=subject,
            query="Saturn icy",
        )
        assert len(post_reset_search) == 0
    finally:
        connection = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
        await connection.execute(
            "delete from ava_private.reset_log where subject = %s", (subject,)
        )
        await connection.execute(
            "delete from ava_private.memory_state where subject = %s", (subject,)
        )
        await connection.close()
        await store.close()


async def test_postgres_semantic_memory_allows_immutable_a_b_a_recurrence() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    await store.open()
    try:
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(1),
        )
        first = await store.apply_semantic_memory_proposal(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-1",
            proposal=_semantic_proposal("The child prefers Jupiter."),
        )
        first_id = first.journal_results[0].event.event_id

        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(2),
        )
        second = await store.apply_semantic_memory_proposal(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-2",
            proposal=_semantic_proposal(
                "The child prefers Saturn.", supersedes=[first_id]
            ),
        )
        second_id = second.journal_results[0].event.event_id

        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(3),
        )
        third = await store.apply_semantic_memory_proposal(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-3",
            proposal=_semantic_proposal(
                "The child prefers Jupiter.", supersedes=[second_id]
            ),
        )
        third_id = third.journal_results[0].event.event_id

        journal = {
            str(row[0]): {
                "summary": row[1],
                "source_turn_ids": row[2],
                "superseded_by_event_ids": row[3],
            }
            for row in await _semantic_journal_rows(subject)
        }
        assert third.journal_results[0].disposition == "created"
        assert first_id != third_id
        assert len(journal) == 3
        assert journal[first_id] == {
            "summary": "The child prefers Jupiter.",
            "source_turn_ids": ["turn-1"],
            "superseded_by_event_ids": [second_id],
        }
        assert journal[second_id] == {
            "summary": "The child prefers Saturn.",
            "source_turn_ids": ["turn-2"],
            "superseded_by_event_ids": [third_id],
        }
        assert journal[third_id] == {
            "summary": "The child prefers Jupiter.",
            "source_turn_ids": ["turn-3"],
            "superseded_by_event_ids": [],
        }
    finally:
        connection = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
        await connection.execute(
            "delete from ava_private.memory_state where subject = %s", (subject,)
        )
        await connection.close()
        await store.close()


async def test_postgres_semantic_conflict_rolls_back_frame_and_receipt() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    await store.open()
    try:
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(1),
        )
        await store.apply_semantic_memory_proposal(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-1",
            proposal=_semantic_proposal("The child prefers Jupiter."),
        )
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(2),
        )
        with pytest.raises(SemanticJournalConflictError):
            await store.apply_semantic_memory_proposal(
                subject=subject,
                expected_reset_revision=0,
                source_turn_id="turn-2",
                proposal=_semantic_proposal(
                    "The child prefers Saturn.", base_revision=0
                ),
            )
        snapshot = await store.load_snapshot(
            subject=subject, age_band=AgeBand.MIDDLE_CHILDHOOD
        )
        journal = await _semantic_journal_rows(subject)
        assert snapshot.dialogue_frame.revision == 0
        assert len(journal) == 1
        connection = await AsyncConnection.connect(TEST_DATABASE_URL)
        try:
            cursor = await connection.execute(
                """
                select count(*)
                from ava_private.semantic_memory_receipts
                where subject = %s and source_turn_id = 'turn-2'
                """,
                (subject,),
            )
            assert await cursor.fetchone() == (0,)
        finally:
            await connection.close()
    finally:
        connection = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
        await connection.execute(
            "delete from ava_private.memory_state where subject = %s", (subject,)
        )
        await connection.close()
        await store.close()


async def test_postgres_migration_backfills_legacy_ties_then_allocates_forward() -> (
    None
):
    assert TEST_DATABASE_URL is not None
    database = "ava_pass3_migration_" + uuid4().hex
    url = make_conninfo(TEST_DATABASE_URL, dbname=database)
    admin = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
    await admin.execute(sql.SQL("create database {}").format(sql.Identifier(database)))
    migrations = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
    timeline = migrations / "202609140001_add_durable_turn_timeline.sql"
    subject = "test:legacy-migration"
    when = datetime(2025, 2, 1, tzinfo=UTC)
    ids = [f"turn-{uuid4()}" for _ in range(4)]
    store = PostgresAuthoritativeMemoryStore(url)
    try:
        async with await AsyncConnection.connect(url, autocommit=True) as connection:
            for migration in sorted(migrations.glob("*.sql")):
                # The July public/auth migration is outside the private memory schema.
                if migration.name >= "202608" and migration.name < timeline.name:
                    await connection.execute(migration.read_text())
            await connection.execute(
                "insert into ava_private.memory_state(subject) values (%s)", (subject,)
            )
            for turn_id in ids:
                await connection.execute(
                    "insert into ava_private.turn_receipts(subject, turn_id) values (%s, %s)",
                    (subject, turn_id),
                )
                await connection.execute(
                    """
                    insert into ava_private.delivered_turns
                    (subject, turn_id, child_text, child_text_source, ava_delivered_text,
                     delivered_sample_count, child_turn_summary, terminal, occurred_at, timezone_id)
                    values (%s, %s, 'Legacy astronomy', 'transcript', 'Heard prefix',
                            24000, 'Legacy astronomy', 'completed', %s, 'UTC')
                """,
                    (subject, turn_id, when),
                )
            await connection.execute(timeline.read_text())
            await connection.execute(timeline.read_text())
        await store.open()
        before = await store.load_snapshot(
            subject=subject, age_band=AgeBand.MIDDLE_CHILDHOOD
        )
        assert [(t.turn_id, t.turn_sequence) for t in before.delivered_turns] == list(
            zip(sorted(ids), range(1, 5))
        )
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(99),
            occurred_at=when - timedelta(days=1),
            timezone_id="UTC",
        )
        after = await store.load_snapshot(
            subject=subject, age_band=AgeBand.MIDDLE_CHILDHOOD
        )
        assert after.delivered_turns[-1].turn_id == "turn-99"
        assert after.delivered_turns[-1].turn_sequence == 5
        assert (
            after.delivered_turns[-1].occurred_at < after.delivered_turns[0].occurred_at
        )
    finally:
        await store.close()
        await admin.execute(
            sql.SQL("drop database {}").format(sql.Identifier(database))
        )
        await admin.close()


async def test_postgres_search_order_source_time_and_provenance_dedup() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    base = datetime(2024, 1, 1, tzinfo=UTC)
    await store.open()
    try:
        for turn_id, day in (("turn-z", 2), ("turn-m", 0), ("turn-a", 0)):
            turn = _turn(1).model_copy(
                update={
                    "turn_id": turn_id,
                    "child_text": "nebula",
                    "ava_delivered_text": "nebula",
                    "child_turn_summary": "nebula",
                }
            )
            await store.commit_delivered_turn(
                subject=subject,
                expected_reset_revision=0,
                turn=turn,
                occurred_at=base + timedelta(days=day),
                timezone_id="Asia/Kolkata",
            )
        for order, expected in (
            ("bestMatch", ["turn-z", "turn-a", "turn-m"]),
            ("newest", ["turn-z", "turn-a", "turn-m"]),
            ("oldest", ["turn-a", "turn-m", "turn-z"]),
        ):
            candidates = await store.search_memory_candidates(
                subject=subject, query="nebula", order=order
            )
            assert [c.source_id for c in candidates] == expected
        filtered = await store.search_memory_candidates(
            subject=subject,
            query="nebula",
            from_instant=base,
            to_instant=base + timedelta(days=1),
            order="newest",
        )
        assert [c.source_id for c in filtered] == ["turn-a", "turn-m"]
        await store.apply_semantic_memory_proposal(
            subject=subject,
            expected_reset_revision=0,
            source_turn_id="turn-z",
            proposal=_semantic_proposal("nebula nebula nebula nebula nebula"),
        )
        candidates = await store.search_memory_candidates(
            subject=subject, query="nebula", order="newest"
        )
        assert len(candidates) == 3
        represented: set[str] = set()
        for candidate in candidates:
            assert represented.isdisjoint(candidate.source_turn_ids)
            represented.update(candidate.source_turn_ids)
        assert represented == {"turn-z", "turn-a", "turn-m"}
        journal = next(c for c in candidates if c.source_kind == "semanticJournal")
        assert journal.started_at == journal.ended_at == base + timedelta(days=2)
        assert journal.timezone_ids == ["Asia/Kolkata"]
        # Simulate a pre-migration episode-only source, never fabricate exact text.
        async with await AsyncConnection.connect(
            TEST_DATABASE_URL, autocommit=True
        ) as connection:
            await connection.execute(
                """
                insert into ava_private.conversation_episodes
                (subject, episode_id, started_at, ended_at, timezone_id, digest,
                 source_turn_ids, source_event_count, created_at)
                values (%s, 'episode-legacy-nebula', %s, %s, 'Europe/Paris',
                        'nebula digest', array['turn-z'], 1, %s)
            """,
                (subject, base + timedelta(days=2), base + timedelta(days=3), base),
            )
            await connection.execute(
                "delete from ava_private.delivered_turns where subject = %s and turn_id = 'turn-z'",
                (subject,),
            )
        legacy = await store.search_memory_candidates(
            subject=subject, query="nebula", order="newest"
        )
        historical = next(c for c in legacy if c.source_kind == "semanticJournal")
        assert historical.started_at == base + timedelta(days=2)
        assert historical.ended_at == base + timedelta(days=3)
        assert historical.timezone_ids == ["Europe/Paris"]
        assert sum("turn-z" in c.source_turn_ids for c in legacy) == 1
    finally:
        await store.close()


async def test_postgres_current_supersession_preserves_historical_raw_truth() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    base = datetime(2024, 1, 1, tzinfo=UTC)
    await store.open()
    try:
        previous = None
        for index, color in enumerate(("blue", "green"), start=1):
            text = f"My favorite color is {color}."
            await store.commit_delivered_turn(
                subject=subject,
                expected_reset_revision=0,
                turn=_turn(index).model_copy(
                    update={"child_text": text, "child_turn_summary": text}
                ),
                occurred_at=base + timedelta(days=index),
                timezone_id="UTC",
            )
            proposal = SemanticMemoryProposal.model_validate(
                {
                    "disposition": "changed",
                    "journalEvents": [
                        {
                            "family": "userPreference",
                            "semanticKey": "favorite_color",
                            "summary": text,
                            "supersedesEventIds": [previous] if previous else [],
                        }
                    ],
                }
            )
            result = await store.apply_semantic_memory_proposal(
                subject=subject,
                expected_reset_revision=0,
                source_turn_id=f"turn-{index}",
                proposal=proposal,
            )
            previous = result.journal_results[0].event.event_id
        active = await store.get_active_semantic_journal_event(
            subject=subject,
            expected_reset_revision=0,
            identity_key="userPreference:favorite_color",
        )
        assert active is not None and "green" in active.summary
        historical = await store.search_memory_candidates(
            subject=subject,
            query="blue",
            from_instant=base + timedelta(days=1),
            to_instant=base + timedelta(days=2),
        )
        assert len(historical) == 1
        assert (
            historical[0].source_kind == "rawEvent"
            and "blue" in historical[0].reference_text
        )
    finally:
        await store.close()


async def test_postgres_durable_sequence_restart_reset_and_retention() -> None:
    assert TEST_DATABASE_URL is not None
    subject = f"test:{uuid4()}"
    when = datetime(2025, 1, 1, tzinfo=UTC)
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    await store.open()
    ids = [f"turn-{uuid4()}" for _ in range(258)]
    try:
        for index, turn_id in enumerate(ids):
            await store.commit_delivered_turn(
                subject=subject,
                expected_reset_revision=0,
                turn=_turn(index).model_copy(update={"turn_id": turn_id}),
                occurred_at=when,
                timezone_id="Asia/Kolkata",
            )
        await store.close()
        store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
        await store.open()
        snapshot = await store.load_snapshot(
            subject=subject, age_band=AgeBand.MIDDLE_CHILDHOOD
        )
        assert [t.turn_sequence for t in snapshot.delivered_turns] == list(
            range(251, 259)
        )
        assert [t.turn_id for t in snapshot.delivered_turns] == ids[-8:]
        assert all(
            t.occurred_at == when and t.timezone_id == "Asia/Kolkata"
            for t in snapshot.delivered_turns
        )
        async with await AsyncConnection.connect(TEST_DATABASE_URL) as connection:
            cursor = await connection.execute(
                "select turn_id, turn_sequence from ava_private.delivered_turns where subject = %s order by turn_sequence",
                (subject,),
            )
            rows = await cursor.fetchall()
            assert rows == list(zip(ids, range(1, 259)))
            cursor = await connection.execute(
                "select count(*) from ava_private.conversation_episodes where subject = %s",
                (subject,),
            )
            assert await cursor.fetchone() == (0,)
        recalled = await store.search_memory_candidates(
            subject=subject,
            query="astronomy",
            from_instant=when,
            to_instant=when + timedelta(seconds=1),
        )
        assert recalled and all(c.source_kind == "rawEvent" for c in recalled)
        await store.reset_conversation(subject=subject)
        with pytest.raises(MemoryResetRevisionMismatchError):
            await store.commit_delivered_turn(
                subject=subject,
                expected_reset_revision=0,
                turn=_turn(999),
            )
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=1,
            turn=_turn(999),
            occurred_at=when,
            timezone_id="UTC",
        )
        fresh = await store.load_snapshot(
            subject=subject, age_band=AgeBand.MIDDLE_CHILDHOOD
        )
        assert [(t.reset_revision, t.turn_sequence) for t in fresh.delivered_turns] == [
            (1, 1)
        ]
    finally:
        await store.close()
