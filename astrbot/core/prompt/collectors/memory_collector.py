"""
Memory context collector for prompt context packing.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING

from astrbot.core.memory.config import get_memory_config
from astrbot.core.memory.scope_context import resolve_memory_scope_context
from astrbot.core.memory.service import get_memory_service
from astrbot.core.memory.snapshot_builder import (
    MemorySnapshotReadOptions,
    memory_injection_to_snapshot_read_options,
)
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
from .persona_state_projection import serialize_persona_state

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


_MEMORY_SNAPSHOT_CACHE_EXTRA_KEY = "_prompt_memory_snapshot_cache"


def _snapshot_read_signature(
    options: MemorySnapshotReadOptions,
) -> tuple[int, int, bool, bool]:
    enabled = bool(options.enabled)
    experiences_top_k = (
        max(0, int(options.experiences.top_k))
        if enabled and options.experiences.enabled
        else 0
    )
    long_term_top_k = (
        max(0, int(options.long_term.top_k))
        if enabled and options.long_term.enabled
        else 0
    )
    return (
        experiences_top_k,
        long_term_top_k,
        bool(options.long_term.query_required) if long_term_top_k > 0 else False,
        enabled and bool(options.persona_state),
    )


def _snapshot_signature_satisfies(
    cached: tuple[int, int, bool, bool],
    requested: tuple[int, int, bool, bool],
) -> bool:
    if cached == requested:
        return True

    requested_experiences, requested_long_term, _, requested_persona = requested
    cached_persona = cached[3]
    return (
        requested_experiences == 0
        and requested_long_term == 0
        and requested_persona
        and cached_persona
    )


async def resolve_prompt_memory_snapshot(
    event: AstrMessageEvent,
    *,
    config,
    provider_request: ProviderRequest | None = None,
    read_options: MemorySnapshotReadOptions | None = None,
) -> MemorySnapshot | None:
    """Read one event-scoped MemorySnapshot and reuse it across collectors."""

    del config

    umo = getattr(event, "unified_msg_origin", None)
    if not isinstance(umo, str) or not umo.strip():
        return None

    event_config = event.get_extra("_astrbot_config")
    if not isinstance(event_config, Mapping):
        event_config = None
    config_id = str(event.get_extra("_astrbot_config_id", "default") or "default")
    memory_config = get_memory_config(event_config, cache_key=config_id)
    if not memory_config.enabled or not memory_config.injection.enabled:
        return None

    conversation_id = MemoryCollector._resolve_conversation_id(provider_request)
    options = read_options or memory_injection_to_snapshot_read_options(
        memory_config.injection
    )
    cache = event.get_extra(_MEMORY_SNAPSHOT_CACHE_EXTRA_KEY, {})
    if not isinstance(cache, dict):
        cache = {}
    cache_key = (config_id, umo, conversation_id)
    cached_entries = cache.get(cache_key, [])
    requested_signature = _snapshot_read_signature(options)
    if isinstance(cached_entries, list):
        for cached in reversed(cached_entries):
            if not isinstance(cached, dict):
                continue
            snapshot = cached.get("snapshot")
            signature = cached.get("read_signature")
            if (
                not isinstance(snapshot, MemorySnapshot)
                or not isinstance(signature, tuple)
                or len(signature) != 4
            ):
                continue
            if _snapshot_signature_satisfies(signature, requested_signature):
                return snapshot

    memory_service = get_memory_service(event_config, cache_key=config_id)
    await memory_service.initialize()
    identity = None
    if memory_service.identity_resolver is not None:
        identity_result = memory_service.identity_resolver.resolve_from_event(event)
        identity = (
            await identity_result
            if inspect.isawaitable(identity_result)
            else identity_result
        )

    snapshot = await memory_service.get_prompt_snapshot(
        umo=umo,
        conversation_id=conversation_id,
        read_options=options,
        identity=identity,
        scope_context=(
            resolve_memory_scope_context(event, identity)
            if identity is not None
            else None
        ),
    )
    cache_entries = cache.setdefault(cache_key, [])
    if not isinstance(cache_entries, list):
        cache_entries = []
        cache[cache_key] = cache_entries
    cache_entries.append(
        {
            "snapshot": snapshot,
            "read_signature": requested_signature,
        }
    )
    event.set_extra(_MEMORY_SNAPSHOT_CACHE_EXTRA_KEY, cache)
    return snapshot


def get_cached_prompt_memory_snapshot(
    event: AstrMessageEvent,
    *,
    provider_request: ProviderRequest | None = None,
) -> MemorySnapshot | None:
    cache = event.get_extra(_MEMORY_SNAPSHOT_CACHE_EXTRA_KEY, {})
    if not isinstance(cache, dict):
        return None
    umo = getattr(event, "unified_msg_origin", None)
    conversation_id = MemoryCollector._resolve_conversation_id(provider_request)
    config_id = str(event.get_extra("_astrbot_config_id", "default") or "default")
    entries = cache.get((config_id, umo, conversation_id), [])
    if not isinstance(entries, list):
        return None
    for entry in reversed(entries):
        if not isinstance(entry, dict):
            continue
        snapshot = entry.get("snapshot")
        if isinstance(snapshot, MemorySnapshot):
            return snapshot
    return None


class MemoryCollector(ContextCollectorInterface):
    """Collect prompt memory context from the current memory snapshot."""

    def __init__(self, *, include_persona_state: bool = True) -> None:
        self.include_persona_state = include_persona_state

    @property
    def failure_policy(self) -> str:
        return "optional"

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        del plugin_context

        event_config = event.get_extra("_astrbot_config")
        if not isinstance(event_config, Mapping):
            event_config = None
        config_id = str(event.get_extra("_astrbot_config_id", "default") or "default")
        memory_config = get_memory_config(event_config, cache_key=config_id)
        if not memory_config.enabled or not memory_config.injection.enabled:
            return []
        snapshot = await resolve_prompt_memory_snapshot(
            event,
            config=config,
            provider_request=provider_request,
        )
        if snapshot is None:
            return []

        slots: list[ContextSlot] = []

        injection_config = memory_config.injection

        topic_state_slot = (
            self._build_topic_state_slot(
                snapshot,
                include_debug_fields=injection_config.include_debug_fields,
            )
            if injection_config.topic_state
            else None
        )
        if topic_state_slot is not None:
            slots.append(topic_state_slot)

        short_term_slot = (
            self._build_short_term_slot(
                snapshot,
                include_debug_fields=injection_config.include_debug_fields,
            )
            if injection_config.short_term
            else None
        )
        if short_term_slot is not None:
            slots.append(short_term_slot)

        experiences_slot = (
            self._build_experiences_slot(
                snapshot,
                include_debug_fields=injection_config.include_debug_fields,
                top_k=injection_config.experiences.top_k,
            )
            if injection_config.experiences.enabled
            else None
        )
        if experiences_slot is not None:
            slots.append(experiences_slot)

        long_term_slot = (
            self._build_long_term_memories_slot(
                snapshot,
                include_debug_fields=injection_config.include_debug_fields,
                top_k=injection_config.long_term.top_k,
            )
            if injection_config.long_term.enabled
            else None
        )
        if long_term_slot is not None:
            slots.append(long_term_slot)

        persona_state_slot = (
            self._build_persona_state_slot(
                snapshot,
                include_debug_fields=injection_config.include_debug_fields,
            )
            if self.include_persona_state and injection_config.persona_state
            else None
        )
        if persona_state_slot is not None:
            slots.append(persona_state_slot)

        return slots

    def _build_topic_state_slot(
        self,
        snapshot: MemorySnapshot,
        *,
        include_debug_fields: bool,
    ) -> ContextSlot | None:
        topic_state = snapshot.topic_state
        if topic_state is None:
            return None

        value: dict[str, object] = {
            "current_topic": topic_state.current_topic,
            "topic_summary": topic_state.topic_summary,
            "topic_confidence": topic_state.topic_confidence,
        }
        if include_debug_fields:
            value.update(
                {
                    "umo": topic_state.umo,
                    "conversation_id": topic_state.conversation_id,
                    "last_active_at": self._serialize_datetime(
                        topic_state.last_active_at
                    ),
                }
            )
        return ContextSlot(
            name="memory.topic_state",
            value=value,
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
        *,
        include_debug_fields: bool,
    ) -> ContextSlot | None:
        short_term_memory = snapshot.short_term_memory
        if short_term_memory is None:
            return None

        value: dict[str, object] = {
            "short_summary": short_term_memory.short_summary,
            "active_focus": short_term_memory.active_focus,
        }
        if include_debug_fields:
            value.update(
                {
                    "umo": short_term_memory.umo,
                    "conversation_id": short_term_memory.conversation_id,
                    "updated_at": self._serialize_datetime(
                        short_term_memory.updated_at
                    ),
                }
            )
        return ContextSlot(
            name="memory.short_term",
            value=value,
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
        *,
        include_debug_fields: bool,
        top_k: int,
    ) -> ContextSlot | None:
        if not snapshot.experiences:
            return None

        limit = max(0, top_k)
        if limit <= 0:
            return None

        items = [
            self._serialize_experience(
                item,
                include_debug_fields=include_debug_fields,
            )
            for item in snapshot.experiences[:limit]
        ]
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
        *,
        include_debug_fields: bool,
        top_k: int,
    ) -> ContextSlot | None:
        if not snapshot.long_term_memories:
            return None

        limit = max(0, top_k)
        if limit <= 0:
            return None

        items = [
            self._serialize_long_term_memory(
                item,
                include_debug_fields=include_debug_fields,
            )
            for item in snapshot.long_term_memories[:limit]
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
        *,
        include_debug_fields: bool,
    ) -> ContextSlot | None:
        persona_state = snapshot.persona_state
        if persona_state is None:
            return None

        value = serialize_persona_state(
            persona_state,
            include_debug_fields=include_debug_fields,
        )
        return ContextSlot(
            name="memory.persona_state",
            value=value,
            category="memory",
            source="memory_snapshot",
            meta={
                "snapshot_field": "persona_state",
                "has_value": True,
            },
        )

    def _serialize_experience(
        self,
        experience: Experience,
        *,
        include_debug_fields: bool,
    ) -> dict[str, object]:
        value: dict[str, object] = {
            "category": self._enum_value(experience.category),
            "summary": experience.summary,
            "detail_summary": experience.detail_summary,
            "importance": experience.importance,
            "confidence": experience.confidence,
        }
        if include_debug_fields:
            value.update(
                {
                    "experience_id": experience.experience_id,
                    "umo": experience.umo,
                    "conversation_id": experience.conversation_id,
                    "scope_type": self._enum_value(experience.scope_type),
                    "scope_id": experience.scope_id,
                    "event_time": self._serialize_datetime(experience.event_time),
                    "updated_at": self._serialize_datetime(experience.updated_at),
                    "source_refs": list(experience.source_refs),
                }
            )
        return value

    def _serialize_long_term_memory(
        self,
        memory: LongTermMemoryIndex,
        *,
        include_debug_fields: bool,
    ) -> dict[str, object]:
        value: dict[str, object] = {
            "category": self._enum_value(memory.category),
            "title": memory.title,
            "summary": memory.summary,
            "status": self._enum_value(memory.status),
            "importance": memory.importance,
            "confidence": memory.confidence,
            "tags": list(memory.tags),
        }
        if include_debug_fields:
            value.update(
                {
                    "memory_id": memory.memory_id,
                    "umo": memory.umo,
                    "scope_type": self._enum_value(memory.scope_type),
                    "scope_id": memory.scope_id,
                    "source_refs": list(memory.source_refs),
                    "first_event_at": self._serialize_datetime(memory.first_event_at),
                    "last_event_at": self._serialize_datetime(memory.last_event_at),
                    "updated_at": self._serialize_datetime(memory.updated_at),
                }
            )
        return value

    @staticmethod
    def _resolve_conversation_id(
        provider_request: ProviderRequest | None,
    ) -> str | None:
        if provider_request is None or provider_request.conversation is None:
            return None

        conversation_id = getattr(provider_request.conversation, "cid", None)
        if isinstance(conversation_id, str) and conversation_id.strip():
            return conversation_id
        return None

    def _serialize_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat(timespec="seconds")

    def _enum_value(self, value: object) -> str:
        return value.value if hasattr(value, "value") else str(value)
