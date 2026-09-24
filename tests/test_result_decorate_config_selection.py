from types import SimpleNamespace

import pytest

from astrbot.core.interaction.turn_state import (
    set_interaction_turn_configuration_selection,
)
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.pipeline.result_decorate.stage import ResultDecorateStage


class _Event:
    def __init__(self) -> None:
        self._extras: dict[str, object] = {}
        self._result = MessageEventResult().message("reply")
        self._stopped = False
        self.plugins_name = ["*"]

    def get_extra(self, key: str, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key: str, value) -> None:
        self._extras[key] = value

    def get_result(self):
        return self._result

    def set_result(self, result) -> None:
        self._result = result

    def stop_event(self) -> None:
        self._stopped = True

    def is_stopped(self) -> bool:
        return self._stopped

    def get_platform_name(self) -> str:
        return "demo"


def _config(*, reply_prefix: str) -> dict:
    return {
        "platform_settings": {
            "reply_prefix": reply_prefix,
            "reply_with_mention": False,
            "reply_with_quote": False,
            "forward_threshold": 999,
            "segmented_reply": {
                "enable": False,
                "words_count_threshold": 0,
                "only_llm_result": False,
                "regex": ".*",
                "content_cleanup_rule": "",
            },
        },
        "provider_settings": {},
        "provider_tts_settings": {"enable": False},
        "t2i": False,
    }


@pytest.mark.asyncio
async def test_result_decorate_uses_frozen_turn_configuration_not_pipeline_default():
    event = _Event()
    default_config = _config(reply_prefix="default: ")
    selected_config = _config(reply_prefix="selected: ")
    stage = ResultDecorateStage()
    await stage.initialize(
        SimpleNamespace(
            astrbot_config=default_config,
            pre_output_processor=None,
            plugin_manager=SimpleNamespace(context=SimpleNamespace()),
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

    assert event.get_result().chain == [Plain("selected: reply")]
