from __future__ import annotations

import asyncio
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
    finalized_turn_material: dict[str, Any] | None = None
    immediate_reply: str | None = None
    visible_outputs: list[dict[str, Any]] = field(default_factory=list)
    core_stream_text: str = ""
    core_stream_pending_text: str = ""
    core_stream_observation_count: int = 0
    core_stream_observation_tasks: list[asyncio.Task[Any]] = field(
        default_factory=list
    )
    core_stream_observation_failures: list[str] = field(default_factory=list)
    core_streaming_active: bool = False
    core_streaming_result_consumed: bool = False
    core_final_result_consumed: bool = False
    visible_message_counter: int = 0
    stream_interjections_emitted: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stream_interjection_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


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
        raw_outputs = event.get_extra("_visible_turn_outputs", [])
        visible_outputs = (
            [dict(item) for item in raw_outputs if isinstance(item, dict)]
            if isinstance(raw_outputs, list)
            else []
        )
        raw_tasks = event.get_extra("_interaction_stream_observation_tasks", [])
        observation_tasks = (
            [task for task in raw_tasks if isinstance(task, asyncio.Task)]
            if isinstance(raw_tasks, list)
            else []
        )
        raw_failures = event.get_extra("_interaction_stream_observation_failures", [])
        observation_failures = (
            [str(item) for item in raw_failures if str(item).strip()]
            if isinstance(raw_failures, list)
            else []
        )
        immediate_reply = event.get_extra("_interaction_immediate_reply")
        state = InteractionTurnState(
            turn_id=resolved_turn_id,
            immediate_reply=(
                str(immediate_reply).strip() if immediate_reply is not None else None
            )
            or None,
            visible_outputs=visible_outputs,
            core_stream_text=str(event.get_extra("_interaction_core_stream_text", "") or ""),
            core_stream_pending_text=str(
                event.get_extra("_interaction_core_stream_pending_text", "") or ""
            ),
            core_stream_observation_count=int(
                event.get_extra("_interaction_core_stream_observation_count", 0) or 0
            ),
            core_stream_observation_tasks=observation_tasks,
            core_stream_observation_failures=observation_failures,
            core_streaming_active=bool(
                event.get_extra("_interaction_core_streaming_active", False)
            ),
            core_streaming_result_consumed=bool(
                event.get_extra("_interaction_core_streaming_result_consumed", False)
            ),
            core_final_result_consumed=bool(
                event.get_extra("_interaction_core_final_result_consumed", False)
            ),
            visible_message_counter=int(
                event.get_extra("_interaction_visible_message_counter", 0) or 0
            ),
            stream_interjections_emitted=int(
                event.get_extra("_interaction_stream_interjections_emitted", 0) or 0
            ),
        )
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


def set_interaction_turn_decision(event, decision: InteractionDecision | None) -> None:
    state = ensure_interaction_turn_state(event)
    state.decision = decision


def set_interaction_turn_finalized_material(
    event,
    material: dict[str, Any] | None,
) -> None:
    state = ensure_interaction_turn_state(event)
    normalized = dict(material) if isinstance(material, dict) else None
    state.finalized_turn_material = normalized
    event.set_extra("_interaction_finalized_turn_material", normalized)


def get_interaction_turn_finalized_material(event) -> dict[str, Any] | None:
    state = get_interaction_turn_state(event)
    if state is not None and isinstance(state.finalized_turn_material, dict):
        return dict(state.finalized_turn_material)
    material = event.get_extra("_interaction_finalized_turn_material")
    if isinstance(material, dict):
        return dict(material)
    return None


def set_interaction_turn_immediate_reply(event, reply: str | None) -> None:
    normalized_reply = (reply or "").strip() or None
    state = ensure_interaction_turn_state(event)
    state.immediate_reply = normalized_reply
    event.set_extra("_interaction_immediate_reply", normalized_reply)


def get_interaction_turn_immediate_reply(event) -> str | None:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.immediate_reply
    reply = event.get_extra("_interaction_immediate_reply")
    if isinstance(reply, str):
        return reply.strip() or None
    return None


def append_interaction_turn_visible_output(
    event,
    *,
    message_kind: str,
    text: str | None,
    memory_relevant: bool = True,
) -> None:
    clean_text = (text or "").strip()
    if not clean_text:
        return
    state = ensure_interaction_turn_state(event)
    item = {
        "turn_id": state.turn_id,
        "kind": message_kind,
        "text": clean_text,
        "memory_relevant": memory_relevant,
    }
    state.visible_outputs.append(item)
    outputs = [dict(output) for output in state.visible_outputs]
    event.set_extra("_visible_turn_outputs", outputs)
    event.set_extra("_postprocess_visible_outputs", outputs)


def get_interaction_turn_visible_outputs(event) -> list[dict[str, Any]]:
    state = ensure_interaction_turn_state(event)
    return [dict(output) for output in state.visible_outputs]


def set_interaction_turn_stream_progress(
    event,
    *,
    total_text: str,
    pending_text: str,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.core_stream_text = total_text
    state.core_stream_pending_text = pending_text
    event.set_extra("_interaction_core_stream_text", total_text)
    event.set_extra("_interaction_core_stream_pending_text", pending_text)


def set_interaction_turn_stream_observation_count(
    event,
    window_index: int,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.core_stream_observation_count = window_index
    event.set_extra("_interaction_core_stream_observation_count", window_index)


def add_interaction_turn_stream_observation_task(
    event,
    task: asyncio.Task[Any],
) -> None:
    state = ensure_interaction_turn_state(event)
    state.core_stream_observation_tasks.append(task)
    event.set_extra(
        "_interaction_stream_observation_tasks",
        list(state.core_stream_observation_tasks),
    )


def remove_interaction_turn_stream_observation_task(
    event,
    task: asyncio.Task[Any],
) -> None:
    state = ensure_interaction_turn_state(event)
    if task in state.core_stream_observation_tasks:
        state.core_stream_observation_tasks.remove(task)
    event.set_extra(
        "_interaction_stream_observation_tasks",
        list(state.core_stream_observation_tasks),
    )


def get_interaction_turn_stream_observation_tasks(
    event,
) -> list[asyncio.Task[Any]]:
    state = ensure_interaction_turn_state(event)
    return list(state.core_stream_observation_tasks)


def record_interaction_turn_stream_observation_failure(
    event,
    failure: str,
) -> None:
    clean_failure = str(failure or "").strip()
    if not clean_failure:
        return
    state = ensure_interaction_turn_state(event)
    state.core_stream_observation_failures.append(clean_failure)
    event.set_extra(
        "_interaction_stream_observation_failures",
        list(state.core_stream_observation_failures),
    )


def get_interaction_turn_stream_text(event) -> str:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.core_stream_text
    return str(event.get_extra("_interaction_core_stream_text", "") or "")


def get_interaction_turn_stream_pending_text(event) -> str:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.core_stream_pending_text
    return str(event.get_extra("_interaction_core_stream_pending_text", "") or "")


def get_interaction_turn_stream_observation_count(event) -> int:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.core_stream_observation_count
    return int(event.get_extra("_interaction_core_stream_observation_count", 0) or 0)


def set_interaction_turn_core_streaming_active(
    event,
    is_active: bool,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.core_streaming_active = is_active
    event.set_extra("_interaction_core_streaming_active", is_active)


def mark_interaction_turn_core_streaming_result_consumed(
    event,
    consumed: bool = True,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.core_streaming_result_consumed = consumed
    event.set_extra("_interaction_core_streaming_result_consumed", consumed)


def has_interaction_turn_core_streaming_result_consumed(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.core_streaming_result_consumed
    return bool(event.get_extra("_interaction_core_streaming_result_consumed", False))


def mark_interaction_turn_core_final_result_consumed(
    event,
    consumed: bool = True,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.core_final_result_consumed = consumed
    event.set_extra("_interaction_core_final_result_consumed", consumed)


def has_interaction_turn_core_final_result_consumed(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.core_final_result_consumed
    return bool(event.get_extra("_interaction_core_final_result_consumed", False))


def is_interaction_turn_core_streaming_active(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.core_streaming_active
    return bool(event.get_extra("_interaction_core_streaming_active", False))


def mark_interaction_turn_stream_interjection_emitted(event) -> int:
    state = ensure_interaction_turn_state(event)
    state.stream_interjections_emitted += 1
    event.set_extra(
        "_interaction_stream_interjections_emitted",
        state.stream_interjections_emitted,
    )
    return state.stream_interjections_emitted


def get_interaction_turn_stream_interjections_emitted(event) -> int:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.stream_interjections_emitted
    return int(event.get_extra("_interaction_stream_interjections_emitted", 0) or 0)


def next_interaction_turn_visible_message_id(event, message_kind: str) -> str:
    state = ensure_interaction_turn_state(event)
    turn_id = state.turn_id.strip() or "turn"
    state.visible_message_counter += 1
    event.set_extra(
        "_interaction_visible_message_counter",
        state.visible_message_counter,
    )
    safe_kind = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_"
        for char in message_kind
    ).strip("_")
    if not safe_kind:
        safe_kind = "message"
    return f"{turn_id}::{safe_kind}::{state.visible_message_counter:04d}"
