import asyncio
from types import SimpleNamespace

from astrbot.core.output_contract import OutputContract
from astrbot.core.prompt.context_types import ContextPack
from astrbot.core.prompt.render import PromptRenderEngine
from astrbot.core.provider.sources.deepseek_source import ProviderDeepSeek


def _make_provider(overrides: dict | None = None) -> ProviderDeepSeek:
    provider_config = {
        "id": "test-deepseek",
        "type": "deepseek_chat_completion",
        "model": "deepseek-v4-flash",
        "key": ["test-key"],
        "custom_extra_body": {},
    }
    if overrides:
        provider_config.update(overrides)
    return ProviderDeepSeek(
        provider_config=provider_config,
        provider_settings={},
    )


def test_deepseek_uses_protocol_tool_call_output_contract():
    pack = ContextPack(slots={})
    pack.meta["output_contract"] = OutputContract(
        mode="tool_call",
        strict=True,
        schema={"type": "object", "properties": {}},
        preferred_tool_name="persona_expression",
        allow_text_fallback=False,
    ).to_dict()

    result = PromptRenderEngine().render(
        pack,
        provider_request=type(
            "RequestStub",
            (),
            {"provider_type": "deepseek_chat_completion"},
        )(),
    )

    assert result.metadata["renderer_name"] == "openai"
    assert result.compiled_output_contract is not None
    assert result.compiled_output_contract.strategy == "protocol_tool_call"
    assert result.compiled_output_contract.tool_name == "persona_expression"
    assert result.compiled_output_contract.degraded is False


def test_deepseek_thinking_mode_keeps_tool_choice():
    provider = _make_provider(
        {
            "custom_extra_body": {
                "thinking": {"type": "enabled"},
                "tool_choice": "required",
            }
        }
    )
    try:
        payloads = {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": "hello"}],
            "tool_choice": "required",
        }

        normalized_payloads, extra_body, _ = provider._prepare_request(payloads, None)

        assert normalized_payloads["tool_choice"] == "required"
        assert "tool_choice" not in extra_body
        assert extra_body["thinking"]["type"] == "enabled"
    finally:
        asyncio.run(provider.terminate())


def test_deepseek_non_thinking_mode_keeps_tool_choice():
    provider = _make_provider(
        {
            "custom_extra_body": {
                "thinking": {"type": "disabled"},
            }
        }
    )
    try:
        payloads = {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": "hello"}],
            "tool_choice": "required",
        }

        normalized_payloads, extra_body, _ = provider._prepare_request(payloads, None)

        assert normalized_payloads["tool_choice"] == "required"
        assert extra_body["thinking"]["type"] == "disabled"
    finally:
        asyncio.run(provider.terminate())


def test_deepseek_default_thinking_mode_keeps_tool_choice():
    provider = _make_provider()
    try:
        payloads = {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": "hello"}],
            "tool_choice": "required",
        }

        normalized_payloads, extra_body, _ = provider._prepare_request(payloads, None)

        assert provider._is_thinking_enabled(normalized_payloads, extra_body) is True
        assert normalized_payloads["tool_choice"] == "required"
        assert "tool_choice" not in extra_body
    finally:
        asyncio.run(provider.terminate())


def test_deepseek_non_thinking_payload_does_not_inject_empty_reasoning_content():
    provider = ProviderDeepSeek.__new__(ProviderDeepSeek)
    provider.provider_config = {
        "custom_extra_body": {
            "thinking": {"type": "disabled"},
        }
    }
    provider.client = SimpleNamespace(base_url=SimpleNamespace(host="api.deepseek.com"))

    payloads = {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "assistant", "content": "previous reply"}],
    }

    provider._finally_convert_payload(payloads)

    assert "reasoning_content" not in payloads["messages"][0]


def test_deepseek_non_thinking_payload_removes_existing_reasoning_content():
    provider = ProviderDeepSeek.__new__(ProviderDeepSeek)
    provider.provider_config = {
        "custom_extra_body": {
            "thinking": {"type": "disabled"},
        }
    }
    provider.client = SimpleNamespace(base_url=SimpleNamespace(host="api.deepseek.com"))

    payloads = {
        "model": "deepseek-v4-flash",
        "messages": [
            {
                "role": "assistant",
                "content": "previous reply",
                "reasoning_content": "old thinking",
            }
        ],
    }

    provider._finally_convert_payload(payloads)

    assert payloads["messages"][0]["content"] == "previous reply"
    assert "reasoning_content" not in payloads["messages"][0]


def test_deepseek_thinking_payload_keeps_empty_reasoning_content_for_history():
    provider = ProviderDeepSeek.__new__(ProviderDeepSeek)
    provider.provider_config = {
        "custom_extra_body": {
            "thinking": {"type": "enabled"},
        }
    }
    provider.client = SimpleNamespace(base_url=SimpleNamespace(host="api.deepseek.com"))

    payloads = {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "assistant", "content": "previous reply"}],
    }

    provider._finally_convert_payload(payloads)

    assert payloads["messages"][0]["reasoning_content"] == ""


def test_deepseek_thinking_tool_call_preserves_reasoning_content_for_next_request():
    provider = ProviderDeepSeek.__new__(ProviderDeepSeek)
    provider.provider_config = {
        "custom_extra_body": {
            "thinking": {"type": "enabled"},
        }
    }
    provider.client = SimpleNamespace(base_url=SimpleNamespace(host="api.deepseek.com"))

    payloads = {
        "model": "deepseek-v4-flash",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "think", "think": "I should call the tool."},
                ],
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "demo_tool",
                            "arguments": "{}",
                        },
                    }
                ],
            }
        ],
    }

    provider._finally_convert_payload(payloads)

    assistant = payloads["messages"][0]
    assert assistant["reasoning_content"] == "I should call the tool."
    assert assistant["content"] is None
    assert assistant["tool_calls"][0]["function"]["name"] == "demo_tool"
