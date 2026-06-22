from __future__ import annotations

import copy
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

PersonaExpressionPhase = Literal[
    "first_response",
    "executor_started",
    "executor_progress",
    "executor_result",
    "plugin_output",
    "final_response",
]

PERSONA_EXPRESSION_PHASES: frozenset[str] = frozenset(
    (
        "first_response",
        "executor_started",
        "executor_progress",
        "executor_result",
        "plugin_output",
        "final_response",
    )
)

_EFFECT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


@dataclass(slots=True)
class PersonaEffectSpec:
    plugin_id: str
    name: str
    description: str
    parameters: dict[str, Any]
    phases: tuple[PersonaExpressionPhase, ...] = ()
    legacy_hint_names: tuple[str, ...] = ()
    priority: int = 100
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PersonaEffectCall:
    name: str
    arguments: dict[str, Any]
    call_id: str | None = None
    plugin_id: str | None = None
    source: str = "persona"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: object) -> PersonaEffectCall | None:
        if not isinstance(payload, dict):
            return None
        name = str(payload.get("name", "") or "").strip()
        arguments = payload.get("arguments", {})
        if not name or not isinstance(arguments, dict):
            return None
        call_id = payload.get("call_id")
        plugin_id = payload.get("plugin_id")
        return cls(
            name=name,
            arguments=copy.deepcopy(arguments),
            call_id=str(call_id) if call_id is not None else None,
            plugin_id=str(plugin_id) if plugin_id is not None else None,
            source=str(payload.get("source", "") or "persona"),
            metadata=(
                copy.deepcopy(payload.get("metadata", {}))
                if isinstance(payload.get("metadata", {}), dict)
                else {}
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": copy.deepcopy(self.arguments),
            "call_id": self.call_id,
            "plugin_id": self.plugin_id,
            "source": self.source,
            "metadata": copy.deepcopy(self.metadata),
        }


@dataclass(slots=True)
class PersonaEffectParseIssue:
    index: int
    name: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "reason": self.reason,
        }


class PersonaEffectRegistryError(ValueError):
    """Raised when a persona effect registration is invalid or conflicts."""


class PersonaEffectValidationError(ValueError):
    """Raised when a persona effect call does not match its registered schema."""


def validate_persona_effect_spec(effect: PersonaEffectSpec) -> None:
    if not isinstance(effect.plugin_id, str) or not effect.plugin_id.strip():
        raise PersonaEffectRegistryError("Persona effect plugin_id must be non-empty")
    if not isinstance(effect.name, str) or not effect.name.strip():
        raise PersonaEffectRegistryError("Persona effect name must be non-empty")
    if not _EFFECT_NAME_PATTERN.match(effect.name):
        raise PersonaEffectRegistryError(
            f"Persona effect name is invalid: {effect.name!r}"
        )
    if not isinstance(effect.description, str):
        raise PersonaEffectRegistryError("Persona effect description must be a string")
    if not _is_valid_parameters_schema(effect.parameters):
        raise PersonaEffectRegistryError(
            "Persona effect parameters must be an object JSON schema"
        )
    for phase in effect.phases:
        if phase not in PERSONA_EXPRESSION_PHASES:
            raise PersonaEffectRegistryError(
                f"Persona effect phase is invalid: {phase!r}"
            )
    seen_aliases: set[str] = set()
    for alias in effect.legacy_hint_names:
        if not isinstance(alias, str) or not alias.strip():
            raise PersonaEffectRegistryError(
                "Persona effect legacy hint names must be non-empty strings"
            )
        if alias in seen_aliases:
            raise PersonaEffectRegistryError(
                f"Persona effect legacy hint name is duplicated: {alias!r}"
            )
        seen_aliases.add(alias)


def clone_persona_effect_spec(effect: PersonaEffectSpec) -> PersonaEffectSpec:
    return PersonaEffectSpec(
        plugin_id=effect.plugin_id,
        name=effect.name,
        description=effect.description,
        parameters=copy.deepcopy(effect.parameters),
        phases=tuple(effect.phases),
        legacy_hint_names=tuple(effect.legacy_hint_names),
        priority=int(effect.priority),
        enabled=bool(effect.enabled),
        metadata=copy.deepcopy(effect.metadata),
    )


def persona_effect_applies_to_phase(
    effect: PersonaEffectSpec,
    phase: str | None,
) -> bool:
    if not effect.enabled:
        return False
    if phase is None or not effect.phases:
        return True
    return phase in effect.phases


def legacy_plugin_hints_to_effect_calls(
    plugin_hints: dict[str, Any],
    effects: list[PersonaEffectSpec],
) -> list[PersonaEffectCall]:
    if not isinstance(plugin_hints, dict):
        return []

    by_name: dict[str, PersonaEffectSpec] = {}
    by_alias: dict[str, PersonaEffectSpec] = {}
    for effect in effects:
        if not effect.enabled:
            continue
        by_name[effect.name] = effect
        for alias in effect.legacy_hint_names:
            by_alias[alias] = effect

    calls: list[PersonaEffectCall] = []
    for hint_name, arguments in plugin_hints.items():
        effect = by_name.get(hint_name) or by_alias.get(hint_name)
        if effect is None or not isinstance(arguments, dict):
            continue
        calls.append(
            PersonaEffectCall(
                name=effect.name,
                arguments=copy.deepcopy(arguments),
                plugin_id=effect.plugin_id,
                source="legacy_plugin_hints",
            )
        )
    return calls


def effect_calls_to_legacy_plugin_hints(
    effect_calls: Sequence[PersonaEffectCall],
    effects: Sequence[PersonaEffectSpec],
) -> dict[str, Any]:
    if not effect_calls:
        return {}

    effects_by_name = {
        effect.name: effect
        for effect in effects
        if effect.enabled and effect.legacy_hint_names
    }
    hints: dict[str, Any] = {}
    for call in effect_calls:
        if not isinstance(call, PersonaEffectCall):
            continue
        effect = effects_by_name.get(call.name)
        if effect is None:
            continue
        alias = effect.legacy_hint_names[0]
        hints.setdefault(alias, copy.deepcopy(call.arguments))
    return hints


def parse_persona_effect_calls(
    raw_calls: object,
    effects: Sequence[PersonaEffectSpec],
) -> list[PersonaEffectCall]:
    calls, _issues = parse_persona_effect_calls_with_issues(raw_calls, effects)
    return calls


def parse_persona_effect_calls_with_issues(
    raw_calls: object,
    effects: Sequence[PersonaEffectSpec],
) -> tuple[list[PersonaEffectCall], list[PersonaEffectParseIssue]]:
    if not isinstance(raw_calls, list):
        return [], [
            PersonaEffectParseIssue(
                index=-1,
                name="",
                reason="effect_calls_not_array",
            )
        ]

    effects_by_name = {
        effect.name: effect
        for effect in effects
        if effect.enabled
    }
    calls: list[PersonaEffectCall] = []
    issues: list[PersonaEffectParseIssue] = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            issues.append(
                PersonaEffectParseIssue(
                    index=index,
                    name="",
                    reason="effect_call_not_object",
                )
            )
            continue
        name = str(raw_call.get("name", "") or "").strip()
        effect = effects_by_name.get(name)
        if effect is None:
            issues.append(
                PersonaEffectParseIssue(
                    index=index,
                    name=name,
                    reason="unknown_effect_name",
                )
            )
            continue
        arguments = raw_call.get("arguments", {})
        if not isinstance(arguments, dict):
            issues.append(
                PersonaEffectParseIssue(
                    index=index,
                    name=name,
                    reason="arguments_not_object",
                )
            )
            continue
        try:
            validate_persona_effect_arguments(arguments, effect.parameters)
        except PersonaEffectValidationError as exc:
            issues.append(
                PersonaEffectParseIssue(
                    index=index,
                    name=name,
                    reason=str(exc) or "arguments_invalid",
                )
            )
            continue
        calls.append(
            PersonaEffectCall(
                name=effect.name,
                arguments=copy.deepcopy(arguments),
                call_id=(
                    str(raw_call.get("call_id"))
                    if raw_call.get("call_id") is not None
                    else None
                ),
                plugin_id=effect.plugin_id,
                source="persona",
            )
        )
    return calls, issues


def validate_persona_effect_arguments(
    arguments: dict[str, Any],
    schema: dict[str, Any],
) -> None:
    if not isinstance(arguments, dict):
        raise PersonaEffectValidationError("arguments must be an object")
    if not _is_valid_parameters_schema(schema):
        raise PersonaEffectValidationError("schema must be an object schema")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required", [])
    if not isinstance(required, list):
        required = []
    for key in required:
        if key not in arguments:
            raise PersonaEffectValidationError(f"missing required argument: {key}")
    for key, value in arguments.items():
        property_schema = properties.get(key)
        if isinstance(property_schema, dict):
            _validate_json_schema_type(value, property_schema, path=key)


def _validate_json_schema_type(value: Any, schema: dict[str, Any], *, path: str) -> None:
    schema_type = schema.get("type")
    if schema_type is None:
        return
    if isinstance(schema_type, list):
        if any(_json_schema_type_matches(value, item) for item in schema_type):
            return
        raise PersonaEffectValidationError(f"invalid type for {path}")
    if isinstance(schema_type, str) and not _json_schema_type_matches(value, schema_type):
        raise PersonaEffectValidationError(f"invalid type for {path}")


def _json_schema_type_matches(value: Any, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return True


def _is_valid_parameters_schema(schema: object) -> bool:
    if not isinstance(schema, dict):
        return False
    if schema.get("type") != "object":
        return False
    properties = schema.get("properties", {})
    if properties is not None and not isinstance(properties, dict):
        return False
    required = schema.get("required", [])
    return required is None or isinstance(required, list)
