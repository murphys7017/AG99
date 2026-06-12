from __future__ import annotations

import asyncio
import json
from typing import Any

from astrbot import logger
from astrbot.core.output_contract import OutputContract
from astrbot.core.prompt.context_collect import build_prompt_extension_slots
from astrbot.core.prompt.extensions import PromptExtension
from astrbot.core.prompt.render import PromptRenderEngine
from astrbot.core.prompt.render.selector import _extract_json_object
from astrbot.core.provider import Provider
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.star.context import Context

from .context_builder import (
    InteractionPromptContributorError,
    append_interaction_prompt_extensions_to_pack,
    clone_interaction_context_pack,
    collect_interaction_prompt_extensions,
    temporary_event_extra,
)
from .decision_agent import (
    _build_decision_build_config,
    _maybe_bypass_protocol_command,
    build_interaction_decision_contexts,
    _should_require_tool_choice,
)
from .memory_store import InteractionMemoryStore
from .turn_state import get_interaction_turn_state, set_interaction_turn_persona_id
from .types import (
    FastRouteMode,
    InteractionAgentConfig,
    InteractionRouteDecision,
)


class InteractionRouterError(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def build_interaction_router_system_prompt() -> str:
    return (
        "你是 AstrBot interaction middleware 的 Router。\n"
        "你只判断当前用户输入是否需要核心执行层继续处理。\n"
        "不要写回复，不要解释原因，不要拆解任务，不要输出置信度。\n\n"
        "输出 self_reply：普通寒暄、情绪回应、轻量角色互动、无需事实核验的闲聊。\n"
        "输出 hybrid：需要工具、检索、文件、代码、项目检查、外部动作、长推理、多步骤分析、准确事实核验，或你不确定时。\n"
        "只输出符合当前结构化约束的 JSON。"
    )


def build_interaction_router_tool_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": [
                    FastRouteMode.SELF_REPLY.value,
                    FastRouteMode.HYBRID.value,
                ],
            },
        },
        "required": ["mode"],
    }


def build_interaction_router_output_contract() -> OutputContract:
    return OutputContract(
        mode="tool_call",
        strict=True,
        schema=build_interaction_router_tool_parameters(),
        preferred_tool_name="interaction_route",
        allow_text_fallback=True,
    )


def build_interaction_router_prompt() -> str:
    return "请只判断当前输入应为 self_reply 还是 hybrid。"


def extract_interaction_route_payload(
    text: object,
    *,
    llm_response: LLMResponse | None = None,
    output_contract: OutputContract | None = None,
) -> dict[str, Any] | None:
    if llm_response is not None:
        preferred = (
            output_contract.preferred_tool_name
            if isinstance(output_contract, OutputContract)
            else None
        )
        for tool_name, tool_arg in zip(
            list(getattr(llm_response, "tools_call_name", []) or []),
            list(getattr(llm_response, "tools_call_args", []) or []),
            strict=False,
        ):
            if preferred and tool_name != preferred:
                continue
            if isinstance(tool_arg, dict):
                return tool_arg
    payload = _extract_json_object(text)
    if payload is not None:
        return payload
    if not isinstance(text, str):
        return None
    raw = text.strip().strip('"').strip("'").lower()
    if raw in {FastRouteMode.SELF_REPLY.value, FastRouteMode.HYBRID.value}:
        return {"mode": raw}
    return None


class InteractionRouterAgent:
    def __init__(self, memory_store: InteractionMemoryStore) -> None:
        self.memory_store = memory_store

    async def route(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
    ) -> InteractionRouteDecision:
        bypass = _maybe_bypass_protocol_command(event, plugin_context)
        if bypass is not None:
            return InteractionRouteDecision(mode=FastRouteMode.HYBRID)

        provider = plugin_context.get_provider_by_id(
            interaction_config.router_provider_id
        )
        if not isinstance(provider, Provider):
            message = (
                f"provider unavailable: provider_id={interaction_config.router_provider_id}"
            )
            raise InteractionRouterError("provider_unavailable", message)
        turn_state = get_interaction_turn_state(event)
        if turn_state is not None:
            async with turn_state.lock:
                render_result = await self._prepare_render_result(
                    event,
                    plugin_context,
                    interaction_config,
                    provider,
                )
        else:
            render_result = await self._prepare_render_result(
                event,
                plugin_context,
                interaction_config,
                provider,
            )
        event.set_extra("_interaction_router_prompt_render_result", render_result)
        try:
            llm_resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=build_interaction_router_prompt(),
                    contexts=build_interaction_decision_contexts(
                        render_result.messages
                    ),
                    system_prompt=render_result.system_prompt or "",
                    temperature=interaction_config.router_temperature,
                    tool_choice="required"
                    if _should_require_tool_choice(render_result.output_contract)
                    else "auto",
                    output_contract=render_result.output_contract,
                    compiled_output_contract=render_result.compiled_output_contract,
                ),
                timeout=interaction_config.router_timeout,
            )
        except asyncio.TimeoutError:
            raise InteractionRouterError("timeout") from None
        except Exception as exc:  # noqa: BLE001
            raise InteractionRouterError("model_error", str(exc)) from exc

        payload = extract_interaction_route_payload(
            llm_resp.completion_text,
            llm_response=llm_resp,
            output_contract=render_result.output_contract,
        )
        route = InteractionRouteDecision.from_mapping(payload)
        if route is None:
            raise InteractionRouterError("invalid_payload")
        logger.info(
            "Interaction router parsed: platform_id=%s session_id=%s mode=%s",
            event.get_platform_id(),
            event.session_id,
            route.mode.value,
        )
        return route

    async def _prepare_render_result(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        provider: Provider,
    ):
        build_config = _build_decision_build_config(plugin_context, event)
        material = await self._build_or_reuse_context_material(
            event=event,
            plugin_context=plugin_context,
            interaction_config=interaction_config,
            build_config=build_config,
        )
        set_interaction_turn_persona_id(
            event,
            material.persona_payload.get("persona_id", ""),
        )
        if not material.prompt_extensions_collected:
            try:
                prompt_extensions = await collect_interaction_prompt_extensions(
                    event,
                    plugin_context,
                    build_config,
                    material.decision_context,
                )
            except InteractionPromptContributorError as exc:
                raise InteractionRouterError(exc.reason, str(exc)) from exc
            append_interaction_prompt_extensions_to_pack(
                material.prompt_context_pack,
                prompt_extensions,
            )
            material.prompt_extensions_collected = True
        route_pack = clone_interaction_context_pack(material.prompt_context_pack)
        add_interaction_router_slots_to_pack(
            pack=route_pack,
            event=event,
            capability_payload=material.capability_payload,
        )
        with temporary_event_extra(event, "provider", provider):
            return PromptRenderEngine().render(
                route_pack,
                event=event,
                plugin_context=plugin_context,
                config=build_config,
                provider_request=event.get_extra("provider_request"),
            )

    async def _build_or_reuse_context_material(
        self,
        *,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        build_config,
    ):
        from .decision_agent import InteractionDecisionAgent

        helper = InteractionDecisionAgent(self.memory_store)
        return await helper._build_or_reuse_context_material(
            event=event,
            plugin_context=plugin_context,
            interaction_config=interaction_config,
            build_config=build_config,
        )


def add_interaction_router_slots_to_pack(
    *,
    pack,
    event,
    capability_payload: dict[str, Any],
) -> None:
    extensions = [
        PromptExtension(
            plugin_id="astrbot.interaction",
            mount="system",
            title="Interaction middleware router policy",
            value_kind="text",
            value=build_interaction_router_system_prompt(),
            order=0,
            meta={
                "scope": "static",
                "node_type": "interaction_router_policy",
            },
        ),
        PromptExtension(
            plugin_id="astrbot.interaction",
            mount="system",
            title="Interaction router output contract",
            value_kind="mapping",
            value=build_interaction_router_output_contract().to_dict(),
            order=1,
            meta={
                "scope": "static",
                "node_type": "interaction_router_output_contract",
            },
        ),
        PromptExtension(
            plugin_id="astrbot.interaction",
            mount="context",
            title="Core capabilities",
            value_kind="mapping",
            value=capability_payload,
            order=0,
            meta={
                "scope": "dynamic",
                "node_type": "interaction_core_capabilities",
            },
        ),
        PromptExtension(
            plugin_id="astrbot.interaction",
            mount="context",
            title="Interaction session",
            value_kind="mapping",
            value={
                "platform_id": event.get_platform_id(),
                "session_id": event.session_id,
                "unified_msg_origin": event.unified_msg_origin,
            },
            order=1,
            meta={
                "scope": "dynamic",
                "node_type": "interaction_session",
            },
        ),
    ]
    for slot in build_prompt_extension_slots(extensions, source="interaction_router"):
        pack.add_slot(slot)
    pack.meta["slot_count"] = len(pack.slots)
    pack.meta["output_contract"] = json.loads(
        json.dumps(build_interaction_router_output_contract().to_dict())
    )
