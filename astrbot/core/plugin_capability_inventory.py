"""Build the plugin capability inventory for diagnostics and configuration.

This is the read model behind the capability list API and UI. It reports, per
plugin capability:

* ``kind`` / ``owner`` / ``plugin_id`` - what it is and who registered it
* ``permission_state`` / ``applicability_state`` - the two admission axes
* ``target`` - the fixed or configured consumer
* ``hard_or_soft`` - whether the contribution is mandatory
* ``source`` - where the current target came from
* ``migration_state`` - compatibility/legacy status

Editable versus read-only fields are separated explicitly
(:data:`EDITABLE_FIELDS` / :data:`READ_ONLY_FIELDS`) so the UI cannot offer a
target for capabilities whose consumer is fixed by contract.
"""

from __future__ import annotations

from typing import Any

from astrbot.core.plugin_admission import (
    INTERACTION_CAPABILITY_KINDS,
    PROCESS_CAPABILITY_KINDS,
    CapabilityKind,
    CapabilityRef,
    PluginAdmissionSnapshot,
    get_plugin_admission_snapshot,
    is_hard_contribution,
    resolve_capability_admission,
)

#: Fields the user may change. Deliberately small: the capability *target* is
#: fixed by contract for most kinds, so exposing it would allow meaningless or
#: illegal values.
EDITABLE_FIELDS: tuple[str, ...] = (
    "permission_override",  # enable/disable this capability
    "session_allowed",  # whether the current session may use it
    "direct_output_compatibility",  # legacy direct send stays on compatibility path
    "hook_target",  # LLM hook: core | personal_expression
    "tool_target",  # FunctionTool: core | personal_expression
)

#: Fields shown but not editable, with the reason recorded per entry.
READ_ONLY_FIELDS: tuple[str, ...] = (
    "kind",
    "owner",
    "plugin_id",
    "target",
    "source",
    "hard_or_soft",
    "migration_state",
    "permission_state",
    "applicability_state",
)

#: Default consumer per capability kind. ``None`` means "decided elsewhere"
#: (Prompt Extension by ``meta.targets``).
_DEFAULT_TARGETS: dict[CapabilityKind, str | None] = {
    CapabilityKind.HANDLER: "pipeline",
    CapabilityKind.LLM_HOOK: "personal_expression",
    CapabilityKind.TOOL: "core",
    CapabilityKind.PROMPT_EXTENSION: None,
    CapabilityKind.INTERACTION_PROMPT: "personal_expression",
    CapabilityKind.INTERACTION_RESULT: "personal_expression",
    CapabilityKind.STREAM_DECIDER: "personal_expression",
    CapabilityKind.LIFECYCLE_OBSERVER: "observe",
    CapabilityKind.PERSONA_EFFECT: "personal_expression",
    CapabilityKind.RUNTIME_SENSOR: "observe",
    CapabilityKind.EVENT_INJECTION: "compatibility",
    CapabilityKind.DIRECT_OUTPUT: "compatibility",
    CapabilityKind.POSTPROCESSOR: "compatibility",
    CapabilityKind.WEB_API: "lifecycle",
    CapabilityKind.PROVIDER: "lifecycle",
    CapabilityKind.PLATFORM_ADAPTER: "lifecycle",
    CapabilityKind.GLOBAL_TASK: "lifecycle",
    CapabilityKind.CRON_JOB: "lifecycle",
    CapabilityKind.AGENT_TOOL: "core",
    CapabilityKind.PLUGIN_PAGE: "lifecycle",
    CapabilityKind.MANAGEMENT_HOOK: "pipeline",
}

#: Kinds whose consumer is fixed by contract and must not be user-configurable.
_FIXED_TARGET_KINDS: frozenset[CapabilityKind] = frozenset(
    {
        CapabilityKind.HANDLER,
        CapabilityKind.PROMPT_EXTENSION,
        CapabilityKind.PERSONA_EFFECT,
        CapabilityKind.RUNTIME_SENSOR,
        CapabilityKind.LIFECYCLE_OBSERVER,
        CapabilityKind.MANAGEMENT_HOOK,
    }
)

_MIGRATION_STATES: dict[CapabilityKind, str] = {
    CapabilityKind.DIRECT_OUTPUT: "legacy_compatibility",
    CapabilityKind.EVENT_INJECTION: "needs_review",
    CapabilityKind.POSTPROCESSOR: "needs_review",
    CapabilityKind.GLOBAL_TASK: "leak_on_unload",
    CapabilityKind.WEB_API: "no_lifecycle_gate",
    CapabilityKind.PROVIDER: "no_lifecycle_gate",
    CapabilityKind.PLATFORM_ADAPTER: "no_lifecycle_gate",
    CapabilityKind.CRON_JOB: "no_lifecycle_gate",
}


def _capability_target_reason(kind: CapabilityKind) -> str:
    if kind in _FIXED_TARGET_KINDS:
        return "fixed_by_contract"
    if kind is CapabilityKind.LLM_HOOK:
        return "plugin_runtime_targets"
    if kind in {CapabilityKind.TOOL, CapabilityKind.AGENT_TOOL}:
        return "plugin_tool_targets"
    if kind in PROCESS_CAPABILITY_KINDS:
        return "process_lifecycle"
    return "default"


def describe_capability(
    *,
    event: Any,
    kind: CapabilityKind,
    owner_module_path: str | None,
    owner_plugin_name: str | None = None,
    plugin_id: str | None = None,
    item_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    snapshot: PluginAdmissionSnapshot | None = None,
) -> dict[str, Any]:
    """Describe one capability entry for the inventory API."""
    ref = CapabilityRef(
        kind=kind,
        owner_module_path=owner_module_path,
        owner_plugin_name=owner_plugin_name,
        plugin_id=plugin_id,
        item_name=item_name,
    )
    snapshot = snapshot if snapshot is not None else get_plugin_admission_snapshot(event)
    decision = (
        snapshot.decision_for(ref)
        if snapshot is not None
        else resolve_capability_admission(event=event, ref=ref)
    )

    is_interaction = kind in INTERACTION_CAPABILITY_KINDS
    hard = is_hard_contribution(metadata)

    entry: dict[str, Any] = {
        "kind": kind.value,
        "owner_module_path": owner_module_path,
        "owner_plugin_name": owner_plugin_name,
        "plugin_id": plugin_id,
        "item_name": item_name,
        "permission_state": "allowed" if decision.allowed else decision.reason,
        "permission_reason": decision.reason,
        "permission_editable": is_interaction,
        "applicability_state": "event_filter",
        "hard_or_soft": "hard" if hard else "soft",
        "target": _DEFAULT_TARGETS.get(kind),
        "target_reason": _capability_target_reason(kind),
        "target_editable": kind not in _FIXED_TARGET_KINDS,
        "migration_state": _MIGRATION_STATES.get(kind, "current"),
        "scope": "interaction" if is_interaction else "process",
        "owner_source": decision is not None and getattr(decision, "reason", "") or "",
    }
    return entry


def build_capability_inventory(
    *,
    event: Any,
    context: Any,
    runtime_config: Any = None,
) -> dict[str, Any]:
    """Return the full capability inventory grouped by plugin.

    Combines every plugin-facing surface:

    * the registered capability registries (owner-attributed), and
    * official Handlers and LLM lifecycle hooks from the handler registry, and
    * plugin-owned FunctionTools from the tool manager.

    ``runtime_config`` lets the API resolve the *actual* configured target for
    hooks and tools when there is no event (the dashboard has none).
    """
    snapshot = get_plugin_admission_snapshot(event) if event is not None else None
    if runtime_config is None and event is not None:
        getter = getattr(event, "get_extra", None)
        if callable(getter):
            runtime_config = getter("_astrbot_config", None) or {}

    by_plugin: dict[str, list[dict[str, Any]]] = {}
    owners: dict[str, str | None] = {}

    def add(plugin_key: str, entry: dict[str, Any], owner: str | None) -> None:
        by_plugin.setdefault(plugin_key, []).append(entry)
        owners.setdefault(plugin_key, owner)

    list_owners = getattr(context, "list_plugin_capability_owners", None)
    if callable(list_owners):
        for row in list_owners():
            kind_value = row.get("kind")
            try:
                kind = CapabilityKind(kind_value)
            except ValueError:
                continue
            entry = describe_capability(
                event=event,
                kind=kind,
                owner_module_path=row.get("owner_module_path"),
                owner_plugin_name=row.get("owner_plugin_name"),
                plugin_id=row.get("plugin_id"),
                metadata=row.get("metadata"),
                snapshot=snapshot,
            )
            entry["owner_source"] = row.get("owner_source") or ""
            key = (
                row.get("owner_plugin_name")
                or row.get("owner_module_path")
                or "<unknown>"
            )
            add(key, entry, row.get("owner_module_path"))

    for entry, key, owner in _iter_handler_capabilities(event, runtime_config):
        add(key, entry, owner)

    for entry, key, owner in _iter_tool_capabilities(event, runtime_config):
        add(key, entry, owner)

    return {
        "editabled_fields": list(EDITABLE_FIELDS),
        "read_only_fields": list(READ_ONLY_FIELDS),
        "plugins": [
            {
                "plugin_name": name,
                "owner_module_path": owners.get(name),
                "capabilities": sorted(
                    entries,
                    key=lambda item: (item["scope"], item["kind"], item["item_name"] or ""),
                ),
            }
            for name, entries in sorted(by_plugin.items())
        ],
    }


#: Event types owned by the official Pipeline rather than a Persona/Core consumer.
_PIPELINE_EVENT_NAMES = {"AdapterMessageEvent"}

#: Management lifecycle hooks. They are intentionally exempt from session-level
#: disabling, so they are reported without an editable permission.
_MANAGEMENT_EVENT_NAMES = {
    "OnAstrBotLoadedEvent",
    "OnPlatformLoadedEvent",
    "OnPluginLoadedEvent",
    "OnPluginUnloadedEvent",
    "OnPluginErrorEvent",
}


def _iter_handler_capabilities(event: Any, runtime_config: Any):
    """Yield inventory entries for official Handlers and LLM lifecycle hooks."""
    try:
        from astrbot.core.plugin_runtime import resolve_plugin_runtime_target
        from astrbot.core.star.star import star_map
        from astrbot.core.star.star_handler import star_handlers_registry
    except Exception:  # noqa: BLE001
        return

    for handler in list(star_handlers_registry._handlers):
        event_type = getattr(handler, "event_type", None)
        event_name = getattr(event_type, "name", "") or ""
        module_path = getattr(handler, "handler_module_path", None)
        metadata = star_map.get(module_path) if module_path else None
        if metadata is None:
            continue
        owner_name = getattr(metadata, "name", None) or module_path
        owner_module = getattr(metadata, "module_path", None) or module_path

        if event_name in _PIPELINE_EVENT_NAMES:
            kind = CapabilityKind.HANDLER
            target, target_reason = "pipeline", "fixed_by_contract"
            editable = False
        elif event_name in _MANAGEMENT_EVENT_NAMES:
            kind = CapabilityKind.MANAGEMENT_HOOK
            target, target_reason = "pipeline", "fixed_by_contract"
            editable = False
        else:
            kind = CapabilityKind.LLM_HOOK
            resolved, source = resolve_plugin_runtime_target(
                runtime_config, metadata, module_path
            )
            target = resolved
            target_reason = (
                "plugin_runtime_targets"
                if source == "plugin_runtime_targets"
                else "default"
            )
            editable = True

        entry = describe_capability(
            event=event,
            kind=kind,
            owner_module_path=owner_module,
            owner_plugin_name=owner_name,
            item_name=getattr(handler, "handler_name", None),
            snapshot=get_plugin_admission_snapshot(event) if event is not None else None,
        )
        entry["target"] = target
        entry["target_reason"] = target_reason
        entry["target_editable"] = editable
        entry["handler_event_type"] = event_name
        entry["description"] = getattr(handler, "desc", "") or ""
        yield entry, owner_name, owner_module


def _iter_tool_capabilities(event: Any, runtime_config: Any):
    """Yield inventory entries for plugin-owned FunctionTools."""
    try:
        from astrbot.core.plugin_runtime import resolve_tool_runtime_target
        from astrbot.core.provider.register import llm_tools
        from astrbot.core.star.star import star_map
    except Exception:  # noqa: BLE001
        return

    for tool in list(getattr(llm_tools, "func_list", []) or []):
        module_path = getattr(tool, "handler_module_path", None)
        handler = getattr(tool, "handler", None)
        if not module_path and handler is not None:
            module_path = getattr(handler, "__module__", None)
        metadata = _metadata_for_module(star_map, module_path)
        if metadata is None:
            # Built-in / MCP tools are not plugin capabilities for this model;
            # they keep their own execution_targets.
            continue
        owner_name = getattr(metadata, "name", None) or module_path
        owner_module = getattr(metadata, "module_path", None) or module_path
        tool_name = str(getattr(tool, "name", "") or "")
        target, source = resolve_tool_runtime_target(
            runtime_config, metadata, module_path, tool_name, tool=tool
        )
        entry = describe_capability(
            event=event,
            kind=CapabilityKind.TOOL,
            owner_module_path=owner_module,
            owner_plugin_name=owner_name,
            item_name=tool_name,
            snapshot=get_plugin_admission_snapshot(event) if event is not None else None,
        )
        entry["target"] = target
        entry["target_reason"] = (
            "plugin_tool_targets" if source == "plugin_tool_targets" else source
        )
        entry["target_editable"] = True
        entry["description"] = str(getattr(tool, "description", "") or "")[:200]
        yield entry, owner_name, owner_module


def _metadata_for_module(star_map: dict, module_path: str | None):
    if not isinstance(module_path, str) or not module_path:
        return None
    direct = star_map.get(module_path)
    if direct is not None:
        return direct
    for candidate_path, metadata in star_map.items():
        if module_path == candidate_path or module_path.startswith(
            f"{candidate_path}."
        ):
            return metadata
    return None


__all__ = [
    "EDITABLE_FIELDS",
    "READ_ONLY_FIELDS",
    "build_capability_inventory",
    "describe_capability",
]
