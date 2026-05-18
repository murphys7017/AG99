"""Tests for adapting rendered prompt output back into ProviderRequest."""

from __future__ import annotations

import pytest

from astrbot.core.agent.message import AudioURLPart, ImageURLPart, TextPart
from astrbot.core.agent.tool import ToolSet
from astrbot.core.prompt.context_types import ContextPack, ContextSlot
from astrbot.core.prompt.render import (
    BasePromptRenderer,
    PromptRenderEngine,
    ProviderRequestAdapter,
    RenderResult,
    apply_render_result_to_request,
)
from astrbot.core.provider.entities import ProviderRequest


def test_request_adapter_applies_system_prompt_history_and_text_user_message():
    adapter = ProviderRequestAdapter()
    tool_set = ToolSet()
    request = ProviderRequest(
        prompt="old prompt",
        system_prompt="old system",
        contexts=[{"role": "assistant", "content": "old history"}],
        image_urls=["file:///tmp/old.png"],
        audio_urls=["file:///tmp/old.mp3"],
        extra_user_content_parts=[TextPart(text="old extra")],
        func_tool=tool_set,
    )
    result = RenderResult(
        system_prompt="<system>new system</system>",
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "final user input"},
        ],
        tool_schema=[
            {
                "type": "function",
                "function": {
                    "name": "tool_a",
                    "description": "Tool A",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    apply_result = adapter.apply_render_result(result, request)

    assert request.system_prompt == "<system>new system</system>"
    assert request.contexts == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert request.prompt == "final user input"
    assert request.extra_user_content_parts == []
    assert request.image_urls == []
    assert request.audio_urls == []
    assert request.func_tool is tool_set
    assert apply_result.applied_system_prompt is True
    assert apply_result.history_message_count == 2
    assert apply_result.used_user_message is True
    assert apply_result.user_content_part_count == 0
    assert apply_result.tool_schema_count == 1
    assert apply_result.warnings == []


def test_request_adapter_preserves_internal_context_messages():
    request = ProviderRequest(prompt="old prompt")
    result = RenderResult(
        system_prompt="<system>stable</system>",
        messages=[
            {
                "role": "user",
                "content": "<memory>context</memory>",
                "_no_save": True,
            },
            {"role": "user", "content": "final user input"},
        ],
    )

    apply_result = apply_render_result_to_request(result, request)

    assert request.system_prompt == "<system>stable</system>"
    assert request.contexts == [
        {
            "role": "user",
            "content": "<memory>context</memory>",
            "_no_save": True,
        }
    ]
    assert request.prompt == "final user input"
    assert apply_result.history_message_count == 1
    assert apply_result.used_user_message is True


def test_request_adapter_maps_multimodal_user_content_into_request_parts():
    result = RenderResult(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "<request_context>ctx</request_context>"},
                    {"type": "text", "text": "<user_input>hello</user_input>"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "file:///tmp/demo.png", "id": "img-1"},
                    },
                    {
                        "type": "audio_url",
                        "audio_url": {"url": "file:///tmp/demo.mp3", "id": "aud-1"},
                    },
                ],
            }
        ]
    )
    request = ProviderRequest()

    apply_result = apply_render_result_to_request(result, request)

    assert request.prompt == "<request_context>ctx</request_context>"
    assert len(request.extra_user_content_parts) == 3
    assert request.extra_user_content_parts[0] == TextPart(
        text="<user_input>hello</user_input>"
    )
    assert request.extra_user_content_parts[1] == ImageURLPart(
        image_url=ImageURLPart.ImageURL(url="file:///tmp/demo.png", id="img-1")
    )
    assert request.extra_user_content_parts[2] == AudioURLPart(
        audio_url=AudioURLPart.AudioURL(url="file:///tmp/demo.mp3", id="aud-1")
    )
    assert apply_result.user_content_part_count == 4
    assert apply_result.warnings == []


def test_request_adapter_maps_anthropic_image_content_into_request_parts():
    result = RenderResult(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "QUJD",
                        },
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": "https://example.com/demo.png",
                        },
                    },
                ],
            }
        ]
    )
    request = ProviderRequest()

    apply_result = apply_render_result_to_request(result, request)

    assert request.prompt == "look"
    assert request.extra_user_content_parts == [
        ImageURLPart(
            image_url=ImageURLPart.ImageURL(url="data:image/png;base64,QUJD")
        ),
        ImageURLPart(
            image_url=ImageURLPart.ImageURL(url="https://example.com/demo.png")
        ),
    ]
    assert apply_result.user_content_part_count == 3
    assert apply_result.warnings == []


def test_request_adapter_keeps_all_messages_as_history_when_no_final_user_exists():
    request = ProviderRequest(prompt="old")
    result = RenderResult(
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "assistant only"},
        ]
    )

    apply_result = apply_render_result_to_request(result, request)

    assert request.contexts == [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "assistant only"},
    ]
    assert request.prompt is None
    assert request.extra_user_content_parts == []
    assert apply_result.used_user_message is False
    assert apply_result.history_message_count == 2


def test_request_adapter_preserves_runtime_only_fields_when_applying_visible_prompt():
    tool_set = ToolSet()
    conversation = object()
    tool_calls_result = ["tool-result"]
    request = ProviderRequest(
        prompt="old prompt",
        session_id="session-1",
        model="gpt-test",
        func_tool=tool_set,
        conversation=conversation,
        tool_calls_result=tool_calls_result,
    )
    result = RenderResult(
        system_prompt="<system>new system</system>",
        messages=[{"role": "user", "content": "new prompt"}],
    )

    apply_result = apply_render_result_to_request(result, request)

    assert request.prompt == "new prompt"
    assert request.system_prompt == "<system>new system</system>"
    assert request.session_id == "session-1"
    assert request.model == "gpt-test"
    assert request.func_tool is tool_set
    assert request.conversation is conversation
    assert request.tool_calls_result == tool_calls_result
    assert apply_result.used_user_message is True


def test_request_adapter_skips_invalid_user_parts_without_touching_tool_runtime():
    tool_set = ToolSet()
    request = ProviderRequest(func_tool=tool_set)
    result = RenderResult(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "image_url", "image_url": {}},
                    {"type": "unsupported", "value": "nope"},
                ],
            }
        ],
        tool_schema=[{"type": "function", "function": {"name": "tool_a"}}],
    )

    apply_result = apply_render_result_to_request(result, request)

    assert request.func_tool is tool_set
    assert request.prompt == "hello"
    assert request.extra_user_content_parts == []
    assert apply_result.tool_schema_count == 1
    assert len(apply_result.warnings) == 2


@pytest.mark.asyncio
async def test_request_adapter_reconstructs_rendered_user_message_order():
    pack = ContextPack(
        slots={
            "session.user_info": ContextSlot(
                name="session.user_info",
                value={
                    "user_id": "u1",
                    "nickname": "Alice",
                    "platform_name": "qq",
                    "umo": "qq:group:1",
                    "group_id": "1",
                    "group_name": "AstrBot Dev",
                    "is_group": True,
                },
                category="session",
                source="test",
            ),
            "input.text": ContextSlot(
                name="input.text",
                value="Check this image",
                category="input",
                source="test",
            ),
            "input.images": ContextSlot(
                name="input.images",
                value=[
                    {
                        "ref": "file:///tmp/demo.png",
                        "transport": "file",
                        "source": "current",
                    }
                ],
                category="input",
                source="test",
            ),
        }
    )
    engine = PromptRenderEngine(default_renderer=BasePromptRenderer())
    rendered = engine.render(pack)
    request = ProviderRequest()

    apply_render_result_to_request(rendered, request)
    assembled_message = await request.assemble_context()

    assert assembled_message == {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    "<request_context>\n"
                    "  <session>\n"
                    "    <user_info>\n"
                    "      <user_id>u1</user_id>\n"
                    "      <nickname>Alice</nickname>\n"
                    "      <platform_name>qq</platform_name>\n"
                    "      <umo>qq:group:1</umo>\n"
                    "      <group_id>1</group_id>\n"
                    "      <group_name>AstrBot Dev</group_name>\n"
                    "      <is_group>true</is_group>\n"
                    "    </user_info>\n"
                    "  </session>\n"
                    "</request_context>"
                ),
            },
            {
                "type": "text",
                "text": "<user_input>\n  <text>Check this image</text>\n</user_input>",
            },
            {
                "type": "image_url",
                "image_url": {"url": "file:///tmp/demo.png", "id": None},
            },
        ],
    }
