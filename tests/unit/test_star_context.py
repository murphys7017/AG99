from astrbot.core.agent.tool import FunctionTool
from astrbot.core.star.context import _resolve_tool_handler_module_path
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
