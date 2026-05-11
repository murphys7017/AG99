from __future__ import annotations

import asyncio
import json
import random
import time
import traceback
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from astrbot import logger
from astrbot.core import file_token_service, html_renderer
from astrbot.core.message.components import Image, Json, Plain, Record
from astrbot.core.message.message_chain_delivery import deliver_message_chain
from astrbot.core.message.message_event_result import MessageChain, ResultContentType
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.prompt.render.selector import _extract_json_object
from astrbot.core.provider import Provider
from astrbot.core.star.session_llm_manager import SessionServiceManager
from astrbot.core.voice import VoiceServiceError, resolve_tts_provider, synthesize_text

from .context_builder import (
    build_interaction_context_pack,
    extract_interaction_memory_payload,
    extract_persona_payload,
    extract_recent_messages,
)
from .contributors import (
    InteractionResultContribution,
    InteractionResultView,
    InteractionStreamView,
    merge_result_contributions,
)
from .core_bridge import get_interaction_decision
from .decision_agent import _build_decision_build_config
from .finalizer import finalize_response
from .memory_store import (
    InteractionMemoryStore,
    build_interaction_memory_reply_from_visible_outputs,
)
from .turn_state import (
    add_interaction_turn_stream_observation_task,
    append_interaction_turn_visible_output,
    get_interaction_turn_finalized_material,
    get_interaction_turn_immediate_reply,
    get_interaction_turn_state,
    get_interaction_turn_stream_interjections_emitted,
    get_interaction_turn_stream_observation_count,
    get_interaction_turn_stream_observation_tasks,
    get_interaction_turn_stream_pending_text,
    get_interaction_turn_stream_text,
    get_interaction_turn_visible_outputs,
    has_interaction_turn_core_final_result_consumed,
    has_interaction_turn_core_streaming_result_consumed,
    is_interaction_turn_completed,
    is_interaction_turn_core_streaming_active,
    mark_interaction_turn_core_final_result_consumed,
    mark_interaction_turn_core_streaming_result_consumed,
    mark_interaction_turn_stream_interjection_emitted,
    next_interaction_turn_visible_message_id,
    record_interaction_turn_completion_failure,
    record_interaction_turn_failure,
    record_interaction_turn_stream_observation_failure,
    remove_interaction_turn_stream_observation_task,
    set_interaction_turn_core_streaming_active,
    set_interaction_turn_finalized_material,
    set_interaction_turn_immediate_reply,
    set_interaction_turn_stream_observation_count,
    update_interaction_turn_stream_buffer,
)
from .types import FinalizerMode, InteractionAgentConfig, RouteMode


@dataclass(slots=True)
class StreamObservationDecision:
    should_interject: bool = False
    reply: str | None = None
    reason: str = ""


class InteractionOutputController:
    def __init__(
        self,
        *,
        plugin_context: Any | None = None,
        interaction_config: InteractionAgentConfig | None = None,
        interaction_memory_store: InteractionMemoryStore | None = None,
        platform_settings: dict[str, Any] | None = None,
        persist_callback: (Callable[[AstrMessageEvent], Awaitable[None]] | None) = None,
    ) -> None:
        self.plugin_context = plugin_context
        self.interaction_config = interaction_config or InteractionAgentConfig()
        self.interaction_memory_store = interaction_memory_store
        self.platform_settings = platform_settings or {}
        self._persist_callback = persist_callback
        self._refresh_outbound_materialization_config()

    def _refresh_outbound_materialization_config(
        self,
        event: AstrMessageEvent | None = None,
    ) -> None:
        self.reply_prefix = str(self.platform_settings.get("reply_prefix", "") or "")
        self.t2i_word_threshold = self._coerce_t2i_word_threshold(
            self._get_config_value("t2i_word_threshold", 150, event=event),
        )
        self.t2i_strategy = str(
            self._get_config_value("t2i_strategy", "remote", event=event) or ""
        )
        self.t2i_use_network = self.t2i_strategy == "remote"
        self.t2i_active_template = str(
            self._get_config_value("t2i_active_template", "base", event=event) or "base"
        )
        self.tts_trigger_probability = self._coerce_probability(
            self._get_tts_settings(event).get("trigger_probability", 1.0),
        )
        provider_cfg = self._get_provider_settings(event)
        self.show_reasoning = bool(provider_cfg.get("display_reasoning_text", False))

    @staticmethod
    def _coerce_t2i_word_threshold(value: Any) -> int:
        try:
            return max(int(value), 50)
        except (TypeError, ValueError):
            return 150

    @staticmethod
    def _coerce_probability(value: Any) -> float:
        try:
            return max(0.0, min(float(value), 1.0))
        except (TypeError, ValueError):
            return 1.0

    def _get_runtime_config(self, event: AstrMessageEvent | None = None) -> Any:
        if self.plugin_context is None:
            return None
        get_config = getattr(self.plugin_context, "get_config", None)
        if not callable(get_config):
            return None
        if event is not None:
            return get_config(umo=event.unified_msg_origin)
        return get_config()

    def _get_config_value(
        self,
        key: str,
        default: Any = None,
        *,
        event: AstrMessageEvent | None = None,
    ) -> Any:
        cfg = self._get_runtime_config(event)
        if isinstance(cfg, dict):
            return cfg.get(key, default)
        if cfg is not None and hasattr(cfg, "get"):
            return cfg.get(key, default)
        return default

    def _get_provider_settings(
        self,
        event: AstrMessageEvent | None = None,
    ) -> dict[str, Any]:
        value = self._get_config_value("provider_settings", {}, event=event)
        return value if isinstance(value, dict) else {}

    def _get_tts_settings(
        self,
        event: AstrMessageEvent | None = None,
    ) -> dict[str, Any]:
        value = self._get_config_value("provider_tts_settings", {}, event=event)
        return value if isinstance(value, dict) else {}

    async def emit_immediate_spoken_reply(
        self,
        decision,
        event: AstrMessageEvent,
    ) -> None:
        reply = (decision.immediate_spoken_reply or "").strip()
        if not reply:
            return
        set_interaction_turn_immediate_reply(event, reply)
        event.set_extra("_interaction_emitting_immediate_reply", True)
        try:
            await self.capture_message_chain(
                MessageChain([Plain(reply)]),
                event,
            )
        finally:
            event.set_extra("_interaction_emitting_immediate_reply", False)

    async def capture_message_chain(
        self,
        message: MessageChain | None,
        event: AstrMessageEvent,
    ) -> None:
        if message is None:
            await self.capture_visible_completion(event)
            return

        is_immediate = bool(event.get_extra("_interaction_emitting_immediate_reply"))
        outbound_kind = self._classify_outbound_message(event, message, is_immediate)
        if is_immediate:
            semantic_text = message.get_plain_text()
            contributions = await self._collect_result_contributions(
                event,
                core_result=None,
                final_result=semantic_text,
                phase="immediate",
                candidate_message_kind="immediate_reply",
            )
            merged = merge_result_contributions(contributions)
            if merged.final_text_override is not None:
                message = message.derive([Plain(merged.final_text_override)])
                semantic_text = message.get_plain_text()
                set_interaction_turn_immediate_reply(event, semantic_text)
            (
                message,
                materialization,
            ) = await self.materialize_immediate_interaction_outbound_message(
                event,
                message,
            )
            delivered_message_ids = await self._deliver_visible_message(
                event,
                message,
                message_kind="immediate_reply",
                platform_extras=self.build_platform_output_base_extras(
                    event,
                    result_contribution=merged,
                ),
                record_send_operation=False,
                allow_segmented_reply=False,
                semantic_text=semantic_text,
            )
            self._record_visible_output(
                event,
                message_kind="immediate_reply",
                text=semantic_text,
                delivered_message_ids=delivered_message_ids,
                metadata=materialization,
            )
            return

        if outbound_kind == "streaming_finish_marker":
            mark_interaction_turn_core_final_result_consumed(event)
            logger.warning(
                "Interaction streaming finish marker skipped after streaming delivery: platform_id=%s session_id=%s turn_id=%s final_length=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                len(message.get_plain_text()),
            )
            self._materialize_finalized_turn(event)
            await self._persist_interaction_turn(event)
            return

        if outbound_kind == "passthrough":
            semantic_text = message.get_plain_text()
            (
                message,
                materialization,
            ) = await self.materialize_interaction_outbound_message(
                event,
                message,
                message_kind="passthrough",
                result_is_model_result=False,
            )
            delivered_message_ids = await self._deliver_visible_message(
                event,
                message,
                message_kind="passthrough",
                allow_segmented_reply=True,
                semantic_text=semantic_text,
            )
            self._record_visible_output(
                event,
                message_kind="passthrough",
                text=semantic_text,
                delivered_message_ids=delivered_message_ids,
                metadata=materialization,
            )
            self._materialize_finalized_turn(event)
            await self._persist_interaction_turn(event)
            return

        if outbound_kind == "suppressed_duplicate_final":
            return

        mark_interaction_turn_core_final_result_consumed(event)
        full_message = self._get_full_core_final_message(event, message)
        await self._deliver_core_reply(full_message, event)

    async def capture_visible_completion(
        self,
        event: AstrMessageEvent,
    ) -> None:
        complete_visible_turn = event.get_extra(
            "_interaction_original_complete_visible_turn"
        )
        if callable(complete_visible_turn):
            await complete_visible_turn()
            return
        await event.complete_visible_turn()

    @staticmethod
    def _get_full_core_final_message(
        event: AstrMessageEvent,
        fallback: MessageChain,
    ) -> MessageChain:
        result = event.get_result()
        if result is None or not result.chain:
            return fallback
        return result.derive(list(result.chain))

    async def capture_streaming(
        self,
        generator: AsyncGenerator[MessageChain, None],
        event: AstrMessageEvent,
        use_fallback: bool = False,
    ) -> None:
        set_interaction_turn_core_streaming_active(event, True)
        observed_generator = self._wrap_core_stream(generator, event)
        try:
            await event.send_interaction_streaming(
                observed_generator,
                platform_extras=self.build_platform_output_extras(
                    event,
                    message_kind="core_stream",
                ),
                use_fallback=use_fallback,
            )
        except Exception as exc:
            event.set_extra("_interaction_core_streaming_failed", True)
            event.set_extra("_interaction_core_streaming_failure_reason", str(exc))
            logger.error(
                "Interaction middleware streaming delivery failed: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )
            raise
        else:
            self._finalize_interaction_stream_output(event)
            await self._persist_interaction_turn(event)
        finally:
            set_interaction_turn_core_streaming_active(event, False)

    async def _wrap_core_stream(
        self,
        generator: AsyncGenerator[MessageChain, None],
        event: AstrMessageEvent,
    ) -> AsyncGenerator[MessageChain, None]:
        if not self.interaction_config.stream_observation_enabled:
            async for chain in generator:
                chunk_text = self._extract_observable_stream_text(chain)
                if chunk_text:
                    self._update_interaction_turn_stream_buffer(
                        event, chunk_text=chunk_text, observe=False
                    )
                yield chain
            return

        min_chars = self.interaction_config.stream_observation_min_chars
        observation_state = self._build_stream_observation_state()
        async for chain in generator:
            chunk_text = self._extract_observable_stream_text(chain)
            if chunk_text:
                self._update_interaction_turn_stream_buffer(
                    event, chunk_text=chunk_text, observe=True
                )
                total_text = get_interaction_turn_stream_text(event)
                pending_text = get_interaction_turn_stream_pending_text(event)
                while len(pending_text) >= min_chars:
                    window_index = (
                        get_interaction_turn_stream_observation_count(event) + 1
                    )
                    observed_text = pending_text[:min_chars]
                    pending_text = pending_text[min_chars:]
                    update_interaction_turn_stream_buffer(
                        event,
                        total_text=total_text,
                        pending_text=pending_text,
                    )
                    self._schedule_interaction_stream_observation(
                        event,
                        observed_text=observed_text,
                        total_text=total_text,
                        window_index=window_index,
                        observation_state=observation_state,
                        chain_type=chain.type,
                        is_final=False,
                    )
            yield chain

        total_text = get_interaction_turn_stream_text(event)
        pending_text = get_interaction_turn_stream_pending_text(event)
        if pending_text:
            window_index = get_interaction_turn_stream_observation_count(event) + 1
            update_interaction_turn_stream_buffer(
                event,
                total_text=total_text,
                pending_text=pending_text,
            )
            set_interaction_turn_stream_observation_count(
                event,
                window_index,
            )
            await self._observe_interaction_stream_window(
                event,
                observed_text=pending_text,
                total_text=total_text,
                window_index=window_index,
                observation_state=observation_state,
                is_final=True,
            )
        await self.wait_for_stream_observations(event)
        update_interaction_turn_stream_buffer(
            event,
            total_text=total_text,
            pending_text="",
        )

    @staticmethod
    def _update_interaction_turn_stream_buffer(
        event: AstrMessageEvent,
        *,
        chunk_text: str,
        observe: bool,
    ) -> None:
        current_total = get_interaction_turn_stream_text(event)
        current_pending = (
            get_interaction_turn_stream_pending_text(event) if observe else ""
        )
        next_total = f"{current_total}{chunk_text}"
        next_pending = f"{current_pending}{chunk_text}" if observe else ""
        update_interaction_turn_stream_buffer(
            event,
            total_text=next_total,
            pending_text=next_pending,
        )

    def _finalize_interaction_stream_output(self, event: AstrMessageEvent) -> None:
        mark_interaction_turn_core_streaming_result_consumed(event)
        self._record_visible_output(
            event,
            message_kind="core_stream",
            text=get_interaction_turn_stream_text(event),
        )
        self._materialize_finalized_turn(event)

    @staticmethod
    def _materialize_finalized_turn(event: AstrMessageEvent) -> None:
        turn_id = str(event.get_extra("_turn_id", "") or "").strip()
        visible_outputs = get_interaction_turn_visible_outputs(event)
        turn_state = get_interaction_turn_state(event)
        canonical_reply = build_interaction_memory_reply_from_visible_outputs(
            visible_outputs,
            turn_id=turn_id,
            utterances=turn_state.utterances if turn_state is not None else None,
        )
        if turn_id and canonical_reply:
            set_interaction_turn_finalized_material(
                event,
                {
                    "turn_id": turn_id,
                    "user_text": (event.message_str or "").strip(),
                    "assistant_text": canonical_reply,
                    "visible_outputs": visible_outputs,
                    "history_source": "interaction.turn.material",
                },
            )

    @staticmethod
    def _build_stream_observation_state() -> dict[str, Any]:
        return {
            "emitted": 0,
            "lock": asyncio.Lock(),
        }

    def _schedule_interaction_stream_observation(
        self,
        event: AstrMessageEvent,
        *,
        observed_text: str,
        total_text: str,
        window_index: int,
        observation_state: dict[str, Any],
        chain_type: str | None,
        is_final: bool,
    ) -> None:
        set_interaction_turn_stream_observation_count(event, window_index)
        task = asyncio.create_task(
            self._observe_interaction_stream_window(
                event,
                observed_text=observed_text,
                total_text=total_text,
                window_index=window_index,
                observation_state=observation_state,
                is_final=is_final,
            ),
            name=f"interaction_stream_observation_{event.get_platform_id()}_{window_index}",
        )
        add_interaction_turn_stream_observation_task(event, task)
        task.add_done_callback(
            lambda done_task: self._on_stream_observation_task_done(event, done_task)
        )

    def _schedule_core_stream_observation(
        self,
        event: AstrMessageEvent,
        *,
        observed_text: str,
        total_text: str,
        window_index: int,
        observation_state: dict[str, Any],
        chain_type: str | None,
        is_final: bool,
    ) -> None:
        self._schedule_interaction_stream_observation(
            event,
            observed_text=observed_text,
            total_text=total_text,
            window_index=window_index,
            observation_state=observation_state,
            chain_type=chain_type,
            is_final=is_final,
        )

    async def _observe_interaction_stream_window(
        self,
        event: AstrMessageEvent,
        *,
        observed_text: str,
        total_text: str,
        window_index: int,
        observation_state: dict[str, Any],
        is_final: bool,
    ) -> None:
        decision = await self._decide_stream_interjection(
            event,
            observed_text=observed_text,
            total_text=total_text,
            window_index=window_index,
            is_final=is_final,
        )
        if not decision.should_interject or not decision.reply:
            return
        turn_state = get_interaction_turn_state(event)
        lock = turn_state.stream_interjection_lock if turn_state is not None else None
        if not isinstance(lock, asyncio.Lock):
            lock = observation_state.get("lock")
            if not isinstance(lock, asyncio.Lock):
                lock = asyncio.Lock()
                observation_state["lock"] = lock
        async with lock:
            if (
                get_interaction_turn_stream_interjections_emitted(event)
                >= self.interaction_config.stream_interjection_max_per_turn
            ):
                return
            observation_state["emitted"] = (
                mark_interaction_turn_stream_interjection_emitted(event)
            )
            await self._emit_stream_interjection(
                event,
                decision.reply,
                window_index=window_index,
                reason=decision.reason,
            )

    async def _observe_core_stream_window(
        self,
        event: AstrMessageEvent,
        *,
        observed_text: str,
        total_text: str,
        window_index: int,
        observation_state: dict[str, Any],
        is_final: bool,
    ) -> None:
        await self._observe_interaction_stream_window(
            event,
            observed_text=observed_text,
            total_text=total_text,
            window_index=window_index,
            observation_state=observation_state,
            is_final=is_final,
        )

    def _on_stream_observation_task_done(
        self,
        event: AstrMessageEvent,
        task: asyncio.Task,
    ) -> None:
        remove_interaction_turn_stream_observation_task(event, task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            record_interaction_turn_stream_observation_failure(event, str(exc))
            self._record_stream_interjection_failure(
                event,
                reason="observation_task_failed",
                exception=exc,
                user_visible_action="continue_core_stream",
            )
            logger.error(
                "Interaction stream observation task failed: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )

    async def wait_for_stream_observations(self, event: AstrMessageEvent) -> None:
        tasks = get_interaction_turn_stream_observation_tasks(event)
        if not tasks:
            return
        await asyncio.gather(*list(tasks), return_exceptions=True)

    async def _decide_stream_interjection(
        self,
        event: AstrMessageEvent,
        *,
        observed_text: str,
        total_text: str,
        window_index: int,
        is_final: bool = False,
    ) -> StreamObservationDecision:
        if not self.interaction_config.stream_interjection_enabled:
            return StreamObservationDecision(reason="disabled")

        decision = await self._collect_stream_interjection_from_plugins(
            event,
            observed_text=observed_text,
            total_text=total_text,
            window_index=window_index,
            is_final=is_final,
        )
        if decision is not None:
            return decision
        return await self._decide_stream_interjection_with_model(
            event,
            observed_text=observed_text,
            total_text=total_text,
            window_index=window_index,
            is_final=is_final,
        )

    async def _decide_stream_interjection_with_model(
        self,
        event: AstrMessageEvent,
        *,
        observed_text: str,
        total_text: str,
        window_index: int,
        is_final: bool,
    ) -> StreamObservationDecision:
        if self.plugin_context is None:
            self._record_stream_interjection_failure(
                event,
                reason="plugin_context_unavailable",
                user_visible_action="continue_core_stream",
            )
            return StreamObservationDecision(reason="plugin_context_unavailable")
        provider = self.plugin_context.get_provider_by_id(
            self.interaction_config.decision_provider_id
        )
        if not isinstance(provider, Provider):
            self._record_stream_interjection_failure(
                event,
                reason="provider_unavailable",
                user_visible_action="continue_core_stream",
            )
            logger.warning(
                "Interaction stream interjection skipped: provider unavailable provider_id=%s",
                self.interaction_config.decision_provider_id,
            )
            return StreamObservationDecision(reason="provider_unavailable")

        prompt = await self._build_stream_interjection_prompt(
            event,
            observed_text=observed_text,
            total_text=total_text,
            window_index=window_index,
            is_final=is_final,
        )
        try:
            llm_resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=prompt,
                    system_prompt="",
                    temperature=self.interaction_config.decision_temperature,
                ),
                timeout=self.interaction_config.decision_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Interaction stream interjection skipped: reason=timeout platform_id=%s session_id=%s turn_id=%s window_index=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                window_index,
            )
            self._record_stream_interjection_failure(
                event,
                reason="timeout",
                user_visible_action="continue_core_stream",
            )
            return StreamObservationDecision(reason="timeout")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Interaction stream interjection skipped: reason=model_error platform_id=%s session_id=%s turn_id=%s window_index=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                window_index,
                exc,
                exc_info=True,
            )
            self._record_stream_interjection_failure(
                event,
                reason="model_error",
                exception=exc,
                user_visible_action="continue_core_stream",
            )
            return StreamObservationDecision(reason="model_error")

        payload = _extract_json_object(llm_resp.completion_text)
        decision = self._coerce_stream_interjection_decision(payload)
        if decision is None:
            logger.warning(
                "Interaction stream interjection skipped: reason=non_json raw=%s",
                llm_resp.completion_text,
            )
            self._record_stream_interjection_failure(
                event,
                reason="non_json",
                message=llm_resp.completion_text,
                user_visible_action="continue_core_stream",
            )
            return StreamObservationDecision(reason="non_json")
        if decision.reply and len(decision.reply) > 40:
            decision.reply = decision.reply[:40].rstrip("，,。.!！?？")
        return decision

    async def _collect_stream_interjection_from_plugins(
        self,
        event: AstrMessageEvent,
        *,
        observed_text: str,
        total_text: str,
        window_index: int,
        is_final: bool,
    ) -> StreamObservationDecision | None:
        if self.plugin_context is None:
            return None
        list_deciders = getattr(
            self.plugin_context,
            "list_interaction_stream_deciders",
            None,
        )
        if not callable(list_deciders):
            return None
        stream_view = self._build_stream_view(
            event,
            observed_text=observed_text,
            total_text=total_text,
            window_index=window_index,
            is_final=is_final,
        ).copy_read_only()
        for decider in list_deciders():
            try:
                payload = await decider.decide(
                    event,
                    self.plugin_context,
                    stream_view,
                )
            except Exception as exc:  # noqa: BLE001
                self._record_stream_interjection_failure(
                    event,
                    reason="plugin_error",
                    exception=exc,
                    message=getattr(decider, "plugin_id", "<unknown>"),
                    user_visible_action="continue_core_stream",
                )
                failures = event.get_extra("_interaction_stream_decider_failures", [])
                if not isinstance(failures, list):
                    failures = []
                failures.append(
                    {
                        "plugin_id": getattr(decider, "plugin_id", "<unknown>"),
                        "error": str(exc),
                    }
                )
                event.set_extra("_interaction_stream_decider_failures", failures)
                logger.warning(
                    "Interaction stream decider failed: plugin_id=%s error=%s",
                    getattr(decider, "plugin_id", "<unknown>"),
                    exc,
                    exc_info=True,
                )
                continue
            decision = self._coerce_stream_interjection_decision(payload)
            if decision is not None:
                return decision
            self._record_stream_interjection_failure(
                event,
                reason="invalid_plugin_payload",
                message=getattr(decider, "plugin_id", "<unknown>"),
                user_visible_action="continue_core_stream",
            )
        return None

    @staticmethod
    def _record_stream_interjection_failure(
        event: AstrMessageEvent,
        *,
        reason: str,
        exception: BaseException | None = None,
        message: str | None = None,
        user_visible_action: str = "continue_core_stream",
    ) -> None:
        record_interaction_turn_failure(
            event,
            stage="stream_interjection",
            reason=reason,
            exception=exception,
            message=message,
            user_visible_action=user_visible_action,
        )

    @staticmethod
    def _build_stream_view(
        event: AstrMessageEvent,
        *,
        observed_text: str,
        total_text: str,
        window_index: int,
        is_final: bool,
    ) -> InteractionStreamView:
        turn_state = get_interaction_turn_state(event)
        pending_text = (
            turn_state.stream_state.pending_text if turn_state is not None else ""
        )
        utterances = tuple(turn_state.utterances) if turn_state is not None else ()
        return InteractionStreamView(
            turn_id=str(event.get_extra("_turn_id", "") or ""),
            platform_id=event.get_platform_id(),
            session_id=event.unified_msg_origin,
            observed_text=observed_text,
            total_text=total_text,
            pending_text=pending_text,
            window_index=window_index,
            is_final=is_final,
            utterances=utterances,
            metadata={
                "stream_observation_count": (
                    turn_state.stream_state.observation_count
                    if turn_state is not None
                    else 0
                ),
            },
        )

    async def _build_stream_interjection_prompt(
        self,
        event: AstrMessageEvent,
        *,
        observed_text: str,
        total_text: str,
        window_index: int,
        is_final: bool,
    ) -> str:
        persona_payload: dict[str, Any] = {}
        memory_payload: dict[str, Any] = {}
        recent_messages: list[dict[str, Any]] = []
        turn_state = get_interaction_turn_state(event)
        cached_material = (
            turn_state.context_material if turn_state is not None else None
        )
        stream_state = turn_state.stream_state if turn_state is not None else None
        existing_utterances = (
            [
                {
                    "kind": utterance.kind,
                    "text": utterance.text,
                    "memory_relevant": utterance.memory_relevant,
                }
                for utterance in turn_state.utterances
                if utterance.kind != "stream_interjection" and utterance.text.strip()
            ]
            if turn_state is not None
            else []
        )
        if cached_material is not None:
            persona_payload = cached_material.persona_payload
            memory_payload = cached_material.memory_payload
            desired_window = self.interaction_config.memory_window_size
            recent_messages = list(cached_material.recent_messages)
            if desired_window > 0:
                recent_messages = recent_messages[-desired_window:]
        elif self.plugin_context is not None:
            try:
                if self.interaction_memory_store is None:
                    raise RuntimeError(
                        "Interaction stream prompt context requires interaction_memory_store"
                    )
                build_config = _build_decision_build_config(self.plugin_context, event)
                prompt_context_pack = await build_interaction_context_pack(
                    event,
                    self.plugin_context,
                    build_config,
                    self.interaction_memory_store,
                )
                persona_payload = extract_persona_payload(prompt_context_pack)
                memory_payload = extract_interaction_memory_payload(prompt_context_pack)
                recent_messages = extract_recent_messages(
                    prompt_context_pack,
                    self.interaction_config.memory_window_size,
                )
            except Exception as exc:  # noqa: BLE001
                event.set_extra(
                    "_interaction_stream_context_build_failed",
                    True,
                )
                event.set_extra(
                    "_interaction_stream_context_build_failure_reason",
                    str(exc),
                )
                logger.warning(
                    "Interaction stream context build failed: platform_id=%s session_id=%s turn_id=%s error=%s",
                    event.get_platform_id(),
                    event.session_id,
                    event.get_extra("_turn_id"),
                    exc,
                    exc_info=True,
                )
                raise
        payload = {
            "platform_id": event.get_platform_id(),
            "session_id": event.unified_msg_origin,
            "turn_id": event.get_extra("_turn_id"),
            "user_input": event.message_str,
            "persona": persona_payload,
            "interaction_memory": memory_payload,
            "recent_messages": recent_messages,
            "window_index": window_index,
            "is_final_window": is_final,
            "observed_core_stream_window": observed_text,
            "core_stream_so_far": (
                (stream_state.total_text if stream_state is not None else total_text)[
                    -800:
                ]
            ),
            "core_stream_pending": (
                stream_state.pending_text if stream_state is not None else ""
            ),
            "existing_turn_utterances": existing_utterances,
            "output_schema": {
                "should_interject": False,
                "reply": "不超过 20 字的口语短句，或 null",
                "reason": "简短原因",
            },
        }
        return (
            "你是 AstrBot interaction middleware 的流式观察器。\n"
            "核心执行层正在流式输出，你要判断此刻是否需要插一句拟人化短回复。\n"
            "只在用户可能需要等待确认、核心输出明显很长、或需要自然承接时插话。\n"
            "不要总结核心结果，不要声称任务完成，不要替核心执行工具。\n"
            "多数情况下 should_interject=false。\n"
            "必须只输出 JSON。\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

    async def _emit_stream_interjection(
        self,
        event: AstrMessageEvent,
        reply: str,
        *,
        window_index: int,
        reason: str,
    ) -> None:
        text = reply.strip()
        if not text:
            return
        message = MessageChain([Plain(text)])
        message.type = "interaction_stream_reply"
        platform_extras = {
            **self.build_platform_output_extras(
                event,
                message_kind="stream_interjection",
            ),
            "interaction_stream_reply": True,
            "stream_window_index": window_index,
        }
        await self._send_platform_message(
            message,
            event,
            platform_extras=platform_extras,
            record_send_operation=False,
        )
        visible_message_id = str(platform_extras.get("visible_message_id", "") or "")
        self._record_visible_output(
            event,
            message_kind="stream_interjection",
            text=text,
            delivered_message_ids=(
                [visible_message_id] if visible_message_id else None
            ),
            memory_relevant=False,
        )

    async def _deliver_core_reply(
        self,
        message: MessageChain,
        event: AstrMessageEvent,
    ) -> None:
        core_result_text = message.get_plain_text()
        immediate_reply = get_interaction_turn_immediate_reply(event)
        final_text = await finalize_response(
            event=event,
            plugin_context=self.plugin_context,
            config=self.interaction_config,
            core_result_text=core_result_text,
            immediate_reply=immediate_reply,
        )
        if (
            self.interaction_config.finalizer_mode == FinalizerMode.FORCE
            and final_text is None
            and event.get_extra("_interaction_finalizer_failed")
        ):
            record_interaction_turn_completion_failure(
                event,
                "finalizer_failed",
            )
            raise RuntimeError("Interaction finalizer failed")
        final_message = message
        if final_text:
            final_message = message.derive([Plain(final_text)])

        contributions = await self._collect_result_contributions(
            event,
            core_result=core_result_text,
            final_result=final_message.get_plain_text(),
            phase="final",
            candidate_message_kind="core_reply",
        )
        merged = merge_result_contributions(contributions)
        if merged.final_text_override is not None:
            final_message = message.derive([Plain(merged.final_text_override)])

        platform_extras = self.build_platform_output_base_extras(
            event,
            result_contribution=merged,
        )
        semantic_text = final_message.get_plain_text()
        (
            materialized_message,
            materialization,
        ) = await self.materialize_interaction_outbound_message(
            event,
            final_message,
            message_kind="core_reply",
            result_is_model_result=True,
        )
        delivered_message_ids = await self._deliver_visible_message(
            event,
            materialized_message,
            message_kind="core_reply",
            platform_extras=platform_extras,
            result_is_model_result=True,
            allow_segmented_reply=True,
            semantic_text=semantic_text,
        )
        self._record_visible_output(
            event,
            message_kind="core_reply",
            text=semantic_text,
            delivered_message_ids=delivered_message_ids,
            metadata=materialization,
        )
        self._materialize_finalized_turn(event)
        await self._persist_interaction_turn(event)

    @staticmethod
    def _is_core_final_model_result(event: AstrMessageEvent) -> bool:
        result = event.get_result()
        if result is None:
            return False
        return result.is_model_result()

    @staticmethod
    def _is_already_delivered_streaming_finish(event: AstrMessageEvent) -> bool:
        if not has_interaction_turn_core_streaming_result_consumed(event):
            return False
        result = event.get_result()
        if result is None:
            return False
        return result.result_content_type == ResultContentType.STREAMING_FINISH

    @staticmethod
    def _extract_observable_stream_text(chain: MessageChain) -> str:
        if chain.type in {"reasoning", "break"}:
            return ""
        if chain.type == "audio_chunk":
            for component in chain.chain:
                if isinstance(component, Json):
                    text = component.data.get("text")
                    return str(text or "")
            return ""
        return chain.get_plain_text()

    @staticmethod
    def _coerce_stream_interjection_decision(
        payload: Any,
    ) -> StreamObservationDecision | None:
        if isinstance(payload, StreamObservationDecision):
            return payload
        if not isinstance(payload, dict):
            return None
        reply = payload.get("reply")
        if reply is None:
            reply = payload.get("immediate_spoken_reply")
        reply_text = str(reply).strip() if reply is not None else None
        return StreamObservationDecision(
            should_interject=bool(payload.get("should_interject", False)),
            reply=reply_text,
            reason=str(payload.get("reason", "") or "plugin_decision"),
        )

    async def _collect_result_contributions(
        self,
        event: AstrMessageEvent,
        *,
        core_result: str | None,
        final_result: str | None,
        phase: str,
        candidate_message_kind: str,
    ) -> list[InteractionResultContribution]:
        if self.plugin_context is None:
            return []
        list_contributors = getattr(
            self.plugin_context,
            "list_interaction_result_contributors",
            None,
        )
        if not callable(list_contributors):
            return []

        view = InteractionResultView(
            turn_id=str(event.get_extra("_turn_id", "") or ""),
            platform_id=event.get_platform_id(),
            session_id=event.unified_msg_origin,
            decision=(
                decision.to_dict()
                if (decision := get_interaction_decision(event)) is not None
                else None
            ),
            immediate_reply=get_interaction_turn_immediate_reply(event),
            core_result=core_result,
            final_result=final_result,
            visible_outputs=self._snapshot_result_visible_outputs(event),
            utterances=self._snapshot_result_utterances(event),
            turn_material_snapshot=get_interaction_turn_finalized_material(event),
            final_candidate_material=self._build_result_final_candidate_material(
                event,
                final_result=final_result,
                message_kind=candidate_message_kind,
            ),
            finalized_turn_material=get_interaction_turn_finalized_material(event),
            metadata={
                "phase": phase,
                "message_kind": candidate_message_kind,
                "is_immediate": phase == "immediate",
                "is_final": phase == "final",
            },
        )
        contributions: list[InteractionResultContribution] = []
        for contributor in list_contributors():
            try:
                payload = await contributor.collect(
                    event,
                    self.plugin_context,
                    view.copy_read_only(),
                )
            except Exception as exc:  # noqa: BLE001
                failures = event.get_extra(
                    "_interaction_result_contributor_failures", []
                )
                if not isinstance(failures, list):
                    failures = []
                failures.append(
                    {
                        "plugin_id": getattr(contributor, "plugin_id", "<unknown>"),
                        "error": str(exc),
                    }
                )
                event.set_extra("_interaction_result_contributor_failures", failures)
                logger.warning(
                    "Interaction result contributor failed: plugin_id=%s error=%s",
                    getattr(contributor, "plugin_id", "<unknown>"),
                    exc,
                    exc_info=True,
                )
                continue
            if isinstance(payload, InteractionResultContribution):
                contributions.append(payload)
        contributions.sort(key=lambda item: (item.priority, item.plugin_id))
        return contributions

    @staticmethod
    def _build_result_final_candidate_material(
        event: AstrMessageEvent,
        *,
        final_result: str | None,
        message_kind: str,
    ) -> dict[str, Any] | None:
        turn_id = str(event.get_extra("_turn_id", "") or "").strip()
        assistant_text = (final_result or "").strip()
        if not turn_id or not assistant_text:
            return None
        visible_outputs = [
            *get_interaction_turn_visible_outputs(event),
            {
                "turn_id": turn_id,
                "kind": message_kind,
                "text": assistant_text,
                "memory_relevant": True,
            },
        ]
        return {
            "turn_id": turn_id,
            "user_text": (event.message_str or "").strip(),
            "assistant_text": assistant_text,
            "visible_outputs": visible_outputs,
            "history_source": "interaction.turn.final_candidate",
        }

    @staticmethod
    def _snapshot_result_visible_outputs(event: AstrMessageEvent) -> tuple[Any, ...]:
        turn_state = get_interaction_turn_state(event)
        if turn_state is not None:
            return tuple(dict(output) for output in turn_state.visible_outputs)
        return ()

    @staticmethod
    def _snapshot_result_utterances(event: AstrMessageEvent) -> tuple[Any, ...]:
        turn_state = get_interaction_turn_state(event)
        if turn_state is not None:
            return tuple(turn_state.utterances)
        return ()

    def build_platform_output_extras(
        self,
        event: AstrMessageEvent,
        *,
        message_kind: str,
        result_contribution: InteractionResultContribution | None = None,
    ) -> dict[str, Any]:
        extras = self.build_platform_output_base_extras(
            event,
            result_contribution=result_contribution,
        )
        visible_message_id = self._next_visible_message_id(event, message_kind)
        extras.update(
            {
                "turn_id": event.get_extra("_turn_id"),
                "visible_message_id": visible_message_id,
                "message_kind": message_kind,
                "composite_message_id": visible_message_id,
            }
        )
        return {key: value for key, value in extras.items() if value is not None}

    def build_platform_output_base_extras(
        self,
        event: AstrMessageEvent,
        *,
        result_contribution: InteractionResultContribution | None = None,
    ) -> dict[str, Any]:
        extras: dict[str, Any] = {}
        hints = event.get_extra("_interaction_plugin_hints", {})
        if isinstance(hints, dict):
            hinted_extras = hints.get("platform_extras", {})
            if isinstance(hinted_extras, dict):
                extras.update(hinted_extras)
        if result_contribution is not None:
            extras.update(result_contribution.platform_extras)
            if result_contribution.client_objects:
                extras["client_objects"] = list(result_contribution.client_objects)
            if result_contribution.metadata:
                extras["metadata"] = dict(result_contribution.metadata)
        return extras

    async def materialize_interaction_outbound_message(
        self,
        event: AstrMessageEvent,
        message: MessageChain,
        *,
        message_kind: str,
        result_is_model_result: bool = False,
    ) -> tuple[MessageChain, dict[str, Any]]:
        self._refresh_outbound_materialization_config(event)
        materialization: dict[str, Any] = {
            "message_kind": message_kind,
            "semantic_text": message.get_plain_text(),
            "delivered_as": "text",
        }
        materialized = self._apply_interaction_reply_prefix(event, message)
        materialized, reasoning_metadata = self._apply_interaction_reasoning_display(
            event,
            materialized,
        )
        materialization.update(reasoning_metadata)
        materialized, tts_metadata = await self._apply_interaction_tts(
            event,
            materialized,
            result_is_model_result=result_is_model_result,
        )
        materialization.update(tts_metadata)
        if tts_metadata.get("delivered_as") == "record":
            return materialized, materialization
        materialized, t2i_metadata = await self._apply_interaction_t2i(
            event,
            materialized,
        )
        materialization.update(t2i_metadata)
        return materialized, materialization

    async def materialize_immediate_interaction_outbound_message(
        self,
        event: AstrMessageEvent,
        message: MessageChain,
    ) -> tuple[MessageChain, dict[str, Any]]:
        self._refresh_outbound_materialization_config(event)
        materialization: dict[str, Any] = {
            "message_kind": "immediate_reply",
            "semantic_text": message.get_plain_text(),
            "delivered_as": "text",
        }
        materialized = self._apply_interaction_reply_prefix(event, message)
        materialized, tts_metadata = await self._apply_interaction_tts(
            event,
            materialized,
            result_is_model_result=True,
        )
        materialization.update(tts_metadata)
        return materialized, materialization

    def _apply_interaction_reply_prefix(
        self,
        event: AstrMessageEvent,
        message: MessageChain,
    ) -> MessageChain:
        del event
        if not self.reply_prefix:
            return message
        chain = list(message.chain)
        for index, comp in enumerate(chain):
            if isinstance(comp, Plain):
                chain[index] = Plain(self.reply_prefix + comp.text)
                return message.derive(chain)
        return message

    def _apply_interaction_reasoning_display(
        self,
        event: AstrMessageEvent,
        message: MessageChain,
    ) -> tuple[MessageChain, dict[str, Any]]:
        reasoning_content = str(event.get_extra("_llm_reasoning_content") or "")
        if not self.show_reasoning or not reasoning_content.strip():
            return message, {}
        chain = list(message.chain)
        if event.get_platform_name() == "lark":
            chain.insert(
                0,
                Json(
                    data={
                        "type": "lark_collapsible_panel_reasoning",
                        "title": "Thinking",
                        "expanded": False,
                        "content": reasoning_content,
                    },
                ),
            )
        else:
            chain.insert(0, Plain(f"思考: {reasoning_content}\n"))
        return message.derive(chain), {"reasoning_displayed": True}

    async def _apply_interaction_tts(
        self,
        event: AstrMessageEvent,
        message: MessageChain,
        *,
        result_is_model_result: bool,
    ) -> tuple[MessageChain, dict[str, Any]]:
        tts_settings = self._get_tts_settings(event)
        should_try_tts = (
            bool(tts_settings.get("enable"))
            and result_is_model_result
            and await SessionServiceManager.should_process_tts_request(event)
            and random.random() <= self.tts_trigger_probability
        )
        if not should_try_tts:
            return message, {}
        try:
            tts_provider = resolve_tts_provider(
                self.plugin_context,
                event,
                stage="interaction.outbound_tts",
            )
        except VoiceServiceError as exc:
            self._record_outbound_materialization_failure(
                event,
                "tts",
                exc.reason,
            )
            raise

        new_chain = []
        converted: list[dict[str, Any]] = []
        for comp in message.chain:
            if not isinstance(comp, Plain) or len(comp.text) <= 1:
                new_chain.append(comp)
                continue
            try:
                logger.info("Interaction TTS request: %s", comp.text)
                result = await synthesize_text(
                    self.plugin_context,
                    event,
                    comp.text,
                    provider=tts_provider,
                    stage="interaction.outbound_tts",
                    use_file_service=bool(tts_settings.get("use_file_service")),
                    callback_api_base=str(
                        self._get_config_value("callback_api_base", "", event=event)
                    ),
                    require_file_registration_config=True,
                )
                logger.info("Interaction TTS result: %s", result.audio_path)
                new_chain.append(
                    Record(
                        file=result.delivered_file,
                        url=result.delivered_file,
                        text=result.text,
                    )
                )
                converted.append(
                    {
                        "tts_source_text": result.text,
                        "tts_audio_path": result.audio_path,
                        "tts_audio_url": result.audio_url,
                        "tts_provider_id": result.provider_id,
                    }
                )
                if bool(tts_settings.get("dual_output")):
                    new_chain.append(comp)
            except VoiceServiceError as exc:
                self._record_outbound_materialization_failure(
                    event,
                    "tts",
                    exc.reason,
                )
                logger.error(traceback.format_exc())
                raise
        if not converted:
            return message.derive(new_chain), {}
        return (
            message.derive(new_chain),
            {
                "delivered_as": "record",
                "tts": converted,
            },
        )

    async def _apply_interaction_t2i(
        self,
        event: AstrMessageEvent,
        message: MessageChain,
    ) -> tuple[MessageChain, dict[str, Any]]:
        use_t2i = (
            message.use_t2i_
            if message.use_t2i_ is not None
            else bool(self._get_config_value("t2i", False, event=event))
        )
        if not use_t2i:
            return message, {}
        parts: list[str] = []
        for comp in message.chain:
            if isinstance(comp, Plain):
                parts.append("\n\n" + comp.text)
            else:
                break
        plain_str = "".join(parts)
        if not plain_str or len(plain_str) <= self.t2i_word_threshold:
            return message, {}
        render_start = time.time()
        try:
            url = await html_renderer.render_t2i(
                plain_str,
                return_url=True,
                use_network=self.t2i_use_network,
                template_name=self.t2i_active_template,
            )
        except BaseException as exc:
            self._record_outbound_materialization_failure(event, "t2i", str(exc))
            logger.error("Interaction t2i failed.", exc_info=True)
            raise
        if time.time() - render_start > 3:
            logger.warning("Interaction t2i rendering took more than 3 seconds.")
        if not url:
            self._record_outbound_materialization_failure(
                event,
                "t2i",
                "empty_image_url",
            )
            raise RuntimeError("Interaction t2i returned empty image URL")
        delivered_url = await self._register_interaction_t2i_file_if_needed(event, url)
        image_url = delivered_url or url
        if image_url.startswith("http"):
            image = Image.fromURL(image_url)
        else:
            image = Image.fromFileSystem(image_url)
        return (
            message.derive([image]),
            {
                "delivered_as": "image",
                "t2i_source_text": plain_str,
                "t2i_image_url": image_url,
            },
        )

    @staticmethod
    def _record_outbound_materialization_failure(
        event: AstrMessageEvent,
        stage: str,
        reason: str,
    ) -> None:
        event.set_extra("_interaction_outbound_materialization_failed", True)
        event.set_extra("_interaction_outbound_materialization_stage", stage)
        event.set_extra("_interaction_outbound_materialization_failure_reason", reason)
        record_interaction_turn_failure(
            event,
            stage="outbound_materialization",
            reason=reason,
            user_visible_action="none",
        )
        record_interaction_turn_completion_failure(
            event,
            f"outbound_materialization:{stage}:{reason}",
        )

    async def _register_interaction_t2i_file_if_needed(
        self,
        event: AstrMessageEvent,
        url: str,
    ) -> str | None:
        callback_api_base = self._get_config_value(
            "callback_api_base",
            "",
            event=event,
        )
        if (
            url.startswith("http")
            or not self._get_config_value(
                "t2i_use_file_service",
                False,
                event=event,
            )
            or not callback_api_base
        ):
            return None
        token = await file_token_service.register_file(url)
        registered_url = f"{callback_api_base}/api/file/{token}"
        logger.debug("Interaction t2i file registered: %s", registered_url)
        return registered_url

    @staticmethod
    def _next_visible_message_id(event: AstrMessageEvent, message_kind: str) -> str:
        return next_interaction_turn_visible_message_id(event, message_kind)

    async def _send_platform_message(
        self,
        message: MessageChain,
        event: AstrMessageEvent,
        *,
        platform_extras: dict[str, Any],
        record_send_operation: bool = True,
    ) -> None:
        await event.send_interaction_message(
            message=message,
            platform_extras=platform_extras,
            record_send_operation=record_send_operation,
        )

    async def _deliver_visible_message(
        self,
        event: AstrMessageEvent,
        message: MessageChain,
        *,
        message_kind: str,
        platform_extras: dict[str, Any] | None = None,
        record_send_operation: bool = True,
        result_is_model_result: bool = False,
        allow_segmented_reply: bool = False,
        semantic_text: str | None = None,
    ) -> list[str]:
        """Send a visible message and record it as a turn utterance.

        Responsibilities are split at the boundary:

        - InteractionUtterance (semantic layer): defines *what* was said,
          its kind, memory relevance, and turn membership.  Produced by
          outer methods (capture_message_chain, capture_streaming, etc.)
          via `append_interaction_turn_visible_output`.

        - deliver_message_chain (physical layer): decides *how* to split
          and deliver the chain to the platform adapter.  It is unaware of
          turn state, utterances, or memory semantics.
        """
        base_extras = self._strip_message_identity_extras(platform_extras or {})
        delivered_message_ids: list[str] = []
        semantic_text = (
            message.get_plain_text() if semantic_text is None else semantic_text
        )

        async def _send(chain: MessageChain) -> None:
            output_extras = {
                **base_extras,
                **self.build_platform_output_extras(
                    event,
                    message_kind=message_kind,
                ),
                "semantic_text": semantic_text,
            }
            await self._send_platform_message(
                chain,
                event,
                platform_extras=output_extras,
                record_send_operation=record_send_operation,
            )
            visible_message_id = str(output_extras.get("visible_message_id", "") or "")
            if visible_message_id:
                delivered_message_ids.append(visible_message_id)

        sent = await deliver_message_chain(
            event,
            message,
            send_message=_send,
            platform_settings=self.platform_settings,
            result_is_model_result=result_is_model_result,
            allow_segmented_reply=allow_segmented_reply,
        )
        return delivered_message_ids if sent else []

    @staticmethod
    def _strip_message_identity_extras(
        platform_extras: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            key: value
            for key, value in platform_extras.items()
            if key
            not in {
                "turn_id",
                "visible_message_id",
                "message_kind",
                "composite_message_id",
            }
        }

    @staticmethod
    def _record_visible_output(
        event: AstrMessageEvent,
        *,
        message_kind: str,
        text: str | None,
        delivered_message_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        memory_relevant: bool = True,
    ) -> None:
        append_interaction_turn_visible_output(
            event,
            message_kind=message_kind,
            text=text,
            message_id=(delivered_message_ids[0] if delivered_message_ids else None),
            delivered_message_ids=delivered_message_ids,
            metadata=metadata,
            memory_relevant=memory_relevant,
        )

    async def _persist_interaction_turn(
        self,
        event: AstrMessageEvent,
    ) -> None:
        if is_interaction_turn_completed(event):
            return
        if self._persist_callback is not None:
            await self._persist_callback(event)
            return

        event.set_extra("_interaction_persist_callback_missing", True)
        event.set_extra("_interaction_turn_finalization_failed", True)
        event.set_extra(
            "_interaction_turn_finalization_failure_reason",
            "missing_persist_callback",
        )
        record_interaction_turn_completion_failure(event, "missing_persist_callback")
        logger.error(
            "Interaction turn persist requested without middleware callback: platform_id=%s session_id=%s turn_id=%s",
            event.get_platform_id(),
            event.session_id,
            event.get_extra("_turn_id"),
        )

    @staticmethod
    def _classify_outbound_message(
        event: AstrMessageEvent,
        message: MessageChain,
        is_immediate: bool,
    ) -> str:
        if is_immediate:
            return "immediate_reply"
        if InteractionOutputController._is_already_delivered_streaming_finish(event):
            return "streaming_finish_marker"
        if has_interaction_turn_core_final_result_consumed(event):
            return "suppressed_duplicate_final"

        result = event.get_result()
        result_is_model = bool(result and result.is_model_result())
        decision = get_interaction_decision(event)
        route_mode = decision.route_mode if decision is not None else None
        streamed = has_interaction_turn_core_streaming_result_consumed(event)
        streaming_active = is_interaction_turn_core_streaming_active(event)

        if result_is_model:
            return "core_final_model_result"
        if (
            route_mode in {RouteMode.HYBRID, RouteMode.DELEGATE_TO_CORE}
            and streamed
            and not streaming_active
            and message.type
            not in {"agent_stats", "tool_call", "interaction_stream_reply"}
        ):
            return "core_final_followup_after_stream"
        return "passthrough"
