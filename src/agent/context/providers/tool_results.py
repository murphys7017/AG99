"""Tool results provider stub (Phase 2)."""

from __future__ import annotations

from ...types import AgentRequest, RoutingPlan
from ..types import ProviderResult


class ToolResultsProvider:
    name = "tool_results"

    async def provide(self, req: AgentRequest, plan: RoutingPlan) -> ProviderResult:
        return ProviderResult(
            slot_name=self.name,
            value={
                "enabled": False,
                "reason": "Phase 2 tool results not implemented",
                "note": "Future: keep raw_result and llm_view separated",
            },
            status="stub",
        )
