from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, aclosing

from astrbot import logger
from astrbot.core.interaction.group_reply import is_group_reply_candidate
from astrbot.core.interaction.personal_runtime import (
    PersonalRuntimeManager,
    PlatformEventSubmission,
)
from astrbot.core.interaction.turn_context import PersonalTurnContext
from astrbot.core.interaction.types import InteractionRouteMode
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.star_handler import StarHandlerMetadata

from ..context import PipelineContext
from ..stage import Stage, register_stage
from ..waking_check.stage import discover_activated_handlers
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
        *,
        ensure_yield: bool = False,
    ) -> AsyncGenerator[None, None]:
        await self._run_interaction_before_core_agent(event)
        if event.is_stopped():
            return
        yielded = False
        agent_source = self.agent_sub_stage.process(event)
        async with aclosing(agent_source):
            async for _ in agent_source:
                yielded = True
                yield
        if ensure_yield and not yielded:
            yield

    async def _run_admitted_turn(
        self,
        event: AstrMessageEvent,
        *,
        activated_handlers: list[StarHandlerMetadata],
        is_group_candidate: bool,
        submission: PlatformEventSubmission | None,
    ) -> AsyncGenerator[None, None]:
        group_candidate_admitted = False
        if is_group_candidate:
            middleware = self.ctx.interaction_middleware
            if middleware is None:
                event.stop_event()
                return
            route = await middleware.admit_group_reply_candidate(event)
            if route.route_mode is InteractionRouteMode.SILENT:
                await middleware.handle_pipeline_event(event)
                return
            group_candidate_admitted = True
            await discover_activated_handlers(
                event,
                config=self.config,
                disable_builtin_commands=bool(
                    self.config.get("disable_builtin_commands", False)
                ),
                no_permission_reply=bool(
                    self.config.get("platform_settings", {}).get(
                        "no_permission_reply",
                        True,
                    )
                ),
            )
            if event.is_stopped():
                return
            activated_handlers = event.get_extra("activated_handlers", [])

        # 有插件 Handler 被激活
        if activated_handlers:
            middleware = self.ctx.interaction_middleware
            output_controller = (
                middleware.output_controller if middleware is not None else None
            )
            event.set_extra(
                "_interaction_plugin_output_transaction_active",
                True,
            )
            delegated_to_core = False
            try:
                plugin_source = self.star_request_sub_stage.process(event)
                async with aclosing(plugin_source):
                    async for resp in plugin_source:
                        if isinstance(resp, ProviderRequest):
                            # Handler 的 LLM 请求。此前可见插件输出是进度，不拥有最终 turn。
                            delegated_to_core = True
                            if output_controller is not None:
                                await output_controller.finalize_plugin_output_transaction(
                                    event,
                                    delegated_to_core=True,
                                )
                            event.set_extra("provider_request", resp)
                            if submission is not None:
                                submission.set_provider_request(resp)
                            agent_source = self._run_agent_turn(
                                event,
                                ensure_yield=True,
                            )
                            async with aclosing(agent_source):
                                async for _ in agent_source:
                                    yield
                            continue
                        yield
            finally:
                if output_controller is not None and not delegated_to_core:
                    await output_controller.finalize_plugin_output_transaction(
                        event,
                        delegated_to_core=False,
                    )
            if delegated_to_core:
                return

        # A Handler may decide asynchronously that an otherwise passive
        # group message is worth evaluating. The plugin proposes; Router
        # still owns reply admission and may fail closed to silence.
        if (
            not is_group_candidate
            and not event.is_stopped()
            and not event._has_send_oper
            and is_group_reply_candidate(event)
        ):
            middleware = self.ctx.interaction_middleware
            if middleware is None:
                event.stop_event()
                return
            route = await middleware.admit_group_reply_candidate(event)
            if route.route_mode is InteractionRouteMode.SILENT:
                await middleware.handle_pipeline_event(event)
                if not event.is_stopped():
                    event.stop_event()
                return
            group_candidate_admitted = True

        # 调用 LLM 相关请求
        if not self.ctx.astrbot_config["provider_settings"].get(
            "enable",
            True,
        ):
            return

        if (
            not event._has_send_oper
            and (event.is_at_or_wake_command or group_candidate_admitted)
            and not event.call_llm
        ):
            # 是否有过发送操作 and 是否是被 @ 或者通过唤醒前缀
            if (
                event.get_result() and not event.is_stopped()
            ) or not event.get_result():
                agent_source = self._run_agent_turn(event)
                async with aclosing(agent_source):
                    async for _ in agent_source:
                        yield

    @staticmethod
    async def _iterate_with_active_turn(
        source: AsyncGenerator[None, None],
        manager: PersonalRuntimeManager,
        turn: PersonalTurnContext,
    ) -> AsyncGenerator[None, None]:
        """Activate a turn only while advancing or closing the inner generator."""
        try:
            while True:
                try:
                    with manager.activate_turn(turn):
                        item = await anext(source)
                except StopAsyncIteration:
                    return
                yield item
        finally:
            with manager.activate_turn(turn):
                await source.aclose()

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> None | AsyncGenerator[None, None]:
        """处理事件"""
        activated_handlers: list[StarHandlerMetadata] = event.get_extra(
            "activated_handlers",
            [],
        )
        is_group_candidate = is_group_reply_candidate(event)
        self._prepare_interaction_output(event)
        manager: PersonalRuntimeManager | None = getattr(
            self,
            "personal_runtime_manager",
            None,
        )
        async with AsyncExitStack() as stack:
            submission = (
                await stack.enter_async_context(
                    manager.submit_platform_event(
                        event,
                        self.ctx.astrbot_config_id,
                        self.plugin_manager.context,
                        self.config,
                    )
                )
                if manager is not None
                else None
            )
            if event.is_stopped():
                return
            lease = None
            turn = None
            if submission is not None:
                admission = await submission.admit(
                    allow_follow_up=not bool(activated_handlers),
                )
                if admission.consumed_as_follow_up:
                    event.set_extra("_personal_runtime_follow_up_consumed", True)
                    logger.info(
                        "Personal Runtime consumed message as active-runner follow-up: session_id=%s",
                        event.unified_msg_origin,
                    )
                    return
                lease = admission.lease
                turn = admission.turn

            source = self._run_admitted_turn(
                event,
                activated_handlers=activated_handlers,
                is_group_candidate=is_group_candidate,
                submission=submission,
            )
            try:
                if manager is not None and turn is not None:
                    active_source = self._iterate_with_active_turn(
                        source,
                        manager,
                        turn,
                    )
                    async with aclosing(active_source):
                        async for item in active_source:
                            yield item
                else:
                    async with aclosing(source):
                        async for item in source:
                            yield item
            finally:
                if lease is not None:
                    if manager is not None and turn is not None:
                        with manager.activate_turn(turn):
                            await lease.release()
                    else:
                        await lease.release()
