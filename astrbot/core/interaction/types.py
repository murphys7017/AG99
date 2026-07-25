from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class InteractionRouteMode(str, Enum):
    SILENT = "silent"
    PERSONA = "persona"
    HYBRID = "hybrid"


@dataclass(slots=True)
class CoreTaskSpec:
    task_intent: str = "general"
    task_summary: str = ""
    execution_prompt: str = ""
    suggested_capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: object) -> CoreTaskSpec | None:
        if not isinstance(payload, dict):
            return None
        if set(payload) != {
            "task_intent",
            "task_summary",
            "execution_prompt",
            "suggested_capabilities",
        }:
            return None
        task_intent = payload["task_intent"]
        task_summary = payload["task_summary"]
        execution_prompt = payload["execution_prompt"]
        suggested_capabilities = payload["suggested_capabilities"]
        if not all(
            isinstance(value, str)
            for value in (task_intent, task_summary, execution_prompt)
        ):
            return None
        if not all(
            value.strip()
            for value in (task_intent, task_summary, execution_prompt)
        ):
            return None
        if not isinstance(suggested_capabilities, list) or not all(
            isinstance(item, str) for item in suggested_capabilities
        ):
            return None
        return cls(
            task_intent=task_intent.strip(),
            task_summary=task_summary.strip(),
            execution_prompt=execution_prompt.strip(),
            suggested_capabilities=[
                item.strip()
                for item in suggested_capabilities
                if item.strip()
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_intent": self.task_intent,
            "task_summary": self.task_summary,
            "execution_prompt": self.execution_prompt,
            "suggested_capabilities": list(self.suggested_capabilities),
            "metadata": dict(self.metadata),
        }


class CorePlanningAction(str, Enum):
    EXECUTE = "execute"
    NOT_REQUIRED = "not_required"


@dataclass(slots=True)
class CorePlanningDecision:
    action: CorePlanningAction
    task_spec: CoreTaskSpec | None = None

    @classmethod
    def from_mapping(cls, payload: object) -> CorePlanningDecision | None:
        if not isinstance(payload, dict):
            return None
        if set(payload) != {"decision", "core_task_spec"}:
            return None
        raw_action = payload["decision"]
        if not isinstance(raw_action, str):
            return None
        try:
            action = CorePlanningAction(raw_action.strip().lower())
        except ValueError:
            return None
        raw_task_spec = payload["core_task_spec"]
        if action is CorePlanningAction.EXECUTE:
            task_spec = CoreTaskSpec.from_mapping(raw_task_spec)
            if task_spec is None:
                return None
        else:
            if raw_task_spec is not None:
                return None
            task_spec = None
        return cls(action=action, task_spec=task_spec)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.action.value,
            "core_task_spec": self.task_spec.to_dict() if self.task_spec else None,
        }


@dataclass(slots=True)
class InteractionRouteDecision:
    route_mode: InteractionRouteMode = InteractionRouteMode.HYBRID
    reason: str = "fast_route"

    @classmethod
    def from_mapping(cls, payload: object) -> InteractionRouteDecision | None:
        if not isinstance(payload, dict):
            return None
        raw_mode = str(payload.get("mode", "") or payload.get("route_mode", ""))
        if raw_mode not in {
            InteractionRouteMode.SILENT.value,
            InteractionRouteMode.PERSONA.value,
            InteractionRouteMode.HYBRID.value,
        }:
            return None
        try:
            route_mode = InteractionRouteMode(raw_mode)
        except ValueError:
            return None
        return cls(route_mode=route_mode)

    def to_dict(self) -> dict[str, str]:
        return {
            "route_mode": self.route_mode.value,
            "reason": self.reason,
        }


@dataclass(slots=True)
class InteractionAgentConfig:
    enabled: bool = False
    expression_provider_id: str = ""
    expression_temperature: float = 0.6
    expression_timeout: float = 8.0
    router_provider_id: str = ""
    router_temperature: float = 0.0
    router_timeout: float = 3.0
    planner_provider_id: str = ""
    planner_temperature: float = 0.1
    planner_timeout: float = 8.0
    personal_policy_enabled: bool = False
    personal_policy_provider_id: str = ""
    personal_policy_temperature: float = 0.1
    personal_policy_timeout: float = 8.0
    personal_policy_daily_call_limit: int = 200
    personal_runtime_muted: bool = False
    personal_runtime_quiet_hours_enabled: bool = False
    personal_runtime_quiet_hours_start: int = 23
    personal_runtime_quiet_hours_end: int = 8
    personal_runtime_timezone: str | None = None
    personal_runtime_reply_cooldown_seconds: float = 1800.0
    personal_runtime_no_action_cooldown_seconds: float = 300.0
    personal_runtime_daily_proactive_output_limit: int = 6
    personal_runtime_conversation_continuation_seconds: float = 120.0
    personal_heartbeat_enabled: bool = False
    personal_conversation_activity_enabled: bool = False
    personal_heartbeat_interval_seconds: float = 300.0
    memory_window_size: int = 8
    stream_observation_enabled: bool = True
    stream_observation_min_chars: int = 200
    stream_interjection_enabled: bool = True
    stream_interjection_max_per_turn: int = 1
    contributor_timeout: float = 1.0


@dataclass(slots=True)
class InteractionPromptBuildConfig:
    provider_settings: dict[str, Any] = field(default_factory=dict)
    timezone: str | None = None
    provider_wake_prefix: str = ""
    file_extract_enabled: bool = False
    file_extract_prov: str = "moonshotai"
    file_extract_msh_api_key: str = ""
    max_quoted_fallback_images: int = 20
    prompt_pipeline_strict_mode: bool = True
    contributor_timeout: float = 1.0
