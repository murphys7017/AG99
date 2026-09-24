"""Core-owned assembly and driving for executor-neutral external turns."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from astrbot import logger
from astrbot.core.capabilities import CapabilitySnapshot
from astrbot.core.deadline import TurnDeadlineBudget, TurnDeadlineExceeded
from astrbot.core.execution import (
    CoreExecutionDeadlineView,
    CoreExecutionHead,
    bind_core_execution_head,
)
from astrbot.core.interaction.executor_result_bridge import (
    drive_executor_to_personal_output,
)
from astrbot.core.interaction.turn_state import (
    bind_interaction_turn_core_execution_journal,
    get_interaction_turn_state,
)
from astrbot.core.prompt.targets import PromptTarget

from .assembly import build_codex_executor_assembly
from .contracts import ExecutionResult
from .external import ExternalExecutorRequest, prepare_external_executor_request
from .registry import SelectedCoreExecutor


@dataclass(frozen=True, slots=True)
class ExternalCoreExecutionResult:
    """Result and identity facts returned to a production Core caller."""

    request: ExternalExecutorRequest
    result: ExecutionResult
    head: CoreExecutionHead


async def execute_external_core_turn(
    *,
    context: Any,
    event: Any,
    selected_executor: SelectedCoreExecutor,
    config: Any,
    session_id: str,
    prompt_config: object,
    deadline: TurnDeadlineBudget,
    output_controller: Any,
    submission_metadata: dict[str, Any] | None = None,
    on_started: Callable[[CoreExecutionHead, str], None] | None = None,
) -> ExternalCoreExecutionResult:
    """Prepare, assemble, drive and output one configured external Body."""

    from astrbot.core.astr_main_agent import prepare_external_core_execution

    executor_id = selected_executor.executor_id
    if executor_id != "codex_cli":
        raise ValueError(f"unsupported external executor: {executor_id}")

    prepared = await prepare_external_core_execution(
        event=event,
        plugin_context=context,
        config=config,
        capabilities=CapabilitySnapshot.empty(target=PromptTarget.CORE.value),
    )
    head = bind_core_execution_head(event, prepared.execution_spec)
    try:
        if getattr(event, "get_extra", lambda *_args, **_kwargs: False)(
            "_interaction_enabled", False
        ) and not bind_interaction_turn_core_execution_journal(event, head):
            raise RuntimeError(
                "External Core execution could not bind the Interaction journal"
            )
        head.bind_deadline_view(
            prepared.deadline_view or CoreExecutionDeadlineView.from_budget(deadline)
        )

        executor_config = dict(selected_executor.config)
        logger.info(
            "Starting external Core executor in restricted capability mode: "
            "executor_id=%s capability_count=0 astrbot_tool_bridge=false",
            executor_id,
        )
        request = prepare_external_executor_request(
            execution_spec=prepared.execution_spec,
            deadline_view=prepared.deadline_view,
            executor_id=executor_id,
            executor_instance_id=selected_executor.instance_id or "",
            runtime_config_id=_runtime_config_id(event),
            session_id=session_id,
            workspace_config=executor_config,
            prompt_config=prompt_config,
        )
        registry = getattr(context, "external_executor_sessions", None)
        if registry is None:
            raise RuntimeError("Core external executor session registry is unavailable")
        assembly = await build_codex_executor_assembly(
            request=request,
            registry=registry,
            executor_config=executor_config,
        )
        run = assembly.build_run(request)
        if on_started is not None:
            on_started(head, run.executor_id)
        result = await drive_executor_to_personal_output(
            event=event,
            output_controller=output_controller,
            head=head,
            body=run,
            run=run,
            deadline=deadline,
            submission_metadata=submission_metadata,
        )
        return ExternalCoreExecutionResult(request=request, result=result, head=head)
    except TurnDeadlineExceeded as exc:
        if head.terminal_event is None:
            head.cancel_for_deadline(
                executor_id=executor_id,
                stage=exc.stage,
                metadata={"reason": exc.reason, "error": str(exc)},
            )
        raise
    except asyncio.CancelledError:
        if head.terminal_event is None:
            head.cancel(
                executor_id=executor_id,
                metadata={"reason": "external_execution_cancelled_before_completion"},
            )
        raise
    except Exception as exc:
        if head.terminal_event is None:
            head.fail(
                executor_id=executor_id,
                metadata={
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:2000],
                    "phase": "external_executor_preparation_or_run",
                },
            )
        raise


def _runtime_config_id(event: Any) -> str:
    turn_state = get_interaction_turn_state(event)
    if turn_state is not None and turn_state.runtime_config_id.strip():
        return turn_state.runtime_config_id.strip()
    state = getattr(event, "get_extra", lambda *_args, **_kwargs: None)(
        "_astrbot_config_id", ""
    )
    config_id = str(state or "").strip()
    if not config_id:
        raise RuntimeError(
            "External Core execution requires an explicit runtime config identity"
        )
    return config_id


__all__ = ["ExternalCoreExecutionResult", "execute_external_core_turn"]
