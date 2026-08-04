"""Tools context collector for prompt context packing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.core import logger
from astrbot.core.agent.tool import TOOL_TARGET_CORE, ToolSet, normalize_tool_targets
from astrbot.core.capabilities import CapabilityResolver, CapabilitySnapshot
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextSlot
from ..interfaces.context_collector_inferface import ContextCollectorInterface

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class ToolsCollector(ContextCollectorInterface):
    """Project one resolved capability snapshot into prompt context."""

    def __init__(
        self,
        *,
        target: str = TOOL_TARGET_CORE,
        capabilities: CapabilitySnapshot | None = None,
    ) -> None:
        normalize_tool_targets((target,))
        if capabilities is not None and capabilities.target != target:
            raise ValueError(
                "capability snapshot target does not match collector target: "
                f"{capabilities.target} != {target}"
            )
        self.target = target
        self.capabilities = capabilities

    @property
    def cache_key(self) -> str:
        return (
            f"{self.__class__.__module__}.{self.__class__.__qualname__}:{self.target}"
        )

    @property
    def lifecycle(self) -> str:
        return "dynamic" if self.capabilities is not None else "static"

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        try:
            capabilities = await self.resolve_capabilities(
                event,
                plugin_context,
                config,
                provider_request,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect tool inventory: umo=%s error=%s",
                getattr(event, "unified_msg_origin", None),
                exc,
                exc_info=True,
            )
            return []

        if capabilities.is_empty():
            return []
        return [self._build_tools_slot(capabilities)]

    async def resolve_capabilities(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> CapabilitySnapshot:
        """Return the supplied snapshot or resolve one through the public owner."""
        if self.capabilities is not None:
            return self.capabilities
        return await CapabilityResolver().resolve(
            event=event,
            plugin_context=plugin_context,
            config=config,
            target=self.target,
            provider_request=provider_request,
        )

    async def resolve_toolset(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> tuple[str | None, ToolSet, str]:
        """Compatibility wrapper for callers not yet migrated to snapshots."""
        capabilities = await self.resolve_capabilities(
            event,
            plugin_context,
            config,
            provider_request,
        )
        return (
            capabilities.persona_id,
            capabilities.to_toolset(),
            capabilities.selection_mode,
        )

    def _build_tools_slot(self, capabilities: CapabilitySnapshot) -> ContextSlot:
        return ContextSlot(
            name="capability.tools_schema",
            value=capabilities.serialized_inventory(),
            category="tools",
            source="capability_resolver",
            meta=capabilities.inventory_metadata(),
        )
