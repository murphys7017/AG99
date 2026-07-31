from types import SimpleNamespace

import pytest

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.interaction.config import (
    is_middleware_enabled,
    load_interaction_agent_config,
)
from astrbot.core.interaction.plugin_runtime import (
    PLUGIN_RUNTIME_TARGET_CORE,
    PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
    tool_supports_runtime_target,
)
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.star.star import StarMetadata, star_map
from astrbot.core.star.star_handler import EventType, star_handlers_registry


def test_personal_runtime_is_enabled_by_default_but_respects_explicit_disable():
    assert is_middleware_enabled({}) is True
    assert load_interaction_agent_config({}).enabled is True
    assert is_middleware_enabled({"interaction_middleware": {"enabled": False}}) is False


@pytest.mark.asyncio
async def test_llm_hook_dispatch_uses_configured_plugin_runtime_target(monkeypatch):
    calls = []

    async def persona_handler(event, request):
        del event, request
        calls.append("persona")

    async def core_handler(event, request):
        del event, request
        calls.append("core")

    persona_module = "test_plugins.persona"
    core_module = "test_plugins.core"
    handlers = [
        SimpleNamespace(
            handler_module_path=persona_module,
            handler_name="persona_handler",
            handler=persona_handler,
        ),
        SimpleNamespace(
            handler_module_path=core_module,
            handler_name="core_handler",
            handler=core_handler,
        ),
    ]
    monkeypatch.setitem(
        star_map,
        persona_module,
        StarMetadata(name="persona", root_dir_name="persona_plugin"),
    )
    monkeypatch.setitem(
        star_map,
        core_module,
        StarMetadata(name="core", root_dir_name="core_plugin"),
    )
    monkeypatch.setattr(
        star_handlers_registry,
        "get_handlers_by_event_type",
        lambda *args, **kwargs: handlers,
    )

    class Event:
        plugins_name = []

        def __init__(self):
            self._extras = {
                "_interaction_enabled": True,
                "_astrbot_config": {
                    "interaction_middleware": {
                        "plugin_runtime_targets": {"core_plugin": "core"}
                    }
                },
            }

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def is_stopped(self):
            return False

    event = Event()

    await call_event_hook(
        event,
        EventType.OnLLMRequestEvent,
        object(),
        execution_surface=PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
    )
    assert calls == ["persona"]

    calls.clear()
    await call_event_hook(
        event,
        EventType.OnLLMRequestEvent,
        object(),
        execution_surface=PLUGIN_RUNTIME_TARGET_CORE,
    )
    assert calls == ["core"]


def test_plugin_tool_runtime_target_defaults_to_persona_in_interaction_turn(
    monkeypatch,
):
    plugin_module = "test_plugins.persona_tools"
    monkeypatch.setitem(
        star_map,
        plugin_module,
        StarMetadata(name="persona tools", root_dir_name="persona_tools"),
    )
    tool = FunctionTool(
        name="persona_tool",
        description="A plugin-owned interaction tool.",
        parameters={"type": "object", "properties": {}},
        handler_module_path=plugin_module,
    )

    class Event:
        def __init__(self, *, interaction_enabled, targets=None):
            self._extras = {
                "_interaction_enabled": interaction_enabled,
                "_astrbot_config": {
                    "interaction_middleware": {
                        "plugin_runtime_targets": targets or {},
                    }
                },
            }

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

    interaction_event = Event(interaction_enabled=True)
    assert tool_supports_runtime_target(
        interaction_event, tool, PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION
    )
    assert not tool_supports_runtime_target(
        interaction_event, tool, PLUGIN_RUNTIME_TARGET_CORE
    )

    core_event = Event(
        interaction_enabled=True,
        targets={"persona_tools": "core"},
    )
    assert not tool_supports_runtime_target(
        core_event, tool, PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION
    )
    assert tool_supports_runtime_target(core_event, tool, PLUGIN_RUNTIME_TARGET_CORE)

    legacy_event = Event(interaction_enabled=False)
    assert tool_supports_runtime_target(legacy_event, tool, PLUGIN_RUNTIME_TARGET_CORE)
    assert not tool_supports_runtime_target(
        legacy_event, tool, PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION
    )
