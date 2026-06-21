from unittest.mock import AsyncMock

import pytest

from astrbot.core.interaction.expression_agent import (
    InteractionExpressionAgent,
    PersonaExpressionRequest,
    PersonaExpressionResult,
    build_persona_expression_output_contract,
    extract_persona_expression_result,
)
from astrbot.core.interaction.memory_store import InteractionMemoryStore
from astrbot.core.interaction.persona_runtime import InteractionPersonaRuntime
from astrbot.core.interaction.types import InteractionAgentConfig
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.output_contract import CompiledOutputContract
from astrbot.core.prompt.render.interfaces import RenderResult
from astrbot.core.provider.entities import LLMResponse


def test_persona_expression_repairs_truncated_json_from_provider():
    text = (
        '{"spoken_reply": "……你倒是说句话啊，发个问号是什么意思。", '
        '"plugin_hints": {"ag99live_motion": {"axes": {"head_yaw": 40, '
        '"head_pitch": 45, "head_roll": 50}, '
        '"resource_id": "embarrassed_lookaway"}}'
    )

    result = extract_persona_expression_result(text)

    assert result.spoken_reply == "……你倒是说句话啊，发个问号是什么意思。"
    assert result.plugin_hints == {
        "ag99live_motion": {
            "axes": {
                "head_yaw": 40,
                "head_pitch": 45,
                "head_roll": 50,
            },
            "resource_id": "embarrassed_lookaway",
        }
    }


@pytest.mark.asyncio
async def test_persona_expression_passes_compiled_contract_and_returns_plugin_hints(
    monkeypatch,
):
    contract = build_persona_expression_output_contract()
    compiled = CompiledOutputContract(
        contract=contract,
        strategy="protocol_tool_call",
        tool_name="persona_expression",
        tool_schema=contract.schema,
    )

    class Provider:
        def __init__(self):
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            return LLMResponse(
                role="assistant",
                completion_text="",
                tools_call_name=["persona_expression"],
                tools_call_args=[
                    {
                        "spoken_reply": "嗯，我来看看。",
                        "plugin_hints": {
                            "ag99live_motion": {"emotion_label": "focused"}
                        },
                    }
                ],
            )

    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"

        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

    provider = Provider()
    plugin_context = type(
        "PluginContext",
        (),
        {"get_provider_by_id": lambda self, provider_id: provider},
    )()
    event = Event()
    agent = InteractionExpressionAgent(InteractionMemoryStore())
    agent._prepare_render_result = AsyncMock(
        return_value=RenderResult(
            system_prompt="persona",
            messages=[{"role": "user", "content": "hello"}],
            output_contract=contract,
            compiled_output_contract=compiled,
        )
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.Provider",
        Provider,
    )

    result = await agent.generate_expression(
        event,
        plugin_context,
        InteractionAgentConfig(expression_provider_id="persona"),
        PersonaExpressionRequest(phase="first_response"),
    )

    assert result.spoken_reply == "嗯，我来看看。"
    assert result.plugin_hints == {
        "ag99live_motion": {"emotion_label": "focused"}
    }
    assert provider.calls[0]["output_contract"] is contract
    assert provider.calls[0]["compiled_output_contract"] is compiled


@pytest.mark.asyncio
async def test_persona_runtime_publishes_plugin_output_hints_after_selection():
    expression_agent = type(
        "ExpressionAgent",
        (),
        {
            "rewrite_plugin_output_result": AsyncMock(
                return_value=PersonaExpressionResult(
                    spoken_reply="人格化结果",
                    plugin_hints={
                        "ag99live_motion": {"emotion_label": "satisfied"}
                    },
                )
            )
        },
    )()

    class Event:
        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

    event = Event()
    runtime = InteractionPersonaRuntime(expression_agent)

    rendered = await runtime.render_plugin_output(
        event,
        MessageChain([Plain("原始插件结果")]),
        plugin_context=object(),
        interaction_config=InteractionAgentConfig(),
    )

    assert rendered.get_plain_text() == "人格化结果"
    assert event.get_extra("_interaction_plugin_hints") == {
        "ag99live_motion": {"emotion_label": "satisfied"}
    }
