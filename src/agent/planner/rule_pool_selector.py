"""规则 PoolSelector（Phase 0 默认实现）。"""

from __future__ import annotations

from ..types import AgentRequest, RoutingPlan
from .signals import extract_signals
from .types import PoolSelectorInputView
from .validator import normalize_routing_plan


class RulePoolSelector:
    """基于简单信号做任务类型路由。"""

    kind = "rule"

    async def select(self, req: AgentRequest, view: PoolSelectorInputView | None = None) -> RoutingPlan:
        override_text = view.current_input_text if view is not None else None
        signals = extract_signals(req, text_override=override_text)
        reason = "dialogue_default"
        task_type = "chat"
        pool_id = "chat"
        complexity = "simple"
        confidence = 0.65
        strategy = "single_pass"

        if signals.has_code_signal:
            task_type = "code"
            pool_id = "code"
            complexity = "multi_step"
            confidence = 0.82
            reason = "code_signal"
        elif signals.has_plan_signal:
            task_type = "plan"
            pool_id = "plan"
            complexity = "multi_step"
            strategy = "draft_critique"
            confidence = 0.78
            reason = "plan_signal"
        elif signals.has_creative_signal:
            task_type = "creative"
            pool_id = "creative"
            complexity = "open_ended"
            confidence = 0.72
            reason = "creative_signal"

        raw = RoutingPlan(
            task_type=task_type,
            pool_id=pool_id,
            required_context=("recent_obs",),
            meta={
                "strategy": strategy,
                "complexity": complexity,
                "confidence": confidence,
                "reason": reason,
            },
        )
        return normalize_routing_plan(raw, pool_selector_kind=self.kind)

    async def plan(self, req: AgentRequest, view: PoolSelectorInputView | None = None) -> RoutingPlan:
        """Deprecated alias: plan() -> select()."""
        return await self.select(req, view=view)


class RulePlanner(RulePoolSelector):
    """Deprecated alias: RulePlanner -> RulePoolSelector。"""
