"""Current input provider."""

from __future__ import annotations

from typing import Any, Dict

from ...types import AgentRequest, RoutingPlan
from ..types import ProviderResult


class CurrentInputProvider:
    name = "current_input"

    async def provide(self, req: AgentRequest, plan: RoutingPlan) -> ProviderResult:
        payload = getattr(req.obs, "payload", None)
        text = getattr(payload, "text", None)
        text = text.strip() if isinstance(text, str) else ""
        attachments = getattr(payload, "attachments", None)

        value: Dict[str, Any] = {
            "text": text,
            "obs_id": req.obs.obs_id,
            "source_name": req.obs.source_name,
            "session_key": req.obs.session_key,
            "actor_id": req.obs.actor.actor_id if req.obs.actor else None,
            "attachments": list(attachments) if attachments else [],
        }
        return ProviderResult(slot_name=self.name, value=value, status="ok", meta={"text_len": len(text)})
