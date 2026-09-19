from types import SimpleNamespace

import pytest

from astrbot.core.agent.response import AgentResponse
from astrbot.core.astr_agent_run_util import NativeExecutorAdapter


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


def test_native_executor_adapter_emits_through_core_boundary(monkeypatch):
    event = object()
    runner = FakeNativeRunner()
    runner.run_context.context = SimpleNamespace(event=event)
    adapter = NativeExecutorAdapter(runner)
    emitted = []
    monkeypatch.setattr(
        "astrbot.core.astr_agent_run_util.record_interaction_turn_core_execution_event",
        lambda actual_event, **kwargs: emitted.append((actual_event, kwargs)),
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
