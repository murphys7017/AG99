from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from src.gate.types import GateAction, GateDecision, GateOutcome, Scene
from src.agent.types import AgentOutcome
from src.core import Core
from src.schemas.observation import Actor, MessagePayload, Observation, ObservationType, SourceKind


pytestmark = pytest.mark.asyncio


async def _wait_until(predicate, *, timeout: float = 2.0, interval: float = 0.02):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("wait_until timeout")


class SpyOrchestrator:
    def __init__(self) -> None:
        self.calls = 0

    async def handle(self, req):
        self.calls += 1
        return AgentOutcome(emit=[], trace={}, error=None)


class EmitOrchestrator:
    def __init__(self) -> None:
        self.calls = 0
        self.last_emit_obs_id = None

    async def handle(self, req):
        self.calls += 1
        emit_obs = Observation(
            obs_type=ObservationType.MESSAGE,
            source_name="agent:speaker",
            source_kind=SourceKind.INTERNAL,
            session_key=req.obs.session_key,
            actor=Actor(actor_id="agent", actor_type="system"),
            payload=MessagePayload(text="ok"),
        )
        self.last_emit_obs_id = emit_obs.obs_id
        return AgentOutcome(emit=[emit_obs], trace={}, error=None)


class EmitOrchestratorWithNoneMetadata:
    def __init__(self) -> None:
        self.calls = 0
        self.last_emit_obs_id = None

    async def handle(self, req):
        self.calls += 1
        emit_obs = Observation(
            obs_type=ObservationType.MESSAGE,
            source_name="agent:speaker",
            source_kind=SourceKind.INTERNAL,
            session_key=req.obs.session_key,
            actor=Actor(actor_id="agent", actor_type="system"),
            payload=MessagePayload(text="ok"),
            metadata=None,
        )
        self.last_emit_obs_id = emit_obs.obs_id
        return AgentOutcome(emit=[emit_obs], trace={}, error=None)


@dataclass
class _MemoryTurn:
    turn_id: str


@dataclass
class _MemoryEvent:
    event_id: str


class FakeMemoryService:
    def __init__(self) -> None:
        self.events = []
        self.turns = []
        self.finished = []
        self.closed = False

    def append_event(self, obs, session_key, gate_result=None, meta=None):
        event_id = f"evt_{len(self.events) + 1}"
        self.events.append(
            {
                "event_id": event_id,
                "obs_id": obs.obs_id,
                "session_key": session_key,
                "gate_result": gate_result,
                "meta": meta or {},
            }
        )
        return _MemoryEvent(event_id=event_id)

    def append_turn(self, session_key, input_event_id, plan=None, meta=None):
        turn_id = f"turn_{len(self.turns) + 1}"
        self.turns.append(
            {
                "turn_id": turn_id,
                "session_key": session_key,
                "input_event_id": input_event_id,
                "plan": plan,
                "meta": meta or {},
            }
        )
        return _MemoryTurn(turn_id=turn_id)

    def finish_turn(self, turn_id, final_output_obs_id=None, status="ok", error=None):
        self.finished.append(
            {
                "turn_id": turn_id,
                "final_output_obs_id": final_output_obs_id,
                "status": status,
                "error": error,
            }
        )

    def close(self):
        self.closed = True


class FailingEventMemoryService(FakeMemoryService):
    def append_event(self, obs, session_key, gate_result=None, meta=None):
        raise RuntimeError("append_event failed")


class DropGate:
    def __init__(self) -> None:
        self.metrics = None

    def handle(self, obs, ctx):
        decision = GateDecision(
            action=GateAction.DROP,
            scene=Scene.DIALOGUE,
            session_key=obs.session_key or "",
        )
        return GateOutcome(decision=decision, emit=[], ingest=[])

    def ingest(self, obs, decision) -> None:
        return None


async def test_core_accepts_injected_orchestrator():
    spy = SpyOrchestrator()
    core = Core(
        bus_maxsize=100,
        inbox_maxsize=16,
        system_session_key="system",
        message_routing="user",
        enable_system_fanout=False,
        enable_session_gc=False,
        agent_queen=spy,
    )

    task = asyncio.create_task(core.run_forever())
    await _wait_until(lambda: core._router_task is not None and core._watcher_task is not None)

    core.bus.publish_nowait(
        Observation(
            obs_type=ObservationType.MESSAGE,
            source_name="test_input",
            session_key="dm:test",
            actor=Actor(actor_id="u1", actor_type="user"),
            payload=MessagePayload(text="hello"),
        )
    )

    await _wait_until(lambda: spy.calls >= 1)
    assert spy.calls == 1

    await core.shutdown()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_core_persists_event_and_turn_with_memory_service():
    orchestrator = EmitOrchestrator()
    memory = FakeMemoryService()
    core = Core(
        bus_maxsize=100,
        inbox_maxsize=16,
        system_session_key="system",
        message_routing="user",
        enable_system_fanout=False,
        enable_session_gc=False,
        agent_queen=orchestrator,
        memory_service=memory,
    )

    task = asyncio.create_task(core.run_forever())
    await _wait_until(lambda: core._router_task is not None and core._watcher_task is not None)

    core.bus.publish_nowait(
        Observation(
            obs_type=ObservationType.MESSAGE,
            source_name="test_input",
            session_key="dm:test_mem",
            actor=Actor(actor_id="u1", actor_type="user"),
            payload=MessagePayload(text="hello"),
        )
    )

    await _wait_until(lambda: len(memory.events) >= 2 and len(memory.finished) >= 1)

    assert len(memory.turns) == 1
    assert memory.turns[0]["input_event_id"] == memory.events[0]["event_id"]
    assert memory.finished[0]["status"] == "ok"
    assert memory.finished[0]["final_output_obs_id"] == orchestrator.last_emit_obs_id

    await core.shutdown()
    assert memory.closed is True
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_core_drop_action_persists_event_only():
    spy = SpyOrchestrator()
    memory = FakeMemoryService()
    core = Core(
        bus_maxsize=100,
        inbox_maxsize=16,
        system_session_key="system",
        message_routing="user",
        enable_system_fanout=False,
        enable_session_gc=False,
        gate=DropGate(),
        agent_queen=spy,
        memory_service=memory,
    )

    task = asyncio.create_task(core.run_forever())
    await _wait_until(lambda: core._router_task is not None and core._watcher_task is not None)

    core.bus.publish_nowait(
        Observation(
            obs_type=ObservationType.MESSAGE,
            source_name="test_input",
            session_key="dm:test_drop",
            actor=Actor(actor_id="u1", actor_type="user"),
            payload=MessagePayload(text="hello"),
        )
    )

    await _wait_until(lambda: len(memory.events) >= 1)
    await asyncio.sleep(0.1)

    assert spy.calls == 0
    assert len(memory.turns) == 0
    assert len(memory.finished) == 0

    await core.shutdown()
    assert memory.closed is True
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_core_skip_turn_when_append_event_failed():
    orchestrator = EmitOrchestrator()
    memory = FailingEventMemoryService()
    core = Core(
        bus_maxsize=100,
        inbox_maxsize=16,
        system_session_key="system",
        message_routing="user",
        enable_system_fanout=False,
        enable_session_gc=False,
        agent_queen=orchestrator,
        memory_service=memory,
    )

    task = asyncio.create_task(core.run_forever())
    await _wait_until(lambda: core._router_task is not None and core._watcher_task is not None)

    core.bus.publish_nowait(
        Observation(
            obs_type=ObservationType.MESSAGE,
            source_name="test_input",
            session_key="dm:test_append_event_fail",
            actor=Actor(actor_id="u1", actor_type="user"),
            payload=MessagePayload(text="hello"),
        )
    )

    await _wait_until(lambda: orchestrator.calls >= 1)
    await asyncio.sleep(0.1)

    assert len(memory.turns) == 0
    assert len(memory.finished) == 0

    await core.shutdown()
    assert memory.closed is True
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_core_emit_metadata_none_is_handled_for_memory_tagging():
    orchestrator = EmitOrchestratorWithNoneMetadata()
    memory = FakeMemoryService()
    core = Core(
        bus_maxsize=100,
        inbox_maxsize=16,
        system_session_key="system",
        message_routing="user",
        enable_system_fanout=False,
        enable_session_gc=False,
        agent_queen=orchestrator,
        memory_service=memory,
    )

    task = asyncio.create_task(core.run_forever())
    await _wait_until(lambda: core._router_task is not None and core._watcher_task is not None)

    core.bus.publish_nowait(
        Observation(
            obs_type=ObservationType.MESSAGE,
            source_name="test_input",
            session_key="dm:test_metadata_none",
            actor=Actor(actor_id="u1", actor_type="user"),
            payload=MessagePayload(text="hello"),
        )
    )

    await _wait_until(lambda: len(memory.finished) >= 1)

    assert orchestrator.calls >= 1
    assert len(memory.turns) == 1
    assert memory.finished[0]["status"] == "ok"
    assert memory.finished[0]["final_output_obs_id"] == orchestrator.last_emit_obs_id

    await core.shutdown()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
