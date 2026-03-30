"""PoolSelector 协议定义。"""

from __future__ import annotations

from typing import Protocol

from ..types import AgentRequest, RoutingPlan
from .types import PoolSelectorInputView


class PoolSelector(Protocol):
    """可插拔 PoolSelector 接口。"""

    kind: str

    async def select(
        self,
        req: AgentRequest,
        view: PoolSelectorInputView | None = None,
    ) -> RoutingPlan:
        """基于输入请求生成 RoutingPlan。"""
        ...
