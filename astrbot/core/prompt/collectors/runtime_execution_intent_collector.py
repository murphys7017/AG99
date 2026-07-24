"""Collect a Policy-approved task as Planner-only runtime context."""

from __future__ import annotations

from astrbot.core.prompt.context_types import ContextSlot
from astrbot.core.prompt.interfaces.context_collector_inferface import (
    ContextCollectorInterface,
)


class RuntimeExecutionIntentCollector(ContextCollectorInterface):
    """Expose an internal Policy task without projecting it as user input."""

    async def collect(
        self,
        event,
        plugin_context,
        config,
        provider_request=None,
    ) -> list[ContextSlot]:
        del plugin_context, config, provider_request
        intent = event.get_extra("_personal_execution_intent")
        to_context = getattr(intent, "to_core_planner_context", None)
        if not callable(to_context):
            return []
        value = to_context()
        if not isinstance(value, dict):
            return []
        task_intent = str(value.get("task_intent", "") or "").strip()
        action_id = str(value.get("action_id", "") or "").strip()
        batch_id = str(value.get("batch_id", "") or "").strip()
        observation_batch = value.get("observation_batch")
        if (
            not task_intent
            or not action_id
            or not batch_id
            or not isinstance(observation_batch, dict)
        ):
            return []
        return [
            ContextSlot(
                name="runtime.execution_intent",
                value=value,
                category="runtime",
                source="personal_runtime_policy",
                render_mode="structured",
                meta={
                    "targets": ["core_planner"],
                    "scope": "ephemeral",
                },
            )
        ]


__all__ = ["RuntimeExecutionIntentCollector"]
