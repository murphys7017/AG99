from types import SimpleNamespace

import pytest
import quart

from astrbot.core.platform.sources.wecom.wecom_adapter import WecomServer
from astrbot.dashboard.routes.platform import PlatformRoute
from astrbot.dashboard.routes.route import RouteContext


class _FakePlatform:
    config = {"webhook_uuid": "hook-id"}

    def unified_webhook(self) -> bool:
        return True

    def meta(self):
        return SimpleNamespace(name="fake-platform")

    async def webhook_callback(self, _request):
        return "accepted", 202, {"Content-Type": "text/plain"}


@pytest.mark.asyncio
async def test_unified_webhook_preserves_adapter_plain_text_response():
    app = quart.Quart(__name__)
    core_lifecycle = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[_FakePlatform()])
    )
    PlatformRoute(RouteContext(config=SimpleNamespace(), app=app), core_lifecycle)

    client = app.test_client()
    response = await client.get("/api/platform/webhook/hook-id")

    assert response.status_code == 202
    assert response.content_type == "text/plain"
    assert await response.get_data() == b"accepted"


@pytest.mark.asyncio
async def test_wecom_verify_returns_plain_text_response():
    server = object.__new__(WecomServer)
    server.crypto = SimpleNamespace(check_signature=lambda *_args: "echo-token")
    request = SimpleNamespace(
        args={
            "msg_signature": "sig",
            "timestamp": "ts",
            "nonce": "nonce",
            "echostr": "echo-token",
        }
    )

    response = await server.handle_verify(request)

    assert response.content_type == "text/plain"
    assert await response.get_data() == b"echo-token"
