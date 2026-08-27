"""Bounded Gateway admission control."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass


class AdmissionRejected(RuntimeError):
    """Raised when both active capacity and bounded waiting capacity are full."""


class AdmissionQueueTimeout(RuntimeError):
    """Raised when a request waits longer than the configured queue timeout."""


@dataclass(frozen=True)
class AdmissionSnapshot:
    active: int
    waiting: int
    max_in_flight: int
    queue_capacity: int


class AdmissionLease:
    def __init__(self, controller: "AdmissionController") -> None:
        self._controller = controller
        self._released = False

    async def __aenter__(self) -> "AdmissionLease":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.release()

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._controller.release()


class AdmissionController:
    """Bound active downstream calls and queued waiters without a worker thread."""

    def __init__(self, *, max_in_flight: int, queue_capacity: int) -> None:
        if not isinstance(max_in_flight, int) or max_in_flight <= 0:
            raise ValueError("max_in_flight must be a positive integer")
        if not isinstance(queue_capacity, int) or queue_capacity < 0:
            raise ValueError("queue_capacity must be a non-negative integer")

        self.max_in_flight = max_in_flight
        self.queue_capacity = queue_capacity
        self._active = 0
        self._queue: deque[asyncio.Future[None]] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self, *, queue_timeout_seconds: float) -> AdmissionLease:
        """Acquire active capacity immediately or through the bounded queue."""

        if queue_timeout_seconds <= 0:
            raise ValueError("queue_timeout_seconds must be positive")

        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[None] | None = None

        async with self._lock:
            if self._active < self.max_in_flight:
                self._active += 1
                return AdmissionLease(self)

            if len(self._queue) >= self.queue_capacity:
                raise AdmissionRejected("gateway admission queue is full")

            waiter = loop.create_future()
            self._queue.append(waiter)

        try:
            await asyncio.wait_for(waiter, timeout=queue_timeout_seconds)
        except TimeoutError as exc:
            await self._remove_waiter(waiter)
            raise AdmissionQueueTimeout("gateway admission queue timed out") from exc
        except asyncio.CancelledError:
            await self._remove_waiter(waiter)
            raise

        return AdmissionLease(self)

    async def release(self) -> None:
        """Release active capacity, transferring it to the next waiter if present."""

        async with self._lock:
            while self._queue:
                waiter = self._queue.popleft()
                if waiter.cancelled() or waiter.done():
                    continue
                waiter.set_result(None)
                return

            if self._active <= 0:
                raise RuntimeError("gateway admission release without acquire")
            self._active -= 1

    async def _remove_waiter(self, waiter: asyncio.Future[None]) -> None:
        async with self._lock:
            try:
                self._queue.remove(waiter)
            except ValueError:
                pass

    async def snapshot(self) -> AdmissionSnapshot:
        async with self._lock:
            return AdmissionSnapshot(
                active=self._active,
                waiting=len(self._queue),
                max_in_flight=self.max_in_flight,
                queue_capacity=self.queue_capacity,
            )
