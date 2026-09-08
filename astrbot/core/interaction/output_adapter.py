"""Compatibility adapter between the official Event send API and Interaction output."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from types import MethodType
from typing import TYPE_CHECKING, Any

from astrbot.core.agent.tool_output_capture import get_active_tool_output_capture
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import (
    INTERACTION_OUTPUT_CONTROLLER_EXTRA_KEY,
    AstrMessageEvent,
)

from .output_modes import OUTPUT_ORIGIN_EXTRA_KEY, OutputOrigin
from .turn_state import is_interaction_turn_pipeline_output_suppressed

if TYPE_CHECKING:
    from .output_controller import InteractionOutputController


class InteractionEventOutputAdapter:
    """Adapt official Event sends into one Interaction output transaction.

    The official Event API remains the compatibility surface for plugins and
    platform adapters. This class owns the private interception mechanics so
    Middleware only attaches the Interaction context and does not also own
    output routing.
    """

    @classmethod
    def install(
        cls,
        event: AstrMessageEvent,
        output_controller: InteractionOutputController,
    ) -> None:
        if event.get_extra("_interaction_output_interceptor_installed", False):
            return

        event.install_interaction_output_hooks(
            original_send=event.send,
            original_send_streaming=event.send_streaming,
            original_complete_visible_turn=event.complete_visible_turn,
        )
        event.set_extra(INTERACTION_OUTPUT_CONTROLLER_EXTRA_KEY, output_controller)
        event.send = MethodType(cls._send, event)
        event.send_streaming = MethodType(cls._send_streaming, event)
        event.complete_visible_turn = MethodType(cls._complete_visible_turn, event)

    @staticmethod
    def _get_controller(event: AstrMessageEvent) -> Any:
        controller = event.get_extra(INTERACTION_OUTPUT_CONTROLLER_EXTRA_KEY)
        if controller is None:
            raise RuntimeError("Interaction output controller is unavailable")
        return controller

    @staticmethod
    async def _send(
        event: AstrMessageEvent,
        message: MessageChain | None,
    ) -> None:
        capture = get_active_tool_output_capture()
        if capture is not None:
            capture.capture(message)
            return

        previous_has_send_oper = event._has_send_oper
        controller = InteractionEventOutputAdapter._get_controller(event)
        origin = event.get_extra(OUTPUT_ORIGIN_EXTRA_KEY)
        if origin == OutputOrigin.CORE.value:
            await controller.capture_message_chain(message, event)
        else:
            await controller.capture_plugin_output(
                message,
                event,
                mode=event.get_extra("_interaction_plugin_output_mode", "direct"),
            )
        event._has_send_oper = (
            previous_has_send_oper
            if is_interaction_turn_pipeline_output_suppressed(event)
            else True
        )

    @staticmethod
    async def _send_streaming(
        event: AstrMessageEvent,
        generator: AsyncGenerator[MessageChain, None],
        use_fallback: bool = False,
    ) -> None:
        capture = get_active_tool_output_capture()
        if capture is not None:
            await capture.capture_stream(generator)
            return

        controller = InteractionEventOutputAdapter._get_controller(event)
        origin = event.get_extra(OUTPUT_ORIGIN_EXTRA_KEY)
        if origin == OutputOrigin.CORE.value:
            await controller.capture_streaming(
                generator,
                event,
                use_fallback=use_fallback,
            )
        else:
            await controller.capture_plugin_streaming(
                generator,
                event,
                mode=event.get_extra("_interaction_plugin_output_mode", "direct"),
                use_fallback=use_fallback,
            )
        event._has_send_oper = True

    @staticmethod
    async def _complete_visible_turn(event: AstrMessageEvent) -> None:
        await InteractionEventOutputAdapter._get_controller(
            event
        ).capture_visible_completion(event)


__all__ = ["InteractionEventOutputAdapter"]
