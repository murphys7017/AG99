from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from astrbot import logger

from .provider import Provider


def resolve_fallback_chat_providers(
    primary_provider: Provider | None,
    provider_settings: Mapping[str, Any],
    get_provider_by_id: Callable[[str], object | None],
) -> list[Provider]:
    """Resolve configured fallback chat providers in declaration order."""
    fallback_ids = provider_settings.get("fallback_chat_models", [])
    if not isinstance(fallback_ids, list):
        logger.warning(
            "fallback_chat_models setting is not a list, skip fallback providers."
        )
        return []

    primary_id = (
        str(primary_provider.provider_config.get("id", ""))
        if primary_provider is not None
        else ""
    )
    seen_provider_ids = {primary_id} if primary_id else set()
    fallback_providers: list[Provider] = []
    for fallback_id in fallback_ids:
        if not isinstance(fallback_id, str) or not fallback_id:
            continue
        if fallback_id in seen_provider_ids:
            continue
        fallback_provider = get_provider_by_id(fallback_id)
        if not isinstance(fallback_provider, Provider):
            logger.warning(
                "Fallback chat provider `%s` is unavailable or invalid, skip.",
                fallback_id,
            )
            continue
        fallback_providers.append(fallback_provider)
        seen_provider_ids.add(fallback_id)
    return fallback_providers


__all__ = ["resolve_fallback_chat_providers"]
