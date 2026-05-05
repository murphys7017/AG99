import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from astrbot.core.message.components import Json, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.webchat.webchat_event import WebChatMessageEvent


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
    message.message = [Plain(text="Hello")]
    message.message_str = "Hello"
    return WebChatMessageEvent(
        message_str="Hello",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="webchat!user!session123",
    )


class CountingAsyncIterable:
    def __init__(self, items: list[MessageChain]) -> None:
        self.items = items
        self.aiter_calls = 0

    def __aiter__(self) -> AsyncIterator[MessageChain]:
        self.aiter_calls += 1

        async def iterator():
            for item in self.items:
                yield item

        return iterator()


class TestWebChatMessageEventSend:
    @pytest.mark.asyncio
    async def test_send_does_not_route_via_output_controller(self, webchat_event):
        queue = asyncio.Queue()
        controller = AsyncMock()
        message = MessageChain([Plain("hello")])
        webchat_event.set_extra("_output_controller", controller)

        with (
            patch(
                "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
                return_value=queue,
            ),
            patch(
                "astrbot.core.platform.astr_message_event.Metric.upload",
                new_callable=AsyncMock,
            ),
        ):
            await webchat_event.send(message)

        controller.capture_message_chain.assert_not_awaited()
        payload = queue.get_nowait()
        assert payload["data"] == "hello"
        assert webchat_event._has_send_oper is True

    @pytest.mark.asyncio
    async def test_send_none_emits_end_payload(self, webchat_event):
        queue = asyncio.Queue()

        with (
            patch(
                "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
                return_value=queue,
            ),
            patch(
                "astrbot.core.platform.astr_message_event.Metric.upload",
                new_callable=AsyncMock,
            ),
        ):
            await webchat_event.send(None)

        payload = queue.get_nowait()
        assert payload["type"] == "end"
        assert payload["message_id"] == "msg123"

    @pytest.mark.asyncio
    async def test_complete_visible_turn_emits_end_payload_once(self, webchat_event):
        queue = asyncio.Queue()

        with patch(
            "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
            return_value=queue,
        ):
            await webchat_event.complete_visible_turn()
            await webchat_event.complete_visible_turn()

        payload = queue.get_nowait()
        assert payload["type"] == "end"
        assert payload["message_id"] == "msg123"
        assert queue.empty()
        assert webchat_event.get_extra("_visible_turn_completion_sent") is True
        assert webchat_event.get_extra("_platform_completion_signal_sent") is True


class TestWebChatMessageEventStreaming:
    @pytest.mark.asyncio
    async def test_send_streaming_does_not_route_via_output_controller(
        self, webchat_event
    ):
        queue = asyncio.Queue()
        controller = AsyncMock()
        webchat_event.set_extra("_output_controller", controller)

        async def generator():
            yield MessageChain([Plain("hello")])

        with (
            patch(
                "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
                return_value=queue,
            ),
            patch(
                "astrbot.core.platform.astr_message_event.Metric.upload",
                new_callable=AsyncMock,
            ),
        ):
            await webchat_event.send_streaming(generator())

        controller.capture_streaming.assert_not_awaited()
        assert queue.get_nowait()["data"] == "hello"
        assert queue.get_nowait()["type"] == "complete"
        assert webchat_event._has_send_oper is True

    @pytest.mark.asyncio
    async def test_send_streaming_legacy_consumes_generator_once(self, webchat_event):
        queue = asyncio.Queue()
        chains = [MessageChain([Plain("hello")])]
        generator = CountingAsyncIterable(chains)

        with (
            patch(
                "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
                return_value=queue,
            ),
            patch(
                "astrbot.core.platform.astr_message_event.Metric.upload",
                new_callable=AsyncMock,
            ),
        ):
            await webchat_event.send_streaming(generator)

        assert generator.aiter_calls == 1

    @pytest.mark.asyncio
    async def test_send_streaming_preserves_audio_chunk_and_reasoning(
        self, webchat_event
    ):
        queue = asyncio.Queue()

        reasoning = MessageChain([Plain("think")])
        reasoning.type = "reasoning"
        answer = MessageChain([Plain("answer")])
        audio_chunk = MessageChain([Plain("audio-base64"), Json({"text": "spoken"})])
        audio_chunk.type = "audio_chunk"

        async def generator():
            yield reasoning
            yield answer
            yield audio_chunk

        with patch(
            "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
            return_value=queue,
        ):
            await WebChatMessageEvent._send_streaming_via_back_queue(
                message_id="msg123",
                session_id="webchat!user!session123",
                generator=generator(),
            )

        payloads = []
        while not queue.empty():
            payloads.append(queue.get_nowait())

        assert [payload["type"] for payload in payloads] == [
            "plain",
            "plain",
            "audio_chunk",
            "complete",
        ]
        assert payloads[2]["data"] == "audio-base64"
        assert payloads[2]["text"] == "spoken"
        assert payloads[3]["data"] == "answer"
        assert payloads[3]["reasoning"] == "think"
