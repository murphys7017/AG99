from astrbot.core.interaction.core_bridge import apply_interaction_core_task_spec
from astrbot.core.interaction.types import CoreTaskSpec
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.provider.entities import ProviderRequest


class ConcreteAstrMessageEvent(AstrMessageEvent):
    async def send(self, message):
        await super().send(message)


def test_apply_interaction_core_task_spec_injects_execution_prompt():
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
    message.message_str = "查天气"
    event = ConcreteAstrMessageEvent(
        message_str="查天气",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="webchat!user!session123",
    )
    event.set_extra(
        "_interaction_core_task_spec",
        CoreTaskSpec(
            task_intent="weather",
            task_summary="查询天气",
            execution_prompt="请查询今天的天气。",
            suggested_capabilities=["search"],
        ),
    )
    req = ProviderRequest(prompt="查天气", system_prompt="base")

    apply_interaction_core_task_spec(req, event)

    assert "<interaction_execution_context>" in req.system_prompt
    assert "请查询今天的天气。" in req.system_prompt
    assert "查询天气" in req.system_prompt
