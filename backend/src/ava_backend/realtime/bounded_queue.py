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
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=max_items)
        self._max_weight = max_weight
        self._weigh = weigh
        self._weight = 0
        self._closed = False
        self._closed_event = asyncio.Event()

    @property
    def item_count(self) -> int:
        return self._queue.qsize()

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
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                if self._closed:
                    raise QueueClosed("realtime queue is closed") from None
            else:
                return self._finish_get(item)

            item_available = asyncio.create_task(self._queue.get())
            queue_closed = asyncio.create_task(self._closed_event.wait())
            try:
                await asyncio.wait(
                    {item_available, queue_closed},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if item_available.done():
                    return self._finish_get(item_available.result())
            finally:
                for task in (item_available, queue_closed):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(
                    item_available,
                    queue_closed,
                    return_exceptions=True,
                )

    def _finish_get(self, item: T) -> T:
        self._weight -= self._weigh(item)
        self._queue.task_done()
        return item

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._closed_event.set()
