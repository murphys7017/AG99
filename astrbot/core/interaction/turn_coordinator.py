from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from astrbot import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest

from .plugin_execution_runtime import (
    PluginExecutionJob,
    PluginExecutionRuntime,
    PluginGatePublisher,
    PluginJobCompletionHandler,
    PluginModuleDrainingError,
)
from .plugin_execution_types import (
    PluginBranchResult,
    PluginGateResolution,
    PluginJobState,
)
from .turn_state import (
    InteractionSpeculativePersonaStatus,
    ensure_interaction_turn_state,
    get_interaction_turn_personal_emitted_monotonic,
    publish_interaction_turn_route_decision,
    suppress_interaction_turn_pending_persona,
)

TurnTaskFactory = Callable[[], Awaitable[Any]]
PluginProviderRequestSubmitter = Callable[[ProviderRequest], Awaitable[None]]
CoordinatedPluginJobRunner = Callable[
    [PluginGatePublisher, PluginProviderRequestSubmitter],
    Awaitable[None],
]


class PluginProviderRequestRejected(RuntimeError):
    """Raised when a Plugin Job can no longer delegate Core work to T1."""


@dataclass(slots=True)
class CoordinatedProviderRequest:
    request: ProviderRequest
    _completion: asyncio.Future[None]

    def complete(self) -> None:
        if not self._completion.done():
            self._completion.set_result(None)

    def fail(self, error: BaseException) -> None:
        if not self._completion.done():
            self._completion.set_exception(error)


class PluginProviderRequestBridge:
    """Rendezvous between a Runtime-owned Plugin Job and T1 Core execution."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[CoordinatedProviderRequest | None] = asyncio.Queue()
        self._closed_error: BaseException | None = None

    async def submit(self, request: ProviderRequest) -> None:
        if self._closed_error is not None:
            raise PluginProviderRequestRejected(str(self._closed_error))
        loop = asyncio.get_running_loop()
        completion = loop.create_future()
        command = CoordinatedProviderRequest(request, completion)
        await self._queue.put(command)
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError:
            completion.cancel()
            self.close(
                PluginProviderRequestRejected(
                    "Plugin ProviderRequest submitter was cancelled"
                )
            )
            raise

    async def receive(self) -> CoordinatedProviderRequest:
        if self._closed_error is not None and self._queue.empty():
            raise PluginProviderRequestRejected(str(self._closed_error))
        command = await self._queue.get()
        if command is None:
            raise PluginProviderRequestRejected(str(self._closed_error))
        return command

    def close(self, error: BaseException | None = None) -> None:
        if self._closed_error is not None:
            return
        self._closed_error = error or PluginProviderRequestRejected(
            "Plugin ProviderRequest bridge is closed"
        )
        while not self._queue.empty():
            command = self._queue.get_nowait()
            if command is not None:
                command.fail(self._closed_error)
        self._queue.put_nowait(None)


@dataclass(slots=True)
class PluginJobLaunch:
    branch_event: AstrMessageEvent
    result: PluginBranchResult
    run_job: CoordinatedPluginJobRunner
    module_paths: tuple[str, ...] = ()
    completion_handler: PluginJobCompletionHandler | None = None


@dataclass(slots=True)
class InteractionControlResolution:
    route: Any | None
    plugin_gate: PluginGateResolution
    router_completed_monotonic: float | None
    plugin_resolved_monotonic: float
    core_gate_monotonic: float

    @property
    def core_start_delay_due_to_plugin_ms(self) -> float:
        if self.router_completed_monotonic is None:
            return 0.0
        return max(
            0.0,
            self.plugin_resolved_monotonic - self.router_completed_monotonic,
        ) * 1000


@dataclass(slots=True)
class InteractionCoordinatedTurn:
    event: AstrMessageEvent
    t0_monotonic: float
    plugin_window_deadline_monotonic: float
    personal_task: asyncio.Task[Any]
    router_task: asyncio.Task[Any]
    plugin_job: PluginExecutionJob | None
    plugin_watcher_task: asyncio.Task[PluginGateResolution] | None
    provider_request_bridge: PluginProviderRequestBridge
    task_started_at: dict[str, float] = field(default_factory=dict)
    task_completed_at: dict[str, float] = field(default_factory=dict)

    async def wait_for_plugin_gate(self) -> PluginGateResolution:
        if self.plugin_watcher_task is None:
            return PluginGateResolution.PASSED
        return await self.plugin_watcher_task

    async def receive_provider_request(self) -> CoordinatedProviderRequest:
        return await self.provider_request_bridge.receive()

    def close_provider_requests(self, error: BaseException | None = None) -> None:
        self.provider_request_bridge.close(error)


class InteractionTurnCoordinator:
    """Create the concurrent owners for one ordinary Interaction turn."""

    def __init__(self, plugin_runtime: PluginExecutionRuntime) -> None:
        self.plugin_runtime = plugin_runtime

    async def start(
        self,
        event: AstrMessageEvent,
        *,
        personal_factory: TurnTaskFactory,
        router_factory: TurnTaskFactory,
        plugin_window_seconds: float,
        plugin_launch: PluginJobLaunch | None = None,
    ) -> InteractionCoordinatedTurn:
        turn_state = ensure_interaction_turn_state(event)
        loop = asyncio.get_running_loop()
        task_started_at: dict[str, float] = {}
        task_completed_at: dict[str, float] = {}
        provider_bridge = PluginProviderRequestBridge()

        async def run_turn_task(role: str, factory: TurnTaskFactory) -> Any:
            task_started_at[role] = loop.time()
            try:
                return await factory()
            finally:
                task_completed_at[role] = loop.time()

        module_lease = None
        if plugin_launch is not None:
            try:
                module_lease = self.plugin_runtime.acquire_module_lease(
                    plugin_launch.module_paths
                )
            except PluginModuleDrainingError as exc:
                logger.warning(
                    "DIAG interaction.plugin_job_skipped: platform_id=%s "
                    "session_id=%s turn_id=%s reason=module_draining "
                    "module_path=%s",
                    event.get_platform_id(),
                    event.session_id,
                    turn_state.turn_id,
                    exc.module_path,
                )
                event.set_extra(
                    "_interaction_plugin_launch_skipped_reason",
                    "module_draining",
                )
                event.set_extra(
                    "_interaction_plugin_draining_module_path",
                    exc.module_path,
                )
                plugin_launch.result.gate_resolution = PluginGateResolution.PASSED
                plugin_launch.result.job_state = PluginJobState.COMPLETED
                plugin_launch.result.cleanup_media()
                plugin_launch = None
        if (
            turn_state.speculative_persona_status
            is InteractionSpeculativePersonaStatus.NOT_STARTED
        ):
            turn_state.speculative_persona_status = (
                InteractionSpeculativePersonaStatus.PENDING
            )
        elif (
            turn_state.speculative_persona_status
            is not InteractionSpeculativePersonaStatus.PENDING
        ):
            if module_lease is not None:
                await module_lease.release()
            raise RuntimeError(
                "Coordinated turn requires a fresh pending Personal task"
            )
        t0 = loop.time()
        effective_window_seconds = (
            max(0.0, float(plugin_window_seconds))
            if plugin_launch is not None
            else 0.0
        )
        deadline = t0 + effective_window_seconds
        personal_task = turn_state.execution_scope.create_task(
            run_turn_task("personal", personal_factory),
            role="speculative_persona",
            name=(
                f"interaction_speculative_persona_{event.get_platform_id()}_"
                f"{turn_state.turn_id}"
            ),
        )
        router_task = turn_state.execution_scope.create_task(
            run_turn_task("router", router_factory),
            role="router",
            name=(
                f"interaction_router_{event.get_platform_id()}_"
                f"{turn_state.turn_id}"
            ),
        )

        plugin_job = None
        plugin_watcher_task = None
        try:
            if plugin_launch is not None:

                async def run_plugin_job(
                    publish_gate: PluginGatePublisher,
                ) -> None:
                    task_started_at["plugin"] = loop.time()
                    try:
                        await plugin_launch.run_job(
                            publish_gate,
                            provider_bridge.submit,
                        )
                    finally:
                        task_completed_at["plugin"] = loop.time()

                plugin_job = self.plugin_runtime.start(
                    branch_event=plugin_launch.branch_event,
                    result=plugin_launch.result,
                    run_job=run_plugin_job,
                    release_leases=(
                        module_lease.release if module_lease is not None else None
                    ),
                    completion_handler=plugin_launch.completion_handler,
                )
                if module_lease is not None:
                    module_lease.bind_job(plugin_job.job_id)
                plugin_watcher_task = turn_state.execution_scope.create_task(
                    plugin_job.wait_for_gate(deadline),
                    role="plugin_window",
                    name=(
                        f"interaction_plugin_window_{event.get_platform_id()}_"
                        f"{turn_state.turn_id}"
                    ),
                )
        except BaseException:
            personal_task.cancel()
            router_task.cancel()
            provider_bridge.close()
            await asyncio.gather(
                personal_task,
                router_task,
                return_exceptions=True,
            )
            if module_lease is not None and not module_lease.released:
                await module_lease.release()
            raise

        logger.debug(
            "DIAG interaction.parallel_start: platform_id=%s session_id=%s "
            "turn_id=%s t0=%.6f plugin_job=%s plugin_window_ms=%.2f",
            event.get_platform_id(),
            event.session_id,
            turn_state.turn_id,
            t0,
            plugin_job.job_id if plugin_job is not None else "",
            effective_window_seconds * 1000,
        )
        return InteractionCoordinatedTurn(
            event=event,
            t0_monotonic=t0,
            plugin_window_deadline_monotonic=deadline,
            personal_task=personal_task,
            router_task=router_task,
            plugin_job=plugin_job,
            plugin_watcher_task=plugin_watcher_task,
            provider_request_bridge=provider_bridge,
            task_started_at=task_started_at,
            task_completed_at=task_completed_at,
        )

    def log_turn_diagnostics(
        self,
        turn: InteractionCoordinatedTurn,
        *,
        phase: str,
        control: InteractionControlResolution | None,
    ) -> None:
        turn_state = ensure_interaction_turn_state(turn.event)
        route = control.route if control is not None else turn_state.route_decision
        route_mode = getattr(route, "route_mode", None)
        route_mode_value = getattr(route_mode, "value", "")
        plugin_result = turn.plugin_job.result if turn.plugin_job is not None else None
        plugin_gate = (
            control.plugin_gate
            if control is not None
            else (
                plugin_result.gate_resolution
                if plugin_result is not None
                else PluginGateResolution.PASSED
            )
        )
        plugin_resolved_at = (
            control.plugin_resolved_monotonic
            if control is not None
            else (
                plugin_result.gate_resolved_monotonic
                if plugin_result is not None
                else turn.t0_monotonic
            )
        )
        core_gate_at = (
            control.core_gate_monotonic if control is not None else None
        )
        plugin_delay_ms = (
            control.core_start_delay_due_to_plugin_ms
            if control is not None
            else 0.0
        )
        plugin_job_state = (
            plugin_result.job_state
            if plugin_result is not None
            else None
        )
        logger.debug(
            "DIAG interaction.parallel_turn: phase=%s platform_id=%s "
            "session_id=%s turn_id=%s t0=%.6f "
            "personal_started_at=%s personal_completed_at=%s "
            "personal_emitted_at=%s personal_status=%s "
            "router_started_at=%s router_completed_at=%s route_mode=%s "
            "plugin_job_id=%s plugin_started_at=%s "
            "plugin_window_deadline=%.6f plugin_resolved_at=%s "
            "plugin_gate=%s plugin_completed_at=%s plugin_job_state=%s "
            "delegated_t1_failure_type=%s "
            "core_gate_at=%s core_gate_reason=%s plugin_delay_ms=%.2f",
            phase,
            turn.event.get_platform_id(),
            turn.event.session_id,
            turn_state.turn_id,
            turn.t0_monotonic,
            self._format_diagnostic_time(turn.task_started_at.get("personal")),
            self._format_diagnostic_time(turn.task_completed_at.get("personal")),
            self._format_diagnostic_time(
                get_interaction_turn_personal_emitted_monotonic(turn.event)
            ),
            turn_state.speculative_persona_status.value,
            self._format_diagnostic_time(turn.task_started_at.get("router")),
            self._format_diagnostic_time(turn.task_completed_at.get("router")),
            route_mode_value,
            turn.plugin_job.job_id if turn.plugin_job is not None else "",
            self._format_diagnostic_time(turn.task_started_at.get("plugin")),
            turn.plugin_window_deadline_monotonic,
            self._format_diagnostic_time(plugin_resolved_at),
            plugin_gate.value,
            self._format_diagnostic_time(turn.task_completed_at.get("plugin")),
            plugin_result.job_state.value if plugin_result is not None else "none",
            (
                plugin_result.delegated_t1_failure_type
                if plugin_result is not None
                else ""
            )
            or "none",
            self._format_diagnostic_time(core_gate_at),
            self._core_gate_reason(
                plugin_gate,
                route_mode,
                plugin_job_state,
            ),
            plugin_delay_ms,
        )

    @staticmethod
    def _format_diagnostic_time(value: float | None) -> str:
        return f"{value:.6f}" if value is not None else "none"

    @staticmethod
    def _core_gate_reason(
        plugin_gate: PluginGateResolution,
        route_mode: Any,
        plugin_job_state: PluginJobState | None = None,
    ) -> str:
        route_value = getattr(route_mode, "value", "")
        if plugin_gate is PluginGateResolution.FAILED:
            route_reason = f"router_{route_value}" if route_value else "router_unresolved"
            if plugin_job_state is PluginJobState.CANCELLED:
                return f"plugin_cancelled_open_{route_reason}"
            return f"plugin_failed_open_{route_reason}"
        if plugin_gate not in {
            PluginGateResolution.PASSED,
            PluginGateResolution.EXPIRED,
        }:
            return f"plugin_{plugin_gate.value}"
        return f"router_{route_value}" if route_value else "router_unresolved"

    async def resolve_control(
        self,
        turn: InteractionCoordinatedTurn,
    ) -> InteractionControlResolution:
        """Resolve Router plus Plugin Gate without waiting for Plugin completion."""
        loop = asyncio.get_running_loop()
        turn_state = ensure_interaction_turn_state(turn.event)
        router_completed_at: float | None = None
        gate_task = turn.plugin_watcher_task
        if gate_task is None:
            route = await turn.router_task
            router_completed_at = turn.task_completed_at.get("router", loop.time())
            await publish_interaction_turn_route_decision(
                turn.event,
                route,
                turn.personal_task,
            )
            return InteractionControlResolution(
                route=route,
                plugin_gate=PluginGateResolution.PASSED,
                router_completed_monotonic=router_completed_at,
                plugin_resolved_monotonic=turn.t0_monotonic,
                core_gate_monotonic=router_completed_at,
            )

        done, _ = await asyncio.wait(
            {turn.router_task, gate_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        route: Any | None = None
        if gate_task in done:
            plugin_gate = await gate_task
            plugin_resolved_at = (
                turn.plugin_job.result.gate_resolved_monotonic
                if turn.plugin_job is not None
                and turn.plugin_job.result.gate_resolved_monotonic is not None
                else loop.time()
            )
            if plugin_gate in {
                PluginGateResolution.HANDLED,
                PluginGateResolution.STOPPED,
                PluginGateResolution.DELEGATED,
            }:
                turn_state.execution_scope.cancel_and_detach(
                    "router",
                    turn.router_task,
                )
                await suppress_interaction_turn_pending_persona(
                    turn.event,
                    turn.personal_task,
                )
                return InteractionControlResolution(
                    route=None,
                    plugin_gate=plugin_gate,
                    router_completed_monotonic=turn.task_completed_at.get("router"),
                    plugin_resolved_monotonic=plugin_resolved_at,
                    core_gate_monotonic=plugin_resolved_at,
                )

        if turn.router_task in done:
            route = await turn.router_task
            router_completed_at = turn.task_completed_at.get("router", loop.time())
            await publish_interaction_turn_route_decision(
                turn.event,
                route,
                turn.personal_task,
            )

        plugin_gate = await gate_task
        plugin_resolved_at = (
            turn.plugin_job.result.gate_resolved_monotonic
            if turn.plugin_job is not None
            and turn.plugin_job.result.gate_resolved_monotonic is not None
            else loop.time()
        )
        if plugin_gate in {
            PluginGateResolution.HANDLED,
            PluginGateResolution.STOPPED,
            PluginGateResolution.DELEGATED,
        }:
            turn_state.execution_scope.cancel_and_detach(
                "router",
                turn.router_task,
            )
            await suppress_interaction_turn_pending_persona(
                turn.event,
                turn.personal_task,
            )
            return InteractionControlResolution(
                route=None,
                plugin_gate=plugin_gate,
                router_completed_monotonic=turn.task_completed_at.get("router"),
                plugin_resolved_monotonic=plugin_resolved_at,
                core_gate_monotonic=plugin_resolved_at,
            )

        if route is None:
            route = await turn.router_task
            router_completed_at = turn.task_completed_at.get("router", loop.time())
            await publish_interaction_turn_route_decision(
                turn.event,
                route,
                turn.personal_task,
            )
        core_gate_at = max(router_completed_at, plugin_resolved_at)
        return InteractionControlResolution(
            route=route,
            plugin_gate=plugin_gate,
            router_completed_monotonic=router_completed_at,
            plugin_resolved_monotonic=plugin_resolved_at,
            core_gate_monotonic=core_gate_at,
        )
