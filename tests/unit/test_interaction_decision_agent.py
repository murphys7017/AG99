from astrbot.core.interaction.decision_agent import (
    _build_decision_build_config,
    _maybe_bypass_protocol_command,
    build_fallback_decision,
    validate_interaction_decision,
)
from astrbot.core.interaction.types import (
    InteractionAgentConfig,
    InteractionDecision,
    RouteMode,
)


def test_validate_interaction_decision_falls_back_on_low_confidence():
    config = InteractionAgentConfig(decision_confidence_threshold=0.6)
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
