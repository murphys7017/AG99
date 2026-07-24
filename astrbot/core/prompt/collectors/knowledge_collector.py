"""
Knowledge context collector for prompt context packing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.core import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context
from astrbot.core.tools.knowledge_base_tools import retrieve_knowledge_base_with_cache

from ..context_types import ContextSlot
from ..interfaces.context_collector_inferface import ContextCollectorInterface

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class KnowledgeCollector(ContextCollectorInterface):
    """Collect non-agentic knowledge-base text blocks for later rendering."""

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        if config.kb_agentic_mode:
            return []

        query, query_source = self._resolve_query(event, provider_request)
        if not query:
            return []

        try:
            kb_text = await retrieve_knowledge_base_with_cache(
                query=query,
                umo=event.unified_msg_origin,
                context=plugin_context,
                event=event,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect knowledge context: umo=%s error=%s",
                getattr(event, "unified_msg_origin", None),
                exc,
                exc_info=True,
            )
            return []

        if not kb_text:
            return []

        return [
            self._build_knowledge_slot(
                query=query,
                query_source=query_source,
                kb_text=kb_text,
                kb_agentic_mode=config.kb_agentic_mode,
            )
        ]

    def _resolve_query(
        self,
        event: AstrMessageEvent,
        provider_request: ProviderRequest | None,
    ) -> tuple[str | None, str | None]:
        if provider_request and isinstance(provider_request.prompt, str):
            prompt = provider_request.prompt.strip()
            if prompt:
                return prompt, "provider_request.prompt"

        message_text = getattr(event, "message_str", None)
        if isinstance(message_text, str):
            message_text = message_text.strip()
            if message_text:
                return message_text, "event.message_str"

        task_spec = getattr(
            event.get_extra("_interaction_turn_state"),
            "core_task_spec",
            None,
        )
        execution_prompt = str(
            getattr(task_spec, "execution_prompt", "") or ""
        ).strip()
        if execution_prompt:
            return execution_prompt, "core_task_spec.execution_prompt"

        return None, None

    def _build_knowledge_slot(
        self,
        *,
        query: str,
        query_source: str | None,
        kb_text: str,
        kb_agentic_mode: bool,
    ) -> ContextSlot:
        return ContextSlot(
            name="knowledge.snippets",
            value={
                "format": "kb_text_block_v1",
                "query": query,
                "text": kb_text,
            },
            category="rag",
            source="knowledge_base",
            meta={
                "format": "kb_text_block_v1",
                "query_source": query_source,
                "kb_agentic_mode": kb_agentic_mode,
            },
        )
