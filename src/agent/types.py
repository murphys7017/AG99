"""Agent 类型定义（Phase 0）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from ..gate.types import GateDecision, GateHint
from ..schemas.observation import Observation
from ..session_router import SessionState
from .context.types import ContextPack, ContextSlot


@dataclass
class AgentRequest:
    """Agent 处理请求（由 Core 构造并传入）。"""

    obs: Observation
    gate_decision: GateDecision
    session_state: SessionState
    now: datetime
    gate_hint: Optional[GateHint] = None

    def __post_init__(self) -> None:
        if self.gate_hint is None and self.gate_decision:
            self.gate_hint = self.gate_decision.hint


@dataclass
class RoutingPlan:
    """Queen 内部路由选择结果（Pool/Strategy/Context/Prompt 选择契约）。"""

    task_type: str = "chat"
    pool_id: str = "chat"
    required_context: tuple[str, ...] = ("recent_obs",)
    meta: Dict[str, Any] = field(default_factory=dict)


# Deprecated alias: 保留旧命名，后续版本删除
TaskPlan = RoutingPlan


@dataclass
class PoolResult:
    """Pool 原始产物容器。"""

    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentOutcome:
    """Agent 处理结果（Core 契约类型）。"""

    emit: List[Observation] = field(default_factory=list)
    trace: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


# ============================================================
# 兼容旧字段（保留，不用于新 Queen 主流程）
# ============================================================

@dataclass
class InfoPlan:
    """旧信息需求计划（Legacy，保留但不用于当前主链路）。"""

    sources: List[str] = field(default_factory=list)
    budget: Dict[str, Any] = field(default_factory=dict)
    tool_calls: Optional[List[str]] = None


@dataclass
class EvidenceItem:
    """旧证据条目（Legacy）。"""

    source: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidencePack:
    """旧证据包（Legacy）。"""

    items: List[EvidenceItem] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AnswerDraft:
    """旧回答草稿（Legacy）。"""

    text: str
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AnswerSpec:
    """旧回答规格（Legacy）。"""

    model: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Step:
    """[Deprecated] 旧执行步骤。"""

    type: Literal["SKILL", "AGENT", "TOOL"]
    target: str
    params: dict = field(default_factory=dict)


@dataclass
class Plan:
    """[Deprecated] 旧执行计划（Legacy Plan，保留兼容）。"""

    steps: List[Step] = field(default_factory=list)
    reason: str = ""


LegacyPlan = Plan


@dataclass
class AgentResponse:
    """[Deprecated] 旧 Agent 响应。"""

    emit: List[Observation] = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
