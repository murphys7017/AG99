"""Read-only relationship state projection for persona-facing targets."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from astrbot.core.memory.config import (
    MemoryInjectionListConfig,
    MemoryLongTermInjectionConfig,
    get_memory_config,
)
from astrbot.core.memory.snapshot_builder import MemorySnapshotReadOptions
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextSlot
from ..interfaces.context_collector_inferface import ContextCollectorInterface
from .memory_collector import resolve_prompt_memory_snapshot


class PersonaRelationshipCollector(ContextCollectorInterface):
    """Collect ``memory.persona_state`` without owning general Memory facts."""

    @property
    def failure_policy(self) -> str:
        return "optional"

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        del plugin_context

        event_config = event.get_extra("_astrbot_config")
        if not isinstance(event_config, Mapping):
            event_config = None
        config_id = str(event.get_extra("_astrbot_config_id", "default") or "default")
        memory_config = get_memory_config(event_config, cache_key=config_id)
        injection = memory_config.injection
        if (
            not memory_config.enabled
            or not injection.enabled
            or not injection.persona_state
        ):
            return []

        snapshot = await resolve_prompt_memory_snapshot(
            event,
            config=config,
            provider_request=provider_request,
            read_options=MemorySnapshotReadOptions(
                enabled=True,
                experiences=MemoryInjectionListConfig(enabled=False, top_k=0),
                long_term=MemoryLongTermInjectionConfig(
                    enabled=False,
                    top_k=0,
                    query_required=False,
                ),
                persona_state=True,
            ),
        )
        if snapshot is None:
            return []
        persona_state = snapshot.persona_state
        if persona_state is None:
            return []

        value: dict[str, object] = {
            "familiarity": persona_state.familiarity,
            "trust": persona_state.trust,
            "warmth": persona_state.warmth,
            "formality_preference": persona_state.formality_preference,
            "directness_preference": persona_state.directness_preference,
        }
        if injection.include_debug_fields:
            value.update(
                {
                    "state_id": persona_state.state_id,
                    "scope_type": self._enum_value(persona_state.scope_type),
                    "scope_id": persona_state.scope_id,
                    "persona_id": persona_state.persona_id,
                    "updated_at": self._serialize_datetime(persona_state.updated_at),
                }
            )

        return [
            ContextSlot(
                name="memory.persona_state",
                value=value,
                category="memory",
                source="persona_relationship_state",
                meta={
                    "snapshot_field": "persona_state",
                    "has_value": True,
                },
            )
        ]

    @staticmethod
    def _enum_value(value: object) -> str:
        return value.value if hasattr(value, "value") else str(value)

    @staticmethod
    def _serialize_datetime(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat(timespec="seconds")


__all__ = ["PersonaRelationshipCollector"]
