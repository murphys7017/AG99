"""Collector for plugin-provided ProviderRequest context messages."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextSlot
from ..interfaces import ContextCollectorInterface

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class ExplicitContextCollector(ContextCollectorInterface):
    """Preserve plugin contexts while leaving official history in its own slot."""

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        del event, plugin_context, config
        if provider_request is None:
            return []
        contexts = [
            deepcopy(item)
            for item in (provider_request.contexts or [])
            if isinstance(item, dict)
        ]
        contexts = _strip_history_prefix(contexts, provider_request)
        slots = []
        if contexts:
            slots.append(
                ContextSlot(
                    name="conversation.explicit_contexts",
                    value=contexts,
                    category="conversation",
                    source="provider_request.contexts",
                    render_mode="structured",
                    meta={"message_count": len(contexts)},
                )
            )
        content_parts = [
            deepcopy(item) for item in (provider_request.extra_user_content_parts or [])
        ]
        if content_parts:
            slots.append(
                ContextSlot(
                    name="input.explicit_content_parts",
                    value=content_parts,
                    category="input",
                    source="provider_request.extra_user_content_parts",
                    render_mode="structured",
                    meta={"part_count": len(content_parts)},
                )
            )
        return slots


def _strip_history_prefix(
    contexts: list[dict[str, Any]],
    request: ProviderRequest,
) -> list[dict[str, Any]]:
    conversation = request.conversation
    if conversation is None:
        return contexts
    raw_history = getattr(conversation, "history", None)
    try:
        history = json.loads(raw_history) if isinstance(raw_history, str) else raw_history
    except (TypeError, ValueError):
        return contexts
    if not isinstance(history, list):
        return contexts
    normalized_history = [item for item in history if isinstance(item, dict)]
    if contexts == normalized_history:
        return []
    if (
        normalized_history
        and len(contexts) >= len(normalized_history)
        and contexts[: len(normalized_history)] == normalized_history
    ):
        return contexts[len(normalized_history) :]
    return contexts


__all__ = ["ExplicitContextCollector"]
