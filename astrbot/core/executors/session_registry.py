"""Core-owned registry for long-lived external executor sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .codex_session import CodexSessionManager
from .external import ExternalExecutorSessionKey


class ExternalExecutorSessionRegistry:
    """Own and reuse one external session per complete execution key."""

    def __init__(self) -> None:
        self._sessions: dict[ExternalExecutorSessionKey, CodexSessionManager] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def get_or_create(
        self,
        *,
        key: ExternalExecutorSessionKey,
        executor_config: Mapping[str, Any],
    ) -> CodexSessionManager:
        if self._closed:
            raise RuntimeError("external executor session registry is closed")
        if key.executor_id != "codex_cli":
            raise ValueError(f"unsupported external executor: {key.executor_id}")
        async with self._lock:
            existing = self._sessions.get(key)
            if existing is not None:
                return existing
            manager = CodexSessionManager(
                executable=_resolve_executable(executor_config),
                cwd=key.workspace,
            )
            await manager.start()
            self._sessions[key] = manager
            return manager

    async def discard(self, key: ExternalExecutorSessionKey) -> None:
        async with self._lock:
            manager = self._sessions.pop(key, None)
        if manager is not None:
            await manager.aclose()

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions = list(self._sessions.values())
            self._sessions.clear()
        if sessions:
            await asyncio.gather(*(manager.aclose() for manager in sessions), return_exceptions=True)

    def __len__(self) -> int:
        return len(self._sessions)


def _resolve_executable(config: Mapping[str, Any]) -> str:
    executable = config.get("executable", "codex")
    if not isinstance(executable, str) or not executable.strip():
        raise ValueError("codex_cli.executable must be a non-empty executable name")
    executable = executable.strip()
    if any(char.isspace() for char in executable):
        raise ValueError("codex_cli.executable must not contain command arguments")
    if Path(executable).name != executable and not Path(executable).is_absolute():
        raise ValueError("codex_cli.executable must be a command name or absolute path")
    return executable


__all__ = ["ExternalExecutorSessionRegistry"]
