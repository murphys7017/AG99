"""Executor-neutral runtime contracts.

The contract deliberately carries result material rather than AstrBot message
chains or platform events. Rendering and delivery remain outside an Executor
Body and are introduced through the shared result bridge in a later phase.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from astrbot.core.assets import AssetRef
from astrbot.core.execution import CoreExecutionArtifact
from astrbot.core.provider.entities import TokenUsage


@dataclass(frozen=True, slots=True)
class ExecutionProgressUpdate:
    """A non-visible, executor-neutral execution progress fact."""

    summary: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionOutputMaterial:
    """Final or incremental material awaiting the shared output bridge."""

    text: str | None
    assets: tuple[AssetRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """One successful Executor Body result.

    Completing an executor run does not imply that the result was rendered or
    delivered to a platform. Those outcomes remain owned by existing output
    and delivery boundaries.
    """

    output: ExecutionOutputMaterial | None
    artifacts: tuple[CoreExecutionArtifact, ...] = ()
    token_usage: TokenUsage | None = None


@dataclass(frozen=True, slots=True)
class ExecutionOutputUpdate:
    """An incremental result material update."""

    output: ExecutionOutputMaterial


@dataclass(frozen=True, slots=True)
class ExecutionFinalUpdate:
    """The single successful terminal result produced by an Executor Body."""

    result: ExecutionResult


ExecutionUpdate: TypeAlias = (
    ExecutionProgressUpdate | ExecutionOutputUpdate | ExecutionFinalUpdate
)


class ExecutorRun(Protocol):
    """Minimal Body contract consumed by the future Core run coordinator."""

    executor_id: str

    def stream(self) -> AsyncIterator[ExecutionUpdate]:
        """Yield progress/output facts and exactly one final result on success."""

    def request_stop(self) -> None:
        """Request non-blocking execution cancellation."""

    async def aclose(self) -> None:
        """Release Body-owned execution resources."""
