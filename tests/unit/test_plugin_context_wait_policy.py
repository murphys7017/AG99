"""End-to-end plugin context wait policy through the Persona Expression agent.

These tests exercise the real policy entry point
(``get_or_build_interaction_persona_context_pack``) rather than asserting that it
was called: they verify which pack reaches the Persona prompt under each
configured mode, that the plugin enrichment task runs exactly once, and that
Personal and Core consume the same single-flight result.
"""

import asyncio

import pytest

from astrbot.core.interaction import context_builder as context_builder_module
from astrbot.core.interaction.context_builder import (
    get_or_build_interaction_core_plugin_context_pack,
    get_or_build_interaction_persona_context_pack,
)
from astrbot.core.interaction.expression_agent import (
    InteractionExpressionAgent,
    PersonaExpressionRequest,
)
from astrbot.core.interaction.turn_state import (
    InteractionContextMaterial,
    ensure_interaction_turn_state,
)
from astrbot.core.interaction.types import InteractionAgentConfig
from astrbot.core.prompt.context_types import ContextPack, ContextSlot

BASE_SLOT = "system.base"
PLUGIN_SLOT = "extension.system"


class _Event:
    session_id = "s"
    unified_msg_origin = "webchat:FriendMessage:s"

    def __init__(self):
        self._extras = {}

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_platform_id(self):
        return "webchat"

    def is_stopped(self):
        return False


class _PluginContext:
    """Minimal Context stand-in: the policy must not re-collect anything."""

    def __init__(self):
        self.collect_calls = 0

    def get_config(self, umo=None):
        del umo
        return {}

    def list_prompt_extension_collectors(self, event=None):
        return []

    def list_persona_effects(self, event=None):
        return []


def _base_pack() -> ContextPack:
    return ContextPack(
        slots={
            BASE_SLOT: ContextSlot(
                name=BASE_SLOT,
                value="base",
                category="system",
                source="test",
            )
        }
    )


def _plugin_pack() -> ContextPack:
    return ContextPack(
        slots={
            BASE_SLOT: ContextSlot(
                name=BASE_SLOT,
                value="base",
                category="system",
                source="test",
            ),
            PLUGIN_SLOT: ContextSlot(
                name=PLUGIN_SLOT,
                value={
                    "format": "prompt_extensions_v1",
                    "mount": "system",
                    "items": [
                        {
                            "plugin_id": "demo",
                            "title": "Demo",
                            "value": "v",
                            "meta": {"targets": ["persona", "core"]},
                        }
                    ],
                },
                category="extension",
                source="test",
                render_mode="structured",
            ),
        }
    )


def _material(
    *,
    ready: bool,
    builds: list[int],
    delay: float = 5.0,
) -> InteractionContextMaterial:
    """Build material with one single-flight plugin enrichment task."""

    async def build_plugin_pack() -> ContextPack:
        builds.append(1)
        if not ready:
            await asyncio.sleep(delay)
        return _plugin_pack()

    material = InteractionContextMaterial(prompt_context_pack=_base_pack())
    material.target_context_tasks["plugin"] = asyncio.ensure_future(build_plugin_pack())
    return material


def _config(mode: str) -> InteractionAgentConfig:
    config = InteractionAgentConfig()
    config.persona_plugin_context_mode = mode
    return config


@pytest.mark.asyncio
async def test_best_effort_uses_base_pack_while_plugin_enrichment_is_pending():
    """best_effort must not block on an unfinished plugin task."""
    event = _Event()
    ensure_interaction_turn_state(event)
    builds: list[int] = []
    material = _material(ready=False, builds=builds)

    try:
        pack = await get_or_build_interaction_persona_context_pack(
            event=event,
            plugin_context=_PluginContext(),
            interaction_config=_config("best_effort"),
            build_config=None,
            material=material,
        )
        names = set(pack.slots)
        assert BASE_SLOT in names
        assert PLUGIN_SLOT not in names, "best_effort must fall back to the base pack"
        assert material.target_context_packs.get("plugin") is None
    finally:
        material.target_context_tasks["plugin"].cancel()
        with pytest.raises(asyncio.CancelledError):
            await material.target_context_tasks["plugin"]


@pytest.mark.asyncio
async def test_wait_complete_waits_for_the_same_plugin_task():
    """wait_complete must await the shared task and use its pack."""
    event = _Event()
    ensure_interaction_turn_state(event)
    builds: list[int] = []
    material = _material(ready=True, builds=builds)

    pack = await get_or_build_interaction_persona_context_pack(
        event=event,
        plugin_context=_PluginContext(),
        interaction_config=_config("wait_complete"),
        build_config=None,
        material=material,
    )
    assert PLUGIN_SLOT in set(pack.slots)
    assert builds == [1]


@pytest.mark.asyncio
async def test_personal_and_core_share_one_enrichment_task():
    """Both consumers must reuse the single-flight task, not re-collect."""
    event = _Event()
    ensure_interaction_turn_state(event)
    builds: list[int] = []
    plugin_context = _PluginContext()
    material = _material(ready=True, builds=builds)

    persona_pack = await get_or_build_interaction_persona_context_pack(
        event=event,
        plugin_context=plugin_context,
        interaction_config=_config("wait_complete"),
        build_config=None,
        material=material,
    )
    core_pack = await get_or_build_interaction_core_plugin_context_pack(
        event=event,
        plugin_context=plugin_context,
        build_config=None,
        material=material,
    )

    assert builds == [1], "the plugin enrichment task must run exactly once"
    assert persona_pack is core_pack, "Personal and Core must share one pack"
    assert PLUGIN_SLOT in set(core_pack.slots)


@pytest.mark.asyncio
async def test_persona_expression_waits_when_mode_is_wait_complete(monkeypatch):
    """Every Persona expression must obey ``wait_complete``.

    This is the discriminating case for the old defect: the ordinary first reply
    used to pass ``compact_context=True``, which read a possibly-pending plugin
    pack instead of waiting for it. Under ``wait_complete`` the agent must block
    until the shared enrichment task finishes and then use its pack.
    """
    event = _Event()
    ensure_interaction_turn_state(event)
    builds: list[int] = []
    material = _material(ready=False, builds=builds, delay=0.05)

    agent = InteractionExpressionAgent()
    monkeypatch.setattr(
        agent,
        "_build_or_reuse_context_material",
        _async_return(material),
    )

    class _Builder:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def build(self, **kwargs):
            return kwargs["base"]

    monkeypatch.setattr(
        context_builder_module,
        "PromptContextBuilder",
        _Builder,
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.PromptContextBuilder",
        _Builder,
    )

    async def _media_pack(**kwargs):
        return kwargs["base_context_pack"]

    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent."
        "get_or_build_interaction_media_context_pack",
        _media_pack,
    )

    result = await agent._prepare_render_result(
        event,
        _PluginContext(),
        _config("wait_complete"),
        _provider(),
        req=PersonaExpressionRequest(),
    )
    names = set(result.metadata.get("selected_slot_names", set()))
    assert PLUGIN_SLOT in names, (
        "wait_complete must wait for and use the plugin enrichment pack"
    )
    assert builds == [1]


@pytest.mark.asyncio
async def test_persona_expression_uses_base_pack_under_best_effort(monkeypatch):
    """Under ``best_effort`` a pending enrichment task must not block the reply."""
    event = _Event()
    ensure_interaction_turn_state(event)
    builds: list[int] = []
    material = _material(ready=False, builds=builds)

    agent = InteractionExpressionAgent()
    monkeypatch.setattr(
        agent,
        "_build_or_reuse_context_material",
        _async_return(material),
    )

    class _Builder:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def build(self, **kwargs):
            return kwargs["base"]

    monkeypatch.setattr(
        context_builder_module,
        "PromptContextBuilder",
        _Builder,
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent.PromptContextBuilder",
        _Builder,
    )

    async def _media_pack(**kwargs):
        return kwargs["base_context_pack"]

    monkeypatch.setattr(
        "astrbot.core.interaction.expression_agent."
        "get_or_build_interaction_media_context_pack",
        _media_pack,
    )

    try:
        result = await agent._prepare_render_result(
            event,
            _PluginContext(),
            _config("best_effort"),
            _provider(),
            req=PersonaExpressionRequest(),
        )
        names = set(result.metadata.get("selected_slot_names", set()))
        assert BASE_SLOT in names
        assert PLUGIN_SLOT not in names, "best_effort must not wait for enrichment"
    finally:
        material.target_context_tasks["plugin"].cancel()
        with pytest.raises(asyncio.CancelledError):
            await material.target_context_tasks["plugin"]


def _async_return(value):
    async def _inner(*args, **kwargs):
        del args, kwargs
        return value

    return _inner


def _provider():
    class _Provider:
        provider_config = {"modalities": ["text"]}

        def get_model(self):
            return "test-model"

    return _Provider()
