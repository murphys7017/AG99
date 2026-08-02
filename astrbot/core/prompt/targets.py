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
            history_turns=history_turns,
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
    *,
    router_history_turns: int,
    history_turns: int | None,
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

    if projected.name == "conversation.group_recent":
        _project_group_recent(
            projected,
            max_records=(
                8
                if target in {PromptTarget.ROUTER, PromptTarget.PERSONAL_POLICY}
                else 12
            ),
            max_record_chars=(
                800
                if target in {PromptTarget.ROUTER, PromptTarget.PERSONAL_POLICY}
                else 1200
            ),
        )
        return projected

    bounded_history_targets = {
        PromptTarget.ROUTER,
        PromptTarget.CORE_PLANNER,
        PromptTarget.PERSONAL_POLICY,
    }
    if target not in bounded_history_targets and history_turns is None:
        return projected

    if projected.name == "conversation.history":
        if history_turns is not None:
            selected_history_turns = history_turns
            max_message_chars = 1800
        elif target is PromptTarget.ROUTER:
            selected_history_turns = router_history_turns
            max_message_chars = 1000
        elif target is PromptTarget.PERSONAL_POLICY:
            selected_history_turns = max(router_history_turns, 6)
            max_message_chars = 1200
        else:
            selected_history_turns = max(router_history_turns, 8)
            max_message_chars = 1800
        _project_history(
            projected,
            selected_history_turns,
            max_message_chars=max_message_chars,
        )
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
        _sanitize_group_record(record, max_content_chars=max_record_chars)
        for record in selected
    ]
    slot.value["records"] = safe_records
    slot.value["text"] = (
        "Recent group messages; sender identities remain distinct:\n"
        + "\n".join(_format_projected_group_record(record) for record in safe_records)
    )
    slot.meta["target_truncated"] = len(selected) != len(records)
    slot.meta["record_count"] = len(safe_records)


def _sanitize_group_record(
    value: Any,
    *,
    max_content_chars: int,
) -> dict[str, Any]:
    """Keep the renderer's structured group-record contract intact.

    Earlier collectors supplied plain strings.  Keep those records usable by
    lifting them into the current shape instead of allowing a target projection
    to turn structured records into opaque strings.
    """
    if not isinstance(value, dict):
        return {
            "content": _sanitize_context_text(
                str(value or ""),
                max_chars=max_content_chars,
            )
        }

    record: dict[str, Any] = {}
    for key, max_chars in (
        ("id", 128),
        ("sender", 256),
        ("user_id", 128),
        ("time", 128),
    ):
        raw_value = value.get(key)
        if raw_value is None:
            continue
        record[key] = _sanitize_context_text(str(raw_value), max_chars=max_chars)

    sequence = value.get("sequence")
    if isinstance(sequence, int) and not isinstance(sequence, bool):
        record["sequence"] = sequence
    record["content"] = _sanitize_context_content(
        value.get("content"),
        max_chars=max_content_chars,
    )
    return record


def _format_projected_group_record(record: dict[str, Any]) -> str:
    sender = str(record.get("sender") or "Unknown")
    user_id = record.get("user_id")
    if user_id:
        sender += f" (user_id={user_id})"
    occurred_at = str(record.get("time") or "unknown-time")
    return f"[{sender}/{occurred_at}]: {record.get('content', '')}"


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
