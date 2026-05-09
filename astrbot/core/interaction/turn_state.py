from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from astrbot.core.prompt.context_types import ContextPack

from .types import InteractionDecision

INTERACTION_TURN_STATE_EXTRA_KEY = "_interaction_turn_state"

_VALID_UTTERANCE_KINDS = frozenset(
    {
        "immediate_reply",
        "stream_interjection",
        "passthrough",
        "core_reply",
        "core_stream",
        "finalized_reply",
    }
)


@dataclass(slots=True)
class InteractionUtterance:
    turn_id: str
    message_id: str
    kind: str
    text: str
    delivered_message_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    visible: bool = True
    memory_relevant: bool = True
    source: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class InteractionContextMaterial:
    prompt_context_pack: ContextPack | None = None
    persona_payload: dict[str, Any] = field(default_factory=dict)
    memory_payload: dict[str, Any] = field(default_factory=dict)
    recent_messages: list[dict[str, Any]] = field(default_factory=list)
    input_payload: dict[str, Any] = field(default_factory=dict)
    capability_payload: dict[str, Any] = field(default_factory=dict)
    decision_context: dict[str, Any] = field(default_factory=dict)
    prompt_extensions_collected: bool = False


@dataclass(slots=True)
class InteractionStreamState:
    total_text: str = ""
    pending_text: str = ""
    observation_count: int = 0
    observation_tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    observation_failures: list[str] = field(default_factory=list)
    active: bool = False
    result_consumed: bool = False
    interjections_emitted: int = 0


@dataclass(slots=True)
class InteractionTurnCompletionState:
    material_finalized: bool = False
    legacy_memory_persisted: bool = False
    postprocess_dispatched: bool = False
    completed: bool = False
    failure_reason: str | None = None


@dataclass(slots=True)
class InteractionTurnFailure:
    stage: str
    reason: str
    exception_type: str | None = None
    message: str | None = None
    user_visible_action: str | None = None
    material_finalized: bool = False
    legacy_memory_persisted: bool = False
    postprocess_dispatched: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "reason": self.reason,
            "exception_type": self.exception_type,
            "message": self.message,
            "user_visible_action": self.user_visible_action,
            "material_finalized": self.material_finalized,
            "legacy_memory_persisted": self.legacy_memory_persisted,
            "postprocess_dispatched": self.postprocess_dispatched,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class InteractionTurnState:
    turn_id: str
    persona_id: str = ""
    prompt_build_config: Any | None = None
    context_material: InteractionContextMaterial | None = None
    decision: InteractionDecision | None = None
    finalized_turn_material: dict[str, Any] | None = None
    immediate_reply: str | None = None
    utterances: list[InteractionUtterance] = field(default_factory=list)
    visible_outputs: list[dict[str, Any]] = field(default_factory=list)
    stream_state: InteractionStreamState = field(default_factory=InteractionStreamState)
    core_stream_text: str = ""
    core_stream_pending_text: str = ""
    core_stream_observation_count: int = 0
    core_stream_observation_tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    core_stream_observation_failures: list[str] = field(default_factory=list)
    core_streaming_active: bool = False
    core_streaming_result_consumed: bool = False
    core_final_result_consumed: bool = False
    visible_message_counter: int = 0
    stream_interjections_emitted: int = 0
    completion_state: InteractionTurnCompletionState = field(
        default_factory=InteractionTurnCompletionState
    )
    failures: list[InteractionTurnFailure] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stream_interjection_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def materialize_utterance(
    turn_state: InteractionTurnState,
    *,
    kind: str,
    text: str,
    message_id: str | None = None,
    delivered_message_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    memory_relevant: bool = True,
    source: str = "",
    visible: bool = True,
) -> InteractionUtterance:
    safe_kind_raw = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_" for char in kind
    ).strip("_")
    safe_kind = safe_kind_raw if safe_kind_raw in _VALID_UTTERANCE_KINDS else kind
    delivered_ids = [
        str(item).strip() for item in (delivered_message_ids or []) if str(item).strip()
    ]
    if message_id is None:
        message_id = (
            delivered_ids[0]
            if delivered_ids
            else (
                f"{turn_state.turn_id}::{safe_kind}::{turn_state.visible_message_counter:04d}"
            )
        )
    utterance = InteractionUtterance(
        turn_id=turn_state.turn_id,
        message_id=message_id,
        kind=kind,
        text=text,
        delivered_message_ids=delivered_ids,
        metadata=dict(metadata or {}),
        visible=visible,
        memory_relevant=memory_relevant,
        source=source,
    )
    turn_state.utterances.append(utterance)
    return utterance


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
        state = InteractionTurnState(
            turn_id=resolved_turn_id,
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
    state.completion_state.material_finalized = normalized is not None
    event.set_extra("_interaction_finalized_turn_material", normalized)
    event.set_extra(
        "_interaction_turn_material_finalized",
        state.completion_state.material_finalized,
    )


def get_interaction_turn_finalized_material(event) -> dict[str, Any] | None:
    state = get_interaction_turn_state(event)
    if state is not None and isinstance(state.finalized_turn_material, dict):
        return dict(state.finalized_turn_material)
    return None


def mark_interaction_turn_legacy_memory_persisted(
    event,
    persisted: bool = True,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.completion_state.legacy_memory_persisted = persisted
    event.set_extra("_interaction_legacy_memory_persisted", persisted)


def mark_interaction_turn_postprocess_dispatched(
    event,
    dispatched: bool = True,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.completion_state.postprocess_dispatched = dispatched
    event.set_extra("_interaction_turn_postprocess_dispatched", dispatched)


def mark_interaction_turn_completed(
    event,
    completed: bool = True,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.completion_state.completed = completed
    event.set_extra("_interaction_turn_completed", completed)


def record_interaction_turn_completion_failure(
    event,
    reason: str,
) -> None:
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        return
    state = ensure_interaction_turn_state(event)
    state.completion_state.failure_reason = clean_reason
    event.set_extra("_interaction_turn_completion_failure_reason", clean_reason)


def record_interaction_turn_failure(
    event,
    *,
    stage: str,
    reason: str,
    exception: BaseException | None = None,
    message: str | None = None,
    user_visible_action: str | None = None,
) -> None:
    clean_stage = str(stage or "").strip()
    clean_reason = str(reason or "").strip()
    if not clean_stage or not clean_reason:
        return
    state = ensure_interaction_turn_state(event)
    failure = InteractionTurnFailure(
        stage=clean_stage,
        reason=clean_reason,
        exception_type=type(exception).__name__ if exception is not None else None,
        message=message
        if message is not None
        else (str(exception) if exception else None),
        user_visible_action=user_visible_action,
        material_finalized=state.completion_state.material_finalized,
        legacy_memory_persisted=state.completion_state.legacy_memory_persisted,
        postprocess_dispatched=state.completion_state.postprocess_dispatched,
    )
    state.failures.append(failure)
    event.set_extra(
        "_interaction_turn_failures", [item.to_dict() for item in state.failures]
    )
    record_interaction_turn_completion_failure(event, f"{clean_stage}:{clean_reason}")


def get_interaction_turn_failures(event) -> list[dict[str, Any]]:
    state = get_interaction_turn_state(event)
    if state is None:
        return []
    return [failure.to_dict() for failure in state.failures]


def is_interaction_turn_completed(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.completion_state.completed
    return False


def set_interaction_turn_immediate_reply(event, reply: str | None) -> None:
    normalized_reply = (reply or "").strip() or None
    state = ensure_interaction_turn_state(event)
    state.immediate_reply = normalized_reply
    event.set_extra("_interaction_immediate_reply", normalized_reply)


def get_interaction_turn_immediate_reply(event) -> str | None:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.immediate_reply
    return None


def append_interaction_turn_visible_output(
    event,
    *,
    message_kind: str,
    text: str | None,
    message_id: str | None = None,
    delivered_message_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
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
    materialize_utterance(
        state,
        kind=message_kind,
        text=clean_text,
        message_id=message_id,
        delivered_message_ids=delivered_message_ids,
        metadata=metadata,
        memory_relevant=memory_relevant,
    )
    outputs = [dict(output) for output in state.visible_outputs]
    event.set_extra("_visible_turn_outputs", outputs)
    event.set_extra("_postprocess_visible_outputs", outputs)


def get_interaction_turn_visible_outputs(event) -> list[dict[str, Any]]:
    state = ensure_interaction_turn_state(event)
    return [dict(output) for output in state.visible_outputs]


def update_interaction_turn_stream_buffer(
    event,
    *,
    total_text: str,
    pending_text: str,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.stream_state.total_text = total_text
    state.stream_state.pending_text = pending_text
    state.core_stream_text = total_text
    state.core_stream_pending_text = pending_text
    event.set_extra("_interaction_core_stream_text", total_text)
    event.set_extra("_interaction_core_stream_pending_text", pending_text)


def set_interaction_turn_stream_progress(
    event,
    *,
    total_text: str,
    pending_text: str,
) -> None:
    update_interaction_turn_stream_buffer(
        event,
        total_text=total_text,
        pending_text=pending_text,
    )


def set_interaction_turn_stream_observation_count(
    event,
    window_index: int,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.stream_state.observation_count = window_index
    state.core_stream_observation_count = window_index
    event.set_extra("_interaction_core_stream_observation_count", window_index)


def add_interaction_turn_stream_observation_task(
    event,
    task: asyncio.Task[Any],
) -> None:
    state = ensure_interaction_turn_state(event)
    state.stream_state.observation_tasks.append(task)
    state.core_stream_observation_tasks.append(task)
    event.set_extra(
        "_interaction_stream_observation_tasks",
        list(state.stream_state.observation_tasks),
    )


def remove_interaction_turn_stream_observation_task(
    event,
    task: asyncio.Task[Any],
) -> None:
    state = ensure_interaction_turn_state(event)
    if task in state.stream_state.observation_tasks:
        state.stream_state.observation_tasks.remove(task)
    if task in state.core_stream_observation_tasks:
        state.core_stream_observation_tasks.remove(task)
    event.set_extra(
        "_interaction_stream_observation_tasks",
        list(state.stream_state.observation_tasks),
    )


def get_interaction_turn_stream_observation_tasks(
    event,
) -> list[asyncio.Task[Any]]:
    state = ensure_interaction_turn_state(event)
    return list(state.stream_state.observation_tasks)


def record_interaction_turn_stream_observation_failure(
    event,
    failure: str,
) -> None:
    clean_failure = str(failure or "").strip()
    if not clean_failure:
        return
    state = ensure_interaction_turn_state(event)
    state.stream_state.observation_failures.append(clean_failure)
    state.core_stream_observation_failures.append(clean_failure)
    event.set_extra(
        "_interaction_stream_observation_failures",
        list(state.stream_state.observation_failures),
    )


def get_interaction_turn_stream_text(event) -> str:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.stream_state.total_text
    return ""


def get_interaction_turn_stream_pending_text(event) -> str:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.stream_state.pending_text
    return ""


def get_interaction_turn_stream_observation_count(event) -> int:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.stream_state.observation_count
    return 0


def set_interaction_turn_core_streaming_active(
    event,
    is_active: bool,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.stream_state.active = is_active
    state.core_streaming_active = is_active
    event.set_extra("_interaction_core_streaming_active", is_active)


def mark_interaction_turn_core_streaming_result_consumed(
    event,
    consumed: bool = True,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.stream_state.result_consumed = consumed
    state.core_streaming_result_consumed = consumed
    event.set_extra("_interaction_core_streaming_result_consumed", consumed)


def has_interaction_turn_core_streaming_result_consumed(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.stream_state.result_consumed
    return False


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
    return False


def is_interaction_turn_core_streaming_active(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.stream_state.active
    return False


def mark_interaction_turn_stream_interjection_emitted(event) -> int:
    state = ensure_interaction_turn_state(event)
    state.stream_state.interjections_emitted += 1
    state.stream_interjections_emitted = state.stream_state.interjections_emitted
    event.set_extra(
        "_interaction_stream_interjections_emitted",
        state.stream_state.interjections_emitted,
    )
    return state.stream_state.interjections_emitted


def get_interaction_turn_stream_interjections_emitted(event) -> int:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.stream_state.interjections_emitted
    return 0


def next_interaction_turn_visible_message_id(event, message_kind: str) -> str:
    state = ensure_interaction_turn_state(event)
    turn_id = state.turn_id.strip() or "turn"
    state.visible_message_counter += 1
    event.set_extra(
        "_interaction_visible_message_counter",
        state.visible_message_counter,
    )
    safe_kind = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_" for char in message_kind
    ).strip("_")
    if not safe_kind:
        safe_kind = "message"
    return f"{turn_id}::{safe_kind}::{state.visible_message_counter:04d}"
