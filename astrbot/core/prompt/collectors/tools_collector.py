"""
Tools context collector for prompt context packing.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from astrbot.core import logger
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextSlot
from ..interfaces.context_collector_inferface import ContextCollectorInterface

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class ToolsCollector(ContextCollectorInterface):
    """Collect the persona-resolved tool inventory without mutating runtime tools."""

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
        try:
            persona_id, persona = await self._resolve_persona(
                event,
                plugin_context,
                config,
                provider_request,
            )
            toolset, selection_mode = self._build_persona_toolset(
                plugin_context,
                persona,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect tool inventory: umo=%s error=%s",
                getattr(event, "unified_msg_origin", None),
                exc,
                exc_info=True,
            )
            return []

        if toolset.empty():
            return []

        return [self._build_tools_slot(toolset, persona_id, selection_mode)]

    async def _resolve_persona(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None,
    ) -> tuple[str | None, dict | None]:
        req = provider_request or event.get_extra("provider_request")
        conversation_persona_id = None
        if req and getattr(req, "conversation", None):
            conversation_persona_id = req.conversation.persona_id

        persona_mgr = getattr(plugin_context, "persona_manager", None)
        if persona_mgr is None:
            return None, None

        persona_id, persona, _, _ = await persona_mgr.resolve_selected_persona(
            umo=event.unified_msg_origin,
            conversation_persona_id=conversation_persona_id,
            platform_name=event.get_platform_name(),
            provider_settings=config.provider_settings,
        )
        if isinstance(persona, dict):
            return persona_id, persona
        return persona_id, None

    def _build_persona_toolset(
        self,
        plugin_context: Context,
        persona: dict | None,
    ) -> tuple[ToolSet, str]:
        tool_manager = plugin_context.get_llm_tool_manager()
        if tool_manager is None:
            return ToolSet(), "none"

        if (persona and persona.get("tools") is None) or not persona:
            full_toolset = tool_manager.get_full_tool_set()
            if not isinstance(full_toolset, ToolSet):
                raise TypeError(
                    f"Expected ToolSet from get_full_tool_set(), got {type(full_toolset)}"
                )

            active_toolset = ToolSet()
            for tool in full_toolset:
                if getattr(tool, "active", True):
                    active_toolset.add_tool(tool)
            return active_toolset, "all"

        persona_toolset = ToolSet()
        allowed_tools = persona.get("tools")
        if not isinstance(allowed_tools, list) or not allowed_tools:
            return persona_toolset, "none"

        for tool_name in allowed_tools:
            tool = tool_manager.get_func(tool_name)
            if tool is not None and getattr(tool, "active", True):
                persona_toolset.add_tool(tool)
        return persona_toolset, "whitelist"

    def _build_tools_slot(
        self,
        toolset: ToolSet,
        persona_id: str | None,
        selection_mode: str,
    ) -> ContextSlot:
        serialized_tools = [self._serialize_tool(tool) for tool in toolset]
        return ContextSlot(
            name="capability.tools_schema",
            value={
                "format": "tool_inventory_v1",
                "tool_count": len(serialized_tools),
                "tools": serialized_tools,
            },
            category="tools",
            source="tool_manager",
            meta={
                "format": "tool_inventory_v1",
                "tool_count": len(serialized_tools),
                "persona_id": persona_id,
                "selection_mode": selection_mode,
            },
        )

    def _serialize_tool(self, tool: FunctionTool) -> dict[str, object]:
        tool_schema = ToolSet([tool]).openai_schema()
        return {
            "name": tool.name,
            "description": tool.description,
            "parameters": deepcopy(tool.parameters),
            "active": bool(getattr(tool, "active", True)),
            "handler_module_path": getattr(tool, "handler_module_path", None),
            "schema": tool_schema[0] if tool_schema else None,
        }
