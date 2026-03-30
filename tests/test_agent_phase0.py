"""Agent Phase 0 骨架验证。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agent.queen import AgentQueen
from src.agent.pool_selector.rule_pool_selector import RulePoolSelector
from src.agent.pool_selector.types import PoolSelectorInputView
from src.agent.types import AgentOutcome, AgentRequest
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
    session_key = "dm:agent-phase0"
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
async def test_agent_queen_handle_returns_agent_outcome_with_message_emit() -> None:
    queen = AgentQueen()
    outcome = await queen.handle(_make_request("你好"))

    assert isinstance(outcome, AgentOutcome)
    assert len(outcome.emit) >= 1

    emitted = outcome.emit[0]
    assert emitted.obs_type == ObservationType.MESSAGE
    assert emitted.source_name.startswith("agent:")
    assert emitted.actor.actor_id == "agent"
    assert isinstance(emitted.metadata, dict)
    assert isinstance(emitted.payload, MessagePayload)
    assert emitted.payload.text and emitted.payload.text.strip()


@pytest.mark.asyncio
async def test_rule_pool_selector_classifies_chat_code_plan() -> None:
    selector = RulePoolSelector()

    chat_plan = await selector.select(_make_request("今天天气怎么样？"))
    code_plan = await selector.select(_make_request("pytest 失败了，有 Traceback"))
    design_plan = await selector.select(_make_request("请给我一份系统架构设计方案"))

    assert chat_plan.task_type == "chat"
    assert code_plan.task_type == "code"
    assert design_plan.task_type == "plan"


@pytest.mark.asyncio
async def test_code_task_falls_back_to_chat_pool_when_pool_missing() -> None:
    queen = AgentQueen()
    outcome = await queen.handle(_make_request("这里有个 traceback，请帮我看报错"))

    assert outcome.trace.get("task_type") == "code"
    assert outcome.trace.get("pool_id") == "chat"
    pool_trace = outcome.trace.get("pool", {})
    assert pool_trace.get("fallback") is True


@pytest.mark.asyncio
async def test_rule_pool_selector_uses_view_text_override() -> None:
    selector = RulePoolSelector()
    req = _make_request("今天天气怎么样？")
    view = PoolSelectorInputView(
        current_input_text="pytest 失败了，有 Traceback",
        recent_obs_view=[],
        session_state_view={},
        gate_hint_view={},
    )

    plan = await selector.select(req, view=view)

    assert plan.task_type == "code"

