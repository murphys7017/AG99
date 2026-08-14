import asyncio

import pytest

from astrbot.core.interaction.context_builder import (
    get_or_build_interaction_persona_context_pack,
)
from astrbot.core.interaction.turn_state import InteractionContextMaterial
from astrbot.core.interaction.types import (
    InteractionAgentConfig,
    InteractionPromptBuildConfig,
)
from astrbot.core.prompt import (
    ContextPack,
    ContextSlot,
    PromptContextConflictError,
    merge_context_packs,
)


def _slot(name: str, value, source: str = "test") -> ContextSlot:
    return ContextSlot(
        name=name,
        value=value,
        category="input",
        source=source,
    )


class _PersonaContextEvent:
    session_id = "test-session"

    @staticmethod
    def get_platform_id() -> str:
        return "test"


@pytest.mark.asyncio
async def test_persona_context_mode_uses_base_while_plugin_pack_is_pending():
    base_pack = ContextPack(slots={"input.text": _slot("input.text", "base")})
    plugin_pack = ContextPack(
        slots={"input.text": _slot("input.text", "plugin")}
    )
    release_plugin_pack = asyncio.Event()

    async def build_plugin_pack() -> ContextPack:
        await release_plugin_pack.wait()
        return plugin_pack

    task = asyncio.create_task(build_plugin_pack())
    material = InteractionContextMaterial(
        prompt_context_pack=base_pack,
        target_context_tasks={"plugin": task},
    )
    try:
        selected = await get_or_build_interaction_persona_context_pack(
            event=_PersonaContextEvent(),
            plugin_context=object(),
            interaction_config=InteractionAgentConfig(
                persona_plugin_context_mode="best_effort"
            ),
            build_config=InteractionPromptBuildConfig(),
            material=material,
        )
        assert selected is base_pack
        assert not task.done()

        release_plugin_pack.set()
        await task
        selected_after_completion = await get_or_build_interaction_persona_context_pack(
            event=_PersonaContextEvent(),
            plugin_context=object(),
            interaction_config=InteractionAgentConfig(
                persona_plugin_context_mode="best_effort"
            ),
            build_config=InteractionPromptBuildConfig(),
            material=material,
        )
        assert selected_after_completion is plugin_pack

        wait_complete_task = asyncio.create_task(asyncio.sleep(0, result=plugin_pack))
        wait_complete_material = InteractionContextMaterial(
            prompt_context_pack=base_pack,
            target_context_tasks={"plugin": wait_complete_task},
        )
        selected_wait_complete = await get_or_build_interaction_persona_context_pack(
            event=_PersonaContextEvent(),
            plugin_context=object(),
            interaction_config=InteractionAgentConfig(
                persona_plugin_context_mode="wait_complete"
            ),
            build_config=InteractionPromptBuildConfig(),
            material=wait_complete_material,
        )
        assert selected_wait_complete is plugin_pack
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_merge_context_packs_returns_new_versioned_snapshot():
    base = ContextPack(
        slots={"input.text": _slot("input.text", "before")},
        meta={"context_version": 1, "collection_scopes": ["base"], "base": True},
    )
    fragment = ContextPack(
        slots={"input.quoted_text": _slot("input.quoted_text", "quote")},
        meta={"fragment": True},
    )

    merged = merge_context_packs(base, fragment, scope="persona")

    assert set(merged.slots) == {"input.text", "input.quoted_text"}
    assert merged.meta["context_version"] == 2
    assert merged.meta["collection_scopes"] == ["base", "persona"]
    assert merged.meta["base"] is True
    assert merged.meta["fragment"] is True
    assert set(base.slots) == {"input.text"}
    assert "context_version" not in fragment.meta


def test_merge_context_packs_rejects_implicit_replacement():
    base = ContextPack(slots={"input.text": _slot("input.text", "before", "a")})
    fragment = ContextPack(
        slots={"input.text": _slot("input.text", "after", "b")}
    )

    with pytest.raises(PromptContextConflictError, match="input.text"):
        merge_context_packs(base, fragment)


def test_merge_context_packs_rejects_same_value_with_different_metadata():
    base_slot = _slot("input.text", "same", "base")
    fragment_slot = _slot("input.text", "same", "base")
    fragment_slot.llm_exposure = "never"

    with pytest.raises(PromptContextConflictError, match="input.text"):
        merge_context_packs(
            ContextPack(slots={"input.text": base_slot}),
            ContextPack(slots={"input.text": fragment_slot}),
        )


def test_merge_context_packs_allows_declared_replacement():
    base = ContextPack(slots={"input.text": _slot("input.text", "before")})
    fragment = ContextPack(slots={"input.text": _slot("input.text", "after")})

    merged = merge_context_packs(
        base,
        fragment,
        replace_slots=frozenset({"input.text"}),
    )

    assert merged.get_slot("input.text").value == "after"
    assert base.get_slot("input.text").value == "before"


def test_merge_context_packs_merges_plugin_directories_and_inherits_targets():
    base = ContextPack(
        slots={
            "capability.plugin_directory": ContextSlot(
                name="capability.plugin_directory",
                value={
                    "plugins": [
                        {"name": "Base", "description": "Base capability"}
                    ]
                },
                category="capability",
                source="base",
                meta={"targets": ["core"]},
            )
        },
        meta={"collectors": ["BaseCollector"]},
    )
    fragment = ContextPack(
        slots={
            "capability.plugin_directory": ContextSlot(
                name="capability.plugin_directory",
                value={
                    "plugins": [
                        {"name": "Plugin", "description": "Plugin capability"}
                    ]
                },
                category="capability",
                source="plugin",
                meta={"targets": ["persona"]},
            )
        },
        meta={"collectors": ["PluginCollector"]},
    )

    merged = merge_context_packs(base, fragment, scope="plugin")

    assert merged.get_slot("capability.plugin_directory").value["plugins"] == [
        {
            "name": "Base",
            "description": "Base capability",
            "targets": ["core"],
        },
        {
            "name": "Plugin",
            "description": "Plugin capability",
            "targets": ["persona"],
        },
    ]
    assert merged.meta["collectors"] == ["BaseCollector", "PluginCollector"]
