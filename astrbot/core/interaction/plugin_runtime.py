"""Compatibility exports for the Core plugin runtime routing policy."""

from astrbot.core.plugin_runtime import (
    PLUGIN_RUNTIME_TARGET_CORE,
    PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
    PLUGIN_RUNTIME_TARGETS_CONFIG_KEY,
    PluginRuntimeTarget,
    plugin_supports_runtime_target,
    tool_supports_runtime_target,
)

__all__ = [
    "PLUGIN_RUNTIME_TARGET_CORE",
    "PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION",
    "PLUGIN_RUNTIME_TARGETS_CONFIG_KEY",
    "PluginRuntimeTarget",
    "plugin_supports_runtime_target",
    "tool_supports_runtime_target",
]
