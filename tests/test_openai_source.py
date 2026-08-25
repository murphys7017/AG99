import asyncio
import base64
import builtins
from io import BytesIO
from types import SimpleNamespace

import pytest
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from PIL import Image as PILImage

import astrbot.core.provider.sources.openai_source as openai_source_module
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.exceptions import EmptyModelOutputError
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.provider.sources.groq_source import ProviderGroq
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from astrbot.core.utils.image_materializer import (
    ImageMaterializationError,
    MaterializedImage,
)


class _ErrorWithBody(Exception):
    def __init__(self, message: str, body: dict):
        super().__init__(message)
        self.body = body


class _ErrorWithResponse(Exception):
    def __init__(self, message: str, response_text: str):
        super().__init__(message)
        self.response = SimpleNamespace(text=response_text)


def _make_provider(overrides: dict | None = None) -> ProviderOpenAIOfficial:
    provider_config = {
        "id": "test-openai",
        "type": "openai_chat_completion",
        "model": "gpt-4o-mini",
        "key": ["test-key"],
    }
    if overrides:
        provider_config.update(overrides)
    return ProviderOpenAIOfficial(
        provider_config=provider_config,
        provider_settings={},
    )


def _make_recovery_state(
    provider: ProviderOpenAIOfficial,
    payloads: dict,
    *,
    image_fallback_used: bool = False,
):
    state = provider._create_chat_recovery_state(
        payloads,
        payloads["messages"],
        None,
    )
    state.image_fallback_used = image_fallback_used
    return state


def _make_groq_provider(overrides: dict | None = None) -> ProviderGroq:
    provider_config = {
        "id": "test-groq",
        "type": "groq_chat_completion",
        "model": "qwen/qwen3-32b",
        "key": ["test-key"],
    }
    if overrides:
        provider_config.update(overrides)
    return ProviderGroq(
        provider_config=provider_config,
        provider_settings={},
    )


def test_create_http_client_uses_openai_httpx_module(monkeypatch):
    captured: dict[str, object] = {}

    def fake_create_proxy_client(
        provider_label: str,
        proxy: str | None = None,
        headers: dict[str, str] | None = None,
        verify=None,
        httpx_module=None,
    ):
        captured["httpx_module"] = httpx_module
        return object()

    monkeypatch.setattr(
        openai_source_module,
        "create_proxy_client",
        fake_create_proxy_client,
    )

    provider = ProviderOpenAIOfficial.__new__(ProviderOpenAIOfficial)
    provider._create_http_client({"proxy": ""})

    from openai import _base_client as openai_base_client

    assert captured["httpx_module"] is openai_base_client.httpx


def test_create_http_client_falls_back_to_global_httpx_module(monkeypatch):
    captured: dict[str, object] = {}

    def fake_create_proxy_client(
        provider_label: str,
        proxy: str | None = None,
        headers: dict[str, str] | None = None,
        verify=None,
        httpx_module=None,
    ):
        captured["httpx_module"] = httpx_module
        return object()

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai" and fromlist:
            raise ImportError("missing openai._base_client")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(
        openai_source_module,
        "create_proxy_client",
        fake_create_proxy_client,
    )
    monkeypatch.setattr(builtins, "__import__", fake_import)

    provider = ProviderOpenAIOfficial.__new__(ProviderOpenAIOfficial)
    provider._create_http_client({"proxy": ""})

    assert captured["httpx_module"] is openai_source_module.httpx


@pytest.mark.asyncio
async def test_handle_api_error_content_moderated_removes_images():
    provider = _make_provider(
        {"image_moderation_error_patterns": ["file:content-moderated"]}
    )
    try:
        payloads = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64,abcd"},
                        },
                    ],
                }
            ]
        }

        state = await provider._handle_api_error(
            Exception("Content is moderated [WKE=file:content-moderated]"),
            _make_recovery_state(provider, payloads),
        )

        assert state.image_fallback_used is True
        updated_context = payloads["messages"]
        assert isinstance(updated_context, list)
        assert updated_context[0]["content"] == [{"type": "text", "text": "hello"}]
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_handle_api_error_model_not_vlm_removes_images_and_retries_text_only():
    provider = _make_provider()
    try:
        payloads = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64,abcd"},
                        },
                    ],
                }
            ]
        }

        state = await provider._handle_api_error(
            Exception("The model is not a VLM and cannot process images"),
            _make_recovery_state(provider, payloads),
        )

        assert state.image_fallback_used is True
        updated_context = payloads["messages"]
        assert isinstance(updated_context, list)
        assert updated_context[0]["content"] == [{"type": "text", "text": "hello"}]
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_handle_api_error_model_not_vlm_after_fallback_raises():
    provider = _make_provider()
    try:
        payloads = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64,abcd"},
                        },
                    ],
                }
            ]
        }

        with pytest.raises(Exception, match="not a VLM"):
            await provider._handle_api_error(
                Exception("The model is not a VLM and cannot process images"),
                _make_recovery_state(
                    provider,
                    payloads,
                    image_fallback_used=True,
                ),
            )
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_handle_api_error_content_moderated_with_unserializable_body():
    provider = _make_provider({"image_moderation_error_patterns": ["blocked"]})
    try:
        payloads = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64,abcd"},
                        },
                    ],
                }
            ]
        }
        err = _ErrorWithBody(
            "upstream error",
            {"error": {"message": "blocked"}, "raw": object()},
        )

        state = await provider._handle_api_error(
            err,
            _make_recovery_state(provider, payloads),
        )
        assert state.image_fallback_used is True
        assert payloads["messages"][0]["content"] == [{"type": "text", "text": "hello"}]
    finally:
        await provider.terminate()


def test_extract_error_text_candidates_truncates_long_response_text():
    long_text = "x" * 20000
    err = _ErrorWithResponse("upstream error", long_text)
    candidates = ProviderOpenAIOfficial._extract_error_text_candidates(err)
    assert candidates
    assert max(len(candidate) for candidate in candidates) <= (
        ProviderOpenAIOfficial._ERROR_TEXT_CANDIDATE_MAX_CHARS
    )


@pytest.mark.asyncio
async def test_openai_payload_keeps_reasoning_content_in_assistant_history():
    provider = _make_provider()
    try:
        payloads = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "think", "think": "step 1"},
                        {"type": "text", "text": "final answer"},
                    ],
                }
            ]
        }

        provider._finally_convert_payload(payloads)

        assistant_message = payloads["messages"][0]
        assert assistant_message["content"] == [
            {"type": "text", "text": "final answer"}
        ]
        assert assistant_message["reasoning_content"] == "step 1"
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_groq_payload_drops_reasoning_content_from_assistant_history():
    provider = _make_groq_provider()
    try:
        payloads = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "think", "think": "step 1"},
                        {"type": "text", "text": "final answer"},
                    ],
                }
            ]
        }

        provider._finally_convert_payload(payloads)

        assistant_message = payloads["messages"][0]
        assert assistant_message["content"] == [
            {"type": "text", "text": "final answer"}
        ]
        assert "reasoning_content" not in assistant_message
        assert "reasoning" not in assistant_message
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_handle_api_error_content_moderated_without_images_raises():
    provider = _make_provider(
        {"image_moderation_error_patterns": ["file:content-moderated"]}
    )
    try:
        payloads = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "hello"}],
                }
            ]
        }
        err = Exception("Content is moderated [WKE=file:content-moderated]")

        with pytest.raises(Exception, match="content-moderated"):
            await provider._handle_api_error(
                err,
                _make_recovery_state(provider, payloads),
            )
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_handle_api_error_content_moderated_detects_structured_body():
    provider = _make_provider(
        {"image_moderation_error_patterns": ["content_moderated"]}
    )
    try:
        payloads = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64,abcd"},
                        },
                    ],
                }
            ]
        }
        err = _ErrorWithBody(
            "upstream error",
            {"error": {"code": "content_moderated", "message": "blocked"}},
        )

        state = await provider._handle_api_error(
            err,
            _make_recovery_state(provider, payloads),
        )
        assert state.image_fallback_used is True
        assert payloads["messages"][0]["content"] == [{"type": "text", "text": "hello"}]
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_handle_api_error_content_moderated_supports_custom_patterns():
    provider = _make_provider(
        {"image_moderation_error_patterns": ["blocked_by_policy_code_123"]}
    )
    try:
        payloads = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64,abcd"},
                        },
                    ],
                }
            ]
        }
        err = Exception("upstream: blocked_by_policy_code_123")

        state = await provider._handle_api_error(
            err,
            _make_recovery_state(provider, payloads),
        )
        assert state.image_fallback_used is True
        assert payloads["messages"][0]["content"] == [{"type": "text", "text": "hello"}]
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_handle_api_error_content_moderated_without_patterns_raises():
    provider = _make_provider()
    try:
        payloads = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64,abcd"},
                        },
                    ],
                }
            ]
        }
        err = Exception("Content is moderated [WKE=file:content-moderated]")

        with pytest.raises(Exception, match="content-moderated"):
            await provider._handle_api_error(
                err,
                _make_recovery_state(provider, payloads),
            )
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_handle_api_error_unknown_image_error_raises():
    provider = _make_provider()
    try:
        payloads = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64,abcd"},
                        },
                    ],
                }
            ]
        }

        with pytest.raises(Exception, match="unknown provider image upload error"):
            await provider._handle_api_error(
                Exception("some unknown provider image upload error"),
                _make_recovery_state(provider, payloads),
            )
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_handle_api_error_invalid_attachment_removes_images_and_retries_text_only():
    provider = _make_provider()
    try:
        payloads = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64,abcd"},
                        },
                    ],
                }
            ]
        }
        err = _ErrorWithBody(
            "upstream error",
            {
                "error": {
                    "code": "INVALID_ATTACHMENT",
                    "message": "download attachment: unexpected status 404",
                }
            },
        )

        state = await provider._handle_api_error(
            err,
            _make_recovery_state(provider, payloads),
        )

        assert state.image_fallback_used is True
        assert payloads["messages"][0]["content"] == [{"type": "text", "text": "hello"}]
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_handle_api_error_invalid_attachment_without_images_raises():
    provider = _make_provider()
    try:
        payloads = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "hello"}],
                }
            ]
        }
        err = _ErrorWithBody(
            "upstream error",
            {
                "error": {
                    "code": "INVALID_ATTACHMENT",
                    "message": "download attachment: unexpected status 404",
                }
            },
        )

        with pytest.raises(_ErrorWithBody, match="upstream error"):
            await provider._handle_api_error(
                err,
                _make_recovery_state(provider, payloads),
            )
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_handle_api_error_invalid_attachment_after_fallback_raises():
    provider = _make_provider()
    try:
        payloads = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64,abcd"},
                        },
                    ],
                }
            ]
        }
        err = _ErrorWithBody(
            "upstream error",
            {
                "error": {
                    "code": "INVALID_ATTACHMENT",
                    "message": "download attachment: unexpected status 404",
                }
            },
        )

        with pytest.raises(_ErrorWithBody, match="upstream error"):
            await provider._handle_api_error(
                err,
                _make_recovery_state(
                    provider,
                    payloads,
                    image_fallback_used=True,
                ),
            )
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_text_chat_returns_success_on_last_recovery_attempt(monkeypatch):
    provider = _make_provider()
    provider._MAX_RECOVERY_ATTEMPTS = 2
    expected = LLMResponse(role="assistant", completion_text="recovered")
    call_count = 0

    async def fake_query(payloads, tools):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("maximum context length exceeded")
        assert payloads["messages"] == []
        assert tools is None
        return expected

    monkeypatch.setattr(provider, "_query", fake_query)
    try:
        result = await provider.text_chat(
            contexts=[
                {"role": "user", "content": "old request"},
                {"role": "assistant", "content": "old response"},
            ],
        )
    finally:
        await provider.terminate()

    assert result is expected
    assert call_count == 2


@pytest.mark.asyncio
async def test_text_chat_preserves_required_tools_on_unsupported_provider(monkeypatch):
    provider = _make_provider()
    call_count = 0
    tools = ToolSet(
        tools=[
            FunctionTool(
                name="required_tool",
                description="Required protocol tool.",
                parameters={"type": "object", "properties": {}},
            )
        ]
    )

    async def fake_query(payloads, func_tool):
        nonlocal call_count
        call_count += 1
        assert payloads["tool_choice"] == "required"
        assert func_tool is tools
        raise Exception("Function calling is not enabled")

    monkeypatch.setattr(provider, "_query", fake_query)
    try:
        with pytest.raises(Exception, match="Function calling is not enabled"):
            await provider.text_chat(
                contexts=[{"role": "user", "content": "hello"}],
                func_tool=tools,
                tool_choice="required",
            )
    finally:
        await provider.terminate()

    assert call_count == 1


@pytest.mark.asyncio
async def test_prepare_chat_payload_materializes_context_http_image_urls(monkeypatch):
    provider = _make_provider()
    try:
        image = MaterializedImage(b"quoted-image", "image/png", "quoted-image")

        async def fake_materialize(url: str) -> MaterializedImage:
            assert url == "https://example.com/quoted.png"
            return image

        monkeypatch.setattr(
            openai_source_module,
            "materialize_image_ref",
            fake_materialize,
        )

        contexts = [
            {
                "role": "user",
                "metadata": {"source": "quoted"},
                "content": [
                    {"type": "text", "text": "look"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "https://example.com/quoted.png",
                            "id": "ctx-img",
                            "detail": "high",
                        },
                    },
                ],
            }
        ]

        payloads, _ = await provider._prepare_chat_payload(
            prompt=None,
            contexts=contexts,
        )

        assert payloads["messages"][0]["content"] == [
            {"type": "text", "text": "look"},
            {
                "type": "image_url",
                "image_url": {
                    "url": image.to_data_url(),
                    "detail": "high",
                },
            },
        ]
        assert payloads["messages"][0]["content"][1]["image_url"].get("id") is None
        assert contexts[0]["content"][1]["image_url"] == {
            "url": "https://example.com/quoted.png",
            "id": "ctx-img",
            "detail": "high",
        }
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_prepare_chat_payload_skips_materialization_for_text_only_context(
    monkeypatch,
):
    provider = _make_provider()
    try:

        async def fail_if_called(_context_query):
            raise AssertionError("materialization should be skipped")

        monkeypatch.setattr(
            provider, "_materialize_context_image_parts", fail_if_called
        )

        payloads, _ = await provider._prepare_chat_payload(
            prompt=None,
            contexts=[{"role": "user", "content": "hello"}],
        )

        assert payloads["messages"] == [{"role": "user", "content": "hello"}]
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_prepare_chat_payload_skips_materialization_for_text_only_parts(
    monkeypatch,
):
    provider = _make_provider()
    try:

        async def fail_if_called(_context_query):
            raise AssertionError("materialization should be skipped")

        monkeypatch.setattr(
            provider, "_materialize_context_image_parts", fail_if_called
        )

        payloads, _ = await provider._prepare_chat_payload(
            prompt=None,
            contexts=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "hello"}],
                }
            ],
        )

        assert payloads["messages"] == [
            {
                "role": "user",
                "content": [{"type": "text", "text": "hello"}],
            }
        ]
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_prepare_chat_payload_materializes_context_http_image_urls_with_detected_mime(
    monkeypatch, tmp_path
):
    provider = _make_provider()
    try:
        image_path = tmp_path / "quoted-image.png"
        PILImage.new("RGBA", (1, 1), (255, 0, 0, 255)).save(image_path)
        image = MaterializedImage(
            image_path.read_bytes(),
            "image/png",
            "quoted-image",
        )

        async def fake_materialize(url: str) -> MaterializedImage:
            assert url == "https://example.com/quoted.png"
            return image

        monkeypatch.setattr(
            openai_source_module,
            "materialize_image_ref",
            fake_materialize,
        )

        payloads, _ = await provider._prepare_chat_payload(
            prompt=None,
            contexts=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "https://example.com/quoted.png",
                            },
                        },
                    ],
                }
            ],
        )

        image_payload = payloads["messages"][0]["content"][1]["image_url"]
        assert image_payload["url"] == image.to_data_url()
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_prepare_chat_payload_materializes_context_file_uri_image_urls(
    monkeypatch,
    tmp_path,
):
    provider = _make_provider()
    try:
        temp_root = tmp_path / "temp"
        temp_root.mkdir()
        monkeypatch.setattr(
            "astrbot.core.utils.image_materializer.get_astrbot_temp_path",
            lambda: str(temp_root),
        )
        image_path = temp_root / "quoted-image.png"
        PILImage.new("RGBA", (1, 1), (255, 0, 0, 255)).save(image_path)

        payloads, _ = await provider._prepare_chat_payload(
            prompt=None,
            contexts=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_path.as_uri(),
                            },
                        },
                    ],
                }
            ],
        )

        image_payload = payloads["messages"][0]["content"][1]["image_url"]
        assert image_payload["url"].startswith("data:image/png;base64,")
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_file_uri_to_path_preserves_windows_drive_letter():
    provider = _make_provider()
    try:
        assert provider._file_uri_to_path("file:///C:/tmp/quoted-image.png") == (
            "C:/tmp/quoted-image.png"
        )
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_file_uri_to_path_accepts_legacy_windows_backslashes():
    provider = _make_provider()
    try:
        assert provider._file_uri_to_path(
            r"file:///C:\tmp\quoted-image.png"
        ) == "C:/tmp/quoted-image.png"
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_file_uri_to_path_preserves_windows_netloc_drive_letter():
    provider = _make_provider()
    try:
        assert provider._file_uri_to_path("file://C:/tmp/quoted-image.png") == (
            "C:/tmp/quoted-image.png"
        )
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_file_uri_to_path_preserves_remote_netloc_as_unc_path():
    provider = _make_provider()
    try:
        assert provider._file_uri_to_path("file://server/share/quoted-image.png") == (
            "//server/share/quoted-image.png"
        )
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_resolve_image_part_rejects_invalid_local_file(tmp_path):
    provider = _make_provider()
    try:
        invalid_file = tmp_path / "not-image.txt"
        invalid_file.write_text("not an image")

        assert await provider._resolve_image_part(str(invalid_file)) is None
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_resolve_image_part_rejects_invalid_file_uri(tmp_path):
    provider = _make_provider()
    try:
        invalid_file = tmp_path / "not-image.txt"
        invalid_file.write_text("not an image")

        assert await provider._resolve_image_part(invalid_file.as_uri()) is None
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_image_ref_to_data_url_mode_controls_invalid_file_behavior(
    monkeypatch,
    tmp_path,
):
    provider = _make_provider()
    try:
        temp_root = tmp_path / "temp"
        temp_root.mkdir()
        monkeypatch.setattr(
            "astrbot.core.utils.image_materializer.get_astrbot_temp_path",
            lambda: str(temp_root),
        )
        invalid_file = temp_root / "not-image.txt"
        invalid_file.write_text("not an image")

        assert (
            await provider._image_ref_to_data_url(str(invalid_file), mode="safe")
            is None
        )
        with pytest.raises(ImageMaterializationError, match="valid image"):
            await provider._image_ref_to_data_url(str(invalid_file), mode="strict")
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_materialize_context_image_parts_returns_new_messages(monkeypatch):
    provider = _make_provider()
    try:
        context_query = [
            {
                "role": "user",
                "metadata": {"source": "quoted"},
                "content": [
                    {"type": "text", "text": "look"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "https://example.com/quoted.png",
                            "detail": "high",
                        },
                    },
                ],
            },
            {"role": "assistant", "content": "plain text"},
        ]

        async def fake_resolve(image_url: str, *, image_detail: str | None = None):
            assert image_url == "https://example.com/quoted.png"
            assert image_detail == "high"
            return {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,abcd",
                    "detail": "high",
                },
            }

        monkeypatch.setattr(provider, "_resolve_image_part", fake_resolve)

        materialized = await provider._materialize_context_image_parts(context_query)

        assert materialized is not context_query
        assert materialized[0] is not context_query[0]
        assert materialized[0]["metadata"] is context_query[0]["metadata"]
        assert materialized[0]["content"][0] is context_query[0]["content"][0]
        assert (
            materialized[0]["content"][1]["image_url"]["url"]
            == "data:image/png;base64,abcd"
        )
        assert (
            context_query[0]["content"][1]["image_url"]["url"]
            == "https://example.com/quoted.png"
        )
        assert materialized[1] is not context_query[1]
        assert materialized[1]["content"] == "plain text"
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_materialize_context_drops_unreadable_image_parts(monkeypatch):
    provider = _make_provider()
    try:
        async def fail_to_resolve(*args, **kwargs):
            return None

        monkeypatch.setattr(provider, "_resolve_image_part", fail_to_resolve)
        contexts = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "file:///missing.png"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "file:///missing.png"},
                    }
                ],
            },
        ]

        materialized = await provider._materialize_context_image_parts(contexts)

        assert materialized[0]["content"] == [{"type": "text", "text": "look"}]
        assert materialized[1]["content"] == [
            {"type": "text", "text": "[Image unavailable]"}
        ]
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_encode_image_bs64_missing_file_raises(monkeypatch, tmp_path):
    provider = _make_provider()
    try:
        temp_root = tmp_path / "temp"
        temp_root.mkdir()
        monkeypatch.setattr(
            "astrbot.core.utils.image_materializer.get_astrbot_temp_path",
            lambda: str(temp_root),
        )
        missing_path = temp_root / "missing-image.png"
        with pytest.raises(ImageMaterializationError, match="could not resolve image file"):
            await provider.encode_image_bs64(str(missing_path))
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_encode_image_bs64_invalid_file_raises(monkeypatch, tmp_path):
    provider = _make_provider()
    try:
        temp_root = tmp_path / "temp"
        temp_root.mkdir()
        monkeypatch.setattr(
            "astrbot.core.utils.image_materializer.get_astrbot_temp_path",
            lambda: str(temp_root),
        )
        invalid_file = temp_root / "not-image.txt"
        invalid_file.write_text("not an image")

        with pytest.raises(ImageMaterializationError, match="valid image"):
            await provider.encode_image_bs64(str(invalid_file))
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_encode_image_bs64_supports_base64_scheme():
    provider = _make_provider()
    try:
        image_buffer = BytesIO()
        PILImage.new("RGBA", (1, 1), (255, 0, 0, 255)).save(
            image_buffer,
            format="PNG",
        )
        image_base64 = base64.b64encode(image_buffer.getvalue()).decode("ascii")
        image_data = await provider.encode_image_bs64(f"base64://{image_base64}")

        assert image_data == f"data:image/png;base64,{image_base64}"
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_encode_image_bs64_supports_file_uri(monkeypatch, tmp_path):
    provider = _make_provider()
    try:
        temp_root = tmp_path / "temp"
        temp_root.mkdir()
        monkeypatch.setattr(
            "astrbot.core.utils.image_materializer.get_astrbot_temp_path",
            lambda: str(temp_root),
        )
        image_path = temp_root / "quoted-image.png"
        PILImage.new("RGBA", (1, 1), (255, 0, 0, 255)).save(image_path)

        image_data = await provider.encode_image_bs64(image_path.as_uri())

        assert image_data.startswith("data:image/png;base64,")
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_resolve_image_part_supports_base64_scheme():
    provider = _make_provider()
    try:
        image_buffer = BytesIO()
        PILImage.new("RGBA", (1, 1), (255, 0, 0, 255)).save(
            image_buffer,
            format="PNG",
        )
        image_base64 = base64.b64encode(image_buffer.getvalue()).decode("ascii")

        assert await provider._resolve_image_part(f"base64://{image_base64}") == {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_base64}"},
        }
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_resolve_image_part_preserves_base64_png_mime_type():
    provider = _make_provider()
    try:
        image_buffer = BytesIO()
        PILImage.new("RGBA", (1, 1), (255, 0, 0, 255)).save(
            image_buffer,
            format="PNG",
        )
        image_base64 = base64.b64encode(image_buffer.getvalue()).decode("ascii")

        image_part = await provider._resolve_image_part(f"base64://{image_base64}")

        assert image_part == {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_base64}"},
        }
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_prepare_chat_payload_materializes_context_localhost_file_uri_image_urls(
    monkeypatch,
    tmp_path,
):
    provider = _make_provider()
    try:
        temp_root = tmp_path / "temp"
        temp_root.mkdir()
        monkeypatch.setattr(
            "astrbot.core.utils.image_materializer.get_astrbot_temp_path",
            lambda: str(temp_root),
        )
        image_path = temp_root / "quoted-image.png"
        PILImage.new("RGBA", (1, 1), (255, 0, 0, 255)).save(image_path)

        localhost_uri = f"file://localhost/{image_path.as_posix()}"
        payloads, _ = await provider._prepare_chat_payload(
            prompt=None,
            contexts=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": localhost_uri,
                            },
                        },
                    ],
                }
            ],
        )

        image_payload = payloads["messages"][0]["content"][1]["image_url"]
        assert image_payload["url"].startswith("data:image/png;base64,")
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_prepare_chat_payload_drops_context_image_when_materialization_fails(
    monkeypatch,
):
    provider = _make_provider()
    try:

        async def fake_materialize(url: str) -> MaterializedImage:
            assert url == "https://example.com/expired.png"
            raise ImageMaterializationError("expired")

        monkeypatch.setattr(
            openai_source_module,
            "materialize_image_ref",
            fake_materialize,
        )

        payloads, _ = await provider._prepare_chat_payload(
            prompt=None,
            contexts=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "https://example.com/expired.png",
                            },
                        },
                    ],
                }
            ],
        )

        assert payloads["messages"][0]["content"] == [
            {"type": "text", "text": "look"},
        ]
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_apply_provider_specific_request_overrides_disables_ollama_thinking():
    provider = _make_provider(
        {
            "provider": "ollama",
            "ollama_disable_thinking": True,
        }
    )
    try:
        extra_body = {
            "reasoning": {"effort": "high"},
            "reasoning_effort": "low",
            "think": True,
            "temperature": 0.2,
        }

        provider._apply_provider_specific_request_overrides({}, extra_body)

        assert extra_body["reasoning_effort"] == "none"
        assert "reasoning" not in extra_body
        assert "think" not in extra_body
        assert extra_body["temperature"] == 0.2
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_provider_specific_request_overrides_sets_minimax_m3_max_tokens():
    provider = _make_provider({"provider": "nvidia"})
    try:
        payloads = {"model": "minimaxai/minimax-m3"}
        extra_body = {"temperature": 0.2}

        provider._apply_provider_specific_request_overrides(payloads, extra_body)

        assert payloads["max_tokens"] == 8192
        assert extra_body == {"temperature": 0.2}
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_minimax_m3_max_tokens_preserves_custom_extra_body_value():
    provider = _make_provider({"provider": "nvidia"})
    try:
        payloads = {"model": "minimaxai/minimax-m3"}
        extra_body = {"max_tokens": 4096}

        provider._apply_provider_specific_request_overrides(payloads, extra_body)

        assert "max_tokens" not in payloads
        assert extra_body["max_tokens"] == 4096
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_minimax_m3_max_tokens_preserves_standard_payload_value():
    provider = _make_provider({"provider": "nvidia"})
    try:
        payloads = {
            "model": "minimaxai/minimax-m3",
            "max_tokens": 2048,
        }
        extra_body = {}

        provider._apply_provider_specific_request_overrides(payloads, extra_body)

        assert payloads["max_tokens"] == 2048
        assert extra_body == {}
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_nvidia_request_does_not_set_max_tokens_for_other_models():
    provider = _make_provider({"provider": "nvidia"})
    try:
        payloads = {"model": "nvidia/usdcode"}
        extra_body = {}

        provider._apply_provider_specific_request_overrides(payloads, extra_body)

        assert "max_tokens" not in payloads
        assert "max_tokens" not in extra_body
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_query_injects_reasoning_effort_none_for_ollama(monkeypatch):
    provider = _make_provider(
        {
            "provider": "ollama",
            "ollama_disable_thinking": True,
            "custom_extra_body": {
                "reasoning": {"effort": "high"},
                "temperature": 0.1,
            },
        }
    )
    try:
        captured_kwargs = {}

        async def fake_create(**kwargs):
            captured_kwargs.update(kwargs)
            return ChatCompletion.model_validate(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "qwen3.5:4b",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "ok",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )

        monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)

        await provider._query(
            payloads={
                "model": "qwen3.5:4b",
                "messages": [{"role": "user", "content": "hello"}],
            },
            tools=None,
        )

        extra_body = captured_kwargs["extra_body"]
        assert extra_body["reasoning_effort"] == "none"
        assert "reasoning" not in extra_body
        assert extra_body["temperature"] == 0.1
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_query_drops_provider_only_abort_signal_from_custom_extra_body(
    monkeypatch,
):
    provider = _make_provider(
        {
            "custom_extra_body": {
                "abort_signal": asyncio.Event(),
                "vendor_flag": True,
            }
        }
    )
    try:
        captured_kwargs = {}

        async def fake_create(**kwargs):
            captured_kwargs.update(kwargs)
            return ChatCompletion.model_validate(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )

        monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)
        await provider._query(
            payloads={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hello"}],
            },
            tools=None,
        )

        assert "abort_signal" not in captured_kwargs
        assert captured_kwargs["extra_body"] == {"vendor_flag": True}
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_parse_openai_completion_raises_empty_model_output_error():
    provider = _make_provider()
    try:
        completion = ChatCompletion.model_validate(
            {
                "id": "chatcmpl-empty",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "refusal": None,
                            "tool_calls": None,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 0,
                    "total_tokens": 1,
                },
            }
        )

        with pytest.raises(EmptyModelOutputError):
            await provider._parse_openai_completion(completion, tools=None)
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_parse_openai_completion_reads_nested_data_choices():
    provider = _make_provider()
    try:
        completion = ChatCompletion.model_construct(
            id=None,
            object="chat.completion",
            created=None,
            model=None,
            choices=None,
            data={
                "id": "gen_test",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek/deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "PONG",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 38,
                    "total_tokens": 50,
                },
            },
        )

        response = await provider._parse_openai_completion(completion, tools=None)

        assert response.completion_text == "PONG"
        assert response.id == "gen_test"
        assert response.usage is not None
        assert response.usage.input_other == 12
        assert response.usage.output == 38
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_query_stream_extracts_usage_from_empty_choices_chunk(monkeypatch):
    provider = _make_provider()
    try:
        chunks = [
            ChatCompletionChunk.model_validate(
                {
                    "id": "chatcmpl-stream",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "content": "ok",
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            ),
            ChatCompletionChunk.model_validate(
                {
                    "id": "chatcmpl-stream",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ),
            ChatCompletionChunk.model_validate(
                {
                    "id": "chatcmpl-stream",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 2550,
                        "completion_tokens": 125,
                        "total_tokens": 2675,
                        "prompt_tokens_details": {
                            "cached_tokens": 2488,
                        },
                    },
                }
            ),
        ]

        async def fake_stream():
            for chunk in chunks:
                yield chunk

        async def fake_create(**kwargs):
            return fake_stream()

        monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)

        responses = [
            response
            async for response in provider._query_stream(
                payloads={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "hello"}],
                },
                tools=None,
            )
        ]

        final_response = responses[-1]
        assert final_response.completion_text == "ok"
        assert final_response.usage is not None
        assert final_response.usage.input_other == 62
        assert final_response.usage.input_cached == 2488
        assert final_response.usage.output == 125
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_query_filters_empty_assistant_message_without_tool_calls(monkeypatch):
    """Test that empty assistant messages without tool_calls are filtered out."""
    provider = _make_provider()
    try:
        captured_kwargs = {}

        async def fake_create(**kwargs):
            captured_kwargs.update(kwargs)
            return ChatCompletion.model_validate(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "ok",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )

        monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)

        payloads = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": ""},  # Should be filtered
                {"role": "user", "content": "world"},
            ],
        }

        await provider._query(payloads=payloads, tools=None)

        # The empty assistant message should be filtered out
        messages = captured_kwargs["messages"]
        assert len(messages) == 2
        assert messages[0] == {"role": "user", "content": "hello"}
        assert messages[1] == {"role": "user", "content": "world"}
    finally:
        await provider.terminate()


def test_sanitize_keeps_reasoning_only_assistant_message():
    payloads = {
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "", "reasoning_content": "thinking"},
        ],
    }

    ProviderOpenAIOfficial._sanitize_assistant_messages(payloads)

    assert payloads["messages"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "", "reasoning_content": "thinking"},
    ]


def test_sanitize_assistant_messages_removes_orphaned_tool_messages():
    payloads = {
        "messages": [
            {"role": "user", "content": "hello"},
            {
                "role": "tool",
                "tool_call_id": "missing_call",
                "content": "stale result",
            },
            {"role": "user", "content": "continue"},
        ]
    }

    ProviderOpenAIOfficial._sanitize_assistant_messages(payloads)

    assert payloads["messages"] == [
        {"role": "user", "content": "hello"},
        {"role": "user", "content": "continue"},
    ]


def test_sanitize_assistant_messages_keeps_valid_tool_messages_only():
    payloads = {
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_00",
                        "type": "function",
                        "function": {"name": "search", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_00", "content": "one"},
            {
                "role": "tool",
                "tool_call_id": "",
                "content": "empty id should not be valid",
            },
        ]
    }

    ProviderOpenAIOfficial._sanitize_assistant_messages(payloads)

    assert payloads["messages"] == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_00",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_00", "content": "one"},
    ]


def test_sanitize_assistant_messages_removes_stale_duplicate_tool_message():
    payloads = {
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_00",
                        "type": "function",
                        "function": {"name": "search", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_00", "content": "one"},
            {
                "role": "tool",
                "tool_call_id": "call_00",
                "content": "stale duplicate",
            },
            {"role": "assistant", "content": "done"},
        ]
    }

    ProviderOpenAIOfficial._sanitize_assistant_messages(payloads)

    assert payloads["messages"] == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_00",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_00", "content": "one"},
        {"role": "assistant", "content": "done"},
    ]


def test_mimo_reasoning_model_adds_empty_reasoning_content_to_assistant_history():
    provider = ProviderOpenAIOfficial.__new__(ProviderOpenAIOfficial)
    provider.client = SimpleNamespace(base_url=SimpleNamespace(host="example.com"))
    payloads = {
        "model": "mimo-v2.5-pro",
        "messages": [
            {"role": "assistant", "content": "previous reply"},
            {
                "role": "assistant",
                "content": "kept",
                "reasoning_content": "existing",
            },
        ],
    }

    provider._finally_convert_payload(payloads)

    assert payloads["messages"][0]["reasoning_content"] == ""
    assert payloads["messages"][1]["reasoning_content"] == "existing"


@pytest.mark.asyncio
async def test_query_filters_null_content_assistant_message_without_tool_calls(
    monkeypatch,
):
    """Test that assistant messages with null content and no tool_calls are filtered."""
    provider = _make_provider()
    try:
        captured_kwargs = {}

        async def fake_create(**kwargs):
            captured_kwargs.update(kwargs)
            return ChatCompletion.model_validate(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "ok",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )

        monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)

        payloads = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": None},  # Should be filtered
                {"role": "user", "content": "world"},
            ],
        }

        await provider._query(payloads=payloads, tools=None)

        # The null content assistant message should be filtered out
        messages = captured_kwargs["messages"]
        assert len(messages) == 2
        assert messages[0] == {"role": "user", "content": "hello"}
        assert messages[1] == {"role": "user", "content": "world"}
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_query_converts_empty_content_to_none_with_tool_calls(monkeypatch):
    """Test that empty content with tool_calls is converted to None (OpenAI spec)."""
    provider = _make_provider()
    try:
        captured_kwargs = {}

        async def fake_create(**kwargs):
            captured_kwargs.update(kwargs)
            return ChatCompletion.model_validate(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "ok",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )

        monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)

        payloads = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-123",
                            "type": "function",
                            "function": {"name": "test", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "user", "content": "world"},
            ],
        }

        await provider._query(payloads=payloads, tools=None)

        # The assistant message with tool_calls should be kept but content set to None
        messages = captured_kwargs["messages"]
        assert len(messages) == 3
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] is None
        assert messages[1]["tool_calls"] is not None
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_query_keeps_valid_assistant_message_with_content(monkeypatch):
    """Test that valid assistant messages with content are kept."""
    provider = _make_provider()
    try:
        captured_kwargs = {}

        async def fake_create(**kwargs):
            captured_kwargs.update(kwargs)
            return ChatCompletion.model_validate(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "ok",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )

        monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)

        payloads = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "response"},
                {"role": "user", "content": "world"},
            ],
        }

        await provider._query(payloads=payloads, tools=None)

        # All messages should be kept
        messages = captured_kwargs["messages"]
        assert len(messages) == 3
        assert messages[1] == {"role": "assistant", "content": "response"}
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_query_keeps_assistant_message_with_tool_calls_and_none_content(
    monkeypatch,
):
    """Test that assistant messages with tool_calls and None content are kept."""
    provider = _make_provider()
    try:
        captured_kwargs = {}

        async def fake_create(**kwargs):
            captured_kwargs.update(kwargs)
            return ChatCompletion.model_validate(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "ok",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )

        monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)

        payloads = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-123",
                            "type": "function",
                            "function": {"name": "test", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "user", "content": "world"},
            ],
        }

        await provider._query(payloads=payloads, tools=None)

        # The assistant message with tool_calls should be kept
        messages = captured_kwargs["messages"]
        assert len(messages) == 3
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] is None
        assert messages[1]["tool_calls"] is not None
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_query_does_not_filter_user_or_system_messages(monkeypatch):
    """Test that user and system messages are not affected by the filter."""
    provider = _make_provider()
    try:
        captured_kwargs = {}

        async def fake_create(**kwargs):
            captured_kwargs.update(kwargs)
            return ChatCompletion.model_validate(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "ok",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )

        monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)

        payloads = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": ""},  # Empty system message
                {"role": "user", "content": ""},  # Empty user message
                {"role": "assistant", "content": ""},  # Should be filtered
                {"role": "user", "content": "hello"},
            ],
        }

        await provider._query(payloads=payloads, tools=None)

        # Only assistant message should be filtered
        messages = captured_kwargs["messages"]
        assert len(messages) == 3
        assert messages[0] == {"role": "system", "content": ""}
        assert messages[1] == {"role": "user", "content": ""}
        assert messages[2] == {"role": "user", "content": "hello"}
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_query_stream_filters_empty_assistant_message(monkeypatch):
    """Regression for #7721: streaming path must also filter empty assistant messages.

    Previously only ``_query`` sanitized the payload; ``_query_stream`` forwarded
    the raw history and strict providers (e.g. DeepSeek Reasoner) returned 400 on
    the next turn after a tool call whose assistant entry had reasoning only.
    """
    provider = _make_provider()
    try:
        captured_kwargs = {}

        async def fake_stream():
            yield ChatCompletionChunk.model_validate(
                {
                    "id": "chatcmpl-stream",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "deepseek-reasoner",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            )

        async def fake_create(**kwargs):
            captured_kwargs.update(kwargs)
            return fake_stream()

        monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)

        payloads = {
            "model": "deepseek-reasoner",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": ""},  # should be filtered
                {"role": "user", "content": "world"},
            ],
        }

        async for _ in provider._query_stream(payloads=payloads, tools=None):
            pass

        messages = captured_kwargs["messages"]
        assert len(messages) == 2
        assert messages[0] == {"role": "user", "content": "hello"}
        assert messages[1] == {"role": "user", "content": "world"}
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_query_filters_empty_list_content_assistant_message(monkeypatch):
    """Empty-list content (``content == []``) must also be filtered, not just ``""`` / ``None``."""
    provider = _make_provider()
    try:
        captured_kwargs = {}

        async def fake_create(**kwargs):
            captured_kwargs.update(kwargs)
            return ChatCompletion.model_validate(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )

        monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)

        payloads = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": []},  # should be filtered
                {"role": "user", "content": "again"},
            ],
        }

        await provider._query(payloads=payloads, tools=None)

        messages = captured_kwargs["messages"]
        assert len(messages) == 2
        assert messages[0] == {"role": "user", "content": "hi"}
        assert messages[1] == {"role": "user", "content": "again"}
    finally:
        await provider.terminate()
