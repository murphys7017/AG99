"""Bridge executor-neutral final results into the existing Interaction output path."""

from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core import logger
from astrbot.core.execution import CoreExecutionEventKind, CoreExecutionHead
from astrbot.core.executors.contracts import (
    ExecutionFinalUpdate,
    ExecutionOutputUpdate,
    ExecutionResult,
    ExecutionUpdate,
)
from astrbot.core.message.message_event_result import MessageChain

from .output_controller import InteractionOutputController


@dataclass(slots=True)
class ExecutionResultOutputBridge:
    """Accept visible executor result text without exposing platform delivery.

    ``AssetRef`` deliberately contains safe identity metadata rather than a
    sendable local path or URL. Assets therefore remain Core evidence until a
    concrete Body supplies an explicit safe attachment-delivery contract.
    """

    event: object
    output_controller: InteractionOutputController
    head: CoreExecutionHead
    _final_delivery_started: bool = field(default=False, init=False)

    async def accept(self, update: ExecutionUpdate) -> None:
        """Handle result-bearing updates while the execution is still current."""

        if not self._can_accept_result():
            logger.debug(
                "Discarding executor result after Core termination: execution_id=%s",
                self.head.spec.execution_id,
            )
            return
        if isinstance(update, ExecutionOutputUpdate):
            # Incremental generic output cannot safely reproduce the existing
            # streaming/TTS protocol yet. It remains non-visible until that
            # protocol is migrated explicitly.
            return
        if isinstance(update, ExecutionFinalUpdate):
            await self.deliver_final(update.result)

    async def deliver_final(self, result: ExecutionResult) -> bool:
        """Deliver final text via Personal's existing Core-final expression path."""

        if not self._can_accept_result():
            return False
        output = result.output
        text = str(output.text or "").strip() if output is not None else ""
        if not text or self._final_delivery_started:
            return False
        self._final_delivery_started = True
        if output is not None and output.assets:
            logger.info(
                "Executor result contains deferred asset references: "
                "execution_id=%s asset_count=%s",
                self.head.spec.execution_id,
                len(output.assets),
            )
        await self.output_controller.deliver_core_execution_result(
            MessageChain().message(text),
            self.event,
        )
        return True

    def _can_accept_result(self) -> bool:
        """Allow a just-completed result, but reject replaced executions."""

        terminal = self.head.terminal_event
        if terminal is None:
            return True
        return getattr(terminal, "kind", None) is CoreExecutionEventKind.COMPLETED


__all__ = ["ExecutionResultOutputBridge"]
