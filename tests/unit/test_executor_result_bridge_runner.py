import pytest

from astrbot.core.execution import (
    CoreExecutionHead,
    CoreExecutionLifecycle,
    CoreExecutionSession,
    CoreExecutionSpec,
)
from astrbot.core.executors.contracts import (
    ExecutionFinalUpdate,
    ExecutionOutputMaterial,
    ExecutionResult,
)
from astrbot.core.interaction.executor_result_bridge import (
    drive_executor_to_personal_output,
)
from astrbot.core.prompt.context_types import ContextPack


class _Body:
    executor_id = "codex_cli"

    def request_stop(self):
        pass

    def request_follow_up(self, message_text):
        return None

    def cancel_follow_up(self, ticket):
        return False


class _Run:
    async def stream(self):
        yield ExecutionFinalUpdate(
            result=ExecutionResult(output=ExecutionOutputMaterial(text="done"))
        )

    async def aclose(self):
        pass


class _Controller:
    async def deliver_core_execution_result(self, message, event):
        event.append(message.get_plain_text())


@pytest.mark.asyncio
async def test_driver_routes_final_result_to_personal_controller():
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(), turn_id="turn-1", task_spec={}
    )
    head = CoreExecutionHead(CoreExecutionLifecycle(CoreExecutionSession(spec)))
    visible = []
    result = await drive_executor_to_personal_output(
        event=visible,
        output_controller=_Controller(),
        head=head,
        body=_Body(),
        run=_Run(),
    )
    assert result.output.text == "done"
    assert visible == ["done"]
