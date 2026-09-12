from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.proactive_agent_turn import run_proactive_agent_turn


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_value", "expected_max_step"),
    [
        (50, 50),
        ({}, 30),
        (True, 30),
        ("50", 50),
        (0, 1),
    ],
)
async def test_proactive_agent_turn_applies_validated_max_agent_step(
    monkeypatch,
    configured_value,
    expected_max_step,
):
    class Runner:
        def __init__(self):
            self.max_step = None

        async def step_until_done(self, max_step):
            self.max_step = max_step
            if False:
                yield None

        def get_final_llm_resp(self):
            return None

    runner = Runner()
    monkeypatch.setattr(
        "astrbot.core.astr_main_agent._get_session_conv",
        AsyncMock(return_value=SimpleNamespace(history="[]")),
    )
    monkeypatch.setattr(
        "astrbot.core.astr_main_agent.build_main_agent",
        AsyncMock(return_value=SimpleNamespace(agent_runner=runner)),
    )
    provider_settings = (
        configured_value
        if isinstance(configured_value, dict)
        else {"max_agent_step": configured_value}
    )

    result = await run_proactive_agent_turn(
        context=SimpleNamespace(),
        session=MessageSession("test", MessageType.FRIEND_MESSAGE, "user"),
        message="run",
        extras={},
        role=None,
        config=SimpleNamespace(provider_settings=provider_settings),
        system_prompt="",
        prompt="",
        require_delivery_tool=False,
        include_history_fences=False,
    )

    assert result is not None
    assert runner.max_step == expected_max_step
