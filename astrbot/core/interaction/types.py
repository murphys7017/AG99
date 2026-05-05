from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RouteMode(str, Enum):
    SELF_REPLY = "self_reply"
    DELEGATE_TO_CORE = "delegate_to_core"
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
    plugin_hints: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""
    is_fallback: bool = False
    fallback_reason: str | None = None

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
        plugin_hints = payload.get("plugin_hints", {})
        if not isinstance(plugin_hints, dict):
            plugin_hints = {}
        try:
            confidence = float(payload.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return cls(
            route_mode=route_mode,
            should_emit_immediate_reply=bool(
                payload.get("should_emit_immediate_reply", False)
            ),
            immediate_spoken_reply=immediate_spoken_reply,
            core_task_spec=core_task_spec,
            plugin_hints=plugin_hints,
            confidence=confidence,
            reason=str(payload.get("reason", "") or ""),
            is_fallback=bool(payload.get("is_fallback", False)),
            fallback_reason=(
                str(payload.get("fallback_reason"))
                if payload.get("fallback_reason") is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_mode": self.route_mode.value,
            "should_emit_immediate_reply": self.should_emit_immediate_reply,
            "immediate_spoken_reply": self.immediate_spoken_reply,
            "core_task_spec": (
                self.core_task_spec.to_dict() if self.core_task_spec else None
            ),
            "plugin_hints": dict(self.plugin_hints),
            "confidence": self.confidence,
            "reason": self.reason,
            "is_fallback": self.is_fallback,
            "fallback_reason": self.fallback_reason,
        }


@dataclass(slots=True)
class InteractionAgentConfig:
    enabled: bool = False
    default_enabled_for_platforms: list[str] = field(default_factory=list)
    platforms: dict[str, dict[str, Any]] = field(default_factory=dict)
    decision_provider_id: str = ""
    decision_model: str = ""
    decision_temperature: float = 0.5
    decision_max_tokens: int = 512
    decision_timeout: float = 15.0
    decision_confidence_threshold: float = 0.6
    finalizer_provider_id: str = ""
    finalizer_model: str = ""
    finalizer_temperature: float = 0.6
    finalizer_max_tokens: int = 512
    finalizer_mode: FinalizerMode = FinalizerMode.AUTO
    memory_window_size: int = 8
    stream_observation_enabled: bool = True
    stream_observation_min_chars: int = 200
    stream_interjection_enabled: bool = True
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
