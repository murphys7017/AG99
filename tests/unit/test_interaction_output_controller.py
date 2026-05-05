import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot.core.interaction.contributors import InteractionResultContribution
from astrbot.core.interaction.output_controller import InteractionOutputController
from astrbot.core.interaction.types import (
    FinalizerMode,
    InteractionAgentConfig,
    InteractionDecision,
)
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import (
    MessageChain,
    MessageEventResult,
    ResultContentType,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.webchat.webchat_event import WebChatMessageEvent


class ConcreteMessageEvent(AstrMessageEvent):
    async def send(self, message):
        await super().send(message)


@pytest.fixture
def webchat_event():
    platform_meta = PlatformMetadata(
        name="webchat",
        description="webchat",
        id="webchat",
    )
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = "webchat"
    message.session_id = "webchat!user!session123"
    message.message_id = "msg123"
    message.sender = MessageMember(user_id="user123", nickname="TestUser")
    message.message = [Plain(text="帮我查一下天气")]
    message.message_str = "帮我查一下天气"
    event = WebChatMessageEvent(
        message_str="帮我查一下天气",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="webchat!user!session123",
    )
    event.set_extra("_turn_id", "turn-1")
    event.set_extra("_interaction_decision", InteractionDecision(reason="test"))
    return event


@pytest.fixture
def generic_event():
    platform_meta = PlatformMetadata(
        name="generic",
        description="generic",
        id="generic",
    )
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = "generic"
    message.session_id = "generic-session"
    message.message_id = "generic-msg"
    message.sender = MessageMember(user_id="user123", nickname="TestUser")
    message.message = [Plain(text="hello")]
    message.message_str = "hello"
    event = ConcreteMessageEvent(
        message_str="hello",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="generic-session",
    )
    event.set_extra("_turn_id", "turn-generic")
    return event


class ResultContributor:
    plugin_id = "result_plugin"
    priority = 10

    async def collect(self, event, plugin_context, result_view):
        assert result_view.turn_id == "turn-1"
        assert result_view.core_result == "dry result"
        return InteractionResultContribution(
            plugin_id=self.plugin_id,
            platform_extras={"adapter_object": {"ok": True}},
            client_objects=[{"kind": "card"}],
            final_text_override="wrapped result",
            metadata={"source": "unit"},
            priority=self.priority,
        )


class MutatingResultContributor:
    plugin_id = "mutating_plugin"

    async def collect(self, event, plugin_context, result_view):
        with pytest.raises(TypeError):
            result_view.metadata["bad"] = True
        return None


class FailingResultContributor:
    plugin_id = "failing_plugin"

    async def collect(self, event, plugin_context, result_view):
        raise RuntimeError("contributor broken")


class StreamInterjectionDecider:
    plugin_id = "stream_plugin"

    async def decide(self, event, plugin_context, stream_view):
        assert stream_view["turn_id"] == "turn-1"
        assert stream_view["window_index"] == 1
        assert stream_view["is_final"] is False
        assert stream_view["observed_text"] == "hello"
        assert stream_view["total_text"] == "hello"
        return {
            "should_interject": True,
            "reply": "嗯，我听着。",
            "reason": "unit",
        }


class FinalStreamInterjectionDecider:
    plugin_id = "final_stream_plugin"

    def __init__(self):
        self.views = []

    async def decide(self, event, plugin_context, stream_view):
        self.views.append(dict(stream_view))
        return {
            "should_interject": True,
            "reply": "收到了。",
            "reason": "final_window",
        }


@pytest.mark.asyncio
async def test_capture_message_chain_collects_result_contributors(webchat_event):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.list_interaction_result_contributors.return_value = [
        ResultContributor()
    ]
    memory_store = MagicMock()
    memory_store.load_interaction_memory = AsyncMock(
        return_value=MagicMock(
            recent_topics=[],
            last_impression_summary="",
        )
    )
    memory_store.save_interaction_memory = AsyncMock()
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
        memory_store=memory_store,
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        webchat_event.set_result(
            MessageEventResult(
                chain=[Plain("dry result")],
                result_content_type=ResultContentType.LLM_RESULT,
            )
        )
        await controller.capture_message_chain(
            MessageChain([Plain("dry result")]),
            webchat_event,
        )

    payload = queue.get_nowait()
    assert payload["data"] == "wrapped result"
    assert payload["platform_extras"]["turn_id"] == "turn-1"
    assert payload["platform_extras"]["adapter_object"] == {"ok": True}
    assert payload["platform_extras"]["client_objects"] == [{"kind": "card"}]
    assert queue.empty()
    assert webchat_event.get_extra("_visible_turn_completion_sent") is None
    memory_store.save_interaction_memory.assert_awaited_once()


@pytest.mark.asyncio
async def test_immediate_reply_skips_final_result_contributors(webchat_event):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.list_interaction_result_contributors.return_value = [
        ResultContributor()
    ]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    decision = InteractionDecision(
        should_emit_immediate_reply=True,
        immediate_spoken_reply="嗯，我来看看。",
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.emit_immediate_spoken_reply(decision, webchat_event)

    payload = queue.get_nowait()
    assert payload["data"] == "嗯，我来看看。"
    assert payload["platform_extras"]["turn_id"] == "turn-1"
    assert queue.empty()
    plugin_context.list_interaction_result_contributors.assert_not_called()


@pytest.mark.asyncio
async def test_immediate_reply_uses_generic_event_send_for_non_webchat(generic_event):
    controller = InteractionOutputController()
    generic_event.send = AsyncMock()
    decision = InteractionDecision(
        should_emit_immediate_reply=True,
        immediate_spoken_reply="嗯，我在。",
    )

    await controller.emit_immediate_spoken_reply(decision, generic_event)

    generic_event.send.assert_awaited_once()
    message = generic_event.send.await_args.args[0]
    assert message.get_plain_text() == "嗯，我在。"
    assert generic_event.get_extra("_output_controller") is None


@pytest.mark.asyncio
async def test_immediate_reply_does_not_mark_generic_event_as_core_sent(
    generic_event,
):
    controller = InteractionOutputController()
    decision = InteractionDecision(
        should_emit_immediate_reply=True,
        immediate_spoken_reply="嗯，我在。",
    )

    await controller.emit_immediate_spoken_reply(decision, generic_event)

    assert generic_event._has_send_oper is False


@pytest.mark.asyncio
async def test_general_result_is_passthrough_without_final_contributors(webchat_event):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.list_interaction_result_contributors.return_value = [
        ResultContributor()
    ]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("command result")],
            result_content_type=ResultContentType.GENERAL_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("command result")]),
            webchat_event,
        )

    payload = queue.get_nowait()
    assert payload["data"] == "command result"
    assert payload["platform_extras"]["turn_id"] == "turn-1"
    assert queue.empty()
    plugin_context.list_interaction_result_contributors.assert_not_called()


@pytest.mark.asyncio
async def test_core_final_result_is_consumed_only_once_for_segmented_sends(
    webchat_event,
):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.list_interaction_result_contributors.return_value = [
        ResultContributor()
    ]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("dry result")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("dry result")]),
            webchat_event,
        )
        await controller.capture_message_chain(
            MessageChain([Plain("second segment")]),
            webchat_event,
        )

    first_payload = queue.get_nowait()
    second_payload = queue.get_nowait()
    assert first_payload["data"] == "wrapped result"
    assert second_payload["data"] == "second segment"
    assert queue.empty()
    plugin_context.list_interaction_result_contributors.assert_called_once()


@pytest.mark.asyncio
async def test_result_contributor_receives_read_only_view(webchat_event):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.list_interaction_result_contributors.return_value = [
        MutatingResultContributor()
    ]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("dry result")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("dry result")]),
            webchat_event,
        )

    payload = queue.get_nowait()
    assert payload["data"] == "dry result"
    assert queue.empty()


@pytest.mark.asyncio
async def test_result_contributor_failure_is_recorded(webchat_event):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.list_interaction_result_contributors.return_value = [
        FailingResultContributor()
    ]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("dry result")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("dry result")]),
            webchat_event,
        )

    payload = queue.get_nowait()
    assert payload["data"] == "dry result"
    assert queue.empty()
    failures = webchat_event.get_extra("_interaction_result_contributor_failures")
    assert failures == [{"plugin_id": "failing_plugin", "error": "contributor broken"}]


@pytest.mark.asyncio
async def test_outbound_memory_persist_failure_is_recorded(webchat_event):
    queue = asyncio.Queue()
    memory_store = MagicMock()
    memory_store.load_interaction_memory = AsyncMock(
        return_value=MagicMock(
            recent_topics=[],
            last_impression_summary="",
        )
    )
    memory_store.save_interaction_memory = AsyncMock(
        side_effect=RuntimeError("disk full")
    )
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
        memory_store=memory_store,
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("dry result")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("dry result")]),
            webchat_event,
        )

    payload = queue.get_nowait()
    assert payload["data"] == "dry result"
    assert queue.empty()
    assert webchat_event.get_extra("_interaction_memory_persist_failed") is True
    assert (
        webchat_event.get_extra("_interaction_memory_persist_failure_reason")
        == "disk full"
    )


@pytest.mark.asyncio
async def test_force_finalizer_failure_does_not_send_raw_core_result(webchat_event):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.get_provider_by_id.return_value = None
    plugin_context.list_interaction_result_contributors.return_value = []
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.FORCE,
            finalizer_provider_id="missing",
        ),
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("raw core result")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("raw core result")]),
            webchat_event,
        )

    payload = queue.get_nowait()
    assert payload["data"] == "最终回复整理失败，请查看日志。"
    assert queue.empty()
    assert webchat_event.get_extra("_interaction_finalizer_failed") is True
    assert (
        webchat_event.get_extra("_interaction_finalizer_failure_reason")
        == "provider_unavailable"
    )


@pytest.mark.asyncio
async def test_capture_streaming_observes_core_chunks_without_interjection(
    webchat_event,
):
    queue = asyncio.Queue()
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_observation_min_chars=5,
            stream_interjection_enabled=False,
        ),
    )

    async def generator():
        yield MessageChain([Plain("hello")])
        yield MessageChain([Plain(" world")])

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_streaming(generator(), webchat_event)

    payloads = []
    while not queue.empty():
        payloads.append(queue.get_nowait())

    assert [payload["data"] for payload in payloads] == [
        "hello",
        " world",
        "hello world",
    ]
    assert payloads[-1]["type"] == "complete"
    assert webchat_event.get_extra("_interaction_core_stream_text") == "hello world"
    assert webchat_event.get_extra("_interaction_core_stream_observation_count") == 3
    assert webchat_event.get_extra("_interaction_core_streaming_result_consumed") is True


@pytest.mark.asyncio
async def test_capture_streaming_interjection_is_separate_from_core_stream(
    webchat_event,
):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.list_interaction_stream_deciders.return_value = [
        StreamInterjectionDecider()
    ]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_observation_min_chars=5,
            stream_interjection_enabled=True,
        ),
    )

    async def generator():
        yield MessageChain([Plain("hello")])
        yield MessageChain([Plain(" core")])

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_streaming(generator(), webchat_event)

    payloads = []
    while not queue.empty():
        payloads.append(queue.get_nowait())

    assert [payload["data"] for payload in payloads] == [
        "嗯，我听着。",
        "hello",
        " core",
        "hello core",
    ]
    assert payloads[0]["streaming"] is False
    assert payloads[0]["chain_type"] == "interaction_stream_reply"
    assert payloads[0]["platform_extras"]["interaction_stream_reply"] is True
    assert payloads[-1]["type"] == "complete"
    assert payloads[-1]["data"] == "hello core"


@pytest.mark.asyncio
async def test_capture_streaming_observes_final_short_output(webchat_event):
    queue = asyncio.Queue()
    decider = FinalStreamInterjectionDecider()
    plugin_context = MagicMock()
    plugin_context.list_interaction_stream_deciders.return_value = [decider]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_observation_min_chars=200,
            stream_interjection_enabled=True,
        ),
    )

    async def generator():
        yield MessageChain([Plain("short result")])

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_streaming(generator(), webchat_event)

    payloads = []
    while not queue.empty():
        payloads.append(queue.get_nowait())

    assert len(decider.views) == 1
    assert decider.views[0]["is_final"] is True
    assert decider.views[0]["observed_text"] == "short result"
    assert decider.views[0]["total_text"] == "short result"
    assert [payload["data"] for payload in payloads] == [
        "short result",
        "收到了。",
        "short result",
    ]
    assert payloads[-1]["type"] == "complete"


@pytest.mark.asyncio
async def test_streaming_finish_marker_is_not_sent_after_streaming_delivery(
    webchat_event,
):
    queue = asyncio.Queue()
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_observation_min_chars=20,
            stream_interjection_enabled=False,
        ),
    )

    async def generator():
        yield MessageChain([Plain("stream final")])

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_streaming(generator(), webchat_event)
        webchat_event.set_result(
            MessageEventResult(
                chain=[Plain("stream final")],
                result_content_type=ResultContentType.STREAMING_FINISH,
            )
        )
        await controller.capture_message_chain(
            MessageChain([Plain("stream final")]),
            webchat_event,
        )

    payloads = []
    while not queue.empty():
        payloads.append(queue.get_nowait())

    assert [payload["data"] for payload in payloads] == [
        "stream final",
        "stream final",
    ]
    assert payloads[-1]["type"] == "complete"


@pytest.mark.asyncio
async def test_end_payload_keeps_turn_id(webchat_event):
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    webchat_event.complete_visible_turn = AsyncMock()

    await controller.capture_message_chain(None, webchat_event)

    webchat_event.complete_visible_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_none_uses_event_visible_completion_and_propagates_failure(
    webchat_event,
):
    webchat_event.complete_visible_turn = AsyncMock(
        side_effect=RuntimeError("queue closed")
    )
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )

    with pytest.raises(RuntimeError, match="queue closed"):
        await controller.capture_message_chain(None, webchat_event)

    webchat_event.complete_visible_turn.assert_awaited_once()
