import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, aclosing

from astrbot import logger
from astrbot.core.deadline import TurnDeadlineBudget, TurnDeadlineExceeded
from astrbot.core.interaction.config import load_interaction_agent_config
from astrbot.core.interaction.delayed_plugin_delivery import (
    DelayedPluginDeliveryContext,
    DelayedPluginDeliveryCoordinator,
)
from astrbot.core.interaction.group_reply import is_group_reply_candidate
from astrbot.core.interaction.personal_runtime import (
    PersonalRuntimeManager,
    PlatformEventSubmission,
)
from astrbot.core.interaction.plugin_artifact_delivery import (
    PluginArtifactDeliveryCoordinator,
)
from astrbot.core.interaction.plugin_execution_types import (
    PluginBranchResult,
    PluginGateResolution,
)
from astrbot.core.interaction.turn_context import PersonalTurnContext
from astrbot.core.interaction.turn_coordinator import (
    InteractionCoordinatedTurn,
    InteractionTurnCoordinator,
    PluginJobLaunch,
)
from astrbot.core.interaction.turn_state import (
    get_interaction_turn_config,
    has_interaction_turn_final_output_claimed,
    is_interaction_turn_completed,
    mark_interaction_turn_failed,
    record_interaction_turn_failure,
)
from astrbot.core.persona_error_reply import (
    extract_persona_custom_error_message_from_event,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import StarHandlerMetadata, star_handlers_registry

from ..context import PipelineContext
from ..stage import Stage, register_stage
from .method.agent_request import AgentRequestSubStage
from .method.star_request import StarRequestSubStage
from .plugin_branch import create_plugin_branch_event
from .plugin_handler_executor import (
    PluginHandlerExecutor,
)

TURN_DEADLINE_FALLBACK_TEXT = "模型服务暂时不可用，请稍后再试。"


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
        self.plugin_handler_executor = PluginHandlerExecutor(
            self.star_request_sub_stage,
        )
        self.interaction_turn_coordinator = (
            InteractionTurnCoordinator(ctx.plugin_execution_runtime)
            if ctx.plugin_execution_runtime is not None
            else None
        )
        self.plugin_artifact_delivery = (
            PluginArtifactDeliveryCoordinator(
                ctx.plugin_execution_runtime,
                ctx.interaction_middleware.output_controller,
            )
            if ctx.plugin_execution_runtime is not None
            and ctx.interaction_middleware is not None
            else None
        )
        self.delayed_plugin_delivery = (
            DelayedPluginDeliveryCoordinator(ctx.plugin_execution_runtime)
            if ctx.plugin_execution_runtime is not None
            else None
        )

    @staticmethod
    def _retain_current_handlers(
        event: AstrMessageEvent,
        handlers: list[StarHandlerMetadata],
    ) -> list[StarHandlerMetadata]:
        """Reject discovery results that were unbound or replaced by reload."""
        current_handlers: list[StarHandlerMetadata] = []
        stale_count = 0
        for handler in handlers:
            if not isinstance(handler, StarHandlerMetadata):
                current_handlers.append(handler)
                continue
            registered = star_handlers_registry.get_handler_by_full_name(
                handler.handler_full_name,
            )
            plugin = star_map.get(handler.handler_module_path)
            if registered is not handler or plugin is None or not plugin.activated:
                stale_count += 1
                continue
            current_handlers.append(handler)
        if stale_count:
            logger.warning(
                "Skipping %d stale Plugin Handler discovery result(s): "
                "platform_id=%s session_id=%s",
                stale_count,
                event.get_platform_id(),
                event.session_id,
            )
        return current_handlers

    def _get_plugin_handler_executor(self) -> PluginHandlerExecutor:
        executor = getattr(self, "plugin_handler_executor", None)
        if (
            executor is None
            or executor.handler_source is not self.star_request_sub_stage
        ):
            executor = PluginHandlerExecutor(self.star_request_sub_stage)
            self.plugin_handler_executor = executor
        return executor

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

    async def _run_core_agent_only(
        self,
        event: AstrMessageEvent,
        *,
        ensure_yield: bool = False,
    ) -> AsyncGenerator[None, None]:
        yielded = False
        agent_source = self.agent_sub_stage.process(event)
        async with aclosing(agent_source):
            async for _ in agent_source:
                yielded = True
                yield
        if ensure_yield and not yielded:
            yield

    def _should_use_coordinated_interaction_runtime(
        self,
        event: AstrMessageEvent,
        *,
        is_group_candidate: bool,
    ) -> bool:
        if not self.ctx.astrbot_config["provider_settings"].get("enable", True):
            return False
        interaction_config = get_interaction_turn_config(event)
        if interaction_config is None:
            runtime_config = event.get_extra(
                "_astrbot_config",
                self.ctx.astrbot_config,
            )
            interaction_config = load_interaction_agent_config(runtime_config)
        if (
            not interaction_config.enabled
            or not interaction_config.parallel_plugin_runtime_enabled
        ):
            return False
        middleware = self.ctx.interaction_middleware
        if middleware is None:
            raise RuntimeError("Parallel Plugin Runtime requires InteractionMiddleware")
        if not middleware.is_parallel_plugin_runtime_eligible(
            event,
            is_group_candidate=is_group_candidate,
        ):
            return False
        missing_dependencies = [
            name
            for name, dependency in (
                (
                    "InteractionTurnCoordinator",
                    getattr(self, "interaction_turn_coordinator", None),
                ),
                (
                    "PluginArtifactDeliveryCoordinator",
                    getattr(self, "plugin_artifact_delivery", None),
                ),
                (
                    "DelayedPluginDeliveryCoordinator",
                    getattr(self, "delayed_plugin_delivery", None),
                ),
                ("PersonalRuntimeManager", self.personal_runtime_manager),
            )
            if dependency is None
        ]
        if missing_dependencies:
            raise RuntimeError(
                "Parallel Plugin Runtime dependencies are unavailable: "
                + ", ".join(missing_dependencies)
            )
        return True

    def _log_interaction_pipeline_path(
        self,
        event: AstrMessageEvent,
        *,
        path: str,
        activated_handler_count: int,
        is_group_candidate: bool,
    ) -> None:
        """Emit one comparable path-selection record for each admitted turn."""
        interaction_config = get_interaction_turn_config(event)
        if interaction_config is None:
            runtime_config = event.get_extra(
                "_astrbot_config",
                self.ctx.astrbot_config,
            )
            interaction_config = load_interaction_agent_config(runtime_config)
        logger.info(
            "DIAG interaction.pipeline_path: platform_id=%s session_id=%s "
            "turn_id=%s path=%s activated_handler_count=%d group_candidate=%s "
            "interaction_enabled=%s parallel_plugin_runtime_enabled=%s",
            event.get_platform_id(),
            event.session_id,
            event.get_extra("_turn_id", ""),
            path,
            activated_handler_count,
            is_group_candidate,
            interaction_config.enabled,
            interaction_config.parallel_plugin_runtime_enabled,
        )

    async def _run_coordinated_interaction_turn(
        self,
        event: AstrMessageEvent,
        *,
        activated_handlers: list[StarHandlerMetadata],
        submission: PlatformEventSubmission | None,
    ) -> AsyncGenerator[None, None]:
        middleware = self.ctx.interaction_middleware
        coordinator = self.interaction_turn_coordinator
        artifact_delivery = self.plugin_artifact_delivery
        delayed_delivery = self.delayed_plugin_delivery
        manager = self.personal_runtime_manager
        if (
            middleware is None
            or coordinator is None
            or artifact_delivery is None
            or delayed_delivery is None
            or manager is None
        ):
            raise RuntimeError("Parallel Plugin Runtime dependencies are unavailable")

        interaction_config = await middleware.prepare_routable_pipeline_turn(event)
        if interaction_config is None:
            agent_source = self._run_core_agent_only(event)
            async with aclosing(agent_source):
                async for _ in agent_source:
                    yield
            return

        middleware.attach_event_context(
            event,
            turn_id=str(event.get_extra("_turn_id", "") or ""),
        )
        middleware.prepare_parallel_turn_control(event)
        runtime_config = event.get_extra("_astrbot_config", self.ctx.astrbot_config)
        t1_settled = asyncio.Event()
        branch_result: PluginBranchResult | None = None
        plugin_launch = None
        turn: InteractionCoordinatedTurn | None = None
        control = None
        if activated_handlers:
            branch_event, branch_result, branch_sink = create_plugin_branch_event(event)
            delayed_context = DelayedPluginDeliveryContext.capture(
                parent_event=event,
                config_id=self.ctx.astrbot_config_id,
                runtime_config=runtime_config,
                plugin_context=self.plugin_manager.context,
                personal_runtime_manager=manager,
                middleware=middleware,
            )

            async def run_plugin_job(
                publish_gate,
                delegate_provider_request,
            ) -> None:
                await branch_result.media_lease.materialize_inputs()
                branch_sink.bind_gate_publisher(publish_gate)
                source = self._get_plugin_handler_executor().process(
                    branch_event,
                    output_controller=branch_sink,
                    submission=None,
                    run_agent_turn=None,
                    result=branch_result,
                    publish_gate=publish_gate,
                    delegate_provider_request=delegate_provider_request,
                )
                async with aclosing(source):
                    async for _ in source:
                        pass

            async def complete_plugin_job(job) -> None:
                try:
                    await t1_settled.wait()
                    if job.result.delayed_delivery_eligible:
                        await delayed_delivery.deliver(delayed_context, job.result)
                finally:
                    if turn is not None:
                        coordinator.log_turn_diagnostics(
                            turn,
                            phase="plugin_completed",
                            control=control,
                        )
                        coordinator.plugin_runtime.log_diagnostics(
                            trigger="plugin_completed",
                            turn_id=str(event.get_extra("_turn_id", "") or ""),
                            job_id=job.job_id,
                        )
                    await coordinator.plugin_runtime.discard_delivery_records(
                        job.job_id
                    )

            plugin_launch = PluginJobLaunch(
                branch_event=branch_event,
                result=branch_result,
                run_job=run_plugin_job,
                module_paths=tuple(
                    sorted(
                        {
                            handler.handler_module_path
                            for handler in activated_handlers
                            if handler.handler_module_path
                        }
                    )
                ),
                completion_handler=complete_plugin_job,
            )

        try:
            turn = await coordinator.start(
                event,
                personal_factory=lambda: middleware.run_personal_task(
                    event,
                    interaction_config,
                ),
                router_factory=lambda: middleware.run_router_task(
                    event,
                    interaction_config,
                ),
                plugin_window_seconds=(
                    interaction_config.plugin_parallel_window_seconds
                ),
                plugin_launch=plugin_launch,
            )
        except BaseException:
            t1_settled.set()
            raise
        try:
            control = await coordinator.resolve_control(turn)
            event.set_extra(
                "_interaction_plugin_gate_resolution",
                control.plugin_gate.value,
            )
            event.set_extra(
                "_interaction_core_start_delay_due_to_plugin_ms",
                control.core_start_delay_due_to_plugin_ms,
            )
            coordinator.log_turn_diagnostics(
                turn,
                phase="control_resolved",
                control=control,
            )

            if control.plugin_gate is PluginGateResolution.DELEGATED:
                if branch_result is None:
                    raise RuntimeError(
                        "Plugin Gate delegated without an active Plugin Job"
                    )
                provider_source = self._drive_plugin_provider_requests(
                    event,
                    turn,
                    submission=submission,
                )
                try:
                    async with aclosing(provider_source):
                        async for _ in provider_source:
                            yield
                except BaseException as exc:
                    if isinstance(exc, (asyncio.CancelledError, Exception)):
                        branch_result.record_delegated_t1_failure(exc)
                        if turn.plugin_job is not None:
                            turn.plugin_job.mark_detached()
                    raise
                if branch_result.output_artifacts:
                    await artifact_delivery.deliver_inline(
                        event,
                        branch_result,
                        claim_final_output=True,
                    )
                event.stop_event()
                return

            if control.plugin_gate in {
                PluginGateResolution.HANDLED,
                PluginGateResolution.STOPPED,
            }:
                if branch_result is not None and branch_result.output_artifacts:
                    await artifact_delivery.deliver_inline(
                        event,
                        branch_result,
                        claim_final_output=True,
                    )
                event.stop_event()
                return

            if control.route is None:
                raise RuntimeError(
                    "Parallel Interaction control resolved without route"
                )
            middleware.accept_coordinated_route(event, control.route)
            await middleware.complete_routed_turn(
                event,
                interaction_config,
                turn.personal_task,
                control.route,
            )
            if event.is_stopped():
                return
            agent_source = self._run_core_agent_only(event)
            async with aclosing(agent_source):
                async for _ in agent_source:
                    yield
        finally:
            turn.close_provider_requests()
            if (
                turn.plugin_job is not None
                and branch_result is not None
                and branch_result.gate_resolution
                in {
                    PluginGateResolution.HANDLED,
                    PluginGateResolution.STOPPED,
                }
                and turn.plugin_job.task is not None
                and not turn.plugin_job.task.done()
            ):
                # T1 is complete, but this Handler chain may still produce
                # additional artifacts for delayed delivery.
                turn.plugin_job.mark_detached()
            coordinator.log_turn_diagnostics(
                turn,
                phase="t1_settled",
                control=control,
            )
            coordinator.plugin_runtime.log_diagnostics(
                trigger="t1_settled",
                turn_id=str(event.get_extra("_turn_id", "") or ""),
                job_id=turn.plugin_job.job_id if turn.plugin_job is not None else "",
            )
            t1_settled.set()

    async def _drive_plugin_provider_requests(
        self,
        event: AstrMessageEvent,
        turn: InteractionCoordinatedTurn,
        *,
        submission: PlatformEventSubmission | None,
    ) -> AsyncGenerator[None, None]:
        job = turn.plugin_job
        if job is None or job.task is None:
            return
        while True:
            receive_task = asyncio.create_task(
                turn.receive_provider_request(),
                name=f"plugin_provider_request_{job.job_id}",
            )
            done, _ = await asyncio.wait(
                {receive_task, job.task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task not in done:
                receive_task.cancel()
                await asyncio.gather(receive_task, return_exceptions=True)
                break
            command = await receive_task
            event.set_extra("provider_request", command.request)
            if submission is not None:
                submission.set_provider_request(command.request)
            try:
                source = self._run_core_agent_only(event, ensure_yield=True)
                async with aclosing(source):
                    async for _ in source:
                        yield
            except BaseException as exc:
                command.fail(exc)
                raise
            else:
                command.complete()
            finally:
                event.get_extra(default={}).pop("provider_request", None)
        # The wait loop exits only after the Runtime-owned Job task is terminal.
        # Do not add a second completion wait here: DELEGATED remains the
        # in-window ProviderRequest compatibility path, but it must not create
        # another apparent Job-completion barrier for the surrounding turn.

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
            group_candidate_admitted = True

        use_coordinated_runtime = self._should_use_coordinated_interaction_runtime(
            event,
            is_group_candidate=is_group_candidate,
        )
        self._log_interaction_pipeline_path(
            event,
            path=(
                "coordinated_plugin_runtime"
                if use_coordinated_runtime
                else "default_handler"
            ),
            activated_handler_count=len(activated_handlers),
            is_group_candidate=is_group_candidate,
        )
        if use_coordinated_runtime:
            source = self._run_coordinated_interaction_turn(
                event,
                activated_handlers=activated_handlers,
                submission=submission,
            )
            async with aclosing(source):
                async for _ in source:
                    yield
            return

        # 有插件 Handler 被激活
        if activated_handlers:
            middleware = self.ctx.interaction_middleware
            output_controller = (
                middleware.output_controller if middleware is not None else None
            )
            execution_result = PluginBranchResult()
            plugin_source = self._get_plugin_handler_executor().process(
                event,
                output_controller=output_controller,
                submission=submission,
                run_agent_turn=self._run_agent_turn,
                result=execution_result,
            )
            async with aclosing(plugin_source):
                async for _ in plugin_source:
                    yield
            if execution_result.delegated_to_core:
                return

        # A Handler may decide asynchronously that an otherwise passive
        # group message is worth evaluating. It joins the same interaction
        # path as every other candidate; Router and Persona start there once.
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

    @staticmethod
    async def _iterate_with_deadline(
        source: AsyncGenerator[None, None],
        deadline: TurnDeadlineBudget,
        event: AstrMessageEvent,
    ) -> AsyncGenerator[None, None]:
        """Enforce execution time only while advancing the stage generator."""
        try:
            while True:
                try:
                    if has_interaction_turn_final_output_claimed(event):
                        item = await anext(source)
                    else:
                        async with deadline.enforce("turn_execution"):
                            try:
                                item = await anext(source)
                            except StopAsyncIteration:
                                # An exhausted stage is a successful completion, not a deadline failure.
                                return
                except StopAsyncIteration:
                    return
                yield item
        finally:
            await source.aclose()

    async def _handle_deadline_expiry(
        self,
        event: AstrMessageEvent,
        *,
        stage: str,
        error: BaseException,
    ) -> None:
        already_completed = is_interaction_turn_completed(event)
        record_interaction_turn_failure(
            event,
            stage=stage,
            reason="turn_deadline_exhausted",
            exception=error,
            user_visible_action=(
                "existing_persona_reply"
                if already_completed
                else "fallback_error_reply"
            ),
        )
        if already_completed:
            event.stop_event()
            logger.warning(
                "Interaction control deadline expired after Persona delivery: "
                "platform_id=%s session_id=%s turn_id=%s stage=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                stage,
            )
            return
        middleware = self.ctx.interaction_middleware
        output_controller = (
            middleware.output_controller if middleware is not None else None
        )
        delivered = False
        if output_controller is not None:
            reply = (
                extract_persona_custom_error_message_from_event(event)
                or TURN_DEADLINE_FALLBACK_TEXT
            )
            try:
                delivered = await output_controller.emit_failure_reply(reply, event)
            except Exception:
                logger.exception(
                    "Interaction deadline fallback delivery failed: turn_id=%s",
                    event.get_extra("_turn_id"),
                )
        if not delivered and not is_interaction_turn_completed(event):
            mark_interaction_turn_failed(event)
        event.stop_event()
        logger.warning(
            "Interaction turn deadline exhausted: platform_id=%s session_id=%s "
            "turn_id=%s stage=%s",
            event.get_platform_id(),
            event.session_id,
            event.get_extra("_turn_id"),
            stage,
        )

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> None | AsyncGenerator[None, None]:
        """处理事件"""
        activated_handlers: list[StarHandlerMetadata] = event.get_extra(
            "activated_handlers",
            [],
        )
        activated_handlers = self._retain_current_handlers(
            event,
            activated_handlers,
        )
        event.set_extra("activated_handlers", activated_handlers)
        is_group_candidate = is_group_reply_candidate(event)
        skip_busy_group_candidate = (
            is_group_candidate and self._is_expirable_group_candidate(event)
        )
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
                try:
                    admission = await submission.admit(
                        allow_follow_up=not bool(activated_handlers),
                        wait_if_busy=not skip_busy_group_candidate,
                    )
                except TurnDeadlineExceeded as exc:
                    await self._handle_deadline_expiry(
                        event,
                        stage=exc.stage,
                        error=exc,
                    )
                    return
                if admission.consumed_as_follow_up:
                    event.set_extra("_personal_runtime_follow_up_consumed", True)
                    logger.info(
                        "Personal Runtime consumed message as active-runner follow-up: session_id=%s",
                        event.unified_msg_origin,
                    )
                    return
                if admission.skipped_busy:
                    event.set_extra(
                        "_personal_runtime_admission_skip_reason",
                        "busy_group_candidate",
                    )
                    plugin_source = self._run_busy_group_candidate_plugins(
                        event,
                        activated_handlers=activated_handlers,
                        submission=submission,
                    )
                    async with aclosing(plugin_source):
                        async for item in plugin_source:
                            yield item
                    event.stop_event()
                    logger.info(
                        "Personal Runtime skipped stale group candidate while "
                        "session was busy: platform_id=%s session_id=%s "
                        "turn_id=%s candidate_kind=%s",
                        event.get_platform_id(),
                        event.session_id,
                        admission.turn.turn_id,
                        event.get_extra(
                            "_interaction_group_reply_candidate_kind",
                            "unknown",
                        ),
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
                iteration_source = (
                    self._iterate_with_active_turn(
                        source,
                        manager,
                        turn,
                    )
                    if manager is not None and turn is not None
                    else source
                )
                deadline = turn.state.deadline if turn is not None else None
                if deadline is not None:
                    iteration_source = self._iterate_with_deadline(
                        iteration_source,
                        deadline,
                        event,
                    )
                try:
                    async with aclosing(iteration_source):
                        async for item in iteration_source:
                            yield item
                except TurnDeadlineExceeded as exc:
                    await self._handle_deadline_expiry(
                        event,
                        stage=exc.stage,
                        error=exc,
                    )
            finally:
                if lease is not None:
                    if manager is not None and turn is not None:
                        with manager.activate_turn(turn):
                            await lease.release()
                    else:
                        await lease.release()

    @staticmethod
    def _is_expirable_group_candidate(
        event: AstrMessageEvent,
    ) -> bool:
        candidate_kind = str(
            event.get_extra("_interaction_group_reply_candidate_kind", "") or ""
        ).strip()
        return candidate_kind in {"ambient", "continuation"}

    async def _run_busy_group_candidate_plugins(
        self,
        event: AstrMessageEvent,
        *,
        activated_handlers: list[StarHandlerMetadata],
        submission: PlatformEventSubmission,
    ) -> AsyncGenerator[None, None]:
        """Preserve official Plugin Handler semantics for a stale passive turn."""
        if not activated_handlers:
            return
        middleware = self.ctx.interaction_middleware
        output_controller = (
            middleware.output_controller if middleware is not None else None
        )
        execution_result = PluginBranchResult()
        plugin_source = self._get_plugin_handler_executor().process(
            event,
            output_controller=output_controller,
            submission=submission,
            run_agent_turn=self._run_core_agent_only,
            result=execution_result,
        )
        async with aclosing(plugin_source):
            async for item in plugin_source:
                yield item
