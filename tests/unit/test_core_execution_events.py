import pytest

from astrbot.core.execution import (
    CORE_EXECUTION_SPEC_EXTRA_KEY,
    CoreExecutionEventKind,
    CoreExecutionSpec,
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
