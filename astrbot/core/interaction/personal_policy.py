from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from astrbot.core.output_contract import CompiledOutputContract, OutputContract
from astrbot.core.platform.message_type import MessageType
from astrbot.core.prompt.builder import PromptContextBuilder
from astrbot.core.prompt.collectors import (
    ConversationHistoryCollector,
    MemoryCollector,
    PersonaCollector,
    RuntimeContextCollector,
)
from astrbot.core.prompt.render import (
    PromptRenderEngine,
    PromptRenderProfile,
    PromptTarget,
)
from astrbot.core.prompt.structured_json import extract_json_object
from astrbot.core.provider import Provider
from astrbot.core.provider.entities import ProviderRequest

from .observation_inbox import ObservationBatch
from .personal_gate import ObservationGateResult, ObservationGateSettings
from .personal_state import PersonalStateSnapshot
from .prompt_support import (
    build_interaction_prompt_build_config,
    build_model_context_messages,
)
from .runtime_context_projection import project_observation_batch
from .types import InteractionAgentConfig

if TYPE_CHECKING:
    from astrbot.core.star.context import Context

    from .personal_runtime import PersonalRuntimeKey


_MODEL_REASON_CODES = (
    "explicit_summon",
    "follow_up_opportunity",
    "pending_commitment",
    "social_opportunity",
    "meaningful_activity",
    "insufficient_value",
    "needs_more_context",
)


class PersonalPolicyAction(str, Enum):
    IGNORE = "ignore"
    OBSERVE = "observe"
    EXPRESS = "express"
    DEFER = "defer"


class PersonalPolicyReason(str, Enum):
    EXPLICIT_SUMMON = "explicit_summon"
    FOLLOW_UP_OPPORTUNITY = "follow_up_opportunity"
    PENDING_COMMITMENT = "pending_commitment"
    SOCIAL_OPPORTUNITY = "social_opportunity"
    MEANINGFUL_ACTIVITY = "meaningful_activity"
    INSUFFICIENT_VALUE = "insufficient_value"
    NEEDS_MORE_CONTEXT = "needs_more_context"
    POLICY_FAILURE = "policy_failure"


class PersonalPolicyEvaluationStatus(str, Enum):
    EVALUATED = "evaluated"
    FAIL_CLOSED = "fail_closed"


@dataclass(frozen=True, slots=True)
class PersonalPolicyDecision:
    action: PersonalPolicyAction
    reason_code: PersonalPolicyReason
    reply_intent: str
    importance: float
    defer_seconds: int

    @classmethod
    def from_mapping(cls, payload: object) -> PersonalPolicyDecision | None:
        if not isinstance(payload, Mapping):
            return None
        required_fields = {
            "action",
            "reason_code",
            "reply_intent",
            "importance",
            "defer_seconds",
        }
        if set(payload) != required_fields:
            return None
        try:
            action = PersonalPolicyAction(str(payload["action"]))
            reason = PersonalPolicyReason(str(payload["reason_code"]))
        except ValueError:
            return None
        if reason is PersonalPolicyReason.POLICY_FAILURE:
            return None

        reply_intent = payload["reply_intent"]
        importance = payload["importance"]
        defer_seconds = payload["defer_seconds"]
        if not isinstance(reply_intent, str):
            return None
        if isinstance(importance, bool) or not isinstance(importance, int | float):
            return None
        if isinstance(defer_seconds, bool) or not isinstance(defer_seconds, int):
            return None
        normalized_reply = reply_intent.strip()
        normalized_importance = float(importance)
        if not 0.0 <= normalized_importance <= 1.0:
            return None
        if not 0 <= defer_seconds <= 86400:
            return None

        if action is PersonalPolicyAction.EXPRESS:
            valid_shape = bool(normalized_reply) and defer_seconds == 0
        elif action is PersonalPolicyAction.DEFER:
            valid_shape = not normalized_reply and defer_seconds > 0
        else:
            valid_shape = not normalized_reply and defer_seconds == 0
        if not valid_shape:
            return None
        return cls(
            action=action,
            reason_code=reason,
            reply_intent=normalized_reply,
            importance=normalized_importance,
            defer_seconds=defer_seconds,
        )

    @classmethod
    def fail_closed(cls) -> PersonalPolicyDecision:
        return cls(
            action=PersonalPolicyAction.OBSERVE,
            reason_code=PersonalPolicyReason.POLICY_FAILURE,
            reply_intent="",
            importance=0.0,
            defer_seconds=0,
        )


@dataclass(frozen=True, slots=True)
class PersonalPolicyEvaluation:
    batch_id: str
    status: PersonalPolicyEvaluationStatus
    decision: PersonalPolicyDecision
    evaluated_at: float
    provider_id: str
    provider_call_started: bool
    failure_code: str | None = None
    selected_slot_names: tuple[str, ...] = ()

    @classmethod
    def fail_closed(
        cls,
        *,
        batch_id: str,
        evaluated_at: float,
        provider_id: str,
        failure_code: str,
        provider_call_started: bool = False,
        selected_slot_names: tuple[str, ...] = (),
    ) -> PersonalPolicyEvaluation:
        return cls(
            batch_id=batch_id,
            status=PersonalPolicyEvaluationStatus.FAIL_CLOSED,
            decision=PersonalPolicyDecision.fail_closed(),
            evaluated_at=evaluated_at,
            provider_id=provider_id,
            provider_call_started=provider_call_started,
            failure_code=failure_code,
            selected_slot_names=selected_slot_names,
        )


class PersonalPolicyError(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def build_personal_policy_system_prompt() -> str:
    return (
        "你是 Personal Policy，一个持续人格运行时的后台行动决策器。\n"
        "你只判断当前 ObservationBatch 是否值得形成后续行动，不生成用户可见回复，不调用工具。\n"
        "动作定义：\n"
        "- ignore：事实没有持续价值，直接忽略。\n"
        "- observe：事实值得记住或影响状态，但现在不需要行动。\n"
        "- express：值得主动表达；reply_intent 只写表达意图，不写最终台词。\n"
        "- defer：需要等待更多事实；填写 defer_seconds。\n"
        "字段约束：ignore/observe 的 reply_intent 必须为空且 defer_seconds=0；"
        "express 的 reply_intent 必须非空且 defer_seconds=0；"
        "defer 的 reply_intent 必须为空且 defer_seconds>0。"
        "不要使用 reply_intent 解释 ignore、observe 或 defer 的理由。\n"
        "reason_code 只能从以下值选择："
        + ", ".join(_MODEL_REASON_CODES)
        + "。\n"
        "人格摘要只用于判断表达边界，不要进入角色扮演。"
        "历史和 Memory 只帮助理解，不能单独制造行动。\n"
        "若最近 assistant 已表达相同意图，且当前 ObservationBatch 没有新增或变化的事实，"
        "必须 ignore 或 observe，不得再次 express。Heartbeat 只表示到了评估时点，"
        "不等于对话事实发生变化。显式配置的 idle_initiation 则表示用户在一次真实互动后"
        "持续空闲，最多可作为一次谨慎开启新话题的事实；仍应优先考虑近期上下文、冷却和事实价值。\n"
        "不要输出思考过程、最终文案、工具参数、effect 或未提供的事实。"
    )


def build_personal_policy_prompt() -> str:
    return "评估当前运行时事实，并严格按 personal_policy_decision 输出契约返回。"


def build_personal_policy_output_contract() -> OutputContract:
    return OutputContract(
        mode="tool_call",
        strict=True,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [action.value for action in PersonalPolicyAction],
                },
                "reason_code": {
                    "type": "string",
                    "enum": list(_MODEL_REASON_CODES),
                },
                "reply_intent": {
                    "type": "string",
                    "description": "仅 action=express 时填写；其他动作必须为空字符串",
                },
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
                "defer_seconds": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 86400,
                    "description": "仅 action=defer 时为正整数；其他动作必须为 0",
                },
            },
            "required": [
                "action",
                "reason_code",
                "reply_intent",
                "importance",
                "defer_seconds",
            ],
        },
        preferred_tool_name="personal_policy_decision",
        allow_text_fallback=False,
    )


def extract_personal_policy_decision(
    llm_response,
    output_contract: OutputContract,
    compiled_output_contract: CompiledOutputContract,
) -> PersonalPolicyDecision:
    if (
        compiled_output_contract.strategy != "protocol_tool_call"
        or compiled_output_contract.degraded
    ):
        raise PersonalPolicyError("unsupported_policy_tool_call")
    preferred_name = output_contract.preferred_tool_name
    matched_tool_call = False
    for tool_name, tool_arg in zip(
        list(getattr(llm_response, "tools_call_name", []) or []),
        list(getattr(llm_response, "tools_call_args", []) or []),
        strict=False,
    ):
        if preferred_name and tool_name != preferred_name:
            continue
        matched_tool_call = True
        payload = tool_arg if isinstance(tool_arg, dict) else extract_json_object(tool_arg)
        decision = PersonalPolicyDecision.from_mapping(payload)
        if decision is not None:
            return decision
    if matched_tool_call:
        raise PersonalPolicyError("invalid_policy_tool_call")
    raise PersonalPolicyError("missing_policy_tool_call")


class PersonalPolicyAgent:
    async def evaluate(
        self,
        *,
        runtime_key: PersonalRuntimeKey,
        batch: ObservationBatch,
        gate_result: ObservationGateResult,
        state: PersonalStateSnapshot,
        gate_settings: ObservationGateSettings,
        plugin_context: Context,
        runtime_config: Mapping[str, Any],
        interaction_config: InteractionAgentConfig,
        on_provider_call_started: Callable[[], Awaitable[None]],
    ) -> PersonalPolicyEvaluation | None:
        if not interaction_config.personal_policy_enabled:
            return None
        provider_id = interaction_config.personal_policy_provider_id.strip()
        if not provider_id:
            return PersonalPolicyEvaluation.fail_closed(
                batch_id=batch.batch_id,
                evaluated_at=gate_result.evaluated_at,
                provider_id="",
                failure_code="provider_not_configured",
            )
        try:
            provider = plugin_context.get_provider_by_id(provider_id)
        except Exception:
            provider = None
        if not isinstance(provider, Provider):
            return PersonalPolicyEvaluation.fail_closed(
                batch_id=batch.batch_id,
                evaluated_at=gate_result.evaluated_at,
                provider_id=provider_id,
                failure_code="provider_unavailable",
            )

        try:
            render_result = await self._prepare_render_result(
                runtime_key=runtime_key,
                batch=batch,
                gate_result=gate_result,
                state=state,
                gate_settings=gate_settings,
                plugin_context=plugin_context,
                runtime_config=runtime_config,
                provider=provider,
            )
        except Exception:
            return PersonalPolicyEvaluation.fail_closed(
                batch_id=batch.batch_id,
                evaluated_at=gate_result.evaluated_at,
                provider_id=provider_id,
                failure_code="prompt_build_failed",
            )

        contract = render_result.output_contract
        compiled = render_result.compiled_output_contract
        slot_names = _selected_slot_names(render_result.metadata)
        if not isinstance(contract, OutputContract) or not isinstance(
            compiled, CompiledOutputContract
        ):
            return PersonalPolicyEvaluation.fail_closed(
                batch_id=batch.batch_id,
                evaluated_at=gate_result.evaluated_at,
                provider_id=provider_id,
                failure_code="unsupported_output_contract",
                selected_slot_names=slot_names,
            )
        if compiled.strategy != "protocol_tool_call" or compiled.degraded:
            return PersonalPolicyEvaluation.fail_closed(
                batch_id=batch.batch_id,
                evaluated_at=gate_result.evaluated_at,
                provider_id=provider_id,
                failure_code="unsupported_policy_tool_call",
                selected_slot_names=slot_names,
            )
        try:
            validated_contract = provider.ensure_output_contract_supported(
                output_contract=contract,
                compiled_output_contract=compiled,
                allow_prompt_only_degrade=False,
            )
        except Exception:
            return PersonalPolicyEvaluation.fail_closed(
                batch_id=batch.batch_id,
                evaluated_at=gate_result.evaluated_at,
                provider_id=provider_id,
                failure_code="unsupported_policy_tool_call",
                selected_slot_names=slot_names,
            )
        if (
            not isinstance(validated_contract, CompiledOutputContract)
            or validated_contract.strategy != "protocol_tool_call"
            or validated_contract.degraded
        ):
            return PersonalPolicyEvaluation.fail_closed(
                batch_id=batch.batch_id,
                evaluated_at=gate_result.evaluated_at,
                provider_id=provider_id,
                failure_code="unsupported_policy_tool_call",
                selected_slot_names=slot_names,
            )
        compiled = validated_contract

        provider_call_started = False
        try:
            await on_provider_call_started()
        except Exception:
            return PersonalPolicyEvaluation.fail_closed(
                batch_id=batch.batch_id,
                evaluated_at=gate_result.evaluated_at,
                provider_id=provider_id,
                failure_code="policy_usage_persistence_error",
                selected_slot_names=slot_names,
            )
        try:
            provider_call_started = True
            response = await asyncio.wait_for(
                provider.text_chat(
                    prompt=render_result.request_prompt or "",
                    contexts=build_model_context_messages(render_result.messages),
                    system_prompt=render_result.system_prompt or "",
                    temperature=interaction_config.personal_policy_temperature,
                    tool_choice="required",
                    output_contract=contract,
                    compiled_output_contract=compiled,
                ),
                timeout=interaction_config.personal_policy_timeout,
            )
        except asyncio.TimeoutError:
            return PersonalPolicyEvaluation.fail_closed(
                batch_id=batch.batch_id,
                evaluated_at=gate_result.evaluated_at,
                provider_id=provider_id,
                failure_code="timeout",
                provider_call_started=provider_call_started,
                selected_slot_names=slot_names,
            )
        except Exception:
            return PersonalPolicyEvaluation.fail_closed(
                batch_id=batch.batch_id,
                evaluated_at=gate_result.evaluated_at,
                provider_id=provider_id,
                failure_code="model_error",
                provider_call_started=provider_call_started,
                selected_slot_names=slot_names,
            )

        try:
            decision = extract_personal_policy_decision(
                llm_response=response,
                output_contract=contract,
                compiled_output_contract=compiled,
            )
        except PersonalPolicyError as exc:
            return PersonalPolicyEvaluation.fail_closed(
                batch_id=batch.batch_id,
                evaluated_at=gate_result.evaluated_at,
                provider_id=provider_id,
                failure_code=exc.reason,
                provider_call_started=True,
                selected_slot_names=slot_names,
            )
        except Exception:
            return PersonalPolicyEvaluation.fail_closed(
                batch_id=batch.batch_id,
                evaluated_at=gate_result.evaluated_at,
                provider_id=provider_id,
                failure_code="invalid_policy_payload",
                provider_call_started=True,
                selected_slot_names=slot_names,
            )
        return PersonalPolicyEvaluation(
            batch_id=batch.batch_id,
            status=PersonalPolicyEvaluationStatus.EVALUATED,
            decision=decision,
            evaluated_at=gate_result.evaluated_at,
            provider_id=provider_id,
            provider_call_started=True,
            selected_slot_names=slot_names,
        )

    async def _prepare_render_result(
        self,
        *,
        runtime_key: PersonalRuntimeKey,
        batch: ObservationBatch,
        gate_result: ObservationGateResult,
        state: PersonalStateSnapshot,
        gate_settings: ObservationGateSettings,
        plugin_context: Context,
        runtime_config: Mapping[str, Any],
        provider: Provider,
    ):
        event = PersonalPolicyPromptContext(
            batch=batch,
            runtime_config=runtime_config,
        )
        request = ProviderRequest(session_id=runtime_key.audience_key)
        request.provider = provider
        request.conversation = SimpleNamespace(
            persona_id=runtime_key.persona_id,
            cid=None,
            history=None,
        )
        event.set_extra("provider_request", request)
        build_config = build_interaction_prompt_build_config(plugin_context, event)
        runtime_collector = RuntimeContextCollector(
            personal_state=_personal_state_payload(state, gate_result),
            observation_batch=_observation_batch_payload(
                batch,
                evaluated_at=gate_result.evaluated_at,
            ),
            observation_features=_observation_features_payload(gate_result),
            session_datetime=_session_datetime_payload(
                gate_result.evaluated_at,
                gate_settings,
            ),
            session_info=_session_info_payload(batch),
        )
        pack = await PromptContextBuilder(
            event,
            plugin_context,
            build_config,
        ).build(
            collectors=[
                PersonaCollector(),
                ConversationHistoryCollector(),
                MemoryCollector(),
                runtime_collector,
            ],
            provider_request=request,
            include_prompt_extensions=False,
            scope="personal_policy",
        )
        return PromptRenderEngine().render(
            pack,
            target=PromptTarget.PERSONAL_POLICY,
            event=event,
            plugin_context=plugin_context,
            config=build_config,
            provider_request=request,
            profile=PromptRenderProfile(
                name="personal_policy",
                system_prompt=build_personal_policy_system_prompt(),
                request_prompt=build_personal_policy_prompt(),
                output_contract=build_personal_policy_output_contract(),
            ),
        )


class PersonalPolicyPromptContext:
    """Read-only collector adapter; it is not a platform or user event."""

    def __init__(
        self,
        *,
        batch: ObservationBatch,
        runtime_config: Mapping[str, Any],
    ) -> None:
        target = batch.observations[-1].target_session
        self.unified_msg_origin = target.unified_msg_origin
        self.session_id = target.session_id
        self.message_str = ""
        self.message_obj = SimpleNamespace(
            sender=None,
            group_id=target.group_id,
            group=SimpleNamespace(group_name=target.group_name),
        )
        self.platform_meta = SimpleNamespace(
            id=target.platform_id,
            name=target.platform_name,
        )
        self._message_type = target.message_type
        self._extras: dict[str, Any] = {"_astrbot_config": runtime_config}

    def get_extra(self, key: str | None = None, default=None) -> Any:
        if key is None:
            return self._extras
        return self._extras.get(key, default)

    def set_extra(self, key: str, value: Any) -> None:
        self._extras[key] = value

    def get_platform_id(self) -> str:
        return str(self.platform_meta.id)

    def get_platform_name(self) -> str:
        return str(self.platform_meta.name)

    def get_message_type(self) -> MessageType:
        return self._message_type

    def get_group_id(self) -> str:
        return str(self.message_obj.group_id or "")

    def get_sender_id(self) -> str:
        if self._message_type is MessageType.FRIEND_MESSAGE:
            return self.session_id
        return ""

    def get_sender_name(self) -> str:
        return ""


def _personal_state_payload(
    state: PersonalStateSnapshot,
    gate_result: ObservationGateResult,
) -> dict[str, Any]:
    features = gate_result.features
    return {
        "attention_state": state.attention_state.value,
        "availability_state": state.availability_state.value,
        "last_observation_at": state.last_observation_at,
        "last_user_activity_at": state.last_user_activity_at,
        "last_expression_at": state.last_expression_at,
        "seconds_since_user_activity": features.seconds_since_user_activity,
        "seconds_since_last_expression": features.seconds_since_last_expression,
        "reply_cooldown_until": state.reply_cooldown_until,
        "no_action_cooldown_until": state.no_action_cooldown_until,
        "mute_until": state.mute_until,
        "pending_observation_count": state.pending_observation_count,
        "usage_day": state.usage_day,
        "daily_policy_calls": state.daily_policy_calls,
        "daily_proactive_outputs": state.daily_proactive_outputs,
        "last_gate_reason": state.last_gate_reason,
        "last_policy_action": state.last_policy_action,
    }


def _observation_features_payload(
    gate_result: ObservationGateResult,
) -> dict[str, Any]:
    features = gate_result.features
    return {
        "is_explicitly_summoned": features.is_explicitly_summoned,
        "is_follow_up_candidate": features.is_follow_up_candidate,
        "message_count": features.message_count,
        "participant_count": features.participant_count,
        "echo_count": features.echo_count,
        "activity_density": features.activity_density,
        "seconds_since_user_activity": features.seconds_since_user_activity,
        "seconds_since_last_expression": features.seconds_since_last_expression,
        "has_pending_commitment": features.has_pending_commitment,
        "is_runtime_busy": features.is_runtime_busy,
        "is_quiet_hours": features.is_quiet_hours,
        "is_muted": features.is_muted,
        "policy_budget_available": features.policy_budget_available,
        "output_budget_available": features.output_budget_available,
        "target_available": features.target_available,
    }


def _observation_batch_payload(
    batch: ObservationBatch,
    *,
    evaluated_at: float,
) -> dict[str, Any]:
    return project_observation_batch(batch, evaluated_at=evaluated_at)


def _session_datetime_payload(
    evaluated_at: float,
    settings: ObservationGateSettings,
) -> dict[str, str]:
    value = settings.local_datetime(evaluated_at)
    return {
        "text": value.strftime("%Y-%m-%d %H:%M (%Z)"),
        "iso": value.isoformat(timespec="seconds"),
        "timezone": settings.timezone_name or str(value.tzinfo or "local"),
        "source": "personal_runtime_gate",
    }


def _session_info_payload(batch: ObservationBatch) -> dict[str, Any]:
    target = batch.observations[-1].target_session
    is_group = target.message_type is MessageType.GROUP_MESSAGE
    return {
        "user_id": None if is_group else target.session_id,
        "nickname": None,
        "role": "target_audience",
        "platform_name": target.platform_name,
        "umo": target.unified_msg_origin,
        "group_id": target.group_id,
        "group_name": target.group_name,
        "is_group": is_group,
        "conversation_scope": "group_multi_user" if is_group else "private_single_user",
    }


def _selected_slot_names(metadata: object) -> tuple[str, ...]:
    if not isinstance(metadata, Mapping):
        return ()
    values = metadata.get("selected_slot_names")
    if not isinstance(values, list | tuple):
        return ()
    return tuple(str(value) for value in values)


__all__ = [
    "PersonalPolicyAction",
    "PersonalPolicyAgent",
    "PersonalPolicyDecision",
    "PersonalPolicyError",
    "PersonalPolicyEvaluation",
    "PersonalPolicyEvaluationStatus",
    "PersonalPolicyPromptContext",
    "PersonalPolicyReason",
    "build_personal_policy_output_contract",
    "build_personal_policy_system_prompt",
    "extract_personal_policy_decision",
]
