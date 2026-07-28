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
    material_revision: int
    last_settled_material_revision: int
    usage_day: str | None
    daily_policy_calls: int
    daily_proactive_outputs: int
    last_gate_reason: str | None
    last_policy_action: str | None


@dataclass(frozen=True, slots=True)
class PersonalPersistentState:
    last_user_activity_at: float | None
    last_idle_initiation_activity_at: float | None
    last_expression_at: float | None
    last_expression_fingerprint: str | None
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
    last_idle_initiation_activity_at: float | None = None
    last_expression_at: float | None = None
    last_expression_fingerprint: str | None = None
    reply_cooldown_until: float | None = None
    no_action_cooldown_until: float | None = None
    mute_until: float | None = None
    pending_observation_count: int = 0
    material_revision: int = 0
    last_settled_material_revision: int = 0
    usage_day: str | None = None
    daily_policy_calls: int = 0
    daily_proactive_outputs: int = 0
    last_gate_reason: str | None = None
    last_policy_action: str | None = None

    def mark_turn_active(
        self,
        *,
        user_activity_at: float | None = None,
    ) -> bool:
        previous_state = self.persistent_snapshot()
        self.attention_state = PersonalAttentionState.ENGAGED
        self.availability_state = PersonalAvailabilityState.BUSY
        if user_activity_at is not None:
            self.last_user_activity_at = max(
                user_activity_at,
                self.last_user_activity_at or user_activity_at,
            )
        return self.persistent_snapshot() != previous_state

    def mark_idle(self, *, now: float) -> None:
        self.attention_state = PersonalAttentionState.IDLE
        self.availability_state = (
            PersonalAvailabilityState.MUTED
            if self.mute_until is not None and self.mute_until > now
            else PersonalAvailabilityState.AVAILABLE
        )

    def claim_idle_initiation(self, *, user_activity_at: float) -> None:
        self.last_idle_initiation_activity_at = user_activity_at

    def apply_completion_feedback(
        self,
        feedback: CompletionFeedback,
        *,
        reply_cooldown_seconds: float = 0.0,
        usage_day: str | None = None,
    ) -> bool:
        previous_state = self.persistent_snapshot()
        if (
            feedback.delivery_status is PersonalDeliveryStatus.DELIVERED
            and feedback.output_completed_at is not None
        ):
            completed_at = feedback.output_completed_at
            self.last_expression_at = max(
                completed_at,
                self.last_expression_at or completed_at,
            )
            cooldown_until = completed_at + max(0.0, reply_cooldown_seconds)
            self.reply_cooldown_until = max(
                cooldown_until,
                self.reply_cooldown_until or cooldown_until,
            )
            if feedback.visible_reply_fingerprint:
                self.last_expression_fingerprint = (
                    feedback.visible_reply_fingerprint
                )
        if (
            feedback.action_id
            and feedback.delivery_status is PersonalDeliveryStatus.DELIVERED
        ):
            completed_at = feedback.output_completed_at
            if completed_at is None:
                raise ValueError("Delivered proactive action requires completion time")
            if usage_day is None:
                raise ValueError("Delivered proactive action requires usage_day")
            self._ensure_usage_day(usage_day)
            self.daily_proactive_outputs += 1
        return self.persistent_snapshot() != previous_state

    def record_observation(
        self,
        *,
        occurred_at: float,
        pending_count: int,
    ) -> None:
        self.last_observation_at = max(
            occurred_at,
            self.last_observation_at or occurred_at,
        )
        self.pending_observation_count = max(0, int(pending_count))

    def record_material_change(self) -> int:
        self.material_revision += 1
        return self.material_revision

    def settle_material_revision(self, material_revision: int) -> int:
        revision = min(self.material_revision, max(0, int(material_revision)))
        self.last_settled_material_revision = max(
            self.last_settled_material_revision,
            revision,
        )
        return self.last_settled_material_revision

    def set_pending_observation_count(self, pending_count: int) -> None:
        self.pending_observation_count = max(0, int(pending_count))

    def record_gate_result(self, reason_code: str) -> None:
        self.last_gate_reason = str(reason_code or "").strip() or None

    def record_policy_call(self, *, usage_day: str) -> None:
        self._ensure_usage_day(usage_day)
        self.daily_policy_calls += 1

    def record_policy_action(self, action: str) -> None:
        self.last_policy_action = str(action or "").strip() or None

    def defer_actions_until(self, not_before: float) -> bool:
        previous = self.no_action_cooldown_until
        self.no_action_cooldown_until = max(
            float(not_before),
            self.no_action_cooldown_until or float(not_before),
        )
        return self.no_action_cooldown_until != previous

    def _ensure_usage_day(self, usage_day: str) -> None:
        normalized_day = str(usage_day or "").strip()
        if not normalized_day:
            raise ValueError("usage_day is required")
        if self.usage_day != normalized_day:
            self.usage_day = normalized_day
            self.daily_policy_calls = 0
            self.daily_proactive_outputs = 0

    def restore_persistent(self, state: PersonalPersistentState) -> None:
        self.last_user_activity_at = state.last_user_activity_at
        self.last_idle_initiation_activity_at = (
            state.last_idle_initiation_activity_at
        )
        self.last_expression_at = state.last_expression_at
        self.last_expression_fingerprint = state.last_expression_fingerprint
        self.reply_cooldown_until = state.reply_cooldown_until
        self.no_action_cooldown_until = state.no_action_cooldown_until
        self.mute_until = state.mute_until
        self.usage_day = str(state.usage_day or "").strip() or None
        self.daily_policy_calls = max(0, int(state.daily_policy_calls))
        self.daily_proactive_outputs = max(0, int(state.daily_proactive_outputs))

    def persistent_snapshot(self) -> PersonalPersistentState:
        return PersonalPersistentState(
            last_user_activity_at=self.last_user_activity_at,
            last_idle_initiation_activity_at=self.last_idle_initiation_activity_at,
            last_expression_at=self.last_expression_at,
            last_expression_fingerprint=self.last_expression_fingerprint,
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
            material_revision=self.material_revision,
            last_settled_material_revision=(self.last_settled_material_revision),
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
    output_completed_at: float | None = None
    failure_code: str | None = None
    user_follow_up_observed: bool = False
    visible_reply_fingerprint: str | None = None


__all__ = [
    "CompletionFeedback",
    "PersonalAttentionState",
    "PersonalAvailabilityState",
    "PersonalDeliveryStatus",
    "PersonalPersistentState",
    "PersonalState",
    "PersonalStateSnapshot",
]
