from __future__ import annotations

from copy import deepcopy
from typing import Any

from astrbot.core.star.context import Context

from .types import InteractionPromptBuildConfig


def build_interaction_prompt_build_config(
    plugin_context: Context,
    event,
) -> InteractionPromptBuildConfig:
    cfg = plugin_context.get_config(umo=event.unified_msg_origin)
    provider_settings = (
        cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
    )
    provider_wake_prefix = ""
    if isinstance(cfg, dict):
        wake_prefix = cfg.get("wake_prefix", "")
        if isinstance(wake_prefix, str):
            provider_wake_prefix = wake_prefix
        elif isinstance(wake_prefix, list):
            provider_wake_prefix = next(
                (
                    str(item)
                    for item in wake_prefix
                    if isinstance(item, str) and item
                ),
                "",
            )
    interaction_settings = (
        cfg.get("interaction_middleware", {}) if isinstance(cfg, dict) else {}
    )
    try:
        contributor_timeout = float(
            interaction_settings.get("contributor_timeout", 1.0)
            if isinstance(interaction_settings, dict)
            else 1.0
        )
    except (TypeError, ValueError):
        contributor_timeout = 1.0
    return InteractionPromptBuildConfig(
        provider_settings=provider_settings,
        timezone=(cfg.get("timezone") if isinstance(cfg, dict) else None),
        provider_wake_prefix=provider_wake_prefix,
        file_extract_enabled=bool(
            cfg.get("file_extract_enabled", False) if isinstance(cfg, dict) else False
        ),
        file_extract_prov=str(
            cfg.get("file_extract_prov", "moonshotai")
            if isinstance(cfg, dict)
            else "moonshotai"
        ),
        file_extract_msh_api_key=str(
            cfg.get("file_extract_msh_api_key", "")
            if isinstance(cfg, dict)
            else ""
        ),
        max_quoted_fallback_images=int(
            provider_settings.get("max_quoted_fallback_images", 20) or 20
        ),
        contributor_timeout=max(0.1, contributor_timeout),
    )


def build_model_context_messages(
    rendered_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for message in rendered_messages:
        if not isinstance(message, dict):
            continue
        context_message = deepcopy(message)
        context_message.pop("_no_save", None)
        contexts.append(context_message)
    return contexts


__all__ = [
    "build_interaction_prompt_build_config",
    "build_model_context_messages",
]
