import pytest

from astrbot.core.interaction.effects import (
    PersonaEffectCall,
    PersonaEffectRegistryError,
    PersonaEffectSpec,
    legacy_plugin_hints_to_effect_calls,
    parse_persona_effect_calls,
)
from astrbot.core.interaction.expression_agent import (
    build_persona_expression_tool_parameters,
)
from astrbot.core.star.context import Context


def _effect(
    name: str = "ag99live.motion",
    *,
    plugin_id: str = "plugin_a",
    aliases: tuple[str, ...] = ("ag99live_motion",),
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
        legacy_hint_names=aliases,
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
    assert schema["properties"]["plugin_hints"] == {
        "type": "object",
        "additionalProperties": True,
    }


def test_effect_schema_uses_stable_portable_enum_without_strict_union_keywords():
    effects = [
        _effect("voice.emotion", plugin_id="plugin_b", aliases=()),
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


def test_context_rejects_duplicate_effect_name_and_alias_conflicts():
    ctx = _init_effect_registry(_context())
    ctx.register_persona_effect(_effect("ag99live.motion", aliases=("ag99live_motion",)))

    with pytest.raises(PersonaEffectRegistryError):
        ctx.register_persona_effect(_effect("ag99live.motion", plugin_id="plugin_b"))
    with pytest.raises(PersonaEffectRegistryError):
        ctx.register_persona_effect(
            _effect("voice.emotion", plugin_id="plugin_b", aliases=("ag99live_motion",))
        )
    with pytest.raises(PersonaEffectRegistryError):
        ctx.register_persona_effect(
            _effect("client.expression", plugin_id="plugin_b", aliases=("ag99live.motion",))
        )


def test_context_rejects_name_that_conflicts_with_existing_alias():
    ctx = _init_effect_registry(_context())
    ctx.register_persona_effect(_effect("ag99live.motion", aliases=("legacy.motion",)))

    with pytest.raises(PersonaEffectRegistryError):
        ctx.register_persona_effect(_effect("legacy.motion", plugin_id="plugin_b"))


def test_legacy_hints_convert_only_by_explicit_alias_or_exact_name():
    effects = [_effect("ag99live.motion", aliases=("ag99live_motion",))]
    hints = {
        "ag99live.motion": {"direct": True},
        "ag99live_motion": {"legacy": True},
        "voice_emotion": {"emotion": "happy"},
    }

    calls = legacy_plugin_hints_to_effect_calls(hints, effects)

    assert calls == [
        PersonaEffectCall(
            name="ag99live.motion",
            arguments={"direct": True},
            plugin_id="plugin_a",
            source="legacy_plugin_hints",
        ),
        PersonaEffectCall(
            name="ag99live.motion",
            arguments={"legacy": True},
            plugin_id="plugin_a",
            source="legacy_plugin_hints",
        ),
    ]


def test_no_underscore_dot_automatic_legacy_conversion():
    calls = legacy_plugin_hints_to_effect_calls(
        {"ag99live_motion": {"legacy": True}},
        [_effect("ag99live.motion", aliases=())],
    )

    assert calls == []


def test_context_lists_effects_by_phase_enabled_state_and_stable_order():
    ctx = _init_effect_registry(_context())
    ctx.register_persona_effect(
        _effect("voice.emotion", plugin_id="plugin_b", aliases=(), priority=20)
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
        _effect("client.expression", plugin_id="plugin_c", aliases=(), enabled=False)
    )

    assert [effect.name for effect in ctx.list_persona_effects()] == [
        "ag99live.motion",
        "voice.emotion",
    ]
    assert [effect.name for effect in ctx.list_persona_effects(phase="executor_progress")] == [
        "voice.emotion",
    ]


def test_context_returns_copies_and_unregisters_names_and_aliases_together():
    ctx = _init_effect_registry(_context())
    ctx.register_persona_effect(_effect())

    listed = ctx.list_persona_effects()
    listed[0].parameters["properties"]["axes"]["mutated"] = True

    assert "mutated" not in ctx.list_persona_effects()[0].parameters["properties"]["axes"]
    assert ctx.unregister_persona_effects(plugin_id="plugin_a") == 1
    assert ctx.list_persona_effects() == []
    ctx.register_persona_effect(_effect("ag99live.motion", aliases=("ag99live_motion",)))


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
