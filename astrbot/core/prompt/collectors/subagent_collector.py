"""
Subagent context collector for prompt context packing.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from astrbot.core import logger
from astrbot.core.agent.handoff import HandoffTool
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextSlot
from ..interfaces.context_collector_inferface import ContextCollectorInterface

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class SubagentCollector(ContextCollectorInterface):
    """Collect subagent handoff tools and router prompt as structured context."""

    @property
    def lifecycle(self) -> str:
        return "static"

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        del event, config, provider_request

        try:
            orchestrator_config = self._resolve_orchestrator_config(plugin_context)
            if not orchestrator_config.get("main_enable", False):
                return []

            orchestrator = getattr(plugin_context, "subagent_orchestrator", None)
            if orchestrator is None:
                return []

            slots: list[ContextSlot] = []

            handoff_tools_slot = self._build_handoff_tools_slot(
                orchestrator_config,
                getattr(orchestrator, "handoffs", []),
            )
            if handoff_tools_slot is not None:
                slots.append(handoff_tools_slot)

            router_prompt_slot = self._build_router_prompt_slot(orchestrator_config)
            if router_prompt_slot is not None:
                slots.append(router_prompt_slot)

            return slots
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect subagent context: error=%s",
                exc,
                exc_info=True,
            )
            return []

    def _resolve_orchestrator_config(self, plugin_context: Context) -> dict:
        raw_config = plugin_context.get_config().get("subagent_orchestrator", {})
        if isinstance(raw_config, dict):
            return raw_config
        return {}

    def _build_handoff_tools_slot(
        self,
        orchestrator_config: dict,
        handoffs: object,
    ) -> ContextSlot | None:
        if not isinstance(handoffs, list) or not handoffs:
            return None

        serialized_tools: list[dict[str, object]] = []
        for handoff in handoffs:
            serialized_tool = self._serialize_handoff_tool(handoff)
            if serialized_tool is not None:
                serialized_tools.append(serialized_tool)

        if not serialized_tools:
            return None

        main_enable = bool(orchestrator_config.get("main_enable", False))
        remove_duplicates = bool(
            orchestrator_config.get("remove_main_duplicate_tools", False)
        )

        return ContextSlot(
            name="capability.subagent_handoff_tools",
            value={
                "format": "handoff_tools_v1",
                "main_enable": main_enable,
                "remove_main_duplicate_tools": remove_duplicates,
                "tool_count": len(serialized_tools),
                "tools": serialized_tools,
            },
            category="tools",
            source="subagent_orchestrator",
            meta={
                "format": "handoff_tools_v1",
                "tool_count": len(serialized_tools),
                "main_enable": main_enable,
                "remove_main_duplicate_tools": remove_duplicates,
            },
        )

    def _build_router_prompt_slot(
        self,
        orchestrator_config: dict,
    ) -> ContextSlot | None:
        router_prompt = orchestrator_config.get("router_system_prompt")
        if not isinstance(router_prompt, str):
            return None

        router_prompt = router_prompt.strip()
        if not router_prompt:
            return None

        main_enable = bool(orchestrator_config.get("main_enable", False))
        return ContextSlot(
            name="capability.subagent_router_prompt",
            value=router_prompt,
            category="tools",
            source="subagent_orchestrator",
            meta={
                "enabled_by_config": True,
                "main_enable": main_enable,
                "source": "subagent_orchestrator.router_system_prompt",
            },
        )

    def _serialize_handoff_tool(self, handoff: object) -> dict[str, object] | None:
        if not isinstance(handoff, HandoffTool):
            return None

        return {
            "name": handoff.name,
            "description": handoff.description,
            "parameters": deepcopy(handoff.parameters),
        }
