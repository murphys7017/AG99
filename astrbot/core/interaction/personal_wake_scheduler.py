from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from astrbot import logger

if TYPE_CHECKING:
    from .personal_runtime import PersonalRuntimeKey


class PersonalRuntimeWakeScheduler:
    """One lifecycle-owned scheduler for deferred Runtime Observation batches."""

    def __init__(
        self,
        wake_runtime: Callable[[PersonalRuntimeKey], Awaitable[None]],
    ) -> None:
        self._wake_runtime = wake_runtime
        self._scheduled: dict[PersonalRuntimeKey, float] = {}
        self._heap: list[tuple[float, int, PersonalRuntimeKey]] = []
        self._sequence = itertools.count()
        self._changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("Personal Runtime wake scheduler is closed")
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run(),
                name="personal_runtime_wake_scheduler",
            )

    def schedule(self, key: PersonalRuntimeKey, due_at: float) -> None:
        if self._closed:
            return
        normalized_due_at = max(time.time(), float(due_at))
        current_due_at = self._scheduled.get(key)
        if current_due_at is not None and current_due_at <= normalized_due_at:
            return
        self._scheduled[key] = normalized_due_at
        heapq.heappush(
            self._heap,
            (normalized_due_at, next(self._sequence), key),
        )
        self._changed.set()

    def cancel(self, key: PersonalRuntimeKey) -> None:
        if self._scheduled.pop(key, None) is not None:
            self._changed.set()

    async def shutdown(self) -> None:
        self._closed = True
        self._scheduled.clear()
        self._heap.clear()
        self._changed.set()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _run(self) -> None:
        while not self._closed:
            self._changed.clear()
            due_at = self._next_due_at()
            if due_at is None:
                await self._changed.wait()
                continue
            delay = max(0.0, due_at - time.time())
            if delay > 0:
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=delay)
                    continue
                except asyncio.TimeoutError:
                    pass
            for key in self._pop_due_keys(now=time.time()):
                try:
                    await self._wake_runtime(key)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Personal Runtime observation wake failed: config_id=%s "
                        "persona_id=%s audience=%s",
                        key.config_id,
                        key.persona_id,
                        key.audience_key,
                    )

    def _next_due_at(self) -> float | None:
        while self._heap:
            due_at, _, key = self._heap[0]
            if self._scheduled.get(key) == due_at:
                return due_at
            heapq.heappop(self._heap)
        return None

    def _pop_due_keys(self, *, now: float) -> list[PersonalRuntimeKey]:
        due_keys: list[PersonalRuntimeKey] = []
        while self._heap:
            due_at, _, key = self._heap[0]
            if due_at > now:
                break
            heapq.heappop(self._heap)
            if self._scheduled.get(key) != due_at:
                continue
            self._scheduled.pop(key, None)
            due_keys.append(key)
        return due_keys


__all__ = ["PersonalRuntimeWakeScheduler"]
