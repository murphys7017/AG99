from __future__ import annotations

import math
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from .analyzers.base import MemoryAnalyzerExecutionError
from .store import MemoryStore
from .types import (
    PersonaEvolutionLog,
    PersonaState,
    PersonaStateEvolutionResult,
    ScopeType,
    SourceRef,
)

PERSONA_STATE_FIELDS = (
    "familiarity",
    "trust",
    "warmth",
    "formality_preference",
    "directness_preference",
)
PERSONA_REFLECTION_MIN_CONFIDENCE = 0.7
PERSONA_REFLECTION_MAX_DELTA = 0.08


class PersonaStateService:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self._diagnostic_counts: Counter[str] = Counter()

    def record_submitted(self) -> None:
        self._diagnostic_counts["submitted"] += 1

    def record_skipped(self) -> None:
        self._diagnostic_counts["skipped"] += 1

    def record_rejected(self) -> None:
        self._diagnostic_counts["rejected"] += 1

    def record_failed(self) -> None:
        self._diagnostic_counts["failed"] += 1

    def diagnostics_view(self) -> dict[str, int]:
        return {
            status: self._diagnostic_counts.get(status, 0)
            for status in (
                "submitted",
                "skipped",
                "applied",
                "rejected",
                "failed",
                "rolled_back",
            )
        }

    @staticmethod
    def neutral_values() -> dict[str, float]:
        return {
            "familiarity": 0.0,
            "trust": 0.5,
            "warmth": 0.5,
            "formality_preference": 0.5,
            "directness_preference": 0.5,
        }

    def reflection_due(
        self,
        state: PersonaState | None,
        *,
        now: datetime | None = None,
    ) -> bool:
        if state is None or state.updated_at is None:
            return True
        interval_hours = max(
            0,
            int(self.store.config.persona.reflection_interval_hours),
        )
        if interval_hours == 0:
            return True
        current_time = self._normalize_datetime(now or datetime.now(UTC))
        updated_at = self._normalize_datetime(state.updated_at)
        return current_time >= updated_at + timedelta(hours=interval_hours)

    async def get_state(self, canonical_user_id: str) -> PersonaState | None:
        return await self.store.get_persona_state(
            ScopeType.USER,
            self._require_user_id(canonical_user_id),
        )

    async def apply_reflection(
        self,
        canonical_user_id: str,
        *,
        persona_id: str | None,
        deltas: Mapping[str, float],
        confidence: float,
        reason: str | None = None,
        source_refs: Sequence[SourceRef] = (),
        now: datetime | None = None,
    ) -> PersonaStateEvolutionResult | None:
        user_id = self._require_user_id(canonical_user_id)
        confidence_value = self._require_finite_number(confidence, "confidence")
        if not 0.0 <= confidence_value <= 1.0:
            raise ValueError("persona reflection confidence must be between 0 and 1")
        if confidence_value < PERSONA_REFLECTION_MIN_CONFIDENCE:
            return None

        normalized_deltas = self._normalize_deltas(deltas)
        if not any(normalized_deltas.values()):
            return None

        current = await self.get_state(user_id)
        normalized_persona_id = self._normalize_optional_id(persona_id)
        if current is not None:
            # PersonaState is USER-scoped in the first version. Keep the original
            # attribution when a later turn selects another static Persona.
            normalized_persona_id = current.persona_id
        current_time = self._normalize_datetime(now or datetime.now(UTC))
        if not self.reflection_due(current, now=current_time):
            return None

        values = self._state_values(current)
        updated_values = {
            field_name: self._clamp_score(
                values[field_name] + normalized_deltas[field_name]
            )
            for field_name in PERSONA_STATE_FIELDS
        }
        if updated_values == values:
            return None

        state = PersonaState(
            state_id=current.state_id if current is not None else str(uuid.uuid4()),
            scope_type=ScopeType.USER,
            scope_id=user_id,
            persona_id=normalized_persona_id,
            familiarity=updated_values["familiarity"],
            trust=updated_values["trust"],
            warmth=updated_values["warmth"],
            formality_preference=updated_values["formality_preference"],
            directness_preference=updated_values["directness_preference"],
            updated_at=current_time,
        )
        persisted, log = await self.store.apply_persona_state_evolution(
            state,
            expected_state=current,
            log_id=str(uuid.uuid4()),
            reason=self._normalize_reason(reason),
            source_refs=self._normalize_source_refs(source_refs),
            created_at=current_time,
        )
        return PersonaStateEvolutionResult(state=persisted, log=log)

    def validate_reflection_result(
        self,
        data: object,
    ) -> tuple[bool, float, str | None, dict[str, float]]:
        if not isinstance(data, dict):
            raise MemoryAnalyzerExecutionError(
                "persona_reflection returned invalid payload"
            )
        should_update = data.get("should_update")
        if not isinstance(should_update, bool):
            raise MemoryAnalyzerExecutionError(
                "persona_reflection field should_update must be boolean"
            )
        try:
            confidence = self._require_finite_number(
                data.get("confidence"),
                "confidence",
            )
        except ValueError as exc:
            raise MemoryAnalyzerExecutionError(str(exc)) from exc
        if not 0.0 <= confidence <= 1.0:
            raise MemoryAnalyzerExecutionError(
                "persona_reflection field confidence must be between 0 and 1"
            )
        reason = data.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise MemoryAnalyzerExecutionError(
                "persona_reflection field reason must be a non-empty string"
            )
        deltas = data.get("deltas")
        if not isinstance(deltas, Mapping):
            raise MemoryAnalyzerExecutionError(
                "persona_reflection field deltas must be an object"
            )
        missing_fields = set(PERSONA_STATE_FIELDS) - set(deltas)
        if missing_fields:
            fields = ", ".join(sorted(missing_fields))
            raise MemoryAnalyzerExecutionError(
                f"persona_reflection deltas missing fields: {fields}"
            )
        try:
            normalized_deltas = self._normalize_deltas(deltas)
        except ValueError as exc:
            raise MemoryAnalyzerExecutionError(str(exc)) from exc
        return should_update, confidence, reason.strip(), normalized_deltas

    async def apply_reflection_result(
        self,
        canonical_user_id: str,
        *,
        persona_id: str | None,
        data: object,
        source_refs: Sequence[SourceRef] = (),
        now: datetime | None = None,
    ) -> PersonaStateEvolutionResult | None:
        should_update, confidence, reason, deltas = self.validate_reflection_result(
            data
        )
        if not should_update:
            self.record_skipped()
            return None
        result = await self.apply_reflection(
            canonical_user_id,
            persona_id=persona_id,
            deltas=deltas,
            confidence=confidence,
            reason=reason,
            source_refs=source_refs,
            now=now,
        )
        if result is None:
            self.record_skipped()
        else:
            self._diagnostic_counts["applied"] += 1
        return result

    async def list_evolution_logs(
        self,
        canonical_user_id: str,
        *,
        limit: int = 50,
    ) -> list[PersonaEvolutionLog]:
        return await self.store.list_persona_evolution_logs(
            ScopeType.USER,
            self._require_user_id(canonical_user_id),
            limit=limit,
        )

    async def rollback(
        self,
        log_id: str,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> PersonaStateEvolutionResult:
        normalized_log_id = str(log_id).strip()
        if not normalized_log_id:
            raise ValueError("persona evolution log_id is required")
        target = await self.store.get_persona_evolution_log(normalized_log_id)
        if target is None:
            raise ValueError(
                f"persona evolution log `{normalized_log_id}` was not found"
            )
        if target.scope_type != ScopeType.USER.value:
            raise ValueError("only USER persona state can be rolled back")
        state, log = await self.store.rollback_persona_state_evolution(
            normalized_log_id,
            rollback_log_id=str(uuid.uuid4()),
            reason=self._normalize_reason(reason),
            created_at=self._normalize_datetime(now or datetime.now(UTC)),
        )
        self._diagnostic_counts["rolled_back"] += 1
        return PersonaStateEvolutionResult(state=state, log=log)

    @classmethod
    def _normalize_deltas(
        cls,
        deltas: Mapping[str, float],
    ) -> dict[str, float]:
        unknown_fields = set(deltas) - set(PERSONA_STATE_FIELDS)
        if unknown_fields:
            fields = ", ".join(sorted(unknown_fields))
            raise ValueError(f"unknown persona state delta fields: {fields}")
        normalized: dict[str, float] = {}
        for field_name in PERSONA_STATE_FIELDS:
            value = cls._require_finite_number(
                deltas.get(field_name, 0.0),
                field_name,
            )
            normalized[field_name] = max(
                -PERSONA_REFLECTION_MAX_DELTA,
                min(PERSONA_REFLECTION_MAX_DELTA, value),
            )
        return normalized

    @classmethod
    def _state_values(cls, state: PersonaState | None) -> dict[str, float]:
        if state is None:
            return cls.neutral_values()
        return {
            field_name: cls._clamp_score(float(getattr(state, field_name)))
            for field_name in PERSONA_STATE_FIELDS
        }

    @staticmethod
    def _require_user_id(value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("canonical_user_id is required for persona state")
        return normalized

    @staticmethod
    def _normalize_optional_id(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _normalize_reason(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _normalize_source_refs(values: Sequence[SourceRef]) -> list[SourceRef]:
        return [str(item).strip() for item in values if str(item).strip()]

    @staticmethod
    def _require_finite_number(value: object, field_name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"persona state `{field_name}` must be numeric")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ValueError(f"persona state `{field_name}` must be finite")
        return normalized

    @staticmethod
    def _clamp_score(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
