import base64

import pytest

from astrbot.core.exceptions import EmptyModelOutputError
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.provider.sources.gemini_source import ProviderGoogleGenAI


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
async def test_gemini_encode_image_uses_detected_png_mime(tmp_path):
    image_path = tmp_path / "sample.png"
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
    image_path.write_bytes(image_bytes)
    provider = object.__new__(ProviderGoogleGenAI)

    encoded = await provider.encode_image_bs64(str(image_path))

    assert encoded == (
        "data:image/png;base64," + base64.b64encode(image_bytes).decode("utf-8")
    )


def test_prepare_conversation_preserves_tool_calls_with_assistant_text():
    provider = object.__new__(ProviderGoogleGenAI)
    provider.provider_config = {}

    conversation = provider._prepare_conversation(
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


def test_prepare_conversation_skips_duplicate_empty_thought_part_when_tool_signature_exists():
    provider = object.__new__(ProviderGoogleGenAI)
    provider.provider_config = {}
    thought_signature = base64.b64encode(b"signature").decode("utf-8")

    conversation = provider._prepare_conversation(
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
