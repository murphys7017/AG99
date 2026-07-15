import asyncio
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.db.po import Conversation
from astrbot.core.interaction.collectors import (
    InteractionCapabilityCollector,
    InteractionMemoryCollector,
)
from astrbot.core.interaction.context_builder import (
    InteractionPromptContributorCollector,
    InteractionPromptContributorError,
    _build_attachment_summary,
    build_interaction_context_pack,
    collect_interaction_prompt_extensions,
    extract_recent_messages,
    get_or_build_interaction_context_material,
)
from astrbot.core.interaction.contributors import InteractionPromptView
from astrbot.core.interaction.memory_store import (
    InteractionMemorySnapshot,
    InteractionMemoryStore,
    build_interaction_memory_payload,
    build_interaction_memory_reply_from_visible_outputs,
    update_interaction_memory_from_turn,
)
from astrbot.core.interaction.turn_state import InteractionTurnState
from astrbot.core.interaction.types import (
    InteractionAgentConfig,
    InteractionPromptBuildConfig,
)
from astrbot.core.prompt import PromptContextBuilder
from astrbot.core.prompt.context_types import ContextPack, ContextSlot
from astrbot.core.prompt.extensions import PromptExtension
from astrbot.core.prompt.targets import PromptTarget, project_context_pack
from astrbot.core.provider.entities import ProviderRequest


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

def test_attachment_summary_keeps_counts_without_media_refs():
    pack = ContextPack()
    pack.add_slot(
        ContextSlot(
            name="input.images",
            value=[{"ref": "file:///secret/a.png"}],
            category="input",
            source="unit",
        )
    )
    pack.add_slot(
        ContextSlot(
            name="input.files",
            value=[
                {"name": "a.txt", "ref": "C:/secret/a.txt"},
                {"name": "b.txt", "ref": "C:/secret/b.txt"},
            ],
            category="input",
            source="unit",
        )
    )

    summary = _build_attachment_summary(pack)
    filtered = project_context_pack(pack, PromptTarget.ROUTER)

    assert summary == {"images": 1, "files": 2}
    assert "input.images" not in filtered.slots
    assert "input.files" not in filtered.slots


@pytest.mark.asyncio
async def test_build_interaction_context_pack_collects_canonical_facts():
    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"
        message_str = "current"
        message_obj = type("Message", (), {"message": []})()

        def __init__(self, provider_request):
            self._extras = {"provider_request": provider_request}

        def get_extra(self, key=None, default=None):
            if key is None:
                return self._extras
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_name(self):
            return "webchat"

        def get_group_id(self):
            return None

    req = ProviderRequest()
    req.conversation = Conversation(
        platform_id="webchat",
        user_id="user",
        cid="conv-id",
        history=(
            '[{"role":"user","content":"u1"},{"role":"assistant","content":"a1"},'
            '{"role":"user","content":"u2"},{"role":"assistant","content":"a2"},'
            '{"role":"user","content":"u3"},{"role":"assistant","content":"a3"},'
            '{"role":"user","content":"u4"},{"role":"assistant","content":"a4"},'
            '{"role":"user","content":"u5"},{"role":"assistant","content":"a5"}]'
        ),
    )
    snapshot = InteractionMemorySnapshot(
        session_id="webchat:friend:session-1",
        recent_turns=[
            {"user": "mu1", "assistant": "ma1"},
            {"user": "mu2", "assistant": "ma2"},
            {"user": "mu3", "assistant": "ma3"},
            {"user": "mu4", "assistant": "ma4"},
            {"user": "mu5", "assistant": "ma5"},
        ],
        recent_topics=["topic"],
        ongoing_threads=["thread"],
        last_impression_summary="summary",
    )
    store = type(
        "Store",
        (),
        {"load_interaction_memory": AsyncMock(return_value=snapshot)},
    )()
    plugin_context = type(
        "PluginContext",
        (),
        {
            "conversation_manager": None,
            "get_config": lambda self, umo=None: {},
        },
    )()

    pack = await build_interaction_context_pack(
        Event(req),
        plugin_context,
        config=InteractionPromptBuildConfig(timezone="Asia/Shanghai"),
        memory_store=store,
    )

    history_slot = pack.get_slot("conversation.history")
    memory_slot = pack.get_slot("memory.interaction")
    assert pack.get_slot("input.text").value == "current"
    assert pack.get_slot("session.datetime") is not None
    assert pack.get_slot("session.datetime").value["timezone"] == "Asia/Shanghai"
    assert pack.get_slot("session.user_info").value["is_group"] is False
    assert history_slot is not None
    assert history_slot.value["turn_count"] == 5
    assert memory_slot is not None
    assert len(memory_slot.value["recent_turns"]) == 5

    router_pack = project_context_pack(pack, PromptTarget.ROUTER)
    router_history = router_pack.get_slot("conversation.history")
    router_memory = router_pack.get_slot("memory.interaction")
    assert router_pack.get_slot("session.datetime").value["timezone"] == (
        "Asia/Shanghai"
    )
    assert router_history.value["turn_count"] == 4
    assert [
        turn["user_message"]["content"] for turn in router_history.value["turns"]
    ] == ["u2", "u3", "u4", "u5"]
    assert router_memory.value == {
        "recent_turns": [
            {"user": "mu1", "assistant": "ma1"},
            {"user": "mu2", "assistant": "ma2"},
            {"user": "mu3", "assistant": "ma3"},
            {"user": "mu4", "assistant": "ma4"},
        ],
        "recent_topics": ["topic"],
        "ongoing_threads": ["thread"],
        "last_impression_summary": "summary",
    }


@pytest.mark.asyncio
async def test_interaction_context_collects_plugin_facts_once_before_projection(
    monkeypatch,
):
    class Event:
        session_id = "session-1"
        unified_msg_origin = "webchat:friend:session-1"

        def __init__(self):
            self._extras = {
                "_turn_id": "turn-1",
                "_interaction_turn_state": InteractionTurnState(turn_id="turn-1"),
            }

        def get_extra(self, key=None, default=None):
            if key is None:
                return self._extras
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

        def get_platform_id(self):
            return "webchat"

    class Contributor:
        plugin_id = "plugin.catalog"

        def __init__(self):
            self.calls = 0
            self.views = []

        async def collect(self, event, plugin_context, view):
            self.calls += 1
            self.views.append(view)
            return [
                PromptExtension(
                    plugin_id=self.plugin_id,
                    mount="context",
                    title="Persona Runtime",
                    value={"state": "ready"},
                    meta={"targets": ["persona"]},
                ),
                PromptExtension(
                    plugin_id=self.plugin_id,
                    mount="capability",
                    value={
                        "plugins": [
                            {
                                "name": "Local Runtime",
                                "description": "Executes local runtime tasks.",
                            }
                        ]
                    },
                    meta={"targets": ["router", "core_planner"]},
                ),
            ]

    canonical_pack = ContextPack(
        slots={
            "input.text": ContextSlot(
                name="input.text",
                value="hello",
                category="input",
                source="test",
            ),
            "capability.core_summary": ContextSlot(
                name="capability.core_summary",
                value={"tools_available": False},
                category="capability",
                source="test",
            ),
        }
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.context_builder.build_interaction_context_pack",
        AsyncMock(return_value=canonical_pack),
    )
    contributor = Contributor()
    plugin_context = type(
        "PluginContext",
        (),
        {
            "list_interaction_prompt_contributors": lambda self: [contributor],
        },
    )()
    event = Event()
    kwargs = {
        "event": event,
        "plugin_context": plugin_context,
        "interaction_config": InteractionAgentConfig(),
        "build_config": InteractionPromptBuildConfig(),
        "memory_store": SimpleNamespace(),
    }

    first = await get_or_build_interaction_context_material(**kwargs)
    second = await get_or_build_interaction_context_material(**kwargs)

    assert first is second
    assert contributor.calls == 1
    assert contributor.views[0].purpose == "context_collection"
    assert contributor.views[0].phase == "collect"
    assert first.prompt_context_pack.get_slot("extension.context") is not None
    assert first.prompt_context_pack.get_slot("capability.plugin_directory") is not None

    router = project_context_pack(first.prompt_context_pack, PromptTarget.ROUTER)
    planner = project_context_pack(
        first.prompt_context_pack,
        PromptTarget.CORE_PLANNER,
    )
    persona = project_context_pack(first.prompt_context_pack, PromptTarget.PERSONA)
    assert router.get_slot("extension.context") is None
    assert planner.get_slot("extension.context") is None
    assert persona.get_slot("extension.context") is not None
    assert router.get_slot("capability.plugin_directory").value["plugins"][0][
        "name"
    ] == "Local Runtime"
    assert planner.get_slot("capability.plugin_directory").value["plugins"][0][
        "name"
    ] == "Local Runtime"


@pytest.mark.asyncio
async def test_interaction_memory_collector_core_brief_limits_fields_and_turns():
    snapshot = InteractionMemorySnapshot(
        session_id="session",
        recent_turns=[
            {"user": "u1", "assistant": "a1"},
            {"user": "u2", "assistant": "a2"},
            {"user": "u3", "assistant": "a3"},
        ],
        speaking_style_notes=["warm"],
        user_preferences=["concise"],
        relationship_notes=["friend"],
        recent_topics=["topic"],
        ongoing_threads=["thread"],
        last_impression_summary="summary",
    )
    store = type(
        "Store",
        (),
        {"load_interaction_memory": AsyncMock(return_value=snapshot)},
    )()
    collector = InteractionMemoryCollector(
        store,
        recent_turn_limit=2,
        brief=True,
    )

    slots = await collector.collect(
        _prompt_event(),
        plugin_context=None,
        config=None,
    )

    assert slots[0].value == {
        "recent_turns": [
            {"user": "u1", "assistant": "a1"},
            {"user": "u2", "assistant": "a2"},
        ],
        "recent_topics": ["topic"],
        "ongoing_threads": ["thread"],
        "last_impression_summary": "summary",
    }


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

    async def collect(self, event, plugin_context, view):
        return PromptExtension(
            plugin_id=self.plugin_id,
            mount="capability",
            value={"ok": True},
            order=self.priority,
            meta={"scope": "static", "node_type": "unit"},
        )


class ViewPromptContributor:
    plugin_id = "view"
    priority = 5

    def __init__(self):
        self.view = None

    async def collect(self, event, plugin_context, view):
        assert isinstance(view, InteractionPromptView)
        assert view.turn_id == "turn-1"
        assert view.purpose == "context_collection"
        assert view.phase == "collect"
        assert view["platform_id"] == "test-platform"
        assert view.config["provider_settings"]["name"] == "provider"
        assert view.context_snapshot["persona"]["name"] == "Yakumo"
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
            view.context_snapshot["persona"]["name"] = "changed"
        with pytest.raises(TypeError):
            view.recent_messages[0]["source"] = "changed"
        with pytest.raises(AttributeError):
            view.recent_messages.append({"source": "bad"})
        self.view = view
        return [
            PromptExtension(
                plugin_id=self.plugin_id,
                mount="capability",
                title="Capability",
                value={"ok": True},
                order=5,
                meta={"scope": "static", "node_type": "capability_contract"},
            ),
            PromptExtension(
                plugin_id=self.plugin_id,
                mount="context",
                title="Runtime State",
                value={"state": "ready"},
                order=6,
                meta={"scope": "dynamic", "node_type": "runtime_state"},
            ),
        ]


class FailingPromptContributor:
    plugin_id = "bad"
    priority = 1

    async def collect(self, event, plugin_context, view):
        raise RuntimeError("broken")


class NewSignatureTypeErrorPromptContributor:
    plugin_id = "new-type-error"

    async def collect(self, event, plugin_context, view):
        raise TypeError("internal type error")


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


def _context_snapshot():
    return {
        "persona": {"name": "Yakumo"},
        "memory": {"recent_turns": []},
        "recent_messages": [{"source": "unit"}],
        "input": {"text": "hello"},
        "core_capabilities": {"tools_available": True},
    }


@pytest.mark.asyncio
async def test_prompt_contributor_receives_read_only_canonical_view():
    event = _prompt_event()
    contributor = ViewPromptContributor()
    config = {"provider_settings": {"name": "provider"}}
    context_snapshot = _context_snapshot()
    plugin_context = type(
        "PluginContext",
        (),
        {"list_interaction_prompt_contributors": lambda self: [contributor]},
    )()

    extensions = await collect_interaction_prompt_extensions(
        event,
        plugin_context,
        config=config,
        context_snapshot=context_snapshot,
    )

    assert [item.plugin_id for item in extensions] == ["view", "view"]
    assert isinstance(contributor.view.config, MappingProxyType)
    assert config["provider_settings"]["name"] == "provider"
    assert context_snapshot["persona"]["name"] == "Yakumo"
    assert context_snapshot["recent_messages"][0]["source"] == "unit"
    pack = await PromptContextBuilder(event, plugin_context, config).build(
        collectors=[InteractionPromptContributorCollector(context_snapshot)],
        include_prompt_extensions=False,
        scope="interaction_contributors",
    )
    capability_slot = pack.get_slot("extension.capability")
    context_slot = pack.get_slot("extension.context")
    assert capability_slot is not None
    assert context_slot is not None
    assert capability_slot.value["items"][0]["meta"] == {
        "scope": "static",
        "node_type": "capability_contract",
        "targets": ["persona"],
    }
    assert context_slot.value["items"][0]["meta"] == {
        "scope": "dynamic",
        "node_type": "runtime_state",
        "targets": ["persona"],
    }


@pytest.mark.asyncio
async def test_interaction_capability_summary_uses_core_tool_selection_rules():
    from astrbot.core.agent.tool import FunctionTool, ToolSet

    active = FunctionTool(name="active_tool", description="active", parameters={})
    inactive = FunctionTool(
        name="inactive_tool",
        description="inactive",
        parameters={},
        active=False,
    )
    request = ProviderRequest(func_tool=ToolSet([active, inactive]))
    event = _prompt_event()
    plugin_context = SimpleNamespace(
        kb_manager=None,
        subagent_orchestrator=None,
        persona_manager=None,
    )

    slots = await InteractionCapabilityCollector().collect(
        event,
        plugin_context,
        InteractionPromptBuildConfig(),
        request,
    )

    assert slots[0].value["sample_tools"] == ["active_tool"]
    assert slots[0].value["tool_count"] == 1
    assert slots[0].value["tool_selection_mode"] == "provider_request"


@pytest.mark.asyncio
async def test_prompt_contributor_internal_type_error_fails_fast():
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

    with pytest.raises(InteractionPromptContributorError, match="internal type error"):
        await collect_interaction_prompt_extensions(
            event,
            plugin_context,
            config={},
            context_snapshot={},
        )

    assert event.get_extra("_interaction_prompt_contributor_failures") == [
        {"plugin_id": "new-type-error", "error": "internal type error"}
    ]


@pytest.mark.asyncio
async def test_prompt_contributor_failure_is_recorded_and_fails_fast():
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

    with pytest.raises(InteractionPromptContributorError, match="broken"):
        await collect_interaction_prompt_extensions(
            event,
            plugin_context,
            config={},
            context_snapshot={},
        )

    assert event.get_extra("_interaction_prompt_contributor_failures") == [
        {"plugin_id": "bad", "error": "broken"}
    ]


@pytest.mark.asyncio
async def test_prompt_contributor_invalid_payload_fails_fast():
    event = _prompt_event()

    class InvalidPromptContributor:
        plugin_id = "invalid"

        async def collect(self, event, plugin_context, view):
            return {"not": "a prompt extension"}

    plugin_context = type(
        "PluginContext",
        (),
        {"list_interaction_prompt_contributors": lambda self: [InvalidPromptContributor()]},
    )()

    with pytest.raises(InteractionPromptContributorError, match="PromptExtension"):
        await collect_interaction_prompt_extensions(
            event,
            plugin_context,
            config={},
            context_snapshot={},
        )


@pytest.mark.asyncio
async def test_prompt_contributor_invalid_extension_mount_fails_fast():
    event = _prompt_event()

    class InvalidMountPromptContributor:
        plugin_id = "invalid-mount"

        async def collect(self, event, plugin_context, view):
            return PromptExtension(
                plugin_id=self.plugin_id,
                mount="bad",
                value={"bad": True},
            )

    plugin_context = type(
        "PluginContext",
        (),
        {
            "list_interaction_prompt_contributors": lambda self: [
                InvalidMountPromptContributor()
            ]
        },
    )()

    with pytest.raises(InteractionPromptContributorError, match="invalid mount"):
        await collect_interaction_prompt_extensions(
            event,
            plugin_context,
            config={},
            context_snapshot={},
        )
    assert event.get_extra("_interaction_prompt_contributor_failures") == [
        {
            "plugin_id": "invalid-mount",
            "error": "Prompt extension has invalid mount: plugin_id=invalid-mount mount=bad",
        }
    ]
