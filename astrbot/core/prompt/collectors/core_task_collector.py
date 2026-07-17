"""Collect delegated Core execution intent for the canonical prompt pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.core.core_execution_contract import (
    CORE_PERSONA_COORDINATION_INSTRUCTION,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextSlot
from ..interfaces.context_collector_inferface import ContextCollectorInterface

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class CoreTaskCollector(ContextCollectorInterface):
    """Expose middleware delegation intent without mutating ProviderRequest."""

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        del plugin_context, config, provider_request
        turn_state = event.get_extra("_interaction_turn_state")
        task_spec = getattr(turn_state, "core_task_spec", None)
        if task_spec is None:
            return []
        execution_prompt = getattr(task_spec, "execution_prompt", "")
        task_summary = getattr(task_spec, "task_summary", "")
        if not execution_prompt and not task_summary:
            return []
        return [
            ContextSlot(
                name="system.core_execution_context",
                value={
                    "instruction": CORE_PERSONA_COORDINATION_INSTRUCTION,
                    "platform_id": event.get_platform_id(),
                    "session_id": event.unified_msg_origin,
                    "task_intent": getattr(task_spec, "task_intent", ""),
                    "task_summary": task_summary,
                    "execution_prompt": execution_prompt,
                    "suggested_capabilities": getattr(
                        task_spec,
                        "suggested_capabilities",
                        [],
                    ),
                    "metadata": getattr(task_spec, "metadata", {}),
                },
                category="system",
                source="interaction_core_task",
                render_mode="structured",
                meta={"targets": ["core"]},
            )
        ]


__all__ = ["CoreTaskCollector"]
