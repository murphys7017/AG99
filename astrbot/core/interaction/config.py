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
    return bool(interaction_config.get("enabled", True))


def load_interaction_agent_config(config: Any) -> InteractionAgentConfig:
    interaction_config = config.get("interaction_middleware", {})
    expression_provider_id = str(
        interaction_config.get("expression_provider_id", "") or ""
    )
    router_provider_id = str(
        interaction_config.get("router_provider_id", "") or ""
    )
    planner_provider_id = str(
        interaction_config.get("planner_provider_id", "") or ""
    ) or expression_provider_id
    quiet_hours_enabled = bool(
        interaction_config.get("personal_runtime_quiet_hours_enabled", False)
    )
    return InteractionAgentConfig(
        enabled=bool(interaction_config.get("enabled", True)),
        expression_provider_id=expression_provider_id,
        expression_temperature=_float_or_default(
            interaction_config.get("expression_temperature", 0.6),
            0.6,
        ),
        expression_timeout=_float_or_default(
            interaction_config.get("expression_timeout", 8.0),
            8.0,
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
        planner_provider_id=planner_provider_id,
        planner_temperature=_float_or_default(
            interaction_config.get("planner_temperature", 0.1),
            0.1,
        ),
        planner_timeout=_float_or_default(
            interaction_config.get("planner_timeout", 8.0),
            8.0,
        ),
        personal_policy_enabled=bool(
            interaction_config.get("personal_policy_enabled", False)
        ),
        personal_policy_provider_id=str(
            interaction_config.get("personal_policy_provider_id", "") or ""
        ),
        personal_policy_temperature=_float_or_default(
            interaction_config.get("personal_policy_temperature", 0.1),
            0.1,
        ),
        personal_policy_timeout=max(
            0.1,
            _float_or_default(
                interaction_config.get("personal_policy_timeout", 8.0),
                8.0,
            ),
        ),
        personal_policy_daily_call_limit=max(
            0,
            _int_or_default(
                interaction_config.get("personal_policy_daily_call_limit", 200),
                200,
            ),
        ),
        personal_runtime_muted=bool(
            interaction_config.get("personal_runtime_muted", False)
        ),
        personal_runtime_quiet_hours_enabled=quiet_hours_enabled,
        personal_runtime_quiet_hours_start=min(
            23,
            max(
                0,
                _int_or_default(
                    interaction_config.get("personal_runtime_quiet_hours_start", 23),
                    23,
                ),
            ),
        ),
        personal_runtime_quiet_hours_end=min(
            23,
            max(
                0,
                _int_or_default(
                    interaction_config.get("personal_runtime_quiet_hours_end", 8),
                    8,
                ),
            ),
        ),
        personal_runtime_timezone=(
            str(config.get("timezone", "") or "").strip() or None
        )
        if quiet_hours_enabled
        else None,
        personal_runtime_reply_cooldown_seconds=max(
            0.0,
            _float_or_default(
                interaction_config.get(
                    "personal_runtime_reply_cooldown_seconds", 1800.0
                ),
                1800.0,
            ),
        ),
        personal_runtime_no_action_cooldown_seconds=max(
            0.0,
            _float_or_default(
                interaction_config.get(
                    "personal_runtime_no_action_cooldown_seconds", 300.0
                ),
                300.0,
            ),
        ),
        personal_runtime_daily_proactive_output_limit=max(
            0,
            _int_or_default(
                interaction_config.get(
                    "personal_runtime_daily_proactive_output_limit", 6
                ),
                6,
            ),
        ),
        personal_runtime_conversation_continuation_seconds=max(
            0.0,
            _float_or_default(
                interaction_config.get(
                    "personal_runtime_conversation_continuation_seconds", 120.0
                ),
                120.0,
            ),
        ),
        personal_heartbeat_enabled=bool(
            interaction_config.get("personal_heartbeat_enabled", False)
        ),
        personal_conversation_activity_enabled=bool(
            interaction_config.get("personal_conversation_activity_enabled", False)
        ),
        personal_heartbeat_interval_seconds=max(
            30.0,
            _float_or_default(
                interaction_config.get("personal_heartbeat_interval_seconds", 300.0),
                300.0,
            ),
        ),
        personal_idle_initiation_enabled=bool(
            interaction_config.get("personal_idle_initiation_enabled", False)
        ),
        personal_idle_initiation_after_seconds=max(
            30.0,
            _float_or_default(
                interaction_config.get(
                    "personal_idle_initiation_after_seconds",
                    1800.0,
                ),
                1800.0,
            ),
        ),
        memory_window_size=int(interaction_config.get("memory_window_size", 8) or 8),
        persona_history_window_size=max(
            1,
            _int_or_default(
                interaction_config.get("persona_history_window_size", 50),
                50,
            ),
        ),
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
