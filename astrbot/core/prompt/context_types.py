"""
上下文类型 - 上下文数据层的数据模型（第一阶段）

本模块定义核心数据结构：
- ContextSlot: 带元数据的单个上下文信息
- ContextPack: 多个上下文槽的容器
- 用于分类的枚举类型
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


class PromptContextConflictError(RuntimeError):
    """Raised when prompt context producers disagree about one canonical fact."""

# ========== 枚举类型 ==========

CategoryType = Literal[
    "system",  # 系统
    "persona",  # 人格
    "conversation",  # 对话与群聊观察
    "memory",  # 记忆
    "input",  # 输入
    "knowledge",  # 知识数据
    "capability",  # 工具与执行能力
    "rag",  # 知识库检索
    "tools",  # 工具
    "session",  # 会话
    "runtime",  # 持续运行时状态与观察事实
    "extension",  # 插件扩展
]

LifecycleType = Literal[
    "static",  # 静态，很少变化
    "session",  # 会话级
    "rolling",  # 滚动窗口（如历史记录）
    "ephemeral",  # 一次性（如当前输入）
    "dynamic",  # 动态生成（如工具）
]

PlacementType = Literal[
    "prefix",  # 前面
    "middle",  # 中间
    "suffix",  # 后面
]

RenderModeType = Literal[
    "raw",  # 原始
    "structured",  # 结构化
    "pinned",  # 重点突出
]

LLMExposureType = Literal[
    "allowed",  # 可以显示给 LLM
    "redacted",  # 脱敏后显示
    "never",  # 绝不显示给 LLM
]

# ========== 稳定的槽名称（布局接口）==========
# 这些是映射到 LLM 消息位置的固定"布局槽"

SlotName = Literal[
    "system",  # 映射到 system message
    "persona",  # 预留人格（未来）
    "history",  # 映射到对话历史
    "user_input",  # 映射到当前用户消息
    "rag_context",  # 映射到 RAG 上下文
    "tools",  # 映射到工具 schema
]


# ========== 数据模型 ==========


@dataclass
class ContextSlot:
    """
    带元数据的单个上下文信息。

    这是系统中上下文的原子单位。
    """

    name: str  # 唯一名称（如 "persona.prompt"）
    value: Any  # 实际的上下文值
    category: CategoryType  # 语义类别
    source: str  # 来源标识（如 "persona_mgr"）

    # 元数据（未来阶段用，当前可选）
    llm_exposure: LLMExposureType = "allowed"
    placement: PlacementType = "middle"
    render_mode: RenderModeType = "raw"
    priority: int = 50

    # 扩展元数据
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextPack:
    """
    收集到的上下文容器。

    这是收集阶段的输出。
    """

    # 核心：按唯一名称索引的 slots
    slots: dict[str, ContextSlot] = field(default_factory=dict)

    # 可选：对原始 ProviderRequest 的引用（用于兼容）
    provider_request_ref: Any | None = None

    # 扩展元数据
    meta: dict[str, Any] = field(default_factory=dict)

    # ========== 便捷方法 ==========

    def add_slot(self, slot: ContextSlot) -> None:
        """添加一个 slot 到 pack。"""
        self.slots[slot.name] = slot

    def get_slot(self, name: str) -> ContextSlot | None:
        """按名称获取 slot。"""
        return self.slots.get(name)

    def has_slot(self, name: str) -> bool:
        """检查 slot 是否存在。"""
        return name in self.slots

    def list_by_category(self, category: CategoryType) -> list[ContextSlot]:
        """列出某个类别中的所有 slots。"""
        return [s for s in self.slots.values() if s.category == category]

    def list_llm_allowed(self) -> list[ContextSlot]:
        """列出所有允许显示给 LLM 的 slots。"""
        return [s for s in self.slots.values() if s.llm_exposure != "never"]
