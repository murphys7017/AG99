import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from apscheduler.events import (
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
    JobSubmissionEvent,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from astrbot import logger
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import CronJob
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.proactive_agent_turn import (
    resolve_proactive_configuration_selection,
    run_proactive_agent_turn,
)

if TYPE_CHECKING:
    from astrbot.core.star.context import Context


_CRONTAB_WEEKDAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
_CRONTAB_WEEKDAY_PATTERN = re.compile(r"^(?:(\*)|(\d+)(?:-(\d+))?)(?:/(\d+))?$")


def _normalize_crontab_day_of_week(day_of_week: str) -> str:
    """Convert standard crontab Sunday=0/7 weekday values for APScheduler."""
    normalized_parts: list[str] = []
    for raw_part in day_of_week.split(","):
        part = raw_part.strip().lower()
        match = _CRONTAB_WEEKDAY_PATTERN.fullmatch(part)
        if not match:
            normalized_parts.append(part)
            continue

        wildcard, start_text, end_text, step_text = match.groups()
        step = int(step_text or "1")
        if step < 1:
            raise ValueError("day_of_week step must be greater than 0")

        if wildcard:
            if step == 1:
                normalized_parts.append("*")
                continue
            values = range(0, 7, step)
        else:
            start = int(start_text)
            end = int(end_text) if end_text is not None else None
            if start < 0 or start > 7 or (end is not None and (end < 0 or end > 7)):
                raise ValueError("day_of_week values must be between 0 and 7")
            if end is not None and start > end:
                raise ValueError("day_of_week range start must not exceed end")
            if end is None:
                end = 7 if step_text else start
            values = range(start, end + 1, step)

        weekdays: list[int] = []
        for value in values:
            weekday = 0 if value == 7 else value
            if weekday not in weekdays:
                weekdays.append(weekday)

        if len(weekdays) == 7:
            normalized_parts.append("*")
        else:
            normalized_parts.extend(_CRONTAB_WEEKDAY_NAMES[value] for value in weekdays)

    return ",".join(normalized_parts)


class CronJobSchedulingError(Exception):
    """Raised when a cron job fails to be scheduled."""

    pass


class CronJobManager:
    """Central scheduler for BasicCronJob and ActiveAgentCronJob."""

    def __init__(self, db: BaseDatabase) -> None:
        self.db = db
        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_listener(
            self._on_job_not_run, EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES
        )
        self._status_tasks: set[asyncio.Task] = set()
        self._execution_tasks: set[asyncio.Task] = set()
        self._running_versions: set[tuple[str, int]] = set()
        self._scheduled_job_ids: dict[str, str] = {}
        self._job_lock = asyncio.Lock()
        self._closing = False
        self._basic_handlers: dict[str, Callable[..., Any]] = {}
        self._lock = asyncio.Lock()
        self._started = False
        # The scheduler may start early via _schedule_job; track DB sync separately.
        self._db_synced = False

    async def start(self, ctx: "Context") -> None:
        self.ctx: Context = ctx  # star context
        async with self._lock:
            if self._closing and self._execution_tasks:
                raise RuntimeError("Cannot start cron while executions are draining")
            self._closing = False
            if self._db_synced:
                return
            if not self._started:
                self.scheduler.start()
                self._started = True
            await self.sync_from_db()
            self._db_synced = True

    async def shutdown(self) -> None:
        async with self._lock:
            self._closing = True
            self._db_synced = False
            if self._started:
                self.scheduler.shutdown(wait=False)
                self._started = False
                await asyncio.sleep(0)
            tasks = self._execution_tasks | self._status_tasks
            for task in self._execution_tasks:
                if not task.done() and not task.cancelling():
                    task.cancel()
            if tasks:
                done, pending = await asyncio.wait(tasks, timeout=15)
                for task in done:
                    if not task.cancelled():
                        task.exception()
                if pending:
                    raise RuntimeError(
                        f"Cron shutdown incomplete: {len(pending)} tasks still draining"
                    )

    def _on_job_not_run(self, event: JobExecutionEvent | JobSubmissionEvent) -> None:
        if isinstance(event, JobSubmissionEvent):
            scheduled_run_time = max(event.scheduled_run_times)
            reason = "Scheduled execution rejected: maximum running instances reached"
        else:
            scheduled_run_time = event.scheduled_run_time
            reason = "Scheduled execution missed its grace period"
        task = asyncio.create_task(
            self._record_unexecuted_job(event.job_id, scheduled_run_time, reason)
        )
        self._status_tasks.add(task)
        task.add_done_callback(self._status_tasks.discard)

    async def _record_unexecuted_job(
        self, scheduler_id: str, scheduled_run_time: datetime, reason: str
    ) -> None:
        try:
            job_id, revision = json.loads(scheduler_id)
            logger.warning(
                "Cron trigger did not run: job_id=%s revision=%s scheduled_at=%s reason=%s",
                job_id,
                revision,
                scheduled_run_time.isoformat(),
                reason,
            )
            async with self._job_lock:
                job = await self.db.get_cron_job(job_id)
                if job is None or not job.enabled or job.revision != revision:
                    return
                # A rejected trigger must not overwrite an in-flight or newer outcome.
                if (job_id, revision) in self._running_versions:
                    return
                last_run = job.last_run_at
                if last_run is not None:
                    if last_run.tzinfo is None:
                        last_run = last_run.replace(tzinfo=timezone.utc)
                    if last_run >= scheduled_run_time:
                        return
                await self.db.update_cron_job(
                    job_id,
                    expected_revision=revision,
                    status="missed",
                    last_error=reason,
                    delivery_status="unconfirmed",
                    next_run_time=self._get_next_run_time(job_id),
                    **({"enabled": False} if job.run_once else {}),
                )
        except Exception:
            logger.exception(
                "Failed to record unexecuted cron trigger: scheduler_id=%s",
                scheduler_id,
            )

    async def sync_from_db(self) -> None:
        jobs = await self.db.list_cron_jobs()
        for job in jobs:
            if not job.enabled or not job.persistent:
                continue
            if job.job_type == "basic" and job.job_id not in self._basic_handlers:
                logger.warning(
                    "Skip scheduling basic cron job %s due to missing handler.",
                    job.job_id,
                )
                continue
            try:
                await self._schedule_persisted_job(job)
            except CronJobSchedulingError:
                continue  # Error already logged in _schedule_job

    async def add_basic_job(
        self,
        *,
        name: str,
        cron_expression: str,
        handler: Callable[..., Any | Awaitable[Any]],
        description: str | None = None,
        timezone: str | None = None,
        payload: dict | None = None,
        enabled: bool = True,
        persistent: bool = False,
    ) -> CronJob:
        if self._closing:
            raise RuntimeError("Cron manager is shutting down")
        job = await self.db.create_cron_job(
            name=name,
            job_type="basic",
            cron_expression=cron_expression,
            timezone=timezone,
            payload=payload or {},
            description=description,
            enabled=enabled,
            persistent=persistent,
        )
        self._basic_handlers[job.job_id] = handler
        if enabled:
            await self._schedule_persisted_job(job)
        return job

    async def add_active_job(
        self,
        *,
        name: str,
        cron_expression: str | None,
        payload: dict,
        description: str | None = None,
        timezone: str | None = None,
        enabled: bool = True,
        persistent: bool = True,
        run_once: bool = False,
        run_at: datetime | None = None,
    ) -> CronJob:
        if self._closing:
            raise RuntimeError("Cron manager is shutting down")
        # If run_once with run_at, store run_at in payload for later reference.
        if run_once and run_at:
            payload = {**payload, "run_at": run_at.isoformat()}
        job = await self.db.create_cron_job(
            name=name,
            job_type="active_agent",
            cron_expression=cron_expression,
            timezone=timezone,
            payload=payload,
            description=description,
            enabled=enabled,
            persistent=persistent,
            run_once=run_once,
        )
        if enabled:
            await self._schedule_persisted_job(job)
        return job

    async def update_job(self, job_id: str, **kwargs) -> CronJob | None:
        async with self._job_lock:
            if self._closing:
                raise RuntimeError("Cron manager is shutting down")
            kwargs.update(
                status="scheduled",
                last_execution_id=None,
                delivery_status=None,
                last_run_at=None,
                last_error=None,
            )
            job = await self.db.update_cron_job(
                job_id,
                **kwargs,
                advance_revision=True,
            )
            if not job:
                return None
            self._remove_scheduled(job_id)
            if job.enabled:
                await self._schedule_persisted_job(job)
            else:
                await self.db.update_cron_job(job_id, next_run_time=None)
            return job

    async def delete_job(self, job_id: str) -> None:
        async with self._job_lock:
            self._remove_scheduled(job_id)
            self._basic_handlers.pop(job_id, None)
            await self.db.delete_cron_job(job_id)

    async def list_jobs(self, job_type: str | None = None) -> list[CronJob]:
        return await self.db.list_cron_jobs(job_type)

    def _remove_scheduled(self, job_id: str) -> None:
        scheduler_id = self._scheduled_job_ids.pop(job_id, None)
        if scheduler_id and self.scheduler.get_job(scheduler_id):
            self.scheduler.remove_job(scheduler_id)

    @staticmethod
    def _scheduler_job_id(job_id: str, revision: int) -> str:
        return json.dumps([job_id, revision], separators=(",", ":"))

    async def _schedule_persisted_job(self, job: CronJob) -> None:
        if self._closing:
            raise RuntimeError("Cron manager is shutting down")
        # Synchronizing an unchanged plan must not erase its execution outcome.
        try:
            self._schedule_job(job)
        except CronJobSchedulingError as exc:
            await self.db.update_cron_job(
                job.job_id,
                enabled=False,
                status="failed",
                last_error=str(exc),
                next_run_time=None,
            )
            raise
        await self.db.update_cron_job(
            job.job_id,
            next_run_time=job.next_run_time,
        )

    def _schedule_job(self, job: CronJob) -> None:
        if self._closing:
            raise RuntimeError("Cron manager is shutting down")
        if not self._started:
            self.scheduler.start()
            self._started = True
        try:
            tzinfo = None
            if job.timezone:
                try:
                    tzinfo = ZoneInfo(job.timezone)
                except Exception:
                    logger.warning(
                        "Invalid timezone %s for cron job %s, fallback to system.",
                        job.timezone,
                        job.job_id,
                    )
            if job.run_once:
                run_at_str = None
                if isinstance(job.payload, dict):
                    run_at_str = job.payload.get("run_at")
                run_at_str = run_at_str or job.cron_expression
                if not run_at_str:
                    raise ValueError("run_once job missing run_at timestamp")
                run_at = datetime.fromisoformat(run_at_str)
                if run_at.tzinfo is None and tzinfo is not None:
                    run_at = run_at.replace(tzinfo=tzinfo)
                trigger = DateTrigger(run_date=run_at, timezone=tzinfo)
                if trigger.run_date <= datetime.now(timezone.utc):
                    raise ValueError(
                        "run_once timestamp is in the past; reschedule explicitly"
                    )
            else:
                if not job.cron_expression:
                    raise ValueError("recurring job missing cron_expression")
                minute, hour, day, month, day_of_week = job.cron_expression.split()
                normalized_cron_expression = " ".join(
                    [
                        minute,
                        hour,
                        day,
                        month,
                        _normalize_crontab_day_of_week(day_of_week),
                    ]
                )
                trigger = CronTrigger.from_crontab(
                    normalized_cron_expression,
                    timezone=tzinfo,
                )
            scheduler_id = self._scheduler_job_id(job.job_id, job.revision)
            self.scheduler.add_job(
                self._run_job,
                id=scheduler_id,
                trigger=trigger,
                args=[job.job_id],
                kwargs={"scheduled_revision": job.revision},
                replace_existing=True,
                misfire_grace_time=30,
            )
            self._scheduled_job_ids[job.job_id] = scheduler_id
            job.next_run_time = self._get_next_run_time(job.job_id)
        except (ValueError, TypeError) as e:
            logger.exception("Failed to schedule cron job %s", job.job_id)
            raise CronJobSchedulingError(str(e)) from e

    def _get_next_run_time(self, job_id: str):
        scheduler_id = self._scheduled_job_ids.get(job_id)
        aps_job = self.scheduler.get_job(scheduler_id) if scheduler_id else None
        if not aps_job or aps_job.next_run_time is None:
            return None
        return aps_job.next_run_time.astimezone(timezone.utc)

    async def run_job_now(self, job_id: str) -> None:
        if self._closing:
            raise RuntimeError("Cron manager is shutting down")
        await self._run_job(job_id, ignore_enabled=True, delete_run_once=False)

    async def _run_job(
        self,
        job_id: str,
        *,
        ignore_enabled: bool = False,
        delete_run_once: bool = True,
        scheduled_revision: int | None = None,
    ) -> None:
        task = asyncio.current_task()
        if task is None or self._closing:
            return
        self._execution_tasks.add(task)
        version = None
        try:
            async with self._job_lock:
                job = await self.db.get_cron_job(job_id)
                if not job or (not job.enabled and not ignore_enabled):
                    return
                if (
                    scheduled_revision is not None
                    and job.revision != scheduled_revision
                ):
                    return
                version = (job_id, job.revision)
                if version in self._running_versions:
                    version = None
                    raise RuntimeError(f"Cron job is already running: {job_id}")
                self._running_versions.add(version)
            await self._execute_job(job, delete_run_once=delete_run_once)
        finally:
            if version is not None:
                self._running_versions.discard(version)
            self._execution_tasks.discard(task)

    async def _execute_job(self, job: CronJob, *, delete_run_once: bool) -> None:
        job_id = job.job_id
        execution_id = uuid.uuid4().hex
        start_time = datetime.now(timezone.utc)
        status = "completed"
        last_error = None
        delivery_status = "not_required" if job.job_type == "basic" else "unknown"
        try:
            async with self._job_lock:
                admitted = await self.db.update_cron_job(
                    job_id,
                    status="running",
                    last_run_at=start_time,
                    last_error=None,
                    expected_revision=job.revision,
                    last_execution_id=execution_id,
                    delivery_status=None,
                )
            if admitted is None:
                status = "superseded"
                return
            job.last_execution_id = execution_id
            if job.job_type == "basic":
                await self._run_basic_job(job)
            elif job.job_type == "active_agent":
                delivered = await self._run_active_agent_job(job, start_time=start_time)
                delivery_status = (
                    "not_required"
                    if delivered is None
                    else "confirmed"
                    if delivered
                    else "unconfirmed"
                )
                if delivered is False:
                    status = "completed_without_delivery"
            else:
                raise ValueError(f"Unknown cron job type: {job.job_type}")
        except Exception as e:  # noqa: BLE001
            status = "failed"
            last_error = str(e)
            logger.error(f"Cron job {job_id} failed: {e!s}", exc_info=True)
        except asyncio.CancelledError:
            status = "cancelled"
            last_error = "Cron execution was cancelled"
            raise
        finally:
            async with self._job_lock:
                next_run = self._get_next_run_time(job_id)
                disable_once = (
                    job.run_once and delete_run_once and status != "completed"
                )
                settled = await self.db.update_cron_job(
                    job_id,
                    expected_revision=job.revision,
                    expected_execution_id=execution_id,
                    status=status,
                    last_run_at=start_time,
                    last_error=last_error,
                    delivery_status=delivery_status,
                    next_run_time=next_run,
                    **({"enabled": False} if disable_once else {}),
                )
                if (
                    settled is not None
                    and job.run_once
                    and delete_run_once
                    and status == "completed"
                ):
                    if await self.db.delete_cron_job(
                        job_id, expected_revision=job.revision
                    ):
                        self._remove_scheduled(job_id)
                        self._basic_handlers.pop(job_id, None)
            logger.info(
                "Cron execution settled: job_id=%s revision=%s execution_id=%s "
                "status=%s delivery=%s current_plan_updated=%s",
                job_id,
                job.revision,
                execution_id,
                status,
                delivery_status,
                settled is not None,
            )

    async def _run_basic_job(self, job: CronJob) -> None:
        handler = self._basic_handlers.get(job.job_id)
        if not handler:
            raise RuntimeError(f"Basic cron job handler not found for {job.job_id}")
        payload = job.payload or {}
        result = handler(**payload) if payload else handler()
        if asyncio.iscoroutine(result):
            await result

    async def _run_active_agent_job(
        self, job: CronJob, start_time: datetime
    ) -> bool | None:
        payload = job.payload or {}
        delivery_session_str = str(payload.get("session") or "").strip()
        if not delivery_session_str:
            default_target = self.ctx.get_proactive_message_target()
            if default_target is not None:
                delivery_session_str = str(default_target)
        session_str = delivery_session_str or str(
            MessageSession(
                platform_name="cron",
                message_type=MessageType.OTHER_MESSAGE,
                session_id=job.job_id,
            )
        )
        note = payload.get("note") or job.description or job.name

        extras = {
            "cron_job": {
                "id": job.job_id,
                "revision": job.revision,
                "execution_id": job.last_execution_id,
                "name": job.name,
                "type": job.job_type,
                "run_once": job.run_once,
                "description": job.description,
                "note": note,
                "run_started_at": start_time.isoformat(),
                "run_at": (
                    job.payload.get("run_at") if isinstance(job.payload, dict) else None
                ),
                "session": delivery_session_str,
            },
            "cron_payload": payload,
        }

        return await self._woke_main_agent(
            message=note,
            session_str=session_str,
            extras=extras,
            delivery_session_str=delivery_session_str,
        )

    async def _woke_main_agent(
        self,
        *,
        message: str,
        session_str: str,
        extras: dict,
        delivery_session_str: str = "",
    ) -> bool | None:
        """Woke the main agent to handle the cron job message."""
        from astrbot.core.astr_main_agent import MainAgentBuildConfig
        from astrbot.core.astr_main_agent_resources import (
            PROACTIVE_AGENT_CRON_WOKE_SYSTEM_PROMPT,
        )

        try:
            session = (
                session_str
                if isinstance(session_str, MessageSession)
                else MessageSession.from_str(session_str)
            )
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Invalid session for cron job: {session_str}") from e

        # judge user's role
        configuration_selection = resolve_proactive_configuration_selection(
            context=self.ctx,
            session=session,
        )
        cfg = configuration_selection.runtime_config
        cron_payload = extras.get("cron_payload", {}) if extras else {}
        sender_id = cron_payload.get("sender_id")
        admin_ids = cfg.get("admins_id", [])
        if admin_ids:
            role = "admin" if sender_id in admin_ids else "member"
        else:
            role = None
        if cron_payload.get("origin", "tool") == "api":
            role = "admin"

        provider_settings = cfg.get("provider_settings", {}) or {}
        tool_call_timeout = provider_settings.get("tool_call_timeout", 120)
        config = MainAgentBuildConfig(
            tool_call_timeout=tool_call_timeout,
            streaming_response=False,
            provider_settings=provider_settings,
        )
        cron_job_str = json.dumps(extras.get("cron_job", {}), ensure_ascii=False)
        turn = await run_proactive_agent_turn(
            context=self.ctx,
            session=session,
            message=message,
            extras=extras or {},
            role=role,
            config=config,
            system_prompt=PROACTIVE_AGENT_CRON_WOKE_SYSTEM_PROMPT.format(
                cron_job=cron_job_str
            ),
            prompt=(
                "You are now responding to a scheduled task. "
                "Proceed according to your system instructions. "
                "Output using same language as previous conversation. "
                "Use the delivery tool when a user-visible message is required; "
                "after the tool succeeds, stop without producing an additional summary."
            ),
            require_delivery_tool=bool(delivery_session_str),
            include_history_fences=False,
            configuration_selection=configuration_selection,
        )
        cron_meta = extras.get("cron_job", {}) if extras else {}
        if delivery_session_str and not turn.delivery_confirmed:
            logger.warning(
                "Cron execution completed without confirmed delivery: job_id=%s target=%s",
                cron_meta.get("id", ""),
                delivery_session_str,
            )
        return turn.delivery_confirmed if delivery_session_str else None


__all__ = ["CronJobManager"]
