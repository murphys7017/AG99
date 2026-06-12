from __future__ import annotations

import asyncio
from typing import Any

from astrbot import logger
from astrbot.core.prompt.context_collect import build_prompt_extension_slots
from astrbot.core.prompt.extensions import PromptExtension
from astrbot.core.prompt.render import PromptRenderEngine
from astrbot.core.provider import Provider
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
    build_interaction_decision_contexts,
)
from .memory_store import InteractionMemoryStore
from .turn_state import get_interaction_turn_state, set_interaction_turn_persona_id
from .types import InteractionAgentConfig


class InteractionExpressionError(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def build_fast_expression_system_prompt() -> str:
    return (
        "你是 AstrBot interaction middleware 的 Fast Expression。\n"
        "你的职责是根据当前人格、关系、语气和用户输入，生成本轮第一段用户可见回应。\n"
        "这段回应一定会发送给用户。\n\n"
        "要求：\n"
        "- 像当前角色本人在说话，不要像系统提示或流程说明。\n"
        "- 不判断是否调用核心，不输出 JSON，不提 Router/Core/Middleware。\n"
        "- 普通闲聊可以完整自然回复。\n"
        "- 如果用户请求明显需要执行、检查、工具、文件、代码、检索或长推理，只给自然的即时回应，不要声称已经完成。\n"
        "- 回复保持简洁。"
    )


def build_fast_expression_prompt() -> str:
    return "请生成本轮第一段用户可见回应。"


class InteractionExpressionAgent:
    def __init__(self, memory_store: InteractionMemoryStore) -> None:
        self.memory_store = memory_store

    async def generate_first_response(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
    ) -> str:
        provider = plugin_context.get_provider_by_id(
            interaction_config.expression_provider_id
        )
        if not isinstance(provider, Provider):
            message = (
                "provider unavailable: "
                f"provider_id={interaction_config.expression_provider_id}"
            )
            raise InteractionExpressionError("provider_unavailable", message)
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
        event.set_extra("_interaction_expression_prompt_render_result", render_result)
        try:
            llm_resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=build_fast_expression_prompt(),
                    contexts=build_interaction_decision_contexts(
                        render_result.messages
                    ),
                    system_prompt=render_result.system_prompt or "",
                    temperature=interaction_config.expression_temperature,
                ),
                timeout=interaction_config.expression_timeout,
            )
        except asyncio.TimeoutError:
            raise InteractionExpressionError("timeout") from None
        except Exception as exc:  # noqa: BLE001
            raise InteractionExpressionError("model_error", str(exc)) from exc

        text = (llm_resp.completion_text or "").strip()
        if not text:
            raise InteractionExpressionError("empty_output")
        logger.info(
            "Interaction fast expression generated: platform_id=%s session_id=%s length=%s",
            event.get_platform_id(),
            event.session_id,
            len(text),
        )
        return text

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
                raise InteractionExpressionError(exc.reason, str(exc)) from exc
            append_interaction_prompt_extensions_to_pack(
                material.prompt_context_pack,
                prompt_extensions,
            )
            material.prompt_extensions_collected = True
        expression_pack = clone_interaction_context_pack(material.prompt_context_pack)
        add_fast_expression_slots_to_pack(expression_pack)
        with temporary_event_extra(event, "provider", provider):
            return PromptRenderEngine().render(
                expression_pack,
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


def add_fast_expression_slots_to_pack(pack) -> None:
    extensions: list[PromptExtension] = [
        PromptExtension(
            plugin_id="astrbot.interaction",
            mount="system",
            title="Interaction fast expression policy",
            value_kind="text",
            value=build_fast_expression_system_prompt(),
            order=0,
            meta={
                "scope": "static",
                "node_type": "interaction_fast_expression_policy",
            },
        )
    ]
    for slot in build_prompt_extension_slots(
        extensions,
        source="interaction_fast_expression",
    ):
        pack.add_slot(slot)
    pack.meta["slot_count"] = len(pack.slots)
    pack.meta.pop("output_contract", None)
