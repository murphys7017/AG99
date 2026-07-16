from __future__ import annotations

import asyncio
from collections.abc import Iterable
from copy import copy, deepcopy
from typing import Any

from astrbot import logger
from astrbot.core.prompt.builder import PromptContextBuilder
from astrbot.core.prompt.collectors import ConversationHistoryCollector
from astrbot.core.prompt.collectors.input_collector import InputCollector
from astrbot.core.prompt.collectors.persona_collector import PersonaCollector
from astrbot.core.prompt.collectors.session_collector import SessionCollector
from astrbot.core.prompt.context_collect import (
    build_prompt_extension_slots,
)
from astrbot.core.prompt.context_types import ContextPack, ContextSlot
from astrbot.core.prompt.extensions import PromptExtension
from astrbot.core.prompt.interfaces import ContextCollectorInterface
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from .collectors import InteractionCapabilityCollector, InteractionMemoryCollector
from .contributors import (
    InteractionPromptPurpose,
    InteractionPromptView,
    PromptViewPhase,
)
from .memory_store import InteractionMemoryStore
from .turn_state import InteractionContextMaterial, get_interaction_turn_state
from .types import InteractionAgentConfig, InteractionPromptBuildConfig


class InteractionPromptContributorError(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


class AttachmentSummaryCollector(ContextCollectorInterface):
    def __init__(self, source_pack: ContextPack) -> None:
        self.source_pack = source_pack

    async def collect(
        self,
        event,
        plugin_context,
        config,
        provider_request=None,
    ) -> list[ContextSlot]:
        del event, plugin_context, config, provider_request
        summary = _build_attachment_summary(self.source_pack)
        if not summary:
            return []
        return [
            ContextSlot(
                name="input.attachment_summary",
                value=summary,
                category="input",
                source="interaction_attachment_summary",
                render_mode="structured",
                meta={"scope": "derived"},
            )
        ]


class InteractionPromptContributorCollector(ContextCollectorInterface):
    def __init__(self, context_snapshot: dict[str, Any]) -> None:
        self.context_snapshot = context_snapshot

    async def collect(
        self,
        event,
        plugin_context,
        config,
        provider_request=None,
    ) -> list[ContextSlot]:
        del provider_request
        extensions = await collect_interaction_prompt_extensions(
            event,
            plugin_context,
            config,
            self.context_snapshot,
        )
        targeted_extensions: list[PromptExtension] = []
        for extension in extensions:
            targeted = deepcopy(extension)
            targeted.meta = dict(targeted.meta)
            targeted.meta.setdefault("targets", ["persona"])
            targeted_extensions.append(targeted)
        return build_prompt_extension_slots(
            targeted_extensions,
            source="interaction_prompt_contributors",
        )


async def build_interaction_context_pack(
    event,
    plugin_context: Context,
    config,
    memory_store: InteractionMemoryStore,
) -> ContextPack:
    builder = PromptContextBuilder(event, plugin_context, config)
    base_pack = await builder.build(
        provider_request=event.get_extra("provider_request"),
        collectors=[
            InputCollector(),
            PersonaCollector(),
            SessionCollector(),
            ConversationHistoryCollector(),
            InteractionMemoryCollector(memory_store),
            InteractionCapabilityCollector(),
        ],
        include_prompt_extensions=True,
        scope="interaction_full",
    )
    return await builder.build(
        provider_request=event.get_extra("provider_request"),
        collectors=[AttachmentSummaryCollector(base_pack)],
        include_prompt_extensions=False,
        base=base_pack,
        scope="interaction_derived",
    )


def _build_attachment_summary(pack: ContextPack) -> dict[str, int]:
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


async def get_or_build_interaction_context_material(
    *,
    event,
    plugin_context: Context,
    interaction_config: InteractionAgentConfig,
    build_config: InteractionPromptBuildConfig,
    memory_store: InteractionMemoryStore,
) -> InteractionContextMaterial:
    turn_state = get_interaction_turn_state(event)
    if turn_state is not None:
        turn_state.prompt_build_config = build_config
        material = turn_state.context_material
        if material is not None:
            _refresh_context_material_view(material, interaction_config)
            _publish_context_material(event, material)
            return material

    prompt_context_pack = await build_interaction_context_pack(
        event,
        plugin_context,
        build_config,
        memory_store,
    )
    capability_payload = extract_core_capability_payload(prompt_context_pack)
    material = InteractionContextMaterial(
        prompt_context_pack=prompt_context_pack,
        persona_payload=extract_persona_payload(prompt_context_pack),
        memory_payload=extract_interaction_memory_payload(prompt_context_pack),
        recent_messages=extract_recent_messages(
            prompt_context_pack,
            interaction_config.memory_window_size,
        ),
        input_payload=extract_input_payload(prompt_context_pack),
        capability_payload=capability_payload,
        collected_scopes=set(
            prompt_context_pack.meta.get("collection_scopes", ["interaction_full"])
        ),
    )
    _refresh_context_material_view(material, interaction_config)
    prompt_context_pack = await PromptContextBuilder(
        event,
        plugin_context,
        build_config,
    ).build(
        provider_request=event.get_extra("provider_request"),
        collectors=[
            InteractionPromptContributorCollector(material.context_snapshot),
        ],
        include_prompt_extensions=False,
        base=prompt_context_pack,
        scope="interaction_contributors",
    )
    material.prompt_context_pack = prompt_context_pack
    material.collected_scopes.add("interaction_contributors")
    _publish_context_material(event, material)
    if turn_state is not None:
        turn_state.context_material = material
    return material


def _refresh_context_material_view(
    material: InteractionContextMaterial,
    interaction_config: InteractionAgentConfig,
) -> None:
    recent_messages = material.recent_messages
    if interaction_config.memory_window_size > 0:
        recent_messages = recent_messages[-interaction_config.memory_window_size :]
    material.recent_messages = recent_messages
    material.context_snapshot = {
        "persona": material.persona_payload,
        "memory": material.memory_payload,
        "recent_messages": recent_messages,
        "input": material.input_payload,
        "core_capabilities": material.capability_payload,
    }


def _publish_context_material(
    event,
    material: InteractionContextMaterial,
) -> None:
    event.set_extra("_interaction_prompt_context_pack", material.prompt_context_pack)
    event.set_extra("_interaction_context_snapshot", material.context_snapshot)


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


def extract_core_capability_payload(pack: ContextPack) -> dict[str, Any]:
    slot = pack.get_slot("capability.core_summary")
    if slot is None or not isinstance(slot.value, dict):
        return {}
    return slot.value


async def collect_interaction_prompt_extensions(
    event,
    plugin_context: Context,
    config,
    context_snapshot: dict[str, Any],
) -> list[PromptExtension]:
    extensions: list[PromptExtension] = []
    view = _build_prompt_view(
        event=event,
        config=config,
        context_snapshot=context_snapshot,
        purpose="context_collection",
        phase="collect",
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


def _build_prompt_view(
    *,
    event,
    config,
    context_snapshot: dict[str, Any],
    purpose: InteractionPromptPurpose,
    phase: PromptViewPhase,
) -> InteractionPromptView:
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
    context = context_snapshot if isinstance(context_snapshot, dict) else {}
    return InteractionPromptView(
        turn_id=str(event.get_extra("_turn_id", "") or ""),
        platform_id=platform_id,
        session_id=session_id,
        purpose=purpose,
        phase=phase,
        config=config,
        context_snapshot=context,
        persona=dict(context.get("persona", {}) or {}),
        input=dict(context.get("input", {}) or {}),
        interaction_memory=dict(context.get("memory", {}) or {}),
        recent_messages=list(context.get("recent_messages", []) or []),
        capabilities=dict(context.get("core_capabilities", {}) or {}),
        metadata={"canonical_context": True},
    )
