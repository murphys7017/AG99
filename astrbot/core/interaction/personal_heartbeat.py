from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import supports_personal_runtime

from .config import load_interaction_agent_config
from .observation import RuntimeObservation, RuntimeObservationTarget

if TYPE_CHECKING:
    from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
    from astrbot.core.star.context import Context

    from .observation_inbox import ObservationAdmissionResult
    from .personal_runtime import PersonalRuntimeManager


@dataclass(frozen=True, slots=True)
class _HeartbeatSubmission:
    submitted_at: float
    status: str
    reason_codes: tuple[str, ...]


class PersonalHeartbeatSource:
    """Submit periodic runtime facts without creating messages or actions."""

    _DISABLED_POLL_SECONDS = 60.0

    def __init__(
        self,
        *,
        context: Context,
        config_manager: AstrBotConfigManager,
        runtime_manager: PersonalRuntimeManager,
    ) -> None:
        self._context = context
        self._config_manager = config_manager
        self._runtime_manager = runtime_manager
        self._next_tick_at: dict[str, float] = {}
        self._last_submissions: dict[str, _HeartbeatSubmission] = {}
        self._last_idle_initiation_submissions: dict[str, _HeartbeatSubmission] = {}

    def _prune_inactive_targets(self, active_targets: set[str]) -> None:
        for target_key in tuple(self._next_tick_at):
            if target_key not in active_targets:
                del self._next_tick_at[target_key]
        for target_key in tuple(self._last_submissions):
            if target_key not in active_targets:
                del self._last_submissions[target_key]
        for target_key in tuple(self._last_idle_initiation_submissions):
            if target_key not in active_targets:
                del self._last_idle_initiation_submissions[target_key]

    def _record_submission(
        self,
        *,
        target_key: str,
        occurred_at: float,
        status: str,
        reason_codes: tuple[str, ...] = (),
    ) -> None:
        self._last_submissions[target_key] = _HeartbeatSubmission(
            submitted_at=occurred_at,
            status=status,
            reason_codes=reason_codes,
        )

    def _record_idle_initiation_submission(
        self,
        *,
        target_key: str,
        occurred_at: float,
        status: str,
        reason_codes: tuple[str, ...] = (),
    ) -> None:
        self._last_idle_initiation_submissions[target_key] = _HeartbeatSubmission(
            submitted_at=occurred_at,
            status=status,
            reason_codes=reason_codes,
        )

    async def run(self) -> None:
        while True:
            try:
                delay = self._next_poll_seconds()
            except Exception:
                logger.exception("Personal Runtime heartbeat configuration failed")
                delay = self._DISABLED_POLL_SECONDS
            await asyncio.sleep(delay)
            try:
                await self.tick()
            except Exception:
                logger.exception("Personal Runtime heartbeat tick failed")

    async def tick(self) -> tuple[ObservationAdmissionResult, ...]:
        occurred_at = time.time()
        results: list[ObservationAdmissionResult] = []
        active_targets: set[str] = set()
        for session in self._context.get_runtime_observation_targets():
            target_key = str(session)
            active_targets.add(target_key)
            runtime_config = self._config_manager.get_conf(session)
            runtime_settings = load_interaction_agent_config(runtime_config)
            if not runtime_settings.personal_heartbeat_enabled:
                self._next_tick_at.pop(target_key, None)
                self._last_idle_initiation_submissions.pop(target_key, None)
                continue
            interval = runtime_settings.personal_heartbeat_interval_seconds
            due_at = self._next_tick_at.get(target_key, occurred_at)
            if due_at > occurred_at:
                continue

            platform = self._context.get_platform_inst(session.platform_id)
            if platform is None:
                self._next_tick_at[target_key] = occurred_at + min(
                    interval,
                    self._DISABLED_POLL_SECONDS,
                )
                continue
            metadata = platform.meta()
            if not supports_personal_runtime(metadata):
                self._next_tick_at[target_key] = occurred_at + min(
                    interval,
                    self._DISABLED_POLL_SECONDS,
                )
                continue

            config_info = self._config_manager.get_conf_info(session)
            target = RuntimeObservationTarget(
                platform_id=session.platform_id,
                platform_name=metadata.name,
                message_type=session.message_type,
                session_id=session.session_id,
                support_proactive_message=metadata.support_proactive_message,
                support_personal_runtime=True,
                group_id=(
                    session.session_id
                    if session.message_type is MessageType.GROUP_MESSAGE
                    else None
                ),
            )
            observation = RuntimeObservation(
                kind="heartbeat",
                source="personal_runtime.heartbeat",
                occurred_at=occurred_at,
                expires_at=occurred_at + interval * 2,
                coalesce_key="heartbeat",
                target_session=target,
            )
            try:
                result = await self._runtime_manager.submit_observation(
                    observation,
                    config_id=str(config_info.get("id") or "default"),
                    plugin_context=self._context,
                    runtime_config=runtime_config,
                )
            except Exception:
                logger.exception(
                    "Personal Runtime heartbeat submission failed for target %s",
                    target_key,
                )
                self._record_submission(
                    target_key=target_key,
                    occurred_at=occurred_at,
                    status="failed",
                    reason_codes=("submission_failed",),
                )
                self._next_tick_at[target_key] = occurred_at + min(
                    interval,
                    self._DISABLED_POLL_SECONDS,
                )
                continue
            self._record_submission(
                target_key=target_key,
                occurred_at=occurred_at,
                status=result.status.value,
                reason_codes=result.reason_codes,
            )
            if not (
                result.status.value == "ignored"
                and result.reason_codes == ("heartbeat_without_material",)
            ):
                logger.debug(
                    "Personal Runtime heartbeat submitted: target=%s status=%s "
                    "reasons=%s",
                    target_key,
                    result.status.value,
                    ",".join(result.reason_codes),
                )
            self._next_tick_at[target_key] = occurred_at + interval
            results.append(result)
            if not runtime_settings.personal_idle_initiation_enabled:
                self._last_idle_initiation_submissions.pop(target_key, None)
                continue
            try:
                idle_result = await self._runtime_manager.submit_idle_initiation(
                    target,
                    config_id=str(config_info.get("id") or "default"),
                    plugin_context=self._context,
                    runtime_config=runtime_config,
                    occurred_at=occurred_at,
                    minimum_idle_seconds=(
                        runtime_settings.personal_idle_initiation_after_seconds
                    ),
                )
            except Exception:
                logger.exception(
                    "Personal Runtime idle-initiation submission failed for target %s",
                    target_key,
                )
                self._record_idle_initiation_submission(
                    target_key=target_key,
                    occurred_at=occurred_at,
                    status="failed",
                    reason_codes=("submission_failed",),
                )
                continue
            self._record_idle_initiation_submission(
                target_key=target_key,
                occurred_at=occurred_at,
                status=idle_result.status.value,
                reason_codes=idle_result.reason_codes,
            )
            logger.debug(
                "Personal Runtime idle initiation submitted: target=%s status=%s reasons=%s",
                target_key,
                idle_result.status.value,
                ",".join(idle_result.reason_codes),
            )
            results.append(idle_result)
        self._prune_inactive_targets(active_targets)
        return tuple(results)

    def _next_poll_seconds(self) -> float:
        now = time.time()
        active_due_at: list[float] = []
        active_targets: set[str] = set()
        for session in self._context.get_runtime_observation_targets():
            target_key = str(session)
            active_targets.add(target_key)
            settings = load_interaction_agent_config(
                self._config_manager.get_conf(session)
            )
            if not settings.personal_heartbeat_enabled:
                self._next_tick_at.pop(target_key, None)
                self._last_idle_initiation_submissions.pop(target_key, None)
                continue
            active_due_at.append(
                self._next_tick_at.setdefault(
                    target_key,
                    now + settings.personal_heartbeat_interval_seconds,
                )
            )
        self._prune_inactive_targets(active_targets)
        if not active_due_at:
            return self._DISABLED_POLL_SECONDS
        return max(0.0, min(active_due_at) - now)

    def diagnostics_view(self) -> dict[str, object]:
        """Return configured Heartbeat scheduling state without observation payloads."""
        now = time.time()
        targets: list[dict[str, object]] = []
        for session in self._context.get_runtime_observation_targets():
            settings = load_interaction_agent_config(
                self._config_manager.get_conf(session)
            )
            target_key = str(session)
            next_tick_at = self._next_tick_at.get(target_key)
            last_submission = self._last_submissions.get(target_key)
            last_idle_initiation = self._last_idle_initiation_submissions.get(
                target_key
            )
            enabled = settings.personal_heartbeat_enabled
            targets.append(
                {
                    "umo": str(session),
                    "heartbeat_enabled": enabled,
                    "interval_seconds": (
                        settings.personal_heartbeat_interval_seconds
                        if enabled
                        else None
                    ),
                    "scheduler_state": (
                        "disabled"
                        if not enabled
                        else "scheduled"
                        if next_tick_at is not None
                        else "pending_initial_tick"
                    ),
                    "next_tick_at": next_tick_at,
                    "seconds_until_next_tick": (
                        max(0.0, next_tick_at - now)
                        if next_tick_at is not None
                        else None
                    ),
                    "last_submission_at": (
                        last_submission.submitted_at
                        if last_submission is not None
                        else None
                    ),
                    "last_submission_status": (
                        last_submission.status
                        if last_submission is not None
                        else None
                    ),
                    "last_submission_reason_codes": list(
                        last_submission.reason_codes
                        if last_submission is not None
                        else ()
                    ),
                    "idle_initiation_enabled": (
                        enabled and settings.personal_idle_initiation_enabled
                    ),
                    "idle_initiation_after_seconds": (
                        settings.personal_idle_initiation_after_seconds
                        if enabled and settings.personal_idle_initiation_enabled
                        else None
                    ),
                    "last_idle_initiation_at": (
                        last_idle_initiation.submitted_at
                        if last_idle_initiation is not None
                        else None
                    ),
                    "last_idle_initiation_status": (
                        last_idle_initiation.status
                        if last_idle_initiation is not None
                        else None
                    ),
                    "last_idle_initiation_reason_codes": list(
                        last_idle_initiation.reason_codes
                        if last_idle_initiation is not None
                        else ()
                    ),
                }
            )
        return {
            "idle_poll_seconds": self._DISABLED_POLL_SECONDS,
            "target_count": len(targets),
            "targets": targets,
        }


__all__ = ["PersonalHeartbeatSource"]
