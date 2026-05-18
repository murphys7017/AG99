"""Anthropic prompt renderer."""

from __future__ import annotations

import base64
from typing import Any

from .base_renderer import BasePromptRenderer
from .prompt_tree import PromptBuilder


class AnthropicPromptRenderer(BasePromptRenderer):
    """Render prompt output using Anthropic message content-block conventions."""

    def get_name(self) -> str:
        return "anthropic"

    def _compile_context_message(
        self,
        prompt_tree: PromptBuilder,
        node_path: str,
    ) -> dict[str, Any] | None:
        message = super()._compile_context_message(prompt_tree, node_path)
        if message is None:
            return None
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = [self._build_text_content_part(content)]
        return message

    def _compile_turn_messages(
        self,
        prompt_tree: PromptBuilder,
        parent_node,
    ) -> list[dict[str, Any]]:
        messages = super()._compile_turn_messages(prompt_tree, parent_node)
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = [self._build_text_content_part(content)]
        return messages

    def _compile_tool_nodes(
        self,
        prompt_tree: PromptBuilder,
        parent_node,
        *,
        prefer_existing_schema: bool,
    ) -> list[dict[str, Any]]:
        openai_schemas = super()._compile_tool_nodes(
            prompt_tree,
            parent_node,
            prefer_existing_schema=prefer_existing_schema,
        )
        anthropic_schemas: list[dict[str, Any]] = []
        for schema in openai_schemas:
            function = schema.get("function") if isinstance(schema, dict) else None
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if not isinstance(name, str) or not name:
                continue
            tool_schema: dict[str, Any] = {"name": name}
            description = function.get("description")
            if isinstance(description, str) and description:
                tool_schema["description"] = description
            parameters = function.get("parameters")
            tool_schema["input_schema"] = (
                parameters
                if isinstance(parameters, dict)
                else {"type": "object", "properties": {}}
            )
            anthropic_schemas.append(tool_schema)
        return anthropic_schemas

    def _compile_image_content_parts(
        self,
        prompt_tree: PromptBuilder,
        parent_node,
    ) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for image_node in self._iter_descendant_tags(
            prompt_tree, parent_node, tag="image"
        ):
            ref = self._render_subtree_text(prompt_tree, image_node, include_root=False)
            if not ref:
                continue
            parts.append({"type": "image", "source": self._build_image_source(ref)})
        return parts

    def _build_image_source(self, ref: str) -> dict[str, Any]:
        if ref.startswith("data:") and ";base64," in ref:
            header, data = ref.split(",", 1)
            media_type = header.removeprefix("data:").split(";", 1)[0]
            return {
                "type": "base64",
                "media_type": media_type or "image/jpeg",
                "data": data,
            }
        if ref.startswith("base64://"):
            raw_base64 = ref.removeprefix("base64://")
            return {
                "type": "base64",
                "media_type": self._detect_image_mime_type_from_base64(raw_base64),
                "data": raw_base64,
            }
        return {"type": "url", "url": ref}

    @staticmethod
    def _detect_image_mime_type_from_base64(data: str) -> str:
        try:
            raw = base64.b64decode(data, validate=False)
        except Exception:
            return "image/jpeg"
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if raw[:2] == b"\xff\xd8":
            return "image/jpeg"
        if raw[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            return "image/webp"
        return "image/jpeg"


__all__ = ["AnthropicPromptRenderer"]
