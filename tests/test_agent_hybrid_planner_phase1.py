"""Agent Phase 1: HybridPoolSelector 验证。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.agent.pool_selector.hybrid_pool_selector import HybridPoolSelector
from src.agent.pool_selector.llm_pool_selector import LLMPoolSelector
from src.agent.pool_selector.types import PoolSelectorInputView
from src.agent.queen import AgentQueen
from src.agent.types import AgentRequest
from src.gate.types import GateAction, GateDecision, Scene
from src.schemas.observation import (
    Actor,
    MessagePayload,
    Observation,
    ObservationType,
    SourceKind,
)
from src.session_router import SessionState


class _FakeBadLLMProvider:
    def call(self, messages, **params):  # type: ignore[no-untyped-def]
        return "```json\n{not valid json}\n```"


class _FakeGoodLLMProvider:
    def call(self, messages, **params):  # type: ignore[no-untyped-def]
        return (
            "```json\n"
            "{"
            "\"task_type\":\"plan\","
            "\"pool_id\":\"plan\","
            "\"required_context\":[\"recent_obs\",\"gate_hint\",\"unknown_slot\"],"
            "\"meta\":{"
            "\"reason\":\"llm_detected_plan\","
            "\"confidence\":1.25,"
            "\"strategy\":\"draft_critique\","
            "\"complexity\":\"multi_step\""
            "}"
            "}\n"
            "```"
        )


class _SpyProvider:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls = 0

    def call(self, messages, **params):  # type: ignore[no-untyped-def]
        self.calls += 1
        return json.dumps(self._payload, ensure_ascii=False)


class _CaptureSelectorInputProvider:
    def __init__(self) -> None:
        self.last_input = None

    def call(self, messages, **params):  # type: ignore[no-untyped-def]
        payload = json.loads(messages[1]["content"])
        self.last_input = payload
        return json.dumps(
            {
                "task_type": "chat",
                "pool_id": "chat",
                "required_context": ["recent_obs"],
                "meta": {
                    "reason": "ok",
                    "confidence": 0.9,
                    "strategy": "single_pass",
                    "complexity": "simple",
                },
            },
            ensure_ascii=False,
        )


def _make_request(text: str) -> AgentRequest:
    session_key = "dm:hybrid-phase1"
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
async def test_hybrid_pool_selector_falls_back_on_invalid_json() -> None:
    llm = LLMPoolSelector(
        config={"llm": {"timeout_seconds": 1}},
        llm_provider=_FakeBadLLMProvider(),  # type: ignore[arg-type]
    )
    selector = HybridPoolSelector(
        config={"timeout_seconds": 2},
        llm_pool_selector=llm,
    )

    plan = await selector.select(_make_request("pytest 失败了，Traceback 如下"))
    assert plan.task_type == "code"  # 来自 rule fallback
    assert plan.meta.get("pool_selector_kind") == "hybrid_rule_fallback"
    assert plan.meta.get("selector_llm_called") is True
    assert plan.meta.get("selector_llm_parse_ok") is False
    assert isinstance(plan.meta.get("fallback_reason"), str)


@pytest.mark.asyncio
async def test_hybrid_pool_selector_parses_llm_json_and_normalizes() -> None:
    llm = LLMPoolSelector(
        config={"llm": {"timeout_seconds": 1}},
        llm_provider=_FakeGoodLLMProvider(),  # type: ignore[arg-type]
    )
    selector = HybridPoolSelector(
        config={"timeout_seconds": 2},
        llm_pool_selector=llm,
    )

    plan = await selector.select(_make_request("帮我设计一个微服务架构方案"))
    assert plan.task_type == "plan"
    assert plan.pool_id == "plan"
    assert plan.meta.get("pool_selector_kind") == "hybrid_llm"
    assert plan.meta.get("selector_llm_parse_ok") is True
    assert plan.meta.get("selector_llm_called") is True
    assert plan.meta.get("confidence") == 1.0  # 裁剪到 [0,1]
    assert "unknown_slot" not in plan.required_context
    assert "recent_obs" in plan.required_context


@pytest.mark.asyncio
async def test_agent_queen_trace_contains_hybrid_pool_selector_signals() -> None:
    llm = LLMPoolSelector(
        config={"llm": {"timeout_seconds": 1}},
        llm_provider=_FakeBadLLMProvider(),  # type: ignore[arg-type]
    )
    hybrid = HybridPoolSelector(
        config={"timeout_seconds": 2},
        llm_pool_selector=llm,
    )
    queen = AgentQueen(pool_selector=hybrid)

    outcome = await queen.handle(_make_request("pytest 报错了"))
    selector_trace = outcome.trace.get("pool_selector_summary", {})

    assert len(outcome.emit) >= 1
    assert outcome.emit[0].obs_type == ObservationType.MESSAGE
    assert isinstance(outcome.emit[0].metadata, dict)
    assert "pool_selector_kind" in selector_trace
    assert "selector_llm_called" in selector_trace
    assert "selector_llm_parse_ok" in selector_trace


@pytest.mark.asyncio
async def test_hybrid_pool_selector_uses_small_llm_without_escalation() -> None:
    small_provider = _SpyProvider(
        {
            "task_type": "chat",
            "pool_id": "chat",
            "required_context": ["recent_obs"],
            "meta": {
                "reason": "small_model_confident",
                "confidence": 0.91,
                "strategy": "single_pass",
                "complexity": "simple",
                "need_big_model": False,
            },
        }
    )
    big_provider = _SpyProvider(
        {
            "task_type": "plan",
            "pool_id": "plan",
            "required_context": ["recent_obs", "gate_hint"],
            "meta": {
                "reason": "big_model_should_not_be_called",
                "confidence": 0.8,
                "strategy": "draft_critique",
                "complexity": "multi_step",
            },
        }
    )

    small = LLMPoolSelector(config={"llm": {"timeout_seconds": 1}}, llm_provider=small_provider)  # type: ignore[arg-type]
    big = LLMPoolSelector(config={"llm": {"timeout_seconds": 1}}, llm_provider=big_provider)  # type: ignore[arg-type]
    selector = HybridPoolSelector(
        config={"timeout_seconds": 2, "small_llm": {"enabled": True}},
        llm_pool_selector=big,
        small_llm_pool_selector=small,
    )

    plan = await selector.select(_make_request("你好，帮我打个招呼就行"))

    assert plan.meta.get("pool_selector_kind") == "hybrid_small_llm"
    assert plan.task_type == "chat"
    assert plan.meta.get("escalated_to_big") is False
    assert plan.meta.get("final_routing_source") == "small_llm"
    assert small_provider.calls == 1
    assert big_provider.calls == 0


@pytest.mark.asyncio
async def test_hybrid_pool_selector_escalates_to_big_llm_when_small_requires() -> None:
    small_provider = _SpyProvider(
        {
            "task_type": "plan",
            "pool_id": "plan",
            "required_context": ["recent_obs"],
            "meta": {
                "reason": "ambiguous_architecture_request",
                "confidence": 0.73,
                "strategy": "draft_critique",
                "complexity": "multi_step",
                "need_big_model": True,
            },
        }
    )
    big_provider = _SpyProvider(
        {
            "task_type": "plan",
            "pool_id": "plan",
            "required_context": ["recent_obs", "gate_hint"],
            "meta": {
                "reason": "big_model_refined_plan",
                "confidence": 0.89,
                "strategy": "draft_critique",
                "complexity": "multi_step",
            },
        }
    )

    small = LLMPoolSelector(config={"llm": {"timeout_seconds": 1}}, llm_provider=small_provider)  # type: ignore[arg-type]
    big = LLMPoolSelector(config={"llm": {"timeout_seconds": 1}}, llm_provider=big_provider)  # type: ignore[arg-type]
    selector = HybridPoolSelector(
        config={"timeout_seconds": 2, "small_llm": {"enabled": True}},
        llm_pool_selector=big,
        small_llm_pool_selector=small,
    )

    plan = await selector.select(_make_request("帮我做一个支付系统与风控系统的架构方案"))

    assert plan.meta.get("pool_selector_kind") == "hybrid_big_llm"
    assert plan.task_type == "plan"
    assert plan.meta.get("escalated_to_big") is True
    assert plan.meta.get("final_routing_source") == "big_llm"
    assert small_provider.calls == 1
    assert big_provider.calls == 1


@pytest.mark.asyncio
async def test_llm_pool_selector_uses_view_recent_obs_preview() -> None:
    provider = _CaptureSelectorInputProvider()
    llm = LLMPoolSelector(config={"llm": {"timeout_seconds": 1}}, llm_provider=provider)  # type: ignore[arg-type]

    view = PoolSelectorInputView(
        current_input_text="hello",
        recent_obs_view=[{"actor_id": "u1", "source_name": "test", "text": "from_view"}],
        session_state_view={},
        gate_hint_view={},
    )

    await llm.select(_make_request("ignored"), view=view)

    assert provider.last_input is not None
    assert provider.last_input.get("recent_obs_preview")[0]["text"] == "from_view"
    assert provider.last_input.get("text") == "hello"

