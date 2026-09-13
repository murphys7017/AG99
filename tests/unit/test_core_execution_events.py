import pytest

from astrbot.core.execution import (
    CORE_EXECUTION_SPEC_EXTRA_KEY,
    CoreCommand,
    CoreCommandKind,
    CoreEvent,
    CoreExecutionEvent,
    CoreExecutionEventKind,
    CoreExecutionLifecycle,
    CoreExecutionSession,
    CoreExecutionSessionStatus,
    CoreExecutionSpec,
    bind_core_execution_session,
    get_core_execution_lifecycle,
    get_core_execution_session,
    start_core_execution_lifecycle,
)
from astrbot.core.interaction.turn_state import (
    MAX_CORE_EXECUTION_EVENTS_PER_TURN,
    InteractionTurnState,
    get_interaction_turn_core_execution_events,
    record_interaction_turn_core_execution_event,
)
from astrbot.core.prompt.context_types import ContextPack


class _Event:
    def __init__(self) -> None:
        self._extras = {}
        self.trace = _Trace()

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value) -> None:
        self._extras[key] = value


class _Trace:
    def __init__(self) -> None:
        self.records = []

    def record(self, name, **metadata) -> None:
        self.records.append((name, metadata))


def _interaction_event() -> _Event:
    event = _Event()
    event.set_extra("_interaction_enabled", True)
    event.set_extra("_interaction_turn_state", InteractionTurnState(turn_id="turn-1"))
    event.set_extra(
        CORE_EXECUTION_SPEC_EXTRA_KEY,
        CoreExecutionSpec.from_context_pack(
            context_pack=ContextPack(),
            turn_id="turn-1",
        ),
    )
    return event


def test_core_execution_journal_preserves_progress_and_terminal_first_write():
    event = _interaction_event()
    metadata = {"nested": {"value": 1}}

    submitted = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.SUBMITTED,
        executor_id="native",
        metadata=metadata,
    )
    metadata["nested"]["value"] = 2
    assert submitted is not None
    assert submitted.metadata == {"nested": {"value": 1}}
    with pytest.raises(TypeError):
        submitted.metadata["other"] = "value"
    with pytest.raises(TypeError):
        submitted.metadata["nested"]["value"] = 3
    assert event.trace.records[0][1]["metadata"] == {"nested": {"value": 1}}

    for index in range(MAX_CORE_EXECUTION_EVENTS_PER_TURN + 8):
        event_item = record_interaction_turn_core_execution_event(
            event,
            kind=CoreExecutionEventKind.PROGRESS,
            executor_id="native",
            metadata={"index": index},
        )
        assert event_item is not None

    completed = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.COMPLETED,
        executor_id="native",
    )
    assert completed is not None
    assert (
        record_interaction_turn_core_execution_event(
            event,
            kind=CoreExecutionEventKind.FAILED,
            executor_id="native",
        )
        is None
    )

    events = get_interaction_turn_core_execution_events(event)
    assert len(events) == MAX_CORE_EXECUTION_EVENTS_PER_TURN
    assert events[0].kind is CoreExecutionEventKind.SUBMITTED
    assert events[-1].kind is CoreExecutionEventKind.COMPLETED
    progress = [item for item in events if item.kind is CoreExecutionEventKind.PROGRESS]
    assert progress[0].metadata == {"index": 10}
    assert progress[-1].metadata == {
        "index": MAX_CORE_EXECUTION_EVENTS_PER_TURN + 7
    }
    assert event.trace.records[0][0] == "core_execution_event"
    assert event.trace.records[0][1]["kind"] == "submitted"


def test_core_execution_journal_requires_matching_interaction_turn():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    event.set_extra(
        CORE_EXECUTION_SPEC_EXTRA_KEY,
        CoreExecutionSpec(
            execution_id=spec.execution_id,
            core_task_id=spec.core_task_id,
            turn_id="other-turn",
            context_pack=spec.context_pack,
        ),
    )

    assert (
        record_interaction_turn_core_execution_event(
            event,
            kind=CoreExecutionEventKind.SUBMITTED,
            executor_id="native",
        )
        is None
    )
    assert get_interaction_turn_core_execution_events(event) == []


def test_core_execution_journal_uses_bound_session_sequence():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)

    assert get_core_execution_session(event) is None
    session = bind_core_execution_session(event, spec)
    assert get_core_execution_session(event) is session

    submitted = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.SUBMITTED,
        executor_id="native",
    )
    working = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.WORKING,
        executor_id="native",
    )

    assert submitted is not None
    assert working is not None
    assert [item.sequence for item in session.events] == [1, 2]
    assert all(isinstance(item, CoreEvent) for item in session.events)
    assert event.trace.records[-1][1]["sequence"] == 2


def test_core_execution_lifecycle_accepts_native_submit_once():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)

    lifecycle = start_core_execution_lifecycle(event, spec)

    assert get_core_execution_lifecycle(event) is lifecycle
    assert get_core_execution_session(event) is lifecycle.session
    assert lifecycle.start() is False

    submitted = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.SUBMITTED,
        executor_id="native",
    )
    assert submitted is not None
    assert lifecycle.session.status is CoreExecutionSessionStatus.SUBMITTED
    assert [item.sequence for item in lifecycle.session.events] == [1]
    assert lifecycle.start() is False


def test_core_execution_lifecycle_normalizes_terminal_ledger_status():
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-1",
    )
    failed = CoreExecutionLifecycle(session=CoreExecutionSession(spec=spec))
    assert failed.start() is True
    failed.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.SUBMITTED,
            executor_id="native",
        )
    )
    failed.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.FAILED,
            executor_id="native",
        )
    )
    assert failed.ledger_status() == "failed"

    cancelled_spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-2",
    )
    cancelled = CoreExecutionLifecycle(
        session=CoreExecutionSession(spec=cancelled_spec)
    )
    cancelled.record_event(
        CoreExecutionEvent.from_spec(
            cancelled_spec,
            kind=CoreExecutionEventKind.CANCELLED,
            executor_id="native",
        )
    )
    assert cancelled.ledger_status() == "cancelled"
    assert cancelled.ledger_status(user_aborted=True) == "aborted"


def test_core_execution_lifecycle_projects_terminal_failure_evidence():
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-1",
    )
    lifecycle = CoreExecutionLifecycle(session=CoreExecutionSession(spec=spec))
    lifecycle.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.FAILED,
            executor_id="native",
            metadata={"error_type": "RuntimeError", "error": "provider unavailable"},
        )
    )

    assert lifecycle.ledger_status() == "failed"
    assert lifecycle.terminal_error() == "provider unavailable"


def test_core_execution_lifecycle_cancellation_accepts_command_once():
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-1",
    )
    lifecycle = CoreExecutionLifecycle(session=CoreExecutionSession(spec=spec))

    cancelled = lifecycle.cancel(
        executor_id="native",
        metadata={"reason": "deadline_exceeded"},
    )

    assert cancelled.kind is CoreExecutionEventKind.CANCELLED
    assert cancelled.execution.metadata == {"reason": "deadline_exceeded"}
    assert lifecycle.session.status is CoreExecutionSessionStatus.CANCELLED
    assert lifecycle.cancel(executor_id="native") is cancelled


def test_core_execution_lifecycle_cancellation_stops_bound_executor_once():
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-1",
    )
    lifecycle = CoreExecutionLifecycle(session=CoreExecutionSession(spec=spec))
    stops = []
    lifecycle.bind_executor_stop_callback(lambda: stops.append("stop"))

    cancelled = lifecycle.cancel(executor_id="native")

    assert cancelled.kind is CoreExecutionEventKind.CANCELLED
    assert stops == ["stop"]
    assert lifecycle.cancel(executor_id="native") is cancelled
    assert stops == ["stop"]


def test_core_execution_lifecycle_preserves_terminal_state_when_stop_fails():
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-1",
    )
    lifecycle = CoreExecutionLifecycle(session=CoreExecutionSession(spec=spec))

    def fail_stop() -> None:
        raise RuntimeError("executor unavailable")

    lifecycle.bind_executor_stop_callback(fail_stop)
    cancelled = lifecycle.cancel(
        executor_id="native",
        metadata={"reason": "deadline_exceeded"},
    )

    assert cancelled.kind is CoreExecutionEventKind.CANCELLED
    assert lifecycle.session.status is CoreExecutionSessionStatus.CANCELLED
    assert lifecycle.executor_stop_error == "RuntimeError: executor unavailable"
    assert lifecycle.terminal_error() == "deadline_exceeded"


def test_core_execution_lifecycle_rebinding_same_executor_method_is_idempotent():
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-1",
    )
    lifecycle = CoreExecutionLifecycle(session=CoreExecutionSession(spec=spec))

    class _Executor:
        def request_stop(self):
            return None

    executor = _Executor()
    lifecycle.bind_executor_stop_callback(executor.request_stop)
    lifecycle.bind_executor_stop_callback(executor.request_stop)


def test_core_execution_journal_routes_cancellation_through_lifecycle():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    lifecycle = start_core_execution_lifecycle(event, spec)
    record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.SUBMITTED,
        executor_id="native",
    )

    cancelled = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.CANCELLED,
        executor_id="native",
        metadata={"reason": "stage_cancelled"},
    )

    assert cancelled is not None
    assert cancelled.metadata == {"reason": "stage_cancelled"}
    assert lifecycle.session.status is CoreExecutionSessionStatus.CANCELLED
    assert [item.kind for item in lifecycle.session.events] == [
        CoreExecutionEventKind.SUBMITTED,
        CoreExecutionEventKind.CANCELLED,
    ]


def test_core_execution_journal_records_executor_stop_callback_failure():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    lifecycle = start_core_execution_lifecycle(event, spec)

    def fail_stop() -> None:
        raise RuntimeError("executor unavailable")

    lifecycle.bind_executor_stop_callback(fail_stop)
    cancelled = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.CANCELLED,
        executor_id="native",
    )

    assert cancelled is not None
    assert [name for name, _ in event.trace.records] == [
        "core_execution_event",
        "core_execution_stop_callback_failed",
    ]


def test_core_execution_session_orders_events_and_accepts_commands_once():
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-1",
    )
    session = CoreExecutionSession(spec=spec)
    submit = CoreCommand(
        execution_id=spec.execution_id,
        turn_id=spec.turn_id,
        kind=CoreCommandKind.SUBMIT,
        command_id="submit-1",
        execution_spec=spec,
    )

    assert session.accept_command(submit) is True
    assert session.accept_command(submit) is False
    submitted = session.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.SUBMITTED,
            executor_id="native",
        )
    )
    working = session.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.WORKING,
            executor_id="native",
        )
    )
    completed = session.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.COMPLETED,
            executor_id="native",
        )
    )

    assert [submitted.sequence, working.sequence, completed.sequence] == [1, 2, 3]
    assert session.status is CoreExecutionSessionStatus.COMPLETED
    assert session.terminal_event is completed
    assert session.record_event(completed.execution) is completed
    with pytest.raises(ValueError, match="terminal"):
        session.record_event(
            CoreExecutionEvent.from_spec(
                spec,
                kind=CoreExecutionEventKind.FAILED,
                executor_id="native",
            )
        )


def test_core_execution_session_allows_cancellation_before_submission():
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-1",
    )
    session = CoreExecutionSession(spec=spec)

    assert session.accept_command(
        CoreCommand(
            execution_id=spec.execution_id,
            turn_id=spec.turn_id,
            kind=CoreCommandKind.CANCEL,
            command_id="cancel-before-submit",
        )
    )
    cancelled = session.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.CANCELLED,
            executor_id="native",
            metadata={"reason": "cancelled_before_submit"},
        )
    )

    assert cancelled.sequence == 1
    assert session.status is CoreExecutionSessionStatus.CANCELLED
