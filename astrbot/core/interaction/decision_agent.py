from __future__ import annotations

import asyncio
import json
from typing import Any

from astrbot import logger
from astrbot.core.prompt.render.selector import _extract_json_object
from astrbot.core.provider import Provider
from astrbot.core.star.context import Context

from .context_builder import (
    build_core_capability_payload,
    build_interaction_context_pack,
    collect_interaction_prompt_contributions,
    extract_input_payload,
    extract_interaction_memory_payload,
    extract_persona_payload,
    extract_recent_messages,
)
from .memory_store import InteractionMemoryStore
from .turn_state import (
    InteractionContextMaterial,
    get_interaction_turn_state,
    set_interaction_turn_persona_id,
)
from .types import (
    FallbackPolicy,
    InteractionAgentConfig,
    InteractionDecision,
    InteractionPromptBuildConfig,
    RouteMode,
)


class InteractionDecisionError(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def build_interaction_agent_system_prompt() -> str:
    return (
        "你是 AstrBot 的 interaction persona middleware。\n"
        "你的职责是先以拟人化、口语化的方式理解用户，再决定这条消息是：\n"
        "1. 你自己直接回复；\n"
        "2. 交给核心执行层处理；\n"
        "3. 先短回复一句，再交给核心执行层处理。\n\n"
        "你不是工具执行层。凡是明显需要搜索、知识库、工具、技能、MCP、subagent、文件处理、外部行动的请求，必须交给核心执行层。\n"
        "如果这类执行请求适合先回应用户一声，请选择 hybrid，并给出一句短的 immediate_spoken_reply。\n"
        "只有当不应该先说话、或者这是一条硬控制/静默委托请求时，才选择 delegate_to_core 且不发 immediate_spoken_reply。\n"
        "普通寒暄、情绪回应、轻量对话，优先选择 self_reply。\n"
        "你的 immediate_spoken_reply 必须是自然、简短、口语化的中文，不要把它写成最终答案，也不要讲一长串流程说明。\n"
        "执行类请求的 immediate_spoken_reply 只能表达“我知道了/我来看看/等我一下”，不能说已经完成，不能汇报工具步骤。\n"
        "你必须严格输出 JSON，不要输出 JSON 之外的任何文字。"
    )


def build_interaction_decision_schema() -> dict[str, Any]:
    return {
        "route_mode": "self_reply | delegate_to_core | hybrid",
        "should_emit_immediate_reply": True,
        "immediate_spoken_reply": "短句口语中文",
        "core_task_spec": {
            "task_intent": "任务意图",
            "task_summary": "任务摘要",
            "execution_prompt": "给核心的执行提示",
            "suggested_capabilities": ["search", "knowledge_base", "tools"],
            "metadata": {},
        },
        "plugin_hints": {},
        "confidence": 0.0,
        "reason": "简短原因",
    }


def build_interaction_decision_prompt(
    *,
    event,
    persona_payload: dict[str, Any],
    memory_payload: dict[str, Any],
    recent_messages: list[dict[str, Any]],
    input_payload: dict[str, Any],
    capability_payload: dict[str, Any],
    plugin_contributions: list[dict[str, Any]],
) -> str:
    schema = build_interaction_decision_schema()
    payload = {
        "platform_id": event.get_platform_id(),
        "session_id": event.unified_msg_origin,
        "persona": persona_payload,
        "interaction_memory": memory_payload,
        "recent_messages": recent_messages,
        "current_input": input_payload,
        "core_capabilities": capability_payload,
        "plugin_prompt_contributions": plugin_contributions,
        "output_schema": schema,
    }
    return (
        f"{build_interaction_agent_system_prompt()}\n\n"
        "请根据下面的上下文做一次完整决策，并只返回 JSON。\n"
        "约束：\n"
        "- immediate_spoken_reply 必须简短、自然、口语化，适合先接一句话。\n"
        "- 如果用户请求明显需要执行能力，不要假装已经完成，应该选择 hybrid 或 delegate_to_core。\n"
        "- 对搜索、文件、目录、工具、知识库、MCP、skill、subagent、外部行动等请求，默认选择 hybrid：先短短接一句，再委托核心。\n"
        "- 只有当即时回复会干扰协议、控制命令、或用户明确要求静默处理时，才选择 delegate_to_core。\n"
        "- 如果是纯寒暄、情绪回应、轻量聊天，选择 self_reply。\n"
        "- 如果你不确定，但可以自然接一句，route_mode 请选择 hybrid；如果完全不确定是否该说话，才选择 delegate_to_core。\n"
        "- confidence 用 0 到 1 的浮点数。\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def build_fallback_decision(reason: str) -> InteractionDecision:
    return InteractionDecision(
        route_mode=RouteMode.DELEGATE_TO_CORE,
        should_emit_immediate_reply=False,
        immediate_spoken_reply=None,
        core_task_spec=None,
        plugin_hints={},
        confidence=0.0,
        reason=reason,
        is_fallback=True,
        fallback_reason=reason,
    )


def build_protocol_bypass_decision(reason: str) -> InteractionDecision:
    return InteractionDecision(
        route_mode=RouteMode.DELEGATE_TO_CORE,
        should_emit_immediate_reply=False,
        immediate_spoken_reply=None,
        core_task_spec=None,
        plugin_hints={},
        confidence=1.0,
        reason=reason,
        is_fallback=False,
        fallback_reason=None,
    )


def validate_interaction_decision(
    decision: InteractionDecision,
    config: InteractionAgentConfig,
) -> InteractionDecision:
    if decision.immediate_spoken_reply:
        reply = decision.immediate_spoken_reply.strip()
        if len(reply) > 60:
            reply = reply[:60].rstrip("，,。.!！?？")
        decision.immediate_spoken_reply = reply
    if decision.route_mode == RouteMode.SELF_REPLY:
        decision.should_emit_immediate_reply = bool(decision.immediate_spoken_reply)
    if decision.route_mode == RouteMode.HYBRID and not decision.immediate_spoken_reply:
        logger.info(
            "Interaction decision downgraded: reason=hybrid_without_immediate_reply",
        )
        decision.should_emit_immediate_reply = False
        decision.route_mode = RouteMode.DELEGATE_TO_CORE
    if decision.confidence < config.decision_confidence_threshold:
        message = (
            f"low confidence: confidence={decision.confidence} "
            f"threshold={config.decision_confidence_threshold} "
            f"route_mode={decision.route_mode.value}"
        )
        if config.fallback_policy == FallbackPolicy.OBSERVABLE_PROTECT:
            logger.warning("Interaction decision fallback: reason=%s", message)
            return build_fallback_decision("low confidence")
        raise InteractionDecisionError("low_confidence", message)
    return decision


async def call_decision_model(
    plugin_context: Context,
    *,
    provider: Provider,
    provider_id: str,
    model: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
):
    logger.debug(
        "Interaction decision model request: provider_id=%s model=%s timeout=%s",
        provider_id,
        model or provider.get_model(),
        timeout,
    )
    return await asyncio.wait_for(
        provider.text_chat(
            prompt=prompt,
            system_prompt="",
            model=model or None,
            temperature=temperature,
            max_tokens=max_tokens,
        ),
        timeout=timeout,
    )


def _build_decision_build_config(
    plugin_context: Context,
    event,
) -> InteractionPromptBuildConfig:
    cfg = plugin_context.get_config(umo=event.unified_msg_origin)
    provider_settings = (
        cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
    )
    provider_wake_prefix = ""
    if isinstance(cfg, dict):
        wake_prefix = cfg.get("wake_prefix", "")
        if isinstance(wake_prefix, str):
            provider_wake_prefix = wake_prefix
        elif isinstance(wake_prefix, list):
            provider_wake_prefix = next(
                (str(item) for item in wake_prefix if isinstance(item, str) and item),
                "",
            )
    return InteractionPromptBuildConfig(
        provider_settings=provider_settings,
        timezone=(cfg.get("timezone") if isinstance(cfg, dict) else None),
        provider_wake_prefix=provider_wake_prefix,
        file_extract_enabled=bool(
            cfg.get("file_extract_enabled", False) if isinstance(cfg, dict) else False
        ),
        file_extract_prov=str(
            cfg.get("file_extract_prov", "moonshotai")
            if isinstance(cfg, dict)
            else "moonshotai"
        ),
        file_extract_msh_api_key=str(
            cfg.get("file_extract_msh_api_key", "") if isinstance(cfg, dict) else ""
        ),
        max_quoted_fallback_images=int(
            provider_settings.get("max_quoted_fallback_images", 20) or 20
        ),
    )


def _extract_configured_wake_prefixes(plugin_context: Context, event) -> list[str]:
    cfg = plugin_context.get_config(umo=event.unified_msg_origin)
    if not isinstance(cfg, dict):
        return []
    wake_prefix = cfg.get("wake_prefix", [])
    if isinstance(wake_prefix, str):
        candidates = [wake_prefix]
    elif isinstance(wake_prefix, list):
        candidates = wake_prefix
    else:
        candidates = []
    return [str(item) for item in candidates if isinstance(item, str) and item]


def _maybe_bypass_protocol_command(
    event,
    plugin_context: Context,
) -> InteractionDecision | None:
    text = (event.message_str or "").strip().lower()
    wake_prefixes = _extract_configured_wake_prefixes(plugin_context, event)
    matched_prefix = next(
        (
            prefix
            for prefix in sorted(wake_prefixes, key=len, reverse=True)
            if text.startswith(prefix.lower()) and len(text) > len(prefix)
        ),
        None,
    )
    if matched_prefix is not None:
        logger.info(
            "Interaction decision bypassed for configured command prefix: platform_id=%s session_id=%s prefix=%s command=%s",
            event.get_platform_id(),
            event.session_id,
            matched_prefix,
            text,
        )
        return build_protocol_bypass_decision("protocol command bypass")
    return None


class InteractionDecisionAgent:
    def __init__(self, memory_store: InteractionMemoryStore) -> None:
        self.memory_store = memory_store

    async def decide(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
    ) -> InteractionDecision:
        bypass = _maybe_bypass_protocol_command(event, plugin_context)
        if bypass is not None:
            return bypass

        provider = plugin_context.get_provider_by_id(
            interaction_config.decision_provider_id
        )
        if not isinstance(provider, Provider):
            message = f"provider unavailable: provider_id={interaction_config.decision_provider_id}"
            if interaction_config.fallback_policy == FallbackPolicy.OBSERVABLE_PROTECT:
                logger.warning("Interaction decision fallback: reason=%s", message)
                return build_fallback_decision("provider unavailable")
            raise InteractionDecisionError("provider_unavailable", message)

        build_config = _build_decision_build_config(plugin_context, event)
        material = await self._build_or_reuse_context_material(
            event=event,
            plugin_context=plugin_context,
            interaction_config=interaction_config,
            build_config=build_config,
        )
        persona_payload = material.persona_payload
        set_interaction_turn_persona_id(event, persona_payload.get("persona_id", ""))
        memory_payload = material.memory_payload
        recent_messages = material.recent_messages
        input_payload = material.input_payload
        capability_payload = material.capability_payload
        decision_context = material.decision_context
        prompt_contributions = await collect_interaction_prompt_contributions(
            event,
            plugin_context,
            build_config,
            decision_context,
        )
        logger.debug(
            "Interaction decision context built: platform_id=%s session_id=%s persona_keys=%s memory_keys=%s recent_messages=%s tools_available=%s tool_count=%s prompt_contributors=%s",
            event.get_platform_id(),
            event.session_id,
            sorted(persona_payload.keys()),
            sorted(memory_payload.keys()),
            len(recent_messages),
            capability_payload.get("tools_available"),
            capability_payload.get("tool_count"),
            len(prompt_contributions),
        )
        prompt = build_interaction_decision_prompt(
            event=event,
            persona_payload=persona_payload,
            memory_payload=memory_payload,
            recent_messages=recent_messages,
            input_payload=input_payload,
            capability_payload=capability_payload,
            plugin_contributions=[
                {
                    "plugin_id": item.plugin_id,
                    "title": item.title,
                    "content": item.content,
                }
                for item in prompt_contributions
            ],
        )
        try:
            llm_resp = await call_decision_model(
                plugin_context,
                provider=provider,
                provider_id=interaction_config.decision_provider_id,
                model=interaction_config.decision_model,
                prompt=prompt,
                temperature=interaction_config.decision_temperature,
                max_tokens=interaction_config.decision_max_tokens,
                timeout=interaction_config.decision_timeout,
            )
        except asyncio.TimeoutError:
            if interaction_config.fallback_policy == FallbackPolicy.OBSERVABLE_PROTECT:
                logger.warning("Interaction decision fallback: reason=timeout")
                return build_fallback_decision("timeout")
            raise InteractionDecisionError("timeout") from None
        except Exception as exc:  # noqa: BLE001
            if interaction_config.fallback_policy == FallbackPolicy.OBSERVABLE_PROTECT:
                logger.warning(
                    "Interaction decision fallback: reason=model_error error=%s",
                    exc,
                    exc_info=True,
                )
                return build_fallback_decision("model error")
            raise InteractionDecisionError("model_error", str(exc)) from exc

        payload = _extract_json_object(llm_resp.completion_text)
        if payload is None:
            message = f"non-json: raw={llm_resp.completion_text}"
            if interaction_config.fallback_policy == FallbackPolicy.OBSERVABLE_PROTECT:
                logger.warning("Interaction decision fallback: reason=%s", message)
                return build_fallback_decision("non-json")
            raise InteractionDecisionError("non_json", message)
        decision = InteractionDecision.from_mapping(payload)
        if decision is None:
            if interaction_config.fallback_policy == FallbackPolicy.OBSERVABLE_PROTECT:
                logger.warning("Interaction decision fallback: reason=invalid_payload")
                return build_fallback_decision("invalid payload")
            raise InteractionDecisionError("invalid_payload")
        if not decision.reason:
            decision.reason = "llm decision"
        decision = validate_interaction_decision(decision, interaction_config)
        logger.info(
            "Interaction decision parsed: platform_id=%s session_id=%s route_mode=%s emit_immediate=%s confidence=%s reason=%s has_core_task_spec=%s",
            event.get_platform_id(),
            event.session_id,
            decision.route_mode.value,
            decision.should_emit_immediate_reply,
            decision.confidence,
            decision.reason,
            decision.core_task_spec is not None,
        )
        turn_state = get_interaction_turn_state(event)
        if turn_state is not None:
            turn_state.decision = decision
        return decision

    async def _build_or_reuse_context_material(
        self,
        *,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        build_config: InteractionPromptBuildConfig,
    ) -> InteractionContextMaterial:
        turn_state = get_interaction_turn_state(event)
        if turn_state is not None:
            turn_state.prompt_build_config = build_config
            cached_material = turn_state.context_material
            if cached_material is not None:
                cached_recent_messages = cached_material.recent_messages
                desired_window = interaction_config.memory_window_size
                if desired_window > 0:
                    cached_recent_messages = cached_recent_messages[-desired_window:]
                cached_material.recent_messages = cached_recent_messages
                cached_material.decision_context = {
                    "persona": cached_material.persona_payload,
                    "memory": cached_material.memory_payload,
                    "recent_messages": cached_recent_messages,
                    "input": cached_material.input_payload,
                    "core_capabilities": cached_material.capability_payload,
                }
                event.set_extra(
                    "_interaction_prompt_context_pack",
                    cached_material.prompt_context_pack,
                )
                event.set_extra(
                    "_interaction_decision_context",
                    cached_material.decision_context,
                )
                return cached_material

        prompt_context_pack = await build_interaction_context_pack(
            event,
            plugin_context,
            build_config,
            self.memory_store,
        )
        persona_payload = extract_persona_payload(prompt_context_pack)
        memory_payload = extract_interaction_memory_payload(prompt_context_pack)
        recent_messages = extract_recent_messages(
            prompt_context_pack,
            interaction_config.memory_window_size,
        )
        input_payload = extract_input_payload(prompt_context_pack)
        capability_payload = build_core_capability_payload(plugin_context, event)
        material = InteractionContextMaterial(
            prompt_context_pack=prompt_context_pack,
            persona_payload=persona_payload,
            memory_payload=memory_payload,
            recent_messages=recent_messages,
            input_payload=input_payload,
            capability_payload=capability_payload,
            decision_context={
                "persona": persona_payload,
                "memory": memory_payload,
                "recent_messages": recent_messages,
                "input": input_payload,
                "core_capabilities": capability_payload,
            },
        )
        event.set_extra("_interaction_prompt_context_pack", prompt_context_pack)
        event.set_extra("_interaction_decision_context", material.decision_context)
        if turn_state is not None:
            turn_state.context_material = material
        return material
