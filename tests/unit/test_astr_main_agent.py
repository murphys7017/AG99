"""Tests for astr_main_agent module."""

import os
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from astrbot.core import astr_main_agent as ama
from astrbot.core.agent.mcp_client import MCPTool
from astrbot.core.agent.message import TextPart
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.conversation_mgr import Conversation
from astrbot.core.message.components import Image, Plain, Reply, Video
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.prompt.collectors import ExplicitContextCollector, InputCollector
from astrbot.core.provider import Provider
from astrbot.core.provider.entities import ProviderRequest


@pytest.fixture
def mock_provider():
    """Create a mock provider."""
    provider = MagicMock(spec=Provider)
    provider.provider_config = {
        "id": "test-provider",
        "modalities": ["image", "tool_use"],
    }
    provider.get_model.return_value = "gpt-4"
    return provider


@pytest.fixture
def mock_context():
    """Create a mock Context."""
    ctx = MagicMock()
    ctx.get_config.return_value = {}
    ctx.conversation_manager = MagicMock()
    ctx.persona_manager = MagicMock()
    ctx.persona_manager.personas_v3 = []
    ctx.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False)
    )
    ctx.persona_manager.get_persona_v3_by_id = MagicMock(return_value=None)
    tool_mgr = MagicMock()
    tool_mgr.get_full_tool_set.return_value = ToolSet()
    tool_mgr.get_builtin_tool.side_effect = lambda cls, **kwargs: cls(**kwargs)
    ctx.get_llm_tool_manager.return_value = tool_mgr
    ctx.subagent_orchestrator = None
    return ctx


@pytest.fixture
def mock_event():
    """Create a mock AstrMessageEvent."""
    platform_meta = PlatformMetadata(
        id="test_platform",
        name="test_platform",
        description="Test platform",
    )
    message_obj = MagicMock()
    message_obj.message = [Plain(text="Hello")]
    message_obj.sender = MagicMock(user_id="user123", nickname="TestUser")
    message_obj.group_id = None
    message_obj.group = None

    event = MagicMock(spec=AstrMessageEvent)
    event.message_str = "Hello"
    event.message_obj = message_obj
    event.platform_meta = platform_meta
    event.session_id = "session123"
    event.unified_msg_origin = "test_platform:private:session123"
    event.get_extra.return_value = None
    event.get_platform_name.return_value = "test_platform"
    event.get_platform_id.return_value = "test_platform"
    event.get_group_id.return_value = None
    event.get_sender_name.return_value = "TestUser"
    event.trace = MagicMock()
    event.plugins_name = None
    return event


@pytest.fixture
def mock_conversation():
    """Create a mock conversation."""
    conv = MagicMock(spec=Conversation)
    conv.cid = "conv-id"
    conv.persona_id = None
    conv.history = "[]"
    return conv


@pytest.fixture
def sample_config():
    """Create a sample MainAgentBuildConfig."""
    module = ama
    return module.MainAgentBuildConfig(
        tool_call_timeout=60,
        streaming_response=True,
        file_extract_enabled=True,
        file_extract_prov="moonshotai",
        file_extract_msh_api_key="test-api-key",
    )


def _new_mock_conversation(cid: str = "conv-id") -> MagicMock:
    conv = MagicMock(spec=Conversation)
    conv.cid = cid
    conv.persona_id = None
    conv.history = "[]"
    return conv


def _setup_conversation_for_build(conv_mgr, cid: str = "conv-id") -> MagicMock:
    conv_mgr.get_curr_conversation_id = AsyncMock(return_value=None)
    conv_mgr.new_conversation = AsyncMock(return_value=cid)
    conversation = _new_mock_conversation(cid=cid)
    conv_mgr.get_conversation = AsyncMock(return_value=conversation)
    return conversation


def test_interaction_core_collectors_only_add_execution_context():
    collector_names = {
        collector.__class__.__name__
        for collector in ama._build_interaction_core_collectors()
    }

    assert "ExplicitContextCollector" in collector_names
    assert "ToolsCollector" in collector_names
    assert "KnowledgeCollector" in collector_names
    assert "InputCollector" not in collector_names
    assert "InteractionMemoryCollector" not in collector_names


@pytest.mark.asyncio
async def test_explicit_context_collector_removes_history_prefix():
    history = [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ]
    plugin_context = {"role": "system", "content": "plugin supplied context"}
    req = ProviderRequest(
        contexts=[*history, plugin_context],
        conversation=MagicMock(history=history),
    )

    slots = await ExplicitContextCollector().collect(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        provider_request=req,
    )

    assert slots[0].value == [plugin_context]


@pytest.mark.asyncio
async def test_explicit_context_collector_keeps_replacement_contexts():
    plugin_context = {"role": "system", "content": "plugin supplied context"}
    req = ProviderRequest(
        contexts=[plugin_context],
        conversation=MagicMock(
            history=[{"role": "user", "content": "old question"}]
        ),
    )

    slots = await ExplicitContextCollector().collect(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        provider_request=req,
    )

    assert slots[0].value == [plugin_context]


@pytest.mark.asyncio
async def test_explicit_context_collector_preserves_user_content_parts():
    parts = [TextPart(text="plugin attachment context")]
    req = ProviderRequest(extra_user_content_parts=parts)

    slots = await ExplicitContextCollector().collect(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        provider_request=req,
    )

    slot = next(item for item in slots if item.name == "input.explicit_content_parts")
    assert slot.value == parts
    assert slot.value is not parts


@pytest.mark.asyncio
async def test_explicit_context_collector_preserves_audio_urls():
    req = ProviderRequest(audio_urls=["C:/media/sample.wav"])

    slots = await ExplicitContextCollector().collect(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        provider_request=req,
    )

    slot = next(item for item in slots if item.name == "input.explicit_content_parts")
    assert slot.value == [
        {
            "type": "audio_url",
            "audio_url": {"url": "C:/media/sample.wav"},
        }
    ]


@pytest.mark.asyncio
async def test_input_collector_preserves_explicit_request_images(
    mock_event,
    mock_context,
):
    req = ProviderRequest(image_urls=["https://example.com/plugin-image.png"])

    slots = await InputCollector().collect(
        mock_event,
        mock_context,
        ama.MainAgentBuildConfig(tool_call_timeout=60),
        provider_request=req,
    )

    slot = next(item for item in slots if item.name == "input.images")
    assert slot.value == [
        {
            "ref": "https://example.com/plugin-image.png",
            "source": "provider_request",
            "transport": "url",
            "resolution": "explicit",
        }
    ]


class TestMainAgentBuildConfig:
    """Tests for MainAgentBuildConfig dataclass."""

    def test_config_initialization(self):
        """Test MainAgentBuildConfig initialization with defaults."""
        module = ama
        config = module.MainAgentBuildConfig(tool_call_timeout=60)
        assert config.tool_call_timeout == 60
        assert config.tool_schema_mode == "full"
        assert config.provider_wake_prefix == ""
        assert config.streaming_response is True
        assert config.sanitize_context_by_modalities is False
        assert config.kb_agentic_mode is False
        assert config.file_extract_enabled is False
        assert config.llm_safety_mode is True

    def test_config_with_custom_values(self):
        """Test MainAgentBuildConfig with custom values."""
        module = ama
        config = module.MainAgentBuildConfig(
            tool_call_timeout=120,
            tool_schema_mode="skills-like",
            provider_wake_prefix="/",
            streaming_response=False,
            kb_agentic_mode=True,
            file_extract_enabled=True,
            computer_use_runtime="sandbox",
            add_cron_tools=False,
        )
        assert config.tool_call_timeout == 120
        assert config.tool_schema_mode == "skills-like"
        assert config.provider_wake_prefix == "/"
        assert config.streaming_response is False
        assert config.kb_agentic_mode is True
        assert config.file_extract_enabled is True
        assert config.computer_use_runtime == "sandbox"
        assert config.add_cron_tools is False


class TestSelectProvider:
    """Tests for _select_provider function."""

    def test_select_provider_by_id(self, mock_event, mock_context, mock_provider):
        """Test selecting provider by ID from event extra."""
        module = ama
        mock_event.get_extra.side_effect = lambda k, default=None: (
            "test-provider" if k == "selected_provider" else None
        )
        mock_context.get_provider_by_id.return_value = mock_provider

        result = module._select_provider(mock_event, mock_context)

        assert result == mock_provider
        mock_context.get_provider_by_id.assert_called_once_with("test-provider")

    def test_select_provider_not_found(self, mock_event, mock_context):
        """Test selecting provider when ID is not found."""
        module = ama
        mock_event.get_extra.side_effect = lambda k, default=None: (
            "non-existent" if k == "selected_provider" else None
        )
        mock_context.get_provider_by_id.return_value = None

        result = module._select_provider(mock_event, mock_context)

        assert result is None
        mock_event.set_extra.assert_called_once()
        assert module.LLM_ERROR_MESSAGE_EXTRA_KEY in mock_event.set_extra.call_args.args

    def test_select_provider_invalid_type(self, mock_event, mock_context):
        """Test selecting provider when result is not a Provider instance."""
        module = ama
        mock_event.get_extra.side_effect = lambda k, default=None: (
            "invalid" if k == "selected_provider" else None
        )
        mock_context.get_provider_by_id.return_value = "not a provider"

        result = module._select_provider(mock_event, mock_context)

        assert result is None
        mock_event.set_extra.assert_called_once()
        assert module.LLM_ERROR_MESSAGE_EXTRA_KEY in mock_event.set_extra.call_args.args

    def test_select_provider_fallback(self, mock_event, mock_context, mock_provider):
        """Test provider selection fallback to using provider."""
        module = ama
        mock_event.get_extra.return_value = None
        mock_context.get_using_provider.return_value = mock_provider

        result = module._select_provider(mock_event, mock_context)

        assert result == mock_provider
        mock_context.get_using_provider.assert_called_once_with(
            umo=mock_event.unified_msg_origin
        )

    def test_select_provider_fallback_error(self, mock_event, mock_context):
        """Test provider selection when fallback raises ValueError."""
        module = ama
        mock_event.get_extra.return_value = None
        mock_context.get_using_provider.side_effect = ValueError("Test error")

        result = module._select_provider(mock_event, mock_context)

        assert result is None
        mock_event.set_extra.assert_called_once()
        assert module.LLM_ERROR_MESSAGE_EXTRA_KEY in mock_event.set_extra.call_args.args


class TestGetSessionConv:
    """Tests for _get_session_conv function."""

    @pytest.mark.asyncio
    async def test_get_session_conv_existing(
        self, mock_event, mock_context, mock_conversation
    ):
        """Test getting existing conversation."""
        module = ama
        conv_mgr = mock_context.conversation_manager
        conv_mgr.get_curr_conversation_id = AsyncMock(return_value="existing-conv-id")
        conv_mgr.get_conversation = AsyncMock(return_value=mock_conversation)

        result = await module._get_session_conv(mock_event, mock_context)

        assert result == mock_conversation
        conv_mgr.get_curr_conversation_id.assert_called_once_with(
            mock_event.unified_msg_origin
        )
        conv_mgr.get_conversation.assert_called_once_with(
            mock_event.unified_msg_origin, "existing-conv-id"
        )

    @pytest.mark.asyncio
    async def test_get_session_conv_create_new(self, mock_event, mock_context):
        """Test creating new conversation when none exists."""
        module = ama
        conv_mgr = mock_context.conversation_manager
        conv_mgr.get_curr_conversation_id = AsyncMock(return_value=None)
        conv_mgr.new_conversation = AsyncMock(return_value="new-conv-id")
        mock_conversation = MagicMock(spec=Conversation)
        mock_conversation.cid = "new-conv-id"
        mock_conversation.persona_id = None
        mock_conversation.history = "[]"
        conv_mgr.get_conversation = AsyncMock(return_value=mock_conversation)

        result = await module._get_session_conv(mock_event, mock_context)

        assert result == mock_conversation
        conv_mgr.new_conversation.assert_called_once_with(
            mock_event.unified_msg_origin, mock_event.get_platform_id()
        )

    @pytest.mark.asyncio
    async def test_get_session_conv_retry(self, mock_event, mock_context):
        """Test retrying conversation creation after failure."""
        module = ama
        conv_mgr = mock_context.conversation_manager
        conv_mgr.get_curr_conversation_id = AsyncMock(return_value="conv-id")
        conv_mgr.get_conversation = AsyncMock(return_value=None)
        conv_mgr.new_conversation = AsyncMock(return_value="retry-conv-id")
        mock_conversation = MagicMock(spec=Conversation)
        mock_conversation.cid = "retry-conv-id"
        mock_conversation.persona_id = None
        mock_conversation.history = "[]"
        conv_mgr.get_conversation.side_effect = [None, mock_conversation]

        result = await module._get_session_conv(mock_event, mock_context)

        assert result == mock_conversation
        assert conv_mgr.new_conversation.call_count == 1
        assert conv_mgr.get_conversation.call_count == 2

    @pytest.mark.asyncio
    async def test_get_session_conv_failure(self, mock_event, mock_context):
        """Test RuntimeError when conversation creation fails."""
        module = ama
        conv_mgr = mock_context.conversation_manager
        conv_mgr.get_curr_conversation_id = AsyncMock(return_value=None)
        conv_mgr.new_conversation = AsyncMock(return_value="new-conv-id")
        conv_mgr.get_conversation = AsyncMock(return_value=None)

        with pytest.raises(RuntimeError, match="无法创建新的对话。"):
            await module._get_session_conv(mock_event, mock_context)


class TestPrepareKnowledgeTools:
    """Knowledge prompt material belongs to KnowledgeCollector; setup registers tools."""

    def test_non_agentic_mode_does_not_mutate_request(self, mock_context):
        req = ProviderRequest(prompt="test", system_prompt="System")
        config = ama.MainAgentBuildConfig(tool_call_timeout=60, kb_agentic_mode=False)

        ama._prepare_knowledge_tools(req, mock_context, config)

        assert req.system_prompt == "System"
        assert req.func_tool is None

    def test_agentic_mode_registers_query_tool(self, mock_context):
        req = ProviderRequest(prompt="test")
        config = ama.MainAgentBuildConfig(tool_call_timeout=60, kb_agentic_mode=True)

        ama._prepare_knowledge_tools(req, mock_context, config)

        assert req.func_tool is not None
        assert "astr_kb_search" in req.func_tool.names()


class TestBuiltinToolInjection:
    """Tests for builtin tool injection paths."""

    @pytest.mark.asyncio
    async def test_apply_web_search_tools_uses_builtin_tool_manager(
        self, mock_event, mock_context
    ):
        """Test web search tool injection through the builtin tool manager."""
        module = ama
        req = ProviderRequest()
        mock_context.get_config.return_value = {
            "provider_settings": {
                "web_search": True,
                "websearch_provider": "baidu_ai_search",
            }
        }
        builtin_tool = MagicMock(spec=FunctionTool)
        builtin_tool.name = "web_search_baidu"
        tool_mgr = MagicMock()
        tool_mgr.get_builtin_tool.return_value = builtin_tool
        mock_context.get_llm_tool_manager.return_value = tool_mgr

        await module._apply_web_search_tools(mock_event, req, mock_context)

        tool_mgr.get_builtin_tool.assert_called_once_with(module.BaiduWebSearchTool)
        assert req.func_tool is not None
        assert req.func_tool.get_tool("web_search_baidu") is builtin_tool

    @pytest.mark.asyncio
    async def test_apply_web_search_tools_mounts_exa_tools(
        self, mock_event, mock_context
    ):
        module = ama
        req = ProviderRequest()
        mock_context.get_config.return_value = {
            "provider_settings": {
                "web_search": True,
                "websearch_provider": "exa",
            }
        }
        exa_search_tool = MagicMock(spec=FunctionTool)
        exa_search_tool.name = "web_search_exa"
        exa_contents_tool = MagicMock(spec=FunctionTool)
        exa_contents_tool.name = "exa_get_contents"
        tool_mgr = MagicMock()
        tool_mgr.get_builtin_tool.side_effect = [exa_search_tool, exa_contents_tool]
        mock_context.get_llm_tool_manager.return_value = tool_mgr

        await module._apply_web_search_tools(mock_event, req, mock_context)

        assert tool_mgr.get_builtin_tool.call_args_list == [
            call(module.ExaWebSearchTool),
            call(module.ExaGetContentsTool),
        ]
        assert req.func_tool is not None
        assert req.func_tool.get_tool("web_search_exa") is exa_search_tool
        assert req.func_tool.get_tool("exa_get_contents") is exa_contents_tool

    def test_proactive_cron_job_tools_uses_builtin_tool_manager(self, mock_context):
        """Test cron tool injection through the builtin tool manager."""
        module = ama
        req = ProviderRequest()
        tool_mgr = MagicMock()

        future_task_tool = MagicMock(spec=FunctionTool)
        future_task_tool.name = "future_task"
        tool_mgr.get_builtin_tool.return_value = future_task_tool
        mock_context.get_llm_tool_manager.return_value = tool_mgr

        module._proactive_cron_job_tools(req, mock_context)

        tool_mgr.get_builtin_tool.assert_called_once_with(module.FutureTaskTool)
        assert req.func_tool is not None
        assert req.func_tool.get_tool("future_task") is future_task_tool








class TestPluginToolFix:
    """Tests for _plugin_tool_fix function."""

    def test_plugin_tool_fix_none_plugins(self, mock_event):
        """Test plugin tool fix when no plugins specified."""
        module = ama
        req = ProviderRequest(func_tool=ToolSet())
        mock_event.plugins_name = None

        module._plugin_tool_fix(mock_event, req)

        assert req.func_tool is not None

    def test_plugin_tool_fix_filters_by_plugin(self, mock_event):
        """Test plugin tool fix filters tools by enabled plugins."""
        module = ama
        mcp_tool = MagicMock(spec=MCPTool)
        mcp_tool.name = "mcp_tool"

        plugin_tool = MagicMock()
        plugin_tool.name = "plugin_tool"
        plugin_tool.handler_module_path = "test_plugin"
        plugin_tool.active = True

        tool_set = ToolSet()
        tool_set.add_tool(mcp_tool)
        tool_set.add_tool(plugin_tool)

        req = ProviderRequest(func_tool=tool_set)
        mock_event.plugins_name = ["test_plugin"]

        with patch("astrbot.core.astr_main_agent.star_map") as mock_star_map:
            mock_plugin = MagicMock()
            mock_plugin.name = "test_plugin"
            mock_plugin.reserved = False
            mock_star_map.get.return_value = mock_plugin

            module._plugin_tool_fix(mock_event, req)

        assert "mcp_tool" in req.func_tool.names()
        assert "plugin_tool" in req.func_tool.names()

    def test_plugin_tool_fix_mcp_preserved(self, mock_event):
        """Test that MCP tools are always preserved."""
        module = ama
        mcp_tool = MagicMock(spec=MCPTool)
        mcp_tool.name = "mcp_tool"
        mcp_tool.active = True

        tool_set = ToolSet()
        tool_set.add_tool(mcp_tool)

        req = ProviderRequest(func_tool=tool_set)
        mock_event.plugins_name = ["other_plugin"]

        with patch("astrbot.core.astr_main_agent.star_map"):
            module._plugin_tool_fix(mock_event, req)

        assert "mcp_tool" in req.func_tool.names()

    def test_plugin_tool_fix_preserves_tools_without_plugin_origin(self, mock_event):
        """Tools without handler_module_path should not be filtered out."""
        module = ama
        handoff_tool = FunctionTool(
            name="transfer_to_demo_agent",
            description="Delegate to demo agent",
            parameters={"type": "object", "properties": {}},
            handler_module_path=None,
            active=True,
        )

        tool_set = ToolSet()
        tool_set.add_tool(handoff_tool)

        req = ProviderRequest(func_tool=tool_set)
        mock_event.plugins_name = ["other_plugin"]

        with patch("astrbot.core.astr_main_agent.star_map"):
            module._plugin_tool_fix(mock_event, req)

        assert "transfer_to_demo_agent" in req.func_tool.names()


class TestBuildMainAgent:
    """Tests for build_main_agent function."""

    @pytest.mark.asyncio
    async def test_build_main_agent_basic(
        self, mock_event, mock_context, mock_provider
    ):
        """Test basic main agent building."""
        module = ama
        mock_context.get_provider_by_id.return_value = None
        mock_context.get_using_provider.return_value = mock_provider
        mock_context.get_config.return_value = {}

        conv_mgr = mock_context.conversation_manager
        _setup_conversation_for_build(conv_mgr)

        with (
            patch("astrbot.core.astr_main_agent.AgentRunner") as mock_runner_cls,
            patch("astrbot.core.astr_main_agent.AstrAgentContext"),
        ):
            mock_runner = MagicMock()
            mock_runner.reset = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            result = await module.build_main_agent(
                event=mock_event,
                plugin_context=mock_context,
                config=module.MainAgentBuildConfig(tool_call_timeout=60),
            )

        assert result is not None
        assert isinstance(result, module.MainAgentBuildResult)
        assert mock_runner.reset.await_args.kwargs["fallback_providers"] == []

    def test_get_fallback_chat_providers_filters_invalid_and_duplicate_entries(
        self, mock_provider
    ):
        fallback_provider = MagicMock(spec=Provider)
        fallback_provider.provider_config = {"id": "fallback-provider"}
        plugin_context = MagicMock()
        plugin_context.get_provider_by_id.side_effect = lambda provider_id: {
            "fallback-provider": fallback_provider,
        }.get(provider_id)

        result = ama._get_fallback_chat_providers(
            mock_provider,
            plugin_context,
            {
                "fallback_chat_models": [
                    "test-provider",
                    "fallback-provider",
                    "fallback-provider",
                    "missing-provider",
                    "",
                    None,
                ]
            },
        )

        assert result == [fallback_provider]

    @pytest.mark.asyncio
    async def test_build_main_agent_no_provider(self, mock_event, mock_context):
        """Test building main agent when no provider is available."""
        module = ama
        mock_context.get_provider_by_id.return_value = None
        mock_context.get_using_provider.side_effect = ValueError("No provider")

        result = await module.build_main_agent(
            event=mock_event,
            plugin_context=mock_context,
            config=module.MainAgentBuildConfig(tool_call_timeout=60),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_build_main_agent_with_wake_prefix(
        self, mock_event, mock_context, mock_provider
    ):
        """Test building main agent with wake prefix."""
        module = ama
        mock_event.message_str = "/command"
        mock_context.get_provider_by_id.return_value = None
        mock_context.get_using_provider.return_value = mock_provider
        mock_context.get_config.return_value = {}

        conv_mgr = mock_context.conversation_manager
        _setup_conversation_for_build(conv_mgr)

        with (
            patch("astrbot.core.astr_main_agent.AgentRunner") as mock_runner_cls,
            patch("astrbot.core.astr_main_agent.AstrAgentContext"),
        ):
            mock_runner = MagicMock()
            mock_runner.reset = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            result = await module.build_main_agent(
                event=mock_event,
                plugin_context=mock_context,
                config=module.MainAgentBuildConfig(
                    tool_call_timeout=60, provider_wake_prefix="/"
                ),
            )

        assert result is not None

    @pytest.mark.asyncio
    async def test_build_main_agent_no_wake_prefix(
        self, mock_event, mock_context, mock_provider
    ):
        """Test building main agent without matching wake prefix."""
        module = ama
        mock_event.message_str = "hello"
        mock_context.get_provider_by_id.return_value = None
        mock_context.get_using_provider.return_value = mock_provider

        result = await module.build_main_agent(
            event=mock_event,
            plugin_context=mock_context,
            config=module.MainAgentBuildConfig(
                tool_call_timeout=60, provider_wake_prefix="/"
            ),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_build_main_agent_with_images(
        self, mock_event, mock_context, mock_provider
    ):
        """Test building main agent with image attachments."""
        module = ama
        mock_image = MagicMock(spec=Image)
        mock_image.convert_to_file_path = AsyncMock(return_value="/path/to/image.jpg")
        mock_event.message_obj.message = [mock_image]

        mock_context.get_provider_by_id.return_value = None
        mock_context.get_using_provider.return_value = mock_provider
        mock_context.get_config.return_value = {}

        conv_mgr = mock_context.conversation_manager
        _setup_conversation_for_build(conv_mgr)

        with (
            patch("astrbot.core.astr_main_agent.AgentRunner") as mock_runner_cls,
            patch("astrbot.core.astr_main_agent.AstrAgentContext"),
        ):
            mock_runner = MagicMock()
            mock_runner.reset = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            result = await module.build_main_agent(
                event=mock_event,
                plugin_context=mock_context,
                config=module.MainAgentBuildConfig(tool_call_timeout=60),
            )

        assert result is not None

    @pytest.mark.asyncio
    async def test_build_main_agent_with_video_attachment(
        self, mock_event, mock_context, mock_provider
    ):
        """Test building main agent with video attachments."""
        module = ama
        mock_video = Video(file="file:///path/to/video.mp4")
        mock_event.message_obj.message = [mock_video]

        mock_context.get_provider_by_id.return_value = None
        mock_context.get_using_provider.return_value = mock_provider
        mock_context.get_config.return_value = {}

        conv_mgr = mock_context.conversation_manager
        _setup_conversation_for_build(conv_mgr)

        with (
            patch("astrbot.core.astr_main_agent.AgentRunner") as mock_runner_cls,
            patch("astrbot.core.astr_main_agent.AstrAgentContext"),
        ):
            mock_runner = MagicMock()
            mock_runner.reset = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            result = await module.build_main_agent(
                event=mock_event,
                plugin_context=mock_context,
                config=module.MainAgentBuildConfig(tool_call_timeout=60),
            )

        assert result is not None
        assert [
            part.text for part in result.provider_request.extra_user_content_parts
        ] == [
            "<user_input>\n  <text>Hello</text>\n</user_input>",
            "[Video Attachment: name video.mp4, path path/to/video.mp4]",
        ]

    @pytest.mark.asyncio
    async def test_build_main_agent_with_quoted_video_attachment(
        self, mock_event, mock_context, mock_provider
    ):
        """Test building main agent with quoted video attachments."""
        module = ama
        mock_video = Video(file="file:///path/to/quoted-video.mp4")
        mock_reply = Reply(
            id="reply-1",
            chain=[mock_video],
            sender_nickname="",
            message_str="quoted message",
        )
        mock_event.message_obj.message = [Plain(text="Hello"), mock_reply]

        mock_context.get_provider_by_id.return_value = None
        mock_context.get_using_provider.return_value = mock_provider
        mock_context.get_config.return_value = {}

        conv_mgr = mock_context.conversation_manager
        _setup_conversation_for_build(conv_mgr)

        with (
            patch("astrbot.core.astr_main_agent.AgentRunner") as mock_runner_cls,
            patch("astrbot.core.astr_main_agent.AstrAgentContext"),
        ):
            mock_runner = MagicMock()
            mock_runner.reset = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            result = await module.build_main_agent(
                event=mock_event,
                plugin_context=mock_context,
                config=module.MainAgentBuildConfig(tool_call_timeout=60),
            )

        assert result is not None
        assert (
            "[Video Attachment in quoted message: "
            "name quoted-video.mp4, path path/to/quoted-video.mp4]"
        ) in [part.text for part in result.provider_request.extra_user_content_parts]

    @pytest.mark.asyncio
    async def test_build_main_agent_skips_quoted_image_caption_for_vision_provider(
        self, mock_event, mock_context, mock_provider
    ):
        """Quoted images should not be captioned when the main provider sees images."""
        module = ama
        mock_image = Image(file="/tmp/quoted.jpg")
        mock_reply = Reply(
            id="reply-1",
            chain=[mock_image],
            sender_nickname="",
            message_str="quoted message",
        )
        mock_event.message_obj.message = [Plain(text="Hello"), mock_reply]

        caption_provider = MagicMock(spec=Provider)
        caption_provider.provider_config = {
            "id": "caption-provider",
            "modalities": ["text", "image"],
        }
        caption_provider.text_chat = AsyncMock()

        mock_context.get_provider_by_id.return_value = caption_provider
        mock_context.get_using_provider.return_value = mock_provider
        mock_context.get_config.return_value = {
            "provider_settings": {
                "default_image_caption_provider_id": "caption-provider",
            }
        }

        conv_mgr = mock_context.conversation_manager
        _setup_conversation_for_build(conv_mgr)

        with (
            patch("astrbot.core.astr_main_agent.AgentRunner") as mock_runner_cls,
            patch("astrbot.core.astr_main_agent.AstrAgentContext"),
            patch.object(
                Image,
                "convert_to_file_path",
                AsyncMock(return_value="/tmp/quoted.jpg"),
            ),
        ):
            mock_runner = MagicMock()
            mock_runner.reset = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            result = await module.build_main_agent(
                event=mock_event,
                plugin_context=mock_context,
                config=module.MainAgentBuildConfig(tool_call_timeout=60),
            )

        assert result is not None
        assert not any(
            "Image Caption" in text or "<image_caption>" in text
            for text in (
                getattr(part, "text", "")
                for part in result.provider_request.extra_user_content_parts
            )
        )
        caption_provider.text_chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_main_agent_skips_quoted_image_caption_without_caption_provider(
        self, mock_event, mock_context, mock_provider
    ):
        """Quoted images should not be sent to the main provider for captions."""
        module = ama
        mock_provider.provider_config = {
            "id": "text-provider",
            "modalities": ["text", "tool_use"],
        }
        mock_provider.text_chat = AsyncMock()
        mock_image = Image(file="/tmp/quoted.jpg")
        mock_reply = Reply(
            id="reply-1",
            chain=[mock_image],
            sender_nickname="",
            message_str="quoted message",
        )
        mock_event.message_obj.message = [Plain(text="Hello"), mock_reply]

        mock_context.get_provider_by_id.return_value = None
        mock_context.get_using_provider.return_value = mock_provider
        mock_context.get_config.return_value = {"provider_settings": {}}

        conv_mgr = mock_context.conversation_manager
        _setup_conversation_for_build(conv_mgr)

        with (
            patch("astrbot.core.astr_main_agent.AgentRunner") as mock_runner_cls,
            patch("astrbot.core.astr_main_agent.AstrAgentContext"),
            patch.object(
                Image,
                "convert_to_file_path",
                AsyncMock(return_value="/tmp/quoted.jpg"),
            ),
        ):
            mock_runner = MagicMock()
            mock_runner.reset = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            result = await module.build_main_agent(
                event=mock_event,
                plugin_context=mock_context,
                config=module.MainAgentBuildConfig(tool_call_timeout=60),
            )

        assert result is not None
        assert not any(
            "Image Caption" in text or "<image_caption>" in text
            for text in (
                getattr(part, "text", "")
                for part in result.provider_request.extra_user_content_parts
            )
        )
        mock_provider.text_chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_main_agent_skips_quoted_image_caption_when_configured_provider_missing(
        self, mock_event, mock_context, mock_provider
    ):
        """Missing caption providers should not fall back to the main chat provider."""
        module = ama
        mock_provider.provider_config = {
            "id": "text-provider",
            "modalities": ["text", "tool_use"],
        }
        mock_provider.text_chat = AsyncMock()
        mock_image = Image(file="/tmp/quoted.jpg")
        mock_reply = Reply(
            id="reply-1",
            chain=[mock_image],
            sender_nickname="",
            message_str="quoted message",
        )
        mock_event.message_obj.message = [Plain(text="Hello"), mock_reply]

        mock_context.get_provider_by_id.return_value = None
        mock_context.get_using_provider.return_value = mock_provider
        mock_context.get_config.return_value = {
            "provider_settings": {
                "default_image_caption_provider_id": "missing-caption-provider",
            }
        }

        conv_mgr = mock_context.conversation_manager
        _setup_conversation_for_build(conv_mgr)

        with (
            patch("astrbot.core.astr_main_agent.AgentRunner") as mock_runner_cls,
            patch("astrbot.core.astr_main_agent.AstrAgentContext"),
            patch.object(
                Image,
                "convert_to_file_path",
                AsyncMock(return_value="/tmp/quoted.jpg"),
            ),
        ):
            mock_runner = MagicMock()
            mock_runner.reset = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            result = await module.build_main_agent(
                event=mock_event,
                plugin_context=mock_context,
                config=module.MainAgentBuildConfig(tool_call_timeout=60),
            )

        assert result is not None
        assert not any(
            "Image Caption" in text or "<image_caption>" in text
            for text in (
                getattr(part, "text", "")
                for part in result.provider_request.extra_user_content_parts
            )
        )
        mock_provider.text_chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_main_agent_skips_video_attachment_when_conversion_fails(
        self, mock_event, mock_context, mock_provider
    ):
        """Test video attachment failures do not abort request construction."""
        module = ama
        mock_video = Video(file="file:///path/to/direct.mp4")
        mock_quoted_video = Video(file="file:///path/to/quoted.mp4")
        mock_reply = Reply(
            id="reply-1",
            chain=[mock_quoted_video],
            sender_nickname="",
            message_str="quoted message",
        )
        mock_event.message_obj.message = [mock_video, mock_reply]

        mock_context.get_provider_by_id.return_value = None
        mock_context.get_using_provider.return_value = mock_provider
        mock_context.get_config.return_value = {}

        conv_mgr = mock_context.conversation_manager
        _setup_conversation_for_build(conv_mgr)

        async def _raise_video_conversion_error(self):
            if self.file.endswith("direct.mp4"):
                raise RuntimeError("direct")
            raise RuntimeError("quoted")

        with (
            patch("astrbot.core.astr_main_agent.AgentRunner") as mock_runner_cls,
            patch("astrbot.core.astr_main_agent.AstrAgentContext"),
            patch(
                "astrbot.core.prompt.collectors.input_collector.logger"
            ) as mock_logger,
            patch.object(
                Video,
                "convert_to_file_path",
                AsyncMock(side_effect=_raise_video_conversion_error),
            ),
        ):
            mock_runner = MagicMock()
            mock_runner.reset = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            result = await module.build_main_agent(
                event=mock_event,
                plugin_context=mock_context,
                config=module.MainAgentBuildConfig(tool_call_timeout=60),
            )

        assert result is not None
        assert not any(
            "Video Attachment" in part.text
            for part in result.provider_request.extra_user_content_parts
        )
        assert mock_logger.warning.call_count == 2
        assert all(
            "Failed to resolve video attachment" in call_args[0][0]
            for call_args in mock_logger.warning.call_args_list
        )

    @pytest.mark.asyncio
    async def test_build_main_agent_no_prompt_no_images(
        self, mock_event, mock_context, mock_provider
    ):
        """Test building main agent returns None when no prompt or images."""
        module = ama
        mock_event.message_str = ""
        mock_event.message_obj.message = []

        mock_context.get_provider_by_id.return_value = None
        mock_context.get_using_provider.return_value = mock_provider
        mock_context.get_config.return_value = {}

        conv_mgr = mock_context.conversation_manager
        _setup_conversation_for_build(conv_mgr)

        result = await module.build_main_agent(
            event=mock_event,
            plugin_context=mock_context,
            config=module.MainAgentBuildConfig(tool_call_timeout=60),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_build_main_agent_apply_reset_false(
        self, mock_event, mock_context, mock_provider
    ):
        """Test building main agent without applying reset."""
        module = ama
        mock_context.get_provider_by_id.return_value = None
        mock_context.get_using_provider.return_value = mock_provider
        mock_context.get_config.return_value = {}

        conv_mgr = mock_context.conversation_manager
        _setup_conversation_for_build(conv_mgr)

        with (
            patch("astrbot.core.astr_main_agent.AgentRunner") as mock_runner_cls,
            patch("astrbot.core.astr_main_agent.AstrAgentContext"),
        ):
            mock_runner = MagicMock()
            mock_runner.reset = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            result = await module.build_main_agent(
                event=mock_event,
                plugin_context=mock_context,
                config=module.MainAgentBuildConfig(tool_call_timeout=60),
                apply_reset=False,
            )

        assert result is not None
        assert result.reset_coro is not None
        mock_runner.reset.assert_called_once()
        result.reset_coro.close()

    @pytest.mark.asyncio
    async def test_build_main_agent_with_existing_request(
        self, mock_event, mock_context, mock_provider
    ):
        """Test building main agent with existing ProviderRequest."""
        module = ama
        existing_req = ProviderRequest(prompt="Existing prompt")
        mock_event.get_extra.side_effect = lambda k, default=None: (
            existing_req if k == "provider_request" else None
        )

        with (
            patch("astrbot.core.astr_main_agent.AgentRunner") as mock_runner_cls,
            patch("astrbot.core.astr_main_agent.AstrAgentContext"),
        ):
            mock_runner = MagicMock()
            mock_runner.reset = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            result = await module.build_main_agent(
                event=mock_event,
                plugin_context=mock_context,
                config=module.MainAgentBuildConfig(tool_call_timeout=60),
                provider=mock_provider,
                req=existing_req,
            )

        assert result is not None
        assert result.provider_request == existing_req


class TestHandleWebchat:
    """Tests for _handle_webchat function."""

    @pytest.mark.asyncio
    async def test_handle_webchat_generates_title(self, mock_event):
        """Test generating title for webchat session without display name."""
        module = ama
        mock_event.session_id = "platform!webchat-session-123"

        req = ProviderRequest(prompt="What is machine learning?")
        prov = MagicMock(spec=Provider)
        llm_response = MagicMock()
        llm_response.completion_text = "Machine Learning Introduction"
        prov.text_chat = AsyncMock(return_value=llm_response)

        mock_session = MagicMock()
        mock_session.display_name = None

        with patch("astrbot.core.db_helper") as mock_db:
            mock_db.get_platform_session_by_id = AsyncMock(return_value=mock_session)
            mock_db.update_platform_session = AsyncMock()

            await module._handle_webchat(mock_event, req, prov)

        mock_db.get_platform_session_by_id.assert_called_once_with(
            "webchat-session-123"
        )
        mock_db.update_platform_session.assert_called_once_with(
            session_id="webchat-session-123",
            display_name="Machine Learning Introduction",
        )

    @pytest.mark.asyncio
    async def test_handle_webchat_no_user_prompt(self, mock_event):
        """Test that title generation is skipped when no user prompt."""
        module = ama
        mock_event.session_id = "platform!webchat-session-123"

        req = ProviderRequest(prompt=None)
        prov = MagicMock(spec=Provider)

        mock_session = MagicMock()
        mock_session.display_name = None

        with patch("astrbot.core.db_helper") as mock_db:
            mock_db.get_platform_session_by_id = AsyncMock(return_value=mock_session)
            await module._handle_webchat(mock_event, req, prov)

        prov.text_chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_webchat_empty_user_prompt(self, mock_event):
        """Test that title generation is skipped when user prompt is empty."""
        module = ama
        mock_event.session_id = "platform!webchat-session-123"

        req = ProviderRequest(prompt="")
        prov = MagicMock(spec=Provider)

        mock_session = MagicMock()
        mock_session.display_name = None

        with patch("astrbot.core.db_helper") as mock_db:
            mock_db.get_platform_session_by_id = AsyncMock(return_value=mock_session)
            await module._handle_webchat(mock_event, req, prov)

        prov.text_chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_webchat_session_already_has_display_name(self, mock_event):
        """Test that title generation is skipped when session already has display name."""
        module = ama
        mock_event.session_id = "platform!webchat-session-123"

        req = ProviderRequest(prompt="What is AI?")
        prov = MagicMock(spec=Provider)

        mock_session = MagicMock()
        mock_session.display_name = "Existing Title"

        with patch("astrbot.core.db_helper") as mock_db:
            mock_db.get_platform_session_by_id = AsyncMock(return_value=mock_session)

            await module._handle_webchat(mock_event, req, prov)

        prov.text_chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_webchat_no_session_found(self, mock_event):
        """Test that title generation is skipped when session is not found."""
        module = ama
        mock_event.session_id = "platform!webchat-session-123"

        req = ProviderRequest(prompt="What is AI?")
        prov = MagicMock(spec=Provider)

        with patch("astrbot.core.db_helper") as mock_db:
            mock_db.get_platform_session_by_id = AsyncMock(return_value=None)

            await module._handle_webchat(mock_event, req, prov)

        prov.text_chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_webchat_llm_returns_none_title(self, mock_event):
        """Test that title is not updated when LLM returns <None>."""
        module = ama
        mock_event.session_id = "platform!webchat-session-123"

        req = ProviderRequest(prompt="hi")
        prov = MagicMock(spec=Provider)
        llm_response = MagicMock()
        llm_response.completion_text = "<None>"
        prov.text_chat = AsyncMock(return_value=llm_response)

        mock_session = MagicMock()
        mock_session.display_name = None

        with patch("astrbot.core.db_helper") as mock_db:
            mock_db.get_platform_session_by_id = AsyncMock(return_value=mock_session)
            mock_db.update_platform_session = AsyncMock()

            await module._handle_webchat(mock_event, req, prov)

        mock_db.update_platform_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_webchat_llm_returns_empty_title(self, mock_event):
        """Test that title is not updated when LLM returns empty string."""
        module = ama
        mock_event.session_id = "platform!webchat-session-123"

        req = ProviderRequest(prompt="hello")
        prov = MagicMock(spec=Provider)
        llm_response = MagicMock()
        llm_response.completion_text = "   "
        prov.text_chat = AsyncMock(return_value=llm_response)

        mock_session = MagicMock()
        mock_session.display_name = None

        with patch("astrbot.core.db_helper") as mock_db:
            mock_db.get_platform_session_by_id = AsyncMock(return_value=mock_session)
            mock_db.update_platform_session = AsyncMock()

            await module._handle_webchat(mock_event, req, prov)

        mock_db.update_platform_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_webchat_llm_returns_none_response(self, mock_event):
        """Test handling when LLM returns None response."""
        module = ama
        mock_event.session_id = "platform!webchat-session-123"

        req = ProviderRequest(prompt="test question")
        prov = MagicMock(spec=Provider)
        prov.text_chat = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.display_name = None

        with patch("astrbot.core.db_helper") as mock_db:
            mock_db.get_platform_session_by_id = AsyncMock(return_value=mock_session)
            mock_db.update_platform_session = AsyncMock()

            await module._handle_webchat(mock_event, req, prov)

        mock_db.update_platform_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_webchat_llm_returns_no_completion_text(self, mock_event):
        """Test handling when LLM response has no completion_text."""
        module = ama
        mock_event.session_id = "platform!webchat-session-123"

        req = ProviderRequest(prompt="test question")
        prov = MagicMock(spec=Provider)
        llm_response = MagicMock()
        llm_response.completion_text = None
        prov.text_chat = AsyncMock(return_value=llm_response)

        mock_session = MagicMock()
        mock_session.display_name = None

        with patch("astrbot.core.db_helper") as mock_db:
            mock_db.get_platform_session_by_id = AsyncMock(return_value=mock_session)
            mock_db.update_platform_session = AsyncMock()

            await module._handle_webchat(mock_event, req, prov)

        mock_db.update_platform_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_webchat_strips_title_whitespace(self, mock_event):
        """Test that generated title has whitespace stripped."""
        module = ama
        mock_event.session_id = "platform!webchat-session-123"

        req = ProviderRequest(prompt="What is Python?")
        prov = MagicMock(spec=Provider)
        llm_response = MagicMock()
        llm_response.completion_text = "  Python Programming Guide  "
        prov.text_chat = AsyncMock(return_value=llm_response)

        mock_session = MagicMock()
        mock_session.display_name = None

        with patch("astrbot.core.db_helper") as mock_db:
            mock_db.get_platform_session_by_id = AsyncMock(return_value=mock_session)
            mock_db.update_platform_session = AsyncMock()

            await module._handle_webchat(mock_event, req, prov)

        mock_db.update_platform_session.assert_called_once_with(
            session_id="webchat-session-123",
            display_name="Python Programming Guide",
        )

    @pytest.mark.asyncio
    async def test_handle_webchat_provider_exception_is_handled(self, mock_event):
        """Test that provider exception during title generation is handled."""
        module = ama
        mock_event.session_id = "platform!webchat-session-123"

        req = ProviderRequest(prompt="What is Python?")
        prov = MagicMock(spec=Provider)
        prov.text_chat = AsyncMock(side_effect=RuntimeError("provider failed"))

        mock_session = MagicMock()
        mock_session.display_name = None

        with (
            patch("astrbot.core.db_helper") as mock_db,
            patch("astrbot.core.astr_main_agent.logger") as mock_logger,
        ):
            mock_db.get_platform_session_by_id = AsyncMock(return_value=mock_session)
            mock_db.update_platform_session = AsyncMock()

            await module._handle_webchat(mock_event, req, prov)

        mock_logger.exception.assert_called_once()
        mock_db.update_platform_session.assert_not_called()




class TestApplySandboxTools:
    """Tests for _apply_sandbox_tools function."""

    def test_apply_sandbox_tools_creates_toolset_if_none(self, mock_context):
        """Test that ToolSet is created when func_tool is None."""
        module = ama
        config = module.MainAgentBuildConfig(
            tool_call_timeout=60,
            computer_use_runtime="sandbox",
            sandbox_cfg={},
        )
        req = ProviderRequest(prompt="Test", func_tool=None)

        module._apply_sandbox_tools(config, req, "session-123")

        assert req.func_tool is not None
        assert isinstance(req.func_tool, ToolSet)

    def test_apply_sandbox_tools_adds_required_tools(self, mock_context):
        """Test that all required sandbox tools are added."""
        module = ama
        config = module.MainAgentBuildConfig(
            tool_call_timeout=60,
            computer_use_runtime="sandbox",
            sandbox_cfg={},
        )
        req = ProviderRequest(prompt="Test", func_tool=None)

        module._apply_sandbox_tools(config, req, "session-123")

        tool_names = req.func_tool.names()
        assert "astrbot_execute_shell" in tool_names
        assert "astrbot_execute_ipython" in tool_names
        assert "astrbot_upload_file" in tool_names
        assert "astrbot_download_file" in tool_names

    def test_apply_sandbox_tools_does_not_mutate_system_prompt(self, mock_context):
        module = ama
        config = module.MainAgentBuildConfig(
            tool_call_timeout=60,
            computer_use_runtime="sandbox",
            sandbox_cfg={},
        )
        req = ProviderRequest(prompt="Test", system_prompt="Original prompt")

        module._apply_sandbox_tools(config, req, "session-123")

        assert req.system_prompt == "Original prompt"

    def test_apply_sandbox_tools_with_shipyard_booter(self, monkeypatch, mock_context):
        """Test sandbox tools with shipyard booter configuration."""
        module = ama
        config = module.MainAgentBuildConfig(
            tool_call_timeout=60,
            computer_use_runtime="sandbox",
            sandbox_cfg={
                "booter": "shipyard",
                "shipyard_endpoint": "https://shipyard.example.com",
                "shipyard_access_token": "test-token",
            },
        )
        req = ProviderRequest(prompt="Test", func_tool=None)

        monkeypatch.delenv("SHIPYARD_ENDPOINT", raising=False)
        monkeypatch.delenv("SHIPYARD_ACCESS_TOKEN", raising=False)

        module._apply_sandbox_tools(config, req, "session-123")

        assert os.environ.get("SHIPYARD_ENDPOINT") == "https://shipyard.example.com"
        assert os.environ.get("SHIPYARD_ACCESS_TOKEN") == "test-token"

    def test_apply_sandbox_tools_shipyard_missing_endpoint(self, mock_context):
        """Test that shipyard config is skipped when endpoint is missing."""
        module = ama
        config = module.MainAgentBuildConfig(
            tool_call_timeout=60,
            computer_use_runtime="sandbox",
            sandbox_cfg={
                "booter": "shipyard",
                "shipyard_endpoint": "",
                "shipyard_access_token": "test-token",
            },
        )
        req = ProviderRequest(prompt="Test", func_tool=None)

        with patch("astrbot.core.astr_main_agent.logger") as mock_logger:
            module._apply_sandbox_tools(config, req, "session-123")

        mock_logger.error.assert_called_once()
        assert (
            "Shipyard sandbox configuration is incomplete"
            in mock_logger.error.call_args[0][0]
        )

    def test_apply_sandbox_tools_shipyard_missing_access_token(self, mock_context):
        """Test that shipyard config is skipped when access token is missing."""
        module = ama
        config = module.MainAgentBuildConfig(
            tool_call_timeout=60,
            computer_use_runtime="sandbox",
            sandbox_cfg={
                "booter": "shipyard",
                "shipyard_endpoint": "https://shipyard.example.com",
                "shipyard_access_token": "",
            },
        )
        req = ProviderRequest(prompt="Test", func_tool=None)

        with patch("astrbot.core.astr_main_agent.logger") as mock_logger:
            module._apply_sandbox_tools(config, req, "session-123")

        mock_logger.error.assert_called_once()

    def test_apply_sandbox_tools_preserves_existing_toolset(self, mock_context):
        """Test that existing tools are preserved when adding sandbox tools."""
        module = ama
        config = module.MainAgentBuildConfig(
            tool_call_timeout=60,
            computer_use_runtime="sandbox",
            sandbox_cfg={},
        )
        existing_toolset = ToolSet()
        existing_tool = MagicMock()
        existing_tool.name = "existing_tool"
        existing_toolset.add_tool(existing_tool)
        req = ProviderRequest(prompt="Test", func_tool=existing_toolset)

        module._apply_sandbox_tools(config, req, "session-123")

        assert "existing_tool" in req.func_tool.names()
        assert "astrbot_execute_shell" in req.func_tool.names()

    def test_apply_sandbox_tools_preserves_existing_system_prompt(self, mock_context):
        module = ama
        config = module.MainAgentBuildConfig(
            tool_call_timeout=60,
            computer_use_runtime="sandbox",
            sandbox_cfg={},
        )
        req = ProviderRequest(prompt="Test", system_prompt="Base prompt")

        module._apply_sandbox_tools(config, req, "session-123")

        assert req.system_prompt == "Base prompt"

    def test_apply_sandbox_tools_preserves_none_system_prompt(self, mock_context):
        module = ama
        config = module.MainAgentBuildConfig(
            tool_call_timeout=60,
            computer_use_runtime="sandbox",
            sandbox_cfg={},
        )
        req = ProviderRequest(prompt="Test", system_prompt=None)

        module._apply_sandbox_tools(config, req, "session-123")

        assert req.system_prompt is None
