from types import SimpleNamespace

from astrbot.core.interaction.failure_policy import (
    InteractionFailureKind,
    should_emit_failure_reply,
)
from astrbot.core.platform.message_type import MessageType


def _event(message_type: MessageType):
    return SimpleNamespace(get_message_type=lambda: message_type)


def test_internal_failures_are_silent_in_group_chats_but_visible_in_private_chats():
    assert not should_emit_failure_reply(
        _event(MessageType.GROUP_MESSAGE),
        InteractionFailureKind.SESSION_QUEUE_TIMEOUT,
    )
    assert should_emit_failure_reply(
        _event(MessageType.FRIEND_MESSAGE),
        InteractionFailureKind.SESSION_QUEUE_TIMEOUT,
    )
