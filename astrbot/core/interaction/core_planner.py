from __future__ import annotations

import asyncio

from astrbot import logger
from astrbot.core.output_contract import CompiledOutputContract, OutputContract
from astrbot.core.prompt.context_types import ContextSlot
from astrbot.core.prompt.render import PromptRenderEngine, PromptTarget
from astrbot.core.prompt.structured_json import extract_json_object
from astrbot.core.provider import Provider
from astrbot.core.star.context import Context

from .context_builder import (
    build_prompt_render_provider_request,
    clone_interaction_context_pack,
    get_or_build_interaction_context_material,
)
from .memory_store import InteractionMemoryStore
from .prompt_support import (
    build_interaction_prompt_build_config,
    build_model_context_messages,
)
from .turn_state import get_interaction_turn_state
from .types import CorePlanningDecision, InteractionAgentConfig


class CorePlannerError(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def build_core_planner_system_prompt() -> str:
    return (
        "你是 Core Planner，一个独立的执行必要性判断器。\n"
        "只根据当前输入与提供的事实，判断是否真的需要执行层。\n"
        "execute：需要查询、搜索、知识库、工具、插件、文件处理、计算、外部行动，"
        "或需要执行器继续完成当前说话者的明确任务。\n"
        "not_required：普通聊天、情绪回应、玩笑、感叹、轻量解释，或统一 Persona "
        "无需执行器即可直接完成。\n"
        "历史、memory、插件目录和其他说话者的任务只能帮助理解，不能单独触发 execute。\n"
        "选择 execute 时，把当前请求整理为简洁、完整、可执行的 CoreTaskSpec；"
        "不要限制 Core 的能力，也不要编造未提供的事实。\n"
        "不要生成用户可见回复，不要输出人格内容、effect、工具调用参数或思考过程。"
    )


def build_core_planner_prompt() -> str:
    return "判断是否需要执行层，并按输出契约返回结果。"


def build_core_planner_output_contract() -> OutputContract:
    task_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task_intent": {"type": "string", "minLength": 1},
            "task_summary": {"type": "string", "minLength": 1},
            "execution_prompt": {"type": "string", "minLength": 1},
            "suggested_capabilities": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "task_intent",
            "task_summary",
            "execution_prompt",
            "suggested_capabilities",
        ],
    }
    return OutputContract(
        mode="tool_call",
        strict=True,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": ["execute", "not_required"],
                },
                "core_task_spec": {
                    "anyOf": [task_schema, {"type": "null"}],
                },
            },
            "required": ["decision", "core_task_spec"],
        },
        preferred_tool_name="core_execution_plan",
        allow_text_fallback=False,
    )


def extract_core_planning_decision(
    text: object,
    *,
    llm_response,
    output_contract: OutputContract,
    compiled_output_contract: CompiledOutputContract,
) -> CorePlanningDecision:
    preferred_name = output_contract.preferred_tool_name
    for tool_name, tool_arg in zip(
        list(getattr(llm_response, "tools_call_name", []) or []),
        list(getattr(llm_response, "tools_call_args", []) or []),
        strict=False,
    ):
        if preferred_name and tool_name != preferred_name:
            continue
        payload = tool_arg if isinstance(tool_arg, dict) else extract_json_object(tool_arg)
        decision = CorePlanningDecision.from_mapping(payload)
        if decision is not None:
            return decision

    if compiled_output_contract.strategy != "prompt_only":
        raise CorePlannerError(
            "missing_core_planner_tool_call",
            "core_execution_plan tool call missing",
        )
    decision = CorePlanningDecision.from_mapping(extract_json_object(text))
    if decision is None:
        raise CorePlannerError(
            "invalid_core_planner_payload",
            "Core Planner returned an invalid structured result",
        )
    return decision


def add_core_planner_slots_to_pack(pack) -> None:
    pack.add_slot(
        ContextSlot(
            name="system.base",
            value=build_core_planner_system_prompt(),
            category="system",
            source="interaction_core_planner",
            render_mode="text",
            meta={
                "scope": "static",
                "node_type": "interaction_core_planner_system_prompt",
            },
        )
    )
    pack.meta["slot_count"] = len(pack.slots)
    pack.meta["output_contract"] = build_core_planner_output_contract().to_dict()


class CorePlannerAgent:
    def __init__(self, memory_store: InteractionMemoryStore) -> None:
        self.memory_store = memory_store

    async def plan(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
    ) -> CorePlanningDecision:
        provider = plugin_context.get_provider_by_id(
            interaction_config.planner_provider_id
        )
        if not isinstance(provider, Provider):
            raise CorePlannerError(
                "provider_unavailable",
                f"provider unavailable: provider_id={interaction_config.planner_provider_id}",
            )
        render_result = await self._prepare_render_result(
            event,
            plugin_context,
            interaction_config,
            provider,
        )
        contract = render_result.output_contract
        compiled = render_result.compiled_output_contract
        if not isinstance(contract, OutputContract) or not isinstance(
            compiled,
            CompiledOutputContract,
        ):
            raise CorePlannerError("unsupported_output_contract")
        try:
            response = await asyncio.wait_for(
                provider.text_chat(
                    prompt=build_core_planner_prompt(),
                    contexts=build_model_context_messages(render_result.messages),
                    system_prompt=render_result.system_prompt or "",
                    temperature=interaction_config.planner_temperature,
                    tool_choice="required",
                    output_contract=contract,
                    compiled_output_contract=compiled,
                ),
                timeout=interaction_config.planner_timeout,
            )
        except asyncio.TimeoutError:
            raise CorePlannerError("timeout") from None
        except Exception as exc:
            raise CorePlannerError("model_error", str(exc)) from exc
        decision = extract_core_planning_decision(
            response.completion_text,
            llm_response=response,
            output_contract=contract,
            compiled_output_contract=compiled,
        )
        logger.info(
            "Core Planner parsed: platform_id=%s session_id=%s decision=%s has_task_spec=%s",
            event.get_platform_id(),
            event.session_id,
            decision.action.value,
            decision.task_spec is not None,
        )
        return decision

    async def _prepare_render_result(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        provider: Provider,
    ):
        build_config = build_interaction_prompt_build_config(plugin_context, event)
        turn_state = get_interaction_turn_state(event)
        if turn_state is not None:
            async with turn_state.lock:
                material = await get_or_build_interaction_context_material(
                    event=event,
                    plugin_context=plugin_context,
                    interaction_config=interaction_config,
                    build_config=build_config,
                    memory_store=self.memory_store,
                )
        else:
            material = await get_or_build_interaction_context_material(
                event=event,
                plugin_context=plugin_context,
                interaction_config=interaction_config,
                build_config=build_config,
                memory_store=self.memory_store,
            )
        planner_pack = clone_interaction_context_pack(material.prompt_context_pack)
        add_core_planner_slots_to_pack(planner_pack)
        render_result = PromptRenderEngine().render(
            planner_pack,
            target=PromptTarget.CORE_PLANNER,
            event=event,
            plugin_context=plugin_context,
            config=build_config,
            provider_request=build_prompt_render_provider_request(event, provider),
        )
        event.set_extra("_interaction_core_planner_prompt_render_result", render_result)
        return render_result


__all__ = [
    "CorePlannerAgent",
    "CorePlannerError",
    "add_core_planner_slots_to_pack",
    "build_core_planner_output_contract",
    "build_core_planner_system_prompt",
    "extract_core_planning_decision",
]
