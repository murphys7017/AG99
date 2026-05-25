"""OpenAI-compatible prompt renderer."""

from __future__ import annotations

from astrbot.core.output_contract import OutputContract

from .base_renderer import BasePromptRenderer


class OpenAIPromptRenderer(BasePromptRenderer):
    """Render prompt output using OpenAI-compatible message conventions."""

    def get_name(self) -> str:
        return "openai"

    def resolve_output_contract_strategy(self, contract: OutputContract) -> str:
        if contract.mode == "tool_call":
            return "protocol_tool_call"
        return "prompt_only"


__all__ = ["OpenAIPromptRenderer"]
