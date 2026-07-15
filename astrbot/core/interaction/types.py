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
        suggested_capabilities = payload.get("suggested_capabilities", [])
        if not isinstance(suggested_capabilities, list):
            suggested_capabilities = []
        return cls(
            task_intent=str(payload.get("task_intent", "general") or "general"),
            task_summary=str(payload.get("task_summary", "") or ""),
            execution_prompt=str(payload.get("execution_prompt", "") or ""),
            suggested_capabilities=[
                str(item).strip()
                for item in suggested_capabilities
                if str(item).strip()
            ],
            metadata=payload.get("metadata", {})
            if isinstance(payload.get("metadata", {}), dict)
            else {},
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
        raw_action = str(payload.get("decision", "") or "").strip().lower()
        try:
            action = CorePlanningAction(raw_action)
        except ValueError:
            return None
        task_spec = CoreTaskSpec.from_mapping(payload.get("core_task_spec"))
        if action is CorePlanningAction.EXECUTE:
            if task_spec is None:
                return None
            if not all(
                (
                    task_spec.task_intent.strip(),
                    task_spec.task_summary.strip(),
                    task_spec.execution_prompt.strip(),
                )
            ):
                return None
        else:
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
