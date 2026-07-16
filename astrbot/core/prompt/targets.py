"""Prompt target projections over one canonical context pack."""

from __future__ import annotations

from copy import deepcopy
from enum import Enum
from typing import Any

from .context_types import ContextPack, ContextSlot


class PromptTarget(str, Enum):
    """A model-facing role that consumes prompt context."""

    ROUTER = "router"
    CORE_PLANNER = "core_planner"
    PERSONA = "persona"
    CORE = "core"


_ROUTER_SLOT_NAMES = frozenset(
    {
        "system.base",
        "persona.summary",
        "input.text",
        "input.quoted_text",
        "input.attachment_summary",
        "session.datetime",
        "session.user_info",
        "conversation.history",
        "conversation.group_recent",
        "memory.interaction",
        "capability.plugin_directory",
        "extension.context",
    }
)

_CORE_BLOCKED_SLOT_NAMES = frozenset(
    {
        "memory.interaction",
        "memory.persona_state",
        "input.visible_reply_material",
        "input.attachment_summary",
        "capability.plugin_directory",
        "capability.core_summary",
    }
)

_CORE_PLANNER_SLOT_NAMES = frozenset(
    {
        "system.base",
        "input.text",
        "input.quoted_text",
        "input.attachment_summary",
        "session.datetime",
        "session.user_info",
        "conversation.history",
        "conversation.group_recent",
        "memory.interaction",
        "capability.plugin_directory",
        "capability.core_summary",
        "extension.context",
    }
)

_CORE_ONLY_SLOT_NAMES = frozenset({"system.core_execution_context"})


def project_context_pack(
    pack: ContextPack,
    target: PromptTarget | str,
    *,
    router_history_turns: int = 4,
) -> ContextPack:
    """Build an isolated target view without mutating the canonical pack."""

    resolved_target = PromptTarget(target)
    projected = ContextPack(
        provider_request_ref=pack.provider_request_ref,
        meta=deepcopy(pack.meta),
    )

    for slot in pack.slots.values():
        if not _slot_is_visible(slot, resolved_target):
            continue
        projected_slot = _project_slot(
            slot,
            resolved_target,
            router_history_turns=router_history_turns,
        )
        if projected_slot is not None:
            projected.add_slot(projected_slot)

    projected.meta["prompt_target"] = resolved_target.value
    projected.meta["source_slot_names"] = sorted(pack.slots)
    projected.meta["selected_slot_names"] = sorted(projected.slots)
    projected.meta["slot_count"] = len(projected.slots)
    return projected


def filter_llm_exposed_context_pack(pack: ContextPack) -> ContextPack:
    """Return an isolated pack containing only slots eligible for LLM rendering."""

    filtered = ContextPack(
        provider_request_ref=pack.provider_request_ref,
        meta=deepcopy(pack.meta),
    )
    for slot in pack.slots.values():
        if slot.llm_exposure != "never":
            filtered.add_slot(deepcopy(slot))
    filtered.meta["source_slot_names"] = sorted(pack.slots)
    filtered.meta["selected_slot_names"] = sorted(filtered.slots)
    filtered.meta["slot_count"] = len(filtered.slots)
    return filtered


def _slot_is_visible(slot: ContextSlot, target: PromptTarget) -> bool:
    if slot.llm_exposure == "never":
        return False

    raw_targets = slot.meta.get("targets")
    if raw_targets is not None:
        if not isinstance(raw_targets, list | tuple | set):
            return False
        targets = {str(value) for value in raw_targets}
        if target.value not in targets:
            return False

    if target is PromptTarget.ROUTER:
        return slot.name in _ROUTER_SLOT_NAMES

    if target is PromptTarget.CORE_PLANNER:
        return slot.name in _CORE_PLANNER_SLOT_NAMES

    group = slot.name.split(".", 1)[0]
    if target is PromptTarget.PERSONA:
        if (
            slot.name == "input.attachment_summary"
            or slot.name in _CORE_ONLY_SLOT_NAMES
        ):
            return False
        if group == "conversation":
            return slot.name in {
                "conversation.history",
                "conversation.group_recent",
            }
        return group not in {"capability", "knowledge", "policy"}

    if group == "persona" or slot.name in _CORE_BLOCKED_SLOT_NAMES:
        return False
    return True


def _project_slot(
    slot: ContextSlot,
    target: PromptTarget,
    *,
    router_history_turns: int,
) -> ContextSlot | None:
    projected = deepcopy(slot)
    if projected.name == "capability.plugin_directory":
        projected = _project_plugin_directory(projected, target)
        if projected is None:
            return None
    if projected.name.startswith("extension."):
        projected = _project_extension_slot(projected, target)
        if projected is None:
            return None

    if target not in {PromptTarget.ROUTER, PromptTarget.CORE_PLANNER}:
        return projected

    if projected.name == "conversation.history":
        history_turns = (
            router_history_turns
            if target is PromptTarget.ROUTER
            else max(router_history_turns, 8)
        )
        _project_history(
            projected,
            history_turns,
            max_message_chars=1000
            if target is PromptTarget.ROUTER
            else 1800,
        )
    elif projected.name == "conversation.group_recent":
        _project_group_recent(
            projected,
            max_records=8 if target is PromptTarget.ROUTER else 12,
            max_record_chars=800
            if target is PromptTarget.ROUTER
            else 1200,
        )
    elif projected.name == "memory.interaction":
        memory_turns = (
            router_history_turns
            if target is PromptTarget.ROUTER
            else max(router_history_turns, 8)
        )
        _summarize_interaction_memory(projected, memory_turns)
    return projected


def _project_plugin_directory(
    slot: ContextSlot,
    target: PromptTarget,
) -> ContextSlot | None:
    if not isinstance(slot.value, dict):
        return None
    plugins = slot.value.get("plugins")
    if not isinstance(plugins, list):
        return None
    slot_targets = slot.meta.get("targets")
    inherited_targets = (
        {str(value) for value in slot_targets}
        if isinstance(slot_targets, list | tuple | set)
        else set()
    )
    selected = []
    for plugin in plugins:
        if not isinstance(plugin, dict):
            continue
        raw_targets = plugin.get("targets")
        targets = (
            {str(value) for value in raw_targets}
            if isinstance(raw_targets, list | tuple | set)
            else inherited_targets
        )
        if target.value not in targets:
            continue
        selected.append(
            {
                "name": plugin.get("name"),
                "description": plugin.get("description"),
            }
        )
    if not selected:
        return None
    slot.value["plugins"] = selected
    slot.meta["plugin_count"] = len(selected)
    return slot


def _project_extension_slot(
    slot: ContextSlot,
    target: PromptTarget,
) -> ContextSlot | None:
    if not isinstance(slot.value, dict):
        return None
    items = slot.value.get("items")
    if not isinstance(items, list):
        return None
    allowed_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        meta = item.get("meta")
        raw_targets = meta.get("targets") if isinstance(meta, dict) else None
        targets = (
            {str(value) for value in raw_targets}
            if isinstance(raw_targets, list | tuple | set)
            else {PromptTarget.CORE.value}
        )
        if target.value in targets:
            allowed_items.append(item)
    if not allowed_items:
        return None
    slot.value["items"] = allowed_items
    slot.meta["item_count"] = len(allowed_items)
    return slot


def _project_history(
    slot: ContextSlot,
    limit: int,
    *,
    max_message_chars: int,
) -> None:
    if not isinstance(slot.value, dict):
        return
    turns = slot.value.get("turns")
    if not isinstance(turns, list):
        return
    safe_limit = max(0, limit)
    selected_turns = deepcopy(turns[-safe_limit:] if safe_limit else [])
    for turn in selected_turns:
        if not isinstance(turn, dict):
            continue
        for key in ("user_message", "assistant_message"):
            message = turn.get(key)
            if not isinstance(message, dict):
                continue
            message["content"] = _sanitize_context_content(
                message.get("content"),
                max_chars=max_message_chars,
            )
            message.pop("tool_calls", None)
            message.pop("reasoning_content", None)
            message.pop("thinking", None)
    slot.value["turns"] = selected_turns
    slot.value["turn_count"] = len(selected_turns)
    slot.meta["target_truncated"] = len(selected_turns) != len(turns)
    slot.meta["turn_count"] = len(selected_turns)


def _project_group_recent(
    slot: ContextSlot,
    *,
    max_records: int,
    max_record_chars: int,
) -> None:
    if not isinstance(slot.value, dict):
        return
    records = slot.value.get("records")
    if not isinstance(records, list):
        return
    selected = records[-max(0, max_records) :]
    safe_records = [
        _sanitize_context_text(str(record), max_chars=max_record_chars)
        for record in selected
    ]
    slot.value["records"] = safe_records
    slot.value["text"] = (
        "Recent group messages; sender identities remain distinct:\n"
        + "\n".join(safe_records)
    )
    slot.meta["target_truncated"] = len(selected) != len(records)
    slot.meta["record_count"] = len(safe_records)


def _summarize_interaction_memory(slot: ContextSlot, limit: int) -> None:
    if not isinstance(slot.value, dict):
        return
    safe_limit = max(0, limit)
    recent_turns = slot.value.get("recent_turns")
    if isinstance(recent_turns, list):
        recent_turns = deepcopy(recent_turns[:safe_limit] if safe_limit else [])
        for turn in recent_turns:
            if not isinstance(turn, dict):
                continue
            for key in ("user", "assistant"):
                if key in turn:
                    turn[key] = _sanitize_context_text(
                        str(turn.get(key, "") or ""),
                        max_chars=800,
                    )
    else:
        recent_turns = []
    slot.value = {
        key: value
        for key, value in {
            "recent_turns": recent_turns,
            "recent_topics": slot.value.get("recent_topics", []),
            "ongoing_threads": slot.value.get("ongoing_threads", []),
            "last_impression_summary": slot.value.get("last_impression_summary", ""),
        }.items()
        if value not in (None, "", [])
    }
    slot.meta["target_summary"] = "compact"


def _sanitize_context_content(value: Any, *, max_chars: int) -> str:
    if isinstance(value, str):
        return _sanitize_context_text(value, max_chars=max_chars)
    if not isinstance(value, list):
        return _sanitize_context_text(str(value or ""), max_chars=max_chars)
    text_parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            text_parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                text_parts.append(text)
    return _sanitize_context_text("\n".join(text_parts), max_chars=max_chars)


def _sanitize_context_text(value: str, *, max_chars: int) -> str:
    text = value.strip()
    lowered = text.lower()
    diagnostic_markers = (
        "traceback (most recent call last)",
        "[erro]",
        "error code:",
        "no such file or directory",
        "invalid image input",
        "获取图片描述失败",
    )
    if any(marker in lowered for marker in diagnostic_markers):
        return "[runtime diagnostic omitted]"
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


__all__ = ["PromptTarget", "project_context_pack"]
