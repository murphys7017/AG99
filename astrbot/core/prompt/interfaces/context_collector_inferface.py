"""
上下文收集器接口 - Context Data Layer (Phase 1)

本模块定义 ContextCollector 抽象基类。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextSlot

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class ContextCollectorInterface(ABC):
    """
    上下文收集器抽象基类。

    每个具体的 Collector 负责收集一类上下文信息（如 Persona、Input、Memory 等）。
    """

    @property
    def lifecycle(self) -> str:
        """Return collector lifecycle for per-event prompt build reuse.

        `static` collectors are stable within one event and may be cached after
        a successful collection. `dynamic` collectors are evaluated on every
        prompt build.
        """
        return "dynamic"

    @abstractmethod
    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        """
        收集上下文信息。

        Args:
            event: 当前消息事件
            plugin_context: 插件上下文（用于访问 persona_manager、conversation_manager 等）
            config: 主 Agent 构建配置
            provider_request: 当前链路中的 ProviderRequest（如果已构建）

        Returns:
            收集到的 ContextSlot 列表
        """
        pass
