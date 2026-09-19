import pytest

from astrbot.core.execution import (
    CORE_EXECUTION_HEAD_EXTRA_KEY,
    CORE_EXECUTION_SPEC_EXTRA_KEY,
    CoreCommand,
    CoreCommandDisposition,
    CoreCommandKind,
    CoreCommandOrigin,
    CoreEvent,
    CoreExecutionDeadlineView,
    CoreExecutionEvent,
    CoreExecutionEventKind,
    CoreExecutionEventMailbox,
    CoreExecutionHead,
    CoreExecutionLedgerPreparation,
    CoreExecutionLifecycle,
    CoreExecutionSession,
    CoreExecutionSessionStatus,
    CoreExecutionSpec,
    bind_core_execution_head,
    bind_core_execution_session,
    get_core_execution_head,
    get_core_execution_lifecycle,
    get_core_execution_session,
    start_core_execution_head,
    start_core_execution_lifecycle,
)
from astrbot.core.interaction.turn_state import (
    MAX_CORE_EXECUTION_EVENTS_PER_TURN,
    InteractionTurnState,
    bind_interaction_turn_core_execution_journal,
    get_interaction_turn_core_execution_events,
    record_interaction_turn_core_execution_event,
    record_interaction_turn_core_execution_ledger_persist_failure,
    record_interaction_turn_core_execution_ledger_settlement,
    record_interaction_turn_core_execution_stop_callback_failure,
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


def test_core_execution_head_publishes_each_event_once_and_routes_cancel():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    head = start_core_execution_head(event, spec)
    observed = []
    head.subscribe(observed.append)

    submitted = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.SUBMITTED,
        executor_id="native",
    )
    assert submitted is not None
    assert [item.sequence for item in observed] == [1]

    duplicate = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.SUBMITTED,
        executor_id="native",
    )
    assert duplicate is None
    assert [item.sequence for item in observed] == [1]

    cancelled = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.CANCELLED,
        executor_id="native",
        metadata={"reason": "user_cancelled"},
    )
    assert cancelled is not None
    assert [item.sequence for item in observed] == [1, 2]
    assert isinstance(get_core_execution_head(event), CoreExecutionHead)
    assert get_core_execution_lifecycle(event) is head.lifecycle


def test_core_execution_head_returns_idempotent_command_receipts():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    head = start_core_execution_head(event, spec)
    command = CoreCommand(
        execution_id=spec.execution_id,
        turn_id=spec.turn_id,
        kind=CoreCommandKind.CANCEL,
        reason="user_cancelled",
    )

    first = head.dispatch_command(command)
    second = head.dispatch_command(command)

    assert first.disposition is CoreCommandDisposition.ACCEPTED
    assert first.accepted is True
    assert first.origin is CoreCommandOrigin.PERSONAL
    assert first.session_status is CoreExecutionSessionStatus.CREATED
    assert second.disposition is CoreCommandDisposition.DUPLICATE
    assert second.accepted is False
    assert second.session_status is CoreExecutionSessionStatus.CREATED


@pytest.mark.asyncio
async def test_core_execution_head_mailbox_delivers_events_until_terminal():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    head = start_core_execution_head(event, spec)
    mailbox = head.subscribe_mailbox()

    record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.SUBMITTED,
        executor_id="native",
    )
    record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.WORKING,
        executor_id="native",
    )
    record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.COMPLETED,
        executor_id="native",
    )

    received = [await mailbox.receive() for _ in range(3)]
    assert [item.kind for item in received] == [
        CoreExecutionEventKind.SUBMITTED,
        CoreExecutionEventKind.WORKING,
        CoreExecutionEventKind.COMPLETED,
    ]
    with pytest.raises(StopAsyncIteration):
        await mailbox.receive()


def test_core_execution_mailbox_drops_progress_before_terminal_events():
    mailbox = CoreExecutionEventMailbox(maxsize=2)
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-mailbox",
    )

    submitted = CoreEvent(
        sequence=1,
        execution=CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.SUBMITTED,
            executor_id="native",
        ),
    )
    progress = CoreEvent(
        sequence=2,
        execution=CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.PROGRESS,
            executor_id="native",
            metadata={"text": "working"},
        ),
    )
    completed = CoreEvent(
        sequence=3,
        execution=CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.COMPLETED,
            executor_id="native",
        ),
    )

    assert mailbox.publish(submitted) is True
    assert mailbox.publish(progress) is True
    assert mailbox.publish(completed) is True
    assert mailbox.dropped_progress == 1
    assert mailbox.closed is False


def test_legacy_lifecycle_event_entry_publishes_through_head():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    lifecycle = start_core_execution_lifecycle(event, spec)
    observed = []
    get_core_execution_head(event).subscribe(observed.append)

    lifecycle.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.SUBMITTED,
            executor_id="native",
        )
    )

    assert [item.sequence for item in observed] == [1]


def test_head_direct_event_projects_to_bound_interaction_journal():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    head = start_core_execution_head(event, spec)

    assert bind_interaction_turn_core_execution_journal(event, head) is True
    head.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.SUBMITTED,
            executor_id="native",
        )
    )

    assert [item.kind for item in get_interaction_turn_core_execution_events(event)] == [
        CoreExecutionEventKind.SUBMITTED
    ]
    assert event.trace.records[-1][1]["sequence"] == 1


def test_head_journal_replays_events_emitted_before_binding():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    head = start_core_execution_head(event, spec)
    head.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.SUBMITTED,
            executor_id="native",
        )
    )
    head.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.WORKING,
            executor_id="native",
        )
    )

    assert bind_interaction_turn_core_execution_journal(event, head) is True
    assert [item.kind for item in get_interaction_turn_core_execution_events(event)] == [
        CoreExecutionEventKind.SUBMITTED,
        CoreExecutionEventKind.WORKING,
    ]
    assert [metadata["sequence"] for _, metadata in event.trace.records] == [1, 2]


def test_head_journal_keeps_terminal_event_when_artifacts_fill_its_bound():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    head = start_core_execution_head(event, spec)

    assert bind_interaction_turn_core_execution_journal(event, head) is True
    head.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.SUBMITTED,
            executor_id="native",
        )
    )
    for index in range(MAX_CORE_EXECUTION_EVENTS_PER_TURN - 1):
        head.record_event(
            CoreExecutionEvent.from_spec(
                spec,
                kind=CoreExecutionEventKind.ARTIFACT_READY,
                executor_id="native",
                metadata={"artifact_id": str(index)},
            )
        )
    head.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.COMPLETED,
            executor_id="native",
        )
    )

    events = get_interaction_turn_core_execution_events(event)
    assert len(events) == MAX_CORE_EXECUTION_EVENTS_PER_TURN
    assert events[0].kind is CoreExecutionEventKind.SUBMITTED
    assert events[-1].kind is CoreExecutionEventKind.COMPLETED
    assert sum(item.kind is CoreExecutionEventKind.ARTIFACT_READY for item in events) == (
        MAX_CORE_EXECUTION_EVENTS_PER_TURN - 2
    )
    assert event.trace.records[-1][1]["kind"] == "completed"


def test_core_execution_head_isolates_subscriber_failure_from_turn_journal(monkeypatch):
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    head = start_core_execution_head(event, spec)
    observed = []
    warnings = []

    monkeypatch.setattr(
        "astrbot.core.execution.logger.warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    def fail_subscriber(_event):
        raise RuntimeError("observer unavailable")

    head.subscribe(fail_subscriber)
    head.subscribe(observed.append)

    submitted = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.SUBMITTED,
        executor_id="native",
    )

    assert submitted is not None
    assert [item.sequence for item in observed] == [1]
    assert [item.kind for item in get_interaction_turn_core_execution_events(event)] == [
        CoreExecutionEventKind.SUBMITTED
    ]
    assert warnings[0][0][0].startswith("Core execution event subscriber failed")


def test_core_execution_binding_rejects_inconsistent_head_and_lifecycle():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    start_core_execution_head(event, spec)
    inconsistent_lifecycle = CoreExecutionLifecycle(
        session=CoreExecutionSession(spec=spec)
    )
    event.set_extra(
        CORE_EXECUTION_HEAD_EXTRA_KEY,
        CoreExecutionHead(inconsistent_lifecycle),
    )

    with pytest.raises(ValueError, match="Head and CoreExecutionLifecycle are inconsistent"):
        bind_core_execution_head(event, spec)


def test_core_execution_binding_rejects_lifecycle_that_lost_its_head():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    start_core_execution_head(event, spec)
    event.set_extra(CORE_EXECUTION_HEAD_EXTRA_KEY, None)

    with pytest.raises(ValueError, match="event publisher but CoreExecutionHead is missing"):
        bind_core_execution_head(event, spec)


def test_artifact_events_deduplicate_by_artifact_id():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    start_core_execution_head(event, spec)
    record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.SUBMITTED,
        executor_id="native",
    )

    first = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.ARTIFACT_READY,
        executor_id="native",
        metadata={"artifact_id": "one"},
    )
    second = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.ARTIFACT_READY,
        executor_id="native",
        metadata={"artifact_id": "two"},
    )
    duplicate = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.ARTIFACT_READY,
        executor_id="native",
        metadata={"artifact_id": "one"},
    )

    assert first is not None
    assert second is not None
    assert duplicate is None
    assert [item.execution.metadata["artifact_id"] for item in get_core_execution_head(
        event
    ).events if item.kind is CoreExecutionEventKind.ARTIFACT_READY] == ["one", "two"]


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


def test_core_execution_outcome_aggregates_terminal_and_artifact_facts():
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-1",
    )
    head = CoreExecutionHead(
        lifecycle=CoreExecutionLifecycle(session=CoreExecutionSession(spec=spec))
    )
    head.start()
    head.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.SUBMITTED,
            executor_id="native",
        )
    )
    head.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.ARTIFACT_READY,
            executor_id="native",
            metadata={"artifact_id": "summary"},
        )
    )
    head.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.FAILED,
            executor_id="native",
            metadata={"error": "provider unavailable"},
        )
    )

    outcome = head.outcome()

    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.terminal_error == "provider unavailable"
    assert outcome.terminal_event.kind is CoreExecutionEventKind.FAILED
    assert [item.metadata["artifact_id"] for item in outcome.artifacts] == ["summary"]


def test_core_execution_head_prepares_ledger_material_from_terminal_outcome():
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-1",
    )
    head = CoreExecutionHead(
        lifecycle=CoreExecutionLifecycle(session=CoreExecutionSession(spec=spec))
    )
    head.start()
    head.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.SUBMITTED,
            executor_id="native",
        )
    )
    head.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.FAILED,
            executor_id="native",
            metadata={"error": "provider unavailable"},
        )
    )

    preparation = head.prepare_ledger_preparation(
        completion_text="raw provider response",
        fallback_status="completed",
        fallback_error="legacy fallback",
    )

    assert preparation.execution_spec is spec
    assert preparation.status == "failed"
    assert preparation.result is None
    assert preparation.error == "provider unavailable"
    assert preparation.outcome is not None


def test_core_execution_head_binds_read_only_deadline_view_and_routes_expiry():
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-1",
    )
    remaining = [3.0]
    view = CoreExecutionDeadlineView(
        deadline_at=10.0,
        _remaining_seconds_reader=lambda: remaining[0],
    )
    head = CoreExecutionHead(
        lifecycle=CoreExecutionLifecycle(session=CoreExecutionSession(spec=spec))
    )
    stopped = []
    head.bind_deadline_view(view)
    head.bind_executor_stop_callback(lambda: stopped.append(True))
    head.start()
    head.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.SUBMITTED,
            executor_id="native",
        )
    )

    assert head.deadline_view is view
    remaining[0] = 0.0
    assert head.deadline_view.expired() is True
    cancelled = head.cancel_for_deadline(executor_id="native", stage="turn_execution")

    assert cancelled.kind is CoreExecutionEventKind.CANCELLED
    assert cancelled.execution.metadata == {
        "reason": "deadline_exceeded",
        "stage": "turn_execution",
    }
    assert stopped == [True]


def test_core_execution_journal_ignores_late_terminal_after_deadline_cancel():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    head = start_core_execution_head(event, spec)
    stop_calls = []
    head.bind_executor_stop_callback(lambda: stop_calls.append(True))

    submitted = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.SUBMITTED,
        executor_id="native",
    )
    cancelled = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.CANCELLED,
        executor_id="native",
        metadata={"reason": "deadline_exceeded", "stage": "turn_execution"},
    )
    late_completed = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.COMPLETED,
        executor_id="native",
    )
    late_failed = record_interaction_turn_core_execution_event(
        event,
        kind=CoreExecutionEventKind.FAILED,
        executor_id="native",
        metadata={"error": "late runner failure"},
    )

    assert submitted is not None
    assert cancelled is not None
    assert late_completed is None
    assert late_failed is None
    assert stop_calls == [True]
    assert [item.kind for item in head.events] == [
        CoreExecutionEventKind.SUBMITTED,
        CoreExecutionEventKind.CANCELLED,
    ]
    outcome = head.outcome()
    assert outcome is not None
    assert outcome.status == "cancelled"


def test_core_execution_lifecycle_prepares_fallback_ledger_material_without_terminal_event():
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-1",
    )
    lifecycle = CoreExecutionLifecycle(session=CoreExecutionSession(spec=spec))

    preparation = lifecycle.prepare_ledger_preparation(
        completion_text="raw provider response",
        fallback_status="failed",
        fallback_error="build failed",
    )

    assert preparation.status == "failed"
    assert preparation.result is None
    assert preparation.error == "build failed"
    assert preparation.outcome is None


def test_core_execution_lifecycle_bounds_terminal_failure_evidence():
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
            metadata={"error": "x" * 2001},
        )
    )

    assert lifecycle.terminal_error() == "x" * 2000


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
    states = []
    lifecycle.bind_executor_stop_callback(lambda: states.append(lifecycle.session.status))

    cancelled = lifecycle.cancel(executor_id="native")

    assert cancelled.kind is CoreExecutionEventKind.CANCELLED
    assert states == [CoreExecutionSessionStatus.CANCELLED]
    assert lifecycle.cancel(executor_id="native") is cancelled
    assert states == [CoreExecutionSessionStatus.CANCELLED]


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


def test_head_deadline_cancellation_records_stop_callback_failure_at_interaction_boundary():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    head = start_core_execution_head(event, spec)
    assert bind_interaction_turn_core_execution_journal(event, head) is True

    def fail_stop() -> None:
        raise RuntimeError("executor unavailable")

    head.bind_executor_stop_callback(fail_stop)
    cancelled = head.cancel_for_deadline(
        executor_id="native",
        stage="turn_execution",
    )
    record_interaction_turn_core_execution_stop_callback_failure(
        event,
        cancelled.execution,
        error=head.executor_stop_error,
    )

    assert [name for name, _ in event.trace.records] == [
        "core_execution_event",
        "core_execution_stop_callback_failed",
    ]
    assert event.trace.records[-1][1]["error"] == "RuntimeError: executor unavailable"


def test_core_execution_ledger_settlement_trace_keeps_terminal_identity():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    head = start_core_execution_head(event, spec)
    head.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.SUBMITTED,
            executor_id="native",
        )
    )
    head.record_event(
        CoreExecutionEvent.from_spec(
            spec,
            kind=CoreExecutionEventKind.COMPLETED,
            executor_id="native",
        )
    )
    preparation = head.prepare_ledger_preparation(completion_text="done")

    record_interaction_turn_core_execution_ledger_settlement(
        event,
        preparation,
        executor_id="native",
        inserted=True,
    )

    name, fields = event.trace.records[-1]
    assert name == "core_execution_ledger_settled"
    assert fields == {
        "execution_id": spec.execution_id,
        "core_task_id": spec.core_task_id,
        "turn_id": spec.turn_id,
        "executor_id": "native",
        "status": "completed",
        "inserted": True,
        "deduplicated": False,
        "terminal_kind": "completed",
        "used_fallback": False,
    }


def test_core_execution_ledger_failure_trace_keeps_execution_identity():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)

    record_interaction_turn_core_execution_ledger_persist_failure(
        event,
        executor_id="native",
        error=RuntimeError("database unavailable"),
    )

    name, fields = event.trace.records[-1]
    assert name == "core_execution_ledger_persist_failed"
    assert fields == {
        "executor_id": "native",
        "error_type": "RuntimeError",
        "error": "database unavailable",
        "execution_id": spec.execution_id,
        "core_task_id": spec.core_task_id,
        "turn_id": spec.turn_id,
    }


def test_core_execution_ledger_settlement_trace_marks_deduplicated_fallback():
    event = _interaction_event()
    spec = event.get_extra(CORE_EXECUTION_SPEC_EXTRA_KEY)
    preparation = CoreExecutionLedgerPreparation.from_fallback(
        execution_spec=spec,
        completion_text="done",
    )

    record_interaction_turn_core_execution_ledger_settlement(
        event,
        preparation,
        executor_id="native",
        inserted=False,
    )

    name, fields = event.trace.records[-1]
    assert name == "core_execution_ledger_settled"
    assert fields["inserted"] is False
    assert fields["deduplicated"] is True
    assert fields["terminal_kind"] is None
    assert fields["used_fallback"] is True


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
