"""
Prompt context collection helpers.

This module wires collectors into a single fail-fast collection flow.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from astrbot.core import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.star.context import Context

from .collectors.conversation_history_collector import ConversationHistoryCollector
from .collectors.input_collector import InputCollector
from .collectors.knowledge_collector import KnowledgeCollector
from .collectors.memory_collector import MemoryCollector
from .collectors.persona_collector import PersonaCollector
from .collectors.policy_collector import PolicyCollector
from .collectors.session_collector import SessionCollector
from .collectors.skills_collector import SkillsCollector
from .collectors.subagent_collector import SubagentCollector
from .collectors.system_collector import SystemCollector
from .collectors.tools_collector import ToolsCollector
from .context_catalog import get_catalog
from .context_types import ContextPack, ContextSlot
from .extensions.types import (
    PROMPT_EXTENSION_MOUNTS,
    PROMPT_EXTENSION_VALUE_KINDS,
    PromptExtension,
)
from .interfaces.context_collector_inferface import ContextCollectorInterface

PROMPT_CONTEXT_PACK_EXTRA_KEY = "prompt_context_pack"
PROMPT_EXTENSION_SLOT_NAMES: dict[str, str] = {
    mount: f"extension.{mount}" for mount in PROMPT_EXTENSION_MOUNTS
}


def _default_collectors() -> list[ContextCollectorInterface]:
    """Return the collectors enabled for the current phase."""
    return [
        SystemCollector(),
        PersonaCollector(),
        InputCollector(),
        SessionCollector(),
        PolicyCollector(),
        MemoryCollector(),
        ConversationHistoryCollector(),
        SkillsCollector(),
        ToolsCollector(),
        SubagentCollector(),
        KnowledgeCollector(),
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


def build_prompt_extension_slots(
    extensions: Iterable[PromptExtension],
    *,
    source: str = "prompt_extension_collectors",
) -> list[ContextSlot]:
    grouped_items: dict[str, list[dict[str, object]]] = {
        mount: [] for mount in PROMPT_EXTENSION_MOUNTS
    }
    for extension in extensions:
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
        grouped_items[extension.mount].append(_build_prompt_extension_record(extension))

    slots: list[ContextSlot] = []
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


async def _collect_prompt_extension_slots(
    *,
    event: AstrMessageEvent,
    plugin_context: Context,
    config,
    provider_request,
) -> tuple[list[ContextSlot], list[str]]:
    list_collectors = getattr(plugin_context, "list_prompt_extension_collectors", None)
    if not callable(list_collectors):
        return [], []

    raw_collectors = list_collectors()
    try:
        collectors = list(raw_collectors or [])
    except TypeError:
        return [], []
    collectors.sort(
        key=lambda collector: (
            _coerce_prompt_extension_collector_priority(collector),
            collector.__class__.__name__,
        )
    )

    collected_extensions: list[PromptExtension] = []
    collector_names: list[str] = []

    for collector in collectors:
        collector_name = collector.__class__.__name__
        collector_names.append(collector_name)
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

            collected_extensions.append(extension)

    slots = build_prompt_extension_slots(
        collected_extensions,
        source="prompt_extension_collectors",
    )
    return slots, collector_names


async def collect_context_pack(
    *,
    event: AstrMessageEvent,
    plugin_context: Context,
    config,
    provider_request=None,
    collectors: Iterable[ContextCollectorInterface] | None = None,
    include_prompt_extensions: bool = True,
) -> ContextPack:
    """
    Collect prompt context into a single pack.

    This stage is fail-fast for internal collectors and does not mutate ProviderRequest.
    """
    catalog = get_catalog(strict=True)
    collector_list = (
        list(collectors) if collectors is not None else _default_collectors()
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

        slots = await collector.collect(
            event,
            plugin_context,
            config,
            provider_request=provider_request,
        )

        for slot in slots:
            if not catalog.has(slot.name):
                logger.warning(
                    "Prompt context slot is not declared in catalog: slot=%s collector=%s",
                    slot.name,
                    collector_name,
                )

            if pack.has_slot(slot.name):
                logger.warning(
                    "Prompt context slot overwritten: slot=%s collector=%s",
                    slot.name,
                    collector_name,
                )

            pack.add_slot(slot)

    if include_prompt_extensions:
        extension_slots, extension_collectors = await _collect_prompt_extension_slots(
            event=event,
            plugin_context=plugin_context,
            config=config,
            provider_request=provider_request,
        )
        pack.meta["extension_collectors"] = extension_collectors

        for slot in extension_slots:
            if not catalog.has(slot.name):
                logger.warning(
                    "Prompt context slot is not declared in catalog: slot=%s collector=%s",
                    slot.name,
                    "PromptExtensionCollectors",
                )

            if pack.has_slot(slot.name):
                logger.warning(
                    "Prompt context slot overwritten: slot=%s collector=%s",
                    slot.name,
                    "PromptExtensionCollectors",
                )

            pack.add_slot(slot)

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
