"""Canonical prompt context construction and enrichment."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass

from astrbot.core.capabilities import CapabilitySnapshot
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from .context_collect import PromptExtensionCollectorScope, collect_context_pack
from .context_types import ContextPack, ContextSlot, PromptContextConflictError
from .interfaces import ContextCollectorInterface


@dataclass(slots=True)
class PromptContextBuilder:
    """Collect structured facts and build versioned ContextPack snapshots."""

    event: AstrMessageEvent
    plugin_context: Context
    config: object

    async def build(
        self,
        *,
        collectors: Iterable[ContextCollectorInterface] | None = None,
        provider_request: ProviderRequest | None = None,
        capabilities: CapabilitySnapshot | None = None,
        include_prompt_extensions: bool = True,
        prompt_extension_collector_scope: PromptExtensionCollectorScope = "all",
        base: ContextPack | None = None,
        replace_slots: Iterable[str] = (),
        scope: str = "default",
    ) -> ContextPack:
        fragment = await collect_context_pack(
            event=self.event,
            plugin_context=self.plugin_context,
            config=self.config,
            provider_request=provider_request,
            collectors=collectors,
            capabilities=capabilities,
            include_prompt_extensions=include_prompt_extensions,
            prompt_extension_collector_scope=prompt_extension_collector_scope,
        )
        return merge_context_packs(
            base,
            fragment,
            replace_slots=frozenset(replace_slots),
            scope=scope,
        )


def merge_context_packs(
    base: ContextPack | None,
    fragment: ContextPack,
    *,
    replace_slots: frozenset[str] = frozenset(),
    scope: str = "default",
) -> ContextPack:
    """Return a new snapshot; never mutate either source pack."""

    if base is None:
        merged = ContextPack(
            slots=deepcopy(fragment.slots),
            provider_request_ref=fragment.provider_request_ref,
            meta=deepcopy(fragment.meta),
        )
        merged.meta["context_version"] = 1
        merged.meta["collection_scopes"] = [scope]
        merged.meta["slot_count"] = len(merged.slots)
        return merged

    merged = ContextPack(
        slots=deepcopy(base.slots),
        provider_request_ref=fragment.provider_request_ref or base.provider_request_ref,
        meta=deepcopy(base.meta),
    )
    _merge_pack_meta(merged.meta, fragment.meta)
    for slot in fragment.slots.values():
        existing = merged.get_slot(slot.name)
        if existing is None:
            merged.add_slot(deepcopy(slot))
            continue
        if slot.name in replace_slots:
            merged.add_slot(deepcopy(slot))
            continue
        if slot.name == "capability.plugin_directory" and _merge_plugin_directory_slot(
            existing,
            slot,
        ):
            continue
        if _slots_equal(existing, slot):
            continue
        if slot.name.startswith("extension.") and _merge_extension_slot(existing, slot):
            continue
        raise PromptContextConflictError(
            f"conflicting prompt context slot: {slot.name} "
            f"({existing.source} != {slot.source})"
        )

    scopes = list(merged.meta.get("collection_scopes", []))
    if scope not in scopes:
        scopes.append(scope)
    merged.meta["collection_scopes"] = scopes
    merged.meta["context_version"] = int(base.meta.get("context_version", 1)) + 1
    merged.meta["slot_count"] = len(merged.slots)
    return merged


def _slots_equal(left: ContextSlot, right: ContextSlot) -> bool:
    return left == right


def _merge_extension_slot(existing: ContextSlot, incoming: ContextSlot) -> bool:
    if not isinstance(existing.value, dict) or not isinstance(incoming.value, dict):
        return False
    existing_items = existing.value.get("items")
    incoming_items = incoming.value.get("items")
    if not isinstance(existing_items, list) or not isinstance(incoming_items, list):
        return False

    seen: set[tuple[str, str, str]] = set()
    merged_items: list[dict] = []
    for item in [*existing_items, *incoming_items]:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("plugin_id", "")),
            str(item.get("title", "")),
            repr(item.get("value")),
        )
        if key in seen:
            continue
        seen.add(key)
        merged_items.append(deepcopy(item))
    merged_items.sort(
        key=lambda item: (
            int(item.get("order", 100) or 100),
            str(item.get("plugin_id", "")),
        )
    )
    existing.value["items"] = merged_items
    existing.meta["item_count"] = len(merged_items)
    existing.meta["plugin_count"] = len(
        {
            str(item.get("plugin_id", ""))
            for item in merged_items
            if str(item.get("plugin_id", ""))
        }
    )
    return True


def _merge_plugin_directory_slot(
    existing: ContextSlot,
    incoming: ContextSlot,
) -> bool:
    if not isinstance(existing.value, dict) or not isinstance(incoming.value, dict):
        return False
    existing_plugins = existing.value.get("plugins")
    incoming_plugins = incoming.value.get("plugins")
    if not isinstance(existing_plugins, list) or not isinstance(incoming_plugins, list):
        return False

    merged_plugins: list[dict] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    entries = [
        *((item, existing.meta) for item in existing_plugins),
        *((item, incoming.meta) for item in incoming_plugins),
    ]
    for plugin, slot_meta in entries:
        if not isinstance(plugin, dict):
            continue
        normalized = deepcopy(plugin)
        raw_targets = normalized.get("targets", slot_meta.get("targets", []))
        targets = (
            sorted({str(target) for target in raw_targets})
            if isinstance(raw_targets, list | tuple | set)
            else []
        )
        if targets:
            normalized["targets"] = targets
        key = (
            str(normalized.get("name", "")),
            str(normalized.get("description", "")),
            tuple(targets),
        )
        if key in seen:
            continue
        seen.add(key)
        merged_plugins.append(normalized)

    existing.value["plugins"] = merged_plugins
    existing.meta.pop("targets", None)
    existing.meta["plugin_count"] = len(merged_plugins)
    return True


def _merge_pack_meta(target: dict, incoming: dict) -> None:
    list_keys = {
        "cached_collectors",
        "collector_failures",
        "collectors",
        "extension_collectors",
    }
    managed_keys = {"collection_scopes", "context_version", "slot_count"}
    for key, value in deepcopy(incoming).items():
        if key in managed_keys:
            continue
        if key not in list_keys or not isinstance(value, list):
            target[key] = value
            continue
        existing = target.get(key, [])
        merged = list(existing) if isinstance(existing, list) else []
        for item in value:
            if item not in merged:
                merged.append(item)
        target[key] = merged


__all__ = [
    "PromptContextBuilder",
    "PromptContextConflictError",
    "merge_context_packs",
]
