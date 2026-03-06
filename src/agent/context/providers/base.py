"""Context provider protocol and helpers."""

from __future__ import annotations

from typing import Protocol

from ...types import AgentRequest, RoutingPlan
from ..types import ProviderResult


class ContextProvider(Protocol):
    """Provider interface for building a context slot."""

    name: str

    async def provide(self, req: AgentRequest, plan: RoutingPlan) -> ProviderResult:
        ...
