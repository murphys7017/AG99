import pytest

from astrbot.core.interaction.router_agent import (
    build_interaction_router_prompt,
    build_interaction_router_system_prompt,
    extract_interaction_route_payload,
)
from astrbot.core.interaction.types import (
    InteractionRouteDecision,
    InteractionRouteMode,
)


def test_route_decision_accepts_persona_mode():
    decision = InteractionRouteDecision.from_mapping({"mode": "persona"})

    assert decision is not None
    assert decision.route_mode == InteractionRouteMode.PERSONA


def test_route_decision_rejects_delegate_mode_from_router_payload():
    decision = InteractionRouteDecision.from_mapping({"route_mode": "delegate_to_core"})

    assert decision is None


def test_route_decision_rejects_invalid_payload():
    assert InteractionRouteDecision.from_mapping({"mode": "maybe"}) is None


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ('{"mode":"silent"}', "silent"),
        ('{"mode":"persona"}', "persona"),
        ("hybrid", "hybrid"),
        ('"persona"', "persona"),
    ],
)
def test_extract_route_payload_accepts_json_and_plain_mode(text, mode):
    assert extract_interaction_route_payload(text) == {"mode": mode}


def test_extract_route_payload_rejects_legacy_self_reply_mode():
    assert extract_interaction_route_payload("self_reply") is None


def test_router_exposes_silent_only_for_group_reply_candidates():
    default_system_prompt = build_interaction_router_system_prompt()
    default_request_prompt = build_interaction_router_prompt()
    continuation_system_prompt = build_interaction_router_system_prompt(
        allow_silent=True
    )
    continuation_request_prompt = build_interaction_router_prompt(
        allow_silent=True
    )

    assert "silent" not in default_system_prompt
    assert "silent" not in default_request_prompt
    assert "插件目录" not in default_system_prompt
    assert "- silent" in continuation_system_prompt
    assert "在 persona、hybrid 与 silent 之间不确定时选择 silent" in (
        continuation_system_prompt
    )
    assert "silent" in continuation_request_prompt


def test_router_treats_plugin_reply_requests_as_silence_capable_candidates():
    system_prompt = build_interaction_router_system_prompt(
        group_candidate_kind="plugin"
    )
    request_prompt = build_interaction_router_prompt(group_candidate_kind="plugin")

    assert "插件判断只表示消息值得评估，不表示必须回复" in system_prompt
    assert "重复呼唤" in system_prompt
    assert "silent" in request_prompt
