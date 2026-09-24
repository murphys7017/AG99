"""Typed configuration access for Pipeline event consumers."""

from collections.abc import Mapping
from typing import Any

from astrbot.core.interaction.turn_state import (
    get_interaction_turn_runtime_config,
    get_interaction_turn_state,
)


def get_pipeline_turn_runtime_config(
    event: Any,
    fallback: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Return the configuration admitted for an event, with legacy fallback."""

    runtime_config = get_interaction_turn_runtime_config(event)
    if isinstance(runtime_config, Mapping):
        return runtime_config
    return fallback


def get_pipeline_turn_config_id(event: Any, fallback: str) -> str:
    """Return the admitted Profile identity, or the legacy Pipeline identity."""

    state = get_interaction_turn_state(event)
    if state is not None and state.runtime_config_id:
        return state.runtime_config_id
    return fallback
