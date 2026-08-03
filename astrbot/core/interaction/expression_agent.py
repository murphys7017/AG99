from __future__ import annotations

import asyncio
import copy
import json
import math
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, replace
from typing import Any

try:
    from json_repair import repair_json
except ImportError:  # pragma: no cover - optional runtime dependency
    repair_json = None

from astrbot import logger
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import (
    TOOL_TARGET_PERSONAL_EXPRESSION,
    FunctionTool,
    ToolSet,
)
from astrbot.core.agent.tool_output_capture import (
    PersonaToolOutputAttachments,
    activate_persona_tool_output_attachments,
)
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.memory.history_source import extract_message_text
from astrbot.core.message.components import Plain
from astrbot.core.output_contract import CompiledOutputContract, OutputContract
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.plugin_runtime import (
    PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
    tool_supports_runtime_target,
)
from astrbot.core.prompt.builder import PromptContextBuilder
from astrbot.core.prompt.context_collect import resolve_toolset_for_target
from astrbot.core.prompt.render import (
    PROMPT_APPLY_RESULT_EXTRA_KEY,
    PromptRenderEngine,
    PromptRenderProfile,
    PromptTarget,
    apply_render_result_to_request,
)
from astrbot.core.prompt.structured_json import extract_json_object
from astrbot.core.provider import Provider, resolve_fallback_chat_providers
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.provider.modalities import (
    log_context_sanitize_stats,
    sanitize_contexts_by_modalities,
)
from astrbot.core.provider.request_media import normalize_provider_request_images
from astrbot.core.star.context import Context
from astrbot.core.star.star_handler import EventType

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
from .personal_expression_guard import (
    PREVIOUS_EXPRESSION_FINGERPRINT_METADATA_KEY,
    fingerprint_personal_expression,
)
from .prompt_support import (
    build_interaction_prompt_build_config,
    build_model_context_messages,
)
from .provider_resolution import resolve_interaction_chat_provider
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
    avoid_previous_reply: bool = False


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
        tool_execution_count: int = 0,
        prepared: Any | None = None,
    ) -> None:
        self.reason = reason
        self.tool_material = tool_material
        self.tool_execution_count = tool_execution_count
        self.prepared = prepared
        super().__init__(message or reason)


_MISSING_PROVIDER_REQUEST = object()
_MISSING_PROMPT_APPLY_RESULT = object()


@contextmanager
def _bind_persona_provider_request(event, provider_request: ProviderRequest) -> Iterator[None]:
    """Expose a branch-local request through the real plugin event.

    Existing plugins may validate the concrete ``AstrMessageEvent`` type. Keep
    that public contract intact while restoring the event's previous request as
    soon as the Persona lifecycle boundary exits.
    """

    previous = event.get_extra("provider_request", _MISSING_PROVIDER_REQUEST)
    event.set_extra("provider_request", provider_request)
    try:
        yield
    finally:
        if previous is not _MISSING_PROVIDER_REQUEST:
            event.set_extra("provider_request", previous)
            return
        extras = getattr(event, "_extras", None)
        if isinstance(extras, dict):
            extras.pop("provider_request", None)
        else:
            event.set_extra("provider_request", None)


@contextmanager
def _bind_persona_prompt_apply_result(event, apply_result: object) -> Iterator[None]:
    """Expose the current Persona render application only to its plugin hooks."""

    previous = event.get_extra(
        PROMPT_APPLY_RESULT_EXTRA_KEY,
        _MISSING_PROMPT_APPLY_RESULT,
    )
    event.set_extra(PROMPT_APPLY_RESULT_EXTRA_KEY, apply_result)
    try:
        yield
    finally:
        if previous is not _MISSING_PROMPT_APPLY_RESULT:
            event.set_extra(PROMPT_APPLY_RESULT_EXTRA_KEY, previous)
            return
        extras = getattr(event, "_extras", None)
        if isinstance(extras, dict):
            extras.pop(PROMPT_APPLY_RESULT_EXTRA_KEY, None)
        else:
            event.set_extra(PROMPT_APPLY_RESULT_EXTRA_KEY, None)


@dataclass(slots=True)
class _PreparedPersonaExpression:
    req: PersonaExpressionRequest
    render_result: Any
    provider_request: ProviderRequest
    rendered_provider_request: ProviderRequest
    run_context: ContextWrapper[Any]
    tool_material: str | None = None
    stopped: bool = False


class _PersonaExpressionToolHooks(BaseAgentRunHooks[AstrAgentContext]):
    """Expose official tool hooks for the Persona-only tool loop."""

    def __init__(self, event, provider_request: ProviderRequest) -> None:
        self._event = event
        self._provider_request = provider_request
        self.tool_execution_count = 0

    async def on_tool_start(
        self,
        run_context: ContextWrapper[AstrAgentContext],
        tool: FunctionTool,
        tool_args: dict | None,
    ) -> None:
        del run_context
        self.tool_execution_count += 1
        with _bind_persona_provider_request(self._event, self._provider_request):
            await call_event_hook(
                self._event,
                EventType.OnUsingLLMToolEvent,
                tool,
                tool_args,
            )

    async def on_tool_end(
        self,
        run_context: ContextWrapper[AstrAgentContext],
        tool: FunctionTool,
        tool_args: dict | None,
        tool_result,
    ) -> None:
        del run_context
        self._event.clear_result()
        with _bind_persona_provider_request(self._event, self._provider_request):
            await call_event_hook(
                self._event,
                EventType.OnLLMToolRespondEvent,
                tool,
                tool_args,
                tool_result,
            )


def _build_persona_hook_run_context(
    plugin_context: Context,
    event,
) -> ContextWrapper[AstrAgentContext]:
    """Build the official public hook context with the concrete event."""

    if isinstance(plugin_context, Context) and isinstance(event, AstrMessageEvent):
        return ContextWrapper(
            context=AstrAgentContext(context=plugin_context, event=event),
        )

    # Unit-level integrations use deliberately small event/context doubles.
    # Keep their public shape while always preserving the original event rather
    # than substituting a proxy object.
    agent_context = object.__new__(AstrAgentContext)
    object.__setattr__(agent_context, "context", plugin_context)
    object.__setattr__(agent_context, "event", event)
    object.__setattr__(agent_context, "extra", {})
    return ContextWrapper(context=agent_context)


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
        "你是 Personal Expression 的内部插件工具 Agent。\n"
        "当前阶段只负责判断并调用本次提供的插件工具，不负责生成用户可见回复。\n"
        "只在当前请求确实需要时调用工具；工具返回后用简洁文本整理已获得的事实。\n"
        "不需要工具时不要调用任何工具，直接输出 no_tool。\n"
        "不要调用 Core 工具、Skill、知识库或未提供的工具。"
    )


def build_persona_tool_loop_prompt() -> str:
    return (
        "检查当前用户输入是否需要调用已提供的 Personal Expression 插件工具。"
        "需要时完成工具调用并总结结果；不需要时只输出 no_tool。"
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
    prompt = "请按输出契约生成当前人格的用户可见回应，不要输出额外自由文本。"
    if req.avoid_previous_reply:
        prompt += (
            "\n这是自主表达。spoken_reply 不得重复 conversation history 中最近一条 "
            "assistant 回复；即使表达意图相近，也必须换用有实质差异的措辞和角度。"
        )
    return prompt


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
        attachment_capture = PersonaToolOutputAttachments()
        with activate_persona_tool_output_attachments(attachment_capture):
            return await self._generate_expression_with_attachments(
                event,
                plugin_context,
                interaction_config,
                req,
                attachment_capture,
            )

    async def _generate_expression_with_attachments(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        req: PersonaExpressionRequest,
        attachment_capture: PersonaToolOutputAttachments,
    ) -> PersonaExpressionResult:
        provider, provider_id = await resolve_interaction_chat_provider(
            event,
            plugin_context,
            interaction_config.expression_provider_id,
        )
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
                f"provider unavailable: provider_id={provider_id}",
            )
        candidates = ([provider] if provider is not None else []) + fallback_providers
        if not candidates:
            raise primary_error or InteractionExpressionError("provider_unavailable")

        last_error: InteractionExpressionError | None = primary_error
        prepared: _PreparedPersonaExpression | None = None
        for index, candidate in enumerate(candidates):
            if prepared is not None:
                candidate_request = prepared.req
            elif primary_error is not None:
                candidate_request = _build_failure_expression_request(
                    req,
                    primary_error,
                )
            else:
                candidate_request = req
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
                result = await self._generate_expression_with_provider(
                    event,
                    plugin_context,
                    interaction_config,
                    candidate,
                    req=candidate_request,
                    prepared=prepared,
                )
                result.metadata["persona_tool_attachments"] = attachment_capture.drain()
                return result
            except InteractionExpressionError as exc:
                last_error = exc
                if isinstance(exc.prepared, _PreparedPersonaExpression):
                    prepared = exc.prepared
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
        prepared: _PreparedPersonaExpression | None = None,
    ) -> PersonaExpressionResult:
        if prepared is not None:
            prepared = await self._prepare_fallback_persona_expression(
                event,
                plugin_context,
                interaction_config,
                provider,
                prepared,
            )
            return await self._complete_persona_expression(
                event,
                interaction_config,
                provider,
                prepared,
            )
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

        provider_request = build_prompt_render_provider_request(event, provider)
        provider_request.session_id = event.session_id
        prompt_apply_result = apply_render_result_to_request(
            render_result,
            provider_request,
        )

        # Preserve the first official lifecycle boundary before any Persona-only
        # tool work starts. Legacy plugins commonly use this hook for per-turn
        # state and must not be silently skipped by the Persona path.
        with (
            _bind_persona_provider_request(event, provider_request),
            _bind_persona_prompt_apply_result(event, prompt_apply_result),
        ):
            if await call_event_hook(
                event,
                EventType.OnWaitingLLMRequestEvent,
                execution_surface=PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
            ):
                return PersonaExpressionResult()

        toolset = ToolSet()
        if req.allow_plugin_tools and self._provider_supports_tool_calls(provider):
            toolset = await self._resolve_personal_expression_tools(
                event,
                plugin_context,
                interaction_config,
            )
            provider_request.func_tool = toolset

        output_contract = render_result.output_contract
        compiled_output_contract = render_result.compiled_output_contract
        provider_request.output_contract = output_contract
        provider_request.compiled_output_contract = compiled_output_contract
        rendered_provider_request = _snapshot_provider_request(provider_request)

        # This is the pre-tool preparation boundary. Legacy plugins may amend
        # ProviderRequest.func_tool here, so running the hook later would make
        # their changes invisible to the Persona tool loop.
        with (
            _bind_persona_provider_request(event, provider_request),
            _bind_persona_prompt_apply_result(event, prompt_apply_result),
        ):
            if await call_event_hook(
                event,
                EventType.OnLLMRequestEvent,
                provider_request,
                execution_surface=PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
            ):
                return PersonaExpressionResult()
        hooked_provider_request = _snapshot_provider_request(provider_request)
        provider_request.output_contract = output_contract
        provider_request.compiled_output_contract = compiled_output_contract

        tool_material: str | None = None
        if req.allow_plugin_tools and isinstance(provider_request.func_tool, ToolSet):
            filtered_tools = [
                tool
                for tool in provider_request.func_tool
                if tool_supports_runtime_target(
                    event,
                    tool,
                    TOOL_TARGET_PERSONAL_EXPRESSION,
                )
            ]
            toolset = (
                provider_request.func_tool
                if len(filtered_tools) == len(provider_request.func_tool)
                else ToolSet(filtered_tools)
            )
            provider_request.func_tool = toolset
            if toolset:
                await normalize_provider_request_images(provider_request)
                hooked_provider_request = _snapshot_provider_request(provider_request)
                try:
                    (
                        tool_result,
                        tool_execution_count,
                    ) = await self._run_persona_tool_loop(
                        event,
                        plugin_context,
                        interaction_config,
                        provider,
                        provider_request,
                        toolset,
                    )
                except InteractionExpressionError as exc:
                    # Tool work may already have happened. Convert its failure
                    # into final-expression material instead of restarting the
                    # whole Persona flow on a fallback provider.
                    if exc.tool_execution_count > 0:
                        tool_material = _build_tool_loop_failure_material(exc)
                else:
                    if tool_execution_count <= 0:
                        logger.info(
                            "DIAG expression.tool_loop_complete: platform_id=%s session_id=%s tool_executions=0 material_discarded=True",
                            event.get_platform_id(),
                            event.session_id,
                        )
                    else:
                        tool_material = (tool_result.completion_text or "").strip()
                        if tool_result.role == "err":
                            tool_material = _build_tool_loop_failure_material(
                                tool_material or "provider returned an error response"
                            )
                        elif not tool_material:
                            tool_material = _build_tool_loop_failure_material(
                                "provider returned empty material after tool execution"
                            )

        if tool_material:
            req = replace(
                req,
                source_text=tool_material,
                preserve_facts=True,
                allow_plugin_tools=False,
            )
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
            provider_request = build_prompt_render_provider_request(event, provider)
            provider_request.session_id = event.session_id
            apply_render_result_to_request(render_result, provider_request)
            output_contract = render_result.output_contract
            compiled_output_contract = render_result.compiled_output_contract
            provider_request.output_contract = output_contract
            provider_request.compiled_output_contract = compiled_output_contract
            rerendered_provider_request = _snapshot_provider_request(provider_request)
            self._apply_request_hook_mutations(
                rendered_provider_request,
                previous_request=hooked_provider_request,
                next_request=provider_request,
            )
            rendered_provider_request = rerendered_provider_request

        output_contract = render_result.output_contract
        compiled_output_contract = render_result.compiled_output_contract
        provider_request.output_contract = output_contract
        provider_request.compiled_output_contract = compiled_output_contract
        run_context = _build_persona_hook_run_context(
            plugin_context,
            event,
        )
        prepared = _PreparedPersonaExpression(
            req=req,
            render_result=render_result,
            provider_request=provider_request,
            rendered_provider_request=rendered_provider_request,
            run_context=run_context,
            tool_material=tool_material,
        )
        with _bind_persona_provider_request(event, provider_request):
            if await call_event_hook(
                event,
                EventType.OnAgentBeginEvent,
                run_context,
                execution_surface=PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
            ):
                return PersonaExpressionResult()
        return await self._complete_persona_expression(
            event,
            interaction_config,
            provider,
            prepared,
        )

    async def _prepare_fallback_persona_expression(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
        provider: Provider,
        previous: _PreparedPersonaExpression,
    ) -> _PreparedPersonaExpression:
        """Rebind an already-hooked expression request to a fallback provider."""
        turn_state = get_interaction_turn_state(event)
        if turn_state is not None:
            async with turn_state.lock:
                render_result = await self._prepare_render_result(
                    event,
                    plugin_context,
                    interaction_config,
                    provider,
                    req=previous.req,
                )
        else:
            render_result = await self._prepare_render_result(
                event,
                plugin_context,
                interaction_config,
                provider,
                req=previous.req,
            )

        provider_request = build_prompt_render_provider_request(event, provider)
        provider_request.session_id = event.session_id
        apply_render_result_to_request(render_result, provider_request)
        rendered_provider_request = _snapshot_provider_request(provider_request)
        self._apply_request_hook_mutations(
            previous.rendered_provider_request,
            previous_request=previous.provider_request,
            next_request=provider_request,
        )
        provider_request.provider = provider
        provider_request.output_contract = render_result.output_contract
        provider_request.compiled_output_contract = render_result.compiled_output_contract
        return _PreparedPersonaExpression(
            req=previous.req,
            render_result=render_result,
            provider_request=provider_request,
            rendered_provider_request=rendered_provider_request,
            run_context=_build_persona_hook_run_context(
                plugin_context,
                event,
            ),
            tool_material=previous.tool_material,
        )

    @staticmethod
    def _apply_request_hook_mutations(
        rendered_request: ProviderRequest,
        *,
        previous_request: ProviderRequest,
        next_request: ProviderRequest,
    ) -> None:
        """Carry official request-hook mutations across provider-specific rendering."""
        for field_info in fields(ProviderRequest):
            name = field_info.name
            if name in {
                "output_contract",
                "compiled_output_contract",
                # The final Persona expression deliberately does not re-enter
                # the tool loop after tool material has been collected.
                "func_tool",
            }:
                continue
            before_hook = getattr(rendered_request, name)
            after_hook = getattr(previous_request, name)
            if after_hook != before_hook:
                next_value = getattr(next_request, name)
                if name == "prompt":
                    setattr(
                        next_request,
                        name,
                        _merge_request_prompt_mutation(
                            before_hook,
                            after_hook,
                            next_value,
                        ),
                    )
                elif name in {
                    "contexts",
                    "extra_user_content_parts",
                    "image_urls",
                    "audio_urls",
                }:
                    setattr(
                        next_request,
                        name,
                        _merge_request_collection_mutation(
                            before_hook,
                            after_hook,
                            next_value,
                        ),
                    )
                else:
                    setattr(
                        next_request,
                        name,
                        _clone_request_mutation_value(after_hook),
                    )

    async def _complete_persona_expression(
        self,
        event,
        interaction_config: InteractionAgentConfig,
        provider: Provider,
        prepared: _PreparedPersonaExpression,
    ) -> PersonaExpressionResult:
        req = prepared.req
        render_result = prepared.render_result
        provider_request = prepared.provider_request
        tool_material = prepared.tool_material
        output_contract = provider_request.output_contract
        compiled_output_contract = provider_request.compiled_output_contract
        persona_effect_specs = render_result.metadata.get("persona_effect_specs", [])
        if not isinstance(persona_effect_specs, list):
            persona_effect_specs = []
        provider_config = getattr(provider, "provider_config", {})
        if not isinstance(provider_config, dict):
            provider_config = {}
        image_stats = await normalize_provider_request_images(provider_request)
        if image_stats.changed:
            logger.debug(
                "Persona ProviderRequest images normalized: platform_id=%s "
                "session_id=%s discovered=%s normalized=%s dropped=%s",
                event.get_platform_id(),
                event.session_id,
                image_stats.discovered,
                image_stats.normalized,
                image_stats.dropped,
            )
        logger.info(
            "DIAG expression.contract: platform_id=%s session_id=%s phase=%s provider_type=%s model=%s renderer=%s contract_mode=%s strategy=%s degraded=%s tool_name=%s",
            event.get_platform_id(),
            event.session_id,
            _describe_expression_request(req),
            provider_config.get("type", ""),
            provider.get_model() if callable(getattr(provider, "get_model", None)) else "",
            render_result.metadata.get("renderer"),
            output_contract.mode if isinstance(output_contract, OutputContract) else None,
            render_result.metadata.get("output_contract_strategy"),
            render_result.metadata.get("output_contract_degraded"),
            compiled_output_contract.tool_name
            if compiled_output_contract is not None
            else None,
        )
        _log_persona_prompt_size_diagnostics(event, req, render_result)
        model_contexts = build_model_context_messages(provider_request.contexts)
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
                    prompt=provider_request.prompt,
                    session_id=provider_request.session_id,
                    image_urls=provider_request.image_urls,
                    audio_urls=provider_request.audio_urls,
                    contexts=model_contexts,
                    system_prompt=provider_request.system_prompt,
                    model=provider_request.model,
                    extra_user_content_parts=provider_request.extra_user_content_parts,
                    temperature=interaction_config.expression_temperature,
                    tool_choice="required"
                    if _should_require_tool_choice(output_contract)
                    else "auto",
                    output_contract=output_contract,
                    compiled_output_contract=compiled_output_contract,
                ),
                timeout=interaction_config.expression_timeout,
            )
        except asyncio.TimeoutError:
            raise InteractionExpressionError(
                "timeout",
                tool_material=tool_material,
                prepared=prepared,
            ) from None
        except Exception as exc:  # noqa: BLE001
            raise InteractionExpressionError(
                "model_error",
                str(exc),
                tool_material=tool_material,
                prepared=prepared,
            ) from exc

        if llm_resp.role == "err":
            raise InteractionExpressionError(
                "model_error",
                llm_resp.completion_text or "provider returned an error response",
                tool_material=tool_material,
                prepared=prepared,
            )
        logger.info(
            "DIAG expression.response_shape: platform_id=%s session_id=%s phase=%s has_tool_calls=%s tool_names=%s text_length=%s",
            event.get_platform_id(),
            event.session_id,
            _describe_expression_request(req),
            bool(llm_resp.tools_call_args),
            list(llm_resp.tools_call_name),
            len((llm_resp.completion_text or "").strip()),
        )
        try:
            result = extract_persona_expression_result(
                llm_resp.completion_text,
                llm_response=llm_resp,
                output_contract=output_contract,
                compiled_output_contract=compiled_output_contract,
                effects=persona_effect_specs,
            )
        except InteractionExpressionError as exc:
            if tool_material and not exc.tool_material:
                exc.tool_material = tool_material
            exc.prepared = prepared
            raise
        previous_expression_fingerprint = render_result.metadata.get(
            PREVIOUS_EXPRESSION_FINGERPRINT_METADATA_KEY
        )
        if isinstance(previous_expression_fingerprint, str):
            result.metadata[PREVIOUS_EXPRESSION_FINGERPRINT_METADATA_KEY] = (
                previous_expression_fingerprint
            )
        logger.info(
            "DIAG expression.effect_calls: platform_id=%s session_id=%s phase=%s payload_present=%s effect_calls=%s effect_parse_issues=%s",
            event.get_platform_id(),
            event.session_id,
            _describe_expression_request(req),
            bool(result.effect_calls),
            [call.name for call in result.effect_calls],
            [
                {"name": str(issue.get("name", "")), "reason": str(issue.get("reason", ""))}
                for issue in result.metadata.get("effect_parse_issues", [])
                if isinstance(issue, dict)
            ],
        )
        try:
            validate_persona_expression_result(req, result)
        except InteractionExpressionError as exc:
            if tool_material and not exc.tool_material:
                exc.tool_material = tool_material
            exc.prepared = prepared
            raise
        hook_result_chain = (
            llm_resp.result_chain.derive(
                [
                    component
                    for component in llm_resp.result_chain.chain
                    if not isinstance(component, Plain)
                ]
            )
            if llm_resp.result_chain is not None
            else None
        )
        response_for_hooks = LLMResponse(
            role=llm_resp.role,
            result_chain=hook_result_chain,
            tools_call_args=list(llm_resp.tools_call_args),
            tools_call_name=list(llm_resp.tools_call_name),
            tools_call_ids=list(llm_resp.tools_call_ids),
            tools_call_extra_content=dict(llm_resp.tools_call_extra_content),
            reasoning_content=llm_resp.reasoning_content,
            reasoning_signature=llm_resp.reasoning_signature,
            raw_completion=llm_resp.raw_completion,
            is_chunk=llm_resp.is_chunk,
            id=llm_resp.id,
            usage=llm_resp.usage,
        )
        # Protocol tool-call responses often carry an empty MessageChain. Keep
        # the parsed Persona reply authoritative for both legacy response APIs.
        response_for_hooks.completion_text = result.spoken_reply
        with _bind_persona_provider_request(event, provider_request):
            response_stopped = await call_event_hook(
                event,
                EventType.OnLLMResponseEvent,
                response_for_hooks,
                execution_surface=PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
            )
            agent_done_stopped = await call_event_hook(
                event,
                EventType.OnAgentDoneEvent,
                prepared.run_context,
                response_for_hooks,
                execution_surface=PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
            )
        if response_stopped or agent_done_stopped:
            return PersonaExpressionResult()
        result.spoken_reply = str(response_for_hooks.completion_text or "")
        try:
            validate_persona_expression_result(req, result)
        except InteractionExpressionError as exc:
            if tool_material and not exc.tool_material:
                exc.tool_material = tool_material
            exc.prepared = prepared
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
        provider_request: ProviderRequest,
        toolset: ToolSet,
    ) -> tuple[LLMResponse, int]:
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
            "DIAG expression.tool_loop: platform_id=%s session_id=%s tool_count=%s tool_names=%s",
            event.get_platform_id(),
            event.session_id,
            len(toolset),
            toolset.names(),
        )
        tool_hooks = _PersonaExpressionToolHooks(event, provider_request)
        try:
            response = await asyncio.wait_for(
                plugin_context.tool_loop_agent(
                    event=event,
                    chat_provider_id=provider_id,
                    prompt=build_persona_tool_loop_prompt(),
                    image_urls=provider_request.image_urls,
                    audio_urls=provider_request.audio_urls,
                    extra_user_content_parts=provider_request.extra_user_content_parts,
                    model=provider_request.model,
                    contexts=build_model_context_messages(provider_request.contexts),
                    system_prompt=build_persona_tool_loop_instruction(),
                    tools=toolset,
                    agent_hooks=tool_hooks,
                    max_steps=8,
                    tool_call_timeout=max(
                        1,
                        int(interaction_config.expression_timeout),
                    ),
                    tool_execution_surface=TOOL_TARGET_PERSONAL_EXPRESSION,
                ),
                timeout=interaction_config.expression_timeout,
            )
            return response, tool_hooks.tool_execution_count
        except asyncio.TimeoutError:
            raise InteractionExpressionError(
                "tool_loop_timeout",
                tool_execution_count=tool_hooks.tool_execution_count,
            ) from None
        except InteractionExpressionError as exc:
            exc.tool_execution_count = max(
                exc.tool_execution_count,
                tool_hooks.tool_execution_count,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            raise InteractionExpressionError(
                "tool_loop_error",
                str(exc),
                tool_execution_count=tool_hooks.tool_execution_count,
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
            history_turns=interaction_config.persona_history_window_size,
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
        if req.avoid_previous_reply:
            previous_expression_fingerprint = (
                _latest_assistant_expression_fingerprint(expression_pack)
            )
            if previous_expression_fingerprint is not None:
                render_result.metadata[
                    PREVIOUS_EXPRESSION_FINGERPRINT_METADATA_KEY
                ] = previous_expression_fingerprint
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


def _latest_assistant_expression_fingerprint(pack) -> str | None:
    history_slot = pack.get_slot("conversation.history")
    if history_slot is None or not isinstance(history_slot.value, dict):
        return None
    turns = history_slot.value.get("turns")
    if not isinstance(turns, list):
        return None
    for turn in reversed(turns):
        if not isinstance(turn, dict):
            continue
        assistant_message = turn.get("assistant_message")
        if not isinstance(assistant_message, dict):
            continue
        fingerprint = fingerprint_personal_expression(
            extract_message_text(assistant_message)
        )
        if fingerprint is not None:
            return fingerprint
    return None


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


def _merge_request_prompt_mutation(
    rendered_value: object,
    hooked_value: object,
    rerendered_value: object,
) -> object:
    """Retain a hook's prompt change without dropping dynamic tool material."""

    if not all(isinstance(value, str) for value in (
        rendered_value,
        hooked_value,
        rerendered_value,
    )):
        return _clone_request_mutation_value(hooked_value)
    if rendered_value and rendered_value in hooked_value:
        return hooked_value.replace(rendered_value, rerendered_value, 1)
    if rerendered_value:
        return (
            f"{hooked_value}\n\n{rerendered_value}"
            if hooked_value
            else rerendered_value
        )
    return hooked_value


def _merge_request_collection_mutation(
    rendered_value: object,
    hooked_value: object,
    rerendered_value: object,
) -> object:
    """Keep hook-owned collection entries and the rerendered dynamic entries."""

    if not all(isinstance(value, list) for value in (
        rendered_value,
        hooked_value,
        rerendered_value,
    )):
        return _clone_request_mutation_value(hooked_value)

    merged = _clone_request_mutation_value(hooked_value)
    rendered_items = [_request_mutation_key(value) for value in rendered_value]
    merged_items = [_request_mutation_key(value) for value in merged]
    for value in rerendered_value:
        key = _request_mutation_key(value)
        if key not in rendered_items and key not in merged_items:
            merged.append(_clone_request_mutation_value(value))
            merged_items.append(key)
    return merged


def _snapshot_provider_request(request: ProviderRequest) -> ProviderRequest:
    """Snapshot hook-visible data without copying live execution handles."""

    snapshot = copy.copy(request)
    for field_info in fields(ProviderRequest):
        name = field_info.name
        setattr(
            snapshot,
            name,
            _clone_request_mutation_value(getattr(request, name)),
        )
    return snapshot


def _clone_request_mutation_value(value: object) -> Any:
    """Clone plain containers while retaining opaque plugin/runtime objects."""

    if isinstance(value, dict):
        return {
            key: _clone_request_mutation_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_clone_request_mutation_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_clone_request_mutation_value(item) for item in value)
    if isinstance(value, set):
        return {_clone_request_mutation_value(item) for item in value}
    return value


def _request_mutation_key(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)
