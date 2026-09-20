"""Shared execution path for proactive Core turns without a platform Event."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from astrbot import logger
from astrbot.core.agent.tool import ToolSet
from astrbot.core.core_request_preparation import (
    CoreRequestPreparationStopped,
    begin_core_request_lifecycle,
    finalize_core_request_preparation,
)
from astrbot.core.db.po import CoreExecutionRecord
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.plugin_admission import (
    build_plugin_admission_snapshot,
    resolve_event_plugins_name,
)
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.tools.message_tools import SendMessageToUserTool
from astrbot.core.utils.config_number import coerce_int_config

if TYPE_CHECKING:
    from astrbot.core.astr_agent_run_util import NativeExecutorAdapter
    from astrbot.core.cron.events import CronMessageEvent
    from astrbot.core.execution import CoreExecutionHead


@dataclass(slots=True)
class ProactiveAgentTurnResult:
    """Completed synthetic-event Core turn and the request used to run it."""

    event: CronMessageEvent
    request: ProviderRequest
    response: LLMResponse
    delivery_confirmed: bool = False


async def run_proactive_agent_turn(
    *,
    context: Any,
    session: MessageSession,
    message: str,
    extras: dict[str, Any],
    role: str | None,
    config: Any,
    system_prompt: str,
    prompt: str,
    require_delivery_tool: bool,
    include_history_fences: bool,
) -> ProactiveAgentTurnResult:
    """Run one proactive Core turn through the standard Main Agent builder.

    Cron and detached background tools have no ordinary platform Event after the
    originating turn. They still share the same synthetic event, history,
    optional delivery tool, Core build, and runner lifecycle.
    """
    # Kept local to avoid making the Core builder import this proactive helper.
    from astrbot.core.astr_agent_run_util import (
        NativeExecutionLoop,
        NativeExecutorAdapter,
    )
    from astrbot.core.astr_main_agent import _get_session_conv, build_main_agent
    from astrbot.core.cron.events import CronMessageEvent
    from astrbot.core.execution import (
        CoreExecutionDeadlineView,
        CoreExecutionSpec,
        bind_core_execution_head,
    )
    from astrbot.core.interaction.turn_state import (
        bind_interaction_turn_core_execution_journal,
        ensure_interaction_turn_state,
        get_interaction_turn_deadline,
        set_interaction_turn_runtime_config,
    )

    event = CronMessageEvent(
        context=context,
        session=session,
        message=message,
        extras=extras,
        message_type=session.message_type,
    )
    if role is not None:
        event.role = role

    runtime_config = set_interaction_turn_runtime_config(
        event, context.get_config(umo=str(session))
    )
    ensure_interaction_turn_state(event, turn_id=uuid.uuid4().hex)
    event.set_extra("_astrbot_config", runtime_config)
    event.plugins_name = resolve_event_plugins_name(runtime_config)
    await build_plugin_admission_snapshot(event=event)
    config = config.with_runtime_config(runtime_config)

    request = ProviderRequest()
    conversation = await _get_session_conv(event=event, plugin_context=context)
    request.conversation = conversation
    history = json.loads(conversation.history)
    if history and include_history_fences:
        request.contexts = history
        history_dump = request._print_friendly_context()
        request.contexts = []
        request.system_prompt += (
            "\n\nBellow is you and user previous conversation history:\n"
            f"---\n{history_dump}\n---\n"
        )

    request.system_prompt += system_prompt
    request.prompt = prompt
    if require_delivery_tool:
        request.func_tool = ToolSet()
        request.func_tool.add_tool(
            context.get_llm_tool_manager().get_builtin_tool(SendMessageToUserTool)
        )

    result = None
    native_executor: NativeExecutorAdapter | None = None
    execution_head: CoreExecutionHead | None = None
    executor_activated = False
    runner_reset_completed = False
    response = None
    status, error = "failed", None
    try:
        request_lifecycle = await begin_core_request_lifecycle(event)
        if request_lifecycle is None:
            event.set_extra("_core_request_preparation_stopped", True)
            raise CoreRequestPreparationStopped("request_stopped_by_plugin")
        result = await build_main_agent(
            event=event,
            plugin_context=context,
            config=config,
            req=request,
            apply_reset=False,
            request_lifecycle=request_lifecycle,
        )
        if result is None:
            raise RuntimeError("Proactive Core could not be built")
        if not await finalize_core_request_preparation(event, result):
            event.set_extra("_core_request_preparation_stopped", True)
            raise CoreRequestPreparationStopped("request_stopped_by_plugin")
        native_executor = NativeExecutorAdapter(result.agent_runner)
        execution_spec = getattr(result, "execution_spec", None)
        if isinstance(execution_spec, CoreExecutionSpec):
            execution_head = bind_core_execution_head(event, execution_spec)
            if deadline := get_interaction_turn_deadline(event):
                execution_head.bind_deadline_view(
                    CoreExecutionDeadlineView.from_budget(deadline)
                )
            bind_interaction_turn_core_execution_journal(event, execution_head)
        await result.reset_prepared_runner()
        runner_reset_completed = True
        if execution_head is not None:
            execution_head.activate_executor(
                executor_id=native_executor.executor_id,
                stop_callback=native_executor.request_stop,
                input_callback=native_executor.request_follow_up,
                submission_metadata={
                    "source": (
                        "cron"
                        if extras.get("cron_job")
                        else "background"
                    ),
                    "streaming": False,
                },
            )
            executor_activated = True
        provider_settings = config.provider_settings
        agent_max_step = coerce_int_config(
            provider_settings.get("max_agent_step", 30),
            default=30,
            min_value=1,
            field_name="provider_settings.max_agent_step",
        )
        native_loop = NativeExecutionLoop(
            native_executor,
            max_step=agent_max_step,
        )
        async for _ in native_loop.stream():
            pass
        response = native_executor.final_response()
        if (
            not native_executor.done()
            or native_executor.was_aborted()
            or response is None
            or response.role == "err"
        ):
            raise RuntimeError("Proactive Core did not complete successfully")
        if execution_head is not None:
            native_executor.finalize()
        status = "completed"
        return ProactiveAgentTurnResult(
            event=event,
            request=result.provider_request,
            response=response,
            delivery_confirmed=bool(event._has_send_oper),
        )
    except asyncio.CancelledError:
        status, error = "cancelled", "Proactive execution cancelled"
        if (
            native_executor is not None
            and execution_head is not None
            and executor_activated
        ):
            native_executor.cancel(metadata={"reason": "task_cancelled"})
        elif native_executor is not None and execution_head is not None:
            execution_head.cancel(
                executor_id=native_executor.executor_id,
                metadata={"reason": "task_cancelled"},
            )
        raise
    except Exception as exc:
        error = str(exc)[:2000]
        if (
            native_executor is not None
            and execution_head is not None
            and executor_activated
        ):
            native_executor.fail(
                metadata={
                    "error_type": type(exc).__name__,
                    "error": error,
                }
            )
        elif native_executor is not None and execution_head is not None:
            execution_head.fail(
                executor_id=native_executor.executor_id,
                metadata={
                    "error_type": type(exc).__name__,
                    "error": error,
                },
            )
        raise
    finally:
        if result is not None:
            result.discard_pending_reset()
        # Execution evidence is not visible dialogue. Personal owns that history.
        ledger = getattr(context, "core_execution_ledger", None)
        if ledger is not None:
            spec = getattr(result, "execution_spec", None)
            turn_id = str(event.get_extra("_turn_id"))
            cron = extras.get("cron_job", {})
            background = extras.get("background_task_result", {})
            ledger_result = response.completion_text if response is not None else None
            ledger_error = error
            if (
                execution_head is not None
                and executor_activated
                and runner_reset_completed
            ):
                preparation = execution_head.prepare_ledger_preparation(
                    completion_text=ledger_result,
                    user_aborted=(
                        native_executor.was_aborted()
                        if native_executor is not None
                        else False
                    ),
                    fallback_status=status,
                    fallback_error=error,
                )
                status = preparation.status
                ledger_result = preparation.result
                ledger_error = preparation.error
            record = CoreExecutionRecord(
                execution_id=spec.execution_id if spec else uuid.uuid4().hex,
                conversation_id=conversation.cid,
                turn_id=turn_id,
                core_task_id=str(
                    cron.get("id") or background.get("task_id") or turn_id
                ),
                status=status,
                task_spec={
                    "source": "cron" if cron else "background",
                    "schedule_revision": cron.get("revision"),
                    "schedule_execution_id": cron.get("execution_id"),
                    "message": message,
                    "background_result": background,
                    "delivery_confirmed": bool(event._has_send_oper),
                },
                result=ledger_result,
                error=ledger_error,
            )
            try:
                if execution_head is not None:
                    await execution_head.settle_ledger(
                        lambda: ledger.append(record)
                    )
                else:
                    await ledger.append(record)
            except Exception:
                logger.exception(
                    "Proactive execution ledger persistence failed: execution_id=%s",
                    record.execution_id,
                )
        if (
            native_executor is not None
            and execution_head is not None
            and executor_activated
        ):
            native_executor.release_from_core_head()


__all__ = ["ProactiveAgentTurnResult", "run_proactive_agent_turn"]
