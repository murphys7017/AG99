from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent_lifecycle_scope import (
    _MISSING as LIFECYCLE_MISSING,
)
from astrbot.core.agent_lifecycle_scope import (
    activate_agent_lifecycle,
    create_agent_lifecycle_overlay,
    get_active_agent_lifecycle,
)
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.plugin_runtime import PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION
from astrbot.core.postprocess import dispatch_postprocess
from astrbot.core.postprocess.types import PostProcessTrigger
from astrbot.core.prompt.render import PROMPT_APPLY_RESULT_EXTRA_KEY
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.star.star_handler import EventType

_MISSING = object()


class AgentRequestLifecycle:
    """Owns one final request's public plugin lifecycle."""

    def __init__(
        self,
        event,
        *,
        execution_surface: str,
        provider_request: ProviderRequest | None = None,
        prompt_apply_result: object = _MISSING,
        hook_dispatcher: Callable[..., Any] = call_event_hook,
        record_reasoning: bool = False,
        dispatch_response_postprocess: bool = False,
    ) -> None:
        self.event = event
        self.execution_surface = execution_surface
        self.provider_request = provider_request
        self.prompt_apply_result = prompt_apply_result
        self.hook_dispatcher = hook_dispatcher
        self.record_reasoning = record_reasoning
        self.dispatch_response_postprocess = dispatch_response_postprocess
        self.lifecycle_id = uuid.uuid4().hex
        self.tool_execution_count = 0
        self._waiting_dispatched = False
        self._request_dispatched = False
        self._agent_begin_dispatched = False
        self._agent_done_dispatched = False
        self._waiting_stopped = False
        self._request_stopped = False
        self._agent_begin_stopped = False
        self._agent_done_stopped = False
        self._overlay = (
            create_agent_lifecycle_overlay(event)
            if execution_surface == PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION
            and getattr(event, "_supports_agent_lifecycle_overlay", False)
            else None
        )

    def bind_request(
        self,
        provider_request: ProviderRequest,
        *,
        prompt_apply_result: object = _MISSING,
    ) -> None:
        self.provider_request = provider_request
        if prompt_apply_result is not _MISSING:
            self.prompt_apply_result = prompt_apply_result

    @contextmanager
    def expose_request(self) -> Iterator[None]:
        with self._activate_scope():
            request = self.provider_request
            if request is None:
                yield
                return

            previous_request = self._capture_extra("provider_request")
            previous_apply_result = self._capture_extra(
                PROMPT_APPLY_RESULT_EXTRA_KEY,
            )
            self.event.set_extra("provider_request", request)
            if self.prompt_apply_result is not _MISSING:
                self.event.set_extra(
                    PROMPT_APPLY_RESULT_EXTRA_KEY,
                    self.prompt_apply_result,
                )
            try:
                yield
            finally:
                self._restore_extra("provider_request", previous_request)
                if self.prompt_apply_result is not _MISSING:
                    self._restore_extra(
                        PROMPT_APPLY_RESULT_EXTRA_KEY,
                        previous_apply_result,
                    )

    async def dispatch_waiting(self) -> bool:
        if self._waiting_dispatched:
            return self._waiting_stopped
        self._waiting_dispatched = True
        with self.expose_request():
            self._waiting_stopped = bool(
                await self.hook_dispatcher(
                    self.event,
                    EventType.OnWaitingLLMRequestEvent,
                    execution_surface=self.execution_surface,
                )
            )
        return self._waiting_stopped

    async def dispatch_request(self) -> bool:
        if self._request_dispatched:
            return self._request_stopped
        if self.provider_request is None:
            raise RuntimeError("provider request is not bound")
        self._request_dispatched = True
        with self.expose_request():
            self._request_stopped = bool(
                await self.hook_dispatcher(
                    self.event,
                    EventType.OnLLMRequestEvent,
                    self.provider_request,
                    execution_surface=self.execution_surface,
                )
            )
        return self._request_stopped

    async def dispatch_agent_begin(self, run_context: ContextWrapper) -> bool:
        if self._agent_begin_dispatched:
            return self._agent_begin_stopped
        self._agent_begin_dispatched = True
        with self.expose_request():
            self._agent_begin_stopped = bool(
                await self.hook_dispatcher(
                    self.event,
                    EventType.OnAgentBeginEvent,
                    run_context,
                    execution_surface=self.execution_surface,
                )
            )
        return self._agent_begin_stopped

    async def dispatch_agent_done(
        self,
        run_context: ContextWrapper,
        llm_response: LLMResponse,
    ) -> bool:
        if self._agent_done_dispatched:
            return self._agent_done_stopped
        self._agent_done_dispatched = True

        with self.expose_request():
            if self.record_reasoning and llm_response.reasoning_content:
                self.event.set_extra(
                    "_llm_reasoning_content",
                    llm_response.reasoning_content,
                )
            response_stopped = bool(
                await self.hook_dispatcher(
                    self.event,
                    EventType.OnLLMResponseEvent,
                    llm_response,
                    execution_surface=self.execution_surface,
                )
            )
            done_stopped = bool(
                await self.hook_dispatcher(
                    self.event,
                    EventType.OnAgentDoneEvent,
                    run_context,
                    llm_response,
                    execution_surface=self.execution_surface,
                )
            )
        self._agent_done_stopped = response_stopped or done_stopped

        if (
            self.dispatch_response_postprocess
            and not self._agent_done_stopped
            and not self.is_stopped()
        ):
            await dispatch_postprocess(
                event=self.event,
                trigger=PostProcessTrigger.ON_LLM_RESPONSE,
                llm_response=llm_response,
                plugin_context=run_context.context.context,
            )
        return self._agent_done_stopped

    async def dispatch_tool_start(
        self,
        tool: FunctionTool,
        tool_args: dict | None,
    ) -> None:
        self.tool_execution_count += 1
        with self.expose_request():
            await self.hook_dispatcher(
                self.event,
                EventType.OnUsingLLMToolEvent,
                tool,
                tool_args,
                execution_surface=self.execution_surface,
            )

    async def dispatch_tool_end(
        self,
        tool: FunctionTool,
        tool_args: dict | None,
        tool_result,
    ) -> None:
        with self.expose_request():
            self.event.clear_result()
            await self.hook_dispatcher(
                self.event,
                EventType.OnLLMToolRespondEvent,
                tool,
                tool_args,
                tool_result,
                execution_surface=self.execution_surface,
            )

    def _restore_extra(self, key: str, previous: object) -> None:
        overlay = get_active_agent_lifecycle(self.event)
        if overlay is not None:
            overlay.restore_extra(key, previous)
            return
        if previous is not _MISSING:
            self.event.set_extra(key, previous)
            return
        extras = getattr(self.event, "_extras", None)
        if isinstance(extras, dict):
            extras.pop(key, None)
        else:
            self.event.set_extra(key, None)

    def _capture_extra(self, key: str) -> object:
        overlay = get_active_agent_lifecycle(self.event)
        if overlay is not None:
            return overlay.capture_extra(key)
        return self.event.get_extra(key, _MISSING)

    @contextmanager
    def _activate_scope(self) -> Iterator[None]:
        if self._overlay is None:
            yield
            return
        with activate_agent_lifecycle(self._overlay):
            yield

    def is_stopped(self) -> bool:
        """Return this lifecycle's stop state without reading a sibling scope."""

        if self._overlay is not None:
            if self._overlay.force_stopped:
                return True
            result = (
                self._overlay.result
                if self._overlay.result is not LIFECYCLE_MISSING
                else self._overlay.initial_result
            )
            if result is None:
                return self._overlay.initial_stopped
            return result.is_stopped()
        return self.event.is_stopped()


class AgentRequestLifecycleHooks(BaseAgentRunHooks):
    """Adapt one request lifecycle to the shared Agent runner hooks."""

    def __init__(
        self,
        lifecycle: AgentRequestLifecycle,
        *,
        dispatch_agent_stages: bool = True,
    ) -> None:
        self.lifecycle = lifecycle
        self.dispatch_agent_stages = dispatch_agent_stages

    async def on_agent_begin(self, run_context: ContextWrapper) -> None:
        if self.dispatch_agent_stages:
            await self.lifecycle.dispatch_agent_begin(run_context)

    async def on_agent_done(
        self,
        run_context: ContextWrapper,
        llm_response: LLMResponse,
    ) -> None:
        if self.dispatch_agent_stages:
            await self.lifecycle.dispatch_agent_done(run_context, llm_response)

    async def on_tool_start(
        self,
        run_context: ContextWrapper,
        tool: FunctionTool,
        tool_args: dict | None,
    ) -> None:
        del run_context
        await self.lifecycle.dispatch_tool_start(tool, tool_args)

    async def on_tool_end(
        self,
        run_context: ContextWrapper,
        tool: FunctionTool,
        tool_args: dict | None,
        tool_result,
    ) -> None:
        del run_context
        await self.lifecycle.dispatch_tool_end(tool, tool_args, tool_result)


__all__ = ["AgentRequestLifecycle", "AgentRequestLifecycleHooks"]
