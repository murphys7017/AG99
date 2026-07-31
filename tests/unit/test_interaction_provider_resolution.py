import pytest

from astrbot.core.interaction.provider_resolution import (
    resolve_interaction_chat_provider,
)


@pytest.mark.asyncio
async def test_empty_interaction_provider_override_reuses_session_provider(monkeypatch):
    class Provider:
        pass

    class Event:
        unified_msg_origin = "test:friend:session"

    class Context:
        async def get_current_chat_provider_id(self, umo):
            assert umo == Event.unified_msg_origin
            return "session-provider"

        def get_provider_by_id(self, provider_id):
            assert provider_id == "session-provider"
            return Provider()

    provider, provider_id = await resolve_interaction_chat_provider(
        Event(),
        Context(),
        "",
    )

    assert isinstance(provider, Provider)
    assert provider_id == "session-provider"


@pytest.mark.asyncio
async def test_explicit_interaction_provider_override_takes_precedence(monkeypatch):
    class Provider:
        pass

    class Event:
        unified_msg_origin = "test:friend:session"

    class Context:
        async def get_current_chat_provider_id(self, _umo):
            raise AssertionError("explicit provider must not resolve the session default")

        def get_provider_by_id(self, provider_id):
            assert provider_id == "expression-provider"
            return Provider()

    provider, provider_id = await resolve_interaction_chat_provider(
        Event(),
        Context(),
        "expression-provider",
    )

    assert isinstance(provider, Provider)
    assert provider_id == "expression-provider"
