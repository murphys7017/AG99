from __future__ import annotations

from typing import Any

from astrbot.core.prompt.collectors.conversation_history_collector import (
    ConversationHistoryCollector,
)
from astrbot.core.prompt.collectors.input_collector import InputCollector
from astrbot.core.prompt.collectors.persona_collector import PersonaCollector
from astrbot.core.prompt.context_collect import collect_context_pack
from astrbot.core.prompt.context_types import ContextPack
from astrbot.core.prompt.interfaces.context_collector_inferface import (
    ContextCollectorInterface,
)
from astrbot.core.star.context import Context

from .collectors import InteractionMemoryCollector
from .contributors import InteractionPromptContribution
from .memory_store import InteractionMemoryStore


def build_interaction_collectors(
    memory_store: InteractionMemoryStore,
) -> list[ContextCollectorInterface]:
    return [
        PersonaCollector(),
        InputCollector(),
        ConversationHistoryCollector(),
        InteractionMemoryCollector(memory_store),
    ]


async def build_interaction_context_pack(
    event,
    plugin_context: Context,
    config,
    memory_store: InteractionMemoryStore,
) -> ContextPack:
    return await collect_context_pack(
        event=event,
        plugin_context=plugin_context,
        config=config,
        provider_request=event.get_extra("provider_request"),
        collectors=build_interaction_collectors(memory_store),
    )


def extract_recent_messages(
    pack: ContextPack,
    limit: int,
) -> list[dict[str, Any]]:
    slot = pack.get_slot("conversation.history")
    if slot is None or not isinstance(slot.value, dict):
        return []
    turns = slot.value.get("turns", [])
    if not isinstance(turns, list):
        return []
    return turns[-limit:] if limit > 0 else turns


def extract_persona_payload(pack: ContextPack) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for slot_name in ("persona.prompt", "persona.segments", "persona.begin_dialogs"):
        slot = pack.get_slot(slot_name)
        if slot is None:
            continue
        payload[slot_name.split(".", 1)[1]] = slot.value
    return payload


def extract_input_payload(pack: ContextPack) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for slot_name in (
        "input.text",
        "input.quoted_text",
        "input.images",
        "input.files",
        "input.image_captions",
    ):
        slot = pack.get_slot(slot_name)
        if slot is None:
            continue
        payload[slot_name.split(".", 1)[1]] = slot.value
    return payload


def extract_interaction_memory_payload(pack: ContextPack) -> dict[str, Any]:
    slot = pack.get_slot("memory.interaction")
    if slot is None or not isinstance(slot.value, dict):
        return {}
    return slot.value


def build_core_capability_payload(plugin_context: Context, event) -> dict[str, Any]:
    provider_tools = plugin_context.get_llm_tool_manager().func_list
    active_tool_names = sorted(
        {
            str(tool.name).strip()
            for tool in provider_tools
            if getattr(tool, "enabled", True) and str(getattr(tool, "name", "")).strip()
        }
    )
    return {
        "tools_available": bool(active_tool_names),
        "tool_count": len(active_tool_names),
        "sample_tools": active_tool_names[:12],
        "knowledge_base_available": bool(plugin_context.kb_manager),
        "subagent_available": plugin_context.subagent_orchestrator is not None,
        "platform_id": event.get_platform_id(),
    }


async def collect_interaction_prompt_contributions(
    event,
    plugin_context: Context,
    config,
    decision_context: dict[str, Any],
) -> list[InteractionPromptContribution]:
    contributions: list[InteractionPromptContribution] = []
    for contributor in plugin_context.list_interaction_prompt_contributors():
        payload = await contributor.collect(
            event,
            plugin_context,
            config,
            decision_context,
        )
        if isinstance(payload, InteractionPromptContribution):
            contributions.append(payload)
    contributions.sort(key=lambda item: (item.priority, item.plugin_id))
    return contributions
