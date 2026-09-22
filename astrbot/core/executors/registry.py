"""Explicit executor registration and per-runtime selection.

This module only resolves a configured execution implementation. It does not
construct platform events, select a provider, or own a Core turn lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .contracts import ExecutorRun

ExecutorFactory = Callable[..., ExecutorRun]
_EXECUTOR_FACTORIES: dict[str, ExecutorFactory] = {}


def _normalize_executor_id(executor_id: object) -> str:
    normalized = str(executor_id or "").strip().lower()
    if not normalized:
        raise ValueError("core_execution.executor_id must be a non-empty string")
    return normalized


def register_executor_factory(executor_id: str, factory: ExecutorFactory) -> None:
    """Register one built-in executor factory exactly once during startup."""

    normalized = _normalize_executor_id(executor_id)
    if not callable(factory):
        raise TypeError("executor factory must be callable")
    if normalized in _EXECUTOR_FACTORIES:
        raise ValueError(f"executor factory is already registered: {normalized}")
    _EXECUTOR_FACTORIES[normalized] = factory


def resolve_executor_factory(executor_id: str) -> ExecutorFactory:
    """Return a registered factory or fail instead of silently using Native."""

    normalized = _normalize_executor_id(executor_id)
    try:
        return _EXECUTOR_FACTORIES[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown core execution executor_id: {normalized}") from exc


def resolve_executor_id(
    config_snapshot: Mapping[str, Any],
    *,
    execution_source: str,
) -> str:
    """Resolve and validate one Body choice from the active bot configuration.

    ``execution_source`` exists solely for actionable diagnostics. It must not
    influence selection: ordinary interaction and proactive Core use the same
    configured value.
    """

    if not isinstance(config_snapshot, Mapping):
        raise ValueError(
            f"{execution_source} requires a mapping runtime configuration snapshot"
        )
    raw_config = config_snapshot.get("core_execution")
    if raw_config is None:
        # Scoped bot configurations can predate the global default object.
        executor_id = "native"
    elif not isinstance(raw_config, Mapping):
        raise ValueError("core_execution must be an object")
    else:
        executor_id = raw_config.get("executor_id", "native")
    normalized = _normalize_executor_id(executor_id)
    resolve_executor_factory(normalized)
    return normalized


def _build_native_executor_run(**kwargs: Any) -> ExecutorRun:
    """Build the built-in run without exposing its implementation to callers."""

    from astrbot.core.astr_agent_run_util import NativeExecutorRun

    return NativeExecutorRun(**kwargs)


register_executor_factory("native", _build_native_executor_run)


__all__ = [
    "ExecutorFactory",
    "register_executor_factory",
    "resolve_executor_factory",
    "resolve_executor_id",
]
