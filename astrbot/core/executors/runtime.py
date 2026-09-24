"""Shared in-process driver for Executor Body runs.

The driver owns activation, terminal projection, cleanup, and release. It does
not render platform messages or decide whether a turn should be admitted.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from typing import TYPE_CHECKING

from astrbot import logger
from astrbot.core.deadline import TurnDeadlineBudget, TurnDeadlineExceeded
from astrbot.core.execution import (
    CoreExecutionArtifact,
    CoreExecutionHead,
)

from .contracts import ExecutionFinalUpdate, ExecutionResult, ExecutionUpdate

if TYPE_CHECKING:
    from astrbot.core.execution import CoreExecutorBody
    from astrbot.core.interaction.turn_state import TurnExecutionScope

    from .contracts import ExecutorRun


OutputSink = Callable[[ExecutionUpdate], Awaitable[None] | None]


async def drive_executor_run(
    *,
    head: CoreExecutionHead | None,
    body: CoreExecutorBody,
    run: ExecutorRun,
    deadline: TurnDeadlineBudget | None = None,
    submission_metadata: dict | None = None,
    output_sink: OutputSink | None = None,
    cleanup_scope: TurnExecutionScope | None = None,
) -> ExecutionResult:
    """Drive one Body and settle its Core lifecycle exactly once.

    ``head`` may be omitted for transitional legacy callers. In that mode the
    Body is still closed deterministically, but no Core terminal fact is
    manufactured. A successful run must emit exactly one final update.
    """

    activated = False
    terminal_projected = False
    final_result: ExecutionResult | None = None
    try:
        if head is not None:
            head.activate_executor_body(
                body,
                submission_metadata=submission_metadata,
            )
            activated = True

        async def consume() -> ExecutionResult:
            nonlocal final_result
            async with aclosing(run.stream()) as updates:
                async for update in updates:
                    if (
                        head is not None
                        and getattr(head, "terminal_event", None) is not None
                    ):
                        raise asyncio.CancelledError(
                            "executor emitted an update after Core termination"
                        )
                    if isinstance(update, ExecutionFinalUpdate):
                        if final_result is not None:
                            raise RuntimeError("executor emitted multiple final updates")
                        final_result = update.result
                        continue
                    if final_result is not None:
                        raise RuntimeError(
                            "executor emitted an update after its final result"
                        )
                    if output_sink is not None:
                        sink_result = output_sink(update)
                        if sink_result is not None:
                            await sink_result
            if final_result is None:
                raise RuntimeError("executor ended without a final result")
            return final_result

        if deadline is None:
            result = await consume()
        else:
            async with deadline.enforce("core_executor_run"):
                result = await consume()

        if head is not None and activated:
            artifact = result.artifacts[0] if result.artifacts else None
            head.complete(
                executor_id=body.executor_id,
                artifact=(
                    artifact if isinstance(artifact, CoreExecutionArtifact) else None
                ),
            )
            terminal_projected = True
        if output_sink is not None:
            sink_result = output_sink(ExecutionFinalUpdate(result=result))
            if sink_result is not None:
                await sink_result
        return result
    except TurnDeadlineExceeded as exc:
        _request_run_stop(run)
        if (
            head is not None
            and activated
            and not terminal_projected
            and getattr(head, "terminal_event", None) is None
        ):
            head.cancel(
                executor_id=body.executor_id,
                metadata={"reason": "deadline_exceeded", "error": str(exc)},
            )
        raise
    except asyncio.CancelledError:
        _request_run_stop(run)
        if (
            head is not None
            and activated
            and not terminal_projected
            and getattr(head, "terminal_event", None) is None
        ):
            head.cancel(
                executor_id=body.executor_id,
                metadata={"reason": "run_cancelled"},
            )
        raise
    except Exception as exc:
        _request_run_stop(run)
        if (
            head is not None
            and activated
            and not terminal_projected
            and getattr(head, "terminal_event", None) is None
        ):
            head.fail(
                executor_id=body.executor_id,
                metadata={
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:2000],
                },
            )
        raise
    finally:
        async def close_run() -> None:
            try:
                await run.aclose()
            except BaseException:
                invalidate = getattr(run, "invalidate", None)
                if callable(invalidate):
                    invalidate()
                logger.warning(
                    "Executor run cleanup failed: executor_id=%s",
                    body.executor_id,
                    exc_info=True,
                )
                raise
            else:
                if head is not None and activated:
                    head.release_executor(executor_id=body.executor_id)

        cleanup = (
            cleanup_scope.create_task(
                close_run(),
                role="executor_cleanup",
                name=f"executor-cleanup:{body.executor_id}",
            )
            if cleanup_scope is not None
            else asyncio.create_task(
                close_run(), name=f"executor-cleanup:{body.executor_id}"
            )
        )
        try:
            _, pending = await asyncio.wait({cleanup}, timeout=1.0)
        except asyncio.CancelledError:
            cleanup.cancel()
            raise
        if pending:
            invalidate = getattr(run, "invalidate", None)
            if callable(invalidate):
                invalidate()
            logger.warning(
                "Executor cleanup still pending; binding retained: executor_id=%s",
                body.executor_id,
            )


def _request_run_stop(run: ExecutorRun) -> None:
    """Issue the non-blocking Body stop signal before projecting terminal state."""

    try:
        run.request_stop()
    except Exception:
        logger.warning("Executor stop request failed", exc_info=True)
