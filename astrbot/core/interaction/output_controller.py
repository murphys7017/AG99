from __future__ import annotations

import asyncio
import random
import time
import traceback
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from astrbot import logger
from astrbot.core import file_token_service, html_renderer
from astrbot.core.message.components import Image, Json, Plain, Record
from astrbot.core.message.message_chain_delivery import deliver_message_chain
from astrbot.core.message.message_chain_transforms import (
    replace_leading_plain_components,
    replace_plain_text_preserving_components,
)
from astrbot.core.message.message_event_result import MessageChain, ResultContentType
from astrbot.core.output_lifecycle import PreOutputProcessor, TurnDeliveryCoordinator
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.platform_metadata import supports_personal_runtime
from astrbot.core.star.session_llm_manager import SessionServiceManager
from astrbot.core.voice import (
    TTSState,
    VoiceServiceError,
    build_tts_delivery_metadata,
    synthesize_text,
)

from .assistant_artifacts import serialize_assistant_message_chain
from .config import load_interaction_agent_config
from .contributors import (
    InteractionOutputDraft,
    InteractionResultContribution,
    InteractionResultView,
    InteractionStreamView,
    merge_result_contributions,
)
from .core_bridge import get_interaction_route_decision
from .expression_agent import (
    PersonaExpressionIntent,
    PersonaExpressionRequest,
    PersonaExpressionResult,
)
from .output_modes import (
    CORE_OUTPUT_DELIVERY_EXTRA_KEY,
    PLUGIN_OUTPUT_LAST_KIND_EXTRA_KEY,
    PLUGIN_OUTPUT_LAST_MODE_EXTRA_KEY,
    CoreOutputDelivery,
    OutputOrigin,
    PluginOutputMode,
    temporary_output_origin,
)
from .turn_state import (
    InteractionFinalOutputStatus,
    add_interaction_turn_stream_observation_task,
    append_interaction_turn_assistant_artifacts,
    append_interaction_turn_visible_output,
    build_interaction_turn_reply,
    consume_interaction_turn_finalization_pending,
    finish_interaction_turn_final_output,
    get_interaction_turn_assistant_artifacts,
    get_interaction_turn_config,
    get_interaction_turn_finalized_material,
    get_interaction_turn_immediate_reply,
    get_interaction_turn_state,
    get_interaction_turn_stream_interjections_emitted,
    get_interaction_turn_stream_observation_count,
    get_interaction_turn_stream_observation_tasks,
    get_interaction_turn_stream_pending_text,
    get_interaction_turn_stream_text,
    get_interaction_turn_visible_outputs,
    has_interaction_turn_core_streaming_result_consumed,
    has_interaction_turn_final_output_claimed,
    is_interaction_turn_completed,
    is_interaction_turn_core_streaming_active,
    is_interaction_turn_finalization_deferred,
    mark_interaction_turn_cancelled,
    mark_interaction_turn_core_streaming_result_consumed,
    mark_interaction_turn_finalization_pending,
    mark_interaction_turn_personal_emitted,
    mark_interaction_turn_stream_interjection_emitted,
    next_interaction_turn_output_segment_id,
    next_interaction_turn_visible_message_id,
    record_interaction_turn_completion_failure,
    record_interaction_turn_failure,
    record_interaction_turn_stream_observation_failure,
    record_interaction_turn_visible_message_fingerprint,
    remove_interaction_turn_stream_observation_task,
    reserve_interaction_turn_final_output,
    set_interaction_turn_core_streaming_active,
    set_interaction_turn_finalized_material,
    set_interaction_turn_immediate_reply,
    set_interaction_turn_stream_observation_count,
    update_interaction_turn_stream_buffer,
)
from .types import InteractionAgentConfig, InteractionRouteMode
from .visible_message_fingerprint import fingerprint_visible_message

PLUGIN_OUTPUT_TRANSACTION_ACTIVE_EXTRA_KEY = (
    "_interaction_plugin_output_transaction_active"
)
PLUGIN_OUTPUT_TRANSACTION_START_EXTRA_KEY = (
    "_interaction_plugin_output_transaction_start"
)
PLUGIN_OUTPUT_TRANSACTION_ARTIFACTS_EXTRA_KEY = (
    "_interaction_plugin_output_transaction_assistant_artifacts"
)


def _merge_runtime_config(
    base: Mapping[str, Any], override: Mapping[str, Any]
) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge_runtime_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def _visible_message_ids_from_extras(extras: Mapping[str, Any]) -> list[str]:
    visible_message_id = str(extras.get("visible_message_id", "") or "").strip()
    return [visible_message_id] if visible_message_id else []


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
        platform_settings: dict[str, Any] | None = None,
        persist_callback: (Callable[[AstrMessageEvent], Awaitable[None]] | None) = None,
        visible_reply_renderer: (
            Callable[
                [AstrMessageEvent, PersonaExpressionRequest],
                Awaitable[PersonaExpressionResult],
            ]
            | None
        ) = None,
        core_reply_handler: (
            Callable[[MessageChain, AstrMessageEvent], Awaitable[None]] | None
        ) = None,
        lifecycle_callback: (
            Callable[[AstrMessageEvent, str, dict[str, Any] | None], Awaitable[None]]
            | None
        ) = None,
        pre_output_processor: PreOutputProcessor | None = None,
        delivery_coordinator: TurnDeliveryCoordinator | None = None,
    ) -> None:
        self.plugin_context = plugin_context
        self.interaction_config = interaction_config or InteractionAgentConfig()
        self.platform_settings = platform_settings or {}
        self._persist_callback = persist_callback
        self.visible_reply_renderer = visible_reply_renderer
        self.core_reply_handler = core_reply_handler
        self.lifecycle_callback = lifecycle_callback
        self.pre_output_processor = pre_output_processor or PreOutputProcessor()
        self.delivery_coordinator = delivery_coordinator or TurnDeliveryCoordinator()
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
        if event is not None:
            event_config = event.get_extra("_astrbot_config")
            if isinstance(event_config, Mapping):
                plugin_config = self._get_plugin_runtime_config(event)
                if isinstance(plugin_config, Mapping):
                    return _merge_runtime_config(event_config, plugin_config)
                return event_config
        if self.plugin_context is None:
            return None
        plugin_config = self._get_plugin_runtime_config(event)
        if isinstance(plugin_config, Mapping):
            return plugin_config
        return None

    def _get_plugin_runtime_config(
        self,
        event: AstrMessageEvent | None = None,
    ) -> Any:
        if self.plugin_context is None:
            return None
        get_config = getattr(self.plugin_context, "get_config", None)
        if not callable(get_config):
            return None
        if event is not None:
            return get_config(umo=event.unified_msg_origin)
        return get_config()

    def _get_interaction_config(
        self,
        event: AstrMessageEvent | None = None,
    ) -> InteractionAgentConfig:
        if event is not None:
            interaction_config = get_interaction_turn_config(event)
            if interaction_config is not None:
                return interaction_config
        runtime_config = self._get_runtime_config(event)
        if isinstance(runtime_config, Mapping):
            return load_interaction_agent_config(runtime_config)
        return self.interaction_config

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

    async def emit_failure_reply(
        self,
        reply: str,
        event: AstrMessageEvent,
    ) -> bool:
        if not await reserve_interaction_turn_final_output(event):
            return False
        try:
            await self.emit_immediate_spoken_reply(
                PersonaExpressionResult(spoken_reply=reply),
                event,
            )
            self._materialize_finalized_turn(event)
            await self._persist_interaction_turn(event)
        except BaseException:
            await finish_interaction_turn_final_output(
                event,
                InteractionFinalOutputStatus.FAILED,
            )
            raise
        await finish_interaction_turn_final_output(
            event,
            InteractionFinalOutputStatus.DELIVERED,
        )
        return True

    async def emit_immediate_spoken_reply(
        self,
        result: PersonaExpressionResult,
        event: AstrMessageEvent,
    ) -> None:
        reply = (result.spoken_reply or "").strip()
        if not reply:
            return
        set_interaction_turn_immediate_reply(event, reply)
        event.set_extra("_interaction_emitting_immediate_reply", True)
        try:
            with temporary_output_origin(event, OutputOrigin.CORE.value):
                await self.capture_message_chain(
                    MessageChain(
                        [
                            Plain(reply),
                            *self._persona_tool_attachment_components(result),
                        ]
                    ),
                    event,
                    prepared_expression=result,
                )
        finally:
            event.set_extra("_interaction_emitting_immediate_reply", False)

    async def capture_message_chain(
        self,
        message: MessageChain | None,
        event: AstrMessageEvent,
        *,
        prepared_expression: PersonaExpressionResult | None = None,
    ) -> None:
        if message is None:
            await self.capture_visible_completion(event)
            return

        is_immediate = bool(event.get_extra("_interaction_emitting_immediate_reply"))
        outbound_kind = self._classify_outbound_message(event, message, is_immediate)
        if is_immediate:
            semantic_text = message.get_plain_text()
            message_id = self._next_output_segment_id(event, "immediate_reply")
            contributions = await self._collect_result_contributions(
                event,
                core_result=None,
                final_result=semantic_text,
                phase="immediate",
                candidate_message_kind="immediate_reply",
                candidate_message_id=message_id,
                effect_calls=(
                    prepared_expression.effect_calls
                    if prepared_expression is not None
                    else ()
                ),
            )
            merged = merge_result_contributions(contributions)
            if merged.final_text_override is not None:
                message = replace_plain_text_preserving_components(
                    message,
                    merged.final_text_override,
                )
                semantic_text = message.get_plain_text()
                set_interaction_turn_immediate_reply(event, semantic_text)
            (
                message,
                materialization,
            ) = await self.materialize_immediate_interaction_outbound_message(
                event,
                message,
                message_id=message_id,
            )
            delivered_message_ids = await self._deliver_visible_message(
                event,
                message,
                message_kind="immediate_reply",
                platform_extras=self.build_platform_output_base_extras(
                    event,
                    result_contribution=merged,
                ),
                output_segment_id=message_id,
                record_send_operation=False,
                allow_segmented_reply=False,
                semantic_text=semantic_text,
            )
            self._record_visible_output(
                event,
                message_kind="immediate_reply",
                text=semantic_text,
                message_id=message_id,
                delivered_message_ids=delivered_message_ids,
                metadata=materialization,
            )
            return

        if outbound_kind == "streaming_finish_marker":
            if not await reserve_interaction_turn_final_output(event):
                return
            logger.warning(
                "Interaction streaming finish marker skipped after streaming delivery: platform_id=%s session_id=%s turn_id=%s final_length=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                len(message.get_plain_text()),
            )
            try:
                self._materialize_finalized_turn(event)
                await self._persist_interaction_turn(event)
            except BaseException:
                await finish_interaction_turn_final_output(
                    event,
                    InteractionFinalOutputStatus.FAILED,
                )
                raise
            await finish_interaction_turn_final_output(
                event,
                InteractionFinalOutputStatus.DELIVERED,
            )
            return

        if outbound_kind in {"core_progress", "passthrough"}:
            is_progress = outbound_kind == "core_progress"
            semantic_text = message.get_plain_text()
            message_id = self._next_output_segment_id(event, outbound_kind)
            (
                message,
                materialization,
            ) = await self.materialize_interaction_outbound_message(
                event,
                message,
                message_kind=outbound_kind,
                result_is_model_result=False,
                message_id=message_id,
            )
            delivered_message_ids = await self._deliver_visible_message(
                event,
                message,
                message_kind=outbound_kind,
                output_segment_id=message_id,
                allow_segmented_reply=True,
                semantic_text=semantic_text,
            )
            self._record_visible_output(
                event,
                message_kind=outbound_kind,
                text=semantic_text,
                message_id=message_id,
                delivered_message_ids=delivered_message_ids,
                metadata=materialization,
                memory_relevant=not is_progress,
            )
            if is_progress:
                return
            self._materialize_finalized_turn(event)
            await self._persist_interaction_turn(event)
            return

        if outbound_kind == "suppressed_duplicate_final":
            return

        if not await reserve_interaction_turn_final_output(event):
            return
        full_message = self._get_full_core_final_message(event, message)
        try:
            if self.core_reply_handler is not None:
                await self.core_reply_handler(full_message, event)
            else:
                await self._deliver_core_reply(full_message, event)
        except BaseException:
            await finish_interaction_turn_final_output(
                event,
                InteractionFinalOutputStatus.FAILED,
            )
            raise
        final_status = (
            InteractionFinalOutputStatus.SUPPRESSED
            if event.get_extra("_interaction_pipeline_output_suppressed", False)
            else InteractionFinalOutputStatus.DELIVERED
        )
        await finish_interaction_turn_final_output(event, final_status)

    async def capture_plugin_output(
        self,
        message: MessageChain | None,
        event: AstrMessageEvent,
        *,
        mode: str = PluginOutputMode.DIRECT.value,
        finalize: bool = True,
        platform_extras: dict[str, Any] | None = None,
    ) -> None:
        """Entry point for plugin-origin output through the Output Runtime.

        Two modes are supported:

        * **direct** — deliver the message as-is.
        * **persona** — extract plain text, pass it through the unified
          visible-reply persona layer, then deliver the rewritten text.

        ``finalize=False`` is for a visible progress update that will be
        followed by another output in the same interaction turn.
        """
        if message is None:
            return

        event.set_extra(PLUGIN_OUTPUT_LAST_MODE_EXTRA_KEY, mode)
        resolved_kind = "plugin_direct"
        resolved_mode = PluginOutputMode(mode)

        if resolved_mode == PluginOutputMode.PERSONA:
            plain = message.get_plain_text().strip()
            if plain:
                result = await self._render_visible_reply(
                    event,
                    PersonaExpressionRequest(
                        source_text=plain,
                        preserve_facts=True,
                        intent=PersonaExpressionIntent(
                            source="plugin_output",
                            phase="plugin",
                        ),
                    ),
                )
                if result.effect_calls:
                    event.set_extra(
                        "_interaction_plugin_output_effect_calls",
                        list(result.effect_calls),
                    )
                message = replace_plain_text_preserving_components(
                    message,
                    result.spoken_reply,
                )
                resolved_kind = "plugin_persona"
            else:
                resolved_kind = "plugin_direct"

        event.set_extra(PLUGIN_OUTPUT_LAST_KIND_EXTRA_KEY, resolved_kind)
        semantic_text = message.get_plain_text()
        message_id = self._next_output_segment_id(event, resolved_kind)
        deferred_by_transaction = finalize and self._begin_plugin_output_transaction(
            event
        )
        if finalize and resolved_kind == "plugin_direct":
            self._record_plugin_assistant_artifacts(
                event,
                message,
                deferred_by_transaction=deferred_by_transaction,
            )

        (
            message,
            materialization,
        ) = await self.materialize_interaction_outbound_message(
            event,
            message,
            message_kind=resolved_kind,
            result_is_model_result=False,
            message_id=message_id,
        )
        delivered_message_ids = await self._deliver_visible_message(
            event,
            message,
            message_kind=resolved_kind,
            platform_extras=platform_extras,
            output_segment_id=message_id,
            allow_segmented_reply=True,
            semantic_text=semantic_text,
        )
        self._record_visible_output(
            event,
            message_kind=resolved_kind,
            text=semantic_text,
            message_id=message_id,
            delivered_message_ids=delivered_message_ids,
            metadata=materialization,
            memory_relevant=finalize and not deferred_by_transaction,
        )
        if not finalize or deferred_by_transaction:
            return
        self._materialize_finalized_turn(event)
        await self._persist_interaction_turn(event)

    async def capture_plugin_streaming(
        self,
        generator: AsyncGenerator[MessageChain, None],
        event: AstrMessageEvent,
        *,
        mode: str = PluginOutputMode.DIRECT.value,
        use_fallback: bool = False,
    ) -> None:
        """Deliver plugin-origin streaming output without core stream semantics.

        Persona rewriting needs the complete semantic text before it can form one
        coherent reply. Therefore an explicitly persona-routed plugin stream is
        buffered and delivered through ``capture_plugin_output`` once, while
        direct plugin streams retain their regular low-latency delivery path.
        """
        resolved_mode = PluginOutputMode(mode)
        if resolved_mode is PluginOutputMode.PERSONA:
            stream_text_parts: list[str] = []
            async for chain in generator:
                chunk_text = self._extract_observable_stream_text(chain)
                if chunk_text:
                    stream_text_parts.append(chunk_text)

            text = "".join(stream_text_parts).strip()
            event.set_extra(PLUGIN_OUTPUT_LAST_MODE_EXTRA_KEY, resolved_mode.value)
            event.set_extra(PLUGIN_OUTPUT_LAST_KIND_EXTRA_KEY, "plugin_persona")
            if text:
                await self.capture_plugin_output(
                    MessageChain([Plain(text)]),
                    event,
                    mode=resolved_mode.value,
                    finalize=True,
                )
            return

        resolved_kind = "plugin_direct"
        event.set_extra(PLUGIN_OUTPUT_LAST_MODE_EXTRA_KEY, resolved_mode.value)
        event.set_extra(PLUGIN_OUTPUT_LAST_KIND_EXTRA_KEY, resolved_kind)
        deferred_by_transaction = self._begin_plugin_output_transaction(event)
        stream_text_parts: list[str] = []
        message_id = self._next_output_segment_id(event, resolved_kind)

        async def _observe_plugin_stream() -> AsyncGenerator[MessageChain, None]:
            async for chain in generator:
                chunk_text = self._extract_observable_stream_text(chain)
                if chunk_text:
                    stream_text_parts.append(chunk_text)
                yield chain

        platform_extras = {
            **self.build_platform_output_extras(
                event,
                message_kind=resolved_kind,
                output_segment_id=message_id,
            ),
            "interaction_plugin_streaming": True,
            "plugin_output_mode": resolved_mode.value,
        }
        await self._notify_lifecycle(
            event,
            "speaking",
            {"message_kind": resolved_kind},
        )
        try:
            await event.send_interaction_streaming(
                _observe_plugin_stream(),
                platform_extras=platform_extras,
                use_fallback=use_fallback,
            )
        except Exception as exc:
            event.set_extra("_interaction_plugin_streaming_failed", True)
            event.set_extra("_interaction_plugin_streaming_failure_reason", str(exc))
            logger.error(
                "Interaction middleware plugin streaming delivery failed: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )
            raise
        text = "".join(stream_text_parts).strip()
        if not text:
            return
        self._record_visible_output(
            event,
            message_kind=resolved_kind,
            text=text,
            message_id=message_id,
            delivered_message_ids=_visible_message_ids_from_extras(platform_extras),
            memory_relevant=not deferred_by_transaction,
        )
        if deferred_by_transaction:
            return
        self._materialize_finalized_turn(event)
        await self._persist_interaction_turn(event)

    async def finalize_plugin_output_transaction(
        self,
        event: AstrMessageEvent,
        *,
        delegated_to_core: bool,
    ) -> None:
        """Commit pending plugin output only when the handler owns the final reply."""
        active = bool(event.get_extra(PLUGIN_OUTPUT_TRANSACTION_ACTIVE_EXTRA_KEY))
        start = event.get_extra(PLUGIN_OUTPUT_TRANSACTION_START_EXTRA_KEY)
        pending_artifacts = event.get_extra(
            PLUGIN_OUTPUT_TRANSACTION_ARTIFACTS_EXTRA_KEY,
            [],
        )
        if not isinstance(pending_artifacts, list):
            pending_artifacts = []
        event.set_extra(PLUGIN_OUTPUT_TRANSACTION_ACTIVE_EXTRA_KEY, False)
        event.set_extra(PLUGIN_OUTPUT_TRANSACTION_START_EXTRA_KEY, None)
        event.set_extra(PLUGIN_OUTPUT_TRANSACTION_ARTIFACTS_EXTRA_KEY, None)
        if not active or delegated_to_core or not isinstance(start, int):
            return

        if pending_artifacts:
            self._append_assistant_artifacts(event, pending_artifacts)
        turn_state = get_interaction_turn_state(event)
        has_visible_outputs = (
            turn_state is not None and start < len(turn_state.visible_outputs)
        )
        if not has_visible_outputs and not pending_artifacts:
            return

        if has_visible_outputs and turn_state is not None:
            final_index = len(turn_state.visible_outputs) - 1
            for index in range(start, len(turn_state.visible_outputs)):
                memory_relevant = index == final_index
                turn_state.visible_outputs[index]["memory_relevant"] = memory_relevant
                if index < len(turn_state.utterances):
                    turn_state.utterances[index].memory_relevant = memory_relevant

        self._materialize_finalized_turn(event)
        await self._persist_interaction_turn(event)

    @staticmethod
    def _begin_plugin_output_transaction(event: AstrMessageEvent) -> bool:
        if not event.get_extra(PLUGIN_OUTPUT_TRANSACTION_ACTIVE_EXTRA_KEY, False):
            return False
        start = event.get_extra(PLUGIN_OUTPUT_TRANSACTION_START_EXTRA_KEY)
        if not isinstance(start, int):
            turn_state = get_interaction_turn_state(event)
            start = len(turn_state.visible_outputs) if turn_state is not None else 0
            event.set_extra(PLUGIN_OUTPUT_TRANSACTION_START_EXTRA_KEY, start)
        return True

    @staticmethod
    def _record_plugin_assistant_artifacts(
        event: AstrMessageEvent,
        message: MessageChain,
        *,
        deferred_by_transaction: bool,
    ) -> None:
        if not any(not isinstance(component, Plain) for component in message.chain):
            return
        artifacts = serialize_assistant_message_chain(message)
        if deferred_by_transaction:
            pending = event.get_extra(
                PLUGIN_OUTPUT_TRANSACTION_ARTIFACTS_EXTRA_KEY,
                [],
            )
            if not isinstance(pending, list):
                pending = []
            event.set_extra(
                PLUGIN_OUTPUT_TRANSACTION_ARTIFACTS_EXTRA_KEY,
                [*pending, *artifacts],
            )
            return
        InteractionOutputController._append_assistant_artifacts(event, artifacts)

    @staticmethod
    def _append_assistant_artifacts(
        event: AstrMessageEvent,
        artifacts: list[dict[str, Any]],
    ) -> None:
        append_interaction_turn_assistant_artifacts(event, artifacts)

    async def capture_visible_completion(
        self,
        event: AstrMessageEvent,
    ) -> None:
        complete_visible_turn = event.get_extra(
            "_interaction_original_complete_visible_turn"
        )
        if callable(complete_visible_turn):
            await complete_visible_turn()
        else:
            await event.complete_visible_turn()

    async def complete_visible_delivery(
        self,
        event: AstrMessageEvent,
    ) -> bool:
        return await self.delivery_coordinator.complete_visible_delivery(
            event,
            plugin_context=self.plugin_context,
            complete_visible_turn=self.capture_visible_completion,
            cancel_deferred_turn_finalization=self.cancel_deferred_turn_finalization,
            flush_deferred_turn_finalization=self.flush_deferred_turn_finalization,
            is_interaction_turn=True,
        )

    async def flush_deferred_turn_finalization(
        self,
        event: AstrMessageEvent,
    ) -> None:
        if consume_interaction_turn_finalization_pending(event):
            await self._persist_interaction_turn(event)

    async def cancel_deferred_turn_finalization(
        self,
        event: AstrMessageEvent,
        *,
        reason: str,
    ) -> None:
        consume_interaction_turn_finalization_pending(event)
        mark_interaction_turn_cancelled(event)
        await self._notify_lifecycle(
            event,
            "cancelled",
            {"reason": reason},
        )

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
        message_id = self._next_output_segment_id(event, "core_stream")
        platform_extras = self.build_platform_output_extras(
            event,
            message_kind="core_stream",
            output_segment_id=message_id,
        )
        await self._notify_lifecycle(
            event,
            "speaking",
            {"message_kind": "core_stream"},
        )
        try:
            await event.send_interaction_streaming(
                observed_generator,
                platform_extras=platform_extras,
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
            self._finalize_interaction_stream_output(
                event,
                message_id=message_id,
                delivered_message_ids=_visible_message_ids_from_extras(
                    platform_extras
                ),
            )
            await self._persist_interaction_turn(event)
        finally:
            set_interaction_turn_core_streaming_active(event, False)

    async def _wrap_core_stream(
        self,
        generator: AsyncGenerator[MessageChain, None],
        event: AstrMessageEvent,
    ) -> AsyncGenerator[MessageChain, None]:
        interaction_config = self._get_interaction_config(event)
        if not interaction_config.stream_observation_enabled:
            async for chain in generator:
                chunk_text = self._extract_observable_stream_text(chain)
                if chunk_text:
                    self._update_interaction_turn_stream_buffer(
                        event, chunk_text=chunk_text, observe=False
                    )
                yield chain
            return

        min_chars = interaction_config.stream_observation_min_chars
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

    def _finalize_interaction_stream_output(
        self,
        event: AstrMessageEvent,
        *,
        message_id: str,
        delivered_message_ids: list[str] | None = None,
    ) -> None:
        mark_interaction_turn_core_streaming_result_consumed(event)
        self._record_visible_output(
            event,
            message_kind="core_stream",
            text=get_interaction_turn_stream_text(event),
            message_id=message_id,
            delivered_message_ids=delivered_message_ids,
        )
        self._materialize_finalized_turn(event)

    @staticmethod
    def _materialize_finalized_turn(event: AstrMessageEvent) -> None:
        turn_id = str(event.get_extra("_turn_id", "") or "").strip()
        visible_outputs = get_interaction_turn_visible_outputs(event)
        assistant_artifacts = get_interaction_turn_assistant_artifacts(event)
        turn_state = get_interaction_turn_state(event)
        canonical_reply = build_interaction_turn_reply(
            visible_outputs,
            turn_id=turn_id,
            utterances=turn_state.utterances if turn_state is not None else None,
        )
        if turn_id and (canonical_reply or assistant_artifacts):
            set_interaction_turn_finalized_material(
                event,
                {
                    "turn_id": turn_id,
                    "user_text": (event.message_str or "").strip(),
                    "assistant_text": canonical_reply,
                    "assistant_artifacts": list(assistant_artifacts),
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
        turn_state = get_interaction_turn_state(event)
        if turn_state is None:
            raise RuntimeError("Interaction stream observation requires turn state")
        task = turn_state.execution_scope.create_task(
            self._observe_interaction_stream_window(
                event,
                observed_text=observed_text,
                total_text=total_text,
                window_index=window_index,
                observation_state=observation_state,
                is_final=is_final,
            ),
            role="stream_observation",
            name=f"interaction_stream_observation_{event.get_platform_id()}_{window_index}",
        )
        add_interaction_turn_stream_observation_task(event, task)
        task.add_done_callback(
            lambda done_task: self._on_stream_observation_task_done(event, done_task)
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
                >= self._get_interaction_config(event).stream_interjection_max_per_turn
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
        interaction_config = self._get_interaction_config(event)
        if not interaction_config.stream_interjection_enabled:
            return StreamObservationDecision(reason="disabled")

        decision = await self._collect_stream_interjection_from_plugins(
            event,
            observed_text=observed_text,
            total_text=total_text,
            window_index=window_index,
            is_final=is_final,
        )
        if decision is not None:
            if not decision.should_interject:
                return decision
            return await self._render_stream_interjection_via_persona(
                event,
                source_text=(decision.reply or "").strip(),
                observed_text=observed_text,
                total_text=total_text,
                window_index=window_index,
                is_final=is_final,
                reason=decision.reason or "plugin_decider",
            )
        return await self._render_stream_interjection_via_persona(
            event,
            observed_text=observed_text,
            total_text=total_text,
            window_index=window_index,
            is_final=is_final,
        )

    async def _render_stream_interjection_via_persona(
        self,
        event: AstrMessageEvent,
        *,
        source_text: str = "",
        observed_text: str,
        total_text: str,
        window_index: int,
        is_final: bool,
        reason: str = "persona_runtime",
    ) -> StreamObservationDecision:
        try:
            result = await self._render_visible_reply(
                event,
                PersonaExpressionRequest(
                    source_text=source_text,
                    observed_text=observed_text,
                    total_text=total_text,
                    pending_text=get_interaction_turn_stream_pending_text(event),
                    short_reply=True,
                    allow_empty=True,
                    intent=PersonaExpressionIntent(
                        kind="interjection",
                        source="stream_observation",
                        phase="interjection",
                    ),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Interaction stream interjection skipped: reason=persona_render_failed platform_id=%s session_id=%s turn_id=%s window_index=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                window_index,
                exc,
                exc_info=True,
            )
            self._record_stream_interjection_failure(
                event,
                reason="persona_render_failed",
                exception=exc,
                user_visible_action="continue_core_stream",
            )
            return StreamObservationDecision(reason="persona_render_failed")

        reply = result.spoken_reply.strip()
        return StreamObservationDecision(
            should_interject=bool(reply),
            reply=reply or None,
            reason=reason,
        )

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
        message_id = self._next_output_segment_id(event, "stream_interjection")
        (
            materialized_message,
            materialization,
        ) = await self.materialize_immediate_interaction_outbound_message(
            event, message, message_id=message_id
        )
        platform_extras = {
            "interaction_stream_reply": True,
            "stream_window_index": window_index,
        }
        delivered_message_ids = await self._deliver_visible_message(
            event,
            materialized_message,
            message_kind="stream_interjection",
            platform_extras=platform_extras,
            output_segment_id=message_id,
            record_send_operation=False,
            allow_segmented_reply=False,
            semantic_text=text,
        )
        self._record_visible_output(
            event,
            message_kind="stream_interjection",
            text=text,
            message_id=message_id,
            delivered_message_ids=delivered_message_ids,
            metadata=materialization,
            memory_relevant=False,
        )

    async def _render_visible_reply(
        self,
        event: AstrMessageEvent,
        request: PersonaExpressionRequest,
    ) -> PersonaExpressionResult:
        if self.visible_reply_renderer is None:
            del event
            raise RuntimeError("interaction visible_reply_renderer unavailable")
        return await self.visible_reply_renderer(event, request)

    async def _deliver_core_reply(
        self,
        message: MessageChain,
        event: AstrMessageEvent,
    ) -> None:
        core_result_text = message.get_plain_text()
        result = await self._render_visible_reply(
            event,
            PersonaExpressionRequest.core_final(
                core_result_text,
                immediate_reply=get_interaction_turn_immediate_reply(event),
            ),
        )
        await self.deliver_prepared_core_reply(message, result, event)

    async def deliver_prepared_core_reply(
        self,
        source_message: MessageChain,
        result: PersonaExpressionResult,
        event: AstrMessageEvent,
    ) -> None:
        final_message = source_message.derive(
            [
                Plain(result.spoken_reply),
                *self._persona_tool_attachment_components(result),
            ]
        )
        await self._deliver_core_final_message(
            source_message,
            final_message,
            event,
            effect_calls=result.effect_calls,
        )

    async def deliver_raw_core_reply(
        self,
        source_message: MessageChain,
        event: AstrMessageEvent,
    ) -> None:
        """Deliver an existing Core result without Persona rewriting it."""
        await self._deliver_core_final_message(
            source_message,
            source_message,
            event,
        )

    async def _deliver_core_final_message(
        self,
        source_message: MessageChain,
        final_message: MessageChain,
        event: AstrMessageEvent,
        *,
        effect_calls: Sequence[Any] = (),
    ) -> None:
        core_result_text = source_message.get_plain_text()
        contributions = await self._collect_result_contributions(
            event,
            core_result=core_result_text,
            final_result=final_message.get_plain_text(),
            phase="final",
            candidate_message_kind="core_reply",
            candidate_message_id=(
                message_id := self._next_output_segment_id(event, "core_reply")
            ),
            effect_calls=effect_calls,
        )
        merged = merge_result_contributions(contributions)
        if merged.final_text_override is not None:
            final_message = replace_plain_text_preserving_components(
                final_message,
                merged.final_text_override,
            )

        source_result = event.get_result()
        result_content_type = (
            source_result.result_content_type
            if source_result is not None
            and source_result.result_content_type is not None
            else ResultContentType.LLM_RESULT
        )
        final_message = await self.pre_output_processor.prepare_interaction_message(
            event,
            final_message,
            result_content_type,
        )
        if final_message is None:
            event.set_extra("_interaction_pipeline_output_suppressed", True)
            mark_interaction_turn_cancelled(event)
            await self._notify_lifecycle(
                event,
                "cancelled",
                {"reason": "pipeline_pre_output_suppressed"},
            )
            return

        platform_extras = self.build_platform_output_base_extras(
            event, result_contribution=merged
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
            message_id=message_id,
        )
        delivered_message_ids = await self._deliver_visible_message(
            event,
            materialized_message,
            message_kind="core_reply",
            platform_extras=platform_extras,
            output_segment_id=message_id,
            result_is_model_result=True,
            allow_segmented_reply=True,
            semantic_text=semantic_text,
        )
        self._record_visible_output(
            event,
            message_kind="core_reply",
            text=semantic_text,
            message_id=message_id,
            delivered_message_ids=delivered_message_ids,
            metadata=materialization,
        )
        self._materialize_finalized_turn(event)
        await self._persist_interaction_turn(event)

    @staticmethod
    def _persona_tool_attachment_components(
        result: PersonaExpressionResult,
    ) -> list[Any]:
        attachments = result.metadata.get("persona_tool_attachments", [])
        if not isinstance(attachments, list):
            return []
        return [
            component
            for message in attachments
            if isinstance(message, MessageChain)
            for component in message.chain
            if not isinstance(component, Plain)
        ]

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
        candidate_message_id: str,
        effect_calls: Sequence[Any] = (),
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

        route_decision = get_interaction_route_decision(event)
        route_payload = route_decision.to_dict() if route_decision is not None else None
        route_mode = (
            route_decision.route_mode.value if route_decision is not None else None
        )
        purpose = "persona_reply" if phase == "immediate" else "core_reply"
        effect_calls = tuple(effect_calls)
        logger.debug(
            "DIAG result_view.effect_calls: platform_id=%s session_id=%s phase=%s payload_present=%s effect_calls=%s",
            event.get_platform_id(),
            event.session_id,
            phase,
            bool(effect_calls),
            [call.name for call in effect_calls],
        )
        output_text = (final_result or core_result or "").strip()
        output_draft = InteractionOutputDraft(
            turn_id=str(event.get_extra("_turn_id", "") or ""),
            message_id=candidate_message_id,
            source="core" if phase == "final" and core_result else "interaction",
            route_mode=route_mode,
            phase=phase,
            text=output_text,
            semantic_text=output_text,
            message_kind=candidate_message_kind,
            latency_policy="fast" if phase == "immediate" else "normal",
            metadata={
                "is_immediate": phase == "immediate",
                "is_final": phase == "final",
                "text_stage": "candidate_pre_contribution",
                "text_may_change_by_legacy_override": True,
            },
        )

        view = InteractionResultView(
            turn_id=str(event.get_extra("_turn_id", "") or ""),
            platform_id=event.get_platform_id(),
            session_id=event.unified_msg_origin,
            purpose=purpose,
            route_decision=route_payload,
            output_draft=output_draft.to_mapping(),
            immediate_reply=get_interaction_turn_immediate_reply(event),
            core_result=core_result,
            final_result=final_result,
            effect_calls=effect_calls,
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
                "purpose": purpose,
                "message_kind": candidate_message_kind,
                "is_immediate": phase == "immediate",
                "is_final": phase == "final",
            },
        )
        contributions: list[InteractionResultContribution] = []
        contributors = list_contributors()
        timeout = self._get_interaction_config(event).contributor_timeout

        async def _collect_one(contributor) -> InteractionResultContribution | None:
            try:
                payload = await asyncio.wait_for(
                    contributor.collect(
                        event,
                        self.plugin_context,
                        view.copy_read_only(),
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                failures = event.get_extra(
                    "_interaction_result_contributor_failures", []
                )
                if not isinstance(failures, list):
                    failures = []
                failures.append(
                    {
                        "plugin_id": getattr(contributor, "plugin_id", "<unknown>"),
                        "error": f"timeout after {timeout:.2f}s",
                    }
                )
                event.set_extra("_interaction_result_contributor_failures", failures)
                logger.warning(
                    "Interaction result contributor timed out: plugin_id=%s timeout=%.2fs",
                    getattr(contributor, "plugin_id", "<unknown>"),
                    timeout,
                )
                return None
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
                return None
            if isinstance(payload, InteractionResultContribution):
                return payload
            return None

        results = await asyncio.gather(
            *[_collect_one(contributor) for contributor in contributors],
        )
        contributions.extend(
            result
            for result in results
            if isinstance(result, InteractionResultContribution)
        )
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
        output_segment_id: str | None = None,
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
                "composite_message_id": output_segment_id or visible_message_id,
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
        delivery_metadata = event.get_extra("_interaction_delivery_metadata")
        if isinstance(delivery_metadata, Mapping):
            extras.update(dict(delivery_metadata))
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
        message_id: str | None = None,
    ) -> tuple[MessageChain, dict[str, Any]]:
        self._refresh_outbound_materialization_config(event)
        materialization: dict[str, Any] = {
            "message_kind": message_kind,
            "semantic_text": message.get_plain_text(),
            "delivered_as": "text",
            "tts_status": "not_attempted",
        }
        materialized = self._apply_interaction_reply_prefix(event, message)
        materialized, reasoning_metadata = self._apply_interaction_reasoning_display(
            event,
            materialized,
        )
        materialization.update(reasoning_metadata)
        try:
            materialized, tts_metadata = await self._apply_interaction_tts(
                event,
                materialized,
                result_is_model_result=result_is_model_result,
                message_id=message_id,
            )
        except VoiceServiceError as exc:
            logger.error(
                "Interaction TTS failed; emitting an audio-failed materialization.",
                exc_info=True,
            )
            tts_metadata = {
                "tts_failed": True,
                "failure_code": (
                    exc.state.failure_code if exc.state is not None else exc.reason
                ),
                "tts_status": "failed",
            }
            if exc.state is not None:
                materialized = self._attach_tts_failure_segment(
                    materialized,
                    exc.state,
                )
        materialization.update(tts_metadata)
        if tts_metadata.get("delivered_as") == "record":
            return materialized, materialization
        try:
            materialized, t2i_metadata = await self._apply_interaction_t2i(
                event,
                materialized,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Interaction t2i failed; sending text fallback.",
                exc_info=True,
            )
            t2i_metadata = {
                "t2i_failed": True,
                "t2i_fallback": "text",
                "t2i_failure_reason": str(exc),
            }
        materialization.update(t2i_metadata)
        return materialized, materialization

    async def materialize_immediate_interaction_outbound_message(
        self,
        event: AstrMessageEvent,
        message: MessageChain,
        *,
        message_id: str | None = None,
    ) -> tuple[MessageChain, dict[str, Any]]:
        self._refresh_outbound_materialization_config(event)
        materialization: dict[str, Any] = {
            "message_kind": "immediate_reply",
            "semantic_text": message.get_plain_text(),
            "delivered_as": "text",
            "tts_status": "not_attempted",
        }
        materialized = self._apply_interaction_reply_prefix(event, message)
        try:
            materialized, tts_metadata = await self._apply_interaction_tts(
                event,
                materialized,
                result_is_model_result=True,
                message_id=message_id,
            )
        except VoiceServiceError as exc:
            logger.error(
                "Immediate interaction TTS failed; emitting an audio-failed segment.",
                exc_info=True,
            )
            tts_metadata = {
                "tts_failed": True,
                "failure_code": (
                    exc.state.failure_code if exc.state is not None else exc.reason
                ),
                "tts_status": "failed",
            }
            if exc.state is not None:
                materialized = self._attach_tts_failure_segment(
                    materialized,
                    exc.state,
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
        message_id: str | None = None,
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
        new_chain = []
        converted: list[dict[str, Any]] = []
        for comp in message.chain:
            if not isinstance(comp, Plain) or len(comp.text) <= 1:
                new_chain.append(comp)
                continue
            try:
                current_message_id = message_id or self._next_output_segment_id(
                    event, "tts"
                )
                message_id = None
                logger.info("Interaction TTS request: %s", comp.text)
                result = await synthesize_text(
                    self.plugin_context,
                    event,
                    comp.text,
                    stage="interaction.outbound_tts",
                    use_file_service=bool(tts_settings.get("use_file_service")),
                    callback_api_base=str(
                        self._get_config_value("callback_api_base", "", event=event)
                    ),
                    require_file_registration_config=True,
                    turn_id=str(event.get_extra("_turn_id", "") or ""),
                    message_id=current_message_id,
                )
                logger.info("Interaction TTS result: %s", result.audio_path)
                new_chain.append(
                    Record(
                        file=result.delivered_file,
                        url=result.delivered_file,
                        text=result.text,
                        delivery_metadata=build_tts_delivery_metadata(
                            result.state,
                            audio_attachment="present",
                        ),
                    )
                )
                converted.append(
                    {
                        "tts_source_text": result.text,
                        "tts_audio_path": result.audio_path,
                        "tts_audio_url": result.audio_url,
                        "tts_provider_id": result.provider_id,
                        "tts_request_id": result.state.tts_request_id,
                        "message_id": result.state.message_id,
                    }
                )
                if bool(tts_settings.get("dual_output")):
                    new_chain.append(
                        Plain(
                            comp.text,
                            delivery_metadata=build_tts_delivery_metadata(
                                result.state,
                                audio_attachment="absent",
                            ),
                        )
                    )
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
                "tts_status": "succeeded",
            },
        )

    @staticmethod
    def _attach_tts_failure_segment(
        message: MessageChain,
        state: TTSState,
    ) -> MessageChain:
        chain = list(message.chain)
        for index, component in enumerate(chain):
            if isinstance(component, Plain) and len(component.text) > 1:
                chain[index] = Plain(
                    component.text,
                    delivery_metadata=build_tts_delivery_metadata(
                        state,
                        audio_attachment="absent",
                    ),
                )
                break
        return message.derive(chain)

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
            replace_leading_plain_components(message, image),
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
    def _next_output_segment_id(
        event: AstrMessageEvent,
        message_kind: str,
    ) -> str:
        return next_interaction_turn_output_segment_id(event, message_kind)

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
        await event.send_message_with_extras(
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
        output_segment_id: str | None = None,
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
        await self._notify_lifecycle(
            event,
            "speaking",
            {"message_kind": message_kind},
        )

        async def _send(
            chain: MessageChain,
            delivery_extras: Mapping[str, Any] | None = None,
        ) -> None:
            output_extras = {
                **base_extras,
                **self.build_platform_output_extras(
                    event,
                    message_kind=message_kind,
                    output_segment_id=output_segment_id,
                ),
                "semantic_text": semantic_text,
            }
            if isinstance(delivery_extras, Mapping):
                output_extras.update(delivery_extras)
            output_segment = output_extras.get("output_segment")
            segment_tts = (
                output_segment.get("tts")
                if isinstance(output_segment, Mapping)
                else None
            )
            if isinstance(segment_tts, Mapping):
                tts_status = str(segment_tts.get("status") or "").strip()
                logical_message_id = str(
                    output_segment.get("message_id") or ""
                ).strip()
                if logical_message_id:
                    output_extras["composite_message_id"] = logical_message_id
                failure_code = str(
                    segment_tts.get("failure_code") or ""
                ).strip()
            else:
                tts_status = ""
                failure_code = ""
            if tts_status:
                output_extras["tts_status"] = tts_status
            if failure_code:
                output_extras["failure_code"] = failure_code
            await self._send_platform_message(
                chain,
                event,
                platform_extras=output_extras,
                record_send_operation=record_send_operation,
            )
            if message_kind == "immediate_reply":
                mark_interaction_turn_personal_emitted(event)
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
            preserve_record_delivery_groups=(
                bool(event.get_extra("_runtime_observation_event", False))
                and supports_personal_runtime(event.platform_meta)
            ),
        )
        if not sent:
            raise RuntimeError(
                f"Interaction output was not delivered: {message_kind}"
            )
        record_interaction_turn_visible_message_fingerprint(
            event,
            fingerprint_visible_message(message),
        )
        return delivered_message_ids

    async def _notify_lifecycle(
        self,
        event: AstrMessageEvent,
        stage: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.lifecycle_callback is not None:
            await self.lifecycle_callback(event, stage, metadata)

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
        message_id: str | None = None,
        delivered_message_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        memory_relevant: bool = True,
    ) -> None:
        resolved_metadata = dict(metadata or {})
        delivery_metadata = event.get_extra("_interaction_delivery_metadata")
        if isinstance(delivery_metadata, Mapping):
            resolved_metadata["delivery_metadata"] = dict(delivery_metadata)
        append_interaction_turn_visible_output(
            event,
            message_kind=message_kind,
            text=text,
            message_id=message_id,
            delivered_message_ids=delivered_message_ids,
            metadata=resolved_metadata,
            memory_relevant=memory_relevant,
        )

    async def _persist_interaction_turn(
        self,
        event: AstrMessageEvent,
    ) -> None:
        if is_interaction_turn_completed(event):
            return
        if is_interaction_turn_finalization_deferred(event):
            mark_interaction_turn_finalization_pending(event)
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
        if has_interaction_turn_final_output_claimed(event):
            return "suppressed_duplicate_final"
        if (
            event.get_extra(CORE_OUTPUT_DELIVERY_EXTRA_KEY)
            == CoreOutputDelivery.PROGRESS.value
        ):
            return "core_progress"

        result = event.get_result()
        result_is_model = bool(result and result.is_model_result())
        decision = get_interaction_route_decision(event)
        route_mode = decision.route_mode if decision is not None else None
        streamed = has_interaction_turn_core_streaming_result_consumed(event)
        streaming_active = is_interaction_turn_core_streaming_active(event)

        if result_is_model:
            return "core_final_model_result"
        if (
            (
                route_mode == InteractionRouteMode.HYBRID
                or bool(event.get_extra("_interaction_protocol_core_bypass", False))
            )
            and streamed
            and not streaming_active
            and message.type
            not in {"agent_stats", "tool_call", "interaction_stream_reply"}
        ):
            return "core_final_followup_after_stream"
        return "passthrough"
