from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from astrbot import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .plugin_execution_runtime import PluginExecutionRuntime
from .plugin_execution_types import (
    PluginBranchResult,
    PluginDeliveryDisposition,
    PluginOutputArtifact,
)
from .turn_state import (
    InteractionFinalOutputStatus,
    finish_interaction_turn_final_output,
    reserve_interaction_turn_final_output,
)


class PluginOutputSink(Protocol):
    async def capture_plugin_output(
        self,
        message: Any,
        event: AstrMessageEvent,
        *,
        mode: str,
        finalize: bool,
        platform_extras: dict[str, Any] | None = None,
    ) -> None: ...


@dataclass(slots=True)
class PluginInlineDeliverySummary:
    delivered_artifact_count: int = 0
    suppressed_artifact_count: int = 0
    final_output_claimed: bool = False
    final_output_delivered: bool = False


class PluginArtifactDeliveryCoordinator:
    """Deliver branch artifacts through one Runtime ledger and Output Controller."""

    def __init__(
        self,
        runtime: PluginExecutionRuntime,
        output_controller: PluginOutputSink,
    ) -> None:
        self.runtime = runtime
        self.output_controller = output_controller

    async def deliver_inline(
        self,
        event: AstrMessageEvent,
        result: PluginBranchResult,
        *,
        claim_final_output: bool,
    ) -> PluginInlineDeliverySummary:
        summary = PluginInlineDeliverySummary()
        artifacts = list(result.output_artifacts)
        if result.t1_artifact_count is not None:
            artifacts = artifacts[: result.t1_artifact_count]
        final_artifacts = [
            artifact
            for artifact in artifacts
            if artifact.kind.value != "progress" and artifact.finalize
        ]
        if final_artifacts and not claim_final_output:
            raise ValueError(
                "T1 final Plugin artifacts require final-output reservation"
            )
        if claim_final_output and final_artifacts:
            summary.final_output_claimed = (
                await reserve_interaction_turn_final_output(event)
            )
        reserved_artifact_key = None
        try:
            for artifact in artifacts:
                if not await self._reserve_artifact(artifact):
                    summary.suppressed_artifact_count += 1
                    continue
                reserved_artifact_key = artifact.delivery_key
                is_last_final = (
                    artifact is final_artifacts[-1] if final_artifacts else False
                )
                finalize = bool(
                    artifact.finalize
                    and (
                        not claim_final_output
                        or summary.final_output_claimed
                        and is_last_final
                    )
                )
                if (
                    claim_final_output
                    and artifact.finalize
                    and not summary.final_output_claimed
                ):
                    await self.runtime.finish_delivery(
                        artifact.delivery_key,
                        PluginDeliveryDisposition.SUPPRESSED_DUPLICATE,
                    )
                    reserved_artifact_key = None
                    summary.suppressed_artifact_count += 1
                    continue
                await self.output_controller.capture_plugin_output(
                    artifact.message,
                    event,
                    mode=artifact.mode,
                    finalize=finalize,
                    platform_extras=dict(artifact.metadata),
                )
                await self.runtime.finish_delivery(
                    artifact.delivery_key,
                    PluginDeliveryDisposition.DELIVERED_INLINE,
                )
                reserved_artifact_key = None
                summary.delivered_artifact_count += 1
                if finalize:
                    summary.final_output_delivered = True
        except BaseException:
            if reserved_artifact_key is not None:
                try:
                    await self.runtime.finish_delivery(
                        reserved_artifact_key,
                        PluginDeliveryDisposition.DELIVERY_FAILED,
                    )
                except Exception:
                    logger.exception(
                        "Failed to close Plugin artifact reservation after delivery error"
                    )
            if summary.final_output_claimed:
                try:
                    await finish_interaction_turn_final_output(
                        event,
                        InteractionFinalOutputStatus.FAILED,
                    )
                except Exception:
                    logger.exception(
                        "Failed to close Plugin final-output reservation after delivery error"
                    )
            raise

        if summary.final_output_claimed:
            await finish_interaction_turn_final_output(
                event,
                (
                    InteractionFinalOutputStatus.DELIVERED
                    if summary.final_output_delivered
                    else InteractionFinalOutputStatus.SUPPRESSED
                ),
            )
        return summary

    async def _reserve_artifact(self, artifact: PluginOutputArtifact) -> bool:
        await self.runtime.register_delivery(artifact.delivery_key)
        return await self.runtime.reserve_delivery(artifact.delivery_key)
