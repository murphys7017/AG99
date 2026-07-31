"""Resolve Interaction models without requiring duplicate provider configuration."""

from __future__ import annotations


async def resolve_interaction_chat_provider(
    event,
    plugin_context,
    configured_provider_id: str,
) -> tuple[object | None, str]:
    """Prefer an explicit model, otherwise reuse the event's chat provider.

    Interaction Middleware is enabled by default, so an empty optional model
    setting must retain the ordinary AstrBot chat-provider selection instead of
    turning a normal conversation into a local fallback reply.
    """
    provider_id = str(configured_provider_id or "").strip()
    if not provider_id:
        get_current_provider_id = getattr(
            plugin_context,
            "get_current_chat_provider_id",
            None,
        )
        if not callable(get_current_provider_id):
            return None, ""
        try:
            provider_id = str(
                await get_current_provider_id(event.unified_msg_origin)
            ).strip()
        except Exception:  # noqa: BLE001
            return None, ""

    return plugin_context.get_provider_by_id(provider_id), provider_id
