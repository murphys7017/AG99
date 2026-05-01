import uuid
from asyncio import Queue
from typing import Any

from astrbot import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .config import is_middleware_enabled_for_platform
from .output_controller import InteractionOutputController


class InteractionMiddleware:
    def __init__(
        self,
        config: Any,
        core_queue: Queue,
        output_controller: InteractionOutputController,
    ) -> None:
        self.config = config
        self.core_queue = core_queue
        self.output_controller = output_controller

    def is_enabled_for_event(self, event: AstrMessageEvent) -> bool:
        return is_middleware_enabled_for_platform(event.get_platform_id(), self.config)

    def attach_event_context(self, event: AstrMessageEvent) -> None:
        turn_id = uuid.uuid4().hex
        event.set_extra("_interaction_enabled", True)
        event.set_extra("_turn_id", turn_id)
        event.set_extra("_output_controller", self.output_controller)
        logger.debug(
            "Interaction middleware attached event context: platform_id=%s session_id=%s turn_id=%s",
            event.get_platform_id(),
            event.session_id,
            turn_id,
        )

    def handle_inbound(self, event: AstrMessageEvent) -> None:
        enabled = self.is_enabled_for_event(event)
        logger.debug(
            "Interaction middleware inbound dispatch: platform_id=%s session_id=%s enabled=%s",
            event.get_platform_id(),
            event.session_id,
            enabled,
        )
        if enabled:
            self.attach_event_context(event)
        self.core_queue.put_nowait(event)
