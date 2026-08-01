import pytest

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent.tool_output_capture import (
    ToolOutputCapture,
    activate_tool_output_capture,
)
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.context import Context, _resolve_tool_handler_module_path
from astrbot.core.star.star import StarMetadata, star_registry


def _make_tool(module_path: str | None) -> FunctionTool:
    tool = FunctionTool(
        name="demo_tool",
        description="demo",
        parameters={"type": "object", "properties": {}},
        handler=None,
    )
    if module_path is None:
        tool.__module__ = ""  # type: ignore[attr-defined]
    else:
        tool.__module__ = module_path  # type: ignore[attr-defined]
    return tool


def test_resolve_tool_handler_module_path_prefers_registered_plugin_root():
    original_registry = list(star_registry)
    star_registry.clear()
    star_registry.append(
        StarMetadata(
            name="demo",
            module_path="data.plugins.astrbot_plugin_demo.main",
            root_dir_name="astrbot_plugin_demo",
        )
    )
    try:
        tool = _make_tool("astrbot_plugin_demo.tools.extra")

        assert (
            _resolve_tool_handler_module_path(tool)
            == "data.plugins.astrbot_plugin_demo.main"
        )
    finally:
        star_registry[:] = original_registry


def test_resolve_tool_handler_module_path_preserves_builtin_subdirectory_root():
    original_registry = list(star_registry)
    star_registry.clear()
    star_registry.append(
        StarMetadata(
            name="builtin_demo",
            module_path="astrbot.builtin_stars.demo.main",
            root_dir_name="demo",
        )
    )
    try:
        tool = _make_tool("astrbot.builtin_stars.demo.tools.extra")

        assert (
            _resolve_tool_handler_module_path(tool)
            == "astrbot.builtin_stars.demo.main"
        )
    finally:
        star_registry[:] = original_registry


def test_resolve_tool_handler_module_path_handles_empty_module():
    tool = _make_tool(None)

    assert _resolve_tool_handler_module_path(tool) == ""


@pytest.mark.asyncio
async def test_context_send_message_is_captured_inside_persona_tool_execution():
    context = object.__new__(Context)
    capture = ToolOutputCapture(session_origin="webchat:FriendMessage:session-1")

    with activate_tool_output_capture(capture):
        sent = await context.send_message(
            "webchat:FriendMessage:session-1",
            MessageChain([Plain("legacy tool output")]),
        )

    assert sent is True
    assert [message.get_plain_text() for message in capture.drain()] == [
        "legacy tool output"
    ]


@pytest.mark.asyncio
async def test_context_send_message_keeps_cross_session_target_inside_persona_tool():
    context = object.__new__(Context)
    dispatched = []
    context._proactive_message_dispatcher = None

    async def send_direct(session, message):
        dispatched.append((session, message))
        return True

    context._send_message_direct = send_direct
    capture = ToolOutputCapture(session_origin="webchat:FriendMessage:current")

    with activate_tool_output_capture(capture):
        sent = await context.send_message(
            "webchat:FriendMessage:other",
            MessageChain([Plain("cross-session output")]),
        )

    assert sent is True
    assert capture.drain() == []
    assert len(dispatched) == 1
    assert str(dispatched[0][0]) == "webchat:FriendMessage:other"
