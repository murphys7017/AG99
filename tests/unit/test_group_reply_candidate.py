from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.api.event import request_group_reply_candidate
from astrbot.core.interaction.group_context_capture import (
    GROUP_CONTEXT_CAPTURE_CANDIDATE_EXTRA,
)
from astrbot.core.interaction.group_reply import (
    is_group_reply_candidate,
    mark_group_reply_candidate,
    select_legacy_active_reply_candidate,
)
from astrbot.core.pipeline.group_context.stage import GroupContextStage
from astrbot.core.platform.message_type import MessageType


def make_event():
    extras = {}
    event = MagicMock()
    event.unified_msg_origin = "alice:GroupMessage:123"
    event.get_message_type.return_value = MessageType.GROUP_MESSAGE
    event.get_message_str.return_value = "ordinary group chat"
    event.get_messages.return_value = [object()]
    event.get_sender_id.return_value = "member"
    event.get_self_id.return_value = "bot"
    event.get_group_id.return_value = "123"
    event.is_at_or_wake_command = False
    event.is_wake = False
    event.is_stopped.return_value = False
    event.get_extra.side_effect = lambda key, default=None: extras.get(key, default)
    event.set_extra.side_effect = lambda key, value: extras.__setitem__(key, value)
    event.stop_event = MagicMock()
    return event


def active_reply_config():
    return {
        "interaction_middleware": {"enabled": True},
        "provider_ltm_settings": {
            "active_reply": {
                "enable": True,
                "method": "possibility_reply",
                "possibility_reply": 1.0,
                "whitelist": ["123"],
            }
        },
    }


def test_legacy_active_reply_only_selects_a_router_candidate():
    event = make_event()

    assert select_legacy_active_reply_candidate(
        event,
        active_reply_config(),
        random_value=0.5,
    )
    assert not is_group_reply_candidate(event)

    mark_group_reply_candidate(event, kind="ambient")

    assert is_group_reply_candidate(event)
    assert event.get_extra("_interaction_group_reply_candidate_kind") == "ambient"


def test_legacy_active_reply_requires_interaction_middleware():
    event = make_event()
    config = active_reply_config()
    config["interaction_middleware"]["enabled"] = False

    assert not select_legacy_active_reply_candidate(event, config, random_value=0.0)


def test_plugin_can_request_router_admission_without_direct_reply_ownership():
    event = make_event()

    assert request_group_reply_candidate(event)
    assert is_group_reply_candidate(event)
    assert event.get_extra("_interaction_group_reply_candidate_kind") == "plugin"
    assert event.is_wake is False
    assert event.is_at_or_wake_command is False


def test_plugin_reply_candidate_rejects_private_messages():
    event = make_event()
    event.get_message_type.return_value = MessageType.FRIEND_MESSAGE

    assert not request_group_reply_candidate(event)
    assert not is_group_reply_candidate(event)
    assert event.is_wake is False
    assert event.is_at_or_wake_command is False


@pytest.mark.asyncio
async def test_group_context_stage_records_after_admission_and_stops_passive_event():
    event = make_event()
    event.set_extra(GROUP_CONTEXT_CAPTURE_CANDIDATE_EXTRA, True)
    collector = SimpleNamespace(
        capture_ambient_message=AsyncMock(),
    )
    stage = GroupContextStage()
    stage.ctx = SimpleNamespace(
        plugin_manager=SimpleNamespace(
            context=SimpleNamespace(
                list_prompt_extension_collectors=lambda: [collector]
            )
        )
    )

    await stage.process(event)

    collector.capture_ambient_message.assert_awaited_once_with(
        event,
        allow_router_candidate=False,
    )
    event.stop_event.assert_called_once()
