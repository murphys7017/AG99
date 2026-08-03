"""Router-gated admission for unaddressed group reply candidates."""

from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Any

from astrbot.core.platform.message_type import MessageType

from .config import is_middleware_enabled

GROUP_REPLY_CANDIDATE_EXTRA = "_interaction_group_reply_candidate"
GROUP_REPLY_CANDIDATE_KIND_EXTRA = "_interaction_group_reply_candidate_kind"


def is_group_reply_candidate(event: Any) -> bool:
    return bool(event.get_extra(GROUP_REPLY_CANDIDATE_EXTRA, False))


def mark_group_reply_candidate(event: Any, *, kind: str) -> None:
    event.set_extra(GROUP_REPLY_CANDIDATE_EXTRA, True)
    event.set_extra(GROUP_REPLY_CANDIDATE_KIND_EXTRA, kind)


def request_group_reply_candidate(event: Any) -> bool:
    """Submit a plugin-owned group message for Router reply admission.

    This grants neither reply ownership nor a direct LLM call. The Router keeps
    the final choice between silence, Persona, and Core delegation.
    """
    if event.get_message_type() is not MessageType.GROUP_MESSAGE:
        return False
    if not is_group_reply_candidate(event):
        mark_group_reply_candidate(event, kind="plugin")
    event.is_wake = True
    event.is_at_or_wake_command = True
    return True


def select_legacy_active_reply_candidate(
    event: Any,
    config: Mapping[str, object],
    *,
    random_value: float | None = None,
) -> bool:
    """Sample a legacy group active-reply candidate for Router admission.

    The historical setting is now only a sampling gate. It never permits a
    direct provider call; the Router can and normally should select ``silent``.
    """
    if (
        not is_middleware_enabled(config)
        or event.get_message_type() is not MessageType.GROUP_MESSAGE
        or event.is_at_or_wake_command
        or event.is_wake
        or event.get_extra("action_type") == "live"
    ):
        return False
    sender_id = str(event.get_sender_id() or "").strip()
    self_id = str(event.get_self_id() or "").strip()
    if not sender_id or (self_id and sender_id == self_id):
        return False
    if not event.get_message_str().strip() and not event.get_messages():
        return False

    settings = config.get("provider_ltm_settings", {})
    if not isinstance(settings, Mapping):
        return False
    active_reply = settings.get("active_reply", {})
    if not isinstance(active_reply, Mapping) or not active_reply.get("enable", False):
        return False
    if active_reply.get("method", "possibility_reply") != "possibility_reply":
        return False

    whitelist = _normalize_whitelist(active_reply.get("whitelist", []))
    if whitelist:
        group_id = str(event.get_group_id() or "").strip()
        if event.unified_msg_origin not in whitelist and group_id not in whitelist:
            return False
    try:
        probability = float(active_reply.get("possibility_reply", 0.0))
    except (TypeError, ValueError):
        return False
    probability = min(1.0, max(0.0, probability))
    if probability <= 0.0:
        return False
    return (random.random() if random_value is None else random_value) < probability


def _normalize_whitelist(value: object) -> set[str]:
    if isinstance(value, str):
        values = [value]
    else:
        try:
            values = list(value)
        except TypeError:
            values = []
    return {str(item).strip() for item in values if str(item).strip()}


__all__ = [
    "GROUP_REPLY_CANDIDATE_EXTRA",
    "GROUP_REPLY_CANDIDATE_KIND_EXTRA",
    "is_group_reply_candidate",
    "mark_group_reply_candidate",
    "request_group_reply_candidate",
    "select_legacy_active_reply_candidate",
]
