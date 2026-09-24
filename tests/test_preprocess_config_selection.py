from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.interaction.turn_state import (
    set_interaction_turn_configuration_selection,
)
from astrbot.core.pipeline.preprocess_stage.stage import PreProcessStage


@pytest.mark.asyncio
async def test_preprocess_uses_frozen_turn_configuration_for_platform_pre_ack(
    monkeypatch,
):
    monkeypatch.setattr(
        "astrbot.core.pipeline.preprocess_stage.stage."
        "is_interaction_turn_inbound_media_materialized",
        lambda _event: True,
    )
    extras: dict[str, object] = {}
    event = SimpleNamespace(
        get_platform_name=lambda: "telegram",
        is_at_or_wake_command=True,
        react=AsyncMock(),
        get_extra=lambda key, default=None: extras.get(key, default),
        set_extra=lambda key, value: extras.__setitem__(key, value),
    )
    default_config = {"platform_specific": {}}
    selected_config = {
        "platform_specific": {
            "telegram": {
                "pre_ack_emoji": {"enable": True, "emojis": ["ok"]},
            }
        }
    }
    stage = PreProcessStage()
    await stage.initialize(
        SimpleNamespace(
            astrbot_config=default_config,
            plugin_manager=SimpleNamespace(),
        )
    )
    set_interaction_turn_configuration_selection(
        event,
        config_id="selected",
        runtime_config=selected_config,
        adapter_binding_id="selected-binding",
        provider_references={},
    )

    await stage.process(event)

    event.react.assert_awaited_once_with("ok")
