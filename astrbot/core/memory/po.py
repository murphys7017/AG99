from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlmodel import JSON, Field, MetaData, SQLModel, Text, UniqueConstraint


class BaseMemoryModel(SQLModel, table=False):
    metadata = MetaData()


class MemoryTurnRecord(BaseMemoryModel, table=True):
    __tablename__ = "memory_turn_records"  # type: ignore

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    turn_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
        max_length=64,
    )
    umo: str = Field(nullable=False, index=True, max_length=255)
    conversation_id: str | None = Field(default=None, index=True, max_length=64)
    platform_id: str | None = Field(default=None, index=True, max_length=64)
    platform_user_key: str | None = Field(default=None, index=True, max_length=255)
    canonical_user_id: str | None = Field(default=None, index=True, max_length=255)
    session_id: str | None = Field(default=None, index=True, max_length=128)
    user_message: dict = Field(default_factory=dict, sa_type=JSON)
    assistant_message: dict = Field(default_factory=dict, sa_type=JSON)
    message_timestamp: datetime = Field(nullable=False, index=True)
    source_refs: list = Field(default_factory=list, sa_type=JSON)
    scope_context: dict = Field(default_factory=dict, sa_type=JSON)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemoryTopicState(BaseMemoryModel, table=True):
    __tablename__ = "memory_topic_states"  # type: ignore

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    umo: str = Field(nullable=False, index=True, max_length=255)
    conversation_id: str = Field(default="", nullable=False, index=True, max_length=64)
    current_topic: str | None = Field(default=None, max_length=255)
    topic_summary: str | None = Field(default=None, sa_type=Text)
    topic_confidence: float = Field(default=0.0, nullable=False)
    last_active_at: datetime | None = Field(default=None, index=True)

    __table_args__ = (
        UniqueConstraint(
            "umo",
            "conversation_id",
            name="uix_memory_topic_state_scope",
        ),
    )


class MemoryShortTermMemory(BaseMemoryModel, table=True):
    __tablename__ = "memory_short_term_memories"  # type: ignore

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    umo: str = Field(nullable=False, index=True, max_length=255)
    conversation_id: str = Field(default="", nullable=False, index=True, max_length=64)
    short_summary: str | None = Field(default=None, sa_type=Text)
    active_focus: str | None = Field(default=None, sa_type=Text)
    updated_at: datetime | None = Field(default=None, index=True)
    revision: int = Field(default=0, nullable=False)
    fingerprint: str | None = Field(default=None, index=True, max_length=64)

    __table_args__ = (
        UniqueConstraint(
            "umo",
            "conversation_id",
            name="uix_memory_short_term_scope",
        ),
    )


class MemorySessionInsight(BaseMemoryModel, table=True):
    __tablename__ = "memory_session_insights"  # type: ignore

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    insight_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
        max_length=64,
    )
    umo: str = Field(nullable=False, index=True, max_length=255)
    conversation_id: str | None = Field(default=None, index=True, max_length=64)
    platform_user_key: str | None = Field(default=None, index=True, max_length=255)
    canonical_user_id: str = Field(nullable=False, index=True, max_length=255)
    window_start_at: datetime | None = Field(default=None, index=True)
    window_end_at: datetime | None = Field(default=None, index=True)
    topic_summary: str | None = Field(default=None, sa_type=Text)
    progress_summary: str | None = Field(default=None, sa_type=Text)
    summary_text: str | None = Field(default=None, sa_type=Text)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemoryExperience(BaseMemoryModel, table=True):
    __tablename__ = "memory_experiences"  # type: ignore

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    experience_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
        max_length=64,
    )
    umo: str = Field(nullable=False, index=True, max_length=255)
    conversation_id: str | None = Field(default=None, index=True, max_length=64)
    platform_user_key: str | None = Field(default=None, index=True, max_length=255)
    canonical_user_id: str = Field(nullable=False, index=True, max_length=255)
    scope_type: str = Field(nullable=False, index=True, max_length=32)
    scope_id: str = Field(nullable=False, index=True, max_length=255)
    event_time: datetime = Field(nullable=False, index=True)
    category: str = Field(nullable=False, index=True, max_length=64)
    summary: str = Field(nullable=False, sa_type=Text)
    detail_summary: str | None = Field(default=None, sa_type=Text)
    importance: float = Field(default=0.0, nullable=False)
    confidence: float = Field(default=0.0, nullable=False)
    source_refs: list = Field(default_factory=list, sa_type=JSON)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column_kwargs={"onupdate": lambda: datetime.now(timezone.utc)},
    )


class MemoryLongTermMemoryIndex(BaseMemoryModel, table=True):
    __tablename__ = "memory_long_term_memories"  # type: ignore

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    memory_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
        max_length=64,
    )
    umo: str = Field(nullable=False, index=True, max_length=255)
    canonical_user_id: str = Field(nullable=False, index=True, max_length=255)
    scope_type: str = Field(nullable=False, index=True, max_length=32)
    scope_id: str = Field(nullable=False, index=True, max_length=255)
    category: str = Field(nullable=False, index=True, max_length=64)
    title: str = Field(nullable=False, max_length=255)
    summary: str = Field(nullable=False, sa_type=Text)
    status: str = Field(nullable=False, index=True, max_length=32, default="active")
    doc_path: str = Field(nullable=False, max_length=512)
    importance: float = Field(default=0.0, nullable=False)
    confidence: float = Field(default=0.0, nullable=False)
    tags: list = Field(default_factory=list, sa_type=JSON)
    source_refs: list = Field(default_factory=list, sa_type=JSON)
    first_event_at: datetime | None = Field(default=None, index=True)
    last_event_at: datetime | None = Field(default=None, index=True)
    vector_sync_status: str = Field(
        nullable=False,
        index=True,
        max_length=32,
        default="pending",
    )
    vector_synced_at: datetime | None = Field(default=None, index=True)
    vector_sync_error: str | None = Field(default=None, sa_type=Text)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column_kwargs={"onupdate": lambda: datetime.now(timezone.utc)},
    )


class MemoryLongTermMemoryLink(BaseMemoryModel, table=True):
    __tablename__ = "memory_long_term_memory_links"  # type: ignore

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    link_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
        max_length=64,
    )
    memory_id: str = Field(nullable=False, index=True, max_length=64)
    experience_id: str = Field(nullable=False, index=True, max_length=64)
    relation_type: str = Field(nullable=False, index=True, max_length=32)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint(
            "memory_id",
            "experience_id",
            name="uix_memory_long_term_memory_experience_link",
        ),
    )


class MemoryLongTermPromotionCursor(BaseMemoryModel, table=True):
    __tablename__ = "memory_long_term_promotion_cursors"  # type: ignore

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    cursor_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
        max_length=64,
    )
    umo: str = Field(nullable=False, index=True, max_length=255)
    canonical_user_id: str = Field(nullable=False, index=True, max_length=255)
    scope_type: str = Field(nullable=False, index=True, max_length=32)
    scope_id: str = Field(nullable=False, index=True, max_length=255)
    last_processed_created_at: datetime | None = Field(default=None, index=True)
    last_processed_experience_id: str | None = Field(default=None, max_length=64)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint(
            "canonical_user_id",
            "scope_type",
            "scope_id",
            name="uix_memory_long_term_promotion_cursor_scope",
        ),
    )


class MemoryIdentityMapping(BaseMemoryModel, table=True):
    __tablename__ = "memory_identity_mappings"  # type: ignore

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    mapping_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
        max_length=64,
    )
    platform_id: str = Field(nullable=False, index=True, max_length=64)
    sender_user_id: str = Field(nullable=False, index=True, max_length=255)
    platform_user_key: str = Field(
        nullable=False, unique=True, index=True, max_length=255
    )
    canonical_user_id: str = Field(nullable=False, index=True, max_length=255)
    nickname_hint: str | None = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column_kwargs={"onupdate": lambda: datetime.now(timezone.utc)},
    )


class MemoryPersonaState(BaseMemoryModel, table=True):
    __tablename__ = "memory_persona_states"  # type: ignore

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    state_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
        max_length=64,
    )
    scope_type: str = Field(nullable=False, index=True, max_length=32)
    scope_id: str = Field(nullable=False, index=True, max_length=255)
    persona_id: str | None = Field(default=None, max_length=255)
    familiarity: float = Field(default=0.0, nullable=False)
    trust: float = Field(default=0.5, nullable=False)
    warmth: float = Field(default=0.5, nullable=False)
    formality_preference: float = Field(default=0.5, nullable=False)
    directness_preference: float = Field(default=0.5, nullable=False)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_id",
            name="uix_memory_persona_state_scope",
        ),
    )


class MemoryPersonaEvolutionLog(BaseMemoryModel, table=True):
    __tablename__ = "memory_persona_evolution_logs"  # type: ignore

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    log_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
        max_length=64,
    )
    scope_type: str = Field(nullable=False, index=True, max_length=32)
    scope_id: str = Field(nullable=False, index=True, max_length=255)
    before_state: dict | None = Field(default=None, sa_type=JSON)
    after_state: dict = Field(default_factory=dict, sa_type=JSON)
    reason: str | None = Field(default=None, sa_type=Text)
    source_refs: list = Field(default_factory=list, sa_type=JSON)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
