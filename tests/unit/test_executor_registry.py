import pytest

from astrbot.core.executors.contracts import (
    ExecutionFinalUpdate,
    ExecutionOutputMaterial,
    ExecutionResult,
)
from astrbot.core.executors.registry import (
    register_executor_factory,
    resolve_executor_factory,
    resolve_executor_id,
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


def test_resolve_executor_id_uses_active_snapshot_not_execution_source():
    snapshot = {"core_execution": {"executor_id": "native"}}

    assert resolve_executor_id(snapshot, execution_source="interaction") == "native"
    assert resolve_executor_id(snapshot, execution_source="proactive") == "native"


def test_resolve_executor_id_rejects_unknown_and_malformed_values():
    with pytest.raises(ValueError, match="unknown core execution executor_id: missing"):
        resolve_executor_id(
            {"core_execution": {"executor_id": "missing"}},
            execution_source="interaction",
        )
    with pytest.raises(ValueError, match="core_execution must be an object"):
        resolve_executor_id({"core_execution": "native"}, execution_source="cron")
    with pytest.raises(ValueError, match="non-empty string"):
        resolve_executor_id(
            {"core_execution": {"executor_id": ""}}, execution_source="cron"
        )


def test_executor_factory_registry_rejects_duplicate_ids():
    assert callable(resolve_executor_factory("native"))
    with pytest.raises(ValueError, match="already registered: native"):
        register_executor_factory("native", lambda **_kwargs: None)


@pytest.mark.asyncio
async def test_scripted_body_uses_configuration_factory_and_shared_driver():
    register_executor_factory("scripted", lambda **kwargs: _ScriptedExecutorBody(**kwargs))
    executor_id = resolve_executor_id(
        {"core_execution": {"executor_id": "scripted"}},
        execution_source="test",
    )
    body = resolve_executor_factory(executor_id)(text="scripted result")

    result = await drive_executor_run(head=None, body=body, run=body)

    assert result.output is not None
    assert result.output.text == "scripted result"
    assert body.closed is True
