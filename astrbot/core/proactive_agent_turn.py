"""Shared execution path for proactive Core turns without a platform Event."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping
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
from astrbot.core.deadline import TurnDeadlineBudget, TurnDeadlineExceeded
from astrbot.core.interaction.config import load_interaction_agent_config
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.plugin_admission import (
    build_plugin_admission_snapshot,
    resolve_event_plugins_name,
)
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.tools.message_tools import SendMessageToUserTool
from astrbot.core.utils.config_number import coerce_int_config

if TYPE_CHECKING:
    from astrbot.core.astr_agent_run_util import (
        NativeExecutorAdapter,
    )
    from astrbot.core.cron.events import CronMessageEvent
    from astrbot.core.execution import CoreExecutionHead


@dataclass(slots=True)
class ProactiveAgentTurnResult:
    """Completed synthetic-event Core turn and the request used to run it."""

    event: CronMessageEvent
    request: ProviderRequest
    response: LLMResponse
    delivery_confirmed: bool = False


def _ensure_proactive_execution_deadline(
    event: Any,
    runtime_config: Mapping[str, Any],
) -> TurnDeadlineBudget:
    from astrbot.core.interaction.turn_state import ensure_interaction_turn_state

    state = ensure_interaction_turn_state(event)
    if state.deadline is None:
        timeout = load_interaction_agent_config(runtime_config).turn_timeout
        state.deadline = TurnDeadlineBudget.start(timeout)
    return state.deadline


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
    from astrbot.core.astr_main_agent import _get_session_conv, build_main_agent
    from astrbot.core.cron.events import CronMessageEvent
    from astrbot.core.execution import (
        CoreExecutionDeadlineView,
        CoreExecutionSpec,
        bind_core_execution_head,
    )
    from astrbot.core.executors.assembly import build_native_executor_assembly
    from astrbot.core.executors.registry import resolve_executor_id
    from astrbot.core.executors.runtime import drive_executor_run
    from astrbot.core.interaction.turn_state import (
        bind_interaction_turn_core_execution_journal,
        ensure_interaction_turn_state,
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
    executor_id = resolve_executor_id(
        runtime_config,
        execution_source="proactive",
    )
    if executor_id != "native":
        raise RuntimeError(
            "selected executor is not available on the Native proactive path: "
            f"{executor_id}"
        )
    ensure_interaction_turn_state(event, turn_id=uuid.uuid4().hex)
    event.set_extra("_astrbot_config", runtime_config)
    deadline = _ensure_proactive_execution_deadline(event, runtime_config)
    event.plugins_name = resolve_event_plugins_name(runtime_config)
    request = ProviderRequest()
    conversation = None
    result = None
    native_executor: NativeExecutorAdapter | None = None
    execution_head: CoreExecutionHead | None = None
    executor_activated = False
    runner_reset_completed = False
    response = None
    status, error = "failed", None

    async def execute() -> ProactiveAgentTurnResult:
        nonlocal config
        nonlocal conversation
        nonlocal executor_activated
        nonlocal execution_head
        nonlocal native_executor
        nonlocal response
        nonlocal result
        nonlocal runner_reset_completed
        nonlocal status

        await build_plugin_admission_snapshot(event=event)
        config = config.with_runtime_config(runtime_config)
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
                context.get_llm_tool_manager().get_builtin_tool(
                    SendMessageToUserTool
                )
            )

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
        execution_spec = getattr(result, "execution_spec", None)
        if isinstance(execution_spec, CoreExecutionSpec):
            execution_head = bind_core_execution_head(event, execution_spec)
            deadline_view = getattr(
                getattr(result, "prepared_execution", None),
                "deadline_view",
                None,
            )
            if deadline_view is not None:
                execution_head.bind_deadline_view(deadline_view)
            else:
                execution_head.bind_deadline_view(
                    CoreExecutionDeadlineView.from_budget(deadline)
                )
            bind_interaction_turn_core_execution_journal(event, execution_head)
        native_assembly = build_native_executor_assembly(
            executor_id=executor_id,
            runner=result.agent_runner,
            core_port=execution_head,
        )
        native_executor = native_assembly.executor
        await result.reset_prepared_runner()
        runner_reset_completed = True
        provider_settings = config.provider_settings
        agent_max_step = coerce_int_config(
            provider_settings.get("max_agent_step", 30),
            default=30,
            min_value=1,
            field_name="provider_settings.max_agent_step",
        )
        executor_run = native_assembly.build_run(
            max_step=agent_max_step,
            should_stop=event.is_stopped,
        )
        # The shared coordinator owns activation, terminal projection, and
        # release. This flag records that the reset Native body reached that
        # boundary, so the existing Ledger preparation can read Head facts.
        executor_activated = execution_head is not None
        await drive_executor_run(
            head=execution_head,
            body=native_executor,
            run=executor_run,
            deadline=deadline,
            submission_metadata={
                "source": "cron" if extras.get("cron_job") else "background",
                "executor_id": executor_id,
                "streaming": False,
            },
        )
        response = native_executor.final_response()
        if (
            not native_executor.done()
            or native_executor.was_aborted()
            or response is None
            or response.role == "err"
        ):
            raise RuntimeError("Proactive Core did not complete successfully")
        status = "completed"
        return ProactiveAgentTurnResult(
            event=event,
            request=result.provider_request,
            response=response,
            delivery_confirmed=bool(event._has_send_oper),
        )

    try:
        async with deadline.enforce("proactive_core_execution"):
            return await execute()
    except TurnDeadlineExceeded as exc:
        status, error = "cancelled", str(exc)
        event.set_extra("_proactive_core_deadline_exceeded", True)
        if (
            execution_head is not None
            and native_executor is not None
            and execution_head.terminal_event is None
        ):
            execution_head.cancel(
                executor_id=native_executor.executor_id,
                metadata={"reason": "deadline_exceeded", "error": str(exc)},
            )
        raise
    except asyncio.CancelledError:
        status, error = "cancelled", "Proactive execution cancelled"
        if (
            execution_head is not None
            and native_executor is not None
            and execution_head.terminal_event is None
        ):
            execution_head.cancel(
                executor_id=native_executor.executor_id,
                metadata={"reason": "task_cancelled"},
            )
        raise
    except Exception as exc:
        error = str(exc)[:2000]
        if (
            execution_head is not None
            and native_executor is not None
            and execution_head.terminal_event is None
        ):
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
        try:
            # Execution evidence is not visible dialogue. Personal owns that history.
            ledger = getattr(context, "core_execution_ledger", None)
            if ledger is not None:
                spec = getattr(result, "execution_spec", None)
                turn_id = str(event.get_extra("_turn_id"))
                cron = extras.get("cron_job", {})
                background = extras.get("background_task_result", {})
                ledger_result = (
                    response.completion_text if response is not None else None
                )
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
                if conversation is None:
                    event.set_extra("_proactive_conversation_unavailable", True)
                    logger.warning(
                        "Proactive execution ended before conversation resolution: "
                        "turn_id=%s status=%s",
                        turn_id,
                        status,
                    )
                else:
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
                    if execution_head is not None:
                        await execution_head.settle_ledger(
                            lambda: ledger.append(record)
                        )
                    else:
                        await ledger.append(record)
        except Exception:
            logger.exception("Proactive execution ledger persistence failed")


__all__ = ["ProactiveAgentTurnResult", "run_proactive_agent_turn"]
