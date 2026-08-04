"""Prompt target projections over one canonical context pack."""

from __future__ import annotations

from copy import deepcopy
from enum import Enum

from .context_types import ContextPack, ContextSlot
from .target_budget import (
    PromptTargetBudget,
    apply_target_budget,
    resolve_target_budget,
)


class PromptTarget(str, Enum):
    """A model-facing role that consumes prompt context."""

    ROUTER = "router"
    CORE_PLANNER = "core_planner"
    PERSONAL_POLICY = "personal_policy"
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
        "memory.topic_state",
        "memory.short_term",
        "capability.plugin_directory",
        "extension.context",
    }
)

_CORE_BLOCKED_SLOT_NAMES = frozenset(
    {
        "memory.persona_state",
        "input.visible_reply_material",
        "input.attachment_summary",
        "capability.plugin_directory",
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
        "memory.topic_state",
        "memory.short_term",
        "capability.plugin_directory",
        "extension.context",
    }
)

_CORE_ONLY_SLOT_NAMES = frozenset({"system.core_execution_context"})

_PERSONA_BLOCKED_SLOT_NAMES = frozenset(
    {
        "extension.capability",
        "input.attachment_summary",
        *_CORE_ONLY_SLOT_NAMES,
    }
)

_PERSONAL_POLICY_SLOT_NAMES = frozenset(
    {
        "system.base",
        "persona.summary",
        "session.datetime",
        "session.user_info",
        "conversation.history",
        "memory.topic_state",
        "memory.short_term",
        "memory.persona_state",
        "runtime.personal_state",
        "runtime.observation_batch",
        "runtime.observation_features",
    }
)


def project_context_pack(
    pack: ContextPack,
    target: PromptTarget | str,
    *,
    router_history_turns: int = 4,
    history_turns: int | None = None,
    config: object | None = None,
) -> ContextPack:
    """Build an isolated target view without mutating the canonical pack."""

    resolved_target = PromptTarget(target)
    budget = resolve_target_budget(
        resolved_target.value,
        router_history_turns=router_history_turns,
        history_turns=history_turns,
        config=config,
    )
    projected = ContextPack(
        provider_request_ref=pack.provider_request_ref,
        meta=deepcopy(pack.meta),
    )
    source_slots: dict[str, ContextSlot] = {}

    for slot in pack.slots.values():
        if not _slot_is_visible(slot, resolved_target):
            continue
        source_slots[slot.name] = slot
        projected_slot = _project_slot(slot, resolved_target)
        if projected_slot is not None:
            projected.add_slot(projected_slot)

    apply_target_budget(
        source_slots=source_slots,
        projected=projected,
        target=resolved_target.value,
        budget=budget,
    )

    projected.meta["prompt_target"] = resolved_target.value
    projected.meta["source_slot_names"] = sorted(pack.slots)
    projected.meta["selected_slot_names"] = sorted(projected.slots)
    projected.meta["slot_count"] = len(projected.slots)
    return projected


def filter_llm_exposed_context_pack(
    pack: ContextPack,
    *,
    config: object | None = None,
) -> ContextPack:
    """Return a Core-budgeted compatibility view of all LLM-exposed slots."""

    filtered = ContextPack(
        provider_request_ref=pack.provider_request_ref,
        meta=deepcopy(pack.meta),
    )
    for slot in pack.slots.values():
        if slot.llm_exposure != "never":
            filtered.add_slot(deepcopy(slot))
    budget = resolve_target_budget(PromptTarget.CORE.value, config=config)
    apply_target_budget(
        source_slots={
            name: slot
            for name, slot in pack.slots.items()
            if slot.llm_exposure != "never"
        },
        projected=filtered,
        target=PromptTarget.CORE.value,
        budget=budget,
    )
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

    if target is PromptTarget.PERSONAL_POLICY:
        return slot.name in _PERSONAL_POLICY_SLOT_NAMES

    group = slot.name.split(".", 1)[0]
    if target is PromptTarget.PERSONA:
        if slot.name in _PERSONA_BLOCKED_SLOT_NAMES:
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


__all__ = [
    "PromptTarget",
    "PromptTargetBudget",
    "filter_llm_exposed_context_pack",
    "project_context_pack",
]
