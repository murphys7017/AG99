"""PromptEngine - 统一 Prompt 渲染引擎（Phase 1.2）。

将 ContextPack + TaskPlan + PromptProfile 渲染为 LLM messages 和可观测 manifest。
"""

from .engine import PromptEngine
from .manifest import RenderManifest, SkippedItem, TruncationRecord
from .view import ContextViewBuilder, RenderedBlock
from .layout import LayoutPolicy, LayoutResult
from .templates import TemplateRenderer
from .composer import MessageComposer
from .budget import BudgetController

__all__ = [
    "PromptEngine",
    "RenderManifest",
    "SkippedItem",
    "TruncationRecord",
    "ContextViewBuilder",
    "RenderedBlock",
    "LayoutPolicy",
    "LayoutResult",
    "TemplateRenderer",
    "MessageComposer",
    "BudgetController",
]
