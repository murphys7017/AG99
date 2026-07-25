from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.core.platform.message_type import MessageType

from .config import load_interaction_agent_config
from .observation import RuntimeObservation, RuntimeObservationTarget

if TYPE_CHECKING:
    from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
    from astrbot.core.star.context import Context

    from .observation_inbox import ObservationAdmissionResult
    from .personal_runtime import PersonalRuntimeManager


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

    def _prune_inactive_targets(self, active_targets: set[str]) -> None:
        for target_key in tuple(self._next_tick_at):
            if target_key not in active_targets:
                del self._next_tick_at[target_key]

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
                continue
            due_at = self._next_tick_at.get(target_key, occurred_at)
            if due_at > occurred_at:
                continue

            platform = self._context.get_platform_inst(session.platform_id)
            if platform is None:
                continue
            metadata = platform.meta()
            if not metadata.support_proactive_message:
                continue

            interval = runtime_settings.personal_heartbeat_interval_seconds
            config_info = self._config_manager.get_conf_info(session)
            observation = RuntimeObservation(
                kind="heartbeat",
                source="personal_runtime.heartbeat",
                occurred_at=occurred_at,
                expires_at=occurred_at + interval * 2,
                coalesce_key="heartbeat",
                target_session=RuntimeObservationTarget(
                    platform_id=session.platform_id,
                    platform_name=metadata.name,
                    message_type=session.message_type,
                    session_id=session.session_id,
                    support_proactive_message=True,
                    group_id=(
                        session.session_id
                        if session.message_type is MessageType.GROUP_MESSAGE
                        else None
                    ),
                ),
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
                self._next_tick_at[target_key] = occurred_at + min(
                    interval,
                    self._DISABLED_POLL_SECONDS,
                )
                continue
            self._next_tick_at[target_key] = occurred_at + interval
            results.append(result)
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
            next_tick_at = self._next_tick_at.get(str(session))
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
                }
            )
        return {
            "idle_poll_seconds": self._DISABLED_POLL_SECONDS,
            "target_count": len(targets),
            "targets": targets,
        }


__all__ = ["PersonalHeartbeatSource"]
