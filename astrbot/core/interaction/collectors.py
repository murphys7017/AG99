from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.prompt.collectors import ConversationHistoryCollector
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


InteractionConversationHistoryCollector = ConversationHistoryCollector
