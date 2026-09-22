import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.astr_main_agent import MainAgentBuildConfig, MainAgentBuildResult
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.core_request_preparation import (
    CoreRequestPreparationStopped,
    begin_core_request_lifecycle,
)
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.deadline import TurnDeadlineBudget, TurnDeadlineExceeded
from astrbot.core.execution_ledger import CoreExecutionLedger
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.proactive_agent_turn import run_proactive_agent_turn
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.star.star_handler import EventType


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
        kwargs["request_lifecycle"].bind_request(
            kwargs["req"],
            prompt_apply_result=SimpleNamespace(tool_schema_count=0),
        )

        async def reset():
            return None

        return MainAgentBuildResult(
            agent_runner=runner,
            provider_request=kwargs["req"],
            provider=SimpleNamespace(),
            reset_coro=reset(),
            request_lifecycle=kwargs["request_lifecycle"],
        )

    monkeypatch.setattr(
        "astrbot.core.astr_main_agent.build_main_agent", build_main_agent
    )
    loop_max_steps = []

    class Loop:
        def __init__(self, executor, *, max_step, should_stop=None):
            loop_max_steps.append(max_step)

        async def stream(self):
            if False:
                yield None

    monkeypatch.setattr("astrbot.core.astr_agent_run_util.NativeExecutionLoop", Loop)
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
        with pytest.raises(
            RuntimeError,
            match="native executor run did not complete successfully",
        ):
            await turn
        return
    result = await turn

    assert result is not None
    assert loop_max_steps == [expected_max_step]
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
        def done(self):
            return True

        def was_aborted(self):
            return False

        def get_final_llm_resp(self):
            return LLMResponse(
                role="assistant", completion_text="Internal execution result"
            )

    async def build(**kwargs):
        kwargs["request_lifecycle"].bind_request(
            kwargs["req"],
            prompt_apply_result=SimpleNamespace(tool_schema_count=0),
        )

        async def reset():
            return None

        return MainAgentBuildResult(
            agent_runner=Runner(),
            provider_request=kwargs["req"],
            provider=SimpleNamespace(),
            execution_spec=None,
            reset_coro=reset(),
            request_lifecycle=kwargs["request_lifecycle"],
        )

    monkeypatch.setattr(
        "astrbot.core.astr_main_agent._get_session_conv",
        AsyncMock(return_value=snapshot),
    )
    monkeypatch.setattr("astrbot.core.astr_main_agent.build_main_agent", build)

    class Loop:
        def __init__(self, executor, *, max_step, should_stop=None):
            pass

        async def stream(self):
            await conversation_manager.append_assistant_turn(
                cid,
                turn_id="visible-output",
                assistant_message={"role": "assistant", "content": "Personal reminder"},
            )
            if False:
                yield None

    monkeypatch.setattr("astrbot.core.astr_agent_run_util.NativeExecutionLoop", Loop)
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


@pytest.mark.asyncio
async def test_proactive_request_hook_mutation_reaches_execution(monkeypatch):
    observed_prompts = []

    async def hook(_event, hook_type, *args, **kwargs):
        assert kwargs["execution_surface"] == "core"
        if hook_type is EventType.OnLLMRequestEvent:
            args[0].prompt = "hooked prompt"
        return False

    async def begin(event):
        return await begin_core_request_lifecycle(event, hook_dispatcher=hook)

    class Runner:
        def done(self):
            return True

        def was_aborted(self):
            return False

        def get_final_llm_resp(self):
            return LLMResponse(role="assistant", completion_text="done")

    async def build(**kwargs):
        kwargs["request_lifecycle"].bind_request(
            kwargs["req"],
            prompt_apply_result=SimpleNamespace(tool_schema_count=0),
        )

        async def reset():
            observed_prompts.append(kwargs["req"].prompt)

        return MainAgentBuildResult(
            agent_runner=Runner(),
            provider_request=kwargs["req"],
            provider=SimpleNamespace(),
            reset_coro=reset(),
            request_lifecycle=kwargs["request_lifecycle"],
        )

    monkeypatch.setattr(
        "astrbot.core.proactive_agent_turn.begin_core_request_lifecycle",
        begin,
    )
    monkeypatch.setattr(
        "astrbot.core.astr_main_agent._get_session_conv",
        AsyncMock(return_value=SimpleNamespace(history="[]")),
    )
    monkeypatch.setattr("astrbot.core.astr_main_agent.build_main_agent", build)

    class Loop:
        def __init__(self, _executor, *, max_step, should_stop=None):
            assert max_step == 30

        async def stream(self):
            if False:
                yield None

    monkeypatch.setattr("astrbot.core.astr_agent_run_util.NativeExecutionLoop", Loop)
    result = await run_proactive_agent_turn(
        context=SimpleNamespace(
            get_config=lambda **kwargs: {"plugin_set": [], "provider_settings": {}}
        ),
        session=MessageSession("test", MessageType.FRIEND_MESSAGE, "user"),
        message="run",
        extras={},
        role=None,
        config=MainAgentBuildConfig(tool_call_timeout=60),
        system_prompt="",
        prompt="original prompt",
        require_delivery_tool=False,
        include_history_fences=False,
    )

    assert observed_prompts == ["hooked prompt"]
    assert result.request.prompt == "hooked prompt"


@pytest.mark.asyncio
async def test_proactive_request_hook_stop_discards_deferred_reset(monkeypatch):
    async def hook(_event, hook_type, *_args, **_kwargs):
        return hook_type is EventType.OnLLMRequestEvent

    async def begin(event):
        return await begin_core_request_lifecycle(event, hook_dispatcher=hook)

    reset_coro = MagicMock()

    async def build(**kwargs):
        kwargs["request_lifecycle"].bind_request(
            kwargs["req"],
            prompt_apply_result=SimpleNamespace(tool_schema_count=0),
        )
        return MainAgentBuildResult(
            agent_runner=SimpleNamespace(),
            provider_request=kwargs["req"],
            provider=SimpleNamespace(),
            reset_coro=reset_coro,
            request_lifecycle=kwargs["request_lifecycle"],
        )

    monkeypatch.setattr(
        "astrbot.core.proactive_agent_turn.begin_core_request_lifecycle",
        begin,
    )
    monkeypatch.setattr(
        "astrbot.core.astr_main_agent._get_session_conv",
        AsyncMock(return_value=SimpleNamespace(history="[]")),
    )
    monkeypatch.setattr("astrbot.core.astr_main_agent.build_main_agent", build)

    with pytest.raises(
        CoreRequestPreparationStopped,
        match="request_stopped_by_plugin",
    ):
        await run_proactive_agent_turn(
            context=SimpleNamespace(
                get_config=lambda **kwargs: {
                    "plugin_set": [],
                    "provider_settings": {},
                }
            ),
            session=MessageSession("test", MessageType.FRIEND_MESSAGE, "user"),
            message="run",
            extras={},
            role=None,
            config=MainAgentBuildConfig(tool_call_timeout=60),
            system_prompt="",
            prompt="original prompt",
            require_delivery_tool=False,
            include_history_fences=False,
        )

    reset_coro.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_proactive_total_deadline_covers_conversation_resolution(monkeypatch):
    async def slow_conversation(**_kwargs):
        await asyncio.sleep(0.1)
        return SimpleNamespace(history="[]")

    monkeypatch.setattr(
        "astrbot.core.proactive_agent_turn._ensure_proactive_execution_deadline",
        lambda _event, _config: TurnDeadlineBudget.start(0.01),
    )
    monkeypatch.setattr(
        "astrbot.core.astr_main_agent._get_session_conv",
        slow_conversation,
    )

    with pytest.raises(
        TurnDeadlineExceeded,
        match="proactive_core_execution",
    ):
        await run_proactive_agent_turn(
            context=SimpleNamespace(
                get_config=lambda **kwargs: {
                    "plugin_set": [],
                    "provider_settings": {},
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


@pytest.mark.asyncio
async def test_proactive_inner_timeout_is_not_reclassified_as_total_deadline(
    monkeypatch,
):
    async def failing_conversation(**_kwargs):
        raise TimeoutError("conversation backend timed out")

    monkeypatch.setattr(
        "astrbot.core.astr_main_agent._get_session_conv",
        failing_conversation,
    )

    with pytest.raises(
        TimeoutError,
        match="conversation backend timed out",
    ) as raised:
        await run_proactive_agent_turn(
            context=SimpleNamespace(
                get_config=lambda **kwargs: {
                    "plugin_set": [],
                    "provider_settings": {},
                    "interaction_middleware": {"turn_timeout": 30},
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

    assert not isinstance(raised.value, TurnDeadlineExceeded)
