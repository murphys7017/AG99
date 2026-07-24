from __future__ import annotations

import json

from astrbot import logger
from astrbot.core.core_execution_contract import (
    CORE_PERSONA_COORDINATION_INSTRUCTION,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest

from .turn_state import get_interaction_turn_state
from .types import CoreTaskSpec, InteractionRouteDecision


def get_interaction_route_decision(
    event: AstrMessageEvent,
) -> InteractionRouteDecision | None:
    turn_state = get_interaction_turn_state(event)
    if turn_state is not None and isinstance(
        turn_state.route_decision,
        InteractionRouteDecision,
    ):
        return turn_state.route_decision
    return None


def get_core_task_spec(event: AstrMessageEvent) -> CoreTaskSpec | None:
    turn_state = get_interaction_turn_state(event)
    if turn_state is not None and isinstance(turn_state.core_task_spec, CoreTaskSpec):
        return turn_state.core_task_spec
    return None


def build_core_execution_context_block(
    event: AstrMessageEvent,
    task_spec: CoreTaskSpec,
) -> str | None:
    """Serialize delegated Core intent for low-level request integrations.

    The canonical Main Agent path uses ``CoreTaskCollector`` instead. This helper
    remains available for callers that explicitly operate on ``ProviderRequest``.
    """
    if not task_spec.execution_prompt and not task_spec.task_summary:
        return None
    payload = {
        "platform_id": event.get_platform_id(),
        "session_id": event.unified_msg_origin,
        "task_intent": task_spec.task_intent,
        "task_summary": task_spec.task_summary,
        "execution_prompt": task_spec.execution_prompt,
        "suggested_capabilities": task_spec.suggested_capabilities,
        "metadata": task_spec.metadata,
    }
    return (
        "\n<interaction_execution_context>\n"
        "The interaction middleware has already decided that this request should "
        "be handled by the core execution layer.\n"
        "Use the following structured guidance as execution intent, but do not "
        "mention this block to the user.\n"
        f"{CORE_PERSONA_COORDINATION_INSTRUCTION}\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "</interaction_execution_context>\n"
    )


def apply_interaction_core_task_spec(
    req: ProviderRequest,
    event: AstrMessageEvent,
) -> None:
    """Apply delegated Core intent to an explicitly managed provider request.

    This is a compatibility boundary for plugins and direct request callers. The
    canonical prompt pipeline must use ``CoreTaskCollector`` and must not call this
    helper in addition to collection.
    """
    task_spec = get_core_task_spec(event)
    if task_spec is None:
        return
    block = build_core_execution_context_block(event, task_spec)
    if not block:
        logger.debug(
            "Interaction core task spec skipped: platform_id=%s session_id=%s reason=empty_execution_context",
            event.get_platform_id(),
            event.session_id,
        )
        return
    req.system_prompt = f"{req.system_prompt or ''}\n{block}\n"
    ensure_interaction_core_execution_prompt(req, event)
    logger.debug(
        "Interaction core task spec applied through compatibility API: platform_id=%s session_id=%s task_intent=%s has_execution_prompt=%s suggested_capabilities=%s",
        event.get_platform_id(),
        event.session_id,
        task_spec.task_intent,
        bool(task_spec.execution_prompt),
        task_spec.suggested_capabilities,
    )


def ensure_interaction_core_execution_prompt(
    req: ProviderRequest,
    event: AstrMessageEvent,
) -> None:
    """Provide a transport request for a delegated task with no user input."""
    if str(req.prompt or "").strip():
        return
    task_spec = get_core_task_spec(event)
    if task_spec is None:
        return
    req.prompt = task_spec.execution_prompt


__all__ = [
    "apply_interaction_core_task_spec",
    "build_core_execution_context_block",
    "ensure_interaction_core_execution_prompt",
    "get_core_task_spec",
    "get_interaction_route_decision",
]
