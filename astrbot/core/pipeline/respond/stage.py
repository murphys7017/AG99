import asyncio
from collections.abc import AsyncGenerator
from copy import copy

from astrbot.core import logger
from astrbot.core.interaction.output_modes import OutputOrigin, temporary_output_origin
from astrbot.core.interaction.turn_state import (
    begin_interaction_turn_finalization_deferral,
    cancel_interaction_turn_finalization_deferral,
    get_interaction_turn_state,
)
from astrbot.core.message.components import ComponentType
from astrbot.core.message.message_chain_delivery import deliver_message_chain
from astrbot.core.message.message_event_result import ResultContentType
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.postprocess import dispatch_postprocess
from astrbot.core.postprocess.types import PostProcessTrigger
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.star_handler import EventType

from ..context import PipelineContext, call_event_hook
from ..stage import Stage, register_stage


@register_stage
class RespondStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        self.config = ctx.astrbot_config
        self.platform_settings: dict = self.config.get("platform_settings", {})

    async def _dispatch_after_message_sent(self, event: AstrMessageEvent) -> bool:
        if await call_event_hook(event, EventType.OnAfterMessageSentEvent):
            await self._cancel_interaction_turn_finalization(
                event,
                reason="after_message_sent_hook_stopped",
            )
            return False

        await self._complete_visible_turn(event)
        self._schedule_after_message_sent_postprocess(event)
        await self._flush_interaction_turn_finalization(event)
        return True

    @staticmethod
    async def _complete_visible_turn(event: AstrMessageEvent) -> None:
        await event.complete_visible_turn()

    @staticmethod
    async def _flush_interaction_turn_finalization(
        event: AstrMessageEvent,
    ) -> None:
        controller = event.get_extra("_interaction_output_controller")
        flush = getattr(type(controller), "flush_deferred_turn_finalization", None)
        if callable(flush):
            await flush(controller, event)

    @staticmethod
    async def _cancel_interaction_turn_finalization(
        event: AstrMessageEvent,
        *,
        reason: str,
    ) -> None:
        controller = event.get_extra("_interaction_output_controller")
        cancel = getattr(type(controller), "cancel_deferred_turn_finalization", None)
        if callable(cancel):
            await cancel(controller, event, reason=reason)

    @staticmethod
    async def _send_with_origin_and_extras(
        event: AstrMessageEvent,
        message,
        origin: str | None,
        platform_extras: dict,
    ) -> None:
        async def _send() -> None:
            if not platform_extras or event.get_extra("_interaction_enabled", False):
                await event.send(message)
                return
            await event.send_message_with_extras(
                message,
                platform_extras=platform_extras,
            )

        if origin is None:
            await _send()
            return
        with temporary_output_origin(event, origin):
            await _send()

    @staticmethod
    async def _send_stream_with_origin(
        event: AstrMessageEvent,
        async_stream,
        realtime_segmenting: bool,
        origin: str | None,
    ) -> None:
        if origin is None:
            await event.send_streaming(async_stream, realtime_segmenting)
            return
        with temporary_output_origin(event, origin):
            await event.send_streaming(async_stream, realtime_segmenting)

    @staticmethod
    def _result_output_origin(result) -> str | None:
        if result.result_content_type == ResultContentType.STREAMING_RESULT:
            return OutputOrigin.CORE.value
        if result.is_model_result():
            return OutputOrigin.CORE.value
        return None

    @staticmethod
    def _is_current_session_send_message_duplicate(result, event: AstrMessageEvent) -> bool:
        sent_plain_texts = event.get_extra(
            "_send_message_to_user_current_session_plain_texts",
            [],
        )
        if not isinstance(sent_plain_texts, list):
            return False
        result_plain_text = result.get_plain_text().strip()
        if not result_plain_text or result_plain_text not in sent_plain_texts:
            return False
        return all(
            comp.type
            in {
                ComponentType.Plain,
                ComponentType.Reply,
                ComponentType.At,
            }
            for comp in result.chain
        )

    def _schedule_after_message_sent_postprocess(
        self,
        event: AstrMessageEvent,
    ) -> None:
        self._schedule_postprocess(
            event,
            trigger=PostProcessTrigger.AFTER_MESSAGE_SENT,
            task_name=f"postprocess_after_message_sent_{event.get_platform_id()}",
        )
        if self._is_interaction_turn(event):
            return
        self._schedule_postprocess(
            event,
            trigger=PostProcessTrigger.AFTER_TURN_COMPLETED,
            task_name=f"postprocess_after_turn_completed_{event.get_platform_id()}",
        )

    @staticmethod
    def _is_interaction_turn(event: AstrMessageEvent) -> bool:
        return bool(event.get_extra("_interaction_enabled")) and (
            get_interaction_turn_state(event) is not None
        )

    def _schedule_postprocess(
        self,
        event: AstrMessageEvent,
        *,
        trigger: PostProcessTrigger,
        task_name: str,
    ) -> None:
        provider_request = self._snapshot_provider_request(
            event.get_extra("provider_request")
        )
        conversation = (
            provider_request.conversation
            if getattr(provider_request, "conversation", None) is not None
            else event.get_extra("conversation")
        )
        task = asyncio.create_task(
            dispatch_postprocess(
                event=event,
                trigger=trigger,
                plugin_context=self.ctx.plugin_manager.context,
                provider_request=provider_request,
                conversation=copy(conversation) if conversation is not None else None,
                turn_id=str(event.get_extra("_turn_id", "") or ""),
                visible_outputs=[
                    dict(item)
                    for item in event.get_extra("_visible_turn_outputs", [])
                    if isinstance(item, dict)
                ],
                turn_material=(
                    dict(material)
                    if isinstance(
                        material := event.get_extra(
                            "_interaction_finalized_turn_material"
                        ),
                        dict,
                    )
                    else None
                ),
            ),
            name=task_name,
        )
        task.add_done_callback(
            lambda done_task: self._log_postprocess_failure(trigger, done_task)
        )

    @staticmethod
    def _snapshot_provider_request(
        provider_request: ProviderRequest | None,
    ) -> ProviderRequest | None:
        if not isinstance(provider_request, ProviderRequest):
            return None
        snapshot = copy(provider_request)
        snapshot.image_urls = list(provider_request.image_urls or [])
        snapshot.audio_urls = list(provider_request.audio_urls or [])
        snapshot.extra_user_content_parts = list(
            provider_request.extra_user_content_parts or []
        )
        snapshot.contexts = [
            dict(item) if isinstance(item, dict) else item
            for item in (provider_request.contexts or [])
        ]
        if isinstance(provider_request.tool_calls_result, list):
            snapshot.tool_calls_result = list(provider_request.tool_calls_result)
        if provider_request.conversation is not None:
            snapshot.conversation = copy(provider_request.conversation)
        return snapshot

    @staticmethod
    def _log_postprocess_failure(
        trigger: PostProcessTrigger,
        task: asyncio.Task,
    ) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            logger.debug("postprocess(%s): background task cancelled", trigger.value)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "postprocess(%s): background task failed: %s",
                trigger.value,
                exc,
                exc_info=True,
            )

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> None | AsyncGenerator[None, None]:
        result = event.get_result()
        if result is None:
            return
        if event.get_extra("_streaming_finished", False):
            # prevent some plugin make result content type to LLM_RESULT after streaming finished, lead to send again
            return
        if result.result_content_type == ResultContentType.STREAMING_FINISH:
            event.set_extra("_streaming_finished", True)
            return
        if self._is_current_session_send_message_duplicate(result, event):
            logger.info(
                "send_message_to_user already delivered the same text in this session; skipping duplicate respond-stage delivery.",
            )
            return

        logger.debug(
            f"Prepare to send - {event.get_sender_name()}/{event.get_sender_id()}: {event._outline_chain(result.chain)}",
        )

        sent_any = False
        if result.result_content_type == ResultContentType.STREAMING_RESULT:
            if result.async_stream is None:
                logger.warning("async_stream 为空，跳过发送。")
                return
            # 流式结果直接交付平台适配器处理
            realtime_segmenting = (
                self.config.get("provider_settings", {}).get(
                    "unsupported_streaming_strategy",
                    "realtime_segmenting",
                )
                == "realtime_segmenting"
            )
            logger.debug(f"应用流式输出({event.get_platform_id()})")
            deferred = self._begin_interaction_finalization_deferral(event)
            try:
                await self._send_stream_with_origin(
                    event,
                    result.async_stream,
                    realtime_segmenting,
                    self._result_output_origin(result),
                )
                sent_any = True
                await self._dispatch_after_message_sent(event)
            finally:
                if deferred:
                    cancel_interaction_turn_finalization_deferral(event)
            return
        if len(result.chain) > 0:
            output_origin = self._result_output_origin(result)
            deferred = self._begin_interaction_finalization_deferral(event)
            try:
                sent_any = await deliver_message_chain(
                    event,
                    result.derive(result.chain),
                    send_message=lambda chain, extras: (
                        self._send_with_origin_and_extras(
                            event,
                            chain,
                            output_origin,
                            extras,
                        )
                    ),
                    platform_settings=self.platform_settings,
                    result_is_model_result=result.is_model_result(),
                )

                if event.get_extra("_interaction_pipeline_output_suppressed", False):
                    event.set_extra("_interaction_pipeline_output_suppressed", False)
                    sent_any = False

                if not sent_any:
                    event.clear_result()
                    return

                if not await self._dispatch_after_message_sent(event):
                    return
            finally:
                if deferred:
                    cancel_interaction_turn_finalization_deferral(event)

        if not sent_any:
            event.clear_result()
            return

        event.clear_result()

    def _begin_interaction_finalization_deferral(
        self,
        event: AstrMessageEvent,
    ) -> bool:
        if not self._is_interaction_turn(event):
            return False
        return begin_interaction_turn_finalization_deferral(event)
