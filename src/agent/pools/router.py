"""Pool 路由器（Phase 0）。"""

from __future__ import annotations

from typing import Dict

from ..types import AgentRequest, RoutingPlan
from .base import Pool
from .chat_pool import ChatPool


class AgentPoolRouter:
    """根据 plan.pool_id / task_type 选 pool，不存在时回退 chat。"""

    def __init__(self, pools: Dict[str, Pool] | None = None) -> None:
        self._chat_pool = ChatPool()
        self._pools: Dict[str, Pool] = {"chat": self._chat_pool}
        if pools:
            self._pools.update(pools)

    def pick(self, req: AgentRequest, plan: RoutingPlan) -> Pool:
        requested_id = (plan.pool_id or "").strip()
        if requested_id and requested_id in self._pools:
            return self._pools[requested_id]

        task_pool = (plan.task_type or "").strip()
        if task_pool and task_pool in self._pools:
            return self._pools[task_pool]

        return self._chat_pool

    def fallback_pool(self) -> Pool:
        return self._chat_pool
