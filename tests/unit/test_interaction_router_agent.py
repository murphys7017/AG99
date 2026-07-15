from unittest.mock import AsyncMock

import pytest

from astrbot.core.interaction.memory_store import InteractionMemorySnapshot
from astrbot.core.interaction.router_agent import (
    InteractionRouterAgent,
    build_interaction_router_system_prompt,
    extract_interaction_route_payload,
)
from astrbot.core.interaction.turn_state import (
    InteractionContextMaterial,
    InteractionTurnState,
)
from astrbot.core.interaction.types import (
    InteractionAgentConfig,
    InteractionRouteDecision,
    InteractionRouteMode,
)
from astrbot.core.prompt.context_types import ContextPack
from astrbot.core.prompt.render.interfaces import RenderResult
from astrbot.core.provider.entities import LLMResponse


class _EmptyMemoryStore:
    async def load_interaction_memory(
        self,
        session_id: str,
        persona_id: str = "",
    ) -> InteractionMemorySnapshot:
        return InteractionMemorySnapshot(
            session_id=session_id,
            persona_id=persona_id,
        )


def test_route_decision_accepts_persona_mode():
    decision = InteractionRouteDecision.from_mapping({"mode": "persona"})

    assert decision is not None
    assert decision.route_mode == InteractionRouteMode.PERSONA


def test_route_decision_rejects_delegate_mode_from_router_payload():
    decision = InteractionRouteDecision.from_mapping({"route_mode": "delegate_to_core"})

    assert decision is None


def test_route_decision_rejects_invalid_payload():
    assert InteractionRouteDecision.from_mapping({"mode": "maybe"}) is None


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ('{"mode":"silent"}', "silent"),
        ('{"mode":"persona"}', "persona"),
        ("hybrid", "hybrid"),
        ('"persona"', "persona"),
    ],
)
def test_extract_route_payload_accepts_json_and_plain_mode(text, mode):
    assert extract_interaction_route_payload(text) == {"mode": mode}


def test_extract_route_payload_rejects_legacy_self_reply_mode():
    assert extract_interaction_route_payload("self_reply") is None


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
            return LLMResponse(role="assistant", completion_text="persona")

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
    agent = InteractionRouterAgent(memory_store=_EmptyMemoryStore())

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

    assert route.route_mode == InteractionRouteMode.PERSONA
    assert event.get_extra("_interaction_router_result_source") == "parsed"
    assert event.get_extra("_interaction_router_raw_output") == "persona"
    assert "tool_choice" not in provider.calls[0]
    assert "output_contract" not in provider.calls[0]
    assert "compiled_output_contract" not in provider.calls[0]


def test_route_decision_contains_only_route_data():
    decision = InteractionRouteDecision(
        route_mode=InteractionRouteMode.PERSONA,
        reason="router",
    )

    assert decision.to_dict() == {
        "route_mode": "persona",
        "reason": "router",
    }


def test_router_system_prompt_uses_generic_local_capability_boundary():
    prompt = build_interaction_router_system_prompt()

    assert "严格的三分类选择器" in prompt
    assert "当前用户输入是首要依据" in prompt
    assert "用于理解当前对话" in prompt
    assert "不能单独成为选择 hybrid 的理由" in prompt
    assert "普通寒暄、情绪回应、轻量吐槽、短确认" in prompt
    assert "当前输入本身包含明确的执行、查询或处理意图" in prompt
    assert "当前说话者未完成的核心任务" in prompt
    assert "其他说话者的任务" in prompt
    assert "无明确执行意图的短消息选择 persona" in prompt
    assert "在 persona 与 hybrid 之间不确定时也选择 persona" in prompt
    assert "保持沉默比说话更自然" in prompt
    assert "统一拟人层可以直接完成回应" in prompt
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
    agent = InteractionRouterAgent(memory_store=_EmptyMemoryStore())

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
                        input_payload={"text": "hello"},
                        capability_payload={},
                        context_snapshot={"input": {"text": "hello"}},
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
    agent = InteractionRouterAgent(memory_store=_EmptyMemoryStore())

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
