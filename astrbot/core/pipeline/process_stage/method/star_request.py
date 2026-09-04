"""本地 Agent 模式的 AstrBot 插件调用 Stage"""

import time
import traceback
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any

from astrbot.core import logger
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.platform.astr_message_event import (
    INTERACTION_OUTPUT_CONTROLLER_EXTRA_KEY,
    AstrMessageEvent,
)
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import EventType, StarHandlerMetadata

from ...context import PipelineContext, call_event_hook, call_handler
from ...stage import Stage
from ..plugin_handler_executor import PluginHandlerControl


class StarRequestSubStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        self.prompt_prefix = ctx.astrbot_config["provider_settings"]["prompt_prefix"]
        self.identifier = ctx.astrbot_config["provider_settings"]["identifier"]
        self.ctx = ctx

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> AsyncGenerator[Any, PluginHandlerControl | None]:
        activated_handlers: list[StarHandlerMetadata] = event.get_extra(
            "activated_handlers",
        )
        handlers_parsed_params: dict[str, dict[str, Any]] = event.get_extra(
            "handlers_parsed_params",
        )
        if not handlers_parsed_params:
            handlers_parsed_params = {}

        for handler_index, handler in enumerate(activated_handlers):
            if event.is_stopped():
                break
            params = handlers_parsed_params.get(handler.handler_full_name, {})
            md = star_map.get(handler.handler_module_path)
            if not md:
                logger.warning(
                    f"Cannot find plugin for given handler module path: {handler.handler_module_path}",
                )
                continue
            logger.debug(f"plugin -> {md.name} - {handler.handler_name}")
            handler_invocation_id = (
                f"{handler.handler_full_name}:{handler_index}"
            )
            invocation_started_at = time.time()
            invocation_started_perf = time.perf_counter()
            provider_request_count = 0
            artifact_count_before = self._plugin_artifact_count(event)
            event.set_extra(
                "_interaction_plugin_handler_invocation_id",
                handler_invocation_id,
            )
            event.set_extra(
                "_interaction_plugin_handler_module_path",
                handler.handler_module_path,
            )
            event.set_extra(
                "_interaction_plugin_handler_name",
                handler.handler_name,
            )
            try:
                wrapper = call_handler(event, handler.handler, **params)
                async with aclosing(wrapper):
                    async for ret in wrapper:
                        if isinstance(ret, ProviderRequest):
                            provider_request_count += 1
                        control = yield ret
                        if (
                            control
                            is PluginHandlerControl.CLOSE_CURRENT_INVOCATION
                        ):
                            break
                if event.is_stopped():
                    break
                event.clear_result()  # 清除上一个 handler 的结果
            except Exception as e:
                traceback_text = traceback.format_exc()
                logger.error(traceback_text)
                logger.error(f"Star {handler.handler_full_name} handle error: {e}")

                await call_event_hook(
                    event,
                    EventType.OnPluginErrorEvent,
                    md.name,
                    handler.handler_name,
                    e,
                    traceback_text,
                )

                if not event.is_stopped() and event.is_at_or_wake_command:
                    ret = f":(\n\n在调用插件 {md.name} 的处理函数 {handler.handler_name} 时出现异常：{e}"
                    event.set_result(MessageEventResult().message(ret))
                    yield
                    event.clear_result()

                event.stop_event()
            finally:
                invocation_completed_at = time.time()
                artifact_count = max(
                    0,
                    self._plugin_artifact_count(event) - artifact_count_before,
                )
                logger.info(
                    "DIAG plugin.handler_invocation: platform_id=%s "
                    "session_id=%s origin_plugin_id=%s "
                    "origin_handler_name=%s handler_invocation_id=%s "
                    "started_at=%.6f completed_at=%.6f duration_ms=%.2f "
                    "provider_request_count=%d artifact_count=%d",
                    event.get_platform_id(),
                    event.session_id,
                    handler.handler_module_path,
                    handler.handler_name,
                    handler_invocation_id,
                    invocation_started_at,
                    invocation_completed_at,
                    (time.perf_counter() - invocation_started_perf) * 1000,
                    provider_request_count,
                    artifact_count,
                )
                event.set_extra(
                    "_interaction_plugin_handler_invocation_id",
                    None,
                )
                event.set_extra(
                    "_interaction_plugin_handler_module_path",
                    None,
                )
                event.set_extra(
                    "_interaction_plugin_handler_name",
                    None,
                )

    @staticmethod
    def _plugin_artifact_count(event: AstrMessageEvent) -> int:
        controller = event.get_extra(INTERACTION_OUTPUT_CONTROLLER_EXTRA_KEY)
        result = getattr(controller, "result", None)
        artifacts = getattr(result, "output_artifacts", None)
        return len(artifacts) if isinstance(artifacts, list) else 0
