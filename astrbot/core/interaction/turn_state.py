from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astrbot.core.prompt.context_types import ContextPack

from .types import InteractionDecision

INTERACTION_TURN_STATE_EXTRA_KEY = "_interaction_turn_state"


@dataclass(slots=True)
class InteractionContextMaterial:
    prompt_context_pack: ContextPack | None = None
    persona_payload: dict[str, Any] = field(default_factory=dict)
    memory_payload: dict[str, Any] = field(default_factory=dict)
    recent_messages: list[dict[str, Any]] = field(default_factory=list)
    input_payload: dict[str, Any] = field(default_factory=dict)
    capability_payload: dict[str, Any] = field(default_factory=dict)
    decision_context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InteractionTurnState:
    turn_id: str
    persona_id: str = ""
    prompt_build_config: Any | None = None
    context_material: InteractionContextMaterial | None = None
    decision: InteractionDecision | None = None


def get_interaction_turn_state(event) -> InteractionTurnState | None:
    state = event.get_extra(INTERACTION_TURN_STATE_EXTRA_KEY)
    if isinstance(state, InteractionTurnState):
        return state
    return None


def ensure_interaction_turn_state(
    event,
    *,
    turn_id: str | None = None,
) -> InteractionTurnState:
    state = get_interaction_turn_state(event)
    if state is None:
        resolved_turn_id = turn_id or str(event.get_extra("_turn_id", "") or "")
        state = InteractionTurnState(turn_id=resolved_turn_id)
        event.set_extra(INTERACTION_TURN_STATE_EXTRA_KEY, state)
    elif turn_id and not state.turn_id:
        state.turn_id = turn_id

    if state.turn_id:
        event.set_extra("_turn_id", state.turn_id)
    return state


def set_interaction_turn_persona_id(event, persona_id: str) -> None:
    normalized_persona_id = str(persona_id or "")
    state = get_interaction_turn_state(event)
    if state is not None:
        state.persona_id = normalized_persona_id
    event.set_extra("_interaction_persona_id", normalized_persona_id)
