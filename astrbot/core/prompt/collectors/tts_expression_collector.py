"""Expose provider-specific TTS expression guidance to final answer models."""

from __future__ import annotations

from collections.abc import Mapping

from astrbot.core.prompt.context_types import ContextSlot
from astrbot.core.star.context import Context
from astrbot.core.star.session_llm_manager import SessionServiceManager
from astrbot.core.tts_expression_tags import (
    build_minimax_tts_expression_guidance,
    get_minimax_tts_expression_model,
)

from ..interfaces.context_collector_inferface import ContextCollectorInterface


class TTSExpressionCollector(ContextCollectorInterface):
    """Add expression-tag rules when the session uses a supported MiniMax TTS."""

    failure_policy = "optional"
    lifecycle = "dynamic"

    async def collect(
        self,
        event,
        plugin_context: Context,
        config,
        provider_request=None,
    ) -> list[ContextSlot]:
        del config, provider_request
        tts_settings = self._get_tts_settings(event, plugin_context)
        if not bool(tts_settings.get("enable")):
            return []
        if not await SessionServiceManager.should_process_tts_request(event):
            return []

        get_provider = getattr(plugin_context, "get_using_tts_provider", None)
        if not callable(get_provider):
            return []
        provider = get_provider(event.unified_msg_origin)
        if provider is None:
            return []

        model = get_minimax_tts_expression_model(provider)
        if model is None:
            return []

        return [
            ContextSlot(
                name="system.tts_expression_guidance",
                value=build_minimax_tts_expression_guidance(model),
                category="system",
                source="tts_expression",
                render_mode="raw",
                meta={
                    "targets": ["persona", "core"],
                    "provider_type": "minimax_tts_api",
                    "model": model,
                },
            ),
        ]

    @staticmethod
    def _get_tts_settings(event, plugin_context: Context) -> Mapping:
        event_config = event.get_extra("_astrbot_config")
        if isinstance(event_config, Mapping):
            value = event_config.get("provider_tts_settings", {})
            if isinstance(value, Mapping):
                return value
        get_config = getattr(plugin_context, "get_config", None)
        if callable(get_config):
            config = get_config(umo=event.unified_msg_origin)
            if isinstance(config, Mapping):
                value = config.get("provider_tts_settings", {})
                if isinstance(value, Mapping):
                    return value
        return {}
