from __future__ import annotations

import copy
import re
from collections.abc import Callable, Sequence
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
    parameters_resolver: Callable[[Any], dict[str, Any]] | None = None


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
    if effect.parameters_resolver is not None and not callable(
        effect.parameters_resolver
    ):
        raise PersonaEffectRegistryError(
            "Persona effect parameters_resolver must be callable"
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
        parameters_resolver=effect.parameters_resolver,
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
    _validate_json_schema_value(arguments, schema, path="")


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
    _validate_json_schema_combinators(value, schema, path=path)
    _validate_json_schema_fixed_values(value, schema, path=path)
    schema_type = schema.get("type")
    if schema_type is None:
        _validate_inferred_json_schema_value(value, schema, path=path)
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
        _validate_json_schema_object(value, schema, path=path)
        return
    if schema_type == "array":
        _validate_json_schema_array(value, schema, path=path)
        return
    if schema_type == "string":
        _validate_json_schema_string(value, schema, path=path)
        return
    if schema_type in {"number", "integer"}:
        _validate_json_schema_number(value, schema, path=path)


def _validate_json_schema_combinators(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str,
) -> None:
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for branch in all_of:
            if isinstance(branch, dict):
                _validate_json_schema_value(value, branch, path=path)

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        if not any(_schema_branch_matches(value, branch, path=path) for branch in any_of):
            raise PersonaEffectValidationError(
                f"value does not match any allowed schema for {_display_schema_path(path)}"
            )

    one_of = schema.get("oneOf")
    if isinstance(one_of, list) and one_of:
        matches = sum(
            _schema_branch_matches(value, branch, path=path) for branch in one_of
        )
        if matches != 1:
            raise PersonaEffectValidationError(
                f"value must match exactly one schema for {_display_schema_path(path)}"
            )

    prohibited = schema.get("not")
    if isinstance(prohibited, dict) and _schema_branch_matches(
        value,
        prohibited,
        path=path,
    ):
        raise PersonaEffectValidationError(
            f"value matches prohibited schema for {_display_schema_path(path)}"
        )


def _schema_branch_matches(value: Any, schema: object, *, path: str) -> bool:
    if not isinstance(schema, dict):
        return False
    try:
        _validate_json_schema_value(value, schema, path=path)
    except PersonaEffectValidationError:
        return False
    return True


def _validate_json_schema_fixed_values(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str,
) -> None:
    if "const" in schema and value != schema["const"]:
        raise PersonaEffectValidationError(
            f"invalid constant value for {_display_schema_path(path)}"
        )
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise PersonaEffectValidationError(
            f"value is not allowed for {_display_schema_path(path)}"
        )


def _validate_inferred_json_schema_value(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str,
) -> None:
    if isinstance(value, dict) and any(
        key in schema
        for key in (
            "properties",
            "required",
            "additionalProperties",
            "minProperties",
            "maxProperties",
        )
    ):
        _validate_json_schema_object(value, schema, path=path)
    elif isinstance(value, list) and any(
        key in schema for key in ("items", "minItems", "maxItems", "uniqueItems")
    ):
        _validate_json_schema_array(value, schema, path=path)
    elif isinstance(value, str) and any(
        key in schema for key in ("minLength", "maxLength")
    ):
        _validate_json_schema_string(value, schema, path=path)
    elif _is_json_schema_number(value) and any(
        key in schema
        for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
    ):
        _validate_json_schema_number(value, schema, path=path)


def _validate_json_schema_object(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str,
) -> None:
    if not isinstance(value, dict):
        raise PersonaEffectValidationError(f"invalid type for {_display_schema_path(path)}")
    _validate_count_bounds(
        len(value),
        schema,
        minimum_key="minProperties",
        maximum_key="maxProperties",
        path=path,
        label="properties",
    )
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required", [])
    if not isinstance(required, list):
        required = []
    for key in required:
        if key not in value:
            raise PersonaEffectValidationError(
                f"missing required argument: {_join_schema_path(path, key)}"
            )
    additional_properties = schema.get("additionalProperties")
    for key, item in value.items():
        property_schema = properties.get(key)
        if not isinstance(property_schema, dict) and isinstance(
            additional_properties,
            dict,
        ):
            property_schema = additional_properties
        if not isinstance(property_schema, dict) and additional_properties is False:
            raise PersonaEffectValidationError(
                f"unexpected argument: {_join_schema_path(path, key)}"
            )
        if isinstance(property_schema, dict):
            _validate_json_schema_value(
                item,
                property_schema,
                path=_join_schema_path(path, key),
            )


def _validate_json_schema_array(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str,
) -> None:
    if not isinstance(value, list):
        raise PersonaEffectValidationError(f"invalid type for {_display_schema_path(path)}")
    _validate_count_bounds(
        len(value),
        schema,
        minimum_key="minItems",
        maximum_key="maxItems",
        path=path,
        label="items",
    )
    if schema.get("uniqueItems") is True:
        for index, item in enumerate(value):
            if any(item == earlier for earlier in value[:index]):
                raise PersonaEffectValidationError(
                    f"duplicate item for {_display_schema_path(path)}"
                )
    item_schema = schema.get("items")
    if isinstance(item_schema, dict):
        for index, item in enumerate(value):
            _validate_json_schema_value(
                item,
                item_schema,
                path=f"{path}[{index}]",
            )
    elif item_schema is False and value:
        raise PersonaEffectValidationError(
            f"items are not allowed for {_display_schema_path(path)}"
        )


def _validate_json_schema_string(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str,
) -> None:
    if not isinstance(value, str):
        raise PersonaEffectValidationError(f"invalid type for {_display_schema_path(path)}")
    _validate_count_bounds(
        len(value),
        schema,
        minimum_key="minLength",
        maximum_key="maxLength",
        path=path,
        label="characters",
    )


def _validate_json_schema_number(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str,
) -> None:
    if not _is_json_schema_number(value):
        raise PersonaEffectValidationError(f"invalid type for {_display_schema_path(path)}")
    _validate_number_bounds(value, schema, path=path)


def _validate_count_bounds(
    count: int,
    schema: dict[str, Any],
    *,
    minimum_key: str,
    maximum_key: str,
    path: str,
    label: str,
) -> None:
    minimum = schema.get(minimum_key)
    if isinstance(minimum, int) and not isinstance(minimum, bool) and count < minimum:
        raise PersonaEffectValidationError(
            f"too few {label} for {_display_schema_path(path)}"
        )
    maximum = schema.get(maximum_key)
    if isinstance(maximum, int) and not isinstance(maximum, bool) and count > maximum:
        raise PersonaEffectValidationError(
            f"too many {label} for {_display_schema_path(path)}"
        )


def _validate_number_bounds(value: int | float, schema: dict[str, Any], *, path: str) -> None:
    minimum = schema.get("minimum")
    if _is_json_schema_number(minimum) and value < minimum:
        raise PersonaEffectValidationError(
            f"value is below minimum for {_display_schema_path(path)}"
        )
    maximum = schema.get("maximum")
    if _is_json_schema_number(maximum) and value > maximum:
        raise PersonaEffectValidationError(
            f"value is above maximum for {_display_schema_path(path)}"
        )
    exclusive_minimum = schema.get("exclusiveMinimum")
    if _is_json_schema_number(exclusive_minimum) and value <= exclusive_minimum:
        raise PersonaEffectValidationError(
            f"value is below exclusive minimum for {_display_schema_path(path)}"
        )
    exclusive_maximum = schema.get("exclusiveMaximum")
    if _is_json_schema_number(exclusive_maximum) and value >= exclusive_maximum:
        raise PersonaEffectValidationError(
            f"value is above exclusive maximum for {_display_schema_path(path)}"
        )


def _is_json_schema_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _join_schema_path(path: str, key: object) -> str:
    key_text = str(key)
    return f"{path}.{key_text}" if path else key_text


def _display_schema_path(path: str) -> str:
    return path or "arguments"


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
