import asyncio

import pytest

from astrbot.core.interaction.context_builder import (
    build_interaction_collectors,
    collect_interaction_prompt_contributions,
    extract_recent_messages,
)
from types import MappingProxyType

from astrbot.core.interaction.contributors import (
    InteractionDecisionView,
    InteractionPromptContribution,
)
from astrbot.core.interaction.memory_store import (
    InteractionMemorySnapshot,
    InteractionMemoryStore,
    build_interaction_memory_payload,
    build_interaction_memory_reply_from_visible_outputs,
    update_interaction_memory_from_turn,
)
from astrbot.core.prompt.context_types import ContextPack, ContextSlot


def test_extract_recent_messages_includes_interaction_memory_turns():
    snapshot = InteractionMemorySnapshot(
        session_id="session",
        recent_turns=[
            {"user": "为什么没有啊", "assistant": "没有什么啊？"},
            {"user": "联网权限", "assistant": "没有联网权限。"},
        ],
    )
    pack = ContextPack()
    pack.add_slot(
        ContextSlot(
            name="memory.interaction",
            value=build_interaction_memory_payload(snapshot),
            category="memory",
            source="interaction_memory",
        )
    )

    messages = extract_recent_messages(pack, limit=8)

    assert messages == [
        {
            "source": "interaction_memory",
            "user_message": {
                "role": "user",
                "content": "联网权限",
            },
            "assistant_message": {
                "role": "assistant",
                "content": "没有联网权限。",
            },
        },
        {
            "source": "interaction_memory",
            "user_message": {
                "role": "user",
                "content": "为什么没有啊",
            },
            "assistant_message": {
                "role": "assistant",
                "content": "没有什么啊？",
            },
        },
    ]


def test_extract_recent_messages_uses_only_interaction_memory_turns():
    snapshot = InteractionMemorySnapshot(
        session_id="session",
        recent_turns=[
            {
                "user": "联网权限",
                "assistant": "没有联网权限。",
            },
        ],
    )
    pack = ContextPack()
    pack.add_slot(
        ContextSlot(
            name="conversation.history",
            value={
                "turns": [
                    {
                        "user_message": {
                            "role": "user",
                            "content": "联网权限",
                        },
                        "assistant_message": {
                            "role": "assistant",
                            "content": "没有联网权限。",
                        },
                    }
                ]
            },
            category="memory",
            source="conversation",
        )
    )
    pack.add_slot(
        ContextSlot(
            name="memory.interaction",
            value=build_interaction_memory_payload(snapshot),
            category="memory",
            source="interaction_memory",
        )
    )

    messages = extract_recent_messages(pack, limit=8)

    assert len(messages) == 1
    assert messages[0]["source"] == "interaction_memory"

def test_build_interaction_collectors_uses_only_interaction_collectors():
    collectors = build_interaction_collectors(InteractionMemoryStore())

    assert len(collectors) == 3
    assert collectors[-1].__class__.__name__ == "InteractionMemoryCollector"


def test_update_interaction_memory_from_turn_keeps_structured_recent_turns():
    snapshot = InteractionMemorySnapshot(session_id="session")

    snapshot = update_interaction_memory_from_turn(
        snapshot,
        user_text="为什么没有这些权限",
        visible_reply="权限设计问题。",
    )

    assert snapshot.recent_turns == [
        {
            "user": "为什么没有这些权限",
            "assistant": "权限设计问题。",
        }
    ]
    assert snapshot.recent_topics == ["为什么没有这些权限"]
    assert snapshot.last_impression_summary == "权限设计问题。"


def test_update_interaction_memory_merges_same_turn_id():
    snapshot = InteractionMemorySnapshot(session_id="session")
    snapshot = update_interaction_memory_from_turn(
        snapshot,
        user_text="查一下权限",
        visible_reply="等我看看。",
        turn_id="turn-1",
    )
    snapshot = update_interaction_memory_from_turn(
        snapshot,
        user_text="查一下权限",
        visible_reply="没有联网权限。",
        turn_id="turn-1",
    )

    assert snapshot.recent_turns == [
        {
            "user": "查一下权限",
            "assistant": "没有联网权限。",
            "turn_id": "turn-1",
        }
    ]


def test_build_interaction_memory_reply_from_visible_outputs_filters_by_turn_and_relevance():
    reply = build_interaction_memory_reply_from_visible_outputs(
        [
            {
                "turn_id": "turn-1",
                "kind": "immediate_reply",
                "text": "等我看看。",
                "memory_relevant": True,
            },
            {
                "turn_id": "turn-1",
                "kind": "stream_interjection",
                "text": "还在查。",
                "memory_relevant": False,
            },
            {
                "turn_id": "turn-1",
                "kind": "core_reply",
                "text": "你可以执行工作区命令。",
                "memory_relevant": True,
            },
            {
                "turn_id": "turn-2",
                "kind": "core_reply",
                "text": "别串轮。",
                "memory_relevant": True,
            },
        ],
        turn_id="turn-1",
    )

    assert reply == "等我看看。 你可以执行工作区命令。"


@pytest.mark.asyncio
async def test_interaction_memory_store_serializes_concurrent_updates(
    tmp_path,
):
    store = InteractionMemoryStore()
    store._base_dir = tmp_path

    async def _update(user_text: str, visible_reply: str, turn_id: str) -> None:
        await store.update_interaction_memory(
            "session-1",
            "persona-1",
            lambda snapshot: update_interaction_memory_from_turn(
                snapshot,
                user_text=user_text,
                visible_reply=visible_reply,
                turn_id=turn_id,
            ),
        )

    await asyncio.gather(
        _update("问题一", "回答一", "turn-1"),
        _update("问题二", "回答二", "turn-2"),
    )

    snapshot = await store.load_interaction_memory("session-1", "persona-1")

    assert {turn["turn_id"] for turn in snapshot.recent_turns} == {
        "turn-1",
        "turn-2",
    }


class GoodPromptContributor:
    plugin_id = "good"
    priority = 10

    async def collect(self, event, plugin_context, config, decision_context):
        return InteractionPromptContribution(
            plugin_id=self.plugin_id,
            content={"ok": True},
            priority=self.priority,
        )


class ViewPromptContributor:
    plugin_id = "view"
    priority = 5

    def __init__(self):
        self.view = None

    async def collect(self, event, plugin_context, view):
        assert isinstance(view, InteractionDecisionView)
        assert view.turn_id == "turn-1"
        assert view["platform_id"] == "test-platform"
        assert view.config["provider_settings"]["name"] == "provider"
        assert view.decision_context["persona"]["name"] == "Yakumo"
        assert view.persona["name"] == "Yakumo"
        assert view.input["text"] == "hello"
        assert view.interaction_memory["recent_turns"] == ()
        assert view.recent_messages[0]["source"] == "unit"
        assert view.capabilities["tools_available"] is True
        with pytest.raises(TypeError):
            view.metadata["bad"] = True
        with pytest.raises(TypeError):
            view.config["provider_settings"]["name"] = "changed"
        with pytest.raises(TypeError):
            view.decision_context["persona"]["name"] = "changed"
        with pytest.raises(TypeError):
            view.recent_messages[0]["source"] = "changed"
        with pytest.raises(AttributeError):
            view.recent_messages.append({"source": "bad"})
        self.view = view
        return InteractionPromptContribution(plugin_id=self.plugin_id, priority=5)


class FailingPromptContributor:
    plugin_id = "bad"
    priority = 1

    async def collect(self, event, plugin_context, config, decision_context):
        raise RuntimeError("broken")


class NewSignatureTypeErrorPromptContributor:
    plugin_id = "new-type-error"

    async def collect(self, event, plugin_context, view):
        raise TypeError("internal type error")


class LegacyPromptContributor:
    plugin_id = "legacy"

    def __init__(self):
        self.config = None
        self.decision_context = None

    async def collect(self, event, plugin_context, config, decision_context):
        self.config = config
        self.decision_context = decision_context
        return InteractionPromptContribution(plugin_id=self.plugin_id)


def _prompt_event():
    return type(
        "Event",
        (),
        {
            "_extras": {"_turn_id": "turn-1"},
            "unified_msg_origin": "session-1",
            "session_id": "session-1",
            "get_platform_id": lambda self: "test-platform",
            "get_extra": lambda self, key, default=None: self._extras.get(key, default),
            "set_extra": lambda self, key, value: self._extras.__setitem__(key, value),
        },
    )()


def _decision_context():
    return {
        "persona": {"name": "Yakumo"},
        "memory": {"recent_turns": []},
        "recent_messages": [{"source": "unit"}],
        "input": {"text": "hello"},
        "core_capabilities": {"tools_available": True},
    }


@pytest.mark.asyncio
async def test_prompt_contributor_receives_read_only_decision_view():
    event = _prompt_event()
    contributor = ViewPromptContributor()
    config = {"provider_settings": {"name": "provider"}}
    decision_context = _decision_context()
    plugin_context = type(
        "PluginContext",
        (),
        {"list_interaction_prompt_contributors": lambda self: [contributor]},
    )()

    contributions = await collect_interaction_prompt_contributions(
        event,
        plugin_context,
        config=config,
        decision_context=decision_context,
    )

    assert [item.plugin_id for item in contributions] == ["view"]
    assert isinstance(contributor.view.config, MappingProxyType)
    assert config["provider_settings"]["name"] == "provider"
    assert decision_context["persona"]["name"] == "Yakumo"
    assert decision_context["recent_messages"][0]["source"] == "unit"


@pytest.mark.asyncio
async def test_legacy_prompt_contributor_signature_still_receives_old_arguments():
    event = _prompt_event()
    contributor = LegacyPromptContributor()
    config = {"provider_settings": {"name": "provider"}}
    decision_context = _decision_context()
    plugin_context = type(
        "PluginContext",
        (),
        {"list_interaction_prompt_contributors": lambda self: [contributor]},
    )()

    contributions = await collect_interaction_prompt_contributions(
        event,
        plugin_context,
        config=config,
        decision_context=decision_context,
    )

    assert [item.plugin_id for item in contributions] == ["legacy"]
    assert contributor.config is config
    assert contributor.decision_context is decision_context


@pytest.mark.asyncio
async def test_new_prompt_contributor_internal_type_error_is_recorded():
    event = _prompt_event()
    plugin_context = type(
        "PluginContext",
        (),
        {
            "list_interaction_prompt_contributors": lambda self: [
                NewSignatureTypeErrorPromptContributor()
            ]
        },
    )()

    contributions = await collect_interaction_prompt_contributions(
        event,
        plugin_context,
        config={},
        decision_context={},
    )

    assert contributions == []
    assert event.get_extra("_interaction_prompt_contributor_failures") == [
        {"plugin_id": "new-type-error", "error": "internal type error"}
    ]


@pytest.mark.asyncio
async def test_prompt_contributor_failure_is_recorded_and_ignored():
    event = type(
        "Event",
        (),
        {
            "_extras": {},
            "get_extra": lambda self, key, default=None: self._extras.get(key, default),
            "set_extra": lambda self, key, value: self._extras.__setitem__(key, value),
        },
    )()
    plugin_context = type(
        "PluginContext",
        (),
        {
            "list_interaction_prompt_contributors": lambda self: [
                FailingPromptContributor(),
                GoodPromptContributor(),
            ]
        },
    )()

    contributions = await collect_interaction_prompt_contributions(
        event,
        plugin_context,
        config={},
        decision_context={},
    )

    assert [item.plugin_id for item in contributions] == ["good"]
    assert event.get_extra("_interaction_prompt_contributor_failures") == [
        {"plugin_id": "bad", "error": "broken"}
    ]
