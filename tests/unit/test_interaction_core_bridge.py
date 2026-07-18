from html import unescape

import pytest

from astrbot.core.core_execution_contract import (
    CORE_PERSONA_COORDINATION_INSTRUCTION,
)
from astrbot.core.interaction.turn_state import InteractionTurnState
from astrbot.core.interaction.types import CoreTaskSpec
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.prompt.collectors.core_task_collector import CoreTaskCollector
from astrbot.core.prompt.context_types import ContextPack
from astrbot.core.prompt.render import PromptRenderEngine, PromptTarget
from astrbot.core.provider.entities import ProviderRequest


class ConcreteAstrMessageEvent(AstrMessageEvent):
    async def send(self, message):
        await super().send(message)


@pytest.mark.asyncio
async def test_core_task_collector_exposes_structured_execution_context():
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
    task_spec = CoreTaskSpec(
        task_intent="weather",
        task_summary="查询天气",
        execution_prompt="请查询今天的天气。",
        suggested_capabilities=["search"],
    )
    event.set_extra(
        "_interaction_turn_state",
        InteractionTurnState(
            turn_id="turn-1",
            core_task_spec=task_spec,
        ),
    )
    req = ProviderRequest(prompt="查天气", system_prompt="base")

    slots = await CoreTaskCollector().collect(event, None, None, req)

    assert len(slots) == 1
    assert slots[0].name == "system.core_execution_context"
    assert slots[0].value["execution_prompt"] == "请查询今天的天气。"
    assert slots[0].value["task_summary"] == "查询天气"
    assert slots[0].value["instruction"] == CORE_PERSONA_COORDINATION_INSTRUCTION
    assert "immediate_reply_already_sent" not in slots[0].value
    assert "speculative_persona_status" not in slots[0].value
    assert req.system_prompt == "base"

    render_result = PromptRenderEngine().render(
        ContextPack(slots={slots[0].name: slots[0]}),
        target=PromptTarget.CORE,
    )
    rendered_system_prompt = unescape(render_result.system_prompt)
    assert CORE_PERSONA_COORDINATION_INSTRUCTION in rendered_system_prompt
    assert "immediate_reply_already_sent" not in render_result.system_prompt
    assert "speculative_persona_status" not in render_result.system_prompt
