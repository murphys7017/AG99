from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot.core.interaction.personal_runtime import (
    PersonalRuntimeManager,
    TurnAdmission,
)
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.third_party import (
    ThirdPartyAgentSubStage,
)
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.platform.message_type import MessageType
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.star_handler import EventType


class _RuntimeEvent:
    def __init__(
        self,
        *,
        session: str = "test:FriendMessage:session",
        sender_id: str = "user",
    ) -> None:
        self.session = session
        self.unified_msg_origin = session
        self._sender_id = sender_id
        self._extras: dict[str, object] = {}

    def get_extra(self, key: str, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key: str, value: object) -> None:
        self._extras[key] = value

    def get_message_type(self) -> MessageType:
        return MessageType.FRIEND_MESSAGE

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_message_str(self) -> str:
        return "follow up"

    def get_message_outline(self) -> str:
        return "follow up"

    def get_platform_name(self) -> str:
        return "test"


class _FollowUpTicket:
    def __init__(self, *, consumed: bool) -> None:
        self.consumed = consumed
        self.resolved = asyncio.Event()


class _Runner:
    def __init__(self, event: _RuntimeEvent, ticket: _FollowUpTicket) -> None:
        self.run_context = SimpleNamespace(
            context=SimpleNamespace(event=event),
        )
        self.ticket = ticket
        self.follow_up_calls: list[str] = []

    def follow_up(self, *, message_text: str):
        self.follow_up_calls.append(message_text)
        return self.ticket


def _runtime_context() -> MagicMock:
    context = MagicMock()
    context.conversation_manager = MagicMock()
    context.persona_manager = MagicMock()
    return context


async def _bind(
    manager: PersonalRuntimeManager,
    event: _RuntimeEvent,
):
    reservation = manager.reserve(event, "default")
    runtime = await manager.bind(reservation, event, _runtime_context(), {})
    return reservation, runtime


@pytest.mark.asyncio
async def test_same_runtime_serializes_turns_without_early_runtime_cleanup():
    manager = PersonalRuntimeManager()
    manager._resolve_persona_id = AsyncMock(return_value="alice")
    first_event = _RuntimeEvent()
    second_event = _RuntimeEvent()
    first_reservation, runtime = await _bind(manager, first_event)
    first_admission = await manager.admit(
        first_reservation,
        first_event,
        allow_follow_up=False,
    )
    second_reservation, second_runtime = await _bind(manager, second_event)
    assert second_runtime is runtime

    second_task = asyncio.create_task(
        manager.admit(
            second_reservation,
            second_event,
            allow_follow_up=False,
        )
    )
    await asyncio.sleep(0)
    assert not second_task.done()

    assert first_admission.lease is not None
    await first_admission.lease.release()
    manager.settle(first_reservation, first_event)
    assert manager._sessions[runtime.key] is runtime

    second_admission = await asyncio.wait_for(second_task, timeout=1)
    assert second_admission.lease is not None
    await second_admission.lease.release()
    manager.settle(second_reservation, second_event)
    assert runtime.key not in manager._sessions


@pytest.mark.asyncio
async def test_active_runner_consumes_follow_up_without_starting_second_turn():
    manager = PersonalRuntimeManager()
    manager._resolve_persona_id = AsyncMock(return_value="alice")
    first_event = _RuntimeEvent()
    second_event = _RuntimeEvent()
    first_reservation, runtime = await _bind(manager, first_event)
    first_admission = await manager.admit(
        first_reservation,
        first_event,
        allow_follow_up=True,
    )
    ticket = _FollowUpTicket(consumed=True)
    ticket.resolved.set()
    runner = _Runner(first_event, ticket)
    assert manager.register_active_runner(first_event, runner)

    second_reservation, _ = await _bind(manager, second_event)
    second_admission = await manager.admit(
        second_reservation,
        second_event,
        allow_follow_up=True,
    )

    assert second_admission.consumed_as_follow_up
    assert second_admission.lease is None
    assert runner.follow_up_calls == ["follow up"]

    manager.settle(second_reservation, second_event)
    manager.unregister_active_runner(first_event, runner)
    assert first_admission.lease is not None
    await first_admission.lease.release()
    manager.settle(first_reservation, first_event)
    assert runtime.key not in manager._sessions


@pytest.mark.asyncio
async def test_unconsumed_follow_up_waits_for_current_turn_then_becomes_next_turn():
    manager = PersonalRuntimeManager()
    manager._resolve_persona_id = AsyncMock(return_value="alice")
    first_event = _RuntimeEvent()
    second_event = _RuntimeEvent()
    first_reservation, _ = await _bind(manager, first_event)
    first_admission = await manager.admit(
        first_reservation,
        first_event,
        allow_follow_up=True,
    )
    ticket = _FollowUpTicket(consumed=False)
    runner = _Runner(first_event, ticket)
    assert manager.register_active_runner(first_event, runner)
    second_reservation, _ = await _bind(manager, second_event)

    second_task = asyncio.create_task(
        manager.admit(
            second_reservation,
            second_event,
            allow_follow_up=True,
        )
    )
    await asyncio.sleep(0)
    assert not second_task.done()
    ticket.resolved.set()
    await asyncio.sleep(0)
    assert not second_task.done()

    manager.unregister_active_runner(first_event, runner)
    assert first_admission.lease is not None
    await first_admission.lease.release()
    manager.settle(first_reservation, first_event)
    second_admission = await asyncio.wait_for(second_task, timeout=1)
    assert not second_admission.consumed_as_follow_up
    assert second_admission.lease is not None
    await second_admission.lease.release()
    manager.settle(second_reservation, second_event)


@pytest.mark.asyncio
async def test_cancelled_follow_up_admission_releases_order_slot():
    manager = PersonalRuntimeManager()
    manager._resolve_persona_id = AsyncMock(return_value="alice")
    first_event = _RuntimeEvent()
    cancelled_event = _RuntimeEvent()
    next_event = _RuntimeEvent()
    first_reservation, runtime = await _bind(manager, first_event)
    first_admission = await manager.admit(
        first_reservation,
        first_event,
        allow_follow_up=True,
    )
    ticket = _FollowUpTicket(consumed=False)
    runner = _Runner(first_event, ticket)
    assert manager.register_active_runner(first_event, runner)
    cancelled_reservation, _ = await _bind(manager, cancelled_event)
    cancelled_task = asyncio.create_task(
        manager.admit(
            cancelled_reservation,
            cancelled_event,
            allow_follow_up=True,
        )
    )
    await asyncio.sleep(0)
    ticket.resolved.set()
    await asyncio.sleep(0)
    cancelled_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_task
    manager.settle(cancelled_reservation, cancelled_event)
    assert runtime.follow_ups.statuses == {}

    manager.unregister_active_runner(first_event, runner)
    next_reservation, _ = await _bind(manager, next_event)
    next_task = asyncio.create_task(
        manager.admit(
            next_reservation,
            next_event,
            allow_follow_up=False,
        )
    )
    assert first_admission.lease is not None
    await first_admission.lease.release()
    manager.settle(first_reservation, first_event)
    next_admission = await asyncio.wait_for(next_task, timeout=1)
    assert next_admission.lease is not None
    await next_admission.lease.release()
    manager.settle(next_reservation, next_event)


@pytest.mark.asyncio
async def test_different_personas_use_independent_runtimes():
    manager = PersonalRuntimeManager()
    manager._resolve_persona_id = AsyncMock(side_effect=["alice", "bob"])
    first_event = _RuntimeEvent()
    second_event = _RuntimeEvent()
    first_reservation, first_runtime = await _bind(manager, first_event)
    second_reservation, second_runtime = await _bind(manager, second_event)

    first_admission = await manager.admit(
        first_reservation,
        first_event,
        allow_follow_up=False,
    )
    second_admission = await asyncio.wait_for(
        manager.admit(
            second_reservation,
            second_event,
            allow_follow_up=False,
        ),
        timeout=1,
    )

    assert first_runtime is not second_runtime
    assert first_admission.lease is not None
    assert second_admission.lease is not None
    await first_admission.lease.release()
    await second_admission.lease.release()
    manager.settle(first_reservation, first_event)
    manager.settle(second_reservation, second_event)


@pytest.mark.asyncio
async def test_process_stage_stops_before_middleware_when_follow_up_is_consumed(
    mock_event,
):
    stage = ProcessStage()
    manager = MagicMock()
    reservation = MagicMock()
    manager.reserve.return_value = reservation
    manager.bind = AsyncMock()
    manager.admit = AsyncMock(return_value=TurnAdmission(consumed_as_follow_up=True))
    stage.personal_runtime_manager = manager
    stage.ctx = SimpleNamespace(
        interaction_middleware=None,
        astrbot_config_id="default",
        astrbot_config={"provider_settings": {"enable": True}},
    )
    stage.config = stage.ctx.astrbot_config
    stage.plugin_manager = SimpleNamespace(context=MagicMock())
    stage._run_interaction_before_core_agent = AsyncMock()
    stage.agent_sub_stage = MagicMock()
    mock_event.get_extra.return_value = None
    mock_event.is_stopped.return_value = False
    mock_event.get_result.return_value = None
    mock_event._has_send_oper = False
    mock_event.is_at_or_wake_command = True
    mock_event.call_llm = False

    yielded = [item async for item in stage.process(mock_event)]

    assert yielded == []
    stage._run_interaction_before_core_agent.assert_not_awaited()
    stage.agent_sub_stage.process.assert_not_called()
    manager.settle.assert_called_once_with(reservation, mock_event)


@pytest.mark.asyncio
async def test_third_party_stage_preserves_explicit_plugin_request_for_hook():
    stage = object.__new__(ThirdPartyAgentSubStage)
    stage.prov_id = "third-party-provider"
    stage.runner_type = "dify"
    stage.conf = {"provider_settings": {}}
    stage._resolve_persona_custom_error_message = AsyncMock(return_value=None)
    request = ProviderRequest(
        prompt=None,
        session_id="plugin-session",
        contexts=[{"role": "user", "content": "plugin context"}],
        system_prompt="plugin system prompt",
        model="plugin-model",
    )
    event = MagicMock()
    event.message_str = "does-not-match-prefix"
    event.unified_msg_origin = "test:FriendMessage:event-session"
    event.get_extra.side_effect = lambda key: (
        request if key == "provider_request" else None
    )
    hook = AsyncMock(return_value=True)

    with (
        patch(
            "astrbot.core.pipeline.process_stage.method.agent_sub_stages.third_party.astrbot_config",
            {"provider": [{"id": "third-party-provider"}]},
        ),
        patch(
            "astrbot.core.pipeline.process_stage.method.agent_sub_stages.third_party.call_event_hook",
            new=hook,
        ),
    ):
        yielded = [item async for item in stage.process(event, "required-prefix")]

    assert yielded == []
    assert request.session_id == "plugin-session"
    hook.assert_awaited_once_with(event, EventType.OnLLMRequestEvent, request)
