from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ava_backend.realtime.memory import (
    AuthoritativeMemorySnapshot,
    ChildTurnTextSource,
    ConversationEpisode,
    DeliveredConversationTurn,
    StoredDeliveredConversationTurn,
    DevelopmentAuthoritativeMemoryStore,
    MAX_WEEKLY_RECORDS,
    MemoryResetRevisionMismatchError,
    current_local_week_utc_bounds,
    evaluate_memory_proposal,
)
from ava_backend.realtime.memory import AvaMemoryProposal


def _turn(
    *,
    turn_id: str = "turn-1",
    child_text: str = "I love building model airplanes.",
    delivered_text: str = "Model airplanes have so many clever little parts.",
) -> DeliveredConversationTurn:
    return DeliveredConversationTurn.model_validate(
        {
            "turnId": turn_id,
            "childText": child_text,
            "avaDeliveredText": delivered_text,
            "deliveredSampleCount": 24000 if delivered_text else 0,
            "childTurnSummary": "The child shared an interest.",
            "terminal": "completed",
        }
    )


def _proposal(
    *,
    summary: str = "The child enjoys building model airplanes.",
    evidence: str = "I love building model airplanes.",
    confidence: float = 0.9,
) -> AvaMemoryProposal:
    return AvaMemoryProposal.model_validate(
        {
            "kind": "interest",
            "summary": summary,
            "evidence": evidence,
            "confidence": confidence,
        }
    )


async def test_v3_store_commits_only_the_alignment_derived_delivered_prefix() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    turn = _turn(
        delivered_text="Model airplanes have",
    )

    await store.commit_delivered_turn(
        subject="child:123",
        expected_reset_revision=0,
        turn=turn,
    )
    snapshot = await store.load_snapshot(
        subject="child:123",
        age_band="middleChildhood",
    )

    assert snapshot.delivered_turns[0].ava_delivered_text == ("Model airplanes have")
    assert (
        "so many clever little parts"
        not in snapshot.delivered_turns[0].ava_delivered_text
    )
    assert snapshot.stable_memories == []


async def test_v3_store_rejects_duplicate_turn_commit_and_reset_clears_context() -> (
    None
):
    store = DevelopmentAuthoritativeMemoryStore()
    await store.commit_delivered_turn(
        subject="child:123",
        expected_reset_revision=0,
        turn=_turn(),
    )
    with pytest.raises(ValueError, match="already committed"):
        await store.commit_delivered_turn(
            subject="child:123",
            expected_reset_revision=0,
            turn=_turn(),
        )

    receipt = await store.reset_conversation(subject="child:123")
    snapshot = await store.load_snapshot(
        subject="child:123",
        age_band="middleChildhood",
    )
    assert snapshot.delivered_turns == []
    assert snapshot.stable_memories == []
    assert snapshot.current_session_summary is None
    assert receipt.revision == 1
    second_receipt = await store.reset_conversation(subject="child:123")
    assert second_receipt.revision == 2


async def test_raw_history_survives_old_high_water_without_episodes() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    base = datetime.now(UTC) - timedelta(days=1)
    for index in range(1, 256 + 1):
        await store.commit_delivered_turn(
            subject="child:123",
            expected_reset_revision=0,
            turn=_turn(
                turn_id=f"turn-{index}",
                child_text=f"Child statement {index}.",
                delivered_text=f"Ava response {index}.",
            ).model_copy(update={"child_turn_summary": f"Topic {index}."}),
            occurred_at=base + timedelta(minutes=index),
            timezone_id="Asia/Kolkata",
        )
        if index == 8:
            assert len(store._turns["child:123"]) == 8
            assert store._episodes["child:123"] == []

    assert len(store._turns["child:123"]) == 256
    assert store._episodes["child:123"] == []

    await store.commit_delivered_turn(
        subject="child:123",
        expected_reset_revision=0,
        turn=_turn(
            turn_id="turn-257",
            child_text="Child statement 257.",
            delivered_text="Ava response 257.",
        ).model_copy(update={"child_turn_summary": "Topic 257."}),
        occurred_at=base + timedelta(minutes=257),
        timezone_id="America/New_York",
    )

    raw = store._turns["child:123"]
    episodes: list[ConversationEpisode] = store._episodes["child:123"]
    assert len(raw) == 257
    assert episodes == []
    assert raw[0].turn.turn_id == "turn-1"
    assert [event.turn.turn_sequence for event in raw] == list(range(1, 258))

    snapshot = await store.load_snapshot(
        subject="child:123", age_band="middleChildhood"
    )
    assert [turn.turn_id for turn in snapshot.delivered_turns] == [
        f"turn-{index}" for index in range(250, 258)
    ]
    assert snapshot.current_session_summary is None


async def test_episode_search_returns_late_matching_semantic_line() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    episode = ConversationEpisode.model_validate(
        {
            "subject": "child:excerpt",
            "episodeId": "episode-legacy",
            "startedAt": base,
            "endedAt": base + timedelta(minutes=32),
            "timezoneId": "UTC",
            "createdAt": base + timedelta(hours=1),
            "sourceTurnIds": [f"turn-excerpt-{i}" for i in range(1, 33)],
            "sourceEventCount": 32,
            "digest": "\n".join(
                "Discussed cobalt nebula wisps."
                if i == 25
                else f"Earlier ordinary astronomy discussion {i} " + "x" * 90
                for i in range(1, 33)
            ),
        }
    )
    assert len(episode.digest[:1_200]) == 1_200
    assert "cobalt" not in episode.digest[:1_200]
    store._episodes["child:excerpt"].append(episode)

    candidates = await store.search_memory_candidates(
        subject="child:excerpt",
        query="cobalt nebula",
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_kind == "episode"
    assert "cobalt nebula" in candidate.reference_text.casefold()
    assert len(candidate.reference_text) <= 1_200
    assert candidate.source_turn_ids == list(episode.source_turn_ids)
    assert candidate.started_at == episode.started_at
    assert candidate.ended_at == episode.ended_at
    assert "turn=" not in candidate.reference_text
    assert "timezone=" not in candidate.reference_text


async def test_v3_weekly_recap_is_bounded_factual_and_resettable() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    occurred_at = datetime(2026, 9, 1, 6, tzinfo=UTC)
    generated_at = datetime(2026, 9, 2, 6, tzinfo=UTC)
    await store.commit_delivered_turn(
        subject="child:123",
        expected_reset_revision=0,
        turn=_turn().model_copy(
            update={"child_turn_summary": "Talked about model airplanes."}
        ),
        occurred_at=occurred_at,
    )
    recap = await store.load_weekly_recap(
        subject="child:123",
        timezone_id="Asia/Kolkata",
        generated_at=generated_at,
    )
    assert [record.summary for record in recap.records] == [
        "Talked about model airplanes."
    ]
    assert recap.reset_revision == 0

    await store.reset_conversation(subject="child:123")
    reset_recap = await store.load_weekly_recap(
        subject="child:123",
        timezone_id="Asia/Kolkata",
        generated_at=generated_at,
    )
    assert reset_recap.records == []
    assert reset_recap.reset_revision == 1


async def test_weekly_recap_is_newest_first_with_descending_turn_id_ties() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    subject = "child:weekly-order"
    earlier = datetime(2026, 9, 1, 10, tzinfo=UTC)
    tied = datetime(2026, 9, 1, 12, tzinfo=UTC)
    for turn_id, occurred_at in (
        ("turn-earlier", earlier),
        ("turn-a", tied),
        ("turn-b", tied),
    ):
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(turn_id=turn_id).model_copy(
                update={"child_turn_summary": f"Summary for {turn_id}."}
            ),
            occurred_at=occurred_at,
            timezone_id="Europe/London",
        )

    recap = await store.load_weekly_recap(
        subject=subject,
        timezone_id="Europe/London",
        generated_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
    )

    assert [record.turn_id for record in recap.records] == [
        "turn-b",
        "turn-a",
        "turn-earlier",
    ]


async def test_weekly_recap_uses_current_kolkata_local_week_boundaries() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    subject = "child:weekly-kolkata"
    zone = ZoneInfo("Asia/Kolkata")
    instants = {
        "turn-prior-sunday": datetime(2026, 8, 30, 23, 45, tzinfo=zone),
        "turn-monday": datetime(2026, 8, 31, 0, 15, tzinfo=zone),
        "turn-next-monday": datetime(2026, 9, 7, 0, 0, tzinfo=zone),
    }
    for turn_id, local_time in instants.items():
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(turn_id=turn_id),
            occurred_at=local_time.astimezone(UTC),
            timezone_id="Europe/London",
        )

    recap = await store.load_weekly_recap(
        subject=subject,
        timezone_id="Asia/Kolkata",
        generated_at=datetime(2026, 9, 2, 6, tzinfo=UTC),
    )

    assert recap.period_start == date(2026, 8, 31)
    assert recap.period_end == date(2026, 9, 7)
    assert [record.turn_id for record in recap.records] == ["turn-monday"]


@pytest.mark.parametrize(
    ("generated_at", "expected_lower", "expected_upper", "expected_hours"),
    [
        (
            datetime(2026, 3, 4, 12, tzinfo=UTC),
            datetime(2026, 3, 2, 5, tzinfo=UTC),
            datetime(2026, 3, 9, 4, tzinfo=UTC),
            167,
        ),
        (
            datetime(2026, 10, 28, 12, tzinfo=UTC),
            datetime(2026, 10, 26, 4, tzinfo=UTC),
            datetime(2026, 11, 2, 5, tzinfo=UTC),
            169,
        ),
    ],
)
def test_current_new_york_week_bounds_follow_local_dst_calendar_arithmetic(
    generated_at: datetime,
    expected_lower: datetime,
    expected_upper: datetime,
    expected_hours: int,
) -> None:
    period_start, period_end, lower, upper = current_local_week_utc_bounds(
        timezone_id="America/New_York",
        generated_at=generated_at,
    )

    assert period_start.weekday() == 0
    assert period_end == period_start + timedelta(days=7)
    assert lower == expected_lower
    assert upper == expected_upper
    assert (upper - lower).total_seconds() == expected_hours * 3600


async def test_current_week_recap_survives_normal_300_turn_raw_compaction() -> None:
    assert MAX_WEEKLY_RECORDS == 64
    store = DevelopmentAuthoritativeMemoryStore()
    subject = "child:weekly-compaction"
    zone = ZoneInfo("Asia/Kolkata")
    local_monday = datetime(2026, 8, 31, tzinfo=zone)
    expected: dict[str, tuple[str, datetime]] = {}
    for index in range(1, 301):
        turn_id = f"turn-{index:03d}"
        summary = f"Canonical recap summary {index}."
        occurred_at = (local_monday + timedelta(minutes=index)).astimezone(UTC)
        expected[turn_id] = (summary, occurred_at)
        await store.commit_delivered_turn(
            subject=subject,
            expected_reset_revision=0,
            turn=_turn(turn_id=turn_id).model_copy(
                update={"child_turn_summary": summary}
            ),
            occurred_at=occurred_at,
            timezone_id="Asia/Kolkata",
        )

    assert len(store._turns[subject]) == 300
    assert store._episodes[subject] == []
    recap = await store.load_weekly_recap(
        subject=subject,
        timezone_id="Asia/Kolkata",
        generated_at=datetime(2026, 9, 2, 6, tzinfo=UTC),
    )

    expected_ids = [f"turn-{index:03d}" for index in range(300, 236, -1)]
    assert [record.turn_id for record in recap.records] == expected_ids
    assert len(recap.records) == MAX_WEEKLY_RECORDS
    assert [(record.summary, record.occurred_at) for record in recap.records] == [
        expected[turn_id] for turn_id in expected_ids
    ]
    assert not hasattr(store, "_weekly_records")


async def test_v3_rejects_sensitive_weekly_summary_without_partial_commit() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    with pytest.raises(ValueError, match="sensitive"):
        await store.commit_delivered_turn(
            subject="child:123",
            expected_reset_revision=0,
            turn=_turn().model_copy(
                update={"child_turn_summary": "Home address is 12 River Street."}
            ),
        )
    snapshot = await store.load_snapshot(
        subject="child:123",
        age_band="middleChildhood",
    )
    assert snapshot.delivered_turns == []


async def test_v3_rejects_emotional_certainty_in_recap_and_session_summary() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    with pytest.raises(ValueError, match="hidden inference"):
        await store.commit_delivered_turn(
            subject="child:123",
            expected_reset_revision=0,
            turn=_turn().model_copy(
                update={"child_turn_summary": "The child was sad."}
            ),
        )


@pytest.mark.parametrize(
    ("proposal", "child_text", "expected_reason"),
    (
        (
            _proposal(confidence=0.79),
            "I love building model airplanes.",
            "confidence",
        ),
        (
            _proposal(evidence="The child likes airplanes."),
            "I love building model airplanes.",
            "exact excerpt",
        ),
        (
            _proposal(
                summary="The child's home address is 12 River Street.",
                evidence="My home address is 12 River Street.",
            ),
            "My home address is 12 River Street.",
            "sensitive",
        ),
        (
            _proposal(
                summary="The child is gifted.",
                evidence="I got a good score.",
            ),
            "I got a good score.",
            "hidden or diagnostic",
        ),
    ),
)
def test_memory_policy_rejects_weak_sensitive_or_inferred_proposals(
    proposal: AvaMemoryProposal,
    child_text: str,
    expected_reason: str,
) -> None:
    decision = evaluate_memory_proposal(
        proposal=proposal,
        child_text=child_text,
        source_turn_id="turn-1",
        memory_id="memory-1",
    )
    assert decision.accepted is False
    assert expected_reason in decision.reason


def test_memory_policy_rejects_model_summary_as_exact_evidence() -> None:
    decision = evaluate_memory_proposal(
        proposal=_proposal(),
        child_text="The child enjoys model airplanes.",
        child_text_source=ChildTurnTextSource.MODEL_SUMMARY,
        source_turn_id="turn-1",
        memory_id="memory-1",
    )
    assert decision.accepted is False
    assert "transcript" in decision.reason


@pytest.mark.parametrize(
    "field,value",
    [
        ("resetRevision", -1),
        ("turnSequence", 0),
        ("occurredAt", datetime(2026, 1, 1)),
        ("timezoneId", "not/a_timezone"),
    ],
)
def test_stored_timeline_rejects_invalid_metadata(field: str, value: object) -> None:
    payload = _turn().model_dump(by_alias=True)
    payload.update(
        resetRevision=0,
        turnSequence=1,
        occurredAt=datetime(2026, 1, 1, tzinfo=UTC),
        timezoneId="UTC",
    )
    payload[field] = value
    with pytest.raises(ValueError):
        StoredDeliveredConversationTurn.model_validate(payload)


def test_tool_envelope_rejects_extra_exchanges_missing_request_and_private_ids() -> (
    None
):
    payload = _turn().model_dump(by_alias=True)
    search = {
        "tool": "search_memory",
        "request": {"query": "telescopes"},
        "result": {"status": "noMatches"},
    }
    for exchanges in (
        [search, search, search],
        [search, search],
        [{"tool": "search_memory", "request": None, "result": {"status": "noMatches"}}],
        [
            {
                "tool": "request_interactive_canvas",
                "request": {
                    "operation": "new",
                    "brief": "A telescope",
                    "interactionMode": "none",
                    "continuousControl": "none",
                    "completion": "requestComplete",
                },
                "result": {"status": "accepted", "generationId": "generation-private"},
            }
        ],
    ):
        with pytest.raises(ValueError):
            DeliveredConversationTurn.model_validate(
                {**payload, "toolExchanges": exchanges}
            )


def test_delivered_turn_requires_audio_and_text_to_agree() -> None:
    payload = _turn().model_dump(mode="json", by_alias=True)
    payload["avaDeliveredText"] = ""
    immediate_stop = DeliveredConversationTurn.model_validate(payload)
    assert immediate_stop.delivered_sample_count > 0
    assert immediate_stop.ava_delivered_text == ""

    empty = _turn(delivered_text="")
    assert empty.delivered_sample_count == 0
    assert empty.ava_delivered_text == ""

    payload = empty.model_dump(mode="json", by_alias=True)
    payload["avaDeliveredText"] = "Unheard text"
    with pytest.raises(ValueError, match="positive"):
        DeliveredConversationTurn.model_validate(payload)


def test_canvas_interaction_is_a_typed_input_without_fabricated_child_text() -> None:
    turn = DeliveredConversationTurn.model_validate(
        {
            "turnId": "turn-interaction",
            "inputKind": "canvasInteraction",
            "childText": None,
            "childTextSource": None,
            "canvasInteraction": {
                "sceneId": "scene-A",
                "sceneRevision": 2,
                "interactionRevision": 7,
                "interactionMode": "explore",
                "bindingId": "binding-A",
                "targetId": "target-A",
                "accessibilityLabel": "target-A label",
                "stepIndex": None,
            },
            "avaDeliveredText": "A heard response.",
            "deliveredSampleCount": 24_000,
            "childTurnSummary": "Canvas interaction with target-A label.",
            "terminal": "completed",
        }
    )

    assert turn.input_kind == "canvasInteraction"
    assert turn.child_text is None
    assert turn.child_text_source is None
    assert turn.canvas_interaction is not None
    assert turn.canvas_interaction.binding_id == "binding-A"


async def test_canvas_interaction_never_creates_stable_memory_without_speech() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    interaction_turn = DeliveredConversationTurn.model_validate(
        {
            "turnId": "turn-interaction-stable",
            "inputKind": "canvasInteraction",
            "childText": None,
            "childTextSource": None,
            "canvasInteraction": {
                "sceneId": "scene-A",
                "sceneRevision": 1,
                "interactionRevision": 1,
                "interactionMode": "select",
                "bindingId": "binding-A",
                "targetId": "target-A",
                "accessibilityLabel": "target-A label",
                "stepIndex": None,
            },
            "avaDeliveredText": "",
            "deliveredSampleCount": 0,
            "childTurnSummary": "Canvas interaction.",
            "terminal": "cancelled",
        }
    )

    await store.commit_delivered_turn(
        subject="child:interaction-stable",
        expected_reset_revision=0,
        turn=interaction_turn,
    )
    snapshot = await store.load_snapshot(
        subject="child:interaction-stable",
        age_band="middleChildhood",
    )

    assert len(snapshot.delivered_turns) == 1
    assert (
        snapshot.delivered_turns[0].canvas_interaction
        == interaction_turn.canvas_interaction
    )
    assert snapshot.delivered_turns[0].terminal == interaction_turn.terminal
    assert snapshot.delivered_turns[0].turn_sequence == 1
    assert snapshot.stable_memories == []


def test_snapshot_rejects_duplicate_records() -> None:
    turn = _turn().model_dump(mode="json", by_alias=True)
    turn.update(
        resetRevision=0, turnSequence=1, occurredAt=datetime.now(UTC), timezoneId="UTC"
    )
    with pytest.raises(ValueError, match="turn IDs"):
        AuthoritativeMemorySnapshot.model_validate(
            {
                "ageBand": "middleChildhood",
                "resetRevision": 0,
                "currentSessionSummary": None,
                "deliveredTurns": [turn, turn],
                "stableMemories": [],
            }
        )


async def test_stale_delivered_commit_after_reset_is_rejected_without_restore() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    snapshot = await store.load_snapshot(
        subject="child:reset-fence",
        age_band="middleChildhood",
    )
    assert snapshot.reset_revision == 0

    receipt = await store.reset_conversation(subject="child:reset-fence")
    assert receipt.revision == 1

    with pytest.raises(MemoryResetRevisionMismatchError) as caught:
        await store.commit_delivered_turn(
            subject="child:reset-fence",
            expected_reset_revision=snapshot.reset_revision,
            turn=_turn(turn_id="turn-before-reset"),
        )

    assert caught.value.expected_reset_revision == 0
    assert caught.value.current_reset_revision == 1
    after = await store.load_snapshot(
        subject="child:reset-fence",
        age_band="middleChildhood",
    )
    assert after.reset_revision == 1
    assert after.delivered_turns == []
    assert after.stable_memories == []
    assert after.current_session_summary is None


async def test_fresh_post_reset_epoch_writes_and_duplicate_behavior_is_unchanged() -> (
    None
):
    store = DevelopmentAuthoritativeMemoryStore()
    await store.reset_conversation(subject="child:fresh-reset-epoch")
    snapshot = await store.load_snapshot(
        subject="child:fresh-reset-epoch",
        age_band="middleChildhood",
    )
    assert snapshot.reset_revision == 1

    await store.commit_delivered_turn(
        subject="child:fresh-reset-epoch",
        expected_reset_revision=snapshot.reset_revision,
        turn=_turn(turn_id="turn-after-reset"),
    )
    with pytest.raises(ValueError, match="already committed"):
        await store.commit_delivered_turn(
            subject="child:fresh-reset-epoch",
            expected_reset_revision=snapshot.reset_revision,
            turn=_turn(turn_id="turn-after-reset"),
        )

    after = await store.load_snapshot(
        subject="child:fresh-reset-epoch",
        age_band="middleChildhood",
    )
    assert [turn.turn_id for turn in after.delivered_turns] == ["turn-after-reset"]


async def test_timezone_round_trip_uses_canonical_utc_instant() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    occurred_at = datetime.fromisoformat("2026-08-31T12:30:00+05:30")
    await store.commit_delivered_turn(
        subject="child:timezone",
        expected_reset_revision=0,
        turn=_turn(turn_id="turn-timezone"),
        occurred_at=occurred_at,
        timezone_id="Asia/Kolkata",
    )

    stored = store._turns["child:timezone"][0]
    assert stored.occurred_at == datetime(2026, 8, 31, 7, 0, tzinfo=UTC)
    assert stored.occurred_at.tzinfo is UTC
    assert stored.timezone_id == "Asia/Kolkata"


async def test_invalid_timezone_is_rejected_before_memory_write() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    with pytest.raises(ValueError, match="IANA timezone"):
        await store.commit_delivered_turn(
            subject="child:timezone",
            expected_reset_revision=0,
            turn=_turn(turn_id="turn-invalid-timezone"),
            timezone_id="Mars/Olympus_Mons",
        )
    assert store._turns["child:timezone"] == []


async def test_candidate_search_prefers_text_relevance_and_applies_utc_bounds() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    base = datetime(2025, 1, 1, tzinfo=UTC)
    await store.commit_delivered_turn(
        subject="child:search",
        expected_reset_revision=0,
        turn=_turn(
            turn_id="turn-old-relevant",
            child_text="Saturn has beautiful rings.",
            delivered_text="Saturn's rings are made of ice and rock.",
        ).model_copy(update={"child_turn_summary": "Discussed Saturn rings."}),
        occurred_at=base,
        timezone_id="Europe/London",
    )
    await store.commit_delivered_turn(
        subject="child:search",
        expected_reset_revision=0,
        turn=_turn(
            turn_id="turn-new-unrelated",
            child_text="Tell me about bread.",
            delivered_text="Bread rises when yeast makes gas.",
        ).model_copy(update={"child_turn_summary": "Discussed baking bread."}),
        occurred_at=base + timedelta(days=100),
        timezone_id="America/New_York",
    )

    candidates = await store.search_memory_candidates(
        subject="child:search",
        query="Saturn rings ice",
        from_instant=base - timedelta(seconds=1),
        to_instant=base + timedelta(days=1),
        limit=5,
    )

    assert [candidate.source_id for candidate in candidates] == ["turn-old-relevant"]
    assert candidates[0].source_kind == "rawEvent"
    assert candidates[0].timezone_ids == ["Europe/London"]
    assert candidates[0].source_turn_ids == ["turn-old-relevant"]


@pytest.mark.parametrize("query", ["???", "!@#"])
async def test_development_search_rejects_non_lexical_queries_safely(
    query: str,
) -> None:
    store = DevelopmentAuthoritativeMemoryStore()

    with pytest.raises(ValueError, match="must contain searchable text"):
        await store.search_memory_candidates(
            subject="child:non-lexical-search",
            query=query,
        )


async def test_reset_clears_compacted_episodes() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    for index in range(1, 256 + 2):
        await store.commit_delivered_turn(
            subject="child:episode-reset",
            expected_reset_revision=0,
            turn=_turn(turn_id=f"turn-{index}"),
            timezone_id="UTC",
        )
    assert len(store._turns["child:episode-reset"]) == 257
    assert not store._episodes["child:episode-reset"]

    await store.reset_conversation(subject="child:episode-reset")

    assert store._episodes["child:episode-reset"] == []


async def test_development_episode_retention_and_raw_overlap_deduplication() -> None:
    store = DevelopmentAuthoritativeMemoryStore()
    subject = "child:episode-retention"
    now = datetime.now(UTC)
    await store.commit_delivered_turn(
        subject=subject,
        expected_reset_revision=0,
        turn=_turn(
            turn_id="turn-overlap",
            child_text="Saturn has rings.",
            delivered_text="Saturn's rings contain ice.",
        ),
        occurred_at=now,
        timezone_id="UTC",
    )
    overlap = ConversationEpisode.model_validate(
        {
            "subject": subject,
            "episodeId": "episode-overlap",
            "startedAt": now,
            "endedAt": now,
            "timezoneId": "UTC",
            "digest": "Saturn rings ice",
            "sourceTurnIds": ["turn-overlap"],
            "sourceEventCount": 1,
            "createdAt": now,
        }
    )
    store._episodes[subject].append(overlap)
    candidates = await store.search_memory_candidates(
        subject=subject,
        query="Saturn rings ice",
    )
    assert [candidate.source_kind for candidate in candidates] == ["rawEvent"]

    store._episodes[subject] = [
        ConversationEpisode.model_validate(
            {
                "subject": subject,
                "episodeId": f"episode-{index}",
                "startedAt": (
                    now - timedelta(days=181)
                    if index == 0
                    else now - timedelta(hours=129 - index)
                ),
                "endedAt": (
                    now - timedelta(days=181)
                    if index == 0
                    else now - timedelta(hours=129 - index)
                ),
                "timezoneId": "UTC",
                "digest": f"retained episode {index}",
                "sourceTurnIds": [f"turn-episode-{index}"],
                "sourceEventCount": 1,
                "createdAt": now,
            }
        )
        for index in range(130)
    ]
    await store.commit_delivered_turn(
        subject=subject,
        expected_reset_revision=0,
        turn=_turn(turn_id="turn-prune-trigger"),
        occurred_at=now,
        timezone_id="UTC",
    )
    retained = store._episodes[subject]
    assert len(retained) == 130
    assert min(episode.ended_at for episode in retained) == now - timedelta(days=181)
