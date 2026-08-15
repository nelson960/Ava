from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class QueueCapacityExceeded(RuntimeError):
    """Raised instead of silently buffering beyond a realtime queue limit."""


class QueueClosed(RuntimeError):
    """Raised when reading or writing a closed bounded realtime queue."""


class BoundedAsyncQueue(Generic[T]):
    def __init__(
        self,
        *,
        max_items: int,
        max_weight: int,
        weigh: Callable[[T], int],
    ) -> None:
        if max_items <= 0 or max_weight <= 0:
            raise ValueError("queue limits must be positive")
        self._queue: asyncio.Queue[T | object] = asyncio.Queue(maxsize=max_items)
        self._max_weight = max_weight
        self._weigh = weigh
        self._weight = 0
        self._closed = False
        self._sentinel = object()

    @property
    def item_count(self) -> int:
        return self._queue.qsize() - (
            1 if self._closed and not self._queue.empty() else 0
        )

    @property
    def weight(self) -> int:
        return self._weight

    def put_nowait(self, item: T) -> None:
        if self._closed:
            raise QueueClosed("cannot write to a closed realtime queue")
        weight = self._weigh(item)
        if weight <= 0:
            raise ValueError("queue item weight must be positive")
        if self._queue.full() or self._weight + weight > self._max_weight:
            raise QueueCapacityExceeded(
                "realtime queue capacity exceeded "
                f"(items={self._queue.qsize()}, weight={self._weight}, "
                f"incoming={weight})"
            )
        self._queue.put_nowait(item)
        self._weight += weight

    async def get(self) -> T:
        item = await self._queue.get()
        if item is self._sentinel:
            self._queue.task_done()
            raise QueueClosed("realtime queue is closed")
        typed_item = item  # keep the narrowing explicit for type checkers
        weight = self._weigh(typed_item)  # type: ignore[arg-type]
        self._weight -= weight
        self._queue.task_done()
        return typed_item  # type: ignore[return-value]

    def close(self) -> None:
        if self._closed:
            return
        if self._queue.full():
            raise QueueCapacityExceeded(
                "cannot close a full realtime queue before it is drained"
            )
        self._closed = True
        self._queue.put_nowait(self._sentinel)
