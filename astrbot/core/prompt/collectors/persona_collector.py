"""
人格信息收集器 - Context Data Layer (Phase 1)

本模块实现 PersonaCollector，负责收集人格相关的上下文信息。
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from astrbot.core import logger
from astrbot.core.persona_resolution import (
    build_event_persona_resolution_key,
    resolve_event_persona,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.prompt.resources import (
    CHATUI_SPECIAL_DEFAULT_PERSONA_PROMPT,
)
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextSlot
from ..interfaces.context_collector_inferface import ContextCollectorInterface
from ..persona_segments import parse_legacy_persona_prompt

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


_PERSONA_RESOLUTION_CACHE_EXTRA_KEY = "_prompt_persona_resolution_cache"


class PersonaCollector(ContextCollectorInterface):
    """
    人格信息收集器。

    收集以下 context items:
    - persona.prompt: 人格设定 prompt
    - persona.begin_dialogs: 开场对话（已处理）
    - persona.tools_whitelist: 工具白名单
    - persona.skills_whitelist: Skills 白名单
    """

    @property
    def lifecycle(self) -> str:
        return "static"

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        """
        收集人格相关的上下文信息。

        流程:
        1. 从 event 中获取 provider_request（如果有），从中获取 conversation
        2. 调用 persona_manager.resolve_selected_persona() 解析当前生效的 persona
        3. 从 persona 对象中提取 4 个 context items
        4. 构造 ContextSlot 列表返回
        """
        slots: list[ContextSlot] = []

        # Persona is a required collector. Let collection failures reach the
        # prompt pipeline instead of turning an unavailable persona into an
        # indistinguishable empty result.
        # 步骤 1: 获取 conversation
        req = provider_request or event.get_extra("provider_request")
        conversation_persona_id = None
        if req and hasattr(req, "conversation") and req.conversation:
            conversation_persona_id = req.conversation.persona_id

        # 步骤 2: 调用 persona_manager.resolve_selected_persona()
        persona_mgr = getattr(plugin_context, "persona_manager", None)
        if not persona_mgr:
            raise RuntimeError("PersonaManager not available")

        resolution_key = build_event_persona_resolution_key(
            event=event,
            persona_manager=persona_mgr,
            conversation_persona_id=conversation_persona_id,
            provider_settings=config.provider_settings,
        )
        cached_slots = _get_cached_resolution_slots(event, resolution_key)
        if cached_slots is not None:
            return cached_slots

        resolution = await resolve_event_persona(
            event=event,
            persona_manager=persona_mgr,
            conversation_persona_id=conversation_persona_id,
            provider_settings=config.provider_settings,
        )
        persona_id = resolution.persona_id
        persona = resolution.persona
        force_applied_persona_id = resolution.force_applied_persona_id
        use_webchat_special_default = resolution.use_webchat_special_default

        if not persona and not use_webchat_special_default:
            logger.debug(f"No persona resolved (persona_id={persona_id})")
            _store_resolution_slots(event, resolution_key, slots)
            return slots

        # 步骤 3: 提取并构造 ContextSlot

        # persona.prompt
        if use_webchat_special_default:
            slots.append(
                ContextSlot(
                    name="persona.prompt",
                    value=CHATUI_SPECIAL_DEFAULT_PERSONA_PROMPT,
                    category="persona",
                    source="persona_mgr",
                    meta={
                        "persona_id": persona_id,
                        "force_applied": force_applied_persona_id is not None,
                        "use_webchat_special_default": True,
                    },
                )
            )
        elif persona and "prompt" in persona and persona["prompt"]:
            slots.append(
                ContextSlot(
                    name="persona.prompt",
                    value=persona["prompt"],
                    category="persona",
                    source="persona_mgr",
                    meta={
                        "persona_id": persona_id,
                        "force_applied": force_applied_persona_id is not None,
                    },
                )
            )

        prompt_slot = next(
            (slot for slot in slots if slot.name == "persona.prompt"), None
        )
        if (
            prompt_slot
            and isinstance(prompt_slot.value, str)
            and prompt_slot.value.strip()
        ):
            persona_segments = parse_legacy_persona_prompt(prompt_slot.value)
            slots.append(
                ContextSlot(
                    name="persona.segments",
                    value=persona_segments,
                    category="persona",
                    source="persona_parser",
                    meta={
                        "persona_id": persona_id,
                        "source_slot": "persona.prompt",
                        "parser": "legacy_prompt_v1",
                    },
                )
            )
            slots.append(
                ContextSlot(
                    name="persona.summary",
                    value=_build_persona_summary(persona_segments),
                    category="persona",
                    source="persona_parser",
                    meta={
                        "persona_id": persona_id,
                        "source_slot": "persona.segments",
                        "format": "persona_summary_v1",
                    },
                )
            )

        # persona.begin_dialogs
        if (
            persona
            and "_begin_dialogs_processed" in persona
            and persona["_begin_dialogs_processed"]
        ):
            slots.append(
                ContextSlot(
                    name="persona.begin_dialogs",
                    value=persona["_begin_dialogs_processed"],
                    category="persona",
                    source="persona_mgr",
                    meta={
                        "persona_id": persona_id,
                        "raw_begin_dialogs": persona.get("begin_dialogs", []),
                    },
                )
            )

        # persona.tools_whitelist
        if persona and "tools" in persona:
            # tools 可能是 None（使用所有）、[]（不使用）、或列表
            slots.append(
                ContextSlot(
                    name="persona.tools_whitelist",
                    value=persona["tools"],
                    category="persona",
                    source="persona_mgr",
                    meta={
                        "persona_id": persona_id,
                        "semantics": "None=all, []=none, [...]=specific",
                    },
                )
            )

        # persona.skills_whitelist
        if persona and "skills" in persona:
            # skills 可能是 None（使用所有）、[]（不使用）、或列表
            slots.append(
                ContextSlot(
                    name="persona.skills_whitelist",
                    value=persona["skills"],
                    category="persona",
                    source="persona_mgr",
                    meta={
                        "persona_id": persona_id,
                        "semantics": "None=all, []=none, [...]=specific",
                    },
                )
            )

        # 记录 webchat special default
        if use_webchat_special_default:
            for slot in slots:
                slot.meta["use_webchat_special_default"] = True

        _store_resolution_slots(event, resolution_key, slots)

        return slots


def _get_cached_resolution_slots(
    event: AstrMessageEvent,
    key: object,
) -> list[ContextSlot] | None:
    cache = event.get_extra(_PERSONA_RESOLUTION_CACHE_EXTRA_KEY, {})
    if not isinstance(cache, dict) or key not in cache:
        return None
    try:
        return deepcopy(cache[key])
    except Exception:  # noqa: BLE001
        return None


def _store_resolution_slots(
    event: AstrMessageEvent,
    key: object,
    slots: list[ContextSlot],
) -> None:
    cache = event.get_extra(_PERSONA_RESOLUTION_CACHE_EXTRA_KEY, {})
    if not isinstance(cache, dict):
        cache = {}
    try:
        cache[key] = deepcopy(slots)
        event.set_extra(_PERSONA_RESOLUTION_CACHE_EXTRA_KEY, cache)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to cache resolved persona slots", exc_info=True)


def _build_persona_summary(segments: dict[str, object]) -> dict[str, list[str]]:
    """Keep only stable identity cues needed by lightweight consumers."""

    summary: dict[str, list[str]] = {}
    for key in ("identity", "core_persona", "dialogue_style", "stable_rules"):
        value = segments.get(key)
        if not isinstance(value, list):
            continue
        normalized = [str(item).strip() for item in value if str(item).strip()]
        if normalized:
            summary[key] = normalized[:6]
    return summary
