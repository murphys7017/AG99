from __future__ import annotations

import asyncio
import hashlib
import json
import traceback
from collections.abc import Awaitable, Callable, Mapping
from copy import copy
from typing import Any

from astrbot.core import logger
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import (
    MessageChain,
    MessageEventResult,
    ResultContentType,
)
from astrbot.core.pipeline.content_safety_check.strategies.strategy import (
    StrategySelector,
)
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.postprocess import dispatch_postprocess, get_postprocess_manager
from astrbot.core.postprocess.types import PostProcessTrigger
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import EventType, star_handlers_registry


class PreOutputProcessor:
    """Own the response-safety and legacy decorating-hook boundary."""

    def __init__(self) -> None:
        self._safety_selectors: dict[str, tuple[str, StrategySelector]] = {}

    async def prepare_interaction_message(
        self,
        event: AstrMessageEvent,
        message: MessageChain,
        result_content_type: ResultContentType,
    ) -> MessageChain | None:
        result = MessageEventResult(
            chain=list(message.chain),
            result_content_type=result_content_type,
        )
        result.use_t2i_ = message.use_t2i_
        result.use_markdown_ = message.use_markdown_
        result.type = message.type
        event.set_result(result)

        if result.is_llm_result() and not self.response_is_safe(event, result):
            return None
        if await self.run_decorating_hooks(event):
            return None

        decorated = event.get_result()
        if decorated is None or not decorated.chain:
            return None
        return decorated.derive(list(decorated.chain))

    async def run_decorating_hooks(
        self,
        event: AstrMessageEvent,
        *,
        is_stream: bool = False,
    ) -> bool:
        handlers = star_handlers_registry.get_handlers_by_event_type(
            EventType.OnDecoratingResultEvent,
            plugins_name=event.plugins_name,
        )
        for handler in handlers:
            plugin = star_map.get(handler.handler_module_path)
            plugin_name = (
                plugin.name if plugin is not None else handler.handler_module_path
            )
            try:
                logger.debug(
                    "hook(on_decorating_result) -> %s - %s",
                    plugin_name,
                    handler.handler_name,
                )
                if is_stream:
                    logger.warning(
                        "启用流式输出时，依赖发送消息前事件钩子的插件可能无法正常工作"
                    )
                await handler.handler(event)
                result = event.get_result()
                if result is None or not result.chain:
                    logger.debug(
                        "hook(on_decorating_result) -> %s - %s 将消息结果清空。",
                        plugin_name,
                        handler.handler_name,
                    )
            except BaseException:
                logger.error(traceback.format_exc())

            if event.is_stopped():
                logger.info(
                    "%s - %s 终止了事件传播。",
                    plugin_name,
                    handler.handler_name,
                )
                return True

        return event.is_stopped()

    def response_is_safe(
        self,
        event: AstrMessageEvent,
        result: MessageEventResult,
    ) -> bool:
        config = event.get_extra("_astrbot_config")
        if not isinstance(config, Mapping):
            return True
        safety_config = config.get("content_safety")
        if not isinstance(safety_config, Mapping) or not safety_config.get(
            "also_use_in_response",
            False,
        ):
            return True

        config_id = str(event.get_extra("_astrbot_config_id", "default") or "default")
        serialized_config = json.dumps(
            dict(safety_config),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        config_fingerprint = hashlib.sha256(serialized_config.encode("utf-8")).hexdigest()
        cached_selector = self._safety_selectors.get(config_id)
        if cached_selector is None or cached_selector[0] != config_fingerprint:
            selector = StrategySelector(dict(safety_config))
            self._safety_selectors[config_id] = (config_fingerprint, selector)
        else:
            selector = cached_selector[1]

        text = "".join(
            component.text
            for component in result.chain
            if isinstance(component, Plain)
        )
        ok, info = selector.check(text)
        if ok:
            return True

        if event.is_at_or_wake_command:
            event.set_result(
                MessageEventResult().message(
                    "你的消息或者大模型的响应中包含不适当的内容，已被屏蔽。"
                )
            )
        event.stop_event()
        logger.info("内容安全检查不通过，原因：%s", info)
        return False


class TurnDeliveryCoordinator:
    """Own the common after-send, completion, and postprocess lifecycle."""

    async def complete_visible_delivery(
        self,
        event: AstrMessageEvent,
        *,
        plugin_context: Any = None,
        complete_visible_turn: Callable[[AstrMessageEvent], Awaitable[None]] | None = None,
        cancel_deferred_turn_finalization: Callable[..., Awaitable[None]] | None = None,
        flush_deferred_turn_finalization: Callable[[AstrMessageEvent], Awaitable[None]]
        | None = None,
        is_interaction_turn: bool = False,
    ) -> bool:
        if await call_event_hook(event, EventType.OnAfterMessageSentEvent):
            if cancel_deferred_turn_finalization is not None:
                await cancel_deferred_turn_finalization(
                    event,
                    reason="after_message_sent_hook_stopped",
                )
            return False

        completion = complete_visible_turn or self._complete_visible_turn
        await completion(event)
        self.schedule_after_message_sent_postprocess(
            event,
            plugin_context=plugin_context,
            is_interaction_turn=is_interaction_turn,
        )
        if flush_deferred_turn_finalization is not None:
            await flush_deferred_turn_finalization(event)
        return True

    @staticmethod
    async def _complete_visible_turn(event: AstrMessageEvent) -> None:
        await event.complete_visible_turn()

    def schedule_after_message_sent_postprocess(
        self,
        event: AstrMessageEvent,
        *,
        plugin_context: Any = None,
        is_interaction_turn: bool = False,
    ) -> None:
        self._schedule_postprocess(
            event,
            trigger=PostProcessTrigger.AFTER_MESSAGE_SENT,
            task_name=f"postprocess_after_message_sent_{event.get_platform_id()}",
            plugin_context=plugin_context,
        )
        if is_interaction_turn:
            return
        self._schedule_postprocess(
            event,
            trigger=PostProcessTrigger.AFTER_TURN_COMPLETED,
            task_name=f"postprocess_after_turn_completed_{event.get_platform_id()}",
            plugin_context=plugin_context,
        )

    def _schedule_postprocess(
        self,
        event: AstrMessageEvent,
        *,
        trigger: PostProcessTrigger,
        task_name: str,
        plugin_context: Any,
    ) -> None:
        provider_request = self.snapshot_provider_request(
            event.get_extra("provider_request")
        )
        conversation = (
            provider_request.conversation
            if getattr(provider_request, "conversation", None) is not None
            else event.get_extra("conversation")
        )
        task = get_postprocess_manager().schedule(
            dispatch_postprocess(
                event=event,
                trigger=trigger,
                plugin_context=plugin_context,
                provider_request=provider_request,
                conversation=copy(conversation) if conversation is not None else None,
                turn_id=str(event.get_extra("_turn_id", "") or ""),
                visible_outputs=[
                    dict(item)
                    for item in event.get_extra("_visible_turn_outputs", [])
                    if isinstance(item, dict)
                ],
                turn_material=(
                    dict(material)
                    if isinstance(
                        material := event.get_extra(
                            "_interaction_finalized_turn_material"
                        ),
                        dict,
                    )
                    else None
                ),
            ),
            name=task_name,
        )
        if task is not None:
            task.add_done_callback(
                lambda done_task: self._log_postprocess_failure(trigger, done_task)
            )

    @staticmethod
    def snapshot_provider_request(
        provider_request: ProviderRequest | None,
    ) -> ProviderRequest | None:
        if not isinstance(provider_request, ProviderRequest):
            return None
        snapshot = copy(provider_request)
        snapshot.image_urls = list(provider_request.image_urls or [])
        snapshot.audio_urls = list(provider_request.audio_urls or [])
        snapshot.extra_user_content_parts = list(
            provider_request.extra_user_content_parts or []
        )
        snapshot.contexts = [
            dict(item) if isinstance(item, dict) else item
            for item in (provider_request.contexts or [])
        ]
        if isinstance(provider_request.tool_calls_result, list):
            snapshot.tool_calls_result = list(provider_request.tool_calls_result)
        if provider_request.conversation is not None:
            snapshot.conversation = copy(provider_request.conversation)
        return snapshot

    @staticmethod
    def _log_postprocess_failure(
        trigger: PostProcessTrigger,
        task: asyncio.Task,
    ) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            logger.debug("postprocess(%s): background task cancelled", trigger.value)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "postprocess(%s): background task failed: %s",
                trigger.value,
                exc,
                exc_info=True,
            )


__all__ = ["PreOutputProcessor", "TurnDeliveryCoordinator"]
