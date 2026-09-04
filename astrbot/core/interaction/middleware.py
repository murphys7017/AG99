import asyncio
import uuid
from collections.abc import AsyncGenerator, Mapping
from types import MethodType
from typing import Any

from astrbot import logger
from astrbot.core.agent.tool_output_capture import get_active_tool_output_capture
from astrbot.core.deadline import TurnDeadlineExceeded
from astrbot.core.message.components import Image, Plain, Record
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import (
    INTERACTION_OUTPUT_CONTROLLER_EXTRA_KEY,
    AstrMessageEvent,
)
from astrbot.core.postprocess import dispatch_postprocess, get_postprocess_manager
from astrbot.core.postprocess.types import PostProcessTrigger
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.utils.media_utils import ensure_wav
from astrbot.core.voice import (
    VoiceServiceError,
    resolve_stt_provider,
    transcribe_record,
)

from .config import is_middleware_enabled, load_interaction_agent_config
from .conversation_history import commit_interaction_conversation_turn
from .core_planner import CorePlannerAgent, CorePlannerError
from .dialogue import build_canonical_user_message
from .expression_agent import (
    InteractionExpressionAgent,
    InteractionExpressionError,
    PersonaExpressionIntent,
    PersonaExpressionRequest,
    PersonaExpressionResult,
)
from .group_reply import group_conversation_allows_silent
from .lifecycle import dispatch_interaction_lifecycle
from .output_controller import InteractionOutputController
from .output_modes import OUTPUT_ORIGIN_EXTRA_KEY, OutputOrigin
from .persona_runtime import InteractionPersonaRuntime
from .personal_action import PersonalActionIntent
from .personal_expression_guard import (
    PREVIOUS_EXPRESSION_FINGERPRINT_METADATA_KEY,
    fingerprint_personal_expression,
)
from .protocol_bypass import match_protocol_command_bypass
from .router_agent import InteractionRouterAgent, InteractionRouterError
from .runtime_event import RuntimeObservationEvent
from .turn_context import PersonalTurnContext
from .turn_state import (
    InteractionFinalOutputStatus,
    InteractionLifecycleStage,
    InteractionSpeculativePersonaStatus,
    InteractionTurnOutcome,
    build_interaction_turn_reply,
    ensure_interaction_turn_state,
    finish_interaction_turn_final_output,
    get_interaction_turn_assistant_artifacts,
    get_interaction_turn_config,
    get_interaction_turn_finalized_material,
    get_interaction_turn_immediate_reply,
    get_interaction_turn_state,
    get_interaction_turn_visible_outputs,
    is_interaction_turn_completed,
    is_interaction_turn_pipeline_route_handled,
    mark_interaction_turn_cancelled,
    mark_interaction_turn_completed,
    mark_interaction_turn_core_delegated,
    mark_interaction_turn_failed,
    mark_interaction_turn_pipeline_route_handled,
    mark_interaction_turn_postprocess_dispatched,
    record_interaction_turn_completion_failure,
    record_interaction_turn_failure,
    reserve_interaction_turn_final_output,
    reserve_interaction_turn_immediate_output,
    set_interaction_turn_config,
    set_interaction_turn_core_planning_decision,
    set_interaction_turn_core_task_spec,
    set_interaction_turn_finalized_material,
    set_interaction_turn_route_decision,
    suppress_interaction_turn_pending_persona,
)
from .types import (
    CorePlanningAction,
    CorePlanningDecision,
    InteractionAgentConfig,
    InteractionRouteDecision,
    InteractionRouteMode,
)

LOCAL_FAST_EXPRESSION_FALLBACK_RESULT = PersonaExpressionResult(
    spoken_reply="模型服务暂时不可用，请稍后再试。"
)

def _merge_runtime_config(base: Any, override: Any) -> Any:
    if not isinstance(base, Mapping):
        return override if isinstance(override, Mapping) else base
    if not isinstance(override, Mapping):
        return dict(base)

    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge_runtime_config(merged[key], value)
        else:
            merged[key] = value
    return merged


class InteractionMiddleware:
    def __init__(
        self,
        config: Any,
        output_controller: InteractionOutputController,
        plugin_context: Any | None = None,
    ) -> None:
        self.config = config
        self.output_controller = output_controller
        self.plugin_context = plugin_context
        self._reject_development_fallback_policy(config)
        self.interaction_config = load_interaction_agent_config(config)
        self.expression_agent = InteractionExpressionAgent()
        self.persona_runtime = InteractionPersonaRuntime(self.expression_agent)
        self.router_agent = InteractionRouterAgent()
        self.core_planner = CorePlannerAgent()
        self.output_controller.interaction_config = self.interaction_config
        self.output_controller.plugin_context = plugin_context
        self.output_controller._persist_callback = self._on_output_persist_requested
        self.output_controller.visible_reply_renderer = (
            self._render_visible_reply_via_persona
        )
        self.output_controller.core_reply_handler = self._handle_core_reply_via_persona
        self.output_controller.lifecycle_callback = self._emit_lifecycle_from_output
    async def _emit_lifecycle_from_output(
        self,
        event: AstrMessageEvent,
        stage: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await dispatch_interaction_lifecycle(
            event,
            self.plugin_context,
            InteractionLifecycleStage(stage),
            metadata=metadata,
        )

    def set_plugin_context(self, plugin_context: Any) -> None:
        self.plugin_context = plugin_context
        self.output_controller.plugin_context = plugin_context

    async def _render_visible_reply_via_persona(
        self,
        event: AstrMessageEvent,
        request: PersonaExpressionRequest,
    ) -> PersonaExpressionResult:
        interaction_config = get_interaction_turn_config(event)
        if interaction_config is None:
            interaction_config = load_interaction_agent_config(
                self._get_runtime_config(event)
            )
        return await self.persona_runtime.express_visible_reply(
            event,
            plugin_context=self.plugin_context,
            interaction_config=interaction_config,
            request=request,
        )

    async def _handle_core_reply_via_persona(
        self,
        message: MessageChain,
        event: AstrMessageEvent,
    ) -> None:
        core_result_text = message.get_plain_text()
        turn_state = get_interaction_turn_state(event)
        immediate_reply = turn_state.immediate_reply if turn_state is not None else None
        try:
            result = await self._render_visible_reply_via_persona(
                event,
                PersonaExpressionRequest.core_final(
                    core_result_text,
                    immediate_reply=immediate_reply,
                ),
            )
        except TurnDeadlineExceeded as exc:
            await self._deliver_core_result_without_persona(
                message,
                event,
                stage=exc.stage,
                reason=exc.reason,
                exception=exc,
            )
            return
        except Exception as exc:  # noqa: BLE001
            await self._deliver_core_result_without_persona(
                message,
                event,
                stage="core_persona_render",
                reason=str(getattr(exc, "reason", "") or "exception"),
                exception=exc,
            )
            return
        await self.output_controller.deliver_prepared_core_reply(
            message,
            result,
            event,
        )

    async def _deliver_core_result_without_persona(
        self,
        message: MessageChain,
        event: AstrMessageEvent,
        *,
        stage: str,
        reason: str,
        exception: BaseException,
    ) -> None:
        """Deliver the completed Core result when Persona rendering is unavailable."""
        record_interaction_turn_failure(
            event,
            stage=stage,
            reason=reason,
            exception=exception,
            user_visible_action="deliver_core_result_without_persona",
        )
        logger.warning(
            "Core result Persona rendering failed; delivering the existing Core "
            "result without another model call: turn_id=%s stage=%s reason=%s",
            event.get_extra("_turn_id"),
            stage,
            reason,
        )
        fallback_message = message
        if not fallback_message.chain:
            fallback_message = message.derive(
                [Plain(LOCAL_FAST_EXPRESSION_FALLBACK_RESULT.spoken_reply)]
            )
        await self.output_controller.deliver_raw_core_reply(fallback_message, event)

    def _get_runtime_config(self, event: AstrMessageEvent | None = None) -> Any:
        if self.plugin_context is None:
            return self.config
        get_config = getattr(self.plugin_context, "get_config", None)
        if not callable(get_config):
            return self.config
        if event is not None:
            runtime_config = get_config(umo=event.unified_msg_origin)
            if not isinstance(runtime_config, Mapping):
                return self.config
            return _merge_runtime_config(self.config, runtime_config)
        runtime_config = get_config()
        if not isinstance(runtime_config, Mapping):
            return self.config
        return _merge_runtime_config(self.config, runtime_config)

    def refresh_interaction_config(
        self,
        event: AstrMessageEvent | None = None,
    ) -> None:
        runtime_config = self._get_runtime_config(event)
        self._reject_development_fallback_policy(runtime_config)
        self.interaction_config = load_interaction_agent_config(runtime_config)
        self.output_controller.interaction_config = self.interaction_config

    @staticmethod
    def _reject_development_fallback_policy(config: Any) -> None:
        interaction_config = config.get("interaction_middleware", {})
        if not isinstance(interaction_config, dict):
            return
        fallback_policy = interaction_config.get("fallback_policy")
        if fallback_policy is None:
            return
        raise RuntimeError(
            "interaction_middleware.fallback_policy is disabled during development"
        )

    def is_enabled_for_event(self, event: AstrMessageEvent) -> bool:
        interaction_config = get_interaction_turn_config(event)
        if interaction_config is not None:
            return interaction_config.enabled
        return is_middleware_enabled(self._get_runtime_config(event))

    def is_parallel_plugin_runtime_eligible(
        self,
        event: AstrMessageEvent,
        *,
        is_group_candidate: bool,
    ) -> bool:
        """Keep the experimental three-line path out of protocol turns."""
        if not self.is_enabled_for_event(event):
            return False
        if event.is_stopped() or event._has_send_oper or event.call_llm:
            return False
        if self._is_live_mode_event(event):
            return False
        if isinstance(event.get_extra("provider_request"), ProviderRequest):
            return False
        if self._maybe_prepare_protocol_command_bypass(event) is not None:
            return False
        return bool(event.is_at_or_wake_command or is_group_candidate)

    def accept_coordinated_route(
        self,
        event: AstrMessageEvent,
        route: InteractionRouteDecision,
    ) -> None:
        """Record a Router result already published by the shared coordinator."""
        self._record_route_diagnostics(event, route)
        self.attach_event_context(
            event,
            turn_id=str(event.get_extra("_turn_id", "") or ""),
        )

    @staticmethod
    def _is_live_mode_event(event: AstrMessageEvent) -> bool:
        return event.get_extra("action_type") == "live"

    def prepare_pipeline_event(self, event: AstrMessageEvent) -> None:
        if event.is_stopped():
            return
        turn_state = get_interaction_turn_state(event)
        if turn_state is not None and turn_state.pipeline_event_prepared:
            return
        runtime_config = self._get_runtime_config(event)
        interaction_config = get_interaction_turn_config(event)
        if interaction_config is None:
            if not is_middleware_enabled(runtime_config):
                return
            interaction_config = load_interaction_agent_config(runtime_config)
        if not interaction_config.enabled:
            return
        self._reject_development_fallback_policy(runtime_config)
        if isinstance(runtime_config, Mapping):
            event.set_extra("_astrbot_config", runtime_config)
        turn_id = str(event.get_extra("_turn_id", "") or "") or uuid.uuid4().hex
        turn_state = ensure_interaction_turn_state(event, turn_id=turn_id)
        set_interaction_turn_config(event, interaction_config)
        self.attach_event_context(event, turn_id=turn_id)
        turn_state.pipeline_event_prepared = True

    def attach_event_context(
        self,
        event: AstrMessageEvent,
        *,
        turn_id: str,
        route_decision: InteractionRouteDecision | None = None,
    ) -> None:
        event.set_extra("_interaction_enabled", True)
        event.set_extra("_turn_id", turn_id)
        event.set_extra(
            INTERACTION_OUTPUT_CONTROLLER_EXTRA_KEY,
            self.output_controller,
        )
        self._install_core_output_interceptor(event)
        if route_decision is not None:
            set_interaction_turn_route_decision(event, route_decision)

    def _install_core_output_interceptor(self, event: AstrMessageEvent) -> None:
        if event.get_extra("_interaction_output_interceptor_installed", False):
            return

        original_send = event.send
        original_send_streaming = event.send_streaming
        original_complete_visible_turn = event.complete_visible_turn
        output_controller = self.output_controller

        async def send_wrapper(
            wrapped_event: AstrMessageEvent,
            message: MessageChain | None,
        ) -> None:
            capture = get_active_tool_output_capture()
            if capture is not None:
                capture.capture(message)
                return
            previous_has_send_oper = wrapped_event._has_send_oper
            origin = wrapped_event.get_extra(OUTPUT_ORIGIN_EXTRA_KEY)
            if origin == OutputOrigin.CORE.value:
                await output_controller.capture_message_chain(message, wrapped_event)
            else:
                await output_controller.capture_plugin_output(
                    message,
                    wrapped_event,
                    mode=wrapped_event.get_extra(
                        "_interaction_plugin_output_mode",
                        "direct",
                    ),
                )
            if wrapped_event.get_extra(
                "_interaction_pipeline_output_suppressed",
                False,
            ):
                wrapped_event._has_send_oper = previous_has_send_oper
            else:
                wrapped_event._has_send_oper = True

        async def send_streaming_wrapper(
            wrapped_event: AstrMessageEvent,
            generator: AsyncGenerator[MessageChain, None],
            use_fallback: bool = False,
        ) -> None:
            capture = get_active_tool_output_capture()
            if capture is not None:
                await capture.capture_stream(generator)
                return
            origin = wrapped_event.get_extra(OUTPUT_ORIGIN_EXTRA_KEY)
            if origin == OutputOrigin.CORE.value:
                await output_controller.capture_streaming(
                    generator,
                    wrapped_event,
                    use_fallback=use_fallback,
                )
            else:
                await output_controller.capture_plugin_streaming(
                    generator,
                    wrapped_event,
                    mode=wrapped_event.get_extra(
                        "_interaction_plugin_output_mode",
                        "direct",
                    ),
                    use_fallback=use_fallback,
                )
            wrapped_event._has_send_oper = True

        async def complete_visible_turn_wrapper(
            wrapped_event: AstrMessageEvent,
        ) -> None:
            await output_controller.capture_visible_completion(wrapped_event)

        event.set_extra("_interaction_original_send", original_send)
        event.set_extra("_interaction_original_send_streaming", original_send_streaming)
        event.set_extra(
            "_interaction_original_complete_visible_turn",
            original_complete_visible_turn,
        )
        event.send = MethodType(send_wrapper, event)
        event.send_streaming = MethodType(send_streaming_wrapper, event)
        event.complete_visible_turn = MethodType(complete_visible_turn_wrapper, event)
        event.set_extra("_interaction_output_interceptor_installed", True)

    async def handle_pipeline_event(self, event: AstrMessageEvent) -> None:
        if event.is_stopped() or is_interaction_turn_pipeline_route_handled(event):
            return
        if not self.is_enabled_for_event(event):
            return
        self.prepare_pipeline_event(event)
        if not self._has_routeable_user_content(event):
            mark_interaction_turn_pipeline_route_handled(event)
            event.set_extra(
                "_interaction_route_skipped_reason",
                "empty_non_content_event",
            )
            logger.debug(
                "Interaction middleware skipped empty non-content event: platform_id=%s session_id=%s raw_type=%s",
                event.get_platform_id(),
                event.session_id,
                self._get_raw_event_field(event, "post_type"),
            )
            return
        await self._handle_pipeline_turn(event)
        mark_interaction_turn_pipeline_route_handled(event)

    async def handle_runtime_observation(
        self,
        event: RuntimeObservationEvent,
        turn: PersonalTurnContext,
    ) -> PersonaExpressionResult | None:
        """Express one admitted system observation without Router or Core."""
        if not isinstance(event, RuntimeObservationEvent):
            raise TypeError("event must be a RuntimeObservationEvent")
        if turn.event is not event or turn.observation is not event.observation:
            raise ValueError("Runtime observation does not match the admitted turn")
        if event.get_extra("_interaction_runtime_observation_handled", False):
            return None

        action_intent = event.get_extra("_personal_action_intent")
        is_personal_action = isinstance(action_intent, PersonalActionIntent)
        material = event.observation.visible_reply_material
        if not material:
            event.set_extra(
                "_interaction_runtime_observation_skipped_reason",
                "missing_visible_reply_material",
            )
            return None

        interaction_config = get_interaction_turn_config(event)
        if interaction_config is None:
            interaction_config = load_interaction_agent_config(
                self._get_runtime_config(event)
            )
        if not interaction_config.enabled:
            event.set_extra(
                "_interaction_runtime_observation_skipped_reason",
                "interaction_middleware_disabled",
            )
            return None

        self.prepare_pipeline_event(event)
        ensure_interaction_turn_state(
            event,
            turn_id=str(event.get_extra("_turn_id", "") or "") or uuid.uuid4().hex,
        )
        interaction_config = set_interaction_turn_config(event, interaction_config)
        event.set_extra("_interaction_runtime_observation_active", True)
        await dispatch_interaction_lifecycle(
            event,
            self.plugin_context,
            InteractionLifecycleStage.RECEIVED,
            metadata={
                "source": "runtime_observation",
                "kind": event.observation.kind,
            },
        )
        try:
            expression = await self._generate_expression(
                event,
                interaction_config,
                request=PersonaExpressionRequest(
                    source_text=material,
                    preserve_facts=True,
                    intent=PersonaExpressionIntent(
                        kind="proactive",
                        source="runtime_observation",
                        phase="proactive",
                    ),
                    avoid_previous_reply=is_personal_action,
                ),
                fallback_on_error=not is_personal_action,
            )
            if is_personal_action and self._is_duplicate_personal_expression(
                turn,
                expression,
            ):
                await self._suppress_duplicate_personal_expression(event)
                return None
            await self._emit_immediate_reply_or_record_failure(event, expression)
            await self._complete_persona_only_turn(event, expression)
            event.set_extra("_interaction_runtime_observation_handled", True)
            return expression
        except TurnDeadlineExceeded as exc:
            record_interaction_turn_failure(
                event,
                stage=exc.stage,
                reason=exc.reason,
                exception=exc,
                user_visible_action="none",
            )
            mark_interaction_turn_failed(event)
            await dispatch_interaction_lifecycle(
                event,
                self.plugin_context,
                InteractionLifecycleStage.FAILED,
                metadata={
                    "source": "runtime_observation",
                    "reason": exc.reason,
                },
            )
            raise
        except asyncio.CancelledError:
            mark_interaction_turn_cancelled(event)
            await dispatch_interaction_lifecycle(
                event,
                self.plugin_context,
                InteractionLifecycleStage.CANCELLED,
                metadata={"source": "runtime_observation"},
            )
            raise
        except Exception as exc:
            mark_interaction_turn_failed(event)
            await dispatch_interaction_lifecycle(
                event,
                self.plugin_context,
                InteractionLifecycleStage.FAILED,
                metadata={
                    "source": "runtime_observation",
                    "reason": str(exc),
                },
            )
            raise
        finally:
            event.set_extra("_interaction_runtime_observation_active", False)

    @staticmethod
    def _is_duplicate_personal_expression(
        turn: PersonalTurnContext,
        expression: PersonaExpressionResult,
    ) -> bool:
        current_fingerprint = fingerprint_personal_expression(
            expression.spoken_reply
        )
        if current_fingerprint is None:
            return False
        previous_fingerprints = {
            fingerprint
            for fingerprint in (
                turn.previous_expression_fingerprint,
                expression.metadata.get(
                    PREVIOUS_EXPRESSION_FINGERPRINT_METADATA_KEY
                ),
            )
            if isinstance(fingerprint, str) and fingerprint
        }
        return current_fingerprint in previous_fingerprints

    async def _suppress_duplicate_personal_expression(
        self,
        event: RuntimeObservationEvent,
    ) -> None:
        reason = "duplicate_previous_expression"
        event.set_extra(
            "_interaction_runtime_observation_skipped_reason",
            reason,
        )
        event.set_extra("_interaction_personal_expression_suppressed", True)
        if await reserve_interaction_turn_final_output(event):
            await finish_interaction_turn_final_output(
                event,
                InteractionFinalOutputStatus.SUPPRESSED,
            )
        mark_interaction_turn_completed(event)
        await dispatch_interaction_lifecycle(
            event,
            self.plugin_context,
            InteractionLifecycleStage.COMPLETED,
            metadata={
                "source": "runtime_observation",
                "outcome": "suppressed",
                "reason": reason,
            },
        )
        event.set_extra("_interaction_runtime_observation_handled", True)
        logger.info(
            "Personal autonomous expression suppressed: platform_id=%s "
            "session_id=%s turn_id=%s reason=%s",
            event.get_platform_id(),
            event.session_id,
            event.get_extra("_turn_id"),
            reason,
        )

    async def handle_runtime_output(
        self,
        event: RuntimeObservationEvent,
        turn: PersonalTurnContext,
        message: MessageChain,
        *,
        platform_extras: dict[str, Any] | None = None,
    ) -> None:
        """Deliver an admitted proactive plugin output through the turn runtime."""
        if turn.event is not event or turn.observation is not event.observation:
            raise ValueError("Runtime output does not match the admitted turn")
        runtime_config = self._get_runtime_config(event)
        if isinstance(runtime_config, Mapping):
            event.set_extra("_astrbot_config", runtime_config)
        self.prepare_pipeline_event(event)
        await dispatch_interaction_lifecycle(
            event,
            self.plugin_context,
            InteractionLifecycleStage.RECEIVED,
            metadata={"source": "proactive_output"},
        )
        if not await reserve_interaction_turn_final_output(event):
            return
        try:
            await self.output_controller.capture_plugin_output(
                message,
                event,
                mode="direct",
                finalize=True,
                platform_extras=platform_extras,
            )
        except BaseException:
            await finish_interaction_turn_final_output(
                event,
                InteractionFinalOutputStatus.FAILED,
            )
            raise
        await finish_interaction_turn_final_output(
            event,
            InteractionFinalOutputStatus.DELIVERED,
        )
        event.set_extra("_interaction_runtime_output_handled", True)

    async def handle_active_turn_output(
        self,
        turn: PersonalTurnContext,
        message: MessageChain,
        *,
        finalize: bool,
    ) -> None:
        """Emit output through the active turn's existing output transaction."""
        if not finalize:
            await self.output_controller.capture_plugin_output(
                message,
                turn.event,
                mode="direct",
                finalize=False,
            )
            return
        if not await reserve_interaction_turn_final_output(turn.event):
            return
        try:
            await self.output_controller.capture_plugin_output(
                message,
                turn.event,
                mode="direct",
                finalize=True,
            )
        except BaseException:
            await finish_interaction_turn_final_output(
                turn.event,
                InteractionFinalOutputStatus.FAILED,
            )
            raise
        await finish_interaction_turn_final_output(
            turn.event,
            InteractionFinalOutputStatus.DELIVERED,
        )

    @staticmethod
    def _has_routeable_user_content(event: AstrMessageEvent) -> bool:
        if InteractionMiddleware._is_live_mode_event(event):
            return True
        if event.get_extra("provider_request") is not None:
            return True
        if (event.message_str or "").strip():
            return True
        for comp in event.get_messages() or []:
            if isinstance(comp, Plain):
                if (comp.text or "").strip():
                    return True
            else:
                return True
        return False

    @staticmethod
    def _get_raw_event_field(event: AstrMessageEvent, field: str) -> Any:
        raw_message = getattr(event.message_obj, "raw_message", None)
        getter = getattr(raw_message, "get", None)
        if callable(getter):
            try:
                return getter(field)
            except Exception:
                return None
        if isinstance(raw_message, Mapping):
            return raw_message.get(field)
        return None

    async def _handle_pipeline_turn(
        self,
        event: AstrMessageEvent,
    ) -> None:
        try:
            interaction_config = await self.prepare_routable_pipeline_turn(event)
            if interaction_config is None:
                return
            await self._run_personal_reply_with_router_control(
                event,
                interaction_config,
            )
        except TurnDeadlineExceeded:
            raise
        except asyncio.CancelledError:
            mark_interaction_turn_cancelled(event)
            await dispatch_interaction_lifecycle(
                event,
                self.plugin_context,
                InteractionLifecycleStage.CANCELLED,
            )
            raise
        except Exception as exc:
            mark_interaction_turn_failed(event)
            await dispatch_interaction_lifecycle(
                event,
                self.plugin_context,
                InteractionLifecycleStage.FAILED,
                metadata={"reason": str(exc)},
            )
            raise

    async def prepare_routable_pipeline_turn(
        self,
        event: AstrMessageEvent,
    ) -> InteractionAgentConfig | None:
        """Prepare one turn and return config only when Router/Personal should run."""
        runtime_config = self._get_runtime_config(event)
        self._reject_development_fallback_policy(runtime_config)
        if isinstance(runtime_config, Mapping):
            event.set_extra("_astrbot_config", runtime_config)
        turn_id = str(event.get_extra("_turn_id", "") or "") or uuid.uuid4().hex
        turn_state = ensure_interaction_turn_state(event, turn_id=turn_id)
        interaction_config = get_interaction_turn_config(event)
        if interaction_config is None:
            interaction_config = set_interaction_turn_config(
                event,
                load_interaction_agent_config(runtime_config),
            )
        await dispatch_interaction_lifecycle(
            event,
            self.plugin_context,
            InteractionLifecycleStage.RECEIVED,
        )
        await self._materialize_inbound_media(event)
        if isinstance(event.get_extra("provider_request"), ProviderRequest):
            self.attach_event_context(event, turn_id=turn_state.turn_id)
            event.set_extra("_interaction_protocol_core_bypass", True)
            event.set_extra(
                "_interaction_protocol_core_bypass_reason",
                "explicit_provider_request",
            )
            await dispatch_interaction_lifecycle(
                event,
                self.plugin_context,
                InteractionLifecycleStage.DELEGATED,
                metadata={
                    "route_kind": "explicit_provider_request",
                    "reason": "plugin_handler_requested_llm",
                },
            )
            self._forward_to_core(event)
            return None
        protocol_reason = None
        if self._is_live_mode_event(event):
            protocol_reason = self._prepare_live_mode_protocol_bypass(event)
        else:
            protocol_reason = self._maybe_prepare_protocol_command_bypass(event)
        if protocol_reason is not None:
            self.attach_event_context(event, turn_id=turn_state.turn_id)
            event.set_extra("_interaction_protocol_core_bypass", True)
            event.set_extra(
                "_interaction_protocol_core_bypass_reason",
                protocol_reason,
            )
            await dispatch_interaction_lifecycle(
                event,
                self.plugin_context,
                InteractionLifecycleStage.DELEGATED,
                metadata={
                    "route_kind": "protocol_core_bypass",
                    "reason": protocol_reason,
                },
            )
            self._forward_to_core(event)
            return None
        await dispatch_interaction_lifecycle(
            event,
            self.plugin_context,
            InteractionLifecycleStage.ROUTING,
        )
        return interaction_config

    def _prepare_live_mode_protocol_bypass(
        self,
        event: AstrMessageEvent,
    ) -> str:
        event.set_extra("_interaction_live_mode_protocol_route", "core_audio_stream")
        event.set_extra(
            "_interaction_live_mode_protocol_reason",
            "live_mode_requires_audio_chunk_stream",
        )
        logger.info(
            "Interaction live mode routed to core audio stream: platform_id=%s session_id=%s turn_id=%s",
            event.get_platform_id(),
            event.session_id,
            event.get_extra("_turn_id"),
        )
        return "live_mode_requires_audio_chunk_stream"

    def _maybe_prepare_protocol_command_bypass(
        self,
        event: AstrMessageEvent,
    ) -> str | None:
        if self.plugin_context is None:
            return None
        reason = match_protocol_command_bypass(event, self.plugin_context)
        if reason is None:
            return None
        return reason

    async def _run_personal_reply_with_router_control(
        self,
        event: AstrMessageEvent,
        interaction_config: InteractionAgentConfig,
    ) -> None:
        self.prepare_parallel_turn_control(event)
        persona_task = self._start_speculative_persona_task(
            event,
            interaction_config,
        )
        turn_state = ensure_interaction_turn_state(event)
        router_task = turn_state.execution_scope.create_task(
            self.run_router_task(event, interaction_config),
            role="router",
            name=(
                f"interaction_router_{event.get_platform_id()}_"
                f"{turn_state.turn_id}"
            ),
        )
        route = await self.await_route_with_persona_control(
            event,
            persona_task,
            router_task,
        )
        await self.complete_routed_turn(
            event,
            interaction_config,
            persona_task,
            route,
        )

    def prepare_parallel_turn_control(self, event: AstrMessageEvent) -> None:
        self.attach_event_context(
            event,
            turn_id=str(event.get_extra("_turn_id", "") or ""),
        )
        self._set_speculative_persona_status(
            event,
            InteractionSpeculativePersonaStatus.PENDING,
        )

    async def run_personal_task(
        self,
        event: AstrMessageEvent,
        interaction_config: InteractionAgentConfig,
    ) -> PersonaExpressionResult | None:
        return await self._generate_and_emit_persona(event, interaction_config)

    async def run_router_task(
        self,
        event: AstrMessageEvent,
        interaction_config: InteractionAgentConfig,
    ) -> InteractionRouteDecision:
        return await self._route_interaction(event, interaction_config)

    async def await_route_with_persona_control(
        self,
        event: AstrMessageEvent,
        persona_task: asyncio.Task[PersonaExpressionResult | None],
        router_task: asyncio.Task[InteractionRouteDecision],
    ) -> InteractionRouteDecision:
        try:
            # Personal owns delivery and can emit while this coroutine waits for
            # Router to decide only silence and Core delegation.
            route = await router_task
        except TurnDeadlineExceeded:
            expression = await self._suppress_or_await_speculative_persona(
                event,
                persona_task,
                propagate_failure=False,
            )
            await self._complete_emitted_persona_after_control_timeout(
                event,
                expression,
            )
            raise
        except asyncio.CancelledError:
            await self._suppress_or_await_speculative_persona(
                event,
                persona_task,
                propagate_failure=False,
            )
            raise
        self._record_route_diagnostics(event, route)
        self.attach_event_context(
            event,
            turn_id=str(event.get_extra("_turn_id", "") or ""),
            route_decision=route,
        )
        return route

    async def complete_routed_turn(
        self,
        event: AstrMessageEvent,
        interaction_config: InteractionAgentConfig,
        persona_task: asyncio.Task[PersonaExpressionResult | None],
        route: InteractionRouteDecision,
    ) -> None:
        turn_state = ensure_interaction_turn_state(event)
        if route.route_mode == InteractionRouteMode.SILENT:
            expression = await self._suppress_or_await_speculative_persona(
                event,
                persona_task,
                propagate_failure=False,
            )
            await self._complete_silent_or_committed_persona_turn(event, expression)
            return

        planning_decision = None
        if route.route_mode == InteractionRouteMode.HYBRID:
            try:
                planning_decision = await self._plan_core_execution(
                    event,
                    interaction_config,
                )
            except TurnDeadlineExceeded:
                expression = await self._suppress_or_await_speculative_persona(
                    event,
                    persona_task,
                    propagate_failure=False,
                )
                await self._complete_emitted_persona_after_control_timeout(
                    event,
                    expression,
                )
                raise
            except asyncio.CancelledError:
                await self._suppress_or_await_speculative_persona(
                    event,
                    persona_task,
                    propagate_failure=False,
                )
                raise
            except Exception as planner_error:
                result = await asyncio.gather(
                    persona_task,
                    return_exceptions=True,
                )
                expression = result[0]
                if (
                    isinstance(expression, PersonaExpressionResult)
                    and turn_state.speculative_persona_status
                    is InteractionSpeculativePersonaStatus.EMITTED
                ):
                    event.set_extra(
                        "_interaction_core_planner_recovered_via_persona",
                        True,
                    )
                    if (
                        turn_state.failures
                        and turn_state.failures[-1].stage == "core_planner"
                    ):
                        turn_state.failures[-1].user_visible_action = "persona_only"
                    await self._complete_persona_only_turn(event, expression)
                    return
                if isinstance(expression, BaseException):
                    raise expression from planner_error
                raise

        if (
            planning_decision is not None
            and planning_decision.action is CorePlanningAction.EXECUTE
        ):
            await self._emit_delegated(event, route)
            self._forward_to_core(event)
            return

        expression = await persona_task
        if expression is None:
            # A Persona hook may intentionally suppress the speculative reply
            # after the Router has selected the Persona route. Complete this as
            # a silent turn instead of turning a valid control outcome into a
            # pipeline failure.
            await self._complete_silent_or_committed_persona_turn(event, None)
            return
        await self._complete_persona_only_turn(event, expression)

    def _start_speculative_persona_task(
        self,
        event: AstrMessageEvent,
        interaction_config: InteractionAgentConfig,
    ) -> asyncio.Task[PersonaExpressionResult | None]:
        turn_state = ensure_interaction_turn_state(event)
        return turn_state.execution_scope.create_task(
            self.run_personal_task(event, interaction_config),
            role="speculative_persona",
            name=(
                f"interaction_speculative_persona_{event.get_platform_id()}_"
                f"{turn_state.turn_id}"
            ),
        )

    async def _generate_and_emit_persona(
        self,
        event: AstrMessageEvent,
        interaction_config,
    ) -> PersonaExpressionResult | None:
        if self.plugin_context is None:
            event.set_extra("_interaction_expression_failed", True)
            event.set_extra(
                "_interaction_expression_failure_reason",
                "plugin_context_unavailable",
            )
            self._set_speculative_persona_status(
                event,
                InteractionSpeculativePersonaStatus.SUPPRESSED,
            )
            return None
        expression = await self._generate_expression(
            event,
            interaction_config,
            request=PersonaExpressionRequest(
                compact_context=True,
                intent=PersonaExpressionIntent(
                    source="user_message",
                    phase="immediate",
                ),
            ),
        )
        turn_state = ensure_interaction_turn_state(event)
        if expression is None or not expression.spoken_reply.strip():
            async with turn_state.lock:
                if (
                    turn_state.speculative_persona_status
                    is InteractionSpeculativePersonaStatus.PENDING
                ):
                    self._set_speculative_persona_status(
                        event,
                        InteractionSpeculativePersonaStatus.SUPPRESSED,
                    )
            return None

        if not await reserve_interaction_turn_immediate_output(event):
            return None
        try:
            await self._emit_immediate_reply_or_record_failure(event, expression)
        except Exception:
            async with turn_state.lock:
                self._set_speculative_persona_status(
                    event,
                    InteractionSpeculativePersonaStatus.FAILED,
                )
            raise
        async with turn_state.lock:
            self._set_speculative_persona_status(
                event,
                InteractionSpeculativePersonaStatus.EMITTED,
            )
        return expression

    async def _suppress_or_await_speculative_persona(
        self,
        event: AstrMessageEvent,
        persona_task: asyncio.Task,
        *,
        propagate_failure: bool = True,
    ) -> PersonaExpressionResult | None:
        turn_state = ensure_interaction_turn_state(event)
        async with turn_state.lock:
            status = turn_state.speculative_persona_status
        if status is InteractionSpeculativePersonaStatus.PENDING:
            if await suppress_interaction_turn_pending_persona(event, persona_task):
                return None
            status = turn_state.speculative_persona_status
        if status is InteractionSpeculativePersonaStatus.EMITTED:
            # The visible reply is already committed. Do not wait for trailing
            # Persona bookkeeping just to complete the control-plane turn.
            reply = get_interaction_turn_immediate_reply(event)
            return (
                PersonaExpressionResult(spoken_reply=reply)
                if reply
                else None
            )
        if status in {
            InteractionSpeculativePersonaStatus.NOT_STARTED,
            InteractionSpeculativePersonaStatus.SUPPRESSED,
            InteractionSpeculativePersonaStatus.FAILED,
        }:
            return None
        result = await asyncio.gather(persona_task, return_exceptions=True)
        value = result[0]
        if isinstance(value, BaseException):
            if isinstance(value, asyncio.CancelledError):
                return None
            if propagate_failure:
                raise value
            return None
        return value if isinstance(value, PersonaExpressionResult) else None

    async def _complete_emitted_persona_after_control_timeout(
        self,
        event: AstrMessageEvent,
        expression: PersonaExpressionResult | None,
    ) -> bool:
        turn_state = ensure_interaction_turn_state(event)
        if (
            expression is None
            or turn_state.speculative_persona_status
            is not InteractionSpeculativePersonaStatus.EMITTED
        ):
            return False
        event.set_extra("_interaction_control_timeout_completed_via_persona", True)
        await self._complete_persona_only_turn(event, expression)
        return True

    async def _complete_silent_or_committed_persona_turn(
        self,
        event: AstrMessageEvent,
        expression: PersonaExpressionResult | None,
    ) -> None:
        turn_state = ensure_interaction_turn_state(event)
        if (
            turn_state.speculative_persona_status
            is InteractionSpeculativePersonaStatus.EMITTED
        ):
            await self._complete_persona_only_turn(event, expression)
            return
        self._materialize_silent_turn(event)
        await self._finalize_turn(event)
        event.stop_event()

    async def _complete_persona_only_turn(
        self,
        event: AstrMessageEvent,
        expression: PersonaExpressionResult | None,
    ) -> None:
        if expression is None or not expression.spoken_reply.strip():
            event.set_extra("_interaction_persona_reply_invalid", True)
            event.set_extra(
                "_interaction_persona_reply_invalid_reason",
                "missing_immediate_reply",
            )
            record_interaction_turn_failure(
                event,
                stage="persona_expression",
                reason="missing_persona_reply",
                user_visible_action="none",
            )
            raise RuntimeError("Interaction persona expression missing reply")
        reply = get_interaction_turn_immediate_reply(event)
        self._materialize_persona_reply_turn(
            event,
            reply=reply or expression.spoken_reply,
        )
        completed = await self._complete_visible_turn_or_record_failure(event)
        if completed:
            await self._finalize_turn(event)
        event.stop_event()

    @staticmethod
    def _set_speculative_persona_status(
        event: AstrMessageEvent,
        status: InteractionSpeculativePersonaStatus,
    ) -> None:
        turn_state = ensure_interaction_turn_state(event)
        turn_state.speculative_persona_status = status

    async def _plan_core_execution(
        self,
        event: AstrMessageEvent,
        interaction_config,
    ) -> CorePlanningDecision:
        try:
            if self.plugin_context is None:
                raise CorePlannerError("plugin_context_unavailable")
            decision = await self.core_planner.plan(
                event,
                self.plugin_context,
                interaction_config,
            )
        except CorePlannerError as exc:
            record_interaction_turn_failure(
                event,
                stage="core_planner",
                reason=exc.reason,
                exception=exc,
                user_visible_action="none",
            )
            event.set_extra("_interaction_core_planner_failed", True)
            event.set_extra("_interaction_core_planner_failure_reason", str(exc))
            raise
        set_interaction_turn_core_planning_decision(event, decision)
        if decision.action is CorePlanningAction.EXECUTE:
            set_interaction_turn_core_task_spec(event, decision.task_spec)
        return decision

    def _record_route_diagnostics(
        self,
        event: AstrMessageEvent,
        route: InteractionRouteDecision,
    ) -> None:
        router_source = str(
            event.get_extra("_interaction_router_result_source", "fallback")
        )
        router_failure_reason = str(
            event.get_extra("_interaction_router_failure_reason", "") or ""
        )
        router_context_nodes = event.get_extra("_interaction_router_context_nodes", [])
        if not isinstance(router_context_nodes, list):
            router_context_nodes = []
        logger.info(
            "DIAG interaction.route: platform_id=%s session_id=%s route_mode=%s route_source=%s fallback_reason=%s context_nodes=%s",
            event.get_platform_id(),
            event.session_id,
            route.route_mode.value,
            router_source,
            router_failure_reason,
            router_context_nodes,
        )

    async def _emit_delegated(
        self,
        event: AstrMessageEvent,
        route: InteractionRouteDecision,
    ) -> None:
        await dispatch_interaction_lifecycle(
            event,
            self.plugin_context,
            InteractionLifecycleStage.DELEGATED,
            metadata={"route_mode": route.route_mode.value},
        )

    async def _generate_expression(
        self,
        event: AstrMessageEvent,
        interaction_config,
        *,
        request: PersonaExpressionRequest | None = None,
        fallback_on_error: bool = True,
    ) -> PersonaExpressionResult:
        if self.plugin_context is None:
            if not fallback_on_error:
                raise InteractionExpressionError("plugin_context_unavailable")
            event.set_extra("_interaction_expression_failed", True)
            event.set_extra(
                "_interaction_expression_failure_reason",
                "plugin_context_unavailable",
            )
            return LOCAL_FAST_EXPRESSION_FALLBACK_RESULT
        try:
            return await self.persona_runtime.express_visible_reply(
                event,
                plugin_context=self.plugin_context,
                interaction_config=interaction_config,
                request=request or PersonaExpressionRequest(),
            )
        except TurnDeadlineExceeded:
            raise
        except InteractionExpressionError as exc:
            reason = exc.reason
            error: Exception = exc
        except Exception as exc:  # noqa: BLE001
            reason = "expression_pipeline_error"
            error = exc
        if not fallback_on_error:
            raise error

        event.set_extra("_interaction_expression_failed", True)
        event.set_extra("_interaction_expression_failure_reason", str(error))
        record_interaction_turn_failure(
            event,
            stage="fast_expression",
            reason=reason,
            exception=error,
            user_visible_action="fallback_first_response",
        )
        logger.warning(
            "Interaction fast expression failed; using local fallback: platform_id=%s session_id=%s reason=%s error=%s",
            event.get_platform_id(),
            event.session_id,
            reason,
            error,
            exc_info=(type(error), error, error.__traceback__),
        )
        return LOCAL_FAST_EXPRESSION_FALLBACK_RESULT

    async def _route_interaction(
        self,
        event: AstrMessageEvent,
        interaction_config,
    ) -> InteractionRouteDecision:
        fallback_mode = (
            InteractionRouteMode.SILENT
            if group_conversation_allows_silent(event)
            else InteractionRouteMode.PERSONA
        )
        if self.plugin_context is None:
            event.set_extra("_interaction_router_failed", True)
            event.set_extra(
                "_interaction_router_failure_reason",
                "plugin_context_unavailable",
            )
            event.set_extra("_interaction_router_result_source", "fallback")
            return InteractionRouteDecision(route_mode=fallback_mode)
        try:
            return await self.router_agent.route(
                event,
                self.plugin_context,
                interaction_config,
            )
        except TurnDeadlineExceeded:
            raise
        except InteractionRouterError as exc:
            reason = exc.reason
            error: Exception = exc
        except Exception as exc:  # noqa: BLE001
            reason = "router_pipeline_error"
            error = exc

        event.set_extra("_interaction_router_failed", True)
        event.set_extra("_interaction_router_failure_reason", str(error))
        event.set_extra("_interaction_router_result_source", "fallback")
        record_interaction_turn_failure(
            event,
            stage="router",
            reason=reason,
            exception=error,
            user_visible_action=f"fallback_{fallback_mode.value}",
        )
        logger.warning(
            "Interaction router failed; falling back to %s: platform_id=%s session_id=%s reason=%s error=%s",
            fallback_mode.value,
            event.get_platform_id(),
            event.session_id,
            reason,
            error,
            exc_info=(type(error), error, error.__traceback__),
        )
        return InteractionRouteDecision(route_mode=fallback_mode)

    async def _materialize_inbound_media(self, event: AstrMessageEvent) -> None:
        runtime_config = self._get_runtime_config(event)
        self._apply_inbound_path_mapping(event, runtime_config)
        await self._normalize_inbound_records(event)
        await self._transcribe_inbound_records(event, runtime_config)
        event.set_extra("_interaction_inbound_media_materialized", True)

    def _apply_inbound_path_mapping(
        self,
        event: AstrMessageEvent,
        runtime_config: Any,
    ) -> None:
        platform_settings = runtime_config.get("platform_settings", {})
        mappings = platform_settings.get("path_mapping", [])
        if not isinstance(mappings, list) or not mappings:
            return

        message_chain = event.get_messages()
        for idx, component in enumerate(message_chain):
            if not isinstance(component, Record | Image) or not component.url:
                continue
            for mapping in mappings:
                if not isinstance(mapping, str) or ":" not in mapping:
                    continue
                from_, to_ = mapping.split(":", 1)
                from_ = from_.removesuffix("/")
                to_ = to_.removesuffix("/")
                url = component.url.removeprefix("file://")
                if url.startswith(from_):
                    component.url = url.replace(from_, to_, 1)
                    logger.debug(
                        "Interaction inbound path mapped: platform_id=%s session_id=%s from=%s to=%s",
                        event.get_platform_id(),
                        event.session_id,
                        url,
                        component.url,
                    )
            message_chain[idx] = component

    async def _normalize_inbound_records(self, event: AstrMessageEvent) -> None:
        message_chain = event.get_messages()
        for idx, component in enumerate(message_chain):
            if not isinstance(component, Record):
                continue
            try:
                original_path = await component.convert_to_file_path()
                record_path = await ensure_wav(original_path)
                if record_path != original_path:
                    event.track_temporary_local_file(record_path)
                component.file = record_path
                component.path = record_path
                message_chain[idx] = component
            except Exception as exc:  # noqa: BLE001
                event.set_extra("_interaction_record_normalize_failed", True)
                event.set_extra(
                    "_interaction_record_normalize_failure_reason",
                    str(exc),
                )
                record_interaction_turn_failure(
                    event,
                    stage="inbound_record_normalize",
                    reason="record_normalize_failed",
                    exception=exc,
                    user_visible_action="none",
                )
                logger.warning(
                    "Interaction inbound voice normalization failed: platform_id=%s session_id=%s turn_id=%s error=%s",
                    event.get_platform_id(),
                    event.session_id,
                    event.get_extra("_turn_id"),
                    exc,
                )
                raise

    async def _transcribe_inbound_records(
        self,
        event: AstrMessageEvent,
        runtime_config: Any,
    ) -> None:
        stt_settings = runtime_config.get("provider_stt_settings", {})
        if not isinstance(stt_settings, dict) or not stt_settings.get("enable", False):
            return
        try:
            stt_provider = resolve_stt_provider(
                self.plugin_context,
                event,
                stage="interaction.inbound_stt",
            )
        except VoiceServiceError as exc:
            event.set_extra("_interaction_stt_failed", True)
            event.set_extra(
                "_interaction_stt_failure_reason",
                exc.reason,
            )
            record_interaction_turn_failure(
                event,
                stage="inbound_stt",
                reason=exc.reason,
                user_visible_action="none",
            )
            logger.warning(
                "Interaction inbound STT skipped: platform_id=%s session_id=%s reason=%s",
                event.get_platform_id(),
                event.session_id,
                exc.reason,
            )
            raise

        message_chain = event.get_messages()
        for idx, component in enumerate(message_chain):
            if not isinstance(component, Record):
                continue
            try:
                result = await transcribe_record(
                    self.plugin_context,
                    event,
                    component,
                    provider=stt_provider,
                    stage="interaction.inbound_stt",
                )
            except VoiceServiceError as exc:
                event.set_extra("_interaction_stt_failed", True)
                event.set_extra("_interaction_stt_failure_reason", str(exc))
                record_interaction_turn_failure(
                    event,
                    stage="inbound_stt",
                    reason=exc.reason,
                    exception=exc,
                    user_visible_action="none",
                )
                if exc.reason in {"audio_path_resolution_failed", "provider_error"}:
                    logger.error(
                        "Interaction inbound STT failed: platform_id=%s session_id=%s turn_id=%s reason=%s error=%s",
                        event.get_platform_id(),
                        event.session_id,
                        event.get_extra("_turn_id"),
                        exc.reason,
                        exc,
                        exc_info=True,
                    )
                raise
            logger.info("Interaction inbound STT result: %s", result.text)
            message_chain[idx] = Plain(result.text)
            event.message_str = f"{event.message_str or ''}{result.text}"
            event.message_obj.message_str = (
                f"{event.message_obj.message_str or ''}{result.text}"
            )
            event.set_extra("_interaction_stt_transcribed", True)

    async def _emit_immediate_reply(
        self,
        event: AstrMessageEvent,
        expression: PersonaExpressionResult,
    ) -> None:
        if not expression.spoken_reply.strip():
            return
        await self.output_controller.emit_immediate_spoken_reply(expression, event)

    async def _emit_immediate_reply_or_record_failure(
        self,
        event: AstrMessageEvent,
        expression: PersonaExpressionResult,
    ) -> bool:
        try:
            await self._emit_immediate_reply(event, expression)
            return True
        except Exception as exc:  # noqa: BLE001
            event.set_extra("_interaction_immediate_reply_failed", True)
            event.set_extra("_interaction_immediate_reply_failure_reason", str(exc))
            record_interaction_turn_failure(
                event,
                stage="immediate_reply",
                reason="send_failed",
                exception=exc,
                user_visible_action="none",
            )
            logger.error(
                "Interaction immediate reply failed; aborting turn: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )
            raise

    async def _complete_visible_turn_or_record_failure(
        self,
        event: AstrMessageEvent,
    ) -> bool:
        try:
            controller = event.get_extra(INTERACTION_OUTPUT_CONTROLLER_EXTRA_KEY)
            complete_visible_delivery = getattr(
                type(controller),
                "complete_visible_delivery",
                None,
            )
            if callable(complete_visible_delivery):
                return await complete_visible_delivery(controller, event)
            await event.complete_visible_turn()
            return True
        except Exception as exc:  # noqa: BLE001
            event.set_extra("_interaction_visible_completion_failed", True)
            event.set_extra("_interaction_visible_completion_failure_reason", str(exc))
            record_interaction_turn_failure(
                event,
                stage="visible_completion",
                reason="completion_failed",
                exception=exc,
                user_visible_action="none",
            )
            logger.error(
                "Interaction visible completion failed; aborting turn: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )
            raise

    def _schedule_turn_postprocess(self, event: AstrMessageEvent) -> None:
        visible_outputs = get_interaction_turn_visible_outputs(event)
        turn_material = get_interaction_turn_finalized_material(event)
        if turn_material is None:
            event.set_extra("_interaction_turn_postprocess_failed", True)
            event.set_extra(
                "_interaction_turn_postprocess_failure_reason",
                "missing_finalized_turn_material",
            )
            record_interaction_turn_completion_failure(
                event,
                "missing_finalized_turn_material",
            )
            logger.error(
                "Interaction turn postprocess skipped: missing finalized material platform_id=%s session_id=%s turn_id=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
            )
            return
        task = get_postprocess_manager().schedule(
            dispatch_postprocess(
                event=event,
                trigger=PostProcessTrigger.AFTER_TURN_COMPLETED,
                plugin_context=self.plugin_context,
                turn_id=str(event.get_extra("_turn_id", "") or ""),
                visible_outputs=visible_outputs,
                turn_material=turn_material,
            ),
            name=f"interaction_turn_postprocess_{event.get_platform_id()}",
        )
        if task is not None:
            task.add_done_callback(
                lambda done_task: self._log_turn_postprocess_failure(event, done_task)
            )

    @staticmethod
    def _log_turn_postprocess_failure(
        event: AstrMessageEvent,
        task: asyncio.Task,
    ) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            event.set_extra("_interaction_turn_postprocess_failed", True)
            event.set_extra("_interaction_turn_postprocess_failure_reason", str(exc))
            record_interaction_turn_completion_failure(event, f"postprocess:{exc}")
            logger.error(
                "Interaction turn postprocess failed: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )

    def _forward_to_core(
        self,
        event: AstrMessageEvent,
    ) -> None:
        mark_interaction_turn_core_delegated(event)
        event.is_wake = True
        event.is_at_or_wake_command = True
        event._extras.pop("provider", None)
        turn_state = get_interaction_turn_state(event)
        route = turn_state.route_decision if turn_state is not None else None
        if (
            isinstance(route, InteractionRouteDecision)
            and route.route_mode == InteractionRouteMode.HYBRID
            and turn_state is not None
            and turn_state.speculative_persona_status
            is InteractionSpeculativePersonaStatus.EMITTED
            and event._has_send_oper
        ):
            event._has_send_oper = False

    def _build_finalized_turn_material(
        self,
        event: AstrMessageEvent,
        visible_outputs: list[dict[str, Any]] | None = None,
        *,
        canonical_reply: str | None = None,
    ) -> dict[str, Any] | None:
        turn_id = str(event.get_extra("_turn_id", "") or "").strip()
        if not turn_id:
            return None
        outputs = [
            dict(item)
            for item in (
                visible_outputs
                if isinstance(visible_outputs, list)
                else get_interaction_turn_visible_outputs(event)
            )
            if isinstance(item, dict)
        ]
        if canonical_reply is None:
            turn_state = get_interaction_turn_state(event)
            utterances = turn_state.utterances if turn_state is not None else None
            canonical_reply = build_interaction_turn_reply(
                outputs,
                turn_id=turn_id,
                utterances=utterances,
            )
        canonical_reply = (canonical_reply or "").strip()
        assistant_artifacts = get_interaction_turn_assistant_artifacts(event)
        if not canonical_reply and not assistant_artifacts:
            return None
        is_observation = isinstance(event, RuntimeObservationEvent)
        material = {
            "turn_id": turn_id,
            "source": "observation" if is_observation else "platform",
            "user_text": "" if is_observation else (event.message_str or "").strip(),
            "user_message": (
                None if is_observation else build_canonical_user_message(event)
            ),
            "assistant_text": canonical_reply,
            "assistant_artifacts": list(assistant_artifacts),
            "visible_outputs": outputs,
            "history_source": (
                "interaction.runtime_observation"
                if is_observation
                else "interaction.turn.material"
            ),
        }
        if is_observation:
            material["observation_kind"] = event.observation.kind
            material["observation_source"] = event.observation.source
            material["observation_correlation_id"] = event.observation.correlation_id
        delivery_metadata = event.get_extra("_interaction_delivery_metadata")
        if isinstance(delivery_metadata, Mapping):
            material["assistant_metadata"] = dict(delivery_metadata)
        set_interaction_turn_finalized_material(event, material)
        return material

    def _materialize_persona_reply_turn(
        self,
        event: AstrMessageEvent,
        *,
        reply: str | None,
    ) -> dict[str, Any]:
        material = self._build_finalized_turn_material(
            event,
            visible_outputs=get_interaction_turn_visible_outputs(event),
            canonical_reply=reply,
        )
        if material is None:
            event.set_extra("_interaction_finalized_turn_material_failed", True)
            event.set_extra(
                "_interaction_finalized_turn_material_failure_reason",
                "missing_persona_reply_material",
            )
            record_interaction_turn_completion_failure(
                event,
                "missing_persona_reply_material",
            )
            raise RuntimeError("Interaction persona reply material missing")
        return material

    def _materialize_silent_turn(self, event: AstrMessageEvent) -> dict[str, Any]:
        material = {
            "turn_id": str(event.get_extra("_turn_id", "") or "").strip(),
            "user_text": (event.message_str or "").strip(),
            "user_message": build_canonical_user_message(event),
            "assistant_text": "",
            "visible_outputs": [],
            "history_source": "interaction.turn.material",
            "outcome": InteractionTurnOutcome.SILENT.value,
        }
        set_interaction_turn_finalized_material(event, material)
        return material

    async def _finalize_turn(
        self,
        event: AstrMessageEvent,
    ) -> None:
        turn_state = get_interaction_turn_state(event)
        if turn_state is None:
            turn_state = ensure_interaction_turn_state(event)
        if is_interaction_turn_completed(event):
            return

        material = get_interaction_turn_finalized_material(event)
        if material is None:
            self._record_turn_finalization_failure(
                event,
                "missing_finalized_turn_material",
            )
            record_interaction_turn_completion_failure(
                event,
                "missing_finalized_turn_material",
            )
            mark_interaction_turn_failed(event)
            await dispatch_interaction_lifecycle(
                event,
                self.plugin_context,
                InteractionLifecycleStage.FAILED,
                metadata={"reason": "missing_finalized_turn_material"},
            )
            logger.error(
                "Interaction turn finalization failed: missing finalized material platform_id=%s session_id=%s turn_id=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
            )
            return
        turn_id = str(material.get("turn_id", "") or "").strip()
        if not turn_id:
            self._record_turn_finalization_failure(event, "missing_turn_id")
            record_interaction_turn_completion_failure(event, "missing_turn_id")
            mark_interaction_turn_failed(event)
            await dispatch_interaction_lifecycle(
                event,
                self.plugin_context,
                InteractionLifecycleStage.FAILED,
                metadata={"reason": "missing_turn_id"},
            )
            return

        outcome = str(
            material.get("outcome", InteractionTurnOutcome.REPLIED.value) or ""
        )
        if outcome == InteractionTurnOutcome.SILENT.value:
            event.set_extra("_interaction_silent_completed", True)
            mark_interaction_turn_completed(event)
            await dispatch_interaction_lifecycle(
                event,
                self.plugin_context,
                InteractionLifecycleStage.COMPLETED,
                metadata={"outcome": InteractionTurnOutcome.SILENT.value},
            )
            self._record_turn_resolution(event, outcome)
            return

        canonical_reply = str(material.get("assistant_text", "") or "").strip()
        assistant_artifacts = material.get("assistant_artifacts", [])
        if not canonical_reply and not assistant_artifacts:
            self._record_turn_finalization_failure(
                event,
                "missing_canonical_reply",
            )
            record_interaction_turn_completion_failure(
                event,
                "missing_canonical_reply",
            )
            mark_interaction_turn_failed(event)
            await dispatch_interaction_lifecycle(
                event,
                self.plugin_context,
                InteractionLifecycleStage.FAILED,
                metadata={"reason": "missing_canonical_reply"},
            )
            return

        committed = await commit_interaction_conversation_turn(
            event=event,
            plugin_context=self.plugin_context,
            turn_id=turn_id,
            turn_material=material,
        )
        if not committed:
            self._record_turn_finalization_failure(
                event,
                "conversation_history_commit_failed",
            )
            record_interaction_turn_completion_failure(
                event,
                "conversation_history_commit_failed",
            )
            mark_interaction_turn_failed(event)
            await dispatch_interaction_lifecycle(
                event,
                self.plugin_context,
                InteractionLifecycleStage.FAILED,
                metadata={"reason": "conversation_history_commit_failed"},
            )
            return
        self._schedule_turn_postprocess(event)
        mark_interaction_turn_postprocess_dispatched(event)
        mark_interaction_turn_completed(event)
        await dispatch_interaction_lifecycle(
            event,
            self.plugin_context,
            InteractionLifecycleStage.COMPLETED,
        )
        self._record_turn_resolution(event, outcome)

    @staticmethod
    def _record_turn_resolution(event: AstrMessageEvent, outcome: str) -> None:
        turn_state = ensure_interaction_turn_state(event)
        route_mode = (
            turn_state.route_decision.route_mode.value
            if turn_state.route_decision is not None
            else "none"
        )
        logger.debug(
            "DIAG interaction.turn_resolution: platform_id=%s session_id=%s "
            "turn_id=%s route_mode=%s personal_status=%s turn_outcome=%s",
            event.get_platform_id(),
            event.session_id,
            turn_state.turn_id,
            route_mode,
            turn_state.speculative_persona_status.value,
            outcome,
        )

    @staticmethod
    def _record_turn_finalization_failure(
        event: AstrMessageEvent,
        reason: str,
    ) -> None:
        event.set_extra("_interaction_turn_finalization_failed", True)
        event.set_extra("_interaction_turn_finalization_failure_reason", reason)

    async def _on_output_persist_requested(
        self,
        event: AstrMessageEvent,
    ) -> None:
        material = self._build_finalized_turn_material(event)
        if material is None:
            self._record_turn_finalization_failure(
                event,
                "missing_canonical_turn_material",
            )
            record_interaction_turn_completion_failure(
                event,
                "missing_canonical_turn_material",
            )
            mark_interaction_turn_failed(event)
            await dispatch_interaction_lifecycle(
                event,
                self.plugin_context,
                InteractionLifecycleStage.FAILED,
                metadata={"reason": "missing_canonical_turn_material"},
            )
            return
        await self._finalize_turn(event)
