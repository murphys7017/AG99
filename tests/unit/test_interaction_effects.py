import pytest

from astrbot.core.interaction.effects import (
    PersonaEffectCall,
    PersonaEffectRegistryError,
    PersonaEffectSpec,
    normalize_persona_effect_arguments,
    normalize_persona_effect_parameters_schema,
    parse_persona_effect_calls,
    parse_persona_effect_calls_with_issues,
)
from astrbot.core.interaction.expression_agent import (
    InteractionExpressionAgent,
    build_persona_expression_tool_parameters,
)
from astrbot.core.star.context import Context


def _effect(
    name: str = "ag99live.motion",
    *,
    plugin_id: str = "plugin_a",
    priority: int = 100,
    enabled: bool = True,
    metadata: dict | None = None,
) -> PersonaEffectSpec:
    return PersonaEffectSpec(
        plugin_id=plugin_id,
        name=name,
        description="Live2D motion intent",
        parameters={
            "type": "object",
            "properties": {"axes": {"type": "object"}},
            "required": [],
        },
        priority=priority,
        enabled=enabled,
        metadata=metadata or {"internal": "not-for-prompt"},
    )


def _context() -> Context:
    return Context.__new__(Context)


def _init_effect_registry(ctx: Context) -> Context:
    ctx._persona_effects = []
    ctx._persona_effect_seq = 0
    return ctx


def test_empty_effect_list_does_not_generate_effect_calls_schema():
    schema = build_persona_expression_tool_parameters()

    assert schema["properties"]["effect_calls"] == {"type": "array", "items": False}
    assert "metadata" not in schema["properties"]
    assert schema["required"] == ["spoken_reply", "speech_cues", "effect_calls"]


def test_effect_schema_freezes_effect_call_shape_per_effect():
    effects = [
        _effect("voice.emotion", plugin_id="plugin_b"),
        _effect("ag99live.motion", plugin_id="plugin_a"),
    ]

    schema = build_persona_expression_tool_parameters(effects)
    effect_calls = schema["properties"]["effect_calls"]
    variants = effect_calls["items"]["oneOf"]

    assert [variant["properties"]["name"]["const"] for variant in variants] == [
        "ag99live.motion",
        "voice.emotion",
    ]
    assert all(variant["additionalProperties"] is False for variant in variants)
    assert all(variant["required"] == ["name", "arguments"] for variant in variants)


def test_required_effect_metadata_constrains_call_count():
    effect = _effect(
        metadata={
            "required_per_segment": True,
            "exactly_one_per_segment": True,
        }
    )

    effect_calls = build_persona_expression_tool_parameters([effect])["properties"][
        "effect_calls"
    ]

    assert effect_calls["minItems"] == 1
    assert effect_calls["maxItems"] == 1


def test_multiple_required_effects_do_not_create_contradictory_call_bounds():
    effects = [
        _effect(
            "face.expression",
            metadata={
                "required_per_segment": True,
                "exactly_one_per_segment": True,
            },
        ),
        _effect(
            "body.motion",
            metadata={
                "required_per_segment": True,
                "exactly_one_per_segment": True,
            },
        ),
    ]

    effect_calls = build_persona_expression_tool_parameters(effects)["properties"][
        "effect_calls"
    ]

    assert effect_calls["minItems"] == 2
    assert "maxItems" not in effect_calls


def test_effect_schema_does_not_mutate_plugin_parameters_or_include_metadata():
    effect = _effect(metadata={"secret_prompt_hint": "nope"})
    original_parameters = {
        "type": "object",
        "properties": {"axes": {"type": "object"}},
        "required": [],
    }
    effect.parameters = original_parameters

    schema = build_persona_expression_tool_parameters([effect])

    assert effect.parameters == original_parameters
    assert "secret_prompt_hint" not in str(schema)


def test_effect_schema_preserves_plugin_declared_field_types():
    normalized = normalize_persona_effect_parameters_schema(
        {
            "type": "object",
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "payload": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer"},
                        "label": {"type": "string"},
                    },
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["labels", "payload"],
        }
    )

    assert normalized["properties"]["payload"]["properties"]["count"]["type"] == "integer"
    assert normalized["properties"]["payload"]["properties"]["label"]["type"] == "string"
    assert normalized["properties"]["payload"]["additionalProperties"]["type"] == "string"


def test_context_rejects_duplicate_effect_name():
    ctx = _init_effect_registry(_context())
    ctx.register_persona_effect(_effect("ag99live.motion"))

    with pytest.raises(PersonaEffectRegistryError):
        ctx.register_persona_effect(_effect("ag99live.motion", plugin_id="plugin_b"))


def test_context_lists_effects_by_enabled_state_and_stable_order():
    ctx = _init_effect_registry(_context())
    ctx.register_persona_effect(
        _effect("voice.emotion", plugin_id="plugin_b", priority=20)
    )
    ctx.register_persona_effect(
        _effect(
            "ag99live.motion",
            plugin_id="plugin_a",
            priority=10,
        )
    )
    ctx.register_persona_effect(
        _effect("client.expression", plugin_id="plugin_c", enabled=False)
    )

    assert [effect.name for effect in ctx.list_persona_effects()] == [
        "ag99live.motion",
        "voice.emotion",
    ]


def test_context_filters_effects_for_current_event_without_hiding_registrations():
    ctx = _init_effect_registry(_context())
    ctx.register_persona_effect(
        _effect(),
        event_filter=lambda event: event.platform_id == "olv_pet_adapter",
    )

    matching_event = type("Event", (), {"platform_id": "olv_pet_adapter"})()
    other_event = type("Event", (), {"platform_id": "aiocqhttp"})()

    assert [effect.name for effect in ctx.list_persona_effects()] == [
        "ag99live.motion"
    ]
    assert [
        effect.name for effect in ctx.list_persona_effects(event=matching_event)
    ] == ["ag99live.motion"]
    assert ctx.list_persona_effects(event=other_event) == []


def test_context_fails_closed_when_persona_effect_event_filter_raises():
    ctx = _init_effect_registry(_context())

    def broken_filter(_event):
        raise RuntimeError("filter failed")

    ctx.register_persona_effect(_effect(), event_filter=broken_filter)

    assert ctx.list_persona_effects(event=object()) == []


def test_expression_agent_resolves_persona_effects_for_current_event():
    event = object()
    seen_events = []

    class ContextStub:
        def list_persona_effects(self, *, event=None):
            seen_events.append(event)
            return [_effect()]

    effects = InteractionExpressionAgent._list_persona_effects(ContextStub(), event)

    assert seen_events == [event]
    assert [effect.name for effect in effects] == ["ag99live.motion"]


def test_context_returns_copies_and_unregisters_by_plugin():
    ctx = _init_effect_registry(_context())
    ctx.register_persona_effect(_effect())

    listed = ctx.list_persona_effects()
    listed[0].parameters["properties"]["axes"]["mutated"] = True

    assert "mutated" not in ctx.list_persona_effects()[0].parameters["properties"]["axes"]
    assert ctx.unregister_persona_effects(plugin_id="plugin_a") == 1
    assert ctx.list_persona_effects() == []

def test_parse_persona_effect_calls_keeps_valid_calls_and_drops_unknown_or_invalid():
    effect = _effect()
    effect.parameters = {
        "type": "object",
        "properties": {
            "axes": {"type": "object"},
            "duration_ms": {"type": "integer"},
        },
        "required": ["axes"],
    }

    calls = parse_persona_effect_calls(
        [
            {
                "name": "ag99live.motion",
                "arguments": {"axes": {"head_yaw": 40}, "duration_ms": 1200},
                "call_id": "call-1",
            },
            {"name": "unknown.effect", "arguments": {"axes": {}}},
            {"name": "ag99live.motion", "arguments": {"duration_ms": 1200}},
            {"name": "ag99live.motion", "arguments": {"axes": {}, "duration_ms": 1.5}},
        ],
        [effect],
    )

    assert calls == [
        PersonaEffectCall(
            name="ag99live.motion",
            arguments={"axes": {"head_yaw": 40}, "duration_ms": 1200},
            call_id="call-1",
            plugin_id="plugin_a",
            source="persona",
        )
    ]


def test_parse_persona_effect_calls_reports_rejection_reasons():
    effect = _effect()
    effect.parameters = {
        "type": "object",
        "properties": {"axes": {"type": "object"}},
        "required": ["axes"],
    }

    calls, issues = parse_persona_effect_calls_with_issues(
        [
            {"name": "ag99live.motion", "arguments": {"axes": {}}},
            "not-object",
            {"name": "unknown.effect", "arguments": {}},
            {"name": "ag99live.motion", "arguments": []},
            {"name": "ag99live.motion", "arguments": {}},
        ],
        [effect],
    )

    assert [call.name for call in calls] == ["ag99live.motion"]
    assert [issue.to_dict() for issue in issues] == [
        {"index": 1, "name": "", "reason": "effect_call_not_object"},
        {"index": 2, "name": "unknown.effect", "reason": "unknown_effect_name"},
        {"index": 3, "name": "ag99live.motion", "reason": "arguments_not_object"},
        {
            "index": 4,
            "name": "ag99live.motion",
            "reason": "missing required argument: axes",
        },
    ]


def test_normalize_persona_effect_arguments_coerces_nested_numeric_strings():
    effect = _effect()
    effect.parameters = {
        "type": "object",
        "properties": {
            "axes": {
                "type": "object",
                "properties": {
                    "head_yaw": {"type": "number"},
                    "duration_ms": {"type": "integer"},
                },
            }
        },
        "required": ["axes"],
    }

    normalized = normalize_persona_effect_arguments(
        {
            "axes": {
                "head_yaw": "55",
                "duration_ms": "1200",
            }
        },
        effect.parameters,
    )

    assert normalized == {
        "axes": {
            "head_yaw": 55.0,
            "duration_ms": 1200.0,
        }
    }


def test_parse_persona_effect_calls_accepts_numeric_strings_after_normalization():
    effect = _effect()
    effect.parameters = {
        "type": "object",
        "properties": {
            "axes": {
                "type": "object",
                "properties": {
                    "head_yaw": {"type": "number"},
                    "body_roll": {"type": "number"},
                },
                "required": ["head_yaw"],
            }
        },
        "required": ["axes"],
    }

    calls = parse_persona_effect_calls(
        [
            {
                "name": "ag99live.motion",
                "arguments": {
                    "axes": {
                        "head_yaw": "55",
                        "body_roll": "47.5",
                    }
                },
            }
        ],
        [effect],
    )

    assert calls == [
        PersonaEffectCall(
            name="ag99live.motion",
            arguments={"axes": {"head_yaw": 55.0, "body_roll": 47.5}},
            plugin_id="plugin_a",
            source="persona",
        )
    ]


def test_parse_persona_effect_calls_rejects_extra_arguments_when_schema_is_closed():
    effect = _effect()
    effect.name = "demo.effect"
    effect.parameters = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "labels": {
                "type": "array",
                "items": {"type": "string"},
            }
        },
        "required": ["labels"],
    }

    calls, issues = parse_persona_effect_calls_with_issues(
        [
            {
                "name": "demo.effect",
                "arguments": {
                    "labels": ["focused"],
                    "debug": True,
                },
            }
        ],
        [effect],
    )

    assert calls == []
    assert [issue.to_dict() for issue in issues] == [
        {
            "index": 0,
            "name": "demo.effect",
            "reason": "unexpected argument: debug",
        }
    ]


def test_parse_persona_effect_calls_rejects_nested_extra_arguments_when_schema_is_closed():
    effect = _effect()
    effect.name = "demo.effect"
    effect.parameters = {
        "type": "object",
        "properties": {
            "pose": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            }
        },
        "required": ["pose"],
    }

    calls, issues = parse_persona_effect_calls_with_issues(
        [
            {
                "name": "demo.effect",
                "arguments": {"pose": {"name": "lean", "debug": True}},
            }
        ],
        [effect],
    )

    assert calls == []
    assert [issue.to_dict() for issue in issues] == [
        {
            "index": 0,
            "name": "demo.effect",
            "reason": "unexpected argument: pose.debug",
        }
    ]
