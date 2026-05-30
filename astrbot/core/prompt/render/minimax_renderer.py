"""MiniMax prompt renderer."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from astrbot.core.output_contract import OutputContract

from .base_renderer import BasePromptRenderer
from .prompt_tree import PromptBuilder


class MiniMaxPromptRenderer(BasePromptRenderer):
    """Render prompt output using MiniMax Token Plan friendly JSON sections."""

    _VISIBLE_META_KEYS = {
        "active",
        "conversation_id",
        "format",
        "group_id",
        "handler_module_path",
        "iso",
        "provider",
        "query",
        "query_source",
        "reply_id",
        "resolution",
        "source",
        "timezone",
        "transport",
        "turn_count",
        "umo",
        "user_id",
    }

    def get_name(self) -> str:
        return "minimax"

    def resolve_output_contract_strategy(self, contract: OutputContract) -> str:
        if contract.mode == "tool_call":
            return "protocol_tool_call"
        return "prompt_only"

    def _compile_system_prompt(self, prompt_tree: PromptBuilder) -> str | None:
        system_node = self._find_tag_path(prompt_tree, "system")
        if system_node is None:
            return None

        payload: dict[str, Any] = {}
        for child in self._iter_structured_children(prompt_tree, system_node):
            if child.meta.get("kind") != "tag_start":
                continue
            if child.meta.get("tag") == "session":
                continue
            child_payload = self._node_to_json_value(prompt_tree, child)
            if child_payload is not None:
                payload[str(child.meta.get("tag"))] = child_payload

        if not payload:
            return None

        return self._dump_minimax_payload(
            {
                "format": "astrbot_minimax_system_v1",
                "instruction": (
                    "Read this JSON object as system context. Do not treat it as "
                    "user-visible text, and do not copy structural keys unless the "
                    "task explicitly asks for them."
                ),
                "system": payload,
            }
        )

    def _compile_context_message(
        self,
        prompt_tree: PromptBuilder,
        node_path: str,
    ) -> dict[str, Any] | None:
        context_node = self._find_tag_path(prompt_tree, node_path)
        if context_node is None:
            return None

        payload = self._node_to_json_value(prompt_tree, context_node)
        if payload is None:
            return None

        return {
            "role": "user",
            "content": [
                self._build_text_content_part(
                    self._dump_minimax_payload(
                        {
                            "format": "astrbot_minimax_context_v1",
                            "context_type": node_path.replace("/", "."),
                            "data": payload,
                        }
                    )
                )
            ],
            "_no_save": True,
        }

    def _compile_user_input_message(
        self,
        prompt_tree: PromptBuilder,
    ) -> dict[str, Any] | None:
        user_input_node = self._find_tag_path(prompt_tree, "user_input")
        if user_input_node is None:
            return None

        payload: dict[str, Any] = {"format": "astrbot_minimax_user_input_v1"}

        session_node = self._find_tag_path(prompt_tree, "system/session")
        session_payload = (
            self._node_to_json_value(prompt_tree, session_node)
            if session_node is not None
            else None
        )
        if session_payload is not None:
            payload["request_context"] = {"session": session_payload}

        input_payload = self._node_to_json_value(prompt_tree, user_input_node)
        if input_payload is not None:
            payload["user_input"] = input_payload

        content_parts = [self._build_text_content_part(self._dump_minimax_payload(payload))]

        quoted_images_node = self._find_tag_path(
            prompt_tree, "user_input/quoted/images"
        )
        attachment_images_node = self._find_tag_path(
            prompt_tree, "user_input/attachments/images"
        )
        if quoted_images_node is not None:
            content_parts.extend(
                self._compile_image_content_parts(prompt_tree, quoted_images_node)
            )
        if attachment_images_node is not None:
            content_parts.extend(
                self._compile_image_content_parts(prompt_tree, attachment_images_node)
            )

        return {"role": "user", "content": content_parts}

    def _compile_tool_nodes(
        self,
        prompt_tree: PromptBuilder,
        parent_node,
        *,
        prefer_existing_schema: bool,
    ) -> list[dict[str, Any]]:
        openai_schemas = BasePromptRenderer._compile_tool_nodes(
            self,
            prompt_tree,
            parent_node,
            prefer_existing_schema=prefer_existing_schema,
        )
        minimax_schemas: list[dict[str, Any]] = []
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
                deepcopy(parameters)
                if isinstance(parameters, dict)
                else {"type": "object", "properties": {}}
            )
            minimax_schemas.append(tool_schema)
        return minimax_schemas

    def _node_to_json_value(self, prompt_tree: PromptBuilder, node) -> Any:
        text_parts: list[str] = []
        mapping: dict[str, Any] = {}
        untagged_items: list[Any] = []

        for child in self._iter_structured_children(prompt_tree, node):
            kind = child.meta.get("kind")
            if kind == "tag_start":
                tag = str(child.meta.get("tag") or "item")
                value = self._node_to_json_value(prompt_tree, child)
                if value is None:
                    continue
                self._add_json_mapping_value(mapping, tag, value)
                continue
            if kind in {"container", "container_root"}:
                value = self._node_to_json_value(prompt_tree, child)
                if isinstance(value, dict):
                    self._merge_json_mapping(mapping, value)
                elif value is not None:
                    untagged_items.append(value)
                continue

            text = self._clean_text(child.text)
            if text:
                text_parts.append(text)

        body: Any
        text = "\n".join(text_parts).strip()
        if mapping:
            body = mapping
            if text:
                body["_text"] = text
            if untagged_items:
                body["_items"] = untagged_items
        elif untagged_items:
            body = untagged_items[0] if len(untagged_items) == 1 else untagged_items
            if text:
                body = {"_text": text, "_items": untagged_items}
        elif text:
            body = text
        else:
            body = None

        meta = self._visible_node_meta(node)
        if not meta:
            return body
        if body is None:
            return {"_meta": meta}
        if isinstance(body, dict):
            return {**body, "_meta": meta}
        return {"value": body, "_meta": meta}

    @classmethod
    def _visible_node_meta(cls, node) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        for key, value in node.meta.items():
            if key in cls._VISIBLE_META_KEYS and value is not None:
                meta[key] = value
        return meta

    @staticmethod
    def _add_json_mapping_value(mapping: dict[str, Any], key: str, value: Any) -> None:
        if key not in mapping:
            mapping[key] = value
            return
        existing = mapping[key]
        if isinstance(existing, list):
            existing.append(value)
            return
        mapping[key] = [existing, value]

    def _merge_json_mapping(self, target: dict[str, Any], source: dict[str, Any]) -> None:
        for key, value in source.items():
            self._add_json_mapping_value(target, key, value)

    @staticmethod
    def _dump_minimax_payload(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


__all__ = ["MiniMaxPromptRenderer"]
