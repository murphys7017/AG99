from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .observation import RuntimeObservation
from .observation_inbox import ObservationBatch
from .personal_policy import PersonalPolicyAction, PersonalPolicyDecision


@dataclass(frozen=True, slots=True)
class PersonalActionIntent:
    """One internal, policy-approved proactive expression request."""

    batch_id: str
    reply_intent: str
    created_at: float
    target_observation: RuntimeObservation
    action_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if not self.batch_id.strip():
            raise ValueError("PersonalActionIntent.batch_id is required")
        if not self.reply_intent.strip():
            raise ValueError("PersonalActionIntent.reply_intent is required")

    def to_observation(self) -> RuntimeObservation:
        return RuntimeObservation(
            kind="personal_action",
            source="personal_runtime.policy",
            occurred_at=self.created_at,
            target_session=self.target_observation.target_session,
            correlation_id=self.action_id,
            payload={
                "personal_action_id": self.action_id,
                "personal_action_kind": PersonalPolicyAction.EXPRESS.value,
                "personal_policy_batch_id": self.batch_id,
                "visible_reply_material": (
                    "这是由持续人格运行时形成的主动表达任务，不是新的用户消息。\n"
                    f"主动表达意图：{self.reply_intent}\n"
                    "请结合已有对话与人格自然表达，不要提及系统、策略、"
                    "Observation 或内部任务。不要虚构未提供的事实。"
                ),
            },
        )


@dataclass(frozen=True, slots=True)
class PersonalActionPlan:
    intent: PersonalActionIntent | None = None
    defer_until: float | None = None


class PersonalActionCoordinator:
    """Turns validated Policy decisions into explicit runtime control actions."""

    @staticmethod
    def plan(
        *,
        decision: PersonalPolicyDecision,
        batch: ObservationBatch,
        evaluated_at: float,
        minimum_defer_seconds: float,
    ) -> PersonalActionPlan:
        if decision.action is PersonalPolicyAction.EXPRESS:
            return PersonalActionPlan(
                intent=PersonalActionIntent(
                    batch_id=batch.batch_id,
                    reply_intent=decision.reply_intent,
                    created_at=evaluated_at,
                    target_observation=batch.observations[-1],
                )
            )
        if decision.action is PersonalPolicyAction.DEFER:
            return PersonalActionPlan(
                defer_until=evaluated_at
                + max(float(decision.defer_seconds), minimum_defer_seconds)
            )
        return PersonalActionPlan()


__all__ = [
    "PersonalActionCoordinator",
    "PersonalActionIntent",
    "PersonalActionPlan",
]
