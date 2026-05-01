from collections.abc import AsyncGenerator

from astrbot import logger
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.sources.webchat.webchat_event import WebChatMessageEvent


class InteractionOutputController:
    async def capture_message_chain(
        self,
        message: MessageChain | None,
        event: AstrMessageEvent,
    ) -> None:
        if event.get_platform_id() == "webchat":
            logger.debug(
                "Interaction middleware outbound send intercepted: platform_id=%s session_id=%s turn_id=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
            )
            await WebChatMessageEvent._send(
                message_id=event.message_obj.message_id,
                message=message,
                session_id=event.session_id,
            )
            return
        raise NotImplementedError(
            f"Middleware output is not implemented for platform {event.get_platform_id()}.",
        )

    async def capture_streaming(
        self,
        generator: AsyncGenerator[MessageChain, None],
        event: AstrMessageEvent,
        use_fallback: bool = False,
    ) -> None:
        if event.get_platform_id() == "webchat":
            logger.debug(
                "Interaction middleware outbound streaming intercepted: platform_id=%s session_id=%s turn_id=%s use_fallback=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                use_fallback,
            )
            await WebChatMessageEvent._send_streaming_via_back_queue(
                message_id=event.message_obj.message_id,
                session_id=event.session_id,
                generator=generator,
            )
            return
        raise NotImplementedError(
            f"Middleware streaming output is not implemented for platform {event.get_platform_id()}.",
        )
