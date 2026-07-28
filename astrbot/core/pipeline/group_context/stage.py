from collections.abc import AsyncGenerator

from astrbot import logger
from astrbot.core.interaction.conversation_activity_source import (
    CONVERSATION_ACTIVITY_CANDIDATE_EXTRA_KEY,
)
from astrbot.core.interaction.group_context_capture import (
    GROUP_CONTEXT_CAPTURE_CANDIDATE_EXTRA,
    resolve_group_context_capture_collector,
)
from astrbot.core.interaction.group_reply import is_group_reply_candidate
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from ..context import PipelineContext
from ..stage import Stage, register_stage


@register_stage
class GroupContextStage(Stage):
    """Persist ambient group context after official admission checks."""

    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> None | AsyncGenerator[None, None]:
        if not event.get_extra(GROUP_CONTEXT_CAPTURE_CANDIDATE_EXTRA, False):
            return
        try:
            collector = resolve_group_context_capture_collector(
                self.ctx.plugin_manager.context.list_prompt_extension_collectors()
            )
            if collector is not None:
                await collector.capture_ambient_message(
                    event,
                    allow_router_candidate=is_group_reply_candidate(event),
                )
        except Exception:
            logger.exception(
                "Group context capture failed: session_id=%s",
                event.unified_msg_origin,
            )
        finally:
            if not (
                is_group_reply_candidate(event)
                or event.get_extra(CONVERSATION_ACTIVITY_CANDIDATE_EXTRA_KEY, False)
            ):
                event.stop_event()
