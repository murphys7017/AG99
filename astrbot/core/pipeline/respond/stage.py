from collections.abc import AsyncGenerator

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
from astrbot.core.output_lifecycle import TurnDeliveryCoordinator
from astrbot.core.platform.astr_message_event import (
    INTERACTION_OUTPUT_CONTROLLER_EXTRA_KEY,
    AstrMessageEvent,
)

from ..context import PipelineContext
from ..stage import Stage, register_stage


@register_stage
class RespondStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        self.config = ctx.astrbot_config
        self.platform_settings: dict = self.config.get("platform_settings", {})
        self.delivery_coordinator = (
            ctx.turn_delivery_coordinator or TurnDeliveryCoordinator()
        )

    async def _dispatch_after_message_sent(self, event: AstrMessageEvent) -> bool:
        controller = event.get_extra(INTERACTION_OUTPUT_CONTROLLER_EXTRA_KEY)
        complete_visible_delivery = getattr(
            type(controller),
            "complete_visible_delivery",
            None,
        )
        if callable(complete_visible_delivery):
            return await complete_visible_delivery(controller, event)

        return await self.delivery_coordinator.complete_visible_delivery(
            event,
            plugin_context=self.ctx.plugin_manager.context,
            is_interaction_turn=self._is_interaction_turn(event),
        )

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

    @staticmethod
    def _is_interaction_turn(event: AstrMessageEvent) -> bool:
        return bool(event.get_extra("_interaction_enabled")) and (
            get_interaction_turn_state(event) is not None
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
