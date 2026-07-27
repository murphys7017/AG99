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
    IGNORED = "ignored"


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
class ObservationMaterial:
    """Process-local freshness metadata for one queued observation."""

    revision: int
    occurred_at: float


@dataclass(frozen=True, slots=True)
class ObservationBatch:
    batch_id: str
    runtime_key: PersonalRuntimeKey
    opened_at: float
    closed_at: float
    observations: tuple[RuntimeObservation, ...]
    source_counts: Mapping[str, int]
    latest_occurred_at: float
    material_revision: int
    material_count: int
    latest_material_occurred_at: float | None
    held_since: float | None
    release_reason: str | None
    material_by_observation_id: Mapping[str, ObservationMaterial]

    @classmethod
    def create(
        cls,
        *,
        runtime_key: PersonalRuntimeKey,
        opened_at: float,
        closed_at: float,
        observations: Sequence[RuntimeObservation],
        material_by_observation_id: Mapping[str, ObservationMaterial] | None = None,
        held_since: float | None = None,
        release_reason: str | None = None,
    ) -> ObservationBatch:
        items = tuple(observations)
        if not items:
            raise ValueError("ObservationBatch requires at least one observation")
        source_counts = MappingProxyType(dict(Counter(item.source for item in items)))
        material = {
            observation_id: item
            for observation_id, item in (material_by_observation_id or {}).items()
            if observation_id in {observation.observation_id for observation in items}
            and item.revision > 0
        }
        normalized_held_since = (
            min(float(held_since), float(closed_at))
            if held_since is not None
            else None
        )
        return cls(
            batch_id=uuid.uuid4().hex,
            runtime_key=runtime_key,
            opened_at=float(opened_at),
            closed_at=float(closed_at),
            observations=items,
            source_counts=source_counts,
            latest_occurred_at=max(item.occurred_at for item in items),
            material_revision=max(
                (item.revision for item in material.values()),
                default=0,
            ),
            material_count=len(material),
            latest_material_occurred_at=max(
                (item.occurred_at for item in material.values()),
                default=None,
            ),
            held_since=normalized_held_since,
            release_reason=str(release_reason or "").strip() or None,
            material_by_observation_id=MappingProxyType(material),
        )

    @property
    def held_duration_seconds(self) -> float:
        if self.held_since is None:
            return 0.0
        return max(0.0, self.closed_at - self.held_since)


@dataclass(frozen=True, slots=True)
class _InboxItem:
    observation: RuntimeObservation
    material: ObservationMaterial | None = None


class ObservationInbox:
    """Bounded, coalescing observation storage owned by one Runtime."""

    def __init__(self, *, max_pending: int) -> None:
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        self._max_pending = int(max_pending)
        self._items: OrderedDict[str, _InboxItem] = OrderedDict()
        self._coalesced_ids: dict[tuple[str, str, str], str] = {}
        self._opened_at: float | None = None
        self._held_since: float | None = None
        self._release_reason: str | None = None
        self._discarded_material_revision = 0
        self.overflow_drop_count = 0
        self.expired_drop_count = 0

    @property
    def pending_count(self) -> int:
        return len(self._items)

    @property
    def pending_material_count(self) -> int:
        return sum(item.material is not None for item in self._items.values())

    def admit(
        self,
        observation: RuntimeObservation,
        *,
        runtime_key: PersonalRuntimeKey,
        now: float,
        material_revision: int | None = None,
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
        material = (
            ObservationMaterial(
                revision=max(1, int(material_revision)),
                occurred_at=observation.occurred_at,
            )
            if material_revision is not None
            else None
        )
        if replaced_id is not None:
            replaced = self._remove(replaced_id)
            dropped_ids.append(replaced_id)
            reason_codes.append(
                "inbox_duplicate_replaced"
                if replaced_id == observation.observation_id
                else "inbox_coalesced_replaced"
            )
            status = ObservationAdmissionStatus.COALESCED
            if replaced is not None and replaced.material is not None:
                if material is None:
                    material = replaced.material
                elif material.revision != replaced.material.revision:
                    self._discard_material(replaced.material)

        if self.pending_count >= self._max_pending:
            oldest_id = next(iter(self._items))
            dropped = self._remove(oldest_id)
            if dropped is not None:
                self._discard_material(dropped.material)
            self.overflow_drop_count += 1
            dropped_ids.append(oldest_id)
            reason_codes.append("inbox_overflow_drop_oldest")

        if not self._items:
            self._opened_at = now
        self._items[observation.observation_id] = _InboxItem(
            observation=observation,
            material=material,
        )
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
        items = tuple(self._items.values())
        observations = tuple(item.observation for item in items)
        opened_at = self._opened_at if self._opened_at is not None else closed_at
        held_since = self._held_since
        release_reason = self._release_reason
        self._items.clear()
        self._coalesced_ids.clear()
        self._opened_at = None
        self._held_since = None
        self._release_reason = None
        return ObservationBatch.create(
            runtime_key=runtime_key,
            opened_at=opened_at,
            closed_at=closed_at,
            observations=observations,
            material_by_observation_id={
                item.observation.observation_id: item.material
                for item in items
                if item.material is not None
            },
            held_since=held_since,
            release_reason=release_reason,
        )

    def restore(self, batch: ObservationBatch, *, hold_reason: str) -> None:
        """Restore held facts while retaining newer observations admitted meanwhile."""
        restored = OrderedDict(
            (
                observation.observation_id,
                _InboxItem(
                    observation=observation,
                    material=batch.material_by_observation_id.get(
                        observation.observation_id
                    ),
                ),
            )
            for observation in batch.observations
        )
        for observation_id, item in self._items.items():
            replaced = restored.pop(observation_id, None)
            item = self._merge_replacement_material(replaced, item)
            observation = item.observation
            if observation.coalesce_identity is not None:
                stale_ids = [
                    item_id
                    for item_id, restored_item in restored.items()
                    if restored_item.observation.coalesce_identity
                    == observation.coalesce_identity
                ]
                for stale_id in stale_ids:
                    stale = restored.pop(stale_id)
                    item = self._merge_replacement_material(stale, item)
            restored[observation_id] = item

        overflow = max(0, len(restored) - self._max_pending)
        for _ in range(overflow):
            _, dropped = restored.popitem(last=False)
            self._discard_material(dropped.material)
        self.overflow_drop_count += overflow
        self._items = restored
        self._coalesced_ids = {
            observation.coalesce_identity: observation_id
            for observation_id, item in restored.items()
            if (observation := item.observation).coalesce_identity is not None
        }
        if restored:
            opened_at = self._opened_at
            self._opened_at = min(
                value
                for value in (batch.opened_at, opened_at)
                if value is not None
            )
            self._held_since = batch.held_since or batch.closed_at
            self._release_reason = str(hold_reason or "").strip() or None
        else:
            self._opened_at = None
            self._held_since = None
            self._release_reason = None

    def clear(self) -> None:
        self._items.clear()
        self._coalesced_ids.clear()
        self._opened_at = None
        self._held_since = None
        self._release_reason = None
        self._discarded_material_revision = 0

    def take_discarded_material_revision(self) -> int:
        revision = self._discarded_material_revision
        self._discarded_material_revision = 0
        return revision

    def _remove_expired(self, *, now: float) -> list[str]:
        expired_ids = [
            observation_id
            for observation_id, item in self._items.items()
            if item.observation.expires_at is not None
            and item.observation.expires_at <= now
        ]
        for observation_id in expired_ids:
            removed = self._remove(observation_id)
            if removed is not None:
                self._discard_material(removed.material)
        self.expired_drop_count += len(expired_ids)
        if not self._items:
            self._opened_at = None
            self._held_since = None
            self._release_reason = None
        return expired_ids

    def _merge_replacement_material(
        self,
        replaced: _InboxItem | None,
        item: _InboxItem,
    ) -> _InboxItem:
        if replaced is None or replaced.material is None:
            return item
        if item.material is None:
            return _InboxItem(observation=item.observation, material=replaced.material)
        if item.material.revision != replaced.material.revision:
            self._discard_material(replaced.material)
        return item

    def _discard_material(self, material: ObservationMaterial | None) -> None:
        if material is not None:
            self._discarded_material_revision = max(
                self._discarded_material_revision,
                material.revision,
            )

    def _remove(self, observation_id: str) -> _InboxItem | None:
        item = self._items.pop(observation_id, None)
        if item is None or item.observation.coalesce_identity is None:
            return item
        if self._coalesced_ids.get(item.observation.coalesce_identity) == observation_id:
            self._coalesced_ids.pop(item.observation.coalesce_identity, None)
        return item


__all__ = [
    "ObservationAdmissionResult",
    "ObservationAdmissionStatus",
    "ObservationBatch",
    "ObservationInbox",
    "ObservationMaterial",
]
