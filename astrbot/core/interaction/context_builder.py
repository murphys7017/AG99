from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from astrbot import logger
from astrbot.core.prompt.collectors.input_collector import InputCollector
from astrbot.core.prompt.collectors.persona_collector import PersonaCollector
from astrbot.core.prompt.context_collect import (
    build_prompt_extension_slots,
    collect_context_pack,
)
from astrbot.core.prompt.context_types import ContextPack
from astrbot.core.prompt.extensions import PromptExtension
from astrbot.core.prompt.interfaces.context_collector_inferface import (
    ContextCollectorInterface,
)
from astrbot.core.star.context import Context

from .collectors import (
    InteractionConversationHistoryCollector,
    InteractionMemoryCollector,
)
from .contributors import InteractionDecisionView
from .memory_store import InteractionMemoryStore
from .turn_state import get_interaction_turn_state


class InteractionPromptContributorError(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def build_interaction_collectors(
    memory_store: InteractionMemoryStore,
) -> list[ContextCollectorInterface]:
    return [
        PersonaCollector(),
        InputCollector(),
        InteractionConversationHistoryCollector(),
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
        include_prompt_extensions=False,
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
    return interaction_messages[-limit:] if limit > 0 else interaction_messages


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


async def collect_interaction_prompt_extensions(
    event,
    plugin_context: Context,
    config,
    decision_context: dict[str, Any],
) -> list[PromptExtension]:
    extensions: list[PromptExtension] = []
    view = _build_decision_view(
        event=event,
        config=config,
        decision_context=decision_context,
    ).copy_read_only()
    for contributor in plugin_context.list_interaction_prompt_contributors():
        plugin_id = str(getattr(contributor, "plugin_id", "<unknown>") or "<unknown>")
        try:
            payload = await contributor.collect(event, plugin_context, view)
        except Exception as exc:  # noqa: BLE001
            _record_interaction_prompt_contributor_failure(
                event,
                plugin_id=plugin_id,
                error=str(exc),
            )
            raise InteractionPromptContributorError(
                "collector_failed",
                f"Interaction prompt contributor failed: plugin_id={plugin_id} error={exc}",
            ) from exc

        try:
            contributor_extensions = _normalize_interaction_prompt_extensions(payload)
            build_prompt_extension_slots(
                contributor_extensions,
                source="interaction_prompt_contributors",
            )
            extensions.extend(contributor_extensions)
        except (InteractionPromptContributorError, ValueError) as exc:
            _record_interaction_prompt_contributor_failure(
                event,
                plugin_id=plugin_id,
                error=str(exc),
            )
            raise InteractionPromptContributorError(
                "invalid_payload", str(exc)
            ) from exc

    extensions.sort(key=lambda item: (item.order, item.plugin_id))
    return extensions


def append_interaction_prompt_extensions_to_pack(
    pack: ContextPack,
    extensions: list[PromptExtension],
) -> None:
    if not extensions:
        return
    slots = build_prompt_extension_slots(
        extensions,
        source="interaction_prompt_contributors",
    )
    for slot in slots:
        _merge_or_add_extension_slot(pack, slot)
    pack.meta["interaction_prompt_extension_count"] = len(extensions)
    pack.meta["slot_count"] = len(pack.slots)


def _merge_or_add_extension_slot(pack: ContextPack, slot) -> None:
    existing = pack.get_slot(slot.name)
    if (
        existing is None
        or not isinstance(existing.value, dict)
        or not isinstance(slot.value, dict)
    ):
        pack.add_slot(slot)
        return
    existing_items = existing.value.get("items")
    incoming_items = slot.value.get("items")
    if not isinstance(existing_items, list) or not isinstance(incoming_items, list):
        pack.add_slot(slot)
        return
    existing_items.extend(incoming_items)
    existing_items.sort(
        key=lambda item: (
            int(item.get("order", 100) or 100) if isinstance(item, dict) else 100,
            str(item.get("plugin_id", "")) if isinstance(item, dict) else "",
        )
    )
    existing.meta["item_count"] = len(existing_items)
    existing.meta["plugin_count"] = len(
        {
            item.get("plugin_id")
            for item in existing_items
            if isinstance(item, dict) and isinstance(item.get("plugin_id"), str)
        }
    )


def _normalize_interaction_prompt_extensions(payload: object) -> list[PromptExtension]:
    if payload is None:
        return []
    if isinstance(payload, PromptExtension):
        return [payload]
    if isinstance(payload, Iterable) and not isinstance(payload, str | bytes | dict):
        items = list(payload)
        if all(isinstance(item, PromptExtension) for item in items):
            return items
    raise InteractionPromptContributorError(
        "invalid_payload",
        "interaction prompt contributor must return PromptExtension, list[PromptExtension], or None",
    )


def _record_interaction_prompt_contributor_failure(
    event,
    *,
    plugin_id: str,
    error: str,
) -> None:
    failures = event.get_extra("_interaction_prompt_contributor_failures", [])
    if not isinstance(failures, list):
        failures = []
    failures.append({"plugin_id": plugin_id, "error": error})
    event.set_extra("_interaction_prompt_contributor_failures", failures)
    logger.error(
        "Interaction prompt contributor failed: plugin_id=%s error=%s",
        plugin_id,
        error,
    )


def _build_decision_view(
    *,
    event,
    config,
    decision_context: dict[str, Any],
) -> InteractionDecisionView:
    turn_state = get_interaction_turn_state(event)
    material = turn_state.context_material if turn_state is not None else None
    platform_id = (
        event.get_platform_id()
        if callable(getattr(event, "get_platform_id", None))
        else ""
    )
    session_id = str(
        getattr(event, "unified_msg_origin", None)
        or getattr(event, "session_id", "")
        or ""
    )
    context = decision_context if isinstance(decision_context, dict) else {}
    return InteractionDecisionView(
        turn_id=str(event.get_extra("_turn_id", "") or ""),
        platform_id=platform_id,
        session_id=session_id,
        config=config,
        decision_context=context,
        persona=(
            material.persona_payload
            if material is not None
            else dict(context.get("persona", {}) or {})
        ),
        input=(
            material.input_payload
            if material is not None
            else dict(context.get("input", {}) or {})
        ),
        interaction_memory=(
            material.memory_payload
            if material is not None
            else dict(context.get("memory", {}) or {})
        ),
        recent_messages=(
            material.recent_messages
            if material is not None
            else list(context.get("recent_messages", []) or [])
        ),
        capabilities=(
            material.capability_payload
            if material is not None
            else dict(context.get("core_capabilities", {}) or {})
        ),
        metadata={"prompt_context_cached": material is not None},
    )
