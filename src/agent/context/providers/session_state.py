"""Session state provider."""

from __future__ import annotations

from typing import Any, Dict

from ...types import AgentRequest, RoutingPlan
from ..types import ProviderResult


class SessionStateProvider:
    name = "session_state"

    async def provide(self, req: AgentRequest, plan: RoutingPlan) -> ProviderResult:
        state = req.session_state
        value: Dict[str, Any] = {
            "session_key": state.session_key,
            "processed_total": state.processed_total,
            "error_total": state.error_total,
            "last_active_at": state.last_active_at,
            "recent_obs_count": len(state.recent_obs or []),
        }
        return ProviderResult(slot_name=self.name, value=value, status="ok")
