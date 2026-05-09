"""Tests for prompt tree building and base renderer routing."""

from unittest.mock import patch

from astrbot.core.prompt.context_types import ContextPack, ContextSlot
from astrbot.core.prompt.render import (
    BasePromptRenderer,
    PromptBuilder,
    PromptRenderEngine,
    SerializedRenderValue,
)
from astrbot.core.prompt.render.engine import logger as render_logger


def test_prompt_builder_builds_nested_tag_tree():
    builder = PromptBuilder("prompt")
    builder.tag("persona").add("You are Alice.")
    policy_ref = builder.tag("policy")
    policy_ref.tag("safety").add("Be safe.")

    rendered = builder.build()

    assert "<prompt>" in rendered
    assert "<persona>" in rendered
    assert "You are Alice." in rendered
    assert "<policy>" in rendered
    assert "<safety>" in rendered
    assert "Be safe." in rendered
    assert "</prompt>" in rendered


def test_prompt_builder_include_and_extend_work():
    prompt = PromptBuilder("prompt")
    persona = PromptBuilder("persona")
    persona.add("Alice")
    prompt.include(persona)

    policy = PromptBuilder("policy")
    policy.add("Safe mode.")
    prompt.extend(policy)

    rendered = prompt.build()

    assert "<prompt>" in rendered
    assert "<persona>" in rendered
    assert "Alice" in rendered
    assert "<policy>" in rendered
    assert "Safe mode." in rendered


def test_base_prompt_renderer_enables_all_slot_groups():
    renderer = BasePromptRenderer()

    assert renderer.get_enabled_slot_groups() == (
        "system",
        "persona",
        "policy",
        "input",
        "session",
        "conversation",
        "knowledge",
        "capability",
        "memory",
        "extension",
    )


def test_base_prompt_renderer_returns_nested_node_structure():
    renderer = BasePromptRenderer()
    structure = renderer.get_node_structure()

    assert structure["system"] == "system/core"
    assert structure["persona"] == "system/persona"
    assert structure["policy"] == "system/policy"
    assert structure["session"] == "system/session"
    assert structure["conversation"] == "history/conversation"
    assert structure["knowledge"] == "context/knowledge"
    assert structure["capability"] == "system/capability"
    assert structure["memory"] == "context/memory"
    assert structure["extension"] == "system/extensions"


def test_base_prompt_renderer_serializes_dict_slot_to_structured_object():
    renderer = BasePromptRenderer()
    slot = ContextSlot(
        name="session.user_info",
        value={"user_id": "u1", "nickname": "Alice"},
        category="session",
        source="test",
    )

    serialized = renderer.serialize_slot_value(slot, group="session")

    assert isinstance(serialized, SerializedRenderValue)
    assert serialized.kind == "mapping"
    assert serialized.tag == "user_info"
    assert serialized.value == {"user_id": "u1", "nickname": "Alice"}


def test_base_prompt_renderer_serializes_list_slot_to_structured_object():
    renderer = BasePromptRenderer()
    slot = ContextSlot(
        name="input.files",
        value=[{"name": "a.txt"}, {"name": "b.txt"}],
        category="input",
        source="test",
    )

    serialized = renderer.serialize_slot_value(slot, group="input")

    assert isinstance(serialized, SerializedRenderValue)
    assert serialized.kind == "sequence"
    assert serialized.tag == "files"
    assert serialized.value == [{"name": "a.txt"}, {"name": "b.txt"}]


def test_render_engine_builds_prompt_tree_from_nested_slots():
    pack = ContextPack(
        slots={
            "system.base": ContextSlot(
                name="system.base",
                value="Base system prompt.",
                category="system",
                source="test",
            ),
            "persona.prompt": ContextSlot(
                name="persona.prompt",
                value="You are Alice.",
                category="persona",
                source="test",
            ),
            "policy.safety_prompt": ContextSlot(
                name="policy.safety_prompt",
                value="Safety prompt.",
                category="system",
                source="test",
            ),
            "knowledge.snippets": ContextSlot(
                name="knowledge.snippets",
                value={
                    "format": "kb_text_block_v1",
                    "query": "test",
                    "text": "Knowledge result.",
                },
                category="memory",
                source="test",
            ),
        }
    )

    engine = PromptRenderEngine(default_renderer=BasePromptRenderer())
    result = engine.render(pack)
    rendered = result.system_prompt

    assert result.prompt_tree is not None
    assert "<system>" in rendered
    assert "<core>" in rendered
    assert "<base>" in rendered
    assert "Base system prompt." in rendered
    assert "<persona>" in rendered
    assert "You are Alice." in rendered
    assert "<policy>" in rendered
    assert "<safety>" in rendered
    assert "Safety prompt." in rendered
    assert "<knowledge>" not in rendered
    assert "Knowledge result." not in rendered
    assert "<tools>" not in rendered
    assert len(result.messages) == 1
    knowledge_message = result.messages[0]
    assert knowledge_message["role"] == "user"
    assert knowledge_message["_no_save"] is True
    assert "<knowledge>" in knowledge_message["content"]
    assert "<snippets>" in knowledge_message["content"]
    assert "Knowledge result." in knowledge_message["content"]
    assert result.tool_schema is None


def test_render_engine_compiles_memory_and_knowledge_as_context_messages():
    pack = ContextPack(
        slots={
            "memory.short_term": ContextSlot(
                name="memory.short_term",
                value={
                    "short_summary": "Recent project focus.",
                    "active_focus": "Prompt layout",
                    "updated_at": "2026-04-17T20:33:00+08:00",
                },
                category="memory",
                source="test",
            ),
            "knowledge.snippets": ContextSlot(
                name="knowledge.snippets",
                value={
                    "format": "kb_text_block_v1",
                    "query": "prompt cache",
                    "text": "Cacheable prefix guidance.",
                },
                category="memory",
                source="test",
            ),
            "conversation.history": ContextSlot(
                name="conversation.history",
                value={
                    "format": "turn_pairs",
                    "turns": [
                        {
                            "user_message": {
                                "role": "user",
                                "content": "Previous question",
                            },
                            "assistant_message": {
                                "role": "assistant",
                                "content": "Previous answer",
                            },
                        }
                    ],
                },
                category="memory",
                source="test",
            ),
            "input.text": ContextSlot(
                name="input.text",
                value="Current question",
                category="input",
                source="test",
            ),
        }
    )

    result = PromptRenderEngine(default_renderer=BasePromptRenderer()).render(pack)

    assert result.system_prompt is None
    assert [message["role"] for message in result.messages] == [
        "user",
        "user",
        "user",
        "assistant",
        "user",
    ]
    assert result.messages[0]["_no_save"] is True
    assert result.messages[1]["_no_save"] is True
    assert "<memory>" in result.messages[0]["content"]
    assert "<short_term>" in result.messages[0]["content"]
    assert "Recent project focus." in result.messages[0]["content"]
    assert "Prompt layout" in result.messages[0]["content"]
    assert "<knowledge>" in result.messages[1]["content"]
    assert "Cacheable prefix guidance." in result.messages[1]["content"]
    assert result.messages[2] == {"role": "user", "content": "Previous question"}
    assert result.messages[3] == {"role": "assistant", "content": "Previous answer"}
    assert result.messages[4] == {"role": "user", "content": "Current question"}


def test_render_engine_prunes_empty_persona_segment_nodes():
    pack = ContextPack(
        slots={
            "persona.segments": ContextSlot(
                name="persona.segments",
                value={
                    "identity": {},
                    "core_persona": [],
                    "unparsed_sections": ["You are Alice."],
                },
                category="persona",
                source="test",
            )
        }
    )

    engine = PromptRenderEngine(default_renderer=BasePromptRenderer())
    result = engine.render(pack)

    assert result.system_prompt is not None
    assert "<persona>" in result.system_prompt
    assert "<identity>" not in result.system_prompt
    assert "<core_persona>" not in result.system_prompt
    assert "<unparsed_sections>" in result.system_prompt
    assert "You are Alice." in result.system_prompt


def test_render_engine_routes_cross_target_slots():
    pack = ContextPack(
        slots={
            "persona.prompt": ContextSlot(
                name="persona.prompt",
                value="You are Alice.",
                category="persona",
                source="test",
            ),
            "persona.begin_dialogs": ContextSlot(
                name="persona.begin_dialogs",
                value=[
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi"},
                ],
                category="persona",
                source="test",
            ),
            "capability.subagent_router_prompt": ContextSlot(
                name="capability.subagent_router_prompt",
                value="Route carefully.",
                category="tools",
                source="test",
            ),
            "capability.tools_schema": ContextSlot(
                name="capability.tools_schema",
                value={
                    "format": "tool_inventory_v1",
                    "tool_count": 1,
                    "tools": [
                        {
                            "name": "tool_a",
                            "description": "Run tool A",
                            "parameters": {"type": "object", "properties": {}},
                            "schema": {"type": "function"},
                            "active": True,
                            "handler_module_path": "mod.tool_a",
                        }
                    ],
                },
                category="tools",
                source="test",
            ),
        }
    )

    engine = PromptRenderEngine(default_renderer=BasePromptRenderer())
    result = engine.render(pack)

    assert "<history>" not in result.system_prompt
    assert "<tools>" not in result.system_prompt
    assert "Route carefully." in result.system_prompt
    assert result.messages == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]
    assert result.tool_schema == [
        {
            "type": "function",
            "function": {
                "name": "tool_a",
                "description": "Run tool A",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def test_render_engine_applies_persona_whitelists_to_capabilities():
    pack = ContextPack(
        slots={
            "persona.tools_whitelist": ContextSlot(
                name="persona.tools_whitelist",
                value=["tool_a"],
                category="persona",
                source="test",
            ),
            "persona.skills_whitelist": ContextSlot(
                name="persona.skills_whitelist",
                value=["skill_a"],
                category="persona",
                source="test",
            ),
            "capability.skills_prompt": ContextSlot(
                name="capability.skills_prompt",
                value={
                    "format": "skills_inventory_v1",
                    "runtime": "local",
                    "skill_count": 2,
                    "skills": [
                        {
                            "name": "skill_a",
                            "description": "Alpha skill",
                            "path": "/skills/a/SKILL.md",
                            "source_type": "local_only",
                            "source_label": "local",
                            "active": True,
                            "local_exists": True,
                            "sandbox_exists": False,
                        },
                        {
                            "name": "skill_b",
                            "description": "Beta skill",
                            "path": "/skills/b/SKILL.md",
                            "source_type": "local_only",
                            "source_label": "local",
                            "active": True,
                            "local_exists": True,
                            "sandbox_exists": False,
                        },
                    ],
                },
                category="tools",
                source="test",
            ),
            "capability.tools_schema": ContextSlot(
                name="capability.tools_schema",
                value={
                    "format": "tool_inventory_v1",
                    "tool_count": 2,
                    "tools": [
                        {
                            "name": "tool_a",
                            "description": "Tool A",
                            "parameters": {"type": "object", "properties": {}},
                            "schema": {"type": "function"},
                            "active": True,
                            "handler_module_path": "mod.tool_a",
                        },
                        {
                            "name": "tool_b",
                            "description": "Tool B",
                            "parameters": {"type": "object", "properties": {}},
                            "schema": {"type": "function"},
                            "active": True,
                            "handler_module_path": "mod.tool_b",
                        },
                    ],
                },
                category="tools",
                source="test",
            ),
        }
    )

    engine = PromptRenderEngine(default_renderer=BasePromptRenderer())
    result = engine.render(pack)

    assert "skill_a" in result.system_prompt
    assert "skill_b" not in result.system_prompt
    assert result.tool_schema == [
        {
            "type": "function",
            "function": {
                "name": "tool_a",
                "description": "Tool A",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def test_render_engine_compiles_user_input_and_merged_tool_schema():
    pack = ContextPack(
        slots={
            "input.text": ContextSlot(
                name="input.text",
                value="Look at this",
                category="input",
                source="test",
            ),
            "input.images": ContextSlot(
                name="input.images",
                value=[
                    {
                        "ref": "file:///tmp/demo.png",
                        "transport": "file",
                        "source": "current",
                    }
                ],
                category="input",
                source="test",
            ),
            "input.files": ContextSlot(
                name="input.files",
                value=[
                    {
                        "name": "spec.txt",
                        "file": "/tmp/spec.txt",
                        "url": "",
                        "source": "current",
                        "reply_id": None,
                    }
                ],
                category="input",
                source="test",
            ),
            "capability.tools_schema": ContextSlot(
                name="capability.tools_schema",
                value={
                    "format": "tool_inventory_v1",
                    "tool_count": 1,
                    "tools": [
                        {
                            "name": "tool_a",
                            "description": "Tool A",
                            "parameters": {"type": "object", "properties": {}},
                            "schema": {"type": "function"},
                            "active": True,
                            "handler_module_path": "mod.tool_a",
                        }
                    ],
                },
                category="tools",
                source="test",
            ),
            "capability.subagent_handoff_tools": ContextSlot(
                name="capability.subagent_handoff_tools",
                value={
                    "format": "handoff_tools_v1",
                    "main_enable": True,
                    "remove_main_duplicate_tools": False,
                    "tool_count": 1,
                    "tools": [
                        {
                            "name": "transfer_to_writer",
                            "description": "Delegate to writer",
                            "parameters": {"type": "object", "properties": {}},
                        }
                    ],
                },
                category="tools",
                source="test",
            ),
        }
    )

    engine = PromptRenderEngine(default_renderer=BasePromptRenderer())
    result = engine.render(pack)

    assert result.messages[-1]["role"] == "user"
    assert isinstance(result.messages[-1]["content"], list)
    assert result.messages[-1]["content"] == [
        {
            "type": "text",
            "text": "<user_input>\n  <text>Look at this</text>\n</user_input>",
        },
        {
            "type": "image_url",
            "image_url": {"url": "file:///tmp/demo.png"},
        },
        {
            "type": "text",
            "text": "[File Attachment: name spec.txt, path /tmp/spec.txt]",
        },
    ]
    assert result.tool_schema == [
        {
            "type": "function",
            "function": {
                "name": "tool_a",
                "description": "Tool A",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "transfer_to_writer",
                "description": "Delegate to writer",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]


def test_render_engine_renders_workspace_and_local_env_prompts_in_system_prompt():
    pack = ContextPack(
        slots={
            "system.workspace_extra_prompt": ContextSlot(
                name="system.workspace_extra_prompt",
                value={
                    "path": "C:/workspace/EXTRA_PROMPT.md",
                    "text": "Use <workspace> rules & keep notes.",
                },
                category="system",
                source="test",
            ),
            "policy.local_env_prompt": ContextSlot(
                name="policy.local_env_prompt",
                value="You can use the local shell & inspect files.",
                category="system",
                source="test",
            ),
            "system.live_mode_prompt": ContextSlot(
                name="system.live_mode_prompt",
                value="Respond in live mode.",
                category="system",
                source="test",
            ),
        }
    )

    engine = PromptRenderEngine(default_renderer=BasePromptRenderer())
    result = engine.render(pack)

    assert result.system_prompt is not None
    assert "<workspace_extra_prompt>" in result.system_prompt
    assert "C:/workspace/EXTRA_PROMPT.md" in result.system_prompt
    assert "Use &lt;workspace&gt; rules &amp; keep notes." in result.system_prompt
    assert "<local_env>" in result.system_prompt
    assert "local shell &amp; inspect files." in result.system_prompt
    assert "<live_mode>" in result.system_prompt
    assert "Respond in live mode." in result.system_prompt


def test_render_engine_compiles_caption_and_file_extract_blocks_in_user_message():
    pack = ContextPack(
        slots={
            "input.text": ContextSlot(
                name="input.text",
                value="Look at this",
                category="input",
                source="test",
            ),
            "input.quoted_images": ContextSlot(
                name="input.quoted_images",
                value=[
                    {
                        "ref": "https://example.com/quoted.png",
                        "transport": "url",
                        "source": "quoted",
                        "reply_id": "reply-1",
                    }
                ],
                category="input",
                source="test",
            ),
            "input.quoted_image_captions": ContextSlot(
                name="input.quoted_image_captions",
                value=[
                    {
                        "ref": "https://example.com/quoted.png",
                        "caption": "Quoted <caption> & context",
                        "provider_id": "caption-provider",
                        "source": "quoted",
                        "reply_id": "reply-1",
                    }
                ],
                category="input",
                source="test",
            ),
            "input.images": ContextSlot(
                name="input.images",
                value=[
                    {
                        "ref": "file:///tmp/demo.png",
                        "transport": "file",
                        "source": "current",
                    }
                ],
                category="input",
                source="test",
            ),
            "input.image_captions": ContextSlot(
                name="input.image_captions",
                value=[
                    {
                        "ref": "file:///tmp/demo.png",
                        "caption": "Current <caption> & detail",
                        "provider_id": "caption-provider",
                        "source": "current",
                    }
                ],
                category="input",
                source="test",
            ),
            "input.files": ContextSlot(
                name="input.files",
                value=[
                    {
                        "name": "spec.txt",
                        "file": "/tmp/spec.txt",
                        "url": "",
                        "source": "current",
                        "reply_id": None,
                    }
                ],
                category="input",
                source="test",
            ),
            "input.file_extracts": ContextSlot(
                name="input.file_extracts",
                value=[
                    {
                        "name": "spec.txt",
                        "content": "File summary <raw> & detail",
                        "provider": "moonshotai",
                        "source": "current",
                    }
                ],
                category="input",
                source="test",
            ),
        }
    )

    engine = PromptRenderEngine(default_renderer=BasePromptRenderer())
    result = engine.render(pack)

    assert result.messages[-1]["role"] == "user"
    assert isinstance(result.messages[-1]["content"], list)

    text_parts = [
        part["text"]
        for part in result.messages[-1]["content"]
        if part.get("type") == "text"
    ]
    image_parts = [
        part
        for part in result.messages[-1]["content"]
        if part.get("type") == "image_url"
    ]

    assert "<user_input>\n  <text>Look at this</text>\n</user_input>" in text_parts
    assert any(
        "<quoted_image_captions>" in text
        and "Quoted &lt;caption&gt; &amp; context" in text
        and "reply-1" in text
        for text in text_parts
    )
    assert any(
        "<image_captions>" in text and "Current &lt;caption&gt; &amp; detail" in text
        for text in text_parts
    )
    assert any(
        "<file_extracts>" in text
        and "File summary &lt;raw&gt; &amp; detail" in text
        and "moonshotai" in text
        for text in text_parts
    )
    assert "[File Attachment: name spec.txt, path /tmp/spec.txt]" in text_parts
    assert image_parts == [
        {"type": "image_url", "image_url": {"url": "https://example.com/quoted.png"}},
        {"type": "image_url", "image_url": {"url": "file:///tmp/demo.png"}},
    ]


def test_render_engine_keeps_text_only_user_input_as_plain_string_message():
    pack = ContextPack(
        slots={
            "input.text": ContextSlot(
                name="input.text",
                value="Hello <there> & everyone",
                category="input",
                source="test",
            )
        }
    )

    engine = PromptRenderEngine(default_renderer=BasePromptRenderer())
    result = engine.render(pack)

    assert result.messages == [{"role": "user", "content": "Hello <there> & everyone"}]


def test_render_engine_includes_input_annotations_in_user_message():
    pack = ContextPack(
        slots={
            "input.text": ContextSlot(
                name="input.text",
                value="Please inspect this",
                category="input",
                source="test",
                meta={
                    "semantic_type": "user_text",
                    "explanation": "This text is the user's explicit request.",
                    "explanation_source": "platform",
                    "context_role": "primary",
                },
            ),
            "input.quoted_text": ContextSlot(
                name="input.quoted_text",
                value="Earlier screenshot context",
                category="input",
                source="test",
                meta={
                    "semantic_type": "quoted_reference",
                    "explanation": "This quoted text comes from the message being referenced.",
                    "explanation_source": "platform",
                    "context_role": "reference",
                },
            ),
            "input.images": ContextSlot(
                name="input.images",
                value=[
                    {
                        "ref": "file:///tmp/desktop.png",
                        "transport": "file",
                        "source": "current",
                        "semantic_type": "desktop_screenshot",
                        "explanation": "This image is the user's current desktop screenshot.",
                        "explanation_source": "platform",
                        "context_role": "supporting",
                    }
                ],
                category="input",
                source="test",
            ),
            "input.files": ContextSlot(
                name="input.files",
                value=[
                    {
                        "name": "debug.log",
                        "file": "/tmp/debug.log",
                        "url": "",
                        "source": "current",
                        "reply_id": None,
                        "semantic_type": "runtime_log",
                        "explanation": "This file is the runtime log collected for debugging.",
                        "explanation_source": "platform",
                        "context_role": "reference",
                    }
                ],
                category="input",
                source="test",
            ),
        }
    )

    engine = PromptRenderEngine(default_renderer=BasePromptRenderer())
    result = engine.render(pack)

    assert result.messages[-1]["role"] == "user"
    assert isinstance(result.messages[-1]["content"], list)

    text_parts = [
        part["text"]
        for part in result.messages[-1]["content"]
        if part.get("type") == "text"
    ]
    image_parts = [
        part
        for part in result.messages[-1]["content"]
        if part.get("type") == "image_url"
    ]

    assert any(
        "<text_annotation>" in text
        and "This text is the user&apos;s explicit request." in text
        and "<quoted_text_annotation>" in text
        and "Earlier screenshot context" in text
        for text in text_parts
    )
    assert any(
        "<image_annotations>" in text
        and "desktop_screenshot" in text
        and "current desktop screenshot" in text
        for text in text_parts
    )
    assert any(
        "<file_annotations>" in text and "runtime_log" in text and "debug.log" in text
        for text in text_parts
    )
    assert image_parts == [
        {"type": "image_url", "image_url": {"url": "file:///tmp/desktop.png"}}
    ]
    assert any(
        part.get("type") == "text"
        and part.get("text") == "[File Attachment: name debug.log, path /tmp/debug.log]"
        for part in result.messages[-1]["content"]
    )


def test_render_engine_escapes_markup_text_in_system_and_structured_input():
    pack = ContextPack(
        slots={
            "persona.prompt": ContextSlot(
                name="persona.prompt",
                value="You are <Alice> & Bob > Carol \"quotes\" 'apostrophe'",
                category="persona",
                source="test",
            ),
            "session.user_info": ContextSlot(
                name="session.user_info",
                value={
                    "user_id": "u1",
                    "nickname": 'Alice <Admin> & Co "Lead"',
                    "platform_name": "qq",
                    "umo": "qq:group:1",
                    "group_id": "1",
                    "group_name": "Dev > Test's",
                    "is_group": True,
                },
                category="session",
                source="test",
            ),
            "input.text": ContextSlot(
                name="input.text",
                value="Need <help> & support",
                category="input",
                source="test",
            ),
            "input.quoted_text": ContextSlot(
                name="input.quoted_text",
                value="Quoted > raw & text",
                category="input",
                source="test",
            ),
        }
    )

    engine = PromptRenderEngine(default_renderer=BasePromptRenderer())
    result = engine.render(pack)

    assert result.system_prompt is not None
    assert (
        "You are &lt;Alice&gt; &amp; Bob &gt; Carol &quot;quotes&quot; &apos;apostrophe&apos;"
        in result.system_prompt
    )
    assert result.messages == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "<request_context>\n"
                        "  <session>\n"
                        "    <user_info>\n"
                        "      <user_id>u1</user_id>\n"
                        "      <nickname>Alice &lt;Admin&gt; &amp; Co &quot;Lead&quot;</nickname>\n"
                        "      <platform_name>qq</platform_name>\n"
                        "      <umo>qq:group:1</umo>\n"
                        "      <group_id>1</group_id>\n"
                        "      <group_name>Dev &gt; Test&apos;s</group_name>\n"
                        "      <is_group>true</is_group>\n"
                        "    </user_info>\n"
                        "  </session>\n"
                        "</request_context>"
                    ),
                },
                {
                    "type": "text",
                    "text": (
                        "<user_input>\n"
                        "  <text>Need &lt;help&gt; &amp; support</text>\n"
                        "  <quoted_text>Quoted &gt; raw &amp; text</quoted_text>\n"
                        "</user_input>"
                    ),
                },
            ],
        }
    ]


def test_render_engine_includes_session_context_in_current_user_message():
    pack = ContextPack(
        slots={
            "session.datetime": ContextSlot(
                name="session.datetime",
                value={
                    "text": "2026-04-17 20:30 (CST)",
                    "iso": "2026-04-17T20:30:00+08:00",
                    "timezone": "Asia/Shanghai",
                    "source": "config.timezone",
                },
                category="session",
                source="test",
            ),
            "session.user_info": ContextSlot(
                name="session.user_info",
                value={
                    "user_id": "u1",
                    "nickname": "Alice",
                    "platform_name": "qq",
                    "umo": "qq:group:1",
                    "group_id": "1",
                    "group_name": "AstrBot Dev",
                    "is_group": True,
                },
                category="session",
                source="test",
            ),
            "input.text": ContextSlot(
                name="input.text",
                value="Hello there",
                category="input",
                source="test",
            ),
        }
    )

    engine = PromptRenderEngine(default_renderer=BasePromptRenderer())
    result = engine.render(pack)

    assert result.messages == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "<request_context>\n"
                        "  <session>\n"
                        "    <datetime>\n"
                        "      <text>2026-04-17 20:30 (CST)</text>\n"
                        "      <iso>2026-04-17T20:30:00+08:00</iso>\n"
                        "      <timezone>Asia/Shanghai</timezone>\n"
                        "      <source>config.timezone</source>\n"
                        "    </datetime>\n"
                        "    <user_info>\n"
                        "      <user_id>u1</user_id>\n"
                        "      <nickname>Alice</nickname>\n"
                        "      <platform_name>qq</platform_name>\n"
                        "      <umo>qq:group:1</umo>\n"
                        "      <group_id>1</group_id>\n"
                        "      <group_name>AstrBot Dev</group_name>\n"
                        "      <is_group>true</is_group>\n"
                        "    </user_info>\n"
                        "  </session>\n"
                        "</request_context>"
                    ),
                },
                {
                    "type": "text",
                    "text": "<user_input>\n  <text>Hello there</text>\n</user_input>",
                },
            ],
        }
    ]
    assert result.system_prompt is None or "<session>" not in result.system_prompt


def test_render_engine_returns_prompt_tree_and_system_prompt():
    pack = ContextPack(
        slots={
            "persona.prompt": ContextSlot(
                name="persona.prompt",
                value="You are Alice.",
                category="persona",
                source="test",
            )
        }
    )

    engine = PromptRenderEngine()
    result = engine.render(pack)

    assert result.prompt_tree is not None
    assert result.system_prompt is not None
    assert "<persona>" in result.system_prompt
    assert result.messages == []
    assert result.tool_schema is None
    assert result.metadata["renderer"] == "base"
    assert result.metadata["engine"] == "PromptRenderEngine"
    assert result.metadata["enabled_slot_groups"] == list(
        BasePromptRenderer.ALL_SLOT_GROUPS
    )
    assert result.metadata["rendered_slots"] == ["persona.prompt"]
    assert result.metadata["compiled_message_count"] == 0
    assert result.metadata["compiled_tool_count"] == 0


def test_render_engine_emits_debug_log_for_render_result():
    pack = ContextPack(
        slots={
            "persona.prompt": ContextSlot(
                name="persona.prompt",
                value="You are Alice.",
                category="persona",
                source="test",
            ),
            "input.text": ContextSlot(
                name="input.text",
                value="Hello there",
                category="input",
                source="test",
            ),
        }
    )

    engine = PromptRenderEngine()
    with (
        patch.object(render_logger, "isEnabledFor", return_value=True),
        patch.object(render_logger, "debug") as debug_mock,
    ):
        engine.render(pack)

    debug_mock.assert_called_once()
    message_template, payload = debug_mock.call_args.args
    assert message_template == "Prompt render result: %s"
    assert '"renderer": "base"' in payload
    assert '"system_prompt_preview": "<system>' in payload
    assert '"message_count": 1' in payload
    assert '"content_preview": "Hello there"' in payload


def test_render_engine_respects_renderer_disabled_groups():
    class NoKnowledgeRenderer(BasePromptRenderer):
        def get_enabled_slot_groups(self) -> tuple[str, ...]:
            return tuple(
                group for group in self.ALL_SLOT_GROUPS if group != "knowledge"
            )

    pack = ContextPack(
        slots={
            "knowledge.snippets": ContextSlot(
                name="knowledge.snippets",
                value={
                    "format": "kb_text_block_v1",
                    "query": "test",
                    "text": "Knowledge result.",
                },
                category="memory",
                source="test",
            )
        }
    )

    engine = PromptRenderEngine(default_renderer=NoKnowledgeRenderer())
    result = engine.render(pack)

    assert result.system_prompt is None
    assert result.messages == []
    assert result.tool_schema is None


def test_custom_renderer_can_override_group_renderer():
    class CompactSessionRenderer(BasePromptRenderer):
        def include_session_in_system_prompt(self) -> bool:
            return True

        def render_session_context(
            self,
            target,
            slots,
            *,
            pack,
            resolve_node,
            event=None,
            plugin_context=None,
            config=None,
            provider_request=None,
        ) -> list[str]:
            del (
                slots,
                pack,
                resolve_node,
                event,
                plugin_context,
                config,
                provider_request,
            )
            self._add_text_tag(target, "compact", "user=Alice")
            return ["session.user_info"]

    pack = ContextPack(
        slots={
            "session.user_info": ContextSlot(
                name="session.user_info",
                value={"user_id": "u1", "nickname": "Alice"},
                category="session",
                source="test",
            )
        }
    )

    engine = PromptRenderEngine(default_renderer=CompactSessionRenderer())
    result = engine.render(pack)

    assert "user=Alice" in result.system_prompt
    assert "<compact>" in result.system_prompt
    assert "<nickname>" not in result.system_prompt


def test_custom_renderer_can_override_render_text_escape():
    class CustomEscapeRenderer(BasePromptRenderer):
        def escape_render_text(self, text: str) -> str:
            return text.replace("&", "[amp]").replace("<", "[lt]").replace(">", "[gt]")

    pack = ContextPack(
        slots={
            "persona.prompt": ContextSlot(
                name="persona.prompt",
                value="You are <Alice> & Bob",
                category="persona",
                source="test",
            )
        }
    )

    engine = PromptRenderEngine(default_renderer=CustomEscapeRenderer())
    result = engine.render(pack)

    assert result.system_prompt is not None
    assert "You are [lt]Alice[gt] [amp] Bob" in result.system_prompt


def test_render_engine_renders_extension_slots_to_system_and_input_targets():
    pack = ContextPack(
        slots={
            "extension.system": ContextSlot(
                name="extension.system",
                value={
                    "format": "prompt_extensions_v1",
                    "mount": "system",
                    "items": [
                        {
                            "plugin_id": "desktop.sidecar",
                            "title": "Desktop Mode",
                            "value_kind": "mapping",
                            "value": {"mode": "assistant"},
                            "order": 100,
                            "meta": {},
                        }
                    ],
                },
                category="extension",
                source="test",
            ),
            "extension.input": ContextSlot(
                name="extension.input",
                value={
                    "format": "prompt_extensions_v1",
                    "mount": "input",
                    "items": [
                        {
                            "plugin_id": "desktop.sidecar",
                            "title": "Desktop Snapshot",
                            "value_kind": "mapping",
                            "value": {"summary": "Visible desktop"},
                            "order": 100,
                            "meta": {},
                        }
                    ],
                },
                category="extension",
                source="test",
            ),
            "extension.conversation": ContextSlot(
                name="extension.conversation",
                value={
                    "format": "prompt_extensions_v1",
                    "mount": "conversation",
                    "items": [
                        {
                            "plugin_id": "desktop.sidecar",
                            "title": None,
                            "value_kind": "mapping",
                            "value": {"topic": "desktop help"},
                            "order": 100,
                            "meta": {},
                        }
                    ],
                },
                category="extension",
                source="test",
            ),
            "input.text": ContextSlot(
                name="input.text",
                value="Hello",
                category="input",
                source="test",
            ),
        }
    )

    result = PromptRenderEngine(default_renderer=BasePromptRenderer()).render(pack)

    assert result.system_prompt is not None
    assert "<extensions>" in result.system_prompt
    assert "<desktop_sidecar>" in result.system_prompt
    assert "<plugin_id>" in result.system_prompt
    assert "desktop.sidecar" in result.system_prompt
    assert "<conversation_extensions>" in result.system_prompt
    assert "desktop help" in result.system_prompt
    assert len(result.messages) == 1
    assert result.messages[0]["role"] == "user"
    assert len(result.messages[0]["content"]) == 2
    assert result.messages[0]["content"][0]["type"] == "text"
    assert "<extensions>" in result.messages[0]["content"][0]["text"]
    assert "desktop.sidecar" in result.messages[0]["content"][0]["text"]
    assert result.messages[0]["content"][1] == {
        "type": "text",
        "text": "<user_input>\n  <text>Hello</text>\n</user_input>",
    }
