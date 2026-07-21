"""
Conversation history collector for prompt context packing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from astrbot.core import logger
from astrbot.core.memory.config import get_memory_config
from astrbot.core.memory.history_source import (
    extract_turn_payloads,
    normalize_message_payload,
    parse_conversation_history,
)
from astrbot.core.memory.service import get_memory_service
from astrbot.core.memory.types import TurnRecord
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextSlot
from ..interfaces.context_collector_inferface import ContextCollectorInterface

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class ConversationHistoryCollector(ContextCollectorInterface):
    def __init__(self, *, recent_turn_limit: int | None = None) -> None:
        self.recent_turn_limit = recent_turn_limit

    """Collect the current conversation history as normalized turn pairs."""

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        history_payload = await self._resolve_history_source(
            event,
            plugin_context,
            config,
            provider_request,
        )
        if history_payload is None:
            return []

        history_payload = self._truncate_history_payload(history_payload, config)
        return [self._build_history_slot(provider_request, history_payload)]

    async def _resolve_history_source(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None,
    ) -> dict[str, Any] | None:
        conversation_payload = await self._load_current_conversation_history(
            event,
            plugin_context,
        )
        if conversation_payload is not None:
            return conversation_payload

        memory_payload = await self._load_memory_turn_records(
            event,
            config,
            provider_request,
        )
        if memory_payload is not None:
            return memory_payload

        if provider_request is None:
            return None

        conversation = getattr(provider_request, "conversation", None)
        if conversation is not None:
            history_payload = self._load_conversation_history(
                raw_history=getattr(conversation, "history", None),
                source_name="provider_request.conversation.history",
            )
            if history_payload is not None:
                return history_payload

        return self._load_conversation_history(
            raw_history=getattr(provider_request, "contexts", None),
            source_name="provider_request.contexts",
        )

    async def _load_current_conversation_history(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
    ) -> dict[str, Any] | None:
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
                "Failed to collect current official conversation history: umo=%s error=%s",
                event.unified_msg_origin,
                exc,
                exc_info=True,
            )
            return None

        if conversation is None:
            return None
        payload = self._load_conversation_history(
            raw_history=getattr(conversation, "history", None),
            source_name="conversation_manager.current_conversation.history",
        )
        if payload is not None:
            payload["conversation_id"] = getattr(conversation, "cid", conversation_id)
        return payload

    async def _load_memory_turn_records(
        self,
        event: AstrMessageEvent,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None,
    ) -> dict[str, Any] | None:
        umo = getattr(event, "unified_msg_origin", None)
        if not isinstance(umo, str) or not umo.strip():
            return None

        event_config = event.get_extra("_astrbot_config")
        if not isinstance(event_config, dict):
            event_config = None
        memory_config = get_memory_config(event_config)
        if not memory_config.enabled:
            return None

        conversation_id = self._resolve_conversation_id(provider_request)
        limit = self._resolve_memory_turn_limit(config, memory_config)
        if limit <= 0:
            return None

        try:
            records = await get_memory_service(event_config).store.get_recent_turn_records(
                umo,
                limit,
                conversation_id=conversation_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect conversation history from memory turn records: %s",
                exc,
                exc_info=True,
            )
            return None

        if not records:
            return None

        records.reverse()
        return {
            "source": "memory.turn_records",
            "conversation_id": conversation_id,
            "turns": [self._turn_record_to_payload(record) for record in records],
        }

    def _load_conversation_history(
        self,
        *,
        raw_history: str | list[dict[str, Any]] | None,
        source_name: str,
    ) -> dict[str, Any] | None:
        try:
            messages = parse_conversation_history(raw_history)
            turns = extract_turn_payloads(messages)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect conversation history from %s: %s",
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
    def _resolve_conversation_id(
        provider_request: ProviderRequest | None,
    ) -> str | None:
        if provider_request is None or provider_request.conversation is None:
            return None
        raw_conversation_id = getattr(provider_request.conversation, "cid", None)
        if isinstance(raw_conversation_id, str) and raw_conversation_id.strip():
            return raw_conversation_id
        return None

    @staticmethod
    def _resolve_memory_turn_limit(
        config: MainAgentBuildConfig,
        memory_config,
    ) -> int:
        max_context_length = getattr(config, "max_context_length", -1)
        if isinstance(max_context_length, int) and max_context_length >= 0:
            return max_context_length
        return max(0, int(memory_config.short_term.recent_turns_window))

    @staticmethod
    def _turn_record_to_payload(record: TurnRecord) -> dict[str, Any]:
        user_message = normalize_message_payload(record.user_message)
        return {
            "user_message": user_message if user_message.get("content") else {},
            "assistant_message": normalize_message_payload(record.assistant_message),
            "assistant_only": not bool(user_message.get("content")),
        }

    def _truncate_history_payload(
        self,
        history_payload: dict[str, Any],
        config: MainAgentBuildConfig,
    ) -> dict[str, Any]:
        max_context_length = self.recent_turn_limit
        if max_context_length is None:
            max_context_length = getattr(config, "max_context_length", -1)
        if not isinstance(max_context_length, int) or max_context_length < 0:
            return history_payload

        turns = history_payload.get("turns")
        if not isinstance(turns, list) or len(turns) <= max_context_length:
            return history_payload

        return {
            **history_payload,
            "pre_truncate_turn_count": len(turns),
            "turns": turns[-max_context_length:] if max_context_length else [],
        }

    def _build_history_slot(
        self,
        provider_request: ProviderRequest | None,
        history_payload: dict[str, Any],
    ) -> ContextSlot:
        conversation_id = history_payload.get("conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            conversation_id = self._resolve_conversation_id(provider_request)

        turns = history_payload["turns"]
        source_name = history_payload["source"]
        pre_truncate_turn_count = history_payload.get("pre_truncate_turn_count")
        meta = {
            "format": "turn_pairs",
            "turn_count": len(turns),
        }
        if isinstance(pre_truncate_turn_count, int):
            meta["pre_truncate_turn_count"] = pre_truncate_turn_count
            meta["collector_truncated"] = True

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
            meta=meta,
        )
