from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from astrbot import logger
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain, ResultContentType
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.prompt.render.selector import _extract_json_object
from astrbot.core.provider import Provider

from .contributors import (
    InteractionResultContribution,
    InteractionResultView,
    merge_result_contributions,
)
from .core_bridge import get_interaction_decision
from .finalizer import finalize_response
from .memory_store import InteractionMemoryStore, update_interaction_memory_from_turn
from .types import FinalizerMode, InteractionAgentConfig


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
        memory_store: InteractionMemoryStore | None = None,
    ) -> None:
        self.plugin_context = plugin_context
        self.interaction_config = interaction_config or InteractionAgentConfig()
        self.memory_store = memory_store or InteractionMemoryStore()

    async def emit_immediate_spoken_reply(
        self,
        decision,
        event: AstrMessageEvent,
    ) -> None:
        reply = (decision.immediate_spoken_reply or "").strip()
        if not reply:
            return
        logger.debug(
            "Interaction immediate reply emit: platform_id=%s session_id=%s turn_id=%s length=%s",
            event.get_platform_id(),
            event.session_id,
            event.get_extra("_turn_id"),
            len(reply),
        )
        event.set_extra("_interaction_immediate_reply", reply)
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
            await event.complete_visible_turn()
            return

        is_immediate = bool(event.get_extra("_interaction_emitting_immediate_reply"))
        if is_immediate:
            logger.debug(
                "Interaction outbound classified: platform_id=%s session_id=%s turn_id=%s kind=immediate_reply",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
            )
            await self._send_platform_message(
                message,
                event,
                platform_extras=self.build_platform_output_extras(event),
                record_send_operation=False,
            )
            return

        if self._is_already_delivered_streaming_finish(event):
            event.set_extra("_interaction_core_final_result_consumed", True)
            logger.warning(
                "Interaction streaming finish marker skipped after streaming delivery: platform_id=%s session_id=%s turn_id=%s final_length=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                len(message.get_plain_text()),
            )
            await self._persist_interaction_turn(
                event,
                message.get_plain_text(),
            )
            return

        if not self._is_core_final_model_result(event):
            logger.debug(
                "Interaction outbound classified: platform_id=%s session_id=%s turn_id=%s kind=passthrough",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
            )
            await self._send_platform_message(
                message,
                event,
                platform_extras=self.build_platform_output_extras(event),
            )
            return

        if event.get_extra("_interaction_core_final_result_consumed", False):
            logger.debug(
                "Interaction outbound classified: platform_id=%s session_id=%s turn_id=%s kind=core_final_model_result_segment_passthrough",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
            )
            await self._send_platform_message(
                message,
                event,
                platform_extras=self.build_platform_output_extras(event),
            )
            return

        event.set_extra("_interaction_core_final_result_consumed", True)
        logger.debug(
            "Interaction outbound classified: platform_id=%s session_id=%s turn_id=%s kind=core_final_model_result",
            event.get_platform_id(),
            event.session_id,
            event.get_extra("_turn_id"),
        )
        final_message = await self.maybe_finalize_and_send(message, event)
        if final_message is not None:
            return

        await self._send_platform_message(
            message,
            event,
            platform_extras=self.build_platform_output_extras(event),
        )

    async def capture_streaming(
        self,
        generator: AsyncGenerator[MessageChain, None],
        event: AstrMessageEvent,
        use_fallback: bool = False,
    ) -> None:
        logger.debug(
            "Interaction middleware outbound streaming intercepted: platform_id=%s session_id=%s turn_id=%s use_fallback=%s",
            event.get_platform_id(),
            event.session_id,
            event.get_extra("_turn_id"),
            use_fallback,
        )
        event.set_extra("_interaction_core_streaming_active", True)
        observed_generator = self._wrap_core_stream(generator, event)
        try:
            await event.send_interaction_streaming(
                observed_generator,
                platform_extras=self.build_platform_output_extras(event),
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
            event.set_extra("_interaction_core_streaming_result_consumed", True)
        finally:
            event.set_extra("_interaction_core_streaming_active", False)

    async def _wrap_core_stream(
        self,
        generator: AsyncGenerator[MessageChain, None],
        event: AstrMessageEvent,
    ) -> AsyncGenerator[MessageChain, None]:
        if not self.interaction_config.stream_observation_enabled:
            async for chain in generator:
                yield chain
            return

        turn_id = event.get_extra("_turn_id")
        total_text = ""
        pending_text = ""
        window_index = 0
        interjection_count = 0
        min_chars = self.interaction_config.stream_observation_min_chars
        logger.debug(
            "Interaction core stream observation started: platform_id=%s session_id=%s turn_id=%s min_chars=%s interjection_enabled=%s",
            event.get_platform_id(),
            event.session_id,
            turn_id,
            min_chars,
            self.interaction_config.stream_interjection_enabled,
        )
        async for chain in generator:
            chunk_text = self._extract_observable_stream_text(chain)
            if chunk_text:
                total_text += chunk_text
                pending_text += chunk_text
                event.set_extra("_interaction_core_stream_text", total_text)
                event.set_extra(
                    "_interaction_core_stream_pending_text",
                    pending_text,
                )
                while len(pending_text) >= min_chars:
                    window_index += 1
                    observed_text = pending_text[:min_chars]
                    pending_text = pending_text[min_chars:]
                    interjection_count = await self._observe_core_stream_window(
                        event,
                        observed_text=observed_text,
                        total_text=total_text,
                        window_index=window_index,
                        interjection_count=interjection_count,
                        chain_type=chain.type,
                        is_final=False,
                    )
            yield chain

        if pending_text:
            event.set_extra(
                "_interaction_core_stream_pending_text",
                pending_text,
            )
            window_index += 1
            interjection_count = await self._observe_core_stream_window(
                event,
                observed_text=pending_text,
                total_text=total_text,
                window_index=window_index,
                interjection_count=interjection_count,
                chain_type=None,
                is_final=True,
            )
        event.set_extra("_interaction_core_stream_text", total_text)
        logger.debug(
            "Interaction core stream observation finished: platform_id=%s session_id=%s turn_id=%s total_chars=%s windows=%s interjections=%s",
            event.get_platform_id(),
            event.session_id,
            turn_id,
            len(total_text),
            window_index,
            interjection_count,
        )

    async def _observe_core_stream_window(
        self,
        event: AstrMessageEvent,
        *,
        observed_text: str,
        total_text: str,
        window_index: int,
        interjection_count: int,
        chain_type: str | None,
        is_final: bool,
    ) -> int:
        event.set_extra(
            "_interaction_core_stream_observation_count",
            window_index,
        )
        logger.debug(
            "Interaction core stream observation window: platform_id=%s session_id=%s turn_id=%s window_index=%s observed_chars=%s total_chars=%s chain_type=%s final=%s",
            event.get_platform_id(),
            event.session_id,
            event.get_extra("_turn_id"),
            window_index,
            len(observed_text),
            len(total_text),
            chain_type,
            is_final,
        )
        if (
            interjection_count
            >= self.interaction_config.stream_interjection_max_per_turn
        ):
            return interjection_count
        decision = await self._decide_stream_interjection(
            event,
            observed_text=observed_text,
            total_text=total_text,
            window_index=window_index,
            is_final=is_final,
        )
        if decision.should_interject and decision.reply:
            await self._emit_stream_interjection(
                event,
                decision.reply,
                window_index=window_index,
                reason=decision.reason,
            )
            return interjection_count + 1
        return interjection_count

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
            logger.debug(
                "Interaction stream interjection skipped by config: platform_id=%s session_id=%s turn_id=%s window_index=%s observed_chars=%s final=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                window_index,
                len(observed_text),
                is_final,
            )
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
            return StreamObservationDecision(reason="plugin_context_unavailable")
        provider = self.plugin_context.get_provider_by_id(
            self.interaction_config.decision_provider_id
        )
        if not isinstance(provider, Provider):
            logger.warning(
                "Interaction stream interjection skipped: provider unavailable provider_id=%s",
                self.interaction_config.decision_provider_id,
            )
            return StreamObservationDecision(reason="provider_unavailable")

        prompt = self._build_stream_interjection_prompt(
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
                    model=self.interaction_config.decision_model or None,
                    temperature=self.interaction_config.decision_temperature,
                    max_tokens=min(self.interaction_config.decision_max_tokens, 160),
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
            return StreamObservationDecision(reason="model_error")

        payload = _extract_json_object(llm_resp.completion_text)
        decision = self._coerce_stream_interjection_decision(payload)
        if decision is None:
            logger.warning(
                "Interaction stream interjection skipped: reason=non_json raw=%s",
                llm_resp.completion_text,
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
        metadata = {
            "turn_id": str(event.get_extra("_turn_id", "") or ""),
            "platform_id": event.get_platform_id(),
            "session_id": event.unified_msg_origin,
            "window_index": window_index,
            "observed_text": observed_text,
            "total_text": total_text,
            "is_final": is_final,
        }
        for decider in list_deciders():
            try:
                payload = await decider.decide(event, self.plugin_context, metadata)
            except Exception as exc:  # noqa: BLE001
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
                logger.debug(
                    "Interaction stream decider result: plugin_id=%s should_interject=%s reason=%s",
                    getattr(decider, "plugin_id", "<unknown>"),
                    decision.should_interject,
                    decision.reason,
                )
                return decision
        return None

    @staticmethod
    def _build_stream_interjection_prompt(
        event: AstrMessageEvent,
        *,
        observed_text: str,
        total_text: str,
        window_index: int,
        is_final: bool,
    ) -> str:
        payload = {
            "platform_id": event.get_platform_id(),
            "session_id": event.unified_msg_origin,
            "turn_id": event.get_extra("_turn_id"),
            "user_input": event.message_str,
            "window_index": window_index,
            "is_final_window": is_final,
            "observed_core_stream_window": observed_text,
            "core_stream_so_far": total_text[-800:],
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
        logger.debug(
            "Interaction stream interjection emit: platform_id=%s session_id=%s turn_id=%s window_index=%s length=%s reason=%s",
            event.get_platform_id(),
            event.session_id,
            event.get_extra("_turn_id"),
            window_index,
            len(text),
            reason,
        )
        await self._send_platform_message(
            message,
            event,
            platform_extras={
                **self.build_platform_output_extras(event),
                "interaction_stream_reply": True,
                "stream_window_index": window_index,
            },
            record_send_operation=False,
        )

    async def maybe_finalize_and_send(
        self,
        message: MessageChain,
        event: AstrMessageEvent,
    ) -> MessageChain | None:
        core_result_text = message.get_plain_text()
        immediate_reply = event.get_extra("_interaction_immediate_reply")
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
            final_message = message.derive([Plain("最终回复整理失败，请查看日志。")])
            await self._send_platform_message(
                final_message,
                event,
                platform_extras=self.build_platform_output_extras(event),
            )
            await self._persist_interaction_turn(
                event,
                final_message.get_plain_text(),
            )
            return final_message
        final_message = message
        if final_text:
            final_message = message.derive([Plain(final_text)])
            logger.debug(
                "Interaction finalizer applied: platform_id=%s session_id=%s turn_id=%s original_length=%s final_length=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                len(core_result_text),
                len(final_text),
            )

        contributions = await self._collect_result_contributions(
            event,
            core_result=core_result_text,
            final_result=final_message.get_plain_text(),
        )
        merged = merge_result_contributions(contributions)
        logger.debug(
            "Interaction result contributions merged: platform_id=%s session_id=%s turn_id=%s count=%s has_text_override=%s client_objects=%s platform_extra_keys=%s",
            event.get_platform_id(),
            event.session_id,
            event.get_extra("_turn_id"),
            len(contributions),
            merged.final_text_override is not None,
            len(merged.client_objects),
            sorted(merged.platform_extras.keys()),
        )
        if merged.final_text_override is not None:
            final_message = message.derive([Plain(merged.final_text_override)])

        platform_extras = self.build_platform_output_extras(
            event,
            result_contribution=merged,
        )
        await self._send_platform_message(
            final_message,
            event,
            platform_extras=platform_extras,
        )
        await self._persist_interaction_turn(
            event,
            final_message.get_plain_text(),
        )
        return final_message

    @staticmethod
    def _is_core_final_model_result(event: AstrMessageEvent) -> bool:
        result = event.get_result()
        if result is None:
            return False
        return result.is_model_result()

    @staticmethod
    def _is_already_delivered_streaming_finish(event: AstrMessageEvent) -> bool:
        if not event.get_extra("_interaction_core_streaming_result_consumed", False):
            return False
        result = event.get_result()
        if result is None:
            return False
        return result.result_content_type == ResultContentType.STREAMING_FINISH

    @staticmethod
    def _extract_observable_stream_text(chain: MessageChain) -> str:
        if chain.type in {"reasoning", "audio_chunk", "break"}:
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
            decision=get_interaction_decision(event),
            immediate_reply=event.get_extra("_interaction_immediate_reply"),
            core_result=core_result,
            final_result=final_result,
            metadata={"session_id": event.unified_msg_origin},
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
                logger.debug(
                    "Interaction result contributor collected: plugin_id=%s priority=%s has_text_override=%s client_objects=%s platform_extra_keys=%s",
                    payload.plugin_id,
                    payload.priority,
                    payload.final_text_override is not None,
                    len(payload.client_objects),
                    sorted(payload.platform_extras.keys()),
                )
                contributions.append(payload)
        contributions.sort(key=lambda item: (item.priority, item.plugin_id))
        return contributions

    def build_platform_output_extras(
        self,
        event: AstrMessageEvent,
        *,
        result_contribution: InteractionResultContribution | None = None,
    ) -> dict[str, Any]:
        extras: dict[str, Any] = {
            "turn_id": event.get_extra("_turn_id"),
        }
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
        return {key: value for key, value in extras.items() if value is not None}

    async def _send_platform_message(
        self,
        message: MessageChain,
        event: AstrMessageEvent,
        *,
        platform_extras: dict[str, Any],
        record_send_operation: bool = True,
    ) -> None:
        logger.debug(
            "Interaction middleware outbound send intercepted: platform_id=%s session_id=%s turn_id=%s",
            event.get_platform_id(),
            event.session_id,
            event.get_extra("_turn_id"),
        )
        await event.send_interaction_message(
            message=message,
            platform_extras=platform_extras,
            record_send_operation=record_send_operation,
        )

    async def _persist_interaction_turn(
        self,
        event: AstrMessageEvent,
        visible_reply: str | None,
    ) -> None:
        if not visible_reply:
            return
        try:
            snapshot = await self.memory_store.load_interaction_memory(
                event.unified_msg_origin,
                str(event.get_extra("_interaction_persona_id", "") or ""),
            )
            snapshot = update_interaction_memory_from_turn(
                snapshot,
                user_text=event.message_str,
                visible_reply=visible_reply,
            )
            await self.memory_store.save_interaction_memory(
                event.unified_msg_origin,
                snapshot,
            )
            logger.debug(
                "Interaction memory persisted: platform_id=%s session_id=%s turn_id=%s visible_reply_length=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                len(visible_reply),
            )
        except Exception as exc:  # noqa: BLE001
            event.set_extra("_interaction_memory_persist_failed", True)
            event.set_extra("_interaction_memory_persist_failure_reason", str(exc))
            logger.error(
                "Interaction memory persistence failed after outbound send: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )
