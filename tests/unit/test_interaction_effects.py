import pytest

from astrbot.core.interaction.effects import (
    PersonaEffectCall,
    PersonaEffectRegistryError,
    PersonaEffectSpec,
    normalize_persona_effect_arguments,
    parse_persona_effect_calls,
    parse_persona_effect_calls_with_issues,
)
from astrbot.core.interaction.expression_agent import (
    build_persona_expression_tool_parameters,
)
from astrbot.core.star.context import Context


def _effect(
    name: str = "ag99live.motion",
    *,
    plugin_id: str = "plugin_a",
    phases: tuple[str, ...] = (),
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
        phases=phases,
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

    assert "effect_calls" not in schema["properties"]
    assert "metadata" in schema["properties"]


def test_effect_schema_uses_stable_portable_enum_without_strict_union_keywords():
    effects = [
        _effect("voice.emotion", plugin_id="plugin_b"),
        _effect("ag99live.motion", plugin_id="plugin_a"),
    ]

    schema = build_persona_expression_tool_parameters(effects)
    effect_calls = schema["properties"]["effect_calls"]

    assert effect_calls["items"]["properties"]["name"]["enum"] == [
        "ag99live.motion",
        "voice.emotion",
    ]
    assert "oneOf" not in str(effect_calls)
    assert "const" not in str(effect_calls)
    assert "maxItems" not in str(effect_calls)


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


def test_context_rejects_duplicate_effect_name():
    ctx = _init_effect_registry(_context())
    ctx.register_persona_effect(_effect("ag99live.motion"))

    with pytest.raises(PersonaEffectRegistryError):
        ctx.register_persona_effect(_effect("ag99live.motion", plugin_id="plugin_b"))


def test_context_lists_effects_by_phase_enabled_state_and_stable_order():
    ctx = _init_effect_registry(_context())
    ctx.register_persona_effect(
        _effect("voice.emotion", plugin_id="plugin_b", priority=20)
    )
    ctx.register_persona_effect(
        _effect(
            "ag99live.motion",
            plugin_id="plugin_a",
            phases=("first_response",),
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
    assert [effect.name for effect in ctx.list_persona_effects(phase="executor_progress")] == [
        "voice.emotion",
    ]


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
            "duration_ms": 1200,
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
