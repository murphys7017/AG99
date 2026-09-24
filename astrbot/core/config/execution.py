"""Typed execution-configuration projections for one bot profile.

Agent Runner selection and Core Body selection are deliberately separate:
the former replaces the legacy ordinary Pipeline agent, while the latter
selects the Body that executes delegated Core work.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

AGENT_RUNNER_LOCAL_MODE = "local"
AGENT_RUNNER_EXTERNAL_MODES = frozenset({"dify", "coze", "dashscope", "deerflow"})
CORE_EXECUTOR_NATIVE_ID = "native"
CORE_EXECUTOR_CODEX_CLI_ID = "codex_cli"


@dataclass(frozen=True, slots=True)
class AgentRunnerConfiguration:
    """The legacy whole-agent Pipeline replacement selected by a Profile."""

    mode: str
    provider_id: str

    @property
    def is_external(self) -> bool:
        return self.mode in AGENT_RUNNER_EXTERNAL_MODES


@dataclass(frozen=True, slots=True)
class CoreExecutionConfiguration:
    """The Core Body selected after Personal/Core planning."""

    executor_id: str
    provider_id: str | None


def resolve_agent_runner_configuration(
    config_snapshot: Mapping[str, Any],
) -> AgentRunnerConfiguration:
    """Read the Profile-owned ordinary Pipeline runner selection."""

    section = _require_mapping(config_snapshot, "agent_runner")
    mode = _normalize_identifier(section.get("mode"), "agent_runner.mode")
    if mode not in {AGENT_RUNNER_LOCAL_MODE, *AGENT_RUNNER_EXTERNAL_MODES}:
        raise ValueError(f"unknown agent_runner.mode: {mode}")
    provider_id = _optional_identifier(section.get("provider_id"))
    if mode == AGENT_RUNNER_LOCAL_MODE:
        return AgentRunnerConfiguration(mode=mode, provider_id="")
    if not provider_id:
        raise ValueError(f"agent_runner.provider_id is required when mode is {mode}")
    return AgentRunnerConfiguration(mode=mode, provider_id=provider_id)


def resolve_core_execution_configuration(
    config_snapshot: Mapping[str, Any],
) -> CoreExecutionConfiguration:
    """Read the Profile-owned Core Body selection."""

    section = _require_mapping(config_snapshot, "core_execution")
    executor_id = _normalize_identifier(
        section.get("executor_id"), "core_execution.executor_id"
    )
    if executor_id == CORE_EXECUTOR_NATIVE_ID:
        return CoreExecutionConfiguration(executor_id=executor_id, provider_id=None)
    if executor_id != CORE_EXECUTOR_CODEX_CLI_ID:
        raise ValueError(f"unknown core_execution.executor_id: {executor_id}")
    codex_cli = _require_mapping(section, "codex_cli")
    provider_id = _require_text(
        codex_cli.get("provider_id"), "core_execution.codex_cli.provider_id"
    )
    return CoreExecutionConfiguration(executor_id=executor_id, provider_id=provider_id)


def _require_mapping(config_snapshot: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = config_snapshot.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object")
    return value


def _normalize_identifier(value: object, field_name: str) -> str:
    normalized = _require_text(value, field_name)
    return normalized.lower()


def _require_text(value: object, field_name: str) -> str:
    normalized = _optional_identifier(value)
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def _optional_identifier(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = [
    "AGENT_RUNNER_EXTERNAL_MODES",
    "AGENT_RUNNER_LOCAL_MODE",
    "CORE_EXECUTOR_CODEX_CLI_ID",
    "CORE_EXECUTOR_NATIVE_ID",
    "AgentRunnerConfiguration",
    "CoreExecutionConfiguration",
    "resolve_agent_runner_configuration",
    "resolve_core_execution_configuration",
]
