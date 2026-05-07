from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.db.po import Conversation
from astrbot.core.memory.analyzer import MemoryAnalyzerResult
from astrbot.core.memory.analyzers.base import (
    MemoryAnalyzerConfigurationError,
    MemoryAnalyzerExecutionError,
)
from astrbot.core.memory.config import (
    MemoryAnalysisConfig,
    MemoryConsolidationConfig,
    MemoryLongTermConfig,
    load_memory_config,
)
from astrbot.core.memory.consolidation_service import ConsolidationService
from astrbot.core.memory.document_loader import DocumentLoader
from astrbot.core.memory.document_search import DocumentSearchService
from astrbot.core.memory.document_serializer import DocumentSerializer
from astrbot.core.memory.experience_service import ExperienceService
from astrbot.core.memory.history_source import (
    RecentConversationSource,
    extract_turn_payloads,
)
from astrbot.core.memory.identity import (
    MemoryIdentityMappingService,
    MemoryIdentityResolver,
)
from astrbot.core.memory.long_term_service import LongTermMemoryService
from astrbot.core.memory.manual_service import LongTermMemoryManualService
from astrbot.core.memory.postprocessor import (
    MemoryPostProcessor,
    register_memory_postprocessor,
    reset_memory_postprocessor,
)
from astrbot.core.memory.projection import ExperienceProjectionService
from astrbot.core.memory.service import MemoryService
from astrbot.core.memory.short_term_service import ShortTermMemoryService
from astrbot.core.memory.snapshot_builder import MemorySnapshotBuilder
from astrbot.core.memory.store import MemoryStore
from astrbot.core.memory.turn_record_service import TurnRecordService
from astrbot.core.memory.types import (
    DocumentSearchRequest,
    DocumentSearchResult,
    Experience,
    LongTermMemoryDocument,
    LongTermMemoryIndex,
    LongTermMemoryLink,
    LongTermMemoryStatus,
    LongTermVectorSyncStatus,
    MemoryIdentity,
    MemoryUpdateRequest,
    PersonaState,
    ScopeType,
    SessionInsight,
    ShortTermMemory,
    TopicState,
    TurnRecord,
)
from astrbot.core.memory.vector_index import MemoryVectorIndex
from astrbot.core.postprocess import get_postprocess_manager
from astrbot.core.postprocess.types import PostProcessTrigger
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.provider.provider import EmbeddingProvider

TEST_UMO = "test:private:user"
TEST_PLATFORM_ID = "test"
TEST_PLATFORM_USER_KEY = "test:user-1"
TEST_CANONICAL_USER_ID = "canonical-user-1"


def _make_history() -> list[dict]:
    return [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "We should build memory first."},
        {"role": "assistant", "content": "Let's start from data types and store."},
        {"role": "user", "content": "Then wire postprocess and snapshot."},
        {"role": "assistant", "content": "That gives us a minimal closed loop."},
    ]


def _memory_update_request(
    *,
    user_message: dict[str, object],
    assistant_message: dict[str, object],
    message_timestamp: datetime,
    conversation_id: str | None = "conv-1",
    provider_request: dict | None = None,
    source_refs: list[str] | None = None,
    canonical_user_id: str | None = TEST_CANONICAL_USER_ID,
    platform_user_key: str | None = TEST_PLATFORM_USER_KEY,
) -> MemoryUpdateRequest:
    return MemoryUpdateRequest(
        umo=TEST_UMO,
        conversation_id=conversation_id,
        platform_id=TEST_PLATFORM_ID,
        platform_user_key=platform_user_key,
        canonical_user_id=canonical_user_id,
        session_id="session-1",
        provider_request=provider_request,
        user_message=user_message,
        assistant_message=assistant_message,
        message_timestamp=message_timestamp,
        source_refs=list(source_refs or []),
    )


def _session_insight(
    *,
    insight_id: str,
    window_start_at: datetime | None,
    window_end_at: datetime | None,
    topic_summary: str,
    progress_summary: str,
    summary_text: str,
    created_at: datetime,
    conversation_id: str | None = "conv-1",
    platform_user_key: str | None = TEST_PLATFORM_USER_KEY,
) -> SessionInsight:
    return SessionInsight(
        insight_id=insight_id,
        umo=TEST_UMO,
        conversation_id=conversation_id,
        platform_user_key=platform_user_key,
        canonical_user_id=TEST_CANONICAL_USER_ID,
        window_start_at=window_start_at,
        window_end_at=window_end_at,
        topic_summary=topic_summary,
        progress_summary=progress_summary,
        summary_text=summary_text,
        created_at=created_at,
    )


def _experience(
    *,
    experience_id: str,
    scope_type: str,
    scope_id: str,
    event_time: datetime,
    category: str,
    summary: str,
    detail_summary: str | None = None,
    conversation_id: str | None = "conv-1",
    importance: float = 0.0,
    confidence: float = 0.0,
    source_refs: list[str] | None = None,
    created_at: datetime,
    updated_at: datetime,
    canonical_user_id: str = TEST_CANONICAL_USER_ID,
    platform_user_key: str = TEST_PLATFORM_USER_KEY,
    umo: str = TEST_UMO,
) -> Experience:
    return Experience(
        experience_id=experience_id,
        umo=umo,
        conversation_id=conversation_id,
        platform_user_key=platform_user_key,
        canonical_user_id=canonical_user_id,
        scope_type=scope_type,
        scope_id=scope_id,
        event_time=event_time,
        category=category,
        summary=summary,
        detail_summary=detail_summary,
        importance=importance,
        confidence=confidence,
        source_refs=list(source_refs or []),
        created_at=created_at,
        updated_at=updated_at,
    )


def _long_term_memory_index(
    *,
    memory_id: str,
    scope_type: str,
    scope_id: str,
    category: str,
    title: str,
    summary: str,
    status: LongTermMemoryStatus | str,
    doc_path: str,
    importance: float,
    confidence: float,
    tags: list[str],
    source_refs: list[str],
    first_event_at: datetime | None = None,
    last_event_at: datetime | None = None,
    vector_sync_status: LongTermVectorSyncStatus
    | str = LongTermVectorSyncStatus.PENDING,
    vector_synced_at: datetime | None = None,
    vector_sync_error: str | None = None,
    created_at: datetime,
    updated_at: datetime,
    canonical_user_id: str = TEST_CANONICAL_USER_ID,
    umo: str = TEST_UMO,
) -> LongTermMemoryIndex:
    return LongTermMemoryIndex(
        memory_id=memory_id,
        umo=umo,
        canonical_user_id=canonical_user_id,
        scope_type=scope_type,
        scope_id=scope_id,
        category=category,
        title=title,
        summary=summary,
        status=status,
        doc_path=doc_path,
        importance=importance,
        confidence=confidence,
        tags=tags,
        source_refs=source_refs,
        first_event_at=first_event_at,
        last_event_at=last_event_at,
        vector_sync_status=vector_sync_status,
        vector_synced_at=vector_synced_at,
        vector_sync_error=vector_sync_error,
        created_at=created_at,
        updated_at=updated_at,
    )


def _document_search_request(
    *,
    query: str,
    top_k: int = 5,
    include_body: bool = False,
    conversation_id: str | None = "conv-1",
    scope_type: str | None = None,
    scope_id: str | None = None,
    category: str | None = None,
) -> DocumentSearchRequest:
    return DocumentSearchRequest(
        canonical_user_id=TEST_CANONICAL_USER_ID,
        query=query,
        umo=TEST_UMO,
        conversation_id=conversation_id,
        scope_type=scope_type,
        scope_id=scope_id,
        category=category,
        top_k=top_k,
        include_body=include_body,
    )


def _memory_identity(
    *,
    canonical_user_id: str | None = TEST_CANONICAL_USER_ID,
) -> MemoryIdentity:
    return MemoryIdentity(
        umo=TEST_UMO,
        platform_id=TEST_PLATFORM_ID,
        sender_user_id="user-1",
        sender_nickname="tester",
        platform_user_key=TEST_PLATFORM_USER_KEY,
        canonical_user_id=canonical_user_id,
    )


def test_extract_turn_payloads_handles_tool_use_final_assistant():
    payloads = extract_turn_payloads(
        [
            {"role": "user", "content": "Search the docs."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-1", "type": "function"}],
            },
            {"role": "tool", "content": "Found relevant files."},
            {"role": "assistant", "content": "I found the matching docs."},
        ]
    )

    assert len(payloads) == 1
    assert payloads[0]["assistant_message"]["content"] == "I found the matching docs."


def test_extract_turn_payloads_handles_multiple_tool_rounds():
    payloads = extract_turn_payloads(
        [
            {"role": "user", "content": "Run the workflow."},
            {
                "role": "assistant",
                "content": "Let me inspect first.",
                "tool_calls": [{"id": "call-1", "type": "function"}],
            },
            {"role": "tool", "content": "Step one complete."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-2", "type": "function"}],
            },
            {"role": "tool", "content": "Step two complete."},
            {"role": "assistant", "content": "Everything is done now."},
        ]
    )

    assert len(payloads) == 1
    assert payloads[0]["assistant_message"]["content"] == "Everything is done now."


def test_extract_turn_payloads_falls_back_to_tool_call_placeholder_when_no_final_reply():
    payloads = extract_turn_payloads(
        [
            {"role": "user", "content": "Call the tool only."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-1", "type": "function"}],
            },
            {"role": "tool", "content": "Tool finished."},
        ]
    )

    assert len(payloads) == 1
    assert payloads[0]["assistant_message"]["content"] == "[tool_call]"


def test_extract_turn_payloads_ignores_malformed_messages_without_crashing():
    payloads = extract_turn_payloads(
        [
            {"role": "system", "content": "init"},
            "not-a-dict",
            {"role": "user", "content": "Need a summary."},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "Here is the summary."},
            {"role": "user", "content": "Second turn."},
            {"role": "tool", "content": "orphan tool"},
        ]
    )

    assert len(payloads) == 1
    assert payloads[0]["assistant_message"]["content"] == "Here is the summary."


@pytest.mark.asyncio
async def test_turn_record_service_builds_and_persists_turn(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    service = TurnRecordService(store)
    req = _memory_update_request(
        user_message={"role": "user", "content": "hello"},
        assistant_message={"role": "assistant", "content": "hi"},
        message_timestamp=datetime.now(UTC),
        source_refs=["conversation:conv-1"],
    )

    try:
        turn = await service.ingest_turn(req)
        loaded = await store.get_turn_record(turn.turn_id)
    finally:
        await store.close()

    assert turn.turn_id
    assert loaded is not None
    assert loaded.user_message["content"] == "hello"
    assert loaded.assistant_message["content"] == "hi"


@pytest.mark.asyncio
async def test_turn_record_service_uses_request_turn_id_when_available(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    service = TurnRecordService(store)
    req = _memory_update_request(
        user_message={"role": "user", "content": "hello"},
        assistant_message={"role": "assistant", "content": "hi"},
        message_timestamp=datetime.now(UTC),
    )
    req.turn_id = "turn-from-interaction"

    try:
        turn = await service.ingest_turn(req)
        loaded = await store.get_turn_record("turn-from-interaction")
    finally:
        await store.close()

    assert turn.turn_id == "turn-from-interaction"
    assert loaded is not None
    assert loaded.turn_id == "turn-from-interaction"


@pytest.mark.asyncio
async def test_short_term_memory_service_updates_from_history(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    history_source = RecentConversationSource(store, recent_turns_window=8)
    turn_record_service = TurnRecordService(store)
    short_term_service = _build_short_term_service(store, history_source)
    history = _make_history()
    req = _memory_update_request(
        provider_request={"conversation_history": history},
        user_message=history[-2],
        assistant_message=history[-1],
        message_timestamp=datetime.now(UTC),
        source_refs=["conversation:conv-1"],
    )

    try:
        turn = await turn_record_service.ingest_turn(req)
        topic_state, short_term_memory = await short_term_service.update_after_turn(
            turn,
            conversation_history=history,
        )
    finally:
        await store.close()

    assert topic_state.current_topic == "Analyzer topic"
    assert topic_state.topic_summary == "Analyzer topic summary"
    assert short_term_memory.active_focus == "Analyzer focus"
    assert short_term_memory.short_summary == "Analyzer short summary"


class StubShortTermAnalyzerManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def dispatch_stage(
        self,
        stage: str,
        *,
        payload: dict[str, object],
        umo: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, MemoryAnalyzerResult]:
        self.calls.append(
            {
                "stage": stage,
                "payload": payload,
                "umo": umo,
                "conversation_id": conversation_id,
            }
        )
        return {
            "topic_v1": MemoryAnalyzerResult(
                analyzer_name="topic_v1",
                stage=stage,
                data={
                    "current_topic": "Analyzer topic",
                    "topic_summary": "Analyzer topic summary",
                    "topic_confidence": 0.92,
                },
                raw_text="{}",
                provider_id="memory-lite",
                model="dummy-model",
            ),
            "focus_v1": MemoryAnalyzerResult(
                analyzer_name="focus_v1",
                stage=stage,
                data={
                    "active_focus": "Analyzer focus",
                },
                raw_text="{}",
                provider_id="memory-lite",
                model="dummy-model",
            ),
            "summary_v1": MemoryAnalyzerResult(
                analyzer_name="summary_v1",
                stage=stage,
                data={
                    "short_summary": "Analyzer short summary",
                },
                raw_text="{}",
                provider_id="memory-lite",
                model="dummy-model",
            ),
        }


class InvalidShortTermAnalyzerManager:
    async def dispatch_stage(
        self,
        stage: str,
        *,
        payload: dict[str, object],
        umo: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, MemoryAnalyzerResult]:
        del payload, umo, conversation_id
        return {
            "topic_v1": MemoryAnalyzerResult(
                analyzer_name="topic_v1",
                stage=stage,
                data={
                    "current_topic": "Analyzer topic",
                    "topic_summary": "Analyzer topic summary",
                    "topic_confidence": "high",
                },
                raw_text="{}",
                provider_id="memory-lite",
                model="dummy-model",
            ),
            "focus_v1": MemoryAnalyzerResult(
                analyzer_name="focus_v1",
                stage=stage,
                data={},
                raw_text="{}",
                provider_id="memory-lite",
                model="dummy-model",
            ),
            "summary_v1": MemoryAnalyzerResult(
                analyzer_name="summary_v1",
                stage=stage,
                data={
                    "short_summary": "Analyzer short summary",
                },
                raw_text="{}",
                provider_id="memory-lite",
                model="dummy-model",
            ),
        }


def _build_short_term_service(
    store: MemoryStore,
    history_source: RecentConversationSource | None = None,
    *,
    analyzer_manager=None,
):
    return ShortTermMemoryService(
        store,
        history_source or RecentConversationSource(store, recent_turns_window=8),
        analyzer_manager=analyzer_manager or StubShortTermAnalyzerManager(),
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
    )


class StubConsolidationAnalyzerManager:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def dispatch_stage(
        self,
        stage: str,
        *,
        payload: dict[str, object],
        umo: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, MemoryAnalyzerResult]:
        del payload, umo, conversation_id
        self.calls.append(stage)
        if stage == "session_insight_update":
            return {
                "session_insight_v1": MemoryAnalyzerResult(
                    analyzer_name="session_insight_v1",
                    stage=stage,
                    data={
                        "topic_summary": "Session topic summary",
                        "progress_summary": "Session progress summary",
                        "summary_text": "Session insight overall summary",
                    },
                    raw_text="{}",
                    provider_id="memory-lite",
                    model="dummy-model",
                )
            }
        if stage == "experience_extract":
            return {
                "experience_extract_v1": MemoryAnalyzerResult(
                    analyzer_name="experience_extract_v1",
                    stage=stage,
                    data={
                        "experiences": [
                            {
                                "category": "project_progress",
                                "summary": "Progress moved forward",
                                "detail_summary": "The implementation advanced after discussion.",
                                "importance": 0.82,
                                "confidence": 0.91,
                            },
                            {
                                "category": "episodic_event",
                                "summary": "A memory milestone was reached",
                                "detail_summary": "The team completed the short-term memory phase.",
                                "importance": 0.75,
                                "confidence": 0.88,
                            },
                        ]
                    },
                    raw_text="{}",
                    provider_id="memory-lite",
                    model="dummy-model",
                )
            }
        raise AssertionError(f"Unexpected stage: {stage}")


class InvalidConsolidationAnalyzerManager:
    async def dispatch_stage(
        self,
        stage: str,
        *,
        payload: dict[str, object],
        umo: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, MemoryAnalyzerResult]:
        del payload, umo, conversation_id
        if stage == "session_insight_update":
            return {
                "session_insight_v1": MemoryAnalyzerResult(
                    analyzer_name="session_insight_v1",
                    stage=stage,
                    data={
                        "topic_summary": "Session topic summary",
                        "progress_summary": "Session progress summary",
                        "summary_text": "Session insight overall summary",
                    },
                    raw_text="{}",
                    provider_id="memory-lite",
                    model="dummy-model",
                )
            }
        if stage == "experience_extract":
            return {
                "experience_extract_v1": MemoryAnalyzerResult(
                    analyzer_name="experience_extract_v1",
                    stage=stage,
                    data={
                        "experiences": [
                            {
                                "category": "unknown_type",
                                "summary": "Bad category",
                                "importance": 0.5,
                                "confidence": 0.5,
                            }
                        ]
                    },
                    raw_text="{}",
                    provider_id="memory-lite",
                    model="dummy-model",
                )
            }
        raise AssertionError(f"Unexpected stage: {stage}")


class StubLongTermAnalyzerManager:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.promote_existing_memories: list[dict[str, object]] = []

    async def dispatch_stage(
        self,
        stage: str,
        *,
        payload: dict[str, object],
        umo: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, MemoryAnalyzerResult]:
        del umo, conversation_id
        self.calls.append(stage)
        if stage == "long_term_promote":
            pending_raw = payload.get("pending_experiences_json")
            pending_experiences = (
                json.loads(pending_raw) if isinstance(pending_raw, str) else []
            )
            existing_raw = payload.get("existing_memories_json")
            self.promote_existing_memories = (
                json.loads(existing_raw) if isinstance(existing_raw, str) else []
            )
            experience_ids = [
                str(item.get("experience_id"))
                for item in pending_experiences
                if isinstance(item, dict) and str(item.get("experience_id"))
            ]
            return {
                "long_term_promote_v1": MemoryAnalyzerResult(
                    analyzer_name="long_term_promote_v1",
                    stage=stage,
                    data={
                        "actions": [
                            {
                                "action": "create",
                                "target_memory_id": "",
                                "category": "project_progress",
                                "reason": "A stable project preference emerged.",
                                "experience_ids": experience_ids,
                            }
                        ]
                    },
                    raw_text="{}",
                    provider_id="memory-lite",
                    model="dummy-model",
                )
            }
        if stage == "long_term_compose":
            return {
                "long_term_compose_v1": MemoryAnalyzerResult(
                    analyzer_name="long_term_compose_v1",
                    stage=stage,
                    data={
                        "title": "Memory-first implementation preference",
                        "summary": "The user prefers landing memory infrastructure early.",
                        "detail_summary": "The recent experiences show repeated preference for memory-first sequencing and infrastructure work.",
                        "tags": ["memory", "planning"],
                        "importance": 0.87,
                        "confidence": 0.9,
                        "status": "active",
                    },
                    raw_text="{}",
                    provider_id="memory-lite",
                    model="dummy-model",
                )
            }
        raise AssertionError(f"Unexpected stage: {stage}")


class InvalidLongTermAnalyzerManager:
    async def dispatch_stage(
        self,
        stage: str,
        *,
        payload: dict[str, object],
        umo: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, MemoryAnalyzerResult]:
        del payload, umo, conversation_id
        if stage == "long_term_promote":
            return {
                "long_term_promote_v1": MemoryAnalyzerResult(
                    analyzer_name="long_term_promote_v1",
                    stage=stage,
                    data={"actions": [{"action": "create"}]},
                    raw_text="{}",
                    provider_id="memory-lite",
                    model="dummy-model",
                )
            }
        raise AssertionError(f"Unexpected stage: {stage}")


class OutOfRangeLongTermAnalyzerManager:
    async def dispatch_stage(
        self,
        stage: str,
        *,
        payload: dict[str, object],
        umo: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, MemoryAnalyzerResult]:
        del payload, umo, conversation_id
        if stage == "long_term_promote":
            return {
                "long_term_promote_v1": MemoryAnalyzerResult(
                    analyzer_name="long_term_promote_v1",
                    stage=stage,
                    data={
                        "actions": [
                            {
                                "action": "create",
                                "target_memory_id": "",
                                "category": "project_progress",
                                "reason": "Create a memory for the new experience.",
                                "experience_ids": ["exp-1"],
                            }
                        ]
                    },
                    raw_text="{}",
                    provider_id="memory-lite",
                    model="dummy-model",
                )
            }
        if stage == "long_term_compose":
            return {
                "long_term_compose_v1": MemoryAnalyzerResult(
                    analyzer_name="long_term_compose_v1",
                    stage=stage,
                    data={
                        "title": "Out of range memory",
                        "summary": "The score is invalid.",
                        "detail_summary": "The analyzer returned a score above one.",
                        "tags": ["invalid"],
                        "importance": 1.2,
                        "confidence": 0.9,
                        "status": "active",
                    },
                    raw_text="{}",
                    provider_id="memory-lite",
                    model="dummy-model",
                )
            }
        raise AssertionError(f"Unexpected stage: {stage}")


class MissingCoverageLongTermAnalyzerManager:
    async def dispatch_stage(
        self,
        stage: str,
        *,
        payload: dict[str, object],
        umo: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, MemoryAnalyzerResult]:
        del umo, conversation_id
        if stage == "long_term_promote":
            pending_raw = payload.get("pending_experiences_json")
            pending_experiences = (
                json.loads(pending_raw) if isinstance(pending_raw, str) else []
            )
            first_experience_id = ""
            if pending_experiences and isinstance(pending_experiences[0], dict):
                first_experience_id = str(
                    pending_experiences[0].get("experience_id", "")
                ).strip()
            return {
                "long_term_promote_v1": MemoryAnalyzerResult(
                    analyzer_name="long_term_promote_v1",
                    stage=stage,
                    data={
                        "actions": [
                            {
                                "action": "ignore",
                                "target_memory_id": "",
                                "category": "project_progress",
                                "reason": "Ignore only the first experience.",
                                "experience_ids": [first_experience_id],
                            }
                        ]
                    },
                    raw_text="{}",
                    provider_id="memory-lite",
                    model="dummy-model",
                )
            }
        raise AssertionError(f"Unexpected stage: {stage}")


class DuplicateCoverageLongTermAnalyzerManager:
    async def dispatch_stage(
        self,
        stage: str,
        *,
        payload: dict[str, object],
        umo: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, MemoryAnalyzerResult]:
        del umo, conversation_id
        if stage == "long_term_promote":
            pending_raw = payload.get("pending_experiences_json")
            pending_experiences = (
                json.loads(pending_raw) if isinstance(pending_raw, str) else []
            )
            experience_ids = [
                str(item.get("experience_id"))
                for item in pending_experiences
                if isinstance(item, dict) and str(item.get("experience_id"))
            ]
            return {
                "long_term_promote_v1": MemoryAnalyzerResult(
                    analyzer_name="long_term_promote_v1",
                    stage=stage,
                    data={
                        "actions": [
                            {
                                "action": "ignore",
                                "target_memory_id": "",
                                "category": "project_progress",
                                "reason": "Ignore the experiences once.",
                                "experience_ids": experience_ids,
                            },
                            {
                                "action": "create",
                                "target_memory_id": "",
                                "category": "project_progress",
                                "reason": "Create with the same experiences again.",
                                "experience_ids": experience_ids,
                            },
                        ]
                    },
                    raw_text="{}",
                    provider_id="memory-lite",
                    model="dummy-model",
                )
            }
        raise AssertionError(f"Unexpected stage: {stage}")


class DuplicateUpdateTargetLongTermAnalyzerManager:
    def __init__(self, memory_id: str) -> None:
        self.memory_id = memory_id

    async def dispatch_stage(
        self,
        stage: str,
        *,
        payload: dict[str, object],
        umo: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, MemoryAnalyzerResult]:
        del umo, conversation_id
        if stage == "long_term_promote":
            pending_raw = payload.get("pending_experiences_json")
            pending_experiences = (
                json.loads(pending_raw) if isinstance(pending_raw, str) else []
            )
            experience_ids = [
                str(item.get("experience_id"))
                for item in pending_experiences
                if isinstance(item, dict) and str(item.get("experience_id"))
            ]
            return {
                "long_term_promote_v1": MemoryAnalyzerResult(
                    analyzer_name="long_term_promote_v1",
                    stage=stage,
                    data={
                        "actions": [
                            {
                                "action": "update",
                                "target_memory_id": self.memory_id,
                                "category": "project_progress",
                                "reason": "Apply first update.",
                                "experience_ids": [experience_ids[0]],
                            },
                            {
                                "action": "update",
                                "target_memory_id": self.memory_id,
                                "category": "project_progress",
                                "reason": "Apply second update to same memory.",
                                "experience_ids": [experience_ids[1]],
                            },
                        ]
                    },
                    raw_text="{}",
                    provider_id="memory-lite",
                    model="dummy-model",
                )
            }
        raise AssertionError(f"Unexpected stage: {stage}")


class StubLongTermUpdateAnalyzerManager:
    def __init__(self, memory_id: str) -> None:
        self.memory_id = memory_id
        self.calls: list[str] = []
        self.promote_existing_memories: list[dict[str, object]] = []

    async def dispatch_stage(
        self,
        stage: str,
        *,
        payload: dict[str, object],
        umo: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, MemoryAnalyzerResult]:
        del umo, conversation_id
        self.calls.append(stage)
        if stage == "long_term_promote":
            pending_raw = payload.get("pending_experiences_json")
            pending_experiences = (
                json.loads(pending_raw) if isinstance(pending_raw, str) else []
            )
            existing_raw = payload.get("existing_memories_json")
            self.promote_existing_memories = (
                json.loads(existing_raw) if isinstance(existing_raw, str) else []
            )
            experience_ids = [
                str(item.get("experience_id"))
                for item in pending_experiences
                if isinstance(item, dict) and str(item.get("experience_id"))
            ]
            return {
                "long_term_promote_v1": MemoryAnalyzerResult(
                    analyzer_name="long_term_promote_v1",
                    stage=stage,
                    data={
                        "actions": [
                            {
                                "action": "update",
                                "target_memory_id": self.memory_id,
                                "category": "project_progress",
                                "reason": "The existing long-term memory received new support.",
                                "experience_ids": experience_ids,
                            }
                        ]
                    },
                    raw_text="{}",
                    provider_id="memory-lite",
                    model="dummy-model",
                )
            }
        if stage == "long_term_compose":
            return {
                "long_term_compose_v1": MemoryAnalyzerResult(
                    analyzer_name="long_term_compose_v1",
                    stage=stage,
                    data={
                        "title": "Updated memory-first implementation preference",
                        "summary": "The user still prefers memory-first implementation, now reinforced by new experiences.",
                        "detail_summary": "New experiences reinforced the existing memory and updated the detail summary.",
                        "tags": ["memory", "planning", "update"],
                        "importance": 0.9,
                        "confidence": 0.94,
                        "status": "active",
                    },
                    raw_text="{}",
                    provider_id="memory-lite",
                    model="dummy-model",
                )
            }
        raise AssertionError(f"Unexpected stage: {stage}")


class StubVectorIndex:
    def __init__(self, hits: list[tuple[str, float]]) -> None:
        self.hits = hits
        self.calls: list[dict[str, object]] = []
        self.config = MagicMock()

    async def ensure_ready(self) -> None:
        return None

    async def upsert_long_term_memory(self, memory_id: str) -> None:
        del memory_id
        return None

    async def search_long_term_memories(
        self,
        umo: str,
        query: str,
        top_k: int,
        metadata_filters: dict | None = None,
    ):
        self.calls.append(
            {
                "umo": umo,
                "query": query,
                "top_k": top_k,
                "metadata_filters": metadata_filters,
            }
        )
        return [
            MagicMock(memory_id=memory_id, score=score, metadata={})
            for memory_id, score in self.hits
        ]


class SequencedStubVectorIndex(StubVectorIndex):
    def __init__(self, hit_sequences: list[list[tuple[str, float]]]) -> None:
        super().__init__([])
        self.hit_sequences = list(hit_sequences)

    async def search_long_term_memories(
        self,
        umo: str,
        query: str,
        top_k: int,
        metadata_filters: dict | None = None,
    ):
        self.calls.append(
            {
                "umo": umo,
                "query": query,
                "top_k": top_k,
                "metadata_filters": metadata_filters,
            }
        )
        hits = self.hit_sequences.pop(0) if self.hit_sequences else []
        return [
            MagicMock(memory_id=memory_id, score=score, metadata={})
            for memory_id, score in hits
        ]


class StubManualVectorIndex:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def ensure_ready(self) -> None:
        return None

    async def upsert_long_term_memory(self, memory_id: str) -> None:
        self.calls.append(memory_id)


class StubSnapshotDocumentSearchService:
    def __init__(self, memory_ids: list[str]) -> None:
        self.memory_ids = list(memory_ids)
        self.calls: list[DocumentSearchRequest] = []

    async def search_long_term_memories(
        self,
        req: DocumentSearchRequest,
    ) -> list[DocumentSearchResult]:
        self.calls.append(req)
        return [
            DocumentSearchResult(
                memory_id=memory_id,
                score=1.0 - (index * 0.1),
                title=memory_id,
                summary=memory_id,
                category="project_progress",
                tags=[],
                doc_path=f"{memory_id}.md",
            )
            for index, memory_id in enumerate(self.memory_ids)
        ]


class FailingManualVectorIndex:
    async def ensure_ready(self) -> None:
        return None

    async def upsert_long_term_memory(self, memory_id: str) -> None:
        del memory_id
        raise RuntimeError("manual vector refresh failed")


class FailingPromotionVectorIndex:
    async def ensure_ready(self) -> None:
        return None

    async def upsert_long_term_memory(self, memory_id: str) -> None:
        del memory_id
        raise RuntimeError("promotion vector refresh failed")


class FailingDocumentLoader(DocumentLoader):
    def prepare_long_term_document_write(
        self,
        document: LongTermMemoryDocument,
        doc_path: Path | str | None = None,
    ):
        del document, doc_path
        raise RuntimeError("long-term markdown write failed")


class FailingProjectionService:
    async def refresh_for_experiences(self, experiences):  # noqa: ANN001
        del experiences
        raise RuntimeError("projection write failed")

    async def refresh_scope_projection(
        self,
        umo: str,
        scope_type: str,
        scope_id: str,
    ):
        del umo, scope_type, scope_id
        raise RuntimeError("projection write failed")


class DummyEmbeddingProvider(EmbeddingProvider):
    def __init__(self, provider_id: str = "embedding-test") -> None:
        super().__init__({"id": provider_id, "type": "dummy_embedding"}, {})

    async def get_embedding(self, text: str) -> list[float]:
        lowered = text.lower()
        return [
            float(lowered.count("memory")),
            float(lowered.count("project")),
            float(lowered.count("preference")),
            float(lowered.count("search")),
        ]

    async def get_embeddings(self, text: list[str]) -> list[list[float]]:
        return [await self.get_embedding(item) for item in text]

    def get_dim(self) -> int:
        return 4


class DummyEmbeddingProviderManager:
    def __init__(self, provider: EmbeddingProvider) -> None:
        self.provider = provider
        self.embedding_provider_insts = [provider]

    async def get_provider_by_id(self, provider_id: str):
        if provider_id == self.provider.provider_config.get("id"):
            return self.provider
        return None


@pytest.mark.asyncio
async def test_short_term_memory_service_uses_analyzer_when_enabled(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    history_source = RecentConversationSource(store, recent_turns_window=8)
    analyzer_manager = StubShortTermAnalyzerManager()
    short_term_service = ShortTermMemoryService(
        store,
        history_source,
        analyzer_manager=analyzer_manager,  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
    )
    turn_record_service = TurnRecordService(store)
    history = _make_history()
    req = _memory_update_request(
        provider_request={"conversation_history": history},
        user_message=history[-2],
        assistant_message=history[-1],
        message_timestamp=datetime.now(UTC),
        source_refs=["conversation:conv-1"],
    )

    try:
        turn = await turn_record_service.ingest_turn(req)
        topic_state, short_term_memory = await short_term_service.update_after_turn(
            turn,
            conversation_history=history,
        )
    finally:
        await store.close()

    assert analyzer_manager.calls
    assert analyzer_manager.calls[0]["stage"] == "short_term_update"
    assert topic_state.current_topic == "Analyzer topic"
    assert topic_state.topic_summary == "Analyzer topic summary"
    assert topic_state.topic_confidence == 0.92
    assert short_term_memory.short_summary == "Analyzer short summary"
    assert short_term_memory.active_focus == "Analyzer focus"


@pytest.mark.asyncio
async def test_short_term_memory_service_raises_on_invalid_analyzer_payload(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    history_source = RecentConversationSource(store, recent_turns_window=8)
    short_term_service = ShortTermMemoryService(
        store,
        history_source,
        analyzer_manager=InvalidShortTermAnalyzerManager(),  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
    )
    turn_record_service = TurnRecordService(store)
    history = _make_history()
    req = _memory_update_request(
        provider_request={"conversation_history": history},
        user_message=history[-2],
        assistant_message=history[-1],
        message_timestamp=datetime.now(UTC),
        source_refs=["conversation:conv-1"],
    )

    try:
        turn = await turn_record_service.ingest_turn(req)
        with pytest.raises(MemoryAnalyzerExecutionError):
            await short_term_service.update_after_turn(
                turn,
                conversation_history=history,
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_short_term_memory_service_requires_analysis_configuration(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    history_source = RecentConversationSource(store, recent_turns_window=8)
    short_term_service = ShortTermMemoryService(store, history_source)
    turn_record_service = TurnRecordService(store)
    history = _make_history()
    req = _memory_update_request(
        provider_request={"conversation_history": history},
        user_message=history[-2],
        assistant_message=history[-1],
        message_timestamp=datetime.now(UTC),
        source_refs=["conversation:conv-1"],
    )

    try:
        turn = await turn_record_service.ingest_turn(req)
        with pytest.raises(
            MemoryAnalyzerConfigurationError,
            match="is not enabled",
        ):
            await short_term_service.update_after_turn(
                turn,
                conversation_history=history,
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_memory_service_update_and_snapshot_form_closed_loop(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    history_source = RecentConversationSource(store, recent_turns_window=8)
    turn_record_service = TurnRecordService(store)
    short_term_service = _build_short_term_service(store, history_source)
    snapshot_builder = MemorySnapshotBuilder(store)
    memory_service = MemoryService(
        store,
        turn_record_service,
        short_term_service,
        snapshot_builder,
    )
    history = _make_history()
    req = _memory_update_request(
        provider_request={"conversation_history": history},
        user_message=history[-2],
        assistant_message=history[-1],
        message_timestamp=datetime.now(UTC),
        source_refs=["conversation:conv-1"],
    )

    try:
        snapshot_before = await memory_service.get_snapshot(
            "test:private:user", "conv-1"
        )
        await memory_service.update_from_postprocess(req)
        snapshot_after = await memory_service.get_snapshot(
            "test:private:user", "conv-1"
        )
    finally:
        await store.close()

    assert snapshot_before.topic_state is None
    assert snapshot_before.short_term_memory is None
    assert snapshot_after.topic_state is not None
    assert snapshot_after.short_term_memory is not None
    assert snapshot_after.experiences == []
    assert snapshot_after.long_term_memories == []
    assert snapshot_after.persona_state is None
    assert snapshot_after.debug_meta == {}


@pytest.mark.asyncio
async def test_memory_service_snapshot_keeps_query_as_debug_meta(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    snapshot_builder = MemorySnapshotBuilder(store)
    memory_service = MemoryService(
        store,
        TurnRecordService(store),
        _build_short_term_service(store, RecentConversationSource(store)),
        snapshot_builder,
    )

    try:
        snapshot = await memory_service.get_snapshot(
            "test:private:user",
            "conv-1",
            query="memory lookup query",
        )
    finally:
        await store.close()

    assert snapshot.debug_meta == {"query": "memory lookup query"}


@pytest.mark.asyncio
async def test_memory_service_snapshot_uses_query_search_for_long_term_memories(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    document_search_service = StubSnapshotDocumentSearchService(
        ["ltm-query-2", "ltm-query-1"]
    )
    snapshot_builder = MemorySnapshotBuilder(
        store,
        document_search_service=document_search_service,  # type: ignore[arg-type]
    )
    memory_service = MemoryService(
        store,
        TurnRecordService(store),
        _build_short_term_service(store, RecentConversationSource(store)),
        snapshot_builder,
    )
    now = datetime.now(UTC)

    try:
        await store.save_turn_record(
            TurnRecord(
                turn_id="turn-query-1",
                umo=TEST_UMO,
                conversation_id="conv-1",
                platform_id=TEST_PLATFORM_ID,
                platform_user_key=TEST_PLATFORM_USER_KEY,
                canonical_user_id=TEST_CANONICAL_USER_ID,
                session_id="session-1",
                user_message={"role": "user", "content": "Need memory search."},
                assistant_message={
                    "role": "assistant",
                    "content": "Searching memories.",
                },
                message_timestamp=now,
                source_refs=[],
                created_at=now,
            )
        )
        for memory_id, updated_at in (
            ("ltm-query-1", now),
            ("ltm-query-2", now + timedelta(seconds=1)),
        ):
            await store.upsert_long_term_memory_index(
                _long_term_memory_index(
                    memory_id=memory_id,
                    scope_type="user",
                    scope_id=TEST_CANONICAL_USER_ID,
                    category="project_progress",
                    title=memory_id,
                    summary=f"Summary for {memory_id}",
                    status="active",
                    doc_path=str(temp_dir / f"{memory_id}.md"),
                    importance=0.8,
                    confidence=0.9,
                    tags=[],
                    source_refs=[],
                    first_event_at=updated_at,
                    last_event_at=updated_at,
                    created_at=updated_at,
                    updated_at=updated_at,
                )
            )

        snapshot = await memory_service.get_snapshot(
            TEST_UMO,
            "conv-1",
            query="query driven snapshot",
        )
    finally:
        await store.close()

    assert snapshot.debug_meta == {"query": "query driven snapshot"}
    assert [item.memory_id for item in snapshot.long_term_memories] == [
        "ltm-query-2",
        "ltm-query-1",
    ]
    assert len(document_search_service.calls) == 1
    assert document_search_service.calls[0].query == "query driven snapshot"
    assert document_search_service.calls[0].canonical_user_id == TEST_CANONICAL_USER_ID
    assert document_search_service.calls[0].scope_type == ScopeType.USER
    assert document_search_service.calls[0].scope_id == TEST_CANONICAL_USER_ID


@pytest.mark.asyncio
async def test_memory_service_snapshot_uses_story_links_for_query_aware_experiences(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    document_search_service = StubSnapshotDocumentSearchService(
        ["ltm-query-2", "ltm-query-1"]
    )
    snapshot_builder = MemorySnapshotBuilder(
        store,
        document_search_service=document_search_service,  # type: ignore[arg-type]
    )
    memory_service = MemoryService(
        store,
        TurnRecordService(store),
        _build_short_term_service(store, RecentConversationSource(store)),
        snapshot_builder,
    )
    now = datetime.now(UTC)

    try:
        await store.save_turn_record(
            TurnRecord(
                turn_id="turn-query-experiences-1",
                umo=TEST_UMO,
                conversation_id="conv-1",
                platform_id=TEST_PLATFORM_ID,
                platform_user_key=TEST_PLATFORM_USER_KEY,
                canonical_user_id=TEST_CANONICAL_USER_ID,
                session_id="session-1",
                user_message={"role": "user", "content": "Need story-linked context."},
                assistant_message={
                    "role": "assistant",
                    "content": "Searching linked experiences.",
                },
                message_timestamp=now,
                source_refs=[],
                created_at=now,
            )
        )
        for memory_id, updated_at in (
            ("ltm-query-1", now),
            ("ltm-query-2", now + timedelta(seconds=1)),
        ):
            await store.upsert_long_term_memory_index(
                _long_term_memory_index(
                    memory_id=memory_id,
                    scope_type="user",
                    scope_id=TEST_CANONICAL_USER_ID,
                    category="project_progress",
                    title=memory_id,
                    summary=f"Summary for {memory_id}",
                    status="active",
                    doc_path=str(temp_dir / f"{memory_id}.md"),
                    importance=0.8,
                    confidence=0.9,
                    tags=[],
                    source_refs=[],
                    first_event_at=updated_at,
                    last_event_at=updated_at,
                    created_at=updated_at,
                    updated_at=updated_at,
                )
            )

        await store.save_experience(
            _experience(
                experience_id="exp-linked-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now,
                category="project_progress",
                summary="First linked experience",
                detail_summary="Supports the older matched story.",
                created_at=now,
                updated_at=now,
            )
        )
        await store.save_experience(
            _experience(
                experience_id="exp-linked-2",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now + timedelta(seconds=1),
                category="project_progress",
                summary="Second linked experience",
                detail_summary="Supports the higher-ranked matched story.",
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=1),
            )
        )
        await store.save_experience(
            _experience(
                experience_id="exp-recent-fallback",
                scope_type="conversation",
                scope_id="conv-1",
                event_time=now + timedelta(seconds=2),
                category="episodic_event",
                summary="Most recent conversation experience",
                detail_summary="Fills the snapshot after linked experiences.",
                created_at=now + timedelta(seconds=2),
                updated_at=now + timedelta(seconds=2),
            )
        )

        await store.save_long_term_memory_link(
            LongTermMemoryLink(
                link_id="link-query-1",
                memory_id="ltm-query-1",
                experience_id="exp-linked-1",
                relation_type="support",
                created_at=now,
            )
        )
        await store.save_long_term_memory_link(
            LongTermMemoryLink(
                link_id="link-query-2",
                memory_id="ltm-query-2",
                experience_id="exp-linked-2",
                relation_type="support",
                created_at=now + timedelta(seconds=1),
            )
        )

        snapshot = await memory_service.get_snapshot(
            TEST_UMO,
            "conv-1",
            query="query driven snapshot",
        )
    finally:
        await store.close()

    assert [item.memory_id for item in snapshot.long_term_memories] == [
        "ltm-query-2",
        "ltm-query-1",
    ]
    assert [item.experience_id for item in snapshot.experiences[:3]] == [
        "exp-linked-2",
        "exp-linked-1",
        "exp-recent-fallback",
    ]


@pytest.mark.asyncio
async def test_memory_store_migrates_platform_user_key_to_nullable(
    temp_dir: Path,
):
    db_path = temp_dir / "memory.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE memory_session_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                insight_id VARCHAR(64) NOT NULL,
                umo VARCHAR(255) NOT NULL,
                conversation_id VARCHAR(64),
                platform_user_key VARCHAR(255) NOT NULL,
                canonical_user_id VARCHAR(255) NOT NULL,
                window_start_at DATETIME,
                window_end_at DATETIME,
                topic_summary TEXT,
                progress_summary TEXT,
                summary_text TEXT,
                created_at DATETIME NOT NULL
            );
            CREATE UNIQUE INDEX ix_memory_session_insights_insight_id ON memory_session_insights (insight_id);
            CREATE INDEX ix_memory_session_insights_umo ON memory_session_insights (umo);
            CREATE INDEX ix_memory_session_insights_conversation_id ON memory_session_insights (conversation_id);
            CREATE INDEX ix_memory_session_insights_platform_user_key ON memory_session_insights (platform_user_key);
            CREATE INDEX ix_memory_session_insights_canonical_user_id ON memory_session_insights (canonical_user_id);
            CREATE INDEX ix_memory_session_insights_window_start_at ON memory_session_insights (window_start_at);
            CREATE INDEX ix_memory_session_insights_window_end_at ON memory_session_insights (window_end_at);

            CREATE TABLE memory_experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experience_id VARCHAR(64) NOT NULL,
                umo VARCHAR(255) NOT NULL,
                conversation_id VARCHAR(64),
                platform_user_key VARCHAR(255) NOT NULL,
                canonical_user_id VARCHAR(255) NOT NULL,
                scope_type VARCHAR(32) NOT NULL,
                scope_id VARCHAR(255) NOT NULL,
                event_time DATETIME NOT NULL,
                category VARCHAR(64) NOT NULL,
                summary TEXT NOT NULL,
                detail_summary TEXT,
                importance FLOAT NOT NULL DEFAULT 0.0,
                confidence FLOAT NOT NULL DEFAULT 0.0,
                source_refs JSON,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE UNIQUE INDEX ix_memory_experiences_experience_id ON memory_experiences (experience_id);
            CREATE INDEX ix_memory_experiences_umo ON memory_experiences (umo);
            CREATE INDEX ix_memory_experiences_conversation_id ON memory_experiences (conversation_id);
            CREATE INDEX ix_memory_experiences_platform_user_key ON memory_experiences (platform_user_key);
            CREATE INDEX ix_memory_experiences_canonical_user_id ON memory_experiences (canonical_user_id);
            CREATE INDEX ix_memory_experiences_scope_type ON memory_experiences (scope_type);
            CREATE INDEX ix_memory_experiences_scope_id ON memory_experiences (scope_id);
            CREATE INDEX ix_memory_experiences_event_time ON memory_experiences (event_time);
            CREATE INDEX ix_memory_experiences_category ON memory_experiences (category);
            """
        )
        conn.commit()
    finally:
        conn.close()

    store = MemoryStore(db_path=db_path)
    now = datetime.now(UTC)

    try:
        await store.initialize()
        insight = await store.save_session_insight(
            _session_insight(
                insight_id="insight-migrated",
                window_start_at=now,
                window_end_at=now,
                topic_summary="Migrated insight",
                progress_summary="Migrated progress",
                summary_text="Migrated summary",
                created_at=now,
                platform_user_key=None,
            )
        )
        experience = await store.save_experience(
            _experience(
                experience_id="exp-migrated",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now,
                category="project_progress",
                summary="Migrated experience",
                detail_summary="The migrated table should allow null platform_user_key.",
                importance=0.8,
                confidence=0.9,
                source_refs=[],
                created_at=now,
                updated_at=now,
                platform_user_key=None,
            )
        )
    finally:
        await store.close()

    assert insight.platform_user_key is None
    assert experience.platform_user_key is None


@pytest.mark.asyncio
async def test_memory_identity_resolver_uses_event_and_mapping(temp_dir: Path):
    config_path = temp_dir / "memory-config.yaml"
    mappings_path = (temp_dir / "identity_mappings.yaml").as_posix()
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "identity:",
                "  enabled: true",
                f'  mappings_path: "{mappings_path}"',
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    mapping_service = MemoryIdentityMappingService(store, config=config)
    resolver = MemoryIdentityResolver(mapping_service)
    event = MagicMock()
    event.unified_msg_origin = TEST_UMO
    event.get_platform_id.return_value = TEST_PLATFORM_ID
    event.get_sender_id.return_value = "user-1"
    event.get_sender_name.return_value = "tester"

    try:
        await mapping_service.bind_platform_user(
            TEST_PLATFORM_ID,
            "user-1",
            TEST_CANONICAL_USER_ID,
            nickname_hint="tester",
        )
        identity = await resolver.resolve_from_event(event)
    finally:
        await store.close()

    assert identity.umo == TEST_UMO
    assert identity.platform_user_key == TEST_PLATFORM_USER_KEY
    assert identity.canonical_user_id == TEST_CANONICAL_USER_ID
    assert identity.sender_nickname == "tester"


@pytest.mark.asyncio
async def test_memory_service_update_skips_mid_long_when_canonical_user_missing(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    history_source = RecentConversationSource(store, recent_turns_window=8)
    turn_record_service = TurnRecordService(store)
    short_term_service = _build_short_term_service(store, history_source)
    consolidation_service = ConsolidationService(
        store,
        analyzer_manager=StubConsolidationAnalyzerManager(),  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        consolidation_config=MemoryConsolidationConfig(
            enabled=True,
            min_short_term_updates=1,
        ),
    )
    memory_service = MemoryService(
        store,
        turn_record_service,
        short_term_service,
        MemorySnapshotBuilder(store),
        consolidation_service=consolidation_service,
        experience_service=ExperienceService(store),
    )
    history = _make_history()

    try:
        turn = await memory_service.update_from_postprocess(
            _memory_update_request(
                provider_request={"conversation_history": history},
                user_message=history[-2],
                assistant_message=history[-1],
                message_timestamp=datetime.now(UTC),
                source_refs=["conversation:conv-1"],
                canonical_user_id=None,
            )
        )
        snapshot = await memory_service.get_snapshot(TEST_UMO, "conv-1")
        latest_insight = await store.get_latest_session_insight(
            TEST_CANONICAL_USER_ID,
            "conv-1",
        )
        experiences = await store.list_recent_experiences(TEST_CANONICAL_USER_ID, 10)
    finally:
        await store.close()

    assert turn.canonical_user_id is None
    assert snapshot.short_term_memory is not None
    assert snapshot.canonical_user_id is None
    assert latest_insight is None
    assert experiences == []


@pytest.mark.asyncio
async def test_consolidation_service_runs_when_pending_turns_reach_threshold(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    analyzer_manager = StubConsolidationAnalyzerManager()
    consolidation_service = ConsolidationService(
        store,
        analyzer_manager=analyzer_manager,  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        consolidation_config=MemoryConsolidationConfig(
            enabled=True,
            min_short_term_updates=2,
        ),
    )
    turn_record_service = TurnRecordService(store)
    now = datetime.now(UTC)

    try:
        first_turn = await turn_record_service.ingest_turn(
            _memory_update_request(
                user_message={"role": "user", "content": "First turn"},
                assistant_message={"role": "assistant", "content": "First reply"},
                message_timestamp=now,
                source_refs=[],
            )
        )
        second_turn = await turn_record_service.ingest_turn(
            _memory_update_request(
                user_message={"role": "user", "content": "Second turn"},
                assistant_message={"role": "assistant", "content": "Second reply"},
                message_timestamp=now + timedelta(microseconds=1),
                source_refs=[],
            )
        )
        await store.upsert_topic_state(
            TopicState(
                umo="test:private:user",
                conversation_id="conv-1",
                current_topic="Memory work",
                topic_summary="Working on memory consolidation",
                topic_confidence=0.9,
                last_active_at=second_turn.message_timestamp,
            )
        )
        await store.upsert_short_term_memory(
            ShortTermMemory(
                umo="test:private:user",
                conversation_id="conv-1",
                short_summary="Two turns happened",
                active_focus="Consolidate them",
                updated_at=second_turn.message_timestamp,
            )
        )

        should_run = await consolidation_service.should_run_consolidation(
            TEST_CANONICAL_USER_ID,
            "conv-1",
        )
        insight, experiences = await consolidation_service.run_for_scope(
            TEST_CANONICAL_USER_ID,
            "conv-1",
        )
    finally:
        await store.close()

    assert first_turn.turn_id
    assert should_run is True
    assert analyzer_manager.calls == ["session_insight_update", "experience_extract"]
    assert insight is not None
    assert insight.topic_summary == "Session topic summary"
    assert len(experiences) == 2
    assert experiences[0].scope_id == TEST_CANONICAL_USER_ID
    assert experiences[0].source_refs[-1].startswith("insight:")


@pytest.mark.asyncio
async def test_consolidation_service_only_counts_turns_after_latest_insight(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    consolidation_service = ConsolidationService(
        store,
        analyzer_manager=StubConsolidationAnalyzerManager(),  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        consolidation_config=MemoryConsolidationConfig(
            enabled=True,
            min_short_term_updates=2,
        ),
    )
    turn_record_service = TurnRecordService(store)
    now = datetime.now(UTC)

    try:
        await turn_record_service.ingest_turn(
            _memory_update_request(
                user_message={"role": "user", "content": "Old turn"},
                assistant_message={"role": "assistant", "content": "Old reply"},
                message_timestamp=now,
                source_refs=[],
            )
        )
        await store.save_session_insight(
            _session_insight(
                insight_id="insight-1",
                window_start_at=now,
                window_end_at=now,
                topic_summary="Old insight",
                progress_summary="Old progress",
                summary_text="Old summary",
                created_at=now,
            )
        )
        await turn_record_service.ingest_turn(
            _memory_update_request(
                user_message={"role": "user", "content": "New turn 1"},
                assistant_message={"role": "assistant", "content": "New reply 1"},
                message_timestamp=now + timedelta(microseconds=1),
                source_refs=[],
            )
        )
        await turn_record_service.ingest_turn(
            _memory_update_request(
                user_message={"role": "user", "content": "New turn 2"},
                assistant_message={"role": "assistant", "content": "New reply 2"},
                message_timestamp=now + timedelta(microseconds=2),
                source_refs=[],
            )
        )

        should_run = await consolidation_service.should_run_consolidation(
            TEST_CANONICAL_USER_ID,
            "conv-1",
        )
    finally:
        await store.close()

    assert should_run is True


@pytest.mark.asyncio
async def test_memory_service_update_triggers_and_persists_consolidation(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    history_source = RecentConversationSource(store, recent_turns_window=8)
    turn_record_service = TurnRecordService(store)
    short_term_service = _build_short_term_service(store, history_source)
    analyzer_manager = StubConsolidationAnalyzerManager()
    consolidation_service = ConsolidationService(
        store,
        analyzer_manager=analyzer_manager,  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        consolidation_config=MemoryConsolidationConfig(
            enabled=True,
            min_short_term_updates=2,
        ),
    )
    experience_service = ExperienceService(store)
    memory_service = MemoryService(
        store,
        turn_record_service,
        short_term_service,
        MemorySnapshotBuilder(store),
        consolidation_service=consolidation_service,
        experience_service=experience_service,
    )
    now = datetime.now(UTC)

    try:
        for index in range(2):
            await memory_service.update_from_postprocess(
                _memory_update_request(
                    user_message={"role": "user", "content": f"Turn {index}"},
                    assistant_message={
                        "role": "assistant",
                        "content": f"Reply {index}",
                    },
                    message_timestamp=now + timedelta(microseconds=index),
                    source_refs=[],
                )
            )

        latest_insight = await store.get_latest_session_insight(
            TEST_CANONICAL_USER_ID,
            "conv-1",
        )
        experiences = await store.list_recent_experiences(TEST_CANONICAL_USER_ID, 10)
        snapshot = await memory_service.get_snapshot(TEST_UMO, "conv-1")
    finally:
        await store.close()

    assert analyzer_manager.calls == ["session_insight_update", "experience_extract"]
    assert latest_insight is not None
    assert len(experiences) == 2
    assert len(snapshot.experiences) == 2
    assert {item.experience_id for item in snapshot.experiences} == {
        item.experience_id for item in experiences
    }
    assert snapshot.long_term_memories == []
    assert snapshot.persona_state is None


@pytest.mark.asyncio
async def test_memory_service_consolidation_allows_missing_platform_user_key(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    history_source = RecentConversationSource(store, recent_turns_window=8)
    turn_record_service = TurnRecordService(store)
    short_term_service = _build_short_term_service(store, history_source)
    analyzer_manager = StubConsolidationAnalyzerManager()
    consolidation_service = ConsolidationService(
        store,
        analyzer_manager=analyzer_manager,  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        consolidation_config=MemoryConsolidationConfig(
            enabled=True,
            min_short_term_updates=2,
        ),
    )
    experience_service = ExperienceService(store)
    memory_service = MemoryService(
        store,
        turn_record_service,
        short_term_service,
        MemorySnapshotBuilder(store),
        consolidation_service=consolidation_service,
        experience_service=experience_service,
    )
    now = datetime.now(UTC)

    try:
        for index in range(2):
            await memory_service.update_from_postprocess(
                _memory_update_request(
                    user_message={"role": "user", "content": f"Turn {index}"},
                    assistant_message={
                        "role": "assistant",
                        "content": f"Reply {index}",
                    },
                    message_timestamp=now + timedelta(microseconds=index),
                    source_refs=[],
                    platform_user_key=None,
                )
            )

        latest_insight = await store.get_latest_session_insight(
            TEST_CANONICAL_USER_ID,
            "conv-1",
        )
        experiences = await store.list_recent_experiences(TEST_CANONICAL_USER_ID, 10)
    finally:
        await store.close()

    assert analyzer_manager.calls == ["session_insight_update", "experience_extract"]
    assert latest_insight is not None
    assert latest_insight.platform_user_key is None
    assert len(experiences) == 2
    assert all(item.platform_user_key is None for item in experiences)


@pytest.mark.asyncio
async def test_memory_service_update_triggers_long_term_promotion_after_consolidation(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    history_source = RecentConversationSource(store, recent_turns_window=8)
    turn_record_service = TurnRecordService(store)
    short_term_service = _build_short_term_service(store, history_source)
    consolidation_service = ConsolidationService(
        store,
        analyzer_manager=StubConsolidationAnalyzerManager(),  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        consolidation_config=MemoryConsolidationConfig(
            enabled=True,
            min_short_term_updates=2,
        ),
    )
    long_term_analyzer = StubLongTermAnalyzerManager()
    long_term_service = LongTermMemoryService(
        store,
        analyzer_manager=long_term_analyzer,  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        long_term_config=MemoryLongTermConfig(
            enabled=True,
            min_experience_importance=0.7,
            min_pending_experiences=2,
        ),
    )
    memory_service = MemoryService(
        store,
        turn_record_service,
        short_term_service,
        MemorySnapshotBuilder(store),
        consolidation_service=consolidation_service,
        experience_service=ExperienceService(store),
        long_term_service=long_term_service,
    )
    now = datetime.now(UTC)

    try:
        for index in range(2):
            await memory_service.update_from_postprocess(
                _memory_update_request(
                    user_message={"role": "user", "content": f"Turn {index}"},
                    assistant_message={
                        "role": "assistant",
                        "content": f"Reply {index}",
                    },
                    message_timestamp=now + timedelta(microseconds=index),
                    source_refs=[],
                )
            )
        memories = await store.list_long_term_memory_indexes(
            TEST_CANONICAL_USER_ID,
            10,
            scope_type="user",
            scope_id=TEST_CANONICAL_USER_ID,
        )
        snapshot = await memory_service.get_snapshot(TEST_UMO, "conv-1")
    finally:
        await store.close()

    assert long_term_analyzer.calls == ["long_term_promote", "long_term_compose"]
    assert len(memories) == 1
    assert len(snapshot.experiences) == 2
    assert len(snapshot.long_term_memories) == 1
    assert snapshot.long_term_memories[0].memory_id == memories[0].memory_id


@pytest.mark.asyncio
async def test_memory_service_keeps_database_results_when_projection_refresh_fails(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    history_source = RecentConversationSource(store, recent_turns_window=8)
    turn_record_service = TurnRecordService(store)
    short_term_service = _build_short_term_service(store, history_source)
    analyzer_manager = StubConsolidationAnalyzerManager()
    consolidation_service = ConsolidationService(
        store,
        analyzer_manager=analyzer_manager,  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        consolidation_config=MemoryConsolidationConfig(
            enabled=True,
            min_short_term_updates=2,
        ),
    )
    experience_service = ExperienceService(
        store,
        projection_service=FailingProjectionService(),  # type: ignore[arg-type]
    )
    memory_service = MemoryService(
        store,
        turn_record_service,
        short_term_service,
        MemorySnapshotBuilder(store),
        consolidation_service=consolidation_service,
        experience_service=experience_service,
    )
    now = datetime.now(UTC)

    try:
        for index in range(2):
            await memory_service.update_from_postprocess(
                _memory_update_request(
                    user_message={"role": "user", "content": f"Turn {index}"},
                    assistant_message={
                        "role": "assistant",
                        "content": f"Reply {index}",
                    },
                    message_timestamp=now + timedelta(microseconds=index),
                    source_refs=[],
                )
            )

        latest_insight = await store.get_latest_session_insight(
            TEST_CANONICAL_USER_ID,
            "conv-1",
        )
        experiences = await store.list_recent_experiences(
            TEST_CANONICAL_USER_ID,
            10,
            conversation_id="conv-1",
        )
        snapshot = await memory_service.get_snapshot(TEST_UMO, "conv-1")
    finally:
        await store.close()

    assert latest_insight is not None
    assert len(experiences) == 2
    assert len(snapshot.experiences) == 2


@pytest.mark.asyncio
async def test_memory_service_snapshot_returns_persona_state_and_long_term_context(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    history_source = RecentConversationSource(store, recent_turns_window=8)
    memory_service = MemoryService(
        store,
        TurnRecordService(store),
        _build_short_term_service(store, history_source),
        MemorySnapshotBuilder(store),
    )
    now = datetime.now(UTC)

    try:
        await store.save_turn_record(
            TurnRecord(
                turn_id="turn-snapshot-1",
                umo=TEST_UMO,
                conversation_id="conv-1",
                platform_id=TEST_PLATFORM_ID,
                platform_user_key=TEST_PLATFORM_USER_KEY,
                canonical_user_id=TEST_CANONICAL_USER_ID,
                session_id="session-1",
                user_message={"role": "user", "content": "Need memory context."},
                assistant_message={
                    "role": "assistant",
                    "content": "Here is the context.",
                },
                message_timestamp=now,
                source_refs=[],
                created_at=now,
            )
        )
        await store.upsert_topic_state(
            TopicState(
                umo=TEST_UMO,
                conversation_id="conv-1",
                current_topic="Snapshot assembly",
                topic_summary="Assemble a useful memory snapshot.",
                topic_confidence=0.91,
                last_active_at=now,
            )
        )
        await store.upsert_short_term_memory(
            ShortTermMemory(
                umo=TEST_UMO,
                conversation_id="conv-1",
                short_summary="The user wants the snapshot to include multiple memory layers.",
                active_focus="Return relevant memory data",
                updated_at=now,
            )
        )
        await store.save_experience(
            _experience(
                experience_id="exp-snapshot-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now,
                category="project_progress",
                summary="Built the snapshot aggregator",
                detail_summary="The snapshot now reads from persisted memory layers.",
                importance=0.85,
                confidence=0.93,
                source_refs=["turn:turn-snapshot-1"],
                created_at=now,
                updated_at=now,
            )
        )
        await store.upsert_long_term_memory_index(
            _long_term_memory_index(
                memory_id="ltm-snapshot-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                title="Snapshot should expose memory layers",
                summary="Snapshot should include recent experience and long-term memory context.",
                status="active",
                doc_path=str(temp_dir / "ltm-snapshot-1.md"),
                importance=0.84,
                confidence=0.9,
                tags=["snapshot", "memory"],
                source_refs=["exp:exp-snapshot-1"],
                first_event_at=now,
                last_event_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await store.upsert_persona_state(
            PersonaState(
                state_id="persona-state-1",
                scope_type=ScopeType.USER,
                scope_id=TEST_CANONICAL_USER_ID,
                persona_id=None,
                familiarity=0.6,
                trust=0.72,
                warmth=0.64,
                formality_preference=0.35,
                directness_preference=0.88,
                updated_at=now,
            )
        )

        snapshot = await memory_service.get_snapshot(TEST_UMO, "conv-1")
    finally:
        await store.close()

    assert snapshot.topic_state is not None
    assert snapshot.short_term_memory is not None
    assert snapshot.canonical_user_id == TEST_CANONICAL_USER_ID
    assert len(snapshot.experiences) == 1
    assert snapshot.experiences[0].experience_id == "exp-snapshot-1"
    assert len(snapshot.long_term_memories) == 1
    assert snapshot.long_term_memories[0].memory_id == "ltm-snapshot-1"
    assert snapshot.persona_state is not None
    assert snapshot.persona_state.scope_id == TEST_CANONICAL_USER_ID
    assert snapshot.persona_state.directness_preference == 0.88


@pytest.mark.asyncio
async def test_memory_service_keeps_short_term_when_consolidation_fails(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    history_source = RecentConversationSource(store, recent_turns_window=8)
    turn_record_service = TurnRecordService(store)
    short_term_service = _build_short_term_service(store, history_source)
    consolidation_service = ConsolidationService(
        store,
        analyzer_manager=InvalidConsolidationAnalyzerManager(),  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        consolidation_config=MemoryConsolidationConfig(
            enabled=True,
            min_short_term_updates=1,
        ),
    )
    memory_service = MemoryService(
        store,
        turn_record_service,
        short_term_service,
        MemorySnapshotBuilder(store),
        consolidation_service=consolidation_service,
        experience_service=ExperienceService(store),
    )
    now = datetime.now(UTC)

    try:
        with pytest.raises(MemoryAnalyzerExecutionError):
            await memory_service.update_from_postprocess(
                _memory_update_request(
                    user_message={"role": "user", "content": "Trigger consolidation"},
                    assistant_message={"role": "assistant", "content": "Reply"},
                    message_timestamp=now,
                    source_refs=[],
                )
            )
        snapshot = await memory_service.get_snapshot(TEST_UMO, "conv-1")
        experiences = await store.list_recent_experiences(TEST_CANONICAL_USER_ID, 10)
    finally:
        await store.close()

    assert snapshot.topic_state is not None
    assert snapshot.short_term_memory is not None
    assert experiences == []


@pytest.mark.asyncio
async def test_long_term_service_creates_memory_document_links_and_cursor(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    vector_root = (temp_dir / "vector_index").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: false",
                f'  root_dir: "{vector_root}"',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    analyzer_manager = StubLongTermAnalyzerManager()
    long_term_service = LongTermMemoryService(
        store,
        analyzer_manager=analyzer_manager,  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        long_term_config=MemoryLongTermConfig(
            enabled=True,
            min_experience_importance=0.7,
            min_pending_experiences=2,
        ),
    )
    now = datetime.now(UTC)

    try:
        await store.save_experience(
            _experience(
                experience_id="exp-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now,
                category="project_progress",
                summary="Implemented memory postprocess integration",
                detail_summary="The memory pipeline was integrated after message send.",
                importance=0.82,
                confidence=0.91,
                source_refs=["turn:1"],
                created_at=now,
                updated_at=now,
            )
        )
        await store.save_experience(
            _experience(
                experience_id="exp-2",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now + timedelta(seconds=1),
                category="project_progress",
                summary="Settled on memory-first roadmap",
                detail_summary="The team aligned around shipping memory before prompt integration.",
                importance=0.88,
                confidence=0.93,
                source_refs=["turn:2"],
                created_at=now + timedelta(microseconds=1),
                updated_at=now + timedelta(microseconds=1),
            )
        )

        should_run = await long_term_service.should_run_promotion(
            TEST_CANONICAL_USER_ID,
        )
        persisted = await long_term_service.run_promotion(
            TEST_CANONICAL_USER_ID,
        )
        links = await store.list_long_term_memory_links(persisted[0].memory_id)
        cursor = await store.get_long_term_promotion_cursor(
            TEST_CANONICAL_USER_ID,
            "user",
            TEST_CANONICAL_USER_ID,
        )
    finally:
        await store.close()

    assert should_run is True
    assert analyzer_manager.calls == ["long_term_promote", "long_term_compose"]
    assert len(persisted) == 1
    assert persisted[0].title == "Memory-first implementation preference"
    assert persisted[0].status == LongTermMemoryStatus.ACTIVE.value
    assert Path(persisted[0].doc_path).exists()
    content = Path(persisted[0].doc_path).read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "# Memory-first implementation preference" in content
    assert "## Supporting Experiences" in content
    assert len(links) == 2
    assert cursor is not None
    assert cursor.last_processed_experience_id == "exp-2"
    assert analyzer_manager.promote_existing_memories == []


@pytest.mark.asyncio
async def test_long_term_service_uses_vector_candidates_for_promotion_context(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    vector_root = (temp_dir / "vector_index").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: true",
                f'  root_dir: "{vector_root}"',
                "  long_term_top_k: 1",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    loader = DocumentLoader(config)
    analyzer_manager = StubLongTermAnalyzerManager()
    vector_index = StubVectorIndex([("ltm-target", 0.97)])
    long_term_service = LongTermMemoryService(
        store,
        analyzer_manager=analyzer_manager,  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        long_term_config=MemoryLongTermConfig(
            enabled=True,
            min_experience_importance=0.7,
            min_pending_experiences=2,
        ),
        vector_index=vector_index,  # type: ignore[arg-type]
    )
    now = datetime.now(UTC)

    try:
        for memory_id, title in (
            ("ltm-target", "Memory-first implementation preference"),
            ("ltm-other", "Frontend polishing story"),
        ):
            doc_path = loader.save_long_term_document(
                LongTermMemoryDocument(
                    memory_id=memory_id,
                    umo=TEST_UMO,
                    canonical_user_id=TEST_CANONICAL_USER_ID,
                    scope_type="user",
                    scope_id=TEST_CANONICAL_USER_ID,
                    category="project_progress",
                    status="active",
                    title=title,
                    summary=title,
                    detail_summary=f"Detail for {title}.",
                    importance=0.8,
                    confidence=0.9,
                    supporting_experiences=[],
                    updates=[],
                    source_refs=[],
                    tags=["memory"],
                    first_event_at=now,
                    last_event_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            await store.upsert_long_term_memory_index(
                _long_term_memory_index(
                    memory_id=memory_id,
                    scope_type="user",
                    scope_id=TEST_CANONICAL_USER_ID,
                    category="project_progress",
                    title=title,
                    summary=title,
                    status="active",
                    doc_path=str(doc_path),
                    importance=0.8,
                    confidence=0.9,
                    tags=["memory"],
                    source_refs=[],
                    first_event_at=now,
                    last_event_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )

        await store.save_experience(
            _experience(
                experience_id="exp-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now,
                category="project_progress",
                summary="Implemented memory postprocess integration",
                detail_summary="The memory pipeline was integrated after message send.",
                importance=0.82,
                confidence=0.91,
                source_refs=["turn:1"],
                created_at=now,
                updated_at=now,
            )
        )
        await store.save_experience(
            _experience(
                experience_id="exp-2",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now + timedelta(seconds=1),
                category="project_progress",
                summary="Settled on memory-first roadmap",
                detail_summary="The team aligned around shipping memory before prompt integration.",
                importance=0.88,
                confidence=0.93,
                source_refs=["turn:2"],
                created_at=now + timedelta(microseconds=1),
                updated_at=now + timedelta(microseconds=1),
            )
        )

        await long_term_service.run_promotion(TEST_CANONICAL_USER_ID)
    finally:
        await store.close()

    assert len(vector_index.calls) == 1
    assert vector_index.calls[0]["metadata_filters"] == {
        "scope_type": "user",
        "scope_id": TEST_CANONICAL_USER_ID,
    }
    assert len(analyzer_manager.promote_existing_memories) == 1
    assert analyzer_manager.promote_existing_memories[0]["memory_id"] == "ltm-target"


@pytest.mark.asyncio
async def test_long_term_service_merges_experience_and_context_vector_candidates(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    vector_root = (temp_dir / "vector_index").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: true",
                f'  root_dir: "{vector_root}"',
                "  long_term_top_k: 2",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    loader = DocumentLoader(config)
    analyzer_manager = StubLongTermAnalyzerManager()
    vector_index = SequencedStubVectorIndex(
        [
            [("ltm-experience", 0.97)],
            [("ltm-context", 0.94)],
        ]
    )
    long_term_service = LongTermMemoryService(
        store,
        analyzer_manager=analyzer_manager,  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        long_term_config=MemoryLongTermConfig(
            enabled=True,
            min_experience_importance=0.7,
            min_pending_experiences=2,
        ),
        vector_index=vector_index,  # type: ignore[arg-type]
    )
    now = datetime.now(UTC)

    try:
        for memory_id, title in (
            ("ltm-experience", "Memory-first implementation preference"),
            ("ltm-context", "Active roadmap planning"),
        ):
            doc_path = loader.save_long_term_document(
                LongTermMemoryDocument(
                    memory_id=memory_id,
                    umo=TEST_UMO,
                    canonical_user_id=TEST_CANONICAL_USER_ID,
                    scope_type="user",
                    scope_id=TEST_CANONICAL_USER_ID,
                    category="project_progress",
                    status="active",
                    title=title,
                    summary=title,
                    detail_summary=f"Detail for {title}.",
                    importance=0.8,
                    confidence=0.9,
                    supporting_experiences=[],
                    updates=[],
                    source_refs=[],
                    tags=["memory"],
                    first_event_at=now,
                    last_event_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            await store.upsert_long_term_memory_index(
                _long_term_memory_index(
                    memory_id=memory_id,
                    scope_type="user",
                    scope_id=TEST_CANONICAL_USER_ID,
                    category="project_progress",
                    title=title,
                    summary=title,
                    status="active",
                    doc_path=str(doc_path),
                    importance=0.8,
                    confidence=0.9,
                    tags=["memory"],
                    source_refs=[],
                    first_event_at=now,
                    last_event_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )

        await store.upsert_topic_state(
            TopicState(
                umo=TEST_UMO,
                conversation_id="conv-1",
                current_topic="Roadmap planning",
                topic_summary="The discussion is currently focused on memory roadmap decisions.",
                topic_confidence=0.95,
                last_active_at=now,
            )
        )
        await store.upsert_short_term_memory(
            ShortTermMemory(
                umo=TEST_UMO,
                conversation_id="conv-1",
                short_summary="The user wants to connect long-term memory with the active roadmap topic.",
                active_focus="Choose the next memory milestone",
                updated_at=now,
            )
        )
        await store.save_experience(
            _experience(
                experience_id="exp-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now,
                category="project_progress",
                summary="Implemented memory postprocess integration",
                detail_summary="The memory pipeline was integrated after message send.",
                importance=0.82,
                confidence=0.91,
                source_refs=["turn:1"],
                created_at=now,
                updated_at=now,
            )
        )
        await store.save_experience(
            _experience(
                experience_id="exp-2",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now + timedelta(seconds=1),
                category="project_progress",
                summary="Settled on memory-first roadmap",
                detail_summary="The team aligned around shipping memory before prompt integration.",
                importance=0.88,
                confidence=0.93,
                source_refs=["turn:2"],
                created_at=now + timedelta(microseconds=1),
                updated_at=now + timedelta(microseconds=1),
            )
        )

        await long_term_service.run_promotion(TEST_CANONICAL_USER_ID)
    finally:
        await store.close()

    assert len(vector_index.calls) == 2
    assert "Implemented memory postprocess integration" in str(
        vector_index.calls[0]["query"]
    )
    assert "Keywords:" in str(vector_index.calls[0]["query"])
    assert "Roadmap planning" in str(vector_index.calls[1]["query"])
    assert "Choose the next memory milestone" in str(vector_index.calls[1]["query"])
    assert "Keywords:" in str(vector_index.calls[1]["query"])
    assert {
        item["memory_id"] for item in analyzer_manager.promote_existing_memories
    } == {"ltm-experience", "ltm-context"}


@pytest.mark.asyncio
async def test_long_term_service_rolls_back_markdown_when_db_commit_fails(
    temp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    analyzer_manager = StubLongTermAnalyzerManager()
    service = LongTermMemoryService(
        store,
        analyzer_manager=analyzer_manager,  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        long_term_config=MemoryLongTermConfig(
            enabled=True,
            min_experience_importance=0.7,
            min_pending_experiences=1,
        ),
    )
    now = datetime.now(UTC)

    async def failing_persist_batch(memories, links, cursor):  # noqa: ANN001
        del memories, links, cursor
        raise RuntimeError("db batch failed")

    monkeypatch.setattr(
        store, "persist_long_term_promotion_batch", failing_persist_batch
    )

    try:
        await store.save_experience(
            _experience(
                experience_id="exp-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now,
                category="project_progress",
                summary="Implemented memory postprocess integration",
                detail_summary="The memory pipeline was integrated after message send.",
                importance=0.82,
                confidence=0.91,
                source_refs=["turn:1"],
                created_at=now,
                updated_at=now,
            )
        )

        with pytest.raises(RuntimeError, match="db batch failed"):
            await service.run_promotion(TEST_CANONICAL_USER_ID)
    finally:
        await store.close()

    assert list((temp_dir / "long_term").rglob("*.md")) == []


@pytest.mark.asyncio
async def test_long_term_service_restores_existing_markdown_when_db_commit_fails(
    temp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    loader = DocumentLoader(config)
    now = datetime.now(UTC)

    async def failing_persist_batch(memories, links, cursor):  # noqa: ANN001
        del memories, links, cursor
        raise RuntimeError("db batch failed")

    monkeypatch.setattr(
        store, "persist_long_term_promotion_batch", failing_persist_batch
    )

    try:
        existing_path = loader.save_long_term_document(
            LongTermMemoryDocument(
                memory_id="ltm-existing",
                umo=TEST_UMO,
                canonical_user_id=TEST_CANONICAL_USER_ID,
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                status="active",
                title="Existing memory-first preference",
                summary="The user prefers memory-first sequencing.",
                detail_summary="Original detail summary.",
                supporting_experiences=["exp-old"],
                updates=[{"timestamp": now.isoformat(), "summary": "created"}],
                source_refs=["exp:old"],
                tags=["memory"],
                first_event_at=now - timedelta(days=1),
                last_event_at=now - timedelta(days=1),
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
        )
        before_content = existing_path.read_text(encoding="utf-8")
        await store.upsert_long_term_memory_index(
            _long_term_memory_index(
                memory_id="ltm-existing",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                title="Existing memory-first preference",
                summary="The user prefers memory-first sequencing.",
                status="active",
                doc_path=str(existing_path),
                importance=0.8,
                confidence=0.85,
                tags=["memory"],
                source_refs=["exp:old"],
                first_event_at=now - timedelta(days=1),
                last_event_at=now - timedelta(days=1),
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
        )
        await store.save_experience(
            _experience(
                experience_id="exp-new",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now,
                category="project_progress",
                summary="A new experience reinforced the roadmap preference",
                detail_summary="The roadmap preference was reinforced again.",
                importance=0.9,
                confidence=0.95,
                source_refs=["turn:new"],
                created_at=now,
                updated_at=now,
            )
        )
        service = LongTermMemoryService(
            store,
            analyzer_manager=StubLongTermUpdateAnalyzerManager("ltm-existing"),  # type: ignore[arg-type]
            analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
            long_term_config=MemoryLongTermConfig(
                enabled=True,
                min_experience_importance=0.7,
                min_pending_experiences=1,
            ),
            document_loader=loader,
        )

        with pytest.raises(RuntimeError, match="db batch failed"):
            await service.run_promotion(TEST_CANONICAL_USER_ID)
        after_content = existing_path.read_text(encoding="utf-8")
    finally:
        await store.close()

    assert after_content == before_content


@pytest.mark.asyncio
async def test_long_term_service_updates_existing_memory(temp_dir: Path):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    loader = DocumentLoader(config)
    now = datetime.now(UTC)

    try:
        existing_path = loader.save_long_term_document(
            LongTermMemoryDocument(
                memory_id="ltm-1",
                umo=TEST_UMO,
                canonical_user_id=TEST_CANONICAL_USER_ID,
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                status="active",
                title="Existing memory-first preference",
                summary="The user prefers memory-first sequencing.",
                detail_summary="Original detail summary.",
                supporting_experiences=["exp-old"],
                updates=[{"timestamp": now.isoformat(), "summary": "created"}],
                source_refs=["exp:old"],
                tags=["memory"],
                first_event_at=now - timedelta(days=1),
                last_event_at=now - timedelta(days=1),
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
        )
        await store.upsert_long_term_memory_index(
            _long_term_memory_index(
                memory_id="ltm-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                title="Existing memory-first preference",
                summary="The user prefers memory-first sequencing.",
                status="active",
                doc_path=str(existing_path),
                importance=0.8,
                confidence=0.85,
                tags=["memory"],
                source_refs=["exp:old"],
                first_event_at=now - timedelta(days=1),
                last_event_at=now - timedelta(days=1),
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
        )
        await store.save_experience(
            _experience(
                experience_id="exp-new",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now,
                category="project_progress",
                summary="A new experience reinforced the roadmap preference",
                detail_summary="The roadmap preference was reinforced again.",
                importance=0.9,
                confidence=0.95,
                source_refs=["turn:new"],
                created_at=now,
                updated_at=now,
            )
        )
        analyzer_manager = StubLongTermUpdateAnalyzerManager("ltm-1")
        service = LongTermMemoryService(
            store,
            analyzer_manager=analyzer_manager,  # type: ignore[arg-type]
            analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
            long_term_config=MemoryLongTermConfig(
                enabled=True,
                min_experience_importance=0.7,
                min_pending_experiences=1,
            ),
            document_loader=loader,
        )

        persisted = await service.run_promotion(TEST_CANONICAL_USER_ID)
        links = await store.list_long_term_memory_links("ltm-1")
        updated_doc = loader.load_long_term_document(existing_path)
    finally:
        await store.close()

    assert len(persisted) == 1
    assert persisted[0].memory_id == "ltm-1"
    assert persisted[0].title == "Updated memory-first implementation preference"
    assert len(links) == 1
    assert updated_doc.title == "Updated memory-first implementation preference"
    assert "exp-new" in updated_doc.supporting_experiences
    assert len(analyzer_manager.promote_existing_memories) == 1
    assert analyzer_manager.promote_existing_memories[0]["detail_summary"] == (
        "Original detail summary."
    )
    assert analyzer_manager.promote_existing_memories[0]["updates"] == [
        {"timestamp": now.isoformat(), "summary": "created"}
    ]


@pytest.mark.asyncio
async def test_long_term_service_stops_before_db_when_markdown_prepare_fails(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    analyzer_manager = StubLongTermAnalyzerManager()
    service = LongTermMemoryService(
        store,
        analyzer_manager=analyzer_manager,  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        long_term_config=MemoryLongTermConfig(
            enabled=True,
            min_experience_importance=0.7,
            min_pending_experiences=1,
        ),
        document_loader=FailingDocumentLoader(config),
    )
    now = datetime.now(UTC)

    try:
        await store.save_experience(
            _experience(
                experience_id="exp-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now,
                category="project_progress",
                summary="Implemented memory postprocess integration",
                detail_summary="The memory pipeline was integrated after message send.",
                importance=0.82,
                confidence=0.91,
                source_refs=["turn:1"],
                created_at=now,
                updated_at=now,
            )
        )

        with pytest.raises(RuntimeError, match="long-term markdown write failed"):
            await service.run_promotion(TEST_CANONICAL_USER_ID)
        stored_memory = await store.list_long_term_memory_indexes(
            TEST_CANONICAL_USER_ID,
            limit=0,
            scope_type="user",
            scope_id=TEST_CANONICAL_USER_ID,
        )
        cursor = await store.get_long_term_promotion_cursor(
            TEST_CANONICAL_USER_ID,
            "user",
            TEST_CANONICAL_USER_ID,
        )
    finally:
        await store.close()

    assert stored_memory == []
    assert cursor is None
    assert list((temp_dir / "long_term").rglob("*.md")) == []


@pytest.mark.asyncio
async def test_long_term_service_raises_when_promote_does_not_cover_all_candidates(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    service = LongTermMemoryService(
        store,
        analyzer_manager=MissingCoverageLongTermAnalyzerManager(),  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        long_term_config=MemoryLongTermConfig(
            enabled=True,
            min_experience_importance=0.7,
            min_pending_experiences=2,
        ),
    )
    now = datetime.now(UTC)

    try:
        for offset, exp_id in enumerate(("exp-1", "exp-2")):
            await store.save_experience(
                _experience(
                    experience_id=exp_id,
                    scope_type="user",
                    scope_id=TEST_CANONICAL_USER_ID,
                    event_time=now + timedelta(seconds=offset),
                    category="project_progress",
                    summary=f"Experience {exp_id}",
                    detail_summary="Promotion coverage should fail.",
                    importance=0.82,
                    confidence=0.91,
                    source_refs=[f"turn:{exp_id}"],
                    created_at=now + timedelta(microseconds=offset),
                    updated_at=now + timedelta(microseconds=offset),
                )
            )

        with pytest.raises(
            MemoryAnalyzerExecutionError,
            match="did not cover all candidate experiences",
        ):
            await service.run_promotion(TEST_CANONICAL_USER_ID)

        cursor = await store.get_long_term_promotion_cursor(
            TEST_CANONICAL_USER_ID,
            "user",
            TEST_CANONICAL_USER_ID,
        )
        memories = await store.list_long_term_memory_indexes(
            TEST_CANONICAL_USER_ID,
            limit=0,
            scope_type="user",
            scope_id=TEST_CANONICAL_USER_ID,
        )
    finally:
        await store.close()

    assert cursor is None
    assert memories == []


@pytest.mark.asyncio
async def test_long_term_service_raises_when_promote_duplicates_experience_coverage(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    service = LongTermMemoryService(
        store,
        analyzer_manager=DuplicateCoverageLongTermAnalyzerManager(),  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        long_term_config=MemoryLongTermConfig(
            enabled=True,
            min_experience_importance=0.7,
            min_pending_experiences=2,
        ),
    )
    now = datetime.now(UTC)

    try:
        for offset, exp_id in enumerate(("exp-1", "exp-2")):
            await store.save_experience(
                _experience(
                    experience_id=exp_id,
                    scope_type="user",
                    scope_id=TEST_CANONICAL_USER_ID,
                    event_time=now + timedelta(seconds=offset),
                    category="project_progress",
                    summary=f"Experience {exp_id}",
                    detail_summary="Promotion duplicate coverage should fail.",
                    importance=0.82,
                    confidence=0.91,
                    source_refs=[f"turn:{exp_id}"],
                    created_at=now + timedelta(microseconds=offset),
                    updated_at=now + timedelta(microseconds=offset),
                )
            )

        with pytest.raises(
            MemoryAnalyzerExecutionError,
            match="duplicate experience coverage",
        ):
            await service.run_promotion(TEST_CANONICAL_USER_ID)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_long_term_service_raises_on_duplicate_update_target_in_same_batch(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    loader = DocumentLoader(config)
    now = datetime.now(UTC)

    try:
        existing_path = loader.save_long_term_document(
            LongTermMemoryDocument(
                memory_id="ltm-1",
                umo=TEST_UMO,
                canonical_user_id=TEST_CANONICAL_USER_ID,
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                status="active",
                title="Existing memory-first preference",
                summary="The user prefers memory-first sequencing.",
                detail_summary="Original detail summary.",
                supporting_experiences=["exp-old"],
                updates=[{"timestamp": now.isoformat(), "summary": "created"}],
                source_refs=["exp:old"],
                tags=["memory"],
                first_event_at=now - timedelta(days=1),
                last_event_at=now - timedelta(days=1),
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
        )
        await store.upsert_long_term_memory_index(
            _long_term_memory_index(
                memory_id="ltm-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                title="Existing memory-first preference",
                summary="The user prefers memory-first sequencing.",
                status="active",
                doc_path=str(existing_path),
                importance=0.8,
                confidence=0.85,
                tags=["memory"],
                source_refs=["exp:old"],
                first_event_at=now - timedelta(days=1),
                last_event_at=now - timedelta(days=1),
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
        )
        for offset, exp_id in enumerate(("exp-1", "exp-2")):
            await store.save_experience(
                _experience(
                    experience_id=exp_id,
                    scope_type="user",
                    scope_id=TEST_CANONICAL_USER_ID,
                    event_time=now + timedelta(seconds=offset),
                    category="project_progress",
                    summary=f"Experience {exp_id}",
                    detail_summary="Duplicate update target should fail.",
                    importance=0.9,
                    confidence=0.95,
                    source_refs=[f"turn:{exp_id}"],
                    created_at=now + timedelta(microseconds=offset),
                    updated_at=now + timedelta(microseconds=offset),
                )
            )
        service = LongTermMemoryService(
            store,
            analyzer_manager=DuplicateUpdateTargetLongTermAnalyzerManager("ltm-1"),  # type: ignore[arg-type]
            analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
            long_term_config=MemoryLongTermConfig(
                enabled=True,
                min_experience_importance=0.7,
                min_pending_experiences=2,
            ),
            document_loader=loader,
        )

        with pytest.raises(
            MemoryAnalyzerExecutionError,
            match="duplicate update target",
        ):
            await service.run_promotion(TEST_CANONICAL_USER_ID)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_long_term_service_raises_on_invalid_analyzer_payload(temp_dir: Path):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    service = LongTermMemoryService(
        store,
        analyzer_manager=InvalidLongTermAnalyzerManager(),  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        long_term_config=MemoryLongTermConfig(
            enabled=True,
            min_experience_importance=0.7,
            min_pending_experiences=1,
        ),
    )
    now = datetime.now(UTC)

    try:
        await store.save_experience(
            _experience(
                experience_id="exp-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now,
                category="project_progress",
                summary="Trigger invalid promotion",
                detail_summary="Invalid analyzer payload should raise.",
                importance=0.9,
                confidence=0.95,
                source_refs=["turn:1"],
                created_at=now,
                updated_at=now,
            )
        )
        with pytest.raises(MemoryAnalyzerExecutionError):
            await service.run_promotion(TEST_CANONICAL_USER_ID)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_long_term_service_marks_vector_dirty_when_refresh_fails(temp_dir: Path):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: true",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    service = LongTermMemoryService(
        store,
        analyzer_manager=StubLongTermAnalyzerManager(),  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        long_term_config=MemoryLongTermConfig(
            enabled=True,
            min_experience_importance=0.7,
            min_pending_experiences=1,
        ),
        vector_index=FailingPromotionVectorIndex(),  # type: ignore[arg-type]
    )
    now = datetime.now(UTC)

    try:
        await store.save_experience(
            _experience(
                experience_id="exp-vector-dirty",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now,
                category="project_progress",
                summary="Promotion vector failure",
                detail_summary="The vector refresh fails after persistence.",
                importance=0.9,
                confidence=0.95,
                source_refs=["turn:1"],
                created_at=now,
                updated_at=now,
            )
        )
        persisted = await service.run_promotion(TEST_CANONICAL_USER_ID)
        loaded = await store.get_long_term_memory_index(persisted[0].memory_id)
    finally:
        await store.close()

    assert len(persisted) == 1
    assert loaded is not None
    assert loaded.vector_sync_status == LongTermVectorSyncStatus.DIRTY
    assert loaded.vector_sync_error == "promotion vector refresh failed"
    assert Path(loaded.doc_path).exists()


@pytest.mark.asyncio
async def test_long_term_service_raises_on_out_of_range_compose_score(temp_dir: Path):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    service = LongTermMemoryService(
        store,
        analyzer_manager=OutOfRangeLongTermAnalyzerManager(),  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
        long_term_config=MemoryLongTermConfig(
            enabled=True,
            min_experience_importance=0.7,
            min_pending_experiences=1,
        ),
    )
    now = datetime.now(UTC)

    try:
        await store.save_experience(
            _experience(
                experience_id="exp-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now,
                category="project_progress",
                summary="Trigger out-of-range compose score",
                detail_summary="The analyzer score should be rejected.",
                importance=0.9,
                confidence=0.95,
                source_refs=["turn:1"],
                created_at=now,
                updated_at=now,
            )
        )
        with pytest.raises(
            MemoryAnalyzerExecutionError,
            match="must be between 0 and 1",
        ):
            await service.run_promotion(TEST_CANONICAL_USER_ID)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_document_search_service_hydrates_results_and_loads_body(temp_dir: Path):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    loader = DocumentLoader(config)
    now = datetime.now(UTC)

    try:
        doc_path = loader.save_long_term_document(
            LongTermMemoryDocument(
                memory_id="ltm-1",
                umo=TEST_UMO,
                canonical_user_id=TEST_CANONICAL_USER_ID,
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                status="active",
                title="Searchable long-term memory",
                summary="This memory should be returned by document search.",
                detail_summary="The body contains the long-term detail text.",
                supporting_experiences=["exp-1"],
                updates=[{"timestamp": now.isoformat(), "summary": "created"}],
                source_refs=["exp:exp-1"],
                tags=["memory", "search"],
                first_event_at=now,
                last_event_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await store.upsert_long_term_memory_index(
            _long_term_memory_index(
                memory_id="ltm-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                title="Searchable long-term memory",
                summary="This memory should be returned by document search.",
                status="active",
                doc_path=str(doc_path),
                importance=0.85,
                confidence=0.9,
                tags=["memory", "search"],
                source_refs=["exp:exp-1"],
                first_event_at=now,
                last_event_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        vector_index = StubVectorIndex([("ltm-1", 0.93)])
        service = DocumentSearchService(
            store,
            vector_index=vector_index,  # type: ignore[arg-type]
            document_loader=loader,
        )

        results = await service.search_long_term_memories(
            _document_search_request(
                query="memory roadmap",
                include_body=True,
                conversation_id=None,
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
            )
        )
    finally:
        await store.close()

    assert len(results) == 1
    assert results[0].memory_id == "ltm-1"
    assert results[0].body_text is not None
    assert results[0].body_text.startswith("# Searchable long-term memory")
    assert "## Summary" in results[0].body_text
    assert "memory_id:" not in results[0].body_text
    assert "---" not in results[0].body_text
    assert vector_index.calls[0]["metadata_filters"] == {
        "scope_type": "user",
        "scope_id": TEST_CANONICAL_USER_ID,
    }


def test_document_loader_raises_on_missing_front_matter(temp_dir: Path):
    loader = DocumentLoader()
    doc_path = temp_dir / "invalid.md"
    doc_path.write_text("# Missing front matter\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing YAML front matter"):
        loader.load_long_term_document(doc_path)


def test_document_loader_raises_on_missing_required_field(temp_dir: Path):
    loader = DocumentLoader()
    doc_path = temp_dir / "missing-field.md"
    doc_path.write_text(
        "\n".join(
            [
                "---",
                "memory_id: ltm-1",
                "umo: test:private:user",
                "canonical_user_id: canonical-user-1",
                "scope_type: user",
                "scope_id: canonical-user-1",
                "category: project_progress",
                "status: active",
                "importance: 0.8",
                "confidence: 0.9",
                "---",
                "",
                "# Missing title",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing required field `title`"):
        loader.load_long_term_document(doc_path)


def test_document_loader_extracts_fenced_sections(temp_dir: Path):
    loader = DocumentLoader()
    doc_path = temp_dir / "fenced.md"
    doc_path.write_text(
        "\n".join(
            [
                "---",
                "memory_id: ltm-fenced",
                "umo: test:private:user",
                "canonical_user_id: canonical-user-1",
                "scope_type: user",
                "scope_id: canonical-user-1",
                "category: project_progress",
                "status: active",
                "title: Fenced Memory",
                "importance: 0.8",
                "confidence: 0.9",
                "---",
                "",
                "# Fenced Memory",
                "",
                "## Summary",
                "```text",
                "Summary line 1",
                "Summary line 2",
                "```",
                "",
                "## Detail",
                "```text",
                "Detail line 1",
                "Detail line 2",
                "```",
            ]
        ),
        encoding="utf-8",
    )

    document = loader.load_long_term_document(doc_path)

    assert document.summary == "Summary line 1\nSummary line 2"
    assert document.detail_summary == "Detail line 1\nDetail line 2"


def test_document_serializer_builds_structured_search_text_with_keywords():
    serializer = DocumentSerializer()
    now = datetime.now(UTC)

    search_text = serializer.build_search_text(
        _long_term_memory_index(
            memory_id="ltm-serializer",
            scope_type="user",
            scope_id=TEST_CANONICAL_USER_ID,
            category="project_progress",
            title="记忆优先实现方案",
            summary="用户持续强调先完成 memory 和向量检索能力。",
            status="active",
            doc_path="ignored.md",
            importance=0.9,
            confidence=0.95,
            tags=["memory", "vector"],
            source_refs=[],
            first_event_at=now,
            last_event_at=now,
            created_at=now,
            updated_at=now,
        ),
        LongTermMemoryDocument(
            memory_id="ltm-serializer",
            umo=TEST_UMO,
            canonical_user_id=TEST_CANONICAL_USER_ID,
            scope_type="user",
            scope_id=TEST_CANONICAL_USER_ID,
            category="project_progress",
            status="active",
            title="记忆优先实现方案",
            summary="用户持续强调先完成 memory 和向量检索能力。",
            detail_summary="长期记忆应该先具备 story 检索和向量召回。",
            importance=0.9,
            confidence=0.95,
            supporting_experiences=["exp-1", "exp-2"],
            updates=[
                {
                    "timestamp": now.isoformat(),
                    "action": "update",
                    "summary": "完成向量检索接入",
                }
            ],
            source_refs=[],
            tags=["memory", "vector"],
            first_event_at=now,
            last_event_at=now,
            created_at=now,
            updated_at=now,
        ),
    )

    assert "Supporting Experiences:" not in search_text
    assert "exp-1" not in search_text
    assert "Updates:" in search_text
    assert "完成向量检索接入" in search_text
    assert "Keywords:" in search_text
    assert "memory" in search_text.lower()
    keyword_line = next(
        line for line in search_text.splitlines() if line.startswith("Keywords:")
    )
    assert "exp-1" not in keyword_line
    assert "exp-2" not in keyword_line


def test_document_serializer_respects_keyword_extraction_top_k(temp_dir: Path):
    config_path = temp_dir / "memory-config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "keyword_extraction:",
                "  enabled: true",
                '  implementation: "jieba_tfidf"',
                "  top_k: 2",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    serializer = DocumentSerializer(config=config)
    now = datetime.now(UTC)

    search_text = serializer.build_search_text(
        _long_term_memory_index(
            memory_id="ltm-top-k",
            scope_type="user",
            scope_id=TEST_CANONICAL_USER_ID,
            category="project_progress",
            title="Memory roadmap vector roadmap",
            summary="Memory roadmap vector pipeline search search",
            status="active",
            doc_path="ignored.md",
            importance=0.9,
            confidence=0.95,
            tags=["memory", "vector", "roadmap"],
            source_refs=[],
            first_event_at=now,
            last_event_at=now,
            created_at=now,
            updated_at=now,
        )
    )

    keyword_line = next(
        line for line in search_text.splitlines() if line.startswith("Keywords:")
    )
    keywords = [
        item.strip() for item in keyword_line.removeprefix("Keywords:").split(",")
    ]
    keywords = [item for item in keywords if item]
    assert len(keywords) <= 2


@pytest.mark.asyncio
async def test_memory_vector_index_upserts_and_searches_real_faiss(temp_dir: Path):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    vector_root = (temp_dir / "vector_index").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: true",
                '  provider_id: "embedding-test"',
                f'  root_dir: "{vector_root}"',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    loader = DocumentLoader(config)
    provider = DummyEmbeddingProvider("embedding-test")
    provider_manager = DummyEmbeddingProviderManager(provider)
    vector_index = MemoryVectorIndex(store, config=config, document_loader=loader)
    vector_index.bind_provider_manager(provider_manager)
    now = datetime.now(UTC)

    try:
        doc_path = loader.save_long_term_document(
            LongTermMemoryDocument(
                memory_id="ltm-real-1",
                umo=TEST_UMO,
                canonical_user_id=TEST_CANONICAL_USER_ID,
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                status="active",
                title="Memory project roadmap",
                summary="The project roadmap should stay searchable in long-term memory.",
                detail_summary="This memory records the project roadmap and memory search behavior.",
                tags=["memory", "project", "search"],
                importance=0.9,
                confidence=0.93,
                created_at=now,
                updated_at=now,
            )
        )
        await store.upsert_long_term_memory_index(
            _long_term_memory_index(
                memory_id="ltm-real-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                title="Memory project roadmap",
                summary="The project roadmap should stay searchable in long-term memory.",
                status="active",
                doc_path=str(doc_path),
                importance=0.9,
                confidence=0.93,
                tags=["memory", "project", "search"],
                source_refs=["exp:1"],
                first_event_at=now,
                last_event_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await vector_index.upsert_long_term_memory("ltm-real-1")
        hits = await vector_index.search_long_term_memories(
            TEST_CANONICAL_USER_ID,
            "memory project search",
            top_k=3,
        )
    finally:
        await store.close()

    assert hits
    assert hits[0].memory_id == "ltm-real-1"
    assert isinstance(hits[0].score, float)


@pytest.mark.asyncio
async def test_memory_vector_index_falls_back_to_first_embedding_provider_when_provider_id_missing(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    vector_root = (temp_dir / "vector_index").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: true",
                '  provider_id: ""',
                f'  root_dir: "{vector_root}"',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    loader = DocumentLoader(config)
    provider = DummyEmbeddingProvider("embedding-fallback")
    provider_manager = DummyEmbeddingProviderManager(provider)
    vector_index = MemoryVectorIndex(store, config=config, document_loader=loader)
    vector_index.bind_provider_manager(provider_manager)
    now = datetime.now(UTC)

    try:
        doc_path = loader.save_long_term_document(
            LongTermMemoryDocument(
                memory_id="ltm-fallback-1",
                umo=TEST_UMO,
                canonical_user_id=TEST_CANONICAL_USER_ID,
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="user_preference",
                status="active",
                title="Search fallback memory",
                summary="Fallback provider selection should still support search.",
                detail_summary="The first embedding provider is reused when provider_id is absent.",
                tags=["fallback", "memory"],
                importance=0.84,
                confidence=0.9,
                created_at=now,
                updated_at=now,
            )
        )
        await store.upsert_long_term_memory_index(
            _long_term_memory_index(
                memory_id="ltm-fallback-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="user_preference",
                title="Search fallback memory",
                summary="Fallback provider selection should still support search.",
                status="active",
                doc_path=str(doc_path),
                importance=0.84,
                confidence=0.9,
                tags=["fallback", "memory"],
                source_refs=["exp:2"],
                first_event_at=now,
                last_event_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await vector_index.ensure_ready()
        await vector_index.upsert_long_term_memory("ltm-fallback-1")
        hits = await vector_index.search_long_term_memories(
            TEST_CANONICAL_USER_ID,
            "fallback memory",
            top_k=3,
        )
    finally:
        await store.close()

    assert hits
    assert hits[0].memory_id == "ltm-fallback-1"


@pytest.mark.asyncio
async def test_memory_vector_index_raises_when_configured_provider_is_missing(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    vector_root = (temp_dir / "vector_index").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: true",
                '  provider_id: "missing-provider"',
                f'  root_dir: "{vector_root}"',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    provider = DummyEmbeddingProvider("embedding-test")
    provider_manager = DummyEmbeddingProviderManager(provider)
    vector_index = MemoryVectorIndex(store, config=config)
    vector_index.bind_provider_manager(provider_manager)

    try:
        with pytest.raises(
            RuntimeError,
            match="embedding provider was not found: missing-provider",
        ):
            await vector_index.ensure_ready()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_memory_vector_index_raises_when_provider_is_not_embedding_provider(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    vector_root = (temp_dir / "vector_index").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: true",
                '  provider_id: "not-embedding"',
                f'  root_dir: "{vector_root}"',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    provider_manager = MagicMock()
    provider_manager.get_provider_by_id = AsyncMock(return_value=object())
    vector_index = MemoryVectorIndex(store, config=config)
    vector_index.bind_provider_manager(provider_manager)

    try:
        with pytest.raises(
            RuntimeError,
            match="provider is not an embedding provider: not-embedding",
        ):
            await vector_index.ensure_ready()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_long_term_manual_service_imports_new_document_and_normalizes_path(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: true",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    loader = DocumentLoader(config)
    vector_index = StubManualVectorIndex()
    service = LongTermMemoryManualService(
        store,
        document_loader=loader,
        vector_index=vector_index,  # type: ignore[arg-type]
    )
    now = datetime.now(UTC)
    source_path = temp_dir / "manual-source.md"

    try:
        loader.save_long_term_document(
            LongTermMemoryDocument(
                memory_id="manual-1",
                umo=TEST_UMO,
                canonical_user_id=TEST_CANONICAL_USER_ID,
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="user_preference",
                status="active",
                title="Manual imported memory",
                summary="The user wants manual long-term imports.",
                detail_summary="This document was created manually before import.",
                importance=0.88,
                confidence=0.91,
                supporting_experiences=["exp-manual"],
                updates=[{"timestamp": now.isoformat(), "summary": "created"}],
                source_refs=["manual:1"],
                tags=["manual", "import"],
                first_event_at=now,
                last_event_at=now,
                created_at=now,
                updated_at=now,
            ),
            doc_path=source_path,
        )
        persisted = await service.upsert_memory_from_document(source_path)
        loaded = await store.get_long_term_memory_index("manual-1")
    finally:
        await store.close()

    expected_path = loader.build_long_term_doc_path(
        LongTermMemoryDocument(
            memory_id="manual-1",
            umo=TEST_UMO,
            canonical_user_id=TEST_CANONICAL_USER_ID,
            scope_type="user",
            scope_id=TEST_CANONICAL_USER_ID,
            category="user_preference",
            status="active",
            title="Manual imported memory",
            summary="The user wants manual long-term imports.",
            detail_summary="This document was created manually before import.",
        )
    )
    assert persisted.memory_id == "manual-1"
    assert loaded is not None
    assert loaded.doc_path == str(expected_path)
    assert expected_path.exists()
    assert source_path.exists()
    assert loaded.vector_sync_status == LongTermVectorSyncStatus.READY
    assert loaded.vector_sync_error is None
    assert vector_index.calls == ["manual-1"]


@pytest.mark.asyncio
async def test_long_term_manual_service_updates_existing_memory_from_document(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    loader = DocumentLoader(config)
    service = LongTermMemoryManualService(
        store,
        document_loader=loader,
    )
    now = datetime.now(UTC)
    normalized_path = loader.build_long_term_doc_path(
        LongTermMemoryDocument(
            memory_id="manual-2",
            umo=TEST_UMO,
            canonical_user_id=TEST_CANONICAL_USER_ID,
            scope_type="user",
            scope_id=TEST_CANONICAL_USER_ID,
            category="project_progress",
            status="active",
            title="Original manual memory",
            summary="Original summary.",
            detail_summary="Original detail.",
        )
    )
    update_source_path = temp_dir / "manual-update.md"

    try:
        original_doc = LongTermMemoryDocument(
            memory_id="manual-2",
            umo=TEST_UMO,
            canonical_user_id=TEST_CANONICAL_USER_ID,
            scope_type="user",
            scope_id=TEST_CANONICAL_USER_ID,
            category="project_progress",
            status="active",
            title="Original manual memory",
            summary="Original summary.",
            detail_summary="Original detail.",
            importance=0.7,
            confidence=0.8,
            source_refs=["manual:original"],
            tags=["original"],
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        loader.save_long_term_document(original_doc, doc_path=normalized_path)
        await store.upsert_long_term_memory_index(
            _long_term_memory_index(
                memory_id="manual-2",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                title="Original manual memory",
                summary="Original summary.",
                status="active",
                doc_path=str(normalized_path),
                importance=0.7,
                confidence=0.8,
                tags=["original"],
                source_refs=["manual:original"],
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
        )
        loader.save_long_term_document(
            LongTermMemoryDocument(
                memory_id="manual-2",
                umo=TEST_UMO,
                canonical_user_id=TEST_CANONICAL_USER_ID,
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                status="archived",
                title="Updated manual memory",
                summary="Updated summary.",
                detail_summary="Updated detail.",
                importance=0.92,
                confidence=0.96,
                source_refs=["manual:updated"],
                tags=["updated"],
                created_at=now - timedelta(days=1),
                updated_at=now,
            ),
            doc_path=update_source_path,
        )

        persisted = await service.upsert_memory_from_document(update_source_path)
        loaded_doc = loader.load_long_term_document(normalized_path)
    finally:
        await store.close()

    assert persisted.memory_id == "manual-2"
    assert persisted.title == "Updated manual memory"
    assert persisted.status == LongTermMemoryStatus.ARCHIVED.value
    assert persisted.doc_path == str(normalized_path)
    assert loaded_doc.title == "Updated manual memory"
    assert loaded_doc.importance == pytest.approx(0.92)
    assert loaded_doc.confidence == pytest.approx(0.96)


@pytest.mark.asyncio
async def test_long_term_manual_service_restores_existing_markdown_when_db_write_fails(
    temp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    loader = DocumentLoader(config)
    service = LongTermMemoryManualService(
        store,
        document_loader=loader,
    )
    now = datetime.now(UTC)
    normalized_path = loader.build_long_term_doc_path(
        LongTermMemoryDocument(
            memory_id="manual-rollback",
            umo=TEST_UMO,
            canonical_user_id=TEST_CANONICAL_USER_ID,
            scope_type="user",
            scope_id=TEST_CANONICAL_USER_ID,
            category="project_progress",
            status="active",
            title="Original manual memory",
            summary="Original summary.",
            detail_summary="Original detail.",
        )
    )
    update_source_path = temp_dir / "manual-rollback-update.md"

    async def failing_upsert(index):  # noqa: ANN001
        del index
        raise RuntimeError("db write failed")

    monkeypatch.setattr(store, "upsert_long_term_memory_index", failing_upsert)

    try:
        original_doc = LongTermMemoryDocument(
            memory_id="manual-rollback",
            umo=TEST_UMO,
            canonical_user_id=TEST_CANONICAL_USER_ID,
            scope_type="user",
            scope_id=TEST_CANONICAL_USER_ID,
            category="project_progress",
            status="active",
            title="Original manual memory",
            summary="Original summary.",
            detail_summary="Original detail.",
            importance=0.7,
            confidence=0.8,
            source_refs=["manual:original"],
            tags=["original"],
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        loader.save_long_term_document(original_doc, doc_path=normalized_path)
        before_content = normalized_path.read_text(encoding="utf-8")
        loader.save_long_term_document(
            LongTermMemoryDocument(
                memory_id="manual-rollback",
                umo=TEST_UMO,
                canonical_user_id=TEST_CANONICAL_USER_ID,
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                status="archived",
                title="Updated manual memory",
                summary="Updated summary.",
                detail_summary="Updated detail.",
                importance=0.92,
                confidence=0.96,
                source_refs=["manual:updated"],
                tags=["updated"],
                created_at=now - timedelta(days=1),
                updated_at=now,
            ),
            doc_path=update_source_path,
        )

        with pytest.raises(RuntimeError, match="db write failed"):
            await service.upsert_memory_from_document(update_source_path)
        after_content = normalized_path.read_text(encoding="utf-8")
    finally:
        await store.close()

    assert after_content == before_content


@pytest.mark.asyncio
async def test_long_term_manual_service_rejects_invalid_document_fields(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    loader = DocumentLoader(config)
    service = LongTermMemoryManualService(store, document_loader=loader)
    source_path = temp_dir / "invalid-manual.md"

    try:
        source_path.write_text(
            "\n".join(
                [
                    "---",
                    "memory_id: invalid-1",
                    "umo: test:private:user",
                    "canonical_user_id: canonical-user-1",
                    "scope_type: user",
                    "scope_id: canonical-user-1",
                    "category: invalid_category",
                    "status: active",
                    "title: Invalid manual memory",
                    "importance: 0.5",
                    "confidence: 0.7",
                    "---",
                    "",
                    "# Invalid manual memory",
                    "",
                    "## Summary",
                    "```text",
                    "Summary exists.",
                    "```",
                    "",
                ],
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="invalid category"):
            await service.upsert_memory_from_document(source_path)
        loaded = await store.get_long_term_memory_index("invalid-1")
    finally:
        await store.close()

    assert loaded is None


@pytest.mark.asyncio
async def test_long_term_manual_service_raises_when_vector_enabled_without_binding(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: true",
                "  provider_id: embedding-test",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    loader = DocumentLoader(config)
    service = LongTermMemoryManualService(
        store,
        document_loader=loader,
        vector_index=MemoryVectorIndex(store, config=config),
    )
    source_path = temp_dir / "manual-no-binding.md"
    now = datetime.now(UTC)

    try:
        loader.save_long_term_document(
            LongTermMemoryDocument(
                memory_id="manual-unbound",
                umo=TEST_UMO,
                canonical_user_id=TEST_CANONICAL_USER_ID,
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                status="active",
                title="Unbound vector import",
                summary="This import should fail before any write.",
                detail_summary="The vector index is enabled but not bound.",
                importance=0.8,
                confidence=0.9,
                created_at=now,
                updated_at=now,
            ),
            doc_path=source_path,
        )
        with pytest.raises(
            RuntimeError,
            match="not bound to ProviderManager",
        ):
            await service.upsert_memory_from_document(source_path)
        loaded = await store.get_long_term_memory_index("manual-unbound")
    finally:
        await store.close()

    assert loaded is None


@pytest.mark.asyncio
async def test_long_term_manual_service_raises_when_vector_refresh_fails(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: true",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    loader = DocumentLoader(config)
    service = LongTermMemoryManualService(
        store,
        document_loader=loader,
        vector_index=FailingManualVectorIndex(),  # type: ignore[arg-type]
    )
    source_path = temp_dir / "manual-vector-failure.md"
    now = datetime.now(UTC)

    try:
        loader.save_long_term_document(
            LongTermMemoryDocument(
                memory_id="manual-3",
                umo=TEST_UMO,
                canonical_user_id=TEST_CANONICAL_USER_ID,
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                status="active",
                title="Vector failure manual memory",
                summary="Vector refresh should not roll back import.",
                detail_summary="The vector index fails after the import succeeds.",
                importance=0.82,
                confidence=0.9,
                created_at=now,
                updated_at=now,
            ),
            doc_path=source_path,
        )
        persisted = await service.upsert_memory_from_document(source_path)
        loaded = await store.get_long_term_memory_index("manual-3")
    finally:
        await store.close()

    assert persisted.memory_id == "manual-3"
    assert loaded is not None
    assert Path(loaded.doc_path).exists()
    assert loaded.vector_sync_status == LongTermVectorSyncStatus.DIRTY
    assert loaded.vector_sync_error == "manual vector refresh failed"


@pytest.mark.asyncio
async def test_memory_service_import_long_term_memory_document_delegates_to_manual_service(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    memory_service = MemoryService(
        store,
        TurnRecordService(store),
        _build_short_term_service(
            store, RecentConversationSource(store, recent_turns_window=8)
        ),
        MemorySnapshotBuilder(store),
        manual_long_term_service=MagicMock(),
    )
    expected = LongTermMemoryIndex(
        memory_id="manual-service-1",
        umo=TEST_UMO,
        canonical_user_id=TEST_CANONICAL_USER_ID,
        scope_type="user",
        scope_id=TEST_CANONICAL_USER_ID,
        category="user_preference",
        title="Delegated import",
        summary="Delegated summary",
        status="active",
        doc_path="ignored.md",
    )
    memory_service.manual_long_term_service.upsert_memory_from_document = AsyncMock(  # type: ignore[method-assign]
        return_value=expected
    )

    try:
        result = await memory_service.import_long_term_memory_document(
            temp_dir / "manual.md"
        )
    finally:
        await store.close()

    assert result.memory_id == "manual-service-1"


@pytest.mark.asyncio
async def test_memory_service_refresh_long_term_vector_index_marks_ready(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    memory_service = MemoryService(
        store,
        TurnRecordService(store),
        _build_short_term_service(
            store, RecentConversationSource(store, recent_turns_window=8)
        ),
        MemorySnapshotBuilder(store),
        long_term_service=MagicMock(vector_index=StubManualVectorIndex()),
    )
    now = datetime.now(UTC)

    try:
        await store.upsert_long_term_memory_index(
            _long_term_memory_index(
                memory_id="refresh-1",
                scope_type="user",
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                title="Refresh candidate",
                summary="Needs vector repair.",
                status="active",
                doc_path=str(temp_dir / "refresh-1.md"),
                importance=0.8,
                confidence=0.9,
                tags=[],
                source_refs=[],
                first_event_at=now,
                last_event_at=now,
                vector_sync_status=LongTermVectorSyncStatus.DIRTY,
                vector_sync_error="failed before",
                created_at=now,
                updated_at=now,
            )
        )
        refreshed = await memory_service.refresh_long_term_vector_index("refresh-1")
    finally:
        await store.close()

    assert refreshed.vector_sync_status == LongTermVectorSyncStatus.READY
    assert refreshed.vector_sync_error is None


@pytest.mark.asyncio
async def test_memory_service_refresh_dirty_long_term_vector_indexes_only_processes_dirty(
    temp_dir: Path,
):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "memory.db").as_posix()
    docs_root = (temp_dir / "long_term").as_posix()
    projections_root = (temp_dir / "projections").as_posix()
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "storage:",
                f'  sqlite_path: "{sqlite_path}"',
                f'  docs_root: "{docs_root}"',
                f'  projections_root: "{projections_root}"',
                "vector_index:",
                "  enabled: true",
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    store = MemoryStore(config=config)
    vector_index = StubManualVectorIndex()
    memory_service = MemoryService(
        store,
        TurnRecordService(store),
        _build_short_term_service(
            store, RecentConversationSource(store, recent_turns_window=8)
        ),
        MemorySnapshotBuilder(store),
        long_term_service=MagicMock(vector_index=vector_index),
    )
    now = datetime.now(UTC)

    try:
        for memory_id, status in (
            ("dirty-1", LongTermVectorSyncStatus.DIRTY),
            ("dirty-2", LongTermVectorSyncStatus.DIRTY),
            ("ready-1", LongTermVectorSyncStatus.READY),
        ):
            await store.upsert_long_term_memory_index(
                _long_term_memory_index(
                    memory_id=memory_id,
                    scope_type="user",
                    scope_id=TEST_CANONICAL_USER_ID,
                    category="project_progress",
                    title=memory_id,
                    summary=memory_id,
                    status="active",
                    doc_path=str(temp_dir / f"{memory_id}.md"),
                    importance=0.8,
                    confidence=0.9,
                    tags=[],
                    source_refs=[],
                    first_event_at=now,
                    last_event_at=now,
                    vector_sync_status=status,
                    created_at=now,
                    updated_at=now,
                )
            )
        refreshed = await memory_service.refresh_dirty_long_term_vector_indexes(
            limit=10
        )
    finally:
        await store.close()

    assert [item.memory_id for item in refreshed] == ["dirty-1", "dirty-2"]
    assert vector_index.calls == ["dirty-1", "dirty-2"]


@pytest.mark.asyncio
async def test_experience_service_writes_markdown_projection(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    projection_service = ExperienceProjectionService(
        store,
        projections_root=temp_dir / "projections",
    )
    experience_service = ExperienceService(
        store,
        projection_service=projection_service,
    )
    now = datetime.now(UTC)

    try:
        persisted = await experience_service.persist_experiences(
            [
                _experience(
                    experience_id="exp-1",
                    scope_type="user",
                    scope_id=TEST_CANONICAL_USER_ID,
                    event_time=now,
                    category="project_progress",
                    summary="Implemented snapshot exposure",
                    detail_summary="Snapshot now includes recent conversation experiences.",
                    importance=0.81,
                    confidence=0.93,
                    source_refs=["turn:turn-1"],
                    created_at=now,
                    updated_at=now,
                ),
                _experience(
                    experience_id="exp-2",
                    scope_type="user",
                    scope_id=TEST_CANONICAL_USER_ID,
                    event_time=now + timedelta(seconds=1),
                    category="episodic_event",
                    summary="Wrote the experience projection",
                    detail_summary="A markdown timeline projection was generated.",
                    importance=0.78,
                    confidence=0.9,
                    source_refs=["turn:turn-2"],
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        await experience_service.refresh_projections_for_experiences(persisted)
        projection_path = projection_service._build_projection_path(
            TEST_CANONICAL_USER_ID,
            "user",
            TEST_CANONICAL_USER_ID,
        )
    finally:
        await store.close()

    assert len(persisted) == 2
    assert projection_path.exists()
    content = projection_path.read_text(encoding="utf-8")
    assert "projection_type: experience_timeline" in content
    assert "generated_at:" in content
    assert "## Experience exp-1" in content
    assert "### Summary" in content
    assert "```text" in content
    assert "Implemented snapshot exposure" in content
    assert "Wrote the experience projection" in content


@pytest.mark.asyncio
async def test_experience_projection_handles_multiline_markdown_like_content(
    temp_dir: Path,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    projection_service = ExperienceProjectionService(
        store,
        projections_root=temp_dir / "projections",
    )
    experience_service = ExperienceService(
        store,
        projection_service=projection_service,
    )
    now = datetime.now(UTC)

    try:
        persisted = await experience_service.persist_experiences(
            [
                _experience(
                    experience_id="exp-special",
                    scope_type="user",
                    scope_id=TEST_CANONICAL_USER_ID,
                    event_time=now,
                    category="episodic_event",
                    summary="Line 1\n- bullet\n---\n# heading",
                    detail_summary="code fence ``` inside\nvalue: test",
                    importance=0.8,
                    confidence=0.9,
                    source_refs=["turn:1", "weird:ref:#-value"],
                    created_at=now,
                    updated_at=now,
                )
            ]
        )
        await experience_service.refresh_projections_for_experiences(persisted)
        projection_path = projection_service._build_projection_path(
            TEST_CANONICAL_USER_ID,
            "user",
            TEST_CANONICAL_USER_ID,
        )
    finally:
        await store.close()

    content = projection_path.read_text(encoding="utf-8")
    assert content.startswith("---\nprojection_type: experience_timeline\n")
    assert "## Experience exp-special" in content
    assert "### Summary" in content
    assert "### Detail Summary" in content
    assert "### Source Refs" in content
    assert "Line 1\n- bullet\n---\n# heading" in content
    assert "code fence ``` inside\nvalue: test" in content


@pytest.mark.asyncio
async def test_memory_postprocessor_builds_request_from_conversation_history():
    history = _make_history()
    conversation = Conversation(
        platform_id="test",
        user_id="test:private:user",
        cid="conv-1",
        history=json.dumps(history),
    )
    event = MagicMock()
    event.unified_msg_origin = TEST_UMO
    event.get_platform_id.return_value = TEST_PLATFORM_ID
    event.get_sender_id.return_value = "user-1"
    event.get_sender_name.return_value = "tester"
    event.session_id = "session-1"
    memory_service = MagicMock()
    memory_service.update_from_postprocess = AsyncMock()
    memory_service.identity_resolver = MagicMock()
    memory_service.identity_resolver.resolve_from_event = AsyncMock(
        return_value=_memory_identity()
    )
    processor = MemoryPostProcessor(memory_service)
    ctx = MagicMock()
    ctx.event = event
    ctx.conversation = conversation
    ctx.provider_request = ProviderRequest(prompt="hello", session_id="session-1")
    ctx.timestamp = datetime.now(UTC)

    req = await processor.build_update_request(ctx)

    assert req is not None
    assert req.conversation_id == "conv-1"
    assert req.platform_user_key == TEST_PLATFORM_USER_KEY
    assert req.canonical_user_id == TEST_CANONICAL_USER_ID
    assert req.user_message["content"] == "Then wire postprocess and snapshot."
    assert req.assistant_message["content"] == "That gives us a minimal closed loop."
    assert req.provider_request is not None
    assert isinstance(req.provider_request.get("conversation_history"), list)


@pytest.mark.asyncio
async def test_memory_postprocessor_skips_invalid_conversation_history():
    event = MagicMock()
    event.unified_msg_origin = TEST_UMO
    event.get_platform_id.return_value = TEST_PLATFORM_ID
    event.get_sender_id.return_value = "user-1"
    event.get_sender_name.return_value = "tester"
    event.session_id = "session-1"
    memory_service = MagicMock()
    memory_service.update_from_postprocess = AsyncMock()
    memory_service.identity_resolver = MagicMock()
    memory_service.identity_resolver.resolve_from_event = AsyncMock(
        return_value=_memory_identity()
    )
    processor = MemoryPostProcessor(memory_service)
    ctx = MagicMock()
    ctx.event = event
    ctx.conversation = Conversation(
        platform_id="test",
        user_id="test:private:user",
        cid="conv-1",
        history=json.dumps([{"role": "user", "content": "only user"}]),
    )
    ctx.provider_request = None
    ctx.timestamp = datetime.now(UTC)

    req = await processor.build_update_request(ctx)
    await processor.run(ctx)

    assert req is None
    memory_service.update_from_postprocess.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_postprocessor_falls_back_to_provider_request_conversation():
    history = [
        {"role": "user", "content": "Previous turn."},
        {"role": "assistant", "content": "Previous answer."},
    ]
    provider_conversation = Conversation(
        platform_id="test",
        user_id="test:private:user",
        cid="conv-1",
        history=json.dumps(history),
    )
    event = MagicMock()
    event.unified_msg_origin = TEST_UMO
    event.get_platform_id.return_value = TEST_PLATFORM_ID
    event.get_sender_id.return_value = "user-1"
    event.get_sender_name.return_value = "tester"
    event.session_id = "session-1"
    memory_service = MagicMock()
    memory_service.update_from_postprocess = AsyncMock()
    memory_service.identity_resolver = MagicMock()
    memory_service.identity_resolver.resolve_from_event = AsyncMock(
        return_value=_memory_identity()
    )
    processor = MemoryPostProcessor(memory_service)
    ctx = MagicMock()
    ctx.event = event
    ctx.conversation = None
    ctx.provider_request = ProviderRequest(
        prompt="Current user turn.",
        session_id="session-1",
        conversation=provider_conversation,
    )
    ctx.llm_response = LLMResponse(
        role="assistant",
        completion_text="Current assistant reply.",
    )
    ctx.timestamp = datetime.now(UTC)

    req = await processor.build_update_request(ctx)

    assert req is not None
    assert req.conversation_id == "conv-1"
    assert req.platform_user_key == TEST_PLATFORM_USER_KEY
    assert req.canonical_user_id == TEST_CANONICAL_USER_ID
    assert req.user_message["content"] == "Current user turn."
    assert req.assistant_message["content"] == "Current assistant reply."
    assert req.provider_request is not None
    assert (
        req.provider_request["history_source"]
        == "provider_request.conversation.history"
    )


@pytest.mark.asyncio
async def test_memory_postprocessor_ignores_stale_conversation_history_for_current_turn():
    stale_history = [
        {"role": "user", "content": "Previous turn."},
        {"role": "assistant", "content": "Previous answer."},
    ]
    conversation = Conversation(
        platform_id="test",
        user_id="test:private:user",
        cid="conv-1",
        history=json.dumps(stale_history),
    )
    provider_conversation = Conversation(
        platform_id="test",
        user_id="test:private:user",
        cid="conv-1",
        history=json.dumps(stale_history),
    )
    event = MagicMock()
    event.unified_msg_origin = TEST_UMO
    event.get_platform_id.return_value = TEST_PLATFORM_ID
    event.get_sender_id.return_value = "user-1"
    event.get_sender_name.return_value = "tester"
    event.session_id = "session-1"
    memory_service = MagicMock()
    memory_service.update_from_postprocess = AsyncMock()
    memory_service.identity_resolver = MagicMock()
    memory_service.identity_resolver.resolve_from_event = AsyncMock(
        return_value=_memory_identity()
    )
    processor = MemoryPostProcessor(memory_service)
    ctx = MagicMock()
    ctx.event = event
    ctx.conversation = conversation
    ctx.provider_request = ProviderRequest(
        prompt="Current user turn.",
        session_id="session-1",
        conversation=provider_conversation,
    )
    ctx.llm_response = LLMResponse(
        role="assistant",
        completion_text="Current assistant reply.",
    )
    ctx.timestamp = datetime.now(UTC)

    req = await processor.build_update_request(ctx)

    assert req is not None
    assert req.user_message["content"] == "Current user turn."
    assert req.assistant_message["content"] == "Current assistant reply."
    assert req.provider_request is not None
    assert (
        req.provider_request["history_source"]
        == "provider_request.conversation.history"
    )


@pytest.mark.asyncio
async def test_memory_postprocessor_falls_back_to_provider_request_contexts():
    event = MagicMock()
    event.unified_msg_origin = TEST_UMO
    event.get_platform_id.return_value = TEST_PLATFORM_ID
    event.get_sender_id.return_value = "user-1"
    event.get_sender_name.return_value = "tester"
    event.session_id = "session-1"
    memory_service = MagicMock()
    memory_service.update_from_postprocess = AsyncMock()
    memory_service.identity_resolver = MagicMock()
    memory_service.identity_resolver.resolve_from_event = AsyncMock(
        return_value=_memory_identity()
    )
    processor = MemoryPostProcessor(memory_service)
    ctx = MagicMock()
    ctx.event = event
    ctx.conversation = None
    ctx.provider_request = ProviderRequest(
        prompt="Please continue the task.",
        session_id="session-1",
        contexts=[
            {"role": "user", "content": "Earlier request."},
            {"role": "assistant", "content": "Earlier answer."},
        ],
    )
    ctx.llm_response = LLMResponse(
        role="assistant",
        completion_text="Current task completed.",
    )
    ctx.timestamp = datetime.now(UTC)

    req = await processor.build_update_request(ctx)

    assert req is not None
    assert req.conversation_id is None
    assert req.user_message["content"] == "Please continue the task."
    assert req.assistant_message["content"] == "Current task completed."
    assert req.provider_request is not None
    assert req.provider_request["history_source"] == "provider_request.contexts"


@pytest.mark.asyncio
async def test_memory_postprocessor_skips_interaction_turn_without_finalized_material():
    event = MagicMock()
    event.unified_msg_origin = TEST_UMO
    event.get_platform_id.return_value = TEST_PLATFORM_ID
    event.get_sender_id.return_value = "user-1"
    event.get_sender_name.return_value = "tester"
    event.message_str = "Can you check permissions?"
    event.session_id = "session-1"
    memory_service = MagicMock()
    memory_service.update_from_postprocess = AsyncMock()
    memory_service.identity_resolver = MagicMock()
    memory_service.identity_resolver.resolve_from_event = AsyncMock(
        return_value=_memory_identity()
    )
    processor = MemoryPostProcessor(memory_service)
    ctx = MagicMock()
    ctx.event = event
    ctx.conversation = None
    ctx.provider_request = ProviderRequest(
        prompt="Can you check permissions?",
        session_id="session-1",
    )
    ctx.llm_response = None
    ctx.turn_id = "turn-1"
    ctx.visible_outputs = [
        {
            "turn_id": "turn-1",
            "kind": "immediate_reply",
            "text": "Let me check.",
            "memory_relevant": True,
        },
        {
            "turn_id": "turn-1",
            "kind": "stream_interjection",
            "text": "Still looking.",
            "memory_relevant": False,
        },
        {
            "turn_id": "turn-1",
            "kind": "core_reply",
            "text": "You can run commands in the workspace.",
            "memory_relevant": True,
        },
    ]
    ctx.timestamp = datetime.now(UTC)

    req = await processor.build_update_request(ctx)

    assert req is None


@pytest.mark.asyncio
async def test_memory_postprocessor_does_not_fallback_for_interaction_turn_material():
    event = MagicMock()
    event.unified_msg_origin = TEST_UMO
    event.get_platform_id.return_value = TEST_PLATFORM_ID
    event.get_sender_id.return_value = "user-1"
    event.get_sender_name.return_value = "tester"
    event.message_str = "Can you check permissions?"
    event.session_id = "session-1"
    memory_service = MagicMock()
    memory_service.update_from_postprocess = AsyncMock()
    memory_service.identity_resolver = MagicMock()
    memory_service.identity_resolver.resolve_from_event = AsyncMock(
        return_value=_memory_identity()
    )
    processor = MemoryPostProcessor(memory_service)
    ctx = MagicMock()
    ctx.event = event
    ctx.conversation = None
    ctx.provider_request = ProviderRequest(
        prompt="Can you check permissions?",
        session_id="session-1",
        contexts=[
            {"role": "user", "content": "Earlier request."},
            {"role": "assistant", "content": "Earlier answer."},
        ],
    )
    ctx.llm_response = LLMResponse(
        role="assistant",
        completion_text="You can run commands in the workspace.",
    )
    ctx.turn_id = "turn-1"
    ctx.turn_material = {
        "turn_id": "turn-1",
        "assistant_text": "",
    }
    ctx.visible_outputs = []
    ctx.timestamp = datetime.now(UTC)

    req = await processor.build_update_request(ctx)
    await processor.run(ctx)

    assert req is None
    memory_service.update_from_postprocess.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_postprocessor_prefers_explicit_turn_material():
    event = MagicMock()
    event.unified_msg_origin = TEST_UMO
    event.get_platform_id.return_value = TEST_PLATFORM_ID
    event.get_sender_id.return_value = "user-1"
    event.get_sender_name.return_value = "tester"
    event.message_str = "Can you check permissions?"
    event.session_id = "session-1"
    memory_service = MagicMock()
    memory_service.update_from_postprocess = AsyncMock()
    memory_service.identity_resolver = MagicMock()
    memory_service.identity_resolver.resolve_from_event = AsyncMock(
        return_value=_memory_identity()
    )
    processor = MemoryPostProcessor(memory_service)
    ctx = MagicMock()
    ctx.event = event
    ctx.conversation = None
    ctx.provider_request = ProviderRequest(
        prompt="Can you check permissions?",
        session_id="session-1",
    )
    ctx.llm_response = None
    ctx.turn_id = "turn-1"
    ctx.visible_outputs = []
    ctx.turn_material = {
        "turn_id": "turn-1",
        "assistant_text": "Let me check. You can run commands in the workspace.",
        "visible_outputs": [
            {
                "turn_id": "turn-1",
                "kind": "immediate_reply",
                "text": "Let me check.",
                "memory_relevant": True,
            },
            {
                "turn_id": "turn-1",
                "kind": "core_reply",
                "text": "You can run commands in the workspace.",
                "memory_relevant": True,
            },
        ],
        "history_source": "interaction.turn.material",
    }
    ctx.timestamp = datetime.now(UTC)

    req = await processor.build_update_request(ctx)

    assert req is not None
    assert req.turn_id == "turn-1"
    assert req.assistant_message["content"] == (
        "Let me check. You can run commands in the workspace."
    )
    assert req.provider_request is not None
    assert req.provider_request["history_source"] == "interaction.turn.material"
    assert len(req.provider_request["visible_outputs"]) == 2


def test_register_memory_postprocessor_reuses_singleton_and_updates_service(
    monkeypatch: pytest.MonkeyPatch,
):
    manager = get_postprocess_manager()
    manager.clear()
    reset_memory_postprocessor()

    try:
        monkeypatch.setattr(
            "astrbot.core.memory.postprocessor.get_memory_config",
            lambda: MagicMock(enabled=True),
        )

        first_service = MagicMock()
        second_service = MagicMock()

        first_processor = register_memory_postprocessor(first_service)
        second_processor = register_memory_postprocessor(second_service)

        assert first_processor is not None
        assert second_processor is first_processor
        assert first_processor.memory_service is second_service
        assert first_processor.triggers == (PostProcessTrigger.AFTER_TURN_COMPLETED,)
        assert manager.get_processors(PostProcessTrigger.AFTER_TURN_COMPLETED) == [
            first_processor
        ]
        assert manager.get_processors(PostProcessTrigger.AFTER_MESSAGE_SENT) == []
    finally:
        reset_memory_postprocessor()
        manager.clear()
