from __future__ import annotations

import asyncio
import copy
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

try:
    from json_repair import repair_json
except ImportError:  # pragma: no cover - optional runtime dependency
    repair_json = None

from astrbot import logger
from astrbot.core.agent.tool import TOOL_TARGET_PERSONAL_EXPRESSION, ToolSet
from astrbot.core.output_contract import CompiledOutputContract, OutputContract
from astrbot.core.prompt.builder import PromptContextBuilder
from astrbot.core.prompt.context_collect import resolve_toolset_for_target
from astrbot.core.prompt.render import (
    PromptRenderEngine,
    PromptRenderProfile,
    PromptTarget,
)
from astrbot.core.prompt.structured_json import extract_json_object
from astrbot.core.provider import Provider, resolve_fallback_chat_providers
from astrbot.core.provider.modalities import (
    log_context_sanitize_stats,
    sanitize_contexts_by_modalities,
)
from astrbot.core.star.context import Context

from .collectors import PersonaVisibleReplyCollector
from .context_builder import (
    build_prompt_render_provider_request,
    get_or_build_interaction_context_material,
)
from .effects import (
    PersonaEffectCall,
    PersonaEffectSpec,
    normalize_persona_effect_parameters_schema,
    parse_persona_effect_calls_with_issues,
)
from .prompt_support import (
    build_interaction_prompt_build_config,
    build_model_context_messages,
)
from .turn_state import get_interaction_turn_state, set_interaction_turn_persona_id
from .types import InteractionAgentConfig


@dataclass(slots=True)
class PersonaExpressionRequest:
    source_text: str = ""
    immediate_reply: str = ""
    delegated_task_summary: str = ""
    observed_text: str = ""
    total_text: str = ""
    pending_text: str = ""
    preserve_facts: bool = False
    short_reply: bool = False
    allow_empty: bool = False
    allow_plugin_tools: bool = False


@dataclass(slots=True)
class PersonaExpressionResult:
    spoken_reply: str = ""
    effect_calls: list[PersonaEffectCall] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class InteractionExpressionError(RuntimeError):
    def __init__(
        self,
        reason: str,
        message: str | None = None,
        *,
        tool_material: str | None = None,
    ) -> None:
        self.reason = reason
        self.tool_material = tool_material
        super().__init__(message or reason)


_DEEPSEEK_REASONING_MARKER_APPLIED_EXTRA_KEY = (
    "_interaction_deepseek_reasoning_marker_applied"
)
_DEEPSEEK_INNER_OS_MARKER = (
    "\n\n【角色沉浸要求】在你的思考过程（<think>标签内）中，请遵守以下规则：\n"
    '1. 请以角色第一人称进行内心独白，用括号包裹内心活动，例如"（心想：……）"或"(内心OS：……)"\n'
    '2. 用第一人称描写角色的内心感受，例如"我心想""我觉得""我暗自"等\n'
    "3. 思考内容应沉浸在角色中，通过内心独白分析剧情和规划回复"
)


def validate_persona_expression_result(
    req: PersonaExpressionRequest,
    result: PersonaExpressionResult,
) -> None:
    if not result.spoken_reply and not req.allow_empty:
        raise InteractionExpressionError("empty_output")


def build_persona_runtime_system_prompt() -> str:
    return (
        "你负责以当前人格对用户表达。\n"
        "根据本次调用提供的 visible_reply_material，生成自然语言表达以及必要的人格 effect 调用。\n"
        "必须按本次输出契约返回只包含 spoken_reply 与 effect_calls 的结构化结果。\n"
        "支持协议级 tool call 时，使用 persona_expression 工具承载结构化结果。\n"
        "effect_calls 只能使用注册过的 effect 与参数 schema。\n"
        "effect 参数必须严格符合对应 effect 的 arguments schema：必填字段必须补全，未声明字段不要输出，字段类型必须匹配。\n"
        "source_text 是待表达语义材料，应以它为准组织用户可见回应。\n"
        "immediate_reply 是本轮之前已经说过的短回复，可参考但不要矛盾或重复。\n"
        "delegated_task_summary 表示执行层已经接受的任务；只做简短自然的开始处理确认，不要假装任务已经完成。\n"
        "observed_text、total_text、pending_text 是核心流式执行中的本轮临时内容，只用于理解当前进度，不要当作历史对话。\n"
        "当 source_text 表示调用失败时，应如实说明失败及可确认原因，不要声称仍在处理，也不要复述原始异常结构或敏感信息。\n"
        "preserve_facts 为 true 时必须保留原始事实、数字、结论，不要编造。\n"
        "short_reply 为 true 时只说一句简短口语短句，尽量控制在 20 字以内。\n"
        "allow_empty 为 true 且当前没有必要说话时，可以让 spoken_reply 为空字符串。\n"
        "不要决定是否进入执行层，不要假装已完成尚未完成的任务。\n"
        "协议字段不会直接展示给用户，spoken_reply 才是用户可见内容。"
    )


def build_persona_tool_loop_instruction() -> str:
    return (
        "你现在处于 Personal Expression 的工具处理阶段。\n"
        "只在当前请求确实需要时调用提供的插件工具；工具返回后继续整理事实。\n"
        "这一阶段的输出是交给后续人格表达阶段的内部材料，不是直接发送给用户的最终回复。\n"
        "完成工具调用后，用简洁文本总结已经获得的结果；不需要工具时直接给出简洁的内部判断。\n"
        "不要调用 Core 工具、Skill、知识库或未提供的工具。"
    )


def _resolve_provider_model(provider: Provider) -> str:
    getter = getattr(provider, "get_model", None)
    if callable(getter):
        try:
            return str(getter() or "").strip().lower()
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _is_deepseek_reasoning_provider(provider: Provider) -> bool:
    provider_config = getattr(provider, "provider_config", {})
    if not isinstance(provider_config, dict):
        provider_config = {}
    provider_type = str(provider_config.get("type", "") or "").strip().lower()
    model = _resolve_provider_model(provider)
    if model.startswith("deepseek-v4") or model.startswith("deepseek-reasoner"):
        return True
    return provider_type == "deepseek_chat_completion" and (
        model.startswith("deepseek-v4") or model.startswith("deepseek-reasoner")
    )


def _pack_has_conversation_history(pack) -> bool:
    slot = pack.get_slot("conversation.history")
    if slot is None or not isinstance(slot.value, dict):
        return False
    turns = slot.value.get("turns", [])
    return isinstance(turns, list) and len(turns) > 0


def resolve_deepseek_first_turn_reasoning_marker(
    event,
    pack,
    provider: Provider,
) -> str:
    if not _is_deepseek_reasoning_provider(provider):
        return ""
    if event.get_extra(_DEEPSEEK_REASONING_MARKER_APPLIED_EXTRA_KEY):
        return ""
    if _pack_has_conversation_history(pack):
        return ""
    input_slot = pack.get_slot("input.text")
    if input_slot is None or not isinstance(input_slot.value, str):
        return ""
    if not input_slot.value.strip():
        return ""
    event.set_extra(_DEEPSEEK_REASONING_MARKER_APPLIED_EXTRA_KEY, True)
    return _DEEPSEEK_INNER_OS_MARKER


def build_persona_expression_tool_parameters(
    effects: Sequence[PersonaEffectSpec] = (),
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "spoken_reply": {"type": "string"},
        "effect_calls": {
            "type": "array",
            "items": False,
        },
    }
    enabled_effects = sorted(
        (
            effect
            for effect in effects
            if isinstance(effect, PersonaEffectSpec) and effect.enabled
        ),
        key=lambda effect: effect.name,
    )
    effect_schemas = [
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"const": effect.name},
                "arguments": normalize_persona_effect_parameters_schema(
                    effect.parameters
                ),
            },
            "required": ["name", "arguments"],
        }
        for effect in enabled_effects
    ]
    if effect_schemas:
        properties["effect_calls"] = {
            "type": "array",
            "items": {"oneOf": copy.deepcopy(effect_schemas)},
        }

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": copy.deepcopy(properties),
        "required": ["spoken_reply", "effect_calls"],
    }


def build_persona_expression_output_contract() -> OutputContract:
    return build_persona_expression_output_contract_for_effects(())


def build_persona_expression_output_contract_for_effects(
    effects: Sequence[PersonaEffectSpec] = (),
) -> OutputContract:
    return OutputContract(
        mode="tool_call",
        strict=True,
        schema=build_persona_expression_tool_parameters(effects),
        preferred_tool_name="persona_expression",
        allow_text_fallback=False,
    )


def _coerce_mapping_dict(value: object) -> dict[str, Any]:
    """将 metadata 值强制转换为 dict，处理 provider 返回 JSON 字符串的情况。"""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            pass
    return {}


def _coerce_json_like(value: object) -> Any:
    if isinstance(value, dict | list):
        return copy.deepcopy(value)
    if not isinstance(value, str):
        return value

    cleaned = value.strip()
    if not cleaned:
        return value
    try:
        return json.loads(cleaned)
    except (ValueError, TypeError):
        pass

    extracted = extract_json_object(cleaned)
    if extracted is not None:
        return extracted

    if repair_json is None:
        return value
    try:
        repaired = repair_json(cleaned, return_objects=True)
    except Exception:  # noqa: BLE001
        return value
    return repaired


def _coerce_tool_call_payload(tool_arg: object) -> dict[str, Any] | None:
    payload = _coerce_json_like(tool_arg)
    if not isinstance(payload, dict):
        return None

    normalized = dict(payload)
    effect_calls = _coerce_json_like(normalized.get("effect_calls", []))
    if isinstance(effect_calls, list):
        normalized["effect_calls"] = effect_calls
    metadata = _coerce_json_like(normalized.get("metadata", {}))
    if isinstance(metadata, dict):
        normalized["metadata"] = metadata
    return normalized


def _build_persona_expression_result_from_payload(
    payload: dict[str, Any],
    *,
    effects: Sequence[PersonaEffectSpec] = (),
) -> PersonaExpressionResult:
    effect_calls, effect_issues = parse_persona_effect_calls_with_issues(
        payload.get("effect_calls", []),
        effects,
    )
    metadata = _coerce_mapping_dict(payload.get("metadata"))
    if effect_issues:
        metadata["effect_parse_issues"] = [issue.to_dict() for issue in effect_issues]
    return PersonaExpressionResult(
        spoken_reply=str(payload.get("spoken_reply", "") or ""),
        effect_calls=effect_calls,
        metadata=metadata,
    )


def extract_persona_expression_result(
    text: object,
    *,
    llm_response=None,
    output_contract: OutputContract | None = None,
    compiled_output_contract: CompiledOutputContract | None = None,
    effects: Sequence[PersonaEffectSpec] = (),
) -> PersonaExpressionResult:
    """优先解析结构化输出；严格 JSON 合约下不接受自由文本。"""
    preferred = (
        output_contract.preferred_tool_name
        if isinstance(output_contract, OutputContract)
        else None
    )
    strict_tool_call = (
        isinstance(output_contract, OutputContract)
        and output_contract.mode == "tool_call"
        and not output_contract.allow_text_fallback
    )
    protocol_tool_call_required = (
        strict_tool_call
        and not (
            isinstance(compiled_output_contract, CompiledOutputContract)
            and compiled_output_contract.strategy == "prompt_only"
        )
    )
    strict_json_object = (
        isinstance(output_contract, OutputContract)
        and output_contract.mode == "json_object"
        and output_contract.strict
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
            payload = _coerce_tool_call_payload(tool_arg)
            if isinstance(payload, dict):
                return _build_persona_expression_result_from_payload(
                    payload,
                    effects=effects,
                )
    if protocol_tool_call_required:
        raise InteractionExpressionError(
            "missing_persona_expression_tool_call",
            "persona_expression tool call missing",
        )
    # 2. JSON object fallback
    payload = extract_json_object(text)
    if isinstance(payload, dict) and "spoken_reply" in payload:
        return _build_persona_expression_result_from_payload(
            payload,
            effects=effects,
        )
    if strict_tool_call or strict_json_object:
        raise InteractionExpressionError(
            "invalid_persona_expression_json",
            "persona expression must be a single JSON object",
        )
    # 3. 纯文本兼容
    return PersonaExpressionResult(spoken_reply=(str(text or "")).strip())


def _build_expression_prompt(req: PersonaExpressionRequest) -> str:
    del req
    return "请按输出契约生成当前人格的用户可见回应，不要输出额外自由文本。"


def _should_require_tool_choice(output_contract: OutputContract | None) -> bool:
    return (
        isinstance(output_contract, OutputContract)
        and output_contract.mode == "tool_call"
        and output_contract.strict
    )


class InteractionExpressionAgent:
    async def generate_expression(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        req: PersonaExpressionRequest,
    ) -> PersonaExpressionResult:
        """统一 Persona 表达入口，返回结构化 PersonaExpressionResult。"""
        selected_provider = plugin_context.get_provider_by_id(
            interaction_config.expression_provider_id
        )
        provider = selected_provider if isinstance(selected_provider, Provider) else None
        provider_settings = build_interaction_prompt_build_config(
            plugin_context,
            event,
        ).provider_settings
        fallback_providers = resolve_fallback_chat_providers(
            provider,
            provider_settings,
            plugin_context.get_provider_by_id,
        )
        primary_error: InteractionExpressionError | None = None
        if provider is None:
            primary_error = InteractionExpressionError(
                "provider_unavailable",
                f"provider unavailable: provider_id={interaction_config.expression_provider_id}",
            )
        candidates = ([provider] if provider is not None else []) + fallback_providers
        if not candidates:
            raise primary_error or InteractionExpressionError("provider_unavailable")

        last_error: InteractionExpressionError | None = primary_error
        tool_material_for_fallback: str | None = None
        for index, candidate in enumerate(candidates):
            if tool_material_for_fallback:
                fallback_request = replace(
                    req,
                    source_text=tool_material_for_fallback,
                    preserve_facts=True,
                    allow_plugin_tools=False,
                )
            elif primary_error is not None:
                fallback_request = _build_failure_expression_request(
                    req,
                    primary_error,
                )
            else:
                fallback_request = req
            if primary_error is not None:
                fallback_provider_id = str(
                    candidate.provider_config.get("id", "<unknown>")
                )
                event.set_extra("_interaction_expression_fallback_used", True)
                event.set_extra(
                    "_interaction_expression_primary_failure_reason",
                    str(primary_error),
                )
                event.set_extra(
                    "_interaction_expression_fallback_provider_id",
                    fallback_provider_id,
                )
                logger.warning(
                    "Persona expression switched to fallback provider: platform_id=%s session_id=%s provider_id=%s primary_error=%s",
                    event.get_platform_id(),
                    event.session_id,
                    fallback_provider_id,
                    primary_error,
                )
            try:
                return await self._generate_expression_with_provider(
                    event,
                    plugin_context,
                    interaction_config,
                    candidate,
                    req=fallback_request,
                )
            except InteractionExpressionError as exc:
                last_error = exc
                if exc.tool_material:
                    tool_material_for_fallback = exc.tool_material
                if primary_error is None:
                    primary_error = exc
                if index + 1 < len(candidates):
                    continue
                break

        if primary_error is not None and last_error is not primary_error:
            raise InteractionExpressionError(
                "fallback_exhausted",
                f"primary error: {primary_error}; fallback error: {last_error}",
            ) from last_error
        raise last_error or InteractionExpressionError("model_error")

    async def _generate_expression_with_provider(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        provider: Provider,
        *,
        req: PersonaExpressionRequest,
    ) -> PersonaExpressionResult:
        turn_state = get_interaction_turn_state(event)
        if turn_state is not None:
            async with turn_state.lock:
                render_result = await self._prepare_render_result(
                    event,
                    plugin_context,
                    interaction_config,
                    provider,
                    req=req,
                )
        else:
            render_result = await self._prepare_render_result(
                event,
                plugin_context,
                interaction_config,
                provider,
                req=req,
            )

        tool_material: str | None = None
        if req.allow_plugin_tools and self._provider_supports_tool_calls(provider):
            toolset = await self._resolve_personal_expression_tools(
                event,
                plugin_context,
                interaction_config,
            )
            if toolset:
                try:
                    tool_result = await self._run_persona_tool_loop(
                        event,
                        plugin_context,
                        interaction_config,
                        provider,
                        render_result,
                        toolset,
                    )
                except InteractionExpressionError as exc:
                    raise InteractionExpressionError(
                        exc.reason,
                        str(exc),
                        tool_material=_build_tool_loop_failure_material(exc),
                    ) from exc
                tool_material = (tool_result.completion_text or "").strip()
                if tool_result.role == "err":
                    raise InteractionExpressionError(
                        "tool_loop_error",
                        tool_material or "persona plugin tool loop failed",
                        tool_material=_build_tool_loop_failure_material(
                            tool_material or "provider returned an error response"
                        ),
                    )
                if tool_material:
                    req = replace(
                        req,
                        source_text=tool_material,
                        preserve_facts=True,
                        allow_plugin_tools=False,
                    )
                    render_result = await self._prepare_render_result(
                        event,
                        plugin_context,
                        interaction_config,
                        provider,
                        req=req,
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
            "DIAG expression.contract: platform_id=%s session_id=%s phase=%s provider_type=%s model=%s renderer=%s contract_mode=%s strategy=%s degraded=%s tool_name=%s",
            event.get_platform_id(),
            event.session_id,
            _describe_expression_request(req),
            provider_config.get("type", ""),
            (
                provider.get_model()
                if callable(getattr(provider, "get_model", None))
                else ""
            ),
            render_result.metadata.get("renderer"),
            (output_contract.mode if isinstance(output_contract, OutputContract) else None),
            render_result.metadata.get("output_contract_strategy"),
            render_result.metadata.get("output_contract_degraded"),
            (
                render_result.compiled_output_contract.tool_name
                if render_result.compiled_output_contract is not None
                else None
            ),
        )
        _log_persona_prompt_size_diagnostics(event, req, render_result)
        model_contexts = build_model_context_messages(render_result.messages)
        modalities = provider_config.get("modalities")
        if isinstance(modalities, list):
            model_contexts, sanitize_stats = sanitize_contexts_by_modalities(
                model_contexts,
                modalities,
            )
            log_context_sanitize_stats(sanitize_stats)
        try:
            llm_resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=render_result.request_prompt or "",
                    contexts=model_contexts,
                    system_prompt=render_result.system_prompt or "",
                    temperature=interaction_config.expression_temperature,
                    tool_choice="required"
                    if _should_require_tool_choice(output_contract)
                    else "auto",
                    output_contract=output_contract,
                    compiled_output_contract=render_result.compiled_output_contract,
                ),
                timeout=interaction_config.expression_timeout,
            )
        except asyncio.TimeoutError:
            raise InteractionExpressionError(
                "timeout",
                tool_material=tool_material,
            ) from None
        except Exception as exc:  # noqa: BLE001
            raise InteractionExpressionError(
                "model_error",
                str(exc),
                tool_material=tool_material,
            ) from exc

        if llm_resp.role == "err":
            raise InteractionExpressionError(
                "model_error",
                llm_resp.completion_text or "provider returned an error response",
                tool_material=tool_material,
            )
        logger.info(
            "DIAG expression.response_shape: platform_id=%s session_id=%s phase=%s has_tool_calls=%s tool_names=%s text_length=%s",
            event.get_platform_id(),
            event.session_id,
            _describe_expression_request(req),
            bool(getattr(llm_resp, "tools_call_args", None)),
            list(getattr(llm_resp, "tools_call_name", []) or []),
            len((llm_resp.completion_text or "").strip()),
        )
        try:
            result = extract_persona_expression_result(
                llm_resp.completion_text,
                llm_response=llm_resp,
                output_contract=output_contract,
                compiled_output_contract=render_result.compiled_output_contract,
                effects=persona_effect_specs,
            )
        except InteractionExpressionError as exc:
            if tool_material and not exc.tool_material:
                exc.tool_material = tool_material
            raise
        logger.info(
            "DIAG expression.effect_calls: platform_id=%s session_id=%s phase=%s payload_present=%s effect_calls=%s effect_parse_issues=%s",
            event.get_platform_id(),
            event.session_id,
            _describe_expression_request(req),
            bool(result.effect_calls),
            [call.name for call in result.effect_calls],
            [
                {
                    "name": str(issue.get("name", "")),
                    "reason": str(issue.get("reason", "")),
                }
                for issue in result.metadata.get("effect_parse_issues", [])
                if isinstance(issue, dict)
            ],
        )
        try:
            validate_persona_expression_result(req, result)
        except InteractionExpressionError as exc:
            if tool_material and not exc.tool_material:
                exc.tool_material = tool_material
            raise
        if req.short_reply and result.spoken_reply and len(result.spoken_reply) > 40:
            result.spoken_reply = result.spoken_reply[:40].rstrip("，,。.!！?？")
        logger.info(
            "Persona expression generated: platform_id=%s session_id=%s phase=%s length=%s effect_calls=%s",
            event.get_platform_id(),
            event.session_id,
            _describe_expression_request(req),
            len(result.spoken_reply),
            [call.name for call in result.effect_calls],
        )
        return result

    async def _resolve_personal_expression_tools(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
    ) -> ToolSet:
        build_config = build_interaction_prompt_build_config(plugin_context, event)
        _, toolset, _ = await resolve_toolset_for_target(
            event=event,
            plugin_context=plugin_context,
            config=build_config,
            target=TOOL_TARGET_PERSONAL_EXPRESSION,
            provider_request=None,
        )
        return toolset

    async def _run_persona_tool_loop(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        provider: Provider,
        render_result,
        toolset: ToolSet,
    ):
        provider_config = getattr(provider, "provider_config", {})
        provider_id = (
            str(provider_config.get("id", "")).strip()
            if isinstance(provider_config, dict)
            else ""
        )
        if not provider_id:
            meta = provider.meta()
            provider_id = str(getattr(meta, "id", "")).strip()
        if not provider_id:
            raise InteractionExpressionError(
                "tool_loop_provider_unavailable",
                "persona tool loop provider id unavailable",
            )

        logger.info(
            "DIAG expression.tool_loop: platform_id=%s session_id=%s tool_names=%s",
            event.get_platform_id(),
            event.session_id,
            toolset.names(),
        )
        try:
            return await asyncio.wait_for(
                plugin_context.tool_loop_agent(
                    event=event,
                    chat_provider_id=provider_id,
                    prompt=(
                        render_result.request_prompt or ""
                    ).strip(),
                    contexts=build_model_context_messages(render_result.messages),
                    system_prompt=(
                        f"{render_result.system_prompt or ''}\n\n"
                        f"{build_persona_tool_loop_instruction()}"
                    ).strip(),
                    tools=toolset,
                    max_steps=8,
                    tool_call_timeout=max(
                        1,
                        int(interaction_config.expression_timeout),
                    ),
                ),
                timeout=interaction_config.expression_timeout,
            )
        except asyncio.TimeoutError:
            raise InteractionExpressionError("tool_loop_timeout") from None
        except InteractionExpressionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise InteractionExpressionError(
                "tool_loop_error",
                str(exc),
            ) from exc

    @staticmethod
    def _provider_supports_tool_calls(provider: Provider) -> bool:
        provider_config = getattr(provider, "provider_config", {})
        if not isinstance(provider_config, dict):
            return True
        modalities = provider_config.get("modalities")
        return not isinstance(modalities, list) or "tool_use" in modalities

    async def express_visible_reply_result(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        req: PersonaExpressionRequest,
    ) -> PersonaExpressionResult:
        return await self.generate_expression(
            event,
            plugin_context,
            interaction_config,
            req,
        )

    async def _prepare_render_result(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        provider: Provider,
        *,
        req: PersonaExpressionRequest,
    ):
        build_config = build_interaction_prompt_build_config(plugin_context, event)
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
        provider_request = build_prompt_render_provider_request(event, provider)
        expression_pack = await PromptContextBuilder(
            event,
            plugin_context,
            build_config,
        ).build(
            provider_request=provider_request,
            collectors=[PersonaVisibleReplyCollector(req)],
            include_prompt_extensions=False,
            base=material.prompt_context_pack,
            scope="persona_expression",
        )
        reasoning_marker = resolve_deepseek_first_turn_reasoning_marker(
            event,
            expression_pack,
            provider,
        )
        persona_effect_specs = self._list_persona_effects(plugin_context, event)
        hidden_slot_names = (
            frozenset(
                {
                    "input.images",
                    "input.quoted_images",
                    "input.image_captions",
                    "input.quoted_image_captions",
                }
            )
            if _has_visible_reply_material(req)
            else frozenset()
        )
        profile = PromptRenderProfile(
            name="interaction_persona_runtime",
            system_prompt=build_persona_runtime_system_prompt(),
            request_prompt=_build_expression_prompt(req),
            output_contract=build_persona_expression_output_contract_for_effects(
                persona_effect_specs
            ),
            input_text_suffix=reasoning_marker,
            hidden_slot_names=hidden_slot_names,
        )
        prompt_slot_sizes = {
            str(name): _serialized_size(slot.value)
            for name, slot in expression_pack.slots.items()
        }
        render_result = PromptRenderEngine().render(
            expression_pack,
            target=PromptTarget.PERSONA,
            event=event,
            plugin_context=plugin_context,
            config=build_config,
            provider_request=provider_request,
            profile=profile,
        )
        if reasoning_marker:
            logger.info(
                "DIAG expression.deepseek_reasoning_marker: platform_id=%s session_id=%s phase=%s mode=inner_os applied=True model=%s",
                event.get_platform_id(),
                event.session_id,
                _describe_expression_request(req),
                _resolve_provider_model(provider),
            )
        render_result.metadata["persona_effect_specs"] = persona_effect_specs
        render_result.metadata["prompt_slot_sizes"] = prompt_slot_sizes
        return render_result

    @staticmethod
    def _list_persona_effects(
        plugin_context: Context,
        event,
    ) -> list[PersonaEffectSpec]:
        list_effects = getattr(plugin_context, "list_persona_effects", None)
        if not callable(list_effects):
            return []
        effects = list_effects(event=event)
        return effects if isinstance(effects, list) else []

    async def _build_or_reuse_context_material(
        self,
        *,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        build_config,
    ):
        return await get_or_build_interaction_context_material(
            event=event,
            plugin_context=plugin_context,
            interaction_config=interaction_config,
            build_config=build_config,
        )


def _describe_expression_request(req: PersonaExpressionRequest) -> str:
    if req.observed_text.strip():
        return "stream_reply"
    if req.source_text.strip():
        return "material_reply"
    return "direct_reply"


def _log_persona_prompt_size_diagnostics(event, req, render_result) -> None:
    raw_slot_sizes = render_result.metadata.get("prompt_slot_sizes", {})
    slot_sizes = raw_slot_sizes if isinstance(raw_slot_sizes, dict) else {}

    section_sizes = {
        "system": len(render_result.system_prompt or ""),
        "messages": _serialized_size(render_result.messages),
        "tool_schema": _serialized_size(render_result.tool_schema or []),
    }
    total_chars = sum(section_sizes.values())
    logger.info(
        "DIAG expression.prompt_size: platform_id=%s session_id=%s phase=%s total_chars=%s estimated_tokens=%s sections=%s slots=%s",
        event.get_platform_id(),
        event.session_id,
        _describe_expression_request(req),
        total_chars,
        math.ceil(total_chars / 4),
        section_sizes,
        dict(sorted(slot_sizes.items(), key=lambda item: item[1], reverse=True)),
    )


def _serialized_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(value or ""))


def _has_visible_reply_material(req: PersonaExpressionRequest) -> bool:
    return any(
        value.strip()
        for value in (
            req.source_text,
            req.delegated_task_summary,
            req.observed_text,
            req.total_text,
            req.pending_text,
        )
    )


def _build_failure_expression_request(
    req: PersonaExpressionRequest,
    error: InteractionExpressionError,
) -> PersonaExpressionRequest:
    message = " ".join(str(error).split())
    if len(message) > 2000:
        message = f"{message[:1997]}..."
    return replace(
        req,
        source_text=(
            "本轮模型调用已经失败。"
            f"可确认的错误原因：{message or error.reason}"
        ),
        preserve_facts=True,
        allow_empty=False,
    )


def _build_tool_loop_failure_material(error: object) -> str:
    message = " ".join(str(error or "").split())
    if len(message) > 1000:
        message = f"{message[:997]}..."
    return "Personal Expression 插件工具处理失败。可确认的错误原因：" + (
        message or "未知错误"
    )
