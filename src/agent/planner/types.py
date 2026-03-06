"""PoolSelector 相关类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..types import AgentRequest
from ...schemas.observation import MessagePayload, Observation
from ...gate.types import GateHint


@dataclass
class PoolSelectorSignals:
    """从输入文本抽取的规则路由信号。"""

    text: str = ""
    has_code_signal: bool = False
    has_plan_signal: bool = False
    has_creative_signal: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PoolSelectorInputView:
    """Lightweight pool selector input view (not a ContextPack)."""

    current_input_text: str = ""
    recent_obs_view: List[Dict[str, str]] = field(default_factory=list)
    session_state_view: Dict[str, Any] = field(default_factory=dict)
    gate_hint_view: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)


def build_pool_selector_input_view(
    req: AgentRequest,
    *,
    recent_limit: int = 6,
    max_chars: int = 160,
) -> PoolSelectorInputView:
    """Build a lightweight view for pool selector decisions."""
    payload = getattr(req.obs, "payload", None)
    text = getattr(payload, "text", None)
    text = text.strip() if isinstance(text, str) else ""

    recent_obs_view = _extract_recent_obs_view(
        list(req.session_state.recent_obs or []),
        limit=recent_limit,
        max_chars=max_chars,
    )

    session_state_view = {
        "session_key": req.session_state.session_key,
        "processed_total": req.session_state.processed_total,
        "error_total": req.session_state.error_total,
        "last_active_at": req.session_state.last_active_at,
        "recent_obs_count": len(req.session_state.recent_obs or []),
    }

    gate_hint_view = _extract_gate_hint_view(req.gate_hint)

    return PoolSelectorInputView(
        current_input_text=text,
        recent_obs_view=recent_obs_view,
        session_state_view=session_state_view,
        gate_hint_view=gate_hint_view,
        meta={"recent_obs_count": len(req.session_state.recent_obs or [])},
    )


# Deprecated aliases
PlannerSignals = PoolSelectorSignals
PlannerInputView = PoolSelectorInputView
build_planner_input_view = build_pool_selector_input_view


def _extract_recent_obs_view(
    recent: List[Observation],
    *,
    limit: int,
    max_chars: int,
) -> List[Dict[str, str]]:
    if not recent:
        return []

    out: List[Dict[str, str]] = []
    for obs in recent[-limit:]:
        payload = getattr(obs, "payload", None)
        if not isinstance(payload, MessagePayload):
            continue
        text = (payload.text or "").strip()
        if not text:
            continue
        if len(text) > max_chars:
            text = text[: max_chars - 3] + "..."
        actor_id = getattr(getattr(obs, "actor", None), "actor_id", None)
        out.append(
            {
                "actor_id": str(actor_id or ""),
                "source_name": str(getattr(obs, "source_name", "") or ""),
                "text": text,
            }
        )
    return out


def _extract_gate_hint_view(hint: GateHint | None) -> Dict[str, Any]:
    if hint is None:
        return {}
    budget = getattr(hint, "budget", None)
    return {
        "model_tier": getattr(hint, "model_tier", None),
        "response_policy": getattr(hint, "response_policy", None),
        "budget_level": getattr(budget, "budget_level", None) if budget else None,
        "max_tokens": getattr(budget, "max_tokens", None) if budget else None,
        "max_tool_calls": getattr(budget, "max_tool_calls", None) if budget else None,
    }
