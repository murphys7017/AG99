"""Explicit executor registration and per-runtime selection.

This module only resolves a configured execution implementation. It does not
construct platform events, select a provider, or own a Core turn lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from astrbot.core.config.execution import resolve_core_execution_configuration

from .contracts import ExecutorRun

ExecutorFactory = Callable[..., ExecutorRun]
_EXECUTOR_FACTORIES: dict[str, ExecutorFactory] = {}


def _normalize_executor_id(executor_id: object) -> str:
    normalized = str(executor_id or "").strip().lower()
    if not normalized:
        raise ValueError("executor_id must be a non-empty string")
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


@dataclass(frozen=True, slots=True)
class SelectedCoreExecutor:
    executor_id: str
    instance_id: str | None
    config: Mapping[str, Any]


def resolve_core_executor_selection(
    config_snapshot: Mapping[str, Any],
    *,
    provider_manager: Any,
    execution_source: str,
) -> SelectedCoreExecutor:
    """Resolve the immutable Core Body selection for one frozen bot configuration."""

    if not isinstance(config_snapshot, Mapping):
        raise ValueError(
            f"{execution_source} requires a mapping runtime configuration snapshot"
        )
    selection = resolve_core_execution_configuration(config_snapshot)
    if selection.executor_id == "native":
        return SelectedCoreExecutor("native", None, {})
    if provider_manager is None:
        raise RuntimeError("Codex CLI Core execution requires a provider manager")
    if selection.executor_id != "codex_cli" or selection.provider_id is None:
        raise ValueError(
            f"{execution_source} does not use a supported Core executor: "
            f"{selection.executor_id}"
        )
    instance_id = selection.provider_id
    config = provider_manager.get_execution_adapter_config(instance_id, "codex_cli")
    resolve_executor_factory("codex_cli")
    return SelectedCoreExecutor("codex_cli", instance_id, config)


def _build_native_executor_run(**kwargs: Any) -> ExecutorRun:
    """Build the built-in run without exposing its implementation to callers."""

    from astrbot.core.astr_agent_run_util import NativeExecutorRun

    return NativeExecutorRun(**kwargs)


def _build_codex_executor_run(**kwargs: Any) -> ExecutorRun:
    """Build Codex only when its external session and prompt are explicit."""

    from .codex_cli import build_codex_executor_run

    session = kwargs.pop("session", None)
    prompt = kwargs.pop("prompt", None)
    if kwargs or session is None or not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(
            "codex_cli requires explicit session and non-empty prompt construction inputs"
        )
    return build_codex_executor_run(session=session, prompt=prompt)


register_executor_factory("native", _build_native_executor_run)
register_executor_factory("codex_cli", _build_codex_executor_run)


__all__ = [
    "ExecutorFactory",
    "register_executor_factory",
    "resolve_executor_factory",
    "SelectedCoreExecutor",
    "resolve_core_executor_selection",
]
