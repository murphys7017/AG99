from __future__ import annotations

import asyncio
from collections.abc import Iterable
from contextlib import contextmanager
from copy import copy, deepcopy
from typing import Any

from astrbot import logger
from astrbot.core.message.components import File, Image, Reply
from astrbot.core.prompt.builder import PromptContextBuilder
from astrbot.core.prompt.collectors import ConversationHistoryCollector
from astrbot.core.prompt.collectors.input_collector import InputCollector
from astrbot.core.prompt.collectors.persona_collector import PersonaCollector
from astrbot.core.prompt.context_catalog import get_catalog
from astrbot.core.prompt.context_collect import (
    build_prompt_extension_slots,
)
from astrbot.core.prompt.context_types import ContextPack, ContextSlot
from astrbot.core.prompt.extensions import PromptExtension
from astrbot.core.prompt.interfaces.context_collector_inferface import (
    ContextCollectorInterface,
)
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from .collectors import InteractionMemoryCollector
from .contributors import (
    InteractionDecisionView,
    PromptViewPhase,
    PromptViewPurpose,
)
from .memory_store import InteractionMemoryStore
from .turn_state import InteractionContextMaterial, get_interaction_turn_state


class InteractionPromptContributorError(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def build_interaction_collectors(
    memory_store: InteractionMemoryStore,
) -> list[ContextCollectorInterface]:
    """Collect provider-aware input to enrich the shared turn context."""
    return [
        InputCollector(),
    ]


def build_router_collectors() -> list[ContextCollectorInterface]:
    """Router 专用 collectors：仅输入内容。"""
    return [InputCollector()]


async def build_interaction_context_pack(
    event,
    plugin_context: Context,
    config,
    memory_store: InteractionMemoryStore,
) -> ContextPack:
    return await build_persona_context_pack(
        event,
        plugin_context,
        config,
        memory_store,
    )


async def build_router_context_pack(
    event,
    plugin_context: Context,
    config,
    memory_store: InteractionMemoryStore | None = None,
) -> ContextPack:
    """Build the shared lightweight turn context used first by Router."""
    input_pack = build_minimal_router_context_pack(
        event,
        provider_request=event.get_extra("provider_request"),
    )
    provider_request = event.get_extra("provider_request")
    router_collectors: list[ContextCollectorInterface] = [
        PersonaCollector(),
        ConversationHistoryCollector(),
    ]
    if memory_store is not None:
        router_collectors.append(InteractionMemoryCollector(memory_store))
    source_pack = await PromptContextBuilder(event, plugin_context, config).build(
        collectors=router_collectors,
        provider_request=provider_request,
        include_prompt_extensions=True,
        base=input_pack,
        scope="interaction_base",
    )
    attachment_summary = _build_router_attachment_summary(source_pack)
    if attachment_summary:
        source_pack.add_slot(
            ContextSlot(
                name="input.router_attachment_summary",
                value=attachment_summary,
                category="input",
                source="interaction_router",
                render_mode="structured",
            )
        )
        source_pack.meta["slot_count"] = len(source_pack.slots)
    turn_state = get_interaction_turn_state(event)
    if turn_state is not None:
        material = turn_state.context_material or InteractionContextMaterial()
        material.prompt_context_pack = source_pack
        material.persona_payload = extract_persona_payload(source_pack)
        material.memory_payload = extract_interaction_memory_payload(source_pack)
        material.recent_messages = extract_recent_messages(source_pack, 0)
        material.input_payload = extract_input_payload(source_pack)
        material.capability_payload = build_core_capability_payload(
            plugin_context,
            event,
        )
        material.decision_context = {
            "persona": material.persona_payload,
            "memory": material.memory_payload,
            "recent_messages": material.recent_messages,
            "input": material.input_payload,
            "core_capabilities": material.capability_payload,
        }
        material.collected_scopes.add("interaction_base")
        turn_state.context_material = material
        event.set_extra("_interaction_prompt_context_pack", source_pack)
    return source_pack


def build_minimal_router_context_pack(
    event,
    *,
    provider_request=None,
) -> ContextPack:
    """Build a cheap router input pack without resolving media or quoted payloads."""
    catalog = get_catalog(strict=True)
    pack = ContextPack(
        provider_request_ref=provider_request,
        meta={
            "catalog_version": catalog.version,
            "collectors": ["MinimalRouterInput"],
            "extension_collectors": [],
        },
    )
    text = (getattr(event, "message_str", "") or "").strip()
    if text:
        pack.add_slot(
            ContextSlot(
                name="input.text",
                value=text,
                category="input",
                source="event_input",
                meta={"source_field": "message_str", "router_minimal": True},
            )
        )

    images: list[dict[str, Any]] = []
    quoted_images: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    quoted_files: list[dict[str, Any]] = []
    for index, component in enumerate(getattr(event.message_obj, "message", []) or []):
        if isinstance(component, Image):
            images.append({"source": "current", "index": index})
            continue
        if isinstance(component, File):
            files.append({"source": "current", "index": index})
            continue
        if isinstance(component, Reply):
            for reply_index, reply_component in enumerate(component.chain or []):
                if isinstance(reply_component, Image):
                    quoted_images.append(
                        {
                            "source": "quoted",
                            "index": reply_index,
                            "reply_id": getattr(component, "id", None),
                        }
                    )
                elif isinstance(reply_component, File):
                    quoted_files.append(
                        {
                            "source": "quoted",
                            "index": reply_index,
                            "reply_id": getattr(component, "id", None),
                        }
                    )

    if images:
        pack.add_slot(
            ContextSlot(
                name="input.images",
                value=images,
                category="input",
                source="event_input",
                meta={"count": len(images), "source": "current", "router_minimal": True},
            )
        )
    if quoted_images:
        pack.add_slot(
            ContextSlot(
                name="input.quoted_images",
                value=quoted_images,
                category="input",
                source="quoted_message",
                meta={"count": len(quoted_images), "router_minimal": True},
            )
        )
    if files or quoted_files:
        pack.add_slot(
            ContextSlot(
                name="input.files",
                value=[*files, *quoted_files],
                category="input",
                source="event_input",
                meta={
                    "count": len(files) + len(quoted_files),
                    "quoted_count": len(quoted_files),
                    "router_minimal": True,
                },
            )
        )
    pack.meta["slot_count"] = len(pack.slots)
    return pack


def _build_router_attachment_summary(pack: ContextPack) -> dict[str, int]:
    slot_names = {
        "images": "input.images",
        "quoted_images": "input.quoted_images",
        "files": "input.files",
        "quoted_files": "input.quoted_files",
    }
    summary: dict[str, int] = {}
    for label, slot_name in slot_names.items():
        slot = pack.get_slot(slot_name)
        if slot is None:
            continue
        if isinstance(slot.value, list):
            count = len(slot.value)
        else:
            try:
                count = int(slot.meta.get("count", 0))
            except (TypeError, ValueError):
                count = 0
        if count > 0:
            summary[label] = count
    return summary


async def build_persona_context_pack(
    event,
    plugin_context: Context,
    config,
    memory_store: InteractionMemoryStore,
) -> ContextPack:
    """Enrich the shared turn context with full provider-aware input data."""
    turn_state = get_interaction_turn_state(event)
    base = None
    if turn_state is not None and turn_state.context_material is not None:
        base = turn_state.context_material.prompt_context_pack
    collectors: list[ContextCollectorInterface] = build_interaction_collectors(
        memory_store
    )
    include_prompt_extensions = False
    scope = "persona_input"
    if base is None:
        collectors = [
            PersonaCollector(),
            ConversationHistoryCollector(),
            InteractionMemoryCollector(memory_store),
            *collectors,
        ]
        include_prompt_extensions = True
        scope = "interaction_full"
    return await PromptContextBuilder(event, plugin_context, config).build(
        provider_request=event.get_extra("provider_request"),
        collectors=collectors,
        include_prompt_extensions=include_prompt_extensions,
        base=base,
        replace_slots={
            "input.text",
            "input.quoted_text",
            "input.images",
            "input.quoted_images",
            "input.image_captions",
            "input.quoted_image_captions",
            "input.files",
            "input.file_extracts",
        },
        scope=scope,
    )


def build_prompt_render_provider_request(event, provider) -> ProviderRequest:
    """Build a branch-local render request without mutating shared event extras."""
    source = event.get_extra("provider_request")
    request = copy(source) if isinstance(source, ProviderRequest) else ProviderRequest()
    request.provider = provider
    return request


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
    get_tool_manager = getattr(plugin_context, "get_llm_tool_manager", None)
    tool_manager = get_tool_manager() if callable(get_tool_manager) else None
    provider_tools = getattr(tool_manager, "func_list", []) or []
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
        "knowledge_base_available": bool(getattr(plugin_context, "kb_manager", None)),
        "subagent_available": getattr(plugin_context, "subagent_orchestrator", None)
        is not None,
        "platform_id": event.get_platform_id(),
    }


def clone_interaction_context_pack(pack: ContextPack) -> ContextPack:
    return ContextPack(
        slots=deepcopy(pack.slots),
        provider_request_ref=pack.provider_request_ref,
        meta=deepcopy(pack.meta),
    )


@contextmanager
def temporary_event_extra(event, key: str, value: Any):
    extras = getattr(event, "_extras", None)
    if not isinstance(extras, dict):
        event.set_extra(key, value)
        try:
            yield
        finally:
            event.set_extra(key, None)
        return

    sentinel = object()
    previous = extras.get(key, sentinel)
    event.set_extra(key, value)
    try:
        yield
    finally:
        if previous is sentinel:
            extras.pop(key, None)
        else:
            event.set_extra(key, previous)


async def collect_interaction_prompt_extensions(
    event,
    plugin_context: Context,
    config,
    decision_context: dict[str, Any],
    *,
    purpose: PromptViewPurpose = "unknown",
    phase: PromptViewPhase = "unknown",
) -> list[PromptExtension]:
    extensions: list[PromptExtension] = []
    view = _build_decision_view(
        event=event,
        config=config,
        decision_context=decision_context,
        purpose=purpose,
        phase=phase,
    ).copy_read_only()
    contributors = list(plugin_context.list_interaction_prompt_contributors())
    raw_timeout = (
        config.get("contributor_timeout", 1.0)
        if isinstance(config, dict)
        else getattr(config, "contributor_timeout", 1.0)
    )
    try:
        timeout = max(0.1, float(raw_timeout or 1.0))
    except (TypeError, ValueError):
        timeout = 1.0

    async def _collect_one(contributor):
        plugin_id = str(getattr(contributor, "plugin_id", "<unknown>") or "<unknown>")
        try:
            payload = await asyncio.wait_for(
                contributor.collect(event, plugin_context, view),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            error = f"timeout after {timeout:.2f}s"
            _record_interaction_prompt_contributor_failure(
                event,
                plugin_id=plugin_id,
                error=error,
            )
            return InteractionPromptContributorError(
                "collector_timeout",
                f"Interaction prompt contributor timed out: plugin_id={plugin_id} timeout={timeout:.2f}s",
            )
        except Exception as exc:  # noqa: BLE001
            _record_interaction_prompt_contributor_failure(
                event,
                plugin_id=plugin_id,
                error=str(exc),
            )
            return InteractionPromptContributorError(
                "collector_failed",
                f"Interaction prompt contributor failed: plugin_id={plugin_id} error={exc}",
            )

        try:
            contributor_extensions = _normalize_interaction_prompt_extensions(payload)
            build_prompt_extension_slots(
                contributor_extensions,
                source="interaction_prompt_contributors",
            )
        except (InteractionPromptContributorError, ValueError) as exc:
            _record_interaction_prompt_contributor_failure(
                event,
                plugin_id=plugin_id,
                error=str(exc),
            )
            return InteractionPromptContributorError("invalid_payload", str(exc))
        return contributor_extensions

    results = await asyncio.gather(
        *[_collect_one(contributor) for contributor in contributors],
    )
    for result in results:
        if isinstance(result, InteractionPromptContributorError):
            raise result
        extensions.extend(result)

    extensions.sort(key=lambda item: (item.order, item.plugin_id))
    return extensions


async def get_or_collect_interaction_prompt_extensions(
    event,
    plugin_context: Context,
    config,
    decision_context: dict[str, Any],
    material: InteractionContextMaterial,
    *,
    purpose: PromptViewPurpose,
    phase: PromptViewPhase = "unknown",
) -> list[PromptExtension]:
    cache_key = f"{purpose}:{phase}"
    cached_extensions = material.prompt_extensions_by_purpose.get(cache_key)
    if cached_extensions is not None:
        return cached_extensions
    extensions = await collect_interaction_prompt_extensions(
        event,
        plugin_context,
        config,
        decision_context,
        purpose=purpose,
        phase=phase,
    )
    material.prompt_extensions_by_purpose[cache_key] = extensions
    material.prompt_extensions_collected = True
    return extensions


def append_interaction_prompt_extensions_to_pack(
    pack: ContextPack,
    extensions: list[PromptExtension],
) -> None:
    if not extensions:
        return
    targeted_extensions = []
    for extension in extensions:
        targeted = deepcopy(extension)
        targeted.meta = dict(targeted.meta)
        targeted.meta.setdefault("targets", ["persona"])
        targeted_extensions.append(targeted)
    slots = build_prompt_extension_slots(
        targeted_extensions,
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
    purpose: PromptViewPurpose,
    phase: PromptViewPhase,
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
    use_material = material is not None and purpose != "router"
    return InteractionDecisionView(
        turn_id=str(event.get_extra("_turn_id", "") or ""),
        platform_id=platform_id,
        session_id=session_id,
        purpose=purpose,
        phase=phase,
        config=config,
        decision_context=context,
        persona=(
            material.persona_payload
            if use_material
            else dict(context.get("persona", {}) or {})
        ),
        input=(
            material.input_payload
            if use_material
            else dict(context.get("input", {}) or {})
        ),
        interaction_memory=(
            material.memory_payload
            if use_material
            else dict(context.get("memory", {}) or {})
        ),
        recent_messages=(
            material.recent_messages
            if use_material
            else list(context.get("recent_messages", []) or [])
        ),
        capabilities=(
            material.capability_payload
            if use_material
            else dict(context.get("core_capabilities", {}) or {})
        ),
        metadata={"prompt_context_cached": use_material},
    )
