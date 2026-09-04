from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.core.agent.handoff import HandoffTool
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.capabilities import (
    CAPABILITY_REASON_PLUGIN_NOT_SELECTED,
    CAPABILITY_REASON_SUBAGENT_CORE_ONLY,
    CapabilityResolver,
    CapabilitySnapshot,
)
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
from astrbot.core.interaction.turn_state import (
    append_interaction_turn_assistant_artifacts,
    ensure_interaction_turn_state,
    get_interaction_turn_assistant_artifacts,
    get_interaction_turn_config,
    get_interaction_turn_state,
    set_interaction_turn_config,
    set_interaction_turn_immediate_reply,
)
from astrbot.core.interaction.types import (
    InteractionRouteDecision,
    InteractionRouteMode,
)
from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.pipeline.respond.stage import RespondStage
from astrbot.core.star.base import Star
from astrbot.core.star.star import StarMetadata, star_map, star_registry
from astrbot.core.star.star_handler import EventType, star_handlers_registry


def test_personal_runtime_is_enabled_by_default_but_respects_explicit_disable():
    assert is_middleware_enabled({}) is True
    default_config = load_interaction_agent_config({})
    assert default_config.enabled is True
    assert default_config.persona_history_window_size == 50
    assert default_config.parallel_plugin_runtime_enabled is False
    assert default_config.plugin_parallel_window_seconds == 3.0
    assert default_config.persona_plugin_context_mode == "wait_complete"
    assert (
        load_interaction_agent_config(
            {
                "interaction_middleware": {
                    "persona_plugin_context_mode": "best_effort"
                }
            }
        ).persona_plugin_context_mode
        == "best_effort"
    )
    assert is_middleware_enabled({"interaction_middleware": {"enabled": False}}) is False


def test_interaction_turn_config_is_frozen_on_first_admission():
    class Event:
        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

    event = Event()
    admitted_config = load_interaction_agent_config(
        {"interaction_middleware": {"turn_timeout": 15}}
    )
    later_config = load_interaction_agent_config(
        {"interaction_middleware": {"turn_timeout": 90}}
    )

    assert set_interaction_turn_config(event, admitted_config) is admitted_config
    assert set_interaction_turn_config(event, later_config) is admitted_config
    assert get_interaction_turn_config(event) is admitted_config

    controller = InteractionOutputController(interaction_config=later_config)
    assert controller._get_interaction_config(event) is admitted_config


def test_interaction_turn_state_owns_assistant_artifacts():
    class Event:
        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

    event = Event()
    artifact = {"type": "image", "url": "https://example.invalid/image.png"}

    append_interaction_turn_assistant_artifacts(event, [artifact])
    artifact["url"] = "mutated"

    assert get_interaction_turn_assistant_artifacts(event) == [
        {"type": "image", "url": "https://example.invalid/image.png"}
    ]
    assert event.get_extra("_interaction_assistant_artifacts") is None


def test_coordinated_plugin_path_uses_admitted_turn_config_snapshot():
    class Event:
        def __init__(self):
            self._extras = {
                "_astrbot_config": {
                    "interaction_middleware": {
                        "enabled": True,
                        "parallel_plugin_runtime_enabled": False,
                    }
                }
            }

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

    event = Event()
    set_interaction_turn_config(
        event,
        load_interaction_agent_config(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "parallel_plugin_runtime_enabled": True,
                }
            }
        ),
    )
    middleware = SimpleNamespace(
        is_parallel_plugin_runtime_eligible=Mock(return_value=True)
    )
    stage = ProcessStage()
    stage.ctx = SimpleNamespace(
        astrbot_config={"provider_settings": {"enable": True}},
        interaction_middleware=middleware,
    )
    stage.interaction_turn_coordinator = object()
    stage.plugin_artifact_delivery = object()
    stage.delayed_plugin_delivery = object()
    stage.personal_runtime_manager = object()

    assert stage._should_use_coordinated_interaction_runtime(
        event,
        is_group_candidate=False,
    )
    middleware.is_parallel_plugin_runtime_eligible.assert_called_once_with(
        event,
        is_group_candidate=False,
    )


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


def test_plugin_tool_runtime_target_defaults_to_core_in_interaction_turn(
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
        def __init__(self, *, interaction_enabled, tool_targets=None):
            self._extras = {
                "_interaction_enabled": interaction_enabled,
                "_astrbot_config": {
                    "interaction_middleware": {
                        "plugin_tool_targets": tool_targets or {},
                    }
                },
            }

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

    interaction_event = Event(interaction_enabled=True)
    assert not tool_supports_runtime_target(
        interaction_event, tool, PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION
    )
    assert tool_supports_runtime_target(interaction_event, tool, PLUGIN_RUNTIME_TARGET_CORE)

    declared_persona_tool = FunctionTool(
        name="declared_persona_tool",
        description="A plugin tool explicitly declared for Persona.",
        parameters={"type": "object", "properties": {}},
        handler_module_path=plugin_module,
        execution_targets={PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION},
    )
    assert tool_supports_runtime_target(
        interaction_event,
        declared_persona_tool,
        PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
    )
    assert not tool_supports_runtime_target(
        interaction_event,
        declared_persona_tool,
        PLUGIN_RUNTIME_TARGET_CORE,
    )

    persona_event = Event(
        interaction_enabled=True,
        tool_targets={
            "persona_tools": "core",
            "persona_tools.persona_tool": "personal_expression",
        },
    )
    assert tool_supports_runtime_target(
        persona_event, tool, PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION
    )
    assert not tool_supports_runtime_target(persona_event, tool, PLUGIN_RUNTIME_TARGET_CORE)

    legacy_event = Event(interaction_enabled=False)
    assert tool_supports_runtime_target(legacy_event, tool, PLUGIN_RUNTIME_TARGET_CORE)
    assert not tool_supports_runtime_target(
        legacy_event, tool, PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION
    )


def test_capability_snapshot_derives_detached_schema_from_execution_handles():
    tool = FunctionTool(
        name="persona_lookup",
        description="Look up persona-facing data.",
        parameters={"type": "object", "properties": {}},
        execution_targets={PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION},
    )
    snapshot = CapabilitySnapshot(
        target=PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
        persona_id="persona-a",
        selection_mode="test",
        tools=(tool,),
    )

    serialized = snapshot.serialized_tools()
    serialized[0]["name"] = "mutated"

    assert snapshot.names() == ["persona_lookup"]
    assert snapshot.serialized_tools()[0]["name"] == "persona_lookup"


@pytest.mark.asyncio
async def test_capability_resolver_applies_exact_override_and_rejects_persona_subagent(
    monkeypatch,
):
    plugin_module = "test_plugins.capability_tools"
    unselected_plugin_module = "test_plugins.unselected_tools"
    monkeypatch.setitem(
        star_map,
        plugin_module,
        StarMetadata(name="capability tools", root_dir_name="capability_tools"),
    )
    monkeypatch.setitem(
        star_map,
        unselected_plugin_module,
        StarMetadata(name="unselected tools", root_dir_name="unselected_tools"),
    )
    plugin_tool = FunctionTool(
        name="persona_lookup",
        description="Look up persona-facing data.",
        parameters={"type": "object", "properties": {}},
        handler_module_path=f"{plugin_module}.services",
    )
    unselected_tool = FunctionTool(
        name="hidden_lookup",
        description="A tool from a plugin disabled for this session.",
        parameters={"type": "object", "properties": {}},
        handler_module_path=f"{unselected_plugin_module}.services",
        execution_targets={PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION},
    )
    handoff_tool = HandoffTool(
        agent=SimpleNamespace(name="worker"),
        execution_targets={PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION},
    )

    class Event:
        unified_msg_origin = "test:FriendMessage:user"
        plugins_name = ["capability tools"]

        def __init__(self):
            self._extras = {
                "_interaction_enabled": True,
                "_astrbot_config": {
                    "interaction_middleware": {
                        "plugin_tool_targets": {
                            "capability_tools": "core",
                            "capability_tools.persona_lookup": "personal_expression",
                        }
                    }
                },
            }

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def get_platform_name(self):
            return "test"

    context = SimpleNamespace(
        persona_manager=SimpleNamespace(
            resolve_selected_persona=AsyncMock(
                return_value=("persona-a", None, None, False)
            )
        ),
        get_llm_tool_manager=lambda: SimpleNamespace(
            func_list=[plugin_tool, unselected_tool, handoff_tool]
        ),
    )
    snapshot = await CapabilityResolver().resolve(
        event=Event(),
        plugin_context=context,
        config=SimpleNamespace(provider_settings={}),
        target=PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
    )

    assert snapshot.names() == ["persona_lookup"]
    assert any(
        decision.tool_name == unselected_tool.name
        and decision.reason == CAPABILITY_REASON_PLUGIN_NOT_SELECTED
        for decision in snapshot.decisions
    )
    assert any(
        decision.tool_name == handoff_tool.name
        and decision.reason == CAPABILITY_REASON_SUBAGENT_CORE_ONLY
        for decision in snapshot.decisions
    )


@pytest.mark.asyncio
async def test_core_result_returns_through_unified_persona_expression():
    class Event:
        def __init__(self):
            self._extras = {}

        def get_extra(self, _key, default=None):
            return self._extras.get(_key, default)

        def set_extra(self, _key, _value):
            self._extras[_key] = _value

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
    ensure_interaction_turn_state(event)
    set_interaction_turn_immediate_reply(event, "我先看看。")
    source_message = MessageChain([Plain("Core execution completed")])

    await middleware._handle_core_reply_via_persona(source_message, event)

    assert rendered_requests[0].intent.kind == "reply"
    assert rendered_requests[0].intent.source == "core_result"
    assert rendered_requests[0].intent.phase == "final"
    assert rendered_requests[0].immediate_reply == "我先看看。"
    middleware.output_controller.deliver_prepared_core_reply.assert_awaited_once_with(
        source_message,
        PersonaExpressionResult(spoken_reply="人格化后的执行结果"),
        event,
    )


@pytest.mark.asyncio
async def test_core_persona_failure_falls_back_to_raw_core_output():
    class Event:
        def __init__(self):
            self._extras = {"_turn_id": "turn-core-fallback"}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

    middleware = object.__new__(InteractionMiddleware)
    middleware._render_visible_reply_via_persona = AsyncMock(
        side_effect=RuntimeError("provider unavailable")
    )
    middleware.output_controller = SimpleNamespace(
        deliver_prepared_core_reply=AsyncMock(),
        deliver_raw_core_reply=AsyncMock(),
    )
    event = Event()
    source_message = MessageChain([Plain("Core execution completed")])

    await middleware._handle_core_reply_via_persona(source_message, event)

    middleware.output_controller.deliver_prepared_core_reply.assert_not_awaited()
    middleware.output_controller.deliver_raw_core_reply.assert_awaited_once_with(
        source_message,
        event,
    )
    state = get_interaction_turn_state(event)
    assert state is not None
    assert state.failures[-1].stage == "core_persona_render"
    assert state.failures[-1].user_visible_action == "deliver_core_result_without_persona"


@pytest.mark.asyncio
async def test_persona_route_allows_explicitly_targeted_function_tools():
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
    event = Event()
    turn_state = ensure_interaction_turn_state(event)
    turn_state.route_decision = InteractionRouteDecision(
        route_mode=InteractionRouteMode.PERSONA,
    )

    result = await middleware._generate_and_emit_persona(event, object())

    assert result is None
    assert requests[0].intent.kind == "reply"
    assert requests[0].intent.source == "user_message"
    assert requests[0].intent.phase == "immediate"
    assert requests[0].delegated_task_summary == ""


@pytest.mark.asyncio
async def test_persona_completion_delegates_to_interaction_delivery_boundary():
    class Controller:
        def __init__(self):
            self.complete = AsyncMock(return_value=True)

        async def complete_visible_delivery(self, event):
            return await self.complete(event)

    class Event:
        def __init__(self, controller):
            self.controller = controller
            self.complete_visible_turn = AsyncMock()

        def get_extra(self, key, default=None):
            if key == "_interaction_output_controller":
                return self.controller
            return default

    controller = Controller()
    event = Event(controller)
    middleware = object.__new__(InteractionMiddleware)

    completed = await middleware._complete_visible_turn_or_record_failure(event)

    assert completed is True
    controller.complete.assert_awaited_once_with(event)
    event.complete_visible_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_persona_materializes_turn_before_delivery_completion():
    class Event:
        def __init__(self):
            self._extras = {}
            self.stop_event = Mock()

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

    order = []
    middleware = object.__new__(InteractionMiddleware)
    middleware._materialize_persona_reply_turn = Mock(
        side_effect=lambda *_args, **_kwargs: order.append("materialize")
    )
    middleware._complete_visible_turn_or_record_failure = AsyncMock(
        side_effect=lambda _event: order.append("complete") or True
    )
    middleware._finalize_turn = AsyncMock(
        side_effect=lambda _event: order.append("finalize")
    )
    event = Event()

    await middleware._complete_persona_only_turn(
        event,
        PersonaExpressionResult(spoken_reply="persona reply"),
    )

    assert order == ["materialize", "complete", "finalize"]
    event.stop_event.assert_called_once_with()


@pytest.mark.asyncio
async def test_respond_stage_delegates_interaction_completion_once():
    class Controller:
        def __init__(self):
            self.complete = AsyncMock(return_value=True)

        async def complete_visible_delivery(self, event):
            return await self.complete(event)

    class Event:
        def __init__(self, controller):
            self.controller = controller

        def get_extra(self, key, default=None):
            if key == "_interaction_output_controller":
                return self.controller
            return default

    controller = Controller()
    event = Event(controller)

    completed = await RespondStage()._dispatch_after_message_sent(event)

    assert completed is True
    controller.complete.assert_awaited_once_with(event)


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


@pytest.mark.asyncio
async def test_plugin_persona_output_keeps_non_text_components():
    class Event:
        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

    controller = object.__new__(InteractionOutputController)
    delivered = []
    controller._render_visible_reply = AsyncMock(
        return_value=PersonaExpressionResult(spoken_reply="rewritten reply")
    )
    controller._next_output_segment_id = lambda _event, _kind: "segment-1"
    controller._begin_plugin_output_transaction = lambda _event: False
    controller._record_plugin_assistant_artifacts = Mock()
    controller.materialize_interaction_outbound_message = AsyncMock(
        side_effect=lambda _event, message, **_kwargs: (message, {})
    )
    controller._deliver_visible_message = AsyncMock(
        side_effect=lambda _event, message, **_kwargs: delivered.append(message) or []
    )
    controller._record_visible_output = Mock()
    controller._materialize_finalized_turn = Mock()
    controller._persist_interaction_turn = AsyncMock()
    event = Event()

    await controller.capture_plugin_output(
        MessageChain([Plain("source reply"), Image("attachment.png")]),
        event,
        mode="persona",
    )

    assert len(delivered) == 1
    assert delivered[0].get_plain_text() == "rewritten reply"
    assert [type(component) for component in delivered[0].chain] == [Plain, Image]


@pytest.mark.asyncio
async def test_t2i_keeps_components_after_the_leading_text(monkeypatch):
    controller = object.__new__(InteractionOutputController)
    controller.t2i_word_threshold = 1
    controller.t2i_use_network = False
    controller.t2i_active_template = "base"
    controller._register_interaction_t2i_file_if_needed = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "astrbot.core.interaction.output_controller.html_renderer.render_t2i",
        AsyncMock(return_value="https://example.invalid/rendered.png"),
    )
    attachment = Image("attachment.png")
    message = MessageChain([Plain("long text"), attachment]).use_t2i(True)

    rendered, metadata = await controller._apply_interaction_t2i(object(), message)

    assert metadata["delivered_as"] == "image"
    assert [type(component) for component in rendered.chain] == [Image, Image]
    assert rendered.chain[1] is attachment


def test_plugin_lifecycle_target_does_not_override_tool_target(monkeypatch):
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

    lifecycle_overridden_event = Event("personal_expression")
    assert not tool_supports_runtime_target(
        lifecycle_overridden_event,
        tool,
        PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
    )
    assert tool_supports_runtime_target(
        lifecycle_overridden_event,
        tool,
        PLUGIN_RUNTIME_TARGET_CORE,
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
