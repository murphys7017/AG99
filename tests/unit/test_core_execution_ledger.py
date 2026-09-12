import pytest

from astrbot.core.execution import CoreExecutionSpec
from astrbot.core.execution_ledger import CoreExecutionLedger
from astrbot.core.prompt.context_types import ContextPack


class _Database:
    def __init__(self) -> None:
        self.records = []

    async def insert_core_execution_record(self, record, *, retain: int) -> bool:
        self.records.append((record, retain))
        return True


@pytest.mark.asyncio
async def test_append_execution_materializes_record_from_spec():
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-1",
        task_spec={"metadata": {"core_task_id": "task-1"}},
        parent_execution_id="parent-1",
    )
    database = _Database()
    ledger = CoreExecutionLedger(database, retain_per_conversation=7)

    assert await ledger.append_execution(
        execution_spec=spec,
        conversation_id="conversation-1",
        executor_id="native",
        status="completed",
        messages=[{"role": "tool", "content": "evidence"}],
        result="done",
        token_usage={"total": 12},
    )

    record, retain = database.records[0]
    assert retain == 7
    assert record.execution_id == spec.execution_id
    assert record.core_task_id == "task-1"
    assert record.conversation_id == "conversation-1"
    assert record.turn_id == "turn-1"
    assert record.parent_execution_id == "parent-1"
    assert record.executor_id == "native"
    assert record.status == "completed"
    assert record.task_spec == spec.task_spec
    assert record.messages == [{"role": "tool", "content": "evidence"}]
    assert record.result == "done"
    assert record.token_usage == {"total": 12}
