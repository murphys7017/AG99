import pytest

from astrbot.core.executors.contracts import (
    ExecutionFinalUpdate,
    ExecutionOutputMaterial,
    ExecutionResult,
)
from astrbot.core.executors.registry import (
    register_executor_factory,
    resolve_core_executor_selection,
    resolve_executor_factory,
)
from astrbot.core.executors.runtime import drive_executor_run


class _ScriptedExecutorBody:
    executor_id = "scripted"

    def __init__(self, text: str) -> None:
        self.text = text
        self.closed = False
        self.stop_requested = False

    def request_stop(self) -> None:
        self.stop_requested = True

    def request_follow_up(self, message_text: str):
        self.text = message_text
        return None

    def cancel_follow_up(self, ticket) -> bool:
        del ticket
        return False

    async def stream(self):
        if self.stop_requested:
            return
        yield ExecutionFinalUpdate(
            ExecutionResult(output=ExecutionOutputMaterial(text=self.text))
        )

    async def aclose(self) -> None:
        self.closed = True


class _ProviderManager:
    def __init__(self, config=None):
        self.config = config

    def get_execution_adapter_config(self, provider_id, runner_type):
        assert runner_type == "codex_cli"
        if self.config is None:
            raise ValueError("Agent runner provider not found")
        assert provider_id == "codex-main"
        return self.config


def test_resolve_core_executor_selection_uses_frozen_bot_settings():
    native = resolve_core_executor_selection(
        {"core_execution": {"executor_id": "native", "codex_cli": {}}},
        provider_manager=_ProviderManager(),
        execution_source="interaction",
    )
    assert native.executor_id == "native"
    assert native.instance_id is None

    codex = resolve_core_executor_selection(
        {
            "core_execution": {
                "executor_id": "codex_cli",
                "codex_cli": {"provider_id": "codex-main"},
            }
        },
        provider_manager=_ProviderManager({"id": "codex-main", "enable": True}),
        execution_source="proactive",
    )
    assert codex.executor_id == "codex_cli"
    assert codex.instance_id == "codex-main"


def test_core_executor_selection_is_independent_from_agent_runner():
    selected = resolve_core_executor_selection(
        {
            "agent_runner": {"mode": "dify", "provider_id": "dify-main"},
            "core_execution": {"executor_id": "native", "codex_cli": {}},
        },
        provider_manager=_ProviderManager(),
        execution_source="proactive",
    )
    assert selected.executor_id == "native"


def test_executor_factory_registry_rejects_duplicate_ids():
    assert callable(resolve_executor_factory("native"))
    with pytest.raises(ValueError, match="already registered: native"):
        register_executor_factory("native", lambda **_kwargs: None)


@pytest.mark.asyncio
async def test_scripted_body_uses_configuration_factory_and_shared_driver():
    register_executor_factory("scripted", lambda **kwargs: _ScriptedExecutorBody(**kwargs))
    body = resolve_executor_factory("scripted")(text="scripted result")

    result = await drive_executor_run(head=None, body=body, run=body)

    assert result.output is not None
    assert result.output.text == "scripted result"
    assert body.closed is True
