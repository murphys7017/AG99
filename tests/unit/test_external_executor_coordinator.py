import pytest

from astrbot.core.deadline import TurnDeadlineBudget
from astrbot.core.execution import (
    CoreExecutionEventKind,
    CoreExecutionSpec,
    get_core_execution_head,
)
from astrbot.core.executors.coordinator import (
    _runtime_config_id,
    execute_external_core_turn,
)
from astrbot.core.interaction.turn_state import (
    InteractionTurnState,
    get_interaction_turn_state,
    set_interaction_turn_core_execution_spec,
)
from astrbot.core.prompt.context_types import ContextPack


class _Event:
    def __init__(self, *, turn_state=None, extras=None):
        self._interaction_turn_state = turn_state
        self._extras = extras or {}

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)


def test_runtime_config_id_prefers_typed_turn_state():
    event = _Event(
        turn_state=InteractionTurnState(
            turn_id="turn-1",
            runtime_config_id="bot-typed",
        ),
        extras={"_astrbot_config_id": "bot-extra"},
    )
    assert _runtime_config_id(event) == "bot-typed"


def test_runtime_config_id_uses_explicit_legacy_extra():
    event = _Event(extras={"_astrbot_config_id": "bot-extra"})
    assert _runtime_config_id(event) == "bot-extra"


def test_runtime_config_id_rejects_implicit_default():
    with pytest.raises(RuntimeError, match="explicit runtime config identity"):
        _runtime_config_id(_Event())


@pytest.mark.asyncio
async def test_external_preparation_failure_records_executor_terminal(
    monkeypatch,
):
    event = _Event(
        turn_state=InteractionTurnState(turn_id="turn-1"),
        extras={"_interaction_enabled": True},
    )
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=ContextPack(),
        turn_id="turn-1",
        task_spec={},
    )
    set_interaction_turn_core_execution_spec(event, spec)

    async def prepare(**_kwargs):
        return type(
            "Prepared",
            (),
            {"execution_spec": spec, "deadline_view": None},
        )()

    monkeypatch.setattr(
        "astrbot.core.astr_main_agent.prepare_external_core_execution",
        prepare,
    )

    with pytest.raises(RuntimeError, match="explicit runtime config identity"):
        await execute_external_core_turn(
            context=object(),
            event=event,
            runtime_config={"core_execution": {"executor_id": "codex_cli"}},
            config=object(),
            session_id="test:FriendMessage:user",
            prompt_config=object(),
            deadline=TurnDeadlineBudget.start(30),
            output_controller=object(),
        )

    head = get_core_execution_head(event)
    assert head is not None
    assert head.terminal_event is not None
    assert head.terminal_event.kind is CoreExecutionEventKind.FAILED
    assert head.terminal_event.execution.executor_id == "codex_cli"
    assert head.terminal_event.execution.metadata["phase"] == (
        "external_executor_preparation_or_run"
    )
    state = get_interaction_turn_state(event)
    assert state is not None
    assert state.core_execution_events[-1].kind is CoreExecutionEventKind.FAILED
