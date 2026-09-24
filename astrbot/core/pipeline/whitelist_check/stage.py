from collections.abc import AsyncGenerator

from astrbot.core import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.message_type import MessageType

from ..context import PipelineContext
from ..runtime_config import get_pipeline_turn_runtime_config
from ..stage import Stage, register_stage


@register_stage
class WhitelistCheckStage(Stage):
    """检查是否在群聊/私聊白名单"""

    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> None | AsyncGenerator[None, None]:
        runtime_config = get_pipeline_turn_runtime_config(
            event,
            self.ctx.astrbot_config,
        )
        platform_settings = runtime_config.get("platform_settings", {})
        whitelist = [
            str(item).strip()
            for item in platform_settings.get("id_whitelist", [])
            if str(item).strip()
        ]
        enable_whitelist_check = platform_settings.get("enable_id_white_list", False)
        wl_ignore_admin_on_group = platform_settings.get(
            "wl_ignore_admin_on_group", False
        )
        wl_ignore_admin_on_friend = platform_settings.get(
            "wl_ignore_admin_on_friend", False
        )
        wl_log = platform_settings.get("id_whitelist_log", False)

        if not enable_whitelist_check:
            # 白名单检查未启用
            return

        if not whitelist:
            # 白名单为空，不检查
            return

        if event.get_platform_name() == "webchat":
            # WebChat 豁免
            return

        # 检查是否在白名单
        if wl_ignore_admin_on_group:
            if (
                event.role == "admin"
                and event.get_message_type() == MessageType.GROUP_MESSAGE
            ):
                return
        if wl_ignore_admin_on_friend:
            if (
                event.role == "admin"
                and event.get_message_type() == MessageType.FRIEND_MESSAGE
            ):
                return
        if (
            event.unified_msg_origin not in whitelist
            and str(event.get_group_id()).strip() not in whitelist
        ):
            if wl_log:
                logger.info(
                    f"会话 ID {event.unified_msg_origin} 不在会话白名单中，已终止事件传播。请在配置文件中添加该会话 ID 到白名单。",
                )
            event.stop_event()
