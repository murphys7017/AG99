from collections.abc import AsyncGenerator, Callable

from astrbot import logger
from astrbot.core.interaction.conversation_activity_source import (
    CONVERSATION_ACTIVITY_CANDIDATE_EXTRA_KEY,
    is_conversation_activity_candidate,
    is_conversation_activity_capture_enabled,
    resolve_conversation_activity_target,
)
from astrbot.core.message.components import At, AtAll, Reply
from astrbot.core.message.message_event_result import MessageChain, MessageEventResult
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.message_type import MessageType
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.filter.permission import PermissionTypeFilter
from astrbot.core.star.session_plugin_manager import SessionPluginManager
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import EventType, star_handlers_registry

from ..context import PipelineContext
from ..stage import Stage, register_stage

UNIQUE_SESSION_ID_BUILDERS: dict[str, Callable[[AstrMessageEvent], str | None]] = {
    "aiocqhttp": lambda e: f"{e.get_sender_id()}_{e.get_group_id()}",
    "slack": lambda e: f"{e.get_sender_id()}_{e.get_group_id()}",
    "dingtalk": lambda e: e.get_sender_id(),
    "qq_official": lambda e: e.get_sender_id(),
    "qq_official_webhook": lambda e: e.get_sender_id(),
    "lark": lambda e: f"{e.get_sender_id()}%{e.get_group_id()}",
    "misskey": lambda e: f"{e.get_session_id()}_{e.get_sender_id()}",
    "matrix": lambda e: f"{e.get_sender_id()}_{e.get_group_id() or e.get_session_id()}",
}


def build_unique_session_id(event: AstrMessageEvent) -> str | None:
    platform = event.get_platform_name()
    builder = UNIQUE_SESSION_ID_BUILDERS.get(platform)
    return builder(event) if builder else None


async def discover_activated_handlers(
    event: AstrMessageEvent,
    *,
    config: dict,
    disable_builtin_commands: bool,
    no_permission_reply: bool,
) -> bool:
    """Run the existing Handler discovery once after a message is admitted."""

    activated_handlers = []
    handlers_parsed_params = {}
    enabled_plugins_name = config.get("plugin_set", ["*"])
    event.plugins_name = None if enabled_plugins_name == ["*"] else enabled_plugins_name
    logger.debug("enabled_plugins_name: %s", enabled_plugins_name)

    handler_woke = False
    for handler in star_handlers_registry.get_handlers_by_event_type(
        EventType.AdapterMessageEvent,
        plugins_name=event.plugins_name,
    ):
        if (
            disable_builtin_commands
            and handler.handler_module_path
            == "astrbot.builtin_stars.builtin_commands.main"
        ):
            continue

        passed = True
        permission_not_pass = False
        permission_filter_raise_error = False
        if len(handler.event_filters) == 0:
            continue

        for filter in handler.event_filters:
            try:
                if isinstance(filter, PermissionTypeFilter):
                    if not filter.filter(event, config):
                        permission_not_pass = True
                        permission_filter_raise_error = filter.raise_error
                elif not filter.filter(event, config):
                    passed = False
                    break
            except Exception as exc:
                await event.send(
                    MessageEventResult().message(
                        f"插件 {star_map[handler.handler_module_path].name}: {exc}",
                    ),
                )
                event.stop_event()
                passed = False
                break
        if passed:
            if permission_not_pass:
                if not permission_filter_raise_error:
                    continue
                if no_permission_reply:
                    await event.send(
                        MessageChain().message(
                            f"您(ID: {event.get_sender_id()})的权限不足以使用此指令。通过 /sid 获取 ID 并请管理员添加。",
                        ),
                    )
                logger.info(
                    "触发 %s 时, 用户(ID=%s) 权限不足。",
                    star_map[handler.handler_module_path].name,
                    event.get_sender_id(),
                )
                event.stop_event()
                return True

            handler_woke = True
            event.is_wake = True
            is_group_cmd_handler = any(
                isinstance(item, CommandGroupFilter) for item in handler.event_filters
            )
            if not is_group_cmd_handler:
                activated_handlers.append(handler)
                if "parsed_params" in event.get_extra(default={}):
                    handlers_parsed_params[handler.handler_full_name] = event.get_extra(
                        "parsed_params"
                    )

        event._extras.pop("parsed_params", None)

    activated_handlers = await SessionPluginManager.filter_handlers_by_session(
        event,
        activated_handlers,
    )
    event.set_extra("activated_handlers", activated_handlers)
    event.set_extra("handlers_parsed_params", handlers_parsed_params)
    return handler_woke


@register_stage
class WakingCheckStage(Stage):
    """检查是否需要唤醒。唤醒机器人有如下几点条件：

    1. 机器人被 @ 了
    2. 机器人的消息被提到了
    3. 以 wake_prefix 前缀开头，并且消息没有以 At 消息段开头
    4. 插件（Star）的 handler filter 通过
    5. 私聊情况下，位于 admins_id 列表中的管理员的消息（在白名单阶段中）
    """

    async def initialize(self, ctx: PipelineContext) -> None:
        """初始化唤醒检查阶段

        Args:
            ctx (PipelineContext): 消息管道上下文对象, 包括配置和插件管理器

        """
        self.ctx = ctx
        self.no_permission_reply = self.ctx.astrbot_config["platform_settings"].get(
            "no_permission_reply",
            True,
        )
        # 私聊是否需要 wake_prefix 才能唤醒机器人
        self.friend_message_needs_wake_prefix = self.ctx.astrbot_config[
            "platform_settings"
        ].get("friend_message_needs_wake_prefix", False)
        # 是否忽略机器人自己发送的消息
        self.ignore_bot_self_message = self.ctx.astrbot_config["platform_settings"].get(
            "ignore_bot_self_message",
            False,
        )
        self.ignore_at_all = self.ctx.astrbot_config["platform_settings"].get(
            "ignore_at_all",
            False,
        )
        self.disable_builtin_commands = self.ctx.astrbot_config.get(
            "disable_builtin_commands", False
        )
        platform_settings = self.ctx.astrbot_config.get("platform_settings", {})
        self.unique_session = platform_settings.get("unique_session", False)

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> None | AsyncGenerator[None, None]:
        # apply unique session
        if self.unique_session and event.message_obj.type == MessageType.GROUP_MESSAGE:
            sid = build_unique_session_id(event)
            if sid:
                event.session_id = sid

        # ignore bot self message
        if (
            self.ignore_bot_self_message
            and event.get_self_id() == event.get_sender_id()
        ):
            event.stop_event()
            return

        # 设置 sender 身份
        event.message_str = event.message_str.strip()
        for admin_id in self.ctx.astrbot_config["admins_id"]:
            if str(event.get_sender_id()) == admin_id:
                event.role = "admin"
                break

        # 检查 wake
        wake_prefixes = self.ctx.astrbot_config["wake_prefix"]
        messages = event.get_messages()
        is_wake = False
        for wake_prefix in wake_prefixes:
            if event.message_str.startswith(wake_prefix):
                if (
                    not event.is_private_chat()
                    and isinstance(messages[0], At)
                    and str(messages[0].qq) != str(event.get_self_id())
                    and str(messages[0].qq) != "all"
                ):
                    # 如果是群聊，且第一个消息段是 At 消息，但不是 At 机器人或 At 全体成员，则不唤醒
                    break
                is_wake = True
                event.is_at_or_wake_command = True
                event.is_wake = True
                event.message_str = event.message_str[len(wake_prefix) :].strip()
                break
        if not is_wake:
            # 检查是否有at消息 / at全体成员消息 / 引用了bot的消息
            for message in messages:
                if (
                    (
                        isinstance(message, At)
                        and (str(message.qq) == str(event.get_self_id()))
                    )
                    or (isinstance(message, AtAll) and not self.ignore_at_all)
                    or (
                        isinstance(message, Reply)
                        and str(message.sender_id) == str(event.get_self_id())
                    )
                ):
                    is_wake = True
                    event.is_wake = True
                    wake_prefix = ""
                    event.is_at_or_wake_command = True
                    break
            # 检查是否是私聊
            if event.is_private_chat() and not self.friend_message_needs_wake_prefix:
                is_wake = True
                event.is_wake = True
                event.is_at_or_wake_command = True
                wake_prefix = ""
            elif not any(
                (
                    isinstance(message, At)
                    and str(message.qq) not in {str(event.get_self_id()), "all"}
                )
                or (isinstance(message, AtAll) and self.ignore_at_all)
                or (
                    isinstance(message, Reply)
                    and str(message.sender_id)
                    not in {"", str(event.get_self_id())}
                )
                for message in messages
            ) and self.ctx.personal_runtime_manager is not None:
                continuation = self.ctx.personal_runtime_manager.classify_group_conversation_continuation(
                    event,
                    config_id=self.ctx.astrbot_config_id,
                    runtime_config=self.ctx.astrbot_config,
                )
                if continuation is not None:
                    is_wake = True
                    event.is_wake = True
                    event.is_at_or_wake_command = True
                    if continuation == "model":
                        event.set_extra(
                            "_personal_runtime_model_continuation",
                            True,
                        )
                    logger.info(
                        "Personal Runtime accepted group continuation: "
                        "session_id=%s sender_id=%s mode=%s",
                        event.unified_msg_origin,
                        event.get_sender_id(),
                        continuation,
                    )

        if event.get_extra("_personal_runtime_model_continuation", False):
            # Router owns admission for this unaddressed continuation candidate.
            event.set_extra("activated_handlers", [])
            event.set_extra("handlers_parsed_params", {})
            return

        is_wake = (
            await discover_activated_handlers(
                event,
                config=self.ctx.astrbot_config,
                disable_builtin_commands=self.disable_builtin_commands,
                no_permission_reply=self.no_permission_reply,
            )
            or is_wake
        )
        if event.is_stopped():
            return

        if not is_wake:
            if is_conversation_activity_capture_enabled(self.ctx.astrbot_config):
                target = resolve_conversation_activity_target(
                    event,
                    self.ctx.plugin_manager.context.get_runtime_observation_targets(),
                )
                if is_conversation_activity_candidate(
                    event,
                    self.ctx.astrbot_config,
                    target,
                ):
                    event.set_extra(CONVERSATION_ACTIVITY_CANDIDATE_EXTRA_KEY, True)
                    return
            event.stop_event()
