import pytest

from astrbot.core.executors.codex_cli import CodexExecutorRun
from astrbot.core.executors.contracts import (
    ExecutionFinalUpdate,
    ExecutionProgressUpdate,
)


class _Session:
    def __init__(self, events):
        self.events = events
        self.interrupted = False

    async def iter_turn(self, prompt):
        assert prompt == "inspect"
        for event in self.events:
            yield event

    async def interrupt(self, turn_id=None):
        self.interrupted = True


@pytest.mark.asyncio
async def test_codex_run_emits_progress_and_one_final_text():
    run = CodexExecutorRun(
        session=_Session(
            [
                {"method": "turn/started", "params": {}},
                {"method": "item/agentMessage/delta", "params": {"delta": "done"}},
                {"method": "turn/completed", "params": {}},
            ]
        ),
        prompt="inspect",
    )
    updates = [update async for update in run.stream()]
    assert isinstance(updates[0], ExecutionProgressUpdate)
    assert isinstance(updates[1], ExecutionFinalUpdate)
    assert updates[1].result.output.text == "done"
    await run.aclose()


@pytest.mark.asyncio
async def test_codex_run_rejects_failed_turn():
    run = CodexExecutorRun(
        session=_Session([{"method": "turn/failed", "params": {"message": "boom"}}]),
        prompt="inspect",
    )
    with pytest.raises(RuntimeError, match="boom"):
        _ = [update async for update in run.stream()]
