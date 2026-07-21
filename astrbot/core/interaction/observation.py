from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
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
    correlation_id: str | None = None
    payload: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        kind = str(self.kind or "").strip()
        source = str(self.source or "").strip()
        if not kind:
            raise ValueError("RuntimeObservation.kind is required")
        if not source:
            raise ValueError("RuntimeObservation.source is required")
        if not isinstance(self.target_session, RuntimeObservationTarget):
            raise TypeError("RuntimeObservation.target_session must be a target session")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "occurred_at", float(self.occurred_at))
        object.__setattr__(self, "correlation_id", self.correlation_id or None)
        object.__setattr__(self, "payload", _freeze(self.payload))

    @property
    def visible_reply_material(self) -> str:
        return str(self.payload.get("visible_reply_material", "") or "").strip()

    @property
    def is_user_message(self) -> bool:
        return False


__all__ = ["RuntimeObservation", "RuntimeObservationTarget"]
