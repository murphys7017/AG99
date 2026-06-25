from unittest.mock import AsyncMock

import pytest

from astrbot.core.interaction.effects import PersonaEffectCall, PersonaEffectSpec
from astrbot.core.interaction.expression_agent import (
    InteractionExpressionAgent,
    InteractionExpressionError,
    PersonaExpressionRequest,
    PersonaExpressionResult,
    build_persona_expression_output_contract_for_effects,
    extract_persona_expression_result,
    validate_persona_expression_result,
)
from astrbot.core.interaction.memory_store import InteractionMemoryStore
from astrbot.core.interaction.persona_runtime import InteractionPersonaRuntime
from astrbot.core.interaction.types import InteractionAgentConfig
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.output_contract import CompiledOutputContract
from astrbot.core.prompt.render.interfaces import RenderResult
from astrbot.core.provider.entities import LLMResponse


def test_persona_expression_empty_result_without_effects_is_rejected():
    with pytest.raises(InteractionExpressionError) as exc_info:
        validate_persona_expression_result(
            PersonaExpressionRequest(),
            PersonaExpressionResult(spoken_reply=""),
        )

    assert exc_info.value.reason == "empty_output"


def test_persona_expression_allows_effect_only_reply_when_request_explicitly_allows_empty():
    validate_persona_expression_result(
        PersonaExpressionRequest(allow_empty=True),
        PersonaExpressionResult(
            spoken_reply="",
            effect_calls=[
                PersonaEffectCall(
                    name="ag99live.motion",
                    arguments={"axes": {"head_yaw": 40}},
                )
            ],
        ),
    )


def test_persona_expression_still_requires_reply_for_first_response_even_with_effect():
    with pytest.raises(InteractionExpressionError):
        validate_persona_expression_result(
            PersonaExpressionRequest(),
            PersonaExpressionResult(
                spoken_reply="",
                effect_calls=[
                    PersonaEffectCall(
                        name="ag99live.motion",
                        arguments={"axes": {"head_yaw": 40}},
                    )
                ],
            ),
        )


def test_persona_expression_repairs_truncated_json_from_provider():
    text = (
        '{"spoken_reply": "……你倒是说句话啊，发个问号是什么意思。", '
        '"effect_calls": [{"name":"ag99live.motion","arguments":{"axes":{"head_yaw":40,'
        '"head_pitch":45,"head_roll":50},"resource_id":"embarrassed_lookaway"}}]'
    )
    effect = PersonaEffectSpec(
        plugin_id="plugin_a",
        name="ag99live.motion",
        description="Live2D motion",
        parameters={
            "type": "object",
            "properties": {
                "axes": {"type": "object"},
                "resource_id": {"type": "string"},
            },
            "required": ["axes"],
        },
    )

    result = extract_persona_expression_result(text, effects=[effect])

    assert result.spoken_reply == "……你倒是说句话啊，发个问号是什么意思。"
    assert result.effect_calls == [
        PersonaEffectCall(
            name="ag99live.motion",
            arguments={
                "axes": {
                    "head_yaw": 40,
                    "head_pitch": 45,
                    "head_roll": 50,
                },
                "resource_id": "embarrassed_lookaway",
            },
            plugin_id="plugin_a",
            source="persona",
        )
    ]


def test_persona_expression_parses_effect_calls_from_json_fallback():
    effect = PersonaEffectSpec(
        plugin_id="plugin_a",
        name="ag99live.motion",
        description="Live2D motion",
        parameters={
            "type": "object",
            "properties": {"axes": {"type": "object"}},
            "required": ["axes"],
        },
    )

    result = extract_persona_expression_result(
        '{"spoken_reply":"嗯。","effect_calls":[{"name":"ag99live.motion","arguments":{"axes":{"head_yaw":40}}}]}',
        effects=[effect],
    )

    assert result.spoken_reply == "嗯。"
    assert result.effect_calls == [
        PersonaEffectCall(
            name="ag99live.motion",
            arguments={"axes": {"head_yaw": 40}},
            plugin_id="plugin_a",
            source="persona",
        )
    ]


def test_persona_expression_parses_tool_args_from_string_payload():
    effect = PersonaEffectSpec(
        plugin_id="plugin_a",
        name="ag99live.motion",
        description="Live2D motion",
        parameters={
            "type": "object",
            "properties": {
                "axes": {
                    "type": "object",
                    "properties": {"head_yaw": {"type": "number"}},
                }
            },
            "required": ["axes"],
        },
    )
    response = LLMResponse(
        role="assistant",
        completion_text="",
        tools_call_name=["persona_expression"],
        tools_call_args=[
            """{
                "spoken_reply":"嗯。",
                "effect_calls":"[{\\"name\\":\\"ag99live.motion\\",\\"arguments\\":{\\"axes\\":{\\"head_yaw\\":\\"55\\"}}}]"
            }"""
        ],
    )

    result = extract_persona_expression_result(
        "",
        llm_response=response,
        output_contract=build_persona_expression_output_contract_for_effects([effect]),
        effects=[effect],
    )

    assert result.spoken_reply == "嗯。"
    assert result.effect_calls == [
        PersonaEffectCall(
            name="ag99live.motion",
            arguments={"axes": {"head_yaw": 55.0}},
            plugin_id="plugin_a",
            source="persona",
        )
    ]


def test_persona_expression_records_effect_parse_issues_in_metadata():
    effect = PersonaEffectSpec(
        plugin_id="plugin_a",
        name="ag99live.motion",
        description="Live2D motion",
        parameters={
            "type": "object",
            "properties": {"axes": {"type": "object"}},
            "required": ["axes"],
        },
    )

    result = extract_persona_expression_result(
        '{"spoken_reply":"嗯。","effect_calls":[{"name":"ag99live.motion","arguments":{}},{"name":"unknown.effect","arguments":{}}]}',
        effects=[effect],
    )

    assert result.effect_calls == []
    assert result.metadata["effect_parse_issues"] == [
        {
            "index": 0,
            "name": "ag99live.motion",
            "reason": "missing required argument: axes",
        },
        {"index": 1, "name": "unknown.effect", "reason": "unknown_effect_name"},
    ]


@pytest.mark.asyncio
async def test_persona_expression_passes_compiled_contract_and_returns_effect_calls(
    monkeypatch,
):
    effect = PersonaEffectSpec(
        plugin_id="plugin_a",
        name="ag99live.motion",
        description="Live2D motion",
        parameters={
            "type": "object",
            "properties": {"emotion_label": {"type": "string"}},
            "required": [],
        },
    )
    contract = build_persona_expression_output_contract_for_effects([effect])
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
                        "effect_calls": [
                            {
                                "name": "ag99live.motion",
                                "arguments": {"emotion_label": "focused"},
                            }
                        ],
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
            metadata={"persona_effect_specs": [effect]},
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
        PersonaExpressionRequest(),
    )

    assert result.spoken_reply == "嗯，我来看看。"
    assert result.effect_calls == [
        PersonaEffectCall(
            name="ag99live.motion",
            arguments={"emotion_label": "focused"},
            plugin_id="plugin_a",
            source="persona",
        )
    ]
    assert provider.calls[0]["output_contract"] is contract
    assert provider.calls[0]["compiled_output_contract"] is compiled


@pytest.mark.asyncio
async def test_persona_runtime_publishes_plugin_output_effect_calls():
    expression_agent = type(
        "ExpressionAgent",
        (),
        {
            "express_visible_reply_result": AsyncMock(
                return_value=PersonaExpressionResult(
                    spoken_reply="人格化结果",
                    effect_calls=[
                        PersonaEffectCall(
                            name="ag99live.motion",
                            arguments={"emotion_label": "satisfied"},
                            plugin_id="plugin_a",
                        )
                    ],
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
    assert event.get_extra("_interaction_plugin_output_effect_calls") == [
        PersonaEffectCall(
            name="ag99live.motion",
            arguments={"emotion_label": "satisfied"},
            plugin_id="plugin_a",
        )
    ]


@pytest.mark.asyncio
async def test_persona_runtime_renders_core_reply_via_shared_visible_reply_entry():
    expression_agent = type(
        "ExpressionAgent",
        (),
        {
            "express_visible_reply_result": AsyncMock(
                return_value=PersonaExpressionResult(
                    spoken_reply="整理后的最终回复",
                    effect_calls=[
                        PersonaEffectCall(
                            name="ag99live.motion",
                            arguments={"emotion_label": "focused"},
                            plugin_id="plugin_a",
                        )
                    ],
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

    plugin_context = object()
    interaction_config = InteractionAgentConfig()

    reply = await runtime.render_core_reply(
        event,
        "原始 core 结果",
        plugin_context=plugin_context,
        interaction_config=interaction_config,
        immediate_reply="我先看一下。",
    )

    assert reply == "整理后的最终回复"
    expression_agent.express_visible_reply_result.assert_awaited_once_with(
        event,
        plugin_context,
        interaction_config,
        PersonaExpressionRequest(
            source_text="原始 core 结果",
            immediate_reply="我先看一下。",
            preserve_facts=True,
        ),
    )
