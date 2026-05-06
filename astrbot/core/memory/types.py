from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

JsonValue = str | int | float | bool | None | dict[str, Any] | list[Any]
JsonDict = dict[str, JsonValue]
MessagePayload = dict[str, Any]
SourceRef = str


class ScopeType(str, Enum):
    USER = "user"
    CONVERSATION = "conversation"
    SESSION = "session"
    GLOBAL = "global"


class ExperienceCategory(str, Enum):
    USER_FACT = "user_fact"
    USER_PREFERENCE = "user_preference"
    PROJECT_PROGRESS = "project_progress"
    INTERACTION_PATTERN = "interaction_pattern"
    RELATIONSHIP_SIGNAL = "relationship_signal"
    EPISODIC_EVENT = "episodic_event"


class LongTermMemoryStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    CONTRADICTED = "contradicted"


class LongTermVectorSyncStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    DIRTY = "dirty"


class LongTermMemoryLinkRelation(str, Enum):
    SEED = "seed"
    SUPPORT = "support"
    UPDATE = "update"
    CONTRADICT = "contradict"


@dataclass(slots=True)
class ScopeRef:
    scope_type: ScopeType | str
    scope_id: str


@dataclass(slots=True)
class MemoryUpdateRequest:
    umo: str
    conversation_id: str | None
    platform_id: str | None
    platform_user_key: str | None
    canonical_user_id: str | None
    session_id: str | None
    provider_request: JsonDict | None
    user_message: MessagePayload
    assistant_message: MessagePayload
    message_timestamp: datetime
    turn_id: str | None = None
    source_refs: list[SourceRef] = field(default_factory=list)


@dataclass(slots=True)
class TurnRecord:
    turn_id: str
    umo: str
    conversation_id: str | None
    platform_id: str | None
    platform_user_key: str | None
    canonical_user_id: str | None
    session_id: str | None
    user_message: MessagePayload
    assistant_message: MessagePayload
    message_timestamp: datetime
    source_refs: list[SourceRef] = field(default_factory=list)
    created_at: datetime | None = None


@dataclass(slots=True)
class TopicState:
    umo: str
    conversation_id: str | None
    current_topic: str | None
    topic_summary: str | None
    topic_confidence: float = 0.0
    last_active_at: datetime | None = None


@dataclass(slots=True)
class ShortTermMemory:
    umo: str
    conversation_id: str | None
    short_summary: str | None
    active_focus: str | None
    updated_at: datetime | None = None


@dataclass(slots=True)
class SessionInsight:
    insight_id: str
    umo: str
    conversation_id: str | None
    platform_user_key: str | None
    canonical_user_id: str
    window_start_at: datetime | None
    window_end_at: datetime | None
    topic_summary: str | None
    progress_summary: str | None
    summary_text: str | None
    created_at: datetime | None = None


@dataclass(slots=True)
class Experience:
    experience_id: str
    umo: str
    conversation_id: str | None
    platform_user_key: str | None
    canonical_user_id: str
    scope_type: ScopeType | str
    scope_id: str
    event_time: datetime
    category: ExperienceCategory | str
    summary: str
    detail_summary: str | None = None
    importance: float = 0.0
    confidence: float = 0.0
    source_refs: list[SourceRef] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class LongTermMemoryIndex:
    memory_id: str
    umo: str
    canonical_user_id: str
    scope_type: ScopeType | str
    scope_id: str
    category: ExperienceCategory | str
    title: str
    summary: str
    status: LongTermMemoryStatus | str
    doc_path: str
    importance: float = 0.0
    confidence: float = 0.0
    tags: list[str] = field(default_factory=list)
    source_refs: list[SourceRef] = field(default_factory=list)
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None
    vector_sync_status: LongTermVectorSyncStatus | str = (
        LongTermVectorSyncStatus.PENDING
    )
    vector_synced_at: datetime | None = None
    vector_sync_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class LongTermMemoryLink:
    link_id: str
    memory_id: str
    experience_id: str
    relation_type: LongTermMemoryLinkRelation | str
    created_at: datetime | None = None


@dataclass(slots=True)
class LongTermPromotionCursor:
    cursor_id: str
    umo: str
    canonical_user_id: str
    scope_type: ScopeType | str
    scope_id: str
    last_processed_created_at: datetime | None = None
    last_processed_experience_id: str | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class LongTermMemoryDocument:
    memory_id: str
    umo: str
    canonical_user_id: str
    scope_type: ScopeType | str
    scope_id: str
    category: ExperienceCategory | str
    status: LongTermMemoryStatus | str
    title: str
    summary: str
    detail_summary: str | None
    importance: float = 0.0
    confidence: float = 0.0
    supporting_experiences: list[str] = field(default_factory=list)
    updates: list[JsonDict] = field(default_factory=list)
    source_refs: list[SourceRef] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw_text: str | None = None


@dataclass(slots=True)
class VectorSearchHit:
    memory_id: str
    score: float
    metadata: JsonDict = field(default_factory=dict)


@dataclass(slots=True)
class DocumentSearchRequest:
    canonical_user_id: str
    query: str
    umo: str | None = None
    conversation_id: str | None = None
    scope_type: ScopeType | str | None = None
    scope_id: str | None = None
    category: ExperienceCategory | str | None = None
    top_k: int = 5
    include_body: bool = False


@dataclass(slots=True)
class DocumentSearchResult:
    memory_id: str
    score: float
    title: str
    summary: str
    category: ExperienceCategory | str
    tags: list[str] = field(default_factory=list)
    doc_path: str = ""
    body_text: str | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class PersonaState:
    state_id: str
    scope_type: ScopeType | str
    scope_id: str
    persona_id: str | None
    familiarity: float = 0.0
    trust: float = 0.0
    warmth: float = 0.0
    formality_preference: float = 0.0
    directness_preference: float = 0.0
    updated_at: datetime | None = None


@dataclass(slots=True)
class PersonaEvolutionLog:
    log_id: str
    scope_type: ScopeType | str
    scope_id: str
    before_state: JsonDict | None
    after_state: JsonDict
    reason: str | None = None
    source_refs: list[SourceRef] = field(default_factory=list)
    created_at: datetime | None = None


@dataclass(slots=True)
class MemoryIdentity:
    umo: str
    platform_id: str | None
    sender_user_id: str | None
    sender_nickname: str | None
    platform_user_key: str | None
    canonical_user_id: str | None = None


@dataclass(slots=True)
class MemoryIdentityBinding:
    mapping_id: str
    platform_id: str
    sender_user_id: str
    platform_user_key: str
    canonical_user_id: str
    nickname_hint: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class MemorySnapshot:
    umo: str
    conversation_id: str | None
    platform_user_key: str | None = None
    canonical_user_id: str | None = None
    topic_state: TopicState | None = None
    short_term_memory: ShortTermMemory | None = None
    experiences: list[Experience] = field(default_factory=list)
    long_term_memories: list[LongTermMemoryIndex] = field(default_factory=list)
    persona_state: PersonaState | None = None
    debug_meta: JsonDict = field(default_factory=dict)
