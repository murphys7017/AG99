"""MiniMax prompt renderer."""

from __future__ import annotations

from .anthropic_renderer import AnthropicPromptRenderer


class MiniMaxPromptRenderer(AnthropicPromptRenderer):
    """Render prompt output using MiniMax Anthropic-compatible conventions."""

    def get_name(self) -> str:
        return "minimax"


__all__ = ["MiniMaxPromptRenderer"]
