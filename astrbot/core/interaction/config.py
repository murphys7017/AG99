from typing import Any


def is_middleware_enabled_for_platform(platform_id: str, config: Any) -> bool:
    interaction_config = config.get("interaction_middleware", {})
    if not interaction_config.get("enabled", False):
        return False

    platforms = interaction_config.get("platforms", {})
    platform_config = platforms.get(platform_id, {})
    if "enabled" in platform_config:
        return bool(platform_config["enabled"])

    default_enabled_for_platforms = interaction_config.get(
        "default_enabled_for_platforms",
        [],
    )
    return platform_id in default_enabled_for_platforms
