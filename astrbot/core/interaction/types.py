from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .effects import PersonaEffectCall


class RouteMode(str, Enum):
    SELF_REPLY = "self_reply"
    DELEGATE_TO_CORE = "delegate_to_core"
    HYBRID = "hybrid"


class FastRouteMode(str, Enum):
    SELF_REPLY = "self_reply"
    HYBRID = "hybrid"


class FinalizerMode(str, Enum):
    OFF = "off"
    AUTO = "auto"
    FORCE = "force"


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


@dataclass(slots=True)
class InteractionDecision:
    route_mode: RouteMode = RouteMode.DELEGATE_TO_CORE
    should_emit_immediate_reply: bool = False
    immediate_spoken_reply: str | None = None
    core_task_spec: CoreTaskSpec | None = None
    effect_calls: list[PersonaEffectCall] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def from_mapping(cls, payload: object) -> InteractionDecision | None:
        if not isinstance(payload, dict):
            return None
        route_mode_raw = str(
            payload.get("route_mode", RouteMode.DELEGATE_TO_CORE.value)
        )
        try:
            route_mode = RouteMode(route_mode_raw)
        except ValueError:
            route_mode = RouteMode.DELEGATE_TO_CORE
        immediate_spoken_reply = payload.get("immediate_spoken_reply")
        if immediate_spoken_reply is not None:
            immediate_spoken_reply = str(immediate_spoken_reply)
        core_task_spec = CoreTaskSpec.from_mapping(payload.get("core_task_spec"))
        effect_calls = _coerce_effect_calls(payload.get("effect_calls", []))
        return cls(
            route_mode=route_mode,
            should_emit_immediate_reply=bool(
                payload.get("should_emit_immediate_reply", False)
            ),
            immediate_spoken_reply=immediate_spoken_reply,
            core_task_spec=core_task_spec,
            effect_calls=effect_calls,
            reason=str(payload.get("reason", "") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_mode": self.route_mode.value,
            "should_emit_immediate_reply": self.should_emit_immediate_reply,
            "immediate_spoken_reply": self.immediate_spoken_reply,
            "core_task_spec": (
                self.core_task_spec.to_dict() if self.core_task_spec else None
            ),
            "effect_calls": [call.to_dict() for call in self.effect_calls],
            "reason": self.reason,
        }


@dataclass(slots=True)
class InteractionRouteDecision:
    mode: FastRouteMode = FastRouteMode.HYBRID

    @classmethod
    def from_mapping(cls, payload: object) -> InteractionRouteDecision | None:
        if not isinstance(payload, dict):
            return None
        raw_mode = str(payload.get("mode", "") or payload.get("route_mode", ""))
        if raw_mode == RouteMode.DELEGATE_TO_CORE.value:
            raw_mode = FastRouteMode.HYBRID.value
        try:
            mode = FastRouteMode(raw_mode)
        except ValueError:
            return None
        return cls(mode=mode)

    def to_interaction_decision(
        self,
        *,
        first_response: str | None,
        effect_calls: list[PersonaEffectCall] | None = None,
    ) -> InteractionDecision:
        reply = (first_response or "").strip() or None
        route_mode = (
            RouteMode.SELF_REPLY
            if self.mode == FastRouteMode.SELF_REPLY
            else RouteMode.HYBRID
        )
        return InteractionDecision(
            route_mode=route_mode,
            should_emit_immediate_reply=bool(reply),
            immediate_spoken_reply=reply,
            core_task_spec=None,
            effect_calls=list(effect_calls) if isinstance(effect_calls, list) else [],
            reason="fast_route",
        )


def _coerce_effect_calls(value: object) -> list[PersonaEffectCall]:
    if not isinstance(value, list):
        return []
    calls: list[PersonaEffectCall] = []
    for item in value:
        call = PersonaEffectCall.from_mapping(item)
        if call is not None:
            calls.append(call)
    return calls


@dataclass(slots=True)
class InteractionAgentConfig:
    enabled: bool = False
    default_enabled_for_platforms: list[str] = field(default_factory=list)
    platforms: dict[str, dict[str, Any]] = field(default_factory=dict)
    decision_provider_id: str = ""
    decision_temperature: float = 0.5
    decision_timeout: float = 15.0
    expression_provider_id: str = ""
    expression_temperature: float = 0.6
    expression_timeout: float = 8.0
    router_provider_id: str = ""
    router_temperature: float = 0.0
    router_timeout: float = 3.0
    parallel_expression_router: bool = True
    finalizer_provider_id: str = ""
    finalizer_temperature: float = 0.6
    finalizer_max_tokens: int = 512
    finalizer_timeout: float = 15.0
    finalizer_mode: FinalizerMode = FinalizerMode.AUTO
    memory_window_size: int = 8
    stream_observation_enabled: bool = True
    stream_observation_min_chars: int = 200
    stream_interjection_enabled: bool = True
    stream_interjection_provider_id: str = ""
    stream_interjection_temperature: float = 0.5
    stream_interjection_timeout: float = 15.0
    stream_interjection_max_per_turn: int = 1


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
