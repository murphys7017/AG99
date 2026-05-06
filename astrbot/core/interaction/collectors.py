from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot import logger
from astrbot.core.memory.history_source import (
    extract_turn_payloads,
    parse_conversation_history,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
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
    def __init__(self, store: InteractionMemoryStore) -> None:
        self.store = store

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
        return [
            ContextSlot(
                name="memory.interaction",
                value=build_interaction_memory_payload(snapshot),
                category="memory",
                source="interaction_memory",
                render_mode="structured",
                meta={"session_id": event.unified_msg_origin},
            )
        ]


class InteractionConversationHistoryCollector(ContextCollectorInterface):
    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        del config

        history_payload = await self._resolve_history_source(
            event,
            plugin_context,
            provider_request,
        )
        if history_payload is None:
            return []

        return [self._build_history_slot(provider_request, history_payload)]

    async def _resolve_history_source(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        provider_request: ProviderRequest | None,
    ) -> dict[str, object] | None:
        history_payload = await self._load_current_conversation_history(
            event,
            plugin_context,
        )
        if history_payload is not None:
            return history_payload

        if provider_request is None:
            return None

        conversation = getattr(provider_request, "conversation", None)
        if conversation is not None:
            history_payload = self._load_history_payload(
                raw_history=getattr(conversation, "history", None),
                source_name="provider_request.conversation.history",
            )
            if history_payload is not None:
                history_payload["conversation_id"] = getattr(conversation, "cid", None)
                return history_payload

        return self._load_history_payload(
            raw_history=getattr(provider_request, "contexts", None),
            source_name="provider_request.contexts",
        )

    async def _load_current_conversation_history(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
    ) -> dict[str, object] | None:
        conversation_manager = getattr(plugin_context, "conversation_manager", None)
        if conversation_manager is None:
            return None

        try:
            conversation_id = await conversation_manager.get_curr_conversation_id(
                event.unified_msg_origin
            )
            if not conversation_id:
                return None
            conversation = await conversation_manager.get_conversation(
                event.unified_msg_origin,
                conversation_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect interaction conversation history for umo=%s: %s",
                event.unified_msg_origin,
                exc,
                exc_info=True,
            )
            return None

        if conversation is None:
            return None

        history_payload = self._load_history_payload(
            raw_history=getattr(conversation, "history", None),
            source_name="conversation_manager.current_conversation.history",
        )
        if history_payload is None:
            return None
        history_payload["conversation_id"] = getattr(conversation, "cid", None)
        return history_payload

    def _load_history_payload(
        self,
        *,
        raw_history: str | list[dict] | None,
        source_name: str,
    ) -> dict[str, object] | None:
        try:
            messages = parse_conversation_history(raw_history)
            turns = extract_turn_payloads(messages)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect interaction conversation history from %s: %s",
                source_name,
                exc,
                exc_info=True,
            )
            return None

        if not turns:
            return None

        return {
            "source": source_name,
            "turns": turns,
        }

    @staticmethod
    def _build_history_slot(
        provider_request: ProviderRequest | None,
        history_payload: dict[str, object],
    ) -> ContextSlot:
        conversation_id = history_payload.get("conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            conversation_id = None
            if provider_request is not None and provider_request.conversation is not None:
                raw_conversation_id = getattr(provider_request.conversation, "cid", None)
                if (
                    isinstance(raw_conversation_id, str)
                    and raw_conversation_id.strip()
                ):
                    conversation_id = raw_conversation_id

        turns = history_payload["turns"]
        source_name = history_payload["source"]
        return ContextSlot(
            name="conversation.history",
            value={
                "format": "turn_pairs",
                "source": source_name,
                "conversation_id": conversation_id,
                "turn_count": len(turns),
                "turns": turns,
            },
            category="memory",
            source=source_name,
            meta={
                "format": "turn_pairs",
                "turn_count": len(turns),
            },
        )
