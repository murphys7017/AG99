"""Task-local state used by isolated agent request lifecycles."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

_MISSING = object()


@dataclass
class AgentLifecycleOverlay:
    """Temporary event state owned by one isolated agent lifecycle."""

    event: object
    local_extras: dict[str, Any] = field(default_factory=dict)
    extras_cleared: bool = False
    result: Any = _MISSING
    initial_result: Any = None
    initial_stopped: bool = False
    force_stopped: bool = False

    def capture_extra(self, key: str) -> Any:
        return self.local_extras.get(key, _MISSING)

    def restore_extra(self, key: str, value: Any) -> None:
        if value is _MISSING:
            self.local_extras.pop(key, None)
        else:
            self.local_extras[key] = value


_ACTIVE_AGENT_LIFECYCLE: ContextVar[AgentLifecycleOverlay | None] = ContextVar(
    "active_agent_lifecycle",
    default=None,
)


def create_agent_lifecycle_overlay(event: object) -> AgentLifecycleOverlay:
    """Snapshot the event's initial result state without mutating the event."""

    get_result = getattr(event, "get_result", None)
    initial_result = get_result() if callable(get_result) else None
    is_stopped = getattr(event, "is_stopped", None)
    initial_stopped = bool(is_stopped()) if callable(is_stopped) else False
    return AgentLifecycleOverlay(
        event=event,
        initial_result=initial_result,
        initial_stopped=initial_stopped,
    )


def get_active_agent_lifecycle(event: object) -> AgentLifecycleOverlay | None:
    """Return the active overlay when it belongs to ``event``."""

    overlay = _ACTIVE_AGENT_LIFECYCLE.get()
    if overlay is None or overlay.event is not event:
        return None
    return overlay


@contextmanager
def activate_agent_lifecycle(
    overlay: AgentLifecycleOverlay,
) -> Iterator[AgentLifecycleOverlay]:
    """Expose one lifecycle overlay only to the current task/context."""

    token = _ACTIVE_AGENT_LIFECYCLE.set(overlay)
    try:
        yield overlay
    finally:
        _ACTIVE_AGENT_LIFECYCLE.reset(token)


__all__ = [
    "AgentLifecycleOverlay",
    "_MISSING",
    "activate_agent_lifecycle",
    "create_agent_lifecycle_overlay",
    "get_active_agent_lifecycle",
]
