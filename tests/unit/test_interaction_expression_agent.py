from unittest.mock import AsyncMock

import pytest

from astrbot.core.interaction.expression_agent import (
    InteractionExpressionAgent,
    InteractionExpressionError,
    PersonaExpressionRequest,
    PersonaExpressionResult,
    build_persona_expression_output_contract,
    extract_persona_expression_result,
    phase_requires_spoken_reply,
    validate_persona_expression_result,
)
from astrbot.core.interaction.effects import PersonaEffectCall, PersonaEffectSpec
from astrbot.core.interaction.memory_store import InteractionMemoryStore
from astrbot.core.interaction.persona_runtime import InteractionPersonaRuntime
from astrbot.core.interaction.types import InteractionAgentConfig
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.output_contract import CompiledOutputContract
from astrbot.core.prompt.render.interfaces import RenderResult
from astrbot.core.provider.entities import LLMResponse


@pytest.mark.parametrize(
    ("phase", "requires_reply"),
    [
        ("first_response", True),
        ("plugin_output", True),
        ("final_response", True),
        ("executor_result", True),
        ("executor_started", False),
        ("executor_progress", False),
    ],
)
def test_phase_requires_spoken_reply_matches_persona_phase_contract(
    phase,
    requires_reply,
):
    assert phase_requires_spoken_reply(phase) is requires_reply


@pytest.mark.parametrize(
    "phase",
    [
        "first_response",
        "plugin_output",
        "final_response",
        "executor_result",
        "executor_started",
        "executor_progress",
    ],
)
def test_persona_expression_empty_result_without_effects_is_rejected(phase):
    with pytest.raises(InteractionExpressionError) as exc_info:
        validate_persona_expression_result(
            phase,
            PersonaExpressionResult(spoken_reply=""),
        )

    assert exc_info.value.reason == "empty_output"


def test_persona_expression_allows_effect_only_for_progress_phases():
    validate_persona_expression_result(
        "executor_progress",
        PersonaExpressionResult(
            spoken_reply="",
            effect_calls=[
                PersonaEffectCall(
                    name="ag99live.motion",
                    arguments={"axes": {"head_yaw": 40}},
                )
            ],
        ),
    )


def test_persona_expression_still_requires_reply_for_first_response_even_with_effect():
    with pytest.raises(InteractionExpressionError):
        validate_persona_expression_result(
            "first_response",
            PersonaExpressionResult(
                spoken_reply="",
                effect_calls=[
                    PersonaEffectCall(
                        name="ag99live.motion",
                        arguments={"axes": {"head_yaw": 40}},
                    )
                ],
            ),
        )


def test_persona_expression_repairs_truncated_json_from_provider():
    text = (
        '{"spoken_reply": "……你倒是说句话啊，发个问号是什么意思。", '
        '"plugin_hints": {"ag99live_motion": {"axes": {"head_yaw": 40, '
        '"head_pitch": 45, "head_roll": 50}, '
        '"resource_id": "embarrassed_lookaway"}}'
    )

    result = extract_persona_expression_result(text)

    assert result.spoken_reply == "……你倒是说句话啊，发个问号是什么意思。"
    assert result.plugin_hints == {
        "ag99live_motion": {
            "axes": {
                "head_yaw": 40,
                "head_pitch": 45,
                "head_roll": 50,
            },
            "resource_id": "embarrassed_lookaway",
        }
    }


def test_persona_expression_parses_effect_calls_from_json_fallback():
    effect = PersonaEffectSpec(
        plugin_id="plugin_a",
        name="ag99live.motion",
        description="Live2D motion",
        parameters={
            "type": "object",
            "properties": {"axes": {"type": "object"}},
            "required": ["axes"],
        },
    )

    result = extract_persona_expression_result(
        '{"spoken_reply":"嗯。","effect_calls":[{"name":"ag99live.motion","arguments":{"axes":{"head_yaw":40}}}]}',
        effects=[effect],
    )

    assert result.spoken_reply == "嗯。"
    assert result.effect_calls == [
        PersonaEffectCall(
            name="ag99live.motion",
            arguments={"axes": {"head_yaw": 40}},
            plugin_id="plugin_a",
            source="persona",
        )
    ]


@pytest.mark.asyncio
async def test_persona_expression_passes_compiled_contract_and_returns_plugin_hints(
    monkeypatch,
):
    contract = build_persona_expression_output_contract()
    compiled = CompiledOutputContract(
        contract=contract,
        strategy="protocol_tool_call",
        tool_name="persona_expression",
        tool_schema=contract.schema,
    )

    class Provider:
        def __init__(self):
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            return LLMResponse(
                role="assistant",
                completion_text="",
                tools_call_name=["persona_expression"],
                tools_call_args=[
                    {
                        "spoken_reply": "嗯，我来看看。",
                        "plugin_hints": {
                            "ag99live_motion": {"emotion_label": "focused"}
                        },
                    }
                ],
            )

    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"

        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

    provider = Provider()
    plugin_context = type(
        "PluginContext",
        (),
        {"get_provider_by_id": lambda self, provider_id: provider},
    )()
    event = Event()
    agent = InteractionExpressionAgent(InteractionMemoryStore())
    agent._prepare_render_result = AsyncMock(
        return_value=RenderResult(
            system_prompt="persona",
            messages=[{"role": "user", "content": "hello"}],
            output_contract=contract,
            compiled_output_contract=compiled,
        )
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.Provider",
        Provider,
    )

    result = await agent.generate_expression(
        event,
        plugin_context,
        InteractionAgentConfig(expression_provider_id="persona"),
        PersonaExpressionRequest(phase="first_response"),
    )

    assert result.spoken_reply == "嗯，我来看看。"
    assert result.plugin_hints == {
        "ag99live_motion": {"emotion_label": "focused"}
    }
    assert provider.calls[0]["output_contract"] is contract
    assert provider.calls[0]["compiled_output_contract"] is compiled


@pytest.mark.asyncio
async def test_persona_runtime_publishes_plugin_output_hints_after_selection():
    expression_agent = type(
        "ExpressionAgent",
        (),
        {
            "rewrite_plugin_output_result": AsyncMock(
                return_value=PersonaExpressionResult(
                    spoken_reply="人格化结果",
                    plugin_hints={
                        "ag99live_motion": {"emotion_label": "satisfied"}
                    },
                )
            )
        },
    )()

    class Event:
        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

    event = Event()
    runtime = InteractionPersonaRuntime(expression_agent)

    rendered = await runtime.render_plugin_output(
        event,
        MessageChain([Plain("原始插件结果")]),
        plugin_context=object(),
        interaction_config=InteractionAgentConfig(),
    )

    assert rendered.get_plain_text() == "人格化结果"
    assert event.get_extra("_interaction_plugin_hints") == {
        "ag99live_motion": {"emotion_label": "satisfied"}
    }
