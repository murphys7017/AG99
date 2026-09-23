"""Production assembly for the current Native executor implementation.

This module is the one place where a selected executor id is paired with an
already-built Native runner.  Ordinary Interaction keeps its Native rich
output bridge, while proactive execution can additionally create the neutral
``ExecutorRun`` consumed by the shared coordinator.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .codex_cli import build_codex_executor_run
from .contracts import ExecutorRun
from .external import ExternalExecutorRequest
from .registry import resolve_executor_factory
from .session_registry import ExternalExecutorSessionRegistry

if TYPE_CHECKING:
    from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
    from astrbot.core.astr_agent_context import AstrAgentContext
    from astrbot.core.astr_agent_run_util import (
        NativeExecutionAttachment,
        NativeExecutorAdapter,
    )
    from astrbot.core.execution import CoreExecutionPort


@dataclass(frozen=True, slots=True)
class NativeExecutorAssembly:
    """Selected Native Body plus the two supported execution projections."""

    executor_id: str
    attachment: NativeExecutionAttachment
    executor: NativeExecutorAdapter

    def build_run(
        self,
        *,
        max_step: int,
        should_stop: Callable[[], bool] | None = None,
    ) -> ExecutorRun:
        """Create the neutral run through the registered executor factory."""

        run = resolve_executor_factory(self.executor_id)(
            executor=self.executor,
            max_step=max_step,
            should_stop=should_stop,
        )
        if run.executor_id != self.executor_id:
            raise RuntimeError(
                "executor factory returned a mismatched executor_id: "
                f"expected={self.executor_id} actual={run.executor_id}"
            )
        return run


@dataclass(frozen=True, slots=True)
class CodexExecutorAssembly:
    """Production pairing of an external request with the Codex Body."""

    session: object

    def build_run(self, request: ExternalExecutorRequest) -> ExecutorRun:
        if request.session_key.executor_id != "codex_cli":
            raise ValueError(
                "Codex assembly requires executor_id=codex_cli, got "
                f"{request.session_key.executor_id}"
            )
        return build_codex_executor_run(session=self.session, prompt=request.prompt)


async def build_codex_executor_assembly(
    *,
    request: ExternalExecutorRequest,
    registry: ExternalExecutorSessionRegistry,
    executor_config: dict,
) -> CodexExecutorAssembly:
    """Resolve the Core-owned long-lived session for one prepared request."""

    if request.session_key.executor_id != "codex_cli":
        raise ValueError(
            "Codex assembly requires executor_id=codex_cli, got "
            f"{request.session_key.executor_id}"
        )
    session = await registry.get_or_create(
        key=request.session_key,
        executor_config=executor_config,
    )
    return CodexExecutorAssembly(session=session)


def build_native_executor_assembly(
    *,
    executor_id: str,
    runner: ToolLoopAgentRunner[AstrAgentContext],
    core_port: CoreExecutionPort | None = None,
) -> NativeExecutorAssembly:
    """Attach the selected Native runner without leaking setup into callers.

    Non-Native implementations cannot use a prebuilt ``ToolLoopAgentRunner``.
    They must get their own concrete assembly function when their adapter is
    introduced, rather than silently constructing an unused Native runner.
    """

    normalized = str(executor_id or "").strip().lower()
    resolve_executor_factory(normalized)
    if normalized != "native":
        raise RuntimeError(
            "selected executor is not available on the Native assembly path: "
            f"{normalized}"
        )

    from astrbot.core.astr_agent_run_util import NativeExecutionAttachment

    attachment = NativeExecutionAttachment.from_runner(runner, core_port=core_port)
    return NativeExecutorAssembly(
        executor_id=normalized,
        attachment=attachment,
        executor=attachment.executor,
    )


__all__ = [
    "CodexExecutorAssembly",
    "NativeExecutorAssembly",
    "build_codex_executor_assembly",
    "build_native_executor_assembly",
]
