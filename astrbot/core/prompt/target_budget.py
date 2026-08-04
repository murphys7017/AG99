"""Target-local prompt budgets applied to isolated context projections."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .context_types import ContextPack, ContextSlot


@dataclass(frozen=True, slots=True)
class PromptTargetBudget:
    """Bound model-facing context without changing canonical prompt facts."""

    history_turns: int
    history_limit_reason: str
    history_max_message_chars: int
    history_max_estimated_tokens: int
    group_recent_records: int
    group_recent_max_record_chars: int
    execution_history_records: int = 4
    execution_history_max_estimated_tokens: int = 6000
    memory_max_estimated_tokens: int = 10000


CORE_HISTORY_HARD_TURN_LIMIT = 64
_TARGET_MEMORY_TOKEN_LIMITS = {
    "router": 3000,
    "core_planner": 4000,
    "personal_policy": 4000,
    "persona": 10000,
    "core": 12000,
}


def resolve_target_budget(
    target: str,
    *,
    router_history_turns: int = 4,
    history_turns: int | None = None,
    config: object | None = None,
) -> PromptTargetBudget:
    """Resolve one explicit budget for a model-facing prompt target."""
    if target not in _TARGET_MEMORY_TOKEN_LIMITS:
        raise ValueError(f"unsupported prompt target budget: {target}")

    if history_turns is not None:
        selected_history_turns = max(0, int(history_turns))
        history_limit_reason = "render_profile_history_limit"
    elif target == "router":
        selected_history_turns = max(0, int(router_history_turns))
        history_limit_reason = "router_history_limit"
    elif target == "personal_policy":
        selected_history_turns = max(int(router_history_turns), 6)
        history_limit_reason = "personal_policy_history_limit"
    elif target == "core_planner":
        selected_history_turns = max(int(router_history_turns), 8)
        history_limit_reason = "core_planner_history_limit"
    elif target == "persona":
        selected_history_turns = 50
        history_limit_reason = "persona_history_limit"
    else:
        configured_limit = getattr(config, "max_context_length", -1)
        if isinstance(configured_limit, int) and configured_limit >= 0:
            selected_history_turns = configured_limit
            history_limit_reason = "configured_core_history_limit"
        else:
            selected_history_turns = CORE_HISTORY_HARD_TURN_LIMIT
            history_limit_reason = "core_history_hard_fallback"

    compact_target = target in {"router", "personal_policy"}
    history_token_limit = {
        "router": 2000,
        "personal_policy": 3000,
        "core_planner": 4000,
        "persona": 16000,
        "core": 16000,
    }[target]
    history_max_message_chars = 1800
    if target == "router":
        history_max_message_chars = 1000
    elif compact_target:
        history_max_message_chars = 1200

    return PromptTargetBudget(
        history_turns=selected_history_turns,
        history_limit_reason=history_limit_reason,
        history_max_message_chars=history_max_message_chars,
        history_max_estimated_tokens=history_token_limit,
        group_recent_records=8 if compact_target else 12,
        group_recent_max_record_chars=800 if compact_target else 1200,
        memory_max_estimated_tokens=_TARGET_MEMORY_TOKEN_LIMITS[target],
    )


def apply_target_budget(
    *,
    source_slots: dict[str, ContextSlot],
    projected: ContextPack,
    target: str,
    budget: PromptTargetBudget,
) -> None:
    """Apply one budget and attach diagnostics to the isolated projection."""
    history = projected.get_slot("conversation.history")
    if history is not None:
        _project_history(history, budget)

    group_recent = projected.get_slot("conversation.group_recent")
    if group_recent is not None:
        _project_group_recent(group_recent, budget)

    _apply_execution_history_budget(projected, budget)
    _apply_memory_budget(projected, budget)
    _attach_budget_diagnostics(source_slots, projected, target, budget)


def _project_history(slot: ContextSlot, budget: PromptTargetBudget) -> None:
    if not isinstance(slot.value, dict):
        return
    turns = slot.value.get("turns")
    if not isinstance(turns, list):
        return

    safe_limit = max(0, budget.history_turns)
    selected_turns = deepcopy(turns[-safe_limit:] if safe_limit else [])
    reasons: list[str] = []
    if len(selected_turns) != len(turns):
        reasons.append(budget.history_limit_reason)

    for turn in selected_turns:
        if not isinstance(turn, dict):
            continue
        for key in ("user_message", "assistant_message"):
            message = turn.get(key)
            if not isinstance(message, dict):
                continue
            original_content = message.get("content")
            full_content = _sanitize_context_content(
                original_content,
                max_chars=1_000_000,
            )
            message["content"] = _sanitize_context_content(
                original_content,
                max_chars=budget.history_max_message_chars,
            )
            if full_content == "[runtime diagnostic omitted]":
                reasons.append("runtime_diagnostic_omitted")
            elif len(full_content) > budget.history_max_message_chars:
                reasons.append("message_char_limit")
            message.pop("tool_calls", None)
            message.pop("reasoning_content", None)
            message.pop("thinking", None)

    while (
        len(selected_turns) > 1
        and _estimate_value_tokens(selected_turns)
        > budget.history_max_estimated_tokens
    ):
        selected_turns.pop(0)
        reasons.append("history_estimated_token_limit")
    if _estimate_value_tokens(selected_turns) > budget.history_max_estimated_tokens:
        reasons.append("history_budget_exceeded_minimum_turn")

    slot.value["turns"] = selected_turns
    slot.value["turn_count"] = len(selected_turns)
    slot.meta["target_truncated"] = bool(reasons)
    slot.meta["turn_count"] = len(selected_turns)
    slot.meta["budget_truncation_reasons"] = list(dict.fromkeys(reasons))


def _project_group_recent(
    slot: ContextSlot,
    budget: PromptTargetBudget,
) -> None:
    if not isinstance(slot.value, dict):
        return
    records = slot.value.get("records")
    if not isinstance(records, list):
        return
    selected = records[-max(0, budget.group_recent_records) :]
    safe_records = [
        _sanitize_group_record(
            record,
            max_content_chars=budget.group_recent_max_record_chars,
        )
        for record in selected
    ]
    slot.value["records"] = safe_records
    slot.value["text"] = (
        "Recent group messages; sender identities remain distinct:\n"
        + "\n".join(_format_projected_group_record(record) for record in safe_records)
    )
    slot.meta["target_truncated"] = len(selected) != len(records)
    slot.meta["record_count"] = len(safe_records)


def _apply_execution_history_budget(
    pack: ContextPack,
    budget: PromptTargetBudget,
) -> None:
    slot = pack.get_slot("conversation.core_execution_history")
    if slot is None or not isinstance(slot.value, dict):
        return
    records = slot.value.get("records")
    if not isinstance(records, list):
        return
    selected = deepcopy(records[-budget.execution_history_records :])
    reasons: list[str] = []
    if len(selected) != len(records):
        reasons.append("execution_record_limit")
    while (
        len(selected) > 1
        and _estimate_value_tokens(selected)
        > budget.execution_history_max_estimated_tokens
    ):
        selected.pop(0)
        reasons.append("execution_estimated_token_limit")
    if _estimate_value_tokens(selected) > budget.execution_history_max_estimated_tokens:
        reasons.append("execution_budget_exceeded_minimum_record")
    slot.value["records"] = selected
    slot.value["record_count"] = len(selected)
    slot.meta["record_count"] = len(selected)
    slot.meta["target_truncated"] = bool(reasons)
    slot.meta["budget_truncation_reasons"] = list(dict.fromkeys(reasons))


def _apply_memory_budget(pack: ContextPack, budget: PromptTargetBudget) -> None:
    memory_slots = [
        slot for slot in pack.slots.values() if slot.name.startswith("memory.")
    ]
    list_slots = [
        slot
        for slot in memory_slots
        if slot.name in {"memory.experiences", "memory.long_term_memories"}
        and isinstance(slot.value, dict)
        and isinstance(slot.value.get("items"), list)
    ]
    while (
        list_slots
        and _estimate_named_slots_tokens(memory_slots)
        > budget.memory_max_estimated_tokens
    ):
        changed = False
        for slot in reversed(list_slots):
            items = slot.value["items"]
            if not items:
                continue
            items.pop()
            slot.value["count"] = len(items)
            slot.meta["count"] = len(items)
            slot.meta["target_truncated"] = True
            slot.meta["budget_truncation_reasons"] = [
                "memory_estimated_token_limit"
            ]
            changed = True
            if (
                _estimate_named_slots_tokens(memory_slots)
                <= budget.memory_max_estimated_tokens
            ):
                break
        if not changed:
            break
    if (
        memory_slots
        and _estimate_named_slots_tokens(memory_slots)
        > budget.memory_max_estimated_tokens
    ):
        pack.meta["memory_budget_reasons"] = [
            "memory_budget_exceeded_fixed_facts"
        ]


def _attach_budget_diagnostics(
    source_slots: dict[str, ContextSlot],
    projected: ContextPack,
    target: str,
    budget: PromptTargetBudget,
) -> None:
    source_history = source_slots.get("conversation.history")
    projected_history = projected.get_slot("conversation.history")
    source_execution = source_slots.get("conversation.core_execution_history")
    projected_execution = projected.get_slot("conversation.core_execution_history")
    source_memory = [
        slot for slot in source_slots.values() if slot.name.startswith("memory.")
    ]
    projected_memory = [
        slot for slot in projected.slots.values() if slot.name.startswith("memory.")
    ]
    source_tools = source_slots.get("capability.tools_schema")
    projected_tools = projected.get_slot("capability.tools_schema")

    projected.meta["context_budgets"] = {
        "target": target,
        "conversation_history": _budget_report(
            original_amount=_history_turn_count(source_history),
            retained_amount=_history_turn_count(projected_history),
            original_value=_slot_value(source_history),
            retained_value=_slot_value(projected_history),
            limit_amount=budget.history_turns,
            limit_estimated_tokens=budget.history_max_estimated_tokens,
            reasons=_slot_budget_reasons(projected_history),
            extra={
                "original_message_count": _history_message_count(source_history),
                "retained_message_count": _history_message_count(projected_history),
            },
        ),
        "execution_ledger": _budget_report(
            original_amount=_record_count(source_execution),
            retained_amount=_record_count(projected_execution),
            original_value=_slot_value(source_execution),
            retained_value=_slot_value(projected_execution),
            limit_amount=budget.execution_history_records,
            limit_estimated_tokens=budget.execution_history_max_estimated_tokens,
            reasons=_slot_budget_reasons(projected_execution),
        ),
        "memory": _budget_report(
            original_amount=_memory_fact_count(source_memory),
            retained_amount=_memory_fact_count(projected_memory),
            original_value=_named_slot_values(source_memory),
            retained_value=_named_slot_values(projected_memory),
            limit_amount=None,
            limit_estimated_tokens=budget.memory_max_estimated_tokens,
            reasons=list(
                dict.fromkeys(
                    [
                        *_collect_slot_budget_reasons(projected_memory),
                        *_coerce_reasons(projected.meta.get("memory_budget_reasons")),
                    ]
                )
            ),
        ),
        "tool_schema": _budget_report(
            original_amount=_tool_count(source_tools),
            retained_amount=_tool_count(projected_tools),
            original_value=_slot_value(source_tools),
            retained_value=_slot_value(projected_tools),
            limit_amount=None,
            limit_estimated_tokens=None,
            reasons=(
                ["capability_snapshot_selection"] if projected_tools is not None else []
            ),
            enforced=False,
        ),
    }


def _budget_report(
    *,
    original_amount: int,
    retained_amount: int,
    original_value: object,
    retained_value: object,
    limit_amount: int | None,
    limit_estimated_tokens: int | None,
    reasons: list[str],
    extra: dict[str, int] | None = None,
    enforced: bool = True,
) -> dict[str, object]:
    report: dict[str, object] = {
        "original_amount": original_amount,
        "retained_amount": retained_amount,
        "original_estimated_tokens": _estimate_value_tokens(original_value),
        "retained_estimated_tokens": _estimate_value_tokens(retained_value),
        "limit_amount": limit_amount,
        "limit_estimated_tokens": limit_estimated_tokens,
        "truncated": enforced
        and (original_amount != retained_amount or bool(reasons)),
        "truncation_reasons": reasons or ["within_budget"],
        "enforced": enforced,
    }
    if extra:
        report.update(extra)
    return report


def _slot_value(slot: ContextSlot | None) -> object:
    return slot.value if slot is not None else None


def _history_turn_count(slot: ContextSlot | None) -> int:
    value = _slot_value(slot)
    turns = value.get("turns") if isinstance(value, dict) else None
    return len(turns) if isinstance(turns, list) else 0


def _history_message_count(slot: ContextSlot | None) -> int:
    value = _slot_value(slot)
    turns = value.get("turns") if isinstance(value, dict) else None
    if not isinstance(turns, list):
        return 0
    return sum(
        1
        for turn in turns
        if isinstance(turn, dict)
        for key in ("user_message", "assistant_message")
        if isinstance(turn.get(key), dict) and turn[key]
    )


def _record_count(slot: ContextSlot | None) -> int:
    value = _slot_value(slot)
    records = value.get("records") if isinstance(value, dict) else None
    return len(records) if isinstance(records, list) else 0


def _memory_fact_count(slots: list[ContextSlot]) -> int:
    count = 0
    for slot in slots:
        value = slot.value
        items = value.get("items") if isinstance(value, dict) else None
        count += len(items) if isinstance(items, list) else 1
    return count


def _tool_count(slot: ContextSlot | None) -> int:
    value = _slot_value(slot)
    tools = value.get("tools") if isinstance(value, dict) else None
    return len(tools) if isinstance(tools, list) else 0


def _named_slot_values(slots: list[ContextSlot]) -> dict[str, object]:
    return {slot.name: slot.value for slot in slots}


def _estimate_named_slots_tokens(slots: list[ContextSlot]) -> int:
    return _estimate_value_tokens(_named_slot_values(slots))


def _slot_budget_reasons(slot: ContextSlot | None) -> list[str]:
    if slot is None:
        return []
    reasons = slot.meta.get("budget_truncation_reasons")
    return [str(reason) for reason in reasons] if isinstance(reasons, list) else []


def _collect_slot_budget_reasons(slots: list[ContextSlot]) -> list[str]:
    reasons: list[str] = []
    for slot in slots:
        reasons.extend(_slot_budget_reasons(slot))
    return list(dict.fromkeys(reasons))


def _coerce_reasons(value: object) -> list[str]:
    return [str(reason) for reason in value] if isinstance(value, list) else []


def _estimate_value_tokens(value: object) -> int:
    if value is None:
        return 0
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        serialized = str(value or "")
    return math.ceil(len(serialized) / 4)


def _sanitize_group_record(
    value: Any,
    *,
    max_content_chars: int,
) -> dict[str, Any]:
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
        if raw_value is not None:
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


__all__ = [
    "CORE_HISTORY_HARD_TURN_LIMIT",
    "PromptTargetBudget",
    "apply_target_budget",
    "resolve_target_budget",
]
