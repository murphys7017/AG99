"""Prompt render-layer exports."""

from astrbot.core.output_contract import OutputContract
from astrbot.core.prompt.targets import PromptTarget

from .anthropic_renderer import AnthropicPromptRenderer
from .base_renderer import BasePromptRenderer
from .engine import PromptRenderEngine
from .interfaces import RenderResult, SerializedRenderValue
from .minimax_renderer import MiniMaxPromptRenderer
from .openai_renderer import OpenAIPromptRenderer
from .prompt_tree import NodeRef, PromptBuilder, PromptNode
from .request_adapter import (
    PROMPT_APPLY_RESULT_EXTRA_KEY,
    PROMPT_RENDER_RESULT_EXTRA_KEY,
    PromptApplyResult,
    ProviderRequestAdapter,
    apply_render_result_to_request,
)
from .tree_builder import PromptTreeBuilder

__all__ = [
    "BasePromptRenderer",
    "AnthropicPromptRenderer",
    "MiniMaxPromptRenderer",
    "OpenAIPromptRenderer",
    "NodeRef",
    "OutputContract",
    "PROMPT_APPLY_RESULT_EXTRA_KEY",
    "PROMPT_RENDER_RESULT_EXTRA_KEY",
    "PromptApplyResult",
    "PromptBuilder",
    "PromptNode",
    "PromptRenderEngine",
    "PromptTreeBuilder",
    "PromptTarget",
    "ProviderRequestAdapter",
    "RenderResult",
    "SerializedRenderValue",
    "apply_render_result_to_request",
]
