from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from astrbot.core.agent_lifecycle import AgentRequestLifecycle
from astrbot.core.execution import bind_effective_core_request
from astrbot.core.interaction.turn_state import (
    set_interaction_turn_core_execution_spec,
)
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.plugin_runtime import PLUGIN_RUNTIME_TARGET_CORE

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildResult


class CoreRequestPreparationStopped(RuntimeError):
    """Raised when a proactive Core request is stopped by a public plugin hook."""


async def begin_core_request_lifecycle(
    event: AstrMessageEvent,
    *,
    hook_dispatcher: Callable[..., Any] = call_event_hook,
) -> AgentRequestLifecycle | None:
    """Start the public Core request lifecycle before building the request."""

    lifecycle = AgentRequestLifecycle(
        event,
        execution_surface=PLUGIN_RUNTIME_TARGET_CORE,
        hook_dispatcher=hook_dispatcher,
        record_reasoning=True,
        dispatch_response_postprocess=True,
    )
    if await lifecycle.dispatch_waiting():
        return None
    return lifecycle


async def finalize_core_request_preparation(
    event: AstrMessageEvent,
    build_result: MainAgentBuildResult,
) -> bool:
    """Apply request hooks and synchronize the effective Core request contract."""

    lifecycle = build_result.request_lifecycle
    if lifecycle is None:
        raise RuntimeError("Core request lifecycle is not bound")
    if build_result.reset_coro is None:
        raise RuntimeError("Core runner reset was not deferred")
    lifecycle.bind_request(build_result.provider_request)
    if await lifecycle.dispatch_request():
        return False

    effective_capabilities, effective_execution_spec = bind_effective_core_request(
        event=event,
        provider_request=build_result.provider_request,
        persona_id=(
            build_result.capabilities.persona_id
            if build_result.capabilities is not None
            else None
        ),
        execution_spec=build_result.execution_spec,
        prompt_apply_result=lifecycle.prompt_apply_result,
    )
    build_result.capabilities = effective_capabilities
    build_result.execution_spec = effective_execution_spec
    if (
        build_result.prepared_execution is not None
        and effective_execution_spec is not None
    ):
        build_result.prepared_execution = replace(
            build_result.prepared_execution,
            execution_spec=effective_execution_spec,
        )
    if effective_execution_spec is not None:
        set_interaction_turn_core_execution_spec(event, effective_execution_spec)
    return True


__all__ = [
    "CoreRequestPreparationStopped",
    "begin_core_request_lifecycle",
    "finalize_core_request_preparation",
]
