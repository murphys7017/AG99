"""最小聚合器：直接返回 draft。"""

from __future__ import annotations

from typing import Any, Dict

from ..types import AgentRequest, ContextPack, RoutingPlan


class DraftAggregator:
    """Phase 0 聚合器，后续可替换为评估/选择流程。"""

    async def aggregate(
        self,
        req: AgentRequest,
        plan: RoutingPlan,
        ctx: ContextPack,
        raw: Dict[str, Any],
    ) -> str:
        draft = raw.get("draft") if isinstance(raw, dict) else None
        if isinstance(draft, str) and draft.strip():
            return draft.strip()
        return "我已收到请求，当前在最小模式下先返回默认结果。"
