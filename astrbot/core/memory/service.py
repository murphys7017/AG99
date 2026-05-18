from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from astrbot.core import logger

from .analyzer_manager import MemoryAnalyzerManager
from .config import MemoryConfig, get_memory_config
from .consolidation_service import ConsolidationService
from .document_search import DocumentSearchService
from .experience_service import ExperienceService
from .history_source import RecentConversationSource
from .identity import MemoryIdentityMappingService, MemoryIdentityResolver
from .long_term_service import LongTermMemoryService
from .manual_service import LongTermMemoryManualService
from .short_term_service import ShortTermMemoryService
from .snapshot_builder import MemorySnapshotBuilder, MemorySnapshotReadOptions
from .store import MemoryStore
from .turn_record_service import TurnRecordService
from .types import (
    DocumentSearchRequest,
    DocumentSearchResult,
    Experience,
    LongTermMemoryIndex,
    LongTermVectorSyncStatus,
    MemoryIdentityBinding,
    MemorySnapshot,
    MemoryUpdateRequest,
    SessionInsight,
    TurnRecord,
)
from .vector_index import MemoryVectorIndex


class MemoryService:
    def __init__(
        self,
        store: MemoryStore,
        turn_record_service: TurnRecordService,
        short_term_service: ShortTermMemoryService,
        snapshot_builder: MemorySnapshotBuilder,
        analyzer_manager: MemoryAnalyzerManager | None = None,
        identity_mapping_service: MemoryIdentityMappingService | None = None,
        identity_resolver: MemoryIdentityResolver | None = None,
        consolidation_service: ConsolidationService | None = None,
        experience_service: ExperienceService | None = None,
        long_term_service: LongTermMemoryService | None = None,
        manual_long_term_service: LongTermMemoryManualService | None = None,
        document_search_service: DocumentSearchService | None = None,
    ) -> None:
        self.store = store
        self.turn_record_service = turn_record_service
        self.short_term_service = short_term_service
        self.snapshot_builder = snapshot_builder
        self.analyzer_manager = analyzer_manager or MemoryAnalyzerManager()
        self.identity_mapping_service = identity_mapping_service
        self.identity_resolver = identity_resolver
        self.consolidation_service = consolidation_service
        self.experience_service = experience_service
        self.long_term_service = long_term_service
        self.manual_long_term_service = manual_long_term_service
        self.document_search_service = document_search_service
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    def bind_provider_manager(self, provider_manager) -> None:
        self.analyzer_manager.bind_provider_manager(provider_manager)
        if self.long_term_service is not None:
            self.long_term_service.bind_provider_manager(provider_manager)
        if self.manual_long_term_service is not None:
            self.manual_long_term_service.bind_provider_manager(provider_manager)

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            if self.identity_mapping_service is not None:
                count = await self.identity_mapping_service.reload_from_yaml()
                logger.info(
                    "memory identity mappings synchronized: count=%s",
                    count,
                )
            self._initialized = True

    async def update_from_postprocess(self, req: MemoryUpdateRequest) -> TurnRecord:
        await self.initialize()
        logger.info(
            "memory update started: umo=%s conversation_id=%s source_refs=%s",
            req.umo,
            req.conversation_id,
            req.source_refs,
        )
        turn = await self.turn_record_service.ingest_turn(req)
        conversation_history = _get_conversation_history(req.provider_request)
        await self.short_term_service.update_after_turn(
            turn,
            conversation_history=conversation_history,
        )
        if not turn.canonical_user_id:
            logger.warning(
                "memory update skipped mid-long pipeline: missing canonical_user_id turn_id=%s umo=%s platform_user_key=%s",
                turn.turn_id,
                turn.umo,
                turn.platform_user_key,
            )
            logger.info(
                "memory update finished: turn_id=%s umo=%s conversation_id=%s",
                turn.turn_id,
                turn.umo,
                turn.conversation_id,
            )
            return turn
        if (
            self.consolidation_service is not None
            and await self.consolidation_service.should_run_consolidation(
                turn.canonical_user_id,
                turn.conversation_id,
            )
        ):
            logger.info(
                "memory consolidation triggered after update: umo=%s conversation_id=%s",
                turn.umo,
                turn.conversation_id,
            )
            _, experiences = await self.run_consolidation(
                turn.canonical_user_id,
                turn.conversation_id,
            )
            if (
                experiences
                and self.long_term_service is not None
                and await self.long_term_service.should_run_promotion(
                    turn.canonical_user_id,
                )
            ):
                logger.info(
                    "memory long-term promotion triggered after consolidation: canonical_user_id=%s conversation_id=%s",
                    turn.canonical_user_id,
                    turn.conversation_id,
                )
                await self.long_term_service.run_promotion(
                    turn.canonical_user_id,
                )
        logger.info(
            "memory update finished: turn_id=%s umo=%s conversation_id=%s",
            turn.turn_id,
            turn.umo,
            turn.conversation_id,
        )
        return turn

    async def get_snapshot(
        self,
        umo: str,
        conversation_id: str | None,
        query: str | None = None,
        read_options: MemorySnapshotReadOptions | None = None,
    ) -> MemorySnapshot:
        await self.initialize()
        logger.info(
            "memory snapshot requested: umo=%s conversation_id=%s query_present=%s",
            umo,
            conversation_id,
            query is not None,
        )
        return await self.snapshot_builder.build_snapshot(
            umo,
            conversation_id,
            query,
            read_options=read_options,
        )

    async def run_consolidation(
        self,
        canonical_user_id: str,
        conversation_id: str | None,
    ) -> tuple[SessionInsight | None, list[Experience]]:
        await self.initialize()
        if self.consolidation_service is None:
            logger.info(
                "memory consolidation skipped: service unavailable canonical_user_id=%s conversation_id=%s",
                canonical_user_id,
                conversation_id,
            )
            return None, []
        if self.experience_service is None:
            raise RuntimeError(
                "memory consolidation requested without experience service"
            )

        logger.info(
            "memory consolidation started: canonical_user_id=%s conversation_id=%s",
            canonical_user_id,
            conversation_id,
        )
        insight, experiences = await self.consolidation_service.run_for_scope(
            canonical_user_id,
            conversation_id,
        )
        (
            persisted_insight,
            persisted_experiences,
        ) = await self.store.persist_consolidation_batch(
            insight,
            experiences,
        )
        projection_paths: list[Path] = []
        try:
            projection_paths = (
                await self.experience_service.refresh_projections_for_experiences(
                    persisted_experiences
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "memory consolidation projection refresh failed: umo=%s conversation_id=%s error=%s",
                canonical_user_id,
                conversation_id,
                exc,
                exc_info=True,
            )
        logger.info(
            "memory consolidation finished: canonical_user_id=%s conversation_id=%s insight_created=%s experiences=%s projections=%s",
            canonical_user_id,
            conversation_id,
            persisted_insight is not None,
            len(persisted_experiences),
            len(projection_paths),
        )
        return persisted_insight, persisted_experiences

    async def bind_platform_user(
        self,
        platform_id: str,
        sender_user_id: str,
        canonical_user_id: str,
        nickname_hint: str | None = None,
    ) -> MemoryIdentityBinding:
        await self.initialize()
        if self.identity_mapping_service is None:
            raise RuntimeError("memory identity mapping service is unavailable")
        return await self.identity_mapping_service.bind_platform_user(
            platform_id,
            sender_user_id,
            canonical_user_id,
            nickname_hint=nickname_hint,
        )

    async def unbind_platform_user(self, platform_user_key: str) -> bool:
        await self.initialize()
        if self.identity_mapping_service is None:
            raise RuntimeError("memory identity mapping service is unavailable")
        return await self.identity_mapping_service.unbind_platform_user(
            platform_user_key
        )

    async def list_bindings_for_canonical_user(
        self,
        canonical_user_id: str,
    ) -> list[MemoryIdentityBinding]:
        await self.initialize()
        if self.identity_mapping_service is None:
            raise RuntimeError("memory identity mapping service is unavailable")
        return await self.identity_mapping_service.list_bindings_for_canonical_user(
            canonical_user_id
        )

    async def reload_identity_mappings(self) -> int:
        if self.identity_mapping_service is None:
            raise RuntimeError("memory identity mapping service is unavailable")
        count = await self.identity_mapping_service.reload_from_yaml()
        self._initialized = True
        logger.info("memory identity mappings reloaded: count=%s", count)
        return count

    async def search_long_term_memories(
        self,
        req: DocumentSearchRequest,
    ) -> list[DocumentSearchResult]:
        await self.initialize()
        if self.document_search_service is None:
            raise RuntimeError("memory document search service is unavailable")
        return await self.document_search_service.search_long_term_memories(req)

    async def import_long_term_memory_document(
        self,
        doc_path: Path | str,
    ) -> LongTermMemoryIndex:
        await self.initialize()
        if self.manual_long_term_service is None:
            raise RuntimeError("memory manual long-term service is unavailable")
        return await self.manual_long_term_service.upsert_memory_from_document(doc_path)

    async def refresh_long_term_vector_index(
        self,
        memory_id: str,
    ) -> LongTermMemoryIndex:
        await self.initialize()
        memory = await self.store.get_long_term_memory_index(memory_id)
        if memory is None:
            raise RuntimeError(f"long-term memory `{memory_id}` was not found")
        vector_index = self._get_vector_index()
        if vector_index is None or not self.store.config.vector_index.enabled:
            return await self.store.update_long_term_vector_sync_state(
                memory_id,
                status=LongTermVectorSyncStatus.READY,
                synced_at=None,
                error=None,
            )
        await vector_index.ensure_ready()
        try:
            await vector_index.upsert_long_term_memory(memory_id)
            return await self.store.update_long_term_vector_sync_state(
                memory_id,
                status=LongTermVectorSyncStatus.READY,
                synced_at=datetime.now(UTC),
                error=None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "memory long-term vector refresh failed: memory_id=%s error=%s",
                memory_id,
                exc,
                exc_info=True,
            )
            return await self.store.update_long_term_vector_sync_state(
                memory_id,
                status=LongTermVectorSyncStatus.DIRTY,
                synced_at=None,
                error=str(exc)[:500],
            )

    async def refresh_dirty_long_term_vector_indexes(
        self,
        *,
        limit: int = 100,
    ) -> list[LongTermMemoryIndex]:
        await self.initialize()
        dirty_memories = await self.store.list_long_term_memories_by_vector_status(
            LongTermVectorSyncStatus.DIRTY,
            limit=limit,
        )
        refreshed: list[LongTermMemoryIndex] = []
        for memory in dirty_memories:
            refreshed.append(
                await self.refresh_long_term_vector_index(memory.memory_id)
            )
        return refreshed

    def _get_vector_index(self) -> MemoryVectorIndex | None:
        if self.long_term_service is not None and self.long_term_service.vector_index:
            return self.long_term_service.vector_index
        if (
            self.manual_long_term_service is not None
            and self.manual_long_term_service.vector_index
        ):
            return self.manual_long_term_service.vector_index
        return None


_MEMORY_SERVICE: MemoryService | None = None
_MEMORY_SERVICES_BY_KEY: dict[str, MemoryService] = {}
_MEMORY_PROVIDER_MANAGER: Any | None = None


def _memory_service_key(config: object | None) -> str:
    if config is None:
        return "default"
    return str(id(config))


def _build_memory_service(config: MemoryConfig) -> MemoryService:
    store = MemoryStore(config=config)
    analyzer_manager = MemoryAnalyzerManager(config.analysis)
    identity_mapping_service = MemoryIdentityMappingService(store, config=config)
    identity_resolver = MemoryIdentityResolver(identity_mapping_service)
    history_source = RecentConversationSource(
        store,
        recent_turns_window=config.short_term.recent_turns_window,
    )
    turn_record_service = TurnRecordService(store)
    short_term_service = ShortTermMemoryService(
        store,
        history_source,
        analyzer_manager=analyzer_manager,
        analysis_config=config.analysis,
        short_term_config=config.short_term,
    )
    consolidation_service = ConsolidationService(
        store,
        analyzer_manager=analyzer_manager,
        analysis_config=config.analysis,
        consolidation_config=config.consolidation,
    )
    experience_service = ExperienceService(store)
    vector_index = MemoryVectorIndex(store, config=config)
    long_term_service = LongTermMemoryService(
        store,
        analyzer_manager=analyzer_manager,
        analysis_config=config.analysis,
        long_term_config=config.long_term,
        vector_index=vector_index,
    )
    manual_long_term_service = LongTermMemoryManualService(
        store,
        vector_index=vector_index,
    )
    document_search_service = DocumentSearchService(
        store,
        vector_index=vector_index,
    )
    snapshot_builder = MemorySnapshotBuilder(
        store,
        document_search_service=document_search_service,
        config=config,
    )
    service = MemoryService(
        store,
        turn_record_service,
        short_term_service,
        snapshot_builder,
        analyzer_manager,
        identity_mapping_service,
        identity_resolver,
        consolidation_service,
        experience_service,
        long_term_service,
        manual_long_term_service,
        document_search_service,
    )
    if _MEMORY_PROVIDER_MANAGER is not None:
        service.bind_provider_manager(_MEMORY_PROVIDER_MANAGER)
    return service


def bind_memory_provider_manager(provider_manager: Any) -> None:
    global _MEMORY_PROVIDER_MANAGER
    _MEMORY_PROVIDER_MANAGER = provider_manager
    if _MEMORY_SERVICE is not None:
        _MEMORY_SERVICE.bind_provider_manager(provider_manager)
    for service in _MEMORY_SERVICES_BY_KEY.values():
        service.bind_provider_manager(provider_manager)


def get_memory_service(config: Any | None = None) -> MemoryService:
    global _MEMORY_SERVICE
    if config is not None:
        key = _memory_service_key(config)
        cached_service = _MEMORY_SERVICES_BY_KEY.get(key)
        if cached_service is None:
            cached_service = _build_memory_service(get_memory_config(config))
            _MEMORY_SERVICES_BY_KEY[key] = cached_service
        return cached_service

    if _MEMORY_SERVICE is None:
        _MEMORY_SERVICE = _build_memory_service(get_memory_config())
    return _MEMORY_SERVICE


async def shutdown_memory_service(config: Any | None = None) -> None:
    global _MEMORY_SERVICE
    if config is not None:
        service = _MEMORY_SERVICES_BY_KEY.pop(_memory_service_key(config), None)
        if service is not None:
            await service.store.close()
        return

    if _MEMORY_SERVICE is None:
        for service in list(_MEMORY_SERVICES_BY_KEY.values()):
            await service.store.close()
        _MEMORY_SERVICES_BY_KEY.clear()
        return
    await _MEMORY_SERVICE.store.close()
    _MEMORY_SERVICE = None
    for service in list(_MEMORY_SERVICES_BY_KEY.values()):
        await service.store.close()
    _MEMORY_SERVICES_BY_KEY.clear()


def _get_conversation_history(
    provider_request: dict[str, Any] | None,
) -> list[dict[str, Any]] | None:
    if not isinstance(provider_request, dict):
        return None
    history = provider_request.get("conversation_history")
    if isinstance(history, list):
        return [item for item in history if isinstance(item, dict)]
    return None
