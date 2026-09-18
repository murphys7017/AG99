"""Resolve plugin handlers and tools onto Interaction execution surfaces."""

from __future__ import annotations

from typing import Literal

from astrbot.core.agent.handoff import HandoffTool
from astrbot.core.agent.tool import (
    TOOL_TARGET_CORE,
    TOOL_TARGET_PERSONAL_EXPRESSION,
    tool_supports_target,
)
from astrbot.core.plugin_admission import CapabilityKind, capability_allowed
from astrbot.core.star.star import star_map

PluginRuntimeTarget = Literal["core", "personal_expression"]

PLUGIN_RUNTIME_TARGET_CORE: PluginRuntimeTarget = "core"
PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION: PluginRuntimeTarget = "personal_expression"
PLUGIN_RUNTIME_TARGETS_CONFIG_KEY = "plugin_runtime_targets"
PLUGIN_TOOL_TARGETS_CONFIG_KEY = "plugin_tool_targets"


def plugin_supports_runtime_target(
    event,
    module_path: str | None,
    target: PluginRuntimeTarget,
) -> bool:
    """Return whether a plugin-owned lifecycle handler belongs to ``target``.

    Two independent conditions must hold:

    * **Admission** — the plugin may contribute at all (global activation,
      ``plugin_set``, per-session disabled list).
    * **Target** — the handler belongs to this execution surface, resolved by
      ``plugin_runtime_targets``.

    Personal Runtime owns normal interactions. During an interaction turn,
    plugins therefore default to Persona Expression and only an explicit
    configuration entry may place one in Core. Outside an interaction turn we
    retain the legacy Core lifecycle unchanged.
    """
    if not capability_allowed(
        event,
        kind=CapabilityKind.LLM_HOOK,
        owner_module_path=module_path,
    ):
        return False
    if not _is_personal_runtime_turn(event):
        return True
    return _resolve_plugin_target(event, module_path) == target


def tool_supports_runtime_target(event, tool: object, target: str) -> bool:
    """Resolve a tool independently from its plugin's LLM lifecycle target.

    Function tools remain Core-only by default. A plugin can opt a tool into
    Persona through its own ``execution_targets`` declaration, while users can
    override a plugin or one named tool through ``plugin_tool_targets``.
    ``HandoffTool`` is an invariant Core capability and cannot be moved to
    Persona by either declaration or configuration.
    """
    if target == TOOL_TARGET_PERSONAL_EXPRESSION and isinstance(tool, HandoffTool):
        return False
    if not _is_personal_runtime_turn(event):
        return tool_supports_target(tool, target)

    module_path = _tool_module_path(tool)
    metadata = _metadata_for_module(module_path)
    if metadata is not None:
        configured_target = _configured_tool_target(
            event,
            metadata,
            module_path,
            str(getattr(tool, "name", "") or "").strip(),
        )
        if configured_target is not None:
            return configured_target == target
    return tool_supports_target(tool, target)


def tool_plugin_is_selected(event, tool: object) -> bool:
    """Return whether the tool's owning plugin may contribute this tool.

    Delegates to the single admission decision point so that global activation,
    ``plugin_set`` and the per-session disabled list are all applied
    consistently. ``plugin_tool_targets`` still decides the Personal/Core
    target; admission is a separate axis and cannot be bypassed by a target.
    """
    module_path = _tool_module_path(tool)
    metadata = _metadata_for_module(module_path)
    if metadata is None:
        # No owning plugin metadata: treat as a system tool, matching the
        # historical behaviour.
        return True

    tool_name = str(getattr(tool, "name", "") or "").strip() or None
    return capability_allowed(
        event,
        kind=CapabilityKind.TOOL,
        owner_module_path=module_path,
        owner_plugin_name=getattr(metadata, "name", None),
        item_name=tool_name,
    )


def _is_personal_runtime_turn(event) -> bool:
    get_extra = getattr(event, "get_extra", None)
    return bool(get_extra and get_extra("_interaction_enabled", False))


def _resolve_plugin_target(
    event,
    module_path: str | None,
) -> PluginRuntimeTarget:
    metadata = _metadata_for_module(module_path)
    configured_target = _configured_target(event, metadata, module_path)
    if configured_target is not None:
        return configured_target
    declared_target = _declared_target(metadata)
    if declared_target is not None:
        return declared_target
    return PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION


def _configured_target(event, metadata, module_path: str | None) -> str | None:
    configured_targets = _configured_target_map(
        event,
        PLUGIN_RUNTIME_TARGETS_CONFIG_KEY,
    )
    if configured_targets is None:
        return None

    return _target_for_keys(
        configured_targets,
        _plugin_config_keys(metadata, module_path),
    )


def _configured_tool_target(
    event,
    metadata,
    module_path: str | None,
    tool_name: str,
) -> PluginRuntimeTarget | None:
    configured_targets = _configured_target_map(
        event,
        PLUGIN_TOOL_TARGETS_CONFIG_KEY,
    )
    if configured_targets is None:
        return None

    plugin_keys = _plugin_config_keys(metadata, module_path)
    if tool_name:
        exact_target = _target_for_keys(
            configured_targets,
            tuple(f"{key}.{tool_name}" for key in plugin_keys),
        )
        if exact_target is not None:
            return exact_target
    return _target_for_keys(configured_targets, plugin_keys)


def _configured_target_map(event, config_key: str) -> dict | None:
    get_extra = getattr(event, "get_extra", None)
    config = get_extra("_astrbot_config", {}) if get_extra else {}
    return _configured_target_map_from(config, config_key)


def _configured_target_map_from(runtime_config: object, config_key: str) -> dict | None:
    """Read one target map out of a plain AstrBot config mapping.

    Shared by the turn path (which reads the event's frozen config) and the
    inventory API (which has no event and passes the runtime config directly).
    """
    if not isinstance(runtime_config, dict):
        return None
    interaction_config = runtime_config.get("interaction_middleware", {})
    if not isinstance(interaction_config, dict):
        return None
    configured_targets = interaction_config.get(config_key)
    if not isinstance(configured_targets, dict):
        return None
    return configured_targets


def resolve_plugin_runtime_target(
    runtime_config: object,
    metadata: object,
    module_path: str | None,
) -> tuple[PluginRuntimeTarget, str]:
    """Resolve one plugin's LLM lifecycle target plus where it came from.

    Resolution order is unchanged: user config, then the plugin's
    ``interaction_runtime_target`` declaration, then the Persona default.
    """
    configured = _target_for_keys(
        _configured_target_map_from(runtime_config, PLUGIN_RUNTIME_TARGETS_CONFIG_KEY)
        or {},
        _plugin_config_keys(metadata, module_path),
    )
    if configured is not None:
        return configured, "plugin_runtime_targets"
    declared = _declared_target(metadata)
    if declared is not None:
        return declared, "declaration"
    return PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION, "default"


def resolve_tool_runtime_target(
    runtime_config: object,
    metadata: object,
    module_path: str | None,
    tool_name: str,
    tool: object = None,
) -> tuple[str, str]:
    """Resolve one FunctionTool's target plus where it came from.

    Resolution order is unchanged: ``plugin_tool_targets`` (exact
    ``plugin.tool`` before ``plugin``), then the tool's own ``execution_targets``
    declaration, then the Core default.
    """
    configured_targets = (
        _configured_target_map_from(runtime_config, PLUGIN_TOOL_TARGETS_CONFIG_KEY)
        or {}
    )
    plugin_keys = _plugin_config_keys(metadata, module_path)
    if tool_name:
        exact = _target_for_keys(
            configured_targets,
            tuple(f"{key}.{tool_name}" for key in plugin_keys),
        )
        if exact is not None:
            return exact, "plugin_tool_targets"
    configured = _target_for_keys(configured_targets, plugin_keys)
    if configured is not None:
        return configured, "plugin_tool_targets"
    if tool is not None:
        targets = getattr(tool, "execution_targets", None)
        if targets:
            normalized = sorted(str(target) for target in targets)
            if normalized:
                return normalized[0], "declaration"
    return TOOL_TARGET_CORE, "default"


def _target_for_keys(
    configured_targets: dict,
    keys: tuple[str, ...],
) -> PluginRuntimeTarget | None:
    for key in keys:
        value = configured_targets.get(key)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {
                PLUGIN_RUNTIME_TARGET_CORE,
                PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
            }:
                return normalized
    return None


def _declared_target(metadata) -> PluginRuntimeTarget | None:
    declared = getattr(metadata, "interaction_runtime_target", None)
    if not isinstance(declared, str):
        return None
    normalized = declared.strip().lower()
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
        if module_path == candidate_path or module_path.startswith(
            f"{candidate_path}."
        ):
            return metadata
    return None


def _tool_module_path(tool: object) -> str | None:
    module_path = getattr(tool, "handler_module_path", None)
    if isinstance(module_path, str) and module_path:
        return module_path
    handler = getattr(tool, "handler", None)
    candidate = getattr(handler, "__module__", None)
    return candidate if isinstance(candidate, str) and candidate else None
