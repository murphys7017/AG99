from __future__ import annotations

from typing import Any

from astrbot.core.message.message_chain_transforms import (
    replace_plain_text_preserving_components,
)
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.context import Context

from .expression_agent import (
    InteractionExpressionAgent,
    InteractionExpressionError,
    PersonaExpressionRequest,
)
from .types import InteractionAgentConfig


class InteractionPersonaRuntime:
    """Persona-layer entry points for interaction-time output rendering."""

    def __init__(self, expression_agent: InteractionExpressionAgent) -> None:
        self.expression_agent = expression_agent

    async def express_visible_reply(
        self,
        event: Any,
        *,
        plugin_context: Context | None,
        interaction_config: InteractionAgentConfig,
        request: PersonaExpressionRequest,
    ):
        if plugin_context is None:
            raise InteractionExpressionError("plugin_context_unavailable")
        return await self.expression_agent.express_visible_reply_result(
            event,
            plugin_context,
            interaction_config,
            request,
        )

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
        result = await self.express_visible_reply(
            event,
            plugin_context=plugin_context,
            interaction_config=interaction_config,
            request=PersonaExpressionRequest(
                source_text=plain,
                preserve_facts=True,
            ),
        )
        if result.effect_calls:
            event.set_extra(
                "_interaction_plugin_output_effect_calls",
                list(result.effect_calls),
            )
        return replace_plain_text_preserving_components(
            message,
            result.spoken_reply,
        )

    async def render_core_reply(
        self,
        event: Any,
        source_text: str,
        *,
        plugin_context: Context | None,
        interaction_config: InteractionAgentConfig,
        immediate_reply: str | None = None,
    ) -> str:
        result = await self.express_visible_reply(
            event,
            plugin_context=plugin_context,
            interaction_config=interaction_config,
            request=PersonaExpressionRequest(
                source_text=source_text,
                immediate_reply=immediate_reply or "",
                preserve_facts=True,
            ),
        )
        if result.effect_calls:
            event.set_extra(
                "_interaction_final_response_effect_calls",
                list(result.effect_calls),
            )
        return result.spoken_reply

    async def render_stream_interjection(
        self,
        event: Any,
        *,
        observed_text: str,
        total_text: str,
        pending_text: str,
        plugin_context: Context | None,
        interaction_config: InteractionAgentConfig,
    ) -> str:
        result = await self.express_visible_reply(
            event,
            plugin_context=plugin_context,
            interaction_config=interaction_config,
            request=PersonaExpressionRequest(
                observed_text=observed_text,
                total_text=total_text,
                pending_text=pending_text,
                short_reply=True,
                allow_empty=True,
            ),
        )
        return result.spoken_reply.strip()
