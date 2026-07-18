import pytest

from astrbot.core.interaction.router_agent import extract_interaction_route_payload
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
