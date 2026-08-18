from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from astrbot.core import logger

from .config import MemoryRecallConfig
from .fingerprint import build_short_term_fingerprint
from .job_scheduler import MemoryScopeJob
from .recall_policy import ScopedRecallPolicy
from .scope_context import MemoryScopeContext
from .snapshot_builder import MemorySnapshotBuilder, MemorySnapshotReadOptions
from .types import MemoryRecallSnapshot, MemorySnapshot


@dataclass(frozen=True, slots=True)
class _RecallProfile:
    experiences_top_k: int
    long_term_top_k: int
    query_required: bool
    scope_priority: tuple[str, ...]
    deduplicate_across_scopes: bool


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


@dataclass(frozen=True, slots=True)
class _RecallRefreshJob:
    key: _RecallKey
    snapshot: MemorySnapshot
    query: str | None
    read_options: MemorySnapshotReadOptions
    scope_context: MemoryScopeContext | None


class RecallSnapshotManager:
    """Serve stale recall data while refreshing one version in the background."""

    def __init__(
        self,
        builder: MemorySnapshotBuilder,
        config: MemoryRecallConfig,
        submit_job: Callable[[MemoryScopeJob], Awaitable[bool]] | None = None,
    ) -> None:
        self.builder = builder
        self.config = config
        self.scope_policy = ScopedRecallPolicy(config)
        self._entries: OrderedDict[_RecallKey, _RecallEntry] = OrderedDict()
        self._latest_by_family: dict[_RecallFamilyKey, _RecallKey] = {}
        self._scheduled_keys: set[_RecallKey] = set()
        self._detached_tasks: set[asyncio.Task[None]] = set()
        self._submit_job = submit_job
        self._closed = False

    async def get_or_schedule(
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
            scope_priority=tuple(self.config.scope_priority),
            deduplicate_across_scopes=self.config.deduplicate_across_scopes,
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
        scopes = self.scope_policy.cache_scope_key(
            scope_context,
            snapshot.canonical_user_id,
        )
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
        await self._schedule_refresh(
            key,
            snapshot=snapshot,
            query=query,
            read_options=read_options,
            scope_context=scope_context,
        )
        return stale_entry.snapshot if stale_entry is not None else None

    async def close(self) -> None:
        self._closed = True
        self._scheduled_keys.clear()
        tasks = list(self._detached_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._detached_tasks.clear()

    async def _schedule_refresh(
        self,
        key: _RecallKey,
        *,
        snapshot: MemorySnapshot,
        query: str | None,
        read_options: MemorySnapshotReadOptions,
        scope_context: MemoryScopeContext | None,
    ) -> None:
        if key in self._scheduled_keys:
            return
        self._scheduled_keys.add(key)
        job = MemoryScopeJob(
            owner_id=key.family.canonical_user_id,
            scope_type="recall_refresh",
            scope_id=_key_digest(key.family),
            conversation_id=key.family.conversation_id,
            umo=key.family.umo,
            kind="recall_refresh",
            dedupe_key=_key_digest(key),
            payload=_RecallRefreshJob(
                key=key,
                snapshot=snapshot,
                query=query,
                read_options=read_options,
                scope_context=scope_context,
            ),
        )
        if self._submit_job is None:
            task = asyncio.create_task(
                self._run_detached_refresh(job.payload),
                name="memory-recall-refresh",
            )
            self._detached_tasks.add(task)
            task.add_done_callback(self._detached_tasks.discard)
            return
        if not await self._submit_job(job):
            self._scheduled_keys.discard(key)

    async def run_refresh_job(self, payload: object | None) -> None:
        if not isinstance(payload, _RecallRefreshJob):
            raise TypeError("memory recall refresh job payload is invalid")
        key = payload.key
        try:
            if payload.read_options.long_term.query_required and not payload.query:
                return
            recalled = await self.builder.build_recall_snapshot(
                payload.snapshot,
                query=payload.query,
                read_options=payload.read_options,
                scope_context=payload.scope_context,
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
            raise
        finally:
            self._scheduled_keys.discard(key)

    async def _run_detached_refresh(self, payload: object | None) -> None:
        try:
            await self.run_refresh_job(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    def _is_expired(self, entry: _RecallEntry) -> bool:
        interval = max(0.0, float(self.config.refresh_interval_seconds))
        return time.monotonic() - entry.refreshed_at >= interval

    def _prune(self) -> None:
        limit = max(1, int(self.config.max_entries))
        while len(self._entries) > limit:
            key, _ = self._entries.popitem(last=False)
            if self._latest_by_family.get(key.family) == key:
                self._latest_by_family.pop(key.family, None)

def _build_recall_query(short_term) -> str | None:
    parts = [short_term.short_summary, short_term.active_focus]
    query = " ".join(str(part).strip() for part in parts if part and str(part).strip())
    return query or None


def _key_digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


__all__ = ["RecallSnapshotManager"]
