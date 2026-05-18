from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import and_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import col, desc, select, text

from astrbot.core import logger

from .config import MemoryConfig, get_memory_config
from .po import (
    BaseMemoryModel,
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
from .types import (
    Experience,
    LongTermMemoryIndex,
    LongTermMemoryLink,
    LongTermPromotionCursor,
    LongTermVectorSyncStatus,
    MemoryIdentityBinding,
    PersonaEvolutionLog,
    PersonaState,
    ScopeType,
    SessionInsight,
    ShortTermMemory,
    TopicState,
    TurnRecord,
)


class MemoryStore:
    _PLATFORM_USER_KEY_NULLABLE_MIGRATIONS = (
        {
            "table": "memory_session_insights",
            "create_sql": """
                CREATE TABLE "__tmp_memory_session_insights" (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    insight_id VARCHAR(64) NOT NULL,
                    umo VARCHAR(255) NOT NULL,
                    conversation_id VARCHAR(64),
                    platform_user_key VARCHAR(255),
                    canonical_user_id VARCHAR(255) NOT NULL,
                    window_start_at DATETIME,
                    window_end_at DATETIME,
                    topic_summary TEXT,
                    progress_summary TEXT,
                    summary_text TEXT,
                    created_at DATETIME NOT NULL
                )
            """,
            "indexes": (
                'CREATE UNIQUE INDEX "ix_memory_session_insights_insight_id" ON "memory_session_insights" ("insight_id")',
                'CREATE INDEX "ix_memory_session_insights_umo" ON "memory_session_insights" ("umo")',
                'CREATE INDEX "ix_memory_session_insights_conversation_id" ON "memory_session_insights" ("conversation_id")',
                'CREATE INDEX "ix_memory_session_insights_platform_user_key" ON "memory_session_insights" ("platform_user_key")',
                'CREATE INDEX "ix_memory_session_insights_canonical_user_id" ON "memory_session_insights" ("canonical_user_id")',
                'CREATE INDEX "ix_memory_session_insights_window_start_at" ON "memory_session_insights" ("window_start_at")',
                'CREATE INDEX "ix_memory_session_insights_window_end_at" ON "memory_session_insights" ("window_end_at")',
            ),
        },
        {
            "table": "memory_experiences",
            "create_sql": """
                CREATE TABLE "__tmp_memory_experiences" (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experience_id VARCHAR(64) NOT NULL,
                    umo VARCHAR(255) NOT NULL,
                    conversation_id VARCHAR(64),
                    platform_user_key VARCHAR(255),
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
                )
            """,
            "indexes": (
                'CREATE UNIQUE INDEX "ix_memory_experiences_experience_id" ON "memory_experiences" ("experience_id")',
                'CREATE INDEX "ix_memory_experiences_umo" ON "memory_experiences" ("umo")',
                'CREATE INDEX "ix_memory_experiences_conversation_id" ON "memory_experiences" ("conversation_id")',
                'CREATE INDEX "ix_memory_experiences_platform_user_key" ON "memory_experiences" ("platform_user_key")',
                'CREATE INDEX "ix_memory_experiences_canonical_user_id" ON "memory_experiences" ("canonical_user_id")',
                'CREATE INDEX "ix_memory_experiences_scope_type" ON "memory_experiences" ("scope_type")',
                'CREATE INDEX "ix_memory_experiences_scope_id" ON "memory_experiences" ("scope_id")',
                'CREATE INDEX "ix_memory_experiences_event_time" ON "memory_experiences" ("event_time")',
                'CREATE INDEX "ix_memory_experiences_category" ON "memory_experiences" ("category")',
            ),
        },
    )

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        config: MemoryConfig | None = None,
    ) -> None:
        self.config = config or get_memory_config()
        self.db_path = (
            Path(db_path) if db_path is not None else self.config.storage.sqlite_path
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_url = f"sqlite+aiosqlite:///{self.db_path.as_posix()}"
        self.engine = create_async_engine(
            self.database_url,
            echo=False,
            future=True,
            connect_args={"timeout": 30},
        )
        self.async_session = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self.inited = False

    @asynccontextmanager
    async def get_db(self):
        if not self.inited:
            await self.initialize()
        async with self.async_session() as session:
            yield session

    async def initialize(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(BaseMemoryModel.metadata.create_all)
            await self._migrate_nullable_platform_user_key_columns(conn)
        async with self.engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA synchronous=NORMAL"))
            await conn.execute(text("PRAGMA cache_size=20000"))
            await conn.execute(text("PRAGMA temp_store=MEMORY"))
            await conn.execute(text("PRAGMA mmap_size=134217728"))
            await conn.execute(text("PRAGMA optimize"))
        self.inited = True

    async def _migrate_nullable_platform_user_key_columns(self, conn) -> None:
        for migration in self._PLATFORM_USER_KEY_NULLABLE_MIGRATIONS:
            table_name = migration["table"]
            column_info = await self._get_table_column_info(conn, table_name)
            if not column_info:
                continue
            platform_user_key = next(
                (item for item in column_info if item["name"] == "platform_user_key"),
                None,
            )
            if platform_user_key is None or int(platform_user_key["notnull"]) == 0:
                continue

            temp_table = f"__tmp_{table_name}"
            column_names = ", ".join(item["name"] for item in column_info)
            await conn.execute(text(f'DROP TABLE IF EXISTS "{temp_table}"'))
            await conn.execute(text(str(migration["create_sql"])))
            await conn.execute(
                text(
                    f'INSERT INTO "{temp_table}" ({column_names}) '
                    f'SELECT {column_names} FROM "{table_name}"'
                )
            )
            await conn.execute(text(f'DROP TABLE "{table_name}"'))
            await conn.execute(
                text(f'ALTER TABLE "{temp_table}" RENAME TO "{table_name}"')
            )
            for index_sql in migration["indexes"]:
                await conn.execute(text(str(index_sql)))
            logger.info(
                "memory store migrated nullable platform_user_key: table=%s",
                table_name,
            )

    async def _get_table_column_info(self, conn, table_name: str) -> list[dict]:
        result = await conn.execute(text(f'PRAGMA table_info("{table_name}")'))
        return [dict(row) for row in result.mappings().all()]

    async def close(self) -> None:
        await self.engine.dispose()

    async def ping(self) -> bool:
        try:
            async with self.get_db() as session:
                result = await session.execute(text("SELECT 1"))
                return bool(result.scalar_one())
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory store ping failed: %s", exc)
            return False

    async def save_turn_record(self, record: TurnRecord) -> TurnRecord:
        async with self.get_db() as session:
            async with session.begin():
                result = await session.execute(
                    select(MemoryTurnRecord).where(
                        col(MemoryTurnRecord.turn_id) == record.turn_id
                    )
                )
                entity = result.scalar_one_or_none()
                if entity is None:
                    entity = MemoryTurnRecord(
                        turn_id=record.turn_id,
                        created_at=record.created_at or datetime.now(UTC),
                    )
                    session.add(entity)

                entity.umo = record.umo
                entity.conversation_id = record.conversation_id
                entity.platform_id = record.platform_id
                entity.platform_user_key = record.platform_user_key
                entity.canonical_user_id = record.canonical_user_id
                entity.session_id = record.session_id
                entity.user_message = record.user_message
                entity.assistant_message = record.assistant_message
                entity.message_timestamp = record.message_timestamp
                entity.source_refs = list(record.source_refs)

                await session.flush()
                await session.refresh(entity)
                return self._to_turn_record(entity)

    async def get_turn_record(self, turn_id: str) -> TurnRecord | None:
        async with self.get_db() as session:
            result = await session.execute(
                select(MemoryTurnRecord).where(col(MemoryTurnRecord.turn_id) == turn_id)
            )
            entity = result.scalar_one_or_none()
            return self._to_turn_record(entity) if entity else None

    async def get_recent_turn_records(
        self,
        umo: str,
        limit: int,
        conversation_id: str | None = None,
    ) -> list[TurnRecord]:
        async with self.get_db() as session:
            stmt = select(MemoryTurnRecord).where(col(MemoryTurnRecord.umo) == umo)
            if conversation_id is not None:
                stmt = stmt.where(
                    col(MemoryTurnRecord.conversation_id) == conversation_id
                )
            stmt = stmt.order_by(desc(MemoryTurnRecord.message_timestamp)).limit(limit)
            result = await session.execute(stmt)
            return [self._to_turn_record(item) for item in result.scalars().all()]

    async def upsert_topic_state(self, state: TopicState) -> TopicState:
        conversation_key = self._conversation_key(state.conversation_id)
        async with self.get_db() as session:
            async with session.begin():
                entity = await self._get_topic_state_entity(
                    session,
                    state.umo,
                    conversation_key,
                )
                if entity is None:
                    entity = MemoryTopicState(
                        umo=state.umo,
                        conversation_id=conversation_key,
                    )
                    session.add(entity)

                entity.current_topic = state.current_topic
                entity.topic_summary = state.topic_summary
                entity.topic_confidence = self._clamp_score(state.topic_confidence)
                entity.last_active_at = state.last_active_at

                await session.flush()
                await session.refresh(entity)
                return self._to_topic_state(entity)

    async def get_topic_state(
        self,
        umo: str,
        conversation_id: str | None,
    ) -> TopicState | None:
        async with self.get_db() as session:
            entity = await self._get_topic_state_entity(
                session,
                umo,
                self._conversation_key(conversation_id),
            )
            return self._to_topic_state(entity) if entity else None

    async def upsert_short_term_memory(
        self,
        memory: ShortTermMemory,
    ) -> ShortTermMemory:
        conversation_key = self._conversation_key(memory.conversation_id)
        async with self.get_db() as session:
            async with session.begin():
                entity = await self._get_short_term_memory_entity(
                    session,
                    memory.umo,
                    conversation_key,
                )
                if entity is None:
                    entity = MemoryShortTermMemory(
                        umo=memory.umo,
                        conversation_id=conversation_key,
                    )
                    session.add(entity)

                entity.short_summary = memory.short_summary
                entity.active_focus = memory.active_focus
                entity.updated_at = memory.updated_at

                await session.flush()
                await session.refresh(entity)
                return self._to_short_term_memory(entity)

    async def get_short_term_memory(
        self,
        umo: str,
        conversation_id: str | None,
    ) -> ShortTermMemory | None:
        async with self.get_db() as session:
            entity = await self._get_short_term_memory_entity(
                session,
                umo,
                self._conversation_key(conversation_id),
            )
            return self._to_short_term_memory(entity) if entity else None

    async def save_session_insight(self, insight: SessionInsight) -> SessionInsight:
        async with self.get_db() as session:
            async with session.begin():
                return await self._save_session_insight_with_session(session, insight)

    async def get_latest_session_insight(
        self,
        canonical_user_id: str,
        conversation_id: str | None,
    ) -> SessionInsight | None:
        async with self.get_db() as session:
            stmt = select(MemorySessionInsight).where(
                col(MemorySessionInsight.canonical_user_id) == canonical_user_id
            )
            if conversation_id is None:
                stmt = stmt.where(col(MemorySessionInsight.conversation_id).is_(None))
            else:
                stmt = stmt.where(
                    col(MemorySessionInsight.conversation_id) == conversation_id
                )
            stmt = stmt.order_by(
                desc(MemorySessionInsight.window_end_at),
                desc(MemorySessionInsight.created_at),
                desc(MemorySessionInsight.insight_id),
            ).limit(1)
            result = await session.execute(stmt)
            entity = result.scalar_one_or_none()
            return self._to_session_insight(entity) if entity else None

    async def save_experience(self, experience: Experience) -> Experience:
        async with self.get_db() as session:
            async with session.begin():
                return await self._save_experience_with_session(session, experience)

    async def get_experience(
        self,
        experience_id: str,
    ) -> Experience | None:
        async with self.get_db() as session:
            result = await session.execute(
                select(MemoryExperience).where(
                    col(MemoryExperience.experience_id) == experience_id
                )
            )
            entity = result.scalar_one_or_none()
            return self._to_experience(entity) if entity else None

    async def persist_consolidation_batch(
        self,
        insight: SessionInsight | None,
        experiences: list[Experience],
    ) -> tuple[SessionInsight | None, list[Experience]]:
        async with self.get_db() as session:
            async with session.begin():
                persisted_insight = (
                    await self._save_session_insight_with_session(session, insight)
                    if insight is not None
                    else None
                )
                persisted_experiences: list[Experience] = []
                for experience in experiences:
                    persisted_experiences.append(
                        await self._save_experience_with_session(session, experience)
                    )
                return persisted_insight, persisted_experiences

    async def list_recent_experiences(
        self,
        canonical_user_id: str,
        limit: int,
        conversation_id: str | None = None,
    ) -> list[Experience]:
        async with self.get_db() as session:
            stmt = select(MemoryExperience).where(
                col(MemoryExperience.canonical_user_id) == canonical_user_id
            )
            if conversation_id is not None:
                stmt = stmt.where(
                    col(MemoryExperience.conversation_id) == conversation_id
                )
            stmt = stmt.order_by(
                desc(MemoryExperience.event_time),
                desc(MemoryExperience.created_at),
                desc(MemoryExperience.experience_id),
            ).limit(limit)
            result = await session.execute(stmt)
            return [self._to_experience(item) for item in result.scalars().all()]

    async def list_experiences_for_scope(
        self,
        canonical_user_id: str,
        scope_type: ScopeType | str,
        scope_id: str,
        *,
        ascending: bool = True,
    ) -> list[Experience]:
        async with self.get_db() as session:
            stmt = select(MemoryExperience).where(
                and_(
                    col(MemoryExperience.canonical_user_id) == canonical_user_id,
                    col(MemoryExperience.scope_type) == self._enum_value(scope_type),
                    col(MemoryExperience.scope_id) == scope_id,
                )
            )
            if ascending:
                stmt = stmt.order_by(
                    MemoryExperience.event_time,
                    MemoryExperience.created_at,
                    MemoryExperience.experience_id,
                )
            else:
                stmt = stmt.order_by(
                    desc(MemoryExperience.event_time),
                    desc(MemoryExperience.created_at),
                    desc(MemoryExperience.experience_id),
                )
            result = await session.execute(stmt)
            return [self._to_experience(item) for item in result.scalars().all()]

    async def list_experiences_by_time_range(
        self,
        canonical_user_id: str,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> list[Experience]:
        async with self.get_db() as session:
            conditions = [col(MemoryExperience.canonical_user_id) == canonical_user_id]
            if start_at is not None:
                conditions.append(col(MemoryExperience.event_time) >= start_at)
            if end_at is not None:
                conditions.append(col(MemoryExperience.event_time) <= end_at)
            stmt = (
                select(MemoryExperience)
                .where(and_(*conditions))
                .order_by(
                    desc(MemoryExperience.event_time),
                    desc(MemoryExperience.created_at),
                    desc(MemoryExperience.experience_id),
                )
            )
            result = await session.execute(stmt)
            return [self._to_experience(item) for item in result.scalars().all()]

    async def list_turn_records_by_time_range(
        self,
        umo: str,
        conversation_id: str | None,
        start_at: datetime | None,
        end_at: datetime | None = None,
        *,
        start_exclusive: bool = False,
    ) -> list[TurnRecord]:
        async with self.get_db() as session:
            conditions = [col(MemoryTurnRecord.umo) == umo]
            if conversation_id is None:
                conditions.append(col(MemoryTurnRecord.conversation_id).is_(None))
            else:
                conditions.append(
                    col(MemoryTurnRecord.conversation_id) == conversation_id
                )
            if start_at is not None:
                if start_exclusive:
                    conditions.append(
                        col(MemoryTurnRecord.message_timestamp) > start_at
                    )
                else:
                    conditions.append(
                        col(MemoryTurnRecord.message_timestamp) >= start_at
                    )
            if end_at is not None:
                conditions.append(col(MemoryTurnRecord.message_timestamp) <= end_at)
            stmt = (
                select(MemoryTurnRecord)
                .where(and_(*conditions))
                .order_by(MemoryTurnRecord.message_timestamp)
            )
            result = await session.execute(stmt)
            return [self._to_turn_record(item) for item in result.scalars().all()]

    async def count_turn_records_after(
        self,
        umo: str,
        conversation_id: str | None,
        after: datetime,
    ) -> int:
        async with self.get_db() as session:
            conditions = [
                col(MemoryTurnRecord.umo) == umo,
                col(MemoryTurnRecord.message_timestamp) > after,
            ]
            if conversation_id is None:
                conditions.append(col(MemoryTurnRecord.conversation_id).is_(None))
            else:
                conditions.append(
                    col(MemoryTurnRecord.conversation_id) == conversation_id
                )
            stmt = select(MemoryTurnRecord.id).where(and_(*conditions))
            result = await session.execute(stmt)
            return len(result.scalars().all())

    async def list_turn_records_by_canonical_user(
        self,
        canonical_user_id: str,
        conversation_id: str | None,
        start_at: datetime | None,
        end_at: datetime | None = None,
    ) -> list[TurnRecord]:
        async with self.get_db() as session:
            conditions = [col(MemoryTurnRecord.canonical_user_id) == canonical_user_id]
            if conversation_id is None:
                conditions.append(col(MemoryTurnRecord.conversation_id).is_(None))
            else:
                conditions.append(
                    col(MemoryTurnRecord.conversation_id) == conversation_id
                )
            if start_at is not None:
                conditions.append(col(MemoryTurnRecord.message_timestamp) >= start_at)
            if end_at is not None:
                conditions.append(col(MemoryTurnRecord.message_timestamp) <= end_at)
            stmt = (
                select(MemoryTurnRecord)
                .where(and_(*conditions))
                .order_by(MemoryTurnRecord.message_timestamp)
            )
            result = await session.execute(stmt)
            return [self._to_turn_record(item) for item in result.scalars().all()]

    async def upsert_long_term_memory_index(
        self,
        memory: LongTermMemoryIndex,
    ) -> LongTermMemoryIndex:
        async with self.get_db() as session:
            async with session.begin():
                return await self._upsert_long_term_memory_index_with_session(
                    session,
                    memory,
                )

    async def get_long_term_memory_index(
        self,
        memory_id: str,
    ) -> LongTermMemoryIndex | None:
        async with self.get_db() as session:
            result = await session.execute(
                select(MemoryLongTermMemoryIndex).where(
                    col(MemoryLongTermMemoryIndex.memory_id) == memory_id
                )
            )
            entity = result.scalar_one_or_none()
            return self._to_long_term_memory_index(entity) if entity else None

    async def list_long_term_memory_indexes(
        self,
        canonical_user_id: str,
        limit: int,
        *,
        scope_type: ScopeType | str | None = None,
        scope_id: str | None = None,
    ) -> list[LongTermMemoryIndex]:
        async with self.get_db() as session:
            stmt = select(MemoryLongTermMemoryIndex).where(
                col(MemoryLongTermMemoryIndex.canonical_user_id) == canonical_user_id
            )
            if scope_type is not None:
                stmt = stmt.where(
                    col(MemoryLongTermMemoryIndex.scope_type)
                    == self._enum_value(scope_type)
                )
            if scope_id is not None:
                stmt = stmt.where(col(MemoryLongTermMemoryIndex.scope_id) == scope_id)
            stmt = stmt.order_by(
                desc(MemoryLongTermMemoryIndex.updated_at),
                desc(MemoryLongTermMemoryIndex.memory_id),
            )
            if limit > 0:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return [
                self._to_long_term_memory_index(item) for item in result.scalars().all()
            ]

    async def list_long_term_memories_by_vector_status(
        self,
        status: LongTermVectorSyncStatus | str,
        *,
        limit: int = 100,
    ) -> list[LongTermMemoryIndex]:
        async with self.get_db() as session:
            stmt = (
                select(MemoryLongTermMemoryIndex)
                .where(
                    col(MemoryLongTermMemoryIndex.vector_sync_status)
                    == self._enum_value(status)
                )
                .order_by(
                    MemoryLongTermMemoryIndex.updated_at,
                    MemoryLongTermMemoryIndex.memory_id,
                )
            )
            if limit > 0:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return [
                self._to_long_term_memory_index(item) for item in result.scalars().all()
            ]

    async def update_long_term_vector_sync_state(
        self,
        memory_id: str,
        *,
        status: LongTermVectorSyncStatus | str,
        synced_at: datetime | None = None,
        error: str | None = None,
    ) -> LongTermMemoryIndex:
        async with self.get_db() as session:
            async with session.begin():
                result = await session.execute(
                    select(MemoryLongTermMemoryIndex).where(
                        col(MemoryLongTermMemoryIndex.memory_id) == memory_id
                    )
                )
                entity = result.scalar_one_or_none()
                if entity is None:
                    raise RuntimeError(
                        f"long-term memory `{memory_id}` was not found for vector sync update"
                    )
                entity.vector_sync_status = self._enum_value(status)
                entity.vector_synced_at = synced_at
                entity.vector_sync_error = error
                entity.updated_at = datetime.now(UTC)
                await session.flush()
                await session.refresh(entity)
                return self._to_long_term_memory_index(entity)

    async def save_long_term_memory_link(
        self,
        link: LongTermMemoryLink,
    ) -> LongTermMemoryLink:
        async with self.get_db() as session:
            async with session.begin():
                return await self._save_long_term_memory_link_with_session(
                    session, link
                )

    async def list_long_term_memory_links(
        self,
        memory_id: str,
    ) -> list[LongTermMemoryLink]:
        async with self.get_db() as session:
            stmt = (
                select(MemoryLongTermMemoryLink)
                .where(col(MemoryLongTermMemoryLink.memory_id) == memory_id)
                .order_by(
                    MemoryLongTermMemoryLink.created_at,
                    MemoryLongTermMemoryLink.link_id,
                )
            )
            result = await session.execute(stmt)
            return [
                self._to_long_term_memory_link(item) for item in result.scalars().all()
            ]

    async def get_long_term_promotion_cursor(
        self,
        canonical_user_id: str,
        scope_type: ScopeType | str,
        scope_id: str,
    ) -> LongTermPromotionCursor | None:
        async with self.get_db() as session:
            entity = await self._get_long_term_promotion_cursor_entity(
                session,
                canonical_user_id,
                self._enum_value(scope_type),
                scope_id,
            )
            return self._to_long_term_promotion_cursor(entity) if entity else None

    async def upsert_long_term_promotion_cursor(
        self,
        cursor: LongTermPromotionCursor,
    ) -> LongTermPromotionCursor:
        async with self.get_db() as session:
            async with session.begin():
                return await self._upsert_long_term_promotion_cursor_with_session(
                    session,
                    cursor,
                )

    async def list_pending_experiences_for_scope(
        self,
        canonical_user_id: str,
        scope_type: ScopeType | str,
        scope_id: str,
        cursor: LongTermPromotionCursor | None,
    ) -> list[Experience]:
        async with self.get_db() as session:
            stmt = (
                select(MemoryExperience)
                .where(
                    and_(
                        col(MemoryExperience.canonical_user_id) == canonical_user_id,
                        col(MemoryExperience.scope_type)
                        == self._enum_value(scope_type),
                        col(MemoryExperience.scope_id) == scope_id,
                    )
                )
                .order_by(
                    MemoryExperience.created_at,
                    MemoryExperience.experience_id,
                )
            )
            result = await session.execute(stmt)
            experiences = [self._to_experience(item) for item in result.scalars().all()]
            if cursor is None or cursor.last_processed_created_at is None:
                return experiences

            pending: list[Experience] = []
            for experience in experiences:
                created_at = experience.created_at
                if created_at is None:
                    pending.append(experience)
                    continue
                if created_at > cursor.last_processed_created_at:
                    pending.append(experience)
                    continue
                if (
                    created_at == cursor.last_processed_created_at
                    and cursor.last_processed_experience_id is not None
                    and experience.experience_id > cursor.last_processed_experience_id
                ):
                    pending.append(experience)
            return pending

    async def persist_long_term_promotion_batch(
        self,
        memories: list[LongTermMemoryIndex],
        links: list[LongTermMemoryLink],
        cursor: LongTermPromotionCursor | None,
    ) -> tuple[
        list[LongTermMemoryIndex],
        list[LongTermMemoryLink],
        LongTermPromotionCursor | None,
    ]:
        async with self.get_db() as session:
            async with session.begin():
                persisted_memories: list[LongTermMemoryIndex] = []
                for memory in memories:
                    persisted_memories.append(
                        await self._upsert_long_term_memory_index_with_session(
                            session,
                            memory,
                        )
                    )
                persisted_links: list[LongTermMemoryLink] = []
                for link in links:
                    persisted_links.append(
                        await self._save_long_term_memory_link_with_session(
                            session, link
                        )
                    )
                persisted_cursor = None
                if cursor is not None:
                    persisted_cursor = (
                        await self._upsert_long_term_promotion_cursor_with_session(
                            session,
                            cursor,
                        )
                    )
                return persisted_memories, persisted_links, persisted_cursor

    async def save_identity_mapping(
        self,
        binding: MemoryIdentityBinding,
    ) -> MemoryIdentityBinding:
        async with self.get_db() as session:
            async with session.begin():
                result = await session.execute(
                    select(MemoryIdentityMapping).where(
                        col(MemoryIdentityMapping.platform_user_key)
                        == binding.platform_user_key
                    )
                )
                entity = result.scalar_one_or_none()
                if entity is None:
                    entity = MemoryIdentityMapping(
                        mapping_id=binding.mapping_id,
                        created_at=binding.created_at or datetime.now(UTC),
                    )
                    session.add(entity)

                entity.platform_id = binding.platform_id
                entity.sender_user_id = binding.sender_user_id
                entity.platform_user_key = binding.platform_user_key
                entity.canonical_user_id = binding.canonical_user_id
                entity.nickname_hint = binding.nickname_hint
                if binding.updated_at is not None:
                    entity.updated_at = binding.updated_at

                await session.flush()
                await session.refresh(entity)
                return self._to_identity_binding(entity)

    async def get_identity_mapping(
        self,
        platform_user_key: str,
    ) -> MemoryIdentityBinding | None:
        async with self.get_db() as session:
            result = await session.execute(
                select(MemoryIdentityMapping).where(
                    col(MemoryIdentityMapping.platform_user_key) == platform_user_key
                )
            )
            entity = result.scalar_one_or_none()
            return self._to_identity_binding(entity) if entity else None

    async def list_all_identity_mappings(self) -> list[MemoryIdentityBinding]:
        async with self.get_db() as session:
            stmt = select(MemoryIdentityMapping).order_by(
                MemoryIdentityMapping.platform_id,
                MemoryIdentityMapping.sender_user_id,
                MemoryIdentityMapping.platform_user_key,
            )
            result = await session.execute(stmt)
            return [self._to_identity_binding(item) for item in result.scalars().all()]

    async def sync_identity_mappings(
        self,
        bindings: list[MemoryIdentityBinding],
    ) -> int:
        async with self.get_db() as session:
            async with session.begin():
                result = await session.execute(select(MemoryIdentityMapping))
                existing_entities = {
                    entity.platform_user_key: entity
                    for entity in result.scalars().all()
                }
                target_keys = {binding.platform_user_key for binding in bindings}

                for platform_user_key, entity in existing_entities.items():
                    if platform_user_key in target_keys:
                        continue
                    await session.delete(entity)

                for binding in bindings:
                    entity = existing_entities.get(binding.platform_user_key)
                    if entity is None:
                        entity = MemoryIdentityMapping(
                            mapping_id=binding.mapping_id,
                            created_at=binding.created_at or datetime.now(UTC),
                        )
                        session.add(entity)
                    entity.platform_id = binding.platform_id
                    entity.sender_user_id = binding.sender_user_id
                    entity.platform_user_key = binding.platform_user_key
                    entity.canonical_user_id = binding.canonical_user_id
                    entity.nickname_hint = binding.nickname_hint
                    if binding.updated_at is not None:
                        entity.updated_at = binding.updated_at

                return len(bindings)

    async def delete_identity_mapping(self, platform_user_key: str) -> bool:
        async with self.get_db() as session:
            async with session.begin():
                result = await session.execute(
                    select(MemoryIdentityMapping).where(
                        col(MemoryIdentityMapping.platform_user_key)
                        == platform_user_key
                    )
                )
                entity = result.scalar_one_or_none()
                if entity is None:
                    return False
                await session.delete(entity)
                return True

    async def list_identity_mappings_for_canonical_user(
        self,
        canonical_user_id: str,
    ) -> list[MemoryIdentityBinding]:
        async with self.get_db() as session:
            stmt = (
                select(MemoryIdentityMapping)
                .where(
                    col(MemoryIdentityMapping.canonical_user_id) == canonical_user_id
                )
                .order_by(
                    MemoryIdentityMapping.platform_id,
                    MemoryIdentityMapping.sender_user_id,
                    MemoryIdentityMapping.platform_user_key,
                )
            )
            result = await session.execute(stmt)
            return [self._to_identity_binding(item) for item in result.scalars().all()]

    async def upsert_persona_state(self, state: PersonaState) -> PersonaState:
        async with self.get_db() as session:
            async with session.begin():
                result = await session.execute(
                    select(MemoryPersonaState).where(
                        and_(
                            col(MemoryPersonaState.scope_type)
                            == self._enum_value(state.scope_type),
                            col(MemoryPersonaState.scope_id) == state.scope_id,
                        )
                    )
                )
                entity = result.scalar_one_or_none()
                if entity is None:
                    entity = MemoryPersonaState(state_id=state.state_id)
                    session.add(entity)

                entity.scope_type = self._enum_value(state.scope_type)
                entity.scope_id = state.scope_id
                entity.persona_id = state.persona_id
                entity.familiarity = self._clamp_score(state.familiarity)
                entity.trust = self._clamp_score(state.trust)
                entity.warmth = self._clamp_score(state.warmth)
                entity.formality_preference = self._clamp_score(
                    state.formality_preference
                )
                entity.directness_preference = self._clamp_score(
                    state.directness_preference
                )
                entity.updated_at = state.updated_at or datetime.now(UTC)

                await session.flush()
                await session.refresh(entity)
                return self._to_persona_state(entity)

    async def get_persona_state(
        self,
        scope_type: ScopeType | str,
        scope_id: str,
    ) -> PersonaState | None:
        async with self.get_db() as session:
            result = await session.execute(
                select(MemoryPersonaState).where(
                    and_(
                        col(MemoryPersonaState.scope_type)
                        == self._enum_value(scope_type),
                        col(MemoryPersonaState.scope_id) == scope_id,
                    )
                )
            )
            entity = result.scalar_one_or_none()
            return self._to_persona_state(entity) if entity else None

    async def save_persona_evolution_log(
        self,
        log: PersonaEvolutionLog,
    ) -> PersonaEvolutionLog:
        async with self.get_db() as session:
            async with session.begin():
                result = await session.execute(
                    select(MemoryPersonaEvolutionLog).where(
                        col(MemoryPersonaEvolutionLog.log_id) == log.log_id
                    )
                )
                entity = result.scalar_one_or_none()
                if entity is None:
                    entity = MemoryPersonaEvolutionLog(
                        log_id=log.log_id,
                        created_at=log.created_at or datetime.now(UTC),
                    )
                    session.add(entity)

                entity.scope_type = self._enum_value(log.scope_type)
                entity.scope_id = log.scope_id
                entity.before_state = log.before_state
                entity.after_state = log.after_state
                entity.reason = log.reason
                entity.source_refs = list(log.source_refs)

                await session.flush()
                await session.refresh(entity)
                return self._to_persona_evolution_log(entity)

    async def _get_topic_state_entity(
        self,
        session: AsyncSession,
        umo: str,
        conversation_id: str,
    ) -> MemoryTopicState | None:
        result = await session.execute(
            select(MemoryTopicState).where(
                and_(
                    col(MemoryTopicState.umo) == umo,
                    col(MemoryTopicState.conversation_id) == conversation_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def _get_short_term_memory_entity(
        self,
        session: AsyncSession,
        umo: str,
        conversation_id: str,
    ) -> MemoryShortTermMemory | None:
        result = await session.execute(
            select(MemoryShortTermMemory).where(
                and_(
                    col(MemoryShortTermMemory.umo) == umo,
                    col(MemoryShortTermMemory.conversation_id) == conversation_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def _get_long_term_promotion_cursor_entity(
        self,
        session: AsyncSession,
        canonical_user_id: str,
        scope_type: str,
        scope_id: str,
    ) -> MemoryLongTermPromotionCursor | None:
        result = await session.execute(
            select(MemoryLongTermPromotionCursor).where(
                and_(
                    col(MemoryLongTermPromotionCursor.canonical_user_id)
                    == canonical_user_id,
                    col(MemoryLongTermPromotionCursor.scope_type) == scope_type,
                    col(MemoryLongTermPromotionCursor.scope_id) == scope_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def _save_session_insight_with_session(
        self,
        session: AsyncSession,
        insight: SessionInsight,
    ) -> SessionInsight:
        result = await session.execute(
            select(MemorySessionInsight).where(
                col(MemorySessionInsight.insight_id) == insight.insight_id
            )
        )
        entity = result.scalar_one_or_none()
        if entity is None:
            entity = MemorySessionInsight(
                insight_id=insight.insight_id,
                created_at=insight.created_at or datetime.now(UTC),
            )
            session.add(entity)

        entity.umo = insight.umo
        entity.conversation_id = insight.conversation_id
        entity.platform_user_key = insight.platform_user_key
        entity.canonical_user_id = insight.canonical_user_id
        entity.window_start_at = insight.window_start_at
        entity.window_end_at = insight.window_end_at
        entity.topic_summary = insight.topic_summary
        entity.progress_summary = insight.progress_summary
        entity.summary_text = insight.summary_text

        await session.flush()
        await session.refresh(entity)
        return self._to_session_insight(entity)

    async def _save_experience_with_session(
        self,
        session: AsyncSession,
        experience: Experience,
    ) -> Experience:
        result = await session.execute(
            select(MemoryExperience).where(
                col(MemoryExperience.experience_id) == experience.experience_id
            )
        )
        entity = result.scalar_one_or_none()
        if entity is None:
            entity = MemoryExperience(
                experience_id=experience.experience_id,
                created_at=experience.created_at or datetime.now(UTC),
            )
            session.add(entity)

        entity.umo = experience.umo
        entity.conversation_id = experience.conversation_id
        entity.platform_user_key = experience.platform_user_key
        entity.canonical_user_id = experience.canonical_user_id
        entity.scope_type = self._enum_value(experience.scope_type)
        entity.scope_id = experience.scope_id
        entity.event_time = experience.event_time
        entity.category = self._enum_value(experience.category)
        entity.summary = experience.summary
        entity.detail_summary = experience.detail_summary
        entity.importance = self._clamp_score(experience.importance)
        entity.confidence = self._clamp_score(experience.confidence)
        entity.source_refs = list(experience.source_refs)
        if experience.updated_at is not None:
            entity.updated_at = experience.updated_at

        await session.flush()
        await session.refresh(entity)
        return self._to_experience(entity)

    async def _upsert_long_term_memory_index_with_session(
        self,
        session: AsyncSession,
        memory: LongTermMemoryIndex,
    ) -> LongTermMemoryIndex:
        result = await session.execute(
            select(MemoryLongTermMemoryIndex).where(
                col(MemoryLongTermMemoryIndex.memory_id) == memory.memory_id
            )
        )
        entity = result.scalar_one_or_none()
        if entity is None:
            entity = MemoryLongTermMemoryIndex(
                memory_id=memory.memory_id,
                created_at=memory.created_at or datetime.now(UTC),
            )
            session.add(entity)

        entity.umo = memory.umo
        entity.canonical_user_id = memory.canonical_user_id
        entity.scope_type = self._enum_value(memory.scope_type)
        entity.scope_id = memory.scope_id
        entity.category = self._enum_value(memory.category)
        entity.title = memory.title
        entity.summary = memory.summary
        entity.status = self._enum_value(memory.status)
        entity.doc_path = memory.doc_path
        entity.importance = self._clamp_score(memory.importance)
        entity.confidence = self._clamp_score(memory.confidence)
        entity.tags = list(memory.tags)
        entity.source_refs = list(memory.source_refs)
        entity.first_event_at = memory.first_event_at
        entity.last_event_at = memory.last_event_at
        entity.vector_sync_status = self._enum_value(memory.vector_sync_status)
        entity.vector_synced_at = memory.vector_synced_at
        entity.vector_sync_error = memory.vector_sync_error
        if memory.updated_at is not None:
            entity.updated_at = memory.updated_at

        await session.flush()
        await session.refresh(entity)
        return self._to_long_term_memory_index(entity)

    async def _save_long_term_memory_link_with_session(
        self,
        session: AsyncSession,
        link: LongTermMemoryLink,
    ) -> LongTermMemoryLink:
        result = await session.execute(
            select(MemoryLongTermMemoryLink).where(
                and_(
                    col(MemoryLongTermMemoryLink.memory_id) == link.memory_id,
                    col(MemoryLongTermMemoryLink.experience_id) == link.experience_id,
                )
            )
        )
        entity = result.scalar_one_or_none()
        if entity is None:
            entity = MemoryLongTermMemoryLink(
                link_id=link.link_id,
                created_at=link.created_at or datetime.now(UTC),
            )
            session.add(entity)

        entity.memory_id = link.memory_id
        entity.experience_id = link.experience_id
        entity.relation_type = self._enum_value(link.relation_type)

        await session.flush()
        await session.refresh(entity)
        return self._to_long_term_memory_link(entity)

    async def _upsert_long_term_promotion_cursor_with_session(
        self,
        session: AsyncSession,
        cursor: LongTermPromotionCursor,
    ) -> LongTermPromotionCursor:
        entity = await self._get_long_term_promotion_cursor_entity(
            session,
            cursor.canonical_user_id,
            self._enum_value(cursor.scope_type),
            cursor.scope_id,
        )
        if entity is None:
            entity = MemoryLongTermPromotionCursor(
                cursor_id=cursor.cursor_id,
                umo=cursor.umo,
                canonical_user_id=cursor.canonical_user_id,
                scope_type=self._enum_value(cursor.scope_type),
                scope_id=cursor.scope_id,
            )
            session.add(entity)

        entity.umo = cursor.umo
        entity.canonical_user_id = cursor.canonical_user_id
        entity.last_processed_created_at = cursor.last_processed_created_at
        entity.last_processed_experience_id = cursor.last_processed_experience_id
        if cursor.updated_at is not None:
            entity.updated_at = cursor.updated_at

        await session.flush()
        await session.refresh(entity)
        return self._to_long_term_promotion_cursor(entity)

    @staticmethod
    def _conversation_key(conversation_id: str | None) -> str:
        return conversation_id or ""

    @staticmethod
    def _conversation_value(conversation_id: str) -> str | None:
        return conversation_id or None

    @staticmethod
    def _clamp_score(value: float) -> float:
        return max(0.0, min(1.0, value))

    @staticmethod
    def _enum_value(value: ScopeType | str) -> str:
        return value.value if hasattr(value, "value") else str(value)

    def _to_turn_record(self, entity: MemoryTurnRecord) -> TurnRecord:
        return TurnRecord(
            turn_id=entity.turn_id,
            umo=entity.umo,
            conversation_id=entity.conversation_id,
            platform_id=entity.platform_id,
            platform_user_key=entity.platform_user_key,
            canonical_user_id=entity.canonical_user_id,
            session_id=entity.session_id,
            user_message=dict(entity.user_message or {}),
            assistant_message=dict(entity.assistant_message or {}),
            message_timestamp=entity.message_timestamp,
            source_refs=list(entity.source_refs or []),
            created_at=entity.created_at,
        )

    def _to_topic_state(self, entity: MemoryTopicState) -> TopicState:
        return TopicState(
            umo=entity.umo,
            conversation_id=self._conversation_value(entity.conversation_id),
            current_topic=entity.current_topic,
            topic_summary=entity.topic_summary,
            topic_confidence=entity.topic_confidence,
            last_active_at=entity.last_active_at,
        )

    def _to_short_term_memory(
        self,
        entity: MemoryShortTermMemory,
    ) -> ShortTermMemory:
        return ShortTermMemory(
            umo=entity.umo,
            conversation_id=self._conversation_value(entity.conversation_id),
            short_summary=entity.short_summary,
            active_focus=entity.active_focus,
            updated_at=entity.updated_at,
        )

    @staticmethod
    def _to_session_insight(entity: MemorySessionInsight) -> SessionInsight:
        return SessionInsight(
            insight_id=entity.insight_id,
            umo=entity.umo,
            conversation_id=entity.conversation_id,
            platform_user_key=entity.platform_user_key,
            canonical_user_id=entity.canonical_user_id,
            window_start_at=entity.window_start_at,
            window_end_at=entity.window_end_at,
            topic_summary=entity.topic_summary,
            progress_summary=entity.progress_summary,
            summary_text=entity.summary_text,
            created_at=entity.created_at,
        )

    @staticmethod
    def _to_experience(entity: MemoryExperience) -> Experience:
        return Experience(
            experience_id=entity.experience_id,
            umo=entity.umo,
            conversation_id=entity.conversation_id,
            platform_user_key=entity.platform_user_key,
            canonical_user_id=entity.canonical_user_id,
            scope_type=entity.scope_type,
            scope_id=entity.scope_id,
            event_time=entity.event_time,
            category=entity.category,
            summary=entity.summary,
            detail_summary=entity.detail_summary,
            importance=entity.importance,
            confidence=entity.confidence,
            source_refs=list(entity.source_refs or []),
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    @staticmethod
    def _to_long_term_memory_index(
        entity: MemoryLongTermMemoryIndex,
    ) -> LongTermMemoryIndex:
        return LongTermMemoryIndex(
            memory_id=entity.memory_id,
            umo=entity.umo,
            canonical_user_id=entity.canonical_user_id,
            scope_type=entity.scope_type,
            scope_id=entity.scope_id,
            category=entity.category,
            title=entity.title,
            summary=entity.summary,
            status=entity.status,
            doc_path=entity.doc_path,
            importance=entity.importance,
            confidence=entity.confidence,
            tags=list(entity.tags or []),
            source_refs=list(entity.source_refs or []),
            first_event_at=entity.first_event_at,
            last_event_at=entity.last_event_at,
            vector_sync_status=entity.vector_sync_status,
            vector_synced_at=entity.vector_synced_at,
            vector_sync_error=entity.vector_sync_error,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    @staticmethod
    def _to_long_term_memory_link(
        entity: MemoryLongTermMemoryLink,
    ) -> LongTermMemoryLink:
        return LongTermMemoryLink(
            link_id=entity.link_id,
            memory_id=entity.memory_id,
            experience_id=entity.experience_id,
            relation_type=entity.relation_type,
            created_at=entity.created_at,
        )

    @staticmethod
    def _to_long_term_promotion_cursor(
        entity: MemoryLongTermPromotionCursor,
    ) -> LongTermPromotionCursor:
        return LongTermPromotionCursor(
            cursor_id=entity.cursor_id,
            umo=entity.umo,
            canonical_user_id=entity.canonical_user_id,
            scope_type=entity.scope_type,
            scope_id=entity.scope_id,
            last_processed_created_at=entity.last_processed_created_at,
            last_processed_experience_id=entity.last_processed_experience_id,
            updated_at=entity.updated_at,
        )

    @staticmethod
    def _to_identity_binding(entity: MemoryIdentityMapping) -> MemoryIdentityBinding:
        return MemoryIdentityBinding(
            mapping_id=entity.mapping_id,
            platform_id=entity.platform_id,
            sender_user_id=entity.sender_user_id,
            platform_user_key=entity.platform_user_key,
            canonical_user_id=entity.canonical_user_id,
            nickname_hint=entity.nickname_hint,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    @staticmethod
    def _to_persona_state(entity: MemoryPersonaState) -> PersonaState:
        return PersonaState(
            state_id=entity.state_id,
            scope_type=entity.scope_type,
            scope_id=entity.scope_id,
            persona_id=entity.persona_id,
            familiarity=entity.familiarity,
            trust=entity.trust,
            warmth=entity.warmth,
            formality_preference=entity.formality_preference,
            directness_preference=entity.directness_preference,
            updated_at=entity.updated_at,
        )

    @staticmethod
    def _to_persona_evolution_log(
        entity: MemoryPersonaEvolutionLog,
    ) -> PersonaEvolutionLog:
        return PersonaEvolutionLog(
            log_id=entity.log_id,
            scope_type=entity.scope_type,
            scope_id=entity.scope_id,
            before_state=entity.before_state,
            after_state=entity.after_state,
            reason=entity.reason,
            source_refs=list(entity.source_refs or []),
            created_at=entity.created_at,
        )
