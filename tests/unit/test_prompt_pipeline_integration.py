"""Integration tests for prompt collect -> render pipeline."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot.core import astr_main_agent as ama
from astrbot.core.agent.agent import Agent
from astrbot.core.agent.handoff import HandoffTool
from astrbot.core.agent.message import Message, TextPart
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.db.po import Conversation
from astrbot.core.memory.types import (
    Experience,
    LongTermMemoryIndex,
    MemorySnapshot,
    PersonaState,
    ShortTermMemory,
    TopicState,
)
from astrbot.core.message.components import File, Image, Plain, Reply
from astrbot.core.prompt.context_collect import collect_context_pack
from astrbot.core.prompt.extensions import PromptExtension
from astrbot.core.prompt.interfaces import PromptExtensionCollectorInterface
from astrbot.core.prompt.render import (
    BasePromptRenderer,
    PromptRenderEngine,
    apply_render_result_to_request,
)
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (
    InternalAgentSubStage,
)
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.skills.skill_manager import SkillInfo


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

    def _get_extra(key):
        return extras.get(key)

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


class _IntegrationExtensionCollector(PromptExtensionCollectorInterface):
    @property
    def plugin_id(self) -> str:
        return "desktop.sidecar"

    async def collect(self, event, plugin_context, config, provider_request=None):
        return [
            PromptExtension(
                plugin_id="desktop.sidecar",
                mount="system",
                title="Desktop Mode",
                value={"mode": "assistant"},
            ),
            PromptExtension(
                plugin_id="desktop.sidecar",
                mount="input",
                title="Desktop Snapshot",
                value={
                    "kind": "desktop_screenshot",
                    "summary": "Current desktop screenshot from platform sidecar",
                },
            ),
            PromptExtension(
                plugin_id="desktop.sidecar",
                mount="conversation",
                value={"topic": "live desktop assistance"},
            ),
        ]


@pytest.fixture
def memory_service_mock():
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
    ):
        yield service


@pytest.fixture
def skills_list_mock():
    with patch(
        "astrbot.core.prompt.collectors.skills_collector.SkillManager.list_skills",
        return_value=[],
    ) as mock_list_skills:
        yield mock_list_skills


@pytest.fixture(autouse=True)
def suppress_context_catalog_loguru():
    with patch("astrbot.core.prompt.context_catalog.logger"):
        yield


@pytest.mark.asyncio
async def test_collect_and_render_pipeline_builds_base_prompt_contract(
    memory_service_mock,
    skills_list_mock,
):
    event, extras = _make_event()
    event.message_str = "Need <tools> & memory"
    event.message_obj.message = [Plain(text="Need <tools> & memory")]
    context = _make_context()
    context.get_config.return_value = {
        "subagent_orchestrator": {
            "main_enable": True,
            "remove_main_duplicate_tools": False,
            "router_system_prompt": "Route <carefully> & deliberately.",
        }
    }
    context.subagent_orchestrator = MagicMock(
        handoffs=[
            _make_handoff_tool(
                "writer",
                description="Delegate <writing> & editing.",
                parameters={"type": "object", "properties": {}},
            )
        ]
    )

    toolset = ToolSet()
    toolset.add_tool(
        _make_tool(
            "search_docs",
            description="Search <docs> & references.",
        )
    )
    toolset.add_tool(
        _make_tool(
            "hidden_tool",
            description="Should be filtered.",
        )
    )
    context.get_llm_tool_manager.return_value.get_func.side_effect = lambda name: (
        toolset.get_tool(name)
    )
    context.get_llm_tool_manager.return_value.get_full_tool_set.return_value = toolset

    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(
            "persona-a",
            {
                "name": "persona-a",
                "prompt": "You are <Alice> & analyst.",
                "_begin_dialogs_processed": [
                    {"role": "user", "content": "Hi"},
                    {"role": "assistant", "content": "Hello"},
                ],
                "tools": ["search_docs"],
                "skills": ["skill_alpha"],
            },
            None,
            False,
        )
    )

    skills_list_mock.return_value = [
        SkillInfo(
            name="skill_alpha",
            description="Alpha <skill> & helper.",
            path="C:/skills/alpha/SKILL.md",
            source_type="local_only",
            source_label="local",
            active=True,
            local_exists=True,
            sandbox_exists=False,
        ),
        SkillInfo(
            name="skill_beta",
            description="Should be filtered out.",
            path="C:/skills/beta/SKILL.md",
            source_type="local_only",
            source_label="local",
            active=True,
            local_exists=True,
            sandbox_exists=False,
        ),
    ]

    memory_service_mock.get_snapshot.return_value = MemorySnapshot(
        umo=event.unified_msg_origin,
        conversation_id="conv-id",
        topic_state=TopicState(
            umo=event.unified_msg_origin,
            conversation_id="conv-id",
            current_topic="Roadmap <planning>",
            topic_summary="Discuss & decide the next steps.",
            topic_confidence=0.85,
            last_active_at=datetime(2026, 4, 17, 20, 30, 0),
        ),
        short_term_memory=ShortTermMemory(
            umo=event.unified_msg_origin,
            conversation_id="conv-id",
            short_summary="User needs <prompt> support.",
            active_focus="Collect & render pipeline.",
            updated_at=datetime(2026, 4, 17, 20, 32, 0),
        ),
        experiences=[
            Experience(
                experience_id="exp-1",
                umo=event.unified_msg_origin,
                conversation_id="conv-id",
                platform_user_key="platform:user123",
                canonical_user_id="canonical-user",
                scope_type="session",
                scope_id="conv-id",
                event_time=datetime(2026, 4, 17, 20, 0, 0),
                category="preference",
                summary="Asked about <prompt> architecture.",
                detail_summary="Wants XML & structured rendering first.",
                importance=0.7,
                confidence=0.8,
            )
        ],
        long_term_memories=[
            LongTermMemoryIndex(
                memory_id="ltm-1",
                umo=event.unified_msg_origin,
                canonical_user_id="canonical-user",
                scope_type="user",
                scope_id="canonical-user",
                category="preference",
                title="Renderer preferences",
                summary="Prefers <XML> & provider-specific renderers later.",
                status="active",
                doc_path="data/memory/long_term/renderer-preferences.md",
                importance=0.9,
                confidence=0.95,
                tags=["prompt", "render"],
            )
        ],
        persona_state=PersonaState(
            state_id="ps-1",
            scope_type="session",
            scope_id="conv-id",
            persona_id="persona-a",
            familiarity=0.8,
            trust=0.7,
            warmth=0.6,
            formality_preference=0.2,
            directness_preference=0.9,
            updated_at=datetime(2026, 4, 17, 20, 33, 0),
        ),
    )

    req = ProviderRequest(
        prompt="Need <tools> & memory",
        system_prompt="Base <system> & guardrails",
    )
    req.conversation = _make_conversation(persona_id="persona-a")
    req.conversation.history = json.dumps(
        [
            {"role": "user", "content": "Can you help?"},
            {"role": "assistant", "content": "Yes, what do you need?"},
        ]
    )
    extras["provider_request"] = req

    with patch(
        "astrbot.core.prompt.collectors.knowledge_collector.retrieve_knowledge_base_with_cache",
        new=AsyncMock(return_value="Knowledge <snippet> & facts."),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                timezone="Asia/Shanghai",
                llm_safety_mode=True,
                safety_mode_strategy="system_prompt",
                tool_schema_mode="full",
                computer_use_runtime="sandbox",
                kb_agentic_mode=False,
                add_cron_tools=False,
            ),
            provider_request=req,
        )

    result = PromptRenderEngine(default_renderer=BasePromptRenderer()).render(pack)

    assert result.system_prompt is not None
    assert "Base &lt;system&gt; &amp; guardrails" in result.system_prompt
    assert "You are &lt;Alice&gt; &amp; analyst." in result.system_prompt
    assert "Knowledge &lt;snippet&gt; &amp; facts." not in result.system_prompt
    assert "Roadmap &lt;planning&gt;" not in result.system_prompt
    assert "skill_alpha" in result.system_prompt
    assert "skill_beta" not in result.system_prompt
    assert "Route &lt;carefully&gt; &amp; deliberately." in result.system_prompt

    roles = [message["role"] for message in result.messages]
    assert roles == ["user", "user", "user", "assistant", "user", "assistant", "user"]
    assert result.messages[0]["_no_save"] is True
    assert result.messages[1]["_no_save"] is True
    assert "Roadmap &lt;planning&gt;" in result.messages[0]["content"]
    assert "Knowledge &lt;snippet&gt; &amp; facts." in result.messages[1]["content"]
    assert result.messages[2]["content"] == "Hi"
    assert result.messages[3]["content"] == "Hello"
    assert result.messages[4]["content"] == "Can you help?"
    assert result.messages[5]["content"] == "Yes, what do you need?"

    final_message = result.messages[-1]
    assert isinstance(final_message["content"], list)
    final_text_parts = [
        part["text"] for part in final_message["content"] if part["type"] == "text"
    ]
    assert any("<request_context>" in text for text in final_text_parts)
    assert any("<nickname>Tester</nickname>" in text for text in final_text_parts)
    assert (
        "<user_input>\n  <text>Need &lt;tools&gt; &amp; memory</text>\n</user_input>"
    ) in final_text_parts

    tool_names = [
        tool_schema["function"]["name"] for tool_schema in result.tool_schema or []
    ]
    assert tool_names == ["search_docs", "transfer_to_writer"]


@pytest.mark.asyncio
async def test_collect_and_render_pipeline_preserves_multimodal_input_parts(
    memory_service_mock,
    skills_list_mock,
):
    del memory_service_mock, skills_list_mock

    event, extras = _make_event()
    event.message_str = "Look <here> & now"
    event.message_obj.message = [
        Reply(
            id="reply-1",
            message_str="quoted message",
            chain=[
                Image(file="file:///C:/tmp/quoted.png"),
                File(name="quoted.txt", file="C:/tmp/quoted.txt"),
            ],
        ),
        Plain(text="Look <here> & now"),
        Image(file="https://example.com/current.png"),
        File(name="current.txt", file="C:/tmp/current.txt"),
    ]
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    req = ProviderRequest(prompt="Look <here> & now")
    extras["provider_request"] = req

    with patch(
        "astrbot.core.prompt.collectors.input_collector.extract_quoted_message_text",
        new=AsyncMock(return_value="Quoted <text> & file"),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                timezone="Asia/Shanghai",
            ),
            provider_request=req,
        )

    result = PromptRenderEngine(default_renderer=BasePromptRenderer()).render(pack)

    assert result.messages[-1]["role"] == "user"
    assert isinstance(result.messages[-1]["content"], list)
    assert result.messages[-1]["content"][0]["type"] == "text"
    assert "<request_context>" in result.messages[-1]["content"][0]["text"]
    assert result.messages[-1]["content"][1] == {
        "type": "text",
        "text": (
            "<user_input>\n"
            "  <text>Look &lt;here&gt; &amp; now</text>\n"
            "  <quoted_text>Quoted &lt;text&gt; &amp; file</quoted_text>\n"
            "</user_input>"
        ),
    }
    assert result.messages[-1]["content"][2] == {
        "type": "image_url",
        "image_url": {"url": "file:///C:/tmp/quoted.png"},
    }
    assert result.messages[-1]["content"][3] == {
        "type": "image_url",
        "image_url": {"url": "https://example.com/current.png"},
    }
    assert result.messages[-1]["content"][4] == {
        "type": "text",
        "text": "[File Attachment: name current.txt, path C:/tmp/current.txt]",
    }
    assert result.messages[-1]["content"][5] == {
        "type": "text",
        "text": "[File Attachment in quoted message: name quoted.txt, path C:/tmp/quoted.txt]",
    }


@pytest.mark.asyncio
async def test_collect_render_apply_roundtrip_builds_provider_request_message_contract(
    memory_service_mock,
    skills_list_mock,
):
    del memory_service_mock, skills_list_mock

    event, extras = _make_event()
    event.message_str = "Look <here> & now"
    event.message_obj.message = [
        Reply(
            id="reply-1",
            message_str="quoted message",
            chain=[
                Image(file="file:///C:/tmp/quoted.png"),
                File(name="quoted.txt", file="C:/tmp/quoted.txt"),
            ],
        ),
        Plain(text="Look <here> & now"),
        Image(file="https://example.com/current.png"),
        File(name="current.txt", file="C:/tmp/current.txt"),
    ]
    context = _make_context()
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    render_source_request = ProviderRequest(prompt="Look <here> & now")
    extras["provider_request"] = render_source_request

    with patch(
        "astrbot.core.prompt.collectors.input_collector.extract_quoted_message_text",
        new=AsyncMock(return_value="Quoted <text> & file"),
    ):
        pack = await collect_context_pack(
            event=event,
            plugin_context=context,
            config=ama.MainAgentBuildConfig(
                tool_call_timeout=60,
                timezone="Asia/Shanghai",
            ),
            provider_request=render_source_request,
        )

    result = PromptRenderEngine(default_renderer=BasePromptRenderer()).render(pack)

    target_request = ProviderRequest(
        session_id="session-1",
        model="gpt-test",
        func_tool=ToolSet(),
        conversation=_make_conversation(),
        tool_calls_result=["keep-tool-result"],
    )
    apply_render_result_to_request(result, target_request)
    assembled = await target_request.assemble_context()

    assert target_request.session_id == "session-1"
    assert target_request.model == "gpt-test"
    assert target_request.tool_calls_result == ["keep-tool-result"]
    assert target_request.conversation is not None
    assert assembled["role"] == "user"
    assert isinstance(assembled["content"], list)

    request_context_part = assembled["content"][0]
    assert request_context_part["type"] == "text"
    assert "<request_context>" in request_context_part["text"]
    assert "<datetime>" in request_context_part["text"]
    assert (
        "<platform_name>test_platform</platform_name>" in request_context_part["text"]
    )
    assert (
        "<umo>test_platform:private:test-session</umo>" in request_context_part["text"]
    )

    assert assembled["content"][1] == {
        "type": "text",
        "text": (
            "<user_input>\n"
            "  <text>Look &lt;here&gt; &amp; now</text>\n"
            "  <quoted_text>Quoted &lt;text&gt; &amp; file</quoted_text>\n"
            "</user_input>"
        ),
    }
    assert assembled["content"][2] == {
        "type": "image_url",
        "image_url": {"url": "file:///C:/tmp/quoted.png", "id": None},
    }
    assert assembled["content"][3] == {
        "type": "image_url",
        "image_url": {"url": "https://example.com/current.png", "id": None},
    }
    assert assembled["content"][4] == {
        "type": "text",
        "text": "[File Attachment: name current.txt, path C:/tmp/current.txt]",
    }
    assert assembled["content"][5] == {
        "type": "text",
        "text": (
            "[File Attachment in quoted message: name quoted.txt, "
            "path C:/tmp/quoted.txt]"
        ),
    }


@pytest.mark.asyncio
async def test_internal_history_save_uses_prompt_scaffold_free_user_message():
    stage = object.__new__(InternalAgentSubStage)
    stage.conv_manager = MagicMock()
    stage.conv_manager.update_conversation = AsyncMock()

    event = MagicMock()
    event.unified_msg_origin = "test_platform:private:test-session"

    save_message = {
        "role": "user",
        "content": "Look <here> & now\n\n[Image Attachment] current image",
    }

    def _get_extra(key):
        if key == ama.CONVERSATION_SAVE_USER_MESSAGE_EXTRA_KEY:
            return save_message
        return None

    event.get_extra.side_effect = _get_extra

    conversation = Conversation(
        platform_id="test_platform",
        user_id=event.unified_msg_origin,
        cid="conv-id",
        history="[]",
    )
    req = ProviderRequest(
        prompt="<request_context><session>metadata</session></request_context>",
        extra_user_content_parts=[
            TextPart(
                text=(
                    "<user_input>\n"
                    "  <text>Look &lt;here&gt; &amp; now</text>\n"
                    "</user_input>"
                )
            )
        ],
        conversation=conversation,
    )
    current_request_message = Message.model_validate(await req.assemble_context())
    all_messages = [
        Message(role="system", content="system prompt"),
        Message(role="user", content="Previous turn."),
        Message(role="assistant", content="Previous answer."),
        current_request_message,
        Message(role="assistant", content="Done."),
    ]

    await stage._save_to_history(
        event,
        req,
        LLMResponse(role="assistant", completion_text="Done."),
        all_messages,
        runner_stats=None,
    )

    _, conversation_id = stage.conv_manager.update_conversation.await_args.args
    history = stage.conv_manager.update_conversation.await_args.kwargs["history"]

    assert conversation_id == "conv-id"
    assert history[2] == save_message
    assert history[-1]["content"] == "Done."
    rendered_history = json.dumps(history, ensure_ascii=False)
    assert "<request_context>" not in rendered_history
    assert "<user_input>" not in rendered_history


@pytest.mark.asyncio
async def test_collect_and_render_pipeline_includes_prompt_extensions(
    memory_service_mock,
    skills_list_mock,
):
    event, _ = _make_event()
    context = _make_context()
    context.list_prompt_extension_collectors.return_value = [
        _IntegrationExtensionCollector()
    ]
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )

    pack = await collect_context_pack(
        event=event,
        plugin_context=context,
        config=ama.MainAgentBuildConfig(tool_call_timeout=60),
        provider_request=ProviderRequest(prompt="hello"),
    )

    result = PromptRenderEngine(default_renderer=BasePromptRenderer()).render(pack)

    assert result.system_prompt is not None
    assert "<extensions>" in result.system_prompt
    assert "<desktop_sidecar>" in result.system_prompt
    assert "<plugin_id>" in result.system_prompt
    assert "desktop.sidecar" in result.system_prompt
    assert "<conversation_extensions>" in result.system_prompt
    assert "live desktop assistance" in result.system_prompt

    assert result.messages
    user_message = result.messages[-1]
    assert user_message["role"] == "user"
    assert isinstance(user_message["content"], list)
    extension_text_parts = [
        part["text"]
        for part in user_message["content"]
        if part.get("type") == "text" and "<extensions>" in part.get("text", "")
    ]
    assert extension_text_parts
    assert "desktop.sidecar" in extension_text_parts[0]
