import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import (
    MessageChain,
    MessageEventResult,
    ResultContentType,
)
from astrbot.core.output_lifecycle import PreOutputProcessor, TurnDeliveryCoordinator
from astrbot.core.pipeline.result_decorate.stage import ResultDecorateStage
from astrbot.core.postprocess.types import PostProcessTrigger
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import star_handlers_registry


class OutputEvent:
    def __init__(self) -> None:
        self._extras = {}
        self._result = None
        self._stopped = False
        self.plugins_name = ["*"]
        self.is_at_or_wake_command = False
        self.complete_visible_turn = AsyncMock()

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value

    def set_result(self, result):
        self._result = result

    def get_result(self):
        return self._result

    def stop_event(self):
        self._stopped = True

    def is_stopped(self):
        return self._stopped

    def get_platform_id(self):
        return "webchat"


@pytest.mark.asyncio
async def test_pre_output_processor_applies_legacy_decorating_hook_once(monkeypatch):
    event = OutputEvent()
    calls = []

    async def decorate(target_event):
        calls.append("decorate")
        target_event.get_result().chain = [Plain("decorated")]

    module_path = "tests.output_lifecycle_plugin"
    handler = SimpleNamespace(
        handler_module_path=module_path,
        handler_name="decorate",
        handler=decorate,
    )
    monkeypatch.setitem(star_map, module_path, SimpleNamespace(name="test_plugin"))
    monkeypatch.setattr(
        star_handlers_registry,
        "get_handlers_by_event_type",
        Mock(return_value=[handler]),
    )

    prepared = await PreOutputProcessor().prepare_interaction_message(
        event,
        MessageChain([Plain("original")]),
        ResultContentType.LLM_RESULT,
    )

    assert prepared is not None
    assert prepared.get_plain_text() == "decorated"
    assert calls == ["decorate"]
    assert "_interaction_pipeline_pre_output_callback" not in event._extras


@pytest.mark.asyncio
async def test_pipeline_and_interaction_share_response_safety():
    safety_config = {
        "content_safety": {
            "also_use_in_response": True,
            "internal_keywords": {
                "enable": True,
                "extra_keywords": ["blocked reply"],
            },
            "baidu_aip": {"enable": False},
        }
    }
    processor = PreOutputProcessor()

    pipeline_event = OutputEvent()
    pipeline_event.set_extra("_astrbot_config", safety_config)
    pipeline_event.set_extra("_astrbot_config_id", "default")
    pipeline_event.set_result(
        MessageEventResult(
            chain=[Plain("blocked reply")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )
    stage = object.__new__(ResultDecorateStage)
    stage.pre_output_processor = processor

    await stage.process(pipeline_event)

    interaction_event = OutputEvent()
    interaction_event.set_extra("_astrbot_config", safety_config)
    interaction_event.set_extra("_astrbot_config_id", "default")
    prepared = await processor.prepare_interaction_message(
        interaction_event,
        MessageChain([Plain("blocked reply")]),
        ResultContentType.LLM_RESULT,
    )

    assert pipeline_event.is_stopped()
    assert interaction_event.is_stopped()
    assert prepared is None


@pytest.mark.asyncio
async def test_delivery_coordinator_stopped_hook_only_cancels(monkeypatch):
    event = OutputEvent()
    outcomes = []
    monkeypatch.setattr(
        "astrbot.core.output_lifecycle.call_event_hook",
        AsyncMock(return_value=True),
    )

    coordinator = TurnDeliveryCoordinator()
    coordinator.schedule_after_message_sent_postprocess = Mock(
        side_effect=lambda *_args, **_kwargs: outcomes.append("postprocess")
    )

    async def complete(_event):
        outcomes.append("complete")

    async def cancel(_event, *, reason):
        outcomes.append(f"cancel:{reason}")

    async def flush(_event):
        outcomes.append("flush")

    completed = await coordinator.complete_visible_delivery(
        event,
        complete_visible_turn=complete,
        cancel_deferred_turn_finalization=cancel,
        flush_deferred_turn_finalization=flush,
    )

    assert completed is False
    assert outcomes == ["cancel:after_message_sent_hook_stopped"]


@pytest.mark.asyncio
async def test_delivery_coordinator_schedules_turn_postprocess_only_for_normal_turns(
    monkeypatch,
):
    event = OutputEvent()
    triggers = []
    tasks = []

    async def dispatch_postprocess(**kwargs):
        triggers.append(kwargs["trigger"])

    class Manager:
        def schedule(self, coroutine, *, name):
            task = asyncio.create_task(coroutine, name=name)
            tasks.append(task)
            return task

    monkeypatch.setattr(
        "astrbot.core.output_lifecycle.dispatch_postprocess",
        dispatch_postprocess,
    )
    monkeypatch.setattr(
        "astrbot.core.output_lifecycle.get_postprocess_manager",
        lambda: Manager(),
    )

    coordinator = TurnDeliveryCoordinator()
    coordinator.schedule_after_message_sent_postprocess(
        event,
        is_interaction_turn=False,
    )
    await asyncio.gather(*tasks)

    assert triggers == [
        PostProcessTrigger.AFTER_MESSAGE_SENT,
        PostProcessTrigger.AFTER_TURN_COMPLETED,
    ]

    triggers.clear()
    tasks.clear()
    coordinator.schedule_after_message_sent_postprocess(
        event,
        is_interaction_turn=True,
    )
    await asyncio.gather(*tasks)

    assert triggers == [PostProcessTrigger.AFTER_MESSAGE_SENT]
