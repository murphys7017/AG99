import asyncio
from types import SimpleNamespace

import pytest

from astrbot.core.executors.contracts import (
    ExecutionFinalUpdate,
    ExecutionOutputMaterial,
    ExecutionProgressUpdate,
    ExecutionResult,
)
from astrbot.core.executors.runtime import drive_executor_run
from astrbot.core.interaction.turn_state import TurnExecutionScope


class _Run:
    executor_id = "scripted"

    def __init__(self, updates, *, error=None):
        self.updates = updates
        self.error = error
        self.closed = False

    def request_stop(self):
        pass

    async def stream(self):
        for update in self.updates:
            yield update
        if self.error is not None:
            raise self.error

    async def aclose(self):
        self.closed = True


class _Head:
    def __init__(self):
        self.calls = []
        self.terminal_event = None

    def activate_executor_body(self, body, *, submission_metadata=None):
        self.calls.append(("activate", body.executor_id, submission_metadata))

    def complete(self, *, executor_id, artifact=None, metadata=None):
        self.calls.append(("complete", executor_id, artifact, metadata))
        self.terminal_event = SimpleNamespace(kind="completed")

    def fail(self, *, executor_id, metadata=None):
        self.calls.append(("fail", executor_id, metadata))
        self.terminal_event = SimpleNamespace(kind="failed")

    def cancel(self, *, executor_id, metadata=None):
        self.calls.append(("cancel", executor_id, metadata))
        self.terminal_event = SimpleNamespace(kind="cancelled")

    def release_executor(self, *, executor_id):
        self.calls.append(("release", executor_id))
        return True


@pytest.mark.asyncio
async def test_drive_executor_run_settles_success_and_closes():
    result = ExecutionResult(output=ExecutionOutputMaterial(text="done"))
    run = _Run(
        [
            ExecutionProgressUpdate(summary="working"),
            ExecutionFinalUpdate(result=result),
        ]
    )
    head = _Head()
    updates = []

    actual = await drive_executor_run(
        head=head,
        body=SimpleNamespace(executor_id="scripted"),
        run=run,
        submission_metadata={"source": "test"},
        output_sink=updates.append,
    )

    assert actual is result
    assert updates[0].summary == "working"
    assert [call[0] for call in head.calls] == [
        "activate",
        "complete",
        "release",
    ]
    assert run.closed is True


@pytest.mark.asyncio
async def test_drive_executor_run_rejects_missing_final_and_fails():
    run = _Run([ExecutionProgressUpdate(summary="working")])
    head = _Head()

    with pytest.raises(RuntimeError, match="without a final result"):
        await drive_executor_run(
            head=head,
            body=SimpleNamespace(executor_id="scripted"),
            run=run,
        )

    assert [call[0] for call in head.calls] == ["activate", "fail", "release"]
    assert run.closed is True


@pytest.mark.asyncio
async def test_drive_executor_run_cancels_and_releases_on_task_cancellation():
    run = _Run([])
    head = _Head()

    async def cancelled_stream():
        raise asyncio.CancelledError
        yield  # pragma: no cover

    run.stream = cancelled_stream

    with pytest.raises(asyncio.CancelledError):
        await drive_executor_run(
            head=head,
            body=SimpleNamespace(executor_id="scripted"),
            run=run,
        )

    assert [call[0] for call in head.calls] == ["activate", "cancel", "release"]
    assert run.closed is True


@pytest.mark.asyncio
async def test_final_output_failure_does_not_rewrite_completed_execution():
    result = ExecutionResult(output=ExecutionOutputMaterial(text="done"))
    run = _Run([ExecutionFinalUpdate(result=result)])
    head = _Head()

    async def fail_delivery(_update):
        raise RuntimeError("delivery failed")

    with pytest.raises(RuntimeError, match="delivery failed"):
        await drive_executor_run(
            head=head,
            body=SimpleNamespace(executor_id="scripted"),
            run=run,
            output_sink=fail_delivery,
        )

    assert [call[0] for call in head.calls] == ["activate", "complete", "release"]
    assert run.closed is True


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_replace_primary_execution_failure():
    run = _Run([], error=RuntimeError("primary failure"))
    head = _Head()

    async def failing_close():
        raise RuntimeError("cleanup failure")

    run.aclose = failing_close

    with pytest.raises(RuntimeError, match="primary failure"):
        await drive_executor_run(
            head=head,
            body=SimpleNamespace(executor_id="scripted"),
            run=run,
        )

    assert [call[0] for call in head.calls] == ["activate", "fail"]


@pytest.mark.asyncio
async def test_unresponsive_cleanup_retains_binding_and_turn_ownership():
    run = _Run(
        [ExecutionFinalUpdate(result=ExecutionResult(output=ExecutionOutputMaterial(text="done")))]
    )
    head = _Head()
    scope = TurnExecutionScope()
    release_close = asyncio.Event()
    invalidated = []

    async def slow_close():
        await release_close.wait()

    run.aclose = slow_close
    run.invalidate = lambda: invalidated.append(True)
    try:
        await asyncio.wait_for(
            drive_executor_run(
                head=head,
                body=SimpleNamespace(executor_id="scripted"),
                run=run,
                cleanup_scope=scope,
            ),
            timeout=2,
        )
        assert invalidated == [True]
        assert [call[0] for call in head.calls] == ["activate", "complete"]
        assert scope.tasks["executor_cleanup"]
    finally:
        release_close.set()
        await scope.close(timeout_seconds=1.0)
