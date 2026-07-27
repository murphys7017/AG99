from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .observation import RuntimeObservation
from .observation_inbox import ObservationBatch
from .personal_state import PersonalAvailabilityState, PersonalStateSnapshot


class ObservationGateDisposition(str, Enum):
    EVALUATE = "evaluate"
    HOLD = "hold"
    REJECT = "reject"


class ObservationGateReason(str, Enum):
    ACCEPTED = "accepted"
    FEATURE_DISABLED = "feature_disabled"
    OBSERVATION_EXPIRED = "observation_expired"
    MISSING_MATERIAL = "missing_material"
    NO_MATERIAL_CHANGE = "no_material_change"
    RUNTIME_BUSY = "runtime_busy"
    MUTED = "muted"
    QUIET_HOURS = "quiet_hours"
    REPLY_COOLDOWN = "reply_cooldown"
    NO_ACTION_COOLDOWN = "no_action_cooldown"
    POLICY_BUDGET_EXHAUSTED = "policy_budget_exhausted"
    OUTPUT_BUDGET_EXHAUSTED = "output_budget_exhausted"
    TARGET_UNAVAILABLE = "target_unavailable"


@dataclass(frozen=True, slots=True)
class ObservationGateSettings:
    enabled: bool = True
    minimum_observation_count: int = 1
    muted: bool = False
    quiet_hours_start_minute: int | None = None
    quiet_hours_end_minute: int | None = None
    timezone_name: str | None = None
    daily_policy_call_limit: int | None = None
    daily_proactive_output_limit: int | None = None

    def __post_init__(self) -> None:
        if self.minimum_observation_count <= 0:
            raise ValueError("minimum_observation_count must be positive")
        start = self.quiet_hours_start_minute
        end = self.quiet_hours_end_minute
        if (start is None) != (end is None):
            raise ValueError("quiet hours require both start and end minutes")
        if start is not None:
            if not 0 <= start < 24 * 60 or not 0 <= end < 24 * 60:
                raise ValueError("quiet hour minutes must be between 0 and 1439")
        for name, value in (
            ("daily_policy_call_limit", self.daily_policy_call_limit),
            ("daily_proactive_output_limit", self.daily_proactive_output_limit),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        timezone_name = str(self.timezone_name or "").strip() or None
        if timezone_name is not None:
            try:
                ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(f"Unknown timezone: {timezone_name}") from exc
        object.__setattr__(self, "timezone_name", timezone_name)

    def local_datetime(self, timestamp: float) -> datetime:
        if self.timezone_name is not None:
            return datetime.fromtimestamp(timestamp, ZoneInfo(self.timezone_name))
        return datetime.fromtimestamp(timestamp).astimezone()

    def is_quiet_hours(self, timestamp: float) -> bool:
        start = self.quiet_hours_start_minute
        end = self.quiet_hours_end_minute
        if start is None or end is None:
            return False
        if start == end:
            return True
        local = self.local_datetime(timestamp)
        minute = local.hour * 60 + local.minute
        if start < end:
            return start <= minute < end
        return minute >= start or minute < end

    def quiet_hours_end_at(self, timestamp: float) -> float | None:
        """Return the next quiet-hours boundary when the current time is held."""
        start = self.quiet_hours_start_minute
        end = self.quiet_hours_end_minute
        if start is None or end is None or start == end:
            return None
        local = self.local_datetime(timestamp)
        minute = local.hour * 60 + local.minute
        if start < end and not start <= minute < end:
            return None
        if start > end and not (minute >= start or minute < end):
            return None
        boundary = local.replace(
            hour=end // 60,
            minute=end % 60,
            second=0,
            microsecond=0,
        )
        if start > end and minute >= start:
            boundary += timedelta(days=1)
        return boundary.timestamp()


@dataclass(frozen=True, slots=True)
class ObservationFeatures:
    is_explicitly_summoned: bool
    is_follow_up_candidate: bool
    message_count: int
    participant_count: int
    echo_count: int
    activity_density: float
    seconds_since_user_activity: float | None
    seconds_since_last_expression: float | None
    has_pending_commitment: bool
    is_runtime_busy: bool
    is_quiet_hours: bool
    is_muted: bool
    policy_budget_available: bool
    output_budget_available: bool
    budget_available: bool
    target_available: bool


@dataclass(frozen=True, slots=True)
class ObservationGateResult:
    batch_id: str
    disposition: ObservationGateDisposition
    reason_code: ObservationGateReason
    evaluated_at: float
    features: ObservationFeatures


class ObservationFeatureBuilder:
    @classmethod
    def build(
        cls,
        batch: ObservationBatch,
        *,
        state: PersonalStateSnapshot,
        runtime_busy: bool,
        settings: ObservationGateSettings,
        evaluated_at: float,
    ) -> ObservationFeatures:
        observations = batch.observations
        message_count = sum(cls._message_count(item) for item in observations)
        participant_ids = set(cls._participant_ids(observations))
        reported_participant_count = max(
            (
                cls._nonnegative_int(item.payload.get("participant_count"))
                for item in observations
            ),
            default=0,
        )
        participant_count = max(len(participant_ids), reported_participant_count)
        echo_count = sum(
            cls._nonnegative_int(item.payload.get("echo_count"))
            for item in observations
        )
        activity_span = max(
            1.0,
            batch.latest_occurred_at - min(item.occurred_at for item in observations),
        )
        policy_budget_available, output_budget_available = cls._budget_availability(
            state,
            settings=settings,
            evaluated_at=evaluated_at,
        )
        latest_target = observations[-1].target_session
        return ObservationFeatures(
            is_explicitly_summoned=any(
                item.kind == "explicit_summon"
                or bool(item.payload.get("is_explicitly_summoned", False))
                for item in observations
            ),
            is_follow_up_candidate=any(
                item.kind == "follow_up_candidate"
                or bool(item.payload.get("is_follow_up_candidate", False))
                for item in observations
            ),
            message_count=message_count,
            participant_count=participant_count,
            echo_count=echo_count,
            activity_density=message_count / activity_span,
            seconds_since_user_activity=cls._elapsed(
                state.last_user_activity_at,
                now=evaluated_at,
            ),
            seconds_since_last_expression=cls._elapsed(
                state.last_expression_at,
                now=evaluated_at,
            ),
            has_pending_commitment=any(
                item.kind == "memory_commitment_due"
                or bool(item.payload.get("has_pending_commitment", False))
                for item in observations
            ),
            is_runtime_busy=runtime_busy,
            is_quiet_hours=settings.is_quiet_hours(evaluated_at),
            is_muted=(
                settings.muted
                or state.mute_until is not None
                and state.mute_until > evaluated_at
                or state.availability_state is PersonalAvailabilityState.MUTED
                and state.mute_until is None
            ),
            policy_budget_available=policy_budget_available,
            output_budget_available=output_budget_available,
            budget_available=(policy_budget_available and output_budget_available),
            target_available=bool(latest_target.support_personal_runtime),
        )

    @staticmethod
    def _message_count(observation: RuntimeObservation) -> int:
        value = observation.payload.get("message_count")
        if value is None and observation.kind == "conversation_activity":
            return 1
        return ObservationFeatureBuilder._nonnegative_int(value)

    @staticmethod
    def _participant_ids(
        observations: Iterable[RuntimeObservation],
    ) -> Iterable[str]:
        for observation in observations:
            participant_id = str(
                observation.payload.get("participant_id", "") or ""
            ).strip()
            if participant_id:
                yield participant_id
            participant_ids = observation.payload.get("participant_ids", ())
            if isinstance(participant_ids, str | bytes | Mapping):
                continue
            if isinstance(participant_ids, Iterable):
                for value in participant_ids:
                    normalized = str(value or "").strip()
                    if normalized:
                        yield normalized

    @staticmethod
    def _nonnegative_int(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _elapsed(timestamp: float | None, *, now: float) -> float | None:
        if timestamp is None:
            return None
        return max(0.0, now - timestamp)

    @staticmethod
    def _budget_availability(
        state: PersonalStateSnapshot,
        *,
        settings: ObservationGateSettings,
        evaluated_at: float,
    ) -> tuple[bool, bool]:
        usage_day = settings.local_datetime(evaluated_at).date().isoformat()
        policy_calls = state.daily_policy_calls if state.usage_day == usage_day else 0
        proactive_outputs = (
            state.daily_proactive_outputs if state.usage_day == usage_day else 0
        )
        policy_limit = settings.daily_policy_call_limit
        output_limit = settings.daily_proactive_output_limit
        return (
            policy_limit is None or policy_calls < policy_limit,
            output_limit is None or proactive_outputs < output_limit,
        )


class DeterministicObservationGate:
    @staticmethod
    def evaluate(
        batch: ObservationBatch,
        *,
        state: PersonalStateSnapshot,
        features: ObservationFeatures,
        settings: ObservationGateSettings,
        evaluated_at: float,
    ) -> ObservationGateResult:
        disposition = ObservationGateDisposition.EVALUATE
        reason = ObservationGateReason.ACCEPTED
        if not settings.enabled:
            disposition = ObservationGateDisposition.REJECT
            reason = ObservationGateReason.FEATURE_DISABLED
        elif any(
            item.expires_at is not None and item.expires_at <= evaluated_at
            for item in batch.observations
        ):
            disposition = ObservationGateDisposition.REJECT
            reason = ObservationGateReason.OBSERVATION_EXPIRED
        elif len(batch.observations) < settings.minimum_observation_count:
            disposition = ObservationGateDisposition.REJECT
            reason = ObservationGateReason.MISSING_MATERIAL
        elif state.last_expression_attempt_revision >= state.material_revision:
            disposition = ObservationGateDisposition.REJECT
            reason = ObservationGateReason.NO_MATERIAL_CHANGE
        elif not features.target_available:
            disposition = ObservationGateDisposition.REJECT
            reason = ObservationGateReason.TARGET_UNAVAILABLE
        elif features.is_muted:
            disposition = ObservationGateDisposition.REJECT
            reason = ObservationGateReason.MUTED
        elif features.is_quiet_hours:
            disposition = ObservationGateDisposition.HOLD
            reason = ObservationGateReason.QUIET_HOURS
        elif features.is_runtime_busy:
            disposition = ObservationGateDisposition.HOLD
            reason = ObservationGateReason.RUNTIME_BUSY
        elif (
            state.reply_cooldown_until is not None
            and state.reply_cooldown_until > evaluated_at
        ):
            disposition = ObservationGateDisposition.HOLD
            reason = ObservationGateReason.REPLY_COOLDOWN
        elif (
            state.no_action_cooldown_until is not None
            and state.no_action_cooldown_until > evaluated_at
        ):
            disposition = ObservationGateDisposition.HOLD
            reason = ObservationGateReason.NO_ACTION_COOLDOWN
        elif not features.policy_budget_available:
            disposition = ObservationGateDisposition.REJECT
            reason = ObservationGateReason.POLICY_BUDGET_EXHAUSTED
        elif not features.output_budget_available:
            disposition = ObservationGateDisposition.REJECT
            reason = ObservationGateReason.OUTPUT_BUDGET_EXHAUSTED
        return ObservationGateResult(
            batch_id=batch.batch_id,
            disposition=disposition,
            reason_code=reason,
            evaluated_at=evaluated_at,
            features=features,
        )


__all__ = [
    "DeterministicObservationGate",
    "ObservationFeatureBuilder",
    "ObservationFeatures",
    "ObservationGateDisposition",
    "ObservationGateReason",
    "ObservationGateResult",
    "ObservationGateSettings",
]
