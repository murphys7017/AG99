"""Recent observations provider."""

from __future__ import annotations

from ...types import AgentRequest, RoutingPlan
from ..types import ProviderResult


class RecentObsProvider:
    name = "recent_obs"

    async def provide(self, req: AgentRequest, plan: RoutingPlan) -> ProviderResult:
        recent_obs = list(req.session_state.recent_obs or [])
        return ProviderResult(
            slot_name=self.name,
            value=recent_obs,
            status="ok",
            meta={"count": len(recent_obs)},
        )
