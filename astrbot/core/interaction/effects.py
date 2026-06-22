from __future__ import annotations

import copy
import re
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


class PersonaEffectRegistryError(ValueError):
    """Raised when a persona effect registration is invalid or conflicts."""


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
