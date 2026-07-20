from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy.exc import OperationalError

from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import CoreExecutionRecord


class CoreExecutionLedger:
    """Own persistence and retrieval of Core executor attempts."""

    def __init__(self, db: BaseDatabase, *, retain_per_conversation: int = 32) -> None:
        self._db = db
        self._retain = max(1, int(retain_per_conversation))

    async def append(self, record: CoreExecutionRecord) -> bool:
        last_error: OperationalError | None = None
        for attempt in range(3):
            try:
                return await self._db.insert_core_execution_record(
                    record,
                    retain=self._retain,
                )
            except OperationalError as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.05 * (2**attempt))
        if last_error is not None:
            raise last_error
        return False

    async def recent(
        self,
        conversation_id: str,
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        records = await self._db.get_recent_core_execution_records(
            conversation_id,
            limit=limit,
        )
        return [_record_to_prompt_payload(record) for record in records]


def _record_to_prompt_payload(record: CoreExecutionRecord) -> dict[str, Any]:
    return {
        "execution_id": record.execution_id,
        "core_task_id": record.core_task_id,
        "turn_id": record.turn_id,
        "parent_execution_id": record.parent_execution_id,
        "attempt": record.attempt,
        "executor_id": record.executor_id,
        "status": record.status,
        "task_spec": record.task_spec,
        "tool_evidence": _summarize_execution_messages(record.messages or []),
        "result": _bounded_text(record.result, limit=4000),
        "error": _bounded_text(record.error, limit=2000),
    }


def _summarize_execution_messages(messages: list) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for message in messages[-8:]:
        if not isinstance(message, dict):
            continue
        item: dict[str, Any] = {"role": str(message.get("role", ""))}
        content = message.get("content")
        if content is not None:
            serialized = (
                content
                if isinstance(content, str)
                else json.dumps(content, ensure_ascii=False, default=str)
            )
            item["content"] = _bounded_text(serialized, limit=1200)
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            item["tool_calls"] = [
                _summarize_tool_call(call)
                for call in tool_calls[:8]
                if isinstance(call, dict)
            ]
        tool_call_id = message.get("tool_call_id")
        if tool_call_id:
            item["tool_call_id"] = str(tool_call_id)
        evidence.append(item)
    return evidence


def _summarize_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function")
    if not isinstance(function, dict):
        return {"id": call.get("id"), "type": call.get("type")}
    arguments = function.get("arguments")
    serialized_arguments = (
        arguments
        if isinstance(arguments, str)
        else json.dumps(arguments, ensure_ascii=False, default=str)
    )
    return {
        "id": call.get("id"),
        "name": function.get("name"),
        "arguments": _bounded_text(serialized_arguments, limit=1000),
    }


def _bounded_text(value: str | None, *, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text if len(text) <= limit else f"{text[:limit]}..."


__all__ = ["CoreExecutionLedger"]
