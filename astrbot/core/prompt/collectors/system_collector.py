"""
System context collector for prompt context packing.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from astrbot.core import logger
from astrbot.core.db import BaseDatabase
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.prompt.resources import (
    LIVE_MODE_SYSTEM_PROMPT,
    TOOL_CALL_PROMPT,
    TOOL_CALL_PROMPT_SKILLS_LIKE_MODE,
    WEB_SEARCH_CITATION_PROMPT,
    WEB_SEARCH_CITATION_TOOL_NAMES,
)
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context
from astrbot.core.workspace import (
    default_workspace_root,
    resolve_workspace_root_for_umo,
)

from ..context_types import ContextSlot
from ..interfaces.context_collector_inferface import ContextCollectorInterface
from .tools_collector import ToolsCollector

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class SystemCollector(ContextCollectorInterface):
    """Collect base system prompt and tool-call instruction metadata."""

    def __init__(self, *, base_only: bool = False) -> None:
        self.base_only = base_only

    @property
    def cache_key(self) -> str:
        suffix = "base" if self.base_only else "full"
        return f"{self.__class__.__module__}.{self.__class__.__qualname__}:{suffix}"

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
        slots: list[ContextSlot] = []

        try:
            base_slot = self._build_system_base_slot(provider_request)
            if base_slot is not None:
                slots.append(base_slot)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect system base prompt: %s", exc, exc_info=True
            )

        if self.base_only:
            return slots

        try:
            instruction_slot = await self._build_tool_call_instruction_slot(
                event=event,
                plugin_context=plugin_context,
                config=config,
                provider_request=provider_request,
            )
            if instruction_slot is not None:
                slots.append(instruction_slot)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect tool-call instruction prompt: %s",
                exc,
                exc_info=True,
            )

        try:
            workspace_prompt_slot = await self._build_workspace_extra_prompt_slot(
                event,
                plugin_context,
            )
            if workspace_prompt_slot is not None:
                slots.append(workspace_prompt_slot)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect workspace extra prompt: %s",
                exc,
                exc_info=True,
            )

        try:
            live_mode_slot = self._build_live_mode_prompt_slot(event)
            if live_mode_slot is not None:
                slots.append(live_mode_slot)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect live-mode prompt: %s",
                exc,
                exc_info=True,
            )

        web_search_slot = self._build_web_search_citation_slot(
            event,
            provider_request,
        )
        if web_search_slot is not None:
            slots.append(web_search_slot)

        return slots

    def _build_system_base_slot(
        self,
        provider_request: ProviderRequest | None,
    ) -> ContextSlot | None:
        if provider_request is None or not isinstance(
            provider_request.system_prompt, str
        ):
            return None

        system_prompt = provider_request.system_prompt.strip()
        if not system_prompt:
            return None

        return ContextSlot(
            name="system.base",
            value=system_prompt,
            category="system",
            source="provider_request",
            meta={
                "source_field": "provider_request.system_prompt",
            },
        )

    async def _build_workspace_extra_prompt_slot(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
    ) -> ContextSlot | None:
        workspace_root = await self._get_workspace_root(event, plugin_context)
        extra_prompt_path = workspace_root / "EXTRA_PROMPT.md"
        if not extra_prompt_path.is_file():
            return None

        try:
            extra_prompt = extra_prompt_path.read_text(encoding="utf-8").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to read workspace extra prompt for umo=%s from %s: %s",
                event.unified_msg_origin,
                extra_prompt_path,
                exc,
            )
            return None

        if not extra_prompt:
            return None

        return ContextSlot(
            name="system.workspace_extra_prompt",
            value={
                "path": str(extra_prompt_path),
                "text": extra_prompt,
            },
            category="system",
            source="workspace",
            meta={
                "source_field": "workspace/EXTRA_PROMPT.md",
            },
        )

    async def _get_workspace_root(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
    ) -> Path:
        workspace_root = default_workspace_root(event.unified_msg_origin)
        db = getattr(plugin_context, "_db", None)
        if not isinstance(db, BaseDatabase):
            return workspace_root
        try:
            return await resolve_workspace_root_for_umo(
                event.unified_msg_origin,
                db,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Failed to resolve prompt workspace root for %s: %s",
                event.unified_msg_origin,
                exc,
            )
            return workspace_root

    async def _build_tool_call_instruction_slot(
        self,
        *,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None,
    ) -> ContextSlot | None:
        has_tools = await self._has_tool_capability(
            event=event,
            plugin_context=plugin_context,
            config=config,
            provider_request=provider_request,
        )
        if not has_tools:
            return None

        tool_prompt = (
            TOOL_CALL_PROMPT
            if config.tool_schema_mode == "full"
            else TOOL_CALL_PROMPT_SKILLS_LIKE_MODE
        )
        if config.computer_use_runtime == "local":
            workspace_root = await self._get_workspace_root(event, plugin_context)
            tool_prompt += (
                f"\nCurrent workspace you can use: "
                f"`{workspace_root}`\n"
                "Unless the user explicitly specifies a different directory, "
                "perform all file-related operations in this workspace.\n"
            )
        return ContextSlot(
            name="system.tool_call_instruction",
            value=tool_prompt,
            category="system",
            source="main_agent_policy",
            meta={
                "tool_schema_mode": config.tool_schema_mode,
                "requires_tools": True,
                "runtime": config.computer_use_runtime,
            },
        )

    def _build_web_search_citation_slot(
        self,
        event: AstrMessageEvent,
        provider_request: ProviderRequest | None,
    ) -> ContextSlot | None:
        if event.get_platform_name() != "webchat" or provider_request is None:
            return None
        tools = provider_request.func_tool
        if not tools or not any(
            tools.get_tool(name) for name in WEB_SEARCH_CITATION_TOOL_NAMES
        ):
            return None
        return ContextSlot(
            name="system.web_search_citation_prompt",
            value=WEB_SEARCH_CITATION_PROMPT,
            category="system",
            source="web_search_policy",
            meta={"platform": "webchat"},
        )

    def _build_live_mode_prompt_slot(
        self,
        event: AstrMessageEvent,
    ) -> ContextSlot | None:
        if event.get_extra("action_type") != "live":
            return None
        return ContextSlot(
            name="system.live_mode_prompt",
            value=LIVE_MODE_SYSTEM_PROMPT,
            category="system",
            source="main_agent_policy",
            meta={
                "action_type": "live",
            },
        )

    async def _has_tool_capability(
        self,
        *,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None,
    ) -> bool:
        if (
            provider_request
            and provider_request.func_tool
            and provider_request.func_tool.tools
        ):
            return True

        if config.kb_agentic_mode:
            return True

        if config.computer_use_runtime in {"sandbox", "local"}:
            return True

        if config.add_cron_tools:
            return True

        platform_meta = getattr(event, "platform_meta", None)
        if getattr(platform_meta, "support_proactive_message", None) is True:
            return True

        orchestrator_config = plugin_context.get_config().get(
            "subagent_orchestrator", {}
        )
        orchestrator = getattr(plugin_context, "subagent_orchestrator", None)
        if (
            isinstance(orchestrator_config, dict)
            and orchestrator_config.get("main_enable", False)
            and orchestrator is not None
            and bool(getattr(orchestrator, "handoffs", []))
        ):
            return True

        tools_collector = ToolsCollector()
        _, persona = await tools_collector._resolve_persona(
            event,
            plugin_context,
            config,
            provider_request,
        )
        toolset, _ = tools_collector._build_persona_toolset(
            event,
            plugin_context,
            persona,
            provider_request,
        )
        return not toolset.empty()
