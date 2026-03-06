"""Pool 与聚合协议定义。"""

from __future__ import annotations

from typing import Any, Dict, Protocol

from ..types import AgentRequest, ContextPack, RoutingPlan


class Pool(Protocol):
    """可执行 Pool 接口。"""

    pool_id: str
    name: str

    async def run(self, req: AgentRequest, plan: RoutingPlan, ctx: ContextPack) -> Dict[str, Any]:
        ...


class PoolRouter(Protocol):
    """Pool 路由接口。"""

    def pick(self, req: AgentRequest, plan: RoutingPlan) -> Pool:
        ...

    def fallback_pool(self) -> Pool:
        ...


class Aggregator(Protocol):
    """原始 pool 结果聚合接口。"""

    async def aggregate(
        self,
        req: AgentRequest,
        plan: RoutingPlan,
        ctx: ContextPack,
        raw: Dict[str, Any],
    ) -> str:
        ...
