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
    requires_visual_understanding: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: object) -> CoreTaskSpec | None:
        if not isinstance(payload, dict):
            return None
        expected_keys = {
            "task_intent",
            "task_summary",
            "execution_prompt",
            "suggested_capabilities",
        }
        if set(payload) not in (
            expected_keys,
            expected_keys | {"requires_visual_understanding"},
        ):
            return None
        task_intent = payload["task_intent"]
        task_summary = payload["task_summary"]
        execution_prompt = payload["execution_prompt"]
        suggested_capabilities = payload["suggested_capabilities"]
        requires_visual_understanding = payload.get("requires_visual_understanding", False)
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
        if not isinstance(requires_visual_understanding, bool):
            return None
        normalized_capabilities = [
            item.strip()
            for item in suggested_capabilities
            if item.strip()
        ]
        # Seeing the current turn's image is an input requirement, not a
        # workspace file operation. Preserve workspace_io only when the
        # Planner also requested another concrete capability.
        if requires_visual_understanding and normalized_capabilities == [
            "workspace_io"
        ]:
            normalized_capabilities = []
        return cls(
            task_intent=task_intent.strip(),
            task_summary=task_summary.strip(),
            execution_prompt=execution_prompt.strip(),
            suggested_capabilities=normalized_capabilities,
            requires_visual_understanding=requires_visual_understanding,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_intent": self.task_intent,
            "task_summary": self.task_summary,
            "execution_prompt": self.execution_prompt,
            "suggested_capabilities": list(self.suggested_capabilities),
            "requires_visual_understanding": self.requires_visual_understanding,
            "metadata": dict(self.metadata),
        }

    def requires_direct_web_research(self) -> bool:
        """Whether this turn must use in-turn research instead of a handoff.

        Planner capability labels are advisory.  The execution boundary also
        recognizes an unambiguous web-research task description so a weaker
        planner cannot silently re-enable subagent handoff by using a natural
        language label instead of ``web_research``.
        """
        normalized_capabilities = {
            item.strip().lower().replace("-", "_").replace(" ", "_")
            for item in self.suggested_capabilities
        }
        if normalized_capabilities & {
            "web_research",
            "web_search",
            "online_research",
            "internet_research",
            "internet_search",
            "联网搜索",
            "网络搜索",
            "网上搜索",
            "联网检索",
            "网络检索",
            "网上检索",
        }:
            return True

        task_text = " ".join(
            (self.task_intent, self.task_summary, self.execution_prompt)
        ).lower()
        has_web_scope = any(
            marker in task_text
            for marker in (
                "联网",
                "网络",
                "网上",
                "互联网",
                "网站",
                "web",
                "online",
                "internet",
            )
        )
        has_research_action = any(
            marker in task_text
            for marker in ("搜索", "检索", "查询", "search", "research", "lookup")
        )
        return has_web_scope and has_research_action


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
    enabled: bool = True
    turn_timeout: float = 120.0
    parallel_plugin_runtime_enabled: bool = False
    plugin_parallel_window_seconds: float = 3.0
    persona_plugin_context_mode: str = "wait_complete"
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
    personal_runtime_direct_continuation_seconds: float = 10.0
    personal_runtime_conversation_continuation_seconds: float = 120.0
    personal_heartbeat_enabled: bool = False
    personal_conversation_activity_enabled: bool = False
    personal_heartbeat_interval_seconds: float = 300.0
    personal_idle_initiation_enabled: bool = False
    personal_idle_initiation_after_seconds: float = 1800.0
    memory_window_size: int = 8
    persona_history_window_size: int = 50
    stream_observation_enabled: bool = True
    stream_observation_min_chars: int = 200
    stream_interjection_enabled: bool = True
    stream_interjection_max_per_turn: int = 1
    tool_stage_observation_enabled: bool = True
    tool_stage_observation_delay_seconds: float = 8.0
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
