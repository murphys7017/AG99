"""Tests for prompt context collection."""

from asyncio import Queue
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot.core import astr_main_agent as ama
from astrbot.core.agent.agent import Agent
from astrbot.core.agent.handoff import HandoffTool
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_main_agent_resources import (
    CHATUI_SPECIAL_DEFAULT_PERSONA_PROMPT,
    LIVE_MODE_SYSTEM_PROMPT,
    LLM_SAFETY_MODE_SYSTEM_PROMPT,
    SANDBOX_MODE_PROMPT,
)
from astrbot.core.memory.types import (
    Experience,
    LongTermMemoryIndex,
    MemorySnapshot,
    PersonaState,
    ShortTermMemory,
    TopicState,
)
from astrbot.core.message.components import File, Image, Plain, Reply
from astrbot.core.prompt.collectors import (
    ConversationHistoryCollector,
    InputCollector,
    KnowledgeCollector,
    MemoryCollector,
    PolicyCollector,
    SessionCollector,
    SkillsCollector,
    SubagentCollector,
    SystemCollector,
    ToolsCollector,
)
from astrbot.core.prompt.context_collect import (
    PROMPT_CONTEXT_PACK_EXTRA_KEY,
    collect_context_pack,
    log_context_pack,
)
from astrbot.core.prompt.context_types import ContextSlot
from astrbot.core.prompt.extensions import PromptExtension
from astrbot.core.prompt.input_annotations import (
    INPUT_ITEM_ANNOTATIONS_EXTRA_KEY,
    INPUT_QUOTED_TEXT_ANNOTATION_KEY,
    INPUT_TEXT_ANNOTATION_KEY,
    build_message_annotation_key,
    build_reply_chain_annotation_key,
)
from astrbot.core.prompt.interfaces import (
    ContextCollectorInterface,
    PromptExtensionCollectorInterface,
)
from astrbot.core.prompt.render import (
    PROMPT_RENDER_RESULT_EXTRA_KEY,
    PROMPT_SHADOW_APPLY_RESULT_EXTRA_KEY,
    PROMPT_SHADOW_DIFF_EXTRA_KEY,
    PROMPT_SHADOW_PROVIDER_REQUEST_EXTRA_KEY,
)
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.skills.skill_manager import SkillInfo
from astrbot.core.star.context import Context


def _make_event():
    extras: dict[str, object] = {}

    event = MagicMock()
    event.unified_msg_origin = "test_platform:private:test-session"
    event.message_str = "hello"
    event.message_obj = MagicMock()
    event.message_obj.message = [Plain(text="hello")]
    event.message_obj.sender = MagicMock(user_id="user123", nickname="Tester")
    event.message_obj.group_id = None
    event.message_obj.group = None
    event.get_platform_name.return_value = "test_platform"
    event.get_platform_id.return_value = "test_platform"
    event.get_group_id.return_value = None
    event.get_sender_name.return_value = "Tester"
    event.plugins_name = None
    event.platform_meta = MagicMock(support_proactive_message=False)
    event.trace = MagicMock()

    def _get_extra(key, default=None):
        return extras.get(key, default)

    def _set_extra(key, value):
        extras[key] = value

    event.get_extra.side_effect = _get_extra
    event.set_extra.side_effect = _set_extra
    return event, extras


def _make_context():
    ctx = MagicMock()
    ctx.get_config.return_value = {}
    ctx.get_provider_by_id.return_value = None
    ctx.subagent_orchestrator = None
    ctx.list_prompt_extension_collectors.return_value = []
    tool_manager = MagicMock()
    tool_manager.get_full_tool_set.return_value = ToolSet()
    tool_manager.get_func.return_value = None
    ctx.get_llm_tool_manager.return_value = tool_manager
    ctx.persona_manager = MagicMock()
    ctx.persona_manager.get_persona_v3_by_id = MagicMock(return_value=None)
    ctx.conversation_manager = MagicMock()
    return ctx


def _make_real_context() -> Context:
    provider_manager = MagicMock()
    provider_manager.provider_insts = []
    provider_manager.llm_tools = MagicMock(func_list=[])
    return Context(
        event_queue=Queue(),
        config=MagicMock(),
        db=MagicMock(),
        provider_manager=provider_manager,
        platform_manager=MagicMock(platform_insts=[]),
        conversation_manager=MagicMock(),
        message_history_manager=MagicMock(),
        persona_manager=MagicMock(),
        astrbot_config_mgr=MagicMock(),
        knowledge_base_manager=MagicMock(),
        cron_manager=MagicMock(),
    )


def _make_conversation(persona_id=None):
    conversation = MagicMock()
    conversation.cid = "conv-id"
    conversation.persona_id = persona_id
    conversation.history = "[]"
    return conversation


def _make_tool(
    name: str,
    *,
    description: str = "",
    parameters: dict | None = None,
    active: bool = True,
    handler_module_path: str | None = "tests.prompt_tools",
) -> FunctionTool:
    return FunctionTool(
        name=name,
        description=description,
        parameters=parameters
        or {
            "type": "object",
            "properties": {},
        },
        handler=None,
        active=active,
        handler_module_path=handler_module_path,
    )


def _make_handoff_tool(
    agent_name: str,
    *,
    description: str = "",
    parameters: dict | None = None,
) -> HandoffTool:
    agent = Agent(
        name=agent_name,
        instructions="internal subagent prompt",
        tools=["tool_a"],
    )
    return HandoffTool(
        agent=agent,
        tool_description=description or None,
        parameters=parameters,
    )


@pytest.fixture(autouse=True)
def _patch_memory_service():
    service = MagicMock()
    service.get_snapshot = AsyncMock(
        return_value=MemorySnapshot(
            umo="test_platform:private:test-session",
            conversation_id="conv-id",
        )
    )

    with patch(
        "astrbot.core.prompt.collectors.memory_collector.get_memory_service",
        return_value=service,
    ), patch(
        "astrbot.core.prompt.collectors.memory_collector.get_memory_config",
        return_value=MagicMock(enabled=True),
    ):
        yield service


@pytest.fixture(autouse=True)
def _patch_skills_manager():
    with patch(
        "astrbot.core.prompt.collectors.skills_collector.SkillManager.list_skills",
        return_value=[],
    ) as mock_list_skills:
        yield mock_list_skills


@pytest.mark.asyncio
async def test_collect_context_pack_collects_persona_prompt():
    event, extras = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="hello")
    req.conversation = _make_conversation(persona_id="persona-a")
    extras["provider_request"] = req

    persona = {
        "name": "persona-a",
        "prompt": "You are a helpful assistant.",
        "_begin_dialogs_processed": [],
        "tools": None,
        "skills": None,
    }
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=("persona-a", persona, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        provider_request=req,
    )

    slot = pack.get_slot("persona.prompt")
    assert slot is not None
    assert slot.value == "You are a helpful assistant."
    assert pack.provider_request_ref is req
    segments_slot = pack.get_slot("persona.segments")
    assert segments_slot is not None
    assert segments_slot.value["unparsed_sections"] == ["You are a helpful assistant."]


@pytest.mark.asyncio
async def test_collect_context_pack_collects_webchat_default_persona_prompt():
    event, extras = _make_event()
    event.get_platform_name.return_value = "webchat"
    context = _make_context()
    req = ProviderRequest(prompt="hello")
    req.conversation = _make_conversation(persona_id=None)
    extras["provider_request"] = req

    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=("_chatui_default_", None, None, True)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        provider_request=req,
    )

    slot = pack.get_slot("persona.prompt")
    assert slot is not None
    assert slot.value == CHATUI_SPECIAL_DEFAULT_PERSONA_PROMPT
    assert slot.meta["use_webchat_special_default"] is True
    assert pack.get_slot("persona.segments") is not None


@pytest.mark.asyncio
async def test_build_main_agent_stores_prompt_context_pack_in_event_extra():
    event, extras = _make_event()
    context = _make_context()
    provider = MagicMock()
    provider.provider_config = {"id": "test-provider", "modalities": ["tool_use"]}
    provider.get_model.return_value = "gpt-4"
    context.get_using_provider.return_value = provider

    conversation = _make_conversation(persona_id="persona-a")
    context.conversation_manager.get_curr_conversation_id = AsyncMock(return_value=None)
    context.conversation_manager.new_conversation = AsyncMock(return_value="conv-id")
    context.conversation_manager.get_conversation = AsyncMock(return_value=conversation)

    persona = {
        "name": "persona-a",
        "prompt": "You are a helpful assistant.",
        "_begin_dialogs_processed": [],
        "tools": None,
        "skills": None,
    }
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=("persona-a", persona, None, False)
    )

    with (
        patch("astrbot.core.astr_main_agent.AgentRunner") as mock_runner_cls,
        patch("astrbot.core.astr_main_agent.AstrAgentContext"),
    ):
        mock_runner = MagicMock()
        mock_runner.reset = AsyncMock()
        mock_runner_cls.return_value = mock_runner

        result = await ama.build_main_agent(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        )

    assert result is not None
    assert PROMPT_CONTEXT_PACK_EXTRA_KEY in extras
    assert PROMPT_RENDER_RESULT_EXTRA_KEY in extras
    pack = extras[PROMPT_CONTEXT_PACK_EXTRA_KEY]
    slot = pack.get_slot("persona.prompt")
    assert slot is not None
    assert slot.value == "You are a helpful assistant."
    assert pack.get_slot("persona.segments") is not None
    assert result.provider_request.prompt is not None
    assert result.provider_request.prompt.startswith("<request_context>")


@pytest.mark.asyncio
async def test_build_main_agent_runs_prompt_pipeline_in_shadow_mode():
    event, extras = _make_event()
    context = _make_context()
    provider = MagicMock()
    provider.provider_config = {"id": "test-provider", "modalities": ["tool_use"]}
    provider.get_model.return_value = "gpt-4"
    context.get_using_provider.return_value = provider

    conversation = _make_conversation(persona_id="persona-a")
    context.conversation_manager.get_curr_conversation_id = AsyncMock(return_value=None)
    context.conversation_manager.new_conversation = AsyncMock(return_value="conv-id")
    context.conversation_manager.get_conversation = AsyncMock(return_value=conversation)

    persona = {
        "name": "persona-a",
        "prompt": "You are a helpful assistant.",
        "_begin_dialogs_processed": [],
        "tools": None,
        "skills": None,
    }
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=("persona-a", persona, None, False)
    )

    with (
        patch("astrbot.core.astr_main_agent.AgentRunner") as mock_runner_cls,
        patch("astrbot.core.astr_main_agent.AstrAgentContext"),
    ):
        mock_runner = MagicMock()
        mock_runner.reset = AsyncMock()
        mock_runner_cls.return_value = mock_runner

        result = await ama.build_main_agent(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                prompt_pipeline_mode="",
                prompt_pipeline_shadow_mode=True,
            ),
        )

    assert result is not None
    assert PROMPT_CONTEXT_PACK_EXTRA_KEY in extras
    assert PROMPT_RENDER_RESULT_EXTRA_KEY in extras
    assert PROMPT_SHADOW_PROVIDER_REQUEST_EXTRA_KEY in extras
    assert PROMPT_SHADOW_APPLY_RESULT_EXTRA_KEY in extras
    assert PROMPT_SHADOW_DIFF_EXTRA_KEY in extras

    render_result = extras[PROMPT_RENDER_RESULT_EXTRA_KEY]
    shadow_request = extras[PROMPT_SHADOW_PROVIDER_REQUEST_EXTRA_KEY]
    apply_result = extras[PROMPT_SHADOW_APPLY_RESULT_EXTRA_KEY]
    shadow_diff = extras[PROMPT_SHADOW_DIFF_EXTRA_KEY]

    assert render_result.messages
    assert apply_result.used_user_message is True
    assert apply_result.history_message_count == 0
    assert shadow_request is not result.provider_request
    assert shadow_request.prompt is not None
    assert shadow_request.prompt.startswith("<request_context>")
    assert shadow_request.extra_user_content_parts
    assert result.provider_request.prompt == "hello"
    assert shadow_diff["changed"] is True
    assert "prompt" in shadow_diff["changed_fields"]
    assert "system_prompt" in shadow_diff["changed_fields"]
    assert shadow_diff["diff"]["prompt"]["live"] == "hello"
    assert (
        shadow_diff["diff"]["system_prompt"]["live"]
        == result.provider_request.system_prompt
    )
    assert isinstance(shadow_diff["diff"]["prompt"]["shadow"], str)


@pytest.mark.asyncio
async def test_log_context_pack_logs_summary_at_info_and_slots_at_debug():
    event, extras = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="hello")
    req.conversation = _make_conversation(persona_id="persona-a")
    extras["provider_request"] = req

    persona_prompt = """身份
- 你是 Alice，由 YakumoAki 设计。
稳定规则
- 始终以 Alice 身份回应。"""
    persona = {
        "name": "persona-a",
        "prompt": persona_prompt,
        "_begin_dialogs_processed": [
            {"role": "user", "content": "hi", "_no_save": True}
        ],
        "begin_dialogs": ["hi", "hello"],
        "tools": ["tool_a"],
        "skills": ["skill_a"],
    }
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=("persona-a", persona, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        provider_request=req,
    )

    with patch("astrbot.core.prompt.context_collect.logger") as mock_logger:
        log_context_pack(pack, event=event)

    info_messages = [
        call.args[0] % call.args[1:] if call.args else ""
        for call in mock_logger.info.call_args_list
    ]
    debug_messages = [
        call.args[0] % call.args[1:] if call.args else ""
        for call in mock_logger.debug.call_args_list
    ]

    assert any("Prompt context pack collected:" in message for message in info_messages)
    assert not any("Prompt persona loaded:" in message for message in info_messages)
    assert any(
        "Prompt context slot: name=persona.prompt" in message
        for message in debug_messages
    )
    assert any(
        "Prompt context slot: name=persona.tools_whitelist" in message
        for message in debug_messages
    )


@pytest.mark.asyncio
async def test_collect_context_pack_collects_effective_input_text_and_attachments():
    event, _ = _make_event()
    event.message_str = "/ask explain this"
    event.message_obj.message = [
        Plain(text="/ask explain this"),
        Image(file="https://example.com/image.png"),
        File(name="report.txt", file="C:/tmp/report.txt"),
    ]
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            provider_wake_prefix="/ask ",
        ),
        collectors=[InputCollector()],
    )

    text_slot = pack.get_slot("input.text")
    assert text_slot is not None
    assert text_slot.value == "explain this"
    assert text_slot.meta["source_field"] == "event.message_str"

    images_slot = pack.get_slot("input.images")
    assert images_slot is not None
    assert images_slot.value == [
        {
            "ref": "https://example.com/image.png",
            "transport": "url",
            "source": "current",
        }
    ]

    files_slot = pack.get_slot("input.files")
    assert files_slot is not None
    assert files_slot.value == [
        {
            "name": "report.txt",
            "file": "C:/tmp/report.txt",
            "url": "",
            "source": "current",
            "reply_id": None,
        }
    ]


@pytest.mark.asyncio
async def test_collect_context_pack_collects_attachment_only_input_without_text():
    event, _ = _make_event()
    event.message_str = ""
    event.message_obj.message = [File(name="report.txt", file="C:/tmp/report.txt")]
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[InputCollector()],
    )

    assert pack.get_slot("input.text") is None
    files_slot = pack.get_slot("input.files")
    assert files_slot is not None
    assert files_slot.value[0]["source"] == "current"


@pytest.mark.asyncio
async def test_collect_context_pack_prefers_provider_request_prompt_for_input_text():
    event, _ = _make_event()
    event.message_str = "/ask raw message"
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )
    req = ProviderRequest(prompt="effective prompt")

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            provider_wake_prefix="/ask ",
        ),
        provider_request=req,
        collectors=[InputCollector()],
    )

    text_slot = pack.get_slot("input.text")
    assert text_slot is not None
    assert text_slot.value == "effective prompt"
    assert text_slot.meta["source_field"] == "provider_request.prompt"


@pytest.mark.asyncio
async def test_collect_context_pack_applies_prompt_prefix_to_input_text():
    event, _ = _make_event()
    event.message_str = "/ask raw message"
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )
    req = ProviderRequest(prompt="effective prompt")

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            provider_wake_prefix="/ask ",
            provider_settings={"prompt_prefix": "Please answer: {{prompt}}"},
        ),
        provider_request=req,
        collectors=[InputCollector()],
    )

    text_slot = pack.get_slot("input.text")
    assert text_slot is not None
    assert text_slot.value == "Please answer: effective prompt"
    assert text_slot.meta["raw_text"] == "effective prompt"
    assert text_slot.meta["prompt_prefix"] == "Please answer: {{prompt}}"
    assert text_slot.meta["prefix_applied"] is True


@pytest.mark.asyncio
async def test_collect_context_pack_collects_input_sidecar_annotations():
    event, extras = _make_event()
    event.message_str = "describe this"
    event.message_obj.message = [
        Plain(text="describe this"),
        Image(file="https://example.com/screen.png"),
        File(name="debug.log", file="C:/tmp/debug.log"),
    ]
    extras[INPUT_ITEM_ANNOTATIONS_EXTRA_KEY] = {
        INPUT_TEXT_ANNOTATION_KEY: {
            "semantic_type": "user_text",
            "explanation": "This text is the user's current request.",
            "explanation_source": "platform",
            "context_role": "primary",
        },
        build_message_annotation_key(1): {
            "semantic_type": "desktop_screenshot",
            "explanation": "This image is the user's current desktop screenshot.",
            "explanation_source": "platform",
            "context_role": "supporting",
        },
        build_message_annotation_key(2): {
            "semantic_type": "runtime_log",
            "explanation": "This file is a runtime log the user wants analyzed.",
            "explanation_source": "platform",
            "context_role": "reference",
        },
    }
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[InputCollector()],
    )

    text_slot = pack.get_slot("input.text")
    assert text_slot is not None
    assert text_slot.meta["semantic_type"] == "user_text"
    assert text_slot.meta["explanation_source"] == "platform"
    assert text_slot.meta["context_role"] == "primary"

    images_slot = pack.get_slot("input.images")
    assert images_slot is not None
    assert images_slot.value == [
        {
            "ref": "https://example.com/screen.png",
            "transport": "url",
            "source": "current",
            "semantic_type": "desktop_screenshot",
            "explanation": "This image is the user's current desktop screenshot.",
            "explanation_source": "platform",
            "context_role": "supporting",
        }
    ]

    files_slot = pack.get_slot("input.files")
    assert files_slot is not None
    assert files_slot.value == [
        {
            "name": "debug.log",
            "file": "C:/tmp/debug.log",
            "url": "",
            "source": "current",
            "reply_id": None,
            "semantic_type": "runtime_log",
            "explanation": "This file is a runtime log the user wants analyzed.",
            "explanation_source": "platform",
            "context_role": "reference",
        }
    ]


@pytest.mark.asyncio
async def test_collect_context_pack_collects_quoted_input_payloads():
    event, _ = _make_event()
    event.message_obj.message = [
        Reply(
            id="reply-1",
            message_str="quoted message",
            chain=[
                Image(file="file:///C:/tmp/quoted.png"),
                File(name="quoted.txt", file="C:/tmp/quoted.txt"),
            ],
        )
    ]
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    with (
        patch(
            "astrbot.core.prompt.collectors.input_collector.extract_quoted_message_text",
            new=AsyncMock(return_value="quoted message"),
        ),
        patch(
            "astrbot.core.prompt.collectors.input_collector.extract_quoted_message_images",
            new=AsyncMock(return_value=["https://example.com/fallback.png"]),
        ),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(tool_call_timeout=60),
            collectors=[InputCollector()],
        )

    quoted_text_slot = pack.get_slot("input.quoted_text")
    assert quoted_text_slot is not None
    assert quoted_text_slot.value == "quoted message"
    assert "<Quoted Message>" not in quoted_text_slot.value

    quoted_images_slot = pack.get_slot("input.quoted_images")
    assert quoted_images_slot is not None
    assert quoted_images_slot.value == [
        {
            "ref": "file:///C:/tmp/quoted.png",
            "transport": "file",
            "source": "quoted",
            "resolution": "embedded",
            "reply_id": "reply-1",
        }
    ]

    files_slot = pack.get_slot("input.files")
    assert files_slot is not None
    assert files_slot.value == [
        {
            "name": "quoted.txt",
            "file": "C:/tmp/quoted.txt",
            "url": "",
            "source": "quoted",
            "reply_id": "reply-1",
        }
    ]


@pytest.mark.asyncio
async def test_collect_context_pack_collects_reply_chain_sidecar_annotations():
    event, extras = _make_event()
    event.message_obj.message = [
        Reply(
            id="reply-1",
            message_str="quoted message",
            chain=[
                Image(file="file:///C:/tmp/quoted.png"),
                File(name="quoted.txt", file="C:/tmp/quoted.txt"),
            ],
        )
    ]
    extras[INPUT_ITEM_ANNOTATIONS_EXTRA_KEY] = {
        INPUT_QUOTED_TEXT_ANNOTATION_KEY: {
            "semantic_type": "quoted_reference",
            "explanation": "This quoted text is reference material from an earlier message.",
            "explanation_source": "platform",
            "context_role": "reference",
        },
        build_reply_chain_annotation_key(0, 0): {
            "semantic_type": "quoted_screenshot",
            "explanation": "This quoted image is the screenshot referenced in the reply.",
            "explanation_source": "platform",
            "context_role": "reference",
        },
        build_reply_chain_annotation_key(0, 1): {
            "semantic_type": "quoted_log",
            "explanation": "This quoted file is the log attached in the replied message.",
            "explanation_source": "platform",
            "context_role": "reference",
        },
    }
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    with patch(
        "astrbot.core.prompt.collectors.input_collector.extract_quoted_message_text",
        new=AsyncMock(return_value="quoted message"),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(tool_call_timeout=60),
            collectors=[InputCollector()],
        )

    quoted_text_slot = pack.get_slot("input.quoted_text")
    assert quoted_text_slot is not None
    assert quoted_text_slot.meta["semantic_type"] == "quoted_reference"
    assert quoted_text_slot.meta["context_role"] == "reference"

    quoted_images_slot = pack.get_slot("input.quoted_images")
    assert quoted_images_slot is not None
    assert quoted_images_slot.value == [
        {
            "ref": "file:///C:/tmp/quoted.png",
            "transport": "file",
            "source": "quoted",
            "resolution": "embedded",
            "reply_id": "reply-1",
            "semantic_type": "quoted_screenshot",
            "explanation": "This quoted image is the screenshot referenced in the reply.",
            "explanation_source": "platform",
            "context_role": "reference",
        }
    ]

    files_slot = pack.get_slot("input.files")
    assert files_slot is not None
    assert files_slot.value == [
        {
            "name": "quoted.txt",
            "file": "C:/tmp/quoted.txt",
            "url": "",
            "source": "quoted",
            "reply_id": "reply-1",
            "semantic_type": "quoted_log",
            "explanation": "This quoted file is the log attached in the replied message.",
            "explanation_source": "platform",
            "context_role": "reference",
        }
    ]


@pytest.mark.asyncio
async def test_collect_context_pack_collects_current_image_captions():
    event, extras = _make_event()
    event.message_obj.message = [Image(file="https://example.com/image.png")]
    extras[INPUT_ITEM_ANNOTATIONS_EXTRA_KEY] = {
        build_message_annotation_key(0): {
            "semantic_type": "desktop_screenshot",
            "explanation": "This image is the user's current desktop screenshot.",
            "explanation_source": "platform",
            "context_role": "supporting",
        }
    }
    context = _make_context()
    req = ProviderRequest(prompt="describe this")
    req.conversation = _make_conversation()
    caption_provider = MagicMock()
    caption_provider.text_chat = AsyncMock(
        return_value=MagicMock(completion_text="A scenic test image.")
    )
    context.get_provider_by_id.return_value = caption_provider

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            provider_settings={
                "default_image_caption_provider_id": "caption-provider",
                "image_caption_prompt": "Please describe the image.",
            },
        ),
        provider_request=req,
        collectors=[InputCollector()],
    )

    captions_slot = pack.get_slot("input.image_captions")
    assert captions_slot is not None
    assert captions_slot.value == [
        {
            "ref": "https://example.com/image.png",
            "caption": "A scenic test image.",
            "provider_id": "caption-provider",
            "source": "current",
            "semantic_type": "desktop_screenshot",
            "explanation": "This image is the user's current desktop screenshot.",
            "explanation_source": "platform",
            "context_role": "supporting",
        }
    ]


@pytest.mark.asyncio
async def test_collect_context_pack_collects_quoted_image_captions_from_current_provider():
    event, extras = _make_event()
    event.message_obj.message = [
        Reply(
            id="reply-1",
            message_str="quoted image",
            chain=[Image(file="https://example.com/quoted.png")],
        )
    ]
    extras[INPUT_ITEM_ANNOTATIONS_EXTRA_KEY] = {
        build_reply_chain_annotation_key(0, 0): {
            "semantic_type": "quoted_screenshot",
            "explanation": "This quoted image is the screenshot referenced in the reply.",
            "explanation_source": "platform",
            "context_role": "reference",
        }
    }
    context = _make_context()
    caption_provider = MagicMock()
    caption_provider.provider_config = {"id": "active-provider"}
    caption_provider.text_chat = AsyncMock(
        return_value=MagicMock(completion_text="Quoted image caption.")
    )
    context.get_using_provider.return_value = caption_provider

    with patch(
        "astrbot.core.prompt.collectors.input_collector.extract_quoted_message_text",
        new=AsyncMock(return_value="quoted image"),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(tool_call_timeout=60),
            collectors=[InputCollector()],
        )

    captions_slot = pack.get_slot("input.quoted_image_captions")
    assert captions_slot is not None
    assert captions_slot.value == [
        {
            "ref": "https://example.com/quoted.png",
            "caption": "Quoted image caption.",
            "provider_id": "active-provider",
            "source": "quoted",
            "reply_id": "reply-1",
            "semantic_type": "quoted_screenshot",
            "explanation": "This quoted image is the screenshot referenced in the reply.",
            "explanation_source": "platform",
            "context_role": "reference",
        }
    ]


@pytest.mark.asyncio
async def test_collect_context_pack_collects_fallback_quoted_images_with_limit():
    event, _ = _make_event()
    event.message_obj.message = [Reply(id="reply-1", message_str="[Image]")]
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    with (
        patch(
            "astrbot.core.prompt.collectors.input_collector.extract_quoted_message_text",
            new=AsyncMock(return_value="quoted message"),
        ),
        patch(
            "astrbot.core.prompt.collectors.input_collector.extract_quoted_message_images",
            new=AsyncMock(
                return_value=[
                    "https://example.com/1.png",
                    "https://example.com/2.png",
                ]
            ),
        ),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                max_quoted_fallback_images=1,
            ),
            collectors=[InputCollector()],
        )

    quoted_images_slot = pack.get_slot("input.quoted_images")
    assert quoted_images_slot is not None
    assert quoted_images_slot.meta["fallback_count"] == 1
    assert quoted_images_slot.value == [
        {
            "ref": "https://example.com/1.png",
            "transport": "url",
            "source": "quoted",
            "resolution": "fallback",
            "reply_id": "reply-1",
        }
    ]


@pytest.mark.asyncio
async def test_collect_context_pack_collects_file_extracts(tmp_path):
    current_file = tmp_path / "current.txt"
    current_file.write_text("current", encoding="utf-8")
    quoted_file = tmp_path / "quoted.txt"
    quoted_file.write_text("quoted", encoding="utf-8")

    event, extras = _make_event()
    event.message_obj.message = [
        File(name="current.txt", file=str(current_file)),
        Reply(
            id="reply-1",
            message_str="quoted file",
            chain=[File(name="quoted.txt", file=str(quoted_file))],
        ),
    ]
    extras[INPUT_ITEM_ANNOTATIONS_EXTRA_KEY] = {
        build_message_annotation_key(0): {
            "semantic_type": "current_note",
            "explanation": "This file is the user's newly uploaded note.",
            "explanation_source": "platform",
            "context_role": "reference",
        },
        build_reply_chain_annotation_key(1, 0): {
            "semantic_type": "quoted_log",
            "explanation": "This file comes from the quoted reply.",
            "explanation_source": "platform",
            "context_role": "reference",
        },
    }
    context = _make_context()

    async def _extract(path: str, api_key: str) -> str:
        return f"{Path(path).name}:{api_key}"

    with patch(
        "astrbot.core.prompt.collectors.input_collector.extract_file_moonshotai",
        new=AsyncMock(side_effect=_extract),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                file_extract_enabled=True,
                file_extract_prov="moonshotai",
                file_extract_msh_api_key="secret-key",
            ),
            collectors=[InputCollector()],
        )

    extract_slot = pack.get_slot("input.file_extracts")
    assert extract_slot is not None
    assert extract_slot.value == [
        {
            "name": "current.txt",
            "content": "current.txt:secret-key",
            "provider": "moonshotai",
            "source": "current",
            "semantic_type": "current_note",
            "explanation": "This file is the user's newly uploaded note.",
            "explanation_source": "platform",
            "context_role": "reference",
        },
        {
            "name": "quoted.txt",
            "content": "quoted.txt:secret-key",
            "provider": "moonshotai",
            "source": "quoted",
            "reply_id": "reply-1",
            "semantic_type": "quoted_log",
            "explanation": "This file comes from the quoted reply.",
            "explanation_source": "platform",
            "context_role": "reference",
        },
    ]


@pytest.mark.asyncio
async def test_collect_context_pack_collects_session_slots_for_private_chat():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            timezone="Asia/Shanghai",
        ),
        collectors=[SessionCollector()],
    )

    assert pack.meta["collectors"] == ["SessionCollector"]
    datetime_slot = pack.get_slot("session.datetime")
    assert datetime_slot is not None
    assert datetime_slot.value["timezone"] == "Asia/Shanghai"
    assert datetime_slot.value["source"] == "config.timezone"
    assert datetime_slot.meta["from_config"] is True
    assert "T" in datetime_slot.value["iso"]

    user_slot = pack.get_slot("session.user_info")
    assert user_slot is not None
    assert user_slot.value == {
        "user_id": "user123",
        "nickname": "Tester",
        "platform_name": "test_platform",
        "umo": "test_platform:private:test-session",
        "group_id": None,
        "group_name": None,
        "is_group": False,
    }


@pytest.mark.asyncio
async def test_collect_context_pack_collects_group_session_info():
    event, _ = _make_event()
    event.unified_msg_origin = "test_platform:group:test-session"
    event.message_obj.group_id = "group-1"
    event.message_obj.group = MagicMock(group_name="Test Group")
    event.get_group_id.return_value = "group-1"
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[SessionCollector()],
    )

    user_slot = pack.get_slot("session.user_info")
    assert user_slot is not None
    assert user_slot.value["group_id"] == "group-1"
    assert user_slot.value["group_name"] == "Test Group"
    assert user_slot.value["is_group"] is True


@pytest.mark.asyncio
async def test_collect_context_pack_session_timezone_falls_back_to_global_config():
    event, _ = _make_event()
    context = _make_context()
    context.get_config.return_value = {"timezone": "UTC"}
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[SessionCollector()],
    )

    datetime_slot = pack.get_slot("session.datetime")
    assert datetime_slot is not None
    assert datetime_slot.value["timezone"] == "UTC"
    assert datetime_slot.value["source"] == "plugin_context.get_config"
    assert datetime_slot.meta["from_config"] is True


@pytest.mark.asyncio
async def test_collect_context_pack_session_invalid_timezone_falls_back_in_non_strict_mode():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            timezone="Not/A_Timezone",
        ),
        collectors=[SessionCollector()],
    )

    datetime_slot = pack.get_slot("session.datetime")
    assert datetime_slot is not None
    assert datetime_slot.value["source"] == "local_timezone"
    assert datetime_slot.meta["from_config"] is False


@pytest.mark.asyncio
async def test_collect_context_pack_session_invalid_timezone_raises_in_strict_mode():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    with pytest.raises(RuntimeError, match="Failed to collect session datetime"):
        await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                timezone="Not/A_Timezone",
                prompt_pipeline_strict_mode=True,
            ),
            collectors=[SessionCollector()],
        )


@pytest.mark.asyncio
async def test_collect_context_pack_session_uses_local_timezone_when_unconfigured_in_strict_mode():
    event, _ = _make_event()
    context = _make_context()
    context.get_config.return_value = {}
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            prompt_pipeline_strict_mode=True,
        ),
        collectors=[SessionCollector()],
    )

    datetime_slot = pack.get_slot("session.datetime")
    assert datetime_slot is not None
    assert datetime_slot.value["source"] == "local_timezone"


@pytest.mark.asyncio
async def test_collect_context_pack_session_handles_missing_group_object():
    event, _ = _make_event()
    event.message_obj.group_id = "group-1"
    event.message_obj.group = None
    event.get_group_id.return_value = "group-1"
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[SessionCollector()],
    )

    user_slot = pack.get_slot("session.user_info")
    assert user_slot is not None
    assert user_slot.value["group_id"] == "group-1"
    assert user_slot.value["group_name"] is None
    assert user_slot.value["is_group"] is True


@pytest.mark.asyncio
async def test_collect_context_pack_session_fail_open_with_partial_event_data():
    event, _ = _make_event()
    event.message_obj.sender = None
    event.get_platform_name.side_effect = RuntimeError("missing platform")
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[SessionCollector()],
    )

    datetime_slot = pack.get_slot("session.datetime")
    assert datetime_slot is not None
    user_slot = pack.get_slot("session.user_info")
    assert user_slot is not None
    assert user_slot.value["user_id"] is None
    assert user_slot.value["nickname"] is None
    assert user_slot.value["platform_name"] is None
    assert user_slot.value["umo"] == "test_platform:private:test-session"


@pytest.mark.asyncio
async def test_collect_context_pack_default_collectors_include_session_collector():
    event, _ = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="hello")
    req.conversation = _make_conversation()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        provider_request=req,
    )

    assert pack.meta["collectors"] == [
        "SystemCollector",
        "PersonaCollector",
        "InputCollector",
        "SessionCollector",
        "PolicyCollector",
        "MemoryCollector",
        "ConversationHistoryCollector",
        "SkillsCollector",
        "ToolsCollector",
        "SubagentCollector",
        "KnowledgeCollector",
    ]
    assert pack.get_slot("system.base") is None
    assert pack.get_slot("system.tool_call_instruction") is not None
    assert pack.get_slot("policy.local_env_prompt") is not None
    assert pack.get_slot("session.datetime") is not None
    assert pack.get_slot("session.user_info") is not None
    assert pack.get_slot("policy.safety_prompt") is not None
    assert pack.get_slot("capability.skills_prompt") is None
    assert pack.get_slot("capability.tools_schema") is None
    assert pack.get_slot("capability.subagent_handoff_tools") is None
    assert pack.get_slot("capability.subagent_router_prompt") is None
    assert pack.get_slot("knowledge.snippets") is None


@pytest.mark.asyncio
async def test_collect_context_pack_collects_policy_safety_prompt_when_enabled():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            llm_safety_mode=True,
            safety_mode_strategy="system_prompt",
        ),
        collectors=[PolicyCollector()],
    )

    safety_slot = pack.get_slot("policy.safety_prompt")
    assert safety_slot is not None
    assert safety_slot.value == LLM_SAFETY_MODE_SYSTEM_PROMPT
    assert safety_slot.meta["enabled_by_config"] is True
    assert safety_slot.meta["strategy"] == "system_prompt"


@pytest.mark.asyncio
async def test_collect_context_pack_collects_system_base_from_provider_request():
    event, _ = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="hello", system_prompt="Base system prompt")

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        provider_request=req,
        collectors=[SystemCollector()],
    )

    base_slot = pack.get_slot("system.base")
    assert base_slot is not None
    assert base_slot.value == "Base system prompt"
    assert base_slot.meta["source_field"] == "provider_request.system_prompt"


@pytest.mark.asyncio
async def test_collect_context_pack_collects_workspace_extra_prompt(tmp_path):
    event, _ = _make_event()
    context = _make_context()
    workspace_dir = tmp_path / "normalized-umo"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    extra_prompt_path = workspace_dir / "EXTRA_PROMPT.md"
    extra_prompt_path.write_text("Workspace-specific instruction.", encoding="utf-8")

    with (
        patch(
            "astrbot.core.prompt.collectors.system_collector.get_astrbot_workspaces_path",
            return_value=str(tmp_path),
        ),
        patch(
            "astrbot.core.prompt.collectors.system_collector.normalize_umo_for_workspace",
            return_value="normalized-umo",
        ),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(tool_call_timeout=60),
            collectors=[SystemCollector()],
        )

    workspace_slot = pack.get_slot("system.workspace_extra_prompt")
    assert workspace_slot is not None
    assert workspace_slot.value == {
        "path": str(extra_prompt_path),
        "text": "Workspace-specific instruction.",
    }
    assert workspace_slot.meta["source_field"] == "workspace/EXTRA_PROMPT.md"


@pytest.mark.asyncio
async def test_collect_context_pack_collects_full_tool_call_instruction():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )
    context.get_llm_tool_manager.return_value.get_full_tool_set.return_value = ToolSet(
        [_make_tool("search_docs", description="Search docs.")]
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            tool_schema_mode="full",
            computer_use_runtime="none",
            add_cron_tools=False,
        ),
        collectors=[SystemCollector()],
    )

    instruction_slot = pack.get_slot("system.tool_call_instruction")
    assert instruction_slot is not None
    assert instruction_slot.value == ama.TOOL_CALL_PROMPT
    assert instruction_slot.meta["tool_schema_mode"] == "full"


@pytest.mark.asyncio
async def test_collect_context_pack_collects_skills_like_tool_call_instruction():
    event, _ = _make_event()
    context = _make_context()

    with (
        patch(
            "astrbot.core.prompt.collectors.system_collector.get_astrbot_workspaces_path",
            return_value="C:/AstrBot/workspaces",
        ),
        patch(
            "astrbot.core.prompt.collectors.system_collector.normalize_umo_for_workspace",
            return_value="normalized-umo",
        ),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                tool_schema_mode="skills-like",
                computer_use_runtime="local",
                add_cron_tools=False,
            ),
            collectors=[SystemCollector()],
        )

    instruction_slot = pack.get_slot("system.tool_call_instruction")
    assert instruction_slot is not None
    assert instruction_slot.value.startswith(ama.TOOL_CALL_PROMPT_SKILLS_LIKE_MODE)
    assert "normalized-umo" in instruction_slot.value
    assert instruction_slot.meta["tool_schema_mode"] == "skills-like"
    assert instruction_slot.meta["runtime"] == "local"


@pytest.mark.asyncio
async def test_collect_context_pack_skips_tool_call_instruction_when_no_tools_available():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            kb_agentic_mode=False,
            computer_use_runtime="none",
            add_cron_tools=False,
        ),
        collectors=[SystemCollector()],
    )

    assert pack.get_slot("system.tool_call_instruction") is None


@pytest.mark.asyncio
async def test_collect_context_pack_collects_tool_call_instruction_for_agentic_kb_mode():
    event, _ = _make_event()
    context = _make_context()

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            kb_agentic_mode=True,
            computer_use_runtime="none",
            add_cron_tools=False,
        ),
        collectors=[SystemCollector()],
    )

    instruction_slot = pack.get_slot("system.tool_call_instruction")
    assert instruction_slot is not None
    assert instruction_slot.value == ama.TOOL_CALL_PROMPT


@pytest.mark.asyncio
async def test_collect_context_pack_skips_policy_safety_prompt_when_disabled():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            llm_safety_mode=False,
        ),
        collectors=[PolicyCollector()],
    )

    assert pack.get_slot("policy.safety_prompt") is None


@pytest.mark.asyncio
async def test_collect_context_pack_collects_policy_sandbox_prompt_for_sandbox_runtime():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            computer_use_runtime="sandbox",
        ),
        collectors=[PolicyCollector()],
    )

    sandbox_slot = pack.get_slot("policy.sandbox_prompt")
    assert sandbox_slot is not None
    assert sandbox_slot.value.startswith(SANDBOX_MODE_PROMPT)
    assert "[Shipyard Neo File Path Rule]" in sandbox_slot.value
    assert "[Neo Skill Lifecycle Workflow]" in sandbox_slot.value
    assert sandbox_slot.meta["enabled_by_config"] is True
    assert sandbox_slot.meta["runtime"] == "sandbox"
    assert sandbox_slot.meta["booter"] == "shipyard_neo"


@pytest.mark.asyncio
async def test_collect_context_pack_collects_policy_local_env_prompt_for_local_runtime():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            computer_use_runtime="local",
        ),
        collectors=[PolicyCollector()],
    )

    local_env_slot = pack.get_slot("policy.local_env_prompt")
    assert local_env_slot is not None
    assert "host local environment" in local_env_slot.value
    assert local_env_slot.meta["enabled_by_config"] is True
    assert local_env_slot.meta["runtime"] == "local"


@pytest.mark.asyncio
async def test_collect_context_pack_skips_policy_sandbox_prompt_for_local_runtime():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            computer_use_runtime="local",
        ),
        collectors=[PolicyCollector()],
    )

    assert pack.get_slot("policy.sandbox_prompt") is None
    assert pack.get_slot("policy.local_env_prompt") is not None


@pytest.mark.asyncio
async def test_collect_context_pack_collects_live_mode_prompt():
    event, extras = _make_event()
    extras["action_type"] = "live"
    context = _make_context()

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[SystemCollector()],
    )

    live_slot = pack.get_slot("system.live_mode_prompt")
    assert live_slot is not None
    assert live_slot.value == LIVE_MODE_SYSTEM_PROMPT
    assert live_slot.meta["action_type"] == "live"


@pytest.mark.asyncio
async def test_collect_context_pack_collects_memory_slots_from_snapshot(
    _patch_memory_service,
):
    event, _ = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="effective prompt")
    req.conversation = _make_conversation()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )
    snapshot = MemorySnapshot(
        umo=event.unified_msg_origin,
        conversation_id="conv-id",
        topic_state=TopicState(
            umo=event.unified_msg_origin,
            conversation_id="conv-id",
            current_topic="Prompt pipeline",
            topic_summary="We are designing a memory collector.",
            topic_confidence=0.92,
            last_active_at=datetime(2026, 4, 5, 12, 30, 0),
        ),
        short_term_memory=ShortTermMemory(
            umo=event.unified_msg_origin,
            conversation_id="conv-id",
            short_summary="Discussed memory snapshot integration.",
            active_focus="MemoryCollector v1",
            updated_at=datetime(2026, 4, 5, 12, 31, 0),
        ),
        experiences=[
            Experience(
                experience_id="exp-1",
                umo=event.unified_msg_origin,
                conversation_id="conv-id",
                platform_user_key="test:user-1",
                canonical_user_id="canonical-user-1",
                scope_type="user",
                scope_id="canonical-user-1",
                event_time=datetime(2026, 4, 5, 12, 20, 0),
                category="project_progress",
                summary="Implemented snapshot integration.",
                detail_summary="The memory snapshot now returns more layers.",
                importance=0.81,
                confidence=0.9,
                source_refs=["turn:1"],
                updated_at=datetime(2026, 4, 5, 12, 32, 0),
            )
        ],
        long_term_memories=[
            LongTermMemoryIndex(
                memory_id="ltm-1",
                umo=event.unified_msg_origin,
                canonical_user_id="canonical-user-1",
                scope_type="user",
                scope_id="canonical-user-1",
                category="project_progress",
                title="Snapshot roadmap",
                summary="The project is exposing long-term memory through snapshot.",
                status="active",
                doc_path="ignored.md",
                importance=0.88,
                confidence=0.91,
                tags=["snapshot", "memory"],
                source_refs=["exp:1"],
                first_event_at=datetime(2026, 4, 5, 12, 0, 0),
                last_event_at=datetime(2026, 4, 5, 12, 20, 0),
                updated_at=datetime(2026, 4, 5, 12, 33, 0),
            )
        ],
        persona_state=PersonaState(
            state_id="persona-state-1",
            scope_type="user",
            scope_id="canonical-user-1",
            persona_id=None,
            familiarity=0.6,
            trust=0.72,
            warmth=0.64,
            formality_preference=0.35,
            directness_preference=0.88,
            updated_at=datetime(2026, 4, 5, 12, 34, 0),
        ),
    )
    _patch_memory_service.get_snapshot.return_value = snapshot

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        provider_request=req,
        collectors=[MemoryCollector()],
    )

    _patch_memory_service.get_snapshot.assert_awaited_once_with(
        umo=event.unified_msg_origin,
        conversation_id="conv-id",
        query="effective prompt",
    )

    topic_slot = pack.get_slot("memory.topic_state")
    assert topic_slot is not None
    assert topic_slot.value == {
        "umo": event.unified_msg_origin,
        "conversation_id": "conv-id",
        "current_topic": "Prompt pipeline",
        "topic_summary": "We are designing a memory collector.",
        "topic_confidence": 0.92,
        "last_active_at": "2026-04-05T12:30:00",
    }
    assert topic_slot.meta["snapshot_field"] == "topic_state"

    short_term_slot = pack.get_slot("memory.short_term")
    assert short_term_slot is not None
    assert short_term_slot.value == {
        "umo": event.unified_msg_origin,
        "conversation_id": "conv-id",
        "short_summary": "Discussed memory snapshot integration.",
        "active_focus": "MemoryCollector v1",
        "updated_at": "2026-04-05T12:31:00",
    }
    assert short_term_slot.meta["snapshot_field"] == "short_term_memory"

    experiences_slot = pack.get_slot("memory.experiences")
    assert experiences_slot is not None
    assert experiences_slot.value == {
        "count": 1,
        "items": [
            {
                "experience_id": "exp-1",
                "umo": event.unified_msg_origin,
                "conversation_id": "conv-id",
                "scope_type": "user",
                "scope_id": "canonical-user-1",
                "category": "project_progress",
                "summary": "Implemented snapshot integration.",
                "detail_summary": "The memory snapshot now returns more layers.",
                "importance": 0.81,
                "confidence": 0.9,
                "event_time": "2026-04-05T12:20:00",
                "updated_at": "2026-04-05T12:32:00",
                "source_refs": ["turn:1"],
            }
        ],
    }
    assert experiences_slot.meta["snapshot_field"] == "experiences"

    long_term_slot = pack.get_slot("memory.long_term_memories")
    assert long_term_slot is not None
    assert long_term_slot.value == {
        "count": 1,
        "items": [
            {
                "memory_id": "ltm-1",
                "umo": event.unified_msg_origin,
                "scope_type": "user",
                "scope_id": "canonical-user-1",
                "category": "project_progress",
                "title": "Snapshot roadmap",
                "summary": "The project is exposing long-term memory through snapshot.",
                "status": "active",
                "importance": 0.88,
                "confidence": 0.91,
                "tags": ["snapshot", "memory"],
                "source_refs": ["exp:1"],
                "first_event_at": "2026-04-05T12:00:00",
                "last_event_at": "2026-04-05T12:20:00",
                "updated_at": "2026-04-05T12:33:00",
            }
        ],
    }
    assert long_term_slot.meta["snapshot_field"] == "long_term_memories"

    persona_state_slot = pack.get_slot("memory.persona_state")
    assert persona_state_slot is not None
    assert persona_state_slot.value == {
        "state_id": "persona-state-1",
        "scope_type": "user",
        "scope_id": "canonical-user-1",
        "persona_id": None,
        "familiarity": 0.6,
        "trust": 0.72,
        "warmth": 0.64,
        "formality_preference": 0.35,
        "directness_preference": 0.88,
        "updated_at": "2026-04-05T12:34:00",
    }
    assert persona_state_slot.meta["snapshot_field"] == "persona_state"


@pytest.mark.asyncio
async def test_collect_context_pack_memory_skips_empty_snapshot(_patch_memory_service):
    event, _ = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="hello")
    req.conversation = _make_conversation()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )
    _patch_memory_service.get_snapshot.return_value = MemorySnapshot(
        umo=event.unified_msg_origin,
        conversation_id="conv-id",
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        provider_request=req,
        collectors=[MemoryCollector()],
    )

    assert pack.get_slot("memory.topic_state") is None
    assert pack.get_slot("memory.short_term") is None
    assert pack.get_slot("memory.experiences") is None
    assert pack.get_slot("memory.long_term_memories") is None
    assert pack.get_slot("memory.persona_state") is None


@pytest.mark.asyncio
async def test_collect_context_pack_memory_uses_none_conversation_id_without_request(
    _patch_memory_service,
):
    event, _ = _make_event()
    event.message_str = "raw event text"
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[MemoryCollector()],
    )

    _patch_memory_service.get_snapshot.assert_awaited_once_with(
        umo=event.unified_msg_origin,
        conversation_id=None,
        query="raw event text",
    )


@pytest.mark.asyncio
async def test_collect_context_pack_memory_fail_open_when_snapshot_request_raises(
    _patch_memory_service,
):
    event, _ = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="hello")
    req.conversation = _make_conversation()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )
    _patch_memory_service.get_snapshot.side_effect = RuntimeError("memory down")

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        provider_request=req,
        collectors=[MemoryCollector()],
    )

    assert pack.get_slot("memory.topic_state") is None
    assert pack.get_slot("memory.short_term") is None
    assert pack.get_slot("memory.experiences") is None
    assert pack.get_slot("memory.long_term_memories") is None
    assert pack.get_slot("memory.persona_state") is None


@pytest.mark.asyncio
async def test_collect_context_pack_collects_conversation_history_from_conversation():
    event, _ = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="hello")
    req.conversation = _make_conversation()
    req.conversation.history = (
        '[{"role":"user","content":"hi"},{"role":"assistant","content":"hello there"}]'
    )
    req.contexts = [{"role": "user", "content": "ignored fallback"}]
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        provider_request=req,
        collectors=[ConversationHistoryCollector()],
    )

    history_slot = pack.get_slot("conversation.history")
    assert history_slot is not None
    assert history_slot.value == {
        "format": "turn_pairs",
        "source": "provider_request.conversation.history",
        "conversation_id": "conv-id",
        "turn_count": 1,
        "turns": [
            {
                "user_message": {"role": "user", "content": "hi"},
                "assistant_message": {
                    "role": "assistant",
                    "content": "hello there",
                },
            }
        ],
    }
    assert history_slot.meta["format"] == "turn_pairs"
    assert history_slot.meta["turn_count"] == 1


@pytest.mark.asyncio
async def test_collect_context_pack_conversation_history_falls_back_to_contexts():
    event, _ = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="hello")
    req.conversation = _make_conversation()
    req.conversation.history = '[{"role":"user","content":"dangling user only"}]'
    req.contexts = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "draft", "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "content": "tool result", "tool_call_id": "call-1"},
        {"role": "assistant", "content": "final answer"},
    ]
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        provider_request=req,
        collectors=[ConversationHistoryCollector()],
    )

    history_slot = pack.get_slot("conversation.history")
    assert history_slot is not None
    assert history_slot.value["source"] == "provider_request.contexts"
    assert history_slot.value["turn_count"] == len(history_slot.value["turns"]) == 1
    assert history_slot.value["turns"] == [
        {
            "user_message": {"role": "user", "content": "question"},
            "assistant_message": {
                "role": "assistant",
                "content": "final answer",
            },
        }
    ]


@pytest.mark.asyncio
async def test_collect_context_pack_conversation_history_skips_when_unavailable():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[ConversationHistoryCollector()],
    )

    assert pack.get_slot("conversation.history") is None


@pytest.mark.asyncio
async def test_collect_context_pack_conversation_history_fail_open_when_parse_raises():
    event, _ = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="hello")
    req.conversation = _make_conversation()
    req.conversation.history = '[{"role":"user","content":"hi"}]'
    req.contexts = [{"role": "user", "content": "fallback only"}]
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    with patch(
        "astrbot.core.prompt.collectors.conversation_history_collector.extract_turn_payloads",
        side_effect=RuntimeError("history boom"),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(tool_call_timeout=60),
            provider_request=req,
            collectors=[ConversationHistoryCollector()],
        )

    assert pack.get_slot("conversation.history") is None


@pytest.mark.asyncio
async def test_collect_context_pack_collects_skills_inventory_for_local_runtime():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )
    skills = [
        SkillInfo(
            name="docs4agent",
            description="Write concise technical docs.",
            path="C:/skills/docs4agent/SKILL.md",
            active=True,
            source_type="local_only",
            source_label="local",
            local_exists=True,
            sandbox_exists=False,
        )
    ]

    with patch(
        "astrbot.core.prompt.collectors.skills_collector.SkillManager.list_skills",
        return_value=skills,
    ) as mock_list_skills:
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                computer_use_runtime="local",
            ),
            collectors=[SkillsCollector()],
        )

    mock_list_skills.assert_called_once_with(active_only=True, runtime="local")
    skills_slot = pack.get_slot("capability.skills_prompt")
    assert skills_slot is not None
    assert skills_slot.value == {
        "format": "skills_inventory_v1",
        "runtime": "local",
        "skill_count": 1,
        "skills": [
            {
                "name": "docs4agent",
                "description": "Write concise technical docs.",
                "path": "C:/skills/docs4agent/SKILL.md",
                "source_type": "local_only",
                "source_label": "local",
                "active": True,
                "local_exists": True,
                "sandbox_exists": False,
            }
        ],
    }


@pytest.mark.asyncio
async def test_collect_context_pack_collects_skills_inventory_for_sandbox_runtime():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )
    skills = [
        SkillInfo(
            name="browser_skill",
            description="Browser workflow skill.",
            path="/workspace/skills/browser_skill/SKILL.md",
            active=True,
            source_type="sandbox_only",
            source_label="sandbox_preset",
            local_exists=False,
            sandbox_exists=True,
        )
    ]

    with patch(
        "astrbot.core.prompt.collectors.skills_collector.SkillManager.list_skills",
        return_value=skills,
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                computer_use_runtime="sandbox",
            ),
            collectors=[SkillsCollector()],
        )

    skills_slot = pack.get_slot("capability.skills_prompt")
    assert skills_slot is not None
    assert skills_slot.value["runtime"] == "sandbox"
    assert skills_slot.value["skill_count"] == len(skills_slot.value["skills"]) == 1
    assert skills_slot.value["skills"][0]["path"] == (
        "/workspace/skills/browser_skill/SKILL.md"
    )
    assert skills_slot.value["skills"][0]["source_type"] == "sandbox_only"
    assert skills_slot.value["skills"][0]["source_label"] == "sandbox_preset"


@pytest.mark.asyncio
async def test_collect_context_pack_skips_skills_slot_when_no_active_skills():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    with patch(
        "astrbot.core.prompt.collectors.skills_collector.SkillManager.list_skills",
        return_value=[],
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(tool_call_timeout=60),
            collectors=[SkillsCollector()],
        )

    assert pack.get_slot("capability.skills_prompt") is None


@pytest.mark.asyncio
async def test_collect_context_pack_skills_fail_open_when_skill_manager_raises():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    with patch(
        "astrbot.core.prompt.collectors.skills_collector.SkillManager.list_skills",
        side_effect=RuntimeError("skills boom"),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(tool_call_timeout=60),
            collectors=[SkillsCollector()],
        )

    assert pack.get_slot("capability.skills_prompt") is None


@pytest.mark.asyncio
async def test_collect_context_pack_collects_tools_inventory_from_full_toolset():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )
    search_tool = _make_tool(
        "search_docs",
        description="Search project docs.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query.",
                }
            },
            "required": ["query"],
        },
    )
    inactive_tool = _make_tool(
        "disabled_tool",
        description="Disabled tool.",
        active=False,
    )
    context.get_llm_tool_manager.return_value.get_full_tool_set.return_value = ToolSet(
        [search_tool, inactive_tool]
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[ToolsCollector()],
    )

    context.get_llm_tool_manager.return_value.get_full_tool_set.assert_called_once_with()
    tools_slot = pack.get_slot("capability.tools_schema")
    assert tools_slot is not None
    assert tools_slot.value == {
        "format": "tool_inventory_v1",
        "tool_count": 1,
        "tools": [
            {
                "name": "search_docs",
                "description": "Search project docs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query.",
                        }
                    },
                    "required": ["query"],
                },
                "active": True,
                "handler_module_path": "tests.prompt_tools",
                "schema": {
                    "type": "function",
                    "function": {
                        "name": "search_docs",
                        "description": "Search project docs.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Search query.",
                                }
                            },
                            "required": ["query"],
                        },
                    },
                },
            }
        ],
    }
    assert tools_slot.meta["selection_mode"] == "all"


@pytest.mark.asyncio
async def test_collect_context_pack_collects_tools_inventory_with_persona_whitelist():
    event, _ = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="hello")
    req.conversation = _make_conversation(persona_id="persona-a")
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(
            "persona-a",
            {
                "prompt": "persona prompt",
                "tools": ["search_docs"],
            },
            None,
            False,
        )
    )
    search_tool = _make_tool("search_docs", description="Search docs.")
    other_tool = _make_tool("other_tool", description="Unused tool.")
    context.get_llm_tool_manager.return_value.get_func.side_effect = lambda name: {
        "search_docs": search_tool,
        "other_tool": other_tool,
    }.get(name)

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        provider_request=req,
        collectors=[ToolsCollector()],
    )

    tools_slot = pack.get_slot("capability.tools_schema")
    assert tools_slot is not None
    assert tools_slot.value["tool_count"] == 1
    assert tools_slot.value["tools"][0]["name"] == "search_docs"
    assert tools_slot.meta["persona_id"] == "persona-a"
    assert tools_slot.meta["selection_mode"] == "whitelist"


@pytest.mark.asyncio
async def test_collect_context_pack_skips_tools_slot_when_persona_disables_tools():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(
            "persona-a",
            {
                "prompt": "persona prompt",
                "tools": [],
            },
            None,
            False,
        )
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[ToolsCollector()],
    )

    assert pack.get_slot("capability.tools_schema") is None


@pytest.mark.asyncio
async def test_collect_context_pack_tools_fail_open_when_tool_manager_raises():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )
    context.get_llm_tool_manager.return_value.get_full_tool_set.side_effect = (
        RuntimeError("tools boom")
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[ToolsCollector()],
    )

    assert pack.get_slot("capability.tools_schema") is None


@pytest.mark.asyncio
async def test_collect_context_pack_skips_subagent_slots_when_main_enable_disabled():
    event, _ = _make_event()
    context = _make_context()
    context.get_config.return_value = {
        "subagent_orchestrator": {
            "main_enable": False,
            "remove_main_duplicate_tools": True,
            "router_system_prompt": "route to planner",
        }
    }
    context.subagent_orchestrator = MagicMock(
        handoffs=[_make_handoff_tool("planner", description="planner")]
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[SubagentCollector()],
    )

    assert pack.get_slot("capability.subagent_handoff_tools") is None
    assert pack.get_slot("capability.subagent_router_prompt") is None


@pytest.mark.asyncio
async def test_collect_context_pack_collects_subagent_handoff_tools_inventory():
    event, _ = _make_event()
    context = _make_context()
    handoff = _make_handoff_tool(
        "planner",
        description="Delegate planning tasks.",
        parameters={
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "Task input.",
                }
            },
            "required": ["input"],
        },
    )
    context.get_config.return_value = {
        "subagent_orchestrator": {
            "main_enable": True,
            "remove_main_duplicate_tools": True,
        }
    }
    context.subagent_orchestrator = MagicMock(handoffs=[handoff])

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[SubagentCollector()],
    )

    handoff_slot = pack.get_slot("capability.subagent_handoff_tools")
    assert handoff_slot is not None
    assert handoff_slot.value == {
        "format": "handoff_tools_v1",
        "main_enable": True,
        "remove_main_duplicate_tools": True,
        "tool_count": 1,
        "tools": [
            {
                "name": "transfer_to_planner",
                "description": "Delegate planning tasks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "string",
                            "description": "Task input.",
                        }
                    },
                    "required": ["input"],
                },
            }
        ],
    }
    assert handoff_slot.meta["format"] == "handoff_tools_v1"
    assert handoff_slot.meta["tool_count"] == 1
    assert "agent" not in handoff_slot.value["tools"][0]
    assert "provider_id" not in handoff_slot.value["tools"][0]


@pytest.mark.asyncio
async def test_collect_context_pack_collects_subagent_router_prompt():
    event, _ = _make_event()
    context = _make_context()
    context.get_config.return_value = {
        "subagent_orchestrator": {
            "main_enable": True,
            "remove_main_duplicate_tools": False,
            "router_system_prompt": "Route work to the best subagent.",
        }
    }
    context.subagent_orchestrator = MagicMock(handoffs=[])

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[SubagentCollector()],
    )

    router_slot = pack.get_slot("capability.subagent_router_prompt")
    assert router_slot is not None
    assert router_slot.value == "Route work to the best subagent."
    assert router_slot.meta["enabled_by_config"] is True
    assert router_slot.meta["main_enable"] is True
    assert router_slot.meta["source"] == "subagent_orchestrator.router_system_prompt"


@pytest.mark.asyncio
async def test_collect_context_pack_skips_subagent_slots_when_orchestrator_missing():
    event, _ = _make_event()
    context = _make_context()
    context.get_config.return_value = {
        "subagent_orchestrator": {
            "main_enable": True,
            "router_system_prompt": "Route work to the best subagent.",
        }
    }
    context.subagent_orchestrator = None

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[SubagentCollector()],
    )

    assert pack.get_slot("capability.subagent_handoff_tools") is None
    assert pack.get_slot("capability.subagent_router_prompt") is None


@pytest.mark.asyncio
async def test_collect_context_pack_collects_knowledge_snippets_for_non_agentic_mode():
    event, _ = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="test question")

    with patch(
        "astrbot.core.prompt.collectors.knowledge_collector.retrieve_knowledge_base_with_cache",
        AsyncMock(return_value="KB result"),
    ) as mock_retrieve:
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                kb_agentic_mode=False,
            ),
            provider_request=req,
            collectors=[KnowledgeCollector()],
        )

    mock_retrieve.assert_awaited_once_with(
        query="test question",
        umo=event.unified_msg_origin,
        context=context,
        event=event,
    )
    knowledge_slot = pack.get_slot("knowledge.snippets")
    assert knowledge_slot is not None
    assert knowledge_slot.value == {
        "format": "kb_text_block_v1",
        "query": "test question",
        "text": "KB result",
    }
    assert knowledge_slot.meta["query_source"] == "provider_request.prompt"
    assert knowledge_slot.meta["kb_agentic_mode"] is False


@pytest.mark.asyncio
async def test_collect_context_pack_knowledge_uses_event_message_as_fallback_query():
    event, _ = _make_event()
    event.message_str = "fallback query"
    context = _make_context()

    with patch(
        "astrbot.core.prompt.collectors.knowledge_collector.retrieve_knowledge_base_with_cache",
        AsyncMock(return_value="KB fallback result"),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                kb_agentic_mode=False,
            ),
            collectors=[KnowledgeCollector()],
        )

    knowledge_slot = pack.get_slot("knowledge.snippets")
    assert knowledge_slot is not None
    assert knowledge_slot.value["query"] == "fallback query"
    assert knowledge_slot.meta["query_source"] == "event.message_str"


@pytest.mark.asyncio
async def test_collect_context_pack_skips_knowledge_slot_when_query_missing():
    event, _ = _make_event()
    event.message_str = ""
    context = _make_context()

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(
            tool_call_timeout=60,
            kb_agentic_mode=False,
        ),
        collectors=[KnowledgeCollector()],
    )

    assert pack.get_slot("knowledge.snippets") is None


@pytest.mark.asyncio
async def test_collect_context_pack_skips_knowledge_slot_when_no_result():
    event, _ = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="test question")

    with patch(
        "astrbot.core.prompt.collectors.knowledge_collector.retrieve_knowledge_base_with_cache",
        AsyncMock(return_value=None),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                kb_agentic_mode=False,
            ),
            provider_request=req,
            collectors=[KnowledgeCollector()],
        )

    assert pack.get_slot("knowledge.snippets") is None


@pytest.mark.asyncio
async def test_collect_context_pack_skips_knowledge_slot_for_agentic_mode():
    event, _ = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="test question")

    with patch(
        "astrbot.core.prompt.collectors.knowledge_collector.retrieve_knowledge_base_with_cache",
        AsyncMock(return_value="KB result"),
    ) as mock_retrieve:
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                kb_agentic_mode=True,
            ),
            provider_request=req,
            collectors=[KnowledgeCollector()],
        )

    mock_retrieve.assert_not_called()
    assert pack.get_slot("knowledge.snippets") is None


@pytest.mark.asyncio
async def test_collect_context_pack_knowledge_fail_open_when_retrieve_raises():
    event, _ = _make_event()
    context = _make_context()
    req = ProviderRequest(prompt="test question")

    with patch(
        "astrbot.core.prompt.collectors.knowledge_collector.retrieve_knowledge_base_with_cache",
        AsyncMock(side_effect=RuntimeError("kb boom")),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                kb_agentic_mode=False,
            ),
            provider_request=req,
            collectors=[KnowledgeCollector()],
        )

    assert pack.get_slot("knowledge.snippets") is None


class _BrokenCollector(ContextCollectorInterface):
    async def collect(self, event, plugin_context, config, provider_request=None):
        raise RuntimeError("boom")


class _StaticCollector(ContextCollectorInterface):
    async def collect(self, event, plugin_context, config, provider_request=None):
        return [
            ContextSlot(
                name="input.text",
                value="hello",
                category="input",
                source="test",
            )
        ]


@pytest.mark.asyncio
async def test_collect_context_pack_fail_open_when_a_collector_raises():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[_BrokenCollector(), _StaticCollector()],
    )

    text_slot = pack.get_slot("input.text")
    assert text_slot is not None
    assert text_slot.value == "hello"


@pytest.mark.asyncio
async def test_collect_context_pack_raises_when_collector_fails_in_strict_mode():
    event, _ = _make_event()
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    with pytest.raises(RuntimeError, match="Prompt context collector failed"):
        await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                prompt_pipeline_strict_mode=True,
            ),
            collectors=[_BrokenCollector(), _StaticCollector()],
        )


class _ExtensionCollectorAlpha(PromptExtensionCollectorInterface):
    @property
    def plugin_id(self) -> str:
        return "alpha.plugin"

    @property
    def priority(self) -> int:
        return 20

    async def collect(self, event, plugin_context, config, provider_request=None):
        return [
            PromptExtension(
                plugin_id="alpha.plugin",
                mount="system",
                title="Second",
                value={"value": "second"},
                order=20,
            ),
            PromptExtension(
                plugin_id="alpha.plugin",
                mount="system",
                title="First",
                value={"value": "first"},
                order=10,
            ),
            PromptExtension(
                plugin_id="alpha.plugin",
                mount="input",
                value="Input sidecar",
                value_kind="text",
            ),
            PromptExtension(
                plugin_id="alpha.plugin",
                mount="context",
                title="Runtime Context",
                value={"state": "ready"},
                order=30,
            ),
            PromptExtension(
                plugin_id="",
                mount="memory",
                value={"skip": True},
            ),
        ]


class _ExtensionCollectorBeta(PromptExtensionCollectorInterface):
    @property
    def plugin_id(self) -> str:
        return "beta.plugin"

    @property
    def priority(self) -> int:
        return 50

    async def collect(self, event, plugin_context, config, provider_request=None):
        return [
            PromptExtension(
                plugin_id="beta.plugin",
                mount="system",
                title="Beta",
                value={"value": "beta"},
                order=5,
            ),
            PromptExtension(
                plugin_id="beta.plugin",
                mount="conversation",
                value={"topic": "route notes"},
            ),
        ]


class _BrokenExtensionCollector(PromptExtensionCollectorInterface):
    @property
    def plugin_id(self) -> str:
        return "broken.plugin"

    async def collect(self, event, plugin_context, config, provider_request=None):
        raise RuntimeError("extension boom")


@pytest.mark.asyncio
async def test_context_prompt_extension_collector_registry_orders_and_cleans_up():
    context = _make_real_context()

    first = _ExtensionCollectorBeta()
    second = _ExtensionCollectorAlpha()
    context.register_prompt_extension_collector(first)
    context.register_prompt_extension_collector(second)

    ordered = context.list_prompt_extension_collectors()
    assert ordered == [second, first]

    removed = context.remove_prompt_extension_collectors_by_module_prefix(
        __name__,
    )
    assert removed == 2
    assert context.list_prompt_extension_collectors() == []


@pytest.mark.asyncio
async def test_collect_context_pack_collects_prompt_extensions_into_extension_slots():
    event, _ = _make_event()
    context = _make_context()
    context.list_prompt_extension_collectors.return_value = [
        _ExtensionCollectorBeta(),
        _ExtensionCollectorAlpha(),
    ]

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[],
    )

    system_slot = pack.get_slot("extension.system")
    assert system_slot is not None
    assert system_slot.category == "extension"
    assert system_slot.value["format"] == "prompt_extensions_v1"
    assert system_slot.value["mount"] == "system"
    assert [item["title"] for item in system_slot.value["items"]] == [
        "First",
        "Second",
        "Beta",
    ]
    assert [item["plugin_id"] for item in system_slot.value["items"]] == [
        "alpha.plugin",
        "alpha.plugin",
        "beta.plugin",
    ]
    assert system_slot.meta["plugin_count"] == 2
    assert system_slot.meta["item_count"] == 3

    input_slot = pack.get_slot("extension.input")
    assert input_slot is not None
    assert input_slot.value["items"][0]["value"] == "Input sidecar"

    context_slot = pack.get_slot("extension.context")
    assert context_slot is not None
    assert context_slot.value["mount"] == "context"
    assert context_slot.value["items"][0]["title"] == "Runtime Context"
    assert context_slot.value["items"][0]["value"] == {"state": "ready"}

    conversation_slot = pack.get_slot("extension.conversation")
    assert conversation_slot is not None
    assert conversation_slot.value["items"][0]["plugin_id"] == "beta.plugin"

    assert pack.get_slot("extension.memory") is None


@pytest.mark.asyncio
async def test_collect_context_pack_fail_open_when_prompt_extension_collector_raises():
    event, _ = _make_event()
    context = _make_context()
    context.list_prompt_extension_collectors.return_value = [
        _BrokenExtensionCollector(),
        _ExtensionCollectorAlpha(),
    ]

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        collectors=[],
    )

    assert pack.get_slot("extension.system") is not None
    assert pack.get_slot("extension.context") is not None
    assert pack.get_slot("extension.input") is not None
