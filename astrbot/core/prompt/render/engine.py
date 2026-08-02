"""Prompt render engine."""

from __future__ import annotations

import json
import logging
from copy import deepcopy

from astrbot.core import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.provider.register import provider_cls_map
from astrbot.core.star.context import Context

from ..context_types import ContextPack, ContextSlot
from ..targets import (
    PromptTarget,
    filter_llm_exposed_context_pack,
    project_context_pack,
)
from .anthropic_renderer import AnthropicPromptRenderer
from .base_renderer import BasePromptRenderer
from .interfaces import PromptRenderProfile, RenderResult
from .layout import DefaultPromptLayout, PromptLayoutInterface
from .minimax_renderer import MiniMaxPromptRenderer
from .openai_renderer import OpenAIPromptRenderer
from .tree_builder import PromptTreeBuilder

_PROMPT_RENDERER_FAMILIES = {"base", "openai", "anthropic", "minimax"}


class PromptRenderEngine:
    """Project a context target, build its semantic tree, and serialize it."""

    def __init__(
        self,
        *,
        default_renderer: BasePromptRenderer | None = None,
        default_layout: PromptLayoutInterface | None = None,
        tree_builder: PromptTreeBuilder | None = None,
    ) -> None:
        self.default_renderer = default_renderer or BasePromptRenderer()
        self.default_layout = default_layout or DefaultPromptLayout()
        self.tree_builder = tree_builder or PromptTreeBuilder()

    def render(
        self,
        pack: ContextPack,
        *,
        target: PromptTarget | str | None = None,
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config=None,
        provider_request: ProviderRequest | None = None,
        profile: PromptRenderProfile | None = None,
    ) -> RenderResult:
        target_pack = (
            project_context_pack(
                pack,
                target,
                history_turns=profile.history_turns if profile is not None else None,
            )
            if target is not None
            else filter_llm_exposed_context_pack(pack)
        )
        selected_pack = self._apply_render_profile(target_pack, profile)
        renderer = self._resolve_renderer(
            selected_pack,
            event=event,
            plugin_context=plugin_context,
            config=config,
            provider_request=provider_request,
        )
        prompt_tree = self.tree_builder.build(
            selected_pack,
            layout=self.default_layout,
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
        if profile is not None:
            result.request_prompt = profile.request_prompt
        result = self._attach_engine_metadata(
            result,
            selected_pack=selected_pack,
            renderer=renderer,
            layout=self.default_layout,
        )
        if target is not None:
            result.metadata["prompt_target"] = PromptTarget(target).value
        self._log_render_result(
            result,
            selected_pack=selected_pack,
            renderer=renderer,
            event=event,
            provider_request=provider_request,
        )
        return result

    @staticmethod
    def _apply_render_profile(
        pack: ContextPack,
        profile: PromptRenderProfile | None,
    ) -> ContextPack:
        if profile is None:
            return pack

        selected = ContextPack(
            slots=deepcopy(pack.slots),
            provider_request_ref=pack.provider_request_ref,
            meta=deepcopy(pack.meta),
        )
        for slot_name in profile.hidden_slot_names:
            selected.slots.pop(slot_name, None)

        if profile.system_prompt is not None:
            selected.add_slot(
                ContextSlot(
                    name="system.base",
                    value=profile.system_prompt,
                    category="system",
                    source=f"prompt_render_profile:{profile.name}",
                    render_mode="text",
                    meta={
                        "scope": "render_profile",
                        "node_type": f"{profile.name}_system_prompt",
                    },
                )
            )

        suffix = profile.input_text_suffix
        if suffix:
            input_slot = selected.get_slot("input.text")
            if input_slot is not None and isinstance(input_slot.value, str):
                input_slot.value = f"{input_slot.value.rstrip()}{suffix}"

        if profile.output_contract is not None:
            selected.meta["output_contract"] = profile.output_contract.to_dict()
        selected.meta["render_profile"] = profile.name
        selected.meta["slot_count"] = len(selected.slots)
        return selected

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
        renderer_family = self._resolve_prompt_renderer_family(provider)
        if renderer_family == "anthropic":
            return AnthropicPromptRenderer()
        if renderer_family == "openai":
            return OpenAIPromptRenderer()
        if renderer_family == "minimax":
            return MiniMaxPromptRenderer(
                enable_tool_call=self._resolve_minimax_tool_call_enabled(provider)
            )
        return self.default_renderer

    @staticmethod
    def _resolve_prompt_renderer_family(provider) -> str:
        provider_config = getattr(provider, "provider_config", None)
        if not isinstance(provider_config, dict):
            return "base"
        configured_family = str(
            provider_config.get("prompt_renderer_family", "") or ""
        ).strip()
        if configured_family:
            return (
                configured_family
                if configured_family in _PROMPT_RENDERER_FAMILIES
                else "base"
            )
        provider_type = str(provider_config.get("type", "") or "").strip()
        if not provider_type:
            return "base"
        metadata = provider_cls_map.get(provider_type)
        if metadata is None:
            return "base"
        metadata_family = str(
            getattr(metadata, "prompt_renderer_family", "base") or "base"
        )
        return metadata_family if metadata_family in _PROMPT_RENDERER_FAMILIES else "base"

    @staticmethod
    def _resolve_minimax_tool_call_enabled(provider) -> bool:
        provider_config = getattr(provider, "provider_config", None)
        if not isinstance(provider_config, dict):
            return False
        value = provider_config.get("minimax_enable_tool_call", True)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return False

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

    def _attach_engine_metadata(
        self,
        result: RenderResult,
        *,
        selected_pack: ContextPack,
        renderer: BasePromptRenderer,
        layout: PromptLayoutInterface,
    ) -> RenderResult:
        result.metadata.update(
            {
                "engine": "PromptRenderEngine",
                "renderer_name": renderer.get_name(),
                "layout_name": layout.get_name(),
                "slot_count": len(selected_pack.slots),
                "selected_slot_names": sorted(selected_pack.slots),
                "enabled_slot_groups": list(layout.get_enabled_slot_groups()),
            }
        )
        render_profile = selected_pack.meta.get("render_profile")
        if isinstance(render_profile, str) and render_profile:
            result.metadata["render_profile"] = render_profile
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
            "request_prompt_preview": self._preview_text(result.request_prompt),
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
        metadata = provider_cls_map.get(provider_type)
        renderer_family = (
            getattr(metadata, "prompt_renderer_family", "base")
            if metadata is not None
            else "base"
        )
        self.provider_config = {
            "type": provider_type,
            "prompt_renderer_family": renderer_family,
        }
