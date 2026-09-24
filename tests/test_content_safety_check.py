from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import astrbot.core.interaction  # noqa: F401
from astrbot.core.interaction.turn_state import (
    set_interaction_turn_configuration_selection,
)
from astrbot.core.message.components import Plain, Reply
from astrbot.core.pipeline.content_safety_check.stage import ContentSafetyCheckStage


def _content_safety_config(*, keywords: list[str] | None = None) -> dict:
    return {
        "content_safety": {
            "internal_keywords": {
                "enable": bool(keywords),
                "extra_keywords": keywords or [],
            },
            "baidu_aip": {"enable": False},
        }
    }


async def _stage_for_config(config: dict) -> ContentSafetyCheckStage:
    stage = ContentSafetyCheckStage()
    await stage.initialize(SimpleNamespace(astrbot_config=config))
    return stage


@pytest.mark.asyncio
async def test_content_safety_checks_combined_message_text_once(monkeypatch):
    event = SimpleNamespace(
        is_at_or_wake_command=False,
        get_message_str=lambda: "current message",
        get_messages=lambda: [Reply(id="1", message_str="quoted message")],
        stop_event=Mock(),
    )
    strategy_selector = SimpleNamespace(check=Mock(return_value=(True, "")))
    monkeypatch.setattr(
        "astrbot.core.pipeline.content_safety_check.stage.StrategySelector",
        lambda _config: strategy_selector,
    )
    stage = await _stage_for_config(_content_safety_config())

    async for _ in stage.process(event):
        pass

    strategy_selector.check.assert_called_once_with(
        "current message\nquoted message"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "keyword", "check_text", "expected_stopped"),
    [
        (Reply(id="1", message_str="引用中包含淀粉砖"), "淀粉砖", None, True),
        (
            Reply(id="1", message_str="", chain=[Plain("引用中包含淀粉砖")]),
            "淀粉砖",
            None,
            True,
        ),
        (
            Reply(id="1", message_str="引用中包含淀粉砖"),
            "^你说呢\n引用中包含淀粉砖$",
            None,
            True,
        ),
        (Reply(id="1", message_str="引用中包含淀粉砖"), "淀粉砖", "", False),
    ],
)
async def test_content_safety_checks_quoted_text_only_for_inbound_messages(
    reply: Reply,
    keyword: str,
    check_text: str | None,
    expected_stopped: bool,
):
    stopped = False

    def stop_event() -> None:
        nonlocal stopped
        stopped = True

    event = SimpleNamespace(
        is_at_or_wake_command=False,
        get_message_str=lambda: "你说呢",
        get_messages=lambda: [reply],
        stop_event=stop_event,
    )
    stage = await _stage_for_config(_content_safety_config(keywords=[keyword]))

    async for _ in stage.process(event, check_text=check_text):
        pass

    assert stopped is expected_stopped


@pytest.mark.asyncio
async def test_content_safety_uses_frozen_turn_configuration_not_pipeline_default():
    extras: dict[str, object] = {}
    stopped = False

    def stop_event() -> None:
        nonlocal stopped
        stopped = True

    event = SimpleNamespace(
        is_at_or_wake_command=False,
        get_message_str=lambda: "blocked by selected profile",
        get_messages=lambda: [],
        get_extra=lambda key, default=None: extras.get(key, default),
        set_extra=lambda key, value: extras.__setitem__(key, value),
        stop_event=stop_event,
    )
    stage = await _stage_for_config(_content_safety_config())
    selected_config = _content_safety_config(keywords=["selected profile"])
    set_interaction_turn_configuration_selection(
        event,
        config_id="selected",
        runtime_config=selected_config,
        adapter_binding_id="selected-binding",
        provider_references={},
    )

    async for _ in stage.process(event):
        pass

    assert stopped is True
