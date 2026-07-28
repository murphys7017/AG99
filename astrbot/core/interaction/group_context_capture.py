"""Eligibility check for passive group-context capture."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol, runtime_checkable

from astrbot.core.platform.message_type import MessageType

GROUP_CONTEXT_CAPTURE_CANDIDATE_EXTRA = "_group_context_capture_candidate"


@runtime_checkable
class GroupContextCaptureCollector(Protocol):
    """Optional plugin boundary used by the official group-context stage."""

    async def capture_ambient_message(
        self,
        event: Any,
        *,
        allow_router_candidate: bool = False,
    ) -> None: ...


def resolve_group_context_capture_collector(
    collectors: Iterable[object],
) -> GroupContextCaptureCollector | None:
    """Return the first collector that explicitly supports passive capture."""
    return next(
        (
            collector
            for collector in collectors
            if isinstance(collector, GroupContextCaptureCollector)
            and callable(getattr(collector, "capture_ambient_message", None))
        ),
        None,
    )


def is_group_context_capture_candidate(
    event: Any,
    config: Mapping[str, object],
) -> bool:
    if (
        event.is_stopped()
        or event.is_at_or_wake_command
        or event.is_wake
        or event.get_extra("action_type") == "live"
        or event.get_message_type() is not MessageType.GROUP_MESSAGE
    ):
        return False
    sender_id = str(event.get_sender_id() or "").strip()
    self_id = str(event.get_self_id() or "").strip()
    if self_id and sender_id == self_id:
        return False
    settings = config.get("provider_ltm_settings", {})
    return isinstance(settings, Mapping) and bool(
        settings.get("group_icl_enable", False)
    )


__all__ = [
    "GROUP_CONTEXT_CAPTURE_CANDIDATE_EXTRA",
    "GroupContextCaptureCollector",
    "is_group_context_capture_candidate",
    "resolve_group_context_capture_collector",
]
