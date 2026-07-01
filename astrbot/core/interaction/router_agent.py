from __future__ import annotations

import asyncio
from typing import Any

from astrbot import logger
from astrbot.core.prompt.context_types import ContextSlot
from astrbot.core.prompt.render import PromptRenderEngine
from astrbot.core.prompt.render.selector import _extract_json_object
from astrbot.core.provider import Provider
from astrbot.core.star.context import Context

from .context_builder import (
    InteractionPromptContributorError,
    append_interaction_prompt_extensions_to_pack,
    build_prompt_render_provider_request,
    build_router_context_pack,
    clone_interaction_context_pack,
    collect_interaction_prompt_extensions,
    extract_input_payload,
)
from .decision_agent import (
    _build_decision_build_config,
    _maybe_bypass_protocol_command,
    build_interaction_decision_contexts,
)
from .memory_store import InteractionMemoryStore
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
        "你是 Interaction Router，一个严格的二分类选择器。\n"
        "任务：根据当前用户输入、聊天记录、memory 和 router 上下文，从候选标签中选择一个。\n"
        "候选标签：\n"
        "- self_reply：拟人层或上下文声明的本地能力即可完整处理，不需要核心 Agent。\n"
        "- hybrid：需要核心 Agent 执行工具、检索、文件、代码、事实核验、复杂推理，"
        "或本地/拟人层能力无法确认覆盖。\n"
        "判断规则：只按上下文提供的能力描述判断，不推断具体插件协议。\n"
        "输出约束：不要生成用户回复，不要输出 JSON，只返回 self_reply 或 hybrid。"
    )


def build_interaction_router_prompt() -> str:
    return "请只输出 self_reply 或 hybrid。"


def extract_interaction_route_payload(
    text: object,
) -> dict[str, Any] | None:
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
        # Router 不需要锁：它构建自己的独立最小 Pack，不写入共享 context_material
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
                ),
                timeout=interaction_config.router_timeout,
            )
        except asyncio.TimeoutError:
            raise InteractionRouterError("timeout") from None
        except Exception as exc:  # noqa: BLE001
            raise InteractionRouterError("model_error", str(exc)) from exc

        payload = extract_interaction_route_payload(
            llm_resp.completion_text,
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
        # Router 直接构建最小 Pack，不触碰共享 context_material
        router_pack = await build_router_context_pack(
            event,
            plugin_context,
            build_config,
            self.memory_store,
        )
        # Router prompt extensions（purpose="router"），不缓存
        input_payload = extract_input_payload(router_pack)
        decision_context = {"input": input_payload}
        try:
            prompt_extensions = await collect_interaction_prompt_extensions(
                event,
                plugin_context,
                build_config,
                decision_context,
                purpose="router",
                phase="route",
            )
        except InteractionPromptContributorError as exc:
            raise InteractionRouterError(exc.reason, str(exc)) from exc
        route_pack = clone_interaction_context_pack(router_pack)
        append_interaction_prompt_extensions_to_pack(route_pack, prompt_extensions)
        add_interaction_router_slots_to_pack(
            pack=route_pack,
        )
        return PromptRenderEngine().render(
            route_pack,
            event=event,
            plugin_context=plugin_context,
            config=build_config,
            provider_request=build_prompt_render_provider_request(event, provider),
        )



def add_interaction_router_slots_to_pack(
    *,
    pack,
) -> None:
    pack.add_slot(
        ContextSlot(
            name="system.base",
            value=build_interaction_router_system_prompt(),
            category="system",
            source="interaction_router",
            render_mode="text",
            meta={
                "scope": "static",
                "node_type": "interaction_router_system_prompt",
            },
        )
    )
    pack.meta["slot_count"] = len(pack.slots)
