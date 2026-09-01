from __future__ import annotations

import asyncio
import random

import pytest

from ava_backend.realtime.bounded_queue import (
    BoundedAsyncQueue,
    QueueCapacityExceeded,
    QueueClosed,
)
from ava_backend.realtime.fakes import DeterministicArgumentDeltaStream
from ava_backend.realtime.turn_parser import (
    StreamingSpeechParser,
    StreamingTurnParseError,
)


@pytest.mark.asyncio
async def test_streaming_speech_parser_is_fragmentation_independent() -> None:
    speech = "First thought. Second thought? Final thought!"
    random_source = random.Random(20260729)
    expected = ["First thought.", "Second thought?", "Final thought!"]
    for _ in range(40):
        parser = StreamingSpeechParser()
        emitted = []
        sizes = tuple(random_source.randint(1, 17) for _ in range(8))
        stream = DeterministicArgumentDeltaStream(speech, sizes)
        async for delta in stream.stream():
            emitted.extend(parser.feed(delta))
        emitted.extend(parser.finish())
        assert [phrase.speech for phrase in emitted] == expected


def test_streaming_speech_parser_allows_more_than_legacy_phrase_limit() -> None:
    speech = " ".join(f"Thought {index}." for index in range(1, 14))
    parser = StreamingSpeechParser()

    emitted = list(parser.feed(speech))
    emitted.extend(parser.finish())

    assert [phrase.speech for phrase in emitted] == [
        f"Thought {index}." for index in range(1, 14)
    ]


def test_streaming_speech_parser_safely_splits_one_long_phrase() -> None:
    speech = ("gentle words " * 58).strip()
    parser = StreamingSpeechParser(max_total_characters=1_000)
    emitted = list(parser.feed(speech))
    emitted.extend(parser.finish())

    assert len(emitted) == 2
    assert " ".join(phrase.speech for phrase in emitted) == speech
    assert all(len(phrase.speech) <= 600 for phrase in emitted)


def test_streaming_speech_parser_preserves_response_boundaries() -> None:
    parser = StreamingSpeechParser()
    assert parser.feed("I’ll draw that for you") == ()
    bridge = parser.finish_response_chunk()
    continuation = parser.feed("Here is the finished picture.")
    ending = parser.finish()

    assert [phrase.speech for phrase in (*bridge, *continuation, *ending)] == [
        "I’ll draw that for you",
        "Here is the finished picture.",
    ]


def test_streaming_speech_parser_rejects_invalid_streams() -> None:
    with pytest.raises(StreamingTurnParseError, match="control"):
        StreamingSpeechParser().feed("hello\u0000")

    with pytest.raises(StreamingTurnParseError, match="1800"):
        StreamingSpeechParser().feed("word " * 360 + "x")

    with pytest.raises(StreamingTurnParseError, match="600 characters"):
        StreamingSpeechParser().feed("x" * 601)

    parser = StreamingSpeechParser(max_total_characters=10, max_segment_characters=10)
    with pytest.raises(StreamingTurnParseError, match="1800"):
        parser.feed("eleven chars")

    with pytest.raises(StreamingTurnParseError, match="without child-facing speech"):
        StreamingSpeechParser().finish()

    parser = StreamingSpeechParser()
    parser.feed("Done.")
    parser.finish()
    with pytest.raises(StreamingTurnParseError, match="finished"):
        parser.feed("Again.")


@pytest.mark.asyncio
async def test_bounded_queue_capacity_and_close_are_deterministic() -> None:
    queue: BoundedAsyncQueue[bytes] = BoundedAsyncQueue(
        max_items=2,
        max_weight=4,
        weigh=len,
    )
    queue.put_nowait(b"ab")
    queue.put_nowait(b"cd")
    with pytest.raises(QueueCapacityExceeded):
        queue.put_nowait(b"e")
    assert await queue.get() == b"ab"
    queue.close()
    queue.close()
    with pytest.raises(QueueClosed, match="cannot write"):
        queue.put_nowait(b"e")
    assert await queue.get() == b"cd"
    assert queue.item_count == 0
    assert queue.weight == 0
    with pytest.raises(QueueClosed):
        await queue.get()


@pytest.mark.asyncio
async def test_bounded_queue_close_wakes_an_already_blocked_getter() -> None:
    queue: BoundedAsyncQueue[bytes] = BoundedAsyncQueue(
        max_items=2,
        max_weight=4,
        weigh=len,
    )
    getter_started = asyncio.Event()

    async def get_blocked() -> bytes:
        getter_started.set()
        return await queue.get()

    getter = asyncio.create_task(get_blocked())
    await getter_started.wait()
    checkpoint = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(checkpoint.set_result, None)
    await checkpoint
    assert not getter.done()

    queue.close()

    with pytest.raises(QueueClosed, match="closed"):
        await asyncio.wait_for(getter, timeout=0.5)


@pytest.mark.asyncio
async def test_bounded_queue_full_close_drains_items_before_closed() -> None:
    queue: BoundedAsyncQueue[bytes] = BoundedAsyncQueue(
        max_items=2,
        max_weight=4,
        weigh=len,
    )
    queue.put_nowait(b"a")
    queue.put_nowait(b"bcd")
    assert queue.item_count == 2
    assert queue.weight == 4

    queue.close()
    queue.close()

    assert queue.item_count == 2
    assert queue.weight == 4
    assert await queue.get() == b"a"
    assert queue.item_count == 1
    assert queue.weight == 3
    assert await queue.get() == b"bcd"
    assert queue.item_count == 0
    assert queue.weight == 0
    with pytest.raises(QueueClosed, match="closed"):
        await queue.get()
    with pytest.raises(QueueClosed, match="cannot write"):
        queue.put_nowait(b"x")
