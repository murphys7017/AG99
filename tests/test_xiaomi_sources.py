import pytest

import astrbot.core.provider.sources.anthropic_source as anthropic_source
from astrbot.core.config.default import CONFIG_METADATA_2
from astrbot.core.provider.register import provider_cls_map
from astrbot.core.provider.sources.minimax_token_plan_source import (
    MINIMAX_TOKEN_PLAN_MODELS,
    ProviderMiniMaxTokenPlan,
)
from astrbot.core.provider.sources.xiaomi_source import XIAOMI_MODELS, ProviderXiaomi
from astrbot.core.provider.sources.xiaomi_token_plan_source import (
    XIAOMI_TOKEN_PLAN_MODELS,
    ProviderXiaomiTokenPlan,
)


class _FakeAsyncAnthropic:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def close(self):
        return None


def test_xiaomi_templates_exist():
    templates = CONFIG_METADATA_2["provider_group"]["metadata"]["provider"][
        "config_template"
    ]

    xiaomi = templates["Xiaomi"]
    assert xiaomi["type"] == "xiaomi_chat_completion"
    assert xiaomi["provider"] == "xiaomi"
    assert xiaomi["api_base"] == "https://api.xiaomimimo.com/v1"

    token_plan = templates["Xiaomi Token Plan"]
    assert token_plan["type"] == "xiaomi_token_plan"
    assert token_plan["provider"] == "xiaomi-token-plan"
    assert token_plan["api_base"] == "https://token-plan-cn.xiaomimimo.com/anthropic"
    assert token_plan["anth_thinking_config"] == {
        "type": "",
        "budget": 0,
        "effort": "",
    }


def test_xiaomi_provider_registration_metadata():
    assert provider_cls_map["xiaomi_chat_completion"].cls_type is ProviderXiaomi
    assert provider_cls_map["xiaomi_chat_completion"].prompt_renderer_family == "openai"
    assert (
        provider_cls_map["xiaomi_token_plan"].cls_type is ProviderXiaomiTokenPlan
    )
    assert (
        provider_cls_map["xiaomi_token_plan"].prompt_renderer_family == "anthropic"
    )


@pytest.mark.asyncio
async def test_xiaomi_openai_provider_uses_default_endpoint_and_models(monkeypatch):
    async def _fail_get_models(self):
        raise RuntimeError("models endpoint unavailable")

    monkeypatch.setattr(
        "astrbot.core.provider.sources.openai_source.ProviderOpenAIOfficial.get_models",
        _fail_get_models,
    )

    provider = ProviderXiaomi(
        {
            "id": "xiaomi",
            "type": "xiaomi_chat_completion",
            "model": "mimo-v2.5-pro",
            "key": ["test-key"],
            "api_base": "",
        },
        {},
    )

    try:
        assert provider.provider_config["api_base"] == "https://api.xiaomimimo.com/v1"
        assert provider.get_model() == "mimo-v2.5-pro"
        assert await provider.get_models() == XIAOMI_MODELS
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_xiaomi_openai_provider_prefers_remote_models(monkeypatch):
    async def _remote_get_models(self):
        return ["remote-mimo"]

    monkeypatch.setattr(
        "astrbot.core.provider.sources.openai_source.ProviderOpenAIOfficial.get_models",
        _remote_get_models,
    )

    provider = ProviderXiaomi(
        {
            "id": "xiaomi",
            "type": "xiaomi_chat_completion",
            "model": "mimo-v2.5",
            "key": ["test-key"],
        },
        {},
    )

    try:
        assert await provider.get_models() == ["remote-mimo"]
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_xiaomi_token_plan_sets_bearer_header_and_models(monkeypatch):
    monkeypatch.setattr(anthropic_source, "AsyncAnthropic", _FakeAsyncAnthropic)

    provider = ProviderXiaomiTokenPlan(
        {
            "id": "xiaomi-token-plan",
            "type": "xiaomi_token_plan",
            "model": "mimo-v2.5",
            "key": ["token-1"],
            "custom_headers": {"User-Agent": "custom-agent"},
        },
        {},
    )

    assert provider.base_url == "https://token-plan-cn.xiaomimimo.com/anthropic"
    assert provider.get_model() == "mimo-v2.5"
    assert provider.custom_headers == {
        "Authorization": "Bearer token-1",
        "User-Agent": "custom-agent",
    }
    assert provider.client.kwargs["default_headers"] == {
        "Authorization": "Bearer token-1",
        "User-Agent": "custom-agent",
    }
    assert await provider.get_models() == XIAOMI_TOKEN_PLAN_MODELS


@pytest.mark.asyncio
async def test_minimax_token_plan_defaults_to_m3_and_fetches_models(monkeypatch):
    monkeypatch.setattr(anthropic_source, "AsyncAnthropic", _FakeAsyncAnthropic)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "MiniMax-M3"}, {"id": "MiniMax-M3-highspeed"}]}

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers, timeout):
            assert url == "https://api.minimaxi.com/v1/models"
            assert headers == {"Authorization": "Bearer token-1"}
            assert timeout == 10
            return FakeResponse()

    monkeypatch.setattr(
        "astrbot.core.provider.sources.minimax_token_plan_source.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = ProviderMiniMaxTokenPlan(
        {
            "id": "minimax-token-plan",
            "type": "minimax_token_plan",
            "key": ["token-1"],
        },
        {},
    )

    assert provider.get_model() == "MiniMax-M3"
    assert await provider.get_models() == ["MiniMax-M3", "MiniMax-M3-highspeed"]


@pytest.mark.asyncio
async def test_minimax_token_plan_model_fetch_falls_back(monkeypatch):
    monkeypatch.setattr(anthropic_source, "AsyncAnthropic", _FakeAsyncAnthropic)

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers, timeout):
            raise RuntimeError("network unavailable")

    monkeypatch.setattr(
        "astrbot.core.provider.sources.minimax_token_plan_source.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = ProviderMiniMaxTokenPlan(
        {
            "id": "minimax-token-plan",
            "type": "minimax_token_plan",
            "model": "MiniMax-M2.7",
            "key": ["token-1"],
        },
        {},
    )

    assert await provider.get_models() == MINIMAX_TOKEN_PLAN_MODELS
