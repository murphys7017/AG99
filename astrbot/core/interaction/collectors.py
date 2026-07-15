from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.prompt.collectors import ConversationHistoryCollector
from astrbot.core.prompt.collectors.tools_collector import ToolsCollector
from astrbot.core.prompt.context_types import ContextSlot
from astrbot.core.prompt.interfaces.context_collector_inferface import (
    ContextCollectorInterface,
)
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from .memory_store import InteractionMemoryStore, build_interaction_memory_payload

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class InteractionMemoryCollector(ContextCollectorInterface):
    def __init__(
        self,
        store: InteractionMemoryStore,
        *,
        recent_turn_limit: int | None = None,
        brief: bool = False,
    ) -> None:
        self.store = store
        self.recent_turn_limit = recent_turn_limit
        self.brief = brief

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        del plugin_context, config, provider_request
        persona_id = str(event.get_extra("_interaction_persona_id", "") or "")
        snapshot = await self.store.load_interaction_memory(
            event.unified_msg_origin,
            persona_id,
        )
        payload = build_interaction_memory_payload(snapshot)
        if self.recent_turn_limit is not None:
            payload["recent_turns"] = payload["recent_turns"][
                : max(self.recent_turn_limit, 0)
            ]
        if self.brief:
            payload = {
                key: payload[key]
                for key in (
                    "recent_turns",
                    "recent_topics",
                    "ongoing_threads",
                    "last_impression_summary",
                )
            }
        return [
            ContextSlot(
                name="memory.interaction",
                value=payload,
                category="memory",
                source="interaction_memory",
                render_mode="structured",
                meta={"session_id": event.unified_msg_origin},
            )
        ]


class InteractionCapabilityCollector(ContextCollectorInterface):
    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        try:
            _, toolset, selection_mode = await ToolsCollector().resolve_toolset(
                event,
                plugin_context,
                config,
                provider_request,
            )
            active_tool_names = sorted(
                {
                    str(tool.name).strip()
                    for tool in toolset
                    if str(getattr(tool, "name", "")).strip()
                }
            )
        except Exception:  # noqa: BLE001
            active_tool_names = []
            selection_mode = "unavailable"
        get_platform_id = getattr(event, "get_platform_id", None)
        get_platform_name = getattr(event, "get_platform_name", None)
        platform_id = (
            get_platform_id()
            if callable(get_platform_id)
            else get_platform_name()
            if callable(get_platform_name)
            else ""
        )
        return [
            ContextSlot(
                name="capability.core_summary",
                value={
                    "tools_available": bool(active_tool_names),
                    "tool_count": len(active_tool_names),
                    "sample_tools": active_tool_names[:12],
                    "tool_selection_mode": selection_mode,
                    "knowledge_base_available": bool(
                        getattr(plugin_context, "kb_manager", None)
                    ),
                    "subagent_available": getattr(
                        plugin_context,
                        "subagent_orchestrator",
                        None,
                    )
                    is not None,
                    "platform_id": platform_id,
                },
                category="capability",
                source="interaction_capabilities",
                render_mode="structured",
            )
        ]


InteractionConversationHistoryCollector = ConversationHistoryCollector
