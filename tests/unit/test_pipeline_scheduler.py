from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.webchat.webchat_event import WebChatMessageEvent


class GenericEvent(AstrMessageEvent):
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
    message.message = []
    message.message_str = "Hello"
    return WebChatMessageEvent(
        message_str="Hello",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="webchat!user!session123",
    )


@pytest.mark.asyncio
async def test_scheduler_does_not_emit_duplicate_completion_after_visible_turn_completed(
    webchat_event,
):
    scheduler = PipelineScheduler.__new__(PipelineScheduler)
    scheduler.stages = []
    scheduler.ctx = MagicMock()
    webchat_event.send = AsyncMock()
    webchat_event.complete_visible_turn = AsyncMock()
    webchat_event.set_extra("_visible_turn_completion_sent", True)

    await scheduler.execute(webchat_event)

    webchat_event.send.assert_not_awaited()
    webchat_event.complete_visible_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduler_completes_visible_turn_for_queue_platform(webchat_event):
    scheduler = PipelineScheduler.__new__(PipelineScheduler)
    scheduler.stages = []
    scheduler.ctx = MagicMock()
    webchat_event.complete_visible_turn = AsyncMock()

    await scheduler.execute(webchat_event)

    webchat_event.complete_visible_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduler_does_not_complete_generic_platform_by_default():
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
    message.message = []
    message.message_str = "Hello"
    event = GenericEvent(
        message_str="Hello",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="generic-session",
    )
    event.complete_visible_turn = AsyncMock()
    scheduler = PipelineScheduler.__new__(PipelineScheduler)
    scheduler.stages = []
    scheduler.ctx = MagicMock()

    await scheduler.execute(event)

    event.complete_visible_turn.assert_not_awaited()
