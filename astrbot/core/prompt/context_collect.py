"""
Prompt context collection helpers.

This module wires collectors into a single fail-fast collection flow.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from copy import deepcopy
from typing import Literal

from astrbot.core import logger
from astrbot.core.capabilities import (
    CapabilityResolver,
    CapabilitySnapshot,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.star.context import Context

from .collectors.conversation_history_collector import ConversationHistoryCollector
from .collectors.core_execution_history_collector import CoreExecutionHistoryCollector
from .collectors.core_task_collector import CoreTaskCollector
from .collectors.explicit_context_collector import ExplicitContextCollector
from .collectors.input_collector import InputCollector
from .collectors.knowledge_collector import KnowledgeCollector
from .collectors.memory_collector import MemoryCollector
from .collectors.persona_collector import PersonaCollector
from .collectors.persona_relationship_collector import PersonaRelationshipCollector
from .collectors.policy_collector import PolicyCollector
from .collectors.session_collector import SessionCollector
from .collectors.skills_collector import SkillsCollector
from .collectors.subagent_collector import SubagentCollector
from .collectors.system_collector import SystemCollector
from .collectors.tools_collector import ToolsCollector
from .context_catalog import get_catalog
from .context_types import ContextPack, ContextSlot, PromptContextConflictError
from .extensions.types import (
    PROMPT_EXTENSION_MOUNTS,
    PROMPT_EXTENSION_VALUE_KINDS,
    PromptExtension,
)
from .interfaces.context_collector_inferface import ContextCollectorInterface

PROMPT_CONTEXT_PACK_EXTRA_KEY = "prompt_context_pack"
PROMPT_STATIC_CONTEXT_CACHE_EXTRA_KEY = "_prompt_static_context_cache"
PROMPT_EXTENSION_STATIC_CACHE_EXTRA_KEY = "_prompt_extension_static_cache"
PROMPT_EXTENSION_SLOT_NAMES: dict[str, str] = {
    mount: f"extension.{mount}" for mount in PROMPT_EXTENSION_MOUNTS
}
PLUGIN_PROMPT_TARGETS = frozenset({"persona", "core"})
CONTROL_PLANE_PROMPT_TARGETS = frozenset({"router", "core_planner"})
PromptExtensionCollectorScope = Literal["all", "control_plane", "plugin"]
_CONTROL_PLANE_COLLECTOR_MODULE_PREFIXES = (
    "astrbot.core.",
    "astrbot.builtin_stars.",
)


async def resolve_toolset_for_target(
    *,
    event: AstrMessageEvent,
    plugin_context: Context,
    config,
    target: str,
    provider_request=None,
):
    """Compatibility wrapper around the runtime capability owner."""
    capabilities = await CapabilityResolver().resolve(
        event=event,
        plugin_context=plugin_context,
        config=config,
        target=target,
        provider_request=provider_request,
    )
    return (
        capabilities.persona_id,
        capabilities.to_toolset(),
        capabilities.selection_mode,
    )


def _default_collectors(
    capabilities: CapabilitySnapshot | None = None,
) -> list[ContextCollectorInterface]:
    """Return the full collector set used by the native Core path."""
    return [
        SystemCollector(capabilities=capabilities),
        CoreTaskCollector(),
        PersonaCollector(),
        InputCollector(),
        SessionCollector(),
        PolicyCollector(),
        MemoryCollector(),
        ConversationHistoryCollector(),
        CoreExecutionHistoryCollector(),
        ExplicitContextCollector(),
        SkillsCollector(),
        ToolsCollector(capabilities=capabilities),
        SubagentCollector(),
        KnowledgeCollector(),
    ]


def interaction_base_collectors() -> list[ContextCollectorInterface]:
    """Return facts needed before an Interaction route is known.

    Core execution resources are collected later, after routing.  This keeps
    speculative Router and Persona branches independent from Core-only state.
    """
    return [
        SystemCollector(base_only=True),
        PersonaCollector(),
        InputCollector(),
        SessionCollector(),
        MemoryCollector(include_persona_state=False),
        PersonaRelationshipCollector(),
        ConversationHistoryCollector(),
        ExplicitContextCollector(),
    ]


def _stringify_value_preview(value: object, *, max_len: int = 400) -> str:
    """Create a compact preview string for logs."""
    if isinstance(value, str):
        preview = " ".join(value.split())
    else:
        try:
            preview = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            preview = repr(value)

    if len(preview) <= max_len:
        return preview
    return f"{preview[: max_len - 3]}..."


def _normalize_prompt_extension_items(raw_items: object) -> list[PromptExtension]:
    if isinstance(raw_items, list):
        candidates = raw_items
    elif raw_items is None:
        return []
    else:
        try:
            candidates = list(raw_items)
        except TypeError:
            return []
    return [item for item in candidates if isinstance(item, PromptExtension)]


def _build_prompt_extension_record(extension: PromptExtension) -> dict[str, object]:
    return {
        "plugin_id": extension.plugin_id,
        "title": extension.title,
        "value_kind": extension.value_kind,
        "value": extension.value,
        "order": extension.order,
        "meta": extension.meta,
    }


def _coerce_prompt_extension_collector_priority(collector: object) -> int:
    try:
        return int(getattr(collector, "priority", 100))
    except (TypeError, ValueError):
        return 100


def _coerce_prompt_extension_order(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 100


def _collector_lifecycle(collector: object) -> str:
    return str(getattr(collector, "lifecycle", "dynamic") or "dynamic").strip().lower()


def _collector_cache_key(collector: object) -> str:
    explicit_key = getattr(collector, "cache_key", None)
    if isinstance(explicit_key, str) and explicit_key.strip():
        return explicit_key.strip()
    cls = collector.__class__
    return f"{cls.__module__}.{cls.__qualname__}"


def _prompt_extension_cache_key(collector: object, plugin_id: str) -> str:
    collector_key = _collector_cache_key(collector)
    return f"{plugin_id}:{collector_key}" if plugin_id else collector_key


def _get_event_dict_extra(event: AstrMessageEvent, key: str) -> dict:
    cache = event.get_extra(key, {})
    if isinstance(cache, dict):
        return cache
    return {}


def _add_collected_slot(
    pack: ContextPack,
    slot: ContextSlot,
    *,
    producer: str,
) -> None:
    existing = pack.get_slot(slot.name)
    if existing is None:
        pack.add_slot(slot)
        return
    if existing == slot:
        return
    raise PromptContextConflictError(
        f"conflicting prompt context slot in one collection: {slot.name} "
        f"({existing.source} != {producer})"
    )


def _find_static_cache_entry(
    cache: dict,
    key: str,
    *,
    config: object,
    provider_request: object | None,
) -> object:
    entries = cache.get(key)
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("config") is config
            and entry.get("provider_request") is provider_request
        ):
            return entry.get("items")
    return None


def _copy_for_static_cache(items: object) -> object | None:
    try:
        return deepcopy(items)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to copy prompt static cache items; skip static cache: %s",
            exc,
            exc_info=True,
        )
        return None


def _store_static_cache_entry(
    cache: dict,
    key: str,
    *,
    config: object,
    provider_request: object | None,
    items: object,
) -> None:
    entries = cache.setdefault(key, [])
    if not isinstance(entries, list):
        entries = []
        cache[key] = entries
    cached_items = _copy_for_static_cache(items)
    if cached_items is None:
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("config") is config
            and entry.get("provider_request") is provider_request
        ):
            entry["items"] = cached_items
            return
    entries.append(
        {
            "config": config,
            "provider_request": provider_request,
            "items": cached_items,
        }
    )


def build_prompt_extension_slots(
    extensions: Iterable[PromptExtension],
    *,
    source: str = "prompt_extension_collectors",
) -> list[ContextSlot]:
    extension_list = [
        normalized
        for extension in extensions
        if (normalized := _normalize_plugin_prompt_extension(extension)) is not None
    ]
    return _build_normalized_prompt_extension_slots(
        extension_list,
        source=source,
    )


def _build_normalized_prompt_extension_slots(
    extension_list: list[PromptExtension],
    *,
    source: str,
) -> list[ContextSlot]:
    grouped_items: dict[str, list[dict[str, object]]] = {
        mount: [] for mount in PROMPT_EXTENSION_MOUNTS
    }
    direct_slots: list[ContextSlot] = []
    for extension in extension_list:
        if not isinstance(extension.plugin_id, str) or not extension.plugin_id.strip():
            raise ValueError("Prompt extension must define a non-empty plugin_id")
        if extension.mount not in PROMPT_EXTENSION_MOUNTS:
            raise ValueError(
                f"Prompt extension has invalid mount: plugin_id={extension.plugin_id} mount={extension.mount}"
            )
        if extension.value_kind not in PROMPT_EXTENSION_VALUE_KINDS:
            raise ValueError(
                f"Prompt extension has invalid value_kind: plugin_id={extension.plugin_id} value_kind={extension.value_kind}"
            )
        direct_slot_name = extension.meta.get("context_slot")
        if isinstance(direct_slot_name, str) and direct_slot_name.strip():
            direct_slots.append(
                ContextSlot(
                    name=direct_slot_name.strip(),
                    value=deepcopy(extension.value),
                    category=str(extension.meta.get("context_category", "extension")),
                    source=extension.plugin_id,
                    render_mode="structured",
                    meta=deepcopy(extension.meta),
                )
            )
            continue
        grouped_items[extension.mount].append(_build_prompt_extension_record(extension))

    direct_plugin_directories = [
        slot for slot in direct_slots if slot.name == "capability.plugin_directory"
    ]
    slots: list[ContextSlot] = [
        slot for slot in direct_slots if slot.name != "capability.plugin_directory"
    ]
    merged_plugin_directory = _combine_plugin_directories(direct_plugin_directories)
    if merged_plugin_directory:
        slots.append(
            ContextSlot(
                name="capability.plugin_directory",
                value={"plugins": merged_plugin_directory},
                category="capability",
                source=source,
                render_mode="structured",
                meta={
                    "scope": "static",
                    "plugin_count": len(merged_plugin_directory),
                },
            )
        )
    for mount, items in grouped_items.items():
        if not items:
            continue
        slot_name = PROMPT_EXTENSION_SLOT_NAMES[mount]
        slots.append(
            ContextSlot(
                name=slot_name,
                value={
                    "format": "prompt_extensions_v1",
                    "mount": mount,
                    "items": items,
                },
                category="extension",
                source=source,
                render_mode="structured",
                meta={
                    "mount": mount,
                    "plugin_count": len(
                        {
                            item["plugin_id"]
                            for item in items
                            if isinstance(item.get("plugin_id"), str)
                        }
                    ),
                    "item_count": len(items),
                },
            )
        )
    return slots


def _normalize_plugin_prompt_extension(
    extension: PromptExtension,
    *,
    allow_control_plane_targets: bool = False,
) -> PromptExtension | None:
    """Keep third-party prompt contributions off Router and Core Planner."""
    normalized = deepcopy(extension)
    meta = dict(normalized.meta)
    raw_targets = meta.get("targets")
    if raw_targets is None:
        targets = {"core"}
    elif isinstance(raw_targets, list | tuple | set | frozenset):
        targets = {str(target).strip() for target in raw_targets if str(target).strip()}
        allowed_targets = set(PLUGIN_PROMPT_TARGETS)
        if allow_control_plane_targets:
            allowed_targets.update(CONTROL_PLANE_PROMPT_TARGETS)
        targets.intersection_update(allowed_targets)
    else:
        return None
    if not targets:
        return None
    meta["targets"] = sorted(targets)
    normalized.meta = meta
    return normalized


def _collector_allows_control_plane_targets(collector: object) -> bool:
    if getattr(collector, "control_plane_context", False) is not True:
        return False
    module_path = str(getattr(type(collector), "__module__", "") or "")
    return module_path.startswith(_CONTROL_PLANE_COLLECTOR_MODULE_PREFIXES)


def _combine_plugin_directories(
    direct_slots: list[ContextSlot],
) -> list[dict[str, object]]:
    plugins: list[dict[str, object]] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()

    candidates: list[tuple[object, object]] = []
    for slot in direct_slots:
        raw_plugins = (
            slot.value.get("plugins") if isinstance(slot.value, dict) else None
        )
        if isinstance(raw_plugins, dict):
            raw_plugins = [raw_plugins]
        if not isinstance(raw_plugins, list):
            continue
        candidates.extend((plugin, slot.meta.get("targets")) for plugin in raw_plugins)

    for candidate, inherited_targets in candidates:
        if not isinstance(candidate, dict):
            continue
        name = str(candidate.get("name", "") or "").strip()
        description = str(candidate.get("description", "") or "").strip()
        raw_targets = candidate.get("targets", inherited_targets)
        targets = (
            sorted({str(target) for target in raw_targets})
            if isinstance(raw_targets, list | tuple | set)
            else []
        )
        key = (name, description, tuple(targets))
        if not name or not description or not targets or key in seen:
            continue
        seen.add(key)
        plugins.append(
            {
                "name": name,
                "description": description,
                "targets": targets,
            }
        )
    return plugins


async def _collect_prompt_extension_slots(
    *,
    event: AstrMessageEvent,
    plugin_context: Context,
    config,
    provider_request,
    collector_scope: PromptExtensionCollectorScope = "all",
) -> tuple[list[ContextSlot], list[str]]:
    list_collectors = getattr(plugin_context, "list_prompt_extension_collectors", None)
    if not callable(list_collectors):
        return [], []

    raw_collectors = list_collectors()
    try:
        collectors = list(raw_collectors or [])
    except TypeError:
        return [], []
    collectors = [
        collector
        for collector in collectors
        if _prompt_extension_collector_in_scope(collector, collector_scope)
    ]
    collectors.sort(
        key=lambda collector: (
            _coerce_prompt_extension_collector_priority(collector),
            collector.__class__.__name__,
        )
    )

    collected_extensions: list[PromptExtension] = []
    collector_names: list[str] = []
    static_cache = _get_event_dict_extra(event, PROMPT_EXTENSION_STATIC_CACHE_EXTRA_KEY)

    for collector in collectors:
        collector_name = collector.__class__.__name__
        collector_names.append(collector_name)
        allow_control_plane_targets = _collector_allows_control_plane_targets(collector)
        lifecycle = _collector_lifecycle(collector)
        plugin_id = str(getattr(collector, "plugin_id", "") or "").strip()
        static_cache_key = _prompt_extension_cache_key(collector, plugin_id)
        cached_items = _find_static_cache_entry(
            static_cache,
            static_cache_key,
            config=config,
            provider_request=provider_request,
        )
        if lifecycle == "static" and cached_items is not None:
            cached_extensions = _normalize_prompt_extension_items(
                deepcopy(cached_items)
            )
            collected_extensions.extend(
                normalized
                for extension in cached_extensions
                if (
                    normalized := _normalize_plugin_prompt_extension(
                        extension,
                        allow_control_plane_targets=allow_control_plane_targets,
                    )
                )
                is not None
            )
            continue
        try:
            raw_extensions = await collector.collect(
                event,
                plugin_context,
                config,
                provider_request=provider_request,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Prompt extension collector failed: collector=%s error=%s",
                collector_name,
                exc,
                exc_info=True,
            )
            continue

        extensions = _normalize_prompt_extension_items(raw_extensions)
        extensions.sort(key=lambda extension: extension.order)
        for extension in extensions:
            if (
                not isinstance(extension.plugin_id, str)
                or not extension.plugin_id.strip()
            ):
                logger.warning(
                    "Prompt extension skipped: collector=%s reason=empty_plugin_id",
                    collector_name,
                )
                continue
            if extension.mount not in PROMPT_EXTENSION_MOUNTS:
                logger.warning(
                    "Prompt extension skipped: collector=%s plugin_id=%s reason=invalid_mount mount=%s",
                    collector_name,
                    extension.plugin_id,
                    extension.mount,
                )
                continue
            if extension.value_kind not in PROMPT_EXTENSION_VALUE_KINDS:
                logger.warning(
                    "Prompt extension skipped: collector=%s plugin_id=%s reason=invalid_value_kind value_kind=%s",
                    collector_name,
                    extension.plugin_id,
                    extension.value_kind,
                )
                continue

            normalized = _normalize_plugin_prompt_extension(
                extension,
                allow_control_plane_targets=allow_control_plane_targets,
            )
            if normalized is not None:
                collected_extensions.append(normalized)

        if lifecycle == "static":
            _store_static_cache_entry(
                static_cache,
                static_cache_key,
                config=config,
                provider_request=provider_request,
                items=extensions,
            )

    event.set_extra(PROMPT_EXTENSION_STATIC_CACHE_EXTRA_KEY, static_cache)

    slots = _build_normalized_prompt_extension_slots(
        collected_extensions,
        source="prompt_extension_collectors",
    )
    return slots, collector_names


def _prompt_extension_collector_in_scope(
    collector: object,
    scope: PromptExtensionCollectorScope,
) -> bool:
    is_control_plane = _collector_allows_control_plane_targets(collector)
    if scope == "all":
        return True
    if scope == "control_plane":
        return is_control_plane
    if scope == "plugin":
        return not is_control_plane
    raise ValueError(f"unsupported prompt extension collector scope: {scope}")


async def collect_context_pack(
    *,
    event: AstrMessageEvent,
    plugin_context: Context,
    config,
    provider_request=None,
    collectors: Iterable[ContextCollectorInterface] | None = None,
    capabilities: CapabilitySnapshot | None = None,
    include_prompt_extensions: bool = True,
    prompt_extension_collector_scope: PromptExtensionCollectorScope = "all",
) -> ContextPack:
    """
    Collect prompt context into a single pack.

    Required collectors are fail-fast. Explicitly optional collectors record a
    diagnostic and contribute no slots when unavailable. This stage does not
    mutate ProviderRequest.
    """
    catalog = get_catalog(strict=True)
    collector_list = (
        list(collectors)
        if collectors is not None
        else _default_collectors(capabilities)
    )
    static_context_cache = _get_event_dict_extra(
        event, PROMPT_STATIC_CONTEXT_CACHE_EXTRA_KEY
    )

    pack = ContextPack(
        provider_request_ref=provider_request,
        meta={
            "catalog_version": catalog.version,
            "collectors": [],
            "extension_collectors": [],
        },
    )

    for collector in collector_list:
        collector_name = collector.__class__.__name__
        pack.meta["collectors"].append(collector_name)
        lifecycle = _collector_lifecycle(collector)
        static_cache_key = _collector_cache_key(collector)

        cached_items = _find_static_cache_entry(
            static_context_cache,
            static_cache_key,
            config=config,
            provider_request=provider_request,
        )
        if lifecycle == "static" and cached_items is not None:
            slots = deepcopy(cached_items)
            pack.meta.setdefault("cached_collectors", []).append(collector_name)
        else:
            try:
                slots = await collector.collect(
                    event,
                    plugin_context,
                    config,
                    provider_request=provider_request,
                )
            except Exception as exc:  # noqa: BLE001
                if getattr(collector, "failure_policy", "required") != "optional":
                    raise
                logger.warning(
                    "Optional prompt collector failed; continuing without its slots: collector=%s error=%s",
                    collector_name,
                    exc,
                    exc_info=True,
                )
                pack.meta.setdefault("collector_failures", []).append(
                    {
                        "collector": collector_name,
                        "error_type": type(exc).__name__,
                        "reason": str(exc),
                    }
                )
                slots = []
            if lifecycle == "static":
                _store_static_cache_entry(
                    static_context_cache,
                    static_cache_key,
                    config=config,
                    provider_request=provider_request,
                    items=slots,
                )

        for slot in slots:
            if not catalog.has(slot.name):
                logger.warning(
                    "Prompt context slot is not declared in catalog: slot=%s collector=%s",
                    slot.name,
                    collector_name,
                )

            _add_collected_slot(pack, slot, producer=collector_name)

    event.set_extra(PROMPT_STATIC_CONTEXT_CACHE_EXTRA_KEY, static_context_cache)

    if include_prompt_extensions:
        extension_slots, extension_collectors = await _collect_prompt_extension_slots(
            event=event,
            plugin_context=plugin_context,
            config=config,
            provider_request=provider_request,
            collector_scope=prompt_extension_collector_scope,
        )
        pack.meta["extension_collectors"] = extension_collectors

        for slot in extension_slots:
            if not catalog.has(slot.name):
                logger.warning(
                    "Prompt context slot is not declared in catalog: slot=%s collector=%s",
                    slot.name,
                    "PromptExtensionCollectors",
                )

            _add_collected_slot(
                pack,
                slot,
                producer="PromptExtensionCollectors",
            )

    pack.meta["slot_count"] = len(pack.slots)
    return pack


def log_context_pack(
    pack: ContextPack, *, event: AstrMessageEvent | None = None
) -> None:
    """Log a compact summary of the collected context pack."""
    umo = getattr(event, "unified_msg_origin", None) if event else None
    logger.info(
        "Prompt context pack collected: umo=%s catalog=%s collectors=%s slot_count=%s",
        umo,
        pack.meta.get("catalog_version"),
        pack.meta.get("collectors"),
        pack.meta.get("slot_count", len(pack.slots)),
    )

    if not pack.slots:
        return

    for slot_name in sorted(pack.slots):
        slot: ContextSlot = pack.slots[slot_name]
        logger.debug(
            "Prompt context slot: name=%s category=%s source=%s meta=%s value=%s",
            slot.name,
            slot.category,
            slot.source,
            slot.meta,
            _stringify_value_preview(slot.value),
        )
