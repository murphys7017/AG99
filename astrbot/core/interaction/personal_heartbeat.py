from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from astrbot.api import logger

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

    async def tick(self) -> ObservationAdmissionResult | None:
        session = self._context.get_proactive_message_target()
        if session is None:
            return None

        runtime_config = self._config_manager.get_conf(session)
        runtime_settings = load_interaction_agent_config(runtime_config)
        if not runtime_settings.personal_heartbeat_enabled:
            return None

        platform = next(
            (
                item
                for item in self._context.platform_manager.platform_insts
                if item.meta().id == session.platform_id
            ),
            None,
        )
        if platform is None:
            return None
        metadata = platform.meta()
        if not metadata.support_proactive_message:
            return None

        config_info = self._config_manager.get_conf_info(session)
        occurred_at = time.time()
        interval = runtime_settings.personal_heartbeat_interval_seconds
        observation = RuntimeObservation(
            kind="heartbeat",
            source="personal_runtime.heartbeat",
            occurred_at=occurred_at,
            expires_at=occurred_at + interval * 2,
            coalesce_key="default_target",
            target_session=RuntimeObservationTarget(
                platform_id=session.platform_id,
                platform_name=metadata.name,
                message_type=session.message_type,
                session_id=session.session_id,
                support_proactive_message=True,
            ),
        )
        return await self._runtime_manager.submit_observation(
            observation,
            config_id=str(config_info.get("id") or "default"),
            plugin_context=self._context,
            runtime_config=runtime_config,
        )

    def _next_poll_seconds(self) -> float:
        session = self._context.get_proactive_message_target()
        if session is None:
            return self._DISABLED_POLL_SECONDS
        runtime_config = self._config_manager.get_conf(session)
        settings = load_interaction_agent_config(runtime_config)
        if not settings.personal_heartbeat_enabled:
            return self._DISABLED_POLL_SECONDS
        return settings.personal_heartbeat_interval_seconds


__all__ = ["PersonalHeartbeatSource"]
