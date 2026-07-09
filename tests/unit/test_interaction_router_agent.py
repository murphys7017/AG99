from unittest.mock import AsyncMock

import pytest

from astrbot.core.interaction.router_agent import (
    InteractionRouterAgent,
    build_interaction_router_system_prompt,
    extract_interaction_route_payload,
)
from astrbot.core.interaction.effects import PersonaEffectCall
from astrbot.core.interaction.turn_state import (
    InteractionContextMaterial,
    InteractionTurnState,
)
from astrbot.core.interaction.types import (
    FastRouteMode,
    InteractionAgentConfig,
    InteractionRouteDecision,
    RouteMode,
)
from astrbot.core.prompt.context_types import ContextPack
from astrbot.core.prompt.extensions import PromptExtension
from astrbot.core.prompt.render.interfaces import RenderResult
from astrbot.core.provider.entities import LLMResponse


def test_route_decision_accepts_self_reply_mode():
    decision = InteractionRouteDecision.from_mapping({"mode": "self_reply"})

    assert decision is not None
    assert decision.mode == FastRouteMode.SELF_REPLY


def test_route_decision_maps_legacy_delegate_to_hybrid():
    decision = InteractionRouteDecision.from_mapping(
        {"route_mode": RouteMode.DELEGATE_TO_CORE.value}
    )

    assert decision is not None
    assert decision.mode == FastRouteMode.HYBRID


def test_route_decision_rejects_invalid_payload():
    assert InteractionRouteDecision.from_mapping({"mode": "maybe"}) is None


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ('{"mode":"self_reply"}', "self_reply"),
        ("hybrid", "hybrid"),
        ('"self_reply"', "self_reply"),
    ],
)
def test_extract_route_payload_accepts_json_and_plain_mode(text, mode):
    assert extract_interaction_route_payload(text) == {"mode": mode}


@pytest.mark.asyncio
async def test_router_provider_call_uses_plain_text_mode_contract(monkeypatch):
    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"
        message_str = "你好"

        def __init__(self):
            self._extras = {}

        def get_extra(self, key=None, default=None):
            if key is None:
                return self._extras
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

    class Provider:
        def __init__(self):
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            return LLMResponse(role="assistant", completion_text="self_reply")

    provider = Provider()
    plugin_context = type(
        "PluginContext",
        (),
        {
            "get_config": lambda self, umo=None: {},
            "get_provider_by_id": lambda self, provider_id: provider,
        },
    )()
    event = Event()
    agent = InteractionRouterAgent(memory_store=None)

    monkeypatch.setattr(
        "astrbot.core.interaction.router_agent.Provider",
        Provider,
    )
    monkeypatch.setattr(
        agent,
        "_prepare_render_result",
        AsyncMock(return_value=RenderResult(system_prompt="router", messages=[])),
    )

    route = await agent.route(
        event,
        plugin_context,
        InteractionAgentConfig(router_provider_id="router"),
    )

    assert route.mode == FastRouteMode.SELF_REPLY
    assert event.get_extra("_interaction_router_result_source") == "parsed"
    assert event.get_extra("_interaction_router_raw_output") == "self_reply"
    assert "tool_choice" not in provider.calls[0]
    assert "output_contract" not in provider.calls[0]
    assert "compiled_output_contract" not in provider.calls[0]


def test_route_decision_to_legacy_interaction_decision_omits_core_task_spec():
    decision = InteractionRouteDecision(mode=FastRouteMode.HYBRID)

    legacy = decision.to_interaction_decision(first_response="我先看看。")

    assert legacy.route_mode == RouteMode.HYBRID
    assert legacy.should_emit_immediate_reply is True
    assert legacy.immediate_spoken_reply == "我先看看。"
    assert legacy.core_task_spec is None


def test_route_decision_keeps_selected_persona_effect_calls():
    decision = InteractionRouteDecision(mode=FastRouteMode.SELF_REPLY)
    effect_call = PersonaEffectCall(
        name="example.effect",
        arguments={"intent": "acknowledge"},
        plugin_id="example_plugin",
    )

    selected = decision.to_interaction_decision(
        first_response="嗯。",
        effect_calls=[effect_call],
    )

    assert selected.effect_calls == [effect_call]


class PurposeAwarePromptContributor:
    plugin_id = "example.local_presence"

    def __init__(self):
        self.views = []

    async def collect(self, event, plugin_context, view):
        self.views.append(view)
        if view.purpose == "persona_reply":
            return PromptExtension(
                plugin_id=self.plugin_id,
                mount="capability",
                title="Persona-only Local Capability",
                value={"local_presence": {"enabled": True}},
                order=10,
                meta={"scope": "static", "node_type": "local_presence_capability"},
            )
        return []


class RouterScopedPromptContributor:
    plugin_id = "example.plugin_catalog"

    def __init__(self):
        self.views = []

    async def collect(self, event, plugin_context, view):
        self.views.append(view)
        if view.purpose == "router":
            return PromptExtension(
                plugin_id=self.plugin_id,
                mount="capability",
                value={
                    "plugins": [
                        {
                            "name": "Local Presence",
                            "description": "负责本地角色的待机、注意力和轻量身体表现。",
                        }
                    ]
                },
            )
        return []


def test_router_system_prompt_uses_generic_local_capability_boundary():
    prompt = build_interaction_router_system_prompt()

    assert "严格的二分类选择器" in prompt
    assert "当前用户输入是首要依据" in prompt
    assert "只能辅助判断当前消息是否明确延续既有任务" in prompt
    assert "不能单独成为选择 hybrid 的理由" in prompt
    assert "普通寒暄、情绪回应、轻量吐槽、短确认" in prompt
    assert "无明确执行意图的短消息也属于拟人层可处理" in prompt
    assert "含义很弱的短消息默认 self_reply，即使历史或 memory 中出现过任务" in prompt
    assert "明确需要核心 Agent 参与" in prompt
    assert "不要限制或枚举核心 Agent 的能力范围" in prompt
    assert "不要推断具体插件协议" in prompt
    assert "工具、检索、文件、代码、事实核验、复杂推理" not in prompt


@pytest.mark.asyncio
async def test_router_system_prompt_renders_as_native_system_base_not_extension():
    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"
        message_str = "hello"
        message_obj = type("Message", (), {"message": []})()

        def __init__(self):
            self._extras = {}

        def get_extra(self, key=None, default=None):
            if key is None:
                return self._extras
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

        def get_platform_name(self):
            return "webchat"

    class Provider:
        pass

    plugin_context = type(
        "PluginContext",
        (),
        {
            "get_config": lambda self, umo=None: {},
            "list_interaction_prompt_contributors": lambda self: [],
        },
    )()
    agent = InteractionRouterAgent(memory_store=None)

    render_result = await agent._prepare_render_result(
        Event(),
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(),
        provider=Provider(),
    )

    assert "<base" in render_result.system_prompt
    assert "extension.system" not in render_result.metadata["selected_slot_names"]
    assert "system.base" in render_result.metadata["selected_slot_names"]


@pytest.mark.asyncio
async def test_router_render_uses_scoped_provider_and_restores_event_provider(
    monkeypatch,
):
    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"
        message_str = "hello"
        message_obj = type("Message", (), {"message": []})()

        def __init__(self):
            self._extras = {
                "provider": "outer-provider",
                "_interaction_turn_state": InteractionTurnState(
                    turn_id="turn-1",
                    context_material=InteractionContextMaterial(
                        prompt_context_pack=ContextPack(),
                        persona_payload={"persona_id": "alice"},
                        capability_payload={},
                        decision_context={},
                        prompt_extensions_collected=True,
                    ),
                ),
            }

        def get_extra(self, key=None, default=None):
            if key is None:
                return self._extras
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

        def get_platform_name(self):
            return "webchat"

    class Provider:
        pass

    seen_providers = []

    class RenderEngine:
        def render(self, pack, *, event, **kwargs):
            seen_providers.append(kwargs["provider_request"].provider)
            return RenderResult(messages=[], system_prompt="")

    event = Event()
    provider = Provider()
    plugin_context = type(
        "PluginContext",
        (),
        {
            "get_config": lambda self, umo=None: {},
            "list_interaction_prompt_contributors": lambda self: [],
        },
    )()
    agent = InteractionRouterAgent(memory_store=None)

    monkeypatch.setattr(
        "astrbot.core.interaction.router_agent.Provider",
        Provider,
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.router_agent.PromptRenderEngine",
        lambda: RenderEngine(),
    )

    await agent._prepare_render_result(
        event,
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(),
        provider=provider,
    )

    assert seen_providers == [provider]
    assert event.get_extra("provider") == "outer-provider"


@pytest.mark.asyncio
async def test_router_prompt_excludes_persona_only_prompt_extensions(monkeypatch):
    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"
        message_str = "hello"
        message_obj = type("Message", (), {"message": []})()

        def __init__(self):
            self._extras = {
                "_interaction_turn_state": InteractionTurnState(
                    turn_id="turn-1",
                    context_material=InteractionContextMaterial(
                        prompt_context_pack=ContextPack(),
                        persona_payload={"persona_id": "alice"},
                        capability_payload={},
                        decision_context={},
                    ),
                ),
            }

        def get_extra(self, key=None, default=None):
            if key is None:
                return self._extras
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

        def get_platform_name(self):
            return "webchat"

    class Provider:
        pass

    contributor = PurposeAwarePromptContributor()

    class RenderEngine:
        def render(self, pack, *, event, **kwargs):
            capability_slot = pack.get_slot("extension.capability")
            titles = []
            if capability_slot is not None and isinstance(capability_slot.value, dict):
                titles = [item["title"] for item in capability_slot.value["items"]]
            return RenderResult(messages=[], system_prompt="\n".join(titles))

    event = Event()
    provider = Provider()
    plugin_context = type(
        "PluginContext",
        (),
        {
            "get_config": lambda self, umo=None: {},
            "list_interaction_prompt_contributors": lambda self: [contributor],
        },
    )()
    agent = InteractionRouterAgent(memory_store=None)

    monkeypatch.setattr(
        "astrbot.core.interaction.router_agent.Provider",
        Provider,
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.router_agent.PromptRenderEngine",
        lambda: RenderEngine(),
    )

    render_result = await agent._prepare_render_result(
        event,
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(),
        provider=provider,
    )

    assert contributor.views[0].purpose == "router"
    assert contributor.views[0].phase == "route"
    assert contributor.views[0].persona == {}
    assert contributor.views[0].interaction_memory == {}
    assert contributor.views[0].capabilities == {}
    assert contributor.views[0].input["text"] == "hello"
    assert "Persona-only Local Capability" not in render_result.system_prompt


@pytest.mark.asyncio
async def test_router_prompt_includes_router_scoped_capability_extensions(monkeypatch):
    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"
        message_str = "please do the local thing"
        message_obj = type("Message", (), {"message": []})()

        def __init__(self):
            self._extras = {}

        def get_extra(self, key=None, default=None):
            if key is None:
                return self._extras
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

        def get_platform_name(self):
            return "webchat"

    class Provider:
        pass

    contributor = RouterScopedPromptContributor()

    class RenderEngine:
        def render(self, pack, *, event, **kwargs):
            assert pack.get_slot("extension.capability") is None
            directory_slot = pack.get_slot("capability.router_plugin_directory")
            assert directory_slot is not None
            assert directory_slot.value == {
                "plugins": [
                    {
                        "name": "Local Presence",
                        "description": "负责本地角色的待机、注意力和轻量身体表现。",
                    }
                ]
            }
            plugin = directory_slot.value["plugins"][0]
            return RenderResult(
                messages=[],
                system_prompt=f"{plugin['name']}: {plugin['description']}",
            )

    event = Event()
    provider = Provider()
    plugin_context = type(
        "PluginContext",
        (),
        {
            "get_config": lambda self, umo=None: {},
            "list_interaction_prompt_contributors": lambda self: [contributor],
        },
    )()
    agent = InteractionRouterAgent(memory_store=None)

    monkeypatch.setattr(
        "astrbot.core.interaction.router_agent.Provider",
        Provider,
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.router_agent.PromptRenderEngine",
        lambda: RenderEngine(),
    )

    render_result = await agent._prepare_render_result(
        event,
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(),
        provider=provider,
    )

    assert contributor.views[0].purpose == "router"
    assert contributor.views[0].phase == "route"
    assert "Local Presence" in render_result.system_prompt
    assert "example.plugin_catalog" not in render_result.system_prompt
