from __future__ import annotations

import asyncio
import uuid
import weakref
from dataclasses import dataclass
from enum import Enum
from typing import Any

from astrbot import logger
from astrbot.core.persona_error_reply import resolve_event_conversation_persona_id
from astrbot.core.platform.message_type import MessageType
from astrbot.core.provider.entities import ProviderRequest

from .turn_state import (
    InteractionTurnState,
    get_interaction_turn_state,
    set_interaction_turn_persona_id,
)


class PendingTurnState(str, Enum):
    RESERVED = "reserved"
    BOUND = "bound"
    QUEUED = "queued"
    ACTIVE = "active"
    SETTLED = "settled"


@dataclass(frozen=True, slots=True)
class PersonalRuntimeKey:
    config_id: str
    persona_id: str
    audience_key: str
    privacy_scope: str


@dataclass(slots=True)
class PendingTurnReservation:
    turn_id: str
    config_id: str
    audience_key: str
    privacy_scope: str
    turn_state: InteractionTurnState | None
    state: PendingTurnState = PendingTurnState.RESERVED
    runtime_key: PersonalRuntimeKey | None = None

    def transition(self, state: PendingTurnState) -> None:
        if self.state is PendingTurnState.SETTLED:
            return
        self.state = state
        if self.turn_state is not None:
            self.turn_state.runtime_reservation_state = state.value


@dataclass(slots=True)
class _FollowUpCapture:
    ticket: Any
    order_seq: int
    monitor_task: asyncio.Task[None]


class _FollowUpCoordinator:
    def __init__(self) -> None:
        self.active_runner: Any | None = None
        self.condition = asyncio.Condition()
        self.statuses: dict[int, str] = {}
        self.next_order = 0
        self.next_turn = 0

    def register(self, runner: Any) -> None:
        self.active_runner = runner

    def unregister(self, runner: Any) -> None:
        if self.active_runner is runner:
            self.active_runner = None

    def try_capture(self, event: Any) -> _FollowUpCapture | None:
        sender_id = event.get_sender_id()
        if not sender_id or self.active_runner is None:
            return None
        runner_event = getattr(
            getattr(self.active_runner.run_context, "context", None),
            "event",
            None,
        )
        if runner_event is None or runner_event.get_sender_id() != sender_id:
            return None
        if runner_event.get_extra("agent_stop_requested"):
            return None

        message_text = (event.get_message_str() or "").strip()
        if not message_text:
            message_text = event.get_message_outline().strip()
        ticket = self.active_runner.follow_up(message_text=message_text)
        if ticket is None:
            return None

        order_seq = self.next_order
        self.next_order += 1
        self.statuses[order_seq] = "pending"
        monitor_task = asyncio.create_task(
            self._monitor_ticket(ticket, order_seq),
            name=f"personal_runtime_follow_up_{order_seq}",
        )
        return _FollowUpCapture(
            ticket=ticket,
            order_seq=order_seq,
            monitor_task=monitor_task,
        )

    async def prepare(self, capture: _FollowUpCapture) -> tuple[bool, bool]:
        await capture.ticket.resolved.wait()
        if capture.ticket.consumed:
            await self._mark_consumed(capture.order_seq)
            return True, False
        await self._activate_in_order(capture.order_seq)
        return False, True

    async def finalize(
        self,
        capture: _FollowUpCapture,
        *,
        activated: bool,
        consumed_marked: bool,
    ) -> None:
        if not capture.monitor_task.done():
            capture.monitor_task.cancel()
            try:
                await capture.monitor_task
            except asyncio.CancelledError:
                pass
        if activated:
            await self._finish(capture.order_seq)
        elif not consumed_marked:
            await self._mark_consumed(capture.order_seq)

    def is_idle(self) -> bool:
        return self.active_runner is None and not self.statuses

    async def _monitor_ticket(self, ticket: Any, order_seq: int) -> None:
        await ticket.resolved.wait()
        if ticket.consumed:
            await self._mark_consumed(order_seq)

    def _advance(self) -> None:
        while self.statuses.get(self.next_turn) in {"consumed", "finished"}:
            self.statuses.pop(self.next_turn, None)
            self.next_turn += 1

    async def _mark_consumed(self, order_seq: int) -> None:
        async with self.condition:
            if order_seq in self.statuses and self.statuses[order_seq] != "finished":
                self.statuses[order_seq] = "consumed"
            self._advance()
            self.condition.notify_all()

    async def _activate_in_order(self, order_seq: int) -> None:
        async with self.condition:
            if order_seq in self.statuses:
                self.statuses[order_seq] = "active"
            while self.next_turn != order_seq:
                await self.condition.wait()

    async def _finish(self, order_seq: int) -> None:
        async with self.condition:
            if order_seq in self.statuses:
                self.statuses[order_seq] = "finished"
            self._advance()
            self.condition.notify_all()


@dataclass(slots=True)
class TurnAdmission:
    consumed_as_follow_up: bool
    lease: PersonalTurnLease | None = None


class PersonalTurnLease:
    def __init__(
        self,
        runtime: PersonalSessionRuntime,
        reservation: PendingTurnReservation,
        follow_up_capture: _FollowUpCapture | None,
        follow_up_activated: bool,
    ) -> None:
        self.runtime = runtime
        self.reservation = reservation
        self.follow_up_capture = follow_up_capture
        self.follow_up_activated = follow_up_activated
        self.released = False

    async def release(self) -> None:
        if self.released:
            return
        self.released = True
        try:
            if self.follow_up_capture is not None:
                await self.runtime.follow_ups.finalize(
                    self.follow_up_capture,
                    activated=self.follow_up_activated,
                    consumed_marked=False,
                )
        finally:
            self.runtime.active_turn_id = None
            self.reservation.transition(PendingTurnState.SETTLED)
            self.runtime.turn_lock.release()


class PersonalSessionRuntime:
    def __init__(self, key: PersonalRuntimeKey) -> None:
        self.key = key
        self.turn_lock = asyncio.Lock()
        self.active_turn_id: str | None = None
        self.bound_turn_count = 0
        self.follow_ups = _FollowUpCoordinator()

    async def admit(
        self,
        event: Any,
        reservation: PendingTurnReservation,
        *,
        allow_follow_up: bool,
    ) -> TurnAdmission:
        capture = self.follow_ups.try_capture(event) if allow_follow_up else None
        follow_up_activated = False
        try:
            if capture is not None:
                consumed, follow_up_activated = await self.follow_ups.prepare(capture)
                if consumed:
                    await self.follow_ups.finalize(
                        capture,
                        activated=False,
                        consumed_marked=True,
                    )
                    reservation.transition(PendingTurnState.SETTLED)
                    return TurnAdmission(consumed_as_follow_up=True)

            reservation.transition(PendingTurnState.QUEUED)
            await self.turn_lock.acquire()
        except BaseException:
            if capture is not None:
                await self.follow_ups.finalize(
                    capture,
                    activated=follow_up_activated,
                    consumed_marked=False,
                )
            raise
        reservation.transition(PendingTurnState.ACTIVE)
        self.active_turn_id = reservation.turn_id
        return TurnAdmission(
            consumed_as_follow_up=False,
            lease=PersonalTurnLease(
                self,
                reservation,
                capture,
                follow_up_activated,
            ),
        )

    def is_idle(self) -> bool:
        return (
            not self.turn_lock.locked()
            and self.active_turn_id is None
            and self.bound_turn_count == 0
            and self.follow_ups.is_idle()
        )


class PersonalRuntimeManager:
    def __init__(self) -> None:
        self._sessions: dict[PersonalRuntimeKey, PersonalSessionRuntime] = {}
        self._event_sessions: weakref.WeakKeyDictionary[Any, PersonalSessionRuntime] = (
            weakref.WeakKeyDictionary()
        )

    def reserve(self, event: Any, config_id: str) -> PendingTurnReservation:
        turn_state = get_interaction_turn_state(event)
        turn_id = (
            turn_state.turn_id
            if turn_state is not None
            else str(event.get_extra("_turn_id", "") or "") or uuid.uuid4().hex
        )
        audience_key = str(event.session)
        privacy_scope = self._privacy_scope(event.get_message_type())
        reservation = PendingTurnReservation(
            turn_id=turn_id,
            config_id=config_id or "default",
            audience_key=audience_key,
            privacy_scope=privacy_scope,
            turn_state=turn_state,
        )
        if turn_state is not None:
            turn_state.runtime_config_id = reservation.config_id
            turn_state.runtime_audience_key = audience_key
            turn_state.runtime_privacy_scope = privacy_scope
            turn_state.runtime_reservation_state = PendingTurnState.RESERVED.value
        return reservation

    async def bind(
        self,
        reservation: PendingTurnReservation,
        event: Any,
        plugin_context: Any,
        provider_settings: dict,
    ) -> PersonalSessionRuntime:
        persona_id = await self._resolve_persona_id(
            reservation,
            event,
            plugin_context,
            provider_settings,
        )
        key = PersonalRuntimeKey(
            config_id=reservation.config_id,
            persona_id=persona_id,
            audience_key=reservation.audience_key,
            privacy_scope=reservation.privacy_scope,
        )
        runtime = self._sessions.setdefault(key, PersonalSessionRuntime(key))
        runtime.bound_turn_count += 1
        reservation.runtime_key = key
        reservation.transition(PendingTurnState.BOUND)
        self._event_sessions[event] = runtime
        if reservation.turn_state is not None:
            reservation.turn_state.personal_runtime_key = key
            set_interaction_turn_persona_id(event, persona_id)
        return runtime

    async def admit(
        self,
        reservation: PendingTurnReservation,
        event: Any,
        *,
        allow_follow_up: bool,
    ) -> TurnAdmission:
        runtime = self._event_sessions.get(event)
        if runtime is None:
            raise RuntimeError("Pending turn must be bound before admission.")
        return await runtime.admit(
            event,
            reservation,
            allow_follow_up=allow_follow_up,
        )

    def register_active_runner(self, event: Any, runner: Any) -> bool:
        runtime = self._event_sessions.get(event)
        if runtime is None:
            logger.warning(
                "Cannot register active runner without Personal Runtime binding: session_id=%s",
                event.unified_msg_origin,
            )
            return False
        runtime.follow_ups.register(runner)
        return True

    def unregister_active_runner(self, event: Any, runner: Any) -> None:
        runtime = self._event_sessions.get(event)
        if runtime is not None:
            runtime.follow_ups.unregister(runner)

    def settle(self, reservation: PendingTurnReservation, event: Any) -> None:
        reservation.transition(PendingTurnState.SETTLED)
        runtime = self._event_sessions.pop(event, None)
        if runtime is None:
            return
        runtime.bound_turn_count = max(0, runtime.bound_turn_count - 1)
        if runtime.is_idle():
            self._sessions.pop(runtime.key, None)

    async def _resolve_persona_id(
        self,
        reservation: PendingTurnReservation,
        event: Any,
        plugin_context: Any,
        provider_settings: dict,
    ) -> str:
        try:
            request = event.get_extra("provider_request")
            conversation_persona_id = None
            if (
                isinstance(request, ProviderRequest)
                and request.conversation is not None
            ):
                conversation_persona_id = request.conversation.persona_id
            if conversation_persona_id is None:
                conversation_persona_id = await resolve_event_conversation_persona_id(
                    event,
                    plugin_context.conversation_manager,
                )
            (
                persona_id,
                _,
                _,
                _,
            ) = await plugin_context.persona_manager.resolve_selected_persona(
                umo=event.unified_msg_origin,
                conversation_persona_id=conversation_persona_id,
                platform_name=event.get_platform_name(),
                provider_settings=provider_settings,
            )
            return str(persona_id or "default")
        except Exception as exc:
            logger.warning(
                "Personal Runtime persona resolution failed; isolating turn: session_id=%s error=%s",
                event.unified_msg_origin,
                exc,
            )
            return f"unresolved:{reservation.turn_id}"

    @staticmethod
    def _privacy_scope(message_type: MessageType) -> str:
        if message_type is MessageType.GROUP_MESSAGE:
            return "group"
        if message_type is MessageType.FRIEND_MESSAGE:
            return "private"
        return "other"


__all__ = [
    "PendingTurnReservation",
    "PendingTurnState",
    "PersonalRuntimeKey",
    "PersonalRuntimeManager",
    "PersonalSessionRuntime",
    "PersonalTurnLease",
    "TurnAdmission",
]
