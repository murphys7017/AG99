import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot.core.interaction.decision_agent import (
    InteractionDecisionAgent,
    InteractionDecisionError,
    _build_decision_build_config,
    _maybe_bypass_protocol_command,
    build_interaction_decision_contexts,
    build_interaction_decision_json_contract,
    validate_interaction_decision,
)
from astrbot.core.interaction.memory_store import InteractionMemoryStore
from astrbot.core.interaction.turn_state import (
    InteractionContextMaterial,
    InteractionTurnState,
)
from astrbot.core.interaction.types import (
    InteractionAgentConfig,
    InteractionDecision,
    RouteMode,
)
from astrbot.core.prompt.context_types import ContextPack
from astrbot.core.prompt.extensions import PromptExtension
from astrbot.core.provider.entities import LLMResponse


def test_validate_interaction_decision_fail_fast_on_low_confidence():
    config = InteractionAgentConfig(decision_confidence_threshold=0.6)
    decision = InteractionDecision(
        route_mode=RouteMode.SELF_REPLY,
        should_emit_immediate_reply=True,
        immediate_spoken_reply="嗯，我看看",
        confidence=0.2,
        reason="unsure",
    )
    with pytest.raises(InteractionDecisionError, match="low confidence"):
        validate_interaction_decision(decision, config)


def test_validate_interaction_decision_ignores_observable_protect_on_low_confidence():
    config = InteractionAgentConfig(
        decision_confidence_threshold=0.6,
    )
    decision = InteractionDecision(
        route_mode=RouteMode.SELF_REPLY,
        should_emit_immediate_reply=True,
        immediate_spoken_reply="嗯，我看看",
        confidence=0.2,
        reason="unsure",
    )
    with pytest.raises(InteractionDecisionError, match="low confidence"):
        validate_interaction_decision(decision, config)


def test_validate_interaction_decision_truncates_spoken_reply():
    config = InteractionAgentConfig(decision_confidence_threshold=0.1)
    decision = InteractionDecision(
        route_mode=RouteMode.SELF_REPLY,
        should_emit_immediate_reply=True,
        immediate_spoken_reply="这是一段非常非常非常非常非常非常非常非常非常非常非常非常非常长的回复文本，需要被截断一下",
        confidence=0.9,
        reason="ok",
    )
    validated = validate_interaction_decision(decision, config)
    assert validated.immediate_spoken_reply is not None
    assert len(validated.immediate_spoken_reply) <= 60


def test_validate_interaction_decision_rejects_self_reply_without_reply():
    config = InteractionAgentConfig(decision_confidence_threshold=0.1)
    decision = InteractionDecision(
        route_mode=RouteMode.SELF_REPLY,
        should_emit_immediate_reply=False,
        immediate_spoken_reply=None,
        confidence=0.9,
        reason="invalid",
    )

    with pytest.raises(InteractionDecisionError, match="self_reply decision"):
        validate_interaction_decision(decision, config)


def test_validate_interaction_decision_rejects_hybrid_without_reply():
    config = InteractionAgentConfig(decision_confidence_threshold=0.1)
    decision = InteractionDecision(
        route_mode=RouteMode.HYBRID,
        should_emit_immediate_reply=False,
        immediate_spoken_reply=None,
        confidence=0.9,
        reason="invalid",
    )

    with pytest.raises(InteractionDecisionError, match="hybrid decision"):
        validate_interaction_decision(decision, config)


def test_build_interaction_decision_contexts_strips_internal_runtime_fields():
    rendered_messages = [
        {"role": "user", "content": "history"},
        {"role": "user", "content": "<context />", "_no_save": True},
    ]

    contexts = build_interaction_decision_contexts(rendered_messages)

    assert contexts == [
        {"role": "user", "content": "history"},
        {"role": "user", "content": "<context />"},
    ]
    assert rendered_messages[1]["_no_save"] is True


def test_protocol_command_bypass_delegates_without_fallback_or_reply():
    class PluginContext:
        def get_config(self, umo=None):
            assert umo == "umo-1"
            return {"wake_prefix": ["/"]}

    class Event:
        unified_msg_origin = "umo-1"
        message_str = "/sid"
        session_id = "session-1"

        def get_platform_id(self):
            return "webchat"

    decision = _maybe_bypass_protocol_command(Event(), PluginContext())

    assert decision is not None
    assert decision.route_mode == RouteMode.DELEGATE_TO_CORE
    assert decision.should_emit_immediate_reply is False
    assert decision.reason == "protocol command bypass"


def test_protocol_command_bypass_uses_configured_wake_prefix():
    class PluginContext:
        def get_config(self, umo=None):
            return {"wake_prefix": ["!"]}

    class Event:
        unified_msg_origin = "umo-1"
        message_str = "!sid"
        session_id = "session-1"

        def get_platform_id(self):
            return "webchat"

    assert _maybe_bypass_protocol_command(Event(), PluginContext()) is not None


def test_protocol_command_bypass_does_not_hardcode_slash():
    class PluginContext:
        def get_config(self, umo=None):
            return {"wake_prefix": ["!"]}

    class Event:
        unified_msg_origin = "umo-1"
        message_str = "/sid"
        session_id = "session-1"

        def get_platform_id(self):
            return "webchat"

    assert _maybe_bypass_protocol_command(Event(), PluginContext()) is None


def test_build_decision_build_config_exposes_provider_wake_prefix():
    class PluginContext:
        def get_config(self, umo=None):
            assert umo == "umo-1"
            return {
                "provider_settings": {
                    "prompt_prefix": "{{prompt}}",
                    "max_quoted_fallback_images": 3,
                },
                "timezone": "Asia/Shanghai",
                "wake_prefix": ["/", "Alice"],
                "file_extract_enabled": True,
                "file_extract_prov": "moonshotai",
                "file_extract_msh_api_key": "key-1",
            }

    class Event:
        unified_msg_origin = "umo-1"

    config = _build_decision_build_config(PluginContext(), Event())

    assert config.provider_settings == {
        "prompt_prefix": "{{prompt}}",
        "max_quoted_fallback_images": 3,
    }
    assert config.timezone == "Asia/Shanghai"
    assert config.provider_wake_prefix == "/"
    assert config.file_extract_enabled is True
    assert config.file_extract_prov == "moonshotai"
    assert config.file_extract_msh_api_key == "key-1"
    assert config.max_quoted_fallback_images == 3
    assert config.prompt_pipeline_strict_mode is True


class DummyEvent:
    def __init__(self) -> None:
        self._extras: dict[str, object] = {}
        self.message_str = "hello"
        self.session_id = "webchat!user!session123"
        self.unified_msg_origin = "webchat:FriendMessage:webchat!user!session123"

    def get_platform_id(self) -> str:
        return "webchat"

    def get_extra(self, key: str, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key: str, value) -> None:
        self._extras[key] = value


class DummyConversationManager:
    async def get_curr_conversation_id(self, unified_msg_origin):
        assert unified_msg_origin == "webchat:FriendMessage:webchat!user!session123"
        return "conversation-1"

    async def get_conversation(self, unified_msg_origin, conversation_id):
        assert unified_msg_origin == "webchat:FriendMessage:webchat!user!session123"
        assert conversation_id == "conversation-1"
        conversation = MagicMock()
        conversation.cid = conversation_id
        conversation.history = json.dumps(
            [
                {"role": "user", "content": "before user"},
                {"role": "assistant", "content": "before assistant"},
            ],
            ensure_ascii=False,
        )
        return conversation


class MiddlewarePromptContributor:
    plugin_id = "middleware.motion"

    async def collect(self, event, plugin_context, view):
        return PromptExtension(
            plugin_id=self.plugin_id,
            mount="capability",
            title="Motion Contract",
            value={"motion": "available"},
            order=10,
            meta={"scope": "static", "node_type": "motion_contract"},
        )


class CorePromptExtensionCollector:
    plugin_id = "core.only"

    async def collect(self, event, plugin_context, config, provider_request=None):
        return [
            PromptExtension(
                plugin_id=self.plugin_id,
                mount="system",
                title="Core Only",
                value={"must_not": "appear"},
            )
        ]


@pytest.mark.asyncio
async def test_decision_agent_reuses_turn_state_context_material():
    event = DummyEvent()
    cached_pack = ContextPack()
    turn_state = InteractionTurnState(
        turn_id="turn-1",
        context_material=InteractionContextMaterial(
            prompt_context_pack=cached_pack,
            persona_payload={"persona_id": "alice", "prompt": "persona"},
            memory_payload={"recent_turns": [{"user": "u1", "assistant": "a1"}]},
            recent_messages=[
                {
                    "source": "interaction_memory",
                    "user_message": {"role": "user", "content": "u1"},
                    "assistant_message": {"role": "assistant", "content": "a1"},
                }
            ],
            input_payload={"text": "hello"},
            capability_payload={"tools_available": True, "tool_count": 3},
            decision_context={"stale": True},
        ),
    )
    event.set_extra("_interaction_turn_state", turn_state)

    plugin_context = MagicMock()
    plugin_context.get_config.return_value = {}
    plugin_context.get_provider_by_id.return_value = object()
    plugin_context.list_interaction_prompt_contributors.return_value = [
        MiddlewarePromptContributor()
    ]
    config = InteractionAgentConfig(
        decision_provider_id="provider-1",
        decision_confidence_threshold=0.1,
        memory_window_size=1,
    )
    agent = InteractionDecisionAgent(InteractionMemoryStore())

    with (
        patch(
            "astrbot.core.interaction.decision_agent.Provider",
            new=object,
        ),
        patch(
            "astrbot.core.interaction.decision_agent.build_interaction_context_pack",
            new=AsyncMock(side_effect=AssertionError("should not rebuild context")),
        ),
        patch(
            "astrbot.core.interaction.decision_agent.call_decision_model",
            new=AsyncMock(
                return_value=LLMResponse(
                    role="assistant",
                    completion_text=(
                        '{"route_mode":"self_reply","should_emit_immediate_reply":true,'
                        '"immediate_spoken_reply":"嗯。","confidence":0.9,"reason":"ok"}'
                    ),
                )
            ),
        ),
    ):
        decision = await agent.decide(event, plugin_context, config)

    assert decision.route_mode == RouteMode.SELF_REPLY
    assert event.get_extra("_interaction_persona_id") == "alice"
    assert event.get_extra("_interaction_prompt_context_pack") is cached_pack
    decision_context = event.get_extra("_interaction_decision_context")
    assert decision_context["persona"]["persona_id"] == "alice"
    assert len(decision_context["recent_messages"]) == 1
    assert turn_state.decision is decision
    assert turn_state.prompt_build_config is not None
    assert turn_state.context_material is not None
    assert turn_state.context_material.decision_context == decision_context
    assert turn_state.context_material.prompt_extensions_collected is True
    render_result = event.get_extra("_interaction_prompt_render_result")
    assert render_result is not None
    assert "Motion Contract" in render_result.system_prompt


@pytest.mark.asyncio
async def test_decision_agent_renders_middleware_prompt_extensions_without_core_extensions():
    event = DummyEvent()
    event.set_extra("_turn_id", "turn-1")
    plugin_context = MagicMock()
    plugin_context.get_config.return_value = {}
    plugin_context.get_provider_by_id.return_value = object()
    plugin_context.get_llm_tool_manager.return_value.func_list = []
    plugin_context.kb_manager = None
    plugin_context.subagent_orchestrator = None
    plugin_context.conversation_manager = DummyConversationManager()
    plugin_context.list_interaction_prompt_contributors.return_value = [
        MiddlewarePromptContributor()
    ]
    plugin_context.list_prompt_extension_collectors.return_value = [
        CorePromptExtensionCollector()
    ]
    config = InteractionAgentConfig(
        decision_provider_id="provider-1",
        decision_confidence_threshold=0.1,
    )
    agent = InteractionDecisionAgent(InteractionMemoryStore())

    captured: dict[str, object] = {}

    async def _capture_decision_call(*args, **kwargs):
        captured["prompt"] = kwargs["prompt"]
        captured["system_prompt"] = kwargs["system_prompt"]
        captured["contexts"] = kwargs["contexts"]
        return LLMResponse(
            role="assistant",
            completion_text=(
                '{"route_mode":"self_reply","should_emit_immediate_reply":true,'
                '"immediate_spoken_reply":"嗯。","confidence":0.9,"reason":"ok"}'
            ),
        )

    with (
        patch("astrbot.core.interaction.decision_agent.Provider", new=object),
        patch(
            "astrbot.core.interaction.decision_agent.call_decision_model",
            new=AsyncMock(side_effect=_capture_decision_call),
        ),
    ):
        decision = await agent.decide(event, plugin_context, config)

    assert decision.route_mode == RouteMode.SELF_REPLY
    assert "Interaction middleware decision policy" in captured["system_prompt"]
    assert "Interaction output JSON contract" in captured["system_prompt"]
    assert "route_mode" in captured["system_prompt"]
    assert "<route_mode>" not in captured["system_prompt"]
    assert "不能输出 Markdown、XML、HTML 或任何标签格式" in captured["system_prompt"]
    assert "Core capabilities" not in captured["system_prompt"]
    assert "tools_available" not in captured["system_prompt"]
    assert "Motion Contract" in captured["system_prompt"]
    assert "motion" in captured["system_prompt"]
    assert "Core Only" not in captured["system_prompt"]
    assert "Interaction session" not in captured["system_prompt"]
    assert captured["prompt"] == "请根据以上上下文做一次完整决策，并只返回 JSON。"
    render_result = event.get_extra("_interaction_prompt_render_result")
    assert render_result.metadata["engine"] == "PromptRenderEngine"
    assert "extension.system" in render_result.metadata["rendered_slots"]
    assert "extension.capability" in render_result.metadata["rendered_slots"]
    assert "extension.context" in render_result.metadata["rendered_slots"]

    pack = event.get_extra("_interaction_prompt_context_pack")
    system_slot = pack.get_slot("extension.system")
    capability_slot = pack.get_slot("extension.capability")
    context_slot = pack.get_slot("extension.context")
    assert system_slot is not None
    assert capability_slot is not None
    assert context_slot is not None
    assert [item["title"] for item in system_slot.value["items"]] == [
        "Interaction middleware decision policy",
        "Interaction output JSON contract",
    ]
    assert system_slot.value["items"][1]["value_kind"] == "text"
    assert system_slot.value["items"][1]["value"] == (
        build_interaction_decision_json_contract()
    )
    assert [item["title"] for item in capability_slot.value["items"]] == [
        "Motion Contract"
    ]
    context_items_by_title = {
        item["title"]: item for item in context_slot.value["items"]
    }
    assert context_items_by_title["Core capabilities"]["meta"] == {
        "scope": "dynamic",
        "node_type": "interaction_core_capabilities",
    }
    assert context_items_by_title["Interaction session"]["meta"] == {
        "scope": "dynamic",
        "node_type": "interaction_session",
    }

    rendered_messages = captured["contexts"]
    assert rendered_messages == build_interaction_decision_contexts(
        render_result.messages
    )
    assert [message["role"] for message in rendered_messages] == [
        "user",
        "assistant",
        "user",
        "user",
    ]
    assert rendered_messages[0]["content"] == "before user"
    assert rendered_messages[1]["content"] == "before assistant"
    assert "_no_save" not in rendered_messages[2]
    assert "Core capabilities" in rendered_messages[2]["content"]
    assert "tools_available" in rendered_messages[2]["content"]
    assert "Interaction session" in rendered_messages[2]["content"]
    assert "webchat!user!session123" in rendered_messages[2]["content"]
    assert rendered_messages[-1]["role"] == "user"
    user_content = rendered_messages[-1]["content"]
    if isinstance(user_content, list):
        rendered_user_text = "\n".join(
            part["text"] for part in user_content if part.get("type") == "text"
        )
    else:
        rendered_user_text = str(user_content)
    assert "Core capabilities" not in rendered_user_text
    assert "Interaction session" not in rendered_user_text
    assert "hello" in rendered_user_text
