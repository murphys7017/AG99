"""Low-level event contract for Router-arbitrated group reply candidates."""

from __future__ import annotations

from typing import Any

from .message_type import MessageType

GROUP_REPLY_CANDIDATE_EXTRA = "_interaction_group_reply_candidate"
GROUP_REPLY_CANDIDATE_KIND_EXTRA = "_interaction_group_reply_candidate_kind"


def is_group_reply_candidate(event: Any) -> bool:
    return bool(event.get_extra(GROUP_REPLY_CANDIDATE_EXTRA, False))


def mark_group_reply_candidate(event: Any, *, kind: str) -> None:
    event.set_extra(GROUP_REPLY_CANDIDATE_EXTRA, True)
    event.set_extra(GROUP_REPLY_CANDIDATE_KIND_EXTRA, kind)


def request_group_reply_candidate(event: Any) -> bool:
    """Submit a group message for interaction arbitration without claiming a reply."""
    if event.get_message_type() is not MessageType.GROUP_MESSAGE:
        return False
    if not is_group_reply_candidate(event):
        mark_group_reply_candidate(event, kind="plugin")
    return True


__all__ = [
    "GROUP_REPLY_CANDIDATE_EXTRA",
    "GROUP_REPLY_CANDIDATE_KIND_EXTRA",
    "is_group_reply_candidate",
    "mark_group_reply_candidate",
    "request_group_reply_candidate",
]
