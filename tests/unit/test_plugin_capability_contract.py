"""Small public-boundary checks for the breaking plugin capability migration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core import sp
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.interaction.effects import (
    PersonaEffectPreparationError,
    PersonaEffectSpec,
)
from astrbot.core.output_lifecycle import PreOutputProcessor
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.plugin_admission import build_plugin_admission_snapshot
from astrbot.core.plugin_runtime import (
    plugin_supports_runtime_target,
    tool_plugin_is_selected,
    tool_supports_runtime_target,
    validate_plugin_capability_targets,
)
from astrbot.core.prompt.context_collect import collect_context_pack
from astrbot.core.prompt.extensions import PromptExtension
from astrbot.core.star.context import Context, plugin_owner_scope
from astrbot.core.star.star import StarMetadata, star_map
from astrbot.core.star.star_handler import (
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)


class Event:
    unified_msg_origin = ""
    plugins_name = None

    def __init__(self, config=None):
        self.extras = {"_interaction_enabled": True, "_astrbot_config": config or {}}

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def is_stopped(self):
        return False


@pytest.mark.asyncio
async def test_prompt_collectors_merge_and_new_owner_cannot_enter_frozen_turn(
    monkeypatch,
):
    context = Context.__new__(Context)
    context._prompt_extension_collectors = []
    context._prompt_extension_collector_seq = 0
    event = Event()

    class Collector:
        def __init__(self, name):
            self.plugin_id = name

        async def collect(
            self, event, plugin_context, config, *, provider_request=None
        ):
            return [
                PromptExtension(
                    plugin_id=self.plugin_id,
                    mount="system",
                    value=self.plugin_id,
                    value_kind="text",
                    meta={"targets": ["persona"]},
                )
            ]

    for name in ("first", "second"):
        module = f"data.plugins.{name}.main"
        monkeypatch.setitem(
            star_map, module, StarMetadata(name=name, module_path=module)
        )
        with plugin_owner_scope(module_path=module, plugin_name=name):
            context.register_prompt_extension_collector(Collector(name))

    await build_plugin_admission_snapshot(event=event)
    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=SimpleNamespace(plugin_enrichment_timeout=1),
        collectors=[],
        prompt_extension_collector_scope="plugin",
    )
    value = pack.get_slot("extension.system").value
    assert "first" in str(value) and "second" in str(value)

    module = "data.plugins.late.main"
    monkeypatch.setitem(star_map, module, StarMetadata(name="late", module_path=module))
    with plugin_owner_scope(module_path=module, plugin_name="late"):
        context.register_prompt_extension_collector(Collector("late"))
    assert [c.plugin_id for c in context.list_prompt_extension_collectors(event)] == [
        "first",
        "second",
    ]
    monkeypatch.setitem(star_map, "data.plugins.first.main", StarMetadata(name="first"))
    assert [c.plugin_id for c in context.list_prompt_extension_collectors(event)] == [
        "second"
    ]


@pytest.mark.asyncio
async def test_required_effect_failure_is_visible_but_disabled_plugin_is_not_required(
    monkeypatch,
):
    context = Context.__new__(Context)
    context._persona_effects = []
    context._persona_effect_seq = 0
    module = "data.plugins.motion.main"
    monkeypatch.setitem(
        star_map, module, StarMetadata(name="motion", module_path=module)
    )

    def broken_schema(event):
        raise ValueError("profile unavailable")

    with plugin_owner_scope(module_path=module, plugin_name="motion"):
        context.register_persona_effect(
            PersonaEffectSpec(
                plugin_id="motion",
                name="motion.pose",
                description="Pose",
                parameters={"type": "object", "properties": {}},
                metadata={"required_per_segment": True},
                parameters_resolver=broken_schema,
            )
        )
    event = Event()
    await build_plugin_admission_snapshot(event=event)
    with pytest.raises(PersonaEffectPreparationError, match="profile unavailable"):
        context.list_persona_effects(event=event)
    denied = Event()
    denied.plugins_name = []
    await build_plugin_admission_snapshot(event=denied)
    assert context.list_persona_effects(event=denied) == []


def test_hook_and_tool_targets_are_independent(monkeypatch, tmp_path):
    import json

    from astrbot.core.config.astrbot_config import AstrBotConfig
    from astrbot.dashboard.routes.config import save_config

    module = "data.plugins.selected.main"
    monkeypatch.setitem(
        star_map, module, StarMetadata(name="selected", module_path=module)
    )
    binding = {
        "selected": {"llm_hooks": "core", "tools": {"lookup": "personal_expression"}}
    }
    validate_plugin_capability_targets(binding)
    event = Event({"interaction_middleware": {"plugin_capability_targets": binding}})
    tool = FunctionTool(
        name="lookup", description="Lookup", parameters={}, handler_module_path=module
    )
    assert plugin_supports_runtime_target(event, module, "core")
    assert tool_supports_runtime_target(event, tool, "personal_expression")
    assert not tool_supports_runtime_target(event, tool, "core")
    with pytest.raises(ValueError):
        validate_plugin_capability_targets({"selected": "core"})
    path = tmp_path / "config.json"
    config = AstrBotConfig(config_path=str(path), default_config={})
    save_config(event.get_extra("_astrbot_config"), config, is_core=True)
    persisted = json.loads(path.read_text(encoding="utf-8-sig"))
    assert persisted["interaction_middleware"]["plugin_capability_targets"] == binding


def test_external_mcp_tool_is_not_rejected_as_unknown_plugin():
    """Connected MCP tools are process-level Core capabilities."""
    enabled = Event({"provider_settings": {"web_search": True}})
    disabled = Event({"provider_settings": {"web_search": False}})
    for tool_name in ("web_search", "web__search"):
        external_tool = SimpleNamespace(
            name=tool_name,
            mcp_server_name="MiniMax",
            execution_targets=frozenset({"core"}),
        )
        assert tool_plugin_is_selected(enabled, external_tool)
        assert not tool_plugin_is_selected(disabled, external_tool)
        assert tool_supports_runtime_target(enabled, external_tool, "core")
        assert not tool_supports_runtime_target(
            enabled, external_tool, "personal_expression"
        )


@pytest.mark.asyncio
async def test_session_disabled_plugin_cannot_decorate_or_observe_delivery(monkeypatch):
    event = Event()
    event.unified_msg_origin = "test:FriendMessage:user"
    module = "data.plugins.output.main"
    monkeypatch.setitem(
        star_map, module, StarMetadata(name="output", module_path=module)
    )
    monkeypatch.setattr(
        sp,
        "get_async",
        AsyncMock(
            return_value={event.unified_msg_origin: {"disabled_plugins": ["output"]}}
        ),
    )
    callback = AsyncMock()
    handlers = [
        StarHandlerMetadata(
            event_type=event_type,
            handler_full_name=f"{module}.{event_type.name}",
            handler_name=event_type.name,
            handler_module_path=module,
            handler=callback,
            event_filters=[],
        )
        for event_type in (
            EventType.OnDecoratingResultEvent,
            EventType.OnAfterMessageSentEvent,
        )
    ]
    monkeypatch.setattr(star_handlers_registry, "_handlers", handlers)
    await PreOutputProcessor().run_decorating_hooks(event)
    await call_event_hook(event, EventType.OnAfterMessageSentEvent)
    callback.assert_not_awaited()
