"""Prompt render engine."""

from __future__ import annotations

import json
import logging

from astrbot.core import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextPack, ContextSlot
from .anthropic_renderer import AnthropicPromptRenderer
from .base_renderer import BasePromptRenderer
from .interfaces import RenderResult
from .minimax_renderer import MiniMaxPromptRenderer
from .prompt_tree import NodeRef, PromptBuilder
from .selector import PassthroughPromptSelector, select_context_pack


class PromptRenderEngine:
    """Drive prompt rendering from selector through tree output."""

    def __init__(
        self,
        *,
        selector=None,
        default_renderer: BasePromptRenderer | None = None,
    ) -> None:
        self.selector = selector or PassthroughPromptSelector()
        self.default_renderer = default_renderer or BasePromptRenderer()

    def render(
        self,
        pack: ContextPack,
        *,
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config=None,
        provider_request: ProviderRequest | None = None,
    ) -> RenderResult:
        selected_pack = self._select_context_pack(
            pack,
            event=event,
            plugin_context=plugin_context,
            config=config,
            provider_request=provider_request,
        )
        renderer = self._resolve_renderer(
            selected_pack,
            event=event,
            plugin_context=plugin_context,
            config=config,
            provider_request=provider_request,
        )
        prompt_tree = self._build_prompt_tree(
            selected_pack,
            renderer=renderer,
            event=event,
            plugin_context=plugin_context,
            config=config,
            provider_request=provider_request,
        )
        result = renderer.render_prompt_tree(
            prompt_tree,
            event=event,
            plugin_context=plugin_context,
            config=config,
            provider_request=provider_request,
        )
        result = self._attach_engine_metadata(
            result,
            selected_pack=selected_pack,
            renderer=renderer,
        )
        self._log_render_result(
            result,
            selected_pack=selected_pack,
            renderer=renderer,
            event=event,
            provider_request=provider_request,
        )
        return result

    def _select_context_pack(
        self,
        pack: ContextPack,
        *,
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config=None,
        provider_request: ProviderRequest | None = None,
    ) -> ContextPack:
        return select_context_pack(
            pack,
            selector=self.selector,
            event=event,
            plugin_context=plugin_context,
            config=config,
            provider_request=provider_request,
        )

    def _resolve_renderer(
        self,
        pack: ContextPack,
        *,
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config=None,
        provider_request: ProviderRequest | None = None,
    ) -> BasePromptRenderer:
        del pack
        provider = self._resolve_provider_from_request(provider_request)
        if provider is None:
            provider = self._resolve_provider_from_event(event)
        if provider is None:
            provider = self._resolve_request_provider(plugin_context, config)
        if self._is_anthropic_provider(provider):
            return AnthropicPromptRenderer()
        if self._is_minimax_provider(provider):
            return MiniMaxPromptRenderer()
        return self.default_renderer

    @staticmethod
    def _is_anthropic_provider(provider) -> bool:
        provider_config = getattr(provider, "provider_config", None)
        if not isinstance(provider_config, dict):
            return False
        return str(provider_config.get("type", "") or "") == "anthropic_chat_completion"

    @staticmethod
    def _is_minimax_provider(provider) -> bool:
        provider_config = getattr(provider, "provider_config", None)
        if not isinstance(provider_config, dict):
            return False
        return str(provider_config.get("type", "") or "") == "minimax_token_plan"

    @staticmethod
    def _resolve_provider_from_request(provider_request: ProviderRequest | None):
        if provider_request is None:
            return None
        provider = getattr(provider_request, "provider", None)
        if provider is not None:
            return provider
        provider_type = getattr(provider_request, "provider_type", None)
        if provider_type:
            return _PromptRenderProviderProxy(str(provider_type))
        return None

    @staticmethod
    def _resolve_provider_from_event(event: AstrMessageEvent | None):
        if event is None:
            return None
        provider = event.get_extra("provider")
        if provider is not None:
            return provider
        provider_type = event.get_extra("provider_type")
        if provider_type:
            return _PromptRenderProviderProxy(str(provider_type))
        return None

    @staticmethod
    def _resolve_request_provider(
        plugin_context: Context | None,
        config,
    ):
        if plugin_context is None:
            return None
        provider_id = str(getattr(config, "provider_id", "") or "").strip()
        if not provider_id:
            provider_settings = getattr(config, "provider_settings", None)
            if isinstance(provider_settings, dict):
                provider_id = str(
                    provider_settings.get("default_provider_id")
                    or provider_settings.get("provider_id")
                    or ""
                ).strip()
        if provider_id:
            get_provider_by_id = getattr(plugin_context, "get_provider_by_id", None)
            if callable(get_provider_by_id):
                provider = get_provider_by_id(provider_id)
                if provider is not None:
                    return provider
        get_using_provider = getattr(plugin_context, "get_using_provider", None)
        if callable(get_using_provider):
            try:
                return get_using_provider()
            except TypeError:
                try:
                    return get_using_provider(umo=None)
                except TypeError:
                    return None
        return None

    def _build_prompt_tree(
        self,
        pack: ContextPack,
        *,
        renderer: BasePromptRenderer,
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config=None,
        provider_request: ProviderRequest | None = None,
    ) -> PromptBuilder:
        root_tag = renderer.get_root_tag()
        prompt_tree = PromptBuilder(root_tag)
        path_refs: dict[str, NodeRef] = {root_tag: prompt_tree.ref()}
        grouped_slots = self._group_slots(pack)
        enabled_groups = [
            group
            for group in renderer.get_enabled_slot_groups()
            if group in grouped_slots
        ]
        node_structure = renderer.get_node_structure()
        rendered_slots: list[str] = []
        rendered_groups: list[str] = []

        for group in enabled_groups:
            node_path = node_structure.get(group)
            if not node_path:
                continue

            target_ref = self._ensure_node_path(
                prompt_tree,
                path_refs=path_refs,
                root_tag=root_tag,
                node_path=node_path,
            )
            slots = grouped_slots[group]
            rendered = self._render_group_context(
                renderer,
                group=group,
                target=target_ref,
                resolve_node=lambda path: self._ensure_node_path(
                    prompt_tree,
                    path_refs=path_refs,
                    root_tag=root_tag,
                    node_path=path,
                ),
                slots=slots,
                pack=pack,
                event=event,
                plugin_context=plugin_context,
                config=config,
                provider_request=provider_request,
            )
            if rendered:
                rendered_groups.append(group)
                rendered_slots.extend(rendered)

        prompt_tree._root_node.meta["rendered_slots"] = rendered_slots
        prompt_tree._root_node.meta["rendered_groups"] = rendered_groups
        prompt_tree._root_node.meta["renderer"] = renderer.get_name()
        prompt_tree._root_node.meta["enabled_slot_groups"] = list(enabled_groups)
        return prompt_tree

    def _render_group_context(
        self,
        renderer: BasePromptRenderer,
        *,
        group: str,
        target: NodeRef,
        resolve_node,
        slots: list[ContextSlot],
        pack: ContextPack,
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config=None,
        provider_request: ProviderRequest | None = None,
    ) -> list[str]:
        render_method = getattr(renderer, f"render_{group}_context")
        return render_method(
            target,
            slots,
            pack=pack,
            resolve_node=resolve_node,
            event=event,
            plugin_context=plugin_context,
            config=config,
            provider_request=provider_request,
        )

    def _ensure_node_path(
        self,
        prompt_tree: PromptBuilder,
        *,
        path_refs: dict[str, NodeRef],
        root_tag: str,
        node_path: str,
    ) -> NodeRef:
        normalized_path = node_path.strip("/")
        if not normalized_path:
            return prompt_tree.ref()

        parts = normalized_path.split("/")
        if parts[0] == root_tag:
            parts = parts[1:]

        current_path = root_tag
        current_ref = path_refs[root_tag]
        for part in parts:
            current_path = f"{current_path}/{part}"
            if current_path not in path_refs:
                path_refs[current_path] = current_ref.tag(
                    part,
                    meta={"node_path": current_path},
                )
            current_ref = path_refs[current_path]
        return current_ref

    @staticmethod
    def _group_slots(pack: ContextPack) -> dict[str, list[ContextSlot]]:
        grouped_slots: dict[str, list[ContextSlot]] = {}
        for slot in pack.slots.values():
            group = slot.name.split(".", 1)[0]
            grouped_slots.setdefault(group, []).append(slot)
        return grouped_slots

    def _attach_engine_metadata(
        self,
        result: RenderResult,
        *,
        selected_pack: ContextPack,
        renderer: BasePromptRenderer,
    ) -> RenderResult:
        result.metadata.update(
            {
                "engine": "PromptRenderEngine",
                "selector": self.selector.__class__.__name__,
                "renderer_name": renderer.get_name(),
                "slot_count": len(selected_pack.slots),
                "selected_slot_names": sorted(selected_pack.slots),
                "enabled_slot_groups": list(renderer.get_enabled_slot_groups()),
            }
        )
        return result

    def _log_render_result(
        self,
        result: RenderResult,
        *,
        selected_pack: ContextPack,
        renderer: BasePromptRenderer,
        event: AstrMessageEvent | None = None,
        provider_request: ProviderRequest | None = None,
    ) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return

        payload = {
            "umo": getattr(event, "unified_msg_origin", None) if event else None,
            "session_id": (
                getattr(provider_request, "session_id", None)
                if provider_request is not None
                else None
            ),
            "renderer": renderer.get_name(),
            "slot_count": len(selected_pack.slots),
            "selected_slot_names": sorted(selected_pack.slots),
            "system_prompt_preview": self._preview_text(result.system_prompt),
            "message_count": len(result.messages),
            "message_previews": self._preview_messages(result.messages),
            "tool_schema_count": len(result.tool_schema or []),
            "tool_names": self._extract_tool_names(result.tool_schema),
            "metadata": result.metadata,
        }
        logger.debug(
            "Prompt render result: %s",
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        )

    @staticmethod
    def _preview_text(text: object, *, limit: int = 240) -> str | None:
        if not isinstance(text, str):
            return None
        normalized = " ".join(text.split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: limit - 3]}..."

    def _preview_messages(
        self,
        messages: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        previews: list[dict[str, object]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            preview: dict[str, object] = {
                "role": message.get("role"),
            }
            if isinstance(content, str):
                preview["content_preview"] = self._preview_text(content)
            elif isinstance(content, list):
                preview["part_count"] = len(content)
                preview["content_types"] = [
                    part.get("type")
                    for part in content
                    if isinstance(part, dict) and part.get("type") is not None
                ]
                text_previews = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") != "text":
                        continue
                    text = part.get("text")
                    text_preview = self._preview_text(text, limit=160)
                    if text_preview:
                        text_previews.append(text_preview)
                if text_previews:
                    preview["text_previews"] = text_previews[:2]
            previews.append(preview)
        return previews

    @staticmethod
    def _extract_tool_names(
        tool_schema: list[dict[str, object]] | None,
    ) -> list[str]:
        names: list[str] = []
        for schema in tool_schema or []:
            if not isinstance(schema, dict):
                continue
            function_payload = schema.get("function")
            if isinstance(function_payload, dict):
                name = function_payload.get("name")
            else:
                name = schema.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        return names


class _PromptRenderProviderProxy:
    def __init__(self, provider_type: str) -> None:
        self.provider_config = {"type": provider_type}
