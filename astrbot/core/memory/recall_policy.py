from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import MemoryRecallConfig
from .scope_context import MemoryScopeContext
from .types import Experience, LongTermMemoryIndex, ScopeRef, ScopeType


@dataclass(frozen=True, slots=True)
class ScopedMemoryCandidate:
    memory: LongTermMemoryIndex
    scope_rank: int
    score: float | None


class ScopedRecallPolicy:
    """Own scope selection, shared-owner reads, and cross-scope conflict rules."""

    def __init__(self, config: MemoryRecallConfig) -> None:
        self.config = config

    def resolve_scopes(
        self,
        canonical_user_id: str,
        scope_context: MemoryScopeContext | None,
    ) -> tuple[ScopeRef, ...]:
        if scope_context is None:
            return (ScopeRef(ScopeType.USER, canonical_user_id),)
        available = {
            self._enum_value(ref.scope_type): ref
            for ref in scope_context.recall_refs()
        }
        return tuple(
            available[scope_type]
            for scope_type in self.config.scope_priority
            if scope_type in available
        )

    def cache_scope_key(
        self,
        scope_context: MemoryScopeContext | None,
        canonical_user_id: str,
    ) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted(
                (self._enum_value(ref.scope_type), ref.scope_id)
                for ref in self.resolve_scopes(canonical_user_id, scope_context)
            )
        )

    def canonical_user_id_for_scope(
        self,
        scope: ScopeRef,
        canonical_user_id: str,
    ) -> str | None:
        return (
            canonical_user_id
            if self._enum_value(scope.scope_type) == ScopeType.USER.value
            else None
        )

    def merge_long_term_candidates(
        self,
        candidates: list[ScopedMemoryCandidate],
        *,
        limit: int,
        query_present: bool,
    ) -> list[LongTermMemoryIndex]:
        ranked = sorted(candidates, key=self._candidate_selection_key)
        selected: list[ScopedMemoryCandidate] = []
        seen_memory_ids: set[str] = set()
        seen_conflicts: set[tuple[str, str]] = set()
        for candidate in ranked:
            memory = candidate.memory
            if memory.memory_id in seen_memory_ids:
                continue
            conflict_key = self._memory_conflict_key(memory)
            if self.config.deduplicate_across_scopes and conflict_key in seen_conflicts:
                continue
            seen_memory_ids.add(memory.memory_id)
            seen_conflicts.add(conflict_key)
            selected.append(candidate)

        if query_present:
            selected.sort(key=self._candidate_query_order_key)
        else:
            selected.sort(key=self._candidate_recent_order_key)
        return [candidate.memory for candidate in selected[:limit]]

    def merge_experiences(
        self,
        experiences: list[Experience],
        *,
        scopes: tuple[ScopeRef, ...],
        limit: int,
        preferred_ids: list[str] | None = None,
    ) -> list[Experience]:
        scope_ranks = {
            (self._enum_value(scope.scope_type), scope.scope_id): rank
            for rank, scope in enumerate(scopes)
        }
        preferred_ranks = {
            experience_id: rank
            for rank, experience_id in enumerate(preferred_ids or [])
        }
        ranked = sorted(
            experiences,
            key=lambda item: (
                scope_ranks.get(
                    (self._enum_value(item.scope_type), item.scope_id),
                    len(scopes),
                ),
                preferred_ranks.get(item.experience_id, len(preferred_ranks)),
                -self._datetime_rank(item.event_time),
                item.experience_id,
            ),
        )
        selected: list[Experience] = []
        seen_ids: set[str] = set()
        seen_conflicts: set[tuple[str, str]] = set()
        for experience in ranked:
            if experience.experience_id in seen_ids:
                continue
            conflict_key = (
                self._enum_value(experience.category).casefold(),
                self._normalize_semantic_key(experience.summary),
            )
            if self.config.deduplicate_across_scopes and conflict_key in seen_conflicts:
                continue
            seen_ids.add(experience.experience_id)
            seen_conflicts.add(conflict_key)
            selected.append(experience)
        selected.sort(
            key=lambda item: (
                0 if item.experience_id in preferred_ranks else 1,
                preferred_ranks.get(item.experience_id, len(preferred_ranks)),
                scope_ranks.get(
                    (self._enum_value(item.scope_type), item.scope_id),
                    len(scopes),
                ),
                -self._datetime_rank(item.event_time),
                item.experience_id,
            )
        )
        return selected[:limit]

    def _candidate_selection_key(
        self,
        candidate: ScopedMemoryCandidate,
    ) -> tuple[int, float, float, float, str]:
        return (
            candidate.scope_rank,
            -float(candidate.score or 0.0),
            -float(candidate.memory.importance),
            -self._datetime_rank(candidate.memory.updated_at),
            candidate.memory.memory_id,
        )

    def _candidate_query_order_key(
        self,
        candidate: ScopedMemoryCandidate,
    ) -> tuple[float, int, float, float, str]:
        return (
            -float(candidate.score or 0.0),
            candidate.scope_rank,
            -float(candidate.memory.importance),
            -self._datetime_rank(candidate.memory.updated_at),
            candidate.memory.memory_id,
        )

    def _candidate_recent_order_key(
        self,
        candidate: ScopedMemoryCandidate,
    ) -> tuple[int, float, float, str]:
        return (
            candidate.scope_rank,
            -self._datetime_rank(candidate.memory.updated_at),
            -float(candidate.memory.importance),
            candidate.memory.memory_id,
        )

    def _memory_conflict_key(
        self,
        memory: LongTermMemoryIndex,
    ) -> tuple[str, str]:
        return (
            self._enum_value(memory.category).casefold(),
            self._normalize_semantic_key(memory.title or memory.summary),
        )

    @staticmethod
    def _normalize_semantic_key(value: str) -> str:
        return " ".join(str(value or "").casefold().split())

    @staticmethod
    def _datetime_rank(value: datetime | None) -> float:
        return value.timestamp() if value is not None else 0.0

    @staticmethod
    def _enum_value(value: object) -> str:
        return value.value if hasattr(value, "value") else str(value)


__all__ = ["ScopedMemoryCandidate", "ScopedRecallPolicy"]
