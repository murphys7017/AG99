"""Prompt target projections over one canonical context pack."""

from __future__ import annotations

from copy import deepcopy
from enum import Enum

from .context_types import ContextPack, ContextSlot


class PromptTarget(str, Enum):
    """A model-facing role that consumes prompt context."""

    ROUTER = "router"
    PERSONA = "persona"
    CORE = "core"


_ROUTER_SLOT_NAMES = frozenset(
    {
        "system.base",
        "persona.summary",
        "input.text",
        "input.quoted_text",
        "input.router_attachment_summary",
        "session.datetime",
        "session.user_info",
        "conversation.history",
        "conversation.group_recent",
        "memory.interaction",
        "capability.router_plugin_directory",
        "extension.context",
    }
)

_CORE_BLOCKED_SLOT_NAMES = frozenset(
    {
        "memory.interaction",
        "memory.persona_state",
        "input.visible_reply_material",
        "input.router_attachment_summary",
        "capability.router_plugin_directory",
    }
)


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


def _slot_is_visible(slot: ContextSlot, target: PromptTarget) -> bool:
    if slot.llm_exposure == "never":
        return False

    if target is PromptTarget.ROUTER:
        return slot.name in _ROUTER_SLOT_NAMES

    group = slot.name.split(".", 1)[0]
    if target is PromptTarget.PERSONA:
        if slot.name == "input.router_attachment_summary":
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
    if projected.name.startswith("extension."):
        projected = _project_extension_slot(projected, target)
        if projected is None:
            return None

    if target is not PromptTarget.ROUTER:
        return projected

    if projected.name == "conversation.history":
        _truncate_history(projected, router_history_turns)
    elif projected.name == "memory.interaction":
        _summarize_interaction_memory(projected, router_history_turns)
    return projected


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


def _truncate_history(slot: ContextSlot, limit: int) -> None:
    if not isinstance(slot.value, dict):
        return
    turns = slot.value.get("turns")
    if not isinstance(turns, list):
        return
    safe_limit = max(0, limit)
    selected_turns = turns[-safe_limit:] if safe_limit else []
    slot.value["turns"] = selected_turns
    slot.value["turn_count"] = len(selected_turns)
    slot.meta["target_truncated"] = len(selected_turns) != len(turns)
    slot.meta["turn_count"] = len(selected_turns)


def _summarize_interaction_memory(slot: ContextSlot, limit: int) -> None:
    if not isinstance(slot.value, dict):
        return
    safe_limit = max(0, limit)
    recent_turns = slot.value.get("recent_turns")
    if isinstance(recent_turns, list):
        recent_turns = recent_turns[:safe_limit] if safe_limit else []
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
    slot.meta["target_summary"] = "router"


__all__ = ["PromptTarget", "project_context_pack"]
