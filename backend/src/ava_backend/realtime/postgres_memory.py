from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import AsyncIterator, cast

from psycopg import AsyncConnection, Error as PsycopgError, errors
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from ..protocol import AgeBand
from .memory import (
    EPISODE_RETENTION_DAYS,
    MAX_EPISODES,
    MAX_SNAPSHOT_DELIVERED_TURNS,
    MAX_STABLE_MEMORIES,
    MAX_WEEKLY_RECORDS,
    RAW_RETENTION_HIGH_WATER,
    RAW_RETENTION_LOW_WATER,
    AvaMemoryProposal,
    AuthoritativeMemorySnapshot,
    ConversationEpisode,
    ConversationResetReceipt,
    DeliveredConversationTurn,
    DeliveredInputKind,
    MemorySearchCandidate,
    MemoryResetRevisionMismatchError,
    MemoryProposalDecision,
    StoredDeliveredConversationEvent,
    WeeklyConversationRecap,
    WeeklyConversationRecord,
    _validate_aware_datetime,
    _validate_reset_revision,
    _validate_search_bounds,
    _validate_search_limit,
    _validate_search_query,
    _validate_subject,
    _validate_weekly_summary,
    build_conversation_episodes,
    evaluate_delivered_turn_memory_proposal,
    _validate_optional_summary,
)
from .timezone import validate_iana_timezone_id


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
                    where name = 'realtime_memory_v5'
                    """
                )
                row = await cursor.fetchone()
        except (PsycopgError, PoolTimeout) as error:
            await self._pool.close()
            raise PostgresMemoryStoreError(
                "PostgreSQL memory is unavailable or its v5 migration is missing"
            ) from error
        if row is None or cast(int, row["version"]) != 1:
            await self._pool.close()
            raise PostgresMemoryStoreError(
                "PostgreSQL memory schema realtime_memory_v5 version 1 is required"
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
                    select current_session_summary, reset_revision
                    from ava_private.memory_state
                    where subject = %s
                    """,
                    (normalized_subject,),
                )
                state = await state_cursor.fetchone()
                turns_cursor = await connection.execute(
                    """
                    select turn_id, input_kind, child_text, child_text_source,
                           ava_delivered_text, delivered_sample_count,
                           child_turn_summary, terminal,
                           canvas_scene_id, canvas_scene_revision,
                           canvas_interaction_revision, canvas_interaction_mode,
                           canvas_binding_id, canvas_target_id,
                           canvas_accessibility_label, canvas_step_index
                    from (
                      select *
                      from ava_private.delivered_turns
                      where subject = %s
                      order by occurred_at desc, turn_id desc
                      limit %s
                    ) as newest
                    order by occurred_at asc, turn_id asc
                    """,
                    (normalized_subject, MAX_SNAPSHOT_DELIVERED_TURNS),
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
                "resetRevision": (state["reset_revision"] if state is not None else 0),
                "currentSessionSummary": (
                    state["current_session_summary"] if state is not None else None
                ),
                "deliveredTurns": [
                    {
                        "turnId": row["turn_id"],
                        "inputKind": row["input_kind"],
                        "childText": row["child_text"],
                        "childTextSource": row["child_text_source"],
                        "canvasInteraction": (
                            {
                                "sceneId": row["canvas_scene_id"],
                                "sceneRevision": row["canvas_scene_revision"],
                                "interactionRevision": row[
                                    "canvas_interaction_revision"
                                ],
                                "interactionMode": row["canvas_interaction_mode"],
                                "bindingId": row["canvas_binding_id"],
                                "targetId": row["canvas_target_id"],
                                "accessibilityLabel": row["canvas_accessibility_label"],
                                "stepIndex": row["canvas_step_index"],
                            }
                            if row["input_kind"]
                            == DeliveredInputKind.CANVAS_INTERACTION
                            else None
                        ),
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
        expected_reset_revision: int,
        turn: DeliveredConversationTurn,
        proposal: AvaMemoryProposal | None,
        memory_id: str | None,
        occurred_at: datetime | None = None,
        timezone_id: str = "UTC",
    ) -> MemoryProposalDecision:
        normalized_subject = _validate_subject(subject)
        validated_expected_reset_revision = _validate_reset_revision(
            expected_reset_revision,
            field_name="expected_reset_revision",
        )
        normalized_occurred_at = _validate_aware_datetime(
            occurred_at or datetime.now(UTC),
            field_name="occurred_at",
        )
        validated_timezone_id = validate_iana_timezone_id(timezone_id)
        validated_turn = DeliveredConversationTurn.model_validate(
            turn.model_dump(mode="json", by_alias=True)
        )
        weekly_summary = _validate_weekly_summary(validated_turn.child_turn_summary)
        decision = evaluate_delivered_turn_memory_proposal(
            turn=validated_turn,
            proposal=proposal,
            memory_id=memory_id,
        )
        interaction = validated_turn.canvas_interaction

        async with self._connection() as connection:
            try:
                async with connection.transaction():
                    await self._ensure_subject(connection, normalized_subject)
                    current_reset_revision = await self._lock_subject(
                        connection,
                        normalized_subject,
                    )
                    if current_reset_revision != validated_expected_reset_revision:
                        raise MemoryResetRevisionMismatchError(
                            expected_reset_revision=(validated_expected_reset_revision),
                            current_reset_revision=current_reset_revision,
                        )
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
                          subject, turn_id, input_kind, child_text, child_text_source,
                          ava_delivered_text, delivered_sample_count,
                          child_turn_summary, terminal, occurred_at,
                          timezone_id,
                          canvas_scene_id, canvas_scene_revision,
                          canvas_interaction_revision, canvas_interaction_mode,
                          canvas_binding_id, canvas_target_id,
                          canvas_accessibility_label, canvas_step_index
                        ) values (
                          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            normalized_subject,
                            validated_turn.turn_id,
                            str(validated_turn.input_kind),
                            validated_turn.child_text,
                            (
                                str(validated_turn.child_text_source)
                                if validated_turn.child_text_source is not None
                                else None
                            ),
                            validated_turn.ava_delivered_text,
                            validated_turn.delivered_sample_count,
                            validated_turn.child_turn_summary,
                            str(validated_turn.terminal),
                            normalized_occurred_at,
                            validated_timezone_id,
                            interaction.scene_id if interaction else None,
                            interaction.scene_revision if interaction else None,
                            interaction.interaction_revision if interaction else None,
                            str(interaction.interaction_mode) if interaction else None,
                            interaction.binding_id if interaction else None,
                            interaction.target_id if interaction else None,
                            interaction.accessibility_label if interaction else None,
                            interaction.step_index if interaction else None,
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
                    await self._compact_delivered_turns_into_episodes(
                        connection,
                        subject=normalized_subject,
                        created_at=normalized_occurred_at,
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

    async def search_memory_candidates(
        self,
        *,
        subject: str,
        query: str,
        limit: int = 5,
        from_instant: datetime | None = None,
        to_instant: datetime | None = None,
    ) -> list[MemorySearchCandidate]:
        normalized_subject = _validate_subject(subject)
        query_text = " ".join(_validate_search_query(query))
        validated_limit = _validate_search_limit(limit)
        lower, upper = _validate_search_bounds(from_instant, to_instant)
        async with self._connection() as connection:
            cursor = await connection.execute(
                """
                with search_query as (
                  select plainto_tsquery('simple', %s) as value
                ),
                raw_candidates as (
                  select
                    turn.turn_id as source_id,
                    'rawEvent'::text as source_kind,
                    turn.occurred_at as started_at,
                    turn.occurred_at as ended_at,
                    array[turn.timezone_id]::text[] as timezone_ids,
                    left(
                      regexp_replace(
                        coalesce(turn.child_text, '') || ' ' ||
                        turn.child_turn_summary || ' ' ||
                        turn.ava_delivered_text || ' ' ||
                        coalesce(turn.canvas_accessibility_label, ''),
                        '\\s+', ' ', 'g'
                      ),
                      1200
                    ) as reference_text,
                    array[turn.turn_id]::text[] as source_turn_ids,
                    ts_rank_cd(
                      to_tsvector(
                        'simple',
                        coalesce(turn.child_text, '') || ' ' ||
                        turn.child_turn_summary || ' ' ||
                        turn.ava_delivered_text || ' ' ||
                        coalesce(turn.canvas_accessibility_label, '')
                      ),
                      search_query.value
                    ) as relevance
                  from ava_private.delivered_turns as turn
                  cross join search_query
                  where turn.subject = %s
                    and to_tsvector(
                      'simple',
                      coalesce(turn.child_text, '') || ' ' ||
                      turn.child_turn_summary || ' ' ||
                      turn.ava_delivered_text || ' ' ||
                      coalesce(turn.canvas_accessibility_label, '')
                    ) @@ search_query.value
                    and (%s::timestamptz is null or turn.occurred_at >= %s)
                    and (%s::timestamptz is null or turn.occurred_at < %s)
                ),
                episode_candidates as (
                  select
                    episode.episode_id as source_id,
                    'episode'::text as source_kind,
                    episode.started_at,
                    episode.ended_at,
                    array[episode.timezone_id]::text[] as timezone_ids,
                    left(
                      replace(
                        replace(
                          ts_headline(
                            'simple',
                            episode.digest,
                            search_query.value,
                            'StartSel=<ava-search-match>, '
                            'StopSel=</ava-search-match>, MaxWords=48, '
                            'MinWords=8, ShortWord=1, MaxFragments=4, '
                            'FragmentDelimiter= … '
                          ),
                          '<ava-search-match>',
                          ''
                        ),
                        '</ava-search-match>',
                        ''
                      ),
                      1200
                    ) as reference_text,
                    episode.source_turn_ids,
                    ts_rank_cd(
                      to_tsvector('simple', episode.digest),
                      search_query.value
                    ) as relevance
                  from ava_private.conversation_episodes as episode
                  cross join search_query
                  where episode.subject = %s
                    and to_tsvector('simple', episode.digest) @@ search_query.value
                    and (%s::timestamptz is null or episode.ended_at >= %s)
                    and (%s::timestamptz is null or episode.started_at < %s)
                    and not exists (
                      select 1
                      from ava_private.delivered_turns as overlapping_raw
                      where overlapping_raw.subject = episode.subject
                        and overlapping_raw.turn_id = any(episode.source_turn_ids)
                    )
                ),
                stable_candidates as (
                  select
                    memory.memory_id as source_id,
                    'stableMemory'::text as source_kind,
                    coalesce(raw.occurred_at, episode.started_at, memory.created_at)
                      as started_at,
                    coalesce(raw.occurred_at, episode.ended_at, memory.created_at)
                      as ended_at,
                    case
                      when raw.timezone_id is not null
                        then array[raw.timezone_id]::text[]
                      when episode.timezone_id is not null
                        then array[episode.timezone_id]::text[]
                      else array[]::text[]
                    end as timezone_ids,
                    left(memory.summary || ' ' || memory.evidence, 1200)
                      as reference_text,
                    array[memory.source_turn_id]::text[] as source_turn_ids,
                    ts_rank_cd(
                      to_tsvector('simple', memory.summary || ' ' || memory.evidence),
                      search_query.value
                    ) as relevance
                  from ava_private.stable_memories as memory
                  cross join search_query
                  left join ava_private.delivered_turns as raw
                    on raw.subject = memory.subject
                   and raw.turn_id = memory.source_turn_id
                  left join lateral (
                    select candidate.started_at, candidate.ended_at,
                           candidate.timezone_id
                    from ava_private.conversation_episodes as candidate
                    where candidate.subject = memory.subject
                      and memory.source_turn_id = any(candidate.source_turn_ids)
                    order by candidate.ended_at desc, candidate.episode_id desc
                    limit 1
                  ) as episode on true
                  where memory.subject = %s
                    and to_tsvector(
                      'simple', memory.summary || ' ' || memory.evidence
                    ) @@ search_query.value
                    and (
                      %s::timestamptz is null
                      or coalesce(raw.occurred_at, episode.ended_at, memory.created_at)
                         >= %s
                    )
                    and (
                      %s::timestamptz is null
                      or coalesce(raw.occurred_at, episode.started_at, memory.created_at)
                         < %s
                    )
                ),
                candidates as (
                  select * from raw_candidates
                  union all
                  select * from episode_candidates
                  union all
                  select * from stable_candidates
                )
                select source_id, source_kind, started_at, ended_at,
                       timezone_ids, reference_text, source_turn_ids, relevance
                from candidates
                order by relevance desc, ended_at desc, source_id asc
                limit %s
                """,
                (
                    query_text,
                    normalized_subject,
                    lower,
                    lower,
                    upper,
                    upper,
                    normalized_subject,
                    lower,
                    lower,
                    upper,
                    upper,
                    normalized_subject,
                    lower,
                    lower,
                    upper,
                    upper,
                    validated_limit,
                ),
            )
            rows = await cursor.fetchall()
        return [
            MemorySearchCandidate.model_validate(
                {
                    "sourceId": row["source_id"],
                    "sourceKind": row["source_kind"],
                    "startedAt": row["started_at"],
                    "endedAt": row["ended_at"],
                    "timezoneIds": row["timezone_ids"],
                    "referenceText": row["reference_text"],
                    "sourceTurnIds": row["source_turn_ids"],
                    "relevance": row["relevance"],
                }
            )
            for row in rows
        ]

    async def set_session_summary(
        self,
        *,
        subject: str,
        expected_reset_revision: int,
        summary: str | None,
    ) -> None:
        normalized_subject = _validate_subject(subject)
        validated_expected_reset_revision = _validate_reset_revision(
            expected_reset_revision,
            field_name="expected_reset_revision",
        )
        normalized_summary = _validate_optional_summary(summary)
        async with self._connection() as connection:
            async with connection.transaction():
                await self._ensure_subject(connection, normalized_subject)
                current_reset_revision = await self._lock_subject(
                    connection,
                    normalized_subject,
                )
                if current_reset_revision != validated_expected_reset_revision:
                    raise MemoryResetRevisionMismatchError(
                        expected_reset_revision=validated_expected_reset_revision,
                        current_reset_revision=current_reset_revision,
                    )
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
                    "delete from ava_private.conversation_episodes where subject = %s",
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
    ) -> int:
        cursor = await connection.execute(
            """
            select reset_revision
            from ava_private.memory_state
            where subject = %s
            for update
            """,
            (subject,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise PostgresMemoryStoreError(
                "PostgreSQL memory subject disappeared before locking"
            )
        return cast(int, row["reset_revision"])

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

    async def _compact_delivered_turns_into_episodes(
        self,
        connection: AsyncConnection[dict[str, object]],
        *,
        subject: str,
        created_at: datetime,
    ) -> None:
        count_cursor = await connection.execute(
            """
            select count(*) as raw_count
            from ava_private.delivered_turns
            where subject = %s
            """,
            (subject,),
        )
        count_row = await count_cursor.fetchone()
        raw_count = cast(int, count_row["raw_count"]) if count_row else 0
        if raw_count <= RAW_RETENTION_HIGH_WATER:
            return
        compact_count = raw_count - RAW_RETENTION_LOW_WATER
        source_cursor = await connection.execute(
            """
            select turn_id, input_kind, child_text, child_text_source,
                   ava_delivered_text, delivered_sample_count,
                   child_turn_summary, terminal, occurred_at, timezone_id,
                   canvas_scene_id, canvas_scene_revision,
                   canvas_interaction_revision, canvas_interaction_mode,
                   canvas_binding_id, canvas_target_id,
                   canvas_accessibility_label, canvas_step_index
            from ava_private.delivered_turns
            where subject = %s
            order by occurred_at asc, turn_id asc
            limit %s
            """,
            (subject, compact_count),
        )
        source_rows = await source_cursor.fetchall()
        if len(source_rows) != compact_count:
            raise PostgresMemoryStoreError(
                "PostgreSQL raw memory count changed while subject lock was held"
            )
        source_events = tuple(
            StoredDeliveredConversationEvent(
                occurred_at=cast(datetime, row["occurred_at"]).astimezone(UTC),
                timezone_id=validate_iana_timezone_id(cast(str, row["timezone_id"])),
                turn=_delivered_turn_from_row(row),
            )
            for row in source_rows
        )
        episodes = build_conversation_episodes(
            subject=subject,
            source_events=source_events,
            created_at=created_at,
        )
        for episode in episodes:
            await self._insert_episode(connection, episode=episode)
        await connection.execute(
            """
            delete from ava_private.delivered_turns
            where subject = %s and turn_id = any(%s)
            """,
            (subject, [event.turn.turn_id for event in source_events]),
        )

    @staticmethod
    async def _insert_episode(
        connection: AsyncConnection[dict[str, object]],
        *,
        episode: ConversationEpisode,
    ) -> None:
        await connection.execute(
            """
            insert into ava_private.conversation_episodes (
              subject, episode_id, started_at, ended_at, timezone_id,
              digest, source_turn_ids, source_event_count, created_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                episode.subject,
                episode.episode_id,
                episode.started_at,
                episode.ended_at,
                episode.timezone_id,
                episode.digest,
                episode.source_turn_ids,
                episode.source_event_count,
                episode.created_at,
            ),
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
        episode_threshold = datetime.now(UTC) - timedelta(days=EPISODE_RETENTION_DAYS)
        await connection.execute(
            """
            delete from ava_private.conversation_episodes
            where subject = %s and ended_at < %s
            """,
            (subject, episode_threshold),
        )
        await connection.execute(
            """
            delete from ava_private.conversation_episodes as episode
            where episode.subject = %s
              and (episode.ended_at, episode.episode_id) not in (
                select retained.ended_at, retained.episode_id
                from ava_private.conversation_episodes as retained
                where retained.subject = %s
                order by retained.ended_at desc, retained.episode_id desc
                limit %s
              )
            """,
            (subject, subject, MAX_EPISODES),
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
              and not exists (
                select 1 from ava_private.conversation_episodes as episode
                where episode.subject = receipt.subject
                  and receipt.turn_id = any(episode.source_turn_ids)
              )
            """,
            (subject, threshold),
        )


def _rejected_decision(reason: str) -> MemoryProposalDecision:
    return MemoryProposalDecision.model_validate(
        {"accepted": False, "reason": reason, "record": None}
    )


def _delivered_turn_from_row(row: dict[str, object]) -> DeliveredConversationTurn:
    return DeliveredConversationTurn.model_validate(
        {
            "turnId": row["turn_id"],
            "inputKind": row["input_kind"],
            "childText": row["child_text"],
            "childTextSource": row["child_text_source"],
            "canvasInteraction": (
                {
                    "sceneId": row["canvas_scene_id"],
                    "sceneRevision": row["canvas_scene_revision"],
                    "interactionRevision": row["canvas_interaction_revision"],
                    "interactionMode": row["canvas_interaction_mode"],
                    "bindingId": row["canvas_binding_id"],
                    "targetId": row["canvas_target_id"],
                    "accessibilityLabel": row["canvas_accessibility_label"],
                    "stepIndex": row["canvas_step_index"],
                }
                if row["input_kind"] == DeliveredInputKind.CANVAS_INTERACTION
                else None
            ),
            "avaDeliveredText": row["ava_delivered_text"],
            "deliveredSampleCount": row["delivered_sample_count"],
            "childTurnSummary": row["child_turn_summary"],
            "terminal": row["terminal"],
        }
    )
