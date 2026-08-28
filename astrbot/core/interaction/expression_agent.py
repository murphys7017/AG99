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
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import (
    TOOL_TARGET_PERSONAL_EXPRESSION,
    ToolSet,
    normalize_tool_targets,
)
from astrbot.core.agent.tool_output_capture import (
    PersonaToolOutputAttachments,
    activate_persona_tool_output_attachments,
)
from astrbot.core.agent_lifecycle import (
    AgentRequestLifecycle,
    AgentRequestLifecycleHooks,
)
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.capabilities import CapabilityResolver, CapabilitySnapshot
from astrbot.core.deadline import TurnDeadlineExceeded
from astrbot.core.memory.history_source import extract_message_text
from astrbot.core.message.components import Plain
from astrbot.core.output_contract import CompiledOutputContract, OutputContract
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.plugin_runtime import (
    PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
)
from astrbot.core.prompt.builder import PromptContextBuilder
from astrbot.core.prompt.render import (
    PromptRenderEngine,
    PromptRenderProfile,
    PromptTarget,
    apply_render_result_to_request,
)
from astrbot.core.prompt.structured_json import extract_json_object
from astrbot.core.provider import Provider, resolve_fallback_chat_providers
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.provider.request_media import normalize_provider_request_images
from astrbot.core.speech_cues import (
    SpeechCue,
    build_speech_cue_guidance,
    build_speech_cue_schema,
    normalize_speech_cues,
)
from astrbot.core.star.context import Context
from astrbot.core.star.star_handler import EventType

from .collectors import PersonaVisibleReplyCollector
from .context_builder import (
    build_prompt_render_provider_request,
    get_or_build_interaction_context_material,
    get_or_build_interaction_persona_context_pack,
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
)
from .provider_resolution import resolve_interaction_chat_provider
from .turn_state import (
    get_interaction_turn_deadline,
    get_interaction_turn_state,
    set_interaction_turn_persona_id,
)
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
    speech_cues: list[SpeechCue] = field(default_factory=list)


class InteractionExpressionError(RuntimeError):
    def __init__(
        self,
        reason: str,
        message: str | None = None,
        *,
        tool_execution_count: int = 0,
        prepared: Any | None = None,
    ) -> None:
        self.reason = reason
        self.tool_execution_count = tool_execution_count
        self.prepared = prepared
        super().__init__(message or reason)


@dataclass(slots=True)
class _PreparedPersonaExpression:
    req: PersonaExpressionRequest
    render_result: Any
    provider_request: ProviderRequest
    run_context: ContextWrapper[AstrAgentContext]
    capabilities: CapabilitySnapshot
    lifecycle: AgentRequestLifecycle
    tool_execution_count: int = 0
    stopped: bool = False


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
        "必须按本次输出契约返回只包含 spoken_reply、speech_cues 与 effect_calls 的结构化结果。\n"
        "支持协议级 tool call 时，使用 persona_expression 工具承载结构化结果。\n"
        f"{build_speech_cue_guidance()}\n"
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
        "speech_cues": build_speech_cue_schema(),
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
        required_per_segment = sum(
            1
            for effect in enabled_effects
            if isinstance(effect.metadata, dict)
            and effect.metadata.get("required_per_segment") is True
        )
        if required_per_segment:
            properties["effect_calls"]["minItems"] = required_per_segment
            if required_per_segment == 1 and len(enabled_effects) == 1 and any(
                isinstance(effect.metadata, dict)
                and effect.metadata.get("exactly_one_per_segment") is True
                for effect in enabled_effects
            ):
                properties["effect_calls"]["maxItems"] = 1

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": copy.deepcopy(properties),
        "required": ["spoken_reply", "speech_cues", "effect_calls"],
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
    speech_cues = _coerce_json_like(normalized.get("speech_cues", []))
    if isinstance(speech_cues, list):
        normalized["speech_cues"] = speech_cues
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
    speech_cues, speech_cue_issues = normalize_speech_cues(
        payload.get("speech_cues", []),
    )
    metadata = _coerce_mapping_dict(payload.get("metadata"))
    if effect_issues:
        metadata["effect_parse_issues"] = [issue.to_dict() for issue in effect_issues]
    if speech_cue_issues:
        metadata["speech_cue_parse_issues"] = speech_cue_issues
    return PersonaExpressionResult(
        spoken_reply=str(payload.get("spoken_reply", "") or ""),
        speech_cues=speech_cues,
        effect_calls=effect_calls,
        metadata=metadata,
    )


def _normalize_result_speech_cues(result: PersonaExpressionResult) -> None:
    speech_cues, issues = normalize_speech_cues(result.speech_cues)
    result.speech_cues = speech_cues
    if not issues:
        return
    if not isinstance(result.metadata, dict):
        result.metadata = {}
    existing = result.metadata.get("speech_cue_parse_issues", [])
    result.metadata["speech_cue_parse_issues"] = [
        *(existing if isinstance(existing, list) else []),
        *issues,
    ]


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
    protocol_tool_call_required = strict_tool_call and not (
        isinstance(compiled_output_contract, CompiledOutputContract)
        and compiled_output_contract.strategy == "prompt_only"
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
        deadline = get_interaction_turn_deadline(event)
        try:
            timeout_context = (
                deadline.enforce(
                    "persona_expression",
                    interaction_config.expression_timeout,
                )
                if deadline is not None
                else asyncio.timeout(interaction_config.expression_timeout)
            )
            async with timeout_context:
                return await self._generate_expression_with_provider_candidates(
                    event,
                    plugin_context,
                    interaction_config,
                    req,
                    attachment_capture,
                )
        except TurnDeadlineExceeded:
            raise
        except TimeoutError:
            raise InteractionExpressionError("timeout") from None

    async def _generate_expression_with_provider_candidates(
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
                    "Persona expression switched to fallback provider: platform_id=%s session_id=%s lifecycle_id=%s provider_id=%s primary_error=%s",
                    event.get_platform_id(),
                    event.session_id,
                    prepared.lifecycle.lifecycle_id if prepared is not None else "",
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
                if exc.tool_execution_count > 0:
                    break
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
        lifecycle = AgentRequestLifecycle(
            event,
            execution_surface=PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
            provider_request=provider_request,
            prompt_apply_result=prompt_apply_result,
            hook_dispatcher=call_event_hook,
        )

        # Preserve the official lifecycle boundary before Persona generation.
        # Legacy plugins commonly use this hook for per-turn state and must not
        # be silently skipped by the Persona path.
        if await lifecycle.dispatch_waiting():
            return PersonaExpressionResult()

        capabilities = CapabilitySnapshot.empty(
            target=TOOL_TARGET_PERSONAL_EXPRESSION,
        )
        if req.allow_plugin_tools and self._provider_supports_tool_calls(provider):
            capabilities = await self._resolve_personal_expression_capabilities(
                event,
                plugin_context,
                interaction_config,
            )
            provider_request.func_tool = capabilities.to_toolset()
        initial_tool_signature = _toolset_capability_signature(
            provider_request.func_tool
        )

        output_contract = render_result.output_contract
        compiled_output_contract = render_result.compiled_output_contract
        provider_request.output_contract = output_contract
        provider_request.compiled_output_contract = compiled_output_contract

        # Request hooks run once before the shared Persona agent starts. Their
        # ordinary ProviderRequest mutations remain visible throughout the
        # business-tool loop and final structured expression.
        if await lifecycle.dispatch_request():
            return PersonaExpressionResult()
        provider_request.output_contract = output_contract
        provider_request.compiled_output_contract = compiled_output_contract

        if req.allow_plugin_tools and isinstance(provider_request.func_tool, ToolSet):
            if (
                _toolset_capability_signature(provider_request.func_tool)
                != initial_tool_signature
            ):
                capabilities = CapabilityResolver().resolve_explicit_toolset(
                    event=event,
                    target=TOOL_TARGET_PERSONAL_EXPRESSION,
                    toolset=provider_request.func_tool,
                    persona_id=capabilities.persona_id,
                    selection_mode="request_hook",
                )
            provider_request.func_tool = capabilities.to_toolset()
        else:
            provider_request.func_tool = ToolSet()

        run_context = _build_persona_hook_run_context(
            plugin_context,
            event,
        )
        prepared = _PreparedPersonaExpression(
            req=req,
            render_result=render_result,
            provider_request=provider_request,
            run_context=run_context,
            capabilities=capabilities,
            lifecycle=lifecycle,
        )
        if await lifecycle.dispatch_agent_begin(run_context):
            return PersonaExpressionResult()
        return await self._complete_persona_expression(
            event,
            interaction_config,
            provider,
            prepared,
        )

    async def _prepare_fallback_persona_expression(
        self,
        provider: Provider,
        previous: _PreparedPersonaExpression,
    ) -> _PreparedPersonaExpression:
        """Rebind one frozen, already-hooked request to a fallback provider."""
        provider_request = previous.provider_request
        terminal_tool_name = _resolve_terminal_tool_name(
            provider_request.output_contract,
            provider_request.compiled_output_contract,
        )
        if terminal_tool_name and not self._provider_supports_tool_calls(provider):
            raise InteractionExpressionError(
                "fallback_provider_incompatible",
                "fallback provider does not support the required Persona tool contract",
                prepared=previous,
            )
        provider_request.provider = provider
        provider_request.func_tool = (
            previous.capabilities.to_toolset()
            if self._provider_supports_tool_calls(provider)
            else ToolSet()
        )
        previous.lifecycle.bind_request(provider_request)
        return previous

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
            "DIAG expression.contract: platform_id=%s session_id=%s phase=%s lifecycle_id=%s provider_type=%s model=%s renderer=%s contract_mode=%s strategy=%s degraded=%s tool_name=%s",
            event.get_platform_id(),
            event.session_id,
            _describe_expression_request(req),
            prepared.lifecycle.lifecycle_id,
            provider_config.get("type", ""),
            provider.get_model()
            if callable(getattr(provider, "get_model", None))
            else "",
            render_result.metadata.get("renderer"),
            output_contract.mode
            if isinstance(output_contract, OutputContract)
            else None,
            render_result.metadata.get("output_contract_strategy"),
            render_result.metadata.get("output_contract_degraded"),
            compiled_output_contract.tool_name
            if compiled_output_contract is not None
            else None,
        )
        _log_persona_prompt_size_diagnostics(
            event,
            req,
            render_result,
            provider_request,
            prepared.lifecycle.lifecycle_id,
        )
        try:
            llm_resp, tool_execution_count = await self._run_persona_agent(
                event,
                interaction_config,
                provider,
                prepared,
            )
            prepared.tool_execution_count = tool_execution_count
        except InteractionExpressionError as exc:
            exc.prepared = prepared
            prepared.tool_execution_count = max(
                prepared.tool_execution_count,
                exc.tool_execution_count,
            )
            raise
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
            exc.tool_execution_count = prepared.tool_execution_count
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
                {
                    "name": str(issue.get("name", "")),
                    "reason": str(issue.get("reason", "")),
                }
                for issue in result.metadata.get("effect_parse_issues", [])
                if isinstance(issue, dict)
            ],
        )
        logger.info(
            "DIAG expression.speech_cues: platform_id=%s session_id=%s phase=%s cue_count=%s cue_kinds=%s cue_parse_issues=%s",
            event.get_platform_id(),
            event.session_id,
            _describe_expression_request(req),
            len(result.speech_cues),
            [cue.kind for cue in result.speech_cues],
            result.metadata.get("speech_cue_parse_issues", []),
        )
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
        if await prepared.lifecycle.dispatch_agent_done(
            prepared.run_context,
            response_for_hooks,
        ):
            return PersonaExpressionResult()
        result.spoken_reply = str(response_for_hooks.completion_text or "")
        # Persona result hooks belong to this isolated request lifecycle. Keep
        # the lifecycle overlay active so a stop on the parent event (for
        # example, a user interrupt) is not mistaken for a hook-local stop.
        with prepared.lifecycle.expose_request():
            hook_stopped = await call_event_hook(
                event,
                EventType.OnPersonaExpressionResultEvent,
                result,
                execution_surface=PLUGIN_RUNTIME_TARGET_PERSONAL_EXPRESSION,
            )
        if hook_stopped:
            return PersonaExpressionResult()
        _normalize_result_speech_cues(result)
        try:
            validate_persona_expression_result(req, result)
        except InteractionExpressionError as exc:
            exc.tool_execution_count = prepared.tool_execution_count
            exc.prepared = prepared
            raise
        if req.short_reply and result.spoken_reply and len(result.spoken_reply) > 40:
            result.spoken_reply = result.spoken_reply[:40].rstrip("，,。.!！?？")
        logger.info(
            "Persona expression generated: turn_id=%s target=persona_expression "
            "platform_id=%s session_id=%s phase=%s lifecycle_id=%s length=%s "
            "speech_cues=%s effect_calls=%s",
            str(event.get_extra("_turn_id", "") or ""),
            event.get_platform_id(),
            event.session_id,
            _describe_expression_request(req),
            prepared.lifecycle.lifecycle_id,
            len(result.spoken_reply),
            [cue.kind for cue in result.speech_cues],
            [call.name for call in result.effect_calls],
        )
        return result

    async def _run_persona_agent(
        self,
        event,
        interaction_config: InteractionAgentConfig,
        provider: Provider,
        prepared: _PreparedPersonaExpression,
    ) -> tuple[LLMResponse, int]:
        provider_request = prepared.provider_request
        terminal_tool_name = _resolve_terminal_tool_name(
            provider_request.output_contract,
            provider_request.compiled_output_contract,
        )
        terminal_tool_names = (
            {terminal_tool_name} if terminal_tool_name is not None else set()
        )
        prepared.run_context.tool_call_timeout = max(
            1,
            int(interaction_config.expression_timeout),
        )
        prepared.run_context.tool_execution_surface = TOOL_TARGET_PERSONAL_EXPRESSION

        logger.info(
            "DIAG expression.agent_loop: platform_id=%s session_id=%s lifecycle_id=%s tool_count=%s tool_names=%s terminal_tool=%s",
            event.get_platform_id(),
            event.session_id,
            prepared.lifecycle.lifecycle_id,
            len(provider_request.func_tool or ToolSet()),
            (provider_request.func_tool or ToolSet()).names(),
            terminal_tool_name,
        )
        runner = ToolLoopAgentRunner[AstrAgentContext]()
        await runner.reset(
            provider=provider,
            request=provider_request,
            run_context=prepared.run_context,
            tool_executor=FunctionToolExecutor(),
            agent_hooks=AgentRequestLifecycleHooks(
                prepared.lifecycle,
                dispatch_agent_stages=False,
            ),
            streaming=False,
            terminal_tool_names=terminal_tool_names,
            provider_kwargs={
                "temperature": interaction_config.expression_temperature,
            },
            deadline=get_interaction_turn_deadline(event),
        )
        try:
            async for _ in runner.step_until_done(8):
                pass
        except TurnDeadlineExceeded:
            raise
        except TimeoutError:
            raise InteractionExpressionError(
                "timeout",
                tool_execution_count=prepared.lifecycle.tool_execution_count,
            ) from None
        except InteractionExpressionError as exc:
            exc.tool_execution_count = max(
                exc.tool_execution_count,
                prepared.lifecycle.tool_execution_count,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            raise InteractionExpressionError(
                "model_error",
                str(exc),
                tool_execution_count=prepared.lifecycle.tool_execution_count,
            ) from exc

        llm_resp = runner.get_final_llm_resp()
        if llm_resp is None:
            raise InteractionExpressionError(
                "model_error",
                "persona agent did not produce a final LLM response",
                tool_execution_count=prepared.lifecycle.tool_execution_count,
            )
        if llm_resp.role == "err":
            raise InteractionExpressionError(
                "model_error",
                llm_resp.completion_text or "provider returned an error response",
                tool_execution_count=prepared.lifecycle.tool_execution_count,
            )
        return llm_resp, prepared.lifecycle.tool_execution_count

    async def _resolve_personal_expression_capabilities(
        self,
        event,
        plugin_context: Context,
        interaction_config: InteractionAgentConfig,
    ) -> CapabilitySnapshot:
        build_config = build_interaction_prompt_build_config(plugin_context, event)
        return await CapabilityResolver().resolve(
            event=event,
            plugin_context=plugin_context,
            config=build_config,
            target=TOOL_TARGET_PERSONAL_EXPRESSION,
            provider_request=None,
        )

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
        persona_context_pack = await get_or_build_interaction_persona_context_pack(
            event=event,
            plugin_context=plugin_context,
            interaction_config=interaction_config,
            build_config=build_config,
            material=material,
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
            base=persona_context_pack,
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
        if req.avoid_previous_reply:
            previous_expression_fingerprint = _latest_assistant_expression_fingerprint(
                expression_pack
            )
            if previous_expression_fingerprint is not None:
                render_result.metadata[PREVIOUS_EXPRESSION_FINGERPRINT_METADATA_KEY] = (
                    previous_expression_fingerprint
                )
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


def _log_persona_prompt_size_diagnostics(
    event,
    req,
    render_result,
    provider_request: ProviderRequest,
    lifecycle_id: str,
) -> None:
    raw_slot_sizes = render_result.metadata.get("prompt_slot_sizes", {})
    slot_sizes = raw_slot_sizes if isinstance(raw_slot_sizes, dict) else {}

    toolset = (
        provider_request.func_tool
        if isinstance(provider_request.func_tool, ToolSet)
        else ToolSet()
    )
    business_tool_schema = toolset.openai_schema()
    compiled_contract = provider_request.compiled_output_contract
    terminal_tool_schema = []
    if (
        isinstance(compiled_contract, CompiledOutputContract)
        and compiled_contract.strategy == "protocol_tool_call"
        and compiled_contract.tool_name
    ):
        terminal_tool_schema = [
            {
                "type": "function",
                "function": {
                    "name": compiled_contract.tool_name,
                    "parameters": compiled_contract.tool_schema or {},
                },
            }
        ]
    effective_tool_schema = [*business_tool_schema, *terminal_tool_schema]

    section_sizes = {
        "system": len(provider_request.system_prompt or ""),
        "messages": _serialized_size(provider_request.contexts or []),
        "request": _serialized_size(
            {
                "prompt": provider_request.prompt,
                "extra_user_content_parts": provider_request.extra_user_content_parts,
            }
        ),
        "tool_schema": _serialized_size(effective_tool_schema),
    }
    total_chars = sum(section_sizes.values())
    context_budgets = render_result.metadata.get("context_budgets")
    if isinstance(context_budgets, dict):
        context_budgets["tool_schema"] = {
            "original_amount": len(effective_tool_schema),
            "retained_amount": len(effective_tool_schema),
            "original_estimated_tokens": math.ceil(section_sizes["tool_schema"] / 4),
            "retained_estimated_tokens": math.ceil(section_sizes["tool_schema"] / 4),
            "limit_amount": None,
            "limit_estimated_tokens": None,
            "truncated": False,
            "truncation_reasons": ["capability_snapshot_selection"],
            "enforced": False,
        }
    logger.info(
        "DIAG expression.prompt_size: platform_id=%s session_id=%s phase=%s lifecycle_id=%s total_chars=%s estimated_tokens=%s sections=%s tool_count=%s tool_names=%s slots=%s",
        event.get_platform_id(),
        event.session_id,
        _describe_expression_request(req),
        lifecycle_id,
        total_chars,
        math.ceil(total_chars / 4),
        section_sizes,
        len(effective_tool_schema),
        [
            *toolset.names(),
            *([compiled_contract.tool_name] if terminal_tool_schema else []),
        ],
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
            f"本轮模型调用已经失败。可确认的错误原因：{message or error.reason}"
        ),
        preserve_facts=True,
        allow_empty=False,
    )


def _resolve_terminal_tool_name(
    output_contract: OutputContract | None,
    compiled_output_contract: CompiledOutputContract | None,
) -> str | None:
    """Resolve the protocol tool that terminates the Persona agent loop."""

    if isinstance(compiled_output_contract, CompiledOutputContract):
        if compiled_output_contract.strategy != "protocol_tool_call":
            return None
        return str(compiled_output_contract.tool_name or "").strip() or None
    if not isinstance(output_contract, OutputContract):
        return None
    if output_contract.mode != "tool_call":
        return None
    return str(output_contract.preferred_tool_name or "").strip() or None


def _toolset_capability_signature(toolset: object) -> tuple[tuple[object, ...], ...]:
    """Detect material request-hook changes without re-resolving unchanged tools."""
    if not isinstance(toolset, ToolSet):
        return ()
    return tuple(
        (
            id(tool),
            str(getattr(tool, "name", "") or ""),
            bool(getattr(tool, "active", True)),
            str(getattr(tool, "handler_module_path", "") or ""),
            str(getattr(tool, "description", "") or ""),
            json.dumps(
                getattr(tool, "parameters", None),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
            tuple(
                sorted(normalize_tool_targets(getattr(tool, "execution_targets", None)))
            ),
        )
        for tool in toolset
    )
