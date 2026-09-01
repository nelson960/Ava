from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, errors

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
    AvaMemoryProposal,
    ConversationEpisode,
    DeliveredConversationTurn,
    MemoryResetRevisionMismatchError,
    RAW_RETENTION_HIGH_WATER,
    RAW_RETENTION_LOW_WATER,
)
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


class _FailingEpisodeInsertPostgresStore(PostgresAuthoritativeMemoryStore):
    async def _insert_episode(  # type: ignore[no-untyped-def]
        self,
        connection,
        *,
        episode: ConversationEpisode,
    ) -> None:
        del connection, episode
        raise RuntimeError("injected episode insert failure")


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


async def test_postgres_memory_round_trip_does_not_compact_at_event_nine() -> None:
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
                expected_reset_revision=0,
                turn=_turn(index),
                proposal=proposal,
                memory_id=memory_id,
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
        assert [memory.id for memory in snapshot.stable_memories] == ["memory-1"]
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


async def test_postgres_typed_canvas_interaction_round_trip_and_reset() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    interaction_turn = _interaction_turn(1)
    await store.open()
    try:
        decision = await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=interaction_turn,
            proposal=AvaMemoryProposal.model_validate(
                {
                    "kind": "interest",
                    "summary": "A proposal without speech evidence.",
                    "evidence": "target-1 label",
                    "confidence": 0.99,
                }
            ),
            memory_id="memory-from-interaction",
        )
        snapshot = await store.load_snapshot(
            subject=subject,
            age_band="middleChildhood",
        )

        assert decision.accepted is False
        assert "speech transcript" in decision.reason
        assert snapshot.delivered_turns == [interaction_turn]
        assert snapshot.stable_memories == []

        await store.reset_conversation(subject=subject)
        reset_snapshot = await store.load_snapshot(
            subject=subject,
            age_band="middleChildhood",
        )
        assert reset_snapshot.delivered_turns == []
    finally:
        await store.close()


async def test_postgres_event_257_atomically_compacts_to_128_raw_events() -> None:
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    base = datetime.now(UTC) - timedelta(days=1)
    await store.open()
    try:
        for index in range(1, RAW_RETENTION_HIGH_WATER + 2):
            turn = _turn(index)
            if index == 1:
                turn = DeliveredConversationTurn.model_validate(
                    {
                        "turnId": "turn-1",
                        "childText": "Saturn has beautiful rings.",
                        "avaDeliveredText": "Saturn's rings contain ice and rock.",
                        "deliveredSampleCount": 24_000,
                        "childTurnSummary": "Discussed Saturn's icy rings.",
                        "terminal": "completed",
                    }
                )
            await store.commit_delivered_turn(
                subject=subject,
                expected_reset_revision=0,
                turn=turn,
                proposal=None,
                memory_id=None,
                occurred_at=base + timedelta(minutes=index),
                timezone_id=("Asia/Kolkata" if index <= 48 else "America/New_York"),
            )

        connection = await AsyncConnection.connect(TEST_DATABASE_URL)
        try:
            raw_cursor = await connection.execute(
                """
                select turn_id
                from ava_private.delivered_turns
                where subject = %s
                order by occurred_at asc, turn_id asc
                """,
                (subject,),
            )
            episode_cursor = await connection.execute(
                """
                select episode_id, source_turn_ids, source_event_count,
                       timezone_id, digest
                from ava_private.conversation_episodes
                where subject = %s
                order by started_at asc, episode_id asc
                """,
                (subject,),
            )
            raw_rows = await raw_cursor.fetchall()
            episode_rows = await episode_cursor.fetchall()
        finally:
            await connection.close()

        assert len(raw_rows) == RAW_RETENTION_LOW_WATER
        assert raw_rows[0][0] == "turn-130"
        assert [row[2] for row in episode_rows] == [32, 32, 32, 32, 1]
        source_turn_ids = [turn_id for row in episode_rows for turn_id in row[1]]
        assert source_turn_ids == [f"turn-{index}" for index in range(1, 130)]
        assert all(len(row[1]) <= 32 for row in episode_rows)
        assert episode_rows[1][3] == "Asia/Kolkata"
        combined_digest = "\n".join(row[4] for row in episode_rows)
        assert "turn=" not in combined_digest
        assert "occurredAt=" not in combined_digest
        assert "timezone=" not in combined_digest
        assert "Asia/Kolkata" not in combined_digest
        assert "America/New_York" not in combined_digest

        snapshot = await store.load_snapshot(
            subject=subject,
            age_band=AgeBand.MIDDLE_CHILDHOOD,
        )
        assert [turn.turn_id for turn in snapshot.delivered_turns] == [
            f"turn-{index}" for index in range(250, 258)
        ]

        episode_candidates = await store.search_memory_candidates(
            subject=subject,
            query="Saturn icy rings",
            from_instant=base - timedelta(seconds=1),
            to_instant=base + timedelta(minutes=2),
        )
        assert len(episode_candidates) == 1
        assert episode_candidates[0].source_kind == "episode"
        assert "turn-1" in episode_candidates[0].source_turn_ids

        connection = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
        try:
            await connection.execute(
                """
                insert into ava_private.delivered_turns (
                  subject, turn_id, input_kind, child_text, child_text_source,
                  ava_delivered_text, delivered_sample_count, child_turn_summary,
                  terminal, occurred_at, timezone_id
                ) values (
                  %s, 'turn-1', 'speech', 'Saturn has beautiful rings.',
                  'transcript', 'Saturn rings contain ice.', 24000,
                  'Discussed Saturn icy rings.', 'completed', %s, 'Asia/Kolkata'
                )
                """,
                (subject, base),
            )
            overlapping = await store.search_memory_candidates(
                subject=subject,
                query="Saturn icy rings",
            )
            assert [candidate.source_kind for candidate in overlapping] == ["rawEvent"]
            await connection.execute(
                "delete from ava_private.delivered_turns where subject = %s and turn_id = 'turn-1'",
                (subject,),
            )
            await connection.execute(
                "delete from ava_private.weekly_records where subject = %s and turn_id = 'turn-1'",
                (subject,),
            )
            await connection.execute(
                """
                update ava_private.turn_receipts
                set committed_at = %s
                where subject = %s and turn_id = 'turn-1'
                """,
                (datetime.now(UTC) - timedelta(days=365), subject),
            )
            async with store._connection() as pooled:
                async with pooled.transaction():
                    await store._prune_retention(pooled, subject=subject)
            receipt_cursor = await connection.execute(
                """
                select count(*)
                from ava_private.turn_receipts
                where subject = %s and turn_id = 'turn-1'
                """,
                (subject,),
            )
            assert await receipt_cursor.fetchone() == (1,)
        finally:
            await connection.close()

        await store.reset_conversation(subject=subject)
        reset_connection = await AsyncConnection.connect(TEST_DATABASE_URL)
        try:
            reset_cursor = await reset_connection.execute(
                "select count(*) from ava_private.conversation_episodes where subject = %s",
                (subject,),
            )
            assert await reset_cursor.fetchone() == (0,)
        finally:
            await reset_connection.close()
    finally:
        await store.close()


async def test_postgres_repeated_high_water_compaction_preserves_exact_partition() -> (
    None
):
    assert TEST_DATABASE_URL is not None
    store = PostgresAuthoritativeMemoryStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    base = datetime.now(UTC) - timedelta(days=2)

    async def retained_partition() -> tuple[list[str], list[list[str]]]:
        connection = await AsyncConnection.connect(TEST_DATABASE_URL)
        try:
            raw_cursor = await connection.execute(
                """
                select turn_id
                from ava_private.delivered_turns
                where subject = %s
                order by occurred_at asc, turn_id asc
                """,
                (subject,),
            )
            episode_cursor = await connection.execute(
                """
                select source_turn_ids
                from ava_private.conversation_episodes
                where subject = %s
                order by started_at asc, episode_id asc
                """,
                (subject,),
            )
            raw_ids = [row[0] for row in await raw_cursor.fetchall()]
            episode_batches = [list(row[0]) for row in await episode_cursor.fetchall()]
            return raw_ids, episode_batches
        finally:
            await connection.close()

    def assert_exact_partition(
        *,
        total_turns: int,
        expected_raw_start: int,
        raw_ids: list[str],
        episode_batches: list[list[str]],
    ) -> None:
        episode_ids = [turn_id for batch in episode_batches for turn_id in batch]
        retained_ids = [*episode_ids, *raw_ids]
        expected_ids = [f"turn-{index}" for index in range(1, total_turns + 1)]
        assert retained_ids == expected_ids
        assert len(retained_ids) == len(set(retained_ids))
        assert raw_ids == [
            f"turn-{index}" for index in range(expected_raw_start, total_turns + 1)
        ]
        assert episode_ids == [
            f"turn-{index}" for index in range(1, expected_raw_start)
        ]
        assert all(1 <= len(batch) <= 32 for batch in episode_batches)

    await store.open()
    try:
        for index in range(1, RAW_RETENTION_HIGH_WATER + 2):
            await store.commit_delivered_turn(
                subject=subject,
                expected_reset_revision=0,
                turn=_turn(index),
                proposal=None,
                memory_id=None,
                occurred_at=base + timedelta(minutes=index),
                timezone_id="UTC",
            )

        raw_ids, episode_batches = await retained_partition()
        assert len(raw_ids) == RAW_RETENTION_LOW_WATER
        assert_exact_partition(
            total_turns=257,
            expected_raw_start=130,
            raw_ids=raw_ids,
            episode_batches=episode_batches,
        )

        for index in range(258, 386):
            await store.commit_delivered_turn(
                subject=subject,
                expected_reset_revision=0,
                turn=_turn(index),
                proposal=None,
                memory_id=None,
                occurred_at=base + timedelta(minutes=index),
                timezone_id="UTC",
            )

        raw_ids, episode_batches = await retained_partition()
        assert len(raw_ids) == RAW_RETENTION_HIGH_WATER
        assert_exact_partition(
            total_turns=385,
            expected_raw_start=130,
            raw_ids=raw_ids,
            episode_batches=episode_batches,
        )

        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(386),
            proposal=None,
            memory_id=None,
            occurred_at=base + timedelta(minutes=386),
            timezone_id="UTC",
        )

        raw_ids, episode_batches = await retained_partition()
        assert len(raw_ids) == RAW_RETENTION_LOW_WATER
        assert_exact_partition(
            total_turns=386,
            expected_raw_start=259,
            raw_ids=raw_ids,
            episode_batches=episode_batches,
        )
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


async def test_postgres_episode_insert_failure_rolls_back_new_raw_event() -> None:
    assert TEST_DATABASE_URL is not None
    store = _FailingEpisodeInsertPostgresStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    await store.open()
    try:
        for index in range(1, RAW_RETENTION_HIGH_WATER + 1):
            await store.commit_delivered_turn(
                subject=subject,
                expected_reset_revision=0,
                turn=_turn(index),
                proposal=None,
                memory_id=None,
                timezone_id="UTC",
            )

        with pytest.raises(RuntimeError, match="injected episode insert failure"):
            await store.commit_delivered_turn(
                subject=subject,
                expected_reset_revision=0,
                turn=_turn(RAW_RETENTION_HIGH_WATER + 1),
                proposal=None,
                memory_id=None,
                timezone_id="UTC",
            )

        connection = await AsyncConnection.connect(TEST_DATABASE_URL)
        try:
            cursor = await connection.execute(
                """
                select
                  (select count(*) from ava_private.delivered_turns where subject = %s),
                  (select count(*) from ava_private.conversation_episodes where subject = %s),
                  (select count(*) from ava_private.turn_receipts where subject = %s)
                """,
                (subject, subject, subject),
            )
            assert await cursor.fetchone() == (
                RAW_RETENTION_HIGH_WATER,
                0,
                RAW_RETENTION_HIGH_WATER,
            )
        finally:
            await connection.close()
    finally:
        await store.close()


async def test_postgres_episode_retention_enforces_age_and_count_bounds() -> None:
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

        async with store._connection() as pooled:
            async with pooled.transaction():
                await store._prune_retention(pooled, subject=subject)

        cursor = await connection.execute(
            """
            select count(*), min(ended_at)
            from ava_private.conversation_episodes
            where subject = %s
            """,
            (subject,),
        )
        count, oldest = await cursor.fetchone()
        assert count == 128
        assert oldest >= now - timedelta(days=180)
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
                  canvas_accessibility_label, canvas_step_index
                ) values (
                  %s, %s, 'canvasInteraction', %s, 'transcript',
                  '', 0, 'Canvas interaction.', 'completed', 'UTC',
                  'scene-A', 1, 1, 'explore', 'binding-A', 'target-A',
                  'target-A label', null
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
                      canvas_accessibility_label, canvas_step_index
                    ) values (
                      %s, %s, 'canvasInteraction', null, null,
                      '', 0, 'Canvas interaction.', 'completed', 'UTC',
                      %s, %s, %s, %s, %s, %s, %s, %s
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


async def test_postgres_serializes_concurrent_semantic_memory_proposals() -> None:
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
                proposal=None,
                memory_id=None,
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
                as turn_count,
              (select count(*) from ava_private.weekly_records where subject = %s)
                as weekly_count
            """,
            (subject, subject, subject),
        )
        counts = await counts_cursor.fetchone()
        assert counts == (0, 0, 0)
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


async def test_postgres_reset_wins_against_in_flight_summary_write() -> None:
    assert TEST_DATABASE_URL is not None
    store = _PausingFencePostgresStore(TEST_DATABASE_URL)
    subject = f"test:{uuid4()}"
    connection = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
    write: asyncio.Task[None] | None = None
    await store.open()
    try:
        await connection.execute(
            "insert into ava_private.memory_state (subject) values (%s)",
            (subject,),
        )
        write = asyncio.create_task(
            store.set_session_summary(
                subject=subject,
                expected_reset_revision=0,
                summary="A stale pre-reset summary.",
            )
        )
        await store.before_subject_lock.wait()

        receipt = await store.reset_conversation(subject=subject)
        assert receipt.revision == 1
        store.allow_subject_lock.set()

        with pytest.raises(MemoryResetRevisionMismatchError):
            await write
        snapshot = await store.load_snapshot(
            subject=subject,
            age_band=AgeBand.MIDDLE_CHILDHOOD,
        )
        assert snapshot.reset_revision == 1
        assert snapshot.current_session_summary is None
    finally:
        store.allow_subject_lock.set()
        if write is not None:
            await asyncio.gather(write, return_exceptions=True)
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
