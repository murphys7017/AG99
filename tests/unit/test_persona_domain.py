import json
import math
from datetime import UTC, datetime

import pytest

from astrbot.core.interaction.persona_domain import (
    EffectivePersonaContext,
    PersonaDefinition,
    PersonaRelationshipState,
    RuntimeControlSnapshot,
    adapt_memory_snapshot,
    adapt_persona_collector_slots,
    adapt_personal_persistent_state,
    build_effective_persona_context,
    build_persona_scope_key,
)
from astrbot.core.interaction.personal_state import (
    PersonalAttentionState,
    PersonalAvailabilityState,
    PersonalPersistentState,
    PersonalStateSnapshot,
)
from astrbot.core.memory.types import MemorySnapshot, PersonaState, ScopeType
from astrbot.core.prompt.context_types import ContextSlot


def test_persona_definition_adapter_freezes_values_and_drops_slot_metadata():
    segments = {"identity": ["YakumoAki"]}
    slots = [
        ContextSlot(
            name="persona.prompt",
            value="身份\nYakumoAki",
            category="persona",
            source="persona_mgr",
            meta={
                "persona_id": "ag99",
                "force_applied": True,
                "provider_request": object(),
            },
        ),
        ContextSlot(
            name="persona.segments",
            value=segments,
            category="persona",
            source="persona_parser",
        ),
        ContextSlot(
            name="persona.begin_dialogs",
            value=[{"role": "user", "content": "你好"}],
            category="persona",
            source="persona_mgr",
        ),
        ContextSlot(
            name="persona.tools_whitelist",
            value=None,
            category="persona",
            source="persona_mgr",
        ),
        ContextSlot(
            name="persona.skills_whitelist",
            value=[],
            category="persona",
            source="persona_mgr",
        ),
    ]

    definition = adapt_persona_collector_slots(slots)

    assert isinstance(definition, PersonaDefinition)
    assert definition.persona_id == "ag99"
    assert definition.tools is None
    assert definition.skills == ()
    assert definition.force_applied is True
    segments["identity"].append("changed")
    assert definition.segments["identity"] == ("YakumoAki",)
    payload = definition.to_dict()
    assert "provider_request" not in payload
    json.dumps(payload)
    with pytest.raises(TypeError):
        definition.segments["identity"] = ("changed",)


def test_runtime_and_relationship_adapters_are_read_only_and_serializable():
    runtime_source = PersonalStateSnapshot(
        attention_state=PersonalAttentionState.IDLE,
        availability_state=PersonalAvailabilityState.AVAILABLE,
        last_observation_at=1.0,
        last_user_activity_at=2.0,
        last_expression_at=3.0,
        reply_cooldown_until=4.0,
        no_action_cooldown_until=5.0,
        mute_until=None,
        pending_observation_count=1,
        material_revision=2,
        last_settled_material_revision=1,
        usage_day="2026-08-28",
        daily_policy_calls=2,
        daily_proactive_outputs=1,
        last_gate_reason="available",
        last_policy_action="ignore",
    )
    runtime = RuntimeControlSnapshot.from_personal_state_snapshot(runtime_source)
    relation = adapt_memory_snapshot(
        MemorySnapshot(
            umo="test:FriendMessage:user",
            conversation_id=None,
            persona_state=PersonaState(
                state_id="state-1",
                scope_type=ScopeType.USER,
                scope_id="user-1",
                persona_id="ag99",
                trust=0.8,
                updated_at=datetime(2026, 8, 28, tzinfo=UTC),
            ),
        )
    )

    assert runtime.availability_state is PersonalAvailabilityState.AVAILABLE
    assert runtime.to_dict()["attention_state"] == "idle"
    assert relation is not None
    assert relation.scope_type == "user"
    assert relation.trust == 0.8
    json.dumps(runtime.to_dict())
    relationship_payload = relation.to_dict()
    assert "scope_id" not in relationship_payload
    assert relationship_payload["scope_id_hash"]
    group_relation = adapt_memory_snapshot(
        MemorySnapshot(
            umo="test:GroupMessage:user",
            conversation_id=None,
            persona_state=PersonaState(
                state_id="state-2",
                scope_type=ScopeType.GROUP,
                scope_id="user-1",
                persona_id="ag99",
            ),
        )
    )
    assert group_relation is not None
    assert (
        relationship_payload["scope_id_hash"]
        != group_relation.to_dict()["scope_id_hash"]
    )
    json.dumps(relationship_payload)

    persistent = adapt_personal_persistent_state(
        PersonalPersistentState(
            last_user_activity_at=2.0,
            last_idle_initiation_activity_at=None,
            last_expression_at=3.0,
            last_expression_fingerprint="fingerprint",
            reply_cooldown_until=4.0,
            no_action_cooldown_until=5.0,
            mute_until=None,
            usage_day="2026-08-28",
            daily_policy_calls=2,
            daily_proactive_outputs=1,
        )
    )
    with pytest.raises(TypeError):
        persistent["usage_day"] = "changed"


def test_effective_context_and_scope_key_are_explicit_and_stable():
    persona = PersonaDefinition(persona_id="ag99", prompt="stable persona")
    runtime_source = PersonalStateSnapshot(
        attention_state=PersonalAttentionState.IDLE,
        availability_state=PersonalAvailabilityState.AVAILABLE,
        last_observation_at=None,
        last_user_activity_at=None,
        last_expression_at=None,
        reply_cooldown_until=None,
        no_action_cooldown_until=None,
        mute_until=None,
        pending_observation_count=0,
        material_revision=0,
        last_settled_material_revision=0,
        usage_day=None,
        daily_policy_calls=0,
        daily_proactive_outputs=0,
        last_gate_reason=None,
        last_policy_action=None,
    )
    scope_key = build_persona_scope_key(
        config_id="default",
        persona_id="ag99",
        audience_key="test:FriendMessage:user",
        privacy_scope="private",
    )
    context = build_effective_persona_context(
        definition=persona,
        runtime_snapshot=runtime_source,
        scope_key=scope_key,
    )

    assert isinstance(context, EffectivePersonaContext)
    assert context.scope_key == scope_key
    assert context.relationship is None
    assert context.runtime is not None
    assert context.to_dict()["definition"]["persona_id"] == "ag99"
    assert context.to_dict()["runtime"]["attention_state"] == "idle"
    assert scope_key == build_persona_scope_key(
        config_id="default",
        persona_id="ag99",
        audience_key="test:FriendMessage:user",
        privacy_scope="private",
    )
    assert scope_key != build_persona_scope_key(
        config_id="default",
        persona_id="ag99",
        audience_key="test:FriendMessage:other",
        privacy_scope="private",
    )
    json.dumps(context.to_dict())


@pytest.mark.parametrize("value", [b"binary", math.nan, math.inf, -math.inf])
def test_persona_definition_rejects_non_serializable_or_non_finite_values(value):
    with pytest.raises((TypeError, ValueError)):
        PersonaDefinition(persona_id="ag99", segments={"invalid": value})


def test_runtime_control_snapshot_rejects_invalid_control_values():
    values = {
        "attention_state": PersonalAttentionState.IDLE,
        "availability_state": PersonalAvailabilityState.AVAILABLE,
        "last_observation_at": None,
        "last_user_activity_at": None,
        "last_expression_at": None,
        "reply_cooldown_until": None,
        "no_action_cooldown_until": None,
        "mute_until": None,
        "pending_observation_count": 0,
        "material_revision": 0,
        "last_settled_material_revision": 0,
        "usage_day": None,
        "daily_policy_calls": 0,
        "daily_proactive_outputs": 0,
        "last_gate_reason": None,
        "last_policy_action": None,
    }

    with pytest.raises(ValueError):
        RuntimeControlSnapshot(**{**values, "attention_state": "unknown"})
    with pytest.raises(ValueError):
        RuntimeControlSnapshot(**{**values, "pending_observation_count": -1})
    with pytest.raises(ValueError):
        RuntimeControlSnapshot(**{**values, "last_observation_at": math.inf})
    with pytest.raises(ValueError):
        RuntimeControlSnapshot(
            **{**values, "material_revision": 1, "last_settled_material_revision": 2}
        )


def test_relationship_snapshot_rejects_invalid_identity_types():
    with pytest.raises(TypeError):
        PersonaRelationshipState(
            scope_type="user",
            scope_id="user-1",
            persona_id=123,
        )
    with pytest.raises(TypeError):
        PersonaRelationshipState(
            scope_type="user",
            scope_id="user-1",
            persona_id="ag99",
            updated_at="2026-08-28T00:00:00+00:00",
        )
