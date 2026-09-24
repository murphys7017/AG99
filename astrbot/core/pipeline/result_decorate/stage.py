import random
import re
import time
import traceback
from collections.abc import Mapping
from dataclasses import dataclass

from astrbot.core import file_token_service, html_renderer, logger
from astrbot.core.interaction.turn_state import get_interaction_turn_state
from astrbot.core.message.components import At, Image, Json, Node, Plain, Record, Reply
from astrbot.core.message.message_event_result import ResultContentType
from astrbot.core.output_lifecycle import PreOutputProcessor
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.message_type import MessageType
from astrbot.core.star.session_llm_manager import SessionServiceManager
from astrbot.core.voice import (
    VoiceServiceError,
    build_tts_delivery_metadata,
    synthesize_text,
)

from ..context import PipelineContext
from ..runtime_config import get_pipeline_turn_runtime_config
from ..stage import Stage, register_stage


@dataclass(frozen=True, slots=True)
class _OutputDecorationPolicy:
    reply_prefix: str
    reply_with_mention: bool
    reply_with_quote: bool
    t2i_enabled: bool
    t2i_word_threshold: int
    t2i_use_network: bool
    t2i_active_template: str
    t2i_use_file_service: bool
    callback_api_base: str
    forward_threshold: int
    tts_enabled: bool
    tts_trigger_probability: float
    tts_use_file_service: bool
    tts_dual_output: bool
    show_reasoning: bool
    enable_segmented_reply: bool
    words_count_threshold: int
    only_llm_result: bool
    split_mode: str
    regex: str
    split_words: tuple[str, ...]
    content_cleanup_rule: str


@register_stage
class ResultDecorateStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        self.pre_output_processor = ctx.pre_output_processor or PreOutputProcessor()

    @staticmethod
    def _coerce_int(value: object, *, default: int, minimum: int = 0) -> int:
        try:
            return max(int(value), minimum)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _resolve_policy(cls, runtime_config: Mapping[str, object]) -> _OutputDecorationPolicy:
        platform_settings = runtime_config.get("platform_settings", {})
        if not isinstance(platform_settings, Mapping):
            platform_settings = {}
        segmented_reply = platform_settings.get("segmented_reply", {})
        if not isinstance(segmented_reply, Mapping):
            segmented_reply = {}
        tts_settings = runtime_config.get("provider_tts_settings", {})
        if not isinstance(tts_settings, Mapping):
            tts_settings = {}
        provider_settings = runtime_config.get("provider_settings", {})
        if not isinstance(provider_settings, Mapping):
            provider_settings = {}
        split_words = segmented_reply.get("split_words", ["。", "？", "！", "~", "…"])
        if not isinstance(split_words, list):
            split_words = []
        try:
            tts_trigger_probability = max(
                0.0,
                min(float(tts_settings.get("trigger_probability", 1)), 1.0),
            )
        except (TypeError, ValueError):
            tts_trigger_probability = 1.0
        return _OutputDecorationPolicy(
            reply_prefix=str(platform_settings.get("reply_prefix", "") or ""),
            reply_with_mention=bool(platform_settings.get("reply_with_mention", False)),
            reply_with_quote=bool(platform_settings.get("reply_with_quote", False)),
            t2i_enabled=bool(runtime_config.get("t2i", False)),
            t2i_word_threshold=cls._coerce_int(
                runtime_config.get("t2i_word_threshold", 150),
                default=150,
                minimum=50,
            ),
            t2i_use_network=runtime_config.get("t2i_strategy") == "remote",
            t2i_active_template=str(runtime_config.get("t2i_active_template", "") or ""),
            t2i_use_file_service=bool(runtime_config.get("t2i_use_file_service", False)),
            callback_api_base=str(runtime_config.get("callback_api_base", "") or ""),
            forward_threshold=cls._coerce_int(
                platform_settings.get("forward_threshold", 0), default=0
            ),
            tts_enabled=bool(tts_settings.get("enable", False)),
            tts_trigger_probability=tts_trigger_probability,
            tts_use_file_service=bool(tts_settings.get("use_file_service", False)),
            tts_dual_output=bool(tts_settings.get("dual_output", False)),
            show_reasoning=bool(provider_settings.get("display_reasoning_text", False)),
            enable_segmented_reply=bool(segmented_reply.get("enable", False)),
            words_count_threshold=cls._coerce_int(
                segmented_reply.get("words_count_threshold", 0), default=0
            ),
            only_llm_result=bool(segmented_reply.get("only_llm_result", False)),
            split_mode=str(segmented_reply.get("split_mode", "regex") or "regex"),
            regex=str(segmented_reply.get("regex", "") or ""),
            split_words=tuple(str(word) for word in split_words if str(word)),
            content_cleanup_rule=str(
                segmented_reply.get("content_cleanup_rule", "") or ""
            ),
        )

    @staticmethod
    def _split_text_by_words(
        text: str,
        *,
        split_words: tuple[str, ...],
        split_words_pattern: re.Pattern[str] | None,
    ) -> list[str]:
        """使用分段词列表分段文本"""
        if not split_words_pattern:
            return [text]

        segments = split_words_pattern.findall(text)
        result = []
        for seg in segments:
            if isinstance(seg, tuple):
                content = seg[0]
                if not isinstance(content, str):
                    continue
                for word in split_words:
                    if content.endswith(word):
                        content = content[: -len(word)]
                        break
                if content.strip():
                    result.append(content)
            elif seg and seg.strip():
                result.append(seg)
        return result if result else [text]

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> None:
        result = event.get_result()
        if result is None or not result.chain:
            return

        if result.result_content_type == ResultContentType.STREAMING_RESULT:
            return

        is_stream = result.result_content_type == ResultContentType.STREAMING_FINISH

        if self._is_interaction_turn(event) and result.is_model_result():
            logger.debug(
                "Interaction model result defers response safety and decorating hooks to the shared pre-output processor."
            )
            return

        if (
            result.is_llm_result()
            and not is_stream
            and not self.pre_output_processor.response_is_safe(event, result)
        ):
            return

        if await self.pre_output_processor.run_decorating_hooks(
            event,
            is_stream=is_stream,
        ):
            return

        if self._is_interaction_turn(event):
            logger.debug(
                "Interaction turn preserves response safety and decorating hooks, then skips ordinary result decoration."
            )
            return

        # 流式输出不执行下面的逻辑
        if is_stream:
            logger.info("流式输出已启用，跳过结果装饰阶段")
            return

        # 需要再获取一次。插件可能直接对 chain 进行了替换。
        result = event.get_result()
        if result is None:
            return

        runtime_config = get_pipeline_turn_runtime_config(
            event,
            self.ctx.astrbot_config,
        )
        policy = self._resolve_policy(runtime_config)
        split_words_pattern = None
        if policy.split_words:
            escaped_words = sorted(
                (re.escape(word) for word in policy.split_words),
                key=len,
                reverse=True,
            )
            split_words_pattern = re.compile(
                f"(.*?({'|'.join(escaped_words)})|.+$)", re.DOTALL
            )

        if len(result.chain) > 0:
            # 回复前缀
            if policy.reply_prefix:
                for comp in result.chain:
                    if isinstance(comp, Plain):
                        comp.text = policy.reply_prefix + comp.text
                        break

            # 分段回复
            if policy.enable_segmented_reply and event.get_platform_name() not in [
                "qq_official",
                "weixin_official_account",
                "dingtalk",
            ]:
                if (
                    policy.only_llm_result and result.is_model_result()
                ) or not policy.only_llm_result:
                    new_chain = []
                    for comp in result.chain:
                        if isinstance(comp, Plain):
                            if len(comp.text) > policy.words_count_threshold:
                                # 不分段回复
                                new_chain.append(comp)
                                continue

                            # 根据 split_mode 选择分段方式
                            if policy.split_mode == "words":
                                split_response = self._split_text_by_words(
                                    comp.text,
                                    split_words=policy.split_words,
                                    split_words_pattern=split_words_pattern,
                                )
                            else:  # regex 模式
                                try:
                                    split_response = re.findall(
                                        policy.regex,
                                        comp.text,
                                        re.DOTALL | re.MULTILINE,
                                    )
                                except re.error:
                                    logger.error(
                                        f"分段回复正则表达式错误，使用默认分段方式: {traceback.format_exc()}",
                                    )
                                    split_response = re.findall(
                                        r".*?[。？！~…]+|.+$",
                                        comp.text,
                                        re.DOTALL | re.MULTILINE,
                                    )

                            if not split_response:
                                new_chain.append(comp)
                                continue
                            for seg in split_response:
                                if policy.content_cleanup_rule:
                                    try:
                                        seg = re.sub(policy.content_cleanup_rule, "", seg)
                                    except re.error:
                                        logger.error(
                                            f"分段回复过滤表达式失败，无法成功过滤：{traceback.format_exc()}"
                                        )
                                seg = seg.strip()
                                if seg:
                                    new_chain.append(Plain(seg))
                        else:
                            # 非 Plain 类型的消息段不分段
                            new_chain.append(comp)
                    result.chain = new_chain

            # TTS
            should_attempt_tts = (
                policy.tts_enabled
                and result.is_llm_result()
                and await SessionServiceManager.should_process_tts_request(event)
                and random.random() <= policy.tts_trigger_probability
            )
            if (
                not should_attempt_tts
                and policy.show_reasoning
                and event.get_extra("_llm_reasoning_content")
            ):
                # inject reasoning content to chain
                reasoning_content = str(event.get_extra("_llm_reasoning_content"))
                if event.get_platform_name() == "lark":
                    result.chain.insert(
                        0,
                        Json(
                            data={
                                "type": "lark_collapsible_panel_reasoning",
                                "title": "💭 Thinking",
                                "expanded": False,
                                "content": reasoning_content,
                            },
                        ),
                    )
                else:
                    result.chain.insert(
                        0, Plain(f"🤔 思考: {reasoning_content}\n\n────\n")
                    )

            if should_attempt_tts:
                new_chain = []
                turn_id = str(
                    event.get_extra("_turn_id")
                    or event.message_obj.message_id
                    or event.unified_msg_origin
                )
                for index, comp in enumerate(result.chain, start=1):
                    if isinstance(comp, Plain) and len(comp.text) > 1:
                        try:
                            logger.info(f"TTS 请求: {comp.text}")
                            tts_result = await synthesize_text(
                                self.ctx.plugin_manager.context,
                                event,
                                comp.text,
                                stage="pipeline.result_decorate_tts",
                                use_file_service=policy.tts_use_file_service,
                                callback_api_base=policy.callback_api_base,
                                turn_id=turn_id,
                                message_id=(
                                    f"{turn_id}::pipeline_tts::{index:04d}"
                                ),
                            )
                            logger.info(f"TTS 结果: {tts_result.audio_path}")
                            if tts_result.audio_url:
                                logger.debug(f"已注册：{tts_result.audio_url}")

                            new_chain.append(
                                Record(
                                    file=tts_result.delivered_file,
                                    url=tts_result.delivered_file,
                                    text=tts_result.text,
                                    delivery_metadata=build_tts_delivery_metadata(
                                        tts_result.state,
                                        audio_attachment="present",
                                    ),
                                ),
                            )
                            if policy.tts_dual_output:
                                new_chain.append(
                                    Plain(
                                        comp.text,
                                        delivery_metadata=build_tts_delivery_metadata(
                                            tts_result.state,
                                            audio_attachment="absent",
                                        ),
                                    )
                                )
                        except VoiceServiceError as exc:
                            if exc.reason == "provider_unavailable":
                                logger.warning(
                                    f"会话 {event.unified_msg_origin} 未配置文本转语音模型。",
                                )
                            else:
                                logger.error(traceback.format_exc())
                                logger.error("TTS 失败，发送 audio.state=failed。")
                            new_chain.append(
                                Plain(
                                    comp.text,
                                    delivery_metadata=(
                                        build_tts_delivery_metadata(
                                            exc.state,
                                            audio_attachment="absent",
                                        )
                                        if exc.state is not None
                                        else {}
                                    ),
                                )
                            )
                        except Exception:
                            logger.error(traceback.format_exc())
                            logger.error("TTS 输出物化失败，保留文本输出。")
                            new_chain.append(comp)
                    else:
                        new_chain.append(comp)
                result.chain = new_chain

            # 文本转图片
            elif (
                result.use_t2i_ is None and policy.t2i_enabled
            ) or result.use_t2i_:
                parts = []
                for comp in result.chain:
                    if isinstance(comp, Plain):
                        parts.append("\n\n" + comp.text)
                    else:
                        break
                plain_str = "".join(parts)
                if plain_str and len(plain_str) > policy.t2i_word_threshold:
                    render_start = time.time()
                    try:
                        url = await html_renderer.render_t2i(
                            plain_str,
                            return_url=True,
                            use_network=policy.t2i_use_network,
                            template_name=policy.t2i_active_template,
                        )
                    except BaseException:
                        logger.error("文本转图片失败，使用文本发送。")
                        return
                    if time.time() - render_start > 3:
                        logger.warning(
                            "文本转图片耗时超过了 3 秒，如果觉得很慢可以在 WebUI 中关闭文本转图片模式。",
                        )
                    if url:
                        if url.startswith("http"):
                            result.chain = [Image.fromURL(url)]
                        elif (
                            policy.t2i_use_file_service
                            and policy.callback_api_base
                        ):
                            token = await file_token_service.register_file(url)
                            url = f"{policy.callback_api_base}/api/file/{token}"
                            logger.debug(f"已注册：{url}")
                            result.chain = [Image.fromURL(url)]
                        else:
                            result.chain = [Image.fromFileSystem(url)]

            # 触发转发消息
            if event.get_platform_name() == "aiocqhttp":
                word_cnt = 0
                for comp in result.chain:
                    if isinstance(comp, Plain):
                        word_cnt += len(comp.text)
                if word_cnt > policy.forward_threshold:
                    node = Node(
                        uin=event.get_self_id(),
                        name="AstrBot",
                        content=[*result.chain],
                    )
                    result.chain = [node]

            # at 回复 / 引用回复仅适用于纯文本或图文消息
            can_decorate = all(
                isinstance(item, (Plain, Image)) for item in result.chain
            )
            if can_decorate:
                # at 回复
                if (
                    policy.reply_with_mention
                    and event.get_message_type() != MessageType.FRIEND_MESSAGE
                ):
                    result.chain.insert(
                        0,
                        At(qq=event.get_sender_id(), name=event.get_sender_name()),
                    )
                    if len(result.chain) > 1 and isinstance(result.chain[1], Plain):
                        result.chain[1].text = "\n" + result.chain[1].text

                # 引用回复
                if policy.reply_with_quote:
                    result.chain.insert(0, Reply(id=event.message_obj.message_id))

    @staticmethod
    def _is_interaction_turn(event: AstrMessageEvent) -> bool:
        return bool(event.get_extra("_interaction_enabled")) and (
            get_interaction_turn_state(event) is not None
        )
