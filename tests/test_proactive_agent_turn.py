from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.astr_main_agent import MainAgentBuildConfig
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.execution_ledger import CoreExecutionLedger
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.proactive_agent_turn import run_proactive_agent_turn
from astrbot.core.provider.entities import LLMResponse


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_value", "expected_max_step", "response_role"),
    [
        (50, 50, "assistant"),
        ({}, 30, "assistant"),
        (True, 30, "assistant"),
        ("50", 50, "assistant"),
        (0, 1, "assistant"),
        (5, 5, "err"),
    ],
)
async def test_proactive_agent_turn_applies_validated_max_agent_step(
    monkeypatch,
    configured_value,
    expected_max_step,
    response_role,
):
    class Runner:
        def __init__(self):
            self.max_step = None

        async def step_until_done(self, max_step):
            self.max_step = max_step
            if False:
                yield None

        def get_final_llm_resp(self):
            return LLMResponse(role=response_role, completion_text="done")

        def done(self):
            return True

        def was_aborted(self):
            return False

    runner = Runner()
    monkeypatch.setattr(
        "astrbot.core.astr_main_agent._get_session_conv",
        AsyncMock(return_value=SimpleNamespace(history="[]")),
    )

    async def build_main_agent(**kwargs):
        config = kwargs["config"]
        assert config.computer_use_runtime == "none"
        assert config.add_cron_tools is False
        return SimpleNamespace(agent_runner=runner, provider_request=kwargs["req"])

    monkeypatch.setattr(
        "astrbot.core.astr_main_agent.build_main_agent", build_main_agent
    )
    provider_settings = (
        configured_value
        if isinstance(configured_value, dict)
        else {"max_agent_step": configured_value}
    )
    provider_settings.update(
        {
            "computer_use_runtime": "none",
            "proactive_capability": {"add_cron_tools": False},
        }
    )

    turn = run_proactive_agent_turn(
        context=SimpleNamespace(
            get_config=lambda **kwargs: {
                "plugin_set": [],
                "provider_settings": provider_settings,
            }
        ),
        session=MessageSession("test", MessageType.FRIEND_MESSAGE, "user"),
        message="run",
        extras={},
        role=None,
        config=MainAgentBuildConfig(tool_call_timeout=60),
        system_prompt="",
        prompt="",
        require_delivery_tool=False,
        include_history_fences=False,
    )
    if response_role == "err":
        with pytest.raises(RuntimeError, match="did not complete successfully"):
            await turn
        return
    result = await turn

    assert result is not None
    assert runner.max_step == expected_max_step
    assert result.event.plugins_name == []
    assert result.delivery_confirmed is False


@pytest.mark.asyncio
async def test_proactive_result_does_not_replace_visible_history(tmp_path, monkeypatch):
    db = SQLiteDatabase(str(tmp_path / "history.db"))
    await db.initialize()
    conversation_manager = ConversationManager(db)
    cid = "proactive-history"
    await db.create_conversation(user_id="user", platform_id="test", cid=cid)
    snapshot = SimpleNamespace(cid=cid, history="[]")

    class Runner:
        async def step_until_done(self, max_step):
            await conversation_manager.append_assistant_turn(
                cid,
                turn_id="visible-output",
                assistant_message={"role": "assistant", "content": "Personal reminder"},
            )
            if False:
                yield None

        def done(self):
            return True

        def was_aborted(self):
            return False

        def get_final_llm_resp(self):
            return LLMResponse(
                role="assistant", completion_text="Internal execution result"
            )

    async def build(**kwargs):
        return SimpleNamespace(
            agent_runner=Runner(), provider_request=kwargs["req"], execution_spec=None
        )

    monkeypatch.setattr(
        "astrbot.core.astr_main_agent._get_session_conv",
        AsyncMock(return_value=snapshot),
    )
    monkeypatch.setattr("astrbot.core.astr_main_agent.build_main_agent", build)
    try:
        await run_proactive_agent_turn(
            context=SimpleNamespace(
                get_config=lambda **kwargs: {"plugin_set": [], "provider_settings": {}},
                conversation_manager=conversation_manager,
                core_execution_ledger=CoreExecutionLedger(db),
            ),
            session=MessageSession("test", MessageType.FRIEND_MESSAGE, "user"),
            message="remind",
            extras={"cron_job": {"id": "job", "revision": 2}},
            role=None,
            config=MainAgentBuildConfig(tool_call_timeout=60),
            system_prompt="",
            prompt="",
            require_delivery_tool=False,
            include_history_fences=False,
        )
        visible = await db.get_conversation_by_id(cid=cid)
        assert [item["content"] for item in visible.content] == ["Personal reminder"]
        records = await db.get_recent_core_execution_records(cid)
        assert len(records) == 1
        assert records[0].result == "Internal execution result"
        assert records[0].task_spec["schedule_revision"] == 2
    finally:
        await db.engine.dispose()
