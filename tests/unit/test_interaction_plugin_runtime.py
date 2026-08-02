from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.interaction.config import (
    is_middleware_enabled,
    load_interaction_agent_config,
)
from astrbot.core.interaction.contributors import InteractionResultContribution
from astrbot.core.interaction.expression_agent import PersonaExpressionResult
from astrbot.core.interaction.middleware import InteractionMiddleware
from astrbot.core.interaction.output_controller import InteractionOutputController
from astrbot.core.interaction.plugin_runtime import (
    PLUGIN_RUNTIME_TARGET_CORE,
    PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
    tool_supports_runtime_target,
)
from astrbot.core.interaction.turn_state import ensure_interaction_turn_state
from astrbot.core.interaction.types import (
    InteractionRouteDecision,
    InteractionRouteMode,
)
from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.star.base import Star
from astrbot.core.star.star import StarMetadata, star_map, star_registry
from astrbot.core.star.star_handler import EventType, star_handlers_registry


def test_personal_runtime_is_enabled_by_default_but_respects_explicit_disable():
    assert is_middleware_enabled({}) is True
    default_config = load_interaction_agent_config({})
    assert default_config.enabled is True
    assert default_config.persona_history_window_size == 16
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


@pytest.mark.asyncio
async def test_core_result_returns_through_persona_without_reopening_plugin_tools():
    class Event:
        def get_extra(self, _key, default=None):
            return default

        def set_extra(self, _key, _value):
            pass

    middleware = object.__new__(InteractionMiddleware)
    rendered_requests = []

    async def render_visible_reply(_event, request):
        rendered_requests.append(request)
        return PersonaExpressionResult(spoken_reply="人格化后的执行结果")

    middleware._render_visible_reply_via_persona = render_visible_reply
    middleware.output_controller = SimpleNamespace(
        deliver_prepared_core_reply=AsyncMock(),
    )
    event = Event()
    source_message = MessageChain([Plain("Core execution completed")])

    await middleware._handle_core_reply_via_persona(source_message, event)

    assert rendered_requests[0].allow_plugin_tools is False
    middleware.output_controller.deliver_prepared_core_reply.assert_awaited_once_with(
        source_message,
        PersonaExpressionResult(spoken_reply="人格化后的执行结果"),
        event,
    )


@pytest.mark.asyncio
async def test_persona_route_does_not_open_function_tools():
    class Event:
        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

    middleware = object.__new__(InteractionMiddleware)
    middleware.plugin_context = object()
    requests = []

    async def generate_expression(_event, _config, *, request):
        requests.append(request)
        return PersonaExpressionResult()

    middleware._generate_expression = generate_expression
    middleware._apply_immediate_expression_policy = (
        lambda _event, _route, expression, **_kwargs: expression
    )
    event = Event()
    turn_state = ensure_interaction_turn_state(event)
    turn_state.route_decision = InteractionRouteDecision(
        route_mode=InteractionRouteMode.PERSONA,
    )

    result = await middleware._generate_and_emit_persona(event, object())

    assert result is None
    assert requests[0].allow_plugin_tools is False


@pytest.mark.asyncio
async def test_immediate_text_override_keeps_persona_tool_rich_output():
    class Event:
        def __init__(self):
            self._extras = {"_interaction_emitting_immediate_reply": True}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

    controller = object.__new__(InteractionOutputController)
    delivered = []
    controller._collect_result_contributions = AsyncMock(
        return_value=[
            InteractionResultContribution(
                plugin_id="reply_override",
                final_text_override="rewritten reply",
            )
        ]
    )
    controller._next_output_segment_id = lambda _event, _kind: "segment-1"
    controller.materialize_immediate_interaction_outbound_message = AsyncMock(
        side_effect=lambda _event, message, **_kwargs: (message, {})
    )
    controller._deliver_visible_message = AsyncMock(
        side_effect=lambda _event, message, **_kwargs: delivered.append(message) or []
    )
    controller.build_platform_output_base_extras = (
        lambda _event, **_kwargs: {}
    )
    controller._record_visible_output = Mock()

    await controller.capture_message_chain(
        MessageChain([Plain("original reply"), Image("attachment.png")]),
        Event(),
    )

    assert len(delivered) == 1
    assert delivered[0].get_plain_text() == "rewritten reply"
    assert [type(component) for component in delivered[0].chain] == [Plain, Image]


def test_plugin_declaration_sets_default_target_but_config_overrides_it(monkeypatch):
    plugin_module = "test_plugins.work_tools"
    monkeypatch.setitem(
        star_map,
        plugin_module,
        StarMetadata(
            name="work tools",
            root_dir_name="work_tools",
            interaction_runtime_target="core",
        ),
    )
    tool = FunctionTool(
        name="work_tool",
        description="A work execution tool.",
        parameters={"type": "object", "properties": {}},
        handler_module_path=plugin_module,
    )

    class Event:
        def __init__(self, target=None):
            self._extras = {
                "_interaction_enabled": True,
                "_astrbot_config": {
                    "interaction_middleware": {
                        "plugin_runtime_targets": (
                            {"work_tools": target} if target else {}
                        )
                    }
                },
            }

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

    declared_core_event = Event()
    assert tool_supports_runtime_target(
        declared_core_event, tool, PLUGIN_RUNTIME_TARGET_CORE
    )

    overridden_event = Event("personal_expression")
    assert tool_supports_runtime_target(
        overridden_event, tool, PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION
    )
    assert not tool_supports_runtime_target(
        overridden_event, tool, PLUGIN_RUNTIME_TARGET_CORE
    )


def test_star_runtime_target_declaration_is_registered():
    plugin_module = "test_plugins.declared_runtime_target"
    declared_plugin = type(
        "DeclaredRuntimeTargetPlugin",
        (Star,),
        {
            "__module__": plugin_module,
            "interaction_runtime_target": "core",
        },
    )

    try:
        assert star_map[declared_plugin.__module__].interaction_runtime_target == "core"
    finally:
        metadata = star_map.pop(plugin_module, None)
        if metadata in star_registry:
            star_registry.remove(metadata)
