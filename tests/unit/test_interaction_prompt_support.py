from types import SimpleNamespace

from astrbot.core.interaction.prompt_support import (
    build_interaction_prompt_build_config,
)
from astrbot.core.interaction.turn_state import set_interaction_turn_runtime_config


def test_prompt_config_uses_detached_admitted_settings():
    extras = {}
    event = SimpleNamespace(
        unified_msg_origin="test:FriendMessage:user",
        get_extra=lambda key, default=None: extras.get(key, default),
        set_extra=lambda key, value: extras.__setitem__(key, value),
    )
    live = {
        "timezone": "Asia/Shanghai",
        "provider_settings": {
            "web_search": True,
            "file_extract": {"enable": True},
        },
    }
    context = SimpleNamespace(get_config=lambda **kwargs: live)
    fallback = build_interaction_prompt_build_config(context, event)
    assert fallback.file_extract_enabled is True
    set_interaction_turn_runtime_config(event, live)
    live["provider_settings"]["file_extract"]["enable"] = False
    live["provider_settings"]["web_search"] = False
    admitted = build_interaction_prompt_build_config(context, event)
    assert admitted.file_extract_enabled is True
    assert admitted.provider_settings["web_search"] is True
    admitted.provider_settings["web_search"] = False
    assert build_interaction_prompt_build_config(
        context, event,
    ).provider_settings["web_search"] is True


def test_prompt_config_uses_provider_wake_prefix_suffix():
    event = SimpleNamespace(
        unified_msg_origin="test:FriendMessage:user",
        get_extra=lambda key, default=None: default,
    )
    config = {
        "wake_prefix": ["/", "!"],
        "provider_settings": {"wake_prefix": "/chat"},
    }
    context = SimpleNamespace(get_config=lambda **kwargs: config)

    build_config = build_interaction_prompt_build_config(context, event)

    assert build_config.provider_wake_prefix == "chat"
