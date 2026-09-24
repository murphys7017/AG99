from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrbot.core.interaction.turn_state import (
    ensure_interaction_turn_state,
    freeze_interaction_turn_admission_snapshot,
    set_interaction_turn_configuration_selection,
    set_interaction_turn_persona_id,
)
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.plugin_admission import PluginAdmissionSnapshot
from astrbot.core.umop_config_router import UmopConfigRouter


def _event(*, extras=None, umo="aki:FriendMessage:desktop-client"):
    event = MagicMock()
    values = dict(extras or {})
    event.unified_msg_origin = umo
    event.get_extra.side_effect = lambda key, default=None: values.get(key, default)
    event.set_extra.side_effect = values.__setitem__
    return event


def test_turn_admission_snapshot_freezes_once_and_reflects_presence():
    event = _event(extras={"_astrbot_config_id": "config-a"})
    state = ensure_interaction_turn_state(event, turn_id="turn-1")
    state.runtime_config_snapshot = {"interaction_middleware": {}}
    state.plugin_admission = PluginAdmissionSnapshot(session_id="umo")

    first = freeze_interaction_turn_admission_snapshot(event)
    second = freeze_interaction_turn_admission_snapshot(event)

    assert first is second
    assert first.turn_id == "turn-1"
    assert first.config_id == "config-a"
    assert first.persona_id_at_admission is None
    assert first.has_runtime_config is True
    assert first.has_plugin_admission is True

    set_interaction_turn_persona_id(event, "persona-a")
    assert freeze_interaction_turn_admission_snapshot(event) is first
    assert first.persona_id_at_admission is None
    assert state.persona_id == "persona-a"


@pytest.mark.asyncio
async def test_scheduler_preserves_selected_turn_configuration_projection():
    event = _event()
    event.requires_visible_turn_completion.return_value = False
    selected_config = {"provider_settings": {"enable": False}}
    set_interaction_turn_configuration_selection(
        event,
        config_id="selected",
        runtime_config=selected_config,
        adapter_binding_id="selected-binding",
        provider_references={},
    )
    scheduler = PipelineScheduler.__new__(PipelineScheduler)
    scheduler.ctx = SimpleNamespace(
        astrbot_config={"provider_settings": {"enable": True}},
        astrbot_config_id="default",
    )
    scheduler.stages = []

    await scheduler.execute(event)

    assert event.get_extra("_astrbot_config") == selected_config
    assert event.get_extra("_astrbot_config_id") == "selected"


def test_umo_router_prefers_most_specific_matching_pattern():
    router = UmopConfigRouter(MagicMock())
    router.umop_to_conf_id = {
        "aki:*:*": "broad",
        "aki:FriendMessage:*": "message",
        "aki:FriendMessage:desktop-client": "exact",
    }

    assert router.get_conf_id_for_umop("aki:FriendMessage:desktop-client") == "exact"


def test_umo_router_prefers_fixed_wildcard_text_over_bare_wildcard():
    router = UmopConfigRouter(MagicMock())
    router.umop_to_conf_id = {
        "aki:FriendMessage:*": "first",
        "aki:FriendMessage:desktop-*": "second",
    }

    assert router.get_conf_id_for_umop("aki:FriendMessage:desktop-client") == "second"


def test_umo_router_keeps_insertion_order_for_specificity_ties_and_no_match():
    router = UmopConfigRouter(MagicMock())
    router.umop_to_conf_id = {
        "aki:FriendMessage:desktop-*": "first",
        "aki:FriendMessage:desktop-??????": "second",
    }

    assert router.get_conf_id_for_umop("aki:FriendMessage:desktop-client") == "first"
    assert router.get_conf_id_for_umop("other:GroupMessage:room") is None


def test_umo_router_does_not_count_character_class_contents_as_fixed_text():
    router = UmopConfigRouter(MagicMock())
    router.umop_to_conf_id = {
        "aki:FriendMessage:a[bcdefgh]*": "class",
        "aki:FriendMessage:ab*": "prefix",
    }
    assert router.get_conf_id_for_umop("aki:FriendMessage:abc") == "prefix"
