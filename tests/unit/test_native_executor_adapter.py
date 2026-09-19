from types import SimpleNamespace

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

    def request_stop(self):
        self.stop_requested = True

    def done(self):
        return self.completed

    def was_aborted(self):
        return self.aborted

    def get_final_llm_resp(self):
        return self.final_response


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
    assert adapter.runner is runner
