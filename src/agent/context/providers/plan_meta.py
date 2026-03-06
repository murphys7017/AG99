"""Plan metadata provider."""

from __future__ import annotations

from typing import Any, Dict

from ...types import AgentRequest, RoutingPlan
from ..types import ProviderResult


class PlanMetaProvider:
    name = "plan_meta"

    async def provide(self, req: AgentRequest, plan: RoutingPlan) -> ProviderResult:
        value: Dict[str, Any] = {
            "task_type": plan.task_type,
            "pool_id": plan.pool_id,
            "required_context": list(plan.required_context or ()),
            "meta": dict(plan.meta or {}),
        }
        return ProviderResult(slot_name=self.name, value=value, status="ok")
