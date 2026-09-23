"""Policy for exposing interaction failures to users."""

from __future__ import annotations

from enum import Enum
from typing import Any

from astrbot.core.platform.message_type import MessageType


class InteractionFailureKind(str, Enum):
    """Classify failures that may otherwise become a visible fallback reply."""

    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PERSONA_EXPRESSION_FAILED = "persona_expression_failed"
    SESSION_QUEUE_TIMEOUT = "session_queue_timeout"
    CORE_EXECUTION_TIMEOUT = "core_execution_timeout"
    TURN_TIMEOUT = "turn_timeout"
    DATABASE_LOCKED = "database_locked"
    PLATFORM_DELIVERY_FAILED = "platform_delivery_failed"
    INTERNAL_FAILURE = "internal_failure"


def should_emit_failure_reply(
    event: Any,
    failure_kind: InteractionFailureKind,
) -> bool:
    """Keep infrastructure failures out of shared group conversations."""
    del failure_kind
    return event.get_message_type() is not MessageType.GROUP_MESSAGE


__all__ = ["InteractionFailureKind", "should_emit_failure_reply"]
