from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import OperationalError

from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import CoreExecutionRecord
from astrbot.core.prompt.context_types import ContextPack
from astrbot.core.prompt.render.interfaces import RenderResult
from astrbot.core.prompt.render.request_adapter import (
    PromptApplyResult,
    ProviderRequestAdapter,
)
from astrbot.core.provider.entities import ProviderRequest

CORE_EXECUTION_REQUEST_EXTRA_KEY = "_core_execution_request"


@dataclass(frozen=True, slots=True)
class CoreCapabilitySnapshot:
    """Framework-owned capabilities exposed to an executor."""

    tools: Any = None
    tool_schema: Any = None
    skills: Any = None
    knowledge: Any = None
    subagents: Any = None

    @classmethod
    def from_context_pack(
        cls,
        context_pack: ContextPack,
        *,
        tools: Any = None,
    ) -> CoreCapabilitySnapshot:
        return cls(
            tools=tools,
            tool_schema=_slot_value(context_pack, "capability.tools_schema"),
            skills=_slot_value(context_pack, "capability.skills_prompt"),
            knowledge=_slot_value(context_pack, "knowledge.snippets"),
            subagents={
                "handoff_tools": _slot_value(
                    context_pack, "capability.subagent_handoff_tools"
                ),
                "router_prompt": _slot_value(
                    context_pack, "capability.subagent_router_prompt"
                ),
            },
        )


@dataclass(frozen=True, slots=True)
class CoreExecutionRequest:
    """Provider-neutral Core input prepared before backend adaptation."""

    execution_id: str
    core_task_id: str
    turn_id: str
    context_pack: ContextPack
    rendered_prompt: RenderResult
    task_spec: dict[str, Any] | None = None
    execution_history: tuple[dict[str, Any], ...] = ()
    capabilities: CoreCapabilitySnapshot = field(default_factory=CoreCapabilitySnapshot)
    parent_execution_id: str | None = None
    attempt: int = 1

    @classmethod
    def from_context_pack(
        cls,
        *,
        context_pack: ContextPack,
        rendered_prompt: RenderResult,
        turn_id: str,
        task_spec: dict[str, Any] | None = None,
        parent_execution_id: str | None = None,
        capabilities: CoreCapabilitySnapshot | None = None,
    ) -> CoreExecutionRequest:
        execution_id = uuid4().hex
        resolved_turn_id = turn_id.strip() or execution_id
        task_metadata = task_spec.get("metadata") if isinstance(task_spec, dict) else None
        configured_task_id = (
            task_metadata.get("core_task_id")
            if isinstance(task_metadata, dict)
            else None
        )
        core_task_id = str(configured_task_id or f"core:{resolved_turn_id}")
        history_slot = context_pack.get_slot("conversation.core_execution_history")
        history_value = history_slot.value if history_slot is not None else None
        records = history_value.get("records", []) if isinstance(history_value, dict) else []
        neutral_pack = ContextPack(
            slots=dict(context_pack.slots),
            provider_request_ref=None,
            meta=dict(context_pack.meta),
        )
        return cls(
            execution_id=execution_id,
            core_task_id=core_task_id,
            turn_id=resolved_turn_id,
            context_pack=neutral_pack,
            rendered_prompt=rendered_prompt,
            task_spec=dict(task_spec) if isinstance(task_spec, dict) else None,
            execution_history=tuple(
                dict(item) for item in records if isinstance(item, dict)
            ),
            capabilities=capabilities or CoreCapabilitySnapshot(),
            parent_execution_id=parent_execution_id,
        )


@dataclass(frozen=True, slots=True)
class NativeExecutionInput:
    provider_request: ProviderRequest
    prompt_apply_result: PromptApplyResult


class NativeExecutionAdapter:
    """Adapt a neutral execution request to AstrBot's native provider contract."""

    def __init__(self) -> None:
        self._request_adapter = ProviderRequestAdapter()

    def adapt(
        self,
        request: CoreExecutionRequest,
        provider_request: ProviderRequest,
    ) -> NativeExecutionInput:
        apply_result = self._request_adapter.apply_render_result(
            request.rendered_prompt,
            provider_request,
        )
        provider_request.func_tool = request.capabilities.tools
        return NativeExecutionInput(
            provider_request=provider_request,
            prompt_apply_result=apply_result,
        )


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


def _slot_value(pack: ContextPack, name: str) -> Any:
    slot = pack.get_slot(name)
    return slot.value if slot is not None else None


__all__ = [
    "CORE_EXECUTION_REQUEST_EXTRA_KEY",
    "CoreCapabilitySnapshot",
    "CoreExecutionLedger",
    "CoreExecutionRequest",
    "NativeExecutionAdapter",
    "NativeExecutionInput",
]
