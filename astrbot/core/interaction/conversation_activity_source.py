from __future__ import annotations

import time
from collections.abc import Mapping
from typing import TYPE_CHECKING

from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType

from .observation import RuntimeObservation, RuntimeObservationTarget

if TYPE_CHECKING:
    from astrbot.core.platform.astr_message_event import AstrMessageEvent

    from .observation_inbox import ObservationAdmissionResult
    from .personal_runtime import PersonalRuntimeManager


CONVERSATION_ACTIVITY_CANDIDATE_EXTRA_KEY = (
    "_personal_runtime_conversation_activity_candidate"
)
_CONVERSATION_ACTIVITY_TTL_SECONDS = 60.0


def is_conversation_activity_capture_enabled(config: Mapping[str, object]) -> bool:
    interaction_config = config.get("interaction_middleware", {})
    return isinstance(interaction_config, Mapping) and bool(
        interaction_config.get("personal_conversation_activity_enabled", False)
    )


def is_conversation_activity_candidate(
    event: AstrMessageEvent,
    config: Mapping[str, object],
) -> bool:
    """Return whether an unaddressed group event may continue to the observation tap."""
    if (
        not is_conversation_activity_capture_enabled(config)
        or event.is_stopped()
        or event.is_wake
        or event.is_at_or_wake_command
        or event.get_extra("action_type") == "live"
        or event.get_message_type() is not MessageType.GROUP_MESSAGE
        or not event.get_message_str().strip()
    ):
        return False
    sender_id = str(event.get_sender_id() or "").strip()
    self_id = str(event.get_self_id() or "").strip()
    if self_id and sender_id and sender_id == self_id:
        return False
    return _resolve_target(event, config) is not None


def _resolve_target(
    event: AstrMessageEvent,
    config: Mapping[str, object],
) -> RuntimeObservationTarget | None:
    platform_settings = config.get("platform_settings", {})
    if not isinstance(platform_settings, Mapping):
        return None
    raw_target = str(platform_settings.get("proactive_message_target", "") or "").strip()
    if not raw_target:
        return None
    try:
        target = MessageSession.from_str(raw_target)
    except (TypeError, ValueError):
        return None

    group_id = str(event.get_group_id() or "").strip()
    if (
        target.platform_id != event.get_platform_id()
        or target.message_type is not MessageType.GROUP_MESSAGE
        or not group_id
        or target.session_id != group_id
        or not event.platform_meta.support_proactive_message
    ):
        return None
    return RuntimeObservationTarget(
        platform_id=target.platform_id,
        platform_name=event.get_platform_name(),
        message_type=target.message_type,
        session_id=target.session_id,
        support_proactive_message=True,
        group_id=group_id,
    )


class ConversationActivitySource:
    """Convert eligible ambient group activity into an internal Runtime fact."""

    def __init__(self, runtime_manager: PersonalRuntimeManager | None) -> None:
        self._runtime_manager = runtime_manager

    async def submit(
        self,
        event: AstrMessageEvent,
        *,
        config_id: str,
        plugin_context: object,
        runtime_config: Mapping[str, object],
    ) -> ObservationAdmissionResult | None:
        runtime_manager = self._runtime_manager
        target = _resolve_target(event, runtime_config)
        if runtime_manager is None or target is None:
            return None
        if not is_conversation_activity_candidate(event, runtime_config):
            return None

        occurred_at = time.time()
        observation = RuntimeObservation(
            kind="conversation_activity",
            source="personal_runtime.conversation_activity",
            occurred_at=occurred_at,
            expires_at=occurred_at + _CONVERSATION_ACTIVITY_TTL_SECONDS,
            target_session=target,
            payload={
                "message_count": 1,
                "participant_id": str(event.get_sender_id() or "").strip(),
                "is_explicitly_summoned": False,
            },
        )
        return await runtime_manager.submit_observation(
            observation,
            config_id=config_id,
            plugin_context=plugin_context,
            runtime_config=runtime_config,
        )


__all__ = [
    "CONVERSATION_ACTIVITY_CANDIDATE_EXTRA_KEY",
    "ConversationActivitySource",
    "is_conversation_activity_candidate",
    "is_conversation_activity_capture_enabled",
]
