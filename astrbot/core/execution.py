from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from astrbot.core.prompt.context_types import ContextPack
from astrbot.core.prompt.render.interfaces import RenderResult
from astrbot.core.prompt.render.request_adapter import (
    PromptApplyResult,
    ProviderRequestAdapter,
)
from astrbot.core.provider.entities import ProviderRequest

CORE_EXECUTION_SPEC_EXTRA_KEY = "_core_execution_spec"


@dataclass(frozen=True, slots=True)
class CoreCapabilitySnapshot:
    """Framework-owned capabilities exposed to an executor."""

    tools: Any = None
    tool_schema: Any = None
    skills: Any = None
    knowledge: Any = None

    def snapshot(self) -> CoreCapabilitySnapshot:
        """Copy serializable capability facts while retaining the live ToolSet handle."""
        return type(self)(
            tools=self.tools,
            tool_schema=deepcopy(self.tool_schema),
            skills=deepcopy(self.skills),
            knowledge=deepcopy(self.knowledge),
        )

    @classmethod
    def from_context_pack(
        cls,
        context_pack: ContextPack,
        *,
        tools: Any = None,
    ) -> CoreCapabilitySnapshot:
        return cls(
            tools=tools,
            tool_schema=deepcopy(
                _slot_value(context_pack, "capability.tools_schema")
            ),
            skills=deepcopy(_slot_value(context_pack, "capability.skills_prompt")),
            knowledge=deepcopy(_slot_value(context_pack, "knowledge.snippets")),
        )


@dataclass(frozen=True, slots=True)
class CoreExecutionSpec:
    """Provider-neutral Core facts prepared before backend-specific rendering."""

    execution_id: str
    core_task_id: str
    turn_id: str
    context_pack: ContextPack
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
        turn_id: str,
        task_spec: dict[str, Any] | None = None,
        parent_execution_id: str | None = None,
        capabilities: CoreCapabilitySnapshot | None = None,
    ) -> CoreExecutionSpec:
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
            slots=deepcopy(context_pack.slots),
            provider_request_ref=None,
            meta=deepcopy(context_pack.meta),
        )
        return cls(
            execution_id=execution_id,
            core_task_id=core_task_id,
            turn_id=resolved_turn_id,
            context_pack=neutral_pack,
            task_spec=deepcopy(task_spec) if isinstance(task_spec, dict) else None,
            execution_history=tuple(
                deepcopy(item) for item in records if isinstance(item, dict)
            ),
            capabilities=(
                capabilities.snapshot()
                if capabilities is not None
                else CoreCapabilitySnapshot()
            ),
            parent_execution_id=parent_execution_id,
        )


@dataclass(frozen=True, slots=True)
class NativeExecutionInput:
    provider_request: ProviderRequest
    prompt_apply_result: PromptApplyResult


class NativeExecutionAdapter:
    """Apply a Native-rendered prompt and capabilities to AstrBot's request."""

    def __init__(self) -> None:
        self._request_adapter = ProviderRequestAdapter()

    def adapt(
        self,
        spec: CoreExecutionSpec,
        rendered_prompt: RenderResult,
        provider_request: ProviderRequest,
    ) -> NativeExecutionInput:
        apply_result = self._request_adapter.apply_render_result(
            rendered_prompt,
            provider_request,
        )
        provider_request.func_tool = spec.capabilities.tools
        return NativeExecutionInput(
            provider_request=provider_request,
            prompt_apply_result=apply_result,
        )


def _slot_value(pack: ContextPack, name: str) -> Any:
    slot = pack.get_slot(name)
    return slot.value if slot is not None else None


__all__ = [
    "CORE_EXECUTION_SPEC_EXTRA_KEY",
    "CoreCapabilitySnapshot",
    "CoreExecutionSpec",
    "NativeExecutionAdapter",
    "NativeExecutionInput",
]
