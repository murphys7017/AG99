from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.capabilities import CapabilitySnapshot
from astrbot.core.interaction.collectors import PersonaVisibleReplyCollector
from astrbot.core.interaction.effects import PersonaEffectCall, PersonaEffectSpec
from astrbot.core.interaction.expression_agent import (
    InteractionExpressionAgent,
    InteractionExpressionError,
    PersonaExpressionIntent,
    PersonaExpressionRequest,
    PersonaExpressionResult,
    build_persona_expression_output_contract_for_effects,
    build_persona_expression_tool_parameters,
    build_persona_runtime_system_prompt,
    extract_persona_expression_result,
    resolve_deepseek_first_turn_reasoning_marker,
    validate_persona_expression_result,
)
from astrbot.core.interaction.types import InteractionAgentConfig
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.output_contract import CompiledOutputContract
from astrbot.core.prompt.context_types import ContextPack, ContextSlot
from astrbot.core.prompt.render import (
    PROMPT_APPLY_RESULT_EXTRA_KEY,
    PromptRenderEngine,
    PromptRenderProfile,
)
from astrbot.core.prompt.render.interfaces import RenderResult
from astrbot.core.provider.entities import LLMResponse


def _provider_context_text(call: dict) -> str:
    return "\n".join(str(message) for message in call.get("contexts", []))


def _persona_capabilities(tools: ToolSet) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        target="personal_expression",
        persona_id=None,
        selection_mode="test",
        tools=tuple(tools),
    )


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


def test_persona_expression_rejects_missing_required_effect():
    effect = PersonaEffectSpec(
        plugin_id="plugin_a",
        name="ag99live.motion",
        description="Live2D motion",
        parameters={"type": "object", "properties": {}},
        metadata={"required_per_segment": True},
    )

    with pytest.raises(InteractionExpressionError) as exc_info:
        validate_persona_expression_result(
            PersonaExpressionRequest(),
            PersonaExpressionResult(spoken_reply="嗯。"),
            effects=[effect],
        )

    assert exc_info.value.reason == "missing_required_persona_effect"


def test_persona_expression_rejects_invalid_required_effect():
    effect = PersonaEffectSpec(
        plugin_id="plugin_a",
        name="ag99live.motion",
        description="Live2D motion",
        parameters={"type": "object", "properties": {}},
        metadata={"required_per_segment": True},
    )
    result = PersonaExpressionResult(
        spoken_reply="嗯。",
        metadata={
            "effect_parse_issues": [
                {"name": "ag99live.motion", "reason": "arguments_invalid"}
            ]
        },
    )

    with pytest.raises(InteractionExpressionError) as exc_info:
        validate_persona_expression_result(
            PersonaExpressionRequest(),
            result,
            effects=[effect],
        )

    assert exc_info.value.reason == "invalid_required_persona_effect"


def test_persona_expression_rejects_duplicate_exactly_one_required_effect():
    effect = PersonaEffectSpec(
        plugin_id="plugin_a",
        name="ag99live.motion",
        description="Live2D motion",
        parameters={"type": "object", "properties": {}},
        metadata={
            "required_per_segment": True,
            "exactly_one_per_segment": True,
        },
    )
    result = PersonaExpressionResult(
        spoken_reply="嗯。",
        effect_calls=[
            PersonaEffectCall(name="ag99live.motion", arguments={}),
            PersonaEffectCall(name="ag99live.motion", arguments={}),
        ],
    )

    with pytest.raises(InteractionExpressionError) as exc_info:
        validate_persona_expression_result(
            PersonaExpressionRequest(),
            result,
            effects=[effect],
        )

    assert exc_info.value.reason == "required_persona_effect_count"


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
    assert schema["required"] == ["spoken_reply", "speech_cues", "effect_calls"]


def test_persona_expression_tool_requires_exactly_one_required_effect_per_segment():
    effect = PersonaEffectSpec(
        plugin_id="plugin_a",
        name="ag99live.motion",
        description="Live2D motion",
        parameters={
            "type": "object",
            "properties": {"intent_tags": {"type": "array"}},
            "required": ["intent_tags"],
        },
        metadata={
            "required_per_segment": True,
            "exactly_one_per_segment": True,
        },
    )

    schema = build_persona_expression_tool_parameters([effect])

    assert schema["properties"]["effect_calls"]["minItems"] == 1
    assert schema["properties"]["effect_calls"]["maxItems"] == 1


def test_required_effect_guidance_requires_schema_execution_shape_without_defaults():
    effect = PersonaEffectSpec(
        plugin_id="plugin_a",
        name="ag99live.motion",
        description="Live2D motion",
        parameters={
            "type": "object",
            "properties": {
                "intent_tags": {"type": "array"},
                "axis_levels": {"type": "object"},
            },
            "required": ["intent_tags"],
            "oneOf": [{"required": ["axis_levels"]}],
        },
        metadata={"required_per_segment": True},
    )

    prompt = build_persona_runtime_system_prompt([effect])
    schema = build_persona_expression_tool_parameters([effect])
    effect_calls_description = schema["properties"]["effect_calls"]["description"]

    assert "语义标签或注释字段不能代替 schema 要求的执行形态" in prompt
    assert "自行编造默认值" in prompt
    assert "Semantic labels or annotations do not replace an execution shape" in (
        effect_calls_description
    )
    assert "neutral values" not in effect_calls_description


def test_persona_runtime_slots_are_native_system_base_not_extensions():
    pack = ContextPack()
    result = PromptRenderEngine().render(
        pack,
        profile=PromptRenderProfile(
            name="interaction_persona_runtime",
            system_prompt=build_persona_runtime_system_prompt(),
            output_contract=build_persona_expression_output_contract_for_effects(),
        ),
    )

    assert pack.get_slot("system.base") is None
    assert "system.base" in result.metadata["selected_slot_names"]
    assert "extension.system" not in result.metadata["selected_slot_names"]
    assert "<base" in result.system_prompt
    assert "<extensions>" not in result.system_prompt


@pytest.mark.asyncio
async def test_visible_reply_material_renders_as_native_input_message_with_stream_text():
    slots = await PersonaVisibleReplyCollector(
        PersonaExpressionRequest(
            observed_text="核心已经流出",
            total_text="核心累计内容",
            pending_text="待完成内容",
            short_reply=True,
        )
    ).collect(None, None, None)
    pack = ContextPack(slots={slot.name: slot for slot in slots})

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


@pytest.mark.asyncio
async def test_core_visible_reply_material_preserves_immediate_reply_context():
    request = PersonaExpressionRequest.core_final(
        "核心天气结果",
        immediate_reply="我没有联网能力",
    )
    slots = await PersonaVisibleReplyCollector(
        request
    ).collect(None, None, None)

    assert request.intent == PersonaExpressionIntent(
        source="core_result",
        phase="final",
    )
    assert slots[0].value == {
        "source_text": "核心天气结果",
        "immediate_reply": "我没有联网能力",
        "preserve_facts": True,
    }


def test_visible_reply_material_profile_hides_redundant_media_slots():
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

    result = PromptRenderEngine().render(
        pack,
        profile=PromptRenderProfile(
            name="interaction_persona_runtime",
            hidden_slot_names=frozenset(
                {"input.images", "input.image_captions"}
            ),
        ),
    )

    assert pack.get_slot("input.images") is not None
    assert pack.get_slot("input.image_captions") is not None
    assert "input.images" not in result.metadata["selected_slot_names"]
    assert "input.image_captions" not in result.metadata["selected_slot_names"]


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

    result = PromptRenderEngine().render(pack)

    assert pack.get_slot("input.images") is not None
    assert "input.images" in result.metadata["selected_slot_names"]


@pytest.mark.asyncio
async def test_fast_persona_projection_keeps_identity_history_memory_without_waiting(
    monkeypatch,
):
    class Event:
        session_id = "fast-persona"
        unified_msg_origin = "webchat:friend:fast-persona"

        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

    class PluginContext:
        @staticmethod
        def get_config(**kwargs):
            del kwargs
            return {}

        @staticmethod
        def list_persona_effects(**kwargs):
            del kwargs
            return []

    class Provider:
        provider_config = {}

    pack = ContextPack(
        slots={
            "persona.segments": ContextSlot(
                name="persona.segments",
                value={"identity": ["Yakumo"]},
                category="persona",
                source="test",
            ),
            "persona.summary": ContextSlot(
                name="persona.summary",
                value={"identity": ["Yakumo"]},
                category="persona",
                source="test",
            ),
            "conversation.history": ContextSlot(
                name="conversation.history",
                value={"turns": []},
                category="memory",
                source="test",
            ),
            "memory.long_term_memories": ContextSlot(
                name="memory.long_term_memories",
                value={"items": ["长期记忆"]},
                category="memory",
                source="test",
            ),
            "extension.system": ContextSlot(
                name="extension.system",
                value={
                    "format": "prompt_extensions_v1",
                    "mount": "system",
                    "items": [
                        {
                            "plugin_id": "ag99live",
                            "title": "Motion Capability",
                            "value": {"axes": ["head_roll"]},
                            "meta": {"targets": ["persona", "core"]},
                        }
                    ],
                },
                category="extension",
                source="test",
                render_mode="structured",
                meta={"targets": ["persona", "core"]},
            ),
            "extension.context": ContextSlot(
                name="extension.context",
                value={"items": [{"value": "dynamic reference"}]},
                category="extension",
                source="test",
                render_mode="structured",
                meta={"targets": ["persona", "core"]},
            ),
        }
    )

    class Builder:
        def __init__(self, *args):
            del args

        async def build(self, **kwargs):
            return kwargs["base"]

    base_pack = ContextPack(
        slots={
            name: slot
            for name, slot in pack.slots.items()
            if not name.startswith("extension.")
        }
    )

    agent = InteractionExpressionAgent()
    material = SimpleNamespace(
        effective_persona_context=None,
        persona_definition=None,
        persona_payload={},
        prompt_context_pack=base_pack,
        target_context_packs={},
    )
    agent._build_or_reuse_context_material = AsyncMock(return_value=material)
    get_persona_context_pack = AsyncMock(return_value=pack)
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.PromptContextBuilder",
        Builder,
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.get_or_build_interaction_persona_context_pack",
        get_persona_context_pack,
    )

    result = await agent._prepare_render_result(
        Event(),
        PluginContext(),
        InteractionAgentConfig(),
        Provider(),
        req=PersonaExpressionRequest(compact_context=True),
    )

    selected_slots = set(result.metadata["selected_slot_names"])
    assert {
        "persona.summary",
        "conversation.history",
        "memory.long_term_memories",
    } <= selected_slots
    assert "persona.segments" not in selected_slots
    assert "persona.begin_dialogs" not in selected_slots
    assert "extension.system" not in selected_slots
    assert "extension.context" not in selected_slots
    get_persona_context_pack.assert_not_awaited()

    material.target_context_packs["plugin"] = pack
    ready_result = await agent._prepare_render_result(
        Event(),
        PluginContext(),
        InteractionAgentConfig(),
        Provider(),
        req=PersonaExpressionRequest(compact_context=True),
    )

    ready_slots = set(ready_result.metadata["selected_slot_names"])
    assert "extension.system" in ready_slots
    assert "extension.context" not in ready_slots
    get_persona_context_pack.assert_not_awaited()


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
            "conversation.history": ContextSlot(
                name="conversation.history",
                value={"turns": []},
                category="memory",
                source="test",
            ),
        }
    )
    event = Event()

    marker = resolve_deepseek_first_turn_reasoning_marker(
        event,
        pack,
        Provider(),
    )
    assert "【角色沉浸要求】" in marker
    assert pack.get_slot("input.text").value == "你好"
    result = PromptRenderEngine().render(
        pack,
        profile=PromptRenderProfile(
            name="persona",
            input_text_suffix=marker,
        ),
    )
    assert "【角色沉浸要求】" in result.messages[-1]["content"]
    assert not resolve_deepseek_first_turn_reasoning_marker(
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
            "conversation.history": ContextSlot(
                name="conversation.history",
                value={"turns": [{"user": "上轮", "assistant": "回复"}]},
                category="memory",
                source="test",
            ),
        }
    )

    assert not resolve_deepseek_first_turn_reasoning_marker(
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
    provider.provider_config = {
        "id": "persona",
        "type": "test",
        "modalities": ["text", "tool_use"],
    }
    plugin_context = type(
        "PluginContext",
        (),
        {
            "get_provider_by_id": lambda self, provider_id: provider,
            "get_config": lambda self, **kwargs: {},
        },
    )()
    event = Event()
    event.plugins_name = []
    event.is_stopped = lambda: False
    agent = InteractionExpressionAgent()
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
            request_prompt="请按输出契约生成当前人格的用户可见回应，不要输出额外自由文本。",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "file:///C:/tmp/screen.jpg"},
                        },
                    ],
                }
            ],
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
    assert provider.calls[0]["temperature"] == 0.6
    assert "hello" in _provider_context_text(provider.calls[0])
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_persona_expression_keeps_prompt_only_contract(
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
            self.plugins_name = []
            self._stopped = False

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

        def is_stopped(self):
            return self._stopped

    provider = Provider()
    provider.provider_config = {"id": "persona", "type": "test"}
    plugin_context = type(
        "PluginContext",
        (),
        {
            "get_provider_by_id": lambda self, provider_id: provider,
            "get_config": lambda self, **kwargs: {},
        },
    )()
    event = Event()
    agent = InteractionExpressionAgent()
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
            request_prompt="请按输出契约生成当前人格的用户可见回应，不要输出额外自由文本。",
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
    assert provider.calls[0]["compiled_output_contract"] is compiled


@pytest.mark.asyncio
async def test_persona_expression_reuses_official_request_and_response_hooks(
    monkeypatch,
):
    class Provider:
        provider_config = {"id": "persona", "type": "test"}

        def __init__(self):
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            return LLMResponse(
                role="assistant",
                completion_text="",
                result_chain=MessageChain(),
                tools_call_name=["persona_expression"],
                tools_call_args=[{"spoken_reply": "初始回复", "effect_calls": []}],
            )

    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"
        plugins_name = []

        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

        def is_stopped(self):
            return False

    provider = Provider()
    plugin_context = type(
        "PluginContext",
        (),
        {
            "get_provider_by_id": lambda self, provider_id: provider,
            "get_config": lambda self, **kwargs: {},
        },
    )()
    contract = build_persona_expression_output_contract_for_effects([])
    compiled = CompiledOutputContract(
        contract=contract,
        strategy="protocol_tool_call",
        tool_name="persona_expression",
        tool_schema=contract.schema,
    )
    agent = InteractionExpressionAgent()
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.Provider",
        Provider,
    )
    agent._prepare_render_result = AsyncMock(
        return_value=RenderResult(
            system_prompt="persona",
            request_prompt="reply naturally",
            messages=[{"role": "user", "content": "hello"}],
            output_contract=contract,
            compiled_output_contract=compiled,
            metadata={"persona_effect_specs": []},
        )
    )
    observed_hooks = []
    observed_events = []
    hooked_request = None

    async def call_hook(event, hook_type, *args, **kwargs):
        nonlocal hooked_request
        assert kwargs["execution_surface"] == "personal_expression"
        observed_hooks.append(hook_type.name)
        observed_events.append(event)
        if hook_type.name in {
            "OnWaitingLLMRequestEvent",
            "OnLLMRequestEvent",
        }:
            assert event.get_extra(PROMPT_APPLY_RESULT_EXTRA_KEY) is not None
        if hook_type.name == "OnLLMRequestEvent":
            request = args[0]
            hooked_request = request
            request.system_prompt += "\nplugin context"
            request.output_contract = None
            request.compiled_output_contract = None
        elif hook_type.name == "OnAgentBeginEvent":
            assert isinstance(args[0].context, AstrAgentContext)
            assert args[0].context.event.get_extra("provider_request") is hooked_request
        elif hook_type.name == "OnLLMResponseEvent":
            assert args[0].completion_text == "初始回复"
            assert args[0].result_chain.get_plain_text() == "初始回复"
            args[0].completion_text = "插件修饰后的回复"
        return False

    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.call_event_hook",
        call_hook,
    )

    original_event = Event()
    result = await agent.generate_expression(
        original_event,
        plugin_context,
        InteractionAgentConfig(expression_provider_id="persona"),
        PersonaExpressionRequest(),
    )

    assert observed_hooks == [
        "OnWaitingLLMRequestEvent",
        "OnLLMRequestEvent",
        "OnAgentBeginEvent",
        "OnLLMResponseEvent",
        "OnAgentDoneEvent",
        "OnPersonaExpressionResultEvent",
    ]
    context_text = _provider_context_text(provider.calls[0])
    assert "persona" in context_text
    assert "plugin context" in context_text
    assert provider.calls[0]["output_contract"] is contract
    assert provider.calls[0]["compiled_output_contract"] is compiled
    assert result.spoken_reply == "插件修饰后的回复"
    assert observed_events == [original_event] * len(observed_hooks)
    assert original_event.get_extra("provider_request") is None
    assert original_event.get_extra(PROMPT_APPLY_RESULT_EXTRA_KEY) is None


@pytest.mark.asyncio
async def test_persona_expression_dispatches_official_tool_hooks_once(
    monkeypatch,
):
    class Provider:
        provider_config = {
            "id": "persona",
            "type": "test",
            "modalities": ["text", "tool_use"],
        }

        def __init__(self):
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return LLMResponse(
                    role="assistant",
                    completion_text="",
                    tools_call_name=["read_context"],
                    tools_call_args=[{}],
                    tools_call_ids=["call-read-context"],
                )
            return LLMResponse(
                role="assistant",
                completion_text="",
                tools_call_name=["persona_expression"],
                tools_call_args=[{"spoken_reply": "完成了", "effect_calls": []}],
            )

    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"
        plugins_name = []

        def __init__(self):
            self._extras = {}
            self._result = None
            self._force_stopped = False

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

        def is_stopped(self):
            return False

        def clear_result(self):
            self._result = None

        def get_result(self):
            return self._result

    async def read_context(_event):
        return "工具事实"

    tool = FunctionTool(
        name="read_context",
        description="Read additional context.",
        parameters={"type": "object", "properties": {}},
        handler=read_context,
        execution_targets={"personal_expression"},
    )
    tools = ToolSet([tool])
    provider = Provider()
    class PluginContext:
        def get_provider_by_id(self, provider_id):
            return provider

        def get_config(self, **kwargs):
            return {}

    contract = build_persona_expression_output_contract_for_effects([])
    compiled = CompiledOutputContract(
        contract=contract,
        strategy="protocol_tool_call",
        tool_name="persona_expression",
        tool_schema=contract.schema,
    )
    agent = InteractionExpressionAgent()
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.Provider",
        Provider,
    )
    agent._prepare_render_result = AsyncMock(
        return_value=RenderResult(
            system_prompt="persona",
            request_prompt="reply naturally",
            messages=[{"role": "user", "content": "hello"}],
            output_contract=contract,
            compiled_output_contract=compiled,
            metadata={"persona_effect_specs": []},
        )
    )
    agent._resolve_personal_expression_capabilities = AsyncMock(
        return_value=_persona_capabilities(tools)
    )
    observed_hooks = []

    expected_intent = {
        "kind": "reply",
        "source": "core_result",
        "phase": "final",
    }

    async def call_hook(event, hook_type, *args, **kwargs):
        assert kwargs["execution_surface"] == "personal_expression"
        if hook_type.name != "OnPersonaExpressionResultEvent":
            request = event.get_extra("provider_request")
            assert (
                request.metadata["interaction.persona_expression_intent"]
                == expected_intent
            )
            if hook_type.name == "OnLLMRequestEvent":
                assert (
                    args[0].metadata["interaction.persona_expression_intent"]
                    == expected_intent
                )
        observed_hooks.append(hook_type.name)
        return False

    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.call_event_hook",
        call_hook,
    )
    event = Event()

    result = await agent.generate_expression(
        event,
        PluginContext(),
        InteractionAgentConfig(expression_provider_id="persona"),
        PersonaExpressionRequest(
            intent=PersonaExpressionIntent(
                source="core_result",
                phase="final",
            )
        ),
    )

    assert observed_hooks == [
        "OnWaitingLLMRequestEvent",
        "OnLLMRequestEvent",
        "OnAgentBeginEvent",
        "OnUsingLLMToolEvent",
        "OnLLMToolRespondEvent",
        "OnLLMResponseEvent",
        "OnAgentDoneEvent",
        "OnPersonaExpressionResultEvent",
    ]
    assert len(provider.calls) == 2
    assert "工具事实" in _provider_context_text(provider.calls[1])
    assert result.spoken_reply == "完成了"


@pytest.mark.asyncio
async def test_persona_request_hook_cannot_inject_core_tools_into_persona(monkeypatch):
    class Provider:
        provider_config = {"id": "persona", "type": "test"}

        def __init__(self):
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            return LLMResponse(
                role="assistant",
                completion_text="",
                tools_call_name=["persona_expression"],
                tools_call_args=[{"spoken_reply": "无需工具", "effect_calls": []}],
            )

    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"
        plugins_name = []

        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

        def is_stopped(self):
            return False

    tool = FunctionTool(
        name="legacy_tool",
        description="A removable Persona tool.",
        parameters={"type": "object", "properties": {}},
        execution_targets={"personal_expression"},
    )
    core_tool = FunctionTool(
        name="core_tool",
        description="A Core-only tool injected by a request hook.",
        parameters={"type": "object", "properties": {}},
    )
    tools = ToolSet([tool])
    contract = build_persona_expression_output_contract_for_effects([])
    compiled = CompiledOutputContract(
        contract=contract,
        strategy="protocol_tool_call",
        tool_name="persona_expression",
        tool_schema=contract.schema,
    )
    agent = InteractionExpressionAgent()
    agent._prepare_render_result = AsyncMock(
        return_value=RenderResult(
            system_prompt="persona",
            request_prompt="reply naturally",
            messages=[{"role": "user", "content": "hello"}],
            output_contract=contract,
            compiled_output_contract=compiled,
            metadata={"persona_effect_specs": []},
        )
    )
    agent._resolve_personal_expression_capabilities = AsyncMock(
        return_value=_persona_capabilities(tools)
    )

    async def call_hook(_event, hook_type, *args, **_kwargs):
        if hook_type.name == "OnLLMRequestEvent":
            args[0].func_tool = ToolSet([core_tool])
        return False

    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.call_event_hook",
        call_hook,
    )
    provider = Provider()
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.resolve_interaction_chat_provider",
        AsyncMock(return_value=(provider, "persona")),
    )

    result = await agent.generate_expression(
        Event(),
        SimpleNamespace(
            get_provider_by_id=lambda _provider_id: Provider(),
            get_config=lambda **_kwargs: {},
        ),
        InteractionAgentConfig(expression_provider_id="persona"),
        PersonaExpressionRequest(),
    )

    assert provider.calls[0]["func_tool"] is None
    assert result.spoken_reply == "无需工具"


@pytest.mark.asyncio
async def test_persona_tools_available_but_unused_need_one_model_call(
    monkeypatch,
):
    class Provider:
        provider_config = {
            "id": "persona",
            "type": "test",
            "modalities": ["text", "tool_use"],
        }

        def __init__(self):
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            return LLMResponse(
                role="assistant",
                completion_text="",
                tools_call_name=["persona_expression"],
                tools_call_args=[{"spoken_reply": "直接人格回复", "effect_calls": []}],
            )

    class Event:
        session_id = "session-1"
        unified_msg_origin = "test:FriendMessage:session-1"

        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "test"

    contract = build_persona_expression_output_contract_for_effects([])
    compiled = CompiledOutputContract(
        contract=contract,
        strategy="protocol_tool_call",
        tool_name="persona_expression",
        tool_schema=contract.schema,
    )
    agent = InteractionExpressionAgent()
    agent._prepare_render_result = AsyncMock(
        return_value=RenderResult(
            system_prompt="persona",
            request_prompt="reply naturally",
            messages=[{"role": "user", "content": "hello"}],
            output_contract=contract,
            compiled_output_contract=compiled,
            metadata={"persona_effect_specs": []},
        )
    )
    agent._resolve_personal_expression_capabilities = AsyncMock(
        return_value=_persona_capabilities(
            ToolSet(
                [
                    FunctionTool(
                        name="optional_tool",
                        description="Optional Persona tool.",
                        parameters={"type": "object", "properties": {}},
                        execution_targets={"personal_expression"},
                    )
                ]
            )
        )
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.call_event_hook",
        AsyncMock(return_value=False),
    )
    provider = Provider()
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.resolve_interaction_chat_provider",
        AsyncMock(return_value=(provider, "persona")),
    )

    result = await agent.generate_expression(
        Event(),
        SimpleNamespace(
            get_config=lambda **_kwargs: {},
            get_provider_by_id=lambda _provider_id: Provider(),
        ),
        InteractionAgentConfig(expression_provider_id="persona"),
        PersonaExpressionRequest(),
    )

    assert len(provider.calls) == 1
    agent._resolve_personal_expression_capabilities.assert_awaited_once()
    assert result.spoken_reply == "直接人格回复"


@pytest.mark.asyncio
async def test_persona_tool_failure_does_not_restart_the_tool_loop(monkeypatch):
    class Provider:
        def __init__(self, provider_id, *, fallback=False):
            self.provider_config = {
                "id": provider_id,
                "type": "test",
                "modalities": ["text", "tool_use"],
            }
            self.fallback = fallback
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            if self.fallback:
                return LLMResponse(
                    role="assistant",
                    completion_text="",
                    tools_call_name=["persona_expression"],
                    tools_call_args=[
                        {"spoken_reply": "不应回退", "effect_calls": []}
                    ],
                )
            if len(self.calls) == 1:
                return LLMResponse(
                    role="assistant",
                    completion_text="",
                    tools_call_name=["legacy_tool"],
                    tools_call_args=[{}],
                    tools_call_ids=["call-legacy-tool"],
                )
            else:
                raise RuntimeError("primary unavailable")

    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:FriendMessage:session-1"
        plugins_name = []

        def __init__(self):
            self._extras = {}
            self._result = None
            self._force_stopped = False

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

        def is_stopped(self):
            return False

        def get_result(self):
            return self._result

        def clear_result(self):
            self._result = None

    execution_count = 0

    async def legacy_tool(_event):
        nonlocal execution_count
        execution_count += 1
        return "side effect completed"

    primary = Provider("primary")
    fallback = Provider("fallback", fallback=True)
    tool = FunctionTool(
        name="legacy_tool",
        description="Can fail after side effects.",
        parameters={"type": "object", "properties": {}},
        handler=legacy_tool,
        execution_targets={"personal_expression"},
    )
    tools = ToolSet([tool])
    contract = build_persona_expression_output_contract_for_effects([])
    compiled = CompiledOutputContract(
        contract=contract,
        strategy="protocol_tool_call",
        tool_name="persona_expression",
        tool_schema=contract.schema,
    )
    agent = InteractionExpressionAgent()
    agent._prepare_render_result = AsyncMock(
        return_value=RenderResult(
            system_prompt="persona",
            request_prompt="reply naturally",
            messages=[{"role": "user", "content": "hello"}],
            output_contract=contract,
            compiled_output_contract=compiled,
            metadata={"persona_effect_specs": []},
        )
    )
    agent._resolve_personal_expression_capabilities = AsyncMock(
        return_value=_persona_capabilities(tools)
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.resolve_interaction_chat_provider",
        AsyncMock(return_value=(primary, "primary")),
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.resolve_fallback_chat_providers",
        lambda *args: [fallback],
    )

    with pytest.raises(InteractionExpressionError):
        await agent.generate_expression(
            Event(),
            SimpleNamespace(
                get_provider_by_id=lambda _provider_id: primary,
                get_config=lambda **_kwargs: {},
            ),
            InteractionAgentConfig(expression_provider_id="primary"),
            PersonaExpressionRequest(),
        )

    assert execution_count == 1
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_persona_request_hook_context_mutation_survives_business_tool_loop(
    monkeypatch,
):
    class Provider:
        provider_config = {
            "id": "persona",
            "type": "test",
            "modalities": ["text", "tool_use"],
        }

        def __init__(self):
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return LLMResponse(
                    role="assistant",
                    completion_text="",
                    tools_call_name=["legacy_tool"],
                    tools_call_args=[{}],
                    tools_call_ids=["call-legacy-tool"],
                )
            return LLMResponse(
                role="assistant",
                completion_text="",
                tools_call_name=["persona_expression"],
                tools_call_args=[{"spoken_reply": "完成了", "effect_calls": []}],
            )

    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:FriendMessage:session-1"
        plugins_name = []

        def __init__(self):
            self._extras = {}
            self._result = None
            self._force_stopped = False

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

        def is_stopped(self):
            return False

        def get_result(self):
            return self._result

        def clear_result(self):
            self._result = None

    async def legacy_tool(_event):
        return "工具结果"

    provider = Provider()
    tool = FunctionTool(
        name="legacy_tool",
        description="Returns a fact.",
        parameters={"type": "object", "properties": {}},
        handler=legacy_tool,
        execution_targets={"personal_expression"},
    )
    tools = ToolSet([tool])
    contract = build_persona_expression_output_contract_for_effects([])
    compiled = CompiledOutputContract(
        contract=contract,
        strategy="protocol_tool_call",
        tool_name="persona_expression",
        tool_schema=contract.schema,
    )
    agent = InteractionExpressionAgent()
    agent._prepare_render_result = AsyncMock(
        return_value=RenderResult(
            system_prompt="persona",
            request_prompt="initial prompt",
            messages=[{"role": "user", "content": "initial context"}],
            output_contract=contract,
            compiled_output_contract=compiled,
            metadata={"persona_effect_specs": []},
        )
    )
    agent._resolve_personal_expression_capabilities = AsyncMock(
        return_value=_persona_capabilities(tools)
    )

    async def call_hook(_event, hook_type, *args, **_kwargs):
        if hook_type.name == "OnLLMRequestEvent":
            args[0].prompt = ""
            args[0].contexts.append(
                {"role": "system", "content": "plugin context"}
            )
        return False

    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.call_event_hook",
        call_hook,
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.resolve_interaction_chat_provider",
        AsyncMock(return_value=(provider, "persona")),
    )

    await agent.generate_expression(
        Event(),
        SimpleNamespace(
            get_provider_by_id=lambda _provider_id: provider,
            get_config=lambda **_kwargs: {},
        ),
        InteractionAgentConfig(expression_provider_id="persona"),
        PersonaExpressionRequest(),
    )

    assert len(provider.calls) == 2
    assert "plugin context" in _provider_context_text(provider.calls[1])
    assert "工具结果" in _provider_context_text(provider.calls[1])


@pytest.mark.asyncio
async def test_persona_expression_fallback_does_not_repeat_request_hooks(monkeypatch):
    class Provider:
        def __init__(self, provider_id, *, fails=False):
            self.provider_config = {"id": provider_id, "type": "test"}
            self.fails = fails
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            if self.fails:
                raise RuntimeError("primary unavailable")
            return LLMResponse(
                role="assistant",
                completion_text="",
                tools_call_name=["persona_expression"],
                tools_call_args=[{"spoken_reply": "由回退模型完成", "effect_calls": []}],
            )

    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"
        plugins_name = []

        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

        def is_stopped(self):
            return False

    primary = Provider("primary", fails=True)
    fallback = Provider("fallback")
    plugin_context = type(
        "PluginContext",
        (),
        {
            "get_provider_by_id": lambda self, provider_id: primary,
            "get_config": lambda self, **kwargs: {},
        },
    )()
    contract = build_persona_expression_output_contract_for_effects([])
    compiled = CompiledOutputContract(
        contract=contract,
        strategy="protocol_tool_call",
        tool_name="persona_expression",
        tool_schema=contract.schema,
    )
    agent = InteractionExpressionAgent()
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.Provider",
        Provider,
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.resolve_fallback_chat_providers",
        lambda *args: [fallback],
    )
    agent._prepare_render_result = AsyncMock(
        return_value=RenderResult(
            system_prompt="persona",
            request_prompt="reply naturally",
            messages=[{"role": "user", "content": "hello"}],
            output_contract=contract,
            compiled_output_contract=compiled,
            metadata={"persona_effect_specs": []},
        )
    )
    hooks = []
    response_provider_ids = []

    async def call_hook(event, hook_type, *args, **kwargs):
        if hook_type.name == "OnLLMRequestEvent":
            args[0].system_prompt += "\nplugin context"
        if hook_type.name == "OnLLMResponseEvent":
            response_provider_ids.append(
                event.get_extra("provider_request").provider.provider_config["id"]
            )
        hooks.append((hook_type.name, kwargs["execution_surface"]))
        return False

    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.call_event_hook",
        call_hook,
    )

    result = await agent.generate_expression(
        Event(),
        plugin_context,
        InteractionAgentConfig(expression_provider_id="primary"),
        PersonaExpressionRequest(),
    )

    assert result.spoken_reply == "由回退模型完成"
    fallback_context = _provider_context_text(fallback.calls[0])
    assert "persona" in fallback_context
    assert "plugin context" in fallback_context
    assert response_provider_ids == ["fallback"]
    assert hooks == [
        ("OnWaitingLLMRequestEvent", "personal_expression"),
        ("OnLLMRequestEvent", "personal_expression"),
        ("OnAgentBeginEvent", "personal_expression"),
        ("OnLLMResponseEvent", "personal_expression"),
        ("OnAgentDoneEvent", "personal_expression"),
        ("OnPersonaExpressionResultEvent", "personal_expression"),
    ]
