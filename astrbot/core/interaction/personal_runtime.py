from __future__ import annotations

import asyncio
import contextvars
import time
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any

from astrbot import logger
from astrbot.core.persona_error_reply import resolve_event_conversation_persona_id
from astrbot.core.provider.entities import ProviderRequest

from .observation import RuntimeObservation, RuntimeObservationTarget
from .runtime_event import RuntimeObservationEvent
from .turn_context import (
    PersonalTurnContext,
    PlatformTurnContextFactory,
)
from .turn_state import (
    set_interaction_turn_persona_id,
)

_ACTIVE_PERSONAL_TURN: contextvars.ContextVar[PersonalTurnContext | None] = (
    contextvars.ContextVar("active_personal_turn", default=None)
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
    turn: PersonalTurnContext
    state: PendingTurnState = PendingTurnState.RESERVED
    runtime_key: PersonalRuntimeKey | None = None

    def transition(self, state: PendingTurnState) -> None:
        if self.state is PendingTurnState.SETTLED:
            return
        self.state = state
        self.turn.state.runtime_reservation_state = state.value


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
    turn: PersonalTurnContext
    consumed_as_follow_up: bool
    lease: PersonalTurnLease | None = None


class PlatformEventSubmission:
    """Manager-owned lifecycle boundary for one official platform event."""

    def __init__(
        self,
        manager: PersonalRuntimeManager,
        reservation: PendingTurnReservation,
    ) -> None:
        self._manager = manager
        self._reservation = reservation
        self._admitted = False

    @property
    def turn(self) -> PersonalTurnContext:
        return self._reservation.turn

    def set_provider_request(self, request: ProviderRequest) -> None:
        self._reservation.turn.provider_request = request

    async def admit(self, *, allow_follow_up: bool) -> TurnAdmission:
        if self._admitted:
            raise RuntimeError("Platform event has already been admitted.")
        self._admitted = True
        return await self._manager._bind_and_admit(
            self._reservation,
            allow_follow_up=allow_follow_up,
        )


class RuntimeObservationEventSubmission(PlatformEventSubmission):
    """Manager-owned lifecycle boundary for one runtime observation event."""

    async def admit(self) -> TurnAdmission:
        return await super().admit(allow_follow_up=False)


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
            try:
                await self.reservation.turn.state.execution_scope.close()
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
        reservation: PendingTurnReservation,
        *,
        allow_follow_up: bool,
    ) -> TurnAdmission:
        turn = reservation.turn
        event = turn.event
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
                    return TurnAdmission(turn=turn, consumed_as_follow_up=True)

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
        self.active_turn_id = turn.turn_id
        return TurnAdmission(
            turn=turn,
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

    @asynccontextmanager
    async def submit_platform_event(
        self,
        event: Any,
        config_id: str,
        plugin_context: Any,
        runtime_config: dict,
    ) -> AsyncIterator[PlatformEventSubmission]:
        reservation = self._reserve(
            event,
            config_id,
            runtime_config=runtime_config,
            plugin_context=plugin_context,
        )
        submission = PlatformEventSubmission(
            self,
            reservation,
        )
        try:
            yield submission
        finally:
            self._settle(reservation)

    async def submit_runtime_observation_event(
        self,
        event: RuntimeObservationEvent,
        config_id: str,
        plugin_context: Any,
        runtime_config: dict,
        handler: Callable[[RuntimeObservationEvent, PersonalTurnContext], Awaitable[Any]],
    ) -> Any:
        """Submit an internal observation to the regular per-session runtime."""
        if not isinstance(event, RuntimeObservationEvent):
            raise TypeError("event must be a RuntimeObservationEvent")
        if not event.platform_meta.support_proactive_message:
            raise RuntimeError(
                "Runtime observation target does not support proactive messages"
            )
        reservation = self._reserve(
            event,
            config_id,
            runtime_config=runtime_config,
            plugin_context=plugin_context,
        )
        submission = RuntimeObservationEventSubmission(self, reservation)
        event.set_extra("_personal_runtime_submission_kind", "observation")
        try:
            admission = await submission.admit()
            if admission.consumed_as_follow_up or admission.lease is None:
                raise RuntimeError(
                    "Runtime observation admission did not acquire a lease"
                )
            try:
                with self.activate_turn(admission.turn):
                    return await handler(event, admission.turn)
            finally:
                await admission.lease.release()
        finally:
            self._settle(reservation)

    async def dispatch_proactive_message(
        self,
        *,
        context: Any,
        middleware: Any,
        config_id: str,
        runtime_config: dict,
        session: Any,
        message: Any,
        finalize: bool = True,
    ) -> bool:
        active_turn = _ACTIVE_PERSONAL_TURN.get()
        if (
            active_turn is not None
            and not active_turn.state.execution_scope.closed
            and active_turn.session.unified_msg_origin == str(session)
        ):
            await middleware.handle_active_turn_output(
                active_turn,
                message,
                finalize=finalize,
            )
            return True

        platform = next(
            (
                item
                for item in context.platform_manager.platform_insts
                if item.meta().id == session.platform_id
            ),
            None,
        )
        if platform is None:
            logger.warning("Cannot find proactive output platform: %s", session)
            return False

        metadata = platform.meta()
        observation = RuntimeObservation(
            kind="proactive_output",
            source="plugin.context.send_message",
            occurred_at=time.time(),
            target_session=RuntimeObservationTarget(
                platform_id=session.platform_id,
                platform_name=metadata.name,
                message_type=session.message_type,
                session_id=session.session_id,
                support_proactive_message=metadata.support_proactive_message,
            ),
            payload={"visible_reply_material": message.get_plain_text()},
        )
        event = RuntimeObservationEvent(context=context, observation=observation)

        async def _deliver(runtime_event, turn):
            await middleware.handle_runtime_output(runtime_event, turn, message)
            return True

        return bool(
            await self.submit_runtime_observation_event(
                event,
                config_id,
                context,
                runtime_config,
                _deliver,
            )
        )

    @staticmethod
    @contextmanager
    def activate_turn(turn: PersonalTurnContext):
        token = _ACTIVE_PERSONAL_TURN.set(turn)
        try:
            yield
        finally:
            _ACTIVE_PERSONAL_TURN.reset(token)

    def _reserve(
        self,
        event: Any,
        config_id: str,
        *,
        runtime_config: dict,
        plugin_context: Any,
    ) -> PendingTurnReservation:
        turn = PlatformTurnContextFactory.create(
            event,
            config_id=config_id,
            runtime_config=runtime_config,
            plugin_context=plugin_context,
        )
        reservation = PendingTurnReservation(
            turn=turn,
        )
        turn.state.runtime_config_id = turn.session.config_id
        turn.state.runtime_audience_key = turn.session.unified_msg_origin
        turn.state.runtime_privacy_scope = turn.session.privacy_scope
        turn.state.runtime_reservation_state = PendingTurnState.RESERVED.value
        return reservation

    async def _bind(
        self,
        reservation: PendingTurnReservation,
    ) -> PersonalSessionRuntime:
        turn = reservation.turn
        event = turn.event
        persona_id = await self._resolve_persona_id(
            reservation,
        )
        key = PersonalRuntimeKey(
            config_id=turn.session.config_id,
            persona_id=persona_id,
            audience_key=turn.session.unified_msg_origin,
            privacy_scope=turn.session.privacy_scope,
        )
        runtime = self._sessions.setdefault(key, PersonalSessionRuntime(key))
        runtime.bound_turn_count += 1
        reservation.runtime_key = key
        reservation.transition(PendingTurnState.BOUND)
        self._event_sessions[event] = runtime
        turn.state.personal_runtime_key = key
        set_interaction_turn_persona_id(event, persona_id)
        return runtime

    async def _admit(
        self,
        reservation: PendingTurnReservation,
        *,
        allow_follow_up: bool,
    ) -> TurnAdmission:
        event = reservation.turn.event
        runtime = self._event_sessions.get(event)
        if runtime is None:
            raise RuntimeError("Pending turn must be bound before admission.")
        return await runtime.admit(
            reservation,
            allow_follow_up=allow_follow_up,
        )

    async def _bind_and_admit(
        self,
        reservation: PendingTurnReservation,
        *,
        allow_follow_up: bool,
    ) -> TurnAdmission:
        await self._bind(reservation)
        return await self._admit(
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

    def _settle(self, reservation: PendingTurnReservation) -> None:
        event = reservation.turn.event
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
    ) -> str:
        turn = reservation.turn
        event = turn.event
        try:
            request = turn.provider_request
            conversation_persona_id = None
            if (
                isinstance(request, ProviderRequest)
                and request.conversation is not None
            ):
                conversation_persona_id = request.conversation.persona_id
            if conversation_persona_id is None:
                conversation_persona_id = await resolve_event_conversation_persona_id(
                    event,
                    turn.plugin_context.conversation_manager,
                )
            (
                persona_id,
                _,
                _,
                _,
            ) = await turn.plugin_context.persona_manager.resolve_selected_persona(
                umo=turn.session.unified_msg_origin,
                conversation_persona_id=conversation_persona_id,
                platform_name=turn.session.platform_name,
                provider_settings=turn.runtime_config.get("provider_settings", {}),
            )
            return str(persona_id or "default")
        except Exception as exc:
            logger.warning(
                "Personal Runtime persona resolution failed; isolating turn: session_id=%s error=%s",
                event.unified_msg_origin,
                exc,
            )
            return f"unresolved:{turn.turn_id}"


__all__ = [
    "PendingTurnReservation",
    "PendingTurnState",
    "PlatformEventSubmission",
    "RuntimeObservationEventSubmission",
    "PersonalRuntimeKey",
    "PersonalRuntimeManager",
    "PersonalSessionRuntime",
    "PersonalTurnLease",
    "TurnAdmission",
]
