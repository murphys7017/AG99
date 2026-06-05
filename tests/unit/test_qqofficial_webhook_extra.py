from types import SimpleNamespace

from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.qqofficial_webhook.qo_webhook_adapter import (
    botClient,
)


class FakeWebhookHelper:
    def __init__(self):
        self.data = {"msg-1": {"union_openid": "union-1", "message_scene": "scene-1"}}

    def pop_extra_data(self, message_id):
        return self.data.pop(message_id, {})


class FakePlatform:
    def __init__(self):
        self.webhook_helper = FakeWebhookHelper()
        self.events = []
        self.message_ids = []

    def remember_session_message_id(self, session_id, message_id):
        self.message_ids.append((session_id, message_id))

    def meta(self):
        return SimpleNamespace(name="qq", id="qq", description="qq")

    def commit_event(self, event):
        self.events.append(event)


def test_qqofficial_webhook_commit_passes_extra_fields():
    client = botClient.__new__(botClient)
    platform = FakePlatform()
    client.set_platform(platform)
    abm = SimpleNamespace(
        message_str="hello",
        message_id="msg-1",
        session_id="session-1",
        type=MessageType.FRIEND_MESSAGE,
    )

    client._commit(abm)

    assert platform.message_ids == [("session-1", "msg-1")]
    assert platform.events[0].get_extra("union_openid") == "union-1"
    assert platform.events[0].get_extra("message_scene") == "scene-1"
