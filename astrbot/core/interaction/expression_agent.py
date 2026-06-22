from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from astrbot import logger
from astrbot.core.output_contract import OutputContract
from astrbot.core.prompt.context_collect import build_prompt_extension_slots
from astrbot.core.prompt.extensions import PromptExtension
from astrbot.core.prompt.render import PromptRenderEngine
from astrbot.core.prompt.render.selector import _extract_json_object
from astrbot.core.provider import Provider
from astrbot.core.star.context import Context

from .context_builder import (
    InteractionPromptContributorError,
    append_interaction_prompt_extensions_to_pack,
    build_prompt_render_provider_request,
    clone_interaction_context_pack,
    get_or_collect_interaction_prompt_extensions,
)
from .decision_agent import (
    _build_decision_build_config,
    build_interaction_decision_contexts,
)
from .effects import PersonaEffectSpec, PersonaExpressionPhase
from .effects import PersonaEffectCall, parse_persona_effect_calls
from .memory_store import InteractionMemoryStore
from .turn_state import get_interaction_turn_state, set_interaction_turn_persona_id
from .types import InteractionAgentConfig


@dataclass(slots=True)
class PersonaExpressionRequest:
    phase: PersonaExpressionPhase
    source_text: str = ""
    executor_material: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PersonaExpressionResult:
    spoken_reply: str = ""
    effect_calls: list[PersonaEffectCall] = field(default_factory=list)
    plugin_hints: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class InteractionExpressionError(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def phase_requires_spoken_reply(phase: PersonaExpressionPhase) -> bool:
    return phase in {
        "first_response",
        "plugin_output",
        "final_response",
        "executor_result",
    }


def validate_persona_expression_result(
    phase: PersonaExpressionPhase,
    result: PersonaExpressionResult,
) -> None:
    if phase_requires_spoken_reply(phase) and not result.spoken_reply:
        raise InteractionExpressionError("empty_output")
    if not result.spoken_reply and not result.effect_calls:
        raise InteractionExpressionError("empty_output")


def build_persona_runtime_system_prompt() -> str:
    return (
        "你负责以当前人格对用户表达。\n"
        "根据本次调用提供的场景，生成自然语言表达以及必要的插件提示。\n"
        "不要决定是否进入执行层，不要假装已完成尚未完成的任务。\n"
        "协议字段不会直接展示给用户，spoken_reply 才是用户可见内容。\n"
        "如果场景为 first_response 且输入明显需要工具/搜索/代码/检索，"
        "只给自然的即时回应，不声称已完成。\n"
        "如果场景为 plugin_output，必须保持原始事实，只做人格化改写。"
    )


def build_persona_expression_tool_parameters(
    effects: Sequence[PersonaEffectSpec] = (),
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "spoken_reply": {"type": "string"},
        "plugin_hints": {"type": "object", "additionalProperties": True},
        "metadata": {"type": "object", "additionalProperties": True},
    }
    effect_names = sorted(
        {
            effect.name
            for effect in effects
            if isinstance(effect, PersonaEffectSpec) and effect.enabled
        }
    )
    if effect_names:
        properties["effect_calls"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": effect_names},
                    "arguments": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
                "required": ["name", "arguments"],
            },
        }

    return {
        "type": "object",
        "properties": copy.deepcopy(properties),
        "required": ["spoken_reply"],
    }


def build_persona_expression_output_contract() -> OutputContract:
    return build_persona_expression_output_contract_for_effects(())


def build_persona_expression_output_contract_for_effects(
    effects: Sequence[PersonaEffectSpec] = (),
) -> OutputContract:
    return OutputContract(
        mode="tool_call",
        strict=False,
        schema=build_persona_expression_tool_parameters(effects),
        preferred_tool_name="persona_expression",
        allow_text_fallback=True,
    )


def _coerce_hints_dict(value: object) -> dict[str, Any]:
    """将 plugin_hints / metadata 值强制转换为 dict，处理 provider 返回 JSON 字符串的情况。"""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            pass
    return {}


def extract_persona_expression_result(
    text: object,
    *,
    llm_response=None,
    output_contract: OutputContract | None = None,
    effects: Sequence[PersonaEffectSpec] = (),
) -> PersonaExpressionResult:
    """三级解析：tool call → JSON object → 纯文本兼容。"""
    preferred = (
        output_contract.preferred_tool_name
        if isinstance(output_contract, OutputContract)
        else None
    )
    # 1. 协议 tool call
    if llm_response is not None:
        for tool_name, tool_arg in zip(
            list(getattr(llm_response, "tools_call_name", []) or []),
            list(getattr(llm_response, "tools_call_args", []) or []),
            strict=False,
        ):
            if preferred and tool_name != preferred:
                continue
            if isinstance(tool_arg, dict):
                return PersonaExpressionResult(
                    spoken_reply=str(tool_arg.get("spoken_reply", "") or ""),
                    effect_calls=parse_persona_effect_calls(
                        tool_arg.get("effect_calls", []),
                        effects,
                    ),
                    plugin_hints=_coerce_hints_dict(tool_arg.get("plugin_hints")),
                    metadata=_coerce_hints_dict(tool_arg.get("metadata")),
                )
    # 2. JSON object fallback
    payload = _extract_json_object(text)
    if isinstance(payload, dict) and "spoken_reply" in payload:
        return PersonaExpressionResult(
            spoken_reply=str(payload.get("spoken_reply", "") or ""),
            effect_calls=parse_persona_effect_calls(
                payload.get("effect_calls", []),
                effects,
            ),
            plugin_hints=_coerce_hints_dict(payload.get("plugin_hints")),
            metadata=_coerce_hints_dict(payload.get("metadata")),
        )
    # 3. 纯文本兼容
    return PersonaExpressionResult(spoken_reply=(str(text or "")).strip())


def _build_expression_prompt(req: PersonaExpressionRequest) -> str:
    if req.phase == "plugin_output":
        return (
            "请将下面这段插件输出，改写成当前人格会对用户说的话，"
            "并按 persona_expression 输出协议返回。\n\n"
            f"插件输出：\n{req.source_text}"
        )
    return "请按 persona_expression 输出协议生成本轮用户可见回应。"


# ── 兼容旧接口的系统 prompt 构建函数（被 add_*_slots_to_pack 引用）──────────
def build_fast_expression_system_prompt() -> str:
    return build_persona_runtime_system_prompt()


def build_fast_expression_prompt() -> str:
    return _build_expression_prompt(
        PersonaExpressionRequest(phase="first_response")
    )


def build_plugin_output_rewrite_system_prompt() -> str:
    return build_persona_runtime_system_prompt()


def build_plugin_output_rewrite_prompt(source_text: str) -> str:
    return _build_expression_prompt(
        PersonaExpressionRequest(phase="plugin_output", source_text=source_text)
    )


class InteractionExpressionAgent:
    def __init__(self, memory_store: InteractionMemoryStore) -> None:
        self.memory_store = memory_store

    async def generate_expression(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        req: PersonaExpressionRequest,
    ) -> PersonaExpressionResult:
        """统一 Persona 表达入口，返回结构化 PersonaExpressionResult。"""
        provider = plugin_context.get_provider_by_id(
            interaction_config.expression_provider_id
        )
        if not isinstance(provider, Provider):
            raise InteractionExpressionError(
                "provider_unavailable",
                f"provider unavailable: provider_id={interaction_config.expression_provider_id}",
            )
        mode = "plugin_output_rewrite" if req.phase == "plugin_output" else "fast_expression"
        turn_state = get_interaction_turn_state(event)
        if turn_state is not None:
            async with turn_state.lock:
                render_result = await self._prepare_render_result(
                    event,
                    plugin_context,
                    interaction_config,
                    provider,
                    mode=mode,
                    phase=req.phase,
                )
        else:
            render_result = await self._prepare_render_result(
                event,
                plugin_context,
                interaction_config,
                provider,
                mode=mode,
                phase=req.phase,
            )
        event.set_extra("_interaction_expression_prompt_render_result", render_result)
        output_contract = render_result.output_contract
        persona_effect_specs = render_result.metadata.get(
            "persona_effect_specs",
            [],
        )
        if not isinstance(persona_effect_specs, list):
            persona_effect_specs = []
        provider_config = getattr(provider, "provider_config", {})
        if not isinstance(provider_config, dict):
            provider_config = {}
        logger.info(
            "DIAG expression.contract: platform_id=%s session_id=%s phase=%s provider_type=%s model=%s renderer=%s strategy=%s degraded=%s tool_name=%s",
            event.get_platform_id(),
            event.session_id,
            req.phase,
            provider_config.get("type", ""),
            (
                provider.get_model()
                if callable(getattr(provider, "get_model", None))
                else ""
            ),
            render_result.metadata.get("renderer"),
            render_result.metadata.get("output_contract_strategy"),
            render_result.metadata.get("output_contract_degraded"),
            (
                render_result.compiled_output_contract.tool_name
                if render_result.compiled_output_contract is not None
                else None
            ),
        )
        try:
            llm_resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=_build_expression_prompt(req),
                    contexts=build_interaction_decision_contexts(render_result.messages),
                    system_prompt=render_result.system_prompt or "",
                    temperature=interaction_config.expression_temperature,
                    output_contract=output_contract,
                    compiled_output_contract=render_result.compiled_output_contract,
                ),
                timeout=interaction_config.expression_timeout,
            )
        except asyncio.TimeoutError:
            raise InteractionExpressionError("timeout") from None
        except Exception as exc:  # noqa: BLE001
            raise InteractionExpressionError("model_error", str(exc)) from exc

        logger.info(
            "DIAG expression.response_shape: platform_id=%s session_id=%s phase=%s has_tool_calls=%s tool_names=%s text_length=%s",
            event.get_platform_id(),
            event.session_id,
            req.phase,
            bool(getattr(llm_resp, "tools_call_args", None)),
            list(getattr(llm_resp, "tools_call_name", []) or []),
            len((llm_resp.completion_text or "").strip()),
        )
        result = extract_persona_expression_result(
            llm_resp.completion_text,
            llm_response=llm_resp,
            output_contract=output_contract,
            effects=persona_effect_specs,
        )
        logger.info(
            "DIAG expression.plugin_hints: platform_id=%s session_id=%s phase=%s keys=%s payload_present=%s effect_calls=%s",
            event.get_platform_id(),
            event.session_id,
            req.phase,
            sorted(result.plugin_hints.keys()) if result.plugin_hints else [],
            bool(result.plugin_hints),
            [call.name for call in result.effect_calls],
        )
        validate_persona_expression_result(req.phase, result)
        logger.info(
            "Persona expression generated: platform_id=%s session_id=%s phase=%s length=%s plugin_hints_keys=%s effect_calls=%s",
            event.get_platform_id(),
            event.session_id,
            req.phase,
            len(result.spoken_reply),
            sorted(result.plugin_hints.keys()) if result.plugin_hints else [],
            [call.name for call in result.effect_calls],
        )
        return result

    async def generate_first_response(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
    ) -> str:
        """兼容包装：调用 generate_expression 并返回 spoken_reply。"""
        result = await self.generate_expression(
            event,
            plugin_context,
            interaction_config,
            PersonaExpressionRequest(phase="first_response"),
        )
        return result.spoken_reply

    async def rewrite_plugin_output(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        source_text: str,
    ) -> str:
        """兼容包装：调用 generate_expression 并返回 spoken_reply。"""
        result = await self.rewrite_plugin_output_result(
            event,
            plugin_context,
            interaction_config,
            source_text,
        )
        return result.spoken_reply

    async def rewrite_plugin_output_result(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        source_text: str,
    ) -> PersonaExpressionResult:
        return await self.generate_expression(
            event,
            plugin_context,
            interaction_config,
            PersonaExpressionRequest(phase="plugin_output", source_text=source_text),
        )

    async def _prepare_render_result(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        provider: Provider,
        *,
        mode: str = "fast_expression",
        phase: PersonaExpressionPhase = "first_response",
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
        try:
            prompt_extensions = await get_or_collect_interaction_prompt_extensions(
                event,
                plugin_context,
                build_config,
                material.decision_context,
                material,
                purpose="persona_reply",
                phase=phase,
            )
        except InteractionPromptContributorError as exc:
            raise InteractionExpressionError(exc.reason, str(exc)) from exc
        expression_pack = clone_interaction_context_pack(material.prompt_context_pack)
        append_interaction_prompt_extensions_to_pack(
            expression_pack,
            prompt_extensions,
        )
        persona_effect_specs = self._list_persona_effects(plugin_context, phase=phase)
        if mode == "plugin_output_rewrite":
            add_plugin_output_rewrite_slots_to_pack(
                expression_pack,
                effects=persona_effect_specs,
            )
        else:
            add_fast_expression_slots_to_pack(
                expression_pack,
                effects=persona_effect_specs,
            )
        render_result = PromptRenderEngine().render(
            expression_pack,
            event=event,
            plugin_context=plugin_context,
            config=build_config,
            provider_request=build_prompt_render_provider_request(event, provider),
        )
        render_result.metadata["persona_effect_specs"] = persona_effect_specs
        return render_result

    @staticmethod
    def _list_persona_effects(
        plugin_context: Context,
        *,
        phase: PersonaExpressionPhase,
    ) -> list[PersonaEffectSpec]:
        list_effects = getattr(plugin_context, "list_persona_effects", None)
        if not callable(list_effects):
            return []
        effects = list_effects(phase=phase)
        return effects if isinstance(effects, list) else []

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


def add_fast_expression_slots_to_pack(
    pack,
    *,
    effects: Sequence[PersonaEffectSpec] = (),
) -> None:
    extensions: list[PromptExtension] = [
        PromptExtension(
            plugin_id="astrbot.interaction",
            mount="system",
            title="Persona runtime policy",
            value_kind="text",
            value=build_persona_runtime_system_prompt(),
            order=0,
            meta={"scope": "static", "node_type": "interaction_persona_runtime_policy"},
        )
    ]
    for slot in build_prompt_extension_slots(extensions, source="interaction_fast_expression"):
        pack.add_slot(slot)
    pack.meta["slot_count"] = len(pack.slots)
    pack.meta["output_contract"] = build_persona_expression_output_contract_for_effects(
        effects
    ).to_dict()


def add_plugin_output_rewrite_slots_to_pack(
    pack,
    *,
    effects: Sequence[PersonaEffectSpec] = (),
) -> None:
    extensions: list[PromptExtension] = [
        PromptExtension(
            plugin_id="astrbot.interaction",
            mount="system",
            title="Persona runtime policy",
            value_kind="text",
            value=build_persona_runtime_system_prompt(),
            order=0,
            meta={"scope": "static", "node_type": "interaction_persona_runtime_policy"},
        )
    ]
    for slot in build_prompt_extension_slots(extensions, source="interaction_plugin_output_rewrite"):
        pack.add_slot(slot)
    pack.meta["slot_count"] = len(pack.slots)
    pack.meta["output_contract"] = build_persona_expression_output_contract_for_effects(
        effects
    ).to_dict()
