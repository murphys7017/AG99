from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from astrbot.core.config.wake_prefix import resolve_provider_wake_prefix
from astrbot.core.provider.modalities import (
    log_context_sanitize_stats,
    sanitize_contexts_by_modalities,
)
from astrbot.core.star.context import Context

from .turn_state import get_interaction_turn_runtime_config
from .types import InteractionPromptBuildConfig


def build_interaction_prompt_build_config(
    plugin_context: Context,
    event,
) -> InteractionPromptBuildConfig:
    cfg = get_interaction_turn_runtime_config(event)
    if cfg is None:
        cfg = plugin_context.get_config(umo=event.unified_msg_origin)
    if not isinstance(cfg, Mapping):
        cfg = {}
    provider_settings = cfg.get("provider_settings", {})
    if not isinstance(provider_settings, Mapping):
        provider_settings = {}
    provider_settings = deepcopy(dict(provider_settings))
    file_extract = provider_settings.get("file_extract", {})
    if not isinstance(file_extract, Mapping):
        file_extract = {}
    provider_wake_prefix = resolve_provider_wake_prefix(cfg)
    interaction_settings = cfg.get("interaction_middleware", {})
    try:
        contributor_timeout = float(
            interaction_settings.get("contributor_timeout", 1.0)
            if isinstance(interaction_settings, Mapping)
            else 1.0
        )
    except (TypeError, ValueError):
        contributor_timeout = 1.0
    try:
        plugin_enrichment_timeout = float(
            interaction_settings.get("plugin_enrichment_timeout", 3.0)
            if isinstance(interaction_settings, Mapping)
            else 3.0
        )
    except (TypeError, ValueError):
        plugin_enrichment_timeout = 3.0
    return InteractionPromptBuildConfig(
        provider_settings=provider_settings,
        timezone=cfg.get("timezone"),
        provider_wake_prefix=provider_wake_prefix,
        file_extract_enabled=bool(file_extract.get("enable", False)),
        file_extract_msh_api_key=str(file_extract.get("moonshotai_api_key", "")),
        max_quoted_fallback_images=int(
            provider_settings.get("max_quoted_fallback_images", 20) or 20
        ),
        contributor_timeout=max(0.1, contributor_timeout),
        plugin_enrichment_timeout=max(0.1, plugin_enrichment_timeout),
    )


def build_model_context_messages(
    rendered_messages: list[dict[str, Any]],
    *,
    provider: object | None = None,
) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for message in rendered_messages:
        if not isinstance(message, dict):
            continue
        context_message = deepcopy(message)
        context_message.pop("_no_save", None)
        contexts.append(context_message)
    provider_config = getattr(provider, "provider_config", None)
    modalities = provider_config.get("modalities") if isinstance(provider_config, dict) else None
    if isinstance(modalities, list):
        contexts, stats = sanitize_contexts_by_modalities(contexts, modalities)
        log_context_sanitize_stats(stats)
    return contexts


__all__ = [
    "build_interaction_prompt_build_config",
    "build_model_context_messages",
]
