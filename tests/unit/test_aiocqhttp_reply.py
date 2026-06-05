"""测试 Reply 组件 toDict() 行为，确保只输出 OneBot V11 reply 段约定的字段。

Bug 背景：在私聊中引用上文消息时，OneBot 协议端可能返回
ActionFailed status='failed', retcode=100, wording='message not found'。
根因：Reply.toDict() 继承 BaseMessageComponent.toDict()，
会把 chain、sender_id、qq、seq 等字段序列化到 OneBot 协议的 message 数组中。
OneBot V11 标准只期望 {"type": "reply", "data": {"id": "..."}}。
"""

import astrbot.core.message.components as Comp


def test_reply_to_dict_contains_only_id_in_data():
    reply = Comp.Reply(id="123456")

    result = reply.toDict()

    assert result["type"] == "reply"
    assert "id" in result["data"]
    assert set(result["data"].keys()) == {"id"}
    assert result["data"]["id"] == "123456"


def test_reply_to_dict_matches_onebot_v11_format():
    reply = Comp.Reply(id="abc")
    assert reply.toDict() == {
        "type": "reply",
        "data": {"id": "abc"},
    }


def test_reply_to_dict_converts_int_id_to_str():
    reply = Comp.Reply(id=42)
    result = reply.toDict()
    assert result["data"]["id"] == "42"
