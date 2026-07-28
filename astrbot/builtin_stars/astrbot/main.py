import copy
from sys import maxsize

import astrbot.api.message_components as Comp
from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core import logger
from astrbot.core.utils.session_waiter import (
    FILTERS,
    USER_SESSIONS,
    SessionController,
    SessionWaiter,
    session_waiter,
)

from .group_chat_context import GroupChatContext


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context
        self.group_chat_context = None
        try:
            self.group_chat_context = GroupChatContext(
                self.context.astrbot_config_mgr,
                self.context,
            )
            self.context.register_prompt_extension_collector(self.group_chat_context)
        except BaseException as e:
            logger.error(f"聊天增强 err: {e}")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=maxsize)
    async def handle_session_control_agent(self, event: AstrMessageEvent) -> None:
        """会话控制代理"""
        for session_filter in FILTERS:
            session_id = session_filter.filter(event)
            if session_id in USER_SESSIONS:
                await SessionWaiter.trigger(session_id, event)
                event.stop_event()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=maxsize - 1)
    async def handle_empty_mention(self, event: AstrMessageEvent):
        """处理只有一个 @ 或仅有唤醒前缀的消息，并等待用户下一条内容。"""
        try:
            messages = event.get_messages()
            cfg = self.context.get_config(umo=event.unified_msg_origin)
            p_settings = cfg["platform_settings"]
            wake_prefix = cfg.get("wake_prefix", [])
            if len(messages) != 1:
                return

            is_empty_mention = (
                isinstance(messages[0], Comp.At)
                and str(messages[0].qq) == str(event.get_self_id())
                and p_settings.get("empty_mention_waiting", True)
            )
            is_wake_prefix_only = (
                isinstance(messages[0], Comp.Plain)
                and messages[0].text.strip() in wake_prefix
            )

            if not (is_empty_mention or is_wake_prefix_only):
                return

            if p_settings.get("empty_mention_waiting_need_reply", True):
                try:
                    curr_cid = await self.context.conversation_manager.get_curr_conversation_id(
                        event.unified_msg_origin,
                    )
                    conversation = None

                    if curr_cid:
                        conversation = (
                            await self.context.conversation_manager.get_conversation(
                                event.unified_msg_origin,
                                curr_cid,
                            )
                        )
                    else:
                        curr_cid = (
                            await self.context.conversation_manager.new_conversation(
                                event.unified_msg_origin,
                                platform_id=event.get_platform_id(),
                            )
                        )

                    yield event.request_llm(
                        prompt=(
                            "注意，你正在社交媒体上中与用户进行聊天，用户只是通过@来唤醒你，但并未在这条消息中输入内容，他可能会在接下来一条发送他想发送的内容。"
                            "你友好地询问用户想要聊些什么或者需要什么帮助，回复要符合人设，不要太过机械化。"
                            "请注意，你仅需要输出要回复用户的内容，不要输出其他任何东西"
                        ),
                        session_id=curr_cid,
                        contexts=[],
                        system_prompt="",
                        conversation=conversation,
                    )
                except Exception as e:
                    logger.error(f"LLM response failed: {e!s}")
                    yield event.plain_result("想要问什么呢？😄")

            @session_waiter(60)
            async def empty_mention_waiter(
                controller: SessionController,
                event: AstrMessageEvent,
            ) -> None:
                if not event.message_str or not event.message_str.strip():
                    return
                event.message_obj.message.insert(
                    0,
                    Comp.At(qq=event.get_self_id(), name=event.get_self_id()),
                )
                new_event = copy.copy(event)
                self.context.get_event_queue().put_nowait(new_event)
                event.stop_event()
                controller.stop()

            try:
                await empty_mention_waiter(event)
            except TimeoutError:
                pass
            except Exception as e:
                yield event.plain_result("发生错误，请联系管理员: " + str(e))
            finally:
                event.stop_event()
        except Exception as e:
            logger.error("handle_empty_mention error: " + str(e))

    def ltm_enabled(self, event: AstrMessageEvent):
        ltmse = self.context.get_config(umo=event.unified_msg_origin).get(
            "provider_ltm_settings",
            {},
        )
        return bool(ltmse.get("group_icl_enable", False))

    @filter.on_llm_request()
    async def preserve_group_context_for_external_agent(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        """Preserve group context for official runners outside the Core pipeline."""
        if not self.group_chat_context:
            return
        try:
            await self.group_chat_context.decorate_external_agent_request(event, req)
        except BaseException as exc:
            logger.error("Failed to add group context to external agent request: %s", exc)

    @filter.on_llm_response()
    async def record_llm_resp_to_ltm(
        self, event: AstrMessageEvent, resp: LLMResponse
    ) -> None:
        """在 LLM 响应后记录对话"""
        del event, resp

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent) -> None:
        """消息发送后处理"""
        if self.group_chat_context and self.ltm_enabled(event):
            try:
                clean_session = event.get_extra("_clean_group_context_session", False)
                clean_session = clean_session or event.get_extra(
                    "_clean_ltm_session",
                    False,
                )
                if clean_session:
                    await self.group_chat_context.remove_session(event)
                else:
                    await self.group_chat_context.mark_reply_sent(event)
            except Exception as e:
                logger.error(f"ltm: {e}")
