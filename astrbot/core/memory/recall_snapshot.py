from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass

from astrbot.core import logger

from .config import MemoryRecallConfig
from .fingerprint import build_short_term_fingerprint
from .scope_context import MemoryScopeContext
from .snapshot_builder import MemorySnapshotBuilder, MemorySnapshotReadOptions
from .types import MemoryRecallSnapshot, MemorySnapshot, ScopeType


@dataclass(frozen=True, slots=True)
class _RecallProfile:
    experiences_top_k: int
    long_term_top_k: int
    query_required: bool


@dataclass(frozen=True, slots=True)
class _RecallFamilyKey:
    umo: str
    conversation_id: str | None
    canonical_user_id: str
    scopes: tuple[tuple[str, str], ...]
    profile: _RecallProfile


@dataclass(frozen=True, slots=True)
class _RecallKey:
    family: _RecallFamilyKey
    short_term_revision: int
    short_term_fingerprint: str


@dataclass(slots=True)
class _RecallEntry:
    snapshot: MemoryRecallSnapshot
    refreshed_at: float


class RecallSnapshotManager:
    """Serve stale recall data while refreshing one version in the background."""

    def __init__(
        self,
        builder: MemorySnapshotBuilder,
        config: MemoryRecallConfig,
    ) -> None:
        self.builder = builder
        self.config = config
        self._entries: OrderedDict[_RecallKey, _RecallEntry] = OrderedDict()
        self._latest_by_family: dict[_RecallFamilyKey, _RecallKey] = {}
        self._refresh_tasks: dict[_RecallKey, asyncio.Task[None]] = {}
        self._closed = False

    def get_or_schedule(
        self,
        snapshot: MemorySnapshot,
        *,
        scope_context: MemoryScopeContext | None,
        read_options: MemorySnapshotReadOptions,
    ) -> MemoryRecallSnapshot | None:
        if self._closed or not self.config.enabled:
            return None
        if snapshot.canonical_user_id is None or snapshot.short_term_memory is None:
            return None

        profile = _RecallProfile(
            experiences_top_k=max(0, min(read_options.experiences.top_k, 10)),
            long_term_top_k=max(0, min(read_options.long_term.top_k, 10)),
            query_required=read_options.long_term.query_required,
        )
        if profile.experiences_top_k <= 0 and profile.long_term_top_k <= 0:
            return None

        short_term = snapshot.short_term_memory
        query = _build_recall_query(short_term)
        if profile.query_required and not query:
            return None
        fingerprint = short_term.fingerprint or build_short_term_fingerprint(
            short_term.short_summary,
            short_term.active_focus,
        )
        scopes = self._scope_key(scope_context, snapshot.canonical_user_id)
        family = _RecallFamilyKey(
            umo=snapshot.umo,
            conversation_id=snapshot.conversation_id,
            canonical_user_id=snapshot.canonical_user_id,
            scopes=scopes,
            profile=profile,
        )
        key = _RecallKey(
            family=family,
            short_term_revision=max(0, int(short_term.revision)),
            short_term_fingerprint=fingerprint,
        )
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
            if not self._is_expired(entry):
                return entry.snapshot

        stale_key = self._latest_by_family.get(family)
        stale_entry = self._entries.get(stale_key) if stale_key is not None else None
        self._schedule_refresh(
            key,
            snapshot=snapshot,
            query=query,
            read_options=read_options,
        )
        return stale_entry.snapshot if stale_entry is not None else None

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._refresh_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._refresh_tasks.clear()

    def _schedule_refresh(
        self,
        key: _RecallKey,
        *,
        snapshot: MemorySnapshot,
        query: str | None,
        read_options: MemorySnapshotReadOptions,
    ) -> None:
        if key in self._refresh_tasks:
            return
        task = asyncio.create_task(
            self._refresh(key, snapshot, query, read_options),
            name="memory-recall-refresh",
        )
        self._refresh_tasks[key] = task

    async def _refresh(
        self,
        key: _RecallKey,
        snapshot: MemorySnapshot,
        query: str | None,
        read_options: MemorySnapshotReadOptions,
    ) -> None:
        try:
            if read_options.long_term.query_required and not query:
                return
            recalled = await self.builder.build_recall_snapshot(
                snapshot,
                query=query,
                read_options=read_options,
            )
            if self._closed:
                return
            self._entries[key] = _RecallEntry(recalled, time.monotonic())
            self._entries.move_to_end(key)
            self._latest_by_family[key.family] = key
            self._prune()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "memory recall refresh failed: umo=%s conversation_id=%s error=%s",
                key.family.umo,
                key.family.conversation_id,
                exc,
            )
        finally:
            self._refresh_tasks.pop(key, None)

    def _is_expired(self, entry: _RecallEntry) -> bool:
        interval = max(0.0, float(self.config.refresh_interval_seconds))
        return time.monotonic() - entry.refreshed_at >= interval

    def _prune(self) -> None:
        limit = max(1, int(self.config.max_entries))
        while len(self._entries) > limit:
            key, _ = self._entries.popitem(last=False)
            if self._latest_by_family.get(key.family) == key:
                self._latest_by_family.pop(key.family, None)

    @staticmethod
    def _scope_key(
        scope_context: MemoryScopeContext | None,
        canonical_user_id: str,
    ) -> tuple[tuple[str, str], ...]:
        if scope_context is None:
            return ((ScopeType.USER.value, canonical_user_id),)
        return tuple(
            sorted(
                (
                    str(ref.scope_type.value if hasattr(ref.scope_type, "value") else ref.scope_type),
                    ref.scope_id,
                )
                for ref in scope_context.recall_refs()
            )
        )


def _build_recall_query(short_term) -> str | None:
    parts = [short_term.short_summary, short_term.active_focus]
    query = " ".join(str(part).strip() for part in parts if part and str(part).strip())
    return query or None


__all__ = ["RecallSnapshotManager"]
