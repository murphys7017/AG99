from astrbot import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .middleware import InteractionMiddleware


class CoreInputGateway:
    def __init__(self, middleware: InteractionMiddleware) -> None:
        self.middleware = middleware

    def put_nowait(self, event: AstrMessageEvent) -> None:
        logger.debug(
            "Interaction middleware inbound gateway hit: platform_id=%s session_id=%s",
            event.get_platform_id(),
            event.session_id,
        )
        self.middleware.handle_inbound(event)
