"""Core-owned assembly and driving for executor-neutral external turns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from astrbot.core.capabilities import CapabilitySnapshot
from astrbot.core.deadline import TurnDeadlineBudget
from astrbot.core.execution import (
    CoreExecutionDeadlineView,
    CoreExecutionHead,
    bind_core_execution_head,
)
from astrbot.core.interaction.executor_result_bridge import (
    drive_executor_to_personal_output,
)
from astrbot.core.prompt.targets import PromptTarget

from .assembly import build_codex_executor_assembly
from .contracts import ExecutionResult
from .external import ExternalExecutorRequest, prepare_external_executor_request
from .registry import resolve_executor_config


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
    runtime_config: Mapping[str, Any],
    config: Any,
    session_id: str,
    prompt_config: object,
    deadline: TurnDeadlineBudget,
    output_controller: Any,
    submission_metadata: dict[str, Any] | None = None,
) -> ExternalCoreExecutionResult:
    """Prepare, assemble, drive and output one configured external Body."""

    from astrbot.core.astr_main_agent import prepare_external_core_execution

    executor_id = _resolve_executor_id(runtime_config)
    if executor_id != "codex_cli":
        raise ValueError(f"unsupported external executor: {executor_id}")

    prepared = await prepare_external_core_execution(
        event=event,
        plugin_context=context,
        config=config,
        capabilities=CapabilitySnapshot.empty(target=PromptTarget.CORE.value),
    )
    head = bind_core_execution_head(event, prepared.execution_spec)
    head.bind_deadline_view(
        prepared.deadline_view or CoreExecutionDeadlineView.from_budget(deadline)
    )

    executor_config = dict(
        resolve_executor_config(runtime_config, executor_id=executor_id)
    )
    request = prepare_external_executor_request(
        execution_spec=prepared.execution_spec,
        deadline_view=prepared.deadline_view,
        executor_id=executor_id,
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


def _resolve_executor_id(runtime_config: Mapping[str, Any]) -> str:
    raw_core = runtime_config.get("core_execution") or {}
    if not isinstance(raw_core, Mapping):
        raise ValueError("core_execution must be an object")
    return str(raw_core.get("executor_id", "native") or "native").strip().lower()


def _runtime_config_id(event: Any) -> str:
    state = getattr(event, "get_extra", lambda *_args, **_kwargs: None)(
        "_astrbot_config_id", ""
    )
    return str(state or "default").strip() or "default"


__all__ = ["ExternalCoreExecutionResult", "execute_external_core_turn"]
