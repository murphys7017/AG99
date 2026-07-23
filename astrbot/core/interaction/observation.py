from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType
from typing import Any

from astrbot.core.platform.message_type import MessageType


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(item) for item in value)
    if value is None or isinstance(value, str | int | float | bool | bytes):
        return value
    raise TypeError(f"Unsupported mutable observation payload value: {type(value)!r}")


@dataclass(frozen=True, slots=True)
class RuntimeObservationTarget:
    platform_id: str
    platform_name: str
    message_type: MessageType
    session_id: str
    support_proactive_message: bool = False
    group_id: str | None = None
    group_name: str | None = None

    def __post_init__(self) -> None:
        platform_id = str(self.platform_id or "").strip()
        platform_name = str(self.platform_name or "").strip()
        session_id = str(self.session_id or "").strip()
        if not platform_id:
            raise ValueError("RuntimeObservationTarget.platform_id is required")
        if not platform_name:
            raise ValueError("RuntimeObservationTarget.platform_name is required")
        if not isinstance(self.message_type, MessageType):
            raise TypeError("RuntimeObservationTarget.message_type must be MessageType")
        if not session_id:
            raise ValueError("RuntimeObservationTarget.session_id is required")
        object.__setattr__(self, "platform_id", platform_id)
        object.__setattr__(self, "platform_name", platform_name)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(
            self, "support_proactive_message", bool(self.support_proactive_message)
        )
        object.__setattr__(
            self,
            "group_id",
            str(self.group_id).strip() or None if self.group_id is not None else None,
        )
        object.__setattr__(
            self,
            "group_name",
            str(self.group_name).strip() or None
            if self.group_name is not None
            else None,
        )

    @property
    def unified_msg_origin(self) -> str:
        return f"{self.platform_id}:{self.message_type.value}:{self.session_id}"


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    """Immutable internal fact; this is deliberately not a user message."""

    kind: str
    source: str
    occurred_at: float
    target_session: RuntimeObservationTarget
    observation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    expires_at: float | None = None
    coalesce_key: str | None = None
    correlation_id: str | None = None
    payload: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        kind = str(self.kind or "").strip()
        source = str(self.source or "").strip()
        observation_id = str(self.observation_id or "").strip()
        if not kind:
            raise ValueError("RuntimeObservation.kind is required")
        if not source:
            raise ValueError("RuntimeObservation.source is required")
        if not observation_id:
            raise ValueError("RuntimeObservation.observation_id is required")
        if not isinstance(self.target_session, RuntimeObservationTarget):
            raise TypeError("RuntimeObservation.target_session must be a target session")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "observation_id", observation_id)
        occurred_at = float(self.occurred_at)
        expires_at = float(self.expires_at) if self.expires_at is not None else None
        if not isfinite(occurred_at):
            raise ValueError("RuntimeObservation.occurred_at must be finite")
        if expires_at is not None and not isfinite(expires_at):
            raise ValueError("RuntimeObservation.expires_at must be finite")
        object.__setattr__(self, "occurred_at", occurred_at)
        object.__setattr__(
            self,
            "expires_at",
            expires_at,
        )
        object.__setattr__(
            self,
            "coalesce_key",
            str(self.coalesce_key).strip() or None
            if self.coalesce_key is not None
            else None,
        )
        object.__setattr__(self, "correlation_id", self.correlation_id or None)
        object.__setattr__(self, "payload", _freeze(self.payload))

    @property
    def coalesce_identity(self) -> tuple[str, str, str] | None:
        if self.coalesce_key is None:
            return None
        return (self.kind, self.source, self.coalesce_key)

    @property
    def visible_reply_material(self) -> str:
        return str(self.payload.get("visible_reply_material", "") or "").strip()

    @property
    def is_user_message(self) -> bool:
        return False


__all__ = ["RuntimeObservation", "RuntimeObservationTarget"]
