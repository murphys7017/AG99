"""Memory provider stub (Phase 2)."""

from __future__ import annotations

from ...types import AgentRequest, RoutingPlan
from ..types import ProviderResult


class MemoryProvider:
    name = "memory"

    async def provide(self, req: AgentRequest, plan: RoutingPlan) -> ProviderResult:
        return ProviderResult(
            slot_name=self.name,
            value={"enabled": False, "reason": "Phase 2 memory not implemented"},
            status="stub",
        )
