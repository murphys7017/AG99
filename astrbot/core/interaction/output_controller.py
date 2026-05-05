from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from astrbot import logger
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .contributors import (
    InteractionResultContribution,
    InteractionResultView,
    merge_result_contributions,
)
from .core_bridge import get_interaction_decision
from .finalizer import finalize_response
from .memory_store import InteractionMemoryStore, update_interaction_memory_from_turn
from .types import FinalizerMode, InteractionAgentConfig


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
        await event.send_interaction_streaming(
            generator,
            platform_extras=self.build_platform_output_extras(event),
            use_fallback=use_fallback,
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
