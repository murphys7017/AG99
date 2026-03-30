"""Phase 1.26: PoolSelector / RoutingPlan 语义重命名验证。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agent.pool_selector.rule_pool_selector import RulePoolSelector
from src.agent.pool_selector.validator import normalize_routing_plan_payload
from src.agent.types import AgentRequest, RoutingPlan
from src.gate.types import GateAction, GateDecision, Scene
from src.schemas.observation import (
    Actor,
    MessagePayload,
    Observation,
    ObservationType,
    SourceKind,
)
from src.session_router import SessionState


def _make_request(text: str) -> AgentRequest:
    session_key = "dm:pool-selector-phase1-26"
    obs = Observation(
        obs_type=ObservationType.MESSAGE,
        source_name="test:input",
        source_kind=SourceKind.EXTERNAL,
        session_key=session_key,
        actor=Actor(actor_id="u1", actor_type="user"),
        payload=MessagePayload(text=text),
        metadata={},
    )
    state = SessionState(session_key=session_key)
    state.record(obs)
    decision = GateDecision(
        action=GateAction.DELIVER,
        scene=Scene.DIALOGUE,
        session_key=session_key,
    )
    return AgentRequest(
        obs=obs,
        gate_decision=decision,
        session_state=state,
        now=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_pool_selector_select_returns_routing_plan() -> None:
    selector = RulePoolSelector()

    plan = await selector.select(_make_request("你好，今天心情不错"))

    assert isinstance(plan, RoutingPlan)
    assert plan.pool_id == "chat"


def test_routing_plan_field_integrity() -> None:
    payload = {
        "task_type": "chat",
        "pool_id": "chat",
        "required_context": ["recent_obs", "gate_hint"],
        "meta": {
            "strategy": "single_pass",
            "context_policy": "default",
            "prompt_profile_id": "chat.single_pass",
            "budget": {"max_tokens": 512},
            "confidence": 0.83,
            "reason": "rule_default",
            "fallback_pool_id": "chat",
        },
    }

    plan = normalize_routing_plan_payload(payload, pool_selector_kind="rule")

    assert isinstance(plan, RoutingPlan)
    assert plan.pool_id == "chat"
    assert tuple(plan.required_context) == ("recent_obs", "gate_hint")
    assert plan.meta.get("strategy") == "single_pass"
    assert "context_policy" in plan.meta
    assert "prompt_profile_id" in plan.meta
    assert "budget" in plan.meta
    assert "confidence" in plan.meta
    assert "reason" in plan.meta
    assert "fallback_pool_id" in plan.meta

