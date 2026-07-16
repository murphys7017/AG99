from astrbot.core.prompt import ContextPack, ContextSlot, PromptTarget
from astrbot.core.prompt.targets import project_context_pack


def _slot(name: str, value, category: str) -> ContextSlot:
    return ContextSlot(name=name, value=value, category=category, source="test")


def _canonical_pack() -> ContextPack:
    return ContextPack(
        slots={
            "system.base": _slot("system.base", "system", "system"),
            "system.core_execution_context": _slot(
                "system.core_execution_context",
                {"execution_prompt": "run core task"},
                "system",
            ),
            "persona.prompt": _slot("persona.prompt", "full persona", "persona"),
            "persona.summary": _slot("persona.summary", "brief persona", "persona"),
            "input.text": _slot("input.text", "current", "input"),
            "input.visible_reply_material": _slot(
                "input.visible_reply_material", {"source_text": "core"}, "input"
            ),
            "conversation.history": _slot(
                "conversation.history",
                {
                    "turn_count": 5,
                    "turns": [{"id": index} for index in range(5)],
                },
                "memory",
            ),
            "conversation.group_recent": _slot(
                "conversation.group_recent", [{"text": "ambient"}], "conversation"
            ),
            "memory.interaction": _slot(
                "memory.interaction",
                {
                    "recent_turns": [{"id": index} for index in range(6)],
                    "recent_topics": ["topic"],
                    "relationship_notes": ["private"],
                },
                "memory",
            ),
            "memory.persona_state": _slot(
                "memory.persona_state", {"mood": "calm"}, "memory"
            ),
            "knowledge.snippets": _slot(
                "knowledge.snippets", {"text": "docs"}, "rag"
            ),
            "capability.tools_schema": _slot(
                "capability.tools_schema", {"tools": []}, "tools"
            ),
            "capability.core_summary": _slot(
                "capability.core_summary",
                {"tools_available": True},
                "capability",
            ),
            "capability.plugin_directory": _slot(
                "capability.plugin_directory",
                {
                    "plugins": [
                        {
                            "name": "Router Plugin",
                            "description": "Router-visible capability",
                            "targets": ["router"],
                        },
                        {
                            "name": "Planner Plugin",
                            "description": "Planner-visible capability",
                            "targets": ["core_planner"],
                        },
                    ]
                },
                "capability",
            ),
            "interaction.route_decision": _slot(
                "interaction.route_decision", {"route_mode": "hybrid"}, "internal"
            ),
        }
    )


def test_router_projection_uses_summary_and_recent_context_only():
    source = _canonical_pack()

    projected = project_context_pack(source, PromptTarget.ROUTER)

    assert set(projected.slots) == {
        "system.base",
        "persona.summary",
        "input.text",
        "conversation.history",
        "conversation.group_recent",
        "memory.interaction",
        "capability.plugin_directory",
    }
    assert projected.get_slot("conversation.history").value["turns"] == [
        {"id": 1},
        {"id": 2},
        {"id": 3},
        {"id": 4},
    ]
    assert "relationship_notes" not in projected.get_slot("memory.interaction").value
    assert projected.get_slot("capability.plugin_directory").value == {
        "plugins": [
            {
                "name": "Router Plugin",
                "description": "Router-visible capability",
            }
        ]
    }
    assert source.get_slot("conversation.history").value["turn_count"] == 5
    assert source.get_slot("capability.plugin_directory").value["plugins"][0][
        "targets"
    ] == ["router"]


def test_persona_projection_keeps_history_and_hides_core_capabilities():
    projected = project_context_pack(_canonical_pack(), PromptTarget.PERSONA)

    assert projected.get_slot("persona.prompt") is not None
    assert projected.get_slot("conversation.history") is not None
    assert projected.get_slot("memory.persona_state") is not None
    assert projected.get_slot("capability.tools_schema") is None
    assert projected.get_slot("knowledge.snippets") is None
    assert projected.get_slot("system.core_execution_context") is None


def test_core_planner_projection_uses_facts_without_router_or_persona_decisions():
    projected = project_context_pack(_canonical_pack(), PromptTarget.CORE_PLANNER)

    assert projected.get_slot("input.text") is not None
    assert projected.get_slot("conversation.history") is not None
    assert projected.get_slot("memory.interaction") is not None
    assert projected.get_slot("capability.plugin_directory") is not None
    assert projected.get_slot("capability.plugin_directory").value["plugins"] == [
        {
            "name": "Planner Plugin",
            "description": "Planner-visible capability",
        }
    ]
    assert projected.get_slot("capability.core_summary") is not None
    assert projected.get_slot("persona.summary") is None
    assert projected.get_slot("interaction.route_decision") is None
    assert projected.get_slot("system.core_execution_context") is None


def test_plugin_directory_entries_inherit_slot_targets():
    pack = ContextPack(
        slots={
            "capability.plugin_directory": ContextSlot(
                name="capability.plugin_directory",
                value={
                    "plugins": [
                        {
                            "name": "Direct Plugin",
                            "description": "Direct capability",
                        }
                    ]
                },
                category="capability",
                source="plugin",
                meta={"targets": ["router"]},
            )
        }
    )

    router = project_context_pack(pack, PromptTarget.ROUTER)
    planner = project_context_pack(pack, PromptTarget.CORE_PLANNER)

    assert router.get_slot("capability.plugin_directory").value == {
        "plugins": [
            {"name": "Direct Plugin", "description": "Direct capability"}
        ]
    }
    assert planner.get_slot("capability.plugin_directory") is None


def test_direct_slot_targets_are_enforced_before_target_rules():
    pack = ContextPack(
        slots={
            "conversation.group_recent": ContextSlot(
                name="conversation.group_recent",
                value={"records": ["ambient"]},
                category="conversation",
                source="plugin",
                meta={"targets": ["core"]},
            )
        }
    )

    assert (
        project_context_pack(pack, PromptTarget.CORE_PLANNER).get_slot(
            "conversation.group_recent"
        )
        is None
    )
    assert (
        project_context_pack(pack, PromptTarget.CORE).get_slot(
            "conversation.group_recent"
        )
        is not None
    )


def test_direct_slot_with_malformed_targets_is_hidden():
    pack = ContextPack(
        slots={
            "input.text": ContextSlot(
                name="input.text",
                value="private",
                category="input",
                source="plugin",
                meta={"targets": "router"},
            )
        }
    )

    for target in PromptTarget:
        assert project_context_pack(pack, target).get_slot("input.text") is None


def test_router_and_planner_views_remove_runtime_diagnostics_without_mutating_source():
    source = _canonical_pack()
    history = source.get_slot("conversation.history")
    history.value["turns"][-1] = {
        "user_message": {"role": "user", "content": "请继续"},
        "assistant_message": {
            "role": "assistant",
            "content": "Traceback (most recent call last): failed",
            "reasoning_content": "private",
            "tool_calls": [{"name": "internal"}],
        },
    }
    group_recent = source.get_slot("conversation.group_recent")
    group_recent.value = {
        "records": [
            "user_id=1: hello",
            "bot: 获取图片描述失败: invalid image input",
        ],
        "text": "raw diagnostics",
    }

    router = project_context_pack(source, PromptTarget.ROUTER)
    planner = project_context_pack(source, PromptTarget.CORE_PLANNER)

    for projected in (router, planner):
        assistant = projected.get_slot("conversation.history").value["turns"][-1][
            "assistant_message"
        ]
        assert assistant["content"] == "[runtime diagnostic omitted]"
        assert "reasoning_content" not in assistant
        assert "tool_calls" not in assistant
        assert (
            projected.get_slot("conversation.group_recent").value["records"][-1]
            == "[runtime diagnostic omitted]"
        )
    assert "Traceback" in history.value["turns"][-1]["assistant_message"]["content"]


def test_core_projection_keeps_execution_context_without_persona_material():
    projected = project_context_pack(_canonical_pack(), PromptTarget.CORE)

    assert projected.get_slot("conversation.history") is not None
    assert projected.get_slot("conversation.group_recent") is not None
    assert projected.get_slot("knowledge.snippets") is not None
    assert projected.get_slot("capability.tools_schema") is not None
    assert projected.get_slot("capability.core_summary") is None
    assert projected.get_slot("persona.prompt") is None
    assert projected.get_slot("persona.summary") is None
    assert projected.get_slot("memory.persona_state") is None
    assert projected.get_slot("memory.interaction") is None
    assert projected.get_slot("input.visible_reply_material") is None
    assert projected.get_slot("system.core_execution_context") is not None


def test_extension_targets_are_filtered_for_every_prompt_target():
    pack = ContextPack(
        slots={
            "extension.context": _slot(
                "extension.context",
                {
                    "items": [
                        {"plugin_id": "router", "meta": {"targets": ["router"]}},
                        {
                            "plugin_id": "core_planner",
                            "meta": {"targets": ["core_planner"]},
                        },
                        {"plugin_id": "persona", "meta": {"targets": ["persona"]}},
                        {"plugin_id": "core", "meta": {"targets": ["core"]}},
                    ]
                },
                "extension",
            )
        }
    )

    for target in PromptTarget:
        projected = project_context_pack(pack, target)
        items = projected.get_slot("extension.context").value["items"]
        assert [item["plugin_id"] for item in items] == [target.value]
