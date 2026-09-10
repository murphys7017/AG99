import base64
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from google.genai import types

import astrbot.core.provider.sources.gemini_source as gemini_source
from astrbot.core.exceptions import EmptyModelOutputError
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.provider.sources.gemini_source import ProviderGoogleGenAI
from astrbot.core.utils.image_materializer import MaterializedImage


def _valid_png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (1, 1), "white").save(buffer, format="PNG")
    return buffer.getvalue()
@pytest.mark.asyncio
async def test_gemini_thinking_level_is_serialized_on_every_request():
    model = "gemini-3.7-flash"
    provider = ProviderGoogleGenAI.__new__(ProviderGoogleGenAI)
    provider.provider_config = {"gm_thinking_config": {"level": "HIGH"}}
    provider.provider_settings = {}
    provider.model_name = model
    provider.safety_settings = []

    first_config = await provider._prepare_query_config({"model": model})
    second_config = await provider._prepare_query_config({"model": model})

    assert first_config.thinking_config is not None
    assert second_config.thinking_config is not None
    assert first_config.thinking_config.model_dump(exclude_none=True) == {
        "thinking_level": types.ThinkingLevel.HIGH,
    }
    assert second_config.thinking_config.model_dump(exclude_none=True) == {
        "thinking_level": types.ThinkingLevel.HIGH,
    }


@pytest.mark.asyncio
async def test_gemini_37_minimal_thinking_level_falls_back_to_medium():
    model = "gemini-3.7-flash"
    provider = ProviderGoogleGenAI.__new__(ProviderGoogleGenAI)
    provider.provider_config = {"gm_thinking_config": {"level": "MINIMAL"}}
    provider.provider_settings = {}
    provider.model_name = model
    provider.safety_settings = []

    config = await provider._prepare_query_config({"model": model})

    assert config.thinking_config is not None
    assert config.thinking_config.model_dump(exclude_none=True) == {
        "thinking_level": types.ThinkingLevel.MEDIUM,
    }


def test_gemini_empty_output_raises_empty_model_output_error():
    llm_response = LLMResponse(role="assistant")

    with pytest.raises(EmptyModelOutputError):
        ProviderGoogleGenAI._ensure_usable_response(
            llm_response,
            response_id="resp_empty",
            finish_reason="STOP",
        )


def test_gemini_reasoning_only_output_is_allowed():
    llm_response = LLMResponse(
        role="assistant",
        reasoning_content="chain of thought placeholder",
    )

    ProviderGoogleGenAI._ensure_usable_response(
        llm_response,
        response_id="resp_reasoning",
        finish_reason="STOP",
    )


@pytest.mark.asyncio
async def test_gemini_encode_image_uses_detected_png_mime(monkeypatch, tmp_path):
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    monkeypatch.setattr(
        "astrbot.core.utils.image_materializer.get_astrbot_temp_path",
        lambda: str(temp_root),
    )
    image_path = temp_root / "sample.png"
    image_bytes = _valid_png_bytes()
    image_path.write_bytes(image_bytes)
    provider = object.__new__(ProviderGoogleGenAI)

    encoded = await provider.encode_image_bs64(str(image_path))

    assert encoded == (
        "data:image/png;base64," + base64.b64encode(image_bytes).decode("utf-8")
    )


@pytest.mark.asyncio
async def test_prepare_conversation_preserves_tool_calls_with_assistant_text():
    provider = object.__new__(ProviderGoogleGenAI)
    provider.provider_config = {}

    conversation = await provider._prepare_conversation(
        {
            "messages": [
                {"role": "user", "content": "Hi"},
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Need to call a tool."}],
                    "tool_calls": [
                        {
                            "function": {
                                "name": "weather",
                                "arguments": '{"city":"Shanghai"}',
                            }
                        }
                    ],
                }
            ]
        }
    )

    assert len(conversation) == 2
    assert conversation[1].role == "model"
    parts = conversation[1].parts
    assert parts is not None
    assert parts[0].text == "Need to call a tool."
    assert parts[1].function_call is not None
    assert parts[1].function_call.name == "weather"


@pytest.mark.asyncio
async def test_prepare_conversation_materializes_https_context_image(monkeypatch):
    provider = object.__new__(ProviderGoogleGenAI)
    provider.provider_config = {}
    materialize = AsyncMock(
        return_value=MaterializedImage(b"image-data", "image/png", "image-sha")
    )
    monkeypatch.setattr(gemini_source, "materialize_image_ref", materialize)

    conversation = await provider._prepare_conversation(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "https://multimedia.nt.qq.com.cn/download?file=qq"
                            },
                        },
                    ],
                }
            ]
        }
    )

    assert len(conversation[0].parts) == 2
    materialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_conversation_skips_duplicate_empty_thought_part_when_tool_signature_exists():
    provider = object.__new__(ProviderGoogleGenAI)
    provider.provider_config = {}
    thought_signature = base64.b64encode(b"signature").decode("utf-8")

    conversation = await provider._prepare_conversation(
        {
            "messages": [
                {"role": "user", "content": "Hi"},
                {
                    "role": "assistant",
                    "content": [{"type": "think", "encrypted": thought_signature}],
                    "tool_calls": [
                        {
                            "function": {
                                "name": "weather",
                                "arguments": '{"city":"Shanghai"}',
                            },
                            "extra_content": {
                                "google": {"thought_signature": thought_signature}
                            },
                        }
                    ],
                }
            ]
        }
    )

    assert len(conversation) == 2
    parts = conversation[1].parts
    assert parts is not None
    assert len(parts) == 1
    assert parts[0].function_call is not None
    assert parts[0].function_call.name == "weather"
