from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from astrbot.core import logger

from .config import (
    MemoryConfig,
    MemoryInjectionConfig,
    MemoryInjectionListConfig,
    MemoryLongTermInjectionConfig,
    get_memory_config,
)
from .document_search import DocumentSearchService
from .store import MemoryStore
from .types import (
    DocumentSearchRequest,
    Experience,
    LongTermMemoryIndex,
    MemoryIdentity,
    MemoryRecallSnapshot,
    MemorySnapshot,
    ScopeType,
)

SNAPSHOT_EXPERIENCE_LIMIT = 10
SNAPSHOT_LONG_TERM_LIMIT = 10


@dataclass(slots=True)
class MemorySnapshotReadOptions:
    enabled: bool = True
    experiences: MemoryInjectionListConfig = field(
        default_factory=lambda: MemoryInjectionListConfig(
            enabled=True,
            top_k=SNAPSHOT_EXPERIENCE_LIMIT,
        )
    )
    long_term: MemoryLongTermInjectionConfig = field(
        default_factory=lambda: MemoryLongTermInjectionConfig(
            enabled=True,
            top_k=SNAPSHOT_LONG_TERM_LIMIT,
            query_required=False,
        )
    )
    persona_state: bool = True


def memory_injection_to_snapshot_read_options(
    injection: MemoryInjectionConfig,
) -> MemorySnapshotReadOptions:
    return MemorySnapshotReadOptions(
        enabled=injection.enabled,
        experiences=MemoryInjectionListConfig(
            enabled=injection.experiences.enabled,
            top_k=injection.experiences.top_k,
        ),
        long_term=MemoryLongTermInjectionConfig(
            enabled=injection.long_term.enabled,
            top_k=injection.long_term.top_k,
            query_required=injection.long_term.query_required,
        ),
        persona_state=injection.persona_state,
    )


class MemorySnapshotBuilder:
    def __init__(
        self,
        store: MemoryStore,
        document_search_service: DocumentSearchService | None = None,
        config: MemoryConfig | None = None,
    ) -> None:
        self.store = store
        self.document_search_service = document_search_service
        self.config = config or getattr(store, "config", None) or get_memory_config()

    async def build_snapshot(
        self,
        umo: str,
        conversation_id: str | None,
        query: str | None = None,
        read_options: MemorySnapshotReadOptions | None = None,
        identity: MemoryIdentity | None = None,
    ) -> MemorySnapshot:
        options = read_options or MemorySnapshotReadOptions()
        snapshot = await self.build_local_snapshot(
            umo,
            conversation_id,
            read_options=options,
            identity=identity,
        )
        recall = await self.build_recall_snapshot(
            snapshot,
            query=query,
            read_options=options,
        )
        snapshot.experiences = recall.experiences
        snapshot.long_term_memories = recall.long_term_memories
        snapshot.debug_meta.update(recall.debug_meta)
        if query is not None:
            snapshot.debug_meta["query"] = query
        return snapshot

    async def build_local_snapshot(
        self,
        umo: str,
        conversation_id: str | None,
        *,
        read_options: MemorySnapshotReadOptions,
        identity: MemoryIdentity | None = None,
    ) -> MemorySnapshot:
        local_reads = [
            self.store.get_topic_state(umo, conversation_id),
            self.store.get_short_term_memory(umo, conversation_id),
        ]
        # Identity resolution already has the only data needed from the latest
        # turn, so normal collector calls do not need this fallback query.
        if identity is None:
            local_reads.append(
                self.store.get_recent_turn_records(
                    umo,
                    limit=1,
                    conversation_id=conversation_id,
                )
            )
        local_results = await asyncio.gather(*local_reads)
        topic_state = local_results[0]
        short_term_memory = local_results[1]
        recent_turns = local_results[2] if identity is None else []
        latest_turn = recent_turns[0] if recent_turns else None
        if identity is not None:
            canonical_user_id = identity.canonical_user_id
            platform_user_key = identity.platform_user_key
        else:
            canonical_user_id = latest_turn.canonical_user_id if latest_turn else None
            platform_user_key = latest_turn.platform_user_key if latest_turn else None
        persona_state = None
        if canonical_user_id:
            if read_options.enabled and read_options.persona_state:
                persona_state = await self.store.get_persona_state(
                    ScopeType.USER,
                    canonical_user_id,
                )
        logger.info(
            "memory local snapshot built: umo=%s conversation_id=%s topic_state=%s short_term_memory=%s canonical_user_id=%s persona_state=%s",
            umo,
            conversation_id,
            topic_state is not None,
            short_term_memory is not None,
            canonical_user_id,
            persona_state is not None,
        )
        return MemorySnapshot(
            umo=umo,
            conversation_id=conversation_id,
            platform_user_key=platform_user_key,
            canonical_user_id=canonical_user_id,
            topic_state=topic_state,
            short_term_memory=short_term_memory,
            experiences=[],
            long_term_memories=[],
            persona_state=persona_state,
            debug_meta={},
        )

    async def build_recall_snapshot(
        self,
        snapshot: MemorySnapshot,
        *,
        query: str | None,
        read_options: MemorySnapshotReadOptions,
    ) -> MemoryRecallSnapshot:
        options = read_options
        if not snapshot.canonical_user_id or not options.enabled:
            return MemoryRecallSnapshot()

        long_term_memories: list[LongTermMemoryIndex] = []
        degraded_components: list[dict[str, str]] = []
        if options.long_term.enabled:
            try:
                long_term_memories = await self._load_snapshot_long_term_memories(
                    umo=snapshot.umo,
                    canonical_user_id=snapshot.canonical_user_id,
                    conversation_id=snapshot.conversation_id,
                    query=query,
                    read_options=options,
                )
            except Exception as exc:  # noqa: BLE001
                degraded_components.append(
                    {
                        "component": "long_term_retrieval",
                        "error_type": type(exc).__name__,
                        "reason": str(exc),
                    }
                )
                logger.warning(
                    "memory long-term retrieval failed; continuing with local snapshot: umo=%s conversation_id=%s error=%s",
                    snapshot.umo,
                    snapshot.conversation_id,
                    exc,
                    exc_info=True,
                )

        experiences: list[Experience] = []
        if options.experiences.enabled:
            experiences = await self._load_snapshot_experiences(
                canonical_user_id=snapshot.canonical_user_id,
                conversation_id=snapshot.conversation_id,
                query=query,
                long_term_memories=long_term_memories,
                read_options=options,
            )
        return MemoryRecallSnapshot(
            experiences=experiences,
            long_term_memories=long_term_memories,
            debug_meta=(
                {"degraded_components": degraded_components}
                if degraded_components
                else {}
            ),
        )

    async def _load_snapshot_long_term_memories(
        self,
        *,
        umo: str,
        canonical_user_id: str,
        conversation_id: str | None,
        query: str | None,
        read_options: MemorySnapshotReadOptions,
    ) -> list[LongTermMemoryIndex]:
        injection = read_options.long_term
        limit = max(0, min(injection.top_k, SNAPSHOT_LONG_TERM_LIMIT))
        if limit <= 0:
            return []
        if injection.query_required and not query:
            return []

        if query and self.document_search_service is not None:
            results = await self.document_search_service.search_long_term_memories(
                DocumentSearchRequest(
                    canonical_user_id=canonical_user_id,
                    query=query,
                    umo=umo,
                    conversation_id=conversation_id,
                    scope_type=ScopeType.USER,
                    scope_id=canonical_user_id,
                    top_k=limit,
                )
            )
            memories: list[LongTermMemoryIndex] = []
            for item in results:
                memory = await self.store.get_long_term_memory_index(item.memory_id)
                if memory is not None:
                    memories.append(memory)
            return memories

        return await self.store.list_long_term_memory_indexes(
            canonical_user_id,
            limit,
            scope_type=ScopeType.USER,
            scope_id=canonical_user_id,
        )

    async def _load_snapshot_experiences(
        self,
        *,
        canonical_user_id: str,
        conversation_id: str | None,
        query: str | None,
        long_term_memories: list[LongTermMemoryIndex],
        read_options: MemorySnapshotReadOptions,
    ) -> list[Experience]:
        limit = max(0, min(read_options.experiences.top_k, SNAPSHOT_EXPERIENCE_LIMIT))
        if limit <= 0:
            return []

        recent_experiences = await self.store.list_recent_experiences(
            canonical_user_id,
            limit,
            conversation_id=conversation_id,
        )
        if not query or not long_term_memories:
            return recent_experiences

        experiences: list[Experience] = []
        seen_experience_ids: set[str] = set()
        for memory in long_term_memories:
            links = await self.store.list_long_term_memory_links(memory.memory_id)
            for link in reversed(links):
                if link.experience_id in seen_experience_ids:
                    continue
                experience = await self.store.get_experience(link.experience_id)
                if experience is None:
                    continue
                seen_experience_ids.add(experience.experience_id)
                experiences.append(experience)
                if len(experiences) >= limit:
                    return experiences

        for experience in recent_experiences:
            if experience.experience_id in seen_experience_ids:
                continue
            seen_experience_ids.add(experience.experience_id)
            experiences.append(experience)
            if len(experiences) >= limit:
                break
        return experiences
