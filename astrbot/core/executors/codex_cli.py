"""Codex app-server Executor Body adapter."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any, Protocol

from .codex_session import CodexSessionManager
from .contracts import (
    ExecutionFinalUpdate,
    ExecutionOutputMaterial,
    ExecutionProgressUpdate,
    ExecutionResult,
    ExecutionUpdate,
    ExecutorRun,
)


class _CodexSession(Protocol):
    async def iter_turn(self, prompt: str) -> AsyncIterator[dict[str, Any]]: ...

    async def interrupt(self, turn_id: str | None = None) -> None: ...


class CodexExecutorRun:
    """Translate one Codex turn into the executor-neutral run contract."""

    executor_id = "codex_cli"

    def __init__(
        self,
        *,
        session: _CodexSession | CodexSessionManager,
        prompt: str,
    ) -> None:
        self._session = session
        self._prompt = prompt
        self._stop_requested = False
        self._turn_task: asyncio.Task[None] | None = None
        self._interrupt_task: asyncio.Task[None] | None = None
        self._closed = False

    def request_stop(self) -> None:
        self._stop_requested = True
        self._interrupt_task = asyncio.create_task(self._session.interrupt())
        if self._turn_task is None or self._turn_task.done():
            return
        self._turn_task.cancel()

    def request_follow_up(self, message_text: str) -> None:
        del message_text
        return None

    def cancel_follow_up(self, ticket: Any) -> bool:
        del ticket
        return False

    async def aclose(self) -> None:
        self._closed = True
        task = self._turn_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        interrupt_task = self._interrupt_task
        if interrupt_task is not None and not interrupt_task.done():
            interrupt_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await interrupt_task

    async def stream(self) -> AsyncIterator[ExecutionUpdate]:
        if self._closed:
            raise RuntimeError("Codex executor run is closed")
        if self._stop_requested:
            raise asyncio.CancelledError("Codex run was stopped before start")
        self._turn_task = asyncio.current_task()
        text_parts: list[str] = []
        try:
            async for notification in self._session.iter_turn(self._prompt):
                if self._stop_requested:
                    raise asyncio.CancelledError("Codex run was stopped")
                method = str(notification.get("method") or "")
                params = notification.get("params")
                if not isinstance(params, dict):
                    params = {}
                if method == "item/agentMessage/delta":
                    delta = _extract_delta(params)
                    if delta:
                        text_parts.append(delta)
                    continue
                if method in {"turn/started", "item/started", "turn/progress"}:
                    yield ExecutionProgressUpdate(summary=_progress_summary(method, params))
                    continue
                if method == "turn/completed":
                    final_text = "".join(text_parts).strip() or _extract_final_text(params)
                    yield ExecutionFinalUpdate(
                        result=ExecutionResult(
                            output=ExecutionOutputMaterial(text=final_text or None)
                        )
                    )
                    return
                if method in {"turn/failed", "turn/cancelled"}:
                    raise RuntimeError(_failure_message(method, params))
                # New upstream notifications are deliberately ignored.
        finally:
            self._turn_task = None


def build_codex_executor_run(
    *,
    session: _CodexSession | CodexSessionManager,
    prompt: str,
) -> ExecutorRun:
    return CodexExecutorRun(session=session, prompt=prompt)


def _extract_delta(params: dict[str, Any]) -> str:
    value = params.get("delta", params.get("text"))
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        nested = value.get("text", value.get("delta"))
        return nested if isinstance(nested, str) else ""
    return ""


def _extract_final_text(params: dict[str, Any]) -> str:
    for key in ("text", "message", "result"):
        value = params.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            nested = value.get("text", value.get("content"))
            if isinstance(nested, str):
                return nested
    return ""


def _progress_summary(method: str, params: dict[str, Any]) -> str:
    phase = params.get("phase")
    return str(phase or method)


def _failure_message(method: str, params: dict[str, Any]) -> str:
    detail = params.get("error", params.get("message", "Codex turn failed"))
    return f"{method}: {detail}"


__all__ = ["CodexExecutorRun", "build_codex_executor_run"]
