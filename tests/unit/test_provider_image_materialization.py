import base64
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from PIL import Image

import astrbot.core.provider.entities as provider_entities
import astrbot.core.provider.request_media as request_media
import astrbot.core.utils.image_materializer as image_materializer
from astrbot.core.agent.message import ImageURLPart, TextPart
from astrbot.core.agent.runners.request_material import (
    image_filename,
    materialize_runner_request,
)
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.provider.request_media import normalize_provider_request_images
from astrbot.core.utils.image_materializer import (
    ImageMaterializationError,
    MaterializedImage,
    materialize_image_ref,
)


def _valid_png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (1, 1), "white").save(buffer, format="PNG")
    return buffer.getvalue()


PNG_BYTES = _valid_png_bytes()


@pytest.mark.asyncio
async def test_materialize_image_ref_validates_and_preserves_actual_mime_type():
    image = await materialize_image_ref(
        "data:image/jpeg;base64," + base64.b64encode(PNG_BYTES).decode("ascii")
    )

    assert image.data == PNG_BYTES
    assert image.mime_type == "image/png"
    assert image.to_data_url().startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_materialize_image_ref_rejects_non_image_local_content(tmp_path):
    path = tmp_path / "not-image.html"
    path.write_text("<html>not an image</html>", encoding="utf-8")

    with pytest.raises(ImageMaterializationError):
        await materialize_image_ref(str(path))


@pytest.mark.asyncio
async def test_materialize_image_ref_only_reads_local_files_from_temp_media_root(
    monkeypatch,
    tmp_path,
):
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    image_path = temp_root / "inbound.png"
    image_path.write_bytes(PNG_BYTES)
    monkeypatch.setattr(
        image_materializer,
        "get_astrbot_temp_path",
        lambda: str(temp_root),
    )

    materialized = await materialize_image_ref(image_path.as_uri())

    assert materialized.data == PNG_BYTES
    outside_path = tmp_path / "outside.png"
    outside_path.write_bytes(PNG_BYTES)
    with pytest.raises(ImageMaterializationError, match="temporary media storage"):
        await materialize_image_ref(str(outside_path))


@pytest.mark.asyncio
async def test_materialize_image_ref_rejects_unc_file_uris_before_file_access():
    with pytest.raises(ImageMaterializationError, match="remote file URIs"):
        await materialize_image_ref("file://server/share/image.png")


@pytest.mark.asyncio
async def test_provider_request_uses_shared_materializer_for_https_images(monkeypatch):
    image = MaterializedImage(PNG_BYTES, "image/png", "image-sha")
    monkeypatch.setattr(
        provider_entities,
        "materialize_image_ref",
        AsyncMock(return_value=image),
    )
    request = ProviderRequest(
        prompt="look",
        image_urls=["https://multimedia.nt.qq.com.cn/download?file=qq-image"],
    )

    message = await request.assemble_context()

    part = message["content"][1]
    assert part["type"] == "image_url"
    assert part["image_url"]["url"] == image.to_data_url()


@pytest.mark.asyncio
async def test_provider_request_drops_invalid_images_without_dropping_text(monkeypatch):
    monkeypatch.setattr(
        provider_entities,
        "materialize_image_ref",
        AsyncMock(side_effect=ImageMaterializationError("HTTP 403")),
    )
    request = ProviderRequest(prompt="keep this text", image_urls=["https://bad/image"])

    message = await request.assemble_context()

    assert message["role"] == "user"
    assert message["content"] == [{"type": "text", "text": "keep this text"}]


@pytest.mark.asyncio
async def test_normalize_provider_request_images_revalidates_plugin_mutations(
    monkeypatch,
):
    materialized = MaterializedImage(PNG_BYTES, "image/png", "image-sha")

    async def materialize(reference):
        if reference == "https://valid/image":
            return materialized
        raise ImageMaterializationError("invalid image")

    monkeypatch.setattr(request_media, "materialize_image_ref", materialize)
    request = ProviderRequest(
        image_urls=[" https://valid/image ", "https://invalid/image"],
        extra_user_content_parts=[
            ImageURLPart(
                image_url=ImageURLPart.ImageURL(url="https://valid/image")
            ),
        ],
        contexts=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "keep"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://invalid/image"},
                    },
                ],
            }
        ],
    )

    stats = await normalize_provider_request_images(request)

    assert stats.discovered == 2
    assert stats.normalized == 1
    assert stats.dropped == 1
    assert request.image_urls == [materialized.to_data_url()]
    assert request.extra_user_content_parts[0].image_url.url == materialized.to_data_url()
    assert request.contexts[0]["content"] == [
        {"type": "text", "text": "keep"},
        {"type": "text", "text": "[Image]"},
    ]


@pytest.mark.asyncio
async def test_agent_runner_request_material_projects_extensions_and_verified_images():
    image_ref = "base64://" + base64.b64encode(PNG_BYTES).decode("ascii")
    request = ProviderRequest(
        prompt="current message",
        image_urls=[image_ref],
        extra_user_content_parts=[
            TextPart(text="group context"),
            ImageURLPart(image_url=ImageURLPart.ImageURL(url=image_ref)),
        ],
    )

    material = await materialize_runner_request(request)

    assert material.prompt == "current message\n\ngroup context"
    assert len(material.images) == 1
    assert material.images[0].mime_type == "image/png"
    assert image_filename(material.images[0], index=1) == "image-1.png"
