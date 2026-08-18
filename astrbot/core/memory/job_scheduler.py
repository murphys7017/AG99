from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from astrbot.core import logger


@dataclass(frozen=True, slots=True)
class MemoryScopeJob:
    owner_id: str
    scope_type: str
    scope_id: str
    conversation_id: str | None
    umo: str

    @property
    def scope_key(self) -> tuple[str, str, str]:
        return (self.owner_id, self.scope_type, self.scope_id)

    @property
    def conversation_key(self) -> str:
        return self.conversation_id or ""


class MemoryJobScheduler:
    """Serialize memory jobs by scope while coalescing repeated conversations."""

    def __init__(
        self,
        runner: Callable[[MemoryScopeJob], Awaitable[None]],
    ) -> None:
        self._runner = runner
        self._lock = asyncio.Lock()
        self._tasks: dict[tuple[str, str, str], asyncio.Task[None]] = {}
        self._pending: dict[
            tuple[str, str, str], dict[str, MemoryScopeJob]
        ] = defaultdict(dict)
        self._closed = False
        self._submitted = 0
        self._coalesced = 0
        self._completed = 0
        self._failed = 0

    async def submit(self, job: MemoryScopeJob) -> bool:
        async with self._lock:
            if self._closed:
                logger.debug(
                    "memory job rejected during shutdown: scope=%s conversation_id=%s",
                    job.scope_key,
                    job.conversation_id,
                )
                return False

            pending_for_scope = self._pending[job.scope_key]
            if job.conversation_key in pending_for_scope:
                self._coalesced += 1
            pending_for_scope[job.conversation_key] = job
            self._submitted += 1
            task = self._tasks.get(job.scope_key)
            if task is None or task.done():
                self._tasks[job.scope_key] = asyncio.create_task(
                    self._run_scope(job.scope_key),
                    name="memory-scope-job",
                )
        return True

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            tasks = list(self._tasks.values())
            self._pending.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            self._tasks.clear()

    async def wait_for_scope(self, scope_key: tuple[str, str, str]) -> None:
        """Wait until all currently queued work for one scope is settled."""
        while True:
            async with self._lock:
                task = self._tasks.get(scope_key)
                has_pending = bool(self._pending.get(scope_key))
            if task is None:
                if not has_pending:
                    return
                await asyncio.sleep(0)
                continue
            await asyncio.shield(task)

    def diagnostics(self) -> dict[str, int]:
        return {
            "active_scopes": len(self._tasks),
            "pending_jobs": sum(len(items) for items in self._pending.values()),
            "submitted": self._submitted,
            "coalesced": self._coalesced,
            "completed": self._completed,
            "failed": self._failed,
        }

    async def _run_scope(self, scope_key: tuple[str, str, str]) -> None:
        current_task = asyncio.current_task()
        try:
            while True:
                async with self._lock:
                    jobs = list(self._pending.pop(scope_key, {}).values())
                if not jobs:
                    return

                for job in jobs:
                    try:
                        await self._runner(job)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        self._failed += 1
                        logger.error(
                            "memory scope job failed: scope=%s conversation_id=%s error=%s",
                            scope_key,
                            job.conversation_id,
                            exc,
                            exc_info=True,
                        )
                    else:
                        self._completed += 1
        finally:
            async with self._lock:
                if self._tasks.get(scope_key) is current_task:
                    self._tasks.pop(scope_key, None)
                if self._closed:
                    self._pending.pop(scope_key, None)
                elif self._pending.get(scope_key):
                    self._tasks[scope_key] = asyncio.create_task(
                        self._run_scope(scope_key),
                        name="memory-scope-job",
                    )


__all__ = ["MemoryJobScheduler", "MemoryScopeJob"]
