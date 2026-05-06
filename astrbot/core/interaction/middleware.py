import asyncio
import uuid
from asyncio import Queue
from collections.abc import AsyncGenerator, Awaitable, Callable
from types import MethodType
from typing import Any

from astrbot import logger
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.postprocess import dispatch_postprocess
from astrbot.core.postprocess.types import PostProcessTrigger

from .config import is_middleware_enabled_for_platform, load_interaction_agent_config
from .core_bridge import (
    INTERACTION_CORE_TASK_SPEC_EXTRA_KEY,
    INTERACTION_DECISION_EXTRA_KEY,
)
from .decision_agent import InteractionDecisionAgent, build_fallback_decision
from .memory_store import InteractionMemoryStore, update_interaction_memory_from_turn
from .output_controller import InteractionOutputController
from .types import InteractionDecision, RouteMode


class InteractionMiddleware:
    def __init__(
        self,
        config: Any,
        core_queue: Queue,
        output_controller: InteractionOutputController,
        plugin_context: Any | None = None,
    ) -> None:
        self.config = config
        self.core_queue = core_queue
        self.output_controller = output_controller
        self.plugin_context = plugin_context
        self.interaction_config = load_interaction_agent_config(config)
        self.memory_store = InteractionMemoryStore()
        self.decision_agent = InteractionDecisionAgent(self.memory_store)
        self.output_controller.interaction_config = self.interaction_config
        self.output_controller.memory_store = self.memory_store
        self.output_controller.plugin_context = plugin_context
        self._inflight_tasks: set[asyncio.Task] = set()

    def set_plugin_context(self, plugin_context: Any) -> None:
        self.plugin_context = plugin_context
        self.output_controller.plugin_context = plugin_context

    def refresh_interaction_config(self) -> None:
        self.interaction_config = load_interaction_agent_config(self.config)
        self.output_controller.interaction_config = self.interaction_config

    def is_enabled_for_event(self, event: AstrMessageEvent) -> bool:
        return is_middleware_enabled_for_platform(event.get_platform_id(), self.config)

    def attach_event_context(
        self,
        event: AstrMessageEvent,
        *,
        turn_id: str,
        decision: InteractionDecision | None = None,
    ) -> None:
        event.set_extra("_interaction_enabled", True)
        event.set_extra("_turn_id", turn_id)
        event.set_extra("_output_controller", self.output_controller)
        self._install_core_output_interceptor(event)
        if decision is not None:
            event.set_extra(INTERACTION_DECISION_EXTRA_KEY, decision)
            if decision.core_task_spec is not None:
                event.set_extra(
                    INTERACTION_CORE_TASK_SPEC_EXTRA_KEY,
                    decision.core_task_spec,
                )
            event.set_extra("_interaction_plugin_hints", dict(decision.plugin_hints))
        logger.debug(
            "Interaction middleware attached event context: platform_id=%s session_id=%s turn_id=%s",
            event.get_platform_id(),
            event.session_id,
            turn_id,
        )

    def _install_core_output_interceptor(self, event: AstrMessageEvent) -> None:
        if event.get_extra("_interaction_output_interceptor_installed", False):
            return

        original_send = event.send
        original_send_streaming = event.send_streaming
        original_complete_visible_turn = event.complete_visible_turn
        output_controller = self.output_controller

        async def send_wrapper(
            wrapped_event: AstrMessageEvent,
            message: MessageChain | None,
        ) -> None:
            logger.debug(
                "Interaction middleware routing core send via output controller: platform_id=%s session_id=%s turn_id=%s",
                wrapped_event.get_platform_id(),
                wrapped_event.session_id,
                wrapped_event.get_extra("_turn_id"),
            )
            await output_controller.capture_message_chain(message, wrapped_event)
            wrapped_event._has_send_oper = True

        async def send_streaming_wrapper(
            wrapped_event: AstrMessageEvent,
            generator: AsyncGenerator[MessageChain, None],
            use_fallback: bool = False,
        ) -> None:
            logger.debug(
                "Interaction middleware routing core streaming via output controller: platform_id=%s session_id=%s turn_id=%s use_fallback=%s",
                wrapped_event.get_platform_id(),
                wrapped_event.session_id,
                wrapped_event.get_extra("_turn_id"),
                use_fallback,
            )
            await output_controller.capture_streaming(
                generator,
                wrapped_event,
                use_fallback=use_fallback,
            )
            wrapped_event._has_send_oper = True

        async def complete_visible_turn_wrapper(
            wrapped_event: AstrMessageEvent,
        ) -> None:
            logger.debug(
                "Interaction middleware routing visible completion via output controller: platform_id=%s session_id=%s turn_id=%s",
                wrapped_event.get_platform_id(),
                wrapped_event.session_id,
                wrapped_event.get_extra("_turn_id"),
            )
            await output_controller.capture_visible_completion(wrapped_event)

        event.set_extra("_interaction_original_send", original_send)
        event.set_extra("_interaction_original_send_streaming", original_send_streaming)
        event.set_extra(
            "_interaction_original_complete_visible_turn",
            original_complete_visible_turn,
        )
        event.send = MethodType(send_wrapper, event)
        event.send_streaming = MethodType(send_streaming_wrapper, event)
        event.complete_visible_turn = MethodType(complete_visible_turn_wrapper, event)
        event.set_extra("_interaction_output_interceptor_installed", True)

    def handle_inbound(self, event: AstrMessageEvent) -> None:
        enabled = self.is_enabled_for_event(event)
        logger.debug(
            "Interaction middleware inbound dispatch: platform_id=%s session_id=%s enabled=%s",
            event.get_platform_id(),
            event.session_id,
            enabled,
        )
        if not enabled:
            self.core_queue.put_nowait(event)
            return
        self._spawn_inbound_task(event)

    def _spawn_inbound_task(self, event: AstrMessageEvent) -> None:
        task = asyncio.create_task(
            self._handle_inbound_async(event),
            name=f"interaction_inbound_{event.get_platform_id()}_{uuid.uuid4().hex[:8]}",
        )
        self._inflight_tasks.add(task)
        task.add_done_callback(self._on_inflight_task_done)

    def _spawn_background_task(
        self,
        coro: Awaitable[Any],
        *,
        name: str,
        done_callback: Callable[[asyncio.Task], None] | None = None,
    ) -> None:
        task = asyncio.create_task(coro, name=name)
        self._inflight_tasks.add(task)
        if done_callback is not None:
            task.add_done_callback(
                lambda done_task: self._on_specific_inflight_task_done(
                    done_task,
                    done_callback,
                )
            )
        else:
            task.add_done_callback(self._on_inflight_task_done)

    def _on_specific_inflight_task_done(
        self,
        task: asyncio.Task,
        done_callback: Callable[[asyncio.Task], None],
    ) -> None:
        self._inflight_tasks.discard(task)
        done_callback(task)

    def _on_inflight_task_done(self, task: asyncio.Task) -> None:
        self._inflight_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            logger.debug(
                "Interaction middleware task cancelled: name=%s",
                task.get_name(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Interaction middleware task failed: name=%s error=%s",
                task.get_name(),
                exc,
                exc_info=True,
            )

    async def _handle_inbound_async(self, event: AstrMessageEvent) -> None:
        self.refresh_interaction_config()
        turn_id = uuid.uuid4().hex
        logger.debug(
            "Interaction middleware async turn start: platform_id=%s session_id=%s turn_id=%s decision_provider_id=%s decision_model=%s finalizer_provider_id=%s",
            event.get_platform_id(),
            event.session_id,
            turn_id,
            self.interaction_config.decision_provider_id,
            self.interaction_config.decision_model,
            self.interaction_config.finalizer_provider_id,
        )
        decision = await self._decide_or_fallback(event)
        self.attach_event_context(event, turn_id=turn_id, decision=decision)
        if decision.route_mode == RouteMode.SELF_REPLY:
            if not decision.should_emit_immediate_reply:
                event.set_extra("_interaction_self_reply_invalid", True)
                event.set_extra(
                    "_interaction_self_reply_invalid_reason",
                    "missing_immediate_reply",
                )
                logger.error(
                    "Interaction self reply invalid; forwarding to core: platform_id=%s session_id=%s turn_id=%s reason=missing_immediate_reply",
                    event.get_platform_id(),
                    event.session_id,
                    event.get_extra("_turn_id"),
                )
                self._forward_to_core(event)
                return
            if decision.should_emit_immediate_reply:
                sent = await self._emit_immediate_reply_or_record_failure(
                    event,
                    decision,
                )
                if not sent:
                    self._forward_to_core(event)
                    return
            completed = await self._complete_visible_turn_or_record_failure(event)
            if completed:
                self._schedule_turn_postprocess(event)
                await self._persist_turn(
                    event, decision, decision.immediate_spoken_reply
                )
            return
        if decision.route_mode == RouteMode.HYBRID:
            if decision.should_emit_immediate_reply:
                sent = await self._emit_immediate_reply_or_record_failure(
                    event, decision
                )
                if sent:
                    self._schedule_memory_persist(
                        event,
                        decision,
                        decision.immediate_spoken_reply,
                    )
            self._forward_to_core(event)
            return
        if decision.should_emit_immediate_reply:
            await self._emit_immediate_reply_or_record_failure(event, decision)
        self._forward_to_core(event)

    async def _decide_or_fallback(self, event: AstrMessageEvent) -> InteractionDecision:
        if self.plugin_context is None:
            logger.error(
                "Interaction decision fallback: reason=plugin_context_unavailable platform_id=%s session_id=%s",
                event.get_platform_id(),
                event.session_id,
            )
            return build_fallback_decision("plugin_context_unavailable")
        try:
            decision = await self.decision_agent.decide(
                event,
                self.plugin_context,
                self.interaction_config,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Interaction decision fallback: reason=decision_pipeline_error platform_id=%s session_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                exc,
                exc_info=True,
            )
            return build_fallback_decision("decision_pipeline_error")
        logger.debug(
            "Interaction middleware decision resolved: platform_id=%s session_id=%s route_mode=%s confidence=%s reason=%s is_fallback=%s fallback_reason=%s",
            event.get_platform_id(),
            event.session_id,
            decision.route_mode.value,
            decision.confidence,
            decision.reason,
            decision.is_fallback,
            decision.fallback_reason,
        )
        return decision

    async def _emit_immediate_reply(
        self,
        event: AstrMessageEvent,
        decision: InteractionDecision,
    ) -> None:
        if not decision.immediate_spoken_reply:
            return
        await self.output_controller.emit_immediate_spoken_reply(decision, event)

    async def _emit_immediate_reply_or_record_failure(
        self,
        event: AstrMessageEvent,
        decision: InteractionDecision,
    ) -> bool:
        try:
            await self._emit_immediate_reply(event, decision)
            return True
        except Exception as exc:  # noqa: BLE001
            event.set_extra("_interaction_immediate_reply_failed", True)
            event.set_extra("_interaction_immediate_reply_failure_reason", str(exc))
            logger.error(
                "Interaction immediate reply failed; continuing core delegation: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )
            return False

    async def _complete_visible_turn_or_record_failure(
        self,
        event: AstrMessageEvent,
    ) -> bool:
        try:
            await event.complete_visible_turn()
            return True
        except Exception as exc:  # noqa: BLE001
            event.set_extra("_interaction_visible_completion_failed", True)
            event.set_extra("_interaction_visible_completion_failure_reason", str(exc))
            logger.error(
                "Interaction visible completion failed: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )
            return False

    def _schedule_turn_postprocess(self, event: AstrMessageEvent) -> None:
        visible_outputs = [
            dict(item)
            for item in event.get_extra("_visible_turn_outputs", [])
            if isinstance(item, dict)
        ]
        self._spawn_background_task(
            dispatch_postprocess(
                event=event,
                trigger=PostProcessTrigger.AFTER_TURN_COMPLETED,
                plugin_context=self.plugin_context,
                turn_id=str(event.get_extra("_turn_id", "") or ""),
                visible_outputs=visible_outputs,
            ),
            name=f"interaction_turn_postprocess_{event.get_platform_id()}",
            done_callback=lambda done_task: self._log_turn_postprocess_failure(
                event,
                done_task,
            ),
        )

    @staticmethod
    def _log_turn_postprocess_failure(
        event: AstrMessageEvent,
        task: asyncio.Task,
    ) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            logger.debug(
                "Interaction turn postprocess cancelled: platform_id=%s session_id=%s turn_id=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
            )
        except Exception as exc:  # noqa: BLE001
            event.set_extra("_interaction_turn_postprocess_failed", True)
            event.set_extra("_interaction_turn_postprocess_failure_reason", str(exc))
            logger.error(
                "Interaction turn postprocess failed: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )

    def _forward_to_core(self, event: AstrMessageEvent) -> None:
        decision = event.get_extra(INTERACTION_DECISION_EXTRA_KEY)
        if (
            isinstance(decision, InteractionDecision)
            and decision.route_mode in {RouteMode.DELEGATE_TO_CORE, RouteMode.HYBRID}
            and decision.should_emit_immediate_reply
            and event._has_send_oper
        ):
            event._has_send_oper = False
            logger.debug(
                "Interaction middleware restored core send state before forwarding: platform_id=%s session_id=%s turn_id=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
            )
        logger.debug(
            "Interaction middleware forwarding to core: platform_id=%s session_id=%s turn_id=%s",
            event.get_platform_id(),
            event.session_id,
            event.get_extra("_turn_id"),
        )
        self.core_queue.put_nowait(event)

    def _schedule_memory_persist(
        self,
        event: AstrMessageEvent,
        decision: InteractionDecision,
        visible_reply: str | None,
    ) -> None:
        self._spawn_background_task(
            self._persist_turn(event, decision, visible_reply),
            name=f"interaction_memory_persist_{event.get_platform_id()}",
        )

    async def _persist_turn(
        self,
        event: AstrMessageEvent,
        decision: InteractionDecision,
        visible_reply: str | None,
    ) -> None:
        try:
            await self.memory_store.update_interaction_memory(
                event.unified_msg_origin,
                str(event.get_extra("_interaction_persona_id", "") or ""),
                lambda snapshot: update_interaction_memory_from_turn(
                    snapshot,
                    user_text=event.message_str,
                    visible_reply=visible_reply,
                    turn_id=str(event.get_extra("_turn_id", "") or ""),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            event.set_extra("_interaction_memory_persist_failed", True)
            event.set_extra("_interaction_memory_persist_failure_reason", str(exc))
            logger.error(
                "Interaction memory persistence failed: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )
