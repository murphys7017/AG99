import asyncio
import uuid
from asyncio import Queue
from collections.abc import AsyncGenerator, Awaitable, Callable
from types import MethodType
from typing import Any

from astrbot import logger
from astrbot.core.message.components import Image, Plain, Record
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.postprocess import dispatch_postprocess
from astrbot.core.postprocess.types import PostProcessTrigger
from astrbot.core.utils.media_utils import ensure_wav

from .config import is_middleware_enabled_for_platform, load_interaction_agent_config
from .core_bridge import (
    INTERACTION_CORE_TASK_SPEC_EXTRA_KEY,
    INTERACTION_DECISION_EXTRA_KEY,
)
from .decision_agent import InteractionDecisionAgent
from .memory_store import (
    InteractionMemoryStore,
    build_interaction_memory_reply_from_visible_outputs,
    update_interaction_memory_from_turn,
)
from .output_controller import InteractionOutputController
from .turn_state import (
    ensure_interaction_turn_state,
    get_interaction_turn_finalized_material,
    get_interaction_turn_state,
    get_interaction_turn_visible_outputs,
    is_interaction_turn_completed,
    mark_interaction_turn_completed,
    mark_interaction_turn_memory_persisted,
    mark_interaction_turn_postprocess_dispatched,
    record_interaction_turn_completion_failure,
    record_interaction_turn_failure,
    set_interaction_turn_decision,
    set_interaction_turn_finalized_material,
)
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
        self._reject_development_fallback_policy()
        self.interaction_config = load_interaction_agent_config(config)
        self.memory_store = InteractionMemoryStore()
        self.decision_agent = InteractionDecisionAgent(self.memory_store)
        self.output_controller.interaction_config = self.interaction_config
        self.output_controller.memory_store = self.memory_store
        self.output_controller.plugin_context = plugin_context
        self.output_controller._persist_callback = self._on_output_persist_requested
        self._inflight_tasks: set[asyncio.Task] = set()

    def set_plugin_context(self, plugin_context: Any) -> None:
        self.plugin_context = plugin_context
        self.output_controller.plugin_context = plugin_context

    def refresh_interaction_config(self) -> None:
        self._reject_development_fallback_policy()
        self.interaction_config = load_interaction_agent_config(self.config)
        self.output_controller.interaction_config = self.interaction_config

    def _reject_development_fallback_policy(self) -> None:
        interaction_config = self.config.get("interaction_middleware", {})
        if not isinstance(interaction_config, dict):
            return
        fallback_policy = interaction_config.get("fallback_policy")
        if fallback_policy is None:
            return
        raise RuntimeError(
            "interaction_middleware.fallback_policy is disabled during development"
        )

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
            set_interaction_turn_decision(event, decision)
            event.set_extra(INTERACTION_DECISION_EXTRA_KEY, decision)
            if decision.core_task_spec is not None:
                event.set_extra(
                    INTERACTION_CORE_TASK_SPEC_EXTRA_KEY,
                    decision.core_task_spec,
                )
            event.set_extra("_interaction_plugin_hints", dict(decision.plugin_hints))

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
            await output_controller.capture_message_chain(message, wrapped_event)
            wrapped_event._has_send_oper = True

        async def send_streaming_wrapper(
            wrapped_event: AstrMessageEvent,
            generator: AsyncGenerator[MessageChain, None],
            use_fallback: bool = False,
        ) -> None:
            await output_controller.capture_streaming(
                generator,
                wrapped_event,
                use_fallback=use_fallback,
            )
            wrapped_event._has_send_oper = True

        async def complete_visible_turn_wrapper(
            wrapped_event: AstrMessageEvent,
        ) -> None:
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
        if not self.is_enabled_for_event(event):
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
        turn_state = ensure_interaction_turn_state(event, turn_id=turn_id)
        await self._materialize_inbound_media(event)
        decision = await self._decide_interaction_route(event)
        self.attach_event_context(event, turn_id=turn_state.turn_id, decision=decision)
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
            completed = await self._complete_visible_turn_or_record_failure(
                event,
            )
            if completed:
                await self._finalize_turn(
                    event,
                    visible_reply=decision.immediate_spoken_reply,
                )
            return
        if decision.route_mode == RouteMode.HYBRID:
            if decision.should_emit_immediate_reply:
                await self._emit_immediate_reply_or_record_failure(event, decision)
            self._forward_to_core(event)
            return
        if decision.should_emit_immediate_reply:
            await self._emit_immediate_reply_or_record_failure(event, decision)
        self._forward_to_core(event)

    async def _decide_interaction_route(
        self, event: AstrMessageEvent
    ) -> InteractionDecision:
        if self.plugin_context is None:
            event.set_extra("_interaction_decision_failed", True)
            event.set_extra(
                "_interaction_decision_failure_reason",
                "plugin_context_unavailable",
            )
            record_interaction_turn_failure(
                event,
                stage="decision",
                reason="plugin_context_unavailable",
                user_visible_action="none",
            )
            raise RuntimeError("Interaction decision plugin context unavailable")
        try:
            decision = await self.decision_agent.decide(
                event,
                self.plugin_context,
                self.interaction_config,
            )
        except Exception as exc:  # noqa: BLE001
            event.set_extra("_interaction_decision_failed", True)
            event.set_extra("_interaction_decision_failure_reason", str(exc))
            record_interaction_turn_failure(
                event,
                stage="decision",
                reason=getattr(exc, "reason", "decision_pipeline_error"),
                exception=exc,
                user_visible_action="none",
            )
            logger.error(
                "Interaction decision failed: platform_id=%s session_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                exc,
                exc_info=True,
            )
            raise
        if decision.is_fallback:
            reason = str(decision.fallback_reason or "fallback_decision")
            event.set_extra("_interaction_decision_failed", True)
            event.set_extra("_interaction_decision_failure_reason", reason)
            record_interaction_turn_failure(
                event,
                stage="decision",
                reason=reason,
                user_visible_action="none",
            )
            raise RuntimeError(f"Interaction fallback decision rejected: {reason}")
        return decision

    async def _materialize_inbound_media(self, event: AstrMessageEvent) -> None:
        self._apply_inbound_path_mapping(event)
        await self._normalize_inbound_records(event)
        await self._transcribe_inbound_records(event)
        event.set_extra("_interaction_inbound_media_materialized", True)

    def _apply_inbound_path_mapping(self, event: AstrMessageEvent) -> None:
        platform_settings = self.config.get("platform_settings", {})
        mappings = platform_settings.get("path_mapping", [])
        if not isinstance(mappings, list) or not mappings:
            return

        message_chain = event.get_messages()
        for idx, component in enumerate(message_chain):
            if not isinstance(component, Record | Image) or not component.url:
                continue
            for mapping in mappings:
                if not isinstance(mapping, str) or ":" not in mapping:
                    continue
                from_, to_ = mapping.split(":", 1)
                from_ = from_.removesuffix("/")
                to_ = to_.removesuffix("/")
                url = component.url.removeprefix("file://")
                if url.startswith(from_):
                    component.url = url.replace(from_, to_, 1)
                    logger.debug(
                        "Interaction inbound path mapped: platform_id=%s session_id=%s from=%s to=%s",
                        event.get_platform_id(),
                        event.session_id,
                        url,
                        component.url,
                    )
            message_chain[idx] = component

    async def _normalize_inbound_records(self, event: AstrMessageEvent) -> None:
        message_chain = event.get_messages()
        for idx, component in enumerate(message_chain):
            if not isinstance(component, Record):
                continue
            try:
                original_path = await component.convert_to_file_path()
                record_path = await ensure_wav(original_path)
                if record_path != original_path:
                    event.track_temporary_local_file(record_path)
                component.file = record_path
                component.path = record_path
                message_chain[idx] = component
            except Exception as exc:  # noqa: BLE001
                event.set_extra("_interaction_record_normalize_failed", True)
                event.set_extra(
                    "_interaction_record_normalize_failure_reason",
                    str(exc),
                )
                record_interaction_turn_failure(
                    event,
                    stage="inbound_record_normalize",
                    reason="record_normalize_failed",
                    exception=exc,
                    user_visible_action="none",
                )
                logger.warning(
                    "Interaction inbound voice normalization failed: platform_id=%s session_id=%s turn_id=%s error=%s",
                    event.get_platform_id(),
                    event.session_id,
                    event.get_extra("_turn_id"),
                    exc,
                )
                raise

    async def _transcribe_inbound_records(self, event: AstrMessageEvent) -> None:
        stt_settings = self.config.get("provider_stt_settings", {})
        if not isinstance(stt_settings, dict) or not stt_settings.get("enable", False):
            return
        if self.plugin_context is None:
            event.set_extra("_interaction_stt_failed", True)
            event.set_extra(
                "_interaction_stt_failure_reason",
                "plugin_context_unavailable",
            )
            record_interaction_turn_failure(
                event,
                stage="inbound_stt",
                reason="plugin_context_unavailable",
                user_visible_action="none",
            )
            logger.warning(
                "Interaction inbound STT skipped: platform_id=%s session_id=%s reason=plugin_context_unavailable",
                event.get_platform_id(),
                event.session_id,
            )
            raise RuntimeError("Interaction STT plugin context unavailable")

        get_provider = getattr(self.plugin_context, "get_using_stt_provider", None)
        stt_provider = (
            get_provider(event.unified_msg_origin) if callable(get_provider) else None
        )
        if not stt_provider:
            event.set_extra("_interaction_stt_failed", True)
            event.set_extra(
                "_interaction_stt_failure_reason",
                "provider_unavailable",
            )
            record_interaction_turn_failure(
                event,
                stage="inbound_stt",
                reason="provider_unavailable",
                user_visible_action="none",
            )
            logger.warning(
                "Interaction inbound STT skipped: platform_id=%s session_id=%s reason=provider_unavailable",
                event.get_platform_id(),
                event.session_id,
            )
            raise RuntimeError("Interaction STT provider unavailable")

        message_chain = event.get_messages()
        for idx, component in enumerate(message_chain):
            if not isinstance(component, Record):
                continue
            try:
                path = await component.convert_to_file_path()
            except Exception as exc:  # noqa: BLE001
                event.set_extra("_interaction_stt_failed", True)
                event.set_extra("_interaction_stt_failure_reason", str(exc))
                record_interaction_turn_failure(
                    event,
                    stage="inbound_stt",
                    reason="audio_path_resolution_failed",
                    exception=exc,
                    user_visible_action="none",
                )
                logger.error(
                    "Interaction inbound STT audio path resolution failed: platform_id=%s session_id=%s turn_id=%s error=%s",
                    event.get_platform_id(),
                    event.session_id,
                    event.get_extra("_turn_id"),
                    exc,
                    exc_info=True,
                )
                raise
            try:
                result = await stt_provider.get_text(audio_url=path)
            except FileNotFoundError as exc:
                event.set_extra("_interaction_stt_failed", True)
                event.set_extra("_interaction_stt_failure_reason", str(exc))
                record_interaction_turn_failure(
                    event,
                    stage="inbound_stt",
                    reason="source_unavailable",
                    exception=exc,
                    user_visible_action="none",
                )
                raise
            except Exception as exc:  # noqa: BLE001
                event.set_extra("_interaction_stt_failed", True)
                event.set_extra("_interaction_stt_failure_reason", str(exc))
                record_interaction_turn_failure(
                    event,
                    stage="inbound_stt",
                    reason="provider_error",
                    exception=exc,
                    user_visible_action="none",
                )
                logger.error(
                    "Interaction inbound STT failed: platform_id=%s session_id=%s turn_id=%s error=%s",
                    event.get_platform_id(),
                    event.session_id,
                    event.get_extra("_turn_id"),
                    exc,
                    exc_info=True,
                )
                raise
            text = str(result or "").strip()
            if not text:
                record_interaction_turn_failure(
                    event,
                    stage="inbound_stt",
                    reason="empty_transcription",
                    user_visible_action="none",
                )
                raise RuntimeError("Interaction STT returned empty text")
            logger.info("Interaction inbound STT result: %s", text)
            message_chain[idx] = Plain(text)
            event.message_str = f"{event.message_str or ''}{text}"
            event.message_obj.message_str = (
                f"{event.message_obj.message_str or ''}{text}"
            )
            event.set_extra("_interaction_stt_transcribed", True)

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
            record_interaction_turn_failure(
                event,
                stage="immediate_reply",
                reason="send_failed",
                exception=exc,
                user_visible_action="none",
            )
            logger.error(
                "Interaction immediate reply failed; aborting turn: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )
            raise

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
            record_interaction_turn_failure(
                event,
                stage="visible_completion",
                reason="completion_failed",
                exception=exc,
                user_visible_action="none",
            )
            logger.error(
                "Interaction visible completion failed; aborting turn: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )
            raise

    def _schedule_turn_postprocess(self, event: AstrMessageEvent) -> None:
        visible_outputs = get_interaction_turn_visible_outputs(event)
        turn_material = get_interaction_turn_finalized_material(event)
        if turn_material is None:
            event.set_extra("_interaction_turn_postprocess_failed", True)
            event.set_extra(
                "_interaction_turn_postprocess_failure_reason",
                "missing_finalized_turn_material",
            )
            record_interaction_turn_completion_failure(
                event,
                "missing_finalized_turn_material",
            )
            logger.error(
                "Interaction turn postprocess skipped: missing finalized material platform_id=%s session_id=%s turn_id=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
            )
            return
        self._spawn_background_task(
            dispatch_postprocess(
                event=event,
                trigger=PostProcessTrigger.AFTER_TURN_COMPLETED,
                plugin_context=self.plugin_context,
                turn_id=str(event.get_extra("_turn_id", "") or ""),
                visible_outputs=visible_outputs,
                turn_material=turn_material,
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
            pass
        except Exception as exc:  # noqa: BLE001
            event.set_extra("_interaction_turn_postprocess_failed", True)
            event.set_extra("_interaction_turn_postprocess_failure_reason", str(exc))
            record_interaction_turn_completion_failure(event, f"postprocess:{exc}")
            logger.error(
                "Interaction turn postprocess failed: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )

    def _forward_to_core(self, event: AstrMessageEvent) -> None:
        turn_state = get_interaction_turn_state(event)
        decision = turn_state.decision if turn_state is not None else None
        if (
            isinstance(decision, InteractionDecision)
            and decision.route_mode in {RouteMode.DELEGATE_TO_CORE, RouteMode.HYBRID}
            and decision.should_emit_immediate_reply
            and event._has_send_oper
        ):
            event._has_send_oper = False
        self.core_queue.put_nowait(event)

    def _build_finalized_turn_material(
        self,
        event: AstrMessageEvent,
        visible_outputs: list[dict[str, Any]] | None = None,
        *,
        canonical_reply: str | None = None,
    ) -> dict[str, Any] | None:
        turn_id = str(event.get_extra("_turn_id", "") or "").strip()
        if not turn_id:
            return None
        outputs = [
            dict(item)
            for item in (
                visible_outputs
                if isinstance(visible_outputs, list)
                else get_interaction_turn_visible_outputs(event)
            )
            if isinstance(item, dict)
        ]
        if canonical_reply is None:
            turn_state = get_interaction_turn_state(event)
            utterances = turn_state.utterances if turn_state is not None else None
            canonical_reply = build_interaction_memory_reply_from_visible_outputs(
                outputs,
                turn_id=turn_id,
                utterances=utterances,
            )
        canonical_reply = (canonical_reply or "").strip()
        if not canonical_reply:
            return None
        material = {
            "turn_id": turn_id,
            "user_text": (event.message_str or "").strip(),
            "assistant_text": canonical_reply,
            "visible_outputs": outputs,
            "history_source": "interaction.turn.material",
        }
        set_interaction_turn_finalized_material(event, material)
        return material

    async def _finalize_turn(
        self,
        event: AstrMessageEvent,
        *,
        visible_reply: str | None = None,
    ) -> None:
        turn_state = get_interaction_turn_state(event)
        if turn_state is None:
            turn_state = ensure_interaction_turn_state(event)
        if is_interaction_turn_completed(event):
            return

        material = get_interaction_turn_finalized_material(event)
        if material is None:
            material = self._build_finalized_turn_material(
                event,
                canonical_reply=visible_reply,
            )
        if material is not None:
            turn_id = str(material.get("turn_id", "") or "")
            canonical_reply = str(material.get("assistant_text", "") or "").strip()
            if canonical_reply:
                try:
                    await self.memory_store.update_interaction_memory(
                        event.unified_msg_origin,
                        str(event.get_extra("_interaction_persona_id", "") or ""),
                        lambda snapshot: update_interaction_memory_from_turn(
                            snapshot,
                            user_text=event.message_str,
                            visible_reply=canonical_reply,
                            turn_id=turn_id,
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    event.set_extra("_interaction_memory_persist_failed", True)
                    event.set_extra(
                        "_interaction_memory_persist_failure_reason", str(exc)
                    )
                    record_interaction_turn_completion_failure(
                        event,
                        f"memory_persist:{exc}",
                    )
                    logger.error(
                        "Interaction memory persistence failed during turn finalization: platform_id=%s session_id=%s turn_id=%s error=%s",
                        event.get_platform_id(),
                        event.session_id,
                        turn_id,
                        exc,
                        exc_info=True,
                    )
                    return
                mark_interaction_turn_memory_persisted(event)
            else:
                event.set_extra("_interaction_memory_persist_failed", True)
                event.set_extra(
                    "_interaction_memory_persist_failure_reason",
                    "missing_canonical_reply",
                )
                record_interaction_turn_completion_failure(
                    event,
                    "missing_canonical_reply",
                )
                return
        else:
            event.set_extra("_interaction_memory_persist_failed", True)
            event.set_extra(
                "_interaction_memory_persist_failure_reason",
                "missing_material",
            )
            record_interaction_turn_completion_failure(event, "missing_material")
            return

        self._schedule_turn_postprocess(event)
        mark_interaction_turn_postprocess_dispatched(event)
        mark_interaction_turn_completed(event)

    async def _on_output_persist_requested(
        self,
        event: AstrMessageEvent,
        visible_reply: str | None,
    ) -> None:
        await self._finalize_turn(event, visible_reply=visible_reply)
