import builtins

import pytest

import astrbot.core.provider.sources.anthropic_source as anthropic_source
import astrbot.core.provider.sources.kimi_code_source as kimi_code_source
from astrbot.core.exceptions import EmptyModelOutputError
from astrbot.core.output_contract import CompiledOutputContract, OutputContract
from astrbot.core.provider.entities import LLMResponse


class _FakeAsyncAnthropic:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def close(self):
        return None


def test_anthropic_provider_passes_custom_headers_via_default_headers(monkeypatch):
    monkeypatch.setattr(anthropic_source, "AsyncAnthropic", _FakeAsyncAnthropic)

    provider = anthropic_source.ProviderAnthropic(
        provider_config={
            "id": "anthropic-test",
            "type": "anthropic_chat_completion",
            "model": "claude-test",
            "key": ["test-key"],
            "custom_headers": {
                "User-Agent": "custom-agent/1.0",
                "X-Test-Header": 123,
            },
        },
        provider_settings={},
    )

    assert provider.custom_headers == {
        "User-Agent": "custom-agent/1.0",
        "X-Test-Header": "123",
    }
    # Custom headers are forwarded via the SDK's `default_headers` parameter,
    # not via a custom http_client (which is reserved for proxy configuration).
    assert provider.client.kwargs["default_headers"] == {
        "User-Agent": "custom-agent/1.0",
        "X-Test-Header": "123",
    }
    assert provider.client.kwargs["http_client"] is None


def test_kimi_code_provider_sets_defaults_and_preserves_custom_headers(monkeypatch):
    monkeypatch.setattr(anthropic_source, "AsyncAnthropic", _FakeAsyncAnthropic)

    provider = kimi_code_source.ProviderKimiCode(
        provider_config={
            "id": "kimi-code",
            "type": "kimi_code_chat_completion",
            "key": ["test-key"],
            "custom_headers": {"X-Trace-Id": "trace-1"},
        },
        provider_settings={},
    )

    assert provider.base_url == kimi_code_source.KIMI_CODE_API_BASE
    assert provider.get_model() == kimi_code_source.KIMI_CODE_DEFAULT_MODEL
    assert provider.custom_headers == {
        "User-Agent": kimi_code_source.KIMI_CODE_USER_AGENT,
        "X-Trace-Id": "trace-1",
    }
    assert provider.client.kwargs["default_headers"] == {
        "User-Agent": kimi_code_source.KIMI_CODE_USER_AGENT,
        "X-Trace-Id": "trace-1",
    }


def test_kimi_code_provider_restores_required_user_agent_when_blank(monkeypatch):
    monkeypatch.setattr(anthropic_source, "AsyncAnthropic", _FakeAsyncAnthropic)

    provider = kimi_code_source.ProviderKimiCode(
        provider_config={
            "id": "kimi-code",
            "type": "kimi_code_chat_completion",
            "key": ["test-key"],
            "custom_headers": {"User-Agent": "   "},
        },
        provider_settings={},
    )

    assert provider.custom_headers == {
        "User-Agent": kimi_code_source.KIMI_CODE_USER_AGENT,
    }


def test_create_http_client_returns_none_when_no_proxy(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("create_proxy_client should not be called without a proxy")

    monkeypatch.setattr(anthropic_source, "create_proxy_client", fail_if_called)

    provider = anthropic_source.ProviderAnthropic.__new__(
        anthropic_source.ProviderAnthropic
    )
    provider.custom_headers = {"X-Trace-Id": "abc"}

    assert provider._create_http_client({"proxy": ""}) is None


def test_create_http_client_uses_anthropic_httpx_module(monkeypatch):
    captured: dict[str, object] = {}

    def fake_create_proxy_client(
        provider_label: str,
        proxy: str | None = None,
        headers: dict[str, str] | None = None,
        verify=None,
        httpx_module=None,
    ):
        captured["provider_label"] = provider_label
        captured["proxy"] = proxy
        captured["headers"] = headers
        captured["httpx_module"] = httpx_module
        return object()

    monkeypatch.setattr(
        anthropic_source, "create_proxy_client", fake_create_proxy_client
    )

    provider = anthropic_source.ProviderAnthropic.__new__(
        anthropic_source.ProviderAnthropic
    )
    provider.custom_headers = {"X-Trace-Id": "trace-1"}
    provider._create_http_client({"proxy": "http://127.0.0.1:7890"})

    from anthropic import _base_client as anthropic_base_client

    assert captured["provider_label"] == "Anthropic"
    assert captured["proxy"] == "http://127.0.0.1:7890"
    assert captured["headers"] == {"X-Trace-Id": "trace-1"}
    assert captured["httpx_module"] is anthropic_base_client.httpx


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
        if name == "anthropic" and fromlist:
            raise ImportError("missing anthropic._base_client")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(
        anthropic_source, "create_proxy_client", fake_create_proxy_client
    )
    monkeypatch.setattr(builtins, "__import__", fake_import)

    provider = anthropic_source.ProviderAnthropic.__new__(
        anthropic_source.ProviderAnthropic
    )
    provider.custom_headers = None
    provider._create_http_client({"proxy": "http://127.0.0.1:7890"})

    assert captured["httpx_module"] is anthropic_source.httpx


@pytest.mark.asyncio
async def test_text_chat_wraps_string_system_prompt_as_list(monkeypatch):
    monkeypatch.setattr(anthropic_source, "AsyncAnthropic", _FakeAsyncAnthropic)

    provider = anthropic_source.ProviderAnthropic(
        provider_config={
            "id": "anthropic-test",
            "type": "anthropic_chat_completion",
            "model": "claude-test",
            "key": ["test-key"],
        },
        provider_settings={},
    )

    captured_payloads: dict[str, object] = {}

    async def fake_query(payloads, tools):
        captured_payloads.update(payloads)
        return LLMResponse(role="assistant", completion_text="ok")

    monkeypatch.setattr(provider, "_query", fake_query)

    await provider.text_chat(prompt="hello", system_prompt="You are helpful.")

    assert captured_payloads["system"] == [{"type": "text", "text": "You are helpful."}]


@pytest.mark.asyncio
async def test_text_chat_passes_through_list_system_prompt(monkeypatch):
    monkeypatch.setattr(anthropic_source, "AsyncAnthropic", _FakeAsyncAnthropic)

    provider = anthropic_source.ProviderAnthropic(
        provider_config={
            "id": "anthropic-test",
            "type": "anthropic_chat_completion",
            "model": "claude-test",
            "key": ["test-key"],
        },
        provider_settings={},
    )

    captured_payloads: dict[str, object] = {}

    async def fake_query(payloads, tools):
        captured_payloads.update(payloads)
        return LLMResponse(role="assistant", completion_text="ok")

    monkeypatch.setattr(provider, "_query", fake_query)

    structured_system = [
        {"type": "text", "text": "Persona block."},
        {"type": "text", "text": "Style guide."},
    ]
    await provider.text_chat(prompt="hello", system_prompt=structured_system)

    assert captured_payloads["system"] == structured_system


def test_anthropic_empty_output_raises_empty_model_output_error():
    llm_response = LLMResponse(role="assistant")

    with pytest.raises(EmptyModelOutputError):
        anthropic_source.ProviderAnthropic._ensure_usable_response(
            llm_response,
            completion_id="msg_empty",
            stop_reason="end_turn",
        )


def _make_anthropic_provider_for_payload_tests() -> anthropic_source.ProviderAnthropic:
    return anthropic_source.ProviderAnthropic(
        provider_config={"model": "claude-test"},
        provider_settings={},
        use_api_key=False,
    )


def test_prepare_payload_merges_consecutive_tool_results_into_single_user_message():
    provider = _make_anthropic_provider_for_payload_tests()

    _, new_messages = provider._prepare_payload(
        [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Reading files"}],
                "tool_calls": [
                    {
                        "type": "function",
                        "id": "call_00",
                        "function": {
                            "name": "astrbot_file_read_tool",
                            "arguments": '{"path": "/tmp/one.txt"}',
                        },
                    },
                    {
                        "type": "function",
                        "id": "call_01",
                        "function": {
                            "name": "astrbot_file_read_tool",
                            "arguments": '{"path": "/tmp/two.txt"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_00",
                "content": "one",
            },
            {
                "role": "tool",
                "tool_call_id": "call_01",
                "content": "two",
            },
        ]
    )

    assert len(new_messages) == 2
    assert new_messages[1]["role"] == "user"
    assert new_messages[1]["content"] == [
        {"type": "tool_result", "tool_use_id": "call_00", "content": "one"},
        {"type": "tool_result", "tool_use_id": "call_01", "content": "two"},
    ]


def test_prepare_payload_converts_local_file_image_url_to_anthropic_image(tmp_path):
    provider = _make_anthropic_provider_for_payload_tests()
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
    )

    _, new_messages = provider._prepare_payload(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_path.as_uri()},
                    },
                ],
            }
        ]
    )

    assert new_messages[0]["content"][0] == {"type": "text", "text": "look"}
    image_block = new_messages[0]["content"][1]
    assert image_block["type"] == "image"
    assert image_block["source"]["type"] == "base64"
    assert image_block["source"]["media_type"] == "image/png"
    assert image_block["source"]["data"]


def test_prepare_payload_keeps_single_tool_result_as_single_user_message():
    provider = _make_anthropic_provider_for_payload_tests()

    _, new_messages = provider._prepare_payload(
        [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Reading file"}],
                "tool_calls": [
                    {
                        "type": "function",
                        "id": "call_00",
                        "function": {
                            "name": "astrbot_file_read_tool",
                            "arguments": '{"path": "/tmp/one.txt"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_00",
                "content": "one",
            },
        ]
    )

    assert len(new_messages) == 2
    assert new_messages[1] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "call_00", "content": "one"}
        ],
    }


def test_prepare_payload_does_not_merge_non_consecutive_tool_results():
    provider = _make_anthropic_provider_for_payload_tests()

    _, new_messages = provider._prepare_payload(
        [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "First tool"}],
                "tool_calls": [
                    {
                        "type": "function",
                        "id": "call_00",
                        "function": {
                            "name": "astrbot_file_read_tool",
                            "arguments": '{"path": "/tmp/one.txt"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_00",
                "content": "one",
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Second tool"}],
                "tool_calls": [
                    {
                        "type": "function",
                        "id": "call_01",
                        "function": {
                            "name": "astrbot_file_read_tool",
                            "arguments": '{"path": "/tmp/two.txt"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_01",
                "content": "two",
            },
        ]
    )

    assert new_messages == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "First tool"},
                {
                    "type": "tool_use",
                    "name": "astrbot_file_read_tool",
                    "input": {"path": "/tmp/one.txt"},
                    "id": "call_00",
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_00", "content": "one"}
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Second tool"},
                {
                    "type": "tool_use",
                    "name": "astrbot_file_read_tool",
                    "input": {"path": "/tmp/two.txt"},
                    "id": "call_01",
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_01", "content": "two"}
            ],
        },
    ]


def test_anthropic_output_contract_upgrades_strict_json_to_required_tool_call():
    output_contract = OutputContract(
        mode="json_object",
        strict=True,
        schema={"type": "object", "properties": {"value": {"type": "string"}}},
        preferred_tool_name="interaction_decision",
        allow_text_fallback=False,
    )

    func_tool, tool_choice = anthropic_source.ProviderAnthropic._resolve_output_contract(
        output_contract,
        None,
        None,
        "auto",
    )

    assert func_tool is not None
    assert func_tool.get_tool("interaction_decision") is not None
    assert tool_choice == "required"


def test_anthropic_extract_usage_accepts_none():
    provider = anthropic_source.ProviderAnthropic(
        provider_config={"model": "claude-test"},
        provider_settings={},
        use_api_key=False,
    )

    usage = provider._extract_usage(None)

    assert usage.input_other == 0
    assert usage.input_cached == 0
    assert usage.output == 0
    assert usage.total == 0


def test_anthropic_output_contract_prefers_compiled_binding():
    compiled_output_contract = CompiledOutputContract(
        contract=OutputContract(
            mode="tool_call",
            strict=True,
            schema={"type": "object", "properties": {"value": {"type": "string"}}},
            preferred_tool_name="interaction_decision",
            allow_text_fallback=False,
        ),
        strategy="protocol_tool_call",
        tool_name="interaction_decision",
        tool_schema={"type": "object", "properties": {"value": {"type": "string"}}},
    )

    func_tool, tool_choice = anthropic_source.ProviderAnthropic._resolve_output_contract(
        None,
        compiled_output_contract,
        None,
        "auto",
    )

    assert func_tool is not None
    assert func_tool.get_tool("interaction_decision") is not None
    assert tool_choice == "required"


def test_provider_requires_compiled_output_contract_for_structured_modes():
    provider = anthropic_source.ProviderAnthropic(
        provider_config={"model": "claude-test"},
        provider_settings={},
        use_api_key=False,
    )
    output_contract = OutputContract(
        mode="tool_call",
        strict=True,
        schema={"type": "object", "properties": {"value": {"type": "string"}}},
        preferred_tool_name="interaction_decision",
        allow_text_fallback=False,
    )

    with pytest.raises(ValueError, match="compiled_output_contract_required"):
        provider.ensure_output_contract_supported(output_contract=output_contract)


def test_anthropic_normalize_tool_choice_values():
    normalize = anthropic_source.ProviderAnthropic._normalize_tool_choice

    assert normalize("required") == {"type": "any"}
    assert normalize("auto") == {"type": "auto"}
    assert normalize("any") == {"type": "any"}
    assert normalize("none") == {"type": "none"}
    assert normalize({"type": "tool", "name": "search"}) == {
        "type": "tool",
        "name": "search",
    }
    assert normalize("tool") == {"type": "auto"}
    assert normalize("unknown") == {"type": "auto"}
