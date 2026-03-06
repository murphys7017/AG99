"""Agent 模块错误类型。"""

from __future__ import annotations


class AgentError(Exception):
    """Agent 领域错误基类。"""


class PoolSelectorError(AgentError):
    """PoolSelector 处理失败。"""


class PlannerError(PoolSelectorError):
    """Deprecated alias: PlannerError -> PoolSelectorError。"""


class PoolError(AgentError):
    """Pool 执行失败。"""


class SpeakerError(AgentError):
    """Speaker 生成 Observation 失败。"""
