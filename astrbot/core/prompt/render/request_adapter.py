"""Adapt rendered prompt output back into a ProviderRequest."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from astrbot.core.agent.message import AudioURLPart, ContentPart, ImageURLPart, TextPart
from astrbot.core.provider.entities import ProviderRequest

from .interfaces import RenderResult

PROMPT_RENDER_RESULT_EXTRA_KEY = "prompt_render_result"
PROMPT_APPLY_RESULT_EXTRA_KEY = "prompt_apply_result"
PROMPT_SHADOW_PROVIDER_REQUEST_EXTRA_KEY = "prompt_shadow_provider_request"
PROMPT_SHADOW_APPLY_RESULT_EXTRA_KEY = "prompt_shadow_apply_result"
PROMPT_SHADOW_DIFF_EXTRA_KEY = "prompt_shadow_diff"


@dataclass
class PromptApplyResult:
    """Summary of how a rendered prompt was projected into a request."""

    applied_system_prompt: bool = False
    history_message_count: int = 0
    used_user_message: bool = False
    user_content_part_count: int = 0
    tool_schema_count: int = 0
    warnings: list[str] = field(default_factory=list)


class ProviderRequestAdapter:
    """Project a generic RenderResult into AstrBot's ProviderRequest contract."""

    def apply_render_result(
        self,
        result: RenderResult,
        request: ProviderRequest,
    ) -> PromptApplyResult:
        """Apply rendered prompt sections onto an existing request in place."""
        apply_result = PromptApplyResult(
            tool_schema_count=len(result.tool_schema or []),
        )

        request.system_prompt = result.system_prompt or ""
        apply_result.applied_system_prompt = bool(result.system_prompt)

        history_messages, user_message = self._split_rendered_messages(result.messages)
        request.contexts = self._clone_messages(history_messages)
        request.prompt = None
        request.extra_user_content_parts = []
        request.image_urls = []
        request.audio_urls = []

        apply_result.history_message_count = len(request.contexts)

        if user_message is not None:
            self._apply_user_message(user_message, request, apply_result)

        return apply_result

    def _split_rendered_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        normalized_messages = [
            deepcopy(message)
            for message in messages
            if isinstance(message, dict) and isinstance(message.get("role"), str)
        ]
        if not normalized_messages:
            return [], None

        last_message = normalized_messages[-1]
        if last_message.get("role") != "user":
            return normalized_messages, None

        return normalized_messages[:-1], last_message

    def _apply_user_message(
        self,
        user_message: dict[str, Any],
        request: ProviderRequest,
        apply_result: PromptApplyResult,
    ) -> None:
        apply_result.used_user_message = True
        content = user_message.get("content")

        if isinstance(content, str):
            request.prompt = content
            return

        if not isinstance(content, list):
            if content is None:
                return
            request.prompt = str(content)
            apply_result.warnings.append(
                "User message content is not a supported string or list payload.",
            )
            return

        content_parts = self._convert_content_parts(
            content,
            warnings=apply_result.warnings,
        )
        apply_result.user_content_part_count = len(content_parts)
        if not content_parts:
            return

        primary_prompt, trailing_parts = self._extract_primary_prompt_text(
            content_parts
        )
        request.prompt = primary_prompt
        request.extra_user_content_parts = trailing_parts

    def _extract_primary_prompt_text(
        self,
        content_parts: list[ContentPart],
    ) -> tuple[str | None, list[ContentPart]]:
        if not content_parts:
            return None, []

        first_part = content_parts[0]
        if not isinstance(first_part, TextPart):
            return None, content_parts

        return first_part.text, content_parts[1:]

    def _convert_content_parts(
        self,
        parts: list[Any],
        *,
        warnings: list[str],
    ) -> list[ContentPart]:
        converted_parts: list[ContentPart] = []

        for part in parts:
            if isinstance(part, ContentPart):
                converted_parts.append(deepcopy(part))
                continue

            if not isinstance(part, dict):
                warnings.append(
                    f"Skipped unsupported user content part payload: {type(part).__name__}",
                )
                continue

            part_type = part.get("type")
            if part_type == "text":
                text = part.get("text")
                if isinstance(text, str):
                    converted_parts.append(TextPart(text=text))
                    continue
            elif part_type == "image_url":
                image_payload = part.get("image_url")
                image_part = self._convert_image_part(image_payload)
                if image_part is not None:
                    converted_parts.append(image_part)
                    continue
            elif part_type == "audio_url":
                audio_payload = part.get("audio_url")
                audio_part = self._convert_audio_part(audio_payload)
                if audio_part is not None:
                    converted_parts.append(audio_part)
                    continue
            elif part_type == "image":
                image_part = self._convert_anthropic_image_part(part.get("source"))
                if image_part is not None:
                    converted_parts.append(image_part)
                    continue

            warnings.append(
                f"Skipped unsupported or invalid content part of type: {part_type!r}",
            )

        return converted_parts

    def _convert_image_part(self, payload: Any) -> ImageURLPart | None:
        if not isinstance(payload, dict):
            return None
        url = payload.get("url")
        image_id = payload.get("id")
        if not isinstance(url, str) or not url:
            return None
        if image_id is not None and not isinstance(image_id, str):
            image_id = None
        return ImageURLPart(
            image_url=ImageURLPart.ImageURL(url=url, id=image_id),
        )

    def _convert_anthropic_image_part(self, payload: Any) -> ImageURLPart | None:
        if not isinstance(payload, dict):
            return None
        source_type = payload.get("type")
        if source_type == "url":
            url = payload.get("url")
            if isinstance(url, str) and url:
                return ImageURLPart(image_url=ImageURLPart.ImageURL(url=url))
            return None
        if source_type == "base64":
            data = payload.get("data")
            media_type = payload.get("media_type")
            if isinstance(data, str) and data:
                media = media_type if isinstance(media_type, str) else "image/jpeg"
                return ImageURLPart(
                    image_url=ImageURLPart.ImageURL(
                        url=f"data:{media};base64,{data}"
                    )
                )
        return None

    def _convert_audio_part(self, payload: Any) -> AudioURLPart | None:
        if not isinstance(payload, dict):
            return None
        url = payload.get("url")
        audio_id = payload.get("id")
        if not isinstance(url, str) or not url:
            return None
        if audio_id is not None and not isinstance(audio_id, str):
            audio_id = None
        return AudioURLPart(
            audio_url=AudioURLPart.AudioURL(url=url, id=audio_id),
        )

    def _clone_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [deepcopy(message) for message in messages]


def apply_render_result_to_request(
    result: RenderResult,
    request: ProviderRequest,
) -> PromptApplyResult:
    """Convenience helper for applying a RenderResult into a request."""
    adapter = ProviderRequestAdapter()
    return adapter.apply_render_result(result, request)


__all__ = [
    "PROMPT_APPLY_RESULT_EXTRA_KEY",
    "PROMPT_RENDER_RESULT_EXTRA_KEY",
    "PROMPT_SHADOW_APPLY_RESULT_EXTRA_KEY",
    "PROMPT_SHADOW_DIFF_EXTRA_KEY",
    "PROMPT_SHADOW_PROVIDER_REQUEST_EXTRA_KEY",
    "PromptApplyResult",
    "ProviderRequestAdapter",
    "apply_render_result_to_request",
]
