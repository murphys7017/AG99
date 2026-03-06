"""Knowledge provider stub (Phase 2)."""

from __future__ import annotations

from ...types import AgentRequest, RoutingPlan
from ..types import ProviderResult


class KnowledgeProvider:
    name = "knowledge"

    async def provide(self, req: AgentRequest, plan: RoutingPlan) -> ProviderResult:
        return ProviderResult(
            slot_name=self.name,
            value={"enabled": False, "reason": "Phase 2 knowledge not implemented"},
            status="stub",
        )
