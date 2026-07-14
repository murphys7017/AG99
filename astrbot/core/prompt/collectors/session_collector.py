"""
Session context collector for prompt context packing.
"""

from __future__ import annotations

import datetime
import zoneinfo
from typing import TYPE_CHECKING, Any

from astrbot.core import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextSlot
from ..interfaces.context_collector_inferface import ContextCollectorInterface
from ..strict_mode import handle_prompt_pipeline_failure, is_prompt_pipeline_strict

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class SessionCollector(ContextCollectorInterface):
    """Collect stable per-request session metadata."""

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        del provider_request

        slots: list[ContextSlot] = []

        try:
            datetime_payload = self._build_datetime_payload(
                plugin_context=plugin_context,
                config=config,
            )
            if datetime_payload is not None:
                slots.append(
                    ContextSlot(
                        name="session.datetime",
                        value=datetime_payload,
                        category="session",
                        source="session_runtime",
                        meta={
                            "from_config": datetime_payload["source"]
                            != "local_timezone",
                        },
                    )
                )
        except Exception as exc:  # noqa: BLE001
            handle_prompt_pipeline_failure(
                strict=is_prompt_pipeline_strict(config),
                message=f"Failed to collect session datetime: {exc}",
                exc=exc,
                log_failure=lambda exc=exc: logger.warning(
                    "Failed to collect session datetime: %s",
                    exc,
                    exc_info=True,
                ),
            )

        try:
            user_info_payload = self._build_user_info_payload(event=event)
            if user_info_payload is not None:
                slots.append(
                    ContextSlot(
                        name="session.user_info",
                        value=user_info_payload,
                        category="session",
                        source="event_session",
                        meta={"redacted": False},
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect session user info: %s", exc, exc_info=True
            )

        return slots

    def _build_datetime_payload(
        self,
        *,
        plugin_context: Context,
        config: MainAgentBuildConfig,
    ) -> dict[str, str] | None:
        timezone_name, source = self._resolve_timezone(
            plugin_context=plugin_context,
            config=config,
        )

        if timezone_name:
            try:
                now = datetime.datetime.now(zoneinfo.ZoneInfo(timezone_name))
                resolved_timezone = timezone_name
            except Exception as exc:  # noqa: BLE001
                handle_prompt_pipeline_failure(
                    strict=is_prompt_pipeline_strict(config),
                    message=(
                        f"Failed to use configured timezone {timezone_name}: {exc}"
                    ),
                    exc=exc,
                    log_failure=lambda exc=exc: logger.warning(
                        "Failed to use configured timezone %s: %s. Falling back to local timezone.",
                        timezone_name,
                        exc,
                    ),
                )
                now = datetime.datetime.now().astimezone()
                resolved_timezone = self._local_timezone_name(now)
                source = "local_timezone"
        else:
            now = datetime.datetime.now().astimezone()
            resolved_timezone = self._local_timezone_name(now)
            source = "local_timezone"

        return {
            "text": now.strftime("%Y-%m-%d %H:%M (%Z)"),
            "iso": now.isoformat(timespec="seconds"),
            "timezone": resolved_timezone,
            "source": source,
        }

    def _resolve_timezone(
        self,
        *,
        plugin_context: Context,
        config: MainAgentBuildConfig,
    ) -> tuple[str | None, str]:
        if config.timezone:
            return config.timezone, "config.timezone"

        global_config = plugin_context.get_config()
        if isinstance(global_config, dict):
            timezone_name = global_config.get("timezone")
            if isinstance(timezone_name, str) and timezone_name.strip():
                return timezone_name.strip(), "plugin_context.get_config"

        return None, "local_timezone"

    def _build_user_info_payload(
        self,
        *,
        event: AstrMessageEvent,
    ) -> dict[str, Any] | None:
        message_obj = getattr(event, "message_obj", None)
        sender = getattr(message_obj, "sender", None)

        user_id = getattr(sender, "user_id", None)
        nickname = getattr(sender, "nickname", None)
        platform_name = None
        try:
            platform_name = event.get_platform_name()
        except Exception:  # noqa: BLE001
            platform_name = None

        group_id = getattr(message_obj, "group_id", None)
        if group_id is None:
            try:
                group_id = event.get_group_id()
            except Exception:  # noqa: BLE001
                group_id = None
        if isinstance(group_id, str):
            group_id = group_id.strip() or None

        group = getattr(message_obj, "group", None)
        group_name = self._resolve_group_name(group)

        if all(
            value is None
            for value in [
                user_id,
                nickname,
                platform_name,
                group_id,
                group_name,
                getattr(event, "unified_msg_origin", None),
            ]
        ):
            return None

        return {
            "user_id": user_id,
            "nickname": nickname,
            "role": "current_speaker",
            "platform_name": platform_name,
            "umo": getattr(event, "unified_msg_origin", None),
            "group_id": group_id,
            "group_name": group_name,
            "is_group": group_id is not None,
            "conversation_scope": (
                "group_multi_user" if group_id is not None else "private_single_user"
            ),
        }

    def _resolve_group_name(self, group: object | None) -> str | None:
        if group is None:
            return None

        group_name = getattr(group, "group_name", None)
        if isinstance(group_name, str) and group_name.strip():
            return group_name
        return None

    def _local_timezone_name(self, now: datetime.datetime) -> str:
        tzinfo = now.tzinfo
        if tzinfo is None:
            return "local"

        key = getattr(tzinfo, "key", None)
        if isinstance(key, str) and key:
            return key

        name = now.strftime("%Z")
        if name:
            return name
        return str(tzinfo)
