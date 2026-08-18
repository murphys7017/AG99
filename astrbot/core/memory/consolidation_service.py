from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from astrbot.core import logger

from .analyzer_manager import MemoryAnalyzerManager
from .analyzers.base import (
    MemoryAnalyzerConfigurationError,
    MemoryAnalyzerExecutionError,
)
from .config import MemoryAnalysisConfig, MemoryConsolidationConfig
from .history_source import extract_message_text
from .scope_context import scope_owner_id
from .store import MemoryStore
from .types import (
    Experience,
    ExperienceCategory,
    ScopeType,
    SessionInsight,
    ShortTermMemory,
    TopicState,
    TurnRecord,
)

SESSION_INSIGHT_ANALYZER_NAME = "session_insight_v1"
EXPERIENCE_EXTRACT_ANALYZER_NAME = "experience_extract_v1"

SESSION_INSIGHT_FIELDS = {
    "topic_summary": str,
    "progress_summary": str,
    "summary_text": str,
}
EXPERIENCE_REQUIRED_FIELDS = {
    "category": str,
    "summary": str,
    "importance": (int, float),
    "confidence": (int, float),
}


class ConsolidationService:
    def __init__(
        self,
        store: MemoryStore,
        analyzer_manager: MemoryAnalyzerManager | None = None,
        analysis_config: MemoryAnalysisConfig | None = None,
        consolidation_config: MemoryConsolidationConfig | None = None,
    ) -> None:
        self.store = store
        self.analyzer_manager = analyzer_manager
        self.analysis_config = analysis_config
        self.consolidation_config = consolidation_config

    async def should_run_consolidation(
        self,
        canonical_user_id: str,
        conversation_id: str | None,
        *,
        scope_type: ScopeType | str = ScopeType.USER,
        scope_id: str | None = None,
        umo: str | None = None,
    ) -> bool:
        if not self._is_enabled():
            logger.info(
                "memory consolidation skipped: disabled umo=%s conversation_id=%s",
                canonical_user_id,
                conversation_id,
            )
            return False

        owner_id = scope_owner_id(scope_type, scope_id or canonical_user_id, canonical_user_id)
        if owner_id is None:
            return False
        turn_records = await self._get_pending_turn_records(
            owner_id,
            conversation_id,
            scope_type=scope_type,
            scope_id=scope_id or owner_id,
            umo=umo,
        )
        threshold = self._get_threshold()
        should_run = len(turn_records) >= threshold
        logger.info(
            "memory consolidation check: canonical_user_id=%s conversation_id=%s pending_turns=%s threshold=%s should_run=%s",
            owner_id,
            conversation_id,
            len(turn_records),
            threshold,
            should_run,
        )
        return should_run

    async def run_for_scope(
        self,
        canonical_user_id: str,
        conversation_id: str | None,
        *,
        scope_type: ScopeType | str = ScopeType.USER,
        scope_id: str | None = None,
        umo: str | None = None,
    ) -> tuple[SessionInsight | None, list[Experience]]:
        if not self._is_enabled():
            return None, []

        owner_id = scope_owner_id(scope_type, scope_id or canonical_user_id, canonical_user_id)
        if owner_id is None:
            return None, []
        resolved_scope_id = scope_id or owner_id
        turn_records = await self._get_pending_turn_records(
            owner_id,
            conversation_id,
            scope_type=scope_type,
            scope_id=resolved_scope_id,
            umo=umo,
        )
        if len(turn_records) < self._get_threshold():
            return None, []

        latest_turn = turn_records[-1]
        topic_state = await self.store.get_topic_state(latest_turn.umo, conversation_id)
        short_term_memory = await self.store.get_short_term_memory(
            latest_turn.umo,
            conversation_id,
        )

        insight = await self._build_session_insight(
            canonical_user_id=owner_id,
            conversation_id=conversation_id,
            turn_records=turn_records,
            topic_state=topic_state,
            short_term_memory=short_term_memory,
        )
        experiences = await self._extract_experiences(
            insight=insight,
            turn_records=turn_records,
            scope_type=scope_type,
            scope_id=resolved_scope_id,
        )
        return insight, experiences

    def _is_enabled(self) -> bool:
        return bool(
            self.consolidation_config is not None
            and self.consolidation_config.enabled
            and self.analysis_config is not None
            and self.analysis_config.enabled
            and self.analyzer_manager is not None
        )

    def _get_threshold(self) -> int:
        if self.consolidation_config is None:
            raise RuntimeError("consolidation requested without configuration")
        return max(1, int(self.consolidation_config.min_short_term_updates))

    async def _get_pending_turn_records(
        self,
        canonical_user_id: str,
        conversation_id: str | None,
        *,
        scope_type: ScopeType | str,
        scope_id: str,
        umo: str | None,
    ) -> list[TurnRecord]:
        latest_insight = await self.store.get_latest_session_insight(
            canonical_user_id,
            conversation_id,
        )
        start_at = latest_insight.window_end_at if latest_insight is not None else None
        if self._enum_value(scope_type) == ScopeType.USER.value:
            turn_records = await self.store.list_turn_records_by_canonical_user(
                canonical_user_id,
                conversation_id,
                start_at,
            )
        else:
            turn_records = await self.store.list_turn_records_for_scope(
                scope_type,
                scope_id,
                conversation_id,
                start_at,
                umo=umo,
            )
        if start_at is not None:
            turn_records = [
                turn for turn in turn_records if turn.message_timestamp > start_at
            ]
        return turn_records

    async def _build_session_insight(
        self,
        *,
        canonical_user_id: str,
        conversation_id: str | None,
        turn_records: list[TurnRecord],
        topic_state: TopicState | None,
        short_term_memory: ShortTermMemory | None,
    ) -> SessionInsight:
        if self.analyzer_manager is None:
            raise RuntimeError("consolidation requested without analyzer manager")

        payload = self._build_session_insight_payload(
            turn_records=turn_records,
            topic_state=topic_state,
            short_term_memory=short_term_memory,
        )
        logger.info(
            "memory consolidation session insight started: canonical_user_id=%s conversation_id=%s turns=%s",
            canonical_user_id,
            conversation_id,
            len(turn_records),
        )
        results = await self.analyzer_manager.dispatch_stage(
            "session_insight_update",
            payload=payload,
            umo=turn_records[-1].umo,
            conversation_id=conversation_id,
        )
        result = results.get(SESSION_INSIGHT_ANALYZER_NAME)
        if result is None:
            raise MemoryAnalyzerConfigurationError(
                "session_insight_update missing required analyzer `session_insight_v1`"
            )
        data = self._validate_session_insight_payload(result.data)
        return SessionInsight(
            insight_id=str(uuid.uuid4()),
            umo=turn_records[-1].umo,
            conversation_id=conversation_id,
            platform_user_key=turn_records[-1].platform_user_key,
            canonical_user_id=canonical_user_id,
            window_start_at=turn_records[0].message_timestamp,
            window_end_at=turn_records[-1].message_timestamp,
            topic_summary=data["topic_summary"],
            progress_summary=data["progress_summary"],
            summary_text=data["summary_text"],
            created_at=datetime.now(UTC),
        )

    async def _extract_experiences(
        self,
        *,
        insight: SessionInsight,
        turn_records: list[TurnRecord],
        scope_type: ScopeType | str,
        scope_id: str,
    ) -> list[Experience]:
        if self.analyzer_manager is None:
            raise RuntimeError("consolidation requested without analyzer manager")

        payload = self._build_experience_extract_payload(
            insight=insight,
            turn_records=turn_records,
        )
        logger.info(
            "memory consolidation experience extraction started: insight_id=%s turns=%s",
            insight.insight_id,
            len(turn_records),
        )
        results = await self.analyzer_manager.dispatch_stage(
            "experience_extract",
            payload=payload,
            umo=insight.umo,
            conversation_id=insight.conversation_id,
        )
        result = results.get(EXPERIENCE_EXTRACT_ANALYZER_NAME)
        if result is None:
            raise MemoryAnalyzerConfigurationError(
                "experience_extract missing required analyzer `experience_extract_v1`"
            )
        raw_experiences = self._validate_experience_extract_payload(result.data)

        turn_source_refs = [f"turn:{turn.turn_id}" for turn in turn_records]
        insight_source_ref = f"insight:{insight.insight_id}"

        experiences: list[Experience] = []
        for item in raw_experiences:
            detail_summary = item.get("detail_summary")
            if detail_summary is not None and not isinstance(detail_summary, str):
                raise MemoryAnalyzerExecutionError(
                    "experience_extract returned invalid detail_summary type"
                )

            experiences.append(
                Experience(
                    experience_id=str(uuid.uuid4()),
                    umo=insight.umo,
                    conversation_id=insight.conversation_id,
                    platform_user_key=insight.platform_user_key,
                    canonical_user_id=insight.canonical_user_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    event_time=insight.window_end_at or datetime.now(UTC),
                    category=item["category"],
                    summary=item["summary"],
                    detail_summary=detail_summary,
                    importance=float(item["importance"]),
                    confidence=float(item["confidence"]),
                    source_refs=[*turn_source_refs, insight_source_ref],
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        return experiences

    def _build_session_insight_payload(
        self,
        *,
        turn_records: list[TurnRecord],
        topic_state: TopicState | None,
        short_term_memory: ShortTermMemory | None,
    ) -> dict[str, Any]:
        turn_payloads = [
            {
                "turn_id": turn.turn_id,
                "user": extract_message_text(turn.user_message),
                "assistant": extract_message_text(turn.assistant_message),
                "message_timestamp": turn.message_timestamp.isoformat(),
            }
            for turn in turn_records
        ]
        return {
            "turn_records_json": json.dumps(turn_payloads, ensure_ascii=False),
            "recent_dialogue_text": self._build_turn_dialogue_text(turn_records),
            "turn_count": str(len(turn_records)),
            "topic_state_current_topic": topic_state.current_topic
            if topic_state
            else "",
            "topic_state_summary": topic_state.topic_summary if topic_state else "",
            "short_term_summary": (
                short_term_memory.short_summary if short_term_memory else ""
            ),
            "short_term_active_focus": (
                short_term_memory.active_focus if short_term_memory else ""
            ),
        }

    def _build_experience_extract_payload(
        self,
        *,
        insight: SessionInsight,
        turn_records: list[TurnRecord],
    ) -> dict[str, Any]:
        turn_payloads = [
            {
                "turn_id": turn.turn_id,
                "user": extract_message_text(turn.user_message),
                "assistant": extract_message_text(turn.assistant_message),
                "message_timestamp": turn.message_timestamp.isoformat(),
            }
            for turn in turn_records
        ]
        return {
            "insight_topic_summary": insight.topic_summary or "",
            "insight_progress_summary": insight.progress_summary or "",
            "insight_summary_text": insight.summary_text or "",
            "window_start_at": (
                insight.window_start_at.isoformat() if insight.window_start_at else ""
            ),
            "window_end_at": (
                insight.window_end_at.isoformat() if insight.window_end_at else ""
            ),
            "turn_records_json": json.dumps(turn_payloads, ensure_ascii=False),
            "recent_dialogue_text": self._build_turn_dialogue_text(turn_records),
        }

    def _validate_session_insight_payload(self, data: Any) -> dict[str, str]:
        if not isinstance(data, dict):
            raise MemoryAnalyzerExecutionError(
                "session_insight_update returned invalid payload"
            )
        validated: dict[str, str] = {}
        for field_name, expected_type in SESSION_INSIGHT_FIELDS.items():
            value = data.get(field_name)
            if not isinstance(value, expected_type) or not value.strip():
                raise MemoryAnalyzerExecutionError(
                    f"session_insight_update missing required field `{field_name}`"
                )
            validated[field_name] = value.strip()
        return validated

    def _validate_experience_extract_payload(self, data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, dict):
            raise MemoryAnalyzerExecutionError(
                "experience_extract returned invalid payload"
            )
        experiences = data.get("experiences")
        if not isinstance(experiences, list):
            raise MemoryAnalyzerExecutionError(
                "experience_extract missing required field `experiences`"
            )

        validated: list[dict[str, Any]] = []
        valid_categories = {category.value for category in ExperienceCategory}
        for item in experiences:
            if not isinstance(item, dict):
                raise MemoryAnalyzerExecutionError(
                    "experience_extract returned invalid experience item"
                )
            for field_name, expected_type in EXPERIENCE_REQUIRED_FIELDS.items():
                value = item.get(field_name)
                if isinstance(expected_type, tuple):
                    valid = isinstance(value, expected_type)
                else:
                    valid = isinstance(value, expected_type) and bool(value.strip())
                if not valid:
                    raise MemoryAnalyzerExecutionError(
                        f"experience_extract item missing required field `{field_name}`"
                    )
            category = str(item["category"]).strip()
            if category not in valid_categories:
                raise MemoryAnalyzerExecutionError(
                    f"experience_extract returned invalid category `{category}`"
                )
            for score_field in ("importance", "confidence"):
                score = float(item[score_field])
                if not 0.0 <= score <= 1.0:
                    raise MemoryAnalyzerExecutionError(
                        f"experience_extract field `{score_field}` must be between 0 and 1"
                    )
            validated.append(item)
        return validated

    @staticmethod
    def _enum_value(value: ScopeType | str) -> str:
        return value.value if hasattr(value, "value") else str(value)

    @staticmethod
    def _build_turn_dialogue_text(turn_records: list[TurnRecord]) -> str:
        lines: list[str] = []
        for turn in turn_records:
            user_text = extract_message_text(turn.user_message)
            assistant_text = extract_message_text(turn.assistant_message)
            if user_text:
                lines.append(f"User: {user_text}")
            if assistant_text:
                lines.append(f"Assistant: {assistant_text}")
        return "\n".join(lines).strip()
