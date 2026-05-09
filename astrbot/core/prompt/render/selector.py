"""Selector helpers for the prompt render pipeline."""

from __future__ import annotations

import asyncio
import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

from astrbot.core import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.provider.provider import Provider
from astrbot.core.star.context import Context

from ..context_types import ContextPack, ContextSlot
from .interfaces import PromptSelectorInterface

PROMPT_SELECTED_CONTEXT_PACK_EXTRA_KEY = "prompt_selected_context_pack"
PROMPT_SELECTION_DECISION_EXTRA_KEY = "prompt_selection_decision"

PromptContextProfile = Literal["minimal", "balanced", "full"]

_HISTORY_KEYWORDS = (
    "之前",
    "刚才",
    "上面",
    "前面",
    "继续",
    "接着",
    "上一",
    "历史",
    "conversation",
    "previous",
    "continue",
    "above",
)
_MEMORY_KEYWORDS = (
    "记得",
    "记住",
    "我以前",
    "我的偏好",
    "我的习惯",
    "memory",
    "remember",
    "preference",
)
_KNOWLEDGE_KEYWORDS = (
    "知识库",
    "资料",
    "文档",
    "项目里",
    "检索",
    "根据文档",
    "knowledge",
    "docs",
    "document",
    "reference",
    "search in",
)
_TOOL_KEYWORDS = (
    "搜索",
    "查询",
    "读取",
    "打开",
    "执行",
    "运行",
    "生成",
    "下载",
    "修改文件",
    "调用",
    "search",
    "query",
    "read",
    "open",
    "run",
    "execute",
    "generate",
    "download",
    "call",
)
_SUBAGENT_KEYWORDS = (
    "subagent",
    "子代理",
    "代理",
    "并行",
    "委派",
    "多步骤",
    "复杂任务",
    "代码库分析",
    "delegate",
    "parallel",
    "multi-step",
)
_CASUAL_PATTERNS = (
    "hi",
    "hello",
    "hey",
    "你好",
    "您好",
    "早",
    "晚安",
    "谢谢",
    "thanks",
    "ok",
    "好的",
    "嗯",
    "哈哈",
)


@dataclass(slots=True)
class PromptSelectionDecision:
    """Selector output controlling context and capability exposure."""

    profile: PromptContextProfile = "balanced"
    tools: bool = True
    subagent: bool = True
    history: Literal["none", "recent", "detailed"] = "recent"
    memory: Literal["none", "light", "full"] = "light"
    knowledge: bool = True
    confidence: float = 1.0
    reason: str = "fallback"
    source: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(
        cls,
        payload: dict[str, Any],
        *,
        fallback: PromptSelectionDecision | None = None,
        source: str = "llm",
    ) -> PromptSelectionDecision:
        base = fallback or cls(source=source)
        profile = _normalize_choice(
            payload.get("profile") or payload.get("context_profile"),
            {"minimal", "balanced", "full"},
            base.profile,
        )
        history = _normalize_choice(
            payload.get("history") or payload.get("history_level"),
            {"none", "recent", "detailed"},
            base.history,
        )
        memory = _normalize_choice(
            payload.get("memory") or payload.get("memory_level"),
            {"none", "light", "full"},
            base.memory,
        )
        tools = _normalize_bool(
            payload.get("tools") if "tools" in payload else payload.get("needs_tools"),
            base.tools,
        )
        subagent = _normalize_bool(
            payload.get("subagent")
            if "subagent" in payload
            else payload.get("needs_subagent"),
            base.subagent,
        )
        knowledge = _normalize_bool(
            payload.get("knowledge")
            if "knowledge" in payload
            else payload.get("needs_knowledge"),
            base.knowledge,
        )
        confidence = _normalize_confidence(payload.get("confidence"), base.confidence)
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = base.reason
        return cls(
            profile=profile,  # type: ignore[arg-type]
            tools=tools,
            subagent=subagent,
            history=history,  # type: ignore[arg-type]
            memory=memory,  # type: ignore[arg-type]
            knowledge=knowledge,
            confidence=confidence,
            reason=reason.strip(),
            source=source,
        )


@dataclass(slots=True)
class PromptSelectorSettings:
    """Runtime settings for prompt context selection."""

    enabled: bool = False
    provider_id: str = "ollama"
    model: str = "qwen3:1.7b"
    timeout: float = 1.5
    min_confidence: float = 0.5
    fallback_profile: PromptContextProfile = "balanced"
    recent_history_turns: int = 2
    use_rules_first: bool = True

    @classmethod
    def from_config(cls, config: object | None) -> PromptSelectorSettings:
        provider_settings = getattr(config, "provider_settings", {}) or {}
        raw_settings = {}
        if isinstance(provider_settings, dict):
            raw_settings = provider_settings.get("prompt_selector", {}) or {}
        direct_settings = getattr(config, "prompt_selector", {}) or {}
        if isinstance(direct_settings, dict):
            raw_settings = {**raw_settings, **direct_settings}
        if not isinstance(raw_settings, dict):
            raw_settings = {}

        return cls(
            enabled=bool(
                raw_settings.get("enable", raw_settings.get("enabled", False))
            ),
            provider_id=_clean_string(raw_settings.get("provider_id")) or "ollama",
            model=_clean_string(raw_settings.get("model")) or "qwen3:1.7b",
            timeout=_coerce_float(raw_settings.get("timeout"), 1.5, minimum=0.1),
            min_confidence=_coerce_float(
                raw_settings.get("min_confidence"),
                0.5,
                minimum=0.0,
                maximum=1.0,
            ),
            fallback_profile=_normalize_choice(
                raw_settings.get("fallback_profile"),
                {"minimal", "balanced", "full"},
                "balanced",
            ),  # type: ignore[arg-type]
            recent_history_turns=max(
                1,
                _coerce_int(raw_settings.get("recent_history_turns"), 2),
            ),
            use_rules_first=bool(raw_settings.get("use_rules_first", True)),
        )


class PassthroughPromptSelector(PromptSelectorInterface):
    """Return the collected context pack unchanged."""

    def select(
        self,
        pack: ContextPack,
        *,
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config=None,
        provider_request: ProviderRequest | None = None,
    ) -> ContextPack:
        return pack


class RuleBasedPromptSelector(PromptSelectorInterface):
    """Select prompt slots using deterministic request heuristics."""

    def __init__(self, settings: PromptSelectorSettings | None = None) -> None:
        self.settings = settings or PromptSelectorSettings()

    def select(
        self,
        pack: ContextPack,
        *,
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config=None,
        provider_request: ProviderRequest | None = None,
    ) -> ContextPack:
        del plugin_context
        decision = self.decide(
            pack,
            event=event,
            config=config,
            provider_request=provider_request,
        )
        return apply_prompt_selection(
            pack,
            decision,
            recent_history_turns=self.settings.recent_history_turns,
        )

    def decide(
        self,
        pack: ContextPack,
        *,
        event: AstrMessageEvent | None = None,
        config=None,
        provider_request: ProviderRequest | None = None,
    ) -> PromptSelectionDecision:
        del config
        text = _resolve_current_text(event=event, provider_request=provider_request)
        if not text:
            text = _resolve_pack_input_text(pack)
        lowered = text.lower()

        has_media_or_files = any(
            pack.has_slot(slot_name)
            for slot_name in (
                "input.images",
                "input.quoted_images",
                "input.files",
                "input.file_extracts",
            )
        )
        has_quote = pack.has_slot("input.quoted_text") or pack.has_slot(
            "input.quoted_images"
        )
        wants_tools = _contains_any(lowered, _TOOL_KEYWORDS)
        wants_subagent = _contains_any(lowered, _SUBAGENT_KEYWORDS)
        wants_history = has_quote or _contains_any(lowered, _HISTORY_KEYWORDS)
        wants_memory = _contains_any(lowered, _MEMORY_KEYWORDS)
        wants_knowledge = _contains_any(lowered, _KNOWLEDGE_KEYWORDS)

        if wants_subagent:
            return PromptSelectionDecision(
                profile="full",
                tools=True,
                subagent=True,
                history="detailed" if wants_history else "recent",
                memory="full" if wants_memory else "light",
                knowledge=True,
                confidence=0.88,
                reason="subagent signal",
                source="rules",
            )

        if wants_tools:
            return PromptSelectionDecision(
                profile="balanced",
                tools=True,
                subagent=False,
                history="recent" if wants_history else "none",
                memory="light" if wants_memory else "none",
                knowledge=wants_knowledge,
                confidence=0.84,
                reason="tool signal",
                source="rules",
            )

        if wants_knowledge:
            return PromptSelectionDecision(
                profile="balanced",
                tools=False,
                subagent=False,
                history="recent" if wants_history else "none",
                memory="light" if wants_memory else "none",
                knowledge=True,
                confidence=0.82,
                reason="knowledge signal",
                source="rules",
            )

        if wants_memory:
            return PromptSelectionDecision(
                profile="balanced",
                tools=False,
                subagent=False,
                history="recent" if wants_history else "none",
                memory="full",
                knowledge=False,
                confidence=0.82,
                reason="memory signal",
                source="rules",
            )

        if wants_history or has_media_or_files:
            return PromptSelectionDecision(
                profile="balanced",
                tools=False,
                subagent=False,
                history="recent",
                memory="light" if wants_history else "none",
                knowledge=False,
                confidence=0.78,
                reason="history or attachment signal",
                source="rules",
            )

        if _is_casual_text(lowered):
            return PromptSelectionDecision(
                profile="minimal",
                tools=False,
                subagent=False,
                history="none",
                memory="none",
                knowledge=False,
                confidence=0.9,
                reason="casual short input",
                source="rules",
            )

        return _fallback_decision(self.settings.fallback_profile, source="rules")


class LLMPromptContextSelector(RuleBasedPromptSelector):
    """Use a small configured chat provider to classify context needs."""

    async def select_async(
        self,
        pack: ContextPack,
        *,
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config=None,
        provider_request: ProviderRequest | None = None,
    ) -> ContextPack:
        rules_decision = self.decide(
            pack,
            event=event,
            config=config,
            provider_request=provider_request,
        )
        if self.settings.use_rules_first and rules_decision.confidence >= 0.88:
            decision = rules_decision
        else:
            decision = await self._select_with_provider(
                pack,
                rules_decision=rules_decision,
                event=event,
                plugin_context=plugin_context,
                provider_request=provider_request,
            )

        selected = apply_prompt_selection(
            pack,
            decision,
            recent_history_turns=self.settings.recent_history_turns,
        )
        if event is not None:
            event.set_extra(PROMPT_SELECTION_DECISION_EXTRA_KEY, decision.to_dict())
        return selected

    async def _select_with_provider(
        self,
        pack: ContextPack,
        *,
        rules_decision: PromptSelectionDecision,
        event: AstrMessageEvent | None,
        plugin_context: Context | None,
        provider_request: ProviderRequest | None,
    ) -> PromptSelectionDecision:
        provider = self._resolve_provider(plugin_context)
        if provider is None:
            return rules_decision

        prompt = _build_selector_prompt(
            pack,
            event=event,
            provider_request=provider_request,
            rules_decision=rules_decision,
        )
        try:
            response = await asyncio.wait_for(
                provider.text_chat(
                    prompt=prompt,
                    system_prompt=_SELECTOR_SYSTEM_PROMPT,
                    model=self.settings.model or None,
                ),
                timeout=self.settings.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Prompt selector provider call failed: provider_id=%s error=%s",
                self.settings.provider_id,
                exc,
                exc_info=True,
            )
            return rules_decision

        payload = _extract_json_object(response.completion_text)
        if payload is None:
            logger.warning(
                "Prompt selector returned non-JSON output: %s",
                _preview_text(response.completion_text),
            )
            return rules_decision

        decision = PromptSelectionDecision.from_mapping(
            payload,
            fallback=rules_decision,
            source="llm",
        )
        decision = _merge_rule_escalations(decision, rules_decision)
        if decision.confidence < self.settings.min_confidence:
            return _fallback_decision(self.settings.fallback_profile, source="fallback")
        return decision

    def _resolve_provider(self, plugin_context: Context | None) -> Provider | None:
        if plugin_context is None:
            return None
        provider = plugin_context.get_provider_by_id(self.settings.provider_id)
        if isinstance(provider, Provider):
            return provider
        logger.warning(
            "Prompt selector provider is unavailable or not a chat provider: %s",
            self.settings.provider_id,
        )
        return None


def build_prompt_selector(config: object | None = None) -> PromptSelectorInterface:
    """Build the configured prompt selector."""
    settings = PromptSelectorSettings.from_config(config)
    if not settings.enabled:
        return PassthroughPromptSelector()
    return LLMPromptContextSelector(settings)


def apply_prompt_selection(
    pack: ContextPack,
    decision: PromptSelectionDecision,
    *,
    recent_history_turns: int = 2,
) -> ContextPack:
    """Apply a selector decision to a context pack without changing source content."""
    selected = ContextPack(
        provider_request_ref=pack.provider_request_ref,
        meta={
            **pack.meta,
            "selection": decision.to_dict(),
            "pre_selection_slot_count": len(pack.slots),
        },
    )

    dropped_slots: list[str] = []
    for slot_name, slot in pack.slots.items():
        selected_slot = _select_slot(
            slot,
            decision=decision,
            recent_history_turns=recent_history_turns,
        )
        if selected_slot is None:
            dropped_slots.append(slot_name)
            continue
        selected.add_slot(selected_slot)

    selected.meta["slot_count"] = len(selected.slots)
    selected.meta["dropped_slot_names"] = sorted(dropped_slots)
    return selected


def select_context_pack(
    pack: ContextPack,
    *,
    selector: PromptSelectorInterface | None = None,
    event: AstrMessageEvent | None = None,
    plugin_context: Context | None = None,
    config=None,
    provider_request: ProviderRequest | None = None,
) -> ContextPack:
    """Run prompt selection with a default passthrough selector."""
    active_selector = selector or PassthroughPromptSelector()
    return active_selector.select(
        pack,
        event=event,
        plugin_context=plugin_context,
        config=config,
        provider_request=provider_request,
    )


async def select_context_pack_async(
    pack: ContextPack,
    *,
    selector: PromptSelectorInterface | None = None,
    event: AstrMessageEvent | None = None,
    plugin_context: Context | None = None,
    config=None,
    provider_request: ProviderRequest | None = None,
) -> ContextPack:
    """Run prompt selection with async support for provider-backed selectors."""
    active_selector = selector or PassthroughPromptSelector()
    return await active_selector.select_async(
        pack,
        event=event,
        plugin_context=plugin_context,
        config=config,
        provider_request=provider_request,
    )


_SELECTOR_SYSTEM_PROMPT = """You are a context selection classifier.
Return one compact JSON object only. Do not answer the user.
Fields:
- profile: minimal | balanced | full
- tools: boolean
- subagent: boolean
- history: none | recent | detailed
- memory: none | light | full
- knowledge: boolean
- confidence: number from 0 to 1
- reason: short English phrase
Choose what context and capability information should be exposed to the main model."""


def _select_slot(
    slot: ContextSlot,
    *,
    decision: PromptSelectionDecision,
    recent_history_turns: int,
) -> ContextSlot | None:
    slot_name = slot.name
    group = slot_name.split(".", 1)[0]

    if group in {"system", "persona", "policy", "input", "session"}:
        return slot

    if group == "conversation":
        if decision.history == "none":
            return None
        if decision.history == "recent":
            return _truncate_history_slot(slot, recent_history_turns)
        return slot

    if group == "knowledge":
        return slot if decision.knowledge else None

    if group == "memory":
        if decision.memory == "none":
            return None
        if decision.memory == "light" and slot_name not in {
            "memory.topic_state",
            "memory.short_term",
            "memory.persona_state",
        }:
            return None
        return slot

    if group == "capability":
        if slot_name.startswith("capability.subagent_"):
            return slot if decision.subagent else None
        if slot_name in {"capability.tools_schema", "capability.skills_prompt"}:
            return slot if decision.tools else None
        return slot if decision.tools or decision.subagent else None

    if group == "extension":
        return _select_extension_slot(slot, decision)

    return slot


def _select_extension_slot(
    slot: ContextSlot,
    decision: PromptSelectionDecision,
) -> ContextSlot | None:
    if slot.name in {"extension.system", "extension.context", "extension.input"}:
        return slot
    if slot.name == "extension.conversation":
        return slot if decision.history != "none" else None
    if slot.name == "extension.memory":
        return slot if decision.memory != "none" else None
    if slot.name == "extension.capability":
        return slot if decision.tools or decision.subagent else None
    return slot


def _truncate_history_slot(slot: ContextSlot, turn_count: int) -> ContextSlot:
    if not isinstance(slot.value, dict):
        return slot
    turns = slot.value.get("turns")
    if not isinstance(turns, list) or len(turns) <= turn_count:
        return slot

    value = deepcopy(slot.value)
    value["turns"] = turns[-turn_count:]
    value["turn_count"] = len(value["turns"])
    meta = dict(slot.meta)
    meta["turn_count"] = value["turn_count"]
    meta["selection_truncated"] = True
    meta["pre_selection_turn_count"] = len(turns)
    return replace(slot, value=value, meta=meta)


def _build_selector_prompt(
    pack: ContextPack,
    *,
    event: AstrMessageEvent | None,
    provider_request: ProviderRequest | None,
    rules_decision: PromptSelectionDecision,
) -> str:
    summary = {
        "current_input": _preview_text(
            _resolve_current_text(event=event, provider_request=provider_request)
            or _resolve_pack_input_text(pack),
            limit=500,
        ),
        "has_images": pack.has_slot("input.images")
        or pack.has_slot("input.quoted_images"),
        "has_files": pack.has_slot("input.files")
        or pack.has_slot("input.file_extracts"),
        "has_quoted_message": pack.has_slot("input.quoted_text")
        or pack.has_slot("input.quoted_images"),
        "available": {
            "history": pack.has_slot("conversation.history"),
            "memory": bool([name for name in pack.slots if name.startswith("memory.")]),
            "knowledge": pack.has_slot("knowledge.snippets"),
            "tools": pack.has_slot("capability.tools_schema")
            or pack.has_slot("capability.skills_prompt"),
            "subagent": pack.has_slot("capability.subagent_handoff_tools")
            or pack.has_slot("capability.subagent_router_prompt"),
        },
        "slot_names": sorted(pack.slots),
        "rules_guess": rules_decision.to_dict(),
    }
    return json.dumps(summary, ensure_ascii=False, default=str)


def _resolve_current_text(
    *,
    event: AstrMessageEvent | None,
    provider_request: ProviderRequest | None,
) -> str:
    if provider_request is not None and isinstance(provider_request.prompt, str):
        prompt = provider_request.prompt.strip()
        if prompt:
            return prompt
    if event is not None and isinstance(getattr(event, "message_str", None), str):
        return event.message_str.strip()
    return ""


def _resolve_pack_input_text(pack: ContextPack) -> str:
    slot = pack.get_slot("input.text")
    if slot is None or not isinstance(slot.value, str):
        return ""
    return slot.value.strip()


def _fallback_decision(
    profile: PromptContextProfile,
    *,
    source: str,
) -> PromptSelectionDecision:
    if profile == "minimal":
        return PromptSelectionDecision(
            profile="minimal",
            tools=False,
            subagent=False,
            history="none",
            memory="none",
            knowledge=False,
            confidence=0.6,
            reason="minimal fallback",
            source=source,
        )
    if profile == "full":
        return PromptSelectionDecision(
            profile="full",
            tools=True,
            subagent=True,
            history="detailed",
            memory="full",
            knowledge=True,
            confidence=0.6,
            reason="full fallback",
            source=source,
        )
    return PromptSelectionDecision(
        profile="balanced",
        tools=False,
        subagent=False,
        history="recent",
        memory="light",
        knowledge=False,
        confidence=0.6,
        reason="balanced fallback",
        source=source,
    )


def _merge_rule_escalations(
    decision: PromptSelectionDecision,
    rules_decision: PromptSelectionDecision,
) -> PromptSelectionDecision:
    history_rank = {"none": 0, "recent": 1, "detailed": 2}
    memory_rank = {"none": 0, "light": 1, "full": 2}
    if history_rank[rules_decision.history] > history_rank[decision.history]:
        decision.history = rules_decision.history
    if memory_rank[rules_decision.memory] > memory_rank[decision.memory]:
        decision.memory = rules_decision.memory
    decision.tools = decision.tools or rules_decision.tools
    decision.subagent = decision.subagent or rules_decision.subagent
    decision.knowledge = decision.knowledge or rules_decision.knowledge
    return decision


def _extract_json_object(text: object) -> dict[str, Any] | None:
    if not isinstance(text, str):
        return None
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    try:
        payload = json.loads(cleaned)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_choice(value: object, allowed: set[str], default: str) -> str:
    if isinstance(value, bool):
        if not value and "none" in allowed:
            return "none"
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in allowed:
            return normalized
        if normalized in {"true", "yes", "enabled", "retrieve"}:
            return "auto" if "auto" in allowed else default
        if normalized in {"false", "no", "disabled"} and "none" in allowed:
            return "none"
    return default


def _normalize_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "enabled", "auto", "retrieve", "full"}:
            return True
        if normalized in {"false", "no", "disabled", "none"}:
            return False
    return default


def _normalize_confidence(value: object, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return min(max(float(value), 0.0), 1.0)
    return default


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle.lower() in text for needle in needles)


def _is_casual_text(text: str) -> bool:
    compact = text.strip().lower()
    if not compact:
        return False
    if compact in _CASUAL_PATTERNS:
        return True
    return len(compact) <= 12 and any(item in compact for item in _CASUAL_PATTERNS)


def _preview_text(value: object, *, limit: int = 240) -> str:
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _coerce_float(
    value: object,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(result, minimum)
    if maximum is not None:
        result = min(result, maximum)
    return result


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
