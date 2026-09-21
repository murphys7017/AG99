from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from astrbot import logger
from astrbot.core.deadline import TurnDeadlineBudget
from astrbot.core.execution import (
    CORE_EXECUTION_SPEC_EXTRA_KEY,
    CoreEvent,
    CoreExecutionEvent,
    CoreExecutionEventKind,
    CoreExecutionHead,
    CoreExecutionLedgerPreparation,
    CoreExecutionSpec,
    get_core_execution_head,
    get_core_execution_lifecycle,
    get_core_execution_session,
)
from astrbot.core.prompt.context_types import ContextPack

from .types import (
    CorePlanningDecision,
    CoreTaskSpec,
    InteractionAgentConfig,
    InteractionRouteDecision,
    InteractionRouteMode,
)

if TYPE_CHECKING:
    from astrbot.core.message.message_chain_delivery import MessageChainDeliveryResult
    from astrbot.core.plugin_admission import PluginAdmissionSnapshot

    from .execution_capability_summary import ExecutionCapabilitySummary
    from .persona_domain import EffectivePersonaContext, PersonaDefinition
    from .personal_runtime import PersonalRuntimeKey

INTERACTION_TURN_STATE_EXTRA_KEY = "_interaction_turn_state"
INTERACTION_CORE_EXECUTION_JOURNAL_HEAD_EXTRA_KEY = "_interaction_core_execution_journal_head"
MAX_CORE_EXECUTION_EVENTS_PER_TURN = 64


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
        "core_progress",
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
    target_context_packs: dict[str, ContextPack] = field(default_factory=dict)
    target_context_tasks: dict[str, asyncio.Task[ContextPack]] = field(
        default_factory=dict
    )
    persona_payload: dict[str, Any] = field(default_factory=dict)
    persona_definition: PersonaDefinition | None = None
    effective_persona_context: EffectivePersonaContext | None = None
    memory_payload: dict[str, Any] = field(default_factory=dict)
    recent_messages: list[dict[str, Any]] = field(default_factory=list)
    input_payload: dict[str, Any] = field(default_factory=dict)
    capability_payload: dict[str, Any] = field(default_factory=dict)
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    collected_scopes: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class TurnAdmissionSnapshot:
    """Read-only diagnostic facts frozen when a turn is admitted."""

    turn_id: str
    unified_msg_origin: str
    config_id: str
    persona_id_at_admission: str | None
    has_runtime_config: bool
    has_plugin_admission: bool


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
    finalization_failed: bool = False
    finalization_failure_reason: str | None = None
    postprocess_failed: bool = False
    postprocess_failure_reason: str | None = None


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

    def cancel_and_detach(
        self,
        role: str,
        task: asyncio.Task[Any] | None = None,
    ) -> bool:
        """Cancel turn work without making scope shutdown wait for it."""
        role_tasks = self.tasks.get(role)
        if not role_tasks:
            return False
        if task is None:
            detached_tasks = tuple(role_tasks)
        elif task in role_tasks:
            detached_tasks = (task,)
        else:
            return False
        for detached_task in detached_tasks:
            role_tasks.discard(detached_task)
            if not detached_task.done():
                detached_task.cancel()
        if not role_tasks:
            self.tasks.pop(role, None)
        return any(not detached_task.done() for detached_task in detached_tasks)

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
    pipeline_event_prepared: bool = False
    pipeline_route_handled: bool = False
    emitting_immediate_reply: bool = False
    deadline: TurnDeadlineBudget | None = None
    interaction_config: InteractionAgentConfig | None = None
    runtime_config_snapshot: Mapping[str, Any] | None = None
    persona_id: str = ""
    personal_runtime_key: PersonalRuntimeKey | None = None
    runtime_config_id: str = ""
    runtime_audience_key: str = ""
    runtime_privacy_scope: str = ""
    runtime_reservation_state: str = ""
    prompt_build_config: Any | None = None
    context_material: InteractionContextMaterial | None = None
    context_material_task: asyncio.Task[InteractionContextMaterial] | None = None
    core_execution_capability_summary: ExecutionCapabilitySummary | None = None
    delivery_metadata: dict[str, Any] = field(default_factory=dict)
    fixed_conversation_required: bool = False
    fixed_conversation_id: str | None = None
    committed_turn_id: str | None = None
    committed_conversation_id: str | None = None
    delayed_history_skipped_reason: str | None = None
    conversation_history_failed: bool = False
    conversation_history_failure_reason: str | None = None
    pipeline_output_suppressed: bool = False
    inbound_media_materialized: bool = False
    stt_transcribed: bool = False
    stt_failed: bool = False
    stt_failure_reason: str | None = None
    core_planner_failed: bool = False
    core_planner_failure_reason: str | None = None
    core_planner_recovered_via_persona: bool = False
    expression_failed: bool = False
    expression_failure_reason: str | None = None
    expression_fallback_used: bool = False
    expression_primary_failure_reason: str | None = None
    expression_fallback_provider_id: str | None = None
    route_decision: InteractionRouteDecision | None = None
    core_planning_decision: CorePlanningDecision | None = None
    core_task_spec: CoreTaskSpec | None = None
    core_execution_spec: CoreExecutionSpec | None = None
    core_delegated: bool = False
    core_provider_id: str | None = None
    finalized_turn_material: dict[str, Any] | None = None
    immediate_reply: str | None = None
    personal_emitted_monotonic: float | None = None
    speculative_persona_status: InteractionSpeculativePersonaStatus = (
        InteractionSpeculativePersonaStatus.NOT_STARTED
    )
    final_output_status: InteractionFinalOutputStatus = (
        InteractionFinalOutputStatus.PENDING
    )
    execution_scope: TurnExecutionScope = field(default_factory=TurnExecutionScope)
    utterances: list[InteractionUtterance] = field(default_factory=list)
    visible_outputs: list[dict[str, Any]] = field(default_factory=list)
    output_delivery_receipts: list[dict[str, Any]] = field(default_factory=list)
    assistant_artifacts: list[dict[str, Any]] = field(default_factory=list)
    plugin_output_transaction_active: bool = False
    plugin_output_transaction_start: int | None = None
    plugin_output_transaction_artifacts: list[dict[str, Any]] = field(
        default_factory=list
    )
    plugin_output_last_mode: str | None = None
    plugin_output_last_kind: str | None = None
    plugin_output_effect_calls: list[Any] = field(default_factory=list)
    tool_stage_observation_records: list[dict[str, Any]] = field(default_factory=list)
    tool_stage_observation_states: dict[str, dict[str, bool]] = field(
        default_factory=dict
    )
    visible_message_fingerprints: set[str] = field(default_factory=set)
    stream_state: InteractionStreamState = field(default_factory=InteractionStreamState)
    output_segment_counter: int = 0
    visible_message_counter: int = 0
    lifecycle_stage: InteractionLifecycleStage | None = None
    lifecycle_transitions: list[dict[str, Any]] = field(default_factory=list)
    core_execution_events: list[CoreExecutionEvent] = field(default_factory=list)
    completion_state: InteractionTurnCompletionState = field(
        default_factory=InteractionTurnCompletionState
    )
    failures: list[InteractionTurnFailure] = field(default_factory=list)
    #: Frozen plugin capability admission for this turn. Built once at turn
    #: start so every consumer agrees and a mid-turn plugin reload cannot change
    #: the answer between two capability lookups.
    plugin_admission: PluginAdmissionSnapshot | None = None
    admission_snapshot: TurnAdmissionSnapshot | None = None
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
        if (
            clean_turn_id
            and str(item.get("turn_id", "") or "").strip() != clean_turn_id
        ):
            continue
        if not bool(item.get("memory_relevant", True)):
            continue
        text = str(item.get("text", "") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def get_interaction_turn_state(event) -> InteractionTurnState | None:
    state = getattr(event, "_interaction_turn_state", None)
    if isinstance(state, InteractionTurnState):
        return state
    state = event.get_extra(INTERACTION_TURN_STATE_EXTRA_KEY)
    if isinstance(state, InteractionTurnState):
        # Read legacy events and promote them to the typed owner.  This is a
        # one-way compatibility bridge; new writes go through private storage.
        try:
            event._interaction_turn_state = state
        except AttributeError:
            pass
        return state
    return None


def get_interaction_turn_config(event) -> InteractionAgentConfig | None:
    state = get_interaction_turn_state(event)
    return state.interaction_config if state is not None else None


def get_interaction_turn_runtime_config(event) -> Mapping[str, Any] | None:
    state = get_interaction_turn_state(event)
    return state.runtime_config_snapshot if state is not None else None


def is_interaction_turn_pipeline_route_handled(event) -> bool:
    state = get_interaction_turn_state(event)
    return bool(state and state.pipeline_route_handled)


def mark_interaction_turn_pipeline_route_handled(event) -> None:
    ensure_interaction_turn_state(event).pipeline_route_handled = True


def set_interaction_turn_config(
    event,
    interaction_config: InteractionAgentConfig,
) -> InteractionAgentConfig:
    """Freeze the Interaction configuration selected when this turn is admitted."""
    state = ensure_interaction_turn_state(event)
    if state.interaction_config is None:
        state.interaction_config = interaction_config
    return state.interaction_config


def set_interaction_turn_runtime_config(
    event,
    runtime_config: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Deep-copy the runtime configuration selected when this turn is admitted."""
    state = ensure_interaction_turn_state(event)
    if state.runtime_config_snapshot is None:
        state.runtime_config_snapshot = deepcopy(dict(runtime_config))
    return state.runtime_config_snapshot


def get_interaction_turn_deadline(event) -> TurnDeadlineBudget | None:
    state = get_interaction_turn_state(event)
    return state.deadline if state is not None else None


def get_interaction_turn_plugin_admission(event) -> PluginAdmissionSnapshot | None:
    """Return the frozen plugin admission snapshot for this turn."""
    state = get_interaction_turn_state(event)
    return state.plugin_admission if state is not None else None


def set_interaction_turn_plugin_admission(
    event,
    snapshot: PluginAdmissionSnapshot,
) -> None:
    """Freeze plugin admission for this turn. First writer wins.

    The snapshot must not be replaced mid-turn: replacing it would let two
    consumers of the same turn observe different admission state.
    """
    state = ensure_interaction_turn_state(event)
    if state.plugin_admission is None:
        state.plugin_admission = snapshot


def freeze_interaction_turn_admission_snapshot(
    event,
) -> TurnAdmissionSnapshot:
    """Freeze admission diagnostics without changing runtime behavior."""
    state = ensure_interaction_turn_state(event)
    if state.admission_snapshot is not None:
        return state.admission_snapshot

    config_id = str(
        state.runtime_config_id
        or event.get_extra("_astrbot_config_id", "")
        or "default"
    ).strip() or "default"
    persona_id = str(state.persona_id or "").strip() or None
    snapshot = TurnAdmissionSnapshot(
        turn_id=state.turn_id,
        unified_msg_origin=str(getattr(event, "unified_msg_origin", "") or ""),
        config_id=config_id,
        persona_id_at_admission=persona_id,
        has_runtime_config=state.runtime_config_snapshot is not None,
        has_plugin_admission=state.plugin_admission is not None,
    )
    state.admission_snapshot = snapshot
    return snapshot


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
        try:
            event._interaction_turn_state = state
        except AttributeError:
            # Minimal test/event doubles may only expose the public extra API.
            event.set_extra(INTERACTION_TURN_STATE_EXTRA_KEY, state)
        else:
            # Keep the old key as a one-way compatibility projection.
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
        if state.persona_id != normalized_persona_id:
            logger.debug(
                "DIAG interaction.persona_identity: turn_id=%s "
                "previous_persona_id=%s persona_id=%s",
                state.turn_id,
                state.persona_id or "",
                normalized_persona_id,
            )
        state.persona_id = normalized_persona_id


def set_interaction_turn_delivery_metadata(
    event,
    metadata: dict[str, Any] | None,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.delivery_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    event.set_extra("_interaction_delivery_metadata", dict(state.delivery_metadata))


def get_interaction_turn_delivery_metadata(event) -> dict[str, Any]:
    state = get_interaction_turn_state(event)
    if state is not None:
        return dict(state.delivery_metadata)
    metadata = event.get_extra("_interaction_delivery_metadata", {})
    return dict(metadata) if isinstance(metadata, dict) else {}


def set_interaction_turn_fixed_conversation_id(
    event,
    conversation_id: str | None,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.fixed_conversation_required = True
    state.fixed_conversation_id = str(conversation_id or "").strip() or None
    event.set_extra(
        "_interaction_fixed_conversation_id",
        state.fixed_conversation_id,
    )


def get_interaction_turn_fixed_conversation_id(event) -> str | None:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.fixed_conversation_id
    conversation_id = event.get_extra("_interaction_fixed_conversation_id")
    return str(conversation_id).strip() if conversation_id else None


def is_interaction_turn_fixed_conversation_required(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.fixed_conversation_required
    return "_interaction_fixed_conversation_id" in (event.get_extra(default={}) or {})


def mark_interaction_turn_conversation_committed(
    event,
    *,
    turn_id: str,
    conversation_id: str,
) -> None:
    state = ensure_interaction_turn_state(event, turn_id=turn_id)
    state.committed_turn_id = str(turn_id or "").strip() or None
    state.committed_conversation_id = str(conversation_id or "").strip() or None
    event.set_extra(
        "_interaction_conversation_committed_turn_id",
        state.committed_turn_id,
    )
    event.set_extra(
        "_interaction_committed_conversation_id",
        state.committed_conversation_id,
    )


def get_interaction_turn_committed_turn_id(event) -> str | None:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.committed_turn_id
    value = event.get_extra("_interaction_conversation_committed_turn_id")
    return str(value).strip() if value else None


def get_interaction_turn_committed_conversation_id(event) -> str | None:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.committed_conversation_id
    value = event.get_extra("_interaction_committed_conversation_id")
    return str(value).strip() if value else None


def record_interaction_turn_delayed_history_skip(
    event,
    reason: str,
) -> None:
    clean_reason = str(reason or "").strip() or None
    state = ensure_interaction_turn_state(event)
    state.delayed_history_skipped_reason = clean_reason
    event.set_extra("_interaction_delayed_history_skipped_reason", clean_reason)


def get_interaction_turn_delayed_history_skip_reason(event) -> str | None:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.delayed_history_skipped_reason
    value = event.get_extra("_interaction_delayed_history_skipped_reason")
    return str(value).strip() if value else None


def record_interaction_turn_conversation_history_failure(
    event,
    reason: str,
) -> None:
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        return
    state = ensure_interaction_turn_state(event)
    state.conversation_history_failed = True
    state.conversation_history_failure_reason = clean_reason
    event.set_extra("_interaction_conversation_history_failed", True)
    event.set_extra(
        "_interaction_conversation_history_failure_reason",
        clean_reason,
    )


def set_interaction_turn_pipeline_output_suppressed(
    event,
    suppressed: bool = True,
) -> None:
    """Set the canonical turn-level suppression flag."""
    state = ensure_interaction_turn_state(event)
    state.pipeline_output_suppressed = bool(suppressed)
    # Keep the legacy projection for copied branch and external events.
    event.set_extra("_interaction_pipeline_output_suppressed", bool(suppressed))


def is_interaction_turn_pipeline_output_suppressed(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.pipeline_output_suppressed
    return bool(event.get_extra("_interaction_pipeline_output_suppressed", False))


def set_interaction_turn_emitting_immediate_reply(
    event,
    emitting: bool = True,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.emitting_immediate_reply = bool(emitting)
    event.set_extra("_interaction_emitting_immediate_reply", bool(emitting))


def is_interaction_turn_emitting_immediate_reply(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.emitting_immediate_reply
    return bool(event.get_extra("_interaction_emitting_immediate_reply", False))


def set_interaction_turn_inbound_media_materialized(
    event,
    materialized: bool = True,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.inbound_media_materialized = bool(materialized)
    event.set_extra("_interaction_inbound_media_materialized", bool(materialized))


def is_interaction_turn_inbound_media_materialized(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.inbound_media_materialized
    return bool(event.get_extra("_interaction_inbound_media_materialized", False))


def mark_interaction_turn_stt_transcribed(
    event,
    transcribed: bool = True,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.stt_transcribed = bool(transcribed)
    event.set_extra("_interaction_stt_transcribed", bool(transcribed))


def is_interaction_turn_stt_transcribed(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.stt_transcribed
    return bool(event.get_extra("_interaction_stt_transcribed", False))


def record_interaction_turn_stt_failure(
    event,
    reason: str | None = None,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.stt_failed = True
    state.stt_failure_reason = str(reason or "").strip() or None
    event.set_extra("_interaction_stt_failed", True)
    if state.stt_failure_reason is not None:
        event.set_extra(
            "_interaction_stt_failure_reason",
            state.stt_failure_reason,
        )


def is_interaction_turn_stt_failed(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.stt_failed
    return bool(event.get_extra("_interaction_stt_failed", False))


def get_interaction_turn_stt_failure_reason(event) -> str | None:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.stt_failure_reason
    reason = event.get_extra("_interaction_stt_failure_reason")
    return str(reason).strip() if reason else None


def record_interaction_turn_core_planner_failure(
    event,
    reason: str | None = None,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.core_planner_failed = True
    state.core_planner_failure_reason = str(reason or "").strip() or None
    event.set_extra("_interaction_core_planner_failed", True)
    if state.core_planner_failure_reason is not None:
        event.set_extra(
            "_interaction_core_planner_failure_reason",
            state.core_planner_failure_reason,
        )


def mark_interaction_turn_core_planner_recovered_via_persona(
    event,
    recovered: bool = True,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.core_planner_recovered_via_persona = bool(recovered)
    event.set_extra(
        "_interaction_core_planner_recovered_via_persona",
        bool(recovered),
    )


def record_interaction_turn_expression_failure(
    event,
    reason: str | None = None,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.expression_failed = True
    state.expression_failure_reason = str(reason or "").strip() or None
    event.set_extra("_interaction_expression_failed", True)
    if state.expression_failure_reason is not None:
        event.set_extra(
            "_interaction_expression_failure_reason",
            state.expression_failure_reason,
        )


def record_interaction_turn_expression_fallback(
    event,
    *,
    primary_failure_reason: str | None,
    provider_id: str | None,
) -> None:
    state = ensure_interaction_turn_state(event)
    state.expression_fallback_used = True
    state.expression_primary_failure_reason = (
        str(primary_failure_reason or "").strip() or None
    )
    state.expression_fallback_provider_id = str(provider_id or "").strip() or None
    event.set_extra("_interaction_expression_fallback_used", True)
    if state.expression_primary_failure_reason is not None:
        event.set_extra(
            "_interaction_expression_primary_failure_reason",
            state.expression_primary_failure_reason,
        )
    if state.expression_fallback_provider_id is not None:
        event.set_extra(
            "_interaction_expression_fallback_provider_id",
            state.expression_fallback_provider_id,
        )


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


def set_interaction_turn_core_execution_spec(
    event,
    execution_spec: CoreExecutionSpec | None,
) -> CoreExecutionSpec | None:
    """Store the Core execution contract on the typed turn owner."""

    state = ensure_interaction_turn_state(event)
    current_head = get_core_execution_head(event)
    if execution_spec is not None and (
        state.core_execution_spec is None or current_head is None
    ):
        state.core_execution_spec = execution_spec
        # Preserve the public/legacy projection for plugins and diagnostics.
        event.set_extra(CORE_EXECUTION_SPEC_EXTRA_KEY, execution_spec)
    return state.core_execution_spec


def get_interaction_turn_core_execution_spec(
    event,
) -> CoreExecutionSpec | None:
    state = get_interaction_turn_state(event)
    if state is not None and state.core_execution_spec is not None:
        return state.core_execution_spec
    legacy = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    if isinstance(legacy, CoreExecutionSpec):
        if state is not None:
            state.core_execution_spec = legacy
        return legacy
    return None


def begin_interaction_turn_plugin_output_transaction(event) -> int | None:
    state = get_interaction_turn_state(event)
    if state is None or not state.plugin_output_transaction_active:
        return None
    if state.plugin_output_transaction_start is None:
        state.plugin_output_transaction_start = len(state.visible_outputs)
    return state.plugin_output_transaction_start


def get_interaction_turn_plugin_output_transaction(
    event,
) -> tuple[bool, int | None, list[dict[str, Any]]]:
    state = get_interaction_turn_state(event)
    if state is not None:
        return (
            state.plugin_output_transaction_active,
            state.plugin_output_transaction_start,
            [dict(item) for item in state.plugin_output_transaction_artifacts],
        )
    active = bool(event.get_extra("_interaction_plugin_output_transaction_active", False))
    start = event.get_extra("_interaction_plugin_output_transaction_start")
    artifacts = event.get_extra("_interaction_plugin_output_transaction_artifacts", [])
    return active, start if isinstance(start, int) else None, (
        [dict(item) for item in artifacts] if isinstance(artifacts, list) else []
    )


def set_interaction_turn_plugin_output_transaction(
    event,
    *,
    active: bool | None = None,
    start: int | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> None:
    state = ensure_interaction_turn_state(event)
    if active is not None:
        state.plugin_output_transaction_active = bool(active)
    if start is not None or not state.plugin_output_transaction_active:
        state.plugin_output_transaction_start = start
    if artifacts is not None:
        state.plugin_output_transaction_artifacts = [
            dict(item) for item in artifacts if isinstance(item, dict)
        ]
    # Compatibility projection only.
    event.set_extra(
        "_interaction_plugin_output_transaction_active",
        state.plugin_output_transaction_active,
    )
    event.set_extra(
        "_interaction_plugin_output_transaction_start",
        state.plugin_output_transaction_start,
    )
    event.set_extra(
        "_interaction_plugin_output_transaction_artifacts",
        [dict(item) for item in state.plugin_output_transaction_artifacts]
        if state.plugin_output_transaction_artifacts
        else None,
    )


def get_interaction_turn_tool_stage_observation_records(
    event,
) -> list[dict[str, Any]]:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.tool_stage_observation_records
    records = event.get_extra("_interaction_tool_stage_observation_tasks", [])
    return records if isinstance(records, list) else []


def set_interaction_turn_tool_stage_observation_records(
    event,
    records: list[dict[str, Any]],
) -> None:
    state = ensure_interaction_turn_state(event)
    state.tool_stage_observation_records = records
    # Compatibility projection only.
    event.set_extra("_interaction_tool_stage_observation_tasks", records)


def get_interaction_turn_tool_stage_observation_state(
    event,
    descriptor: str,
) -> dict[str, bool]:
    state = ensure_interaction_turn_state(event)
    result = state.tool_stage_observation_states.get(descriptor)
    if not isinstance(result, dict):
        result = {
            "running_attempt_started": False,
            "running_emitted": False,
            "completed_attempt_started": False,
            "completed_emitted": False,
        }
        state.tool_stage_observation_states[descriptor] = result
    # Compatibility projection only.
    event.set_extra(
        "_interaction_tool_stage_observation_state",
        state.tool_stage_observation_states,
    )
    return result


def set_interaction_turn_plugin_output_metadata(
    event,
    *,
    mode: str | None = None,
    kind: str | None = None,
    effect_calls: list[Any] | None = None,
) -> None:
    state = ensure_interaction_turn_state(event)
    if mode is not None:
        state.plugin_output_last_mode = mode
        event.set_extra("_interaction_plugin_output_last_mode", mode)
    if kind is not None:
        state.plugin_output_last_kind = kind
        event.set_extra("_interaction_plugin_output_last_kind", kind)
    if effect_calls is not None:
        state.plugin_output_effect_calls = list(effect_calls)
        event.set_extra(
            "_interaction_plugin_output_effect_calls",
            list(state.plugin_output_effect_calls),
        )


def mark_interaction_turn_core_delegated(event) -> None:
    ensure_interaction_turn_state(event).core_delegated = True


def get_interaction_turn_core_provider_id(event) -> str | None:
    state = get_interaction_turn_state(event)
    return state.core_provider_id if state is not None else None


def set_interaction_turn_core_provider_id(
    event,
    provider_id: str | None,
) -> str | None:
    state = ensure_interaction_turn_state(event)
    if state.core_provider_id is None:
        normalized = str(provider_id or "").strip()
        state.core_provider_id = normalized or None
    return state.core_provider_id


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


def _project_core_execution_event_to_interaction_turn(
    event,
    envelope: CoreEvent | None,
) -> CoreExecutionEvent | None:
    """Project one Head fact into the owning Interaction journal and trace."""

    if envelope is None or not event.get_extra("_interaction_enabled", False):
        return None
    state = get_interaction_turn_state(event)
    execution_spec = get_interaction_turn_core_execution_spec(event)
    if state is None or not isinstance(execution_spec, CoreExecutionSpec):
        return None
    execution_event = envelope.execution
    if (
        execution_spec.turn_id != state.turn_id
        or execution_event.execution_id != execution_spec.execution_id
        or execution_event.turn_id != state.turn_id
    ):
        return None

    existing = state.core_execution_events
    replay_key = execution_event.replay_key
    if replay_key is not None and any(
        item.execution_id == execution_event.execution_id
        and item.replay_key == replay_key
        for item in existing
    ):
        return None
    if execution_event.is_terminal and any(
        item.execution_id == execution_event.execution_id and item.is_terminal
        for item in existing
    ):
        return None

    if not _make_interaction_turn_core_execution_journal_room(
        existing,
        execution_event,
    ):
        return None
    existing.append(execution_event)
    trace = getattr(event, "trace", None)
    record = getattr(trace, "record", None)
    if callable(record):
        try:
            record(
                "core_execution_event",
                execution_id=execution_event.execution_id,
                core_task_id=execution_event.core_task_id,
                turn_id=execution_event.turn_id,
                executor_id=execution_event.executor_id,
                kind=execution_event.kind.value,
                sequence=envelope.sequence,
                metadata=execution_event.metadata_for_trace(),
            )
        except Exception:
            pass
    return execution_event


def _make_interaction_turn_core_execution_journal_room(
    existing: list[CoreExecutionEvent],
    execution_event: CoreExecutionEvent,
) -> bool:
    """Keep terminal facts while bounding the local Interaction projection."""

    if len(existing) < MAX_CORE_EXECUTION_EVENTS_PER_TURN:
        return True
    eviction_kinds = [CoreExecutionEventKind.PROGRESS]
    if execution_event.is_terminal:
        eviction_kinds.append(CoreExecutionEventKind.ARTIFACT_READY)
    for eviction_kind in eviction_kinds:
        for index, item in enumerate(existing):
            if (
                item.execution_id == execution_event.execution_id
                and item.kind is eviction_kind
            ):
                del existing[index]
                return True
    return False


def record_interaction_turn_core_execution_stop_callback_failure(
    event,
    execution_event: CoreExecutionEvent,
    *,
    error: str | None,
) -> None:
    """Record a non-fatal stop failure after the terminal event is projected."""

    if not error:
        return
    trace = getattr(event, "trace", None)
    record = getattr(trace, "record", None)
    if not callable(record):
        return
    try:
        record(
            "core_execution_stop_callback_failed",
            execution_id=execution_event.execution_id,
            turn_id=execution_event.turn_id,
            executor_id=execution_event.executor_id,
            error=error,
        )
    except Exception:
        pass


def record_interaction_turn_core_execution_ledger_settlement(
    event,
    preparation: CoreExecutionLedgerPreparation,
    *,
    executor_id: str,
    inserted: bool,
) -> None:
    """Record one inserted or deduplicated Ledger append without execution material."""

    if not event.get_extra("_interaction_enabled", False):
        return
    state = get_interaction_turn_state(event)
    execution_spec = get_interaction_turn_core_execution_spec(event)
    prepared_spec = preparation.execution_spec
    if (
        state is None
        or not isinstance(execution_spec, CoreExecutionSpec)
        or execution_spec.execution_id != prepared_spec.execution_id
        or execution_spec.turn_id != state.turn_id
        or prepared_spec.turn_id != state.turn_id
    ):
        return
    trace = getattr(event, "trace", None)
    record = getattr(trace, "record", None)
    if not callable(record):
        return
    outcome = preparation.outcome
    try:
        record(
            "core_execution_ledger_settled",
            execution_id=prepared_spec.execution_id,
            core_task_id=prepared_spec.core_task_id,
            turn_id=prepared_spec.turn_id,
            executor_id=executor_id,
            status=preparation.status,
            inserted=bool(inserted),
            deduplicated=not bool(inserted),
            terminal_kind=(
                outcome.terminal_event.kind.value if outcome is not None else None
            ),
            used_fallback=outcome is None,
        )
    except Exception:
        pass


def record_interaction_turn_core_execution_ledger_persist_failure(
    event,
    *,
    executor_id: str,
    error: Exception,
) -> None:
    """Record an execution-scoped Ledger persistence failure."""

    error_text = str(error)[:2000]
    execution_spec = get_interaction_turn_core_execution_spec(event)
    state = get_interaction_turn_state(event)
    if (
        not event.get_extra("_interaction_enabled", False)
        or state is None
        or not isinstance(execution_spec, CoreExecutionSpec)
        or execution_spec.turn_id != state.turn_id
    ):
        return
    trace = getattr(event, "trace", None)
    record = getattr(trace, "record", None)
    if not callable(record):
        return
    fields = {
        "executor_id": executor_id,
        "error_type": type(error).__name__,
        "error": error_text,
        "execution_id": execution_spec.execution_id,
        "core_task_id": execution_spec.core_task_id,
        "turn_id": execution_spec.turn_id,
    }
    try:
        record("core_execution_ledger_persist_failed", **fields)
    except Exception:
        pass


def bind_interaction_turn_core_execution_journal(
    event,
    execution_head: CoreExecutionHead,
) -> bool:
    """Attach the Interaction journal as the Head's local event projection."""

    if not event.get_extra("_interaction_enabled", False):
        return False
    state = get_interaction_turn_state(event)
    execution_spec = get_interaction_turn_core_execution_spec(event)
    if (
        state is None
        or not isinstance(execution_spec, CoreExecutionSpec)
        or execution_spec.turn_id != state.turn_id
        or execution_head.spec.execution_id != execution_spec.execution_id
        or execution_head.spec.turn_id != state.turn_id
    ):
        return False
    bound_head = event.get_extra(INTERACTION_CORE_EXECUTION_JOURNAL_HEAD_EXTRA_KEY)
    if bound_head is execution_head:
        return True
    if bound_head is not None:
        raise ValueError("Interaction Core execution journal is already bound to another Head")

    def project_event(envelope: CoreEvent) -> CoreExecutionEvent | None:
        return _project_core_execution_event_to_interaction_turn(event, envelope)

    execution_head.subscribe(project_event)
    for envelope in execution_head.events:
        project_event(envelope)
    event.set_extra(INTERACTION_CORE_EXECUTION_JOURNAL_HEAD_EXTRA_KEY, execution_head)
    return True


def record_interaction_turn_core_execution_event(
    event,
    *,
    kind: CoreExecutionEventKind,
    executor_id: str,
    metadata: Mapping[str, Any] | None = None,
) -> CoreExecutionEvent | None:
    """Record one non-visible execution fact through the current Core boundary."""

    if not event.get_extra("_interaction_enabled", False):
        return None
    state = get_interaction_turn_state(event)
    execution_spec = get_interaction_turn_core_execution_spec(event)
    if state is None or not isinstance(execution_spec, CoreExecutionSpec):
        return None
    if execution_spec.turn_id != state.turn_id:
        return None

    execution_event = CoreExecutionEvent.from_spec(
        execution_spec,
        kind=kind,
        executor_id=executor_id,
        metadata=metadata,
    )
    existing = state.core_execution_events
    replay_key = execution_event.replay_key
    if replay_key is not None and any(
        item.execution_id == execution_event.execution_id
        and item.replay_key == replay_key
        for item in existing
    ):
        return None
    if execution_event.is_terminal and any(
        item.execution_id == execution_event.execution_id and item.is_terminal
        for item in existing
    ):
        return None

    execution_head = get_core_execution_head(event)
    if execution_head is not None:
        bind_interaction_turn_core_execution_journal(event, execution_head)
        projected = execution_head.emit_event(
            kind=kind,
            executor_id=executor_id,
            metadata=metadata,
        )
        if kind is CoreExecutionEventKind.CANCELLED:
            record_interaction_turn_core_execution_stop_callback_failure(
                event,
                projected,
                error=execution_head.executor_stop_error,
            )
        return projected

    execution_lifecycle = get_core_execution_lifecycle(event)
    execution_session = get_core_execution_session(event)
    if execution_lifecycle is not None:
        if kind is CoreExecutionEventKind.CANCELLED:
            envelope = execution_lifecycle.cancel(
                executor_id=executor_id,
                metadata=metadata,
            )
        else:
            envelope = execution_lifecycle.record_event(execution_event)
    elif execution_session is not None:
        envelope = execution_session.record_event(execution_event)
    else:
        envelope = CoreEvent(sequence=len(state.core_execution_events) + 1, execution=execution_event)

    return (
        _project_interaction_turn_core_execution_event_from_legacy_bridge(
            event,
            envelope,
            execution_event,
            kind=kind,
            lifecycle=execution_lifecycle,
        )
    )


def _project_interaction_turn_core_execution_event_from_legacy_bridge(
    event,
    envelope: CoreEvent,
    execution_event: CoreExecutionEvent,
    *,
    kind: CoreExecutionEventKind,
    lifecycle,
) -> CoreExecutionEvent | None:
    """Retain diagnostics for the compatibility path without a Head binding."""

    if envelope.execution is not execution_event:
        return None
    projected = _project_core_execution_event_to_interaction_turn(event, envelope)
    if kind is CoreExecutionEventKind.CANCELLED and lifecycle is not None:
        record_interaction_turn_core_execution_stop_callback_failure(
            event,
            envelope.execution,
            error=lifecycle.executor_stop_error,
        )
    return projected


def get_interaction_turn_core_execution_events(
    event,
) -> list[CoreExecutionEvent]:
    state = get_interaction_turn_state(event)
    return list(state.core_execution_events) if state is not None else []


def record_interaction_turn_completion_failure(
    event,
    reason: str,
) -> None:
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        return
    state = ensure_interaction_turn_state(event)
    state.completion_state.failure_reason = clean_reason


def record_interaction_turn_finalization_failure(
    event,
    reason: str,
) -> None:
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        return
    state = ensure_interaction_turn_state(event)
    state.completion_state.finalization_failed = True
    state.completion_state.finalization_failure_reason = clean_reason
    event.set_extra("_interaction_turn_finalization_failed", True)
    event.set_extra("_interaction_turn_finalization_failure_reason", clean_reason)
    record_interaction_turn_completion_failure(event, clean_reason)


def record_interaction_turn_postprocess_failure(
    event,
    reason: str,
) -> None:
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        return
    state = ensure_interaction_turn_state(event)
    state.completion_state.postprocess_failed = True
    state.completion_state.postprocess_failure_reason = clean_reason
    event.set_extra("_interaction_turn_postprocess_failed", True)
    event.set_extra("_interaction_turn_postprocess_failure_reason", clean_reason)
    record_interaction_turn_completion_failure(event, f"postprocess:{clean_reason}")


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


def is_interaction_turn_completed(event) -> bool:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.completion_state.completed
    return False


def set_interaction_turn_immediate_reply(event, reply: str | None) -> None:
    normalized_reply = (reply or "").strip() or None
    state = ensure_interaction_turn_state(event)
    state.immediate_reply = normalized_reply


def get_interaction_turn_immediate_reply(event) -> str | None:
    state = get_interaction_turn_state(event)
    if state is not None:
        return state.immediate_reply
    return None


def mark_interaction_turn_personal_emitted(event) -> float:
    state = ensure_interaction_turn_state(event)
    if state.personal_emitted_monotonic is None:
        state.personal_emitted_monotonic = time.monotonic()
    return state.personal_emitted_monotonic


def get_interaction_turn_personal_emitted_monotonic(event) -> float | None:
    state = get_interaction_turn_state(event)
    return state.personal_emitted_monotonic if state is not None else None


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


_DELIVERY_COMPLETION_TERMINAL_STATUSES = frozenset(
    {
        "not_attempted",
        "completed",
        "failed",
        "unknown",
        "not_required",
    }
)
_DELIVERY_FAILURE_STAGES = frozenset(
    {
        "physical_send",
        "message_completion",
    }
)
InteractionDeliveryCompletionStatus = Literal[
    "not_attempted",
    "completed",
    "failed",
    "unknown",
    "not_required",
]
InteractionDeliveryFailureStage = Literal[
    "physical_send",
    "message_completion",
]


def begin_interaction_turn_delivery_receipt(
    event,
    *,
    message_id: str,
    message_kind: str,
    delivery_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state = ensure_interaction_turn_state(event)
    normalized_message_id = str(message_id or "").strip()
    if not normalized_message_id:
        raise ValueError("Delivery receipt requires a non-empty message_id")
    if any(
        item.get("message_id") == normalized_message_id
        for item in state.output_delivery_receipts
    ):
        raise ValueError(
            f"Delivery receipt already exists: {normalized_message_id}"
        )
    receipt = {
        "turn_id": state.turn_id,
        "message_id": normalized_message_id,
        "message_kind": message_kind,
        "status": "pending",
        "physical_status": "unknown",
        "completion_status": "pending",
        "failure_stage": None,
        "sent_any": False,
        "all_succeeded": False,
        "attempted_count": 0,
        "failed_count": 0,
    }
    if isinstance(delivery_identity, Mapping):
        receipt["delivery_identity"] = dict(delivery_identity)
    state.output_delivery_receipts.append(receipt)
    return deepcopy(receipt)


def finish_interaction_turn_delivery_receipt(
    event,
    *,
    message_id: str,
    physical_result: MessageChainDeliveryResult | None,
    completion_status: InteractionDeliveryCompletionStatus,
    failure_stage: InteractionDeliveryFailureStage | None = None,
) -> dict[str, Any]:
    normalized_message_id = str(message_id or "").strip()
    if not normalized_message_id:
        raise ValueError("Delivery receipt requires a non-empty message_id")
    if completion_status not in _DELIVERY_COMPLETION_TERMINAL_STATUSES:
        raise ValueError(
            f"Invalid delivery completion status: {completion_status}"
        )
    if failure_stage is not None and failure_stage not in _DELIVERY_FAILURE_STAGES:
        raise ValueError(f"Invalid delivery failure stage: {failure_stage}")

    state = ensure_interaction_turn_state(event)
    receipt = next(
        (
            item
            for item in state.output_delivery_receipts
            if item.get("message_id") == normalized_message_id
        ),
        None,
    )
    if receipt is None:
        raise ValueError(f"Delivery receipt does not exist: {normalized_message_id}")

    if physical_result is None:
        physical_status = "unknown"
        sent_any = False
        all_succeeded = False
        attempted_count = 0
        failed_count = 0
    else:
        sent_any = bool(physical_result.sent_any)
        all_succeeded = bool(physical_result.all_succeeded)
        attempted_count = int(physical_result.attempted_count)
        failed_count = int(physical_result.failed_count)
        physical_status = (
            "delivered"
            if all_succeeded
            else "partial"
            if sent_any
            else "failed"
        )

    if (
        physical_status != "delivered"
        and completion_status != "not_attempted"
    ):
        raise ValueError(
            "Message completion cannot finish before full physical delivery"
        )

    if physical_status == "unknown":
        status = "unknown"
    elif physical_status == "failed":
        status = "failed"
    elif physical_status == "partial":
        status = "partial"
    elif completion_status in {"completed", "not_required"}:
        status = "delivered"
    elif completion_status == "failed":
        status = "failed"
    elif completion_status == "unknown":
        status = "unknown"
    else:
        raise ValueError(
            "A complete physical delivery requires a terminal message completion"
        )

    if physical_status in {"failed", "partial", "unknown"}:
        expected_failure_stage = "physical_send"
    elif completion_status in {"failed", "unknown"}:
        expected_failure_stage = "message_completion"
    else:
        expected_failure_stage = None
    if failure_stage != expected_failure_stage:
        raise ValueError(
            "Delivery failure stage does not match the terminal delivery state"
        )

    completed = {
        **receipt,
        "status": status,
        "physical_status": physical_status,
        "completion_status": completion_status,
        "failure_stage": failure_stage,
        "sent_any": sent_any,
        "all_succeeded": all_succeeded,
        "attempted_count": attempted_count,
        "failed_count": failed_count,
    }
    if receipt.get("status") != "pending":
        if receipt != completed:
            raise ValueError(
                f"Delivery receipt already completed: {normalized_message_id}"
            )
        return deepcopy(receipt)

    receipt.clear()
    receipt.update(completed)
    return deepcopy(receipt)


def get_interaction_turn_delivery_receipts(event) -> list[dict[str, Any]]:
    state = get_interaction_turn_state(event)
    return (
        deepcopy(state.output_delivery_receipts)
        if state is not None
        else []
    )


def get_interaction_turn_visible_outputs(event) -> list[dict[str, Any]]:
    state = get_interaction_turn_state(event)
    if state is None:
        return []
    return [dict(output) for output in state.visible_outputs]


def append_interaction_turn_assistant_artifacts(
    event,
    artifacts: list[dict[str, Any]],
) -> None:
    state = ensure_interaction_turn_state(event)
    state.assistant_artifacts.extend(
        dict(artifact) for artifact in artifacts if isinstance(artifact, dict)
    )


def get_interaction_turn_assistant_artifacts(event) -> list[dict[str, Any]]:
    state = get_interaction_turn_state(event)
    if state is None:
        return []
    return [dict(artifact) for artifact in state.assistant_artifacts]


def record_interaction_turn_visible_message_fingerprint(
    event,
    fingerprint: str | None,
) -> None:
    normalized = str(fingerprint or "").strip()
    if normalized:
        ensure_interaction_turn_state(event).visible_message_fingerprints.add(
            normalized
        )


def get_interaction_turn_visible_message_fingerprints(event) -> set[str]:
    state = get_interaction_turn_state(event)
    if state is None:
        return set()
    return set(state.visible_message_fingerprints)


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
    state = get_interaction_turn_state(event)
    if state is None:
        return []
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
            state.execution_scope.cancel_and_detach("speculative_persona")
        return True


async def suppress_interaction_turn_pending_persona(
    event,
    persona_task: asyncio.Task[Any] | None = None,
) -> bool:
    """Suppress only Persona work that has not committed visible output."""
    state = ensure_interaction_turn_state(event)
    async with state.lock:
        if (
            state.speculative_persona_status
            is not InteractionSpeculativePersonaStatus.PENDING
        ):
            return False
        state.speculative_persona_status = (
            InteractionSpeculativePersonaStatus.SUPPRESSED
        )
        state.execution_scope.cancel_and_detach(
            "speculative_persona",
            persona_task,
        )
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
        if (
            state.speculative_persona_status
            is not InteractionSpeculativePersonaStatus.PENDING
        ):
            return False
        if (
            state.route_decision is not None
            and state.route_decision.route_mode is InteractionRouteMode.SILENT
        ):
            state.speculative_persona_status = (
                InteractionSpeculativePersonaStatus.SUPPRESSED
            )
            return False
        if state.final_output_status is not InteractionFinalOutputStatus.PENDING:
            state.speculative_persona_status = (
                InteractionSpeculativePersonaStatus.SUPPRESSED
            )
            return False
        state.speculative_persona_status = InteractionSpeculativePersonaStatus.COMMITTED
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
