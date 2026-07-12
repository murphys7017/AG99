import asyncio
from unittest.mock import MagicMock

import pytest

from astrbot.core.interaction.lifecycle import dispatch_interaction_lifecycle
from astrbot.core.interaction.turn_state import (
    InteractionLifecycleStage,
    InteractionTurnStatus,
    append_interaction_turn_visible_output,
    ensure_interaction_turn_state,
    mark_interaction_turn_cancelled,
    mark_interaction_turn_completed,
    mark_interaction_turn_failed,
)
from astrbot.core.message.components import Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.star.context import Context


class ConcreteMessageEvent(AstrMessageEvent):
    async def send(self, message):
        await super().send(message)


class LifecycleObserver:
    plugin_id = "lifecycle.test"
    priority = 10

    def __init__(self) -> None:
        self.views = []

    async def on_interaction_lifecycle(self, event, plugin_context, view):
        del event, plugin_context
        self.views.append(view)


class FailingLifecycleObserver:
    plugin_id = "lifecycle.failing"

    async def on_interaction_lifecycle(self, event, plugin_context, view):
        del event, plugin_context, view
        raise RuntimeError("observer unavailable")


class SlowLifecycleObserver:
    plugin_id = "lifecycle.slow"

    async def on_interaction_lifecycle(self, event, plugin_context, view):
        del event, plugin_context, view
        await asyncio.sleep(1)


@pytest.fixture
def interaction_event():
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = "bot"
    message.session_id = "session"
    message.message_id = "input-1"
    message.sender = MessageMember(user_id="user", nickname="User")
    message.message = [Plain("hello")]
    message.message_str = "hello"
    event = ConcreteMessageEvent(
        message_str="hello",
        message_obj=message,
        platform_meta=PlatformMetadata(
            name="test",
            description="test",
            id="test",
        ),
        session_id="session",
    )
    ensure_interaction_turn_state(event, turn_id="turn-1")
    return event


@pytest.mark.asyncio
async def test_lifecycle_dispatches_read_only_ordered_views_and_isolates_failures(
    interaction_event,
):
    observer = LifecycleObserver()
    plugin_context = MagicMock()
    plugin_context.list_interaction_lifecycle_observers.return_value = [
        observer,
        FailingLifecycleObserver(),
    ]

    await dispatch_interaction_lifecycle(
        interaction_event,
        plugin_context,
        InteractionLifecycleStage.RECEIVED,
    )
    await dispatch_interaction_lifecycle(
        interaction_event,
        plugin_context,
        InteractionLifecycleStage.ROUTING,
        metadata={"source": "router"},
    )

    assert [view.stage for view in observer.views] == ["received", "routing"]
    assert observer.views[1].previous_stage == "received"
    assert observer.views[1].metadata["source"] == "router"
    with pytest.raises(TypeError):
        observer.views[1].metadata["source"] = "changed"
    assert interaction_event.get_extra("_interaction_lifecycle_stage") == "routing"
    failures = interaction_event.get_extra("_interaction_lifecycle_observer_failures")
    assert [failure["plugin_id"] for failure in failures] == [
        "lifecycle.failing",
        "lifecycle.failing",
    ]


@pytest.mark.asyncio
async def test_lifecycle_observer_timeout_does_not_block_turn(
    interaction_event,
    monkeypatch,
):
    monkeypatch.setattr(
        "astrbot.core.interaction.lifecycle.LIFECYCLE_OBSERVER_TIMEOUT_SECONDS",
        0.001,
    )
    plugin_context = MagicMock()
    plugin_context.list_interaction_lifecycle_observers.return_value = [
        SlowLifecycleObserver()
    ]

    await dispatch_interaction_lifecycle(
        interaction_event,
        plugin_context,
        InteractionLifecycleStage.RECEIVED,
    )

    failures = interaction_event.get_extra("_interaction_lifecycle_observer_failures")
    assert failures == [
        {
            "plugin_id": "lifecycle.slow",
            "stage": "received",
            "reason": "TimeoutError",
        }
    ]


def test_turn_completion_status_is_explicit(interaction_event):
    state = ensure_interaction_turn_state(interaction_event)
    assert state.completion_state.status is InteractionTurnStatus.ACTIVE

    mark_interaction_turn_failed(interaction_event)
    assert state.completion_state.status is InteractionTurnStatus.FAILED

    mark_interaction_turn_cancelled(interaction_event)
    assert state.completion_state.status is InteractionTurnStatus.CANCELLED

    mark_interaction_turn_completed(interaction_event)
    assert state.completion_state.status is InteractionTurnStatus.COMPLETED


def test_visible_output_snapshot_keeps_message_identity(interaction_event):
    append_interaction_turn_visible_output(
        interaction_event,
        message_kind="core_reply",
        text="hello",
        delivered_message_ids=["platform-message-1"],
    )

    state = ensure_interaction_turn_state(interaction_event)
    assert state.visible_outputs == [
        {
            "turn_id": "turn-1",
            "message_id": "platform-message-1",
            "delivered_message_ids": ["platform-message-1"],
            "kind": "core_reply",
            "text": "hello",
            "memory_relevant": True,
        }
    ]


def test_context_registers_and_removes_lifecycle_observers():
    context = Context.__new__(Context)
    context._interaction_lifecycle_observers = []
    context._interaction_lifecycle_observer_seq = 0
    observer = LifecycleObserver()

    context.register_interaction_lifecycle_observer(observer)

    assert context.list_interaction_lifecycle_observers() == [observer]
    assert (
        context.remove_interaction_lifecycle_observers_by_module_prefix(
            __name__,
        )
        == 1
    )
    assert context.list_interaction_lifecycle_observers() == []
