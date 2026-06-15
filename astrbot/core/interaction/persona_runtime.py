from __future__ import annotations

from typing import Any

from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.context import Context

from .expression_agent import InteractionExpressionAgent, InteractionExpressionError
from .types import InteractionAgentConfig


class InteractionPersonaRuntime:
    """Persona-layer entry points for interaction-time output rendering."""

    def __init__(self, expression_agent: InteractionExpressionAgent) -> None:
        self.expression_agent = expression_agent

    async def render_plugin_output(
        self,
        event: Any,
        message: MessageChain,
        *,
        plugin_context: Context | None,
        interaction_config: InteractionAgentConfig,
        metadata: dict[str, Any] | None = None,
    ) -> MessageChain:
        del metadata
        plain = message.get_plain_text().strip()
        if not plain:
            return message
        if plugin_context is None:
            raise InteractionExpressionError("plugin_context_unavailable")

        rewritten = await self.expression_agent.rewrite_plugin_output(
            event,
            plugin_context,
            interaction_config,
            plain,
        )
        return message.derive([Plain(rewritten)])
