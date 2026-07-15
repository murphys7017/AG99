from __future__ import annotations

from astrbot import logger
from astrbot.core.star.context import Context


def match_protocol_command_bypass(event, plugin_context: Context) -> str | None:
    text = (event.message_str or "").strip().lower()
    prefixes = _extract_configured_wake_prefixes(plugin_context, event)
    matched_prefix = next(
        (
            prefix
            for prefix in sorted(prefixes, key=len, reverse=True)
            if text.startswith(prefix.lower()) and len(text) > len(prefix)
        ),
        None,
    )
    if matched_prefix is None:
        return None
    logger.info(
        "Interaction protocol command bypassed: platform_id=%s session_id=%s prefix=%s",
        event.get_platform_id(),
        event.session_id,
        matched_prefix,
    )
    return "protocol_command_bypass"


def _extract_configured_wake_prefixes(
    plugin_context: Context,
    event,
) -> list[str]:
    cfg = plugin_context.get_config(umo=event.unified_msg_origin)
    if not isinstance(cfg, dict):
        return []
    wake_prefix = cfg.get("wake_prefix", [])
    if isinstance(wake_prefix, str):
        candidates = [wake_prefix]
    elif isinstance(wake_prefix, list):
        candidates = wake_prefix
    else:
        candidates = []
    return [str(item) for item in candidates if isinstance(item, str) and item]


__all__ = ["match_protocol_command_bypass"]
