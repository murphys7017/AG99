import asyncio
import json

import pytest

from astrbot.core.executors.codex_protocol import (
    CodexProtocolError,
    decode_message,
    encode_request,
    parse_error,
)
from astrbot.core.executors.codex_session import CodexSessionManager


def test_codex_protocol_round_trip_and_error():
    message = json.loads(encode_request(7, "thread/start").decode())
    assert decode_message(json.dumps(message)) == message
    assert parse_error({"jsonrpc": "2.0", "id": 7, "result": {}}) is None
    assert decode_message(
        '{"method":"remoteControl/status/changed","params":{},"emittedAtMs":1}'
    )["method"] == "remoteControl/status/changed"
    assert decode_message('{"id":7,"result":{}}')["id"] == 7
    with pytest.raises(CodexProtocolError):
        decode_message(b"not-json")


@pytest.mark.asyncio
async def test_codex_session_initializes_thread_and_yields_turn_notifications(monkeypatch):
    class FakeStdin:
        def __init__(self, stdout):
            self.stdout = stdout

        def write(self, payload):
            request = json.loads(payload)
            method = request.get("method")
            if "id" in request:
                if method == "initialize":
                    result = {}
                elif method == "thread/start":
                    result = {"thread": {"id": "thread-1"}}
                elif method == "turn/start":
                    result = {"turn": {"id": "turn-1"}}
                else:
                    result = {}
                self.stdout.feed_data(
                    (json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}) + "\n").encode()
                )
                if method == "turn/start":
                    self.stdout.feed_data(
                        b'{"jsonrpc":"2.0","method":"item/agentMessage/delta","params":{"delta":"ok"}}\n'
                    )
                    self.stdout.feed_data(
                        b'{"jsonrpc":"2.0","method":"turn/completed","params":{}}\n'
                    )

        async def drain(self):
            await asyncio.sleep(0)

    class FakeProcess:
        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stdin = FakeStdin(self.stdout)
            self.returncode = None

        def terminate(self):
            self.returncode = 0
            self.stdout.feed_eof()
            self.stderr.feed_eof()

        def kill(self):
            self.returncode = -9

        async def wait(self):
            return self.returncode

    process = FakeProcess()

    async def create_process(*args, **kwargs):
        assert args[:3] == ("codex", "app-server", "--stdio")
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    manager = CodexSessionManager(request_timeout=1)
    try:
        assert await manager.start_thread() == "thread-1"
        events = [event async for event in manager.iter_turn("do it")]
        assert [event["method"] for event in events] == [
            "item/agentMessage/delta",
            "turn/completed",
        ]
    finally:
        await manager.aclose()
