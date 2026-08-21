import asyncio
import html
import inspect
import itertools
import logging
import time
import uuid
from collections.abc import Awaitable
from typing import Any, cast

from aiocqhttp import CQHttp, Event
from aiocqhttp.exceptions import ActionFailed

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import *
from astrbot.api.platform import (
    AstrBotMessage,
    MessageMember,
    MessageType,
    Platform,
    PlatformMetadata,
)
from astrbot.core.platform.astr_message_event import MessageSesion

from ...register import register_platform_adapter
from .aiocqhttp_message_event import *
from .aiocqhttp_message_event import AiocqhttpMessageEvent


@register_platform_adapter(
    "aiocqhttp",
    "适用于 OneBot V11 标准的消息平台适配器，支持反向 WebSockets。",
    support_streaming_message=False,
)
class AiocqhttpAdapter(Platform):
    _GROUP_MEMBER_CACHE_TTL_SECONDS = 24 * 60 * 60.0
    _GROUP_MEMBER_PREWARM_CONCURRENCY = 2
    _GROUP_MEMBER_PREWARM_ATTEMPTS = 3
    _GROUP_MEMBER_REFRESH_TIMEOUT_SECONDS = 15.0
    _GROUP_MEMBER_REFRESH_RETRY_DELAY_SECONDS = 60.0

    def __init__(
        self,
        platform_config: dict,
        platform_settings: dict,
        event_queue: asyncio.Queue,
    ) -> None:
        super().__init__(platform_config, event_queue)

        self.settings = platform_settings
        self.host = platform_config["ws_reverse_host"]
        self.port = platform_config["ws_reverse_port"]
        self._group_member_cache: dict[str, tuple[float, dict[str, str]]] = {}
        self._group_member_refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._group_member_pending_refreshes: set[str] = set()
        self._group_member_refresh_retry_at: dict[str, float] = {}
        self._group_member_refresh_semaphore = asyncio.Semaphore(
            self._GROUP_MEMBER_PREWARM_CONCURRENCY,
        )
        self._group_member_prewarm_task: asyncio.Task[None] | None = None
        self._group_member_cache_shutdown = False
        self._event_loop: asyncio.AbstractEventLoop | None = None

        self.metadata = PlatformMetadata(
            name="aiocqhttp",
            description="适用于 OneBot 标准的消息平台适配器，支持反向 WebSockets。",
            id=cast(str, self.config.get("id")),
            support_streaming_message=False,
            support_personal_runtime=True,
        )

        self.bot = CQHttp(
            use_ws_reverse=True,
            import_name="aiocqhttp",
            api_timeout_sec=180,
            access_token=platform_config.get(
                "ws_reverse_token",
            ),  # 以防旧版本配置不存在
        )

        @self.bot.on_request()
        async def request(event: Event) -> None:
            try:
                abm = await self.convert_message(event)
                if not abm:
                    return
                await self.handle_msg(abm)
            except Exception as e:
                logger.exception(f"Handle request message failed: {e}")
                return

        @self.bot.on_notice()
        async def notice(event: Event) -> None:
            try:
                self._handle_group_member_notice(event)
                abm = await self.convert_message(event)
                if abm:
                    await self.handle_msg(abm)
            except Exception as e:
                logger.exception(f"Handle notice message failed: {e}")
                return

        @self.bot.on_message("group")
        async def group(event: Event) -> None:
            try:
                abm = await self.convert_message(event)
                if abm:
                    await self.handle_msg(abm)
            except Exception as e:
                logger.exception(f"Handle group message failed: {e}")
                return

        @self.bot.on_message("private")
        async def private(event: Event) -> None:
            try:
                abm = await self.convert_message(event)
                if abm:
                    await self.handle_msg(abm)
            except Exception as e:
                logger.exception(f"Handle private message failed: {e}")
                return

        @self.bot.on_websocket_connection
        def on_websocket_connection(_) -> None:
            logger.info("aiocqhttp(OneBot v11) 适配器已连接。")
            self._schedule_group_member_prewarm()

    async def send_by_session(
        self,
        session: MessageSesion,
        message_chain: MessageChain,
    ) -> None:
        is_group = session.message_type == MessageType.GROUP_MESSAGE
        if is_group:
            session_id = session.session_id.split("_")[-1]
        else:
            session_id = session.session_id
        await AiocqhttpMessageEvent.send_message(
            bot=self.bot,
            message_chain=message_chain,
            event=None,  # 这里不需要 event，因为是通过 session 发送的
            is_group=is_group,
            session_id=session_id,
        )
        await super().send_by_session(session, message_chain)

    async def convert_message(self, event: Event) -> AstrBotMessage | None:
        raw_message = event.get("raw_message")
        if isinstance(raw_message, str) and raw_message:
            # Normalize CQ code escaping for downstream consumers.
            event["raw_message"] = html.unescape(raw_message)

        logger.debug(f"[aiocqhttp] RawMessage {event}")

        if event["post_type"] == "message":
            abm = await self._convert_handle_message_event(event)
            if abm.sender.user_id == "2854196310":
                # 屏蔽 QQ 管家的消息
                return None
        elif event["post_type"] == "notice":
            abm = await self._convert_handle_notice_event(event)
        elif event["post_type"] == "request":
            abm = await self._convert_handle_request_event(event)

        return abm

    async def _convert_handle_request_event(self, event: Event) -> AstrBotMessage:
        """OneBot V11 请求类事件"""
        abm = AstrBotMessage()
        abm.self_id = str(event.self_id)
        abm.sender = MessageMember(
            user_id=str(event.user_id), nickname=str(event.user_id)
        )
        abm.type = MessageType.OTHER_MESSAGE
        if event.get("group_id"):
            abm.type = MessageType.GROUP_MESSAGE
            abm.group_id = str(event.group_id)
        else:
            abm.type = MessageType.FRIEND_MESSAGE
        abm.session_id = (
            str(event.group_id)
            if abm.type == MessageType.GROUP_MESSAGE
            else abm.sender.user_id
        )
        abm.message_str = ""
        abm.message = []
        abm.timestamp = int(time.time())
        abm.message_id = uuid.uuid4().hex
        abm.raw_message = event
        return abm

    async def _convert_handle_notice_event(
        self,
        event: Event,
    ) -> AstrBotMessage | None:
        """OneBot V11 通知类事件"""
        # OneBot's input-status notice describes a user's typing state rather
        # than a message. Enqueuing it lets continuation logic reuse the last
        # real message in the session, which can trigger duplicate replies.
        if (
            event.get("notice_type") == "notify"
            and event.get("sub_type") == "input_status"
        ):
            return None

        abm = AstrBotMessage()
        abm.self_id = str(event.self_id)
        abm.sender = MessageMember(
            user_id=str(event.user_id), nickname=str(event.user_id)
        )
        abm.type = MessageType.OTHER_MESSAGE
        if event.get("group_id"):
            abm.group_id = str(event.group_id)
            abm.type = MessageType.GROUP_MESSAGE
        else:
            abm.type = MessageType.FRIEND_MESSAGE
        abm.session_id = (
            str(event.group_id)
            if abm.type == MessageType.GROUP_MESSAGE
            else abm.sender.user_id
        )
        abm.message_str = ""
        abm.message = []
        abm.raw_message = event
        abm.timestamp = int(time.time())
        abm.message_id = uuid.uuid4().hex

        if "sub_type" in event:
            if event["sub_type"] == "poke" and "target_id" in event:
                abm.message.append(Poke(id=str(event["target_id"])))

        return abm

    async def _convert_handle_message_event(
        self,
        event: Event,
        get_reply=True,
    ) -> AstrBotMessage:
        """OneBot V11 消息类事件

        @param event: 事件对象
        @param get_reply: 是否获取回复消息。这个参数是为了防止多个回复嵌套。
        """
        assert event.sender is not None
        abm = AstrBotMessage()
        abm.self_id = str(event.self_id)
        abm.sender = MessageMember(
            str(event.sender["user_id"]),
            event.sender.get("card") or event.sender.get("nickname", "N/A"),
        )
        if event["message_type"] == "group":
            abm.type = MessageType.GROUP_MESSAGE
            abm.group_id = str(event.group_id)
            abm.group = Group(str(event.group_id))
            abm.group.group_name = event.get("group_name", "N/A")
            self._schedule_group_member_refresh(abm.group_id)
        elif event["message_type"] == "private":
            abm.type = MessageType.FRIEND_MESSAGE
        abm.session_id = (
            str(event.group_id)
            if abm.type == MessageType.GROUP_MESSAGE
            else abm.sender.user_id
        )

        abm.message_id = str(event.message_id)
        abm.message = []

        message_str = ""
        if not isinstance(event.message, list):
            err = f"aiocqhttp: 无法识别的消息类型: {event.message!s}，此条消息将被忽略。如果您在使用 go-cqhttp，请将其配置文件中的 message.post-format 更改为 array。"
            logger.critical(err)
            try:
                await self.bot.send(event, err)
            except BaseException as e:
                logger.error(f"回复消息失败: {e}")
            raise ValueError(err)

        # 按消息段类型类型适配
        for t, m_group in itertools.groupby(event.message, key=lambda x: x["type"]):
            a = None
            if t == "text":
                current_text = "".join(m["data"]["text"] for m in m_group).strip()
                if not current_text:
                    # 如果文本段为空，则跳过
                    continue
                message_str += current_text
                a = ComponentTypes[t](text=current_text)
                abm.message.append(a)

            elif t == "file":
                for m in m_group:
                    if m["data"].get("url") and m["data"].get("url").startswith("http"):
                        # Lagrange
                        logger.info("guessing lagrange")
                        # 检查多个可能的文件名字段
                        file_name = (
                            m["data"].get("file_name", "")
                            or m["data"].get("name", "")
                            or m["data"].get("file", "")
                            or "file"
                        )
                        abm.message.append(File(name=file_name, url=m["data"]["url"]))
                    else:
                        try:
                            # Napcat
                            ret = None
                            if abm.type == MessageType.GROUP_MESSAGE:
                                ret = await self.bot.call_action(
                                    action="get_group_file_url",
                                    file_id=event.message[0]["data"]["file_id"],
                                    group_id=event.group_id,
                                )
                            elif abm.type == MessageType.FRIEND_MESSAGE:
                                ret = await self.bot.call_action(
                                    action="get_private_file_url",
                                    file_id=event.message[0]["data"]["file_id"],
                                )
                            if ret and "url" in ret:
                                file_url = ret["url"]  # https
                                # 优先从 API 返回值获取文件名，其次从原始消息数据获取
                                file_name = (
                                    ret.get("file_name", "")
                                    or ret.get("name", "")
                                    or m["data"].get("file", "")
                                    or m["data"].get("file_name", "")
                                )
                                a = File(name=file_name, url=file_url)
                                abm.message.append(a)
                            else:
                                logger.error(f"获取文件失败: {ret}")

                        except ActionFailed as e:
                            logger.error(f"获取文件失败: {e}，此消息段将被忽略。")
                        except BaseException as e:
                            logger.error(f"获取文件失败: {e}，此消息段将被忽略。")

            elif t == "reply":
                for m in m_group:
                    if not get_reply:
                        a = ComponentTypes[t](**m["data"])
                        abm.message.append(a)
                    else:
                        try:
                            reply_event_data = await self.bot.call_action(
                                action="get_msg",
                                message_id=int(m["data"]["id"]),
                            )
                            # 添加必要的 post_type 字段，防止 Event.from_payload 报错
                            reply_event_data["post_type"] = "message"
                            new_event = Event.from_payload(reply_event_data)
                            if not new_event:
                                logger.error(
                                    f"无法从回复消息数据构造 Event 对象: {reply_event_data}",
                                )
                                continue
                            abm_reply = await self._convert_handle_message_event(
                                new_event,
                                get_reply=False,
                            )

                            reply_seg = Reply(
                                id=abm_reply.message_id,
                                chain=abm_reply.message,
                                sender_id=abm_reply.sender.user_id,
                                sender_nickname=abm_reply.sender.nickname,
                                time=abm_reply.timestamp,
                                message_str=abm_reply.message_str,
                                text=abm_reply.message_str,  # for compatibility
                                qq=abm_reply.sender.user_id,  # for compatibility
                            )

                            abm.message.append(reply_seg)
                        except BaseException as e:
                            logger.error(f"获取引用消息失败: {e}。")
                            a = ComponentTypes[t](**m["data"])
                            abm.message.append(a)
            elif t == "at":
                first_at_self_processed = False
                # Accumulate @ mention text for efficient concatenation
                at_parts = []

                for m in m_group:
                    try:
                        if m["data"]["qq"] == "all":
                            abm.message.append(At(qq="all", name="全体成员"))
                            continue

                        target_qq = str(m["data"]["qq"])
                        is_at_self = target_qq == abm.self_id
                        segment_name = str(m["data"].get("name") or "")
                        if is_at_self:
                            # The event already identifies the bot; do not make a
                            # OneBot member-info request just to recognize self.
                            nickname = segment_name or target_qq
                        else:
                            nickname = self._get_cached_group_member_nickname(
                                str(event.group_id),
                                target_qq,
                            )
                            nickname = nickname or segment_name or target_qq

                        abm.message.append(At(qq=target_qq, name=nickname))

                        if is_at_self and not first_at_self_processed:
                            # 第一个@是机器人，不添加到message_str
                            first_at_self_processed = True
                        elif nickname:
                            # 非第一个@机器人或@其他用户，添加到message_str
                            at_parts.append(f" @{nickname}({target_qq}) ")
                    except ActionFailed as e:
                        logger.error(f"获取 @ 用户信息失败: {e}，此消息段将被忽略。")
                    except BaseException as e:
                        logger.error(f"获取 @ 用户信息失败: {e}，此消息段将被忽略。")

                message_str += "".join(at_parts)
            elif t == "markdown":
                for m in m_group:
                    text = m["data"].get("markdown") or m["data"].get("content", "")
                    abm.message.append(Plain(text=text))
                    message_str += text
            else:
                for m in m_group:
                    try:
                        if t not in ComponentTypes:
                            logger.warning(
                                f"不支持的消息段类型，已忽略: {t}, data={m['data']}"
                            )
                            continue
                        a = ComponentTypes[t](**m["data"])
                        abm.message.append(a)
                    except Exception as e:
                        logger.exception(
                            f"消息段解析失败: type={t}, data={m['data']}. {e}"
                        )
                        continue

        abm.timestamp = int(time.time())
        abm.message_str = message_str
        abm.raw_message = event

        return abm

    def _get_cached_group_member_nickname(
        self,
        group_id: str,
        user_id: str,
    ) -> str:
        """Read a group snapshot without making a network request."""
        snapshot = self._group_member_cache.get(group_id)
        if snapshot is None:
            if self._is_group_member_prewarm_allowed(group_id):
                self._schedule_group_member_refresh(group_id)
            return ""

        loaded_at, members = snapshot
        if time.monotonic() - loaded_at >= self._GROUP_MEMBER_CACHE_TTL_SECONDS:
            self._schedule_group_member_refresh(group_id)
        return members.get(user_id, "")

    def _schedule_group_member_prewarm(self) -> None:
        """Start one background refresh for the bot's known groups."""
        if self._group_member_cache_shutdown:
            return
        loop = self._event_loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # aiocqhttp may invoke connection callbacks from a worker
                # thread before the adapter has captured its main loop.
                return
            self._event_loop = loop

        if loop.is_closed():
            return

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is loop:
            self._schedule_group_member_prewarm_on_loop()
        else:
            # The websocket connection callback is synchronous and aiocqhttp
            # can run it through Quart's executor. Task creation must happen
            # on the loop that owns the adapter and its async resources.
            loop.call_soon_threadsafe(self._schedule_group_member_prewarm_on_loop)

    def _schedule_group_member_prewarm_on_loop(self) -> None:
        """Create the prewarm task on the adapter's owning event loop."""
        if self._group_member_cache_shutdown:
            return
        if (
            self._group_member_prewarm_task
            and not self._group_member_prewarm_task.done()
        ):
            return

        task = asyncio.create_task(
            self._prewarm_group_members(),
            name="aiocqhttp-group-member-prewarm",
        )
        self._group_member_prewarm_task = task
        task.add_done_callback(self._report_group_member_prewarm_result)

    async def _prewarm_group_members(self) -> None:
        groups: Any = None
        last_error: Exception | None = None
        for attempt in range(self._GROUP_MEMBER_PREWARM_ATTEMPTS):
            try:
                groups = await asyncio.wait_for(
                    self.bot.call_action(action="get_group_list"),
                    timeout=self._GROUP_MEMBER_REFRESH_TIMEOUT_SECONDS,
                )
                break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_error = e
                if attempt + 1 < self._GROUP_MEMBER_PREWARM_ATTEMPTS:
                    await asyncio.sleep(2**attempt)

        if last_error is not None and groups is None:
            logger.warning(f"获取 QQ 群列表失败，将在群消息到达时按需预热: {last_error}")
            return

        if not isinstance(groups, list):
            logger.warning("获取 QQ 群列表返回了无法识别的数据，跳过成员预热。")
            return

        group_ids = {
            str(group.get("group_id"))
            for group in groups
            if isinstance(group, dict) and group.get("group_id") is not None
        }
        for group_id in group_ids:
            self._schedule_group_member_refresh(group_id)

        logger.debug(f"QQ 群成员预热任务已提交: groups={len(group_ids)}")

    def _schedule_group_member_refresh(self, group_id: str, force: bool = False) -> None:
        """Schedule a deduplicated, non-blocking refresh for one group."""
        if (
            not group_id
            or self._group_member_cache_shutdown
            or not self._is_group_member_prewarm_allowed(group_id)
        ):
            return

        snapshot = self._group_member_cache.get(group_id)
        if snapshot is not None and not force:
            loaded_at, _ = snapshot
            if time.monotonic() - loaded_at < self._GROUP_MEMBER_CACHE_TTL_SECONDS:
                return
        if not force and time.monotonic() < self._group_member_refresh_retry_at.get(
            group_id,
            0.0,
        ):
            return

        existing = self._group_member_refresh_tasks.get(group_id)
        if existing is not None and not existing.done():
            if force:
                self._group_member_pending_refreshes.add(group_id)
            return

        task = asyncio.create_task(
            self._refresh_group_members(group_id),
            name=f"aiocqhttp-group-member-refresh-{group_id}",
        )
        self._group_member_refresh_tasks[group_id] = task
        task.add_done_callback(
            lambda finished, current_group_id=group_id: self._finish_group_member_refresh(
                current_group_id,
                finished,
            ),
        )

    async def _refresh_group_members(self, group_id: str) -> None:
        async with self._group_member_refresh_semaphore:
            try:
                members = await asyncio.wait_for(
                    self.bot.call_action(
                        action="get_group_member_list",
                        group_id=int(group_id),
                    ),
                    timeout=self._GROUP_MEMBER_REFRESH_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._mark_group_member_refresh_failed(group_id)
                logger.warning(f"获取 QQ 群成员列表失败: group_id={group_id}, error={e}")
                return

            if not isinstance(members, list) or not members:
                self._mark_group_member_refresh_failed(group_id)
                logger.warning(
                    f"获取 QQ 群成员列表返回为空，保留旧缓存: group_id={group_id}",
                )
                return

            member_names: dict[str, str] = {}
            for member in members:
                if not isinstance(member, dict) or member.get("user_id") is None:
                    continue
                user_id = str(member["user_id"])
                member_names[user_id] = str(
                    member.get("card") or member.get("nickname") or user_id,
                )

            if member_names:
                self._group_member_cache[group_id] = (
                    time.monotonic(),
                    member_names,
                )
                self._group_member_refresh_retry_at.pop(group_id, None)
                logger.debug(
                    f"QQ 群成员缓存已刷新: group_id={group_id}, members={len(member_names)}",
                )
            else:
                self._mark_group_member_refresh_failed(group_id)
                logger.warning(
                    f"QQ 群成员列表没有有效成员数据，保留旧缓存: group_id={group_id}",
                )

    def _mark_group_member_refresh_failed(self, group_id: str) -> None:
        self._group_member_refresh_retry_at[group_id] = (
            time.monotonic() + self._GROUP_MEMBER_REFRESH_RETRY_DELAY_SECONDS
        )

    def _is_group_member_prewarm_allowed(self, group_id: str) -> bool:
        """Limit prewarming to configured reply-capable group sessions."""
        if not self.settings.get("enable_id_white_list", False):
            return True

        whitelist = {
            str(item).strip()
            for item in self.settings.get("id_whitelist", [])
            if str(item).strip()
        }
        if not whitelist:
            return True

        for item in whitelist:
            if item == group_id:
                return True
            parts = item.split(":", 2)
            if (
                len(parts) == 3
                and parts[0] == self.metadata.id
                and parts[1] == "GroupMessage"
                and parts[2] == group_id
            ):
                return True
        return False

    def _finish_group_member_refresh(
        self,
        group_id: str,
        task: asyncio.Task[None],
    ) -> None:
        if self._group_member_refresh_tasks.get(group_id) is task:
            self._group_member_refresh_tasks.pop(group_id, None)
        self._report_background_task(task, f"QQ 群成员刷新失败: group_id={group_id}")
        if group_id in self._group_member_pending_refreshes:
            self._group_member_pending_refreshes.discard(group_id)
            self._schedule_group_member_refresh(group_id, force=True)

    def _report_group_member_prewarm_result(self, task: asyncio.Task[None]) -> None:
        self._report_background_task(task, "QQ 群成员预热失败")

    @staticmethod
    def _report_background_task(task: asyncio.Task[None], message: str) -> None:
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.warning(f"{message}: {exception}")

    def _handle_group_member_notice(self, event: Event) -> None:
        group_id = event.get("group_id")
        notice_type = event.get("notice_type")
        if group_id is None or notice_type not in {
            "group_increase",
            "group_decrease",
            "group_card",
        }:
            return

        group_id = str(group_id)
        if notice_type == "group_decrease":
            user_id = event.get("user_id")
            snapshot = self._group_member_cache.get(group_id)
            if snapshot is not None and user_id is not None:
                loaded_at, members = snapshot
                members.pop(str(user_id), None)
                self._group_member_cache[group_id] = (loaded_at, members)

        self._schedule_group_member_refresh(group_id, force=True)

    def run(self) -> Awaitable[Any]:
        self._group_member_cache_shutdown = False
        self._event_loop = asyncio.get_running_loop()
        if not self.host or not self.port:
            logger.warning(
                "aiocqhttp: 未配置 ws_reverse_host 或 ws_reverse_port，将使用默认值：http://0.0.0.0:6199",
            )
            self.host = "0.0.0.0"
            self.port = 6199

        coro = self.bot.run_task(
            host=self.host,
            port=int(self.port),
            shutdown_trigger=self.shutdown_trigger_placeholder,
        )

        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        logging.getLogger("aiocqhttp").setLevel(logging.ERROR)
        self.shutdown_event = asyncio.Event()
        return coro

    async def terminate(self) -> None:
        self._group_member_cache_shutdown = True
        background_tasks: list[asyncio.Task[None]] = []
        if self._group_member_prewarm_task is not None:
            background_tasks.append(self._group_member_prewarm_task)
        background_tasks.extend(self._group_member_refresh_tasks.values())
        for task in set(background_tasks):
            if not task.done():
                task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        self._group_member_refresh_tasks.clear()
        self._group_member_pending_refreshes.clear()
        self._group_member_refresh_retry_at.clear()
        self._group_member_prewarm_task = None
        self._group_member_cache.clear()
        self._event_loop = None

        if hasattr(self, "shutdown_event"):
            self.shutdown_event.set()
        await self._close_reverse_ws_connections()

    async def _close_reverse_ws_connections(self) -> None:
        api_clients = getattr(self.bot, "_wsr_api_clients", None)
        event_clients = getattr(self.bot, "_wsr_event_clients", None)

        ws_clients: set[Any] = set()
        if isinstance(api_clients, dict):
            ws_clients.update(api_clients.values())
        if isinstance(event_clients, set):
            ws_clients.update(event_clients)

        close_tasks: list[Awaitable[Any]] = []
        for ws in ws_clients:
            close_func = getattr(ws, "close", None)
            if not callable(close_func):
                continue
            try:
                close_result = close_func(code=1000, reason="Adapter shutdown")
            except TypeError:
                close_result = close_func()
            except Exception:
                continue

            if inspect.isawaitable(close_result):
                close_tasks.append(close_result)

        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)

        if isinstance(api_clients, dict):
            api_clients.clear()
        if isinstance(event_clients, set):
            event_clients.clear()

    async def shutdown_trigger_placeholder(self) -> None:
        await self.shutdown_event.wait()
        logger.info("aiocqhttp 适配器已被关闭")

    def meta(self) -> PlatformMetadata:
        return self.metadata

    async def handle_msg(self, message: AstrBotMessage) -> None:
        message_event = AiocqhttpMessageEvent(
            message_str=message.message_str,
            message_obj=message,
            platform_meta=self.meta(),
            session_id=message.session_id,
            bot=self.bot,
        )
        logger.debug(f"Handling message: {message_event.message_obj}")
        self.commit_event(message_event)

    def get_client(self) -> CQHttp:
        return self.bot
