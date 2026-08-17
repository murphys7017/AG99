from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astrbot.core.platform.message_type import MessageType

from .types import MemoryIdentity, ScopeRef, ScopeType


@dataclass(frozen=True, slots=True)
class MemoryScopeContext:
    """Stable memory scopes derived from one interaction event."""

    user: ScopeRef | None = None
    group: ScopeRef | None = None
    global_scope: ScopeRef = field(
        default_factory=lambda: ScopeRef(ScopeType.GLOBAL, "global")
    )

    def refs(self) -> tuple[ScopeRef, ...]:
        return tuple(
            ref
            for ref in (self.user, self.group, self.global_scope)
            if ref is not None
        )


def resolve_memory_scope_context(
    event: Any,
    identity: MemoryIdentity,
) -> MemoryScopeContext:
    user_scope = (
        ScopeRef(ScopeType.USER, identity.canonical_user_id)
        if identity.canonical_user_id
        else None
    )

    group_scope = None
    message_type = _safe_call(event, "get_message_type")
    group_id = _normalize(_safe_call(event, "get_group_id"))
    if message_type == MessageType.GROUP_MESSAGE and group_id:
        platform_scope = identity.platform_id or _normalize(
            _safe_call(event, "get_platform_name")
        )
        scope_id = f"{platform_scope}:{group_id}" if platform_scope else group_id
        group_scope = ScopeRef(ScopeType.GROUP, scope_id)

    return MemoryScopeContext(user=user_scope, group=group_scope)


def scope_context_to_dict(context: MemoryScopeContext | None) -> dict[str, Any]:
    if context is None:
        return {}
    return {
        "user": _scope_ref_to_dict(context.user),
        "group": _scope_ref_to_dict(context.group),
        "global": _scope_ref_to_dict(context.global_scope),
    }


def scope_context_from_dict(value: Any) -> MemoryScopeContext | None:
    if not isinstance(value, dict) or not value:
        return None
    return MemoryScopeContext(
        user=_scope_ref_from_dict(value.get("user")),
        group=_scope_ref_from_dict(value.get("group")),
        global_scope=_scope_ref_from_dict(value.get("global"))
        or ScopeRef(ScopeType.GLOBAL, "global"),
    )


def _scope_ref_to_dict(ref: ScopeRef | None) -> dict[str, str] | None:
    if ref is None:
        return None
    return {
        "scope_type": _enum_value(ref.scope_type),
        "scope_id": ref.scope_id,
    }


def _scope_ref_from_dict(value: Any) -> ScopeRef | None:
    if not isinstance(value, dict):
        return None
    scope_type = _normalize(value.get("scope_type"))
    scope_id = _normalize(value.get("scope_id"))
    if not scope_type or not scope_id:
        return None
    return ScopeRef(scope_type, scope_id)


def _safe_call(event: Any, name: str) -> Any:
    method = getattr(event, name, None)
    return method() if callable(method) else None


def _normalize(value: Any) -> str:
    return str(value or "").strip()


def _enum_value(value: ScopeType | str) -> str:
    return value.value if hasattr(value, "value") else str(value)


__all__ = [
    "MemoryScopeContext",
    "resolve_memory_scope_context",
    "scope_context_from_dict",
    "scope_context_to_dict",
]
