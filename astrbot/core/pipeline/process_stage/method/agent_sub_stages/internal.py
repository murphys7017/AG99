"""本地 Agent 模式的 LLM 调用 Stage"""

import asyncio
import base64
import json
from collections.abc import AsyncGenerator
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
from astrbot.core.agent.tool import TOOL_TARGET_CORE, ToolSet
from astrbot.core.agent_lifecycle import AgentRequestLifecycle
from astrbot.core.astr_main_agent import (
    CONVERSATION_SAVE_USER_MESSAGE_EXTRA_KEY,
    LLM_ERROR_MESSAGE_EXTRA_KEY,
    MainAgentBuildConfig,
    MainAgentBuildResult,
    build_main_agent,
)
from astrbot.core.capabilities import CapabilityResolver
from astrbot.core.db.po import CoreExecutionRecord as CoreExecutionLedgerRecord
from astrbot.core.execution import (
    CORE_EXECUTION_SPEC_EXTRA_KEY,
    CoreExecutionSpec,
    bind_effective_core_capabilities,
)
from astrbot.core.interaction.core_bridge import get_core_task_spec
from astrbot.core.interaction.output_modes import OutputOrigin, temporary_output_origin
from astrbot.core.interaction.turn_state import is_interaction_turn_core_delegated
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
from astrbot.core.plugin_runtime import PLUGIN_RUNTIME_TARGET_CORE
from astrbot.core.provider.entities import (
    LLMResponse,
    ProviderRequest,
)
from astrbot.core.utils.metrics import Metric

from .....astr_agent_run_util import AgentRunner, run_agent, run_live_agent
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
        self.file_extract_prov: str = file_extract_conf.get("provider", "moonshotai")
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
        self.llm_compress_keep_recent: int = settings.get("llm_compress_keep_recent", 4)
        self.llm_compress_keep_recent_ratio: float | None = settings.get(
            "llm_compress_keep_recent_ratio"
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
        self.safety_mode_strategy = settings.get(
            "safety_mode_strategy", "system_prompt"
        )

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
            file_extract_prov=self.file_extract_prov,
            file_extract_msh_api_key=self.file_extract_msh_api_key,
            context_limit_reached_strategy=self.context_limit_reached_strategy,
            llm_compress_instruction=self.llm_compress_instruction,
            llm_compress_keep_recent=self.llm_compress_keep_recent,
            llm_compress_keep_recent_ratio=self.llm_compress_keep_recent_ratio,
            llm_compress_provider_id=self.llm_compress_provider_id,
            max_context_length=self.max_context_length,
            dequeue_context_length=self.dequeue_context_length,
            fallback_max_context_tokens=self.fallback_max_context_tokens,
            llm_safety_mode=self.llm_safety_mode,
            safety_mode_strategy=self.safety_mode_strategy,
            computer_use_runtime=self.computer_use_runtime,
            sandbox_cfg=self.sandbox_cfg,
            add_cron_tools=self.add_cron_tools,
            provider_settings=settings,
            subagent_orchestrator=conf.get("subagent_orchestrator", {}),
            timezone=self.ctx.plugin_manager.context.get_config().get("timezone"),
            max_quoted_fallback_images=settings.get("max_quoted_fallback_images", 20),
        )

    async def _send_llm_error_message(
        self, event: AstrMessageEvent, message: object
    ) -> None:
        await event.send(MessageChain().message(str(message)))

    async def process(
        self, event: AstrMessageEvent, provider_wake_prefix: str
    ) -> AsyncGenerator[None, None]:
        typing_requested = False
        agent_runner: AgentRunner | None = None
        req: ProviderRequest | None = None
        try:
            streaming_response = self.streaming_response
            if (enable_streaming := event.get_extra("enable_streaming")) is not None:
                streaming_response = bool(enable_streaming)

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
            request_lifecycle = AgentRequestLifecycle(
                event,
                execution_surface=PLUGIN_RUNTIME_TARGET_CORE,
                hook_dispatcher=call_event_hook,
                record_reasoning=True,
                dispatch_response_postprocess=True,
            )
            if await request_lifecycle.dispatch_waiting():
                return

            runner_registered = False
            try:
                build_cfg = replace(
                    self.main_agent_cfg,
                    provider_wake_prefix=provider_wake_prefix,
                    streaming_response=streaming_response,
                )

                build_result: MainAgentBuildResult | None = await build_main_agent(
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

                agent_runner = build_result.agent_runner
                req = build_result.provider_request
                provider = build_result.provider
                reset_coro = build_result.reset_coro
                request_lifecycle = (
                    build_result.request_lifecycle or request_lifecycle
                )
                request_lifecycle.bind_request(req)

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
                    self.unsupported_streaming_strategy == "turn_off"
                    and not event.platform_meta.support_streaming_message
                )

                if await request_lifecycle.dispatch_request():
                    if reset_coro:
                        reset_coro.close()
                    return

                effective_capabilities = CapabilityResolver().resolve_explicit_toolset(
                    event=event,
                    target=TOOL_TARGET_CORE,
                    toolset=req.func_tool if isinstance(req.func_tool, ToolSet) else ToolSet(),
                    persona_id=(
                        build_result.capabilities.persona_id
                        if build_result.capabilities is not None
                        else None
                    ),
                    selection_mode="request_hook",
                )
                req.func_tool = effective_capabilities.to_toolset()
                build_result.capabilities = effective_capabilities
                prompt_apply_result = request_lifecycle.prompt_apply_result
                if hasattr(prompt_apply_result, "tool_schema_count"):
                    prompt_apply_result.tool_schema_count = len(
                        effective_capabilities.tools
                    )
                if build_result.execution_spec is not None:
                    build_result.execution_spec = bind_effective_core_capabilities(
                        build_result.execution_spec,
                        effective_capabilities,
                    )
                    event.set_extra(
                        CORE_EXECUTION_SPEC_EXTRA_KEY,
                        build_result.execution_spec,
                    )

                # apply reset
                if reset_coro:
                    await reset_coro

                runtime_manager = self.ctx.personal_runtime_manager
                if runtime_manager is not None:
                    runner_registered = runtime_manager.register_active_runner(
                        event,
                        agent_runner,
                    )
                action_type = event.get_extra("action_type")

                event.trace.record(
                    "astr_agent_prepare",
                    request_lifecycle_id=request_lifecycle.lifecycle_id,
                    system_prompt=req.system_prompt,
                    tools=req.func_tool.names() if req.func_tool else [],
                    stream=streaming_response,
                    chat_provider={
                        "id": provider.provider_config.get("id", ""),
                        "model": provider.get_model(),
                    },
                )

                # 检测 Live Mode
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

                    # 使用 run_live_agent，总是使用流式响应
                    event.set_result(
                        MessageEventResult()
                        .set_result_content_type(ResultContentType.STREAMING_RESULT)
                        .set_async_stream(
                            run_live_agent(
                                agent_runner,
                                tts_provider,
                                self.max_step,
                                self.show_tool_use,
                                self.show_tool_call_result,
                                show_reasoning=self.show_reasoning,
                                buffer_intermediate_messages=self.buffer_intermediate_messages,
                            ),
                        ),
                    )
                    yield

                    # 保存历史记录
                    if agent_runner.done() and (
                        not event.is_stopped() or agent_runner.was_aborted()
                    ):
                        await self._save_to_history(
                            event,
                            req,
                            agent_runner.get_final_llm_resp(),
                            agent_runner.run_context.messages,
                            agent_runner.stats,
                            user_aborted=agent_runner.was_aborted(),
                        )

                elif streaming_response and not stream_to_general:
                    # 流式响应
                    event.set_result(
                        MessageEventResult()
                        .set_result_content_type(ResultContentType.STREAMING_RESULT)
                        .set_async_stream(
                            run_agent(
                                agent_runner,
                                self.max_step,
                                self.show_tool_use,
                                self.show_tool_call_result,
                                show_reasoning=self.show_reasoning,
                                buffer_intermediate_messages=self.buffer_intermediate_messages,
                            ),
                        ),
                    )
                    yield
                    if agent_runner.done():
                        if final_llm_resp := agent_runner.get_final_llm_resp():
                            if final_llm_resp.completion_text:
                                chain = (
                                    MessageChain()
                                    .message(final_llm_resp.completion_text)
                                    .chain
                                )
                            elif final_llm_resp.result_chain:
                                chain = final_llm_resp.result_chain.chain
                            else:
                                chain = MessageChain().chain
                            event.set_result(
                                MessageEventResult(
                                    chain=chain,
                                    result_content_type=ResultContentType.STREAMING_FINISH,
                                ),
                            )
                else:
                    async for _ in run_agent(
                        agent_runner,
                        self.max_step,
                        self.show_tool_use,
                        self.show_tool_call_result,
                        stream_to_general,
                        show_reasoning=self.show_reasoning,
                        buffer_intermediate_messages=self.buffer_intermediate_messages,
                    ):
                        yield

                final_resp = agent_runner.get_final_llm_resp()

                event.trace.record(
                    "astr_agent_complete",
                    request_lifecycle_id=request_lifecycle.lifecycle_id,
                    stats=agent_runner.stats.to_dict(),
                    resp=final_resp.completion_text if final_resp else None,
                )

                asyncio.create_task(
                    _record_internal_agent_stats(
                        event,
                        req,
                        agent_runner,
                        final_resp,
                    )
                )

                # 检查事件是否被停止，如果被停止则不保存历史记录
                if not event.is_stopped() or agent_runner.was_aborted():
                    await self._save_to_history(
                        event,
                        req,
                        final_resp,
                        agent_runner.run_context.messages,
                        agent_runner.stats,
                        user_aborted=agent_runner.was_aborted(),
                    )

                asyncio.create_task(
                    Metric.upload(
                        llm_tick=1,
                        model_name=agent_runner.provider.get_model(),
                        provider_type=agent_runner.provider.meta().type,
                    ),
                )
            finally:
                if runner_registered and agent_runner is not None:
                    runtime_manager = self.ctx.personal_runtime_manager
                    if runtime_manager is not None:
                        runtime_manager.unregister_active_runner(event, agent_runner)

        except Exception as e:
            logger.error(f"Error occurred while processing agent: {e}")
            await self._save_failed_interaction_core_state(
                event,
                req,
                agent_runner,
                e,
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
                event.set_extra("_core_execution_ledger_failed", True)
                event.set_extra("_core_execution_ledger_failure_reason", str(exc))
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
    ) -> None:
        """Persist Core telemetry and execution continuity, never visible dialogue."""
        if not req or not req.conversation:
            return

        execution_spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
        if not isinstance(execution_spec, CoreExecutionSpec):
            return
        if (
            event.get_extra("_core_execution_ledger_recorded_id")
            == execution_spec.execution_id
        ):
            return

        token_usage = (
            llm_response.usage.total
            if llm_response is not None and llm_response.usage is not None
            else None
        )
        if token_usage is not None:
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
        record = CoreExecutionLedgerRecord(
            execution_id=execution_spec.execution_id,
            conversation_id=req.conversation.cid,
            turn_id=execution_spec.turn_id,
            core_task_id=execution_spec.core_task_id,
            parent_execution_id=execution_spec.parent_execution_id,
            attempt=execution_spec.attempt,
            executor_id="native",
            status="aborted" if user_aborted else "completed",
            task_spec=execution_spec.task_spec,
            messages=messages,
            result=(
                llm_response.completion_text
                if llm_response is not None
                else ""
            ),
            token_usage=(
                runner_stats.token_usage.__dict__ if runner_stats is not None else None
            ),
        )
        await ledger.append(record)
        event.set_extra(
            "_core_execution_ledger_recorded_id",
            execution_spec.execution_id,
        )

    async def _save_failed_interaction_core_state(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest | None,
        agent_runner: AgentRunner | None,
        error: Exception,
    ) -> None:
        if (
            not event.get_extra("_interaction_enabled", False)
            or req is None
            or req.conversation is None
        ):
            return
        execution_spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
        if not isinstance(execution_spec, CoreExecutionSpec):
            return
        messages: list[dict] = []
        if agent_runner is not None:
            try:
                messages = _extract_core_execution_messages(
                    agent_runner.run_context.messages
                )
            except Exception:  # noqa: BLE001
                messages = []
        record = CoreExecutionLedgerRecord(
            execution_id=execution_spec.execution_id,
            conversation_id=req.conversation.cid,
            turn_id=execution_spec.turn_id,
            core_task_id=execution_spec.core_task_id,
            parent_execution_id=execution_spec.parent_execution_id,
            attempt=execution_spec.attempt,
            executor_id="native",
            status="failed",
            task_spec=execution_spec.task_spec,
            messages=messages,
            error=str(error),
        )
        try:
            ledger = self.ctx.plugin_manager.context.core_execution_ledger
            if ledger is None:
                return
            await ledger.append(record)
        except Exception:  # noqa: BLE001
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
    agent_runner: AgentRunner | None,
    final_resp: LLMResponse | None,
) -> None:
    """Persist internal agent stats without affecting the user response flow."""
    if agent_runner is None:
        return

    provider = agent_runner.provider
    stats = agent_runner.stats
    if provider is None or stats is None:
        return

    try:
        provider_config = getattr(provider, "provider_config", {}) or {}
        conversation_id = (
            req.conversation.cid
            if req is not None and req.conversation is not None
            else None
        )

        if agent_runner.was_aborted():
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
