"""本地 Agent 模式的 LLM 调用 Stage"""

import asyncio
import base64
import json
from collections.abc import AsyncGenerator, Mapping
from dataclasses import replace

from sqlalchemy.exc import OperationalError

from astrbot.core import db_helper, logger
from astrbot.core.agent.message import (
    CheckpointData,
    CheckpointMessageSegment,
    Message,
    dump_messages_with_checkpoints,
)
from astrbot.core.agent.response import AgentStats
from astrbot.core.astr_main_agent import (
    CONVERSATION_SAVE_USER_MESSAGE_EXTRA_KEY,
    LLM_ERROR_MESSAGE_EXTRA_KEY,
    MainAgentBuildConfig,
    MainAgentBuildResult,
    _get_session_conv,
    build_main_agent,
)
from astrbot.core.core_request_preparation import (
    begin_core_request_lifecycle,
    finalize_core_request_preparation,
)
from astrbot.core.deadline import TurnDeadlineExceeded
from astrbot.core.execution import (
    CoreExecutionDeadlineView,
    CoreExecutionLedgerPreparation,
    CoreExecutionSpec,
    CoreFollowUpControl,
    bind_core_execution_head,
    get_core_execution_head,
    get_core_execution_lifecycle,
)
from astrbot.core.executors.assembly import build_native_executor_assembly
from astrbot.core.executors.coordinator import execute_external_core_turn
from astrbot.core.executors.registry import resolve_core_executor_selection
from astrbot.core.interaction.core_bridge import get_core_task_spec
from astrbot.core.interaction.output_modes import OutputOrigin, temporary_output_origin
from astrbot.core.interaction.turn_state import (
    bind_interaction_turn_core_execution_journal,
    get_interaction_turn_core_execution_spec,
    get_interaction_turn_deadline,
    get_interaction_turn_runtime_config,
    is_interaction_turn_core_delegated,
    record_interaction_turn_core_execution_ledger_persist_failure,
    record_interaction_turn_core_execution_ledger_settlement,
)
from astrbot.core.message.components import File, Image, Record, Reply, Video
from astrbot.core.message.message_event_result import (
    MessageChain,
    MessageEventResult,
    ResultContentType,
)
from astrbot.core.persona_error_reply import (
    extract_persona_custom_error_message_from_event,
)
from astrbot.core.pipeline.stage import Stage
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import (
    LLMResponse,
    ProviderRequest,
)
from astrbot.core.utils.metrics import Metric

from .....astr_agent_run_util import (
    NativeExecutionAttachment,
    NativeExecutionOutputBridge,
    NativeExecutorAdapter,
)
from ....context import PipelineContext, call_event_hook


class InternalAgentSubStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        conf = ctx.astrbot_config
        settings = conf["provider_settings"]
        self.streaming_response: bool = settings["streaming_response"]
        self.unsupported_streaming_strategy: str = settings[
            "unsupported_streaming_strategy"
        ]
        self.max_step: int = settings.get("max_agent_step", 30)
        self.tool_call_timeout: int = settings.get("tool_call_timeout", 60)
        self.tool_schema_mode: str = settings.get("tool_schema_mode", "full")
        if self.tool_schema_mode not in ("skills_like", "full"):
            logger.warning(
                "Unsupported tool_schema_mode: %s, fallback to skills_like",
                self.tool_schema_mode,
            )
            self.tool_schema_mode = "full"
        if isinstance(self.max_step, bool):  # workaround: #2622
            self.max_step = 30
        self.show_tool_use: bool = settings.get("show_tool_use_status", True)
        self.show_tool_call_result: bool = settings.get("show_tool_call_result", False)
        self.buffer_intermediate_messages: bool = settings.get(
            "buffer_intermediate_messages",
            False,
        )
        self.show_reasoning = settings.get("display_reasoning_text", False)
        self.sanitize_context_by_modalities: bool = settings.get(
            "sanitize_context_by_modalities",
            False,
        )
        self.kb_agentic_mode: bool = conf.get("kb_agentic_mode", False)

        file_extract_conf: dict = settings.get("file_extract", {})
        self.file_extract_enabled: bool = file_extract_conf.get("enable", False)
        self.file_extract_msh_api_key: str = file_extract_conf.get(
            "moonshotai_api_key", ""
        )

        # 上下文管理相关
        self.context_limit_reached_strategy: str = settings.get(
            "context_limit_reached_strategy", "truncate_by_turns"
        )
        self.llm_compress_instruction: str = settings.get(
            "llm_compress_instruction", ""
        )
        self.llm_compress_keep_recent_ratio: float = settings.get(
            "llm_compress_keep_recent_ratio", 0.15
        )
        self.llm_compress_provider_id: str = settings.get(
            "llm_compress_provider_id", ""
        )
        self.max_context_length = settings["max_context_length"]  # int
        self.dequeue_context_length: int = min(
            max(1, settings["dequeue_context_length"]),
            self.max_context_length - 1,
        )
        if self.dequeue_context_length <= 0:
            self.dequeue_context_length = 1
        self.fallback_max_context_tokens: int = settings.get(
            "fallback_max_context_tokens", 128000
        )

        self.llm_safety_mode = settings.get("llm_safety_mode", True)

        self.computer_use_runtime = settings.get("computer_use_runtime")
        self.sandbox_cfg = settings.get("sandbox", {})

        # Proactive capability configuration
        proactive_cfg = settings.get("proactive_capability", {})
        self.add_cron_tools = proactive_cfg.get("add_cron_tools", True)

        self.conv_manager = ctx.plugin_manager.context.conversation_manager

        self.main_agent_cfg = MainAgentBuildConfig(
            tool_call_timeout=self.tool_call_timeout,
            tool_schema_mode=self.tool_schema_mode,
            sanitize_context_by_modalities=self.sanitize_context_by_modalities,
            kb_agentic_mode=self.kb_agentic_mode,
            file_extract_enabled=self.file_extract_enabled,
            file_extract_msh_api_key=self.file_extract_msh_api_key,
            context_limit_reached_strategy=self.context_limit_reached_strategy,
            llm_compress_instruction=self.llm_compress_instruction,
            llm_compress_keep_recent_ratio=self.llm_compress_keep_recent_ratio,
            llm_compress_provider_id=self.llm_compress_provider_id,
            max_context_length=self.max_context_length,
            dequeue_context_length=self.dequeue_context_length,
            fallback_max_context_tokens=self.fallback_max_context_tokens,
            llm_safety_mode=self.llm_safety_mode,
            computer_use_runtime=self.computer_use_runtime,
            sandbox_cfg=self.sandbox_cfg,
            add_cron_tools=self.add_cron_tools,
            provider_settings=settings,
            subagent_orchestrator=conf.get("subagent_orchestrator", {}),
            timezone=self.ctx.plugin_manager.context.get_config().get("timezone"),
            max_quoted_fallback_images=settings.get("max_quoted_fallback_images", 20),
        )

    def _build_turn_main_agent_config(
        self,
        event: AstrMessageEvent,
        *,
        provider_wake_prefix: str,
        streaming_response: bool,
    ) -> tuple[MainAgentBuildConfig, Mapping[str, object]]:
        """Project an admitted Interaction configuration into the Core boundary."""
        runtime_config = get_interaction_turn_runtime_config(event)
        if not isinstance(runtime_config, Mapping):
            return (
                replace(
                    self.main_agent_cfg,
                    provider_wake_prefix=provider_wake_prefix,
                    streaming_response=streaming_response,
                ),
                self.main_agent_cfg.provider_settings,
            )

        config = self.main_agent_cfg.with_runtime_config(runtime_config)
        return (
            replace(
                config,
                provider_wake_prefix=provider_wake_prefix,
                streaming_response=streaming_response,
            ),
            config.provider_settings,
        )

    async def _send_llm_error_message(
        self, event: AstrMessageEvent, message: object
    ) -> None:
        await event.send(MessageChain().message(str(message)))

    async def process(
        self, event: AstrMessageEvent, provider_wake_prefix: str
    ) -> AsyncGenerator[None, None]:
        typing_requested = False
        native_executor: NativeExecutorAdapter | None = None
        native_attachment: NativeExecutionAttachment | None = None
        req: ProviderRequest | None = None
        build_result: MainAgentBuildResult | None = None
        execution_head = None
        runner_reset_completed = False
        executor_activated = False
        try:
            runtime_config = get_interaction_turn_runtime_config(event)
            runtime_settings = (
                runtime_config.get("provider_settings", {})
                if isinstance(runtime_config, Mapping)
                else {}
            )
            if not isinstance(runtime_settings, Mapping):
                runtime_settings = {}
            selected_executor = resolve_core_executor_selection(
                runtime_config if isinstance(runtime_config, Mapping) else {},
                provider_manager=self.ctx.plugin_manager.context.provider_manager,
                execution_source="interaction",
            )
            executor_id = selected_executor.executor_id
            streaming_response = runtime_settings.get(
                "streaming_response",
                getattr(self, "streaming_response", self.main_agent_cfg.streaming_response),
            )
            if (enable_streaming := event.get_extra("enable_streaming")) is not None:
                streaming_response = bool(enable_streaming)
            unsupported_streaming_strategy = runtime_settings.get(
                "unsupported_streaming_strategy",
                getattr(self, "unsupported_streaming_strategy", "turn_off"),
            )
            default_max_step = getattr(self, "max_step", 30)
            max_step = runtime_settings.get("max_agent_step", default_max_step)
            if isinstance(max_step, bool):
                max_step = default_max_step
            show_tool_use = bool(
                runtime_settings.get(
                    "show_tool_use_status", getattr(self, "show_tool_use", True)
                )
            )
            show_tool_call_result = bool(
                runtime_settings.get(
                    "show_tool_call_result",
                    getattr(self, "show_tool_call_result", False),
                )
            )
            show_reasoning = bool(
                runtime_settings.get(
                    "display_reasoning_text", getattr(self, "show_reasoning", False)
                )
            )
            buffer_intermediate_messages = bool(
                runtime_settings.get(
                    "buffer_intermediate_messages",
                    getattr(self, "buffer_intermediate_messages", False),
                )
            )

            has_provider_request = event.get_extra("provider_request") is not None
            has_valid_message = bool(event.message_str and event.message_str.strip())
            has_delegated_core_task = (
                is_interaction_turn_core_delegated(event)
                and get_core_task_spec(event) is not None
            )
            has_media_content = any(
                isinstance(comp, (Image, File, Record, Video))
                for comp in event.message_obj.message
            )
            has_reply = any(
                isinstance(comp, Reply) for comp in event.message_obj.message
            )

            if (
                not has_provider_request
                and not has_valid_message
                and not has_delegated_core_task
                and not has_media_content
                and not has_reply
            ):
                logger.debug("skip llm request: empty message and no provider_request")
                return

            logger.debug("ready to request llm provider")
            try:
                typing_requested = True
                await event.send_typing()
            except Exception:
                logger.warning("send_typing failed", exc_info=True)
            request_lifecycle = await begin_core_request_lifecycle(
                event,
                hook_dispatcher=call_event_hook,
            )
            if request_lifecycle is None:
                return

            input_control_registered = False
            input_control: CoreFollowUpControl | None = None
            try:
                build_cfg, _ = self._build_turn_main_agent_config(
                    event,
                    provider_wake_prefix=provider_wake_prefix,
                    streaming_response=bool(streaming_response),
                )

                if executor_id != "native":
                    output_controller = event.get_extra(
                        "_interaction_output_controller"
                    )
                    if output_controller is None:
                        raise RuntimeError(
                            "Core interaction output controller is unavailable"
                        )
                    core_deadline = get_interaction_turn_deadline(event)
                    if core_deadline is None:
                        raise RuntimeError("Core interaction deadline is unavailable")

                    def register_external_input_control(head, selected_executor_id):
                        nonlocal input_control, input_control_registered
                        runtime_manager = self.ctx.personal_runtime_manager
                        if runtime_manager is None:
                            return
                        input_control = CoreFollowUpControl(
                            head=head,
                            executor_id=selected_executor_id,
                            actor_id=str(event.get_sender_id() or "").strip(),
                            is_stopping=lambda: bool(
                                event.get_extra("agent_stop_requested")
                            ),
                        )
                        input_control_registered = (
                            runtime_manager.register_active_input_control(
                                event,
                                input_control,
                            )
                        )

                    external_conversation = await _get_session_conv(
                        event,
                        self.ctx.plugin_manager.context,
                    )
                    req = ProviderRequest(conversation=external_conversation)
                    external = await execute_external_core_turn(
                        context=self.ctx.plugin_manager.context,
                        event=event,
                        selected_executor=selected_executor,
                        config=build_cfg,
                        session_id=event.unified_msg_origin,
                        prompt_config=build_cfg,
                        deadline=core_deadline,
                        output_controller=output_controller,
                        submission_metadata={
                            "source": "interaction_external",
                            "executor_id": executor_id,
                            "streaming": False,
                        },
                        on_started=register_external_input_control,
                    )
                    execution_head = external.head
                    executor_activated = True
                    runner_reset_completed = True
                    req = ProviderRequest(
                        prompt=external.request.prompt,
                        conversation=external_conversation,
                    )
                    response = LLMResponse(
                        role="assistant",
                        completion_text=(
                            external.result.output.text
                            if external.result.output is not None
                            else ""
                        ),
                    )
                    await self._save_interaction_core_state(
                        event,
                        req,
                        response,
                        [],
                        None,
                        user_aborted=False,
                    )
                    return

                build_result = await build_main_agent(
                    event=event,
                    plugin_context=self.ctx.plugin_manager.context,
                    config=build_cfg,
                    apply_reset=False,
                    request_lifecycle=request_lifecycle,
                )

                if build_result is None:
                    if llm_error_message := event.get_extra(
                        LLM_ERROR_MESSAGE_EXTRA_KEY
                    ):
                        await self._send_llm_error_message(
                            event,
                            llm_error_message,
                        )
                    return

                req = build_result.provider_request
                provider = build_result.provider
                request_lifecycle = (
                    build_result.request_lifecycle or request_lifecycle
                )

                api_base = provider.provider_config.get("api_base", "")
                for host in decoded_blocked:
                    if host in api_base:
                        error_message = (
                            f"LLM 请求失败：Provider API base `{api_base}` "
                            "因安全原因被拦截，请更换可用的 AI 提供商。"
                        )
                        logger.error(error_message)
                        await self._send_llm_error_message(event, error_message)
                        return

                stream_to_general = (
                    unsupported_streaming_strategy == "turn_off"
                    and not event.platform_meta.support_streaming_message
                )

                if not await finalize_core_request_preparation(event, build_result):
                    return

                effective_execution_spec = build_result.execution_spec
                if effective_execution_spec is not None:
                    execution_head = bind_core_execution_head(
                        event,
                        effective_execution_spec,
                    )
                    deadline_view = getattr(
                        getattr(build_result, "prepared_execution", None),
                        "deadline_view",
                        None,
                    )
                    if deadline_view is not None:
                        execution_head.bind_deadline_view(deadline_view)
                    elif deadline := get_interaction_turn_deadline(event):
                        execution_head.bind_deadline_view(
                            CoreExecutionDeadlineView.from_budget(deadline)
                        )
                    bind_interaction_turn_core_execution_journal(event, execution_head)

                native_assembly = build_native_executor_assembly(
                    executor_id=executor_id,
                    runner=build_result.agent_runner,
                    core_port=execution_head,
                )
                native_attachment = native_assembly.attachment
                native_executor = native_assembly.executor

                await build_result.reset_prepared_runner()
                runner_reset_completed = True

                native_attachment.activate(
                    submission_metadata={
                        "provider_id": str(provider.provider_config.get("id", "") or ""),
                        "provider_model": str(provider.get_model() or ""),
                        "streaming": bool(streaming_response),
                    },
                )
                executor_activated = native_attachment.activated

                runtime_manager = self.ctx.personal_runtime_manager
                if runtime_manager is not None and execution_head is not None:
                    input_control = CoreFollowUpControl(
                        head=execution_head,
                        executor_id=native_executor.executor_id,
                        actor_id=str(event.get_sender_id() or "").strip(),
                        is_stopping=lambda: bool(
                            event.get_extra("agent_stop_requested")
                        ),
                    )
                    input_control_registered = (
                        runtime_manager.register_active_input_control(
                            event,
                            input_control,
                        )
                    )
                action_type = event.get_extra("action_type")

                event.trace.record(
                    "astr_agent_prepare",
                    request_lifecycle_id=request_lifecycle.lifecycle_id,
                    executor_id=executor_id,
                    system_prompt=req.system_prompt,
                    tools=req.func_tool.names() if req.func_tool else [],
                    stream=streaming_response,
                    chat_provider={
                        "id": provider.provider_config.get("id", ""),
                        "model": provider.get_model(),
                    },
                )

                output_bridge = NativeExecutionOutputBridge(
                    native_executor,
                    max_step=max_step,
                    show_tool_use=show_tool_use,
                    show_tool_call_result=show_tool_call_result,
                    stream_to_general=(
                        False if action_type == "live" else stream_to_general
                    ),
                    show_reasoning=show_reasoning,
                    buffer_intermediate_messages=buffer_intermediate_messages,
                    should_stop=event.is_stopped,
                )

                # 检测 Live Mode。
                if action_type == "live":
                    # Live Mode: 使用 run_live_agent
                    logger.info("[Internal Agent] 检测到 Live Mode，启用 TTS 处理")

                    # 获取 TTS Provider
                    tts_provider = (
                        self.ctx.plugin_manager.context.get_using_tts_provider(
                            event.unified_msg_origin
                        )
                    )

                    if not tts_provider:
                        logger.warning(
                            "[Live Mode] TTS Provider 未配置，将使用普通流式模式"
                        )

                    # Live Mode 总是使用流式响应。
                    event.set_result(
                        MessageEventResult()
                        .set_result_content_type(ResultContentType.STREAMING_RESULT)
                        .set_async_stream(
                            output_bridge.stream_live(tts_provider),
                        ),
                    )
                    yield

                elif streaming_response and not stream_to_general:
                    # 流式响应
                    event.set_result(
                        MessageEventResult()
                        .set_result_content_type(ResultContentType.STREAMING_RESULT)
                        .set_async_stream(
                            output_bridge.stream(),
                        ),
                    )
                    yield
                    if native_executor.done():
                        if final_chain := native_executor.final_response_chain():
                            event.set_result(
                                MessageEventResult(
                                    chain=final_chain.chain,
                                    result_content_type=ResultContentType.STREAMING_FINISH,
                                ),
                            )
                else:
                    async for _ in output_bridge.stream():
                        yield

                evidence = native_attachment.finalize()
                final_resp = evidence.final_response

                event.trace.record(
                    "astr_agent_complete",
                    request_lifecycle_id=request_lifecycle.lifecycle_id,
                    stats=evidence.stats.to_dict(),
                    resp=final_resp.completion_text if final_resp else None,
                )

                asyncio.create_task(
                    _record_internal_agent_stats(
                        event,
                        req,
                        final_resp,
                        native_executor,
                    )
                )

                # 检查事件是否被停止，如果被停止则不保存历史记录
                if (
                    not event.is_stopped() or evidence.was_aborted
                ):
                    await self._save_to_history(
                        event,
                        req,
                        final_resp,
                        evidence.messages,
                        evidence.stats,
                        user_aborted=evidence.was_aborted,
                    )

                asyncio.create_task(
                    Metric.upload(
                        llm_tick=1,
                        model_name=native_executor.provider.get_model(),
                        provider_type=native_executor.provider.meta().type,
                    ),
                )
            finally:
                if input_control_registered and input_control is not None:
                    runtime_manager = self.ctx.personal_runtime_manager
                    if runtime_manager is not None:
                        runtime_manager.unregister_active_input_control(
                            event,
                            input_control,
                        )

        except TurnDeadlineExceeded:
            cancellation_reason = "deadline_exceeded"
            if native_attachment is not None and executor_activated:
                native_attachment.cancel(metadata={"reason": cancellation_reason})
            elif native_executor is not None and execution_head is not None:
                execution_head.cancel(
                    executor_id=native_executor.executor_id,
                    metadata={"reason": cancellation_reason},
                )
            await self._save_cancelled_interaction_core_state(
                event,
                req,
                native_executor=(
                    native_executor if runner_reset_completed else None
                ),
                cancellation_reason=cancellation_reason,
            )
            raise
        except asyncio.CancelledError:
            deadline = get_interaction_turn_deadline(event)
            cancellation_reason = (
                "deadline_exceeded"
                if deadline is not None and deadline.expired()
                else "stage_cancelled"
            )
            if native_attachment is not None and executor_activated:
                native_attachment.cancel(metadata={"reason": cancellation_reason})
            elif native_executor is not None and execution_head is not None:
                execution_head.cancel(
                    executor_id=native_executor.executor_id,
                    metadata={"reason": cancellation_reason},
                )
            await self._save_cancelled_interaction_core_state(
                event,
                req,
                native_executor=(
                    native_executor if runner_reset_completed else None
                ),
                cancellation_reason=cancellation_reason,
            )
            raise
        except Exception as e:
            logger.error(f"Error occurred while processing agent: {e}")
            if native_attachment is not None and executor_activated:
                native_attachment.fail(
                    metadata={
                        "error_type": type(e).__name__,
                        "error": str(e)[:2000],
                    },
                )
            elif native_executor is not None and execution_head is not None:
                execution_head.fail(
                    executor_id=native_executor.executor_id,
                    metadata={
                        "error_type": type(e).__name__,
                        "error": str(e)[:2000],
                    },
                )
            await self._save_failed_interaction_core_state(
                event,
                req,
                native_executor=(
                    native_executor if runner_reset_completed else None
                ),
                error=e,
            )
            custom_error_message = extract_persona_custom_error_message_from_event(
                event
            )
            error_text = custom_error_message or (
                f"Error occurred while processing agent request: {e}"
            )
            with temporary_output_origin(event, OutputOrigin.CORE.value):
                await event.send(MessageChain().message(error_text))
        finally:
            if build_result is not None:
                build_result.discard_pending_reset()
            if native_attachment is not None and executor_activated:
                native_attachment.release()
            if typing_requested:
                try:
                    await event.stop_typing()
                except Exception:
                    logger.warning("stop_typing failed", exc_info=True)

    async def _save_to_history(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        llm_response: LLMResponse | None,
        all_messages: list[Message],
        runner_stats: AgentStats | None,
        user_aborted: bool = False,
    ) -> None:
        if event.get_extra("_interaction_enabled", False):
            try:
                await self._save_interaction_core_state(
                    event,
                    req,
                    llm_response,
                    all_messages,
                    runner_stats,
                    user_aborted=user_aborted,
                )
            except Exception as exc:  # noqa: BLE001
                self._record_interaction_core_ledger_failure(event, exc)
                logger.error(
                    "Core execution ledger persistence failed after execution: turn_id=%s error=%s",
                    event.get_extra("_turn_id"),
                    exc,
                    exc_info=True,
                )
            return
        if not req or not req.conversation:
            return

        if not llm_response and not user_aborted:
            return

        if llm_response and llm_response.role != "assistant":
            if not user_aborted:
                return
            llm_response = LLMResponse(
                role="assistant",
                completion_text=llm_response.completion_text or "",
            )
        elif llm_response is None:
            llm_response = LLMResponse(role="assistant", completion_text="")

        if (
            not llm_response.completion_text
            and not req.tool_calls_result
            and not user_aborted
        ):
            logger.debug("LLM 响应为空，不保存记录。")
            return

        messages_to_save: list[Message] = []
        skipped_initial_system = False
        for message in all_messages:
            if message.role == "system" and not skipped_initial_system:
                skipped_initial_system = True
                continue
            if message.role in ["assistant", "user"] and message._no_save:
                continue
            messages_to_save.append(message)

        save_user_message = event.get_extra(CONVERSATION_SAVE_USER_MESSAGE_EXTRA_KEY)
        if isinstance(save_user_message, dict):
            for index in range(len(messages_to_save) - 1, -1, -1):
                if messages_to_save[index].role != "user":
                    continue
                messages_to_save[index] = Message.model_validate(save_user_message)
                break

        checkpoint_id = event.get_extra("llm_checkpoint_id")
        message_to_save = dump_messages_with_checkpoints(messages_to_save)
        if isinstance(checkpoint_id, str) and checkpoint_id:
            message_to_save.append(
                CheckpointMessageSegment(
                    content=CheckpointData(id=checkpoint_id),
                ).model_dump()
            )

        # if user_aborted:
        #     message_to_save.append(
        #         Message(
        #             role="assistant",
        #             content="[User aborted this request. Partial output before abort was preserved.]",
        #         ).model_dump()
        #     )

        token_usage = None
        if runner_stats:
            # token_usage = runner_stats.token_usage.total
            token_usage = llm_response.usage.total if llm_response.usage else None

        await self.conv_manager.update_conversation(
            event.unified_msg_origin,
            req.conversation.cid,
            history=message_to_save,
            token_usage=token_usage,
        )

    async def _save_interaction_core_state(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        llm_response: LLMResponse | None,
        all_messages: list[Message],
        runner_stats: AgentStats | None,
        *,
        user_aborted: bool,
        terminal_error: str | None = None,
        status_override: str | None = None,
        update_conversation_token_usage: bool = True,
    ) -> None:
        """Persist Core telemetry and execution continuity, never visible dialogue."""
        if not req or not req.conversation:
            return

        execution_spec = get_interaction_turn_core_execution_spec(event)
        if not isinstance(execution_spec, CoreExecutionSpec):
            return
        execution_head = get_core_execution_head(event)
        terminal_event = (
            execution_head.terminal_event if execution_head is not None else None
        )
        executor_id = (
            execution_head.executor_id
            if execution_head is not None and execution_head.executor_id
            else terminal_event.execution.executor_id
            if terminal_event is not None
            else "native"
        )
        if execution_head is None and (
            event.get_extra("_core_execution_ledger_recorded_id")
            == execution_spec.execution_id
        ):
            return

        token_usage = (
            llm_response.usage.total
            if llm_response is not None and llm_response.usage is not None
            else None
        )
        if token_usage is not None and update_conversation_token_usage:
            try:
                await self.conv_manager.update_conversation(
                    event.unified_msg_origin,
                    req.conversation.cid,
                    token_usage=token_usage,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to persist Interaction Core token usage",
                    exc_info=True,
                )
        messages = _extract_core_execution_messages(all_messages)
        ledger = self.ctx.plugin_manager.context.core_execution_ledger
        if ledger is None:
            return
        completion_text = str(
            llm_response.completion_text if llm_response is not None else ""
        )
        lifecycle = (
            execution_head.lifecycle
            if execution_head is not None
            else get_core_execution_lifecycle(event)
        )
        preparation = (
            execution_head.prepare_ledger_preparation(
                completion_text=completion_text,
                user_aborted=user_aborted,
                fallback_status=status_override,
                fallback_error=terminal_error,
            )
            if execution_head is not None
            else lifecycle.prepare_ledger_preparation(
                completion_text=completion_text,
                user_aborted=user_aborted,
                fallback_status=status_override,
                fallback_error=terminal_error,
            )
            if lifecycle is not None
            else CoreExecutionLedgerPreparation.from_fallback(
                execution_spec=execution_spec,
                completion_text=completion_text,
                user_aborted=user_aborted,
                fallback_status=status_override,
                fallback_error=terminal_error,
            )
        )
        async def append_to_ledger() -> bool:
            return await ledger.append_execution(
                execution_spec=preparation.execution_spec,
                conversation_id=req.conversation.cid,
                executor_id=executor_id,
                status=preparation.status,
                messages=messages,
                result=preparation.result,
                error=preparation.error,
                token_usage=(
                    runner_stats.token_usage.__dict__
                    if runner_stats is not None
                    else None
                ),
            )

        if execution_head is not None:
            inserted = await execution_head.settle_ledger(append_to_ledger)
            if inserted is None:
                return
        else:
            inserted = await append_to_ledger()
        record_interaction_turn_core_execution_ledger_settlement(
            event,
            preparation,
            executor_id=executor_id,
            inserted=inserted,
        )
        event.set_extra(
            "_core_execution_ledger_recorded_id",
            execution_spec.execution_id,
        )

    @staticmethod
    def _record_interaction_core_ledger_failure(
        event: AstrMessageEvent,
        error: Exception,
    ) -> None:
        """Expose a terminal Ledger write failure to the owning turn diagnostics."""

        error_text = str(error)[:2000]
        execution_head = get_core_execution_head(event)
        executor_id = (
            execution_head.executor_id
            if execution_head is not None and execution_head.executor_id
            else "native"
        )
        event.set_extra("_core_execution_ledger_failed", True)
        event.set_extra("_core_execution_ledger_failure_reason", error_text)
        record_interaction_turn_core_execution_ledger_persist_failure(
            event,
            executor_id=executor_id,
            error=error,
        )

    async def _save_cancelled_interaction_core_state(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest | None,
        *,
        native_executor: NativeExecutorAdapter | None,
        cancellation_reason: str,
    ) -> None:
        """Persist a cancelled interaction execution without mutating dialogue history."""

        if (
            not event.get_extra("_interaction_enabled", False)
            or req is None
            or req.conversation is None
        ):
            return
        messages: list[Message] = []
        final_response = None
        runner_stats = None
        if native_executor is not None:
            try:
                messages = native_executor.messages
            except Exception:  # noqa: BLE001
                messages = []
            try:
                final_response = native_executor.final_response()
                runner_stats = native_executor.stats
            except Exception:  # noqa: BLE001
                pass
        try:
            await self._save_interaction_core_state(
                event,
                req,
                final_response,
                messages,
                runner_stats,
                # This path is entered only for an outer task cancellation or
                # deadline. A runner stop signal must not recategorize it as a
                # user-aborted execution.
                user_aborted=False,
                terminal_error=cancellation_reason,
                status_override="cancelled",
                update_conversation_token_usage=False,
            )
        except Exception as exc:  # noqa: BLE001
            self._record_interaction_core_ledger_failure(event, exc)
            logger.warning(
                "Failed to persist cancelled Core execution",
                exc_info=True,
            )

    async def _save_failed_interaction_core_state(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest | None,
        *,
        native_executor: NativeExecutorAdapter | None,
        error: Exception,
    ) -> None:
        if (
            not event.get_extra("_interaction_enabled", False)
            or req is None
            or req.conversation is None
        ):
            return
        execution_spec = get_interaction_turn_core_execution_spec(event)
        if not isinstance(execution_spec, CoreExecutionSpec):
            return
        messages: list[Message] = []
        final_response = None
        runner_stats = None
        user_aborted = False
        if native_executor is not None:
            try:
                messages = native_executor.messages
            except Exception:  # noqa: BLE001
                messages = []
            try:
                final_response = native_executor.final_response()
                runner_stats = native_executor.stats
                user_aborted = native_executor.was_aborted()
            except Exception:  # noqa: BLE001
                pass
        try:
            await self._save_interaction_core_state(
                event,
                req,
                final_response,
                messages,
                runner_stats,
                user_aborted=user_aborted,
                terminal_error=str(error)[:2000],
                status_override="failed",
                update_conversation_token_usage=False,
            )
        except Exception as exc:  # noqa: BLE001
            self._record_interaction_core_ledger_failure(event, exc)
            logger.warning("Failed to persist Core execution failure", exc_info=True)


def _extract_core_execution_messages(
    all_messages: list[Message],
) -> list[dict]:
    """Keep only Core execution evidence needed by a later executor turn."""
    execution_messages: list[dict] = []
    for message in all_messages:
        if message.role == "tool" or (
            message.role == "assistant" and message.tool_calls
        ):
            execution_messages.append(message.model_dump(mode="json"))
    bounded: list[dict] = []
    for message in execution_messages[-16:]:
        serialized = json.dumps(message, ensure_ascii=False, default=str)
        if len(serialized) <= 6000:
            bounded.append(message)
            continue
        bounded.append(
            {
                "role": message.get("role", "tool"),
                "content": f"{serialized[:6000]}...",
            }
        )
    return bounded


# we prevent astrbot from connecting to known malicious hosts
# these hosts are base64 encoded
BLOCKED = {"dGZid2h2d3IuY2xvdWQuc2VhbG9zLmlv", "a291cmljaGF0"}
decoded_blocked = [base64.b64decode(b).decode("utf-8") for b in BLOCKED]

PROVIDER_STATS_SQLITE_LOCK_RETRY_ATTEMPTS = 3
PROVIDER_STATS_SQLITE_LOCK_RETRY_BASE_DELAY = 0.2


def _is_sqlite_database_locked_error(exc: OperationalError) -> bool:
    raw = getattr(exc, "orig", exc)
    message = str(raw).lower()
    return "database" in message and "locked" in message


async def _record_internal_agent_stats(
    event: AstrMessageEvent,
    req: ProviderRequest | None,
    final_resp: LLMResponse | None,
    native_executor: NativeExecutorAdapter,
) -> None:
    """Persist Adapter-backed internal agent stats outside the response flow."""
    provider = native_executor.provider
    stats = native_executor.stats
    if provider is None or stats is None:
        return

    try:
        provider_config = getattr(provider, "provider_config", {}) or {}
        conversation_id = (
            req.conversation.cid
            if req is not None and req.conversation is not None
            else None
        )

        was_aborted = native_executor.was_aborted()
        if was_aborted:
            status = "aborted"
        elif final_resp is not None and final_resp.role == "err":
            status = "error"
        else:
            status = "completed"
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("Persist provider stats failed: %s", e, exc_info=True)
        return

    for attempt in range(PROVIDER_STATS_SQLITE_LOCK_RETRY_ATTEMPTS):
        last_attempt = attempt == PROVIDER_STATS_SQLITE_LOCK_RETRY_ATTEMPTS - 1
        try:
            await db_helper.insert_provider_stat(
                umo=event.unified_msg_origin,
                conversation_id=conversation_id,
                provider_id=provider_config.get("id", "") or provider.meta().id,
                provider_model=provider.get_model(),
                status=status,
                stats=stats.to_dict(),
                agent_type="internal",
            )
            break
        except asyncio.CancelledError:
            raise
        except OperationalError as e:
            if _is_sqlite_database_locked_error(e) and not last_attempt:
                await asyncio.sleep(
                    PROVIDER_STATS_SQLITE_LOCK_RETRY_BASE_DELAY * (2**attempt)
                )
                continue
            logger.warning("Persist provider stats failed: %s", e, exc_info=True)
            break
        except Exception as e:
            logger.warning("Persist provider stats failed: %s", e, exc_info=True)
            break
