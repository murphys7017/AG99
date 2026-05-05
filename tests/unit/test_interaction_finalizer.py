import pytest

from astrbot.core.interaction.finalizer import finalize_response
from astrbot.core.interaction.types import FinalizerMode, InteractionAgentConfig
from astrbot.core.message.components import Plain
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
    message.message = [Plain(text="帮我整理结果")]
    message.message_str = "帮我整理结果"
    return WebChatMessageEvent(
        message_str="帮我整理结果",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="webchat!user!session123",
    )


@pytest.mark.asyncio
async def test_force_finalizer_provider_unavailable_records_failure(webchat_event):
    class PluginContext:
        def get_provider_by_id(self, provider_id):
            return None

    result = await finalize_response(
        event=webchat_event,
        plugin_context=PluginContext(),
        config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.FORCE,
            finalizer_provider_id="missing",
        ),
        core_result_text="dry result",
    )

    assert result is None
    assert webchat_event.get_extra("_interaction_finalizer_failed") is True
    assert (
        webchat_event.get_extra("_interaction_finalizer_failure_reason")
        == "provider_unavailable"
    )
