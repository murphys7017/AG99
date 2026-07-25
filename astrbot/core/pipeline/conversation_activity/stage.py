from collections.abc import AsyncGenerator

from astrbot import logger
from astrbot.core.interaction.conversation_activity_source import (
    CONVERSATION_ACTIVITY_CANDIDATE_EXTRA_KEY,
    ConversationActivitySource,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from ..context import PipelineContext
from ..stage import Stage, register_stage


@register_stage
class ConversationActivityStage(Stage):
    """Submit one eligible ambient group fact, then stop its normal message path."""

    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        self.source = ConversationActivitySource(
            ctx.personal_runtime_manager,
            ctx.plugin_manager.context,
        )

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> None | AsyncGenerator[None, None]:
        if not event.get_extra(CONVERSATION_ACTIVITY_CANDIDATE_EXTRA_KEY, False):
            return
        try:
            await self.source.submit(
                event,
                config_id=self.ctx.astrbot_config_id,
                plugin_context=self.ctx.plugin_manager.context,
                runtime_config=self.ctx.astrbot_config,
            )
        except Exception:
            logger.exception(
                "Personal Runtime conversation activity observation failed: session_id=%s",
                event.unified_msg_origin,
            )
        finally:
            event.stop_event()
