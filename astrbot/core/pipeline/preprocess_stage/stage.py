import asyncio
import random
import traceback
from collections.abc import AsyncGenerator

from astrbot.core import logger
from astrbot.core.message.components import Image, Plain, Record, Reply
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.utils.media_utils import ensure_wav
from astrbot.core.voice import (
    VoiceServiceError,
    resolve_stt_provider,
    transcribe_record,
)

from ..context import PipelineContext
from ..stage import Stage, register_stage


@register_stage
class PreProcessStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        self.config = ctx.astrbot_config
        self.plugin_manager = ctx.plugin_manager

        self.stt_settings: dict = self.config.get("provider_stt_settings", {})
        self.platform_settings: dict = self.config.get("platform_settings", {})

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> None | AsyncGenerator[None, None]:
        """在处理事件之前的预处理"""
        # 平台特异配置：platform_specific.<platform>.pre_ack_emoji
        supported = {"telegram", "lark", "discord"}
        platform = event.get_platform_name()
        cfg = (
            self.config.get("platform_specific", {})
            .get(platform, {})
            .get("pre_ack_emoji", {})
        ) or {}
        emojis = cfg.get("emojis") or []
        if (
            cfg.get("enable", False)
            and platform in supported
            and emojis
            and event.is_at_or_wake_command
        ):
            try:
                await event.react(random.choice(emojis))
            except Exception as e:
                logger.warning(f"{platform} 预回应表情发送失败: {e}")

        if event.get_extra("_interaction_inbound_media_materialized", False):
            return

        # 路径映射
        if mappings := self.platform_settings.get("path_mapping", []):
            # 支持 Record，Image 消息段的路径映射。
            message_chain = event.get_messages()

            for idx, component in enumerate(message_chain):
                if isinstance(component, Record | Image) and component.url:
                    for mapping in mappings:
                        from_, to_ = mapping.split(":")
                        from_ = from_.removesuffix("/")
                        to_ = to_.removesuffix("/")

                        url = component.url.removeprefix("file://")
                        if url.startswith(from_):
                            component.url = url.replace(from_, to_, 1)
                            logger.debug(f"路径映射: {url} -> {component.url}")
                    message_chain[idx] = component

        async def _materialize_record(record: Record) -> Record:
            original_path = await record.convert_to_file_path()
            record_path = await ensure_wav(original_path)
            if record_path != original_path:
                event.track_temporary_local_file(record_path)
            record.file = record_path
            record.path = record_path
            return record

        def _iter_reply_records(reply: Reply):
            if not reply.chain:
                return
            for idx, reply_comp in enumerate(reply.chain):
                if isinstance(reply_comp, Record):
                    yield idx, reply_comp

        # In here, we convert all Record components to wav format and update the file path.
        message_chain = event.get_messages()
        for idx, component in enumerate(message_chain):
            if isinstance(component, Record):
                try:
                    message_chain[idx] = await _materialize_record(component)
                except Exception as e:
                    logger.warning(f"Voice processing failed: {e}")
            elif isinstance(component, Reply) and component.chain:
                for reply_idx, reply_comp in _iter_reply_records(component):
                    try:
                        component.chain[reply_idx] = await _materialize_record(reply_comp)
                    except Exception as e:
                        logger.warning(f"Voice processing in reply chain failed: {e}")

        # STT
        if self.stt_settings.get("enable", False):
            ctx = self.plugin_manager.context
            try:
                stt_provider = resolve_stt_provider(
                    ctx,
                    event,
                    stage="pipeline.preprocess_stt",
                )
            except VoiceServiceError:
                logger.warning(
                    f"会话 {event.unified_msg_origin} 未配置语音转文本模型。",
                )
                return
            async def _transcribe_record_to_plain(
                record: Record,
                *,
                is_reply: bool = False,
            ) -> Plain | None:
                prefix = "引用消息" if is_reply else ""
                retry = 5
                for i in range(retry):
                    try:
                        result = await transcribe_record(
                            ctx,
                            event,
                            record,
                            provider=stt_provider,
                            stage="pipeline.preprocess_stt",
                        )
                        suffix = "(引用消息)" if is_reply else ""
                        logger.info(f"语音转文本{suffix}结果: " + result.text)
                        return Plain(result.text)
                    except VoiceServiceError as e:
                        if e.reason != "source_unavailable":
                            logger.error(traceback.format_exc())
                            logger.error(f"{prefix}语音转文本失败: {e}")
                            break
                        # napcat workaround
                        logger.warning(e)
                        logger.warning(f"重试中: {i + 1}/{retry}")
                        await asyncio.sleep(0.5)
                        continue
                    except BaseException as e:
                        logger.error(traceback.format_exc())
                        logger.error(f"{prefix}语音转文本失败: {e}")
                        break
                return None

            message_chain = event.get_messages()
            for idx, component in enumerate(message_chain):
                if isinstance(component, Record):
                    plain_comp = await _transcribe_record_to_plain(component)
                    if plain_comp:
                        message_chain[idx] = plain_comp
                        event.message_str += plain_comp.text
                        event.message_obj.message_str += plain_comp.text
                elif isinstance(component, Reply) and component.chain:
                    for reply_idx, reply_comp in _iter_reply_records(component):
                        plain_comp = await _transcribe_record_to_plain(
                            reply_comp,
                            is_reply=True,
                        )
                        if plain_comp:
                            component.chain[reply_idx] = plain_comp
                            event.message_str += plain_comp.text
                            event.message_obj.message_str += plain_comp.text
