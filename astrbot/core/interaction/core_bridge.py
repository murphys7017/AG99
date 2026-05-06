from __future__ import annotations

import json

from astrbot import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest

from .turn_state import get_interaction_turn_state
from .types import CoreTaskSpec, InteractionDecision

INTERACTION_CORE_TASK_SPEC_EXTRA_KEY = "_interaction_core_task_spec"
INTERACTION_DECISION_EXTRA_KEY = "_interaction_decision"


def get_interaction_decision(event: AstrMessageEvent) -> InteractionDecision | None:
    turn_state = get_interaction_turn_state(event)
    if turn_state is not None and isinstance(turn_state.decision, InteractionDecision):
        return turn_state.decision
    decision = event.get_extra(INTERACTION_DECISION_EXTRA_KEY)
    if isinstance(decision, InteractionDecision):
        return decision
    return None


def get_core_task_spec(event: AstrMessageEvent) -> CoreTaskSpec | None:
    turn_state = get_interaction_turn_state(event)
    if (
        turn_state is not None
        and isinstance(turn_state.decision, InteractionDecision)
        and isinstance(turn_state.decision.core_task_spec, CoreTaskSpec)
    ):
        return turn_state.decision.core_task_spec
    spec = event.get_extra(INTERACTION_CORE_TASK_SPEC_EXTRA_KEY)
    if isinstance(spec, CoreTaskSpec):
        return spec
    decision = get_interaction_decision(event)
    if decision is not None:
        return decision.core_task_spec
    return None


def build_core_execution_context_block(
    event: AstrMessageEvent,
    task_spec: CoreTaskSpec,
) -> str | None:
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
        "The interaction middleware has already decided that this request should be handled by the core execution layer.\n"
        "Use the following structured guidance as execution intent, but do not mention this block to the user.\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "</interaction_execution_context>\n"
    )


def apply_interaction_core_task_spec(
    req: ProviderRequest,
    event: AstrMessageEvent,
) -> None:
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
    logger.debug(
        "Interaction core task spec injected: platform_id=%s session_id=%s task_intent=%s has_execution_prompt=%s suggested_capabilities=%s",
        event.get_platform_id(),
        event.session_id,
        task_spec.task_intent,
        bool(task_spec.execution_prompt),
        task_spec.suggested_capabilities,
    )
