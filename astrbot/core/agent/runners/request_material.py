"""Provider-neutral input material for third-party Agent Runners.

Agent Runners do not share the normal Provider adapter pipeline. Normalize
their text extensions and image references here so they cannot silently drift
from the canonical ``ProviderRequest`` contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from astrbot import logger
from astrbot.core.agent.message import ImageURLPart, TextPart
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.utils.image_materializer import (
    ImageMaterializationError,
    MaterializedImage,
    materialize_image_ref,
)


@dataclass(frozen=True, slots=True)
class RunnerRequestMaterial:
    """Text and verified images projected from one ``ProviderRequest``."""

    prompt: str
    images: tuple[MaterializedImage, ...]


async def materialize_runner_request(
    request: ProviderRequest,
) -> RunnerRequestMaterial:
    """Project request extensions and images for Agent Runner transports."""
    prompt_parts = [str(request.prompt or "").strip()]
    image_refs = list(request.image_urls or [])
    for part in request.extra_user_content_parts or []:
        text = _text_part_value(part)
        if text:
            prompt_parts.append(text)
            continue
        image_ref = _image_part_ref(part)
        if image_ref:
            image_refs.append(image_ref)

    images: list[MaterializedImage] = []
    seen_refs: set[str] = set()
    seen_hashes: set[str] = set()
    for image_ref in image_refs:
        normalized_ref = str(image_ref or "").strip()
        if not normalized_ref or normalized_ref in seen_refs:
            continue
        seen_refs.add(normalized_ref)
        try:
            image = await materialize_image_ref(normalized_ref)
        except ImageMaterializationError as exc:
            logger.warning("Agent Runner ignored invalid image input: %s", exc)
            continue
        if image.sha256 in seen_hashes:
            continue
        seen_hashes.add(image.sha256)
        images.append(image)

    return RunnerRequestMaterial(
        prompt="\n\n".join(part for part in prompt_parts if part),
        images=tuple(images),
    )


def image_filename(image: MaterializedImage, *, index: int) -> str:
    """Return a stable, format-correct upload filename."""
    suffix = {
        "image/avif": "avif",
        "image/bmp": "bmp",
        "image/gif": "gif",
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/tiff": "tiff",
        "image/webp": "webp",
    }.get(image.mime_type, "img")
    return f"image-{index}.{suffix}"


def _text_part_value(part: Any) -> str:
    if isinstance(part, TextPart):
        return part.text.strip()
    if isinstance(part, dict) and part.get("type") == "text":
        value = part.get("text")
        return value.strip() if isinstance(value, str) else ""
    return ""


def _image_part_ref(part: Any) -> str:
    if isinstance(part, ImageURLPart):
        return str(part.image_url.url or "").strip()
    if not isinstance(part, dict) or part.get("type") != "image_url":
        return ""
    image_url = part.get("image_url")
    if isinstance(image_url, dict):
        image_url = image_url.get("url")
    return image_url.strip() if isinstance(image_url, str) else ""


__all__ = [
    "RunnerRequestMaterial",
    "image_filename",
    "materialize_runner_request",
]
