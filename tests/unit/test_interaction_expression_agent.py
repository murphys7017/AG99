from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.core.interaction.effects import PersonaEffectCall, PersonaEffectSpec
from astrbot.core.interaction.expression_agent import (
    InteractionExpressionAgent,
    InteractionExpressionError,
    PersonaExpressionRequest,
    PersonaExpressionResult,
    _log_persona_prompt_size_diagnostics,
    add_persona_runtime_slots_to_pack,
    add_visible_reply_material_slots_to_pack,
    build_persona_expression_output_contract_for_effects,
    build_persona_expression_tool_parameters,
    build_persona_runtime_system_prompt,
    extract_persona_expression_result,
    maybe_inject_deepseek_first_turn_reasoning_marker,
    remove_redundant_media_slots_for_visible_reply_material,
    validate_persona_expression_result,
)
from astrbot.core.interaction.memory_store import InteractionMemoryStore
from astrbot.core.interaction.persona_runtime import InteractionPersonaRuntime
from astrbot.core.interaction.types import InteractionAgentConfig
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.output_contract import CompiledOutputContract
from astrbot.core.prompt.context_types import ContextPack, ContextSlot
from astrbot.core.prompt.render import PromptRenderEngine
from astrbot.core.prompt.render.interfaces import RenderResult
from astrbot.core.provider.entities import LLMResponse


def test_persona_prompt_size_diagnostics_logs_sizes_without_content(monkeypatch):
    log = Mock()
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.logger.info",
        log,
    )

    class Event:
        session_id = "session"

        @staticmethod
        def get_platform_id():
            return "platform"

    result = RenderResult(
        system_prompt="private system text",
        messages=[{"role": "user", "content": "private message text"}],
        tool_schema=[{"name": "private_tool"}],
        metadata={"prompt_slot_sizes": {"persona.prompt": 120}},
    )

    _log_persona_prompt_size_diagnostics(
        Event(),
        PersonaExpressionRequest(source_text="private source text"),
        result,
    )

    args = log.call_args.args
    assert args[0].startswith("DIAG expression.prompt_size:")
    assert args[-1] == {"persona.prompt": 120}
    assert "private system text" not in repr(args)
    assert "private message text" not in repr(args)


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
        output_contract=build_persona_expression_output_contract_for_effects(
            [effect],
        ),
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


def test_persona_expression_rejects_plain_text_when_protocol_tool_call_required():
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

    with pytest.raises(InteractionExpressionError) as exc_info:
        extract_persona_expression_result(
            "嗯，我直接说一句。",
            output_contract=build_persona_expression_output_contract_for_effects(
                [effect]
            ),
            effects=[effect],
        )

    assert exc_info.value.reason == "missing_persona_expression_tool_call"


def test_persona_expression_accepts_json_when_tool_call_contract_degrades_to_prompt_only():
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
        strategy="prompt_only",
        degraded=True,
        degrade_reason="renderer_has_no_protocol_support",
    )

    result = extract_persona_expression_result(
        '{"spoken_reply":"嗯。","effect_calls":[{"name":"ag99live.motion","arguments":{"emotion_label":"focused"}}]}',
        output_contract=contract,
        compiled_output_contract=compiled,
        effects=[effect],
    )

    assert result.spoken_reply == "嗯。"
    assert result.effect_calls == [
        PersonaEffectCall(
            name="ag99live.motion",
            arguments={"emotion_label": "focused"},
            plugin_id="plugin_a",
            source="persona",
        )
    ]


def test_persona_expression_repairs_json_when_tool_call_degrades_to_prompt_only():
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
        strategy="prompt_only",
        degraded=True,
        degrade_reason="renderer_has_no_protocol_support",
    )

    result = extract_persona_expression_result(
        '{"spoken_reply":"嗯。","effect_calls":[{"name":"ag99live.motion","arguments":{"emotion_label":"focused"}}]',
        output_contract=contract,
        compiled_output_contract=compiled,
        effects=[effect],
    )

    assert result.spoken_reply == "嗯。"
    assert result.effect_calls == [
        PersonaEffectCall(
            name="ag99live.motion",
            arguments={"emotion_label": "focused"},
            plugin_id="plugin_a",
            source="persona",
        )
    ]


def test_persona_expression_rejects_plain_text_when_tool_call_degrades_to_prompt_only():
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
        strategy="prompt_only",
        degraded=True,
        degrade_reason="renderer_has_no_protocol_support",
    )

    with pytest.raises(InteractionExpressionError) as exc_info:
        extract_persona_expression_result(
            "少熬夜，对脑子不好。",
            output_contract=contract,
            compiled_output_contract=compiled,
            effects=[effect],
        )

    assert exc_info.value.reason == "invalid_persona_expression_json"


def test_persona_expression_defaults_to_strict_tool_call_contract():
    schema = build_persona_expression_tool_parameters()
    contract = build_persona_expression_output_contract_for_effects()

    assert contract.mode == "tool_call"
    assert contract.strict is True
    assert contract.allow_text_fallback is False
    assert contract.preferred_tool_name == "persona_expression"
    assert schema["required"] == ["spoken_reply", "effect_calls"]


def test_persona_runtime_prompt_describes_generic_effect_schema_contract():
    prompt = build_persona_runtime_system_prompt()

    assert "persona_expression" in prompt
    assert "effect_calls 只能使用注册过的 effect 与参数 schema" in prompt
    assert "未声明字段不要输出" in prompt
    assert "intent_tags" not in prompt
    assert "axes" not in prompt


def test_persona_runtime_slots_are_native_system_base_not_extensions():
    pack = ContextPack()

    add_persona_runtime_slots_to_pack(pack, effects=[])

    assert pack.get_slot("system.base") is not None
    assert pack.get_slot("extension.system") is None
    result = PromptRenderEngine().render(pack)
    assert "system.base" in result.metadata["selected_slot_names"]
    assert "extension.system" not in result.metadata["selected_slot_names"]
    assert "<base" in result.system_prompt
    assert "<extensions>" not in result.system_prompt


def test_visible_reply_material_renders_as_native_input_message_with_stream_text():
    pack = ContextPack()

    add_visible_reply_material_slots_to_pack(
        pack,
        PersonaExpressionRequest(
            observed_text="核心已经流出",
            total_text="核心累计内容",
            pending_text="待完成内容",
            short_reply=True,
        ),
    )

    assert pack.get_slot("input.visible_reply_material") is not None
    assert pack.get_slot("extension.context") is None
    result = PromptRenderEngine().render(pack)
    assert "input.visible_reply_material" in result.metadata["selected_slot_names"]
    assert "extension.context" not in result.metadata["selected_slot_names"]
    assert len(result.messages) == 1
    material_text = result.messages[0]["content"][0]["text"]
    assert "<visible_reply_material>" in material_text
    assert "核心已经流出" in material_text
    assert "核心累计内容" in material_text
    assert "待完成内容" in material_text
    assert "extensions" not in material_text


def test_visible_reply_material_removes_redundant_media_slots():
    pack = ContextPack(
        slots={
            "input.images": ContextSlot(
                name="input.images",
                value=[{"ref": "https://example.com/image.png"}],
                category="input",
                source="event_input",
            ),
            "input.image_captions": ContextSlot(
                name="input.image_captions",
                value=[{"caption": "already described"}],
                category="input",
                source="image_caption_provider",
            ),
        }
    )

    remove_redundant_media_slots_for_visible_reply_material(
        pack,
        PersonaExpressionRequest(source_text="核心已经描述图片"),
    )

    assert pack.get_slot("input.images") is None
    assert pack.get_slot("input.image_captions") is None
    assert pack.meta["slot_count"] == 0


def test_direct_reply_keeps_media_slots():
    pack = ContextPack(
        slots={
            "input.images": ContextSlot(
                name="input.images",
                value=[{"ref": "https://example.com/image.png"}],
                category="input",
                source="event_input",
            )
        }
    )

    remove_redundant_media_slots_for_visible_reply_material(
        pack,
        PersonaExpressionRequest(),
    )

    assert pack.get_slot("input.images") is not None


def test_deepseek_first_turn_reasoning_marker_injects_once_for_v4_provider():
    class Provider:
        provider_config = {"type": "deepseek_chat_completion"}

        @staticmethod
        def get_model():
            return "deepseek-v4-flash"

    class Event:
        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

    pack = ContextPack(
        slots={
            "input.text": ContextSlot(
                name="input.text",
                value="你好",
                category="input",
                source="test",
            ),
            "memory.interaction": ContextSlot(
                name="memory.interaction",
                value={"recent_turns": []},
                category="memory",
                source="test",
            ),
        }
    )
    event = Event()

    assert maybe_inject_deepseek_first_turn_reasoning_marker(
        event,
        pack,
        Provider(),
    )
    assert "【角色沉浸要求】" in pack.get_slot("input.text").value
    assert not maybe_inject_deepseek_first_turn_reasoning_marker(
        event,
        pack,
        Provider(),
    )


def test_deepseek_first_turn_reasoning_marker_skips_nonfirst_turn_history():
    class Provider:
        provider_config = {"type": "deepseek_chat_completion"}

        @staticmethod
        def get_model():
            return "deepseek-v4-pro"

    class Event:
        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

    pack = ContextPack(
        slots={
            "input.text": ContextSlot(
                name="input.text",
                value="你好",
                category="input",
                source="test",
            ),
            "memory.interaction": ContextSlot(
                name="memory.interaction",
                value={"recent_turns": [{"user": "上轮", "assistant": "回复"}]},
                category="memory",
                source="test",
            ),
        }
    )

    assert not maybe_inject_deepseek_first_turn_reasoning_marker(
        Event(),
        pack,
        Provider(),
    )
    assert pack.get_slot("input.text").value == "你好"


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
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.Provider",
        Provider,
    )
    contract = build_persona_expression_output_contract_for_effects(
        [effect],
    )
    compiled = CompiledOutputContract(
        contract=contract,
        strategy="protocol_tool_call",
        tool_name="persona_expression",
        tool_schema=contract.schema,
    )
    agent._prepare_render_result = AsyncMock(
        return_value=RenderResult(
            system_prompt="persona",
            messages=[{"role": "user", "content": "hello"}],
            output_contract=contract,
            compiled_output_contract=compiled,
            metadata={"persona_effect_specs": [effect]},
        )
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
    assert provider.calls[0]["tool_choice"] == "required"


@pytest.mark.asyncio
async def test_persona_expression_keeps_prompt_only_contract_in_rendered_system_prompt(
    monkeypatch,
):
    class Provider:
        def __init__(self):
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            return LLMResponse(
                role="assistant",
                completion_text='{"spoken_reply":"嗯。","effect_calls":[]}',
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
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.Provider",
        Provider,
    )
    contract = build_persona_expression_output_contract_for_effects([])
    compiled = CompiledOutputContract(
        contract=contract,
        strategy="prompt_only",
        degraded=True,
        degrade_reason="renderer_has_no_protocol_support",
        fallback_prompt_text="必须只输出一个 JSON object。",
    )
    agent._prepare_render_result = AsyncMock(
        return_value=RenderResult(
            system_prompt="persona",
            messages=[{"role": "user", "content": "hello"}],
            output_contract=contract,
            compiled_output_contract=compiled,
            metadata={"persona_effect_specs": []},
        )
    )

    result = await agent.generate_expression(
        event,
        plugin_context,
        InteractionAgentConfig(expression_provider_id="persona"),
        PersonaExpressionRequest(),
    )

    assert result.spoken_reply == "嗯。"
    assert "必须只输出一个 JSON object" not in provider.calls[0]["prompt"]
    assert provider.calls[0]["prompt"] == "请按输出契约生成当前人格的用户可见回应，不要输出额外自由文本。"
    assert provider.calls[0]["tool_choice"] == "required"


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
