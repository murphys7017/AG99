from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Literal, Optional, Protocol, TYPE_CHECKING

from ..schemas.observation import Observation


class GateAction(str, Enum):
    DROP = "drop"
    SINK = "sink"
    DELIVER = "deliver"


class Scene(str, Enum):
    DIALOGUE = "dialogue"
    GROUP = "group"
    SYSTEM = "system"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ALERT = "alert"
    UNKNOWN = "unknown"


# ========== Gate Hint 相关类型 ==========

@dataclass
class BudgetSpec:
    """执行预算规格"""
    budget_level: Literal["tiny", "normal", "deep"] = "normal"
    
    # 计算资源
    time_ms: int = 1500
    max_tokens: int = 512
    max_parallel: int = 2
    
    # 能力约束
    evidence_allowed: bool = True
    max_tool_calls: int = 1
    can_search_kb: bool = True
    can_call_tools: bool = True
    
    # 扩展字段（用于未来增强）
    auto_clarify: bool = False  # 是否允许主动澄清
    fallback_mode: bool = False  # 回源失败时的降级模式


@dataclass
class GateHint:
    """Gate 向 Agent/PoolSelector 输出的预算与风险提示"""
    
    # 平台层决策
    model_tier: Literal["low", "high"] = "low"
    response_policy: Literal["respond_now", "clarify", "ack"] = "respond_now"
    
    # 预算
    budget: BudgetSpec = field(default_factory=BudgetSpec)
    
    # 可观测
    reason_tags: List[str] = field(default_factory=list)
    # 可能值如："score_high", "score_medium", "score_low",
    # "override_deliver", "emergency_mode", "system_overload",
    # "user_dialogue_safe_valve" 等
    
    # 元数据
    debug: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GateDecision:
    action: GateAction
    scene: Scene
    session_key: str
    target_worker: Optional[str] = None
    model_tier: Optional[str] = None
    response_policy: Optional[str] = None
    tool_policy: Optional[Dict[str, Any]] = None
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    tags: Dict[str, str] = field(default_factory=dict)
    fingerprint: Optional[str] = None
    hint: GateHint = field(default_factory=GateHint)


@dataclass
class GateOutcome:
    decision: GateDecision
    emit: List[Observation] = field(default_factory=list)
    ingest: List[Observation] = field(default_factory=list)


@dataclass
class GateContext:
    now: datetime
    config: "GateConfig"
    system_session_key: str
    metrics: Optional["GateMetrics"] = None
    session_state: Any = None
    system_health: Optional[Dict[str, Any]] = None
    trace: Optional[Callable[[str, Any], None]] = None


@dataclass
class GateWip:
    scene: Optional[Scene] = None
    features: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    tags: Dict[str, str] = field(default_factory=dict)
    fingerprint: Optional[str] = None

    action_hint: Optional[GateAction] = None
    model_tier: Optional[str] = None
    response_policy: Optional[str] = None
    tool_policy: Optional[Dict[str, Any]] = None

    emit: List[Observation] = field(default_factory=list)
    ingest: List[Observation] = field(default_factory=list)
    
    # Gate 提示信息
    gate_hint: Optional[GateHint] = None


class GateStage(Protocol):
    def apply(self, obs: Observation, ctx: GateContext, wip: GateWip) -> None: ...


if TYPE_CHECKING:
    from .config import GateConfig
    from .metrics import GateMetrics
