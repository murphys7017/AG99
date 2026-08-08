from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import select

from astrbot.core.memory import (
    LongTermMemoryIndex,
    LongTermMemoryLink,
    LongTermMemoryLinkRelation,
    LongTermMemoryStatus,
    LongTermPromotionCursor,
    LongTermVectorSyncStatus,
    MemoryStore,
    PersonaEvolutionLog,
    PersonaState,
    ScopeType,
    SessionInsight,
    ShortTermMemory,
    TopicState,
    TurnRecord,
    load_memory_config,
)
from astrbot.core.memory.po import (
    MemoryExperience,
    MemoryIdentityMapping,
    MemoryLongTermMemoryIndex,
    MemoryLongTermMemoryLink,
    MemoryLongTermPromotionCursor,
    MemoryPersonaEvolutionLog,
    MemoryPersonaState,
    MemorySessionInsight,
    MemoryShortTermMemory,
    MemoryTopicState,
    MemoryTurnRecord,
)
from astrbot.core.memory.types import Experience

TEST_UMO = "test:private:user"
TEST_PLATFORM_ID = "test"
TEST_PLATFORM_USER_KEY = "test:user-1"
TEST_CANONICAL_USER_ID = "canonical-user-1"


def _turn_record(
    *,
    turn_id: str,
    conversation_id: str | None = "conv-1",
    user_content: str = "hello",
    assistant_content: str = "hi",
    message_timestamp: datetime,
    created_at: datetime,
) -> TurnRecord:
    return TurnRecord(
        turn_id=turn_id,
        umo=TEST_UMO,
        conversation_id=conversation_id,
        platform_id=TEST_PLATFORM_ID,
        platform_user_key=TEST_PLATFORM_USER_KEY,
        canonical_user_id=TEST_CANONICAL_USER_ID,
        session_id="session-1",
        user_message={"role": "user", "content": user_content},
        assistant_message={"role": "assistant", "content": assistant_content},
        message_timestamp=message_timestamp,
        source_refs=[],
        created_at=created_at,
    )


def _session_insight(
    *,
    insight_id: str,
    conversation_id: str | None = "conv-1",
    window_start_at: datetime | None,
    window_end_at: datetime | None,
    topic_summary: str,
    progress_summary: str,
    summary_text: str,
    created_at: datetime,
) -> SessionInsight:
    return SessionInsight(
        insight_id=insight_id,
        umo=TEST_UMO,
        conversation_id=conversation_id,
        platform_user_key=TEST_PLATFORM_USER_KEY,
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
    scope_type: ScopeType | str,
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
) -> Experience:
    return Experience(
        experience_id=experience_id,
        umo=TEST_UMO,
        conversation_id=conversation_id,
        platform_user_key=TEST_PLATFORM_USER_KEY,
        canonical_user_id=TEST_CANONICAL_USER_ID,
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
    scope_type: ScopeType | str,
    scope_id: str,
    category: str,
    title: str,
    summary: str,
    status: LongTermMemoryStatus,
    doc_path: str,
    importance: float,
    confidence: float,
    tags: list[str],
    source_refs: list[str],
    first_event_at: datetime | None,
    last_event_at: datetime | None,
    vector_sync_status: LongTermVectorSyncStatus
    | str = LongTermVectorSyncStatus.PENDING,
    vector_synced_at: datetime | None = None,
    vector_sync_error: str | None = None,
    created_at: datetime,
    updated_at: datetime,
) -> LongTermMemoryIndex:
    return LongTermMemoryIndex(
        memory_id=memory_id,
        umo=TEST_UMO,
        canonical_user_id=TEST_CANONICAL_USER_ID,
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


def _promotion_cursor(
    *,
    cursor_id: str,
    scope_type: ScopeType | str,
    scope_id: str,
    last_processed_created_at: datetime | None,
    last_processed_experience_id: str | None,
    updated_at: datetime,
) -> LongTermPromotionCursor:
    return LongTermPromotionCursor(
        cursor_id=cursor_id,
        umo=TEST_UMO,
        canonical_user_id=TEST_CANONICAL_USER_ID,
        scope_type=scope_type,
        scope_id=scope_id,
        last_processed_created_at=last_processed_created_at,
        last_processed_experience_id=last_processed_experience_id,
        updated_at=updated_at,
    )


@pytest.mark.asyncio
async def test_memory_store_initializes_tables_and_runtime_dirs(temp_dir: Path):
    config_path = temp_dir / "memory-config.yaml"
    sqlite_path = (temp_dir / "data" / "memory" / "test-memory.db").as_posix()
    docs_root = (temp_dir / "data" / "memory" / "long_term").as_posix()
    projections_root = (temp_dir / "data" / "memory" / "projections").as_posix()
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

    try:
        await store.initialize()
        assert config.storage.sqlite_path.parent.exists()
        assert config.storage.docs_root.exists()
        assert config.storage.projections_root.exists()
        assert await store.ping() is True

        async with store.get_db() as session:
            for model in (
                MemoryTurnRecord,
                MemoryTopicState,
                MemoryShortTermMemory,
                MemorySessionInsight,
                MemoryExperience,
                MemoryIdentityMapping,
                MemoryLongTermMemoryIndex,
                MemoryLongTermMemoryLink,
                MemoryLongTermPromotionCursor,
                MemoryPersonaState,
                MemoryPersonaEvolutionLog,
            ):
                result = await session.execute(select(model))
                assert result.scalars().all() == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_memory_store_concurrent_first_reads_initialize_once(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")

    try:
        results = await asyncio.gather(
            store.get_topic_state(TEST_UMO, "conv-1"),
            store.get_short_term_memory(TEST_UMO, "conv-1"),
            store.get_recent_turn_records(TEST_UMO, limit=1, conversation_id="conv-1"),
        )

        assert results == [None, None, []]
        assert store.inited is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_memory_store_round_trip_for_short_term_objects(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    now = datetime.now(UTC)

    try:
        saved_turn = await store.save_turn_record(
            _turn_record(
                turn_id="turn-1",
                message_timestamp=now,
                created_at=now,
            )
        )
        saved_topic = await store.upsert_topic_state(
            TopicState(
                umo="test:private:user",
                conversation_id="conv-1",
                current_topic="memory design",
                topic_summary="Discussing memory MVP",
                topic_confidence=1.2,
                last_active_at=now,
            )
        )
        saved_short_term = await store.upsert_short_term_memory(
            ShortTermMemory(
                umo="test:private:user",
                conversation_id="conv-1",
                short_summary="User wants memory types and store first",
                active_focus="Implement data layer",
                updated_at=now,
            )
        )

        recent_turns = await store.get_recent_turn_records(TEST_UMO, 10)
        loaded_topic = await store.get_topic_state(TEST_UMO, "conv-1")
        loaded_short_term = await store.get_short_term_memory(
            TEST_UMO,
            "conv-1",
        )

        assert saved_turn.turn_id == "turn-1"
        assert recent_turns[0].assistant_message["content"] == "hi"
        assert saved_topic.topic_confidence == 1.0
        assert loaded_topic is not None
        assert loaded_topic.current_topic == "memory design"
        assert saved_short_term.active_focus == "Implement data layer"
        assert loaded_short_term is not None
        assert loaded_short_term.short_summary.startswith("User wants memory")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_memory_store_round_trip_for_mid_and_long_term_objects(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    now = datetime.now(UTC)

    try:
        saved_insight = await store.save_session_insight(
            _session_insight(
                insight_id="insight-1",
                window_start_at=now - timedelta(hours=1),
                window_end_at=now,
                topic_summary="Memory planning",
                progress_summary="Settled on store-first approach",
                summary_text="The user wants to implement types and store before services.",
                created_at=now,
            )
        )
        saved_experience = await store.save_experience(
            _experience(
                experience_id="exp-1",
                scope_type=ScopeType.USER,
                scope_id=TEST_CANONICAL_USER_ID,
                event_time=now,
                category="project_progress",
                summary="Implemented memory store foundation",
                detail_summary="Types and SQLModel tables landed first.",
                importance=0.8,
                confidence=0.9,
                source_refs=["insight:insight-1"],
                created_at=now,
                updated_at=now,
            )
        )
        saved_memory = await store.upsert_long_term_memory_index(
            _long_term_memory_index(
                memory_id="ltm-1",
                scope_type=ScopeType.USER,
                scope_id=TEST_CANONICAL_USER_ID,
                category="user_preference",
                title="Infrastructure-first preference",
                summary="User prefers infrastructure-first sequencing",
                status=LongTermMemoryStatus.ACTIVE,
                doc_path="user/ltm-1.md",
                importance=0.7,
                confidence=0.8,
                tags=["architecture", "planning"],
                source_refs=["exp:exp-1"],
                first_event_at=now - timedelta(hours=1),
                last_event_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        saved_link = await store.save_long_term_memory_link(
            LongTermMemoryLink(
                link_id="link-1",
                memory_id="ltm-1",
                experience_id="exp-1",
                relation_type=LongTermMemoryLinkRelation.SEED,
                created_at=now,
            )
        )
        saved_cursor = await store.upsert_long_term_promotion_cursor(
            _promotion_cursor(
                cursor_id="cursor-1",
                scope_type=ScopeType.USER,
                scope_id=TEST_CANONICAL_USER_ID,
                last_processed_created_at=now,
                last_processed_experience_id="exp-1",
                updated_at=now,
            )
        )
        saved_state = await store.upsert_persona_state(
            PersonaState(
                state_id="state-1",
                scope_type=ScopeType.USER,
                scope_id=TEST_CANONICAL_USER_ID,
                persona_id=None,
                familiarity=0.6,
                trust=0.7,
                warmth=0.8,
                formality_preference=0.4,
                directness_preference=0.9,
                updated_at=now,
            )
        )
        saved_log = await store.save_persona_evolution_log(
            PersonaEvolutionLog(
                log_id="log-1",
                scope_type=ScopeType.USER,
                scope_id=TEST_CANONICAL_USER_ID,
                before_state={"trust": 0.5},
                after_state={"trust": 0.7},
                reason="Repeated productive collaboration",
                source_refs=["exp:exp-1"],
                created_at=now,
            )
        )

        recent_experiences = await store.list_recent_experiences(
            TEST_CANONICAL_USER_ID,
            5,
        )
        ranged_experiences = await store.list_experiences_by_time_range(
            TEST_CANONICAL_USER_ID,
            now - timedelta(minutes=1),
            now + timedelta(minutes=1),
        )
        long_term_memories = await store.list_long_term_memory_indexes(
            TEST_CANONICAL_USER_ID,
            5,
        )
        loaded_memory = await store.get_long_term_memory_index("ltm-1")
        links = await store.list_long_term_memory_links("ltm-1")
        loaded_cursor = await store.get_long_term_promotion_cursor(
            TEST_CANONICAL_USER_ID,
            ScopeType.USER,
            TEST_CANONICAL_USER_ID,
        )
        loaded_state = await store.get_persona_state(
            ScopeType.USER,
            TEST_CANONICAL_USER_ID,
        )
        latest_insight = await store.get_latest_session_insight(
            TEST_CANONICAL_USER_ID,
            "conv-1",
        )
        turns_in_range = await store.list_turn_records_by_time_range(
            TEST_UMO,
            "conv-1",
            now - timedelta(minutes=5),
            now + timedelta(minutes=5),
        )

        assert saved_insight.insight_id == "insight-1"
        assert saved_experience.experience_id == "exp-1"
        assert latest_insight is not None
        assert latest_insight.insight_id == "insight-1"
        assert turns_in_range == []
        assert recent_experiences[0].summary == "Implemented memory store foundation"
        assert len(ranged_experiences) == 1
        assert saved_memory.memory_id == "ltm-1"
        assert saved_memory.title == "Infrastructure-first preference"
        assert saved_memory.status == LongTermMemoryStatus.ACTIVE.value
        assert saved_memory.vector_sync_status == LongTermVectorSyncStatus.PENDING.value
        assert long_term_memories[0].tags == ["architecture", "planning"]
        assert loaded_memory is not None
        assert loaded_memory.category == "user_preference"
        assert saved_link.relation_type == LongTermMemoryLinkRelation.SEED.value
        assert len(links) == 1
        assert saved_cursor.last_processed_experience_id == "exp-1"
        assert loaded_cursor is not None
        assert loaded_cursor.scope_id == TEST_CANONICAL_USER_ID
        assert saved_state.trust == 0.7
        assert loaded_state is not None
        assert loaded_state.directness_preference == 0.9
        assert saved_log.reason == "Repeated productive collaboration"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_memory_store_persist_long_term_promotion_batch_rolls_back_on_failure(
    temp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    now = datetime.now(UTC)

    async def failing_save_link(session, link):  # noqa: ANN001
        del session, link
        raise RuntimeError("link insert failed")

    monkeypatch.setattr(
        store, "_save_long_term_memory_link_with_session", failing_save_link
    )

    try:
        with pytest.raises(RuntimeError, match="link insert failed"):
            await store.persist_long_term_promotion_batch(
                [
                    _long_term_memory_index(
                        memory_id="ltm-rollback",
                        scope_type=ScopeType.USER,
                        scope_id=TEST_CANONICAL_USER_ID,
                        category="user_preference",
                        title="Rollback candidate",
                        summary="Should not survive failed batch.",
                        status=LongTermMemoryStatus.ACTIVE,
                        doc_path="user/ltm-rollback.md",
                        importance=0.7,
                        confidence=0.8,
                        tags=["rollback"],
                        source_refs=["exp:exp-1"],
                        first_event_at=now,
                        last_event_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                ],
                [
                    LongTermMemoryLink(
                        link_id="link-rollback",
                        memory_id="ltm-rollback",
                        experience_id="exp-1",
                        relation_type=LongTermMemoryLinkRelation.SEED,
                        created_at=now,
                    )
                ],
                _promotion_cursor(
                    cursor_id="cursor-rollback",
                    scope_type=ScopeType.USER,
                    scope_id=TEST_CANONICAL_USER_ID,
                    last_processed_created_at=now,
                    last_processed_experience_id="exp-1",
                    updated_at=now,
                ),
            )

        loaded_memory = await store.get_long_term_memory_index("ltm-rollback")
        loaded_links = await store.list_long_term_memory_links("ltm-rollback")
        loaded_cursor = await store.get_long_term_promotion_cursor(
            TEST_CANONICAL_USER_ID,
            ScopeType.USER,
            TEST_CANONICAL_USER_ID,
        )
    finally:
        await store.close()

    assert loaded_memory is None
    assert loaded_links == []
    assert loaded_cursor is None


@pytest.mark.asyncio
async def test_memory_store_lists_turn_records_by_time_range(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    now = datetime.now(UTC)

    try:
        await store.save_turn_record(
            _turn_record(
                turn_id="turn-1",
                message_timestamp=now - timedelta(minutes=3),
                created_at=now,
            )
        )
        await store.save_turn_record(
            _turn_record(
                turn_id="turn-2",
                user_content="second",
                assistant_content="reply",
                message_timestamp=now - timedelta(minutes=1),
                created_at=now,
            )
        )

        turns = await store.list_turn_records_by_time_range(
            TEST_UMO,
            "conv-1",
            now - timedelta(minutes=2),
        )

        assert [turn.turn_id for turn in turns] == ["turn-2"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_memory_store_lists_recent_experiences_by_conversation(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    now = datetime.now(UTC)

    try:
        await store.save_experience(
            _experience(
                experience_id="exp-1",
                scope_type=ScopeType.CONVERSATION,
                scope_id="conv-1",
                event_time=now - timedelta(minutes=2),
                category="project_progress",
                summary="Conversation one progress",
                detail_summary=None,
                importance=0.7,
                confidence=0.8,
                source_refs=[],
                created_at=now,
                updated_at=now,
            )
        )
        await store.save_experience(
            _experience(
                experience_id="exp-2",
                conversation_id="conv-2",
                scope_type=ScopeType.CONVERSATION,
                scope_id="conv-2",
                event_time=now - timedelta(minutes=1),
                category="episodic_event",
                summary="Conversation two event",
                detail_summary=None,
                importance=0.6,
                confidence=0.7,
                source_refs=[],
                created_at=now,
                updated_at=now,
            )
        )

        filtered = await store.list_recent_experiences(
            TEST_CANONICAL_USER_ID,
            10,
            conversation_id="conv-1",
        )
        scoped = await store.list_experiences_for_scope(
            TEST_CANONICAL_USER_ID,
            ScopeType.CONVERSATION,
            "conv-2",
        )
    finally:
        await store.close()

    assert [experience.experience_id for experience in filtered] == ["exp-1"]
    assert [experience.experience_id for experience in scoped] == ["exp-2"]


@pytest.mark.asyncio
async def test_memory_store_persist_consolidation_batch_is_atomic(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    now = datetime.now(UTC)
    original = store._save_experience_with_session
    call_count = 0

    async def failing_save_experience(session, experience):  # noqa: ANN001
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("forced failure")
        return await original(session, experience)

    store._save_experience_with_session = failing_save_experience  # type: ignore[method-assign]

    try:
        with pytest.raises(RuntimeError):
            await store.persist_consolidation_batch(
                _session_insight(
                    insight_id="insight-1",
                    window_start_at=now - timedelta(minutes=1),
                    window_end_at=now,
                    topic_summary="summary",
                    progress_summary="progress",
                    summary_text="text",
                    created_at=now,
                ),
                [
                    _experience(
                        experience_id="exp-1",
                        scope_type=ScopeType.CONVERSATION,
                        scope_id="conv-1",
                        event_time=now,
                        category="project_progress",
                        summary="first",
                        detail_summary=None,
                        importance=0.8,
                        confidence=0.9,
                        source_refs=[],
                        created_at=now,
                        updated_at=now,
                    ),
                    _experience(
                        experience_id="exp-2",
                        scope_type=ScopeType.CONVERSATION,
                        scope_id="conv-1",
                        event_time=now,
                        category="episodic_event",
                        summary="second",
                        detail_summary=None,
                        importance=0.7,
                        confidence=0.8,
                        source_refs=[],
                        created_at=now,
                        updated_at=now,
                    ),
                ],
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
    finally:
        await store.close()

    assert latest_insight is None
    assert experiences == []


@pytest.mark.asyncio
async def test_memory_store_experience_and_insight_ordering_is_stable(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    now = datetime.now(UTC)

    try:
        await store.save_session_insight(
            _session_insight(
                insight_id="insight-a",
                window_start_at=now,
                window_end_at=now,
                topic_summary="a",
                progress_summary="a",
                summary_text="a",
                created_at=now,
            )
        )
        await store.save_session_insight(
            _session_insight(
                insight_id="insight-b",
                window_start_at=now,
                window_end_at=now,
                topic_summary="b",
                progress_summary="b",
                summary_text="b",
                created_at=now + timedelta(microseconds=1),
            )
        )
        await store.save_experience(
            _experience(
                experience_id="exp-a",
                scope_type=ScopeType.CONVERSATION,
                scope_id="conv-1",
                event_time=now,
                category="project_progress",
                summary="first",
                detail_summary=None,
                importance=0.6,
                confidence=0.7,
                source_refs=[],
                created_at=now,
                updated_at=now,
            )
        )
        await store.save_experience(
            _experience(
                experience_id="exp-b",
                scope_type=ScopeType.CONVERSATION,
                scope_id="conv-1",
                event_time=now,
                category="episodic_event",
                summary="second",
                detail_summary=None,
                importance=0.7,
                confidence=0.8,
                source_refs=[],
                created_at=now + timedelta(microseconds=1),
                updated_at=now + timedelta(microseconds=1),
            )
        )

        latest_insight = await store.get_latest_session_insight(
            TEST_CANONICAL_USER_ID,
            "conv-1",
        )
        recent = await store.list_recent_experiences(
            TEST_CANONICAL_USER_ID,
            10,
            conversation_id="conv-1",
        )
        scoped = await store.list_experiences_for_scope(
            TEST_CANONICAL_USER_ID,
            ScopeType.CONVERSATION,
            "conv-1",
        )
    finally:
        await store.close()

    assert latest_insight is not None
    assert latest_insight.insight_id == "insight-b"
    assert [item.experience_id for item in recent] == ["exp-b", "exp-a"]
    assert [item.experience_id for item in scoped] == ["exp-a", "exp-b"]


@pytest.mark.asyncio
async def test_memory_store_updates_and_lists_vector_sync_state(temp_dir: Path):
    store = MemoryStore(db_path=temp_dir / "memory.db")
    now = datetime.now(UTC)

    try:
        await store.upsert_long_term_memory_index(
            _long_term_memory_index(
                memory_id="ltm-dirty",
                scope_type=ScopeType.USER,
                scope_id=TEST_CANONICAL_USER_ID,
                category="project_progress",
                title="Dirty memory",
                summary="Needs vector refresh.",
                status=LongTermMemoryStatus.ACTIVE,
                doc_path="user/ltm-dirty.md",
                importance=0.8,
                confidence=0.9,
                tags=["dirty"],
                source_refs=["exp:1"],
                first_event_at=now,
                last_event_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        dirty = await store.update_long_term_vector_sync_state(
            "ltm-dirty",
            status=LongTermVectorSyncStatus.DIRTY,
            synced_at=None,
            error="refresh failed",
        )
        listed_dirty = await store.list_long_term_memories_by_vector_status(
            LongTermVectorSyncStatus.DIRTY,
            limit=10,
        )
        ready = await store.update_long_term_vector_sync_state(
            "ltm-dirty",
            status=LongTermVectorSyncStatus.READY,
            synced_at=now,
            error=None,
        )
    finally:
        await store.close()

    assert dirty.vector_sync_status == LongTermVectorSyncStatus.DIRTY.value
    assert dirty.vector_sync_error == "refresh failed"
    assert [item.memory_id for item in listed_dirty] == ["ltm-dirty"]
    assert ready.vector_sync_status == LongTermVectorSyncStatus.READY.value
    assert ready.vector_synced_at is not None
    assert ready.vector_synced_at.isoformat() == now.replace(tzinfo=None).isoformat()
    assert ready.vector_sync_error is None
