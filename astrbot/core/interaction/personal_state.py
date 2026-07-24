from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PersonalAttentionState(str, Enum):
    IDLE = "idle"
    ENGAGED = "engaged"


class PersonalAvailabilityState(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    MUTED = "muted"


class PersonalDeliveryStatus(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPPRESSED = "suppressed"


class PersonalExecutionStatus(str, Enum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class PersonalStateSnapshot:
    attention_state: PersonalAttentionState
    availability_state: PersonalAvailabilityState
    last_observation_at: float | None
    last_user_activity_at: float | None
    last_expression_at: float | None
    reply_cooldown_until: float | None
    no_action_cooldown_until: float | None
    mute_until: float | None
    pending_observation_count: int
    usage_day: str | None
    daily_policy_calls: int
    daily_proactive_outputs: int
    last_gate_reason: str | None
    last_policy_action: str | None


@dataclass(frozen=True, slots=True)
class PersonalPersistentState:
    last_expression_at: float | None
    reply_cooldown_until: float | None
    no_action_cooldown_until: float | None
    mute_until: float | None
    usage_day: str | None
    daily_policy_calls: int
    daily_proactive_outputs: int


@dataclass(slots=True)
class PersonalState:
    """Process-local control state owned by one Personal Session Runtime."""

    attention_state: PersonalAttentionState = PersonalAttentionState.IDLE
    availability_state: PersonalAvailabilityState = PersonalAvailabilityState.AVAILABLE
    last_observation_at: float | None = None
    last_user_activity_at: float | None = None
    last_expression_at: float | None = None
    reply_cooldown_until: float | None = None
    no_action_cooldown_until: float | None = None
    mute_until: float | None = None
    pending_observation_count: int = 0
    usage_day: str | None = None
    daily_policy_calls: int = 0
    daily_proactive_outputs: int = 0
    last_gate_reason: str | None = None
    last_policy_action: str | None = None

    def mark_turn_active(
        self,
        *,
        user_activity_at: float | None = None,
    ) -> None:
        self.attention_state = PersonalAttentionState.ENGAGED
        self.availability_state = PersonalAvailabilityState.BUSY
        if user_activity_at is not None:
            self.last_user_activity_at = max(
                user_activity_at,
                self.last_user_activity_at or user_activity_at,
            )

    def mark_idle(self, *, now: float) -> None:
        self.attention_state = PersonalAttentionState.IDLE
        self.availability_state = (
            PersonalAvailabilityState.MUTED
            if self.mute_until is not None and self.mute_until > now
            else PersonalAvailabilityState.AVAILABLE
        )

    def apply_completion_feedback(self, feedback: CompletionFeedback) -> bool:
        previous_expression_at = self.last_expression_at
        if (
            feedback.delivery_status is PersonalDeliveryStatus.DELIVERED
            and feedback.output_completed_at is not None
        ):
            self.last_expression_at = max(
                feedback.output_completed_at,
                self.last_expression_at or feedback.output_completed_at,
            )
        return self.last_expression_at != previous_expression_at

    def record_observation(self, *, occurred_at: float, pending_count: int) -> None:
        self.last_observation_at = max(
            occurred_at,
            self.last_observation_at or occurred_at,
        )
        self.pending_observation_count = max(0, int(pending_count))

    def set_pending_observation_count(self, pending_count: int) -> None:
        self.pending_observation_count = max(0, int(pending_count))

    def record_gate_result(self, reason_code: str) -> None:
        self.last_gate_reason = str(reason_code or "").strip() or None

    def record_policy_call(self, *, usage_day: str) -> None:
        normalized_day = str(usage_day or "").strip()
        if not normalized_day:
            raise ValueError("usage_day is required")
        if self.usage_day != normalized_day:
            self.usage_day = normalized_day
            self.daily_policy_calls = 0
            self.daily_proactive_outputs = 0
        self.daily_policy_calls += 1

    def record_policy_action(self, action: str) -> None:
        self.last_policy_action = str(action or "").strip() or None

    def restore_persistent(self, state: PersonalPersistentState) -> None:
        self.last_expression_at = state.last_expression_at
        self.reply_cooldown_until = state.reply_cooldown_until
        self.no_action_cooldown_until = state.no_action_cooldown_until
        self.mute_until = state.mute_until
        self.usage_day = str(state.usage_day or "").strip() or None
        self.daily_policy_calls = max(0, int(state.daily_policy_calls))
        self.daily_proactive_outputs = max(0, int(state.daily_proactive_outputs))

    def persistent_snapshot(self) -> PersonalPersistentState:
        return PersonalPersistentState(
            last_expression_at=self.last_expression_at,
            reply_cooldown_until=self.reply_cooldown_until,
            no_action_cooldown_until=self.no_action_cooldown_until,
            mute_until=self.mute_until,
            usage_day=self.usage_day,
            daily_policy_calls=self.daily_policy_calls,
            daily_proactive_outputs=self.daily_proactive_outputs,
        )

    def snapshot(self) -> PersonalStateSnapshot:
        return PersonalStateSnapshot(
            attention_state=self.attention_state,
            availability_state=self.availability_state,
            last_observation_at=self.last_observation_at,
            last_user_activity_at=self.last_user_activity_at,
            last_expression_at=self.last_expression_at,
            reply_cooldown_until=self.reply_cooldown_until,
            no_action_cooldown_until=self.no_action_cooldown_until,
            mute_until=self.mute_until,
            pending_observation_count=self.pending_observation_count,
            usage_day=self.usage_day,
            daily_policy_calls=self.daily_policy_calls,
            daily_proactive_outputs=self.daily_proactive_outputs,
            last_gate_reason=self.last_gate_reason,
            last_policy_action=self.last_policy_action,
        )


@dataclass(frozen=True, slots=True)
class CompletionFeedback:
    action_id: str | None
    turn_id: str
    delivery_status: PersonalDeliveryStatus
    execution_status: PersonalExecutionStatus = PersonalExecutionStatus.NOT_STARTED
    output_completed_at: float | None = None
    failure_code: str | None = None
    user_follow_up_observed: bool = False


__all__ = [
    "CompletionFeedback",
    "PersonalAttentionState",
    "PersonalAvailabilityState",
    "PersonalDeliveryStatus",
    "PersonalExecutionStatus",
    "PersonalPersistentState",
    "PersonalState",
    "PersonalStateSnapshot",
]
