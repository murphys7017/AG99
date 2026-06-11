"""Tests for per-tool permission management."""

import json
from unittest.mock import MagicMock, patch

import mcp.types
import pytest

from astrbot.core import sp
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.provider.func_tool_manager import FunctionToolManager
from astrbot.core.provider.register import llm_tools


def _make_coro(value: object):
    async def _inner():
        return value

    return _inner()


def _make_context(role: str = "member", sender_id: str = "user_123"):
    class FakeEvent:
        unified_msg_origin = "aiocqhttp:GroupMessage:g1"

        def is_admin(self) -> bool:
            return role == "admin"

        def get_sender_id(self) -> str:
            return sender_id

    class FakeConfig:
        def get_config(self, umo: str | None = None):
            return {}

    class FakeAstrContext:
        context = FakeConfig()
        event = FakeEvent()

    class FakeWrapper:
        context = FakeAstrContext()
        tool_call_timeout = 10

    return FakeWrapper()


def _dummy_tool(name: str = "test_tool") -> FunctionTool:
    return FunctionTool(
        name=name,
        description="A test tool",
        parameters={"type": "object", "properties": {}},
        handler=None,
    )


def _clear_tool_permissions() -> None:
    sp.put("tool_permissions", {}, scope="global", scope_id="global")


def test_default_permission_is_member():
    mgr = FunctionToolManager()

    assert mgr._default_permission("any_tool") == "member"
    assert mgr.get_tool_permission("any_tool") == ("member", False)


def test_check_permission_passes_when_no_config():
    _clear_tool_permissions()
    mgr = FunctionToolManager()

    assert mgr._check_tool_permission("no_such_tool", _make_context()) is None


def test_check_permission_passes_for_admin_with_admin_tool():
    sp.put(
        "tool_permissions",
        {"_default": {"dangerous_tool": "admin"}},
        scope="global",
        scope_id="global",
    )
    try:
        mgr = FunctionToolManager()

        assert (
            mgr._check_tool_permission(
                "dangerous_tool",
                _make_context(role="admin", sender_id="admin_001"),
            )
            is None
        )
    finally:
        _clear_tool_permissions()


def test_check_permission_denies_member_for_admin_tool():
    sp.put(
        "tool_permissions",
        {"_default": {"dangerous_tool": "admin"}},
        scope="global",
        scope_id="global",
    )
    try:
        mgr = FunctionToolManager()

        error = mgr._check_tool_permission(
            "dangerous_tool",
            _make_context(role="member", sender_id="user_999"),
        )

        assert error is not None
        assert "dangerous_tool" in error
        assert "admin" in error.lower()
        assert "user_999" in error
    finally:
        _clear_tool_permissions()


def test_set_tool_permission_persists_global_default():
    _clear_tool_permissions()
    mgr = FunctionToolManager()

    mgr.set_tool_permission("target_tool", "admin")

    stored = sp.get("tool_permissions", {}, scope="global", scope_id="global")
    assert stored["_default"]["target_tool"] == "admin"
    assert mgr.get_tool_permission("target_tool") == ("admin", True)
    _clear_tool_permissions()


@pytest.mark.asyncio
async def test_executor_blocks_plugin_tool_before_calling_handler():
    sp.put(
        "tool_permissions",
        {"_default": {"blocked_tool": "admin"}},
        scope="global",
        scope_id="global",
    )
    try:
        called = False

        async def handler(event):
            nonlocal called
            called = True
            return "should not reach"

        tool = FunctionTool(
            name="blocked_tool",
            description="desc",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        )

        results = [
            item
            async for item in FunctionToolExecutor.execute(
                tool,
                _make_context(role="member"),
            )
        ]

        assert called is False
        assert len(results) == 1
        assert isinstance(results[0], mcp.types.CallToolResult)
        assert "Permission denied" in results[0].content[0].text
    finally:
        _clear_tool_permissions()


@pytest.mark.asyncio
async def test_executor_does_not_apply_per_tool_permission_to_builtin():
    sp.put(
        "tool_permissions",
        {"_default": {"send_message_to_user": "admin"}},
        scope="global",
        scope_id="global",
    )
    try:
        called = False

        async def handler(event):
            nonlocal called
            called = True
            return "ok"

        tool = FunctionTool(
            name="send_message_to_user",
            description="desc",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        )

        results = [
            item
            async for item in FunctionToolExecutor.execute(
                tool,
                _make_context(role="member"),
            )
        ]

        assert called is True
        assert len(results) == 1
        assert results[0].content[0].text == "ok"
    finally:
        _clear_tool_permissions()


class TestGetToolListPermission:
    @pytest.mark.asyncio
    async def test_list_includes_permission_fields_for_non_builtin(self):
        from astrbot.dashboard.routes.tools import ToolsRoute

        route = ToolsRoute.__new__(ToolsRoute)
        route.core_lifecycle = MagicMock()
        route.core_lifecycle.astrbot_config_mgr = MagicMock()
        route.core_lifecycle.astrbot_config_mgr.get_conf_list.return_value = []
        route.core_lifecycle.astrbot_config_mgr.confs = {}
        route.tool_mgr = FunctionToolManager()

        sp.put(
            "tool_permissions",
            {"_default": {"my_plugin_tool": "admin"}},
            scope="global",
            scope_id="global",
        )
        try:
            route.tool_mgr.func_list.append(_dummy_tool("my_plugin_tool"))

            resp = await route.get_tool_list()
            data = json.loads(json.dumps(resp))
            target = next(
                tool for tool in data["data"] if tool["name"] == "my_plugin_tool"
            )

            assert target["permission"] == "admin"
            assert target["permission_configured"] is True
            assert target["readonly"] is False
        finally:
            _clear_tool_permissions()

    @pytest.mark.asyncio
    async def test_list_no_permission_fields_for_builtin(self):
        from astrbot.dashboard.routes.tools import ToolsRoute

        route = ToolsRoute.__new__(ToolsRoute)
        route.core_lifecycle = MagicMock()
        route.core_lifecycle.astrbot_config_mgr = MagicMock()
        route.core_lifecycle.astrbot_config_mgr.get_conf_list.return_value = []
        route.core_lifecycle.astrbot_config_mgr.confs = {}
        route.tool_mgr = FunctionToolManager()

        resp = await route.get_tool_list()
        data = json.loads(json.dumps(resp))
        target = next(
            tool for tool in data["data"] if tool["name"] == "send_message_to_user"
        )

        assert "permission" not in target
        assert "permission_configured" not in target
        assert target["readonly"] is True


class TestUpdateToolPermission:
    @pytest.mark.asyncio
    async def test_set_admin_permission(self):
        from astrbot.dashboard.routes.tools import ToolsRoute

        route = ToolsRoute.__new__(ToolsRoute)
        route.core_lifecycle = MagicMock()
        route.tool_mgr = FunctionToolManager()
        route.tool_mgr.func_list.append(_dummy_tool("target_tool"))
        _clear_tool_permissions()

        mock_req = MagicMock()
        mock_req.json = _make_coro({"name": "target_tool", "permission": "admin"})
        with patch("astrbot.dashboard.routes.tools.request", mock_req):
            resp = await route.update_tool_permission()
            data = json.loads(json.dumps(resp))
            assert data["status"] == "ok"

        stored = sp.get("tool_permissions", {}, scope="global", scope_id="global")
        assert stored["_default"]["target_tool"] == "admin"
        _clear_tool_permissions()

    @pytest.mark.asyncio
    async def test_reject_builtin_tool(self):
        from astrbot.dashboard.routes.tools import ToolsRoute

        route = ToolsRoute.__new__(ToolsRoute)
        route.core_lifecycle = MagicMock()
        route.tool_mgr = FunctionToolManager()

        mock_req = MagicMock()
        mock_req.json = _make_coro(
            {"name": "send_message_to_user", "permission": "admin"}
        )
        with patch("astrbot.dashboard.routes.tools.request", mock_req):
            resp = await route.update_tool_permission()
            data = json.loads(json.dumps(resp))

        assert data["status"] == "error"
        assert "builtin" in str(data["message"]).lower()

    @pytest.mark.asyncio
    async def test_reject_unknown_tool(self):
        from astrbot.dashboard.routes.tools import ToolsRoute

        route = ToolsRoute.__new__(ToolsRoute)
        route.core_lifecycle = MagicMock()
        route.tool_mgr = FunctionToolManager()

        mock_req = MagicMock()
        mock_req.json = _make_coro({"name": "ghost_tool", "permission": "admin"})
        with patch("astrbot.dashboard.routes.tools.request", mock_req):
            resp = await route.update_tool_permission()
            data = json.loads(json.dumps(resp))

        assert data["status"] == "error"
        assert "not found" in str(data["message"]).lower()


def test_global_llm_tools_exposes_permission_helpers():
    assert hasattr(llm_tools, "_check_tool_permission")
