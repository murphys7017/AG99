"""Shared serialization for persona relationship state prompt slots."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from astrbot.core.memory.types import PersonaState


def serialize_persona_state(
    persona_state: PersonaState,
    *,
    include_debug_fields: bool,
) -> dict[str, object]:
    """Project a memory persona state into the prompt-facing mapping."""

    value: dict[str, object] = {
        "familiarity": persona_state.familiarity,
        "trust": persona_state.trust,
        "warmth": persona_state.warmth,
        "formality_preference": persona_state.formality_preference,
        "directness_preference": persona_state.directness_preference,
    }
    if include_debug_fields:
        value.update(
            {
                "state_id": persona_state.state_id,
                "scope_type": _enum_value(persona_state.scope_type),
                "scope_id": persona_state.scope_id,
                "persona_id": persona_state.persona_id,
                "updated_at": _serialize_datetime(persona_state.updated_at),
            }
        )
    return value


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")


__all__ = ["serialize_persona_state"]
