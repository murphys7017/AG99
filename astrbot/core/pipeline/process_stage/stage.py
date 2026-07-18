from collections.abc import AsyncGenerator

from astrbot import logger
from astrbot.core.interaction.personal_runtime import (
    PendingTurnReservation,
    PersonalRuntimeManager,
    PersonalTurnLease,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.star_handler import StarHandlerMetadata

from ..context import PipelineContext
from ..stage import Stage, register_stage
from .method.agent_request import AgentRequestSubStage
from .method.star_request import StarRequestSubStage


@register_stage
class ProcessStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        self.config = ctx.astrbot_config
        self.plugin_manager = ctx.plugin_manager
        self.personal_runtime_manager = ctx.personal_runtime_manager

        # initialize agent sub stage
        self.agent_sub_stage = AgentRequestSubStage()
        await self.agent_sub_stage.initialize(ctx)

        # initialize star request sub stage
        self.star_request_sub_stage = StarRequestSubStage()
        await self.star_request_sub_stage.initialize(ctx)

    def _prepare_interaction_output(
        self,
        event: AstrMessageEvent,
    ) -> None:
        middleware = self.ctx.interaction_middleware
        if middleware is None:
            return
        middleware.prepare_pipeline_event(event)

    async def _run_interaction_before_core_agent(
        self,
        event: AstrMessageEvent,
    ) -> None:
        middleware = self.ctx.interaction_middleware
        if middleware is None:
            return
        await middleware.handle_pipeline_event(event)

    async def _run_agent_turn(
        self,
        event: AstrMessageEvent,
        reservation: PendingTurnReservation | None,
        *,
        allow_follow_up: bool,
        ensure_yield: bool = False,
    ) -> AsyncGenerator[None, None]:
        lease: PersonalTurnLease | None = None
        manager: PersonalRuntimeManager | None = getattr(
            self,
            "personal_runtime_manager",
            None,
        )
        if manager is not None and reservation is not None:
            await manager.bind(
                reservation,
                event,
                self.plugin_manager.context,
                self.config["provider_settings"],
            )
            admission = await manager.admit(
                reservation,
                event,
                allow_follow_up=allow_follow_up,
            )
            if admission.consumed_as_follow_up:
                event.set_extra("_personal_runtime_follow_up_consumed", True)
                logger.info(
                    "Personal Runtime consumed message as active-runner follow-up: session_id=%s",
                    event.unified_msg_origin,
                )
                return
            lease = admission.lease

        try:
            await self._run_interaction_before_core_agent(event)
            if event.is_stopped():
                return
            yielded = False
            async for _ in self.agent_sub_stage.process(event):
                yielded = True
                yield
            if ensure_yield and not yielded:
                yield
        finally:
            if lease is not None:
                await lease.release()

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> None | AsyncGenerator[None, None]:
        """处理事件"""
        activated_handlers: list[StarHandlerMetadata] = event.get_extra(
            "activated_handlers",
        )
        self._prepare_interaction_output(event)
        manager: PersonalRuntimeManager | None = getattr(
            self,
            "personal_runtime_manager",
            None,
        )
        reservation = (
            manager.reserve(event, self.ctx.astrbot_config_id)
            if manager is not None
            else None
        )
        try:
            if event.is_stopped():
                return
            # 有插件 Handler 被激活
            if activated_handlers:
                middleware = self.ctx.interaction_middleware
                output_controller = (
                    middleware.output_controller if middleware is not None else None
                )
                event.set_extra("_interaction_plugin_output_transaction_active", True)
                delegated_to_core = False
                try:
                    async for resp in self.star_request_sub_stage.process(event):
                        if isinstance(resp, ProviderRequest):
                            # Handler 的 LLM 请求。此前可见插件输出是进度，不拥有最终 turn。
                            delegated_to_core = True
                            if output_controller is not None:
                                await output_controller.finalize_plugin_output_transaction(
                                    event,
                                    delegated_to_core=True,
                                )
                            event.set_extra("provider_request", resp)
                            async for _ in self._run_agent_turn(
                                event,
                                reservation,
                                allow_follow_up=False,
                                ensure_yield=True,
                            ):
                                yield
                            return
                        yield
                finally:
                    if output_controller is not None and not delegated_to_core:
                        await output_controller.finalize_plugin_output_transaction(
                            event,
                            delegated_to_core=False,
                        )

            # 调用 LLM 相关请求
            if not self.ctx.astrbot_config["provider_settings"].get("enable", True):
                return

            if (
                not event._has_send_oper
                and event.is_at_or_wake_command
                and not event.call_llm
            ):
                # 是否有过发送操作 and 是否是被 @ 或者通过唤醒前缀
                if (
                    event.get_result() and not event.is_stopped()
                ) or not event.get_result():
                    async for _ in self._run_agent_turn(
                        event,
                        reservation,
                        allow_follow_up=True,
                    ):
                        yield
        finally:
            if manager is not None and reservation is not None:
                manager.settle(reservation, event)
