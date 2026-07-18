from __future__ import annotations

import copy
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

_EFFECT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


@dataclass(slots=True)
class PersonaEffectSpec:
    plugin_id: str
    name: str
    description: str
    parameters: dict[str, Any]
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


def normalize_persona_effect_parameters_schema(schema: object) -> dict[str, Any]:
    if not _is_valid_parameters_schema(schema):
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    return _normalize_persona_effect_schema(copy.deepcopy(schema), path=())


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
def clone_persona_effect_spec(effect: PersonaEffectSpec) -> PersonaEffectSpec:
    return PersonaEffectSpec(
        plugin_id=effect.plugin_id,
        name=effect.name,
        description=effect.description,
        parameters=copy.deepcopy(effect.parameters),
        priority=int(effect.priority),
        enabled=bool(effect.enabled),
        metadata=copy.deepcopy(effect.metadata),
    )


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
        normalized_schema = normalize_persona_effect_parameters_schema(effect.parameters)
        normalized_arguments = normalize_persona_effect_arguments(
            copy.deepcopy(arguments),
            normalized_schema,
        )
        try:
            validate_persona_effect_arguments(
                normalized_arguments,
                normalized_schema,
            )
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
                arguments=normalized_arguments,
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
        if not isinstance(property_schema, dict) and isinstance(
            schema.get("additionalProperties"),
            dict,
        ):
            property_schema = schema["additionalProperties"]
        if not isinstance(property_schema, dict) and schema.get(
            "additionalProperties"
        ) is False:
            raise PersonaEffectValidationError(f"unexpected argument: {key}")
        if isinstance(property_schema, dict):
            _validate_json_schema_value(value, property_schema, path=key)


def normalize_persona_effect_arguments(
    arguments: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(arguments, dict) or not _is_valid_parameters_schema(schema):
        return arguments
    normalized = copy.deepcopy(arguments)
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    additional_properties = schema.get("additionalProperties")
    for key, value in list(normalized.items()):
        property_schema = properties.get(key)
        if not isinstance(property_schema, dict) and isinstance(additional_properties, dict):
            property_schema = additional_properties
        if isinstance(property_schema, dict):
            normalized[key] = _normalize_json_schema_value(value, property_schema)
    return normalized


def _normalize_persona_effect_schema(
    schema: dict[str, Any],
    *,
    path: tuple[str, ...],
) -> dict[str, Any]:
    normalized = copy.deepcopy(schema)
    schema_type = normalized.get("type")
    if schema_type == "object":
        properties = normalized.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        normalized_properties: dict[str, Any] = {}
        for key, value in properties.items():
            normalized_properties[key] = (
                _normalize_persona_effect_schema(
                    value,
                    path=(*path, str(key)),
                )
                if isinstance(value, dict)
                else value
            )
        normalized["properties"] = normalized_properties
        additional_properties = normalized.get("additionalProperties")
        if isinstance(additional_properties, dict):
            normalized["additionalProperties"] = _normalize_persona_effect_schema(
                additional_properties,
                path=(*path, "*"),
            )
        return normalized
    if schema_type == "array":
        items = normalized.get("items")
        if isinstance(items, dict):
            normalized["items"] = _normalize_persona_effect_schema(
                items,
                path=(*path, "[]"),
            )
    return normalized


def _validate_json_schema_value(value: Any, schema: dict[str, Any], *, path: str) -> None:
    schema_type = schema.get("type")
    if schema_type is None:
        return
    if isinstance(schema_type, list):
        if any(_json_schema_type_matches(value, item) for item in schema_type):
            _validate_typed_json_schema_value(value, schema, path=path)
            return
        raise PersonaEffectValidationError(f"invalid type for {path}")
    if isinstance(schema_type, str):
        if not _json_schema_type_matches(value, schema_type):
            raise PersonaEffectValidationError(f"invalid type for {path}")
        _validate_typed_json_schema_value(value, schema, path=path)


def _validate_typed_json_schema_value(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str,
) -> None:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next(
            (item for item in schema_type if _json_schema_type_matches(value, item)),
            None,
        )
    if schema_type == "object":
        if not isinstance(value, dict):
            raise PersonaEffectValidationError(f"invalid type for {path}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        required = schema.get("required", [])
        if not isinstance(required, list):
            required = []
        for key in required:
            if key not in value:
                raise PersonaEffectValidationError(
                    f"missing required argument: {path}.{key}"
                )
        additional_properties = schema.get("additionalProperties")
        for key, item in value.items():
            property_schema = properties.get(key)
            if not isinstance(property_schema, dict) and isinstance(
                additional_properties, dict
            ):
                property_schema = additional_properties
            if not isinstance(property_schema, dict) and additional_properties is False:
                raise PersonaEffectValidationError(
                    f"unexpected argument: {path}.{key}"
                )
            if isinstance(property_schema, dict):
                _validate_json_schema_value(
                    item,
                    property_schema,
                    path=f"{path}.{key}",
                )
        return
    if schema_type == "array":
        if not isinstance(value, list):
            raise PersonaEffectValidationError(f"invalid type for {path}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_json_schema_value(
                    item,
                    item_schema,
                    path=f"{path}[{index}]",
                )


def _normalize_json_schema_value(value: Any, schema: dict[str, Any]) -> Any:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        for item in schema_type:
            normalized = _normalize_json_schema_value(
                value,
                {**schema, "type": item},
            )
            if _json_schema_type_matches(normalized, item):
                return normalized
        return value
    if schema_type == "number":
        return _coerce_number_like(value)
    if schema_type == "integer":
        return _coerce_integer_like(value)
    if schema_type == "object":
        if not isinstance(value, dict):
            return value
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        additional_properties = schema.get("additionalProperties")
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            property_schema = properties.get(key)
            if not isinstance(property_schema, dict) and isinstance(
                additional_properties, dict
            ):
                property_schema = additional_properties
            normalized[key] = (
                _normalize_json_schema_value(item, property_schema)
                if isinstance(property_schema, dict)
                else item
            )
        return normalized
    if schema_type == "array":
        if not isinstance(value, list):
            return value
        item_schema = schema.get("items")
        if not isinstance(item_schema, dict):
            return list(value)
        return [_normalize_json_schema_value(item, item_schema) for item in value]
    return value


def _coerce_number_like(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return value
        try:
            return float(stripped)
        except ValueError:
            return value
    return value


def _coerce_integer_like(value: Any) -> Any:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return value
        try:
            number = float(stripped)
        except ValueError:
            return value
        if number.is_integer():
            return int(number)
    return value


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
