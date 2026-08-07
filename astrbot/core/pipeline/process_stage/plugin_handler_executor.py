from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from enum import Enum
from typing import Any, Protocol

from astrbot import logger
from astrbot.core.interaction.plugin_execution_types import (
    PluginArtifactKind,
    PluginBranchResult,
    PluginGateResolution,
    PluginJobState,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest

PLUGIN_HANDLER_EXECUTION_METRICS_EXTRA = "_interaction_plugin_handler_execution_metrics"


class PluginHandlerControl(Enum):
    CLOSE_CURRENT_INVOCATION = "close_current_invocation"


class PluginHandlerSource(Protocol):
    def process(
        self,
        event: AstrMessageEvent,
    ) -> AsyncGenerator[Any, PluginHandlerControl | None]: ...


class PluginOutputTransactionController(Protocol):
    async def finalize_plugin_output_transaction(
        self,
        event: AstrMessageEvent,
        *,
        delegated_to_core: bool,
    ) -> None: ...


class ProviderRequestSubmission(Protocol):
    def set_provider_request(self, provider_request: ProviderRequest) -> None: ...


class AgentTurnRunner(Protocol):
    def __call__(
        self,
        event: AstrMessageEvent,
        *,
        ensure_yield: bool = False,
    ) -> AsyncGenerator[None, None]: ...


ProviderRequestDelegate = Callable[[ProviderRequest], Awaitable[None]]


class PluginHandlerExecutor:
    """Own the legacy Handler generator and ProviderRequest lifecycle."""

    def __init__(self, handler_source: PluginHandlerSource) -> None:
        self.handler_source = handler_source

    async def process(
        self,
        event: AstrMessageEvent,
        *,
        output_controller: PluginOutputTransactionController | None,
        submission: ProviderRequestSubmission | None,
        run_agent_turn: AgentTurnRunner | None,
        result: PluginBranchResult,
        publish_gate: Callable[[PluginGateResolution], object] | None = None,
        delegate_provider_request: ProviderRequestDelegate | None = None,
    ) -> AsyncGenerator[None, None]:
        def resolve_gate(
            resolution: PluginGateResolution,
        ) -> PluginGateResolution:
            if result.gate_resolution is PluginGateResolution.PENDING:
                if publish_gate is None:
                    result.gate_resolution = resolution
                else:
                    published = publish_gate(resolution)
                    if isinstance(published, PluginGateResolution):
                        result.gate_resolution = published
                if result.gate_resolution is PluginGateResolution.HANDLED:
                    result.freeze_t1_artifact_boundary()
            return result.gate_resolution

        event.set_extra(
            "_interaction_plugin_output_transaction_active",
            True,
        )
        result.started_at = time.time()
        started_perf = time.perf_counter()
        try:
            plugin_source = self.handler_source.process(event)
            source_started = False
            next_control: PluginHandlerControl | None = None
            async with aclosing(plugin_source):
                while True:
                    try:
                        if source_started:
                            response = await plugin_source.asend(next_control)
                        else:
                            response = await anext(plugin_source)
                            source_started = True
                    except StopAsyncIteration:
                        break
                    next_control = None
                    if not isinstance(response, ProviderRequest):
                        capture_pipeline_result = getattr(
                            output_controller,
                            "capture_pipeline_result",
                            None,
                        )
                        if callable(capture_pipeline_result):
                            capture_pipeline_result(event)
                        yield
                        continue

                    result.delegated_to_core = True
                    gate_resolution = resolve_gate(PluginGateResolution.DELEGATED)
                    if gate_resolution is not PluginGateResolution.DELEGATED:
                        result.delegated_to_core = False
                        if gate_resolution is PluginGateResolution.EXPIRED:
                            result.ignored_provider_requests_after_detach += 1
                            logger.warning(
                                "Plugin ProviderRequest ignored after detach: "
                                "platform_id=%s session_id=%s",
                                event.get_platform_id(),
                                event.session_id,
                            )
                        else:
                            logger.warning(
                                "Plugin ProviderRequest ignored after terminal gate: "
                                "platform_id=%s session_id=%s gate=%s",
                                event.get_platform_id(),
                                event.session_id,
                                gate_resolution.value,
                            )
                        next_control = PluginHandlerControl.CLOSE_CURRENT_INVOCATION
                        continue
                    result.provider_executions += 1
                    if output_controller is not None:
                        await output_controller.finalize_plugin_output_transaction(
                            event,
                            delegated_to_core=True,
                        )
                    event.set_extra("provider_request", response)
                    if submission is not None:
                        submission.set_provider_request(response)
                    if delegate_provider_request is not None:
                        await delegate_provider_request(response)
                    else:
                        if run_agent_turn is None:
                            raise RuntimeError(
                                "Plugin ProviderRequest has no Core execution boundary"
                            )
                        agent_source = run_agent_turn(event, ensure_yield=True)
                        async with aclosing(agent_source):
                            async for _ in agent_source:
                                yield
            if result.gate_resolution is PluginGateResolution.PENDING:
                if event.is_stopped():
                    resolve_gate(PluginGateResolution.STOPPED)
                elif any(
                    artifact.finalize
                    and artifact.kind is not PluginArtifactKind.PROGRESS
                    for artifact in result.output_artifacts
                ):
                    resolve_gate(PluginGateResolution.HANDLED)
                else:
                    resolve_gate(PluginGateResolution.PASSED)
            result.job_state = PluginJobState.COMPLETED
        except asyncio.CancelledError:
            result.job_state = PluginJobState.CANCELLED
            if result.gate_resolution is PluginGateResolution.PENDING:
                resolve_gate(PluginGateResolution.FAILED)
            raise
        except BaseException as exc:
            result.job_state = PluginJobState.FAILED
            if result.gate_resolution is PluginGateResolution.PENDING:
                resolve_gate(PluginGateResolution.FAILED)
            result.failure = exc
            raise
        finally:
            try:
                if output_controller is not None and not result.delegated_to_core:
                    await output_controller.finalize_plugin_output_transaction(
                        event,
                        delegated_to_core=False,
                    )
            finally:
                result.completed_at = time.time()
                result.duration_ms = (time.perf_counter() - started_perf) * 1000
                result.stopped = event.is_stopped()
                metrics = {
                    "started_at": result.started_at,
                    "completed_at": result.completed_at,
                    "duration_ms": result.duration_ms,
                    "activated_handler_count": len(
                        event.get_extra("activated_handlers", []) or []
                    ),
                    "provider_request_count": result.provider_executions,
                    "ignored_provider_requests_after_detach": (
                        result.ignored_provider_requests_after_detach
                    ),
                    "delegated_to_core": result.delegated_to_core,
                }
                event.set_extra(PLUGIN_HANDLER_EXECUTION_METRICS_EXTRA, metrics)
                logger.info(
                    "DIAG plugin.handler_execution: platform_id=%s session_id=%s "
                    "activated_handlers=%d provider_requests=%d "
                    "delegated_to_core=%s ignored_after_detach=%d "
                    "duration_ms=%.2f",
                    event.get_platform_id(),
                    event.session_id,
                    metrics["activated_handler_count"],
                    result.provider_executions,
                    result.delegated_to_core,
                    result.ignored_provider_requests_after_detach,
                    result.duration_ms,
                )
