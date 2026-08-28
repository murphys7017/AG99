from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from copy import copy, deepcopy
from typing import Any

from astrbot import logger
from astrbot.core.prompt.builder import PromptContextBuilder
from astrbot.core.prompt.context_collect import (
    build_prompt_extension_slots,
    interaction_base_collectors,
)
from astrbot.core.prompt.context_types import ContextPack, ContextSlot
from astrbot.core.prompt.extensions import PromptExtension
from astrbot.core.prompt.interfaces import ContextCollectorInterface
from astrbot.core.prompt.strict_mode import is_prompt_pipeline_strict
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from .contributors import (
    InteractionPromptPurpose,
    InteractionPromptView,
    PromptViewPhase,
)
from .persona_domain import adapt_persona_collector_slots
from .turn_state import (
    InteractionContextMaterial,
    InteractionTurnState,
    get_interaction_turn_state,
)
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
) -> ContextPack:
    builder = PromptContextBuilder(event, plugin_context, config)
    base_pack = await builder.build(
        provider_request=event.get_extra("provider_request"),
        collectors=interaction_base_collectors(),
        include_prompt_extensions=True,
        prompt_extension_collector_scope="control_plane",
        scope="interaction_base",
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
) -> InteractionContextMaterial:
    turn_state = get_interaction_turn_state(event)
    if turn_state is not None:
        turn_state.prompt_build_config = build_config
        material = turn_state.context_material
        if material is not None:
            _refresh_context_material_view(material, interaction_config)
            return material

        build_task = turn_state.context_material_task
        if build_task is None:
            build_task = turn_state.execution_scope.create_task(
                _build_interaction_context_material(
                    event=event,
                    plugin_context=plugin_context,
                    interaction_config=interaction_config,
                    build_config=build_config,
                ),
                role="context_material",
                name=(
                    f"interaction_context_material_"
                    f"{event.get_platform_id()}_{turn_state.turn_id}"
                ),
            )
            turn_state.context_material_task = build_task
            build_task.add_done_callback(
                lambda done_task: _finish_context_material_task(
                    turn_state,
                    done_task,
                )
            )
        return await asyncio.shield(build_task)

    return await _build_interaction_context_material(
        event=event,
        plugin_context=plugin_context,
        interaction_config=interaction_config,
        build_config=build_config,
    )


async def get_or_build_interaction_persona_context_pack(
    *,
    event,
    plugin_context: Context,
    interaction_config: InteractionAgentConfig,
    build_config: InteractionPromptBuildConfig,
    material: InteractionContextMaterial | None = None,
) -> ContextPack:
    material = material or await get_or_build_interaction_context_material(
        event=event,
        plugin_context=plugin_context,
        interaction_config=interaction_config,
        build_config=build_config,
    )
    base_context_pack = material.prompt_context_pack
    if base_context_pack is None:
        raise RuntimeError("Interaction base context pack is unavailable")

    cached = material.target_context_packs.get("plugin")
    if cached is not None:
        _log_persona_context_selection(
            event,
            plugin_status="ready",
            selected_context="plugin",
        )
        return cached

    if interaction_config.persona_plugin_context_mode == "best_effort":
        build_task = material.target_context_tasks.get("plugin")
        if build_task is None:
            if get_interaction_turn_state(event) is None:
                _log_persona_context_selection(
                    event,
                    plugin_status="unowned",
                    selected_context="base_fallback",
                )
                return base_context_pack
            build_task = _ensure_interaction_plugin_context_pack_task(
                event=event,
                plugin_context=plugin_context,
                build_config=build_config,
                material=material,
            )
        if not build_task.done():
            _log_persona_context_selection(
                event,
                plugin_status="pending",
                selected_context="base_fallback",
            )
            return base_context_pack
        if build_task.cancelled():
            _log_persona_context_selection(
                event,
                plugin_status="cancelled",
                selected_context="base_fallback",
            )
            return base_context_pack
        exception = build_task.exception()
        if exception is not None:
            _log_persona_context_selection(
                event,
                plugin_status="failed",
                selected_context="base_fallback",
                error_type=type(exception).__name__,
            )
            return base_context_pack
        context_pack = build_task.result()
        _log_persona_context_selection(
            event,
            plugin_status="ready",
            selected_context="plugin",
        )
        return context_pack

    _log_persona_context_selection(
        event,
        plugin_status="pending",
        selected_context="plugin_wait",
    )
    context_pack = await _get_or_build_interaction_plugin_context_pack(
        event=event,
        plugin_context=plugin_context,
        build_config=build_config,
        material=material,
    )

    _log_persona_context_selection(
        event,
        plugin_status="ready",
        selected_context="plugin",
    )
    return context_pack


async def get_or_build_interaction_core_context_pack(
    *,
    event,
    plugin_context: Context,
    build_config: object,
    material: InteractionContextMaterial,
) -> ContextPack:
    return await _get_or_build_interaction_plugin_context_pack(
        event=event,
        plugin_context=plugin_context,
        build_config=build_config,
        material=material,
    )


async def _get_or_build_interaction_plugin_context_pack(
    *,
    event,
    plugin_context: Context,
    build_config: object,
    material: InteractionContextMaterial,
) -> ContextPack:
    cached = material.target_context_packs.get("plugin")
    if cached is not None:
        return cached

    build_task = material.target_context_tasks.get("plugin")
    if build_task is None and get_interaction_turn_state(event) is None:
        return await _build_interaction_plugin_context_pack(
            event=event,
            plugin_context=plugin_context,
            build_config=build_config,
            material=material,
        )

    build_task = _ensure_interaction_plugin_context_pack_task(
        event=event,
        plugin_context=plugin_context,
        build_config=build_config,
        material=material,
    )
    return await asyncio.shield(build_task)


def _ensure_interaction_plugin_context_pack_task(
    *,
    event,
    plugin_context: Context,
    build_config: object,
    material: InteractionContextMaterial,
) -> asyncio.Task[ContextPack]:
    build_task = material.target_context_tasks.get("plugin")
    if build_task is None:
        turn_state = get_interaction_turn_state(event)
        if turn_state is None:
            raise RuntimeError(
                "Interaction plugin context prefetch requires a turn execution scope"
            )
        awaitable = _build_interaction_plugin_context_pack(
            event=event,
            plugin_context=plugin_context,
            build_config=build_config,
            material=material,
        )
        build_task = turn_state.execution_scope.create_task(
            awaitable,
            role="context_plugin",
            name=(
                f"interaction_context_plugin_"
                f"{event.get_platform_id()}_{turn_state.turn_id}"
            ),
        )
        material.target_context_tasks["plugin"] = build_task
        build_task.add_done_callback(
            lambda done_task: _finish_context_target_task(
                material,
                "plugin",
                done_task,
            )
        )
    return build_task


def _log_persona_context_selection(
    event,
    *,
    plugin_status: str,
    selected_context: str,
    error_type: str = "",
) -> None:
    logger.info(
        "DIAG interaction.persona_context: platform_id=%s session_id=%s plugin_status=%s selected_context=%s error_type=%s",
        event.get_platform_id(),
        event.session_id,
        plugin_status,
        selected_context,
        error_type,
    )


def _finish_context_material_task(
    turn_state: InteractionTurnState,
    task: asyncio.Task[InteractionContextMaterial],
) -> None:
    if turn_state.context_material_task is task:
        turn_state.context_material_task = None
    if task.cancelled():
        return
    task.exception()


def _finish_context_target_task(
    material: InteractionContextMaterial,
    target: str,
    task: asyncio.Task[ContextPack],
) -> None:
    if task.cancelled():
        return
    exception = task.exception()
    if exception is not None:
        return
    if material.target_context_tasks.get(target) is task:
        material.target_context_tasks.pop(target, None)


async def _build_interaction_context_material(
    *,
    event,
    plugin_context: Context,
    interaction_config: InteractionAgentConfig,
    build_config: InteractionPromptBuildConfig,
) -> InteractionContextMaterial:
    turn_state = get_interaction_turn_state(event)
    started_at = time.monotonic()

    prompt_context_pack = await build_interaction_context_pack(
        event,
        plugin_context,
        build_config,
    )
    capability_payload = extract_core_capability_payload(prompt_context_pack)
    material = InteractionContextMaterial(
        prompt_context_pack=prompt_context_pack,
        persona_payload=extract_persona_payload(prompt_context_pack),
        persona_definition=adapt_persona_collector_slots(
            tuple(prompt_context_pack.slots.values())
        ),
        memory_payload=extract_memory_payload(prompt_context_pack),
        recent_messages=extract_recent_messages(
            prompt_context_pack,
            interaction_config.memory_window_size,
        ),
        input_payload=extract_input_payload(prompt_context_pack),
        capability_payload=capability_payload,
        collected_scopes=set(
            prompt_context_pack.meta.get("collection_scopes", ["interaction_base"])
        ),
    )
    _refresh_context_material_view(material, interaction_config)
    if turn_state is not None:
        turn_state.context_material = material
        _ensure_interaction_plugin_context_pack_task(
            event=event,
            plugin_context=plugin_context,
            build_config=build_config,
            material=material,
        )
    logger.info(
        "DIAG interaction.context_material: platform_id=%s session_id=%s scope=base duration_ms=%.2f slot_count=%s extension_collectors=%s",
        event.get_platform_id(),
        event.session_id,
        (time.monotonic() - started_at) * 1000,
        len(prompt_context_pack.slots),
        prompt_context_pack.meta.get("extension_collectors", []),
    )
    return material


async def _build_interaction_plugin_context_pack(
    *,
    event,
    plugin_context: Context,
    build_config: object,
    material: InteractionContextMaterial,
) -> ContextPack:
    started_at = time.monotonic()
    base_extension_collectors = set(
        material.prompt_context_pack.meta.get("extension_collectors", [])
    )
    prompt_context_pack = await PromptContextBuilder(
        event,
        plugin_context,
        build_config,
    ).build(
        provider_request=event.get_extra("provider_request"),
        collectors=[
            InteractionPromptContributorCollector(material.context_snapshot),
        ],
        include_prompt_extensions=True,
        prompt_extension_collector_scope="plugin",
        base=material.prompt_context_pack,
        scope="interaction_plugin_context",
    )
    material.target_context_packs["plugin"] = prompt_context_pack
    plugin_extension_collectors = [
        collector_name
        for collector_name in prompt_context_pack.meta.get(
            "extension_collectors",
            [],
        )
        if collector_name not in base_extension_collectors
    ]
    logger.info(
        "DIAG interaction.context_material: platform_id=%s session_id=%s scope=plugin duration_ms=%.2f slot_count=%s extension_collectors=%s",
        event.get_platform_id(),
        event.session_id,
        (time.monotonic() - started_at) * 1000,
        len(prompt_context_pack.slots),
        plugin_extension_collectors,
    )
    return prompt_context_pack


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
    history_slot = pack.get_slot("conversation.history")
    if history_slot is None or not isinstance(history_slot.value, dict):
        return []
    turns = history_slot.value.get("turns", [])
    if not isinstance(turns, list):
        return []
    messages = [dict(turn) for turn in turns if isinstance(turn, dict)]
    return messages[-limit:] if limit > 0 else messages


def extract_persona_payload(pack: ContextPack) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for slot_name in ("persona.prompt", "persona.segments", "persona.begin_dialogs"):
        slot = pack.get_slot(slot_name)
        if slot is None:
            continue
        payload[slot_name.split(".", 1)[1]] = slot.value

        if slot_name == "persona.prompt" and isinstance(slot.meta, dict):
            persona_id = slot.meta.get("persona_id")
            if persona_id is not None:
                payload["persona_id"] = str(persona_id)
            if "force_applied" in slot.meta:
                payload["force_applied"] = bool(slot.meta["force_applied"])
            if "use_webchat_special_default" in slot.meta:
                payload["webchat_special_default"] = bool(
                    slot.meta["use_webchat_special_default"]
                )
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


def extract_memory_payload(pack: ContextPack) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for slot_name, slot in pack.slots.items():
        if slot_name.startswith("memory."):
            payload[slot_name.split(".", 1)[1]] = slot.value
    return payload


def extract_core_capability_payload(pack: ContextPack) -> dict[str, Any]:
    tools_slot = pack.get_slot("capability.tools_schema")
    tools_value = tools_slot.value if tools_slot is not None else {}
    tools = tools_value.get("tools", []) if isinstance(tools_value, dict) else []
    tool_names = [
        str(tool.get("name", "")).strip()
        for tool in tools
        if isinstance(tool, dict) and str(tool.get("name", "")).strip()
    ]
    return {
        "tools_available": bool(tool_names),
        "tool_count": len(tool_names),
        "sample_tools": tool_names[:12],
        "tool_selection_mode": (
            str(tools_slot.meta.get("selection_mode", "unavailable"))
            if tools_slot is not None
            else "unavailable"
        ),
        "knowledge_available": pack.get_slot("knowledge.snippets") is not None,
        "subagent_available": pack.get_slot("capability.subagent_handoff_tools")
        is not None,
    }


async def collect_interaction_prompt_extensions(
    event,
    plugin_context: Context,
    config,
    context_snapshot: dict[str, Any],
) -> list[PromptExtension]:
    extensions: list[PromptExtension] = []
    contributor_timeout = max(
        0.1,
        float(getattr(config, "contributor_timeout", 1.0) or 1.0),
    )
    strict = is_prompt_pipeline_strict(config)
    view = _build_prompt_view(
        event=event,
        config=config,
        context_snapshot=context_snapshot,
        purpose="context_collection",
        phase="collect",
    ).copy_read_only()
    contributors = list(plugin_context.list_interaction_prompt_contributors())

    async def _collect_one(contributor):
        plugin_id = str(getattr(contributor, "plugin_id", "<unknown>") or "<unknown>")
        try:
            payload = await asyncio.wait_for(
                contributor.collect(event, plugin_context, view),
                timeout=contributor_timeout,
            )
        except TimeoutError:
            error = f"timeout_after_{contributor_timeout:.3f}s"
            _record_interaction_prompt_contributor_failure(
                event,
                plugin_id=plugin_id,
                error=error,
            )
            return InteractionPromptContributorError(
                "collector_timeout",
                "Interaction prompt contributor timed out: "
                f"plugin_id={plugin_id} timeout={contributor_timeout:.3f}s",
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
    failures = [
        result
        for result in results
        if isinstance(result, InteractionPromptContributorError)
    ]
    if strict and failures:
        raise failures[0]
    for result in results:
        if isinstance(result, InteractionPromptContributorError):
            continue
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
        memory=dict(context.get("memory", {}) or {}),
        recent_messages=list(context.get("recent_messages", []) or []),
        capabilities=dict(context.get("core_capabilities", {}) or {}),
        metadata={"canonical_context": True},
    )
