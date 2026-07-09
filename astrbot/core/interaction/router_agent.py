from __future__ import annotations

import asyncio
from typing import Any

from astrbot import logger
from astrbot.core.prompt.context_types import ContextSlot
from astrbot.core.prompt.extensions import PromptExtension
from astrbot.core.prompt.render import PromptRenderEngine
from astrbot.core.prompt.render.selector import _extract_json_object
from astrbot.core.provider import Provider
from astrbot.core.star.context import Context

from .context_builder import (
    InteractionPromptContributorError,
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
        "任务：从候选标签中选择一个。当前用户输入是首要依据；聊天记录、memory 和 router 上下文只能辅助判断当前消息是否明确延续既有任务。\n"
        "router 上下文可能包含插件目录；插件目录只说明本地插件是什么、负责什么，不能单独成为选择 hybrid 的理由。\n"
        "候选标签：\n"
        "- self_reply：拟人层或插件目录声明的本地插件职责即可完整处理，不需要核心 Agent；普通寒暄、情绪回应、轻量吐槽、短确认、表情或无明确执行意图的短消息也属于拟人层可处理。\n"
        "- hybrid：当前输入明确需要核心 Agent 参与，或聊天记录显示它正在继续一个需要核心 Agent 的任务。\n"
        "判断规则：只有当前消息本身表达明确任务意图，或明确指向未完成的核心任务时才选择 hybrid；含义很弱的短消息默认 self_reply，即使历史或 memory 中出现过任务。不要限制或枚举核心 Agent 的能力范围。\n"
        "不要推断具体插件协议、动作参数或输出 schema。\n"
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

        event.set_extra(
            "_interaction_router_raw_output",
            _truncate_router_diagnostic(llm_resp.completion_text),
        )
        payload = extract_interaction_route_payload(llm_resp.completion_text)
        route = InteractionRouteDecision.from_mapping(payload)
        if route is None:
            raise InteractionRouterError("invalid_payload")
        event.set_extra("_interaction_router_result_source", "parsed")
        logger.info(
            "Interaction router parsed: platform_id=%s session_id=%s mode=%s raw_output=%s",
            event.get_platform_id(),
            event.session_id,
            route.mode.value,
            event.get_extra("_interaction_router_raw_output"),
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
            event.set_extra("_interaction_router_extension_error", exc.reason)
            logger.warning(
                "Interaction router prompt contributors failed; continuing without plugin directory: platform_id=%s session_id=%s reason=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                exc.reason,
                exc,
            )
            prompt_extensions = []
        route_pack = clone_interaction_context_pack(router_pack)
        add_router_plugin_directory_slots_to_pack(route_pack, prompt_extensions)
        add_interaction_router_slots_to_pack(
            pack=route_pack,
        )
        render_result = PromptRenderEngine().render(
            route_pack,
            event=event,
            plugin_context=plugin_context,
            config=build_config,
            provider_request=build_prompt_render_provider_request(event, provider),
        )
        metadata = (
            render_result.metadata
            if isinstance(render_result.metadata, dict)
            else {}
        )
        slot_names = metadata.get("selected_slot_names", [])
        event.set_extra(
            "_interaction_router_context_nodes",
            [str(name) for name in slot_names] if isinstance(slot_names, list) else [],
        )
        return render_result


def _truncate_router_diagnostic(value: object, *, limit: int = 160) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."



def add_router_plugin_directory_slots_to_pack(
    pack,
    prompt_extensions: list[PromptExtension],
) -> None:
    plugins = _extract_router_plugin_directory(prompt_extensions)
    if not plugins:
        return
    pack.add_slot(
        ContextSlot(
            name="capability.router_plugin_directory",
            value={"plugins": plugins},
            category="capability",
            source="interaction_router",
            render_mode="structured",
            meta={"scope": "static"},
        )
    )
    pack.meta["slot_count"] = len(pack.slots)


def _extract_router_plugin_directory(
    prompt_extensions: list[PromptExtension],
) -> list[dict[str, str]]:
    plugins: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for extension in prompt_extensions:
        if not isinstance(extension, PromptExtension):
            continue
        if extension.mount != "capability" or not isinstance(extension.value, dict):
            continue
        raw_plugins = extension.value.get("plugins")
        if isinstance(raw_plugins, dict):
            raw_plugins = [raw_plugins]
        if not isinstance(raw_plugins, list):
            continue
        for item in raw_plugins:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            description = str(item.get("description", "") or "").strip()
            if not name or not description:
                continue
            key = (name, description)
            if key in seen:
                continue
            seen.add(key)
            plugins.append({"name": name, "description": description})
    return plugins


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
