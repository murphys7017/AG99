from collections.abc import AsyncGenerator

from astrbot.core import logger
from astrbot.core.message.components import Reply
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.utils.quoted_message.chain_parser import ReplyChainParser

from ..context import PipelineContext
from ..runtime_config import get_pipeline_turn_runtime_config
from ..stage import Stage, register_stage
from .strategies.strategy import StrategySelector


@register_stage
class ContentSafetyCheckStage(Stage):
    """检查内容安全

    当前只会检查文本的。
    """

    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx

    async def process(
        self,
        event: AstrMessageEvent,
        check_text: str | None = None,
    ) -> AsyncGenerator[None, None]:
        """检查内容安全"""
        runtime_config = get_pipeline_turn_runtime_config(
            event,
            self.ctx.astrbot_config,
        )
        strategy_selector = StrategySelector(runtime_config["content_safety"])
        if check_text is None:
            texts = [event.get_message_str()]
            reply_parser = ReplyChainParser()
            for component in event.get_messages():
                if isinstance(component, Reply) and (
                    quoted_text := reply_parser.extract_text_from_reply_component(
                        component
                    )
                ):
                    texts.append(quoted_text)
        else:
            texts = [check_text]

        ok, info = strategy_selector.check("\n".join(texts))
        if not ok:
            if event.is_at_or_wake_command:
                event.set_result(
                    MessageEventResult().message(
                        "你的消息或者大模型的响应中包含不适当的内容，已被屏蔽。",
                    ),
                )
                yield
            event.stop_event()
            logger.info(f"内容安全检查不通过，原因：{info}")
            return
