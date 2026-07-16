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


class PersonaVisibleReplyCollector(ContextCollectorInterface):
    """Collect phase-local material consumed by the Persona render target."""

    def __init__(self, request: object) -> None:
        self.request = request

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        del event, plugin_context, config, provider_request
        request = self.request
        payload = {
            "source_text": str(getattr(request, "source_text", "") or "").strip(),
            "immediate_reply": str(
                getattr(request, "immediate_reply", "") or ""
            ).strip(),
            "delegated_task_summary": str(
                getattr(request, "delegated_task_summary", "") or ""
            ).strip(),
            "observed_text": str(
                getattr(request, "observed_text", "") or ""
            ).strip(),
            "total_text": str(getattr(request, "total_text", "") or "").strip(),
            "pending_text": str(
                getattr(request, "pending_text", "") or ""
            ).strip(),
            "preserve_facts": bool(getattr(request, "preserve_facts", False)),
            "short_reply": bool(getattr(request, "short_reply", False)),
            "allow_empty": bool(getattr(request, "allow_empty", False)),
        }
        payload = {
            key: value for key, value in payload.items() if value not in {"", False}
        }
        if not payload:
            return []
        return [
            ContextSlot(
                name="input.visible_reply_material",
                value=payload,
                category="input",
                source="interaction_visible_reply_material",
                render_mode="structured",
                meta={
                    "scope": "dynamic",
                    "node_type": "interaction_visible_reply_material",
                },
            )
        ]


InteractionConversationHistoryCollector = ConversationHistoryCollector
