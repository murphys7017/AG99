from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from astrbot.core import logger

from .analyzer_manager import MemoryAnalyzerManager
from .analyzers.base import (
    MemoryAnalyzerConfigurationError,
    MemoryAnalyzerExecutionError,
)
from .config import MemoryAnalysisConfig, MemoryLongTermConfig
from .document_loader import DocumentLoader
from .keyword_extractor import (
    ConfiguredMemoryKeywordExtractor,
    build_keyword_extractor,
)
from .scope_context import scope_owner_id
from .store import MemoryStore
from .types import (
    Experience,
    ExperienceCategory,
    LongTermMemoryDocument,
    LongTermMemoryIndex,
    LongTermMemoryLink,
    LongTermMemoryLinkRelation,
    LongTermMemoryStatus,
    LongTermPromotionCursor,
    LongTermVectorSyncStatus,
    ScopeType,
)
from .vector_index import MemoryVectorIndex

LONG_TERM_PROMOTE_ANALYZER_NAME = "long_term_promote_v1"
LONG_TERM_COMPOSE_ANALYZER_NAME = "long_term_compose_v1"
LONG_TERM_ACTIONS = {"create", "update", "ignore"}
LONG_TERM_STATUS_VALUES = {status.value for status in LongTermMemoryStatus}


class LongTermMemoryService:
    def __init__(
        self,
        store: MemoryStore,
        *,
        analyzer_manager: MemoryAnalyzerManager | None = None,
        analysis_config: MemoryAnalysisConfig | None = None,
        long_term_config: MemoryLongTermConfig | None = None,
        document_loader: DocumentLoader | None = None,
        vector_index: MemoryVectorIndex | None = None,
        keyword_extractor: ConfiguredMemoryKeywordExtractor | None = None,
    ) -> None:
        self.store = store
        self.analyzer_manager = analyzer_manager
        self.analysis_config = analysis_config
        self.long_term_config = long_term_config
        self.document_loader = document_loader or DocumentLoader(store.config)
        self.vector_index = vector_index
        self.keyword_extractor = keyword_extractor or build_keyword_extractor(
            store.config.keyword_extraction
        )

    def bind_provider_manager(self, provider_manager) -> None:
        if self.vector_index is not None:
            self.vector_index.bind_provider_manager(provider_manager)

    async def should_run_promotion(
        self,
        canonical_user_id: str,
        *,
        scope_type: ScopeType | str = ScopeType.USER,
        scope_id: str | None = None,
    ) -> bool:
        if not self._is_enabled():
            logger.info(
                "memory long-term promotion skipped: disabled canonical_user_id=%s",
                canonical_user_id,
            )
            return False
        owner_id = scope_owner_id(
            scope_type,
            scope_id or canonical_user_id,
            canonical_user_id,
        )
        if owner_id is None:
            return False
        canonical_user_id = owner_id
        scope_id = scope_id or owner_id
        cursor = await self.store.get_long_term_promotion_cursor(
            canonical_user_id,
            scope_type,
            scope_id,
        )
        pending_experiences = await self.store.list_pending_experiences_for_scope(
            canonical_user_id,
            scope_type,
            scope_id,
            cursor,
        )
        threshold = max(1, int(self.long_term_config.min_pending_experiences))
        should_run = len(pending_experiences) >= threshold
        logger.info(
            "memory long-term promotion check: canonical_user_id=%s scope_type=%s scope_id=%s pending_experiences=%s threshold=%s should_run=%s",
            canonical_user_id,
            self._enum_value(scope_type),
            scope_id,
            len(pending_experiences),
            threshold,
            should_run,
        )
        return should_run

    async def run_promotion(
        self,
        canonical_user_id: str,
        *,
        scope_type: ScopeType | str = ScopeType.USER,
        scope_id: str | None = None,
    ) -> list[LongTermMemoryIndex]:
        if not self._is_enabled():
            return []
        if self.analyzer_manager is None:
            raise RuntimeError("long-term promotion requested without analyzer manager")

        owner_id = scope_owner_id(
            scope_type,
            scope_id or canonical_user_id,
            canonical_user_id,
        )
        if owner_id is None:
            return []
        canonical_user_id = owner_id
        scope_id = scope_id or owner_id
        cursor = await self.store.get_long_term_promotion_cursor(
            canonical_user_id,
            scope_type,
            scope_id,
        )
        pending_experiences = await self.store.list_pending_experiences_for_scope(
            canonical_user_id,
            scope_type,
            scope_id,
            cursor,
        )
        threshold = max(1, int(self.long_term_config.min_pending_experiences))
        if len(pending_experiences) < threshold:
            return []
        candidate_experiences = [
            item
            for item in pending_experiences
            if item.importance >= self.long_term_config.min_experience_importance
        ]
        if not candidate_experiences:
            latest_pending = pending_experiences[-1]
            await self.store.upsert_long_term_promotion_cursor(
                LongTermPromotionCursor(
                    cursor_id=cursor.cursor_id
                    if cursor is not None
                    else str(uuid.uuid4()),
                    umo=latest_pending.umo,
                    canonical_user_id=canonical_user_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    last_processed_created_at=latest_pending.created_at,
                    last_processed_experience_id=latest_pending.experience_id,
                    updated_at=datetime.now(UTC),
                )
            )
            logger.info(
                "memory long-term promotion skipped after filtering: canonical_user_id=%s scope_type=%s scope_id=%s pending=%s threshold=%s min_importance=%s",
                canonical_user_id,
                self._enum_value(scope_type),
                scope_id,
                len(pending_experiences),
                threshold,
                self.long_term_config.min_experience_importance,
            )
            return []

        existing_memories = await self._retrieve_candidate_memories(
            canonical_user_id=canonical_user_id,
            scope_type=scope_type,
            scope_id=scope_id,
            pending_experiences=candidate_experiences,
        )
        actions = await self._build_promotion_actions(
            canonical_user_id=canonical_user_id,
            pending_experiences=candidate_experiences,
            existing_memories=existing_memories,
        )
        self._validate_promote_batch_constraints(actions, candidate_experiences)
        await self._ensure_vector_index_ready()
        memory_map = {memory.memory_id: memory for memory in existing_memories}

        pending_memories: list[LongTermMemoryIndex] = []
        links: list[LongTermMemoryLink] = []
        documents_to_refresh: list[tuple[LongTermMemoryDocument, Path]] = []
        now = datetime.now(UTC)
        for action in actions:
            related_experiences = self._resolve_related_experiences(
                action["experience_ids"],
                candidate_experiences,
            )
            if action["action"] == "ignore":
                continue

            existing_memory = None
            existing_document = None
            target_memory_id = action.get("target_memory_id")
            if action["action"] == "update":
                if (
                    not isinstance(target_memory_id, str)
                    or not target_memory_id.strip()
                ):
                    raise MemoryAnalyzerExecutionError(
                        "long_term_promote update action missing target_memory_id"
                    )
                existing_memory = memory_map.get(target_memory_id)
                if existing_memory is None:
                    raise MemoryAnalyzerExecutionError(
                        f"long_term_promote target memory `{target_memory_id}` was not found"
                    )
                existing_document = self.document_loader.load_long_term_document(
                    existing_memory.doc_path
                )

            source_umo = related_experiences[-1].umo
            source_conversation_id = related_experiences[-1].conversation_id
            compose_data = await self._compose_memory(
                canonical_user_id=canonical_user_id,
                source_umo=source_umo,
                conversation_id=source_conversation_id,
                action=action,
                related_experiences=related_experiences,
                existing_memory=existing_memory,
                existing_document=existing_document,
            )

            memory_id = (
                existing_memory.memory_id
                if existing_memory is not None
                else str(uuid.uuid4())
            )
            doc_path = (
                Path(existing_memory.doc_path)
                if existing_memory is not None
                else self._build_doc_path(canonical_user_id, scope_type, memory_id)
            )
            created_at = existing_memory.created_at if existing_memory else now
            existing_updates = (
                list(existing_document.updates) if existing_document else []
            )
            existing_supporting = (
                list(existing_document.supporting_experiences)
                if existing_document
                else []
            )
            updates = [
                *existing_updates,
                {
                    "timestamp": now.isoformat(),
                    "action": action["action"],
                    "summary": compose_data["summary"],
                },
            ]
            supporting_experiences = self._merge_string_list(
                existing_supporting,
                [experience.experience_id for experience in related_experiences],
            )
            source_refs = self._merge_source_refs(
                existing_memory.source_refs if existing_memory else [],
                related_experiences,
            )
            first_event_at = self._resolve_first_event_at(
                existing_memory,
                related_experiences,
            )
            last_event_at = self._resolve_last_event_at(
                existing_memory,
                related_experiences,
            )
            document = LongTermMemoryDocument(
                memory_id=memory_id,
                umo=source_umo,
                canonical_user_id=canonical_user_id,
                scope_type=scope_type,
                scope_id=scope_id,
                category=compose_data["category"],
                status=compose_data["status"],
                title=compose_data["title"],
                summary=compose_data["summary"],
                detail_summary=compose_data["detail_summary"],
                importance=compose_data["importance"],
                confidence=compose_data["confidence"],
                supporting_experiences=supporting_experiences,
                updates=updates,
                source_refs=source_refs,
                tags=list(compose_data["tags"]),
                first_event_at=first_event_at,
                last_event_at=last_event_at,
                created_at=created_at,
                updated_at=now,
            )
            documents_to_refresh.append((document, doc_path))
            pending_memories.append(
                LongTermMemoryIndex(
                    memory_id=memory_id,
                    umo=source_umo,
                    canonical_user_id=canonical_user_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    category=compose_data["category"],
                    title=compose_data["title"],
                    summary=compose_data["summary"],
                    status=compose_data["status"],
                    doc_path=str(doc_path),
                    importance=compose_data["importance"],
                    confidence=compose_data["confidence"],
                    tags=list(compose_data["tags"]),
                    source_refs=source_refs,
                    first_event_at=first_event_at,
                    last_event_at=last_event_at,
                    vector_sync_status=LongTermVectorSyncStatus.PENDING,
                    vector_synced_at=None,
                    vector_sync_error=None,
                    created_at=created_at,
                    updated_at=now,
                )
            )

            relation_type = (
                LongTermMemoryLinkRelation.SEED
                if action["action"] == "create"
                else LongTermMemoryLinkRelation.UPDATE
            )
            for experience in related_experiences:
                links.append(
                    LongTermMemoryLink(
                        link_id=str(uuid.uuid4()),
                        memory_id=memory_id,
                        experience_id=experience.experience_id,
                        relation_type=relation_type,
                        created_at=now,
                    )
                )

        latest_pending = pending_experiences[-1]
        cursor_to_save = LongTermPromotionCursor(
            cursor_id=cursor.cursor_id if cursor is not None else str(uuid.uuid4()),
            umo=latest_pending.umo,
            canonical_user_id=canonical_user_id,
            scope_type=scope_type,
            scope_id=scope_id,
            last_processed_created_at=latest_pending.created_at,
            last_processed_experience_id=latest_pending.experience_id,
            updated_at=now,
        )
        prepared_documents = self._prepare_documents(documents_to_refresh)
        try:
            self._apply_prepared_documents(prepared_documents)
            (
                persisted_indexes,
                _,
                _,
            ) = await self.store.persist_long_term_promotion_batch(
                pending_memories,
                links,
                cursor_to_save,
            )
        except Exception:
            self._rollback_prepared_documents(prepared_documents)
            self._cleanup_prepared_documents(prepared_documents)
            raise
        self._cleanup_prepared_documents(prepared_documents)
        persisted_indexes = await self._refresh_vector_indexes(persisted_indexes)
        logger.info(
            "memory long-term promotion finished: canonical_user_id=%s scope_type=%s scope_id=%s memories=%s links=%s refreshed_docs=%s",
            canonical_user_id,
            self._enum_value(scope_type),
            scope_id,
            len(persisted_indexes),
            len(links),
            len(prepared_documents),
        )
        return persisted_indexes

    def _is_enabled(self) -> bool:
        return bool(
            self.long_term_config is not None
            and self.long_term_config.enabled
            and self.analysis_config is not None
            and self.analysis_config.enabled
            and self.analyzer_manager is not None
        )

    async def _build_promotion_actions(
        self,
        *,
        canonical_user_id: str,
        pending_experiences: list[Experience],
        existing_memories: list[LongTermMemoryIndex],
    ) -> list[dict[str, Any]]:
        existing_memory_payloads = [
            self._memory_to_promote_payload(item) for item in existing_memories
        ]
        payload = {
            "canonical_user_id": canonical_user_id,
            "pending_experiences_json": json.dumps(
                [self._experience_to_payload(item) for item in pending_experiences],
                ensure_ascii=False,
            ),
            "existing_memories_json": json.dumps(
                existing_memory_payloads,
                ensure_ascii=False,
            ),
        }
        source_umo = pending_experiences[-1].umo if pending_experiences else None
        source_conversation_id = (
            pending_experiences[-1].conversation_id if pending_experiences else None
        )
        results = await self.analyzer_manager.dispatch_stage(
            "long_term_promote",
            payload=payload,
            umo=source_umo,
            conversation_id=source_conversation_id,
        )
        result = results.get(LONG_TERM_PROMOTE_ANALYZER_NAME)
        if result is None:
            raise MemoryAnalyzerConfigurationError(
                "long_term_promote missing required analyzer `long_term_promote_v1`"
            )
        return self._validate_promote_payload(result.data)

    def _memory_to_promote_payload(self, memory: LongTermMemoryIndex) -> dict[str, Any]:
        payload = self._memory_to_payload(memory)
        document = self.document_loader.load_long_term_document(memory.doc_path)
        payload["detail_summary"] = document.detail_summary or ""
        payload["updates"] = list(document.updates)
        return payload

    async def _compose_memory(
        self,
        *,
        canonical_user_id: str,
        source_umo: str | None,
        conversation_id: str | None,
        action: dict[str, Any],
        related_experiences: list[Experience],
        existing_memory: LongTermMemoryIndex | None,
        existing_document: LongTermMemoryDocument | None,
    ) -> dict[str, Any]:
        existing_memory_payload = {}
        if existing_memory is not None:
            existing_memory_payload = self._memory_to_payload(existing_memory)
            if existing_document is not None:
                existing_memory_payload["detail_summary"] = (
                    existing_document.detail_summary or ""
                )
                existing_memory_payload["updates"] = list(existing_document.updates)
        payload = {
            "canonical_user_id": canonical_user_id,
            "promotion_action": action["action"],
            "promotion_category": action["category"],
            "promotion_reason": action["reason"],
            "existing_memory_json": json.dumps(
                existing_memory_payload,
                ensure_ascii=False,
            ),
            "supporting_experiences_json": json.dumps(
                [self._experience_to_payload(item) for item in related_experiences],
                ensure_ascii=False,
            ),
        }
        results = await self.analyzer_manager.dispatch_stage(
            "long_term_compose",
            payload=payload,
            umo=source_umo,
            conversation_id=conversation_id,
        )
        result = results.get(LONG_TERM_COMPOSE_ANALYZER_NAME)
        if result is None:
            raise MemoryAnalyzerConfigurationError(
                "long_term_compose missing required analyzer `long_term_compose_v1`"
            )
        compose_data = self._validate_compose_payload(result.data)
        compose_data["category"] = action["category"]
        return compose_data

    def _validate_promote_payload(self, data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, dict):
            raise MemoryAnalyzerExecutionError(
                "long_term_promote returned invalid payload"
            )
        actions = data.get("actions")
        if not isinstance(actions, list):
            raise MemoryAnalyzerExecutionError(
                "long_term_promote missing required field `actions`"
            )
        valid_categories = {category.value for category in ExperienceCategory}
        validated: list[dict[str, Any]] = []
        for item in actions:
            if not isinstance(item, dict):
                raise MemoryAnalyzerExecutionError(
                    "long_term_promote returned invalid action item"
                )
            action = str(item.get("action", "")).strip()
            category = str(item.get("category", "")).strip()
            reason = str(item.get("reason", "")).strip()
            experience_ids = item.get("experience_ids")
            if action not in LONG_TERM_ACTIONS:
                raise MemoryAnalyzerExecutionError(
                    f"long_term_promote returned invalid action `{action}`"
                )
            if category not in valid_categories:
                raise MemoryAnalyzerExecutionError(
                    f"long_term_promote returned invalid category `{category}`"
                )
            if not reason:
                raise MemoryAnalyzerExecutionError(
                    "long_term_promote item missing required field `reason`"
                )
            if not isinstance(experience_ids, list) or not experience_ids:
                raise MemoryAnalyzerExecutionError(
                    "long_term_promote item missing required field `experience_ids`"
                )
            cleaned_experience_ids = [
                str(experience_id).strip()
                for experience_id in experience_ids
                if str(experience_id).strip()
            ]
            if not cleaned_experience_ids:
                raise MemoryAnalyzerExecutionError(
                    "long_term_promote item returned empty `experience_ids`"
                )
            validated.append(
                {
                    "action": action,
                    "target_memory_id": str(item.get("target_memory_id", "")).strip(),
                    "category": category,
                    "reason": reason,
                    "experience_ids": cleaned_experience_ids,
                }
            )
        return validated

    def _validate_promote_batch_constraints(
        self,
        actions: list[dict[str, Any]],
        pending_experiences: list[Experience],
    ) -> None:
        candidate_ids = {
            experience.experience_id
            for experience in pending_experiences
            if experience.experience_id
        }
        consumed_ids: set[str] = set()
        update_targets: set[str] = set()
        for action in actions:
            if action["action"] == "update":
                target_memory_id = action.get("target_memory_id", "")
                if target_memory_id in update_targets:
                    raise MemoryAnalyzerExecutionError(
                        "long_term_promote returned duplicate update target "
                        f"`{target_memory_id}` in one batch"
                    )
                update_targets.add(target_memory_id)
            for experience_id in action["experience_ids"]:
                if experience_id not in candidate_ids:
                    raise MemoryAnalyzerExecutionError(
                        f"long_term_promote referenced unknown experience `{experience_id}`"
                    )
                if experience_id in consumed_ids:
                    raise MemoryAnalyzerExecutionError(
                        "long_term_promote returned duplicate experience coverage "
                        f"for `{experience_id}`"
                    )
                consumed_ids.add(experience_id)
        missing_ids = sorted(candidate_ids - consumed_ids)
        if missing_ids:
            raise MemoryAnalyzerExecutionError(
                "long_term_promote did not cover all candidate experiences: "
                + ", ".join(missing_ids)
            )

    def _validate_compose_payload(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise MemoryAnalyzerExecutionError(
                "long_term_compose returned invalid payload"
            )
        required_text_fields = ("title", "summary", "detail_summary", "status")
        validated: dict[str, Any] = {}
        for field_name in required_text_fields:
            value = data.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise MemoryAnalyzerExecutionError(
                    f"long_term_compose missing required field `{field_name}`"
                )
            validated[field_name] = value.strip()
        tags = data.get("tags")
        if not isinstance(tags, list):
            raise MemoryAnalyzerExecutionError(
                "long_term_compose missing required field `tags`"
            )
        validated["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()]
        for field_name in ("importance", "confidence"):
            value = data.get(field_name)
            if not isinstance(value, int | float):
                raise MemoryAnalyzerExecutionError(
                    f"long_term_compose missing required field `{field_name}`"
                )
            score = float(value)
            if not 0.0 <= score <= 1.0:
                raise MemoryAnalyzerExecutionError(
                    f"long_term_compose field `{field_name}` must be between 0 and 1"
                )
            validated[field_name] = score
        if validated["status"] not in LONG_TERM_STATUS_VALUES:
            raise MemoryAnalyzerExecutionError(
                f"long_term_compose returned invalid status `{validated['status']}`"
            )
        return validated

    @staticmethod
    def _resolve_related_experiences(
        experience_ids: list[str],
        pending_experiences: list[Experience],
    ) -> list[Experience]:
        index = {
            experience.experience_id: experience for experience in pending_experiences
        }
        related: list[Experience] = []
        for experience_id in experience_ids:
            experience = index.get(experience_id)
            if experience is None:
                raise MemoryAnalyzerExecutionError(
                    f"long_term_promote referenced unknown experience `{experience_id}`"
                )
            related.append(experience)
        return related

    async def _retrieve_candidate_memories(
        self,
        *,
        canonical_user_id: str,
        scope_type: ScopeType | str,
        scope_id: str,
        pending_experiences: list[Experience],
    ) -> list[LongTermMemoryIndex]:
        all_memories = await self.store.list_long_term_memory_indexes(
            canonical_user_id,
            limit=0,
            scope_type=scope_type,
            scope_id=scope_id,
        )
        if not all_memories:
            return []
        if self.vector_index is None or not self.store.config.vector_index.enabled:
            return all_memories

        query_texts = await self._build_candidate_query_texts(pending_experiences)
        if not query_texts:
            return all_memories

        hits = await self._search_candidate_memory_hits(
            canonical_user_id=canonical_user_id,
            scope_type=scope_type,
            scope_id=scope_id,
            query_texts=query_texts,
        )
        if not hits:
            return all_memories

        memory_by_id = {memory.memory_id: memory for memory in all_memories}
        candidate_memories: list[LongTermMemoryIndex] = []
        seen_memory_ids: set[str] = set()
        for hit in hits:
            memory = memory_by_id.get(hit.memory_id)
            if memory is not None and memory.memory_id not in seen_memory_ids:
                seen_memory_ids.add(memory.memory_id)
                candidate_memories.append(memory)
        return candidate_memories or all_memories

    async def _build_candidate_query_texts(
        self,
        experiences: list[Experience],
    ) -> list[str]:
        query_texts = []
        experience_query = self._build_experience_query_text(experiences)
        if experience_query:
            query_texts.append(experience_query)
        context_query = await self._build_context_query_text(experiences)
        if context_query:
            query_texts.append(context_query)
        return query_texts

    def _build_experience_query_text(self, experiences: list[Experience]) -> str:
        sections: list[str] = []
        for experience in experiences:
            parts = [
                experience.summary.strip(),
                (experience.detail_summary or "").strip(),
            ]
            block = "\n".join(part for part in parts if part)
            if block:
                sections.append(block)
        return self._compose_query_text(sections)

    async def _build_context_query_text(self, experiences: list[Experience]) -> str:
        if not experiences:
            return ""
        latest_experience = experiences[-1]
        topic_state = await self.store.get_topic_state(
            latest_experience.umo,
            latest_experience.conversation_id,
        )
        short_term_memory = await self.store.get_short_term_memory(
            latest_experience.umo,
            latest_experience.conversation_id,
        )
        sections = [
            topic_state.current_topic.strip()
            if topic_state is not None and topic_state.current_topic
            else "",
            topic_state.topic_summary.strip()
            if topic_state is not None and topic_state.topic_summary
            else "",
            short_term_memory.active_focus.strip()
            if short_term_memory is not None and short_term_memory.active_focus
            else "",
            short_term_memory.short_summary.strip()
            if short_term_memory is not None and short_term_memory.short_summary
            else "",
        ]
        return self._compose_query_text(sections)

    async def _search_candidate_memory_hits(
        self,
        *,
        canonical_user_id: str,
        scope_type: ScopeType | str,
        scope_id: str,
        query_texts: list[str],
    ) -> list[Any]:
        metadata_filters = {
            "scope_type": self._enum_value(scope_type),
            "scope_id": scope_id,
        }
        combined_hits: list[Any] = []
        seen_memory_ids: set[str] = set()
        for query_text in query_texts:
            hits = await self.vector_index.search_long_term_memories(
                canonical_user_id,
                query_text,
                top_k=max(1, self.store.config.vector_index.long_term_top_k),
                metadata_filters=metadata_filters,
            )
            for hit in hits:
                if hit.memory_id not in seen_memory_ids:
                    seen_memory_ids.add(hit.memory_id)
                    combined_hits.append(hit)
        return combined_hits

    def _compose_query_text(self, sections: list[str]) -> str:
        normalized_sections = [section for section in sections if section]
        if not normalized_sections:
            return ""
        keywords = self._extract_keywords("\n".join(normalized_sections))
        if keywords:
            normalized_sections.append(f"Keywords: {', '.join(keywords)}")
        return "\n".join(normalized_sections).strip()

    def _extract_keywords(self, text: str) -> list[str]:
        return self.keyword_extractor.extract(text)

    def _build_doc_path(
        self,
        canonical_user_id: str,
        scope_type: ScopeType | str,
        memory_id: str,
    ) -> Path:
        return (
            self.store.config.storage.docs_root
            / DocumentLoader._safe_path_component(canonical_user_id)
            / DocumentLoader._safe_path_component(self._enum_value(scope_type))
            / f"{DocumentLoader._safe_path_component(memory_id)}.md"
        )

    @staticmethod
    def _merge_string_list(existing: list[str], additions: list[str]) -> list[str]:
        merged = list(existing)
        seen = set(existing)
        for item in additions:
            if item not in seen:
                seen.add(item)
                merged.append(item)
        return merged

    def _merge_source_refs(
        self,
        existing_source_refs: list[str],
        experiences: list[Experience],
    ) -> list[str]:
        merged = list(existing_source_refs)
        seen = set(existing_source_refs)
        for experience in experiences:
            for source_ref in experience.source_refs:
                if source_ref not in seen:
                    seen.add(source_ref)
                    merged.append(source_ref)
        return merged

    @staticmethod
    def _resolve_first_event_at(
        existing_memory: LongTermMemoryIndex | None,
        experiences: list[Experience],
    ) -> datetime | None:
        candidates = [
            experience.event_time for experience in experiences if experience.event_time
        ]
        if existing_memory and existing_memory.first_event_at is not None:
            candidates.append(existing_memory.first_event_at)
        return min(candidates) if candidates else None

    @staticmethod
    def _resolve_last_event_at(
        existing_memory: LongTermMemoryIndex | None,
        experiences: list[Experience],
    ) -> datetime | None:
        candidates = [
            experience.event_time for experience in experiences if experience.event_time
        ]
        if existing_memory and existing_memory.last_event_at is not None:
            candidates.append(existing_memory.last_event_at)
        return max(candidates) if candidates else None

    async def _refresh_vector_indexes(
        self,
        indexes: list[LongTermMemoryIndex],
    ) -> list[LongTermMemoryIndex]:
        refreshed: list[LongTermMemoryIndex] = []
        if self.vector_index is None or not self.store.config.vector_index.enabled:
            for index in indexes:
                refreshed.append(
                    await self.store.update_long_term_vector_sync_state(
                        index.memory_id,
                        status=LongTermVectorSyncStatus.READY,
                        synced_at=None,
                        error=None,
                    )
                )
            return refreshed
        for index in indexes:
            try:
                await self.vector_index.upsert_long_term_memory(index.memory_id)
                refreshed.append(
                    await self.store.update_long_term_vector_sync_state(
                        index.memory_id,
                        status=LongTermVectorSyncStatus.READY,
                        synced_at=datetime.now(UTC),
                        error=None,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "memory long-term vector refresh failed: memory_id=%s canonical_user_id=%s error=%s",
                    index.memory_id,
                    index.canonical_user_id,
                    exc,
                    exc_info=True,
                )
                refreshed.append(
                    await self.store.update_long_term_vector_sync_state(
                        index.memory_id,
                        status=LongTermVectorSyncStatus.DIRTY,
                        synced_at=None,
                        error=str(exc)[:500],
                    )
                )
        return refreshed

    def _prepare_documents(
        self,
        documents: list[tuple[LongTermMemoryDocument, Path]],
    ) -> list[tuple[LongTermMemoryDocument, object]]:
        prepared_documents: list[tuple[LongTermMemoryDocument, object]] = []
        for document, doc_path in documents:
            prepared_documents.append(
                (
                    document,
                    self.document_loader.prepare_long_term_document_write(
                        document,
                        doc_path=doc_path,
                    ),
                )
            )
        return prepared_documents

    def _apply_prepared_documents(
        self,
        prepared_documents: list[tuple[LongTermMemoryDocument, object]],
    ) -> None:
        try:
            for _, prepared in prepared_documents:
                self.document_loader.apply_prepared_write(prepared)
        except Exception:
            self._rollback_prepared_documents(prepared_documents)
            raise

    def _rollback_prepared_documents(
        self,
        prepared_documents: list[tuple[LongTermMemoryDocument, object]],
    ) -> None:
        for _, prepared in reversed(prepared_documents):
            try:
                self.document_loader.rollback_prepared_write(prepared)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "memory long-term document rollback failed: error=%s",
                    exc,
                    exc_info=True,
                )

    def _cleanup_prepared_documents(
        self,
        prepared_documents: list[tuple[LongTermMemoryDocument, object]],
    ) -> None:
        for _, prepared in prepared_documents:
            self.document_loader.cleanup_prepared_write(prepared)

    async def _ensure_vector_index_ready(self) -> None:
        if self.vector_index is None or not self.store.config.vector_index.enabled:
            return
        await self.vector_index.ensure_ready()

    @staticmethod
    def _experience_to_payload(experience: Experience) -> dict[str, Any]:
        return {
            "experience_id": experience.experience_id,
            "conversation_id": experience.conversation_id,
            "platform_user_key": experience.platform_user_key,
            "canonical_user_id": experience.canonical_user_id,
            "scope_type": LongTermMemoryService._enum_value(experience.scope_type),
            "scope_id": experience.scope_id,
            "event_time": experience.event_time.isoformat(),
            "category": LongTermMemoryService._enum_value(experience.category),
            "summary": experience.summary,
            "detail_summary": experience.detail_summary or "",
            "importance": experience.importance,
            "confidence": experience.confidence,
            "source_refs": list(experience.source_refs),
        }

    @staticmethod
    def _memory_to_payload(memory: LongTermMemoryIndex) -> dict[str, Any]:
        return {
            "memory_id": memory.memory_id,
            "canonical_user_id": memory.canonical_user_id,
            "scope_type": LongTermMemoryService._enum_value(memory.scope_type),
            "scope_id": memory.scope_id,
            "category": LongTermMemoryService._enum_value(memory.category),
            "title": memory.title,
            "summary": memory.summary,
            "status": LongTermMemoryService._enum_value(memory.status),
            "tags": list(memory.tags),
            "importance": memory.importance,
            "confidence": memory.confidence,
            "source_refs": list(memory.source_refs),
            "first_event_at": memory.first_event_at.isoformat()
            if memory.first_event_at
            else "",
            "last_event_at": memory.last_event_at.isoformat()
            if memory.last_event_at
            else "",
        }

    @staticmethod
    def _enum_value(value: ScopeType | str) -> str:
        return value.value if hasattr(value, "value") else str(value)
