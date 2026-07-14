from astrbot.core.prompt import ContextPack, ContextSlot, PromptTarget
from astrbot.core.prompt.targets import project_context_pack


def _slot(name: str, value, category: str) -> ContextSlot:
    return ContextSlot(name=name, value=value, category=category, source="test")


def _canonical_pack() -> ContextPack:
    return ContextPack(
        slots={
            "system.base": _slot("system.base", "system", "system"),
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
            "capability.router_plugin_directory": _slot(
                "capability.router_plugin_directory", {"plugins": []}, "tools"
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
        "capability.router_plugin_directory",
    }
    assert projected.get_slot("conversation.history").value["turns"] == [
        {"id": 1},
        {"id": 2},
        {"id": 3},
        {"id": 4},
    ]
    assert "relationship_notes" not in projected.get_slot("memory.interaction").value
    assert source.get_slot("conversation.history").value["turn_count"] == 5


def test_persona_projection_keeps_history_and_hides_core_capabilities():
    projected = project_context_pack(_canonical_pack(), PromptTarget.PERSONA)

    assert projected.get_slot("persona.prompt") is not None
    assert projected.get_slot("conversation.history") is not None
    assert projected.get_slot("memory.persona_state") is not None
    assert projected.get_slot("capability.tools_schema") is None
    assert projected.get_slot("knowledge.snippets") is None


def test_core_projection_keeps_execution_context_without_persona_material():
    projected = project_context_pack(_canonical_pack(), PromptTarget.CORE)

    assert projected.get_slot("conversation.history") is not None
    assert projected.get_slot("conversation.group_recent") is not None
    assert projected.get_slot("knowledge.snippets") is not None
    assert projected.get_slot("capability.tools_schema") is not None
    assert projected.get_slot("persona.prompt") is None
    assert projected.get_slot("persona.summary") is None
    assert projected.get_slot("memory.persona_state") is None
    assert projected.get_slot("memory.interaction") is None
    assert projected.get_slot("input.visible_reply_material") is None


def test_extension_targets_are_filtered_for_every_prompt_target():
    pack = ContextPack(
        slots={
            "extension.context": _slot(
                "extension.context",
                {
                    "items": [
                        {"plugin_id": "router", "meta": {"targets": ["router"]}},
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
