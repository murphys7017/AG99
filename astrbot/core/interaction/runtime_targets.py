from __future__ import annotations

from collections.abc import Mapping

from astrbot.core.platform.message_session import MessageSession


def configured_runtime_observation_target_values(
    config: Mapping[str, object],
) -> tuple[str, ...]:
    """Return explicit Runtime observation targets or the legacy default fallback."""
    platform_settings = config.get("platform_settings", {})
    if not isinstance(platform_settings, Mapping):
        return ()

    configured_targets = platform_settings.get(
        "personal_runtime_observation_targets"
    )
    if isinstance(configured_targets, list) and configured_targets:
        values = configured_targets
    else:
        values = [platform_settings.get("proactive_message_target")]

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def configured_runtime_observation_targets(
    config: Mapping[str, object],
) -> tuple[MessageSession, ...]:
    """Parse valid Runtime observation targets while preserving their config order."""
    targets: list[MessageSession] = []
    for value in configured_runtime_observation_target_values(config):
        try:
            targets.append(MessageSession.from_str(value))
        except (TypeError, ValueError):
            continue
    return tuple(targets)


__all__ = [
    "configured_runtime_observation_target_values",
    "configured_runtime_observation_targets",
]
