from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from astrbot.core.message.components import BaseMessageComponent
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.message_type import MessageType
from astrbot.core.provider.entities import ProviderRequest

from .observation import RuntimeObservation
from .runtime_event import RuntimeObservationEvent
from .turn_state import InteractionTurnState, ensure_interaction_turn_state


@dataclass(frozen=True, slots=True)
class TurnSession:
    platform_id: str
    platform_name: str
    message_type: MessageType
    session_id: str
    unified_msg_origin: str
    config_id: str
    privacy_scope: str
    group_id: str | None = None
    group_name: str | None = None
    support_proactive_message: bool = False


@dataclass(frozen=True, slots=True)
class TurnActor:
    actor_id: str
    display_name: str
    role: str


@dataclass(frozen=True, slots=True)
class TurnInput:
    text: str
    outline: str
    components: tuple[BaseMessageComponent, ...]
    created_at: float
    source_message_id: str | None


@dataclass(frozen=True, slots=True)
class OutputTarget:
    platform_id: str
    platform_name: str
    message_type: MessageType
    session_id: str
    unified_msg_origin: str


@dataclass(slots=True)
class PersonalTurnContext:
    turn_id: str
    event: AstrMessageEvent
    session: TurnSession
    actor: TurnActor | None
    input: TurnInput | None
    observation: RuntimeObservation | None
    output_target: OutputTarget
    state: InteractionTurnState
    runtime_config: Mapping[str, Any]
    provider_request: ProviderRequest | None
    plugin_context: Any


class PlatformTurnContextFactory:
    """Create the single context owned by one platform submission."""

    @staticmethod
    def create(
        event: AstrMessageEvent,
        *,
        config_id: str,
        runtime_config: Mapping[str, Any],
        plugin_context: Any,
    ) -> PersonalTurnContext:
        existing_turn_id = str(event.get_extra("_turn_id", "") or "").strip()
        observation = (
            event.observation if isinstance(event, RuntimeObservationEvent) else None
        )
        state = ensure_interaction_turn_state(
            event,
            turn_id=existing_turn_id or uuid.uuid4().hex,
        )
        session_data = TurnSession(
            platform_id=event.get_platform_id(),
            platform_name=event.get_platform_name(),
            message_type=event.get_message_type(),
            session_id=event.get_session_id(),
            unified_msg_origin=event.unified_msg_origin,
            config_id=config_id or "default",
            privacy_scope=PlatformTurnContextFactory._privacy_scope(
                event.get_message_type()
            ),
            group_id=(str(event.get_group_id()).strip() or None)
            if getattr(event, "get_group_id", None) and event.get_group_id()
            else None,
            group_name=(
                str(getattr(getattr(event.message_obj, "group", None), "group_name", ""))
                or None
            ),
            support_proactive_message=bool(
                getattr(event.platform_meta, "support_proactive_message", False)
            ),
        )
        actor = (
            None
            if observation is not None
            else TurnActor(
                actor_id=event.get_sender_id(),
                display_name=event.get_sender_name(),
                role=str(getattr(event, "role", "member") or "member"),
            )
        )
        message_obj = event.message_obj
        turn_input = (
            None
            if observation is not None
            else TurnInput(
                text=event.get_message_str(),
                outline=event.get_message_outline(),
                components=tuple(event.get_messages()),
                created_at=event.created_at,
                source_message_id=str(getattr(message_obj, "message_id", "") or "")
                or None,
            )
        )
        output_target = OutputTarget(
            platform_id=session_data.platform_id,
            platform_name=session_data.platform_name,
            message_type=session_data.message_type,
            session_id=session_data.session_id,
            unified_msg_origin=session_data.unified_msg_origin,
        )
        provider_request = event.get_extra("provider_request")
        if not isinstance(provider_request, ProviderRequest):
            provider_request = None
        return PersonalTurnContext(
            turn_id=state.turn_id,
            event=event,
            session=session_data,
            actor=actor,
            input=turn_input,
            observation=observation,
            output_target=output_target,
            state=state,
            runtime_config=MappingProxyType(dict(runtime_config)),
            provider_request=provider_request,
            plugin_context=plugin_context,
        )

    @staticmethod
    def _privacy_scope(message_type: MessageType) -> str:
        if message_type is MessageType.GROUP_MESSAGE:
            return "group"
        if message_type is MessageType.FRIEND_MESSAGE:
            return "private"
        return "other"


__all__ = [
    "OutputTarget",
    "PersonalTurnContext",
    "PlatformTurnContextFactory",
    "TurnActor",
    "TurnInput",
    "TurnSession",
]
