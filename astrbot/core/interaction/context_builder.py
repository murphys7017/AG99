from __future__ import annotations

from typing import Any

from astrbot import logger
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
    interaction_messages: list[dict[str, Any]] = []
    interaction_slot = pack.get_slot("memory.interaction")
    if interaction_slot is not None and isinstance(interaction_slot.value, dict):
        recent_turns = interaction_slot.value.get("recent_turns", [])
        if isinstance(recent_turns, list):
            limited_turns = recent_turns[:limit] if limit > 0 else recent_turns
            for turn in reversed(limited_turns):
                if not isinstance(turn, dict):
                    continue
                user_text = str(turn.get("user", "") or "").strip()
                assistant_text = str(turn.get("assistant", "") or "").strip()
                if user_text or assistant_text:
                    interaction_messages.append(
                        {
                            "source": "interaction_memory",
                            "user_message": {
                                "role": "user",
                                "content": user_text,
                            },
                            "assistant_message": {
                                "role": "assistant",
                                "content": assistant_text,
                            },
                        }
                    )

    core_messages: list[dict[str, Any]] = []
    slot = pack.get_slot("conversation.history")
    if slot is None or not isinstance(slot.value, dict):
        return interaction_messages[-limit:] if limit > 0 else interaction_messages
    turns = slot.value.get("turns", [])
    if not isinstance(turns, list):
        return interaction_messages[-limit:] if limit > 0 else interaction_messages
    limited_core_turns = turns[-limit:] if limit > 0 else turns
    for turn in limited_core_turns:
        if isinstance(turn, dict):
            turn = dict(turn)
            turn.setdefault("source", "core_conversation")
            core_messages.append(turn)
    messages = _dedupe_recent_messages([*core_messages, *interaction_messages])
    return messages[-limit:] if limit > 0 else messages


def _dedupe_recent_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped_reversed: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        user_text = _extract_turn_text(message, "user_message")
        assistant_text = _extract_turn_text(message, "assistant_message")
        key = (user_text, assistant_text)
        if key in seen:
            continue
        seen.add(key)
        deduped_reversed.append(message)
    return list(reversed(deduped_reversed))


def _extract_turn_text(turn: dict[str, Any], key: str) -> str:
    payload = turn.get(key, {})
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("content", "") or "").strip()


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
        try:
            payload = await contributor.collect(
                event,
                plugin_context,
                config,
                decision_context,
            )
        except Exception as exc:  # noqa: BLE001
            failures = event.get_extra("_interaction_prompt_contributor_failures", [])
            if not isinstance(failures, list):
                failures = []
            failures.append(
                {
                    "plugin_id": getattr(contributor, "plugin_id", "<unknown>"),
                    "error": str(exc),
                }
            )
            event.set_extra("_interaction_prompt_contributor_failures", failures)
            logger.warning(
                "Interaction prompt contributor failed: plugin_id=%s error=%s",
                getattr(contributor, "plugin_id", "<unknown>"),
                exc,
                exc_info=True,
            )
            continue
        if isinstance(payload, InteractionPromptContribution):
            contributions.append(payload)
    contributions.sort(key=lambda item: (item.priority, item.plugin_id))
    return contributions
