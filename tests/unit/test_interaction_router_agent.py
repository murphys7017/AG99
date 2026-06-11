import pytest

from astrbot.core.interaction.router_agent import (
    InteractionRouterAgent,
    extract_interaction_route_payload,
)
from astrbot.core.interaction.turn_state import (
    InteractionContextMaterial,
    InteractionTurnState,
)
from astrbot.core.interaction.types import (
    FastRouteMode,
    InteractionAgentConfig,
    InteractionRouteDecision,
    RouteMode,
)
from astrbot.core.prompt.context_types import ContextPack
from astrbot.core.prompt.render.interfaces import RenderResult


def test_route_decision_accepts_self_reply_mode():
    decision = InteractionRouteDecision.from_mapping({"mode": "self_reply"})

    assert decision is not None
    assert decision.mode == FastRouteMode.SELF_REPLY


def test_route_decision_maps_legacy_delegate_to_hybrid():
    decision = InteractionRouteDecision.from_mapping(
        {"route_mode": RouteMode.DELEGATE_TO_CORE.value}
    )

    assert decision is not None
    assert decision.mode == FastRouteMode.HYBRID


def test_route_decision_rejects_invalid_payload():
    assert InteractionRouteDecision.from_mapping({"mode": "maybe"}) is None


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ('{"mode":"self_reply"}', "self_reply"),
        ("hybrid", "hybrid"),
        ('"self_reply"', "self_reply"),
    ],
)
def test_extract_route_payload_accepts_json_and_plain_mode(text, mode):
    assert extract_interaction_route_payload(text) == {"mode": mode}


def test_route_decision_to_legacy_interaction_decision_omits_core_task_spec():
    decision = InteractionRouteDecision(mode=FastRouteMode.HYBRID)

    legacy = decision.to_interaction_decision(first_response="我先看看。")

    assert legacy.route_mode == RouteMode.HYBRID
    assert legacy.should_emit_immediate_reply is True
    assert legacy.immediate_spoken_reply == "我先看看。"
    assert legacy.core_task_spec is None


@pytest.mark.asyncio
async def test_router_render_uses_scoped_provider_and_restores_event_provider(
    monkeypatch,
):
    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"

        def __init__(self):
            self._extras = {
                "provider": "outer-provider",
                "_interaction_turn_state": InteractionTurnState(
                    turn_id="turn-1",
                    context_material=InteractionContextMaterial(
                        prompt_context_pack=ContextPack(),
                        persona_payload={"persona_id": "alice"},
                        capability_payload={},
                        decision_context={},
                        prompt_extensions_collected=True,
                    ),
                ),
            }

        def get_extra(self, key=None, default=None):
            if key is None:
                return self._extras
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

    class Provider:
        pass

    seen_providers = []

    class RenderEngine:
        def render(self, pack, *, event, **kwargs):
            seen_providers.append(event.get_extra("provider"))
            return RenderResult(messages=[], system_prompt="")

    event = Event()
    provider = Provider()
    plugin_context = type(
        "PluginContext",
        (),
        {
            "get_config": lambda self, umo=None: {},
            "list_interaction_prompt_contributors": lambda self: [],
        },
    )()
    agent = InteractionRouterAgent(memory_store=None)

    monkeypatch.setattr(
        "astrbot.core.interaction.router_agent.Provider",
        Provider,
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.router_agent.PromptRenderEngine",
        lambda: RenderEngine(),
    )

    await agent._prepare_render_result(
        event,
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(),
        provider=provider,
    )

    assert seen_providers == [provider]
    assert event.get_extra("provider") == "outer-provider"
