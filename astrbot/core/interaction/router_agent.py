from __future__ import annotations

import asyncio
from typing import Any

from astrbot import logger
from astrbot.core.prompt.render import (
    PromptRenderEngine,
    PromptRenderProfile,
    PromptTarget,
)
from astrbot.core.prompt.structured_json import extract_json_object
from astrbot.core.provider import Provider
from astrbot.core.star.context import Context

from .context_builder import (
    build_prompt_render_provider_request,
    get_or_build_interaction_context_material,
)
from .group_reply import (
    GROUP_REPLY_CANDIDATE_KIND_EXTRA,
    is_group_reply_candidate,
)
from .prompt_support import (
    build_interaction_prompt_build_config,
    build_model_context_messages,
)
from .types import (
    InteractionAgentConfig,
    InteractionRouteDecision,
    InteractionRouteMode,
)


class InteractionRouterError(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def build_interaction_router_system_prompt(
    *,
    allow_silent: bool = False,
    group_candidate_kind: str | None = None,
) -> str:
    is_group_candidate = allow_silent or bool(group_candidate_kind)
    candidate_context = (
        "这是一次未显式唤醒的群聊延续候选。默认选择 silent；只有当前消息明确、直接地承接机器人最近一次回复，且机器人加入确有必要时，才选择 persona 或 hybrid。仅同一发送者、时间相近、短确认、情绪表达或语义不明都不足以回复。\n"
        if group_candidate_kind == "continuation"
        else "这是一次未显式唤醒的群聊主动候选。默认选择 silent；除非当前消息明确需要机器人加入且回复会为群聊增加直接价值，否则选择 silent。不要因为普通闲聊、提及模型、上下文或候选资格本身加入对话。\n"
        if group_candidate_kind == "ambient"
        else ""
    )
    silent_candidate = (
        "- silent：当前未显式唤醒的群聊输入不满足严格回复条件，或当前并不需要机器人加入。\n"
        if is_group_candidate
        else ""
    )
    continuation_rule = (
        "只有已满足严格回复条件时，省略、短确认或情绪表达才可结合最近一轮理解；没有新增明确执行意图时选择 persona。\n"
        "只有已满足严格回复条件时，普通寒暄、情绪回应、轻量吐槽、短确认、感叹、玩笑和普通陈述才选择 persona；在 persona、hybrid 与 silent 之间不确定时选择 silent。\n"
        if is_group_candidate
        else "当前输入若是对最近一轮回复的承接、省略、短确认或情绪表达，应结合最近一轮理解；只要没有新增明确执行意图，就选择 persona。\n"
        "普通寒暄、情绪回应、轻量吐槽、短确认、感叹、玩笑、普通陈述和无明确执行意图的短消息选择 persona；在 persona 与 hybrid 之间不确定时也选择 persona。\n"
    )
    labels = "silent、persona 或 hybrid" if is_group_candidate else "persona 或 hybrid"
    return (
        f"你是 Interaction Router，一个严格的{'三' if is_group_candidate else '二'}分类选择器。\n"
        "任务：从候选标签中选择一个。当前用户输入是首要依据；聊天记录、memory 和 router 上下文用于理解当前对话。\n"
        "router 上下文可能包含插件目录；插件目录只说明本地插件是什么、负责什么，不能单独成为选择 hybrid 的理由。\n"
        "候选标签：\n"
        f"{silent_candidate}"
        "- persona：统一拟人层可以直接完成回应，不需要核心 Agent。\n"
        "- hybrid：当前输入本身包含明确的执行、查询或处理意图，明确需要核心 Agent 参与；或当前输入明确继续当前说话者未完成的核心任务。\n"
        f"{candidate_context}"
        "聊天记录、memory、插件目录或其他说话者的任务不能单独成为选择 hybrid 的理由。\n"
        f"{continuation_rule}"
        "不要限制或枚举核心 Agent 的能力范围。\n"
        "不要推断具体插件协议、动作参数或输出 schema。\n"
        f"输出约束：不要生成用户回复，不要输出 JSON，只返回 {labels}。"
    )


def build_interaction_router_prompt(
    *,
    allow_silent: bool = False,
    group_candidate_kind: str | None = None,
) -> str:
    labels = (
        "silent、persona 或 hybrid"
        if allow_silent or group_candidate_kind
        else "persona 或 hybrid"
    )
    return f"请只输出 {labels}。"


def extract_interaction_route_payload(
    text: object,
) -> dict[str, Any] | None:
    payload = extract_json_object(text)
    if payload is not None:
        return payload
    if not isinstance(text, str):
        return None
    raw = text.strip().strip('"').strip("'").lower()
    if raw in {
        InteractionRouteMode.SILENT.value,
        InteractionRouteMode.PERSONA.value,
        InteractionRouteMode.HYBRID.value,
    }:
        return {"mode": raw}
    return None


class InteractionRouterAgent:
    async def route(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
    ) -> InteractionRouteDecision:
        provider = plugin_context.get_provider_by_id(
            interaction_config.router_provider_id
        )
        if not isinstance(provider, Provider):
            message = (
                f"provider unavailable: provider_id={interaction_config.router_provider_id}"
            )
            raise InteractionRouterError("provider_unavailable", message)
        # Context material uses turn-local single-flight; target rendering stays branch-local.
        render_result = await self._prepare_render_result(
            event,
            plugin_context,
            interaction_config,
            provider,
        )
        try:
            llm_resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=render_result.request_prompt or "",
                    contexts=build_model_context_messages(render_result.messages),
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
        allow_silent = is_group_reply_candidate(event)
        payload = extract_interaction_route_payload(llm_resp.completion_text)
        route = InteractionRouteDecision.from_mapping(payload)
        if route is None:
            raise InteractionRouterError("invalid_payload")
        if route.route_mode is InteractionRouteMode.SILENT and not allow_silent:
            raise InteractionRouterError("disallowed_mode")
        event.set_extra("_interaction_router_result_source", "parsed")
        logger.info(
            "Interaction router parsed: platform_id=%s session_id=%s mode=%s raw_output=%s",
            event.get_platform_id(),
            event.session_id,
            route.route_mode.value,
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
        allow_silent = is_group_reply_candidate(event)
        candidate_kind = str(
            event.get_extra(GROUP_REPLY_CANDIDATE_KIND_EXTRA, "") or ""
        ).strip() or None
        build_config = build_interaction_prompt_build_config(plugin_context, event)
        material = await get_or_build_interaction_context_material(
            event=event,
            plugin_context=plugin_context,
            interaction_config=interaction_config,
            build_config=build_config,
        )
        render_result = PromptRenderEngine().render(
            material.prompt_context_pack,
            target=PromptTarget.ROUTER,
            event=event,
            plugin_context=plugin_context,
            config=build_config,
            provider_request=build_prompt_render_provider_request(event, provider),
            profile=PromptRenderProfile(
                name="interaction_router",
                system_prompt=build_interaction_router_system_prompt(
                    allow_silent=allow_silent,
                    group_candidate_kind=candidate_kind,
                ),
                request_prompt=build_interaction_router_prompt(
                    allow_silent=allow_silent,
                    group_candidate_kind=candidate_kind,
                ),
            ),
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
