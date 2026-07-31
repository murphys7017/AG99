"""Resolve plugin ownership between Persona Expression and Core execution."""

from __future__ import annotations

from typing import Literal

from astrbot.core.agent.tool import tool_supports_target
from astrbot.core.star.star import star_map

PluginRuntimeTarget = Literal["core", "personal_expression"]

PLUGIN_RUNTIME_TARGET_CORE: PluginRuntimeTarget = "core"
PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION: PluginRuntimeTarget = (
    "personal_expression"
)
PLUGIN_RUNTIME_TARGETS_CONFIG_KEY = "plugin_runtime_targets"


def plugin_supports_runtime_target(
    event,
    module_path: str | None,
    target: PluginRuntimeTarget,
) -> bool:
    """Return whether a plugin-owned lifecycle handler belongs to ``target``.

    Personal Runtime owns normal interactions.  During an interaction turn,
    plugins therefore default to Persona Expression and only an explicit
    configuration entry may place one in Core.  Outside an interaction turn we
    retain the legacy Core lifecycle unchanged.
    """
    if not _is_personal_runtime_turn(event):
        return True
    return _resolve_plugin_target(event, module_path) == target


def tool_supports_runtime_target(event, tool: object, target: str) -> bool:
    """Apply plugin runtime routing before a tool's declared capabilities.

    Plugin-owned tools follow their plugin's configured execution surface.
    Built-in and MCP tools have no plugin owner and continue to use their
    explicit ``execution_targets`` declaration.
    """
    # Legacy turns must preserve each tool's declared execution targets.
    # Plugin placement only overrides that declaration during an Interaction turn.
    if not _is_personal_runtime_turn(event):
        return tool_supports_target(tool, target)

    module_path = _tool_module_path(tool)
    if _metadata_for_module(module_path) is not None:
        return plugin_supports_runtime_target(event, module_path, target)
    return tool_supports_target(tool, target)


def _is_personal_runtime_turn(event) -> bool:
    get_extra = getattr(event, "get_extra", None)
    return bool(get_extra and get_extra("_interaction_enabled", False))


def _resolve_plugin_target(
    event,
    module_path: str | None,
) -> PluginRuntimeTarget:
    metadata = _metadata_for_module(module_path)
    configured_target = _configured_target(event, metadata, module_path)
    if configured_target == PLUGIN_RUNTIME_TARGET_CORE:
        return PLUGIN_RUNTIME_TARGET_CORE
    return PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION


def _configured_target(event, metadata, module_path: str | None) -> str | None:
    get_extra = getattr(event, "get_extra", None)
    config = get_extra("_astrbot_config", {}) if get_extra else {}
    if not isinstance(config, dict):
        return None
    interaction_config = config.get("interaction_middleware", {})
    if not isinstance(interaction_config, dict):
        return None
    configured_targets = interaction_config.get(PLUGIN_RUNTIME_TARGETS_CONFIG_KEY)
    if not isinstance(configured_targets, dict):
        return None

    for key in _plugin_config_keys(metadata, module_path):
        value = configured_targets.get(key)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {
                PLUGIN_RUNTIME_TARGET_CORE,
                PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
            }:
                return normalized
    return None


def _plugin_config_keys(metadata, module_path: str | None) -> tuple[str, ...]:
    keys: list[str] = []
    for candidate in (
        getattr(metadata, "root_dir_name", None),
        getattr(metadata, "module_path", None),
        getattr(metadata, "name", None),
        module_path,
    ):
        if isinstance(candidate, str) and candidate.strip() and candidate not in keys:
            keys.append(candidate)
    return tuple(keys)


def _metadata_for_module(module_path: str | None):
    if not isinstance(module_path, str) or not module_path:
        return None
    direct = star_map.get(module_path)
    if direct is not None:
        return direct
    for candidate_path, metadata in star_map.items():
        if module_path == candidate_path or module_path.startswith(f"{candidate_path}."):
            return metadata
    return None


def _tool_module_path(tool: object) -> str | None:
    module_path = getattr(tool, "handler_module_path", None)
    if isinstance(module_path, str) and module_path:
        return module_path
    handler = getattr(tool, "handler", None)
    candidate = getattr(handler, "__module__", None)
    return candidate if isinstance(candidate, str) and candidate else None
