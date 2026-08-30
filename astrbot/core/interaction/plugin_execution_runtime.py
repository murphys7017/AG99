from __future__ import annotations

import asyncio
import contextvars
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from astrbot import logger

from .plugin_execution_types import (
    PluginBranchResult,
    PluginDeliveryDisposition,
    PluginDeliveryKey,
    PluginGateResolution,
    PluginJobState,
)

PluginGatePublisher = Callable[[PluginGateResolution], PluginGateResolution]
PluginJobRunner = Callable[[PluginGatePublisher], Awaitable[None]]
PluginLeaseReleaser = Callable[[], Awaitable[None]]
PluginJobCompletionHandler = Callable[["PluginExecutionJob"], Awaitable[None]]

DEFAULT_PLUGIN_MODULE_DRAIN_TIMEOUT_SECONDS = 15.0

_ACTIVE_PLUGIN_BRANCH_EVENT: contextvars.ContextVar[Any | None] = (
    contextvars.ContextVar("active_plugin_branch_event", default=None)
)


def get_active_plugin_branch_event() -> Any | None:
    return _ACTIVE_PLUGIN_BRANCH_EVENT.get()


@contextmanager
def activate_plugin_branch_event(branch_event: Any):
    token = _ACTIVE_PLUGIN_BRANCH_EVENT.set(branch_event)
    try:
        yield
    finally:
        _ACTIVE_PLUGIN_BRANCH_EVENT.reset(token)


@dataclass(slots=True)
class PluginExecutionRuntimeDiagnostics:
    active_plugin_job_count: int
    detached_plugin_job_count: int
    oldest_plugin_job_age_seconds: float
    background_job_completed_count: int
    background_job_failed_count: int
    background_job_cancelled_on_shutdown_count: int


class PluginModuleDrainingError(RuntimeError):
    def __init__(self, module_path: str) -> None:
        super().__init__(f"Plugin module is draining: {module_path}")
        self.module_path = module_path


class PluginModuleDrainTimeoutError(RuntimeError):
    """Raised when a management action cannot safely drain a plugin module."""

    def __init__(
        self,
        module_path: str,
        *,
        timeout_seconds: float,
        active_lease_count: int,
        active_job_ids: tuple[str, ...],
        oldest_job_age_seconds: float,
    ) -> None:
        super().__init__(
            "Plugin module drain timed out: "
            f"module_path={module_path} timeout_seconds={timeout_seconds:.3f} "
            f"active_lease_count={active_lease_count} "
            f"active_job_ids={','.join(active_job_ids)} "
            f"oldest_job_age_seconds={oldest_job_age_seconds:.3f}"
        )
        self.module_path = module_path
        self.timeout_seconds = timeout_seconds
        self.active_lease_count = active_lease_count
        self.active_job_ids = active_job_ids
        self.oldest_job_age_seconds = oldest_job_age_seconds


@dataclass(slots=True)
class PluginModuleLease:
    runtime: PluginExecutionRuntime
    module_paths: tuple[str, ...]
    job_id: str | None = None
    released: bool = False

    def bind_job(self, job_id: str) -> None:
        if self.released:
            raise RuntimeError("Cannot bind a released plugin module lease")
        if self.job_id is not None:
            if self.job_id != job_id:
                raise RuntimeError("Plugin module lease is already bound to another Job")
            return
        self.runtime._bind_module_lease_job(self.module_paths, job_id)
        self.job_id = job_id

    async def release(self) -> None:
        if self.released:
            return
        self.released = True
        self.runtime._release_module_lease(self.module_paths, self.job_id)


@dataclass(slots=True)
class PluginExecutionJob:
    job_id: str
    branch_event: Any
    result: PluginBranchResult
    started_monotonic: float
    gate_future: asyncio.Future[PluginGateResolution]
    release_leases: PluginLeaseReleaser | None = None
    completion_handler: PluginJobCompletionHandler | None = None
    task: asyncio.Task[None] | None = None
    detached_at: float | None = None
    gate_resolved_monotonic: float | None = None

    def mark_detached(self) -> None:
        if self.detached_at is not None:
            return
        self.detached_at = time.time()
        self.result.detached_at = self.detached_at

    def publish_gate(
        self,
        resolution: PluginGateResolution,
    ) -> PluginGateResolution:
        current = self.result.gate_resolution
        if current is PluginGateResolution.PENDING:
            self.result.gate_resolution = resolution
            current = resolution
        if current is resolution and self.gate_resolved_monotonic is None:
            resolved_monotonic = asyncio.get_running_loop().time()
            self.gate_resolved_monotonic = resolved_monotonic
            self.result.gate_resolved_at = time.time()
            self.result.gate_resolved_monotonic = resolved_monotonic
        if current is resolution and not self.gate_future.done():
            self.gate_future.set_result(current)
        if current is PluginGateResolution.HANDLED:
            self.result.freeze_t1_artifact_boundary()
        if current is PluginGateResolution.EXPIRED:
            self.mark_detached()
        return current

    async def wait_for_gate(
        self,
        deadline_monotonic: float,
    ) -> PluginGateResolution:
        if self.gate_future.done():
            return self.gate_future.result()
        timeout = max(0.0, deadline_monotonic - asyncio.get_running_loop().time())
        try:
            return await asyncio.wait_for(
                asyncio.shield(self.gate_future),
                timeout=timeout,
            )
        except TimeoutError:
            return self.publish_gate(PluginGateResolution.EXPIRED)

    async def wait_completed(self) -> PluginBranchResult:
        if self.task is not None:
            await asyncio.shield(self.task)
        return self.result


class PluginExecutionRuntime:
    """Own Plugin Job tasks independently from one Interaction turn scope."""

    def __init__(self) -> None:
        self._jobs: dict[str, PluginExecutionJob] = {}
        self._closed = False
        self._background_completed_count = 0
        self._background_failed_count = 0
        self._cancelled_on_shutdown_count = 0
        self._module_lease_counts: dict[str, int] = {}
        self._module_job_ids: dict[str, set[str]] = {}
        self._draining_modules: set[str] = set()
        self._drain_events: dict[str, asyncio.Event] = {}
        self._delivery_ledger: dict[
            PluginDeliveryKey,
            PluginDeliveryDisposition,
        ] = {}
        self._delivery_lock = asyncio.Lock()
        self._completion_tasks: set[asyncio.Task[None]] = set()
        self._completion_jobs: dict[asyncio.Task[None], PluginExecutionJob] = {}

    def acquire_module_lease(
        self,
        module_paths: list[str] | tuple[str, ...],
    ) -> PluginModuleLease:
        if self._closed:
            raise RuntimeError("PluginExecutionRuntime is closed")
        unique_paths = tuple(sorted(set(module_paths)))
        for module_path in unique_paths:
            if module_path in self._draining_modules:
                raise PluginModuleDrainingError(module_path)
        for module_path in unique_paths:
            self._module_lease_counts[module_path] = (
                self._module_lease_counts.get(module_path, 0) + 1
            )
        return PluginModuleLease(self, unique_paths)

    def _bind_module_lease_job(
        self,
        module_paths: tuple[str, ...],
        job_id: str,
    ) -> None:
        for module_path in module_paths:
            self._module_job_ids.setdefault(module_path, set()).add(job_id)

    def _release_module_lease(
        self,
        module_paths: tuple[str, ...],
        job_id: str | None,
    ) -> None:
        for module_path in module_paths:
            if job_id is not None:
                job_ids = self._module_job_ids.get(module_path)
                if job_ids is not None:
                    job_ids.discard(job_id)
                    if not job_ids:
                        self._module_job_ids.pop(module_path, None)
            remaining = self._module_lease_counts.get(module_path, 0) - 1
            if remaining > 0:
                self._module_lease_counts[module_path] = remaining
                continue
            self._module_lease_counts.pop(module_path, None)
            drain_event = self._drain_events.get(module_path)
            if drain_event is not None:
                drain_event.set()

    def _get_module_drain_snapshot(
        self,
        module_path: str,
    ) -> tuple[int, tuple[str, ...], float]:
        job_ids = tuple(sorted(self._module_job_ids.get(module_path, ())))
        now = time.monotonic()
        ages = [
            now - job.started_monotonic
            for job_id in job_ids
            if (job := self._jobs.get(job_id)) is not None
        ]
        return (
            self._module_lease_counts.get(module_path, 0),
            job_ids,
            max(ages, default=0.0),
        )

    async def begin_module_draining(
        self,
        module_path: str,
        *,
        timeout_seconds: float = DEFAULT_PLUGIN_MODULE_DRAIN_TIMEOUT_SECONDS,
    ) -> None:
        timeout_seconds = max(0.0, float(timeout_seconds))
        self._draining_modules.add(module_path)
        event = self._drain_events.setdefault(module_path, asyncio.Event())
        if self._module_lease_counts.get(module_path, 0) == 0:
            event.set()
        try:
            await asyncio.wait_for(
                event.wait(),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            active_lease_count, active_job_ids, oldest_job_age_seconds = (
                self._get_module_drain_snapshot(module_path)
            )
            error = PluginModuleDrainTimeoutError(
                module_path,
                timeout_seconds=timeout_seconds,
                active_lease_count=active_lease_count,
                active_job_ids=active_job_ids,
                oldest_job_age_seconds=oldest_job_age_seconds,
            )
            logger.warning(
                "DIAG plugin.module_drain_timeout: module_path=%s "
                "timeout_seconds=%.3f active_lease_count=%d "
                "active_job_ids=%s oldest_job_age_seconds=%.3f",
                module_path,
                timeout_seconds,
                active_lease_count,
                ",".join(active_job_ids),
                oldest_job_age_seconds,
            )
            # Abort the management action without leaving the unchanged plugin blocked.
            self.end_module_draining(module_path)
            raise error from exc

    def end_module_draining(self, module_path: str) -> None:
        self._draining_modules.discard(module_path)
        self._drain_events.pop(module_path, None)

    async def register_delivery(self, key: PluginDeliveryKey) -> None:
        async with self._delivery_lock:
            self._delivery_ledger.setdefault(
                key,
                PluginDeliveryDisposition.PRODUCED,
            )

    async def reserve_delivery(self, key: PluginDeliveryKey) -> bool:
        async with self._delivery_lock:
            if self._delivery_ledger.get(key) is not PluginDeliveryDisposition.PRODUCED:
                return False
            self._delivery_ledger[key] = PluginDeliveryDisposition.DELIVERY_RESERVED
            return True

    async def finish_delivery(
        self,
        key: PluginDeliveryKey,
        disposition: PluginDeliveryDisposition,
    ) -> bool:
        if disposition not in {
            PluginDeliveryDisposition.DELIVERED_INLINE,
            PluginDeliveryDisposition.DELIVERED_DELAYED,
            PluginDeliveryDisposition.SUPPRESSED_DUPLICATE,
            PluginDeliveryDisposition.SUPPRESSED_DUPLICATE_VISIBLE_OUTPUT,
            PluginDeliveryDisposition.DELAYED_TARGET_UNSUPPORTED,
            PluginDeliveryDisposition.DELIVERY_FAILED,
        }:
            raise ValueError(f"Invalid terminal delivery disposition: {disposition}")
        async with self._delivery_lock:
            if (
                self._delivery_ledger.get(key)
                is not PluginDeliveryDisposition.DELIVERY_RESERVED
            ):
                return False
            self._delivery_ledger[key] = disposition
            return True

    async def get_delivery_disposition(
        self,
        key: PluginDeliveryKey,
    ) -> PluginDeliveryDisposition | None:
        async with self._delivery_lock:
            return self._delivery_ledger.get(key)

    async def discard_delivery_records(self, plugin_job_id: str) -> None:
        """Release deduplication records after all T1/T2 delivery work settles."""
        if not plugin_job_id:
            return
        async with self._delivery_lock:
            stale_keys = [
                key
                for key in self._delivery_ledger
                if key.plugin_job_id == plugin_job_id
            ]
            for key in stale_keys:
                self._delivery_ledger.pop(key, None)

    def start(
        self,
        *,
        branch_event: Any,
        result: PluginBranchResult,
        run_job: PluginJobRunner,
        release_leases: PluginLeaseReleaser | None = None,
        completion_handler: PluginJobCompletionHandler | None = None,
    ) -> PluginExecutionJob:
        if self._closed:
            raise RuntimeError("PluginExecutionRuntime is closed")
        loop = asyncio.get_running_loop()
        job = PluginExecutionJob(
            job_id=uuid.uuid4().hex,
            branch_event=branch_event,
            result=result,
            started_monotonic=time.monotonic(),
            gate_future=loop.create_future(),
            release_leases=release_leases,
            completion_handler=completion_handler,
        )
        result.plugin_job_id = job.job_id
        self._jobs[job.job_id] = job
        job.task = asyncio.create_task(
            self._run_job(job, run_job),
            name=f"plugin_execution_{job.job_id}",
            context=contextvars.Context(),
        )
        return job

    async def _run_job(
        self,
        job: PluginExecutionJob,
        run_job: PluginJobRunner,
    ) -> None:
        try:
            with activate_plugin_branch_event(job.branch_event):
                await run_job(job.publish_gate)
            if job.result.job_state is PluginJobState.RUNNING:
                job.result.job_state = PluginJobState.COMPLETED
        except asyncio.CancelledError:
            job.result.job_state = PluginJobState.CANCELLED
            if self._closed:
                self._cancelled_on_shutdown_count += 1
        except BaseException as exc:
            job.result.job_state = PluginJobState.FAILED
            job.result.failure = exc
            logger.exception(
                "Plugin Execution Job failed: job_id=%s",
                job.job_id,
            )
        finally:
            if job.result.gate_resolution is PluginGateResolution.PENDING:
                if job.result.job_state is PluginJobState.FAILED:
                    job.publish_gate(PluginGateResolution.FAILED)
                elif job.result.job_state is PluginJobState.CANCELLED:
                    job.publish_gate(PluginGateResolution.FAILED)
                elif job.result.stopped:
                    job.publish_gate(PluginGateResolution.STOPPED)
                else:
                    job.publish_gate(PluginGateResolution.PASSED)
            if job.release_leases is not None:
                try:
                    await job.release_leases()
                except Exception:
                    logger.exception(
                        "Plugin Execution Job lease release failed: job_id=%s",
                        job.job_id,
                    )
            job.result.completed_at = job.result.completed_at or time.time()
            if job.detached_at is not None:
                if job.result.job_state is PluginJobState.COMPLETED:
                    self._background_completed_count += 1
                elif job.result.job_state is PluginJobState.FAILED:
                    self._background_failed_count += 1
            self._jobs.pop(job.job_id, None)
            self._schedule_completion_handler(job)

    def _schedule_completion_handler(self, job: PluginExecutionJob) -> None:
        handler = job.completion_handler
        if handler is None or self._closed:
            job.result.cleanup_media()
            return
        task = asyncio.create_task(
            self._run_completion_handler(job, handler),
            name=f"plugin_completion_{job.job_id}",
            context=contextvars.Context(),
        )
        self._completion_tasks.add(task)
        self._completion_jobs[task] = job
        task.add_done_callback(self._completion_finished)

    def _completion_finished(self, task: asyncio.Task[None]) -> None:
        self._completion_tasks.discard(task)
        job = self._completion_jobs.pop(task, None)
        if job is not None:
            job.result.cleanup_media()

    @staticmethod
    async def _run_completion_handler(
        job: PluginExecutionJob,
        handler: PluginJobCompletionHandler,
    ) -> None:
        try:
            await handler(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Plugin Execution Job completion handler failed: job_id=%s",
                job.job_id,
            )

    def snapshot_diagnostics(self) -> PluginExecutionRuntimeDiagnostics:
        now = time.monotonic()
        ages = [now - job.started_monotonic for job in self._jobs.values()]
        detached = sum(job.detached_at is not None for job in self._jobs.values())
        return PluginExecutionRuntimeDiagnostics(
            active_plugin_job_count=len(self._jobs),
            detached_plugin_job_count=detached,
            oldest_plugin_job_age_seconds=max(ages, default=0.0),
            background_job_completed_count=self._background_completed_count,
            background_job_failed_count=self._background_failed_count,
            background_job_cancelled_on_shutdown_count=(
                self._cancelled_on_shutdown_count
            ),
        )

    def log_diagnostics(
        self,
        *,
        trigger: str,
        turn_id: str = "",
        job_id: str = "",
    ) -> PluginExecutionRuntimeDiagnostics:
        snapshot = self.snapshot_diagnostics()
        logger.debug(
            "DIAG plugin.runtime: trigger=%s turn_id=%s job_id=%s "
            "active_jobs=%d detached_jobs=%d oldest_job_age_seconds=%.3f "
            "background_completed=%d background_failed=%d "
            "cancelled_on_shutdown=%d",
            trigger,
            turn_id,
            job_id,
            snapshot.active_plugin_job_count,
            snapshot.detached_plugin_job_count,
            snapshot.oldest_plugin_job_age_seconds,
            snapshot.background_job_completed_count,
            snapshot.background_job_failed_count,
            snapshot.background_job_cancelled_on_shutdown_count,
        )
        return snapshot

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = [
            job.task
            for job in self._jobs.values()
            if job.task is not None and not job.task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._jobs.clear()
        completion_tasks = [task for task in self._completion_tasks if not task.done()]
        for task in completion_tasks:
            task.cancel()
        if completion_tasks:
            await asyncio.gather(*completion_tasks, return_exceptions=True)
        for job in self._completion_jobs.values():
            job.result.cleanup_media()
        self._completion_jobs.clear()
        self._completion_tasks.clear()
        async with self._delivery_lock:
            self._delivery_ledger.clear()
