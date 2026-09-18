"""Shared runtime and dashboard resolution of plugin capability targets."""

from __future__ import annotations

from typing import Literal

from astrbot.core.agent.handoff import HandoffTool
from astrbot.core.agent.tool import (
    TOOL_TARGET_CORE,
    TOOL_TARGET_PERSONAL_EXPRESSION,
    tool_supports_target,
)
from astrbot.core.plugin_admission import (
    CapabilityKind,
    capability_allowed,
    resolve_owner_metadata,
)

PluginRuntimeTarget = Literal["core", "personal_expression"]
PLUGIN_RUNTIME_TARGET_CORE: PluginRuntimeTarget = "core"
PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION: PluginRuntimeTarget = "personal_expression"
PLUGIN_CAPABILITY_TARGETS_CONFIG_KEY = "plugin_capability_targets"


def _event_config(event) -> dict:
    return event.get_extra("_astrbot_config", {}) or {}


def _is_personal_runtime_turn(event) -> bool:
    return bool(event.get_extra("_interaction_enabled", False))


def _binding(runtime_config, metadata) -> dict:
    """The canonical key is the registered plugin name, as in plugin_set."""
    if not isinstance(runtime_config, dict):
        return {}
    settings = runtime_config.get("interaction_middleware", {})
    bindings = settings.get(PLUGIN_CAPABILITY_TARGETS_CONFIG_KEY, {})
    value = bindings.get(getattr(metadata, "name", None), {})
    return value if isinstance(value, dict) else {}


def _target(value) -> PluginRuntimeTarget | None:
    return value if value in ("core", "personal_expression") else None


def validate_plugin_capability_targets(value: object) -> None:
    """Reject malformed or ambiguous target configuration before saving."""
    if not isinstance(value, dict):
        raise ValueError("plugin_capability_targets must be an object")
    for name, binding in value.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(binding, dict)
        ):
            raise ValueError("plugin_capability_targets requires plugin-name objects")
        if set(binding) - {"llm_hooks", "tools"}:
            raise ValueError(f"Unknown capability target field for {name}")
        if "llm_hooks" in binding and _target(binding["llm_hooks"]) is None:
            raise ValueError(f"Invalid llm_hooks target for {name}")
        tools = binding.get("tools", {})
        if not isinstance(tools, dict) or any(
            not isinstance(key, str) or not key.strip() or _target(target) is None
            for key, target in tools.items()
        ):
            raise ValueError(f"Invalid tools target map for {name}")


def resolve_plugin_runtime_target(
    runtime_config: object, metadata: object, module_path: str | None
) -> tuple[PluginRuntimeTarget, str]:
    del module_path
    configured = _target(_binding(runtime_config, metadata).get("llm_hooks"))
    if configured is not None:
        return configured, "configuration"
    declared = _target(getattr(metadata, "interaction_runtime_target", None))
    return (
        (declared, "declaration")
        if declared is not None
        else (PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION, "default")
    )


def resolve_tool_runtime_target(
    runtime_config: object,
    metadata: object,
    module_path: str | None,
    tool_name: str,
    tool: object = None,
) -> tuple[str, str]:
    del module_path
    if isinstance(tool, HandoffTool):
        return TOOL_TARGET_CORE, "fixed_by_contract"
    tools = _binding(runtime_config, metadata).get("tools", {})
    configured = _target(tools.get(tool_name, tools.get("*")))
    if configured is not None:
        return configured, "configuration"
    targets = [
        target
        for target in (TOOL_TARGET_CORE, TOOL_TARGET_PERSONAL_EXPRESSION)
        if tool_supports_target(tool, target)
    ]
    return ",".join(targets), "declaration"


def plugin_supports_runtime_target(
    event, module_path: str | None, target: PluginRuntimeTarget
) -> bool:
    if not capability_allowed(
        event, kind=CapabilityKind.LLM_HOOK, owner_module_path=module_path
    ):
        return False
    if not _is_personal_runtime_turn(event):
        return True
    resolved, _ = resolve_plugin_runtime_target(
        _event_config(event), resolve_owner_metadata(module_path), module_path
    )
    return resolved == target


def tool_supports_runtime_target(event, tool: object, target: str) -> bool:
    if not _is_personal_runtime_turn(event):
        return tool_supports_target(tool, target)
    module_path = tool_owner_module(tool)
    resolved, _ = resolve_tool_runtime_target(
        _event_config(event),
        resolve_owner_metadata(module_path),
        module_path,
        str(getattr(tool, "name", "") or ""),
        tool,
    )
    return target in resolved.split(",")


def tool_plugin_is_selected(event, tool: object) -> bool:
    return capability_allowed(
        event,
        kind=CapabilityKind.TOOL,
        owner_module_path=tool_owner_module(tool),
        item_name=getattr(tool, "name", None),
    )


def tool_owner_module(tool: object) -> str:
    return (
        getattr(tool, "handler_module_path", None)
        or getattr(getattr(tool, "handler", None), "__module__", None)
        or type(tool).__module__
    )
