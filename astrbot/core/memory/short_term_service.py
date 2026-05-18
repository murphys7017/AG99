from __future__ import annotations

import json
from typing import Any

from astrbot.core import logger

from .analyzer_manager import MemoryAnalyzerManager
from .analyzers.base import (
    MemoryAnalyzerConfigurationError,
    MemoryAnalyzerExecutionError,
)
from .config import MemoryAnalysisConfig, MemoryShortTermConfig
from .history_source import RecentConversationSource, extract_message_text
from .store import MemoryStore
from .types import ShortTermMemory, TopicState, TurnRecord

TOPIC_ANALYZER_NAME = "topic_v1"
FOCUS_ANALYZER_NAME = "focus_v1"
SUMMARY_ANALYZER_NAME = "summary_v1"

TOPIC_ANALYZER_FIELDS = {
    "current_topic": str,
    "topic_summary": str,
    "topic_confidence": (int, float),
}
FOCUS_ANALYZER_FIELDS = {
    "active_focus": str,
}
SUMMARY_ANALYZER_FIELDS = {
    "short_summary": str,
}


class ShortTermMemoryService:
    def __init__(
        self,
        store: MemoryStore,
        history_source: RecentConversationSource,
        analyzer_manager: MemoryAnalyzerManager | None = None,
        analysis_config: MemoryAnalysisConfig | None = None,
        short_term_config: MemoryShortTermConfig | None = None,
    ) -> None:
        self.store = store
        self.history_source = history_source
        self.analyzer_manager = analyzer_manager
        self.analysis_config = analysis_config
        self.short_term_config = short_term_config or getattr(
            store.config,
            "short_term",
            MemoryShortTermConfig(),
        )

    async def update_topic_state(
        self,
        turn: TurnRecord,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> TopicState:
        recent_payloads = await self.history_source.get_recent_turn_payloads(
            conversation_history=conversation_history,
            umo=turn.umo,
            conversation_id=turn.conversation_id,
        )
        analysis_result = await self._run_short_term_analysis(
            turn,
            recent_payloads,
        )

        state = TopicState(
            umo=turn.umo,
            conversation_id=turn.conversation_id,
            current_topic=analysis_result.get("current_topic") or None,
            topic_summary=analysis_result.get("topic_summary") or None,
            topic_confidence=self._validate_score(
                analyzer_name=TOPIC_ANALYZER_NAME,
                field_name="topic_confidence",
                value=analysis_result.get("topic_confidence"),
            ),
            last_active_at=turn.message_timestamp,
        )
        return await self.store.upsert_topic_state(state)

    async def update_short_term_memory(
        self,
        turn: TurnRecord,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> ShortTermMemory:
        recent_payloads = await self.history_source.get_recent_turn_payloads(
            conversation_history=conversation_history,
            umo=turn.umo,
            conversation_id=turn.conversation_id,
        )
        if not recent_payloads:
            recent_payloads = [
                {
                    "user_message": turn.user_message,
                    "assistant_message": turn.assistant_message,
                }
            ]

        analysis_result = await self._run_short_term_analysis(
            turn,
            recent_payloads,
        )

        memory = ShortTermMemory(
            umo=turn.umo,
            conversation_id=turn.conversation_id,
            short_summary=analysis_result.get("short_summary") or None,
            active_focus=analysis_result.get("active_focus") or None,
            updated_at=turn.message_timestamp,
        )
        return await self.store.upsert_short_term_memory(memory)

    async def update_after_turn(
        self,
        turn: TurnRecord,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> tuple[TopicState | None, ShortTermMemory | None]:
        existing_short_term = await self.store.get_short_term_memory(
            turn.umo,
            turn.conversation_id,
        )
        if not await self._should_update_after_turn(turn, existing_short_term):
            logger.info(
                "memory short-term update skipped by frequency: umo=%s conversation_id=%s interval=%s min_chars=%s",
                turn.umo,
                turn.conversation_id,
                self.short_term_config.update_interval_turns,
                self.short_term_config.update_min_chars,
            )
            topic_state = await self.store.get_topic_state(
                turn.umo,
                turn.conversation_id,
            )
            return topic_state, existing_short_term

        recent_payloads = await self.history_source.get_recent_turn_payloads(
            conversation_history=conversation_history,
            umo=turn.umo,
            conversation_id=turn.conversation_id,
        )
        if not recent_payloads:
            recent_payloads = [
                {
                    "user_message": turn.user_message,
                    "assistant_message": turn.assistant_message,
                }
            ]

        analysis_result = await self._run_short_term_analysis(
            turn,
            recent_payloads,
        )

        topic_state = await self.store.upsert_topic_state(
            TopicState(
                umo=turn.umo,
                conversation_id=turn.conversation_id,
                current_topic=analysis_result.get("current_topic") or None,
                topic_summary=analysis_result.get("topic_summary") or None,
                topic_confidence=self._validate_score(
                    analyzer_name=TOPIC_ANALYZER_NAME,
                    field_name="topic_confidence",
                    value=analysis_result.get("topic_confidence"),
                ),
                last_active_at=turn.message_timestamp,
            )
        )
        short_term_memory = await self.store.upsert_short_term_memory(
            ShortTermMemory(
                umo=turn.umo,
                conversation_id=turn.conversation_id,
                short_summary=analysis_result.get("short_summary") or None,
                active_focus=analysis_result.get("active_focus") or None,
                updated_at=turn.message_timestamp,
            )
        )
        return topic_state, short_term_memory

    async def _should_update_after_turn(
        self,
        turn: TurnRecord,
        existing_short_term: ShortTermMemory | None,
    ) -> bool:
        if not self.short_term_config.enabled:
            return False
        if existing_short_term is None or existing_short_term.updated_at is None:
            return True

        interval = max(1, int(self.short_term_config.update_interval_turns))
        turns_since_update = await self.store.count_turn_records_after(
            turn.umo,
            turn.conversation_id,
            existing_short_term.updated_at,
        )
        if turns_since_update >= interval:
            return True

        min_chars = max(0, int(self.short_term_config.update_min_chars))
        if min_chars <= 0:
            return False

        turns = await self.store.list_turn_records_by_time_range(
            turn.umo,
            turn.conversation_id,
            existing_short_term.updated_at,
            None,
            start_exclusive=True,
        )
        return self._count_turn_chars(turns) >= min_chars

    def _count_turn_chars(self, turns: list[TurnRecord]) -> int:
        total = 0
        for item in turns:
            total += len(extract_message_text(item.user_message))
            total += len(extract_message_text(item.assistant_message))
        return total

    async def _run_short_term_analysis(
        self,
        turn: TurnRecord,
        recent_payloads: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self.analysis_config is None or not self.analysis_config.enabled:
            raise MemoryAnalyzerConfigurationError(
                "short-term memory analysis is not enabled"
            )
        if self.analyzer_manager is None:
            raise RuntimeError("short term analysis requested without analyzer manager")

        payload = self._build_short_term_analysis_payload(turn, recent_payloads)
        logger.info(
            "memory short-term analysis started: stage=%s umo=%s conversation_id=%s analyzers=%s",
            "short_term_update",
            turn.umo,
            turn.conversation_id,
            [
                TOPIC_ANALYZER_NAME,
                FOCUS_ANALYZER_NAME,
                SUMMARY_ANALYZER_NAME,
            ],
        )
        results = await self.analyzer_manager.dispatch_stage(
            "short_term_update",
            payload=payload,
            umo=turn.umo,
            conversation_id=turn.conversation_id,
        )
        analyzed = self._build_short_term_analysis_result(results)
        logger.info(
            "memory short-term analysis finished: stage=%s umo=%s conversation_id=%s keys=%s",
            "short_term_update",
            turn.umo,
            turn.conversation_id,
            sorted(analyzed),
        )
        return analyzed

    def _build_short_term_analysis_payload(
        self,
        turn: TurnRecord,
        recent_payloads: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest_user_text = extract_message_text(turn.user_message)
        latest_assistant_text = extract_message_text(turn.assistant_message)
        recent_turns = [
            {
                "user": extract_message_text(payload.get("user_message")),
                "assistant": extract_message_text(payload.get("assistant_message")),
            }
            for payload in recent_payloads
        ]
        recent_dialogue_text = "\n".join(_build_dialogue_lines(recent_turns)).strip()
        return {
            "umo": turn.umo,
            "conversation_id": turn.conversation_id or "",
            "latest_user_text": latest_user_text,
            "latest_assistant_text": latest_assistant_text,
            "recent_turns_json": json.dumps(recent_turns, ensure_ascii=False),
            "recent_dialogue_text": recent_dialogue_text,
            "recent_turns_window": str(len(recent_turns)),
        }

    def _build_short_term_analysis_result(
        self,
        results: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(results, dict):
            raise MemoryAnalyzerExecutionError(
                "short_term_update returned invalid analyzer results"
            )

        topic_result = self._get_required_analyzer_result(results, TOPIC_ANALYZER_NAME)
        focus_result = self._get_required_analyzer_result(results, FOCUS_ANALYZER_NAME)
        summary_result = self._get_required_analyzer_result(
            results,
            SUMMARY_ANALYZER_NAME,
        )

        topic_data = self._validate_analyzer_payload(
            analyzer_name=TOPIC_ANALYZER_NAME,
            data=getattr(topic_result, "data", None),
            required_fields=TOPIC_ANALYZER_FIELDS,
        )
        focus_data = self._validate_analyzer_payload(
            analyzer_name=FOCUS_ANALYZER_NAME,
            data=getattr(focus_result, "data", None),
            required_fields=FOCUS_ANALYZER_FIELDS,
        )
        summary_data = self._validate_analyzer_payload(
            analyzer_name=SUMMARY_ANALYZER_NAME,
            data=getattr(summary_result, "data", None),
            required_fields=SUMMARY_ANALYZER_FIELDS,
        )

        return {
            "current_topic": topic_data["current_topic"],
            "topic_summary": topic_data["topic_summary"],
            "topic_confidence": self._validate_score(
                analyzer_name=TOPIC_ANALYZER_NAME,
                field_name="topic_confidence",
                value=topic_data["topic_confidence"],
            ),
            "active_focus": focus_data["active_focus"],
            "short_summary": summary_data["short_summary"],
        }

    def _get_required_analyzer_result(
        self,
        results: dict[str, Any],
        analyzer_name: str,
    ) -> Any:
        result = results.get(analyzer_name)
        if result is None:
            raise MemoryAnalyzerConfigurationError(
                f"short_term_update missing required analyzer `{analyzer_name}`"
            )
        logger.info(
            "memory analyzer result received: analyzer=%s provider_id=%s model=%s keys=%s",
            analyzer_name,
            getattr(result, "provider_id", None),
            getattr(result, "model", None),
            sorted(getattr(result, "data", {}).keys())
            if isinstance(getattr(result, "data", None), dict)
            else [],
        )
        return result

    def _validate_analyzer_payload(
        self,
        *,
        analyzer_name: str,
        data: Any,
        required_fields: dict[str, type | tuple[type, ...]],
    ) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise MemoryAnalyzerExecutionError(
                f"analyzer `{analyzer_name}` returned invalid payload"
            )

        validated: dict[str, Any] = {}
        for field_name, expected_type in required_fields.items():
            value = data.get(field_name)
            if not self._is_non_empty_value(value):
                raise MemoryAnalyzerExecutionError(
                    f"analyzer `{analyzer_name}` missing required field `{field_name}`"
                )
            if not isinstance(value, expected_type):
                raise MemoryAnalyzerExecutionError(
                    f"analyzer `{analyzer_name}` field `{field_name}` has invalid type"
                )
            validated[field_name] = value
        return validated

    def _is_non_empty_value(self, value: Any) -> bool:
        if isinstance(value, str):
            return bool(value.strip())
        return value is not None

    def _validate_score(
        self,
        *,
        analyzer_name: str,
        field_name: str,
        value: Any,
    ) -> float:
        if not isinstance(value, int | float):
            raise MemoryAnalyzerExecutionError(
                f"analyzer `{analyzer_name}` field `{field_name}` has invalid type"
            )
        score = float(value)
        if not 0.0 <= score <= 1.0:
            raise MemoryAnalyzerExecutionError(
                f"analyzer `{analyzer_name}` field `{field_name}` must be between 0 and 1"
            )
        return score


def _build_dialogue_lines(recent_turns: list[dict[str, str]]) -> list[str]:
    lines: list[str] = []
    for turn in recent_turns:
        if turn.get("user"):
            lines.append(f"User: {turn['user']}")
        if turn.get("assistant"):
            lines.append(f"Assistant: {turn['assistant']}")
    return lines
