from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import AsyncIterator, cast

from psycopg import AsyncConnection, Error as PsycopgError, errors
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from ..protocol import AgeBand
from .memory import (
    MAX_DELIVERED_TURNS,
    MAX_STABLE_MEMORIES,
    MAX_WEEKLY_RECORDS,
    AvaMemoryProposal,
    AuthoritativeMemorySnapshot,
    ChildTurnTextSource,
    ConversationResetReceipt,
    DeliveredConversationTurn,
    MemoryProposalDecision,
    WeeklyConversationRecap,
    WeeklyConversationRecord,
    _validate_aware_datetime,
    _validate_subject,
    _validate_weekly_summary,
    compact_session_summary,
    evaluate_memory_proposal,
    _validate_optional_summary,
)


class PostgresMemoryStoreError(RuntimeError):
    """Raised when durable protocol-v3 memory cannot complete an operation."""


class PostgresAuthoritativeMemoryStore:
    """Portable PostgreSQL store used locally and by Supabase PostgreSQL.

    The schema lives outside ``public`` so Supabase PostgREST never exposes the
    tables accidentally. All writes that affect a delivered turn, stable
    memory, compaction, and weekly recap are committed in one transaction.
    """

    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 4,
        pool_timeout_seconds: float = 5,
        weekly_retention_days: int = 56,
    ) -> None:
        normalized_url = database_url.strip()
        if not normalized_url:
            raise ValueError("PostgreSQL database URL cannot be empty")
        if not 0 <= min_pool_size <= max_pool_size <= 32:
            raise ValueError("PostgreSQL pool sizes must satisfy 0 <= min <= max <= 32")
        if not 1 <= pool_timeout_seconds <= 30:
            raise ValueError("PostgreSQL pool timeout must be between 1 and 30 seconds")
        if not 7 <= weekly_retention_days <= 365:
            raise ValueError("weekly retention must be between 7 and 365 days")
        self._pool = cast(
            AsyncConnectionPool[AsyncConnection[dict[str, object]]],
            AsyncConnectionPool(
                conninfo=normalized_url,
                min_size=min_pool_size,
                max_size=max_pool_size,
                timeout=pool_timeout_seconds,
                open=False,
                kwargs={"row_factory": dict_row},
            ),
        )
        self._pool_timeout_seconds = pool_timeout_seconds
        self._weekly_retention_days = weekly_retention_days
        self._opened = False

    async def open(self) -> None:
        if self._opened:
            return
        try:
            await self._pool.open(
                wait=True,
                timeout=self._pool_timeout_seconds,
            )
            async with self._pool.connection(
                timeout=self._pool_timeout_seconds
            ) as connection:
                cursor = await connection.execute(
                    """
                    select version
                    from ava_private.schema_migrations
                    where name = 'realtime_memory_v3'
                    """
                )
                row = await cursor.fetchone()
        except (PsycopgError, PoolTimeout) as error:
            await self._pool.close()
            raise PostgresMemoryStoreError(
                "PostgreSQL memory is unavailable or its v3 migration is missing"
            ) from error
        if row is None or cast(int, row["version"]) != 1:
            await self._pool.close()
            raise PostgresMemoryStoreError(
                "PostgreSQL memory schema realtime_memory_v3 version 1 is required"
            )
        self._opened = True

    async def close(self) -> None:
        if not self._opened:
            return
        self._opened = False
        await self._pool.close()

    async def ping(self) -> None:
        async with self._connection() as connection:
            cursor = await connection.execute("select 1 as ready")
            row = await cursor.fetchone()
        if row is None or cast(int, row["ready"]) != 1:
            raise PostgresMemoryStoreError(
                "PostgreSQL memory readiness check returned an invalid result"
            )

    async def load_snapshot(
        self,
        *,
        subject: str,
        age_band: AgeBand,
    ) -> AuthoritativeMemorySnapshot:
        normalized_subject = _validate_subject(subject)
        async with self._connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "set transaction isolation level repeatable read read only"
                )
                state_cursor = await connection.execute(
                    """
                    select current_session_summary
                    from ava_private.memory_state
                    where subject = %s
                    """,
                    (normalized_subject,),
                )
                state = await state_cursor.fetchone()
                turns_cursor = await connection.execute(
                    """
                    select turn_id, child_text, child_text_source,
                           ava_delivered_text, delivered_sample_count,
                           child_turn_summary, terminal
                    from ava_private.delivered_turns
                    where subject = %s
                    order by occurred_at asc, turn_id asc
                    limit %s
                    """,
                    (normalized_subject, MAX_DELIVERED_TURNS),
                )
                turn_rows = await turns_cursor.fetchall()
                memories_cursor = await connection.execute(
                    """
                    select memory_id, kind, summary, evidence, source_turn_id
                    from ava_private.stable_memories
                    where subject = %s
                    order by created_at asc, memory_id asc
                    limit %s
                    """,
                    (normalized_subject, MAX_STABLE_MEMORIES),
                )
                memory_rows = await memories_cursor.fetchall()

        return AuthoritativeMemorySnapshot.model_validate(
            {
                "ageBand": age_band,
                "currentSessionSummary": (
                    state["current_session_summary"] if state is not None else None
                ),
                "deliveredTurns": [
                    {
                        "turnId": row["turn_id"],
                        "childText": row["child_text"],
                        "childTextSource": row["child_text_source"],
                        "avaDeliveredText": row["ava_delivered_text"],
                        "deliveredSampleCount": row["delivered_sample_count"],
                        "childTurnSummary": row["child_turn_summary"],
                        "terminal": row["terminal"],
                    }
                    for row in turn_rows
                ],
                "stableMemories": [
                    {
                        "id": row["memory_id"],
                        "kind": row["kind"],
                        "summary": row["summary"],
                        "evidence": row["evidence"],
                        "sourceTurnId": row["source_turn_id"],
                    }
                    for row in memory_rows
                ],
            }
        )

    async def commit_delivered_turn(
        self,
        *,
        subject: str,
        turn: DeliveredConversationTurn,
        proposal: AvaMemoryProposal | None,
        memory_id: str | None,
        occurred_at: datetime | None = None,
    ) -> MemoryProposalDecision:
        normalized_subject = _validate_subject(subject)
        normalized_occurred_at = _validate_aware_datetime(
            occurred_at or datetime.now(UTC),
            field_name="occurred_at",
        )
        validated_turn = DeliveredConversationTurn.model_validate(
            turn.model_dump(mode="json", by_alias=True)
        )
        weekly_summary = _validate_weekly_summary(validated_turn.child_turn_summary)
        decision = evaluate_memory_proposal(
            proposal=proposal,
            child_text=validated_turn.child_text,
            source_turn_id=validated_turn.turn_id,
            memory_id=memory_id,
            child_text_source=ChildTurnTextSource(validated_turn.child_text_source),
        )

        async with self._connection() as connection:
            try:
                async with connection.transaction():
                    await self._ensure_subject(connection, normalized_subject)
                    await self._lock_subject(connection, normalized_subject)
                    await connection.execute(
                        """
                        insert into ava_private.turn_receipts (subject, turn_id)
                        values (%s, %s)
                        """,
                        (normalized_subject, validated_turn.turn_id),
                    )
                    await connection.execute(
                        """
                        insert into ava_private.delivered_turns (
                          subject, turn_id, child_text, child_text_source,
                          ava_delivered_text, delivered_sample_count,
                          child_turn_summary, terminal, occurred_at
                        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            normalized_subject,
                            validated_turn.turn_id,
                            validated_turn.child_text,
                            str(validated_turn.child_text_source),
                            validated_turn.ava_delivered_text,
                            validated_turn.delivered_sample_count,
                            validated_turn.child_turn_summary,
                            str(validated_turn.terminal),
                            normalized_occurred_at,
                        ),
                    )
                    await connection.execute(
                        """
                        insert into ava_private.weekly_records
                          (subject, turn_id, summary, occurred_at)
                        values (%s, %s, %s, %s)
                        """,
                        (
                            normalized_subject,
                            validated_turn.turn_id,
                            weekly_summary,
                            normalized_occurred_at,
                        ),
                    )
                    decision = await self._store_memory_decision(
                        connection,
                        subject=normalized_subject,
                        decision=decision,
                    )
                    await self._compact_delivered_turns(
                        connection,
                        subject=normalized_subject,
                    )
                    await self._prune_retention(
                        connection,
                        subject=normalized_subject,
                    )
            except errors.UniqueViolation as error:
                raise ValueError(
                    f"delivered turn {validated_turn.turn_id!r} is already committed"
                ) from error
        return decision

    async def set_session_summary(
        self,
        *,
        subject: str,
        summary: str | None,
    ) -> None:
        normalized_subject = _validate_subject(subject)
        normalized_summary = _validate_optional_summary(summary)
        async with self._connection() as connection:
            async with connection.transaction():
                await self._ensure_subject(connection, normalized_subject)
                await connection.execute(
                    """
                    update ava_private.memory_state
                    set current_session_summary = %s, updated_at = now()
                    where subject = %s
                    """,
                    (normalized_summary, normalized_subject),
                )

    async def load_weekly_recap(
        self,
        *,
        subject: str,
        period_start: date,
        generated_at: datetime,
    ) -> WeeklyConversationRecap:
        normalized_subject = _validate_subject(subject)
        normalized_generated_at = _validate_aware_datetime(
            generated_at,
            field_name="generated_at",
        )
        period_end = period_start + timedelta(days=7)
        async with self._connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "set transaction isolation level repeatable read read only"
                )
                state_cursor = await connection.execute(
                    """
                    select reset_revision
                    from ava_private.memory_state
                    where subject = %s
                    """,
                    (normalized_subject,),
                )
                state = await state_cursor.fetchone()
                records_cursor = await connection.execute(
                    """
                    select turn_id, summary, occurred_at
                    from ava_private.weekly_records
                    where subject = %s
                      and occurred_at >= %s
                      and occurred_at < %s
                    order by occurred_at asc, turn_id asc
                    limit %s
                    """,
                    (
                        normalized_subject,
                        datetime.combine(period_start, datetime.min.time(), UTC),
                        datetime.combine(period_end, datetime.min.time(), UTC),
                        MAX_WEEKLY_RECORDS,
                    ),
                )
                rows = await records_cursor.fetchall()
        return WeeklyConversationRecap.model_validate(
            {
                "periodStart": period_start,
                "periodEnd": period_end,
                "generatedAt": normalized_generated_at,
                "resetRevision": state["reset_revision"] if state else 0,
                "records": [
                    WeeklyConversationRecord.model_validate(
                        {
                            "turnId": row["turn_id"],
                            "summary": row["summary"],
                            "occurredAt": row["occurred_at"],
                        }
                    ).model_dump(mode="json", by_alias=True)
                    for row in rows
                ],
            }
        )

    async def reset_conversation(
        self,
        *,
        subject: str,
    ) -> ConversationResetReceipt:
        normalized_subject = _validate_subject(subject)
        reset_at = datetime.now(UTC)
        async with self._connection() as connection:
            async with connection.transaction():
                await self._ensure_subject(connection, normalized_subject)
                revision_cursor = await connection.execute(
                    """
                    update ava_private.memory_state
                    set current_session_summary = null,
                        reset_revision = reset_revision + 1,
                        updated_at = %s
                    where subject = %s
                    returning reset_revision
                    """,
                    (reset_at, normalized_subject),
                )
                revision_row = await revision_cursor.fetchone()
                if revision_row is None:
                    raise PostgresMemoryStoreError(
                        "PostgreSQL memory reset did not return a revision"
                    )
                revision = revision_row["reset_revision"]
                await connection.execute(
                    "delete from ava_private.stable_memories where subject = %s",
                    (normalized_subject,),
                )
                await connection.execute(
                    "delete from ava_private.weekly_records where subject = %s",
                    (normalized_subject,),
                )
                await connection.execute(
                    "delete from ava_private.delivered_turns where subject = %s",
                    (normalized_subject,),
                )
                await connection.execute(
                    "delete from ava_private.turn_receipts where subject = %s",
                    (normalized_subject,),
                )
                await connection.execute(
                    """
                    insert into ava_private.reset_log (subject, revision, reset_at)
                    values (%s, %s, %s)
                    """,
                    (normalized_subject, revision, reset_at),
                )
                await connection.execute(
                    """
                    delete from ava_private.reset_log
                    where subject = %s
                      and revision not in (
                        select revision
                        from ava_private.reset_log
                        where subject = %s
                        order by revision desc
                        limit 32
                      )
                    """,
                    (normalized_subject, normalized_subject),
                )
        return ConversationResetReceipt.model_validate(
            {"revision": revision, "resetAt": reset_at}
        )

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[AsyncConnection[dict[str, object]]]:
        if not self._opened:
            raise PostgresMemoryStoreError(
                "PostgreSQL memory store must be opened before use"
            )
        try:
            async with self._pool.connection(
                timeout=self._pool_timeout_seconds
            ) as connection:
                yield connection
        except (PsycopgError, PoolTimeout) as error:
            raise PostgresMemoryStoreError(
                "PostgreSQL memory operation failed"
            ) from error

    @staticmethod
    async def _ensure_subject(
        connection: AsyncConnection[dict[str, object]],
        subject: str,
    ) -> None:
        await connection.execute(
            """
            insert into ava_private.memory_state (subject)
            values (%s)
            on conflict (subject) do nothing
            """,
            (subject,),
        )

    @staticmethod
    async def _lock_subject(
        connection: AsyncConnection[dict[str, object]],
        subject: str,
    ) -> None:
        cursor = await connection.execute(
            """
            select subject
            from ava_private.memory_state
            where subject = %s
            for update
            """,
            (subject,),
        )
        if await cursor.fetchone() is None:
            raise PostgresMemoryStoreError(
                "PostgreSQL memory subject disappeared before locking"
            )

    @staticmethod
    async def _store_memory_decision(
        connection: AsyncConnection[dict[str, object]],
        *,
        subject: str,
        decision: MemoryProposalDecision,
    ) -> MemoryProposalDecision:
        record = decision.record
        if record is None:
            return decision
        duplicate_cursor = await connection.execute(
            """
            select 1
            from ava_private.stable_memories
            where subject = %s and kind = %s and lower(summary) = lower(%s)
            """,
            (subject, str(record.kind), record.summary),
        )
        if await duplicate_cursor.fetchone() is not None:
            return _rejected_decision("Equivalent stable memory already exists.")
        count_cursor = await connection.execute(
            """
            select count(*) as memory_count
            from ava_private.stable_memories
            where subject = %s
            """,
            (subject,),
        )
        count_row = await count_cursor.fetchone()
        memory_count = (
            cast(int, count_row["memory_count"]) if count_row is not None else 0
        )
        if memory_count >= MAX_STABLE_MEMORIES:
            return _rejected_decision(
                "Stable memory capacity is exhausted; the delivered turn was retained."
            )
        await connection.execute(
            """
            insert into ava_private.stable_memories (
              subject, memory_id, kind, summary, evidence, source_turn_id
            ) values (%s, %s, %s, %s, %s, %s)
            """,
            (
                subject,
                record.id,
                str(record.kind),
                record.summary,
                record.evidence,
                record.source_turn_id,
            ),
        )
        return decision

    @staticmethod
    async def _compact_delivered_turns(
        connection: AsyncConnection[dict[str, object]],
        *,
        subject: str,
    ) -> None:
        overflow_cursor = await connection.execute(
            """
            select turn_id, child_turn_summary
            from ava_private.delivered_turns
            where subject = %s
            order by occurred_at desc, turn_id desc
            offset %s
            """,
            (subject, MAX_DELIVERED_TURNS),
        )
        overflow = await overflow_cursor.fetchall()
        if not overflow:
            return
        state_cursor = await connection.execute(
            """
            select current_session_summary
            from ava_private.memory_state
            where subject = %s
            for update
            """,
            (subject,),
        )
        state = await state_cursor.fetchone()
        if state is None:
            raise PostgresMemoryStoreError("PostgreSQL memory subject disappeared")
        summary = compact_session_summary(
            cast(str | None, state["current_session_summary"]),
            tuple(cast(str, row["child_turn_summary"]) for row in reversed(overflow)),
        )
        await connection.execute(
            """
            update ava_private.memory_state
            set current_session_summary = %s, updated_at = now()
            where subject = %s
            """,
            (summary, subject),
        )
        await connection.execute(
            """
            delete from ava_private.delivered_turns
            where subject = %s and turn_id = any(%s)
            """,
            (subject, [row["turn_id"] for row in overflow]),
        )

    async def _prune_retention(
        self,
        connection: AsyncConnection[dict[str, object]],
        *,
        subject: str,
    ) -> None:
        threshold = datetime.now(UTC) - timedelta(days=self._weekly_retention_days)
        await connection.execute(
            """
            delete from ava_private.weekly_records
            where subject = %s and occurred_at < %s
            """,
            (subject, threshold),
        )
        await connection.execute(
            """
            delete from ava_private.turn_receipts as receipt
            where receipt.subject = %s
              and receipt.committed_at < %s
              and not exists (
                select 1 from ava_private.delivered_turns as turn
                where turn.subject = receipt.subject
                  and turn.turn_id = receipt.turn_id
              )
              and not exists (
                select 1 from ava_private.stable_memories as memory
                where memory.subject = receipt.subject
                  and memory.source_turn_id = receipt.turn_id
              )
              and not exists (
                select 1 from ava_private.weekly_records as weekly
                where weekly.subject = receipt.subject
                  and weekly.turn_id = receipt.turn_id
              )
            """,
            (subject, threshold),
        )


def _rejected_decision(reason: str) -> MemoryProposalDecision:
    return MemoryProposalDecision.model_validate(
        {"accepted": False, "reason": reason, "record": None}
    )
