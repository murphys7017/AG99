from typing import Any

from .types import FinalizerMode, InteractionAgentConfig


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


def load_interaction_agent_config(config: Any) -> InteractionAgentConfig:
    interaction_config = config.get("interaction_middleware", {})
    finalizer_mode_raw = str(
        interaction_config.get("finalizer_mode", FinalizerMode.AUTO.value)
    )
    try:
        finalizer_mode = FinalizerMode(finalizer_mode_raw)
    except ValueError:
        finalizer_mode = FinalizerMode.AUTO
    decision_provider_id = str(
        interaction_config.get("decision_provider_id", "") or ""
    )
    decision_temperature = _float_or_default(
        interaction_config.get("decision_temperature", 0.5),
        0.5,
    )
    decision_timeout = _float_or_default(
        interaction_config.get("decision_timeout", 15.0),
        15.0,
    )
    expression_provider_id = str(
        interaction_config.get("expression_provider_id", "") or ""
    ) or decision_provider_id
    router_provider_id = str(
        interaction_config.get("router_provider_id", "") or ""
    ) or decision_provider_id
    return InteractionAgentConfig(
        enabled=bool(interaction_config.get("enabled", False)),
        default_enabled_for_platforms=list(
            interaction_config.get("default_enabled_for_platforms", [])
        ),
        platforms=dict(interaction_config.get("platforms", {})),
        decision_provider_id=decision_provider_id,
        decision_temperature=decision_temperature,
        decision_timeout=decision_timeout,
        expression_provider_id=expression_provider_id,
        expression_temperature=_float_or_default(
            interaction_config.get("expression_temperature", decision_temperature),
            decision_temperature,
        ),
        expression_timeout=_float_or_default(
            interaction_config.get("expression_timeout", decision_timeout),
            decision_timeout,
        ),
        router_provider_id=router_provider_id,
        router_temperature=_float_or_default(
            interaction_config.get("router_temperature", 0.0),
            0.0,
        ),
        router_timeout=_float_or_default(
            interaction_config.get("router_timeout", 3.0),
            3.0,
        ),
        parallel_expression_router=bool(
            interaction_config.get("parallel_expression_router", True)
        ),
        finalizer_provider_id=str(
            interaction_config.get("finalizer_provider_id", "") or ""
        ),
        finalizer_temperature=float(
            interaction_config.get("finalizer_temperature", 0.6) or 0.6
        ),
        finalizer_max_tokens=int(
            interaction_config.get("finalizer_max_tokens", 512) or 512
        ),
        finalizer_mode=finalizer_mode,
        memory_window_size=int(interaction_config.get("memory_window_size", 8) or 8),
        stream_observation_enabled=bool(
            interaction_config.get("stream_observation_enabled", True)
        ),
        stream_observation_min_chars=max(
            1,
            _int_or_default(
                interaction_config.get("stream_observation_min_chars", 200),
                200,
            ),
        ),
        stream_interjection_enabled=bool(
            interaction_config.get("stream_interjection_enabled", True)
        ),
        stream_interjection_max_per_turn=max(
            0,
            _int_or_default(
                interaction_config.get("stream_interjection_max_per_turn", 1),
                1,
            ),
        ),
    )
