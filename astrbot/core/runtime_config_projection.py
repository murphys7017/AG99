"""Compatibility projection for consumers outside the Interaction import graph."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def resolve_event_runtime_configuration(
    event: Any,
) -> tuple[Mapping[str, Any] | None, str]:
    """Prefer typed turn configuration without creating startup import cycles.

    Prompt and Memory modules are imported while ``InteractionTurnState`` is
    still initializing. Importing it only when an event is actually consumed
    preserves that boundary while retaining the legacy Event-extra projection
    for non-Interaction callers.
    """

    from astrbot.core.interaction.turn_state import (  # noqa: PLC0415
        resolve_interaction_turn_runtime_configuration,
    )

    return resolve_interaction_turn_runtime_configuration(event)
