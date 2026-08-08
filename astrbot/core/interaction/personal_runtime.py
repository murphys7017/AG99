from __future__ import annotations

import asyncio
import contextvars
import hashlib
import time
import weakref
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager, contextmanager, nullcontext
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Protocol

from astrbot import logger
from astrbot.core.deadline import TurnDeadlineBudget, TurnDeadlineExceeded
from astrbot.core.persona_error_reply import (
    resolve_conversation_persona_id,
    resolve_event_conversation_persona_id,
)
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import supports_personal_runtime
from astrbot.core.provider.entities import ProviderRequest

from .config import load_interaction_agent_config
from .group_reply import (
    GROUP_REPLY_CANDIDATE_KIND_EXTRA,
    is_group_reply_candidate,
)
from .lifecycle import dispatch_interaction_lifecycle
from .observation import RuntimeObservation, RuntimeObservationTarget
from .observation_inbox import (
    ObservationAdmissionResult,
    ObservationAdmissionStatus,
    ObservationBatch,
    ObservationInbox,
)
from .personal_action import (
    PersonalActionCoordinator,
    PersonalActionIntent,
)
from .personal_expression_guard import fingerprint_personal_expression
from .personal_gate import (
    DeterministicObservationGate,
    ObservationFeatureBuilder,
    ObservationGateDisposition,
    ObservationGateReason,
    ObservationGateResult,
    ObservationGateSettings,
)
from .personal_policy import PersonalPolicyAgent, PersonalPolicyEvaluation
from .personal_state import (
    CompletionFeedback,
    PersonalDeliveryStatus,
    PersonalPersistentState,
    PersonalState,
    PersonalStateSnapshot,
)
from .personal_state_repository import PersonalStateRepository
from .plugin_execution_runtime import get_active_plugin_branch_event
from .runtime_event import RuntimeObservationEvent
from .turn_context import (
    PersonalTurnContext,
    PlatformTurnContextFactory,
    resolve_privacy_scope,
)
from .turn_state import (
    InteractionFinalOutputStatus,
    InteractionLifecycleStage,
    InteractionTurnStatus,
    mark_interaction_turn_failed,
    record_interaction_turn_failure,
    set_interaction_turn_persona_id,
)
from .types import InteractionAgentConfig

_ACTIVE_PERSONAL_TURN: contextvars.ContextVar[PersonalTurnContext | None] = (
    contextvars.ContextVar("active_personal_turn", default=None)
)

DEFAULT_IDLE_RUNTIME_TTL_SECONDS = 24 * 60 * 60
DEFAULT_MAX_IDLE_RUNTIMES = 1024
DEFAULT_MAX_PENDING_OBSERVATIONS = 64
DEFAULT_OBSERVATION_DEBOUNCE_SECONDS = 1.5
MAX_COALESCED_MATERIAL_FINGERPRINTS = 512


class PendingTurnState(str, Enum):
    RESERVED = "reserved"
    BOUND = "bound"
    QUEUED = "queued"
    ACTIVE = "active"
    SETTLED = "settled"


@dataclass(frozen=True, slots=True)
class PersonalRuntimeKey:
    config_id: str
    persona_id: str
    audience_key: str
    privacy_scope: str


class ObservationWakeScheduler(Protocol):
    """Lifecycle-owned deadline scheduler used by Personal Session Runtimes."""

    def schedule(self, key: PersonalRuntimeKey, due_at: float) -> None: ...

    def cancel(self, key: PersonalRuntimeKey) -> None: ...


def _stable_observation_payload_fingerprint(payload: Mapping[str, Any]) -> str:
    """Fingerprint immutable Sensor payloads without retaining their contents."""

    def normalize(value: Any) -> object:
        if isinstance(value, Mapping):
            return (
                "mapping",
                tuple(
                    (str(key), normalize(item))
                    for key, item in sorted(value.items(), key=lambda item: str(item[0]))
                ),
            )
        if isinstance(value, list | tuple):
            return ("sequence", tuple(normalize(item) for item in value))
        if isinstance(value, set | frozenset):
            return (
                "set",
                tuple(sorted((normalize(item) for item in value), key=repr)),
            )
        if isinstance(value, bytes):
            return ("bytes", value.hex())
        return (type(value).__qualname__, repr(value))

    return hashlib.blake2b(
        repr(normalize(payload)).encode("utf-8"),
        digest_size=16,
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class PersonalSessionRuntimeSnapshot:
    key: PersonalRuntimeKey
    active_turn_id: str | None
    bound_turn_count: int
    created_at: float
    last_access_at: float
    idle_since: float | None
    state: PersonalStateSnapshot
    last_completion_feedback: CompletionFeedback | None
    observation_evaluation_active: bool
    observation_overflow_drop_count: int
    observation_expired_drop_count: int
    next_observation_wake_at: float | None
    last_observation_batch: ObservationBatch | None
    last_observation_gate_result: ObservationGateResult | None
    last_personal_policy_evaluation: PersonalPolicyEvaluation | None


@dataclass(frozen=True, slots=True)
class PersonalRuntimeManagerSnapshot:
    accepting: bool
    session_count: int
    non_idle_session_count: int
    idle_session_count: int
    eviction_count: int
    sessions: tuple[PersonalSessionRuntimeSnapshot, ...]


def _build_completion_feedback(turn: PersonalTurnContext) -> CompletionFeedback:
    turn_state = turn.state
    completion = turn_state.completion_state
    delivered_utterances = [
        utterance
        for utterance in turn_state.utterances
        if utterance.visible and utterance.delivered_message_ids
    ]
    delivered = bool(delivered_utterances) or any(
        isinstance(output, dict) and output.get("delivered_message_ids")
        for output in turn_state.visible_outputs
    )
    delivered_at = (
        max(utterance.created_at for utterance in delivered_utterances)
        if delivered_utterances
        else None
    )
    latest_delivered_utterance = (
        max(delivered_utterances, key=lambda utterance: utterance.created_at)
        if delivered_utterances
        else None
    )
    visible_reply_fingerprint = fingerprint_personal_expression(
        latest_delivered_utterance.text
        if latest_delivered_utterance is not None
        else ""
    )
    if visible_reply_fingerprint is None:
        for output in reversed(turn_state.visible_outputs):
            if not isinstance(output, dict) or not output.get(
                "delivered_message_ids"
            ):
                continue
            visible_reply_fingerprint = fingerprint_personal_expression(
                output.get("text")
            )
            if visible_reply_fingerprint is not None:
                break

    failure_code = completion.failure_reason
    if failure_code is None and turn_state.failures:
        failure = turn_state.failures[-1]
        failure_code = f"{failure.stage}:{failure.reason}"

    if delivered:
        delivery_status = PersonalDeliveryStatus.DELIVERED
    elif completion.status is InteractionTurnStatus.CANCELLED:
        delivery_status = PersonalDeliveryStatus.CANCELLED
        failure_code = failure_code or "turn_cancelled"
    elif turn_state.final_output_status is InteractionFinalOutputStatus.SUPPRESSED:
        delivery_status = PersonalDeliveryStatus.SUPPRESSED
    elif (
        completion.status is InteractionTurnStatus.FAILED
        or turn_state.final_output_status is InteractionFinalOutputStatus.FAILED
        or failure_code is not None
    ):
        delivery_status = PersonalDeliveryStatus.FAILED
        failure_code = failure_code or "output_failed"
    else:
        delivery_status = PersonalDeliveryStatus.NOT_ATTEMPTED

    return CompletionFeedback(
        action_id=_resolve_personal_action_id(turn),
        turn_id=turn.turn_id,
        delivery_status=delivery_status,
        output_completed_at=delivered_at or completion.terminal_at,
        failure_code=failure_code,
        visible_reply_fingerprint=visible_reply_fingerprint,
    )


def _resolve_personal_action_id(turn: PersonalTurnContext) -> str | None:
    action_id = str(turn.event.get_extra("_personal_action_id", "") or "").strip()
    return action_id or None


@dataclass(slots=True)
class PendingTurnReservation:
    turn: PersonalTurnContext
    state: PendingTurnState = PendingTurnState.RESERVED
    runtime_key: PersonalRuntimeKey | None = None

    def transition(self, state: PendingTurnState) -> None:
        if self.state is PendingTurnState.SETTLED:
            return
        self.state = state
        self.turn.state.runtime_reservation_state = state.value


@dataclass(slots=True)
class _FollowUpCapture:
    runner: Any
    ticket: Any
    order_seq: int
    monitor_task: asyncio.Task[None]


class _FollowUpCoordinator:
    def __init__(self) -> None:
        self.active_runner: Any | None = None
        self.condition = asyncio.Condition()
        self.statuses: dict[int, str] = {}
        self.next_order = 0
        self.next_turn = 0

    def register(self, runner: Any) -> None:
        self.active_runner = runner

    def unregister(self, runner: Any) -> None:
        if self.active_runner is runner:
            self.active_runner = None

    def try_capture(self, event: Any) -> _FollowUpCapture | None:
        sender_id = event.get_sender_id()
        runner = self.active_runner
        if not sender_id or runner is None:
            return None
        runner_event = getattr(
            getattr(runner.run_context, "context", None),
            "event",
            None,
        )
        if runner_event is None or runner_event.get_sender_id() != sender_id:
            return None
        if runner_event.get_extra("agent_stop_requested"):
            return None

        message_text = (event.get_message_str() or "").strip()
        if not message_text:
            message_text = event.get_message_outline().strip()
        ticket = runner.follow_up(message_text=message_text)
        if ticket is None:
            return None

        order_seq = self.next_order
        self.next_order += 1
        self.statuses[order_seq] = "pending"
        monitor_task = asyncio.create_task(
            self._monitor_ticket(ticket, order_seq),
            name=f"personal_runtime_follow_up_{order_seq}",
        )
        return _FollowUpCapture(
            runner=runner,
            ticket=ticket,
            order_seq=order_seq,
            monitor_task=monitor_task,
        )

    async def prepare(self, capture: _FollowUpCapture) -> tuple[bool, bool]:
        await capture.ticket.resolved.wait()
        if capture.ticket.consumed:
            await self._mark_consumed(capture.order_seq)
            return True, False
        await self._activate_in_order(capture.order_seq)
        return False, True

    async def finalize(
        self,
        capture: _FollowUpCapture,
        *,
        activated: bool,
        consumed_marked: bool,
    ) -> None:
        if not activated and not consumed_marked:
            cancel_follow_up = getattr(capture.runner, "cancel_follow_up", None)
            if callable(cancel_follow_up):
                try:
                    cancel_follow_up(capture.ticket)
                except Exception:
                    logger.warning(
                        "Failed to withdraw unresolved Personal Runtime follow-up: "
                        "order_seq=%s",
                        capture.order_seq,
                        exc_info=True,
                    )
        if not capture.monitor_task.done():
            capture.monitor_task.cancel()
            try:
                await capture.monitor_task
            except asyncio.CancelledError:
                pass
        if activated:
            await self._finish(capture.order_seq)
        elif not consumed_marked:
            await self._mark_consumed(capture.order_seq)

    def is_idle(self) -> bool:
        return self.active_runner is None and not self.statuses

    async def _monitor_ticket(self, ticket: Any, order_seq: int) -> None:
        await ticket.resolved.wait()
        if ticket.consumed:
            await self._mark_consumed(order_seq)

    def _advance(self) -> None:
        while self.statuses.get(self.next_turn) in {"consumed", "finished"}:
            self.statuses.pop(self.next_turn, None)
            self.next_turn += 1

    async def _mark_consumed(self, order_seq: int) -> None:
        async with self.condition:
            if order_seq in self.statuses and self.statuses[order_seq] != "finished":
                self.statuses[order_seq] = "consumed"
            self._advance()
            self.condition.notify_all()

    async def _activate_in_order(self, order_seq: int) -> None:
        async with self.condition:
            if order_seq in self.statuses:
                self.statuses[order_seq] = "active"
            while self.next_turn != order_seq:
                await self.condition.wait()

    async def _finish(self, order_seq: int) -> None:
        async with self.condition:
            if order_seq in self.statuses:
                self.statuses[order_seq] = "finished"
            self._advance()
            self.condition.notify_all()


@dataclass(slots=True)
class TurnAdmission:
    turn: PersonalTurnContext
    consumed_as_follow_up: bool
    lease: PersonalTurnLease | None = None
    skipped_busy: bool = False


class PlatformEventSubmission:
    """Manager-owned lifecycle boundary for one official platform event."""

    def __init__(
        self,
        manager: PersonalRuntimeManager,
        reservation: PendingTurnReservation,
    ) -> None:
        self._manager = manager
        self._reservation = reservation
        self._admitted = False

    @property
    def turn(self) -> PersonalTurnContext:
        return self._reservation.turn

    def set_provider_request(self, request: ProviderRequest) -> None:
        self._reservation.turn.provider_request = request

    async def admit(
        self,
        *,
        allow_follow_up: bool,
        wait_if_busy: bool = True,
    ) -> TurnAdmission:
        if self._admitted:
            raise RuntimeError("Platform event has already been admitted.")
        self._admitted = True
        return await self._manager._bind_and_admit(
            self._reservation,
            allow_follow_up=allow_follow_up,
            wait_if_busy=wait_if_busy,
        )


class RuntimeObservationEventSubmission(PlatformEventSubmission):
    """Manager-owned lifecycle boundary for one runtime observation event."""

    async def admit(self) -> TurnAdmission:
        return await super().admit(allow_follow_up=False)


class _TurnAdmissionGate:
    """Give normal platform turns priority over delayed background turns."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active = False
        self._normal_waiters = 0

    async def acquire(self, *, delayed: bool = False) -> None:
        async with self._condition:
            if delayed:
                await self._condition.wait_for(
                    lambda: not self._active and self._normal_waiters == 0
                )
                self._active = True
                return

            self._normal_waiters += 1
            try:
                await self._condition.wait_for(lambda: not self._active)
                self._active = True
            finally:
                self._normal_waiters -= 1
                self._condition.notify_all()

    async def try_acquire(self) -> bool:
        """Acquire immediately without overtaking an active or queued turn."""
        async with self._condition:
            if self._active or self._normal_waiters:
                return False
            self._active = True
            return True

    async def release(self) -> None:
        async with self._condition:
            if not self._active:
                raise RuntimeError("Personal Runtime turn admission is not active")
            self._active = False
            self._condition.notify_all()

    def locked(self) -> bool:
        return self._active


class PersonalTurnLease:
    def __init__(
        self,
        runtime: PersonalSessionRuntime,
        reservation: PendingTurnReservation,
        follow_up_capture: _FollowUpCapture | None,
        follow_up_activated: bool,
    ) -> None:
        self.runtime = runtime
        self.reservation = reservation
        self.follow_up_capture = follow_up_capture
        self.follow_up_activated = follow_up_activated
        self.released = False

    async def release(self) -> None:
        if self.released:
            return
        self.released = True
        try:
            if self.follow_up_capture is not None:
                await self.runtime.follow_ups.finalize(
                    self.follow_up_capture,
                    activated=self.follow_up_activated,
                    consumed_marked=False,
                )
        finally:
            try:
                await self.reservation.turn.state.execution_scope.close()
            finally:
                deadline = self.reservation.turn.state.deadline
                try:
                    if deadline is not None and deadline.expired():
                        logger.debug(
                            "Skipping Personal Runtime completion feedback after "
                            "turn deadline: turn_id=%s",
                            self.reservation.turn.turn_id,
                        )
                    else:
                        feedback_context = (
                            deadline.enforce("completion_feedback")
                            if deadline is not None
                            else nullcontext(None)
                        )
                        async with feedback_context:
                            feedback = _build_completion_feedback(
                                self.reservation.turn
                            )
                            await self.runtime.apply_completion_feedback(
                                feedback,
                                turn=self.reservation.turn,
                            )
                except TurnDeadlineExceeded:
                    logger.warning(
                        "Personal Runtime completion feedback reached turn deadline: "
                        "turn_id=%s",
                        self.reservation.turn.turn_id,
                    )
                except Exception:
                    logger.exception(
                        "Personal Runtime completion feedback failed: turn_id=%s",
                        self.reservation.turn.turn_id,
                    )
                finally:
                    self.runtime.active_turn_id = None
                    self.runtime.active_actor_id = None
                    self.runtime._active_turn_context = None
                    self.runtime.touch()
                    self.reservation.transition(PendingTurnState.SETTLED)
                    await self.runtime.turn_lock.release()


class PersonalSessionRuntime:
    def __init__(
        self,
        key: PersonalRuntimeKey,
        *,
        max_pending_observations: int = DEFAULT_MAX_PENDING_OBSERVATIONS,
        observation_debounce_seconds: float = DEFAULT_OBSERVATION_DEBOUNCE_SECONDS,
        observation_gate_settings: ObservationGateSettings | None = None,
        state_repository: PersonalStateRepository | None = None,
        persistent_state: PersonalPersistentState | None = None,
    ) -> None:
        now = time.time()
        self.key = key
        self.turn_lock = _TurnAdmissionGate()
        self.active_turn_id: str | None = None
        self.active_actor_id: str | None = None
        self._active_turn_context: PersonalTurnContext | None = None
        self.conversation_actor_id: str | None = None
        self.conversation_reply_completed_at: float | None = None
        self.bound_turn_count = 0
        self.follow_ups = _FollowUpCoordinator()
        self.state = PersonalState()
        if persistent_state is not None:
            self.state.restore_persistent(persistent_state)
            self.state.mark_idle(now=now)
        self._state_repository = state_repository
        self._state_persistence_lock = asyncio.Lock()
        self._persistent_state_dirty = False
        self.last_completion_feedback: CompletionFeedback | None = None
        self.observation_inbox = ObservationInbox(max_pending=max_pending_observations)
        self._coalesced_material_fingerprints: OrderedDict[
            tuple[str, str, str], str
        ] = OrderedDict()
        self.observation_debounce_seconds = observation_debounce_seconds
        self.observation_gate_settings = (
            observation_gate_settings or ObservationGateSettings()
        )
        self.observation_evaluation_task: asyncio.Task[None] | None = None
        self.last_observation_batch: ObservationBatch | None = None
        self.last_observation_gate_result: ObservationGateResult | None = None
        self.last_personal_policy_evaluation: PersonalPolicyEvaluation | None = None
        self._personal_policy_agent: PersonalPolicyAgent | None = None
        self._personal_action_handler: (
            Callable[
                [PersonalSessionRuntime, PersonalActionIntent],
                Awaitable[Any],
            ]
            | None
        ) = None
        self._plugin_context: Any | None = None
        self._runtime_config: Mapping[str, Any] = {}
        self._interaction_config = InteractionAgentConfig()
        self._observation_batch_due_at: float | None = None
        self._observation_reschedule_requested = False
        self.next_observation_wake_at: float | None = None
        self._observation_wake_scheduler: ObservationWakeScheduler | None = None
        self._closing = False
        self.created_at = now
        self.last_access_at = now
        self.idle_since: float | None = now

    def touch(self, *, now: float | None = None) -> None:
        self.last_access_at = time.time() if now is None else now

    def bind_turn(self, *, now: float) -> None:
        self.bound_turn_count += 1
        self.idle_since = None
        self.touch(now=now)

    def settle_turn(self, *, now: float) -> None:
        self.bound_turn_count = max(0, self.bound_turn_count - 1)
        self.touch(now=now)
        if (
            not self.has_active_conversational_work()
            and self.observation_inbox.pending_count > 0
        ):
            self._clear_observation_wake()
            self._ensure_observation_evaluation_task()
        if self.is_idle():
            self.idle_since = now
            self.state.mark_idle(now=now)

    async def apply_completion_feedback(
        self,
        feedback: CompletionFeedback,
        *,
        turn: PersonalTurnContext,
    ) -> None:
        completed_at = feedback.output_completed_at or time.time()
        usage_day = (
            self.observation_gate_settings.local_datetime(completed_at)
            .date()
            .isoformat()
            if feedback.action_id
            else None
        )
        persistent_state_changed = self.state.apply_completion_feedback(
            feedback,
            reply_cooldown_seconds=(
                self._interaction_config.personal_runtime_reply_cooldown_seconds
            ),
            usage_day=usage_day,
        )
        self._update_group_conversation_continuation(
            turn,
            feedback=feedback,
            completed_at=completed_at,
        )
        self.last_completion_feedback = feedback
        if persistent_state_changed or self._persistent_state_dirty:
            self._persistent_state_dirty = True
            await self._persist_state()

    def classify_group_conversation_continuation(
        self,
        actor_id: str,
        *,
        now: float,
        continuation_seconds: float,
    ) -> str | None:
        normalized_actor_id = str(actor_id or "").strip()
        if not normalized_actor_id or continuation_seconds <= 0:
            return None
        if self.active_actor_id is not None:
            return "active" if self.active_actor_id == normalized_actor_id else None
        if self.conversation_actor_id != normalized_actor_id:
            return None
        completed_at = self.conversation_reply_completed_at
        if completed_at is None or now >= completed_at + continuation_seconds:
            self.conversation_actor_id = None
            self.conversation_reply_completed_at = None
            return None
        # A delivered group reply only makes subsequent messages candidates.
        # Router owns the unaddressed admission decision, including silence.
        return "model"

    def _update_group_conversation_continuation(
        self,
        turn: PersonalTurnContext,
        *,
        feedback: CompletionFeedback,
        completed_at: float,
    ) -> None:
        if (
            turn.observation is not None
            or turn.session.message_type is not MessageType.GROUP_MESSAGE
            or turn.actor is None
        ):
            return
        actor_id = str(turn.actor.actor_id or "").strip() or None
        candidate_kind = str(
            turn.event.get_extra(GROUP_REPLY_CANDIDATE_KIND_EXTRA, "") or ""
        ).strip()
        if (
            feedback.delivery_status is PersonalDeliveryStatus.SUPPRESSED
            and candidate_kind == "continuation"
            and actor_id == self.conversation_actor_id
        ):
            logger.debug(
                "Personal Runtime preserved group continuation after silent "
                "candidate: audience=%s actor_id=%s turn_id=%s",
                self.key.audience_key,
                actor_id,
                turn.turn_id,
            )
            return
        if (
            not self._interaction_config.enabled
            or feedback.delivery_status is not PersonalDeliveryStatus.DELIVERED
            or self._interaction_config.personal_runtime_conversation_continuation_seconds
            <= 0
        ):
            self.conversation_actor_id = None
            self.conversation_reply_completed_at = None
            return
        self.conversation_actor_id = actor_id
        self.conversation_reply_completed_at = completed_at

    def configure_personal_policy(
        self,
        *,
        agent: PersonalPolicyAgent,
        plugin_context: Any,
        runtime_config: Mapping[str, Any],
        interaction_config: InteractionAgentConfig,
        gate_settings: ObservationGateSettings,
        action_handler: Callable[
            [PersonalSessionRuntime, PersonalActionIntent],
            Awaitable[Any],
        ]
        | None,
    ) -> None:
        self._personal_policy_agent = agent
        self._personal_action_handler = action_handler
        self._plugin_context = plugin_context
        self._runtime_config = dict(runtime_config)
        self._interaction_config = interaction_config
        self.observation_gate_settings = gate_settings

    def bind_observation_wake_scheduler(
        self,
        scheduler: ObservationWakeScheduler | None,
    ) -> None:
        if scheduler is self._observation_wake_scheduler:
            return
        self._observation_wake_scheduler = scheduler
        if self.next_observation_wake_at is not None and scheduler is not None:
            scheduler.schedule(self.key, self.next_observation_wake_at)

    def submit_observation(
        self,
        observation: RuntimeObservation,
        *,
        now: float,
    ) -> ObservationAdmissionResult:
        if (
            observation.kind == "heartbeat"
            and self.observation_inbox.pending_material_count == 0
        ):
            return ObservationAdmissionResult(
                status=ObservationAdmissionStatus.IGNORED,
                observation_id=observation.observation_id,
                runtime_key=self.key,
                pending_count=self.observation_inbox.pending_count,
                reason_codes=("heartbeat_without_material",),
            )

        material_revision = self._material_revision_for_observation(
            observation,
            now=now,
        )
        result = self.observation_inbox.admit(
            observation,
            runtime_key=self.key,
            now=now,
            material_revision=material_revision,
        )
        self._settle_discarded_observation_material()
        self.state.set_pending_observation_count(self.observation_inbox.pending_count)
        if not result.admitted:
            return result

        self._remember_observation_material(observation)
        self.idle_since = None
        self.touch(now=now)
        self.state.record_observation(
            occurred_at=observation.occurred_at,
            pending_count=self.observation_inbox.pending_count,
        )
        if observation.kind == "heartbeat":
            if (
                self.observation_inbox.pending_material_count > 0
                and (
                    self.next_observation_wake_at is None
                    or self.next_observation_wake_at <= now
                )
            ):
                self._clear_observation_wake()
                task_created = self._ensure_observation_evaluation_task(
                    observation.observation_id,
                    delay_seconds=0.0,
                )
                return replace(result, evaluation_task_created=task_created)
            return result

        self._clear_observation_wake()
        task_created = self._ensure_observation_evaluation_task(
            observation.observation_id
        )
        return replace(result, evaluation_task_created=task_created)

    async def submit_idle_initiation(
        self,
        target_session: RuntimeObservationTarget,
        *,
        occurred_at: float,
        minimum_idle_seconds: float,
    ) -> ObservationAdmissionResult:
        """Submit one configured idle fact for the current user-activity epoch."""
        user_activity_at = self.state.last_user_activity_at
        if user_activity_at is None:
            return self._ignored_idle_initiation("idle_initiation_no_user_activity")
        if occurred_at < user_activity_at + minimum_idle_seconds:
            return self._ignored_idle_initiation("idle_initiation_not_due")
        if (
            self.state.last_idle_initiation_activity_at is not None
            and self.state.last_idle_initiation_activity_at >= user_activity_at
        ):
            return self._ignored_idle_initiation("idle_initiation_already_submitted")

        previous_idle_initiation_activity_at = (
            self.state.last_idle_initiation_activity_at
        )
        self.state.claim_idle_initiation(user_activity_at=user_activity_at)
        self._persistent_state_dirty = True
        try:
            await self._persist_state()
        except Exception:
            self.state.last_idle_initiation_activity_at = (
                previous_idle_initiation_activity_at
            )
            self._persistent_state_dirty = True
            logger.exception(
                "Personal Runtime idle-initiation persistence failed: "
                "config_id=%s persona_id=%s audience=%s",
                self.key.config_id,
                self.key.persona_id,
                self.key.audience_key,
            )
            return self._ignored_idle_initiation(
                "idle_initiation_persistence_failed"
            )

        observation = RuntimeObservation(
            kind="idle_initiation",
            source="personal_runtime.idle_initiation",
            occurred_at=occurred_at,
            target_session=target_session,
            coalesce_key="idle_initiation",
            payload={
                "user_activity_at": user_activity_at,
                "idle_seconds": max(0.0, occurred_at - user_activity_at),
            },
        )
        return self.submit_observation(observation, now=occurred_at)

    def _ignored_idle_initiation(self, reason_code: str) -> ObservationAdmissionResult:
        return ObservationAdmissionResult(
            status=ObservationAdmissionStatus.IGNORED,
            observation_id="",
            runtime_key=self.key,
            pending_count=self.observation_inbox.pending_count,
            reason_codes=(reason_code,),
        )

    def _material_revision_for_observation(
        self,
        observation: RuntimeObservation,
        *,
        now: float,
    ) -> int | None:
        if observation.kind == "heartbeat":
            return None
        if observation.expires_at is not None and observation.expires_at <= now:
            return None
        identity = observation.coalesce_identity
        if identity is None:
            return self.state.record_material_change()
        fingerprint = _stable_observation_payload_fingerprint(observation.payload)
        if self._coalesced_material_fingerprints.get(identity) == fingerprint:
            return None
        return self.state.record_material_change()

    def _remember_observation_material(
        self,
        observation: RuntimeObservation,
    ) -> None:
        identity = observation.coalesce_identity
        if identity is None or observation.kind == "heartbeat":
            return
        self._coalesced_material_fingerprints[identity] = (
            _stable_observation_payload_fingerprint(observation.payload)
        )
        self._coalesced_material_fingerprints.move_to_end(identity)
        while (
            len(self._coalesced_material_fingerprints)
            > MAX_COALESCED_MATERIAL_FINGERPRINTS
        ):
            self._coalesced_material_fingerprints.popitem(last=False)

    def _settle_discarded_observation_material(self) -> None:
        revision = self.observation_inbox.take_discarded_material_revision()
        if revision:
            self.state.settle_material_revision(revision)

    def _settle_observation_batch(self, batch: ObservationBatch) -> int:
        return self.state.settle_material_revision(batch.material_revision)

    def _ensure_observation_evaluation_task(
        self,
        observation_id: str | None = None,
        *,
        delay_seconds: float | None = None,
    ) -> bool:
        if self._closing:
            return False
        task = self.observation_evaluation_task
        if task is not None and not task.done():
            self._observation_reschedule_requested = True
            return False
        self._observation_reschedule_requested = False
        self._observation_batch_due_at = (
            asyncio.get_running_loop().time()
            + (
                self.observation_debounce_seconds
                if delay_seconds is None
                else max(0.0, delay_seconds)
            )
        )
        self.observation_evaluation_task = asyncio.create_task(
            self._evaluate_observations(),
            name=(
                "personal_runtime_observation"
                if observation_id is None
                else f"personal_runtime_observation_{observation_id[:12]}"
            ),
        )
        return True

    def wake_observations(self) -> bool:
        """Re-evaluate retained facts after a lifecycle-owned wake deadline."""
        self.next_observation_wake_at = None
        if self._closing or self.observation_inbox.pending_count == 0:
            return False
        return self._ensure_observation_evaluation_task(delay_seconds=0.0)

    def _schedule_observation_wake_at(self, due_at: float | None) -> None:
        if due_at is None or self._closing:
            return
        normalized_due_at = max(time.time(), float(due_at))
        current_due_at = self.next_observation_wake_at
        if current_due_at is not None and current_due_at <= normalized_due_at:
            return
        self.next_observation_wake_at = normalized_due_at
        if self._observation_wake_scheduler is not None:
            self._observation_wake_scheduler.schedule(self.key, normalized_due_at)

    def _clear_observation_wake(self) -> None:
        if self.next_observation_wake_at is None:
            return
        self.next_observation_wake_at = None
        if self._observation_wake_scheduler is not None:
            self._observation_wake_scheduler.cancel(self.key)

    async def _evaluate_observations(self) -> None:
        current_task = asyncio.current_task()
        gate_result: ObservationGateResult | None = None
        wake_at: float | None = None
        try:
            loop = asyncio.get_running_loop()
            due_at = self._observation_batch_due_at
            if due_at is None:
                return
            await asyncio.sleep(max(0.0, due_at - loop.time()))
            closed_at = time.time()
            batch = self.observation_inbox.drain(
                runtime_key=self.key,
                closed_at=closed_at,
            )
            self._settle_discarded_observation_material()
            self.state.set_pending_observation_count(
                self.observation_inbox.pending_count
            )
            if batch is not None:
                self.last_observation_batch = batch
                state_snapshot = self.state.snapshot()
                features = ObservationFeatureBuilder.build(
                    batch,
                    state=state_snapshot,
                    runtime_busy=self.has_active_conversational_work(),
                    settings=self.observation_gate_settings,
                    evaluated_at=closed_at,
                )
                gate_result = DeterministicObservationGate.evaluate(
                    batch,
                    state=state_snapshot,
                    features=features,
                    settings=self.observation_gate_settings,
                    evaluated_at=closed_at,
                )
                self.last_observation_gate_result = gate_result
                self.state.record_gate_result(gate_result.reason_code.value)
                if gate_result.disposition is ObservationGateDisposition.HOLD:
                    self.observation_inbox.restore(
                        batch,
                        hold_reason=gate_result.reason_code.value,
                    )
                    self._settle_discarded_observation_material()
                    self.state.set_pending_observation_count(
                        self.observation_inbox.pending_count
                    )
                    wake_at = self._hold_wake_at(
                        gate_result,
                        state_snapshot=state_snapshot,
                    )
                elif gate_result.disposition is ObservationGateDisposition.EVALUATE:
                    wake_at = await self._evaluate_personal_policy(
                        batch,
                        gate_result=gate_result,
                        state_snapshot=state_snapshot,
                    )
                else:
                    self._settle_observation_batch(batch)
            self.touch(now=closed_at)
        finally:
            reschedule_requested = self._observation_reschedule_requested
            self._observation_reschedule_requested = False
            if self.observation_evaluation_task is current_task:
                self.observation_evaluation_task = None
            self._observation_batch_due_at = None
            should_reschedule = (
                not self._closing
                and reschedule_requested
                and self.observation_inbox.pending_count > 0
                and (
                    gate_result is None
                    or gate_result.disposition is not ObservationGateDisposition.HOLD
                    or (
                        gate_result.reason_code is ObservationGateReason.RUNTIME_BUSY
                        and not self.has_active_conversational_work()
                    )
                )
            )
            if wake_at is not None and self.observation_inbox.pending_count > 0:
                self._schedule_observation_wake_at(wake_at)
            elif should_reschedule:
                self._ensure_observation_evaluation_task()
            now = time.time()
            if self.is_idle():
                self.idle_since = now
                self.state.mark_idle(now=now)

    async def _evaluate_personal_policy(
        self,
        batch: ObservationBatch,
        *,
        gate_result: ObservationGateResult,
        state_snapshot: PersonalStateSnapshot,
    ) -> float | None:
        agent = self._personal_policy_agent
        plugin_context = self._plugin_context
        if agent is None or plugin_context is None:
            self._settle_observation_batch(batch)
            return None

        async def record_provider_call() -> None:
            usage_day = self.observation_gate_settings.local_datetime(
                time.time()
            ).date().isoformat()
            self.state.record_policy_call(usage_day=usage_day)
            self._persistent_state_dirty = True
            await self._persist_state()

        try:
            evaluation = await agent.evaluate(
                runtime_key=self.key,
                batch=batch,
                gate_result=gate_result,
                state=state_snapshot,
                gate_settings=self.observation_gate_settings,
                plugin_context=plugin_context,
                runtime_config=self._runtime_config,
                interaction_config=self._interaction_config,
                on_provider_call_started=record_provider_call,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._settle_observation_batch(batch)
            logger.exception(
                "Personal Policy evaluation failed: config_id=%s persona_id=%s "
                "batch_id=%s",
                self.key.config_id,
                self.key.persona_id,
                batch.batch_id,
            )
            return None
        if evaluation is None:
            self._settle_observation_batch(batch)
            return None
        self.last_personal_policy_evaluation = evaluation
        self.state.record_policy_action(evaluation.decision.action.value)
        logger.info(
            "Personal Policy evaluation: config_id=%s persona_id=%s "
            "batch_id=%s status=%s action=%s reason=%s failure=%s "
            "provider_call_started=%s selected_slots=%s",
            self.key.config_id,
            self.key.persona_id,
            evaluation.batch_id,
            evaluation.status.value,
            evaluation.decision.action.value,
            evaluation.decision.reason_code.value,
            evaluation.failure_code or "",
            evaluation.provider_call_started,
            ",".join(evaluation.selected_slot_names),
        )
        plan = PersonalActionCoordinator.plan(
            decision=evaluation.decision,
            batch=batch,
            evaluated_at=evaluation.evaluated_at,
            minimum_defer_seconds=(
                self._interaction_config.personal_runtime_no_action_cooldown_seconds
            ),
        )
        if plan.defer_until is not None:
            self.observation_inbox.restore(batch, hold_reason="defer")
            self._settle_discarded_observation_material()
            self.state.set_pending_observation_count(self.observation_inbox.pending_count)
            if self.state.defer_actions_until(plan.defer_until):
                self._persistent_state_dirty = True
                try:
                    await self._persist_state()
                except Exception:
                    logger.exception(
                        "Personal Policy defer persistence failed: "
                        "config_id=%s persona_id=%s batch_id=%s",
                        self.key.config_id,
                        self.key.persona_id,
                        batch.batch_id,
                    )
            logger.info(
                "Personal Policy deferred action: config_id=%s persona_id=%s "
                "batch_id=%s not_before=%s",
                self.key.config_id,
                self.key.persona_id,
                batch.batch_id,
                plan.defer_until,
            )
            return plan.defer_until
        intent = plan.intent
        if intent is None:
            self._settle_observation_batch(batch)
            return None
        settled_revision = self._settle_observation_batch(batch)
        logger.info(
            "Personal Policy expression dispatch: config_id=%s persona_id=%s "
            "batch_id=%s action_id=%s settled_material_revision=%s",
            self.key.config_id,
            self.key.persona_id,
            batch.batch_id,
            intent.action_id,
            settled_revision,
        )
        handler = self._personal_action_handler
        if handler is None:
            logger.warning(
                "Personal Policy action skipped; no action handler is bound: "
                "config_id=%s persona_id=%s batch_id=%s",
                self.key.config_id,
                self.key.persona_id,
                batch.batch_id,
            )
            return None
        try:
            await handler(self, intent)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Personal Policy action failed: config_id=%s persona_id=%s "
                "batch_id=%s action_id=%s",
                self.key.config_id,
                self.key.persona_id,
                batch.batch_id,
                intent.action_id,
            )
        return None

    def _hold_wake_at(
        self,
        gate_result: ObservationGateResult,
        *,
        state_snapshot: PersonalStateSnapshot,
    ) -> float | None:
        reason = gate_result.reason_code
        if reason is ObservationGateReason.QUIET_HOURS:
            return self.observation_gate_settings.quiet_hours_end_at(
                gate_result.evaluated_at
            )
        if reason is ObservationGateReason.REPLY_COOLDOWN:
            return state_snapshot.reply_cooldown_until
        if reason is ObservationGateReason.NO_ACTION_COOLDOWN:
            return state_snapshot.no_action_cooldown_until
        return None

    async def close(self) -> None:
        self._closing = True
        task = self.observation_evaluation_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.observation_evaluation_task = None
        self._observation_batch_due_at = None
        self._observation_reschedule_requested = False
        self._clear_observation_wake()
        self.observation_inbox.clear()
        self.state.set_pending_observation_count(0)
        try:
            await self._persist_state()
        except Exception:
            logger.exception(
                "Personal Runtime final state persistence failed: config_id=%s "
                "persona_id=%s audience=%s",
                self.key.config_id,
                self.key.persona_id,
                self.key.audience_key,
            )

    async def _persist_state(self) -> None:
        if self._state_repository is None:
            self._persistent_state_dirty = False
            return
        async with self._state_persistence_lock:
            while self._persistent_state_dirty:
                snapshot = self.state.persistent_snapshot()
                self._persistent_state_dirty = False
                try:
                    await self._state_repository.save(self.key, snapshot)
                except Exception:
                    self._persistent_state_dirty = True
                    raise

    def snapshot(self) -> PersonalSessionRuntimeSnapshot:
        return PersonalSessionRuntimeSnapshot(
            key=self.key,
            active_turn_id=self.active_turn_id,
            bound_turn_count=self.bound_turn_count,
            created_at=self.created_at,
            last_access_at=self.last_access_at,
            idle_since=self.idle_since,
            state=self.state.snapshot(),
            last_completion_feedback=self.last_completion_feedback,
            observation_evaluation_active=(
                self.observation_evaluation_task is not None
                and not self.observation_evaluation_task.done()
            ),
            observation_overflow_drop_count=(
                self.observation_inbox.overflow_drop_count
            ),
            observation_expired_drop_count=self.observation_inbox.expired_drop_count,
            next_observation_wake_at=self.next_observation_wake_at,
            last_observation_batch=self.last_observation_batch,
            last_observation_gate_result=self.last_observation_gate_result,
            last_personal_policy_evaluation=self.last_personal_policy_evaluation,
        )

    async def admit(
        self,
        reservation: PendingTurnReservation,
        *,
        allow_follow_up: bool,
        wait_if_busy: bool,
    ) -> TurnAdmission:
        turn = reservation.turn
        event = turn.event
        delayed_admission = bool(
            event.get_extra("_personal_runtime_delayed_admission", False)
        )
        capture = self.follow_ups.try_capture(event) if allow_follow_up else None
        deadline = turn.state.deadline
        queue_context = (
            deadline.enforce("session_queue")
            if deadline is not None
            else nullcontext(None)
        )
        follow_up_activated = False
        lock_acquired = False
        try:
            async with queue_context:
                if capture is not None:
                    consumed, follow_up_activated = await self.follow_ups.prepare(
                        capture
                    )
                    if consumed:
                        await self.follow_ups.finalize(
                            capture,
                            activated=False,
                            consumed_marked=True,
                        )
                        reservation.transition(PendingTurnState.SETTLED)
                        return TurnAdmission(
                            turn=turn,
                            consumed_as_follow_up=True,
                        )

                reservation.transition(PendingTurnState.QUEUED)
                if wait_if_busy:
                    await self.turn_lock.acquire(delayed=delayed_admission)
                    lock_acquired = True
                else:
                    lock_acquired = await self.turn_lock.try_acquire()
                    if not lock_acquired:
                        return TurnAdmission(
                            turn=turn,
                            consumed_as_follow_up=False,
                            skipped_busy=True,
                        )

            reservation.transition(PendingTurnState.ACTIVE)
            if turn.state.deadline is None:
                turn.state.deadline = TurnDeadlineBudget.start(
                    self._interaction_config.turn_timeout
                )
            turn.previous_expression_fingerprint = (
                self.state.last_expression_fingerprint
            )
            self.active_turn_id = turn.turn_id
            self.active_actor_id = (
                str(turn.actor.actor_id or "").strip() or None
                if turn.actor is not None
                else None
            )
            self._active_turn_context = turn
            user_activity_at = (
                None
                if is_group_reply_candidate(event)
                else self._turn_user_activity_at(turn)
            )
            self.touch()
            if self.state.mark_turn_active(user_activity_at=user_activity_at):
                self._persistent_state_dirty = True
            admission = TurnAdmission(
                turn=turn,
                consumed_as_follow_up=False,
                lease=PersonalTurnLease(
                    self,
                    reservation,
                    capture,
                    follow_up_activated,
                ),
            )
            lock_acquired = False
            return admission
        except BaseException:
            if lock_acquired:
                self.active_turn_id = None
                self.active_actor_id = None
                self._active_turn_context = None
                await self.turn_lock.release()
            if capture is not None:
                await self.follow_ups.finalize(
                    capture,
                    activated=follow_up_activated,
                    consumed_marked=False,
                )
            raise

    @staticmethod
    def _turn_user_activity_at(turn: PersonalTurnContext) -> float | None:
        if (
            turn.input is None
            or turn.actor is None
            or not (
                turn.input.text.strip()
                or turn.input.outline.strip()
                or turn.input.components
            )
        ):
            return None
        self_id = str(turn.event.get_self_id() or "").strip()
        if self_id and turn.actor.actor_id == self_id:
            return None
        return turn.input.created_at

    def is_idle(self) -> bool:
        return (
            not self.has_active_conversational_work()
            and self.observation_inbox.pending_count == 0
            and not self._persistent_state_dirty
            and (
                self.observation_evaluation_task is None
                or self.observation_evaluation_task.done()
            )
        )

    def has_active_conversational_work(self) -> bool:
        return (
            self.turn_lock.locked()
            or self.active_turn_id is not None
            or self.bound_turn_count > 0
            or not self.follow_ups.is_idle()
        )


class PersonalRuntimeManager:
    def __init__(
        self,
        *,
        idle_runtime_ttl_seconds: float = DEFAULT_IDLE_RUNTIME_TTL_SECONDS,
        max_idle_runtimes: int = DEFAULT_MAX_IDLE_RUNTIMES,
        max_pending_observations: int = DEFAULT_MAX_PENDING_OBSERVATIONS,
        observation_debounce_seconds: float = DEFAULT_OBSERVATION_DEBOUNCE_SECONDS,
        observation_gate_settings: ObservationGateSettings | None = None,
        state_repository: PersonalStateRepository | None = None,
    ) -> None:
        if idle_runtime_ttl_seconds < 0:
            raise ValueError("idle_runtime_ttl_seconds must be non-negative")
        if max_idle_runtimes < 0:
            raise ValueError("max_idle_runtimes must be non-negative")
        if max_pending_observations <= 0:
            raise ValueError("max_pending_observations must be positive")
        if observation_debounce_seconds < 0:
            raise ValueError("observation_debounce_seconds must be non-negative")
        self._idle_runtime_ttl_seconds = float(idle_runtime_ttl_seconds)
        self._max_idle_runtimes = int(max_idle_runtimes)
        self._max_pending_observations = int(max_pending_observations)
        self._observation_debounce_seconds = float(observation_debounce_seconds)
        self._observation_gate_settings = (
            observation_gate_settings or ObservationGateSettings()
        )
        self._sessions: dict[PersonalRuntimeKey, PersonalSessionRuntime] = {}
        self._event_sessions: weakref.WeakKeyDictionary[Any, PersonalSessionRuntime] = (
            weakref.WeakKeyDictionary()
        )
        self._plugin_context: Any | None = None
        self._personal_policy_agent = PersonalPolicyAgent()
        self._personal_expression_handler: (
            Callable[[RuntimeObservationEvent, PersonalTurnContext], Awaitable[Any]]
            | None
        ) = None
        self._observation_wake_scheduler: ObservationWakeScheduler | None = None
        self._state_repository = state_repository
        self._runtime_creation_lock = asyncio.Lock()
        self._accepting = True
        self._eviction_count = 0

    def bind_plugin_context(self, plugin_context: Any) -> None:
        self._plugin_context = plugin_context

    def bind_personal_expression_handler(
        self,
        handler: Callable[
            [RuntimeObservationEvent, PersonalTurnContext], Awaitable[Any]
        ]
        | None,
    ) -> None:
        self._personal_expression_handler = handler

    def bind_observation_wake_scheduler(
        self,
        scheduler: ObservationWakeScheduler | None,
    ) -> None:
        self._observation_wake_scheduler = scheduler
        for runtime in self._sessions.values():
            runtime.bind_observation_wake_scheduler(scheduler)

    def classify_group_conversation_continuation(
        self,
        event: Any,
        *,
        config_id: str,
        runtime_config: Mapping[str, Any],
    ) -> str | None:
        interaction_config = load_interaction_agent_config(runtime_config)
        if (
            not self._accepting
            or not interaction_config.enabled
            or interaction_config.personal_runtime_conversation_continuation_seconds
            <= 0
            or event.get_message_type() is not MessageType.GROUP_MESSAGE
            or event.get_extra("action_type") == "live"
        ):
            return None
        actor_id = str(event.get_sender_id() or "").strip()
        self_id = str(event.get_self_id() or "").strip()
        if not actor_id or actor_id == self_id:
            return None
        if not event.get_message_str().strip() and not event.get_messages():
            return None

        normalized_config_id = str(config_id or "default")
        audience_key = event.unified_msg_origin
        now = time.time()
        for key, runtime in self._sessions.items():
            if (
                key.config_id != normalized_config_id
                or key.audience_key != audience_key
                or key.privacy_scope != "group"
            ):
                continue
            continuation = runtime.classify_group_conversation_continuation(
                actor_id,
                now=now,
                continuation_seconds=(
                    interaction_config.personal_runtime_conversation_continuation_seconds
                ),
            )
            if continuation is not None:
                return continuation
        return None

    async def wake_observations(self, key: PersonalRuntimeKey) -> None:
        if not self._accepting:
            return
        runtime = self._sessions.get(key)
        if runtime is None:
            return
        runtime.wake_observations()

    async def submit_observation(
        self,
        observation: RuntimeObservation,
        *,
        config_id: str,
        plugin_context: Any,
        runtime_config: Mapping[str, Any],
    ) -> ObservationAdmissionResult:
        """Admit a system fact without creating a platform or user event."""
        self._ensure_accepting()
        if not isinstance(observation, RuntimeObservation):
            raise TypeError("observation must be a RuntimeObservation")
        key = await self._resolve_observation_runtime_key(
            observation,
            config_id=config_id,
            plugin_context=plugin_context,
            runtime_config=runtime_config,
        )
        now = time.time()
        self._evict_idle_sessions(now=now)
        runtime = await self._get_or_create_runtime(
            key,
            plugin_context=plugin_context,
            runtime_config=runtime_config,
        )
        return runtime.submit_observation(observation, now=now)

    async def submit_idle_initiation(
        self,
        target_session: RuntimeObservationTarget,
        *,
        config_id: str,
        plugin_context: Any,
        runtime_config: Mapping[str, Any],
        occurred_at: float,
        minimum_idle_seconds: float,
    ) -> ObservationAdmissionResult:
        """Submit a bounded idle fact without creating a platform event."""
        self._ensure_accepting()
        key = await self._resolve_observation_target_runtime_key(
            target_session,
            config_id=config_id,
            plugin_context=plugin_context,
            runtime_config=runtime_config,
        )
        self._evict_idle_sessions(now=occurred_at)
        runtime = await self._get_or_create_runtime(
            key,
            plugin_context=plugin_context,
            runtime_config=runtime_config,
        )
        return await runtime.submit_idle_initiation(
            target_session,
            occurred_at=occurred_at,
            minimum_idle_seconds=minimum_idle_seconds,
        )

    @asynccontextmanager
    async def submit_platform_event(
        self,
        event: Any,
        config_id: str,
        plugin_context: Any,
        runtime_config: dict,
    ) -> AsyncIterator[PlatformEventSubmission]:
        self._ensure_accepting()
        reservation = self._reserve(
            event,
            config_id,
            runtime_config=runtime_config,
            plugin_context=plugin_context,
        )
        submission = PlatformEventSubmission(
            self,
            reservation,
        )
        try:
            yield submission
        finally:
            self._settle(reservation)

    async def submit_runtime_observation_event(
        self,
        event: RuntimeObservationEvent,
        config_id: str,
        plugin_context: Any,
        runtime_config: dict,
        handler: Callable[
            [RuntimeObservationEvent, PersonalTurnContext], Awaitable[Any]
        ],
        *,
        bound_runtime: PersonalSessionRuntime | None = None,
    ) -> Any:
        """Submit an internal observation to the regular per-session runtime."""
        self._ensure_accepting()
        if not isinstance(event, RuntimeObservationEvent):
            raise TypeError("event must be a RuntimeObservationEvent")
        if not event.platform_meta.support_proactive_message:
            raise RuntimeError(
                "Runtime observation target does not support proactive messages"
            )
        submission_kind = event.get_extra("_personal_runtime_submission_kind")
        if submission_kind == "personal_expression":
            get_platform = getattr(plugin_context, "get_platform_inst", None)
            platform = (
                get_platform(event.get_platform_id())
                if callable(get_platform)
                else None
            )
            if (
                not event.platform_meta.support_personal_runtime
                or platform is None
                or not supports_personal_runtime(platform.meta())
            ):
                raise RuntimeError(
                    "Runtime observation target does not explicitly support Personal "
                    "Runtime output"
                )
        event.set_extra("_astrbot_config_id", config_id)
        reservation = self._reserve(
            event,
            config_id,
            runtime_config=runtime_config,
            plugin_context=plugin_context,
        )
        submission = RuntimeObservationEventSubmission(self, reservation)
        if event.get_extra("_personal_runtime_submission_kind") is None:
            event.set_extra("_personal_runtime_submission_kind", "observation")
        try:
            try:
                if bound_runtime is None:
                    admission = await submission.admit()
                else:
                    if self._sessions.get(bound_runtime.key) is not bound_runtime:
                        raise RuntimeError("Bound runtime is no longer active")
                    self._bind_to_runtime(reservation, bound_runtime)
                    admission = await self._admit(
                        reservation,
                        allow_follow_up=False,
                    )
            except TurnDeadlineExceeded as exc:
                record_interaction_turn_failure(
                    event,
                    stage=exc.stage,
                    reason=exc.reason,
                    exception=exc,
                    user_visible_action="none",
                )
                mark_interaction_turn_failed(event)
                await dispatch_interaction_lifecycle(
                    event,
                    plugin_context,
                    InteractionLifecycleStage.FAILED,
                    metadata={
                        "source": "runtime_observation_admission",
                        "reason": exc.reason,
                    },
                )
                raise
            if admission.consumed_as_follow_up or admission.lease is None:
                raise RuntimeError(
                    "Runtime observation admission did not acquire a lease"
                )
            try:
                deadline = admission.turn.state.deadline
                if deadline is None:
                    with self.activate_turn(admission.turn):
                        return await handler(event, admission.turn)
                async with deadline.enforce("turn_execution"):
                    with self.activate_turn(admission.turn):
                        return await handler(event, admission.turn)
            finally:
                await admission.lease.release()
        finally:
            self._settle(reservation)

    async def submit_delayed_plugin_event(
        self,
        event: RuntimeObservationEvent,
        config_id: str,
        plugin_context: Any,
        runtime_config: dict,
        handler: Callable[
            [RuntimeObservationEvent, PersonalTurnContext], Awaitable[Any]
        ],
        *,
        profile: str,
    ) -> Any:
        """Submit a low-priority T2 whose deadline starts after admission."""
        if profile not in {
            "delayed_plugin_expression",
            "delayed_plugin_direct",
        }:
            raise ValueError(f"Unsupported delayed plugin profile: {profile}")
        event.set_extra("_personal_runtime_submission_kind", profile)
        event.set_extra("_personal_runtime_delayed_admission", True)
        event.set_extra("_personal_runtime_deadline_after_admission", True)
        return await self.submit_runtime_observation_event(
            event,
            config_id,
            plugin_context,
            runtime_config,
            handler,
        )

    async def dispatch_proactive_message(
        self,
        *,
        context: Any,
        middleware: Any,
        config_id: str,
        runtime_config: dict,
        session: Any,
        message: Any,
        finalize: bool = True,
    ) -> bool:
        plugin_branch_event = get_active_plugin_branch_event()
        if (
            plugin_branch_event is not None
            and plugin_branch_event.unified_msg_origin == str(session)
        ):
            controller = plugin_branch_event.get_extra(
                "_interaction_output_controller"
            )
            capture_plugin_output = getattr(controller, "capture_plugin_output", None)
            if callable(capture_plugin_output):
                await capture_plugin_output(
                    message,
                    plugin_branch_event,
                    mode="direct",
                    finalize=finalize,
                )
                return True

        active_turn = _ACTIVE_PERSONAL_TURN.get()
        if (
            active_turn is not None
            and not active_turn.state.execution_scope.closed
            and active_turn.session.unified_msg_origin == str(session)
        ):
            await middleware.handle_active_turn_output(
                active_turn,
                message,
                finalize=finalize,
            )
            return True

        platform = next(
            (
                item
                for item in context.platform_manager.platform_insts
                if item.meta().id == session.platform_id
            ),
            None,
        )
        if platform is None:
            logger.warning("Cannot find proactive output platform: %s", session)
            return False

        metadata = platform.meta()
        if not metadata.support_proactive_message:
            logger.warning(
                "Cannot send proactive output to unsupported platform: %s",
                session,
            )
            return False
        observation = RuntimeObservation(
            kind="proactive_output",
            source="plugin.context.send_message",
            occurred_at=time.time(),
            target_session=RuntimeObservationTarget(
                platform_id=session.platform_id,
                platform_name=metadata.name,
                message_type=session.message_type,
                session_id=session.session_id,
                support_proactive_message=metadata.support_proactive_message,
                support_personal_runtime=supports_personal_runtime(metadata),
            ),
            payload={"visible_reply_material": message.get_plain_text()},
        )
        event = RuntimeObservationEvent(context=context, observation=observation)
        event.set_extra(
            "_personal_runtime_submission_kind", "explicit_proactive_output"
        )

        async def _deliver(runtime_event, turn):
            await middleware.handle_runtime_output(runtime_event, turn, message)
            return True

        return bool(
            await self.submit_runtime_observation_event(
                event,
                config_id,
                context,
                runtime_config,
                _deliver,
            )
        )

    async def _dispatch_personal_action(
        self,
        runtime: PersonalSessionRuntime,
        intent: PersonalActionIntent,
    ) -> bool:
        handler = self._personal_expression_handler
        if handler is None:
            raise RuntimeError("Personal action handler is not bound")
        if self._sessions.get(runtime.key) is not runtime:
            raise RuntimeError("Personal action runtime is no longer active")
        plugin_context = runtime._plugin_context
        if plugin_context is None:
            raise RuntimeError("Personal action plugin context is unavailable")
        event = RuntimeObservationEvent(
            context=plugin_context,
            observation=intent.to_observation(),
        )
        event.set_extra("_personal_action_intent", intent)
        event.set_extra("_personal_action_id", intent.action_id)
        event.set_extra("_personal_action_batch_id", intent.batch_id)
        event.set_extra("_personal_runtime_submission_kind", "personal_expression")
        return bool(
            await self.submit_runtime_observation_event(
                event,
                runtime.key.config_id,
                plugin_context,
                dict(runtime._runtime_config),
                handler,
                bound_runtime=runtime,
            )
        )

    @staticmethod
    @contextmanager
    def activate_turn(turn: PersonalTurnContext):
        token = _ACTIVE_PERSONAL_TURN.set(turn)
        try:
            yield
        finally:
            _ACTIVE_PERSONAL_TURN.reset(token)

    @contextmanager
    def activate_event_turn(self, event: Any):
        runtime = self._event_sessions.get(event)
        turn = runtime._active_turn_context if runtime is not None else None
        if turn is None or turn.state.execution_scope.closed:
            yield
            return
        with self.activate_turn(turn):
            yield

    def _reserve(
        self,
        event: Any,
        config_id: str,
        *,
        runtime_config: dict,
        plugin_context: Any,
    ) -> PendingTurnReservation:
        self._ensure_accepting()
        turn = PlatformTurnContextFactory.create(
            event,
            config_id=config_id,
            runtime_config=runtime_config,
            plugin_context=plugin_context,
        )
        reservation = PendingTurnReservation(
            turn=turn,
        )
        turn.state.runtime_config_id = turn.session.config_id
        turn.state.runtime_audience_key = turn.session.unified_msg_origin
        turn.state.runtime_privacy_scope = turn.session.privacy_scope
        turn.state.runtime_reservation_state = PendingTurnState.RESERVED.value
        return reservation

    async def _bind(
        self,
        reservation: PendingTurnReservation,
    ) -> PersonalSessionRuntime:
        turn = reservation.turn
        persona_id = await self._resolve_persona_id(
            reservation,
        )
        key = PersonalRuntimeKey(
            config_id=turn.session.config_id,
            persona_id=persona_id,
            audience_key=turn.session.unified_msg_origin,
            privacy_scope=turn.session.privacy_scope,
        )
        self._evict_idle_sessions(now=time.time())
        runtime = await self._get_or_create_runtime(
            key,
            plugin_context=turn.plugin_context,
            runtime_config=turn.runtime_config,
        )
        self._bind_to_runtime(reservation, runtime)
        return runtime

    def _bind_to_runtime(
        self,
        reservation: PendingTurnReservation,
        runtime: PersonalSessionRuntime,
    ) -> None:
        turn = reservation.turn
        event = turn.event
        if (
            turn.session.config_id != runtime.key.config_id
            or turn.session.unified_msg_origin != runtime.key.audience_key
            or turn.session.privacy_scope != runtime.key.privacy_scope
        ):
            raise ValueError("Personal action turn does not match its runtime identity")
        runtime.bind_turn(now=time.time())
        reservation.runtime_key = runtime.key
        reservation.transition(PendingTurnState.BOUND)
        self._event_sessions[event] = runtime
        turn.state.personal_runtime_key = runtime.key
        set_interaction_turn_persona_id(event, runtime.key.persona_id)

    async def _admit(
        self,
        reservation: PendingTurnReservation,
        *,
        allow_follow_up: bool,
        wait_if_busy: bool = True,
    ) -> TurnAdmission:
        event = reservation.turn.event
        runtime = self._event_sessions.get(event)
        if runtime is None:
            raise RuntimeError("Pending turn must be bound before admission.")
        return await runtime.admit(
            reservation,
            allow_follow_up=allow_follow_up,
            wait_if_busy=wait_if_busy,
        )

    async def _bind_and_admit(
        self,
        reservation: PendingTurnReservation,
        *,
        allow_follow_up: bool,
        wait_if_busy: bool = True,
    ) -> TurnAdmission:
        deadline = reservation.turn.state.deadline
        if deadline is None:
            await self._bind(reservation)
        else:
            async with deadline.enforce("runtime_binding"):
                await self._bind(reservation)
        return await self._admit(
            reservation,
            allow_follow_up=allow_follow_up,
            wait_if_busy=wait_if_busy,
        )

    def register_active_runner(self, event: Any, runner: Any) -> bool:
        runtime = self._event_sessions.get(event)
        if runtime is None:
            logger.warning(
                "Cannot register active runner without Personal Runtime binding: session_id=%s",
                event.unified_msg_origin,
            )
            return False
        runtime.follow_ups.register(runner)
        return True

    def unregister_active_runner(self, event: Any, runner: Any) -> None:
        runtime = self._event_sessions.get(event)
        if runtime is not None:
            runtime.follow_ups.unregister(runner)

    @staticmethod
    def _record_deadline_diagnostics(reservation: PendingTurnReservation) -> None:
        turn = reservation.turn
        deadline = turn.state.deadline
        if deadline is None:
            return
        snapshot = deadline.snapshot()
        try:
            turn.event.trace.record("interaction_deadline", **snapshot)
        except Exception:
            logger.debug(
                "Failed to record interaction deadline trace: turn_id=%s",
                turn.turn_id,
                exc_info=True,
            )
        logger.info(
            "DIAG interaction.deadline: turn_id=%s session_id=%s "
            "total_seconds=%.3f elapsed_seconds=%.3f remaining_seconds=%.3f "
            "expired=%s stages=%s",
            turn.turn_id,
            turn.session.session_id,
            snapshot["total_seconds"],
            snapshot["elapsed_seconds"],
            snapshot["remaining_seconds"],
            snapshot["expired"],
            snapshot["stages"],
        )

    def _settle(self, reservation: PendingTurnReservation) -> None:
        event = reservation.turn.event
        self._record_deadline_diagnostics(reservation)
        reservation.transition(PendingTurnState.SETTLED)
        runtime = self._event_sessions.pop(event, None)
        if runtime is None:
            return
        now = time.time()
        runtime.settle_turn(now=now)
        self._evict_idle_sessions(now=now)

    def snapshot_diagnostics(self) -> PersonalRuntimeManagerSnapshot:
        sessions = tuple(
            runtime.snapshot()
            for runtime in sorted(
                self._sessions.values(),
                key=lambda item: (
                    item.key.config_id,
                    item.key.persona_id,
                    item.key.audience_key,
                    item.key.privacy_scope,
                ),
            )
        )
        idle_count = sum(runtime.is_idle() for runtime in self._sessions.values())
        return PersonalRuntimeManagerSnapshot(
            accepting=self._accepting,
            session_count=len(sessions),
            non_idle_session_count=len(sessions) - idle_count,
            idle_session_count=idle_count,
            eviction_count=self._eviction_count,
            sessions=sessions,
        )

    def diagnostics_view(self) -> dict[str, Any]:
        """Return a read-only operational view without Observation payloads."""
        snapshot = self.snapshot_diagnostics()
        return {
            "accepting": snapshot.accepting,
            "session_count": snapshot.session_count,
            "non_idle_session_count": snapshot.non_idle_session_count,
            "idle_session_count": snapshot.idle_session_count,
            "eviction_count": snapshot.eviction_count,
            "sessions": [
                {
                    "runtime_key": {
                        "config_id": item.key.config_id,
                        "persona_id": item.key.persona_id,
                        "audience_key": item.key.audience_key,
                        "privacy_scope": item.key.privacy_scope,
                    },
                    "active_turn_id": item.active_turn_id,
                    "bound_turn_count": item.bound_turn_count,
                    "created_at": item.created_at,
                    "last_access_at": item.last_access_at,
                    "idle_since": item.idle_since,
                    "next_observation_wake_at": item.next_observation_wake_at,
                    "state": {
                        "attention_state": item.state.attention_state.value,
                        "availability_state": item.state.availability_state.value,
                        "last_observation_at": item.state.last_observation_at,
                        "last_user_activity_at": item.state.last_user_activity_at,
                        "last_expression_at": item.state.last_expression_at,
                        "reply_cooldown_until": item.state.reply_cooldown_until,
                        "no_action_cooldown_until": item.state.no_action_cooldown_until,
                        "mute_until": item.state.mute_until,
                        "pending_observation_count": item.state.pending_observation_count,
                        "material_revision": item.state.material_revision,
                        "last_settled_material_revision": (
                            item.state.last_settled_material_revision
                        ),
                        "usage_day": item.state.usage_day,
                        "daily_policy_calls": item.state.daily_policy_calls,
                        "daily_proactive_outputs": item.state.daily_proactive_outputs,
                        "last_gate_reason": item.state.last_gate_reason,
                        "last_policy_action": item.state.last_policy_action,
                    },
                    "observation": {
                        "evaluation_active": item.observation_evaluation_active,
                        "overflow_drop_count": item.observation_overflow_drop_count,
                        "expired_drop_count": item.observation_expired_drop_count,
                        "last_batch": (
                            {
                                "batch_id": item.last_observation_batch.batch_id,
                                "opened_at": item.last_observation_batch.opened_at,
                                "closed_at": item.last_observation_batch.closed_at,
                                "observation_count": len(
                                    item.last_observation_batch.observations
                                ),
                                "material_count": (
                                    item.last_observation_batch.material_count
                                ),
                                "material_revision": (
                                    item.last_observation_batch.material_revision
                                ),
                                "latest_material_occurred_at": (
                                    item.last_observation_batch.latest_material_occurred_at
                                ),
                                "held_duration_seconds": (
                                    item.last_observation_batch.held_duration_seconds
                                ),
                                "release_reason": (
                                    item.last_observation_batch.release_reason
                                ),
                                "source_counts": dict(
                                    item.last_observation_batch.source_counts
                                ),
                            }
                            if item.last_observation_batch is not None
                            else None
                        ),
                        "last_gate": (
                            {
                                "batch_id": item.last_observation_gate_result.batch_id,
                                "disposition": (
                                    item.last_observation_gate_result.disposition.value
                                ),
                                "reason_code": (
                                    item.last_observation_gate_result.reason_code.value
                                ),
                                "evaluated_at": (
                                    item.last_observation_gate_result.evaluated_at
                                ),
                            }
                            if item.last_observation_gate_result is not None
                            else None
                        ),
                    },
                    "policy": (
                        {
                            "batch_id": item.last_personal_policy_evaluation.batch_id,
                            "status": (
                                item.last_personal_policy_evaluation.status.value
                            ),
                            "action": (
                                item.last_personal_policy_evaluation.decision.action.value
                            ),
                            "reason_code": (
                                item.last_personal_policy_evaluation.decision.reason_code.value
                            ),
                            "evaluated_at": (
                                item.last_personal_policy_evaluation.evaluated_at
                            ),
                            "provider_id": (
                                item.last_personal_policy_evaluation.provider_id
                            ),
                            "provider_call_started": (
                                item.last_personal_policy_evaluation.provider_call_started
                            ),
                            "failure_code": (
                                item.last_personal_policy_evaluation.failure_code
                            ),
                        }
                        if item.last_personal_policy_evaluation is not None
                        else None
                    ),
                    "completion": (
                        {
                            "action_id": item.last_completion_feedback.action_id,
                            "turn_id": item.last_completion_feedback.turn_id,
                            "delivery_status": (
                                item.last_completion_feedback.delivery_status.value
                            ),
                            "output_completed_at": (
                                item.last_completion_feedback.output_completed_at
                            ),
                            "failure_code": item.last_completion_feedback.failure_code,
                        }
                        if item.last_completion_feedback is not None
                        else None
                    ),
                }
                for item in snapshot.sessions
            ],
        }

    async def shutdown(self) -> None:
        if not self._accepting:
            return
        self._accepting = False
        active_count = sum(not runtime.is_idle() for runtime in self._sessions.values())
        if active_count:
            logger.warning(
                "Personal Runtime shutdown with active sessions: count=%s",
                active_count,
            )
        await asyncio.gather(
            *(runtime.close() for runtime in tuple(self._sessions.values())),
            return_exceptions=False,
        )
        self._event_sessions.clear()
        self._sessions.clear()

    async def _get_or_create_runtime(
        self,
        key: PersonalRuntimeKey,
        *,
        plugin_context: Any,
        runtime_config: Mapping[str, Any],
    ) -> PersonalSessionRuntime:
        async with self._runtime_creation_lock:
            runtime = self._sessions.get(key)
            if runtime is None:
                persistent_state = None
                if self._state_repository is not None:
                    try:
                        persistent_state = await self._state_repository.load(key)
                    except Exception:
                        logger.exception(
                            "Personal Runtime state restore failed; using process-local state: "
                            "config_id=%s persona_id=%s audience=%s",
                            key.config_id,
                            key.persona_id,
                            key.audience_key,
                        )
                runtime = PersonalSessionRuntime(
                    key,
                    max_pending_observations=self._max_pending_observations,
                    observation_debounce_seconds=self._observation_debounce_seconds,
                    observation_gate_settings=self._observation_gate_settings,
                    state_repository=self._state_repository,
                    persistent_state=persistent_state,
                )
                self._sessions[key] = runtime
        interaction_config = load_interaction_agent_config(runtime_config)
        runtime.configure_personal_policy(
            agent=self._personal_policy_agent,
            plugin_context=self._plugin_context or plugin_context,
            runtime_config=runtime_config,
            interaction_config=interaction_config,
            gate_settings=replace(
                self._observation_gate_settings,
                enabled=(
                    self._observation_gate_settings.enabled
                    and interaction_config.enabled
                ),
                muted=interaction_config.personal_runtime_muted,
                quiet_hours_start_minute=(
                    interaction_config.personal_runtime_quiet_hours_start * 60
                    if interaction_config.personal_runtime_quiet_hours_enabled
                    else None
                ),
                quiet_hours_end_minute=(
                    interaction_config.personal_runtime_quiet_hours_end * 60
                    if interaction_config.personal_runtime_quiet_hours_enabled
                    else None
                ),
                timezone_name=interaction_config.personal_runtime_timezone,
                daily_policy_call_limit=(
                    interaction_config.personal_policy_daily_call_limit
                ),
                daily_proactive_output_limit=(
                    interaction_config.personal_runtime_daily_proactive_output_limit
                ),
            ),
            action_handler=self._dispatch_personal_action,
        )
        runtime.bind_observation_wake_scheduler(self._observation_wake_scheduler)
        return runtime

    def _ensure_accepting(self) -> None:
        if not self._accepting:
            raise RuntimeError("Personal Runtime Manager is shutting down")

    def _evict_idle_sessions(self, *, now: float) -> None:
        expired_keys = [
            key
            for key, runtime in self._sessions.items()
            if runtime.is_idle()
            and now - runtime.last_access_at >= self._idle_runtime_ttl_seconds
        ]
        for key in expired_keys:
            self._evict_runtime(key, reason="idle_ttl")

        idle_runtimes = sorted(
            (runtime for runtime in self._sessions.values() if runtime.is_idle()),
            key=lambda runtime: runtime.last_access_at,
        )
        overflow = len(idle_runtimes) - self._max_idle_runtimes
        for runtime in idle_runtimes[: max(0, overflow)]:
            self._evict_runtime(runtime.key, reason="idle_lru")

    def _evict_runtime(self, key: PersonalRuntimeKey, *, reason: str) -> None:
        runtime = self._sessions.get(key)
        if runtime is None or not runtime.is_idle():
            return
        self._sessions.pop(key, None)
        if self._observation_wake_scheduler is not None:
            self._observation_wake_scheduler.cancel(key)
        self._eviction_count += 1
        logger.debug(
            "Personal Runtime evicted: reason=%s config_id=%s persona_id=%s audience=%s privacy_scope=%s",
            reason,
            key.config_id,
            key.persona_id,
            key.audience_key,
            key.privacy_scope,
        )

    async def _resolve_persona_id(
        self,
        reservation: PendingTurnReservation,
    ) -> str:
        turn = reservation.turn
        event = turn.event
        try:
            request = turn.provider_request
            conversation_persona_id = None
            if (
                isinstance(request, ProviderRequest)
                and request.conversation is not None
            ):
                conversation_persona_id = request.conversation.persona_id
            if conversation_persona_id is None:
                conversation_persona_id = await resolve_event_conversation_persona_id(
                    event,
                    turn.plugin_context.conversation_manager,
                )
            return await self._resolve_selected_persona_id(
                unified_msg_origin=turn.session.unified_msg_origin,
                platform_name=turn.session.platform_name,
                conversation_persona_id=conversation_persona_id,
                plugin_context=turn.plugin_context,
                provider_settings=turn.runtime_config.get("provider_settings", {}),
            )
        except Exception as exc:
            logger.warning(
                "Personal Runtime persona resolution failed; isolating turn: session_id=%s error=%s",
                event.unified_msg_origin,
                exc,
            )
            return f"unresolved:{turn.turn_id}"

    async def _resolve_observation_runtime_key(
        self,
        observation: RuntimeObservation,
        *,
        config_id: str,
        plugin_context: Any,
        runtime_config: Mapping[str, Any],
    ) -> PersonalRuntimeKey:
        return await self._resolve_observation_target_runtime_key(
            observation.target_session,
            config_id=config_id,
            plugin_context=plugin_context,
            runtime_config=runtime_config,
        )

    async def _resolve_observation_target_runtime_key(
        self,
        target: RuntimeObservationTarget,
        *,
        config_id: str,
        plugin_context: Any,
        runtime_config: Mapping[str, Any],
    ) -> PersonalRuntimeKey:
        conversation_persona_id = await resolve_conversation_persona_id(
            target.unified_msg_origin,
            plugin_context.conversation_manager,
        )
        persona_id = await self._resolve_selected_persona_id(
            unified_msg_origin=target.unified_msg_origin,
            platform_name=target.platform_name,
            conversation_persona_id=conversation_persona_id,
            plugin_context=plugin_context,
            provider_settings=runtime_config.get("provider_settings", {}),
        )
        return PersonalRuntimeKey(
            config_id=str(config_id or "default"),
            persona_id=persona_id,
            audience_key=target.unified_msg_origin,
            privacy_scope=resolve_privacy_scope(target.message_type),
        )

    @staticmethod
    async def _resolve_selected_persona_id(
        *,
        unified_msg_origin: str,
        platform_name: str,
        conversation_persona_id: str | None,
        plugin_context: Any,
        provider_settings: Mapping[str, Any] | None,
    ) -> str:
        (
            persona_id,
            _,
            _,
            _,
        ) = await plugin_context.persona_manager.resolve_selected_persona(
            umo=unified_msg_origin,
            conversation_persona_id=conversation_persona_id,
            platform_name=platform_name,
            provider_settings=dict(provider_settings or {}),
        )
        return str(persona_id or "default")


__all__ = [
    "PendingTurnReservation",
    "PendingTurnState",
    "PlatformEventSubmission",
    "RuntimeObservationEventSubmission",
    "PersonalRuntimeKey",
    "PersonalRuntimeManagerSnapshot",
    "PersonalRuntimeManager",
    "PersonalSessionRuntime",
    "PersonalSessionRuntimeSnapshot",
    "PersonalTurnLease",
    "TurnAdmission",
]
