"""Tests for prompt selector placeholders."""

import pytest

from astrbot.core.prompt.context_types import ContextPack, ContextSlot
from astrbot.core.prompt.render import (
    LLMPromptContextSelector,
    PassthroughPromptSelector,
    PromptSelectionDecision,
    PromptSelectorInterface,
    PromptSelectorSettings,
    RuleBasedPromptSelector,
    apply_prompt_selection,
    select_context_pack,
    select_context_pack_async,
)
from astrbot.core.prompt.render.selector import _extract_json_object
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.provider.provider import Provider


class _CustomSelector(PromptSelectorInterface):
    def select(
        self,
        pack: ContextPack,
        *,
        event=None,
        plugin_context=None,
        config=None,
        provider_request=None,
    ) -> ContextPack:
        selected = ContextPack(
            slots=dict(pack.slots),
            provider_request_ref=pack.provider_request_ref,
            meta=dict(pack.meta),
        )
        selected.add_slot(
            ContextSlot(
                name="system.base",
                value="selected",
                category="system",
                source="test",
            )
        )
        return selected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1', {"a": 1}),
        ('{"spoken_reply":"ok","effect_calls":[{"name":"ag99live.motion","arguments":{"x":1}}]', {
            "spoken_reply": "ok",
            "effect_calls": [{"name": "ag99live.motion", "arguments": {"x": 1}}],
        }),
        ("```json\n{'spoken_reply': 'ok',}\n```", {"spoken_reply": "ok"}),
        (
            'prefix {"spoken_reply":"ok","effect_calls":[]} suffix',
            {"spoken_reply": "ok", "effect_calls": []},
        ),
    ],
)
def test_extract_json_object_repairs_common_model_output(text, expected):
    assert _extract_json_object(text) == expected


def test_extract_json_object_does_not_treat_plain_text_as_json():
    assert _extract_json_object("普通自然语言回复") is None


class _FakeEvent:
    message_str = "请根据项目文档回答"

    def __init__(self) -> None:
        self.extra = {}

    def set_extra(self, key, value):
        self.extra[key] = value


class _FakeProvider(Provider):
    def __init__(self, response_text: str) -> None:
        super().__init__({"id": "selector", "type": "openai_chat_completion"}, {})
        self.response_text = response_text

    def get_current_key(self) -> str:
        return "test"

    def set_key(self, key: str) -> None:
        del key

    async def get_models(self) -> list[str]:
        return ["qwen3:1.7b"]

    async def text_chat(self, *args, **kwargs) -> LLMResponse:
        del args, kwargs
        return LLMResponse(role="assistant", completion_text=self.response_text)


class _FakePluginContext:
    def __init__(self, provider: Provider | None) -> None:
        self.provider = provider

    def get_provider_by_id(self, provider_id: str):
        return self.provider if provider_id == "selector" else None


def _slot(name: str, value, category: str = "system") -> ContextSlot:
    return ContextSlot(name=name, value=value, category=category, source="test")


def _build_pack() -> ContextPack:
    return ContextPack(
        slots={
            "system.base": _slot("system.base", "system"),
            "persona.prompt": _slot("persona.prompt", "persona", "persona"),
            "input.text": _slot("input.text", "你好", "input"),
            "conversation.history": _slot(
                "conversation.history",
                {
                    "format": "turn_pairs",
                    "turn_count": 3,
                    "turns": [
                        {"user": "u1", "assistant": "a1"},
                        {"user": "u2", "assistant": "a2"},
                        {"user": "u3", "assistant": "a3"},
                    ],
                },
                "memory",
            ),
            "memory.short_term": _slot(
                "memory.short_term",
                {"short_summary": "summary"},
                "memory",
            ),
            "memory.long_term_memories": _slot(
                "memory.long_term_memories",
                {"items": [{"summary": "long"}]},
                "memory",
            ),
            "knowledge.snippets": _slot(
                "knowledge.snippets",
                {"text": "knowledge"},
                "rag",
            ),
            "capability.tools_schema": _slot(
                "capability.tools_schema",
                {"tools": [{"name": "tool"}]},
                "tools",
            ),
            "capability.subagent_handoff_tools": _slot(
                "capability.subagent_handoff_tools",
                {"tools": [{"name": "handoff"}]},
                "tools",
            ),
        }
    )


def test_passthrough_prompt_selector_returns_original_pack():
    pack = ContextPack(
        slots={
            "input.text": ContextSlot(
                name="input.text",
                value="hello",
                category="input",
                source="test",
            )
        }
    )

    selected = PassthroughPromptSelector().select(pack)

    assert selected is pack


def test_select_context_pack_uses_passthrough_selector_by_default():
    pack = ContextPack(
        slots={
            "input.text": ContextSlot(
                name="input.text",
                value="hello",
                category="input",
                source="test",
            )
        }
    )

    selected = select_context_pack(pack)

    assert selected is pack


def test_select_context_pack_supports_custom_selector():
    pack = ContextPack(
        slots={
            "input.text": ContextSlot(
                name="input.text",
                value="hello",
                category="input",
                source="test",
            )
        }
    )

    selected = select_context_pack(pack, selector=_CustomSelector())

    assert selected is not pack
    assert selected.get_slot("input.text") is not None
    assert selected.get_slot("system.base") is not None


def test_apply_prompt_selection_filters_heavy_slots_for_minimal_profile():
    pack = _build_pack()
    decision = PromptSelectionDecision(
        profile="minimal",
        tools=False,
        subagent=False,
        history="none",
        memory="none",
        knowledge=False,
    )

    selected = apply_prompt_selection(pack, decision)

    assert selected.get_slot("input.text") is not None
    assert selected.get_slot("conversation.history") is None
    assert selected.get_slot("memory.short_term") is None
    assert selected.get_slot("knowledge.snippets") is None
    assert selected.get_slot("capability.tools_schema") is None
    assert selected.get_slot("capability.subagent_handoff_tools") is None


def test_apply_prompt_selection_truncates_recent_history():
    pack = _build_pack()
    decision = PromptSelectionDecision(
        history="recent",
        memory="none",
        tools=False,
        subagent=False,
        knowledge=False,
    )

    selected = apply_prompt_selection(pack, decision, recent_history_turns=2)
    history_slot = selected.get_slot("conversation.history")

    assert history_slot is not None
    assert history_slot.value["turn_count"] == 2
    assert history_slot.value["turns"] == [
        {"user": "u2", "assistant": "a2"},
        {"user": "u3", "assistant": "a3"},
    ]
    assert history_slot.meta["selection_truncated"] is True


def test_rule_based_prompt_selector_detects_casual_input():
    pack = _build_pack()
    selector = RuleBasedPromptSelector()

    selected = selector.select(pack)

    assert selected.meta["selection"]["profile"] == "minimal"
    assert selected.get_slot("capability.tools_schema") is None
    assert selected.get_slot("knowledge.snippets") is None


@pytest.mark.asyncio
async def test_async_llm_prompt_selector_uses_provider_decision():
    pack = _build_pack()
    provider = _FakeProvider(
        '{"profile":"balanced","tools":false,"subagent":false,'
        '"history":"none","memory":"none","knowledge":true,'
        '"confidence":0.92,"reason":"knowledge request"}'
    )
    selector = LLMPromptContextSelector(
        PromptSelectorSettings(
            enabled=True,
            provider_id="selector",
            model="qwen3:1.7b",
            use_rules_first=False,
        )
    )
    event = _FakeEvent()

    selected = await select_context_pack_async(
        pack,
        selector=selector,
        event=event,
        plugin_context=_FakePluginContext(provider),
    )

    assert selected.get_slot("knowledge.snippets") is not None
    assert selected.get_slot("conversation.history") is None
    assert selected.get_slot("memory.short_term") is None
    assert selected.get_slot("capability.tools_schema") is None
    assert event.extra["prompt_selection_decision"]["source"] == "llm"
