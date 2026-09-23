"""Long-lived local Codex app-server JSON-RPC session."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from astrbot import logger

from .codex_protocol import (
    decode_message,
    encode_notification,
    encode_request,
    parse_error,
)


class CodexSessionError(RuntimeError):
    """Raised when a Codex app-server session cannot continue."""


class CodexSessionManager:
    """Own one app-server process and its request/notification lifecycle."""

    def __init__(
        self,
        *,
        executable: str = "codex",
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        request_timeout: float = 30.0,
        stderr_limit: int = 16_384,
        max_message_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self.executable = executable
        self.cwd = str(cwd) if cwd is not None else None
        self.env = dict(env) if env is not None else None
        self.request_timeout = request_timeout
        self.stderr_limit = max(1024, stderr_limit)
        self.max_message_bytes = max(1024, max_message_bytes)
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._request_id = 0
        self._write_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._closed = False
        self._reader_failure: CodexSessionError | None = None
        self._stderr_tail = bytearray()
        self._stderr_truncated = False
        self.thread_id: str | None = None
        self._active_turn_id: str | None = None

    async def start(self) -> None:
        async with self._start_lock:
            if self._closed:
                raise CodexSessionError("Codex session is closed")
            if self._process is not None:
                return
            try:
                self._process = await asyncio.create_subprocess_exec(
                    self.executable,
                    "app-server",
                    "--stdio",
                    cwd=self.cwd,
                    env=self.env or os.environ.copy(),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    # Keep StreamReader's line limit above the protocol limit
                    # so oversized messages reach our explicit size check.
                    limit=self.max_message_bytes + 1,
                )
            except OSError as exc:
                raise CodexSessionError(f"failed to start Codex app-server: {exc}") from exc
            self._reader_failure = None
            self._reader_task = asyncio.create_task(self._read_stdout())
            self._stderr_task = asyncio.create_task(self._read_stderr())
            try:
                await self._request(
                    "initialize",
                    {
                        "clientInfo": {"name": "astrbot", "version": "core"},
                        "capabilities": {"experimentalApi": False},
                    },
                )
                await self._notify("initialized")
            except BaseException:
                # Do not leave an unregistered app-server process behind when
                # initialization or protocol negotiation fails.
                with contextlib.suppress(BaseException):
                    await asyncio.shield(self.aclose())
                raise

    async def start_thread(self, *, cwd: str | None = None) -> str:
        await self.start()
        result = await self._request("thread/start", {"cwd": cwd or self.cwd})
        thread = result.get("thread") if isinstance(result, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else result.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise CodexSessionError("Codex thread/start returned no thread id")
        self.thread_id = thread_id
        return thread_id

    async def resume_thread(self, thread_id: str) -> str:
        await self.start()
        result = await self._request("thread/resume", {"threadId": thread_id})
        resumed = result.get("thread") if isinstance(result, dict) else None
        resolved_id = resumed.get("id") if isinstance(resumed, dict) else thread_id
        if not isinstance(resolved_id, str) or not resolved_id:
            raise CodexSessionError("Codex thread/resume returned no thread id")
        self.thread_id = resolved_id
        return resolved_id

    async def iter_turn(self, prompt: str) -> AsyncIterator[dict[str, Any]]:
        """Start one turn and yield notifications until its terminal event."""

        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Codex turn prompt must be a non-empty string")
        if self.thread_id is None:
            await self.start_thread()
        async with self._turn_lock:
            result = await self._request(
                "turn/start",
                {"threadId": self.thread_id, "input": [{"type": "text", "text": prompt}]},
            )
            turn = result.get("turn") if isinstance(result, dict) else None
            self._active_turn_id = (
                str(turn.get("id"))
                if isinstance(turn, dict) and turn.get("id")
                else None
            )
            try:
                while True:
                    message = await self._notifications.get()
                    if isinstance(message, BaseException):
                        raise message
                    params = message.get("params")
                    if (
                        self._active_turn_id
                        and isinstance(params, dict)
                        and params.get("turnId")
                        and str(params["turnId"]) != self._active_turn_id
                    ):
                        continue
                    yield message
                    method = message.get("method")
                    if method in {"turn/completed", "turn/failed", "turn/cancelled"}:
                        return
            finally:
                self._active_turn_id = None

    async def interrupt(self, turn_id: str | None = None) -> None:
        if self._process is None:
            return
        params = {"turnId": turn_id or self._active_turn_id} if (turn_id or self._active_turn_id) else None
        await self._request("turn/interrupt", params)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        self._process = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(CodexSessionError("Codex session closed"))
        self._pending.clear()
        tasks = tuple(
            task
            for task in (self._reader_task, self._stderr_task)
            if task is not None
        )
        for task in tasks:
            task.cancel()
        if process is not None:
            if process.returncode is None:
                process.terminate()
                with contextlib.suppress(ProcessLookupError):
                    await asyncio.wait_for(process.wait(), timeout=2)
                if process.returncode is None:
                    process.kill()
            with contextlib.suppress(Exception):
                await process.wait()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._reader_task = None
        self._stderr_task = None
        if self._stderr_truncated:
            logger.warning(
                "Codex app-server stderr exceeded its diagnostic limit; "
                "retaining only the final %s bytes",
                len(self._stderr_tail),
            )

    async def _request(self, method: str, params: Any = None) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None:
            raise CodexSessionError("Codex app-server is not running")
        self._request_id += 1
        request_id = self._request_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        try:
            async with self._write_lock:
                process.stdin.write(encode_request(request_id, method, params))
                await process.stdin.drain()
            response = await asyncio.wait_for(future, timeout=self.request_timeout)
        except asyncio.TimeoutError as exc:
            raise CodexSessionError(f"Codex request timed out: {method}") from exc
        finally:
            self._pending.pop(request_id, None)
        error = parse_error(response)
        if error is not None:
            raise CodexSessionError(f"Codex {method} failed ({error.code}): {error.message}")
        return response.get("result") or {}

    async def _notify(self, method: str, params: Any = None) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise CodexSessionError("Codex app-server is not running")
        async with self._write_lock:
            process.stdin.write(encode_notification(method, params))
            await process.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while True:
                line = await self._process.stdout.readuntil(b"\n")
                if len(line) > self.max_message_bytes:
                    raise CodexSessionError("Codex app-server message exceeds size limit")
                if not line:
                    raise CodexSessionError("Codex app-server closed stdout")
                message = decode_message(line)
                request_id = message.get("id")
                if isinstance(request_id, int) and request_id in self._pending:
                    future = self._pending[request_id]
                    if not future.done():
                        future.set_result(message)
                elif "method" in message:
                    await self._notifications.put(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._reader_failure = exc if isinstance(exc, CodexSessionError) else CodexSessionError(str(exc))
            await self._notifications.put(self._reader_failure)
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(exc)

    async def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        limit = self.stderr_limit
        while True:
            chunk = await self._process.stderr.read(4096)
            if not chunk:
                return
            self._stderr_tail.extend(chunk)
            excess = len(self._stderr_tail) - limit
            if excess > 0:
                del self._stderr_tail[:excess]
                self._stderr_truncated = True

    @property
    def stderr_diagnostics(self) -> tuple[bytes, bool]:
        """Return a bounded stderr tail and whether earlier output was dropped."""

        return bytes(self._stderr_tail), self._stderr_truncated


__all__ = ["CodexSessionError", "CodexSessionManager"]
