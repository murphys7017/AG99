import pytest

from astrbot.core.interaction.router_agent import (
    InteractionRouterAgent,
    extract_interaction_route_payload,
)
from astrbot.core.interaction.effects import PersonaEffectCall
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
from astrbot.core.prompt.extensions import PromptExtension
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


def test_route_decision_keeps_selected_persona_plugin_hints():
    decision = InteractionRouteDecision(mode=FastRouteMode.SELF_REPLY)

    selected = decision.to_interaction_decision(
        first_response="嗯。",
        plugin_hints={"ag99live_motion": {"emotion_label": "happy"}},
    )

    assert selected.plugin_hints == {
        "ag99live_motion": {"emotion_label": "happy"}
    }


def test_route_decision_keeps_selected_persona_effect_calls():
    decision = InteractionRouteDecision(mode=FastRouteMode.SELF_REPLY)
    effect_call = PersonaEffectCall(
        name="ag99live.motion",
        arguments={"axes": {"head_yaw": 40}},
        plugin_id="plugin_a",
    )

    selected = decision.to_interaction_decision(
        first_response="嗯。",
        effect_calls=[effect_call],
    )

    assert selected.effect_calls == [effect_call]


class PurposeAwarePromptContributor:
    plugin_id = "ag99live.motion"

    def __init__(self):
        self.views = []

    async def collect(self, event, plugin_context, view):
        self.views.append(view)
        if view.purpose == "persona_reply":
            return PromptExtension(
                plugin_id=self.plugin_id,
                mount="capability",
                title="AG99live Motion Prompt",
                value={"ag99live_motion": {"enabled": True}},
                order=10,
                meta={"scope": "static", "node_type": "ag99live_motion_prompt"},
            )
        return []


@pytest.mark.asyncio
async def test_router_render_uses_scoped_provider_and_restores_event_provider(
    monkeypatch,
):
    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"
        message_str = "hello"
        message_obj = type("Message", (), {"message": []})()

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

        def get_platform_name(self):
            return "webchat"

    class Provider:
        pass

    seen_providers = []

    class RenderEngine:
        def render(self, pack, *, event, **kwargs):
            seen_providers.append(kwargs["provider_request"].provider)
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


@pytest.mark.asyncio
async def test_router_prompt_excludes_persona_only_prompt_extensions(monkeypatch):
    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"
        message_str = "hello"
        message_obj = type("Message", (), {"message": []})()

        def __init__(self):
            self._extras = {
                "_interaction_turn_state": InteractionTurnState(
                    turn_id="turn-1",
                    context_material=InteractionContextMaterial(
                        prompt_context_pack=ContextPack(),
                        persona_payload={"persona_id": "alice"},
                        capability_payload={},
                        decision_context={},
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

        def get_platform_name(self):
            return "webchat"

    class Provider:
        pass

    contributor = PurposeAwarePromptContributor()

    class RenderEngine:
        def render(self, pack, *, event, **kwargs):
            capability_slot = pack.get_slot("extension.capability")
            titles = []
            if capability_slot is not None and isinstance(capability_slot.value, dict):
                titles = [item["title"] for item in capability_slot.value["items"]]
            return RenderResult(messages=[], system_prompt="\n".join(titles))

    event = Event()
    provider = Provider()
    plugin_context = type(
        "PluginContext",
        (),
        {
            "get_config": lambda self, umo=None: {},
            "list_interaction_prompt_contributors": lambda self: [contributor],
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

    render_result = await agent._prepare_render_result(
        event,
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(),
        provider=provider,
    )

    assert contributor.views[0].purpose == "router"
    assert contributor.views[0].phase == "route"
    assert contributor.views[0].persona == {}
    assert contributor.views[0].interaction_memory == {}
    assert contributor.views[0].capabilities == {}
    assert contributor.views[0].input["text"] == "hello"
    assert "AG99live Motion Prompt" not in render_result.system_prompt
