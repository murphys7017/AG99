from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot.core.interaction.decision_agent import (
    InteractionDecisionAgent,
    InteractionDecisionError,
    _build_decision_build_config,
    _maybe_bypass_protocol_command,
    build_fallback_decision,
    validate_interaction_decision,
)
from astrbot.core.interaction.memory_store import InteractionMemoryStore
from astrbot.core.interaction.turn_state import (
    InteractionContextMaterial,
    InteractionTurnState,
)
from astrbot.core.interaction.types import (
    FallbackPolicy,
    InteractionAgentConfig,
    InteractionDecision,
    RouteMode,
)
from astrbot.core.prompt.context_types import ContextPack
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


def test_validate_interaction_decision_observable_protects_on_low_confidence():
    config = InteractionAgentConfig(
        decision_confidence_threshold=0.6,
        fallback_policy=FallbackPolicy.OBSERVABLE_PROTECT,
    )
    decision = InteractionDecision(
        route_mode=RouteMode.SELF_REPLY,
        should_emit_immediate_reply=True,
        immediate_spoken_reply="嗯，我看看",
        confidence=0.2,
        reason="unsure",
    )
    validated = validate_interaction_decision(decision, config)
    assert validated.route_mode == RouteMode.DELEGATE_TO_CORE
    assert validated.should_emit_immediate_reply is False
    assert validated.is_fallback is True
    assert validated.fallback_reason == "low confidence"


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


def test_build_fallback_decision_is_marked_as_fallback():
    decision = build_fallback_decision("provider unavailable")

    assert decision.route_mode == RouteMode.DELEGATE_TO_CORE
    assert decision.is_fallback is True
    assert decision.fallback_reason == "provider unavailable"
    assert decision.should_emit_immediate_reply is False


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
    assert decision.is_fallback is False
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
            "astrbot.core.interaction.decision_agent.collect_interaction_prompt_contributions",
            new=AsyncMock(return_value=[]),
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
