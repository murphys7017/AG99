from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any


class TurnDeadlineExceeded(TimeoutError):
    """Raised when a turn has no execution budget left."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.reason = "turn_deadline_exhausted"
        super().__init__(f"turn deadline exhausted during {stage}")


@dataclass(frozen=True, slots=True)
class TurnStageBudget:
    """A stage-local view bounded by the owning turn deadline."""

    name: str
    started_at: float
    deadline_at: float
    turn_deadline_at: float
    configured_limit: float | None
    turn_limited: bool
    _clock: Callable[[], float] = field(repr=False, compare=False)

    def remaining(self) -> float:
        return max(0.0, self.deadline_at - self._clock())

    def timeout_seconds(self) -> float:
        remaining = self.remaining()
        if remaining <= 0:
            raise TurnDeadlineExceeded(self.name)
        return remaining


@dataclass(slots=True)
class TurnDeadlineBudget:
    """One monotonically decreasing wall-clock budget for an interaction turn."""

    total_seconds: float
    started_at: float
    deadline_at: float
    _clock: Callable[[], float] = field(repr=False, compare=False)
    _stages: list[dict[str, Any]] = field(default_factory=list, repr=False)

    @classmethod
    def start(
        cls,
        total_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> TurnDeadlineBudget:
        total = max(0.001, float(total_seconds))
        started_at = clock()
        return cls(
            total_seconds=total,
            started_at=started_at,
            deadline_at=started_at + total,
            _clock=clock,
        )

    def elapsed(self) -> float:
        return max(0.0, self._clock() - self.started_at)

    def remaining(self) -> float:
        return max(0.0, self.deadline_at - self._clock())

    def expired(self) -> bool:
        return self.remaining() <= 0

    def timeout_seconds(self, stage: str = "turn") -> float:
        remaining = self.remaining()
        if remaining <= 0:
            raise TurnDeadlineExceeded(stage)
        return remaining

    @contextmanager
    def stage(
        self,
        name: str,
        configured_limit: float | None = None,
    ) -> Iterator[TurnStageBudget]:
        now = self._clock()
        remaining = self.deadline_at - now
        if remaining <= 0:
            self._stages.append(
                {
                    "name": name,
                    "configured_limit": configured_limit,
                    "allocated_seconds": 0.0,
                    "turn_limited": True,
                    "elapsed_seconds": 0.0,
                    "status": "deadline_exhausted",
                }
            )
            raise TurnDeadlineExceeded(name)

        limit = None
        if configured_limit is not None:
            limit = max(0.001, float(configured_limit))
        allocated = min(remaining, limit) if limit is not None else remaining
        turn_limited = limit is None or remaining <= limit
        stage_budget = TurnStageBudget(
            name=name,
            started_at=now,
            deadline_at=now + allocated,
            turn_deadline_at=self.deadline_at,
            configured_limit=limit,
            turn_limited=turn_limited,
            _clock=self._clock,
        )
        record: dict[str, Any] = {
            "name": name,
            "configured_limit": limit,
            "allocated_seconds": allocated,
            "turn_limited": turn_limited,
            "elapsed_seconds": 0.0,
            "status": "running",
        }
        self._stages.append(record)
        try:
            yield stage_budget
        except BaseException as exc:
            if isinstance(exc, StopAsyncIteration):
                # Async iterator exhaustion is a normal stage boundary, not a
                # failed tool or provider request.
                record["status"] = "completed"
            else:
                record["status"] = (
                    "cancelled"
                    if isinstance(exc, asyncio.CancelledError)
                    else "failed"
                )
                record["exception_type"] = type(exc).__name__
            raise
        else:
            record["status"] = "completed"
        finally:
            record["elapsed_seconds"] = max(0.0, self._clock() - now)

    @asynccontextmanager
    async def enforce(
        self,
        name: str,
        configured_limit: float | None = None,
    ) -> AsyncIterator[TurnStageBudget]:
        """Enforce a stage timeout without ever extending the turn deadline."""
        with self.stage(name, configured_limit) as stage_budget:
            timeout = asyncio.timeout(stage_budget.timeout_seconds())
            try:
                async with timeout:
                    yield stage_budget
            except TimeoutError as exc:
                # Preserve TimeoutError raised by the provider or plugin itself.
                if timeout.expired() and stage_budget.turn_limited:
                    raise TurnDeadlineExceeded(name) from exc
                raise

    def snapshot(self) -> dict[str, Any]:
        return {
            "total_seconds": self.total_seconds,
            "elapsed_seconds": self.elapsed(),
            "remaining_seconds": self.remaining(),
            "expired": self.expired(),
            "stages": [dict(stage) for stage in self._stages],
        }


__all__ = [
    "TurnDeadlineBudget",
    "TurnDeadlineExceeded",
    "TurnStageBudget",
]
