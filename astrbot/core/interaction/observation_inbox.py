from __future__ import annotations

import uuid
from collections import Counter, OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING

from .observation import RuntimeObservation

if TYPE_CHECKING:
    from .personal_runtime import PersonalRuntimeKey


class ObservationAdmissionStatus(str, Enum):
    ADMITTED = "admitted"
    COALESCED = "coalesced"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ObservationAdmissionResult:
    status: ObservationAdmissionStatus
    observation_id: str
    runtime_key: PersonalRuntimeKey
    pending_count: int
    evaluation_task_created: bool = False
    dropped_observation_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    @property
    def admitted(self) -> bool:
        return self.status in {
            ObservationAdmissionStatus.ADMITTED,
            ObservationAdmissionStatus.COALESCED,
        }


@dataclass(frozen=True, slots=True)
class ObservationBatch:
    batch_id: str
    runtime_key: PersonalRuntimeKey
    opened_at: float
    closed_at: float
    observations: tuple[RuntimeObservation, ...]
    source_counts: Mapping[str, int]
    latest_occurred_at: float

    @classmethod
    def create(
        cls,
        *,
        runtime_key: PersonalRuntimeKey,
        opened_at: float,
        closed_at: float,
        observations: Sequence[RuntimeObservation],
    ) -> ObservationBatch:
        items = tuple(observations)
        if not items:
            raise ValueError("ObservationBatch requires at least one observation")
        source_counts = MappingProxyType(dict(Counter(item.source for item in items)))
        return cls(
            batch_id=uuid.uuid4().hex,
            runtime_key=runtime_key,
            opened_at=float(opened_at),
            closed_at=float(closed_at),
            observations=items,
            source_counts=source_counts,
            latest_occurred_at=max(item.occurred_at for item in items),
        )


class ObservationInbox:
    """Bounded, coalescing observation storage owned by one Runtime."""

    def __init__(self, *, max_pending: int) -> None:
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        self._max_pending = int(max_pending)
        self._items: OrderedDict[str, RuntimeObservation] = OrderedDict()
        self._coalesced_ids: dict[tuple[str, str, str], str] = {}
        self._opened_at: float | None = None
        self.overflow_drop_count = 0
        self.expired_drop_count = 0

    @property
    def pending_count(self) -> int:
        return len(self._items)

    def admit(
        self,
        observation: RuntimeObservation,
        *,
        runtime_key: PersonalRuntimeKey,
        now: float,
    ) -> ObservationAdmissionResult:
        dropped_ids = self._remove_expired(now=now)
        reason_codes = ["inbox_expired_removed"] if dropped_ids else []

        if observation.expires_at is not None and observation.expires_at <= now:
            self.expired_drop_count += 1
            return ObservationAdmissionResult(
                status=ObservationAdmissionStatus.EXPIRED,
                observation_id=observation.observation_id,
                runtime_key=runtime_key,
                pending_count=self.pending_count,
                dropped_observation_ids=(*dropped_ids, observation.observation_id),
                reason_codes=(*reason_codes, "observation_expired"),
            )

        coalesce_identity = observation.coalesce_identity
        replaced_id = (
            observation.observation_id
            if observation.observation_id in self._items
            else self._coalesced_ids.get(coalesce_identity)
            if coalesce_identity is not None
            else None
        )
        status = ObservationAdmissionStatus.ADMITTED
        if replaced_id is not None:
            self._remove(replaced_id)
            dropped_ids.append(replaced_id)
            reason_codes.append(
                "inbox_duplicate_replaced"
                if replaced_id == observation.observation_id
                else "inbox_coalesced_replaced"
            )
            status = ObservationAdmissionStatus.COALESCED

        if self.pending_count >= self._max_pending:
            oldest_id = next(iter(self._items))
            self._remove(oldest_id)
            self.overflow_drop_count += 1
            dropped_ids.append(oldest_id)
            reason_codes.append("inbox_overflow_drop_oldest")

        if not self._items:
            self._opened_at = now
        self._items[observation.observation_id] = observation
        if coalesce_identity is not None:
            self._coalesced_ids[coalesce_identity] = observation.observation_id

        return ObservationAdmissionResult(
            status=status,
            observation_id=observation.observation_id,
            runtime_key=runtime_key,
            pending_count=self.pending_count,
            dropped_observation_ids=tuple(dropped_ids),
            reason_codes=tuple(reason_codes),
        )

    def drain(
        self,
        *,
        runtime_key: PersonalRuntimeKey,
        closed_at: float,
    ) -> ObservationBatch | None:
        self._remove_expired(now=closed_at)
        if not self._items:
            self._opened_at = None
            return None
        observations = tuple(self._items.values())
        opened_at = self._opened_at if self._opened_at is not None else closed_at
        self.clear()
        return ObservationBatch.create(
            runtime_key=runtime_key,
            opened_at=opened_at,
            closed_at=closed_at,
            observations=observations,
        )

    def restore(self, batch: ObservationBatch) -> None:
        """Restore held facts while retaining newer observations admitted meanwhile."""
        restored = OrderedDict(
            (observation.observation_id, observation)
            for observation in batch.observations
        )
        for observation_id, observation in self._items.items():
            restored.pop(observation_id, None)
            if observation.coalesce_identity is not None:
                stale_ids = [
                    item_id
                    for item_id, item in restored.items()
                    if item.coalesce_identity == observation.coalesce_identity
                ]
                for stale_id in stale_ids:
                    restored.pop(stale_id, None)
            restored[observation_id] = observation

        overflow = max(0, len(restored) - self._max_pending)
        for _ in range(overflow):
            restored.popitem(last=False)
        self.overflow_drop_count += overflow
        self._items = restored
        self._coalesced_ids = {
            observation.coalesce_identity: observation_id
            for observation_id, observation in restored.items()
            if observation.coalesce_identity is not None
        }
        if restored:
            opened_at = self._opened_at
            self._opened_at = min(
                value
                for value in (batch.opened_at, opened_at)
                if value is not None
            )
        else:
            self._opened_at = None

    def clear(self) -> None:
        self._items.clear()
        self._coalesced_ids.clear()
        self._opened_at = None

    def _remove_expired(self, *, now: float) -> list[str]:
        expired_ids = [
            observation_id
            for observation_id, observation in self._items.items()
            if observation.expires_at is not None and observation.expires_at <= now
        ]
        for observation_id in expired_ids:
            self._remove(observation_id)
        self.expired_drop_count += len(expired_ids)
        if not self._items:
            self._opened_at = None
        return expired_ids

    def _remove(self, observation_id: str) -> None:
        observation = self._items.pop(observation_id, None)
        if observation is None or observation.coalesce_identity is None:
            return
        if self._coalesced_ids.get(observation.coalesce_identity) == observation_id:
            self._coalesced_ids.pop(observation.coalesce_identity, None)


__all__ = [
    "ObservationAdmissionResult",
    "ObservationAdmissionStatus",
    "ObservationBatch",
    "ObservationInbox",
]
