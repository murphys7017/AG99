"""
Input context collector for prompt context packing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from astrbot.core import logger
from astrbot.core.message.components import File, Image, Record, Reply, Video
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context
from astrbot.core.utils.file_extract import extract_file_moonshotai
from astrbot.core.utils.media_utils import (
    IMAGE_COMPRESS_DEFAULT_MAX_SIZE,
    IMAGE_COMPRESS_DEFAULT_QUALITY,
    compress_image,
)
from astrbot.core.utils.quoted_message.settings import (
    SETTINGS as DEFAULT_QUOTED_MESSAGE_SETTINGS,
)
from astrbot.core.utils.quoted_message_parser import (
    extract_quoted_message_images,
    extract_quoted_message_text,
)
from astrbot.core.utils.string_utils import normalize_and_dedupe_strings

from ..context_types import ContextSlot
from ..input_annotations import (
    INPUT_ITEM_ANNOTATIONS_EXTRA_KEY,
    INPUT_QUOTED_TEXT_ANNOTATION_KEY,
    INPUT_TEXT_ANNOTATION_KEY,
    build_message_annotation_key,
    build_reply_chain_annotation_key,
    copy_input_annotation_fields,
    normalize_input_annotations,
)
from ..interfaces.context_collector_inferface import ContextCollectorInterface
from ..runtime_cache import (
    get_cached_file_extract,
    get_cached_image_caption,
    set_cached_file_extract,
    set_cached_image_caption,
)

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig
    from astrbot.core.utils.quoted_message.settings import QuotedMessageParserSettings


@dataclass
class _ReplyPayload:
    texts: list[str] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    fallback_image_count: int = 0


class InputCollector(ContextCollectorInterface):
    """Collect the current user input into prompt context slots."""

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        slots: list[ContextSlot] = []

        try:
            input_annotations = self._load_input_annotations(event)
            provider_settings = self._resolve_provider_settings(
                event=event,
                plugin_context=plugin_context,
                config=config,
            )

            effective_text, text_source, text_meta = self._resolve_effective_text(
                event=event,
                config=config,
                provider_request=provider_request,
                provider_settings=provider_settings,
                annotation=input_annotations.get(INPUT_TEXT_ANNOTATION_KEY),
            )
            if effective_text:
                slots.append(
                    ContextSlot(
                        name="input.text",
                        value=effective_text,
                        category="input",
                        source="event_input",
                        meta={"source_field": text_source, **text_meta},
                    )
                )

            current_images = await self._collect_current_images(
                event,
                provider_request=provider_request,
                annotations=input_annotations,
            )
            if current_images:
                slots.append(
                    ContextSlot(
                        name="input.images",
                        value=current_images,
                        category="input",
                        source="event_input",
                        meta={"count": len(current_images), "source": "current"},
                    )
                )

            media_content_parts = await self._collect_media_content_parts(event)
            if media_content_parts:
                slots.append(
                    ContextSlot(
                        name="input.media_content_parts",
                        value=media_content_parts,
                        category="input",
                        source="event_input",
                        render_mode="structured",
                        meta={"part_count": len(media_content_parts)},
                    )
                )

            current_files = self._collect_files_from_components(
                event.message_obj.message,
                source="current",
                annotations=input_annotations,
                annotation_key_builder=build_message_annotation_key,
            )
            reply_payload = await self._collect_reply_payloads(
                event,
                config,
                annotations=input_annotations,
            )

            current_image_captions = await self._collect_current_image_captions(
                event=event,
                plugin_context=plugin_context,
                provider_request=provider_request,
                provider_settings=provider_settings,
                current_images=current_images,
            )
            if current_image_captions:
                slots.append(
                    ContextSlot(
                        name="input.image_captions",
                        value=current_image_captions,
                        category="input",
                        source="image_caption_provider",
                        meta={"count": len(current_image_captions)},
                    )
                )

            if reply_payload.texts:
                slots.append(
                    ContextSlot(
                        name="input.quoted_text",
                        value="\n\n".join(reply_payload.texts),
                        category="input",
                        source="quoted_message",
                        meta={
                            "reply_count": len(reply_payload.texts),
                            **copy_input_annotation_fields(
                                input_annotations.get(INPUT_QUOTED_TEXT_ANNOTATION_KEY)
                            ),
                        },
                    )
                )

            if reply_payload.images:
                slots.append(
                    ContextSlot(
                        name="input.quoted_images",
                        value=reply_payload.images,
                        category="input",
                        source="quoted_message",
                        meta={
                            "count": len(reply_payload.images),
                            "fallback_count": reply_payload.fallback_image_count,
                        },
                    )
                )

            quoted_image_captions = await self._collect_quoted_image_captions(
                event=event,
                plugin_context=plugin_context,
                provider_request=provider_request,
                provider_settings=provider_settings,
                quoted_images=reply_payload.images,
            )
            if quoted_image_captions:
                slots.append(
                    ContextSlot(
                        name="input.quoted_image_captions",
                        value=quoted_image_captions,
                        category="input",
                        source="image_caption_provider",
                        meta={"count": len(quoted_image_captions)},
                    )
                )

            all_files = [*current_files, *reply_payload.files]
            if all_files:
                slots.append(
                    ContextSlot(
                        name="input.files",
                        value=all_files,
                        category="input",
                        source="event_input",
                        meta={"count": len(all_files)},
                    )
                )

            file_extracts = await self._collect_file_extracts(event, config)
            if file_extracts:
                slots.append(
                    ContextSlot(
                        name="input.file_extracts",
                        value=file_extracts,
                        category="input",
                        source="file_extract_provider",
                        meta={"count": len(file_extracts)},
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to collect input context: %s", exc, exc_info=True)

        return slots

    def _load_input_annotations(
        self,
        event: AstrMessageEvent,
    ) -> dict[str, dict[str, str]]:
        try:
            raw_annotations = event.get_extra(INPUT_ITEM_ANNOTATIONS_EXTRA_KEY)
        except Exception:  # noqa: BLE001
            return {}
        return normalize_input_annotations(raw_annotations)

    def _resolve_provider_settings(
        self,
        *,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
    ) -> dict[str, Any]:
        if isinstance(config.provider_settings, dict) and config.provider_settings:
            return config.provider_settings

        try:
            cfg = plugin_context.get_config(umo=event.unified_msg_origin)
        except TypeError:
            cfg = plugin_context.get_config()
        except Exception:  # noqa: BLE001
            return {}

        if isinstance(cfg, dict):
            provider_settings = cfg.get("provider_settings")
            if isinstance(provider_settings, dict):
                return provider_settings
        return {}

    def _resolve_effective_text(
        self,
        *,
        event: AstrMessageEvent,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None,
        provider_settings: dict[str, Any],
        annotation: dict[str, str] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        if provider_request and isinstance(provider_request.prompt, str):
            raw_text = provider_request.prompt
            source_field = "provider_request.prompt"
        else:
            message_text = event.message_str or ""
            wake_prefix = config.provider_wake_prefix or ""
            if wake_prefix and message_text.startswith(wake_prefix):
                raw_text = message_text[len(wake_prefix) :]
            else:
                raw_text = message_text
            source_field = "event.message_str"

        prompt_prefix = provider_settings.get("prompt_prefix")
        effective_text = self._apply_prompt_prefix(raw_text, prompt_prefix)
        meta = {
            "raw_text": raw_text,
            "prompt_prefix": prompt_prefix if isinstance(prompt_prefix, str) else None,
            "prefix_applied": bool(prompt_prefix),
        }
        meta.update(copy_input_annotation_fields(annotation))
        return effective_text, source_field, meta

    def _apply_prompt_prefix(self, text: str, prompt_prefix: object) -> str:
        if not isinstance(prompt_prefix, str) or not prompt_prefix:
            return text
        if "{{prompt}}" in prompt_prefix:
            return prompt_prefix.replace("{{prompt}}", text)
        return f"{prompt_prefix}{text}"

    async def _collect_current_images(
        self,
        event: AstrMessageEvent,
        *,
        provider_request: ProviderRequest | None = None,
        annotations: dict[str, dict[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        images: list[dict[str, Any]] = []
        seen_refs: set[str] = set()

        for index, comp in enumerate(event.message_obj.message):
            if not isinstance(comp, Image):
                continue
            image_record = await self._build_image_record(
                comp,
                source="current",
                annotation=self._resolve_annotation(
                    annotations,
                    build_message_annotation_key(index),
                ),
            )
            if not image_record:
                continue
            ref = image_record["ref"]
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            images.append(image_record)

        if provider_request is not None:
            for image_ref in normalize_and_dedupe_strings(
                provider_request.image_urls or []
            ):
                if image_ref in seen_refs:
                    continue
                seen_refs.add(image_ref)
                images.append(
                    self._build_image_record_from_ref(
                        image_ref,
                        source="provider_request",
                        resolution="explicit",
                    )
                )

        return images

    async def _collect_media_content_parts(
        self,
        event: AstrMessageEvent,
    ) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []

        async def collect_component(component: object, *, quoted: bool) -> None:
            if isinstance(component, Record):
                try:
                    audio_path = await component.convert_to_file_path()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to resolve audio attachment: %s", exc)
                    return
                parts.append(
                    {"type": "audio_url", "audio_url": {"url": audio_path}}
                )
            elif isinstance(component, Video):
                try:
                    video_path = await component.convert_to_file_path()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to resolve video attachment: %s", exc)
                    return
                label = (
                    "Video Attachment in quoted message"
                    if quoted
                    else "Video Attachment"
                )
                parts.append(
                    {
                        "type": "text",
                        "text": (
                            f"[{label}: name {Path(video_path).name}, "
                            f"path {video_path}]"
                        ),
                    }
                )

        for component in event.message_obj.message:
            await collect_component(component, quoted=False)
            if isinstance(component, Reply) and component.chain:
                for quoted_component in component.chain:
                    await collect_component(quoted_component, quoted=True)
        return parts

    def _collect_files_from_components(
        self,
        components: list[object],
        *,
        source: str,
        reply_id: str | int | None = None,
        annotations: dict[str, dict[str, str]] | None = None,
        annotation_key_builder: Any | None = None,
    ) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []

        for index, comp in enumerate(components):
            if not isinstance(comp, File):
                continue
            annotation_key = None
            if callable(annotation_key_builder):
                annotation_key = annotation_key_builder(index)
            file_record = self._build_file_record(
                comp,
                source=source,
                reply_id=reply_id,
                annotation=self._resolve_annotation(annotations, annotation_key),
            )
            if file_record is not None:
                files.append(file_record)

        return files

    async def _collect_reply_payloads(
        self,
        event: AstrMessageEvent,
        config: MainAgentBuildConfig,
        *,
        annotations: dict[str, dict[str, str]] | None = None,
    ) -> _ReplyPayload:
        payload = _ReplyPayload()
        reply_components = [
            (index, comp)
            for index, comp in enumerate(event.message_obj.message)
            if isinstance(comp, Reply)
        ]
        if not reply_components:
            return payload

        settings = self._get_quoted_message_parser_settings(config)
        seen_texts: set[str] = set()
        seen_image_refs: set[str] = set()

        for message_index, reply_component in reply_components:
            reply_id = getattr(reply_component, "id", None)

            try:
                quoted_text = await extract_quoted_message_text(
                    event,
                    reply_component,
                    settings=settings,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to collect quoted text: umo=%s reply_id=%s error=%s",
                    event.unified_msg_origin,
                    reply_id,
                    exc,
                    exc_info=True,
                )
                quoted_text = None

            if quoted_text:
                normalized_text = quoted_text.strip()
                if normalized_text and normalized_text not in seen_texts:
                    seen_texts.add(normalized_text)
                    payload.texts.append(normalized_text)

            has_embedded_image = False
            if reply_component.chain:
                for chain_index, reply_item in enumerate(reply_component.chain):
                    annotation = self._resolve_annotation(
                        annotations,
                        build_reply_chain_annotation_key(message_index, chain_index),
                    )
                    if isinstance(reply_item, Image):
                        has_embedded_image = True
                        image_record = await self._build_image_record(
                            reply_item,
                            source="quoted",
                            resolution="embedded",
                            reply_id=reply_id,
                            annotation=annotation,
                        )
                        if not image_record:
                            continue
                        ref = image_record["ref"]
                        if ref in seen_image_refs:
                            continue
                        seen_image_refs.add(ref)
                        payload.images.append(image_record)

                payload.files.extend(
                    self._collect_files_from_components(
                        reply_component.chain,
                        source="quoted",
                        reply_id=reply_id,
                        annotations=annotations,
                        annotation_key_builder=lambda chain_index, *, message_index=message_index: (
                            build_reply_chain_annotation_key(message_index, chain_index)
                        ),
                    )
                )

            if has_embedded_image:
                continue

            try:
                fallback_refs = normalize_and_dedupe_strings(
                    await extract_quoted_message_images(
                        event,
                        reply_component,
                        settings=settings,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to collect quoted images: umo=%s reply_id=%s error=%s",
                    event.unified_msg_origin,
                    reply_id,
                    exc,
                    exc_info=True,
                )
                continue

            remaining_limit = max(
                config.max_quoted_fallback_images - payload.fallback_image_count,
                0,
            )
            if remaining_limit <= 0:
                if fallback_refs:
                    logger.warning(
                        "Skip quoted fallback images due to limit=%d for umo=%s",
                        config.max_quoted_fallback_images,
                        event.unified_msg_origin,
                    )
                continue

            if len(fallback_refs) > remaining_limit:
                logger.warning(
                    "Truncate quoted fallback images for umo=%s reply_id=%s from %d to %d",
                    event.unified_msg_origin,
                    reply_id,
                    len(fallback_refs),
                    remaining_limit,
                )
                fallback_refs = fallback_refs[:remaining_limit]

            for image_ref in fallback_refs:
                if image_ref in seen_image_refs:
                    continue
                seen_image_refs.add(image_ref)
                payload.images.append(
                    self._build_image_record_from_ref(
                        image_ref,
                        source="quoted",
                        resolution="fallback",
                        reply_id=reply_id,
                    )
                )
                payload.fallback_image_count += 1

        return payload

    async def _build_image_record(
        self,
        image_component: Image,
        *,
        source: str,
        resolution: str | None = None,
        reply_id: str | int | None = None,
        annotation: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        image_ref = (getattr(image_component, "url", "") or "").strip()
        transport = "url"
        if not image_ref:
            image_ref = (getattr(image_component, "file", "") or "").strip()
            if image_ref.startswith("http://") or image_ref.startswith("https://"):
                transport = "url"
            elif image_ref.startswith("base64://"):
                transport = "base64"
            elif image_ref.startswith("file://"):
                transport = "file"
            elif image_ref:
                transport = "file"
        if not image_ref:
            image_ref = (getattr(image_component, "path", "") or "").strip()
            if image_ref:
                transport = "path"
        if not image_ref:
            try:
                image_ref = await image_component.convert_to_file_path()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to resolve image component: %s", exc, exc_info=True
                )
                return None
            transport = "resolved_path"

        return self._build_image_record_from_ref(
            image_ref,
            source=source,
            resolution=resolution,
            reply_id=reply_id,
            transport=transport,
            annotation=annotation,
        )

    def _build_image_record_from_ref(
        self,
        image_ref: str,
        *,
        source: str,
        resolution: str | None = None,
        reply_id: str | int | None = None,
        transport: str | None = None,
        annotation: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "ref": image_ref,
            "transport": transport or self._infer_transport(image_ref),
            "source": source,
        }
        if resolution is not None:
            record["resolution"] = resolution
        if reply_id is not None:
            record["reply_id"] = reply_id
        record.update(copy_input_annotation_fields(annotation))
        return record

    def _build_file_record(
        self,
        file_component: File,
        *,
        source: str,
        reply_id: str | int | None = None,
        annotation: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        file_path = getattr(file_component, "file_", "") or ""
        file_url = getattr(file_component, "url", "") or ""
        file_name = getattr(file_component, "name", "") or ""

        if not file_name and not file_path and not file_url:
            return None

        record: dict[str, Any] = {
            "name": file_name,
            "file": file_path,
            "url": file_url,
            "source": source,
            "reply_id": reply_id,
        }
        record.update(copy_input_annotation_fields(annotation))
        return record

    def _get_quoted_message_parser_settings(
        self,
        config: MainAgentBuildConfig,
    ) -> QuotedMessageParserSettings:
        provider_settings = config.provider_settings
        if not isinstance(provider_settings, dict):
            return DEFAULT_QUOTED_MESSAGE_SETTINGS

        overrides = provider_settings.get("quoted_message_parser")
        if not isinstance(overrides, dict):
            return DEFAULT_QUOTED_MESSAGE_SETTINGS

        return DEFAULT_QUOTED_MESSAGE_SETTINGS.with_overrides(overrides)

    async def _collect_current_image_captions(
        self,
        *,
        event: AstrMessageEvent,
        plugin_context: Context,
        provider_request: ProviderRequest | None,
        provider_settings: dict[str, Any],
        current_images: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        provider_id = provider_settings.get("default_image_caption_provider_id")
        if not current_images or not isinstance(provider_id, str) or not provider_id:
            return []
        if (
            provider_request is None
            or getattr(provider_request, "conversation", None) is None
        ):
            return []
        if self._request_provider_supports_modality(provider_request, "image"):
            logger.debug(
                "Skip current image caption collection because the main provider supports image input."
            )
            return []

        provider = self._resolve_provider_by_id(plugin_context, provider_id)
        if provider is None:
            logger.warning(
                "Skip current image caption collection because provider `%s` is unavailable",
                provider_id,
            )
            return []

        records: list[dict[str, Any]] = []
        for image in current_images:
            caption = await self._request_image_caption(
                event=event,
                provider=provider,
                provider_id=provider_id,
                image_ref=str(image.get("ref", "")),
                provider_settings=provider_settings,
            )
            if not caption:
                continue
            records.append(
                {
                    "ref": image.get("ref"),
                    "caption": caption,
                    "provider_id": provider_id,
                    "source": "current",
                    **self._extract_annotation_fields_from_record(image),
                }
            )
        return records

    async def _collect_quoted_image_captions(
        self,
        *,
        event: AstrMessageEvent,
        plugin_context: Context,
        provider_request: ProviderRequest | None,
        provider_settings: dict[str, Any],
        quoted_images: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not quoted_images:
            return []
        if self._request_provider_supports_modality(provider_request, "image"):
            logger.debug(
                "Skip quoted image caption collection because the main provider supports image input."
            )
            return []

        provider_id = provider_settings.get("default_image_caption_provider_id")
        if not isinstance(provider_id, str) or not provider_id:
            logger.debug(
                "No dedicated image caption provider configured. "
                "Skipping quoted image caption collection."
            )
            return []
        provider = self._resolve_provider_by_id(plugin_context, provider_id)
        resolved_provider_id = provider_id
        if provider is None:
            logger.warning(
                "Skip quoted image caption collection because provider `%s` is unavailable",
                provider_id,
            )
            return []

        records: list[dict[str, Any]] = []
        for image in quoted_images:
            caption = await self._request_image_caption(
                event=event,
                provider=provider,
                provider_id=resolved_provider_id,
                image_ref=str(image.get("ref", "")),
                provider_settings=provider_settings,
                prompt_override="Please describe the image content.",
            )
            if not caption:
                continue
            record = {
                "ref": image.get("ref"),
                "caption": caption,
                "provider_id": resolved_provider_id,
                "source": "quoted",
                **self._extract_annotation_fields_from_record(image),
            }
            if image.get("reply_id") is not None:
                record["reply_id"] = image.get("reply_id")
            records.append(record)
        return records

    def _resolve_provider_by_id(
        self,
        plugin_context: Context,
        provider_id: str | None,
    ) -> Any | None:
        if not provider_id:
            return None
        try:
            provider = plugin_context.get_provider_by_id(provider_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to resolve image caption provider `%s`: %s",
                provider_id,
                exc,
                exc_info=True,
            )
            return None
        return provider if callable(getattr(provider, "text_chat", None)) else None

    def _resolve_current_provider(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
    ) -> Any | None:
        try:
            provider = plugin_context.get_using_provider(event.unified_msg_origin)
        except TypeError:
            provider = plugin_context.get_using_provider(umo=event.unified_msg_origin)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to resolve current provider for image captioning: %s",
                exc,
                exc_info=True,
            )
            return None
        return provider if callable(getattr(provider, "text_chat", None)) else None

    def _resolve_provider_config_id(self, provider: Any) -> str | None:
        provider_config = getattr(provider, "provider_config", None)
        if not isinstance(provider_config, dict):
            return None
        provider_id = provider_config.get("id")
        return provider_id if isinstance(provider_id, str) and provider_id else None

    def _request_provider_supports_modality(
        self,
        provider_request: ProviderRequest | None,
        modality: str,
    ) -> bool:
        if provider_request is None:
            return False
        provider = getattr(provider_request, "provider", None)
        provider_config = getattr(provider, "provider_config", None)
        if not isinstance(provider_config, dict):
            return False
        modalities = provider_config.get("modalities")
        return isinstance(modalities, list) and modality in modalities

    async def _request_image_caption(
        self,
        *,
        event: AstrMessageEvent,
        provider: Any,
        provider_id: str | None,
        image_ref: str,
        provider_settings: dict[str, Any],
        prompt_override: str | None = None,
    ) -> str | None:
        if not image_ref:
            return None

        prompt = prompt_override or provider_settings.get(
            "image_caption_prompt",
            "Please describe the image.",
        )
        cache_hit, cached_caption = get_cached_image_caption(
            event,
            provider_id=provider_id,
            prompt=prompt,
            image_refs=[image_ref],
        )
        if cache_hit:
            return cached_caption

        prepared_ref = await self._prepare_image_ref_for_caption(
            event=event,
            image_ref=image_ref,
            provider_settings=provider_settings,
        )

        try:
            response = await provider.text_chat(
                prompt=prompt,
                image_urls=[prepared_ref],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to request image caption: provider=%s ref=%s error=%s",
                provider_id,
                image_ref,
                exc,
                exc_info=True,
            )
            return None

        caption = getattr(response, "completion_text", None)
        if not isinstance(caption, str):
            return None
        caption = caption.strip()
        caption = caption or None
        set_cached_image_caption(
            event,
            provider_id=provider_id,
            prompt=prompt,
            image_refs=[image_ref],
            result=caption,
        )
        return caption

    async def _prepare_image_ref_for_caption(
        self,
        *,
        event: AstrMessageEvent,
        image_ref: str,
        provider_settings: dict[str, Any],
    ) -> str:
        try:
            compressed_ref = await self._compress_image_for_provider(
                image_ref,
                provider_settings,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to prepare image ref for captioning: ref=%s error=%s",
                image_ref,
                exc,
                exc_info=True,
            )
            return image_ref

        if self._is_generated_compressed_image_path(image_ref, compressed_ref):
            try:
                event.track_temporary_local_file(compressed_ref)
            except Exception:
                pass
        return compressed_ref

    def _get_image_compress_args(
        self,
        provider_settings: dict[str, Any],
    ) -> tuple[bool, int, int]:
        enabled = provider_settings.get("image_compress_enabled", True)
        if not isinstance(enabled, bool):
            enabled = True

        raw_options = provider_settings.get("image_compress_options", {})
        options = raw_options if isinstance(raw_options, dict) else {}

        max_size = options.get("max_size", IMAGE_COMPRESS_DEFAULT_MAX_SIZE)
        if not isinstance(max_size, int):
            max_size = IMAGE_COMPRESS_DEFAULT_MAX_SIZE
        max_size = max(max_size, 1)

        quality = options.get("quality", IMAGE_COMPRESS_DEFAULT_QUALITY)
        if not isinstance(quality, int):
            quality = IMAGE_COMPRESS_DEFAULT_QUALITY
        quality = min(max(quality, 1), 100)

        return enabled, max_size, quality

    async def _compress_image_for_provider(
        self,
        image_ref: str,
        provider_settings: dict[str, Any],
    ) -> str:
        enabled, max_size, quality = self._get_image_compress_args(provider_settings)
        if not enabled:
            return image_ref
        return await compress_image(image_ref, max_size=max_size, quality=quality)

    def _is_generated_compressed_image_path(
        self,
        original_path: str,
        compressed_path: str | None,
    ) -> bool:
        if not compressed_path or compressed_path == original_path:
            return False
        if compressed_path.startswith("http") or compressed_path.startswith(
            "data:image"
        ):
            return False
        return Path(compressed_path).exists()

    async def _collect_file_extracts(
        self,
        event: AstrMessageEvent,
        config: MainAgentBuildConfig,
    ) -> list[dict[str, Any]]:
        if not config.file_extract_enabled:
            return []
        if config.file_extract_prov != "moonshotai":
            logger.warning(
                "Skip file extract collection because provider `%s` is unsupported",
                config.file_extract_prov,
            )
            return []
        if not config.file_extract_msh_api_key:
            logger.warning(
                "Skip file extract collection because Moonshot API key is missing"
            )
            return []

        file_components = self._collect_file_components(event)
        if not file_components:
            return []

        tasks = [
            self._extract_single_file_record(
                event=event,
                file_component=file_component,
                source=source,
                reply_id=reply_id,
                annotation=annotation,
                provider=config.file_extract_prov,
                api_key=config.file_extract_msh_api_key,
            )
            for file_component, source, reply_id, annotation in file_components
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        extracts: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning(
                    "Failed to collect file extract: %s",
                    result,
                    exc_info=True,
                )
                continue
            if result is not None:
                extracts.append(result)
        return extracts

    def _collect_file_components(
        self,
        event: AstrMessageEvent,
    ) -> list[tuple[File, str, str | int | None, dict[str, str]]]:
        annotations = self._load_input_annotations(event)
        components: list[tuple[File, str, str | int | None, dict[str, str]]] = []

        for message_index, comp in enumerate(event.message_obj.message):
            if isinstance(comp, File):
                components.append(
                    (
                        comp,
                        "current",
                        None,
                        self._resolve_annotation(
                            annotations,
                            build_message_annotation_key(message_index),
                        ),
                    )
                )
            elif isinstance(comp, Reply) and comp.chain:
                reply_id = getattr(comp, "id", None)
                for chain_index, reply_comp in enumerate(comp.chain):
                    if isinstance(reply_comp, File):
                        components.append(
                            (
                                reply_comp,
                                "quoted",
                                reply_id,
                                self._resolve_annotation(
                                    annotations,
                                    build_reply_chain_annotation_key(
                                        message_index,
                                        chain_index,
                                    ),
                                ),
                            )
                        )

        return components

    async def _extract_single_file_record(
        self,
        *,
        event: AstrMessageEvent,
        file_component: File,
        source: str,
        reply_id: str | int | None,
        annotation: dict[str, str],
        provider: str,
        api_key: str,
    ) -> dict[str, Any] | None:
        file_path = await file_component.get_file()
        if not file_path:
            return None

        cache_hit, cached_content = get_cached_file_extract(
            event,
            provider=provider,
            file_path=file_path,
        )
        if cache_hit:
            content = cached_content
        else:
            content = await extract_file_moonshotai(file_path, api_key)
            set_cached_file_extract(
                event,
                provider=provider,
                file_path=file_path,
                result=content,
            )
        if not content:
            return None

        record: dict[str, Any] = {
            "name": file_component.name or Path(file_path).name,
            "content": content,
            "provider": provider,
            "source": source,
            **copy_input_annotation_fields(annotation),
        }
        if reply_id is not None:
            record["reply_id"] = reply_id
        return record

    def _resolve_annotation(
        self,
        annotations: dict[str, dict[str, str]] | None,
        key: str | None,
    ) -> dict[str, str]:
        if not annotations or not key:
            return {}
        return copy_input_annotation_fields(annotations.get(key))

    def _extract_annotation_fields_from_record(
        self,
        record: dict[str, Any],
    ) -> dict[str, str]:
        return {
            field: value
            for field, value in copy_input_annotation_fields(record).items()
            if isinstance(value, str) and value
        }

    def _infer_transport(self, image_ref: str) -> str:
        if image_ref.startswith("http://") or image_ref.startswith("https://"):
            return "url"
        if image_ref.startswith("base64://"):
            return "base64"
        if image_ref.startswith("file://"):
            return "file"
        return "resolved_path"
