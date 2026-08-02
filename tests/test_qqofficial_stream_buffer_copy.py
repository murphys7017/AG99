from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.core.platform.sources.qqofficial.qqofficial_message_event import (
    QQOfficialMessageEvent,
)


def test_qqofficial_stream_buffer_owns_each_plain_delta():
    event = object.__new__(QQOfficialMessageEvent)
    event.send_buffer = None
    shared = MessageChain(chain=[Plain("不")])

    event._append_stream_delta(shared)
    shared.chain[0].text = "稀"
    event._append_stream_delta(shared)
    shared.chain[0].text = "罕"
    event._append_stream_delta(shared)

    texts = [component.text for component in event.send_buffer.chain]
    assert texts == ["不", "稀", "罕"]
