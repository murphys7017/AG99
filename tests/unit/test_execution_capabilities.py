from types import SimpleNamespace

import pytest

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.execution_capabilities import (
    WEB_RESEARCH_CAPABILITY,
    collect_semantic_capability_bindings,
)
from astrbot.core.interaction.capability_route_guard import (
    correct_contradictory_capability_denial,
)
from astrbot.core.interaction.execution_capability_summary import (
    ExecutionCapability,
    ExecutionCapabilitySummary,
    resolve_core_execution_capability_summary,
)
from astrbot.core.interaction.turn_state import ensure_interaction_turn_state
from astrbot.core.interaction.types import (
    InteractionPromptBuildConfig,
    PersonalResponseAction,
)


def _tool(
    name: str,
    *,
    action_capabilities: dict[str, set[str]] | None = None,
) -> FunctionTool:
    return FunctionTool(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        action_semantic_capabilities=action_capabilities or {},
    )


def test_web_research_capability_supports_action_level_declarations():
    bindings = collect_semantic_capability_bindings(
        [
            _tool(
                "minimax_cli",
                action_capabilities={"search": {WEB_RESEARCH_CAPABILITY}},
            ),
            _tool("web_search_tavily"),
            _tool("tavily_extract_web_page"),
        ]
    )[WEB_RESEARCH_CAPABILITY]

    assert [(binding.tool_name, binding.actions) for binding in bindings] == [
        ("minimax_cli", ("search",)),
        ("web_search_tavily", ()),
    ]


def test_capability_denial_guard_delegates_without_exposing_core_tools():
    expression = SimpleNamespace(
        turn_action=PersonalResponseAction.REPLY,
        spoken_reply="我这轮没有联网工具，天气查不了。",
        speech_cues=[{"kind": "pause"}],
        metadata={},
    )
    summary = ExecutionCapabilitySummary(
        config_id="olv",
        capabilities=(
            ExecutionCapability(
                capability_id=WEB_RESEARCH_CAPABILITY,
                state="admitted",
                bindings=("minimax_cli",),
            ),
        ),
    )

    assert correct_contradictory_capability_denial(
        request_text="查一下石家庄今天的天气",
        expression=expression,
        capability_summary=summary,
    )
    assert expression.turn_action is PersonalResponseAction.DELEGATE
    assert expression.spoken_reply == "我去查一下，稍等。"
    assert expression.speech_cues == []
    assert expression.metadata["route_correction_reason"] == (
        "capability_denial_route_corrected"
    )


def test_capability_denial_guard_leaves_ordinary_reply_unchanged():
    expression = SimpleNamespace(
        turn_action=PersonalResponseAction.REPLY,
        spoken_reply="今天想聊点什么？",
        speech_cues=[],
        metadata={},
    )
    summary = ExecutionCapabilitySummary(
        config_id="olv",
        capabilities=(
            ExecutionCapability(
                capability_id=WEB_RESEARCH_CAPABILITY,
                state="admitted",
                bindings=("web_search_tavily",),
            ),
        ),
    )

    assert not correct_contradictory_capability_denial(
        request_text="你好",
        expression=expression,
        capability_summary=summary,
    )
    assert expression.turn_action is PersonalResponseAction.REPLY


@pytest.mark.asyncio
async def test_core_capability_summary_isolated_by_turn_config():
    class Event:
        plugins_name = None

        def __init__(self, config_id: str):
            self._extras = {
                "_astrbot_config_id": config_id,
                "_interaction_enabled": True,
            }

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

    class ToolManager:
        func_list = []

    class PluginContext:
        def get_llm_tool_manager(self):
            return ToolManager()

    enabled_event = Event("search-enabled")
    disabled_event = Event("search-disabled")
    ensure_interaction_turn_state(enabled_event, turn_id="enabled")
    ensure_interaction_turn_state(disabled_event, turn_id="disabled")

    enabled = await resolve_core_execution_capability_summary(
        event=enabled_event,
        plugin_context=PluginContext(),
        config=InteractionPromptBuildConfig(
            provider_settings={
                "web_search": True,
                "websearch_provider": "tavily",
            }
        ),
        persona_selection=(None, None),
    )
    disabled = await resolve_core_execution_capability_summary(
        event=disabled_event,
        plugin_context=PluginContext(),
        config=InteractionPromptBuildConfig(
            provider_settings={"web_search": False}
        ),
        persona_selection=(None, None),
    )

    assert enabled.config_id == "search-enabled"
    assert enabled.get(WEB_RESEARCH_CAPABILITY).state == "admitted"
    assert enabled.get(WEB_RESEARCH_CAPABILITY).bindings == (
        "web_search_tavily",
    )
    assert disabled.config_id == "search-disabled"
    assert disabled.get(WEB_RESEARCH_CAPABILITY).state == "unavailable"
