"""Core-owned registry for long-lived external executor sessions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .codex_session import CodexSessionManager
from .external import ExternalExecutorSessionKey


class ExternalExecutorSessionRegistry:
    """Own and reuse one external session per complete execution key."""

    def __init__(self) -> None:
        self._sessions: dict[ExternalExecutorSessionKey, CodexSessionManager] = {}
        self._fingerprints: dict[ExternalExecutorSessionKey, str] = {}
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
            if self._closed:
                raise RuntimeError("external executor session registry is closed")
            existing = self._sessions.get(key)
            fingerprint = _fingerprint_config(executor_config)
            if existing is not None and self._fingerprints.get(key) == fingerprint:
                return existing
            if existing is not None:
                self._sessions.pop(key, None)
                self._fingerprints.pop(key, None)
                await existing.aclose()
            manager = CodexSessionManager(
                executable=_resolve_executable(executor_config),
                cwd=key.workspace,
                request_timeout=_positive_float(
                    executor_config.get("request_timeout", 30.0),
                    field_name="request_timeout",
                ),
                max_message_bytes=_positive_int(
                    executor_config.get("max_message_bytes", 4 * 1024 * 1024),
                    field_name="max_message_bytes",
                ),
            )
            await manager.start()
            self._sessions[key] = manager
            self._fingerprints[key] = fingerprint
            return manager

    async def discard(self, key: ExternalExecutorSessionKey) -> None:
        async with self._lock:
            manager = self._sessions.pop(key, None)
            self._fingerprints.pop(key, None)
        if manager is not None:
            await manager.aclose()

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._fingerprints.clear()
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


def _fingerprint_config(config: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(config), ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError) as exc:
        raise ValueError("external executor configuration must be serializable") from exc


def _positive_float(value: object, *, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"codex_cli.{field_name} must be a positive number") from exc
    if result <= 0:
        raise ValueError(f"codex_cli.{field_name} must be a positive number")
    return result


def _positive_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"codex_cli.{field_name} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"codex_cli.{field_name} must be a positive integer") from exc
    if result <= 0:
        raise ValueError(f"codex_cli.{field_name} must be a positive integer")
    return result


__all__ = ["ExternalExecutorSessionRegistry"]
