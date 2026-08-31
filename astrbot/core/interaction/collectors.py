from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.prompt.context_types import ContextSlot
from astrbot.core.prompt.interfaces.context_collector_inferface import (
    ContextCollectorInterface,
)
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class PersonaVisibleReplyCollector(ContextCollectorInterface):
    """Collect phase-local material consumed by the Persona render target."""

    def __init__(self, request: object) -> None:
        self.request = request

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        del event, plugin_context, config, provider_request
        request = self.request
        payload = {
            "source_text": str(getattr(request, "source_text", "") or "").strip(),
            "immediate_reply": str(
                getattr(request, "immediate_reply", "") or ""
            ).strip(),
            "delegated_task_summary": str(
                getattr(request, "delegated_task_summary", "") or ""
            ).strip(),
            "observed_text": str(
                getattr(request, "observed_text", "") or ""
            ).strip(),
            "total_text": str(getattr(request, "total_text", "") or "").strip(),
            "pending_text": str(
                getattr(request, "pending_text", "") or ""
            ).strip(),
            "preserve_facts": bool(getattr(request, "preserve_facts", False)),
            "short_reply": bool(getattr(request, "short_reply", False)),
            "allow_empty": bool(getattr(request, "allow_empty", False)),
        }
        payload = {
            key: value for key, value in payload.items() if value not in {"", False}
        }
        if not payload:
            return []
        return [
            ContextSlot(
                name="input.visible_reply_material",
                value=payload,
                category="input",
                source="interaction_visible_reply_material",
                render_mode="structured",
                meta={
                    "scope": "dynamic",
                    "node_type": "interaction_visible_reply_material",
                },
            )
        ]
