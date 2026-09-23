"""Executor-neutral preparation for stateful external Core Bodies.

External executors receive a compact, immutable request derived from the Core
snapshot.  They never receive an AstrBot event, plugin context, ProviderRequest
or live ToolSet.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.core.execution import CoreExecutionDeadlineView, CoreExecutionSpec
from astrbot.core.execution_capabilities import collect_semantic_capability_bindings
from astrbot.core.prompt.targets import PromptTarget, project_context_pack


class ExternalExecutorConfigurationError(ValueError):
    """Raised when an external executor configuration is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class ExternalExecutorSessionKey:
    """Stable identity for one stateful external executor conversation."""

    executor_id: str
    runtime_config_id: str
    session_id: str
    workspace_root: Path
    workspace: Path


@dataclass(frozen=True, slots=True)
class ExternalExecutorRequest:
    """Prepared facts consumed by an external Executor Body.

    ``prompt`` deliberately contains only the Core-target projection.  It is
    not an AstrBot provider prompt and must not be interpreted as one.
    """

    execution_spec: CoreExecutionSpec
    deadline_view: CoreExecutionDeadlineView | None
    session_key: ExternalExecutorSessionKey
    workspace: Path
    prompt: str
    capabilities: tuple[str, ...]


def prepare_external_executor_request(
    *,
    execution_spec: CoreExecutionSpec,
    deadline_view: CoreExecutionDeadlineView | None,
    executor_id: str,
    runtime_config_id: str,
    session_id: str,
    workspace_config: Mapping[str, Any],
    supported_capabilities: frozenset[str] = frozenset(),
    prompt_config: object,
) -> ExternalExecutorRequest:
    """Prepare one request using the active bot's frozen Core prompt budget."""

    normalized_executor_id = _require_non_empty(executor_id, "executor_id").lower()
    normalized_config_id = _require_non_empty(runtime_config_id, "runtime_config_id")
    normalized_session_id = _require_non_empty(session_id, "session_id")
    workspace_root = _resolve_workspace_root(workspace_config)
    workspace = _resolve_workspace(workspace_config, workspace_root)
    context_pack = project_context_pack(
        execution_spec.context_pack,
        PromptTarget.CORE,
        config=prompt_config,
    )
    prompt = _render_external_prompt(
        execution_spec=execution_spec,
        context_pack=context_pack,
        workspace=workspace,
    )
    return ExternalExecutorRequest(
        execution_spec=execution_spec,
        deadline_view=deadline_view,
        session_key=ExternalExecutorSessionKey(
            executor_id=normalized_executor_id,
            runtime_config_id=normalized_config_id,
            session_id=normalized_session_id,
            workspace_root=workspace_root,
            workspace=workspace,
        ),
        workspace=workspace,
        prompt=prompt,
        capabilities=_resolve_capabilities(
            execution_spec,
            supported_capabilities=supported_capabilities,
        ),
    )


def _resolve_workspace_root(config: Mapping[str, Any]) -> Path:
    raw_root = config.get("workspace_root")
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise ExternalExecutorConfigurationError(
            "external executor requires core_execution.<executor>.workspace_root"
        )
    requested_root = Path(raw_root).expanduser()
    if not requested_root.is_absolute():
        raise ExternalExecutorConfigurationError("workspace_root must be absolute")
    root = requested_root.resolve(strict=False)
    if not root.is_dir():
        raise ExternalExecutorConfigurationError(
            f"workspace_root is not an existing directory: {root}"
        )
    return root


def _resolve_workspace(config: Mapping[str, Any], root: Path) -> Path:
    raw_workspace = config.get("workspace")
    if raw_workspace is None:
        return root
    if not isinstance(raw_workspace, str) or not raw_workspace.strip():
        raise ExternalExecutorConfigurationError("workspace must be a non-empty path")
    requested = Path(raw_workspace).expanduser()
    candidate = (
        requested.resolve(strict=False)
        if requested.is_absolute()
        else (root / requested).resolve(strict=False)
    )
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ExternalExecutorConfigurationError(
            f"workspace must be inside workspace_root: {candidate}"
        ) from exc
    if not candidate.is_dir():
        raise ExternalExecutorConfigurationError(
            f"workspace is not an existing directory: {candidate}"
        )
    return candidate


def _render_external_prompt(
    *,
    execution_spec: CoreExecutionSpec,
    context_pack,
    workspace: Path,
) -> str:
    task_spec = execution_spec.task_spec or {}
    execution_prompt = str(task_spec.get("execution_prompt") or "").strip()
    task_summary = str(task_spec.get("task_summary") or "").strip()
    if not execution_prompt:
        execution_prompt = task_summary
    if not execution_prompt:
        raise ExternalExecutorConfigurationError(
            "external executor requires a Core task execution_prompt or task_summary"
        )

    sections = [
        "You are the execution body for a delegated Core task.",
        "Complete the requested workspace task. Do not speak as the user-facing "
        "persona and do not claim to have AstrBot tools.",
        f"Workspace: {workspace}",
        "",
        "Task:",
        execution_prompt,
    ]
    if task_summary and task_summary != execution_prompt:
        sections.extend(("", "Task summary:", task_summary))

    context_lines = _render_context_slots(context_pack)
    if context_lines:
        sections.extend(("", "Authorized context:", *context_lines))
    sections.extend(
        (
            "",
            "Return a concise factual completion summary. Do not expose private "
            "reasoning or raw tool traces.",
        )
    )
    return "\n".join(sections)


def _render_context_slots(context_pack) -> list[str]:
    rendered: list[str] = []
    for slot_name in sorted(context_pack.slots):
        if slot_name in {
            "capability.tools_schema",
            "capability.subagent_handoff_tools",
            "capability.plugin_directory",
        }:
            continue
        slot = context_pack.slots[slot_name]
        value = slot.value
        if value in (None, "", [], {}):
            continue
        rendered.append(f"[{slot_name}]\n{value}")
    return rendered


def _resolve_capabilities(
    execution_spec: CoreExecutionSpec,
    *,
    supported_capabilities: frozenset[str],
) -> tuple[str, ...]:
    """Return actual admitted capabilities supported by this Body.

    Planner suggestions live in ``task_spec`` and are intentionally excluded:
    they describe intent, not authorization. The Core capability snapshot is
    the only source of admitted capabilities at this boundary.
    """

    tools = execution_spec.capabilities.tools
    if tools is None or not supported_capabilities:
        return ()
    bindings = collect_semantic_capability_bindings(tools)
    return tuple(sorted(set(bindings).intersection(supported_capabilities)))


def _require_non_empty(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ExternalExecutorConfigurationError(
            f"external executor requires a non-empty {field_name}"
        )
    return normalized


__all__ = [
    "ExternalExecutorConfigurationError",
    "ExternalExecutorRequest",
    "ExternalExecutorSessionKey",
    "prepare_external_executor_request",
]
