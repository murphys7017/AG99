from __future__ import annotations

import uuid
from typing import Any

from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, Group, MessageMember
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.platform_metadata import PlatformMetadata

from .observation import RuntimeObservation


class RuntimeObservationEvent(AstrMessageEvent):
    """Event adapter for an internal observation that may produce visible output."""

    def __init__(self, *, context: Any, observation: RuntimeObservation) -> None:
        target = observation.target_session
        session = MessageSession(
            target.platform_id,
            target.message_type,
            target.session_id,
        )
        message = AstrBotMessage()
        message.type = target.message_type
        message.self_id = "astrbot"
        message.session_id = target.session_id
        message.message_id = observation.correlation_id or uuid.uuid4().hex
        message.sender = MessageMember(user_id="astrbot", nickname="AstrBot")
        message.message = []
        message.message_str = ""
        message.raw_message = {
            "post_type": "system",
            "sub_type": "runtime_observation",
            "observation_id": message.message_id,
        }
        if target.group_id:
            message.group = Group(group_id=target.group_id, group_name=target.group_name)

        super().__init__(
            "",
            message,
            PlatformMetadata(
                name=target.platform_name,
                description="Runtime observation",
                id=target.platform_id,
                support_proactive_message=target.support_proactive_message,
                support_personal_runtime=target.support_personal_runtime,
            ),
            target.session_id,
        )
        self.session = session
        self.context_obj = context
        self.observation = observation
        self.set_extra("_runtime_observation_event", True)
        self.set_extra("_runtime_observation", observation)
        self.set_extra("_interaction_input_is_observation", True)

    async def send(self, message: MessageChain) -> None:
        if message is None:
            return
        delivered = await self.context_obj._send_message_direct(self.session, message)
        if not delivered:
            raise RuntimeError(
                f"Runtime observation target is unavailable: {self.session}"
            )
        await super().send(message)

    async def send_streaming(self, generator, use_fallback: bool = False) -> None:
        async for chain in generator:
            await self.send(chain)


__all__ = ["RuntimeObservationEvent"]
