from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from astrbot.core.deadline import TurnDeadlineBudget
from astrbot.core.prompt.context_types import ContextPack

from .types import CorePlanningDecision, CoreTaskSpec, InteractionRouteDecision

if TYPE_CHECKING:
    from .personal_runtime import PersonalRuntimeKey

INTERACTION_TURN_STATE_EXTRA_KEY = "_interaction_turn_state"


class InteractionTurnStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InteractionTurnOutcome(str, Enum):
    REPLIED = "replied"
    SILENT = "silent"


class InteractionSpeculativePersonaStatus(str, Enum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    COMMITTED = "committed"
    EMITTED = "emitted"
    SUPPRESSED = "suppressed"
    FAILED = "failed"


class InteractionFinalOutputStatus(str, Enum):
    PENDING = "pending"
    RESERVED = "reserved"
    DELIVERED = "delivered"
    SUPPRESSED = "suppressed"
    FAILED = "failed"


class InteractionLifecycleStage(str, Enum):
    RECEIVED = "received"
    ROUTING = "routing"
    DELEGATED = "delegated"
    THINKING = "thinking"
    TOOL_RUNNING = "tool_running"
    SPEAKING = "speaking"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_VALID_UTTERANCE_KINDS = frozenset(
    {
        "immediate_reply",
        "stream_interjection",
        "passthrough",
        "core_reply",
        "core_stream",
        "finalized_reply",
        "plugin_direct",
        "plugin_persona",
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
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    collected_scopes: set[str] = field(default_factory=set)


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
    status: InteractionTurnStatus = InteractionTurnStatus.ACTIVE
    outcome: InteractionTurnOutcome | None = None
    material_finalized: bool = False
    postprocess_dispatched: bool = False
    completed: bool = False
    failure_reason: str | None = None
    terminal_at: float | None = None
    finalization_deferred: bool = False
    finalization_pending: bool = False


@dataclass(slots=True)
class InteractionTurnFailure:
    stage: str
    reason: str
    exception_type: str | None = None
    message: str | None = None
    user_visible_action: str | None = None
    material_finalized: bool = False
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
            "postprocess_dispatched": self.postprocess_dispatched,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class TurnExecutionScope:
    """Own every asynchronous task whose lifetime belongs to one turn."""

    tasks: dict[str, set[asyncio.Task[Any]]] = field(default_factory=dict)
    closed: bool = False

    def create_task(
        self,
        awaitable,
        *,
        role: str,
        name: str,
    ) -> asyncio.Task[Any]:
        if self.closed:
            raise RuntimeError("Turn execution scope is already closed")
        task = asyncio.create_task(awaitable, name=name)
        self.tasks.setdefault(role, set()).add(task)
        task.add_done_callback(lambda done: self._task_done(role, done))
        return task

    def cancel(self, role: str) -> bool:
        cancelled = False
        for task in tuple(self.tasks.get(role, ())):
            if not task.done():
                task.cancel()
                cancelled = True
        return cancelled

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        tasks = [task for role_tasks in self.tasks.values() for task in role_tasks]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()

    def _task_done(self, role: str, task: asyncio.Task[Any]) -> None:
        role_tasks = self.tasks.get(role)
        if role_tasks is not None:
            role_tasks.discard(task)
            if not role_tasks:
                self.tasks.pop(role, None)
        if task.cancelled():
            return
        task.exception()


@dataclass(slots=True)
class InteractionTurnState:
    turn_id: str
    deadline: TurnDeadlineBudget | None = None
    persona_id: str = ""
    personal_runtime_key: PersonalRuntimeKey | None = None
    runtime_config_id: str = ""
    runtime_audience_key: str = ""
    runtime_privacy_scope: str = ""
    runtime_reservation_state: str = ""
    prompt_build_config: Any | None = None
    context_material: InteractionContextMaterial | None = None
    context_material_task: asyncio.Task[InteractionContextMaterial] | None = None
    route_decision: InteractionRouteDecision | None = None
    core_planning_decision: CorePlanningDecision | None = None
    core_task_spec: CoreTaskSpec | None = None
    core_delegated: bool = False
    finalized_turn_material: dict[str, Any] | None = None
    immediate_reply: str | None = None
    speculative_persona_status: InteractionSpeculativePersonaStatus = (
        InteractionSpeculativePersonaStatus.NOT_STARTED
    )
    final_output_status: InteractionFinalOutputStatus = (
        InteractionFinalOutputStatus.PENDING
    )
    execution_scope: TurnExecutionScope = field(default_factory=TurnExecutionScope)
    utterances: list[InteractionUtterance] = field(default_factory=list)
    visible_outputs: list[dict[str, Any]] = field(default_factory=list)
    stream_state: InteractionStreamState = field(default_factory=InteractionStreamState)
    output_segment_counter: int = 0
    visible_message_counter: int = 0
    lifecycle_stage: InteractionLifecycleStage | None = None
    lifecycle_transitions: list[dict[str, Any]] = field(default_factory=list)
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
        turn_state.output_segment_counter += 1
        message_id = (
            f"{turn_state.turn_id}::segment::{safe_kind}::"
            f"{turn_state.output_segment_counter:04d}"
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


def build_interaction_turn_reply(
    visible_outputs: list[dict[str, Any]] | None,
    *,
    turn_id: str | None = None,
    utterances: list[InteractionUtterance] | None = None,
) -> str:
    if isinstance(utterances, list):
        parts = [
            utterance.text.strip()
            for utterance in utterances
            if utterance.kind != "stream_interjection"
            and utterance.memory_relevant
            and utterance.text.strip()
        ]
        if parts:
            return " ".join(parts)

    if not isinstance(visible_outputs, list):
        return ""
    clean_turn_id = (turn_id or "").strip()
    parts: list[str] = []
    for item in visible_outputs:
        if not isinstance(item, dict):
            continue
        if clean_turn_id and str(item.get("turn_id", "") or "").strip() != clean_turn_id:
            continue
        if not bool(item.get("memory_relevant", True)):
            continue
        text = str(item.get("text", "") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def get_interaction_turn_state(event) -> InteractionTurnState | None:
    state = event.get_extra(INTERACTION_TURN_STATE_EXTRA_KEY)
    if isinstance(state, InteractionTurnState):
        return state
    return None


def get_interaction_turn_deadline(event) -> TurnDeadlineBudget | None:
    state = get_interaction_turn_state(event)
    return state.deadline if state is not None else None


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


def set_interaction_turn_route_decision(
    event,
    decision: InteractionRouteDecision | None,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.route_decision = decision


def set_interaction_turn_core_planning_decision(
    event,
    decision: CorePlanningDecision | None,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.core_planning_decision = decision


def set_interaction_turn_core_task_spec(
    event,
    task_spec: CoreTaskSpec | None,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.core_task_spec = task_spec


def mark_interaction_turn_core_delegated(event) -> None:
    ensure_interaction_turn_state(event).core_delegated = True


def is_interaction_turn_core_delegated(event) -> bool:
    state = get_interaction_turn_state(event)
    return bool(state and state.core_delegated)


def set_interaction_turn_finalized_material(
    event,
    material: dict[str, Any] | None,
) -> None:
    state = ensure_interaction_turn_state(event)
    normalized = dict(material) if isinstance(material, dict) else None
    state.finalized_turn_material = normalized
    state.completion_state.material_finalized = normalized is not None
    if normalized is not None:
        try:
            state.completion_state.outcome = InteractionTurnOutcome(
                str(normalized.get("outcome", InteractionTurnOutcome.REPLIED.value))
            )
        except ValueError:
            state.completion_state.outcome = None
    else:
        state.completion_state.outcome = None
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


def mark_interaction_turn_postprocess_dispatched(
    event,
    dispatched: bool = True,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.completion_state.postprocess_dispatched = dispatched


def begin_interaction_turn_finalization_deferral(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is None:
        return False
    completion = state.completion_state
    if completion.finalization_deferred:
        return True
    completion.finalization_deferred = True
    completion.finalization_pending = False
    return True


def is_interaction_turn_finalization_deferred(event) -> bool:
    state = get_interaction_turn_state(event)
    return bool(state and state.completion_state.finalization_deferred)


def mark_interaction_turn_finalization_pending(event) -> None:
    state = ensure_interaction_turn_state(event)
    state.completion_state.finalization_pending = True


def consume_interaction_turn_finalization_pending(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is None:
        return False
    completion = state.completion_state
    pending = completion.finalization_pending
    completion.finalization_deferred = False
    completion.finalization_pending = False
    return pending


def cancel_interaction_turn_finalization_deferral(event) -> None:
    state = get_interaction_turn_state(event)
    if state is None:
        return
    state.completion_state.finalization_deferred = False
    state.completion_state.finalization_pending = False


def mark_interaction_turn_completed(
    event,
    completed: bool = True,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.completion_state.completed = completed
    state.completion_state.status = (
        InteractionTurnStatus.COMPLETED if completed else InteractionTurnStatus.ACTIVE
    )
    state.completion_state.terminal_at = time.time() if completed else None


def mark_interaction_turn_failed(event) -> None:
    state = ensure_interaction_turn_state(event)
    state.completion_state.completed = False
    state.completion_state.status = InteractionTurnStatus.FAILED
    state.completion_state.terminal_at = time.time()


def mark_interaction_turn_cancelled(event) -> None:
    state = ensure_interaction_turn_state(event)
    state.completion_state.completed = False
    state.completion_state.status = InteractionTurnStatus.CANCELLED
    state.completion_state.terminal_at = time.time()


def transition_interaction_lifecycle(
    event,
    stage: InteractionLifecycleStage,
    *,
    metadata: dict[str, Any] | None = None,
) -> tuple[InteractionLifecycleStage | None, dict[str, Any]]:
    state = ensure_interaction_turn_state(event)
    previous_stage = state.lifecycle_stage
    transition = {
        "stage": stage.value,
        "previous_stage": previous_stage.value if previous_stage is not None else None,
        "created_at": time.time(),
        "metadata": dict(metadata or {}),
    }
    state.lifecycle_stage = stage
    state.lifecycle_transitions.append(transition)
    return previous_stage, transition


def record_interaction_turn_completion_failure(
    event,
    reason: str,
) -> None:
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        return
    state = ensure_interaction_turn_state(event)
    state.completion_state.failure_reason = clean_reason


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
        postprocess_dispatched=state.completion_state.postprocess_dispatched,
    )
    state.failures.append(failure)
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
    utterance = materialize_utterance(
        state,
        kind=message_kind,
        text=clean_text,
        message_id=message_id,
        delivered_message_ids=delivered_message_ids,
        metadata=metadata,
        memory_relevant=memory_relevant,
    )
    item = {
        "turn_id": state.turn_id,
        "message_id": utterance.message_id,
        "delivered_message_ids": list(utterance.delivered_message_ids),
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


def update_interaction_turn_stream_buffer(
    event,
    *,
    total_text: str,
    pending_text: str,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.stream_state.total_text = total_text
    state.stream_state.pending_text = pending_text


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


def add_interaction_turn_stream_observation_task(
    event,
    task: asyncio.Task[Any],
) -> None:
    state = ensure_interaction_turn_state(event)
    state.stream_state.observation_tasks.append(task)


def remove_interaction_turn_stream_observation_task(
    event,
    task: asyncio.Task[Any],
) -> None:
    state = ensure_interaction_turn_state(event)
    if task in state.stream_state.observation_tasks:
        state.stream_state.observation_tasks.remove(task)


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


def mark_interaction_turn_core_streaming_result_consumed(
    event,
    consumed: bool = True,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.stream_state.result_consumed = consumed


def has_interaction_turn_core_streaming_result_consumed(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.stream_state.result_consumed
    return False


async def reserve_interaction_turn_final_output(event) -> bool:
    state = ensure_interaction_turn_state(event)
    async with state.lock:
        if state.final_output_status is not InteractionFinalOutputStatus.PENDING:
            return False
        state.final_output_status = InteractionFinalOutputStatus.RESERVED
        if (
            state.speculative_persona_status
            is InteractionSpeculativePersonaStatus.PENDING
        ):
            state.speculative_persona_status = (
                InteractionSpeculativePersonaStatus.SUPPRESSED
            )
            state.execution_scope.cancel("speculative_persona")
        return True


async def finish_interaction_turn_final_output(
    event,
    status: InteractionFinalOutputStatus,
) -> None:
    if status not in {
        InteractionFinalOutputStatus.DELIVERED,
        InteractionFinalOutputStatus.SUPPRESSED,
        InteractionFinalOutputStatus.FAILED,
    }:
        raise ValueError(f"Invalid terminal final output status: {status.value}")
    state = ensure_interaction_turn_state(event)
    async with state.lock:
        if state.final_output_status is InteractionFinalOutputStatus.PENDING:
            raise RuntimeError("Final output must be reserved before completion")
        if state.final_output_status is not InteractionFinalOutputStatus.RESERVED:
            if state.final_output_status is status:
                return
            raise RuntimeError(
                "Final output already reached terminal status: "
                f"{state.final_output_status.value}"
            )
        state.final_output_status = status


async def reserve_interaction_turn_immediate_output(event) -> bool:
    state = ensure_interaction_turn_state(event)
    async with state.lock:
        if state.speculative_persona_status is not InteractionSpeculativePersonaStatus.PENDING:
            return False
        if state.final_output_status is not InteractionFinalOutputStatus.PENDING:
            state.speculative_persona_status = (
                InteractionSpeculativePersonaStatus.SUPPRESSED
            )
            return False
        state.speculative_persona_status = (
            InteractionSpeculativePersonaStatus.COMMITTED
        )
        return True


def has_interaction_turn_final_output_claimed(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.final_output_status is not InteractionFinalOutputStatus.PENDING
    return False


def is_interaction_turn_core_streaming_active(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.stream_state.active
    return False


def mark_interaction_turn_stream_interjection_emitted(event) -> int:
    state = ensure_interaction_turn_state(event)
    state.stream_state.interjections_emitted += 1
    return state.stream_state.interjections_emitted


def get_interaction_turn_stream_interjections_emitted(event) -> int:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.stream_state.interjections_emitted
    return 0


def next_interaction_turn_output_segment_id(event, message_kind: str) -> str:
    state = ensure_interaction_turn_state(event)
    turn_id = state.turn_id.strip() or "turn"
    state.output_segment_counter += 1
    safe_kind = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_" for char in message_kind
    ).strip("_")
    if not safe_kind:
        safe_kind = "message"
    return f"{turn_id}::segment::{safe_kind}::{state.output_segment_counter:04d}"


def next_interaction_turn_visible_message_id(event, message_kind: str) -> str:
    state = ensure_interaction_turn_state(event)
    turn_id = state.turn_id.strip() or "turn"
    state.visible_message_counter += 1
    safe_kind = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_" for char in message_kind
    ).strip("_")
    if not safe_kind:
        safe_kind = "message"
    return f"{turn_id}::delivery::{safe_kind}::{state.visible_message_counter:04d}"
