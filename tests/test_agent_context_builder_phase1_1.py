"""Agent Phase 1.1: ContextBuilder MVP verification."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agent.context.builder import SlotContextBuilder
from src.agent.context.types import ProviderResult
from src.agent.queen import AgentQueen
from src.agent.types import AgentRequest, RoutingPlan
from src.gate.types import GateAction, GateDecision, Scene
from src.schemas.observation import Actor, MessagePayload, Observation, ObservationType, SourceKind
from src.session_router import SessionState


def _make_request(text: str) -> AgentRequest:
    session_key = "dm:context-phase1-1"
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
async def test_context_builder_includes_required_and_base_slots() -> None:
    builder = SlotContextBuilder()
    plan = RoutingPlan(task_type="chat", pool_id="chat", required_context=("recent_obs",))

    ctx = await builder.build(_make_request("hello"), plan)

    assert "current_input" in ctx.slots
    assert "recent_obs" in ctx.slots
    assert "plan_meta" in ctx.slots
    assert ctx.slots["current_input"].status == "ok"
    assert ctx.slots["recent_obs"].status == "ok"
    assert ctx.slots["plan_meta"].status == "ok"
    assert "current_input" in ctx.meta.get("auto_injected", [])
    assert "recent_obs" in ctx.meta.get("requested_by_plan", [])
    assert "current_input" in ctx.meta.get("auto_injected", [])
    assert "plan_meta" in ctx.meta.get("auto_injected", [])
    assert "recent_obs" in ctx.meta.get("requested_effective", [])
    assert ctx.meta.get("priorities", {}).get("current_input") == 100


@pytest.mark.asyncio
async def test_context_builder_future_slot_stub() -> None:
    builder = SlotContextBuilder()
    plan = RoutingPlan(task_type="chat", pool_id="chat", required_context=("persona",))

    ctx = await builder.build(_make_request("hello"), plan)

    assert ctx.slots["persona"].status in ("stub", "missing")
    assert "persona" in ctx.meta.get("missing", [])


class _BadProvider:
    name = "recent_obs"

    async def provide(self, req: AgentRequest, plan: RoutingPlan) -> ProviderResult:
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_context_builder_provider_error_soft_fails() -> None:
    builder = SlotContextBuilder(providers={"recent_obs": _BadProvider()})
    plan = RoutingPlan(task_type="chat", pool_id="chat", required_context=("recent_obs",))

    ctx = await builder.build(_make_request("hello"), plan)

    assert ctx.slots["recent_obs"].status == "error"
    assert ctx.meta.get("errors")
    assert "current_input" in ctx.slots


@pytest.mark.asyncio
async def test_context_builder_priority_override() -> None:
    builder = SlotContextBuilder()
    plan = RoutingPlan(
        task_type="chat",
        pool_id="chat",
        required_context=("recent_obs",),
        meta={"context_priorities": {"recent_obs": 10}},
    )

    ctx = await builder.build(_make_request("hello"), plan)

    assert ctx.slots["recent_obs"].priority == 10
    assert ctx.meta.get("priorities", {}).get("recent_obs") == 10


@pytest.mark.asyncio
async def test_agent_queen_trace_contains_context_summary() -> None:
    queen = AgentQueen()
    outcome = await queen.handle(_make_request("hello"))

    context_trace = outcome.trace.get("context_build_summary", {})
    assert context_trace.get("requested_effective")
    assert context_trace.get("priorities")


@pytest.mark.asyncio
async def test_agent_queen_trace_separates_pool_selector_input_and_context_build() -> None:
    queen = AgentQueen()
    outcome = await queen.handle(_make_request("hello"))

    assert outcome.trace.get("pool_selector_input_summary")
    assert outcome.trace.get("context_build_summary")
