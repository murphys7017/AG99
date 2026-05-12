"""
Memory context collector for prompt context packing.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING

from astrbot.core.memory.config import get_memory_config
from astrbot.core.memory.service import get_memory_service
from astrbot.core.memory.types import (
    Experience,
    LongTermMemoryIndex,
    MemorySnapshot,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextSlot
from ..interfaces.context_collector_inferface import ContextCollectorInterface

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class MemoryCollector(ContextCollectorInterface):
    """Collect prompt memory context from the current memory snapshot."""

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        del plugin_context, config

        umo = getattr(event, "unified_msg_origin", None)
        if not isinstance(umo, str) or not umo.strip():
            return []

        conversation_id = self._resolve_conversation_id(provider_request)
        query = self._resolve_query(event, provider_request)
        event_config = event.get_extra("_astrbot_config")
        if not isinstance(event_config, Mapping):
            event_config = None

        if not get_memory_config(event_config).enabled:
            return []
        snapshot = await get_memory_service(event_config).get_snapshot(
            umo=umo,
            conversation_id=conversation_id,
            query=query,
        )

        slots: list[ContextSlot] = []

        topic_state_slot = self._build_topic_state_slot(snapshot)
        if topic_state_slot is not None:
            slots.append(topic_state_slot)

        short_term_slot = self._build_short_term_slot(snapshot)
        if short_term_slot is not None:
            slots.append(short_term_slot)

        experiences_slot = self._build_experiences_slot(snapshot)
        if experiences_slot is not None:
            slots.append(experiences_slot)

        long_term_slot = self._build_long_term_memories_slot(snapshot)
        if long_term_slot is not None:
            slots.append(long_term_slot)

        persona_state_slot = self._build_persona_state_slot(snapshot)
        if persona_state_slot is not None:
            slots.append(persona_state_slot)

        return slots

    def _build_topic_state_slot(
        self,
        snapshot: MemorySnapshot,
    ) -> ContextSlot | None:
        topic_state = snapshot.topic_state
        if topic_state is None:
            return None

        return ContextSlot(
            name="memory.topic_state",
            value={
                "umo": topic_state.umo,
                "conversation_id": topic_state.conversation_id,
                "current_topic": topic_state.current_topic,
                "topic_summary": topic_state.topic_summary,
                "topic_confidence": topic_state.topic_confidence,
                "last_active_at": self._serialize_datetime(topic_state.last_active_at),
            },
            category="memory",
            source="memory_snapshot",
            meta={
                "snapshot_field": "topic_state",
                "has_value": True,
            },
        )

    def _build_short_term_slot(
        self,
        snapshot: MemorySnapshot,
    ) -> ContextSlot | None:
        short_term_memory = snapshot.short_term_memory
        if short_term_memory is None:
            return None

        return ContextSlot(
            name="memory.short_term",
            value={
                "umo": short_term_memory.umo,
                "conversation_id": short_term_memory.conversation_id,
                "short_summary": short_term_memory.short_summary,
                "active_focus": short_term_memory.active_focus,
                "updated_at": self._serialize_datetime(short_term_memory.updated_at),
            },
            category="memory",
            source="memory_snapshot",
            meta={
                "snapshot_field": "short_term_memory",
                "has_value": True,
            },
        )

    def _build_experiences_slot(
        self,
        snapshot: MemorySnapshot,
    ) -> ContextSlot | None:
        if not snapshot.experiences:
            return None

        items = [self._serialize_experience(item) for item in snapshot.experiences]
        return ContextSlot(
            name="memory.experiences",
            value={
                "count": len(items),
                "items": items,
            },
            category="memory",
            source="memory_snapshot",
            meta={
                "snapshot_field": "experiences",
                "has_value": True,
                "count": len(items),
            },
        )

    def _build_long_term_memories_slot(
        self,
        snapshot: MemorySnapshot,
    ) -> ContextSlot | None:
        if not snapshot.long_term_memories:
            return None

        items = [
            self._serialize_long_term_memory(item)
            for item in snapshot.long_term_memories
        ]
        return ContextSlot(
            name="memory.long_term_memories",
            value={
                "count": len(items),
                "items": items,
            },
            category="memory",
            source="memory_snapshot",
            meta={
                "snapshot_field": "long_term_memories",
                "has_value": True,
                "count": len(items),
            },
        )

    def _build_persona_state_slot(
        self,
        snapshot: MemorySnapshot,
    ) -> ContextSlot | None:
        persona_state = snapshot.persona_state
        if persona_state is None:
            return None

        return ContextSlot(
            name="memory.persona_state",
            value={
                "state_id": persona_state.state_id,
                "scope_type": self._enum_value(persona_state.scope_type),
                "scope_id": persona_state.scope_id,
                "persona_id": persona_state.persona_id,
                "familiarity": persona_state.familiarity,
                "trust": persona_state.trust,
                "warmth": persona_state.warmth,
                "formality_preference": persona_state.formality_preference,
                "directness_preference": persona_state.directness_preference,
                "updated_at": self._serialize_datetime(persona_state.updated_at),
            },
            category="memory",
            source="memory_snapshot",
            meta={
                "snapshot_field": "persona_state",
                "has_value": True,
            },
        )

    def _serialize_experience(self, experience: Experience) -> dict[str, object]:
        return {
            "experience_id": experience.experience_id,
            "umo": experience.umo,
            "conversation_id": experience.conversation_id,
            "scope_type": self._enum_value(experience.scope_type),
            "scope_id": experience.scope_id,
            "category": self._enum_value(experience.category),
            "summary": experience.summary,
            "detail_summary": experience.detail_summary,
            "importance": experience.importance,
            "confidence": experience.confidence,
            "event_time": self._serialize_datetime(experience.event_time),
            "updated_at": self._serialize_datetime(experience.updated_at),
            "source_refs": list(experience.source_refs),
        }

    def _serialize_long_term_memory(
        self,
        memory: LongTermMemoryIndex,
    ) -> dict[str, object]:
        return {
            "memory_id": memory.memory_id,
            "umo": memory.umo,
            "scope_type": self._enum_value(memory.scope_type),
            "scope_id": memory.scope_id,
            "category": self._enum_value(memory.category),
            "title": memory.title,
            "summary": memory.summary,
            "status": self._enum_value(memory.status),
            "importance": memory.importance,
            "confidence": memory.confidence,
            "tags": list(memory.tags),
            "source_refs": list(memory.source_refs),
            "first_event_at": self._serialize_datetime(memory.first_event_at),
            "last_event_at": self._serialize_datetime(memory.last_event_at),
            "updated_at": self._serialize_datetime(memory.updated_at),
        }

    def _resolve_conversation_id(
        self,
        provider_request: ProviderRequest | None,
    ) -> str | None:
        if provider_request is None or provider_request.conversation is None:
            return None

        conversation_id = getattr(provider_request.conversation, "cid", None)
        if isinstance(conversation_id, str) and conversation_id.strip():
            return conversation_id
        return None

    def _resolve_query(
        self,
        event: AstrMessageEvent,
        provider_request: ProviderRequest | None,
    ) -> str | None:
        if provider_request and isinstance(provider_request.prompt, str):
            prompt = provider_request.prompt.strip()
            if prompt:
                return prompt

        message_str = getattr(event, "message_str", None)
        if isinstance(message_str, str):
            message_str = message_str.strip()
            if message_str:
                return message_str

        return None

    def _serialize_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat(timespec="seconds")

    def _enum_value(self, value: object) -> str:
        return value.value if hasattr(value, "value") else str(value)
