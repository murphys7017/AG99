import platform
from unittest.mock import AsyncMock

import mcp
import pytest

from astrbot.core.tools.computer_tools.python import LocalPythonTool, PythonTool


def test_python_tool_description_contains_os():
    """测试 PythonTool 的描述中是否包含当前操作系统信息"""
    tool = PythonTool()
    current_os = platform.system()
    assert current_os in tool.description
    assert "IPython" in tool.description


def test_local_python_tool_description_contains_os():
    """测试 LocalPythonTool 的描述中是否包含当前操作系统信息和兼容性提示"""
    tool = LocalPythonTool()
    current_os = platform.system()
    assert current_os in tool.description
    assert "Python environment" in tool.description
    assert "system-compatible" in tool.description


class _FakeEvent:
    unified_msg_origin = "aiocqhttp:GroupMessage:g1"
    role = "admin"

    def get_sender_id(self) -> str:
        return "admin_001"

    def get_platform_name(self) -> str:
        return "webchat"


class _FakeConfig:
    def get_config(self, umo: str | None = None):
        del umo
        return {"provider_settings": {"computer_use_require_admin": True}}


class _FakeAstrContext:
    context = _FakeConfig()
    event = _FakeEvent()


class _FakeWrapper:
    context = _FakeAstrContext()
    tool_call_timeout = 15


@pytest.mark.asyncio
async def test_local_python_tool_runs_in_session_workspace(tmp_path, monkeypatch):
    tool = LocalPythonTool()
    exec_mock = AsyncMock(
        return_value={"data": {"output": {"text": "ok", "images": []}, "error": ""}}
    )
    booter = type(
        "Booter",
        (),
        {"python": type("PythonRunner", (), {"exec": exec_mock})()},
    )()

    monkeypatch.setattr(
        "astrbot.core.tools.computer_tools.python.get_local_booter",
        lambda: booter,
    )
    monkeypatch.setattr(
        "astrbot.core.tools.computer_tools.python.workspace_root",
        lambda umo: tmp_path / "workspace",
    )

    result = await tool.call(_FakeWrapper(), code="print('ok')", timeout=30)

    assert isinstance(result, mcp.types.CallToolResult)
    assert result.content[0].text == "ok"
    assert (tmp_path / "workspace").is_dir()
    assert exec_mock.await_args.kwargs["cwd"] == str(tmp_path / "workspace")
    assert exec_mock.await_args.kwargs["timeout"] == 15
