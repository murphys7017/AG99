from typing import Any

from .types import InteractionAgentConfig


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


def is_middleware_enabled(config: Any) -> bool:
    interaction_config = config.get("interaction_middleware", {})
    return bool(interaction_config.get("enabled", False))


def load_interaction_agent_config(config: Any) -> InteractionAgentConfig:
    interaction_config = config.get("interaction_middleware", {})
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
        contributor_timeout=max(
            0.1,
            _float_or_default(
                interaction_config.get("contributor_timeout", 1.0),
                1.0,
            ),
        ),
    )
