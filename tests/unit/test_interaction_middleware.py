import asyncio
from unittest.mock import MagicMock

import pytest

from astrbot.core.interaction.config import is_middleware_enabled_for_platform
from astrbot.core.interaction.input_gateway import CoreInputGateway
from astrbot.core.interaction.middleware import InteractionMiddleware
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata


class ConcreteAstrMessageEvent(AstrMessageEvent):
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
    message.self_id = "bot123"
    message.session_id = "webchat!user!session123"
    message.message_id = "msg123"
    message.sender = MessageMember(user_id="user123", nickname="TestUser")
    message.message = []
    message.message_str = "Hello world"
    return ConcreteAstrMessageEvent(
        message_str="Hello world",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="webchat!user!session123",
    )


class TestInteractionMiddlewareConfig:
    def test_global_disable_takes_precedence(self):
        config = {
            "interaction_middleware": {
                "enabled": False,
                "default_enabled_for_platforms": ["webchat"],
                "platforms": {"webchat": {"enabled": True}},
            }
        }
        assert is_middleware_enabled_for_platform("webchat", config) is False

    def test_explicit_platform_override_is_used(self):
        config = {
            "interaction_middleware": {
                "enabled": True,
                "default_enabled_for_platforms": [],
                "platforms": {"webchat": {"enabled": True}},
            }
        }
        assert is_middleware_enabled_for_platform("webchat", config) is True

    def test_default_platform_policy_is_used(self):
        config = {
            "interaction_middleware": {
                "enabled": True,
                "default_enabled_for_platforms": ["webchat"],
                "platforms": {},
            }
        }
        assert is_middleware_enabled_for_platform("webchat", config) is True
        assert is_middleware_enabled_for_platform("telegram", config) is False


class TestInteractionMiddleware:
    def test_handle_inbound_attaches_context_for_enabled_platform(self, webchat_event):
        queue = asyncio.Queue()
        controller = MagicMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )

        middleware.handle_inbound(webchat_event)

        assert queue.get_nowait() is webchat_event
        assert webchat_event.get_extra("_interaction_enabled") is True
        assert isinstance(webchat_event.get_extra("_turn_id"), str)
        assert webchat_event.get_extra("_output_controller") is controller

    def test_handle_inbound_skips_context_for_disabled_platform(self, webchat_event):
        queue = asyncio.Queue()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "default_enabled_for_platforms": [],
                    "platforms": {},
                }
            },
            queue,
            MagicMock(),
        )

        middleware.handle_inbound(webchat_event)

        assert queue.get_nowait() is webchat_event
        assert webchat_event.get_extra("_interaction_enabled") is None
        assert webchat_event.get_extra("_turn_id") is None
        assert webchat_event.get_extra("_output_controller") is None


class TestCoreInputGateway:
    def test_put_nowait_delegates_to_middleware(self, webchat_event):
        middleware = MagicMock()
        gateway = CoreInputGateway(middleware)

        gateway.put_nowait(webchat_event)

        middleware.handle_inbound.assert_called_once_with(webchat_event)
