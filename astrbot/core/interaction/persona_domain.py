"""Read-only persona domain models and compatibility adapters.

The models in this module are snapshots only. Mutable runtime state remains
owned by Personal Runtime, while Memory and PersonaCollector remain the
authoritative sources for relationship and static persona data.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

from astrbot.core.memory.types import MemorySnapshot, PersonaState
from astrbot.core.prompt.context_types import ContextSlot

from .personal_state import (
    PersonalAttentionState,
    PersonalAvailabilityState,
    PersonalPersistentState,
    PersonalStateSnapshot,
)

PERSONA_DOMAIN_SCHEMA_VERSION = "ag99.persona.v1"


def _enum_value(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def _coerce_enum(value: object, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid {enum_type.__name__}") from exc


def _required_text(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be str or None")
    return value.strip() or None


def _optional_finite_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} must be numeric or None")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} must be finite")
    return normalized


def _non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _freeze(value: Any) -> Any:
    """Freeze JSON-like values so snapshots cannot share mutable state."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("persona snapshot floats must be finite")
    if value is None or isinstance(value, str | int | float | bool | datetime):
        return value
    raise TypeError(f"Unsupported persona snapshot value: {type(value)!r}")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple | list | frozenset | set):
        return [_thaw(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _normalize_allowlist(value: object, field_name: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} must be a sequence or None")
    normalized: list[str] = []
    for item in value:
        item_text = _required_text(item, field_name)
        if item_text not in normalized:
            normalized.append(item_text)
    return tuple(normalized)


def _scoped_identifier_hash(scope_type: str, scope_id: str) -> str:
    raw = "\x00".join(
        (
            _required_text(scope_type, "scope_type"),
            _required_text(scope_id, "scope_id"),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_persona_scope_key(
    *,
    config_id: str,
    persona_id: str,
    audience_key: str,
    privacy_scope: str,
) -> str:
    """Build a stable, non-readable key for one effective persona scope."""
    raw = "\x00".join(
        _required_text(value, field_name)
        for value, field_name in (
            (config_id, "config_id"),
            (persona_id, "persona_id"),
            (audience_key, "audience_key"),
            (privacy_scope, "privacy_scope"),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PersonaDefinition:
    """Immutable static persona material adapted from PersonaCollector slots."""

    persona_id: str
    prompt: str | None = None
    segments: Mapping[str, Any] = field(default_factory=dict)
    begin_dialogs: tuple[Any, ...] = ()
    tools: tuple[str, ...] | None = None
    skills: tuple[str, ...] | None = None
    force_applied: bool = False
    webchat_special_default: bool = False
    schema_version: str = PERSONA_DOMAIN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "persona_id", _required_text(self.persona_id, "persona_id")
        )
        if self.prompt is not None:
            if not isinstance(self.prompt, str):
                raise TypeError("prompt must be str or None")
            object.__setattr__(self, "prompt", self.prompt.strip() or None)
        if not isinstance(self.segments, Mapping):
            raise TypeError("segments must be a mapping")
        object.__setattr__(self, "segments", _freeze(self.segments))
        if isinstance(self.begin_dialogs, str | bytes) or not isinstance(
            self.begin_dialogs,
            Sequence,
        ):
            raise TypeError("begin_dialogs must be a sequence")
        object.__setattr__(
            self,
            "begin_dialogs",
            tuple(_freeze(item) for item in self.begin_dialogs),
        )
        object.__setattr__(
            self,
            "tools",
            _normalize_allowlist(self.tools, "tools"),
        )
        object.__setattr__(
            self,
            "skills",
            _normalize_allowlist(self.skills, "skills"),
        )
        object.__setattr__(
            self,
            "schema_version",
            _required_text(self.schema_version, "schema_version"),
        )

    @classmethod
    def from_context_slots(
        cls, slots: Sequence[ContextSlot]
    ) -> PersonaDefinition | None:
        by_name = {slot.name: slot for slot in slots}
        persona_slots = [
            slot for slot in by_name.values() if slot.name.startswith("persona.")
        ]
        persona_slot = next(
            (
                slot
                for slot in persona_slots
                if isinstance(slot.meta, Mapping) and slot.meta.get("persona_id")
            ),
            persona_slots[0] if persona_slots else None,
        )
        if persona_slot is None:
            return None
        meta = persona_slot.meta if isinstance(persona_slot.meta, Mapping) else {}
        persona_id = meta.get("persona_id")
        if persona_id is None:
            return None

        prompt_slot = by_name.get("persona.prompt")
        segments_slot = by_name.get("persona.segments")
        dialogs_slot = by_name.get("persona.begin_dialogs")
        tools_slot = by_name.get("persona.tools_whitelist")
        skills_slot = by_name.get("persona.skills_whitelist")
        segments = (
            segments_slot.value
            if segments_slot is not None and isinstance(segments_slot.value, Mapping)
            else {}
        )
        begin_dialogs = (
            dialogs_slot.value
            if dialogs_slot is not None
            and isinstance(dialogs_slot.value, Sequence)
            and not isinstance(dialogs_slot.value, str | bytes)
            else ()
        )
        return cls(
            persona_id=str(persona_id),
            prompt=(
                prompt_slot.value
                if prompt_slot is not None and isinstance(prompt_slot.value, str)
                else None
            ),
            segments=segments,
            begin_dialogs=tuple(begin_dialogs),
            tools=tools_slot.value if tools_slot is not None else None,
            skills=skills_slot.value if skills_slot is not None else None,
            force_applied=bool(meta.get("force_applied", False)),
            webchat_special_default=bool(
                meta.get("use_webchat_special_default", False)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "persona_id": self.persona_id,
            "prompt": self.prompt,
            "segments": _thaw(self.segments),
            "begin_dialogs": _thaw(self.begin_dialogs),
            "tools": list(self.tools) if self.tools is not None else None,
            "skills": list(self.skills) if self.skills is not None else None,
            "force_applied": self.force_applied,
            "webchat_special_default": self.webchat_special_default,
        }


@dataclass(frozen=True, slots=True)
class PersonaRelationshipState:
    """Immutable relationship state adapted from Memory's PersonaState."""

    scope_type: str
    scope_id: str
    persona_id: str | None
    familiarity: float = 0.0
    trust: float = 0.5
    warmth: float = 0.5
    formality_preference: float = 0.5
    directness_preference: float = 0.5
    updated_at: datetime | None = None
    schema_version: str = PERSONA_DOMAIN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "scope_type",
            _required_text(self.scope_type, "scope_type"),
        )
        object.__setattr__(
            self,
            "scope_id",
            _required_text(self.scope_id, "scope_id"),
        )
        object.__setattr__(
            self,
            "persona_id",
            _optional_text(self.persona_id, "persona_id"),
        )
        if self.updated_at is not None and not isinstance(self.updated_at, datetime):
            raise TypeError("updated_at must be datetime or None")
        for field_name in (
            "familiarity",
            "trust",
            "warmth",
            "formality_preference",
            "directness_preference",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise TypeError(f"{field_name} must be numeric")
            normalized = float(value)
            if not math.isfinite(normalized):
                raise ValueError(f"{field_name} must be finite")
            object.__setattr__(self, field_name, max(0.0, min(1.0, normalized)))
        object.__setattr__(
            self,
            "schema_version",
            _required_text(self.schema_version, "schema_version"),
        )

    @classmethod
    def from_memory_state(
        cls, state: PersonaState | None
    ) -> PersonaRelationshipState | None:
        if state is None:
            return None
        return cls(
            scope_type=str(_enum_value(state.scope_type)),
            scope_id=state.scope_id,
            persona_id=state.persona_id,
            familiarity=state.familiarity,
            trust=state.trust,
            warmth=state.warmth,
            formality_preference=state.formality_preference,
            directness_preference=state.directness_preference,
            updated_at=state.updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope_type": self.scope_type,
            "scope_id_hash": _scoped_identifier_hash(self.scope_type, self.scope_id),
            "persona_id": self.persona_id,
            "familiarity": self.familiarity,
            "trust": self.trust,
            "warmth": self.warmth,
            "formality_preference": self.formality_preference,
            "directness_preference": self.directness_preference,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass(frozen=True, slots=True)
class RuntimeControlSnapshot:
    """Immutable domain view over the existing PersonalStateSnapshot."""

    attention_state: PersonalAttentionState
    availability_state: PersonalAvailabilityState
    last_observation_at: float | None
    last_user_activity_at: float | None
    last_expression_at: float | None
    reply_cooldown_until: float | None
    no_action_cooldown_until: float | None
    mute_until: float | None
    pending_observation_count: int
    material_revision: int
    last_settled_material_revision: int
    usage_day: str | None
    daily_policy_calls: int
    daily_proactive_outputs: int
    last_gate_reason: str | None
    last_policy_action: str | None
    schema_version: str = PERSONA_DOMAIN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "attention_state",
            _coerce_enum(
                self.attention_state,
                PersonalAttentionState,
                "attention_state",
            ),
        )
        object.__setattr__(
            self,
            "availability_state",
            _coerce_enum(
                self.availability_state,
                PersonalAvailabilityState,
                "availability_state",
            ),
        )
        for field_name in (
            "last_observation_at",
            "last_user_activity_at",
            "last_expression_at",
            "reply_cooldown_until",
            "no_action_cooldown_until",
            "mute_until",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_finite_float(getattr(self, field_name), field_name),
            )
        for field_name in (
            "pending_observation_count",
            "material_revision",
            "last_settled_material_revision",
            "daily_policy_calls",
            "daily_proactive_outputs",
        ):
            object.__setattr__(
                self,
                field_name,
                _non_negative_int(getattr(self, field_name), field_name),
            )
        if self.last_settled_material_revision > self.material_revision:
            raise ValueError(
                "last_settled_material_revision cannot exceed material_revision"
            )
        object.__setattr__(
            self,
            "usage_day",
            _optional_text(self.usage_day, "usage_day"),
        )
        object.__setattr__(
            self,
            "last_gate_reason",
            _optional_text(self.last_gate_reason, "last_gate_reason"),
        )
        object.__setattr__(
            self,
            "last_policy_action",
            _optional_text(self.last_policy_action, "last_policy_action"),
        )
        object.__setattr__(
            self,
            "schema_version",
            _required_text(self.schema_version, "schema_version"),
        )

    @classmethod
    def from_personal_state_snapshot(
        cls,
        snapshot: PersonalStateSnapshot,
    ) -> RuntimeControlSnapshot:
        if not isinstance(snapshot, PersonalStateSnapshot):
            raise TypeError("snapshot must be PersonalStateSnapshot")
        values = {
            item.name: getattr(snapshot, item.name)
            for item in fields(PersonalStateSnapshot)
        }
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"schema_version": self.schema_version}
        for item in fields(self):
            if item.name == "schema_version":
                continue
            result[item.name] = _enum_value(getattr(self, item.name))
        return result


@dataclass(frozen=True, slots=True)
class EffectivePersonaContext:
    """Explicit composition of static, relationship, and runtime snapshots."""

    definition: PersonaDefinition
    relationship: PersonaRelationshipState | None = None
    runtime: RuntimeControlSnapshot | None = None
    scope_key: str | None = None
    schema_version: str = PERSONA_DOMAIN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.definition, PersonaDefinition):
            raise TypeError("definition must be PersonaDefinition")
        if self.relationship is not None and not isinstance(
            self.relationship,
            PersonaRelationshipState,
        ):
            raise TypeError("relationship must be PersonaRelationshipState or None")
        if self.runtime is not None and not isinstance(
            self.runtime,
            RuntimeControlSnapshot,
        ):
            raise TypeError("runtime must be RuntimeControlSnapshot or None")
        if self.scope_key is not None:
            object.__setattr__(
                self, "scope_key", _required_text(self.scope_key, "scope_key")
            )
        object.__setattr__(
            self,
            "schema_version",
            _required_text(self.schema_version, "schema_version"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope_key": self.scope_key,
            "definition": self.definition.to_dict(),
            "relationship": (
                self.relationship.to_dict() if self.relationship is not None else None
            ),
            "runtime": (self.runtime.to_dict() if self.runtime is not None else None),
        }


def adapt_personal_persistent_state(
    state: PersonalPersistentState,
) -> Mapping[str, object]:
    """Expose persisted control fields without returning the mutable owner."""
    if not isinstance(state, PersonalPersistentState):
        raise TypeError("state must be PersonalPersistentState")
    return MappingProxyType(
        {
            "schema_version": PERSONA_DOMAIN_SCHEMA_VERSION,
            "last_user_activity_at": state.last_user_activity_at,
            "last_idle_initiation_activity_at": state.last_idle_initiation_activity_at,
            "last_expression_at": state.last_expression_at,
            "last_expression_fingerprint": state.last_expression_fingerprint,
            "reply_cooldown_until": state.reply_cooldown_until,
            "no_action_cooldown_until": state.no_action_cooldown_until,
            "mute_until": state.mute_until,
            "usage_day": state.usage_day,
            "daily_policy_calls": state.daily_policy_calls,
            "daily_proactive_outputs": state.daily_proactive_outputs,
        }
    )


def adapt_persona_collector_slots(
    slots: Sequence[ContextSlot],
) -> PersonaDefinition | None:
    """Adapt PersonaCollector output without retaining slot metadata or owners."""
    return PersonaDefinition.from_context_slots(slots)


def adapt_memory_snapshot(
    snapshot: MemorySnapshot | None,
) -> PersonaRelationshipState | None:
    """Extract only relationship state from a Memory snapshot."""
    if snapshot is None:
        return None
    if not isinstance(snapshot, MemorySnapshot):
        raise TypeError("snapshot must be MemorySnapshot or None")
    return PersonaRelationshipState.from_memory_state(snapshot.persona_state)


def build_effective_persona_context(
    *,
    definition: PersonaDefinition,
    memory_snapshot: MemorySnapshot | None = None,
    runtime_snapshot: PersonalStateSnapshot | None = None,
    scope_key: str | None = None,
) -> EffectivePersonaContext:
    """Compose read-only adapters while keeping existing owners authoritative."""
    return EffectivePersonaContext(
        definition=definition,
        relationship=adapt_memory_snapshot(memory_snapshot),
        runtime=(
            RuntimeControlSnapshot.from_personal_state_snapshot(runtime_snapshot)
            if runtime_snapshot is not None
            else None
        ),
        scope_key=scope_key,
    )


__all__ = [
    "EffectivePersonaContext",
    "PERSONA_DOMAIN_SCHEMA_VERSION",
    "PersonaDefinition",
    "PersonaRelationshipState",
    "RuntimeControlSnapshot",
    "adapt_memory_snapshot",
    "adapt_persona_collector_slots",
    "adapt_personal_persistent_state",
    "build_effective_persona_context",
    "build_persona_scope_key",
]
