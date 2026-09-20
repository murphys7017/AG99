import asyncio
from types import SimpleNamespace

import pytest

from astrbot.core.agent.response import AgentResponse
from astrbot.core.astr_agent_run_util import (
    ExecutorStreamItem,
    NativeExecutionLoop,
    NativeExecutorAdapter,
    run_agent,
)
from astrbot.core.execution import CoreExecutionArtifact, CoreExecutionProgress
from astrbot.core.message.components import Json
from astrbot.core.message.message_event_result import MessageChain


class FakeNativeRunner:
    def __init__(self):
        self.provider = SimpleNamespace(name="provider")
        self.run_context = SimpleNamespace(messages=["message"])
        self.stats = SimpleNamespace(to_dict=lambda: {"steps": 1})
        self.final_response = object()
        self.stop_requested = False
        self.aborted = False
        self.completed = True
        self.streaming = False
        self.req = None
        self.follow_up_messages = []
        self.req = SimpleNamespace(func_tool="tools")

    def request_stop(self):
        self.stop_requested = True

    def done(self):
        return self.completed

    def was_aborted(self):
        return self.aborted

    def get_final_llm_resp(self):
        return self.final_response

    def follow_up(self, *, message_text):
        self.follow_up_messages.append(message_text)
        return message_text

    def cancel_follow_up(self, ticket):
        return ticket == "ticket"

    class Hooks:
        async def on_agent_done(self, context, response):
            context.done_response = response

    agent_hooks = Hooks()


def test_native_executor_adapter_exposes_control_and_observation_boundary():
    runner = FakeNativeRunner()
    adapter = NativeExecutorAdapter(runner)

    adapter.request_stop()

    assert runner.stop_requested is True
    assert adapter.provider is runner.provider
    assert adapter.done() is True
    assert adapter.was_aborted() is False
    assert adapter.final_response() is runner.final_response
    assert adapter.messages == ["message"]
    assert adapter.stats is runner.stats
    assert adapter.run_context is runner.run_context
    assert adapter.streaming is runner.streaming
    assert adapter.req is runner.req
    assert adapter.agent_hooks is runner.agent_hooks
    assert adapter.follow_up(message_text="follow-up") == "follow-up"
    assert runner.follow_up_messages == ["follow-up"]
    assert adapter.cancel_follow_up("ticket") is True
    adapter.force_final_response(instruction="finish")
    assert runner.req.func_tool is None
    assert runner.run_context.messages[-1].content == "finish"


@pytest.mark.asyncio
async def test_native_step_stream_preserves_response_and_closes_on_early_exit():
    closed = []
    response = AgentResponse(type="llm_result", data={})

    class Runner(FakeNativeRunner):
        async def step(self):
            try:
                yield response
                yield response
            finally:
                closed.append(True)

    stream = NativeExecutorAdapter(Runner()).step()
    assert await anext(stream) is response
    await stream.aclose()
    assert closed == [True]


@pytest.mark.asyncio
async def test_native_executor_adapter_normalizes_native_step_responses():
    native_response = AgentResponse(
        type="tool_call",
        data={
            "chain": MessageChain(
                chain=[Json(data={"id": "call-1", "name": "search"})],
                type="tool_call",
            )
        },
    )

    class Runner(FakeNativeRunner):
        async def step(self):
            yield native_response

    stream = NativeExecutorAdapter(Runner()).stream()
    item = await anext(stream)
    await stream.aclose()

    assert item == ExecutorStreamItem(
        kind="tool_call",
        chain=native_response.data["chain"],
    )


@pytest.mark.asyncio
async def test_native_executor_adapter_normalizes_complete_native_run_and_closes_stream():
    closed = []
    native_response = AgentResponse(type="tool_call", data={})

    class Runner(FakeNativeRunner):
        async def step_until_done(self, max_step):
            assert max_step == 3
            try:
                yield native_response
            finally:
                closed.append(True)

    stream = NativeExecutorAdapter(Runner()).run_until_done(3)
    assert await anext(stream) == ExecutorStreamItem(kind="tool_call")
    await stream.aclose()

    assert closed == [True]


@pytest.mark.asyncio
async def test_native_execution_loop_keeps_progress_and_output_boundary(monkeypatch):
    class Event:
        def is_stopped(self):
            return False

        def get_extra(self, key):
            return None

    response = AgentResponse(
        type="tool_call",
        data={
            "chain": MessageChain(
                chain=[Json(data={"id": "call-1", "name": "search"})],
                type="tool_call",
            )
        },
    )

    class Runner(FakeNativeRunner):
        def __init__(self):
            super().__init__()
            self.completed = False
            self.run_context.context = SimpleNamespace(event=Event())

        async def step(self):
            self.completed = True
            yield response

    emitted = []
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.record_interaction_turn_core_execution_event",
        lambda event, **kwargs: emitted.append(kwargs),
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.get_core_execution_head",
        lambda event: None,
    )

    loop = NativeExecutionLoop(NativeExecutorAdapter(Runner()), max_step=1)
    items = [item async for item in loop.stream()]

    assert items == [ExecutorStreamItem(kind="tool_call", chain=response.data["chain"])]
    assert [item["kind"] for item in emitted] == ["working", "progress"]


@pytest.mark.asyncio
async def test_native_execution_loop_closes_before_reporting_abort(monkeypatch):
    closed = []

    class Event:
        def is_stopped(self):
            return True

        def get_extra(self, key):
            return None

    class Runner(FakeNativeRunner):
        def __init__(self):
            super().__init__()
            self.completed = False
            self.run_context.context = SimpleNamespace(event=Event())

        async def step(self):
            try:
                yield AgentResponse(type="aborted", data={})
            finally:
                closed.append(True)

    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.record_interaction_turn_core_execution_event",
        lambda event, **kwargs: None,
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.get_core_execution_head",
        lambda event: None,
    )

    loop = NativeExecutionLoop(NativeExecutorAdapter(Runner()), max_step=1)
    items = [item async for item in loop.stream()]

    assert items == []
    assert loop.aborted is True
    assert closed == [True]


@pytest.mark.asyncio
async def test_run_agent_preserves_non_streaming_visible_output(monkeypatch):
    class Event:
        def __init__(self):
            self.results = []

        def is_stopped(self):
            return False

        def get_extra(self, key):
            return None

        def set_result(self, result):
            self.results.append(result)

        def clear_result(self):
            pass

        def get_platform_name(self):
            return "test"

        def get_platform_id(self):
            return "test"

    response = AgentResponse(
        type="llm_result",
        data={"chain": MessageChain().message("loop output")},
    )

    class Runner(FakeNativeRunner):
        def __init__(self):
            super().__init__()
            self.completed = False
            self.run_context.context = SimpleNamespace(event=Event())

        async def step(self):
            self.completed = True
            yield response

    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.record_interaction_turn_core_execution_event",
        lambda event, **kwargs: None,
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.get_core_execution_head",
        lambda event: None,
    )

    executor = NativeExecutorAdapter(Runner())
    outputs = [chain async for chain in run_agent(executor, max_step=1)]

    assert [chain.get_plain_text() for chain in outputs] == ["loop output"]
    assert executor.run_context.context.event.results[0].chain == response.data["chain"].chain


def test_run_agent_entry_builds_explicit_output_bridge(monkeypatch):
    executor = NativeExecutorAdapter(FakeNativeRunner())
    calls = []

    class Bridge:
        def __init__(self, actual_executor, **kwargs):
            calls.append((actual_executor, kwargs))

        async def stream(self):
            yield MessageChain().message("bridged")

    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.NativeExecutionOutputBridge",
        Bridge,
    )

    async def collect():
        return [chain async for chain in run_agent(executor, max_step=7)]

    outputs = asyncio.run(collect())

    assert [chain.get_plain_text() for chain in outputs] == ["bridged"]
    assert calls == [
        (
            executor,
            {
                "max_step": 7,
                "show_tool_use": True,
                "show_tool_call_result": False,
                "stream_to_general": False,
                "show_reasoning": False,
                "buffer_intermediate_messages": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_native_executor_adapter_closes_native_stream_on_early_exit():
    closed = []

    class Runner(FakeNativeRunner):
        async def step(self):
            try:
                yield AgentResponse(type="llm_result", data={})
                await asyncio.sleep(60)
            finally:
                closed.append(True)

    stream = NativeExecutorAdapter(Runner()).stream()
    await anext(stream)
    await stream.aclose()

    assert closed == [True]


def test_native_executor_adapter_emits_through_core_boundary(monkeypatch):
    event = object()
    runner = FakeNativeRunner()
    runner.final_response = SimpleNamespace(
        role="assistant",
        completion_text="done",
        result_chain=None,
    )
    runner.run_context.context = SimpleNamespace(event=event)
    adapter = NativeExecutorAdapter(runner)
    emitted = []
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.record_interaction_turn_core_execution_event",
        lambda actual_event, **kwargs: emitted.append((actual_event, kwargs)),
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.get_core_execution_head",
        lambda actual_event: None,
    )

    adapter.emit_event(
        kind="working",
        metadata={"source": "test"},
    )

    assert emitted == [
        (
            event,
            {
                "kind": "working",
                "executor_id": "native",
                "metadata": {"source": "test"},
            },
        )
    ]


def test_native_executor_adapter_reports_normalized_lifecycle_facts(monkeypatch):
    event = object()
    runner = FakeNativeRunner()
    runner.run_context.context = SimpleNamespace(event=event)
    adapter = NativeExecutorAdapter(runner)
    emitted = []
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.record_interaction_turn_core_execution_event",
        lambda actual_event, **kwargs: emitted.append((actual_event, kwargs)),
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.get_core_execution_head",
        lambda actual_event: None,
    )

    adapter.emit_event(
        kind="submitted",
        metadata={"provider_id": "test"},
    )
    adapter.complete(
        artifact=CoreExecutionArtifact(
            artifact_id="final_response",
            artifact_kind="text",
        )
    )
    adapter.fail(metadata={"error": "ignored by lifecycle"})
    adapter.cancel(metadata={"reason": "cancelled"})

    assert [item[1]["kind"] for item in emitted] == [
        "submitted",
        "artifact_ready",
        "completed",
        "failed",
        "cancelled",
    ]
    assert all(item[1]["executor_id"] == "native" for item in emitted)


def test_native_executor_adapter_keeps_earlier_terminal_outcome(monkeypatch):
    event = object()
    runner = FakeNativeRunner()
    runner.run_context.context = SimpleNamespace(event=event)
    adapter = NativeExecutorAdapter(runner)
    terminal = SimpleNamespace(execution=object())
    emitted = []
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.get_core_execution_head",
        lambda actual_event: SimpleNamespace(terminal_event=terminal),
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.record_interaction_turn_core_execution_event",
        lambda actual_event, **kwargs: emitted.append((actual_event, kwargs)),
    )

    assert (
        adapter.complete(
            artifact=CoreExecutionArtifact(
                artifact_id="late_response",
                artifact_kind="text",
            )
        )
        is terminal.execution
    )
    assert emitted == []


def test_native_executor_adapter_releases_core_head(monkeypatch):
    event = object()
    runner = FakeNativeRunner()
    runner.run_context.context = SimpleNamespace(event=event)
    adapter = NativeExecutorAdapter(runner)
    calls = []
    head = SimpleNamespace(
        release_executor=lambda **kwargs: calls.append(("release", kwargs)) or True,
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.get_core_execution_head",
        lambda actual_event: head,
    )

    assert adapter.release_from_core_head() is True
    assert calls == [("release", {"executor_id": "native"})]


def test_native_executor_adapter_falls_back_to_direct_stop_without_head(monkeypatch):
    event = object()
    runner = FakeNativeRunner()
    runner.run_context.context = SimpleNamespace(event=event)
    adapter = NativeExecutorAdapter(runner)
    emitted = []
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.get_core_execution_head",
        lambda actual_event: None,
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.record_interaction_turn_core_execution_event",
        lambda actual_event, **kwargs: emitted.append((actual_event, kwargs)),
    )

    adapter.request_cancellation(metadata={"reason": "agent_aborted"})

    assert runner.stop_requested is True
    assert emitted == [
        (
            event,
            {
                "kind": "cancelled",
                "executor_id": "native",
                "metadata": {"reason": "agent_aborted"},
            },
        )
    ]


def test_native_executor_adapter_projects_final_response_metadata():
    runner = FakeNativeRunner()
    runner.final_response = SimpleNamespace(
        role="assistant",
        completion_text="hello",
        result_chain=None,
    )
    adapter = NativeExecutorAdapter(runner)

    assert adapter.completed_successfully() is True
    assert adapter.final_response_artifact().event_metadata() == {
        "artifact_id": "final_response",
        "artifact_kind": "text",
        "text_length": 5,
        "component_count": 0,
    }
    assert adapter.failure_metadata() == {
        "reason": "runner_error",
        "error": "hello",
    }


def test_native_executor_adapter_finalizes_success_and_failure(monkeypatch):
    event = object()
    runner = FakeNativeRunner()
    runner.final_response = SimpleNamespace(
        role="assistant",
        completion_text="done",
        result_chain=None,
    )
    runner.run_context.context = SimpleNamespace(event=event)
    adapter = NativeExecutorAdapter(runner)
    emitted = []
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.record_interaction_turn_core_execution_event",
        lambda actual_event, **kwargs: emitted.append((actual_event, kwargs)),
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.get_core_execution_head",
        lambda actual_event: None,
    )

    adapter.finalize()
    assert [item[1]["kind"] for item in emitted] == ["artifact_ready", "completed"]

    emitted.clear()
    runner.completed = False
    adapter.finalize()
    assert [item[1]["kind"] for item in emitted] == ["failed"]


def test_native_executor_adapter_builds_final_response_chain():
    runner = FakeNativeRunner()
    runner.final_response = SimpleNamespace(
        role="assistant",
        completion_text="hello",
        result_chain=None,
    )
    adapter = NativeExecutorAdapter(runner)

    assert adapter.final_response_chain().get_plain_text() == "hello"

    runner.final_response = SimpleNamespace(
        role="assistant",
        completion_text="",
        result_chain=MessageChain().message("chain"),
    )
    assert adapter.final_response_chain().get_plain_text() == "chain"

    runner.final_response = None
    assert adapter.final_response_chain() is None


def test_native_executor_adapter_observes_tool_progress_without_result_content(
    monkeypatch,
):
    event = object()
    runner = FakeNativeRunner()
    runner.run_context.context = SimpleNamespace(event=event)
    adapter = NativeExecutorAdapter(runner)
    emitted = []
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.record_interaction_turn_core_execution_event",
        lambda actual_event, **kwargs: emitted.append((actual_event, kwargs)),
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.get_core_execution_head",
        lambda actual_event: None,
    )

    assert adapter.observe_response(
        ExecutorStreamItem(
            kind="tool_call",
            chain=MessageChain(
                chain=[Json(data={"id": "call-1", "name": "search"})],
                type="tool_call",
            ),
        )
    ) is None
    assert adapter.observe_response(
        ExecutorStreamItem(
            kind="tool_call_result",
            chain=MessageChain(
                chain=[Json(data={"id": "call-1", "result": "secret output"})],
                type="tool_call_result",
            ),
        )
    ) is None
    assert adapter.observe_response(ExecutorStreamItem(kind="llm_result")) is None

    assert [item[1]["kind"] for item in emitted] == ["progress", "progress"]
    assert emitted[0][1]["metadata"] == {
        "source": "native_response",
        "response_type": "tool_call",
        "message_type": "tool_call",
        "component_count": 1,
        "tool_name": "search",
        "tool_call_id": "call-1",
    }
    assert emitted[1][1]["metadata"] == {
        "source": "native_response",
        "response_type": "tool_call_result",
        "message_type": "tool_call_result",
        "component_count": 1,
        "tool_call_id": "call-1",
        "result_length": len("secret output"),
    }


def test_core_execution_progress_rejects_reserved_attributes():
    with pytest.raises(ValueError, match="reserved keys: source"):
        CoreExecutionProgress(
            source="native_response",
            phase="tool_call",
            attributes={"source": "override"},
        )
