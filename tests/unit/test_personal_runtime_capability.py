import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.api.event import request_group_reply_candidate
from astrbot.core.cron.events import CronMessageEvent
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.interaction.expression_agent import PersonaExpressionResult
from astrbot.core.interaction.group_reply import (
    GROUP_REPLY_CANDIDATE_EXTRA,
    mark_group_reply_candidate,
)
from astrbot.core.interaction.middleware import InteractionMiddleware
from astrbot.core.interaction.observation import (
    RuntimeObservation,
    RuntimeObservationTarget,
)
from astrbot.core.interaction.observation_inbox import (
    ObservationAdmissionStatus,
    ObservationBatch,
    ObservationMaterial,
)
from astrbot.core.interaction.output_controller import InteractionOutputController
from astrbot.core.interaction.personal_action import PersonalActionIntent
from astrbot.core.interaction.personal_expression_guard import (
    fingerprint_personal_expression,
)
from astrbot.core.interaction.personal_gate import (
    DeterministicObservationGate,
    ObservationFeatureBuilder,
    ObservationGateDisposition,
    ObservationGateReason,
    ObservationGateSettings,
)
from astrbot.core.interaction.personal_policy import (
    PersonalPolicyAction,
    PersonalPolicyDecision,
    PersonalPolicyEvaluation,
    PersonalPolicyEvaluationStatus,
    PersonalPolicyReason,
)
from astrbot.core.interaction.personal_runtime import (
    PersonalRuntimeKey,
    PersonalRuntimeManager,
    PersonalSessionRuntime,
)
from astrbot.core.interaction.personal_state import PersonalDeliveryStatus
from astrbot.core.interaction.personal_state_repository import PersonalStateRepository
from astrbot.core.interaction.runtime_event import RuntimeObservationEvent
from astrbot.core.interaction.turn_state import (
    InteractionFinalOutputStatus,
    append_interaction_turn_visible_output,
    finish_interaction_turn_final_output,
    get_interaction_turn_state,
    reserve_interaction_turn_final_output,
)
from astrbot.core.interaction.types import (
    InteractionRouteDecision,
    InteractionRouteMode,
)
from astrbot.core.message.components import Plain, Record
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.star.context import Context


class _Platform:
    def __init__(self, metadata: PlatformMetadata) -> None:
        self._metadata = metadata

    def meta(self) -> PlatformMetadata:
        return self._metadata


class _RecordingPlatform(_Platform):
    def __init__(self, metadata: PlatformMetadata) -> None:
        super().__init__(metadata)
        self.sent: list[tuple[MessageSession, MessageChain]] = []

    async def send_by_session(
        self,
        session: MessageSession,
        message_chain: MessageChain,
    ) -> None:
        self.sent.append((session, message_chain))


class _FailingPlatform(_Platform):
    async def send_by_session(
        self,
        _session: MessageSession,
        _message_chain: MessageChain,
    ) -> None:
        raise RuntimeError("platform send failed")


class _DirectEvent(AstrMessageEvent):
    def __init__(
        self,
        metadata: PlatformMetadata,
        *,
        fail_send: bool = False,
        message_type: MessageType = MessageType.FRIEND_MESSAGE,
        session_id: str = "target",
        sender_id: str = "user-1",
    ) -> None:
        message = AstrBotMessage()
        message.type = message_type
        message.self_id = "bot"
        message.session_id = session_id
        message.message_id = "user-message-1"
        message.sender = MessageMember(user_id=sender_id, nickname="User")
        message.message = [Plain("hello")]
        message.message_str = "hello"
        message.raw_message = {"post_type": "message"}
        super().__init__("hello", message, metadata, session_id)
        self.fail_send = fail_send
        self.sent: list[MessageChain] = []

    async def send(self, message: MessageChain) -> None:
        if self.fail_send:
            raise RuntimeError("direct send failed")
        self.sent.append(message)
        self._has_send_oper = True


def _context_for_target(metadata: PlatformMetadata) -> Context:
    context = Context.__new__(Context)
    context._config = {
        "platform_settings": {
            "proactive_message_target": "demo:FriendMessage:target",
            "personal_runtime_observation_targets": ["demo:FriendMessage:target"],
        }
    }
    context.platform_manager = SimpleNamespace(platform_insts=[_Platform(metadata)])
    return context


def _metadata(*, support_personal_runtime: bool = False) -> PlatformMetadata:
    return PlatformMetadata(
        name="demo",
        description="demo",
        id="demo",
        support_proactive_message=True,
        support_personal_runtime=support_personal_runtime,
    )


class _ConversationManager:
    async def get_curr_conversation_id(self, _umo: str):
        return None


class _PersonaManager:
    async def resolve_selected_persona(self, **_kwargs):
        return "default", {}, None, None


class _RuntimeContext:
    def __init__(self, metadata: PlatformMetadata) -> None:
        self._platform = _Platform(metadata)
        self.platform_manager = SimpleNamespace(platform_insts=[self._platform])
        self.conversation_manager = _ConversationManager()
        self.persona_manager = _PersonaManager()

    def get_platform_inst(self, platform_id: str):
        if platform_id == self._platform.meta().id:
            return self._platform
        return None


def _context_for_runtime(platform: _Platform) -> Context:
    context = Context.__new__(Context)
    context.platform_manager = SimpleNamespace(platform_insts=[platform])
    context.conversation_manager = _ConversationManager()
    context.persona_manager = _PersonaManager()
    context._proactive_message_dispatcher = None
    return context


def _runtime_event(
    context: _RuntimeContext,
    metadata: PlatformMetadata,
) -> RuntimeObservationEvent:
    observation = RuntimeObservation(
        kind="personal_action",
        source="test",
        occurred_at=1.0,
        target_session=RuntimeObservationTarget(
            platform_id=metadata.id,
            platform_name=metadata.name,
            message_type=MessageType.FRIEND_MESSAGE,
            session_id="target",
            support_proactive_message=metadata.support_proactive_message,
            support_personal_runtime=metadata.support_personal_runtime,
        ),
        payload={"visible_reply_material": "hello"},
    )
    return RuntimeObservationEvent(context=context, observation=observation)


async def _submit_direct_output(
    *,
    manager: PersonalRuntimeManager,
    context: Context,
    event: _DirectEvent,
) -> None:
    runtime_config = {
        "interaction_middleware": {
            "personal_runtime_reply_cooldown_seconds": 45,
        }
    }
    controller = InteractionOutputController()
    event.set_extra("_interaction_output_controller", controller)
    async with manager.submit_platform_event(
        event,
        "default",
        context,
        runtime_config,
    ) as submission:
        admission = await submission.admit(allow_follow_up=False)
        assert admission.lease is not None
        try:
            with manager.activate_turn(admission.turn):
                await event.emit_output(
                    MessageChain([Plain("direct reply")]),
                    mode="direct",
                )
        finally:
            await admission.lease.release()


def test_personal_runtime_targets_require_explicit_adapter_support():
    context = _context_for_target(_metadata())

    assert context.get_proactive_message_target() is not None
    assert context.get_runtime_observation_targets() == ()


def test_personal_runtime_targets_accept_explicit_adapter_support():
    context = _context_for_target(_metadata(support_personal_runtime=True))

    targets = context.get_runtime_observation_targets()

    assert len(targets) == 1
    assert str(targets[0]) == "demo:FriendMessage:target"


def test_personal_runtime_targets_aggregate_effective_config_profiles():
    default_config = {
        "platform_settings": {
            "personal_runtime_observation_targets": [
                "default:FriendMessage:one",
            ],
        }
    }
    alice_config = {
        "platform_settings": {
            "personal_runtime_observation_targets": [
                "alice:GroupMessage:two",
            ],
        }
    }
    unbound_config = {
        "platform_settings": {
            "personal_runtime_observation_targets": [
                "unbound:FriendMessage:three",
            ],
        }
    }

    class _ConfigManager:
        confs = {
            "default": default_config,
            "alice": alice_config,
            "unbound": unbound_config,
        }

        @staticmethod
        def get_conf(umo):
            return (
                alice_config
                if str(umo).startswith("alice:")
                else default_config
            )

    context = Context.__new__(Context)
    context._config = default_config
    context.astrbot_config_mgr = _ConfigManager()
    context.platform_manager = SimpleNamespace(
        platform_insts=[
            _Platform(
                PlatformMetadata(
                    name=platform_id,
                    description=platform_id,
                    id=platform_id,
                    support_proactive_message=True,
                    support_personal_runtime=True,
                )
            )
            for platform_id in ("default", "alice", "unbound")
        ]
    )

    targets = context.get_runtime_observation_targets()

    assert [str(target) for target in targets] == [
        "default:FriendMessage:one",
        "alice:GroupMessage:two",
    ]
    assert [
        str(target)
        for target in context.get_runtime_observation_targets(
            umo="alice:GroupMessage:two"
        )
    ] == ["alice:GroupMessage:two"]


def test_personal_expression_fingerprint_ignores_formatting_only_changes():
    expected = fingerprint_personal_expression("Direct reply")

    assert expected is not None
    assert fingerprint_personal_expression(" direct REPLY!!! ") == expected
    assert fingerprint_personal_expression("Direct reply🙂") != expected


async def _deliver_runtime_output(
    event: RuntimeObservationEvent,
    message: MessageChain,
) -> None:
    if not await reserve_interaction_turn_final_output(event):
        return
    try:
        await event.send(message)
    except Exception:
        await finish_interaction_turn_final_output(
            event,
            InteractionFinalOutputStatus.FAILED,
        )
        raise
    append_interaction_turn_visible_output(
        event,
        message_kind="plugin_direct",
        text=message.get_plain_text(),
        delivered_message_ids=["test-delivery"],
    )
    await finish_interaction_turn_final_output(
        event,
        InteractionFinalOutputStatus.DELIVERED,
    )


@pytest.mark.asyncio
async def test_context_send_message_keeps_proactive_message_boundary():
    metadata = _metadata()
    platform = _RecordingPlatform(metadata)
    context = _context_for_runtime(platform)
    manager = PersonalRuntimeManager()

    class Middleware:
        async def handle_runtime_output(self, event, _turn, message):
            await _deliver_runtime_output(event, message)

    async def dispatcher(session, message, finalize):
        return await manager.dispatch_proactive_message(
            context=context,
            middleware=Middleware(),
            config_id="default",
            runtime_config={},
            session=session,
            message=message,
            finalize=finalize,
        )

    context.set_proactive_message_dispatcher(dispatcher)
    session = MessageSession("demo", MessageType.FRIEND_MESSAGE, "target")
    message = MessageChain([Plain("done")])

    assert await context.send_message(session, message)
    assert platform.sent == [(session, message)]


@pytest.mark.asyncio
async def test_autonomous_expression_requires_personal_runtime_support():
    metadata = _metadata()
    context = _RuntimeContext(metadata)
    manager = PersonalRuntimeManager()
    event = _runtime_event(context, metadata)
    event.set_extra("_personal_runtime_submission_kind", "personal_expression")

    async def handler(_runtime_event, _turn):
        return "unexpected"

    with pytest.raises(RuntimeError, match="Personal Runtime output"):
        await manager.submit_runtime_observation_event(
            event,
            "default",
            context,
            {},
            handler,
        )


@pytest.mark.asyncio
async def test_failed_autonomous_expression_does_not_consume_cooldown_or_quota():
    metadata = _metadata(support_personal_runtime=True)
    context = _context_for_runtime(_FailingPlatform(metadata))
    manager = PersonalRuntimeManager()
    event = _runtime_event(context, metadata)
    event.set_extra("_personal_runtime_submission_kind", "personal_expression")
    event.set_extra("_personal_action_id", "action-1")

    async def handler(runtime_event, _turn):
        await _deliver_runtime_output(runtime_event, MessageChain([Plain("hello")]))

    with pytest.raises(RuntimeError, match="platform send failed"):
        await manager.submit_runtime_observation_event(
            event,
            "default",
            context,
            {},
            handler,
        )

    snapshot = manager.snapshot_diagnostics()
    assert len(snapshot.sessions) == 1
    runtime = snapshot.sessions[0]
    assert runtime.last_completion_feedback is not None
    assert (
        runtime.last_completion_feedback.delivery_status
        is PersonalDeliveryStatus.FAILED
    )
    assert runtime.state.daily_proactive_outputs == 0
    assert runtime.state.reply_cooldown_until is None


@pytest.mark.asyncio
async def test_delivered_autonomous_expression_consumes_cooldown_and_quota():
    metadata = _metadata(support_personal_runtime=True)
    context = _context_for_runtime(_RecordingPlatform(metadata))
    manager = PersonalRuntimeManager()
    event = _runtime_event(context, metadata)
    event.set_extra("_personal_runtime_submission_kind", "personal_expression")
    event.set_extra("_personal_action_id", "action-1")

    async def handler(runtime_event, _turn):
        await _deliver_runtime_output(runtime_event, MessageChain([Plain("hello")]))

    await manager.submit_runtime_observation_event(
        event,
        "default",
        context,
        {
            "interaction_middleware": {
                "personal_runtime_reply_cooldown_seconds": 45,
            }
        },
        handler,
    )

    snapshot = manager.snapshot_diagnostics()
    runtime = snapshot.sessions[0]
    feedback = runtime.last_completion_feedback
    assert feedback is not None
    assert feedback.delivery_status is PersonalDeliveryStatus.DELIVERED
    assert feedback.output_completed_at is not None
    assert runtime.state.reply_cooldown_until == pytest.approx(
        feedback.output_completed_at + 45
    )
    assert runtime.state.daily_proactive_outputs == 1


@pytest.mark.asyncio
async def test_delivered_output_without_action_id_starts_cooldown_without_quota():
    metadata = _metadata()
    context = _context_for_runtime(_RecordingPlatform(metadata))
    manager = PersonalRuntimeManager()
    event = _runtime_event(context, metadata)
    event.set_extra(
        "_personal_runtime_submission_kind",
        "explicit_proactive_output",
    )

    async def handler(runtime_event, _turn):
        await _deliver_runtime_output(runtime_event, MessageChain([Plain("hello")]))

    await manager.submit_runtime_observation_event(
        event,
        "default",
        context,
        {
            "interaction_middleware": {
                "personal_runtime_reply_cooldown_seconds": 45,
            }
        },
        handler,
    )

    snapshot = manager.snapshot_diagnostics()
    runtime = snapshot.sessions[0]
    feedback = runtime.last_completion_feedback
    assert feedback is not None
    assert feedback.delivery_status is PersonalDeliveryStatus.DELIVERED
    assert feedback.output_completed_at is not None
    assert runtime.state.reply_cooldown_until == pytest.approx(
        feedback.output_completed_at + 45
    )
    assert runtime.state.daily_proactive_outputs == 0


@pytest.mark.asyncio
async def test_direct_reply_delivery_starts_gate_cooldown_without_proactive_quota():
    metadata = _metadata(support_personal_runtime=True)
    context = _context_for_runtime(_RecordingPlatform(metadata))
    manager = PersonalRuntimeManager()
    event = _DirectEvent(metadata)

    await _submit_direct_output(manager=manager, context=context, event=event)

    runtime = manager.snapshot_diagnostics().sessions[0]
    feedback = runtime.last_completion_feedback
    assert event.sent[0].get_plain_text() == "direct reply"
    assert feedback is not None
    assert feedback.delivery_status is PersonalDeliveryStatus.DELIVERED
    assert feedback.output_completed_at is not None
    assert runtime.state.reply_cooldown_until == pytest.approx(
        feedback.output_completed_at + 45
    )
    assert runtime.state.daily_proactive_outputs == 0

    evaluated_at = feedback.output_completed_at + 1
    observation = RuntimeObservation(
        kind="heartbeat",
        source="test",
        occurred_at=evaluated_at,
        target_session=RuntimeObservationTarget(
            platform_id=metadata.id,
            platform_name=metadata.name,
            message_type=MessageType.FRIEND_MESSAGE,
            session_id="target",
            support_proactive_message=True,
            support_personal_runtime=True,
        ),
        payload={"visible_reply_material": "follow up"},
    )
    batch = ObservationBatch.create(
        runtime_key=runtime.key,
        opened_at=evaluated_at,
        closed_at=evaluated_at,
        observations=[observation],
        material_by_observation_id={
            observation.observation_id: ObservationMaterial(
                revision=1,
                occurred_at=observation.occurred_at,
            ),
        },
    )
    settings = ObservationGateSettings()
    features = ObservationFeatureBuilder.build(
        batch,
        state=runtime.state,
        runtime_busy=False,
        settings=settings,
        evaluated_at=evaluated_at,
    )
    gate_result = DeterministicObservationGate.evaluate(
        batch,
        state=runtime.state,
        features=features,
        settings=settings,
        evaluated_at=evaluated_at,
    )
    assert gate_result.disposition is ObservationGateDisposition.HOLD
    assert gate_result.reason_code is ObservationGateReason.REPLY_COOLDOWN


@pytest.mark.asyncio
async def test_group_follow_up_uses_model_gated_continuation(monkeypatch):
    metadata = _metadata(support_personal_runtime=True)
    context = _context_for_runtime(_RecordingPlatform(metadata))
    manager = PersonalRuntimeManager()
    event = _DirectEvent(
        metadata,
        message_type=MessageType.GROUP_MESSAGE,
        session_id="group-1",
    )
    same_actor_follow_up = _DirectEvent(
        metadata,
        message_type=MessageType.GROUP_MESSAGE,
        session_id="group-1",
    )
    other_actor_message = _DirectEvent(
        metadata,
        message_type=MessageType.GROUP_MESSAGE,
        session_id="group-1",
        sender_id="user-2",
    )
    runtime_config = {
        "interaction_middleware": {
            "enabled": True,
            "personal_runtime_conversation_continuation_seconds": 120,
        }
    }
    event.set_extra("_interaction_output_controller", InteractionOutputController())

    async with manager.submit_platform_event(
        event,
        "default",
        context,
        runtime_config,
    ) as submission:
        admission = await submission.admit(allow_follow_up=False)
        assert admission.lease is not None
        try:
            with manager.activate_turn(admission.turn):
                assert manager.classify_group_conversation_continuation(
                    same_actor_follow_up,
                    config_id="default",
                    runtime_config=runtime_config,
                ) == "active"
                assert manager.classify_group_conversation_continuation(
                    other_actor_message,
                    config_id="default",
                    runtime_config=runtime_config,
                ) is None
                await event.emit_output(
                    MessageChain([Plain("group reply")]),
                    mode="direct",
                )
        finally:
            await admission.lease.release()

    feedback = manager.snapshot_diagnostics().sessions[0].last_completion_feedback
    assert feedback is not None
    assert feedback.output_completed_at is not None
    completed_at = feedback.output_completed_at

    monkeypatch.setattr(
        "astrbot.core.interaction.personal_runtime.time.time",
        lambda: completed_at + 5,
    )
    assert manager.classify_group_conversation_continuation(
        same_actor_follow_up,
        config_id="default",
        runtime_config=runtime_config,
    ) == "model"

    monkeypatch.setattr(
        "astrbot.core.interaction.personal_runtime.time.time",
        lambda: completed_at + 30,
    )
    assert manager.classify_group_conversation_continuation(
        same_actor_follow_up,
        config_id="default",
        runtime_config=runtime_config,
    ) == "model"
    assert manager.classify_group_conversation_continuation(
        other_actor_message,
        config_id="default",
        runtime_config=runtime_config,
    ) is None

    monkeypatch.setattr(
        "astrbot.core.interaction.personal_runtime.time.time",
        lambda: completed_at + 120,
    )
    assert manager.classify_group_conversation_continuation(
        same_actor_follow_up,
        config_id="default",
        runtime_config=runtime_config,
    ) is None


@pytest.mark.asyncio
async def test_addressed_turn_starts_persona_without_waiting_for_router(monkeypatch):
    metadata = _metadata(support_personal_runtime=True)
    event = _DirectEvent(metadata)
    runtime_config = {"interaction_middleware": {"enabled": True}}
    middleware = InteractionMiddleware(
        runtime_config,
        InteractionOutputController(),
        SimpleNamespace(get_config=lambda **_kwargs: runtime_config),
    )
    persona_started = asyncio.Event()
    release_router = asyncio.Event()

    async def route_after_persona(*_args, **_kwargs):
        await release_router.wait()
        return InteractionRouteDecision(route_mode=InteractionRouteMode.PERSONA)

    async def start_persona(*_args, **_kwargs):
        persona_started.set()
        return PersonaExpressionResult(spoken_reply="hello")

    middleware.router_agent.route = AsyncMock(side_effect=route_after_persona)
    middleware.persona_runtime.express_visible_reply = AsyncMock(
        side_effect=start_persona
    )
    middleware._materialize_inbound_media = AsyncMock()
    monkeypatch.setattr(
        "astrbot.core.interaction.middleware.dispatch_interaction_lifecycle",
        AsyncMock(),
    )

    task = asyncio.create_task(middleware.handle_pipeline_event(event))
    try:
        await asyncio.wait_for(persona_started.wait(), timeout=1.0)
        assert task.done() is False
    finally:
        release_router.set()
        await task

    assert [message.get_plain_text() for message in event.sent] == ["hello"]
    assert event.is_stopped()


@pytest.mark.asyncio
@pytest.mark.parametrize("router_fails", [False, True])
async def test_model_continuation_silent_route_suppresses_pending_persona(
    monkeypatch,
    router_fails,
):
    metadata = _metadata(support_personal_runtime=True)
    event = _DirectEvent(
        metadata,
        message_type=MessageType.GROUP_MESSAGE,
        session_id="group-1",
    )
    mark_group_reply_candidate(event, kind="continuation")
    runtime_config = {"interaction_middleware": {"enabled": True}}
    middleware = InteractionMiddleware(
        runtime_config,
        InteractionOutputController(),
        SimpleNamespace(get_config=lambda **_kwargs: runtime_config),
    )
    persona_started = asyncio.Event()

    async def hold_persona(*_args, **_kwargs):
        persona_started.set()
        await asyncio.Future()

    async def route_after_persona(*_args, **_kwargs):
        await persona_started.wait()
        if router_fails:
            raise RuntimeError("router failed")
        return InteractionRouteDecision(route_mode=InteractionRouteMode.SILENT)

    route = AsyncMock(side_effect=route_after_persona)
    middleware.router_agent.route = route
    middleware._materialize_inbound_media = AsyncMock()
    middleware.persona_runtime.express_visible_reply = AsyncMock(
        side_effect=hold_persona
    )
    monkeypatch.setattr(
        "astrbot.core.interaction.middleware.dispatch_interaction_lifecycle",
        AsyncMock(),
    )

    await middleware.handle_pipeline_event(event)

    route.assert_awaited_once()
    middleware.persona_runtime.express_visible_reply.assert_awaited_once()
    assert event.sent == []
    assert event.get_extra("_interaction_silent_completed") is True
    assert get_interaction_turn_state(event).speculative_persona_status.value == (
        "suppressed"
    )
    assert event.is_stopped()
    assert bool(event.get_extra("_interaction_router_failed", False)) is router_fails


@pytest.mark.asyncio
async def test_duplicate_autonomous_expression_is_suppressed_without_accounting():
    metadata = _metadata(support_personal_runtime=True)
    platform = _RecordingPlatform(metadata)
    context = _RuntimeContext(metadata)
    context._platform = platform
    context.platform_manager = SimpleNamespace(platform_insts=[platform])
    manager = PersonalRuntimeManager()
    direct_event = _DirectEvent(metadata)

    await _submit_direct_output(
        manager=manager,
        context=context,
        event=direct_event,
    )
    initial_runtime = manager.snapshot_diagnostics().sessions[0]
    initial_cooldown = initial_runtime.state.reply_cooldown_until
    assert initial_cooldown is not None

    event = _runtime_event(context, metadata)
    intent = PersonalActionIntent(
        batch_id="batch-duplicate",
        reply_intent="repeat the previous reply",
        created_at=2.0,
        target_observation=event.observation,
        action_id="action-duplicate",
    )
    event.set_extra("_personal_action_intent", intent)
    event.set_extra("_personal_action_id", intent.action_id)
    event.set_extra("_personal_runtime_submission_kind", "personal_expression")
    controller = InteractionOutputController()
    middleware = InteractionMiddleware(
        {"interaction_middleware": {"enabled": True}},
        controller,
        context,
    )
    expression_requests = []

    async def generate_expression(
        _event,
        _interaction_config,
        *,
        request,
        fallback_on_error,
    ):
        expression_requests.append((request, fallback_on_error))
        return PersonaExpressionResult(spoken_reply=" direct REPLY!!! ")

    middleware._generate_expression = generate_expression

    result = await manager.submit_runtime_observation_event(
        event,
        "default",
        context,
        {"interaction_middleware": {"enabled": True}},
        middleware.handle_runtime_observation,
    )

    runtime = manager.snapshot_diagnostics().sessions[0]
    feedback = runtime.last_completion_feedback
    turn_state = get_interaction_turn_state(event)
    assert result is None
    assert platform.sent == []
    assert expression_requests[0][0].avoid_previous_reply is True
    assert expression_requests[0][1] is False
    assert event.get_extra("_interaction_runtime_observation_skipped_reason") == (
        "duplicate_previous_expression"
    )
    assert turn_state is not None
    assert (
        turn_state.final_output_status
        is InteractionFinalOutputStatus.SUPPRESSED
    )
    assert feedback is not None
    assert feedback.delivery_status is PersonalDeliveryStatus.SUPPRESSED
    assert feedback.action_id == intent.action_id
    assert runtime.state.reply_cooldown_until == initial_cooldown
    assert runtime.state.daily_proactive_outputs == 0


@pytest.mark.asyncio
async def test_autonomous_expression_deduplicates_after_runtime_state_restore(tmp_path):
    metadata = _metadata(support_personal_runtime=True)
    platform = _RecordingPlatform(metadata)
    context = _RuntimeContext(metadata)
    context._platform = platform
    context.platform_manager = SimpleNamespace(platform_insts=[platform])
    database = SQLiteDatabase(str(tmp_path / "personal-runtime.db"))
    manager = None
    restored_manager = None

    try:
        await database.initialize()
        state_repository = PersonalStateRepository(database)
        manager = PersonalRuntimeManager(state_repository=state_repository)
        await _submit_direct_output(
            manager=manager,
            context=context,
            event=_DirectEvent(metadata),
        )

        initial_runtime = manager.snapshot_diagnostics().sessions[0]
        persisted_state = await state_repository.load(initial_runtime.key)
        assert persisted_state is not None
        assert persisted_state.last_expression_fingerprint == fingerprint_personal_expression(
            "direct reply"
        )
        await manager.shutdown()

        restored_manager = PersonalRuntimeManager(state_repository=state_repository)
        event = _runtime_event(context, metadata)
        intent = PersonalActionIntent(
            batch_id="batch-restart-duplicate",
            reply_intent="repeat the previous reply",
            created_at=2.0,
            target_observation=event.observation,
            action_id="action-restart-duplicate",
        )
        event.set_extra("_personal_action_intent", intent)
        event.set_extra("_personal_action_id", intent.action_id)
        event.set_extra("_personal_runtime_submission_kind", "personal_expression")
        middleware = InteractionMiddleware(
            {"interaction_middleware": {"enabled": True}},
            InteractionOutputController(),
            context,
        )

        async def generate_expression(
            _event,
            _interaction_config,
            *,
            request,
            fallback_on_error,
        ):
            assert request.avoid_previous_reply is True
            assert fallback_on_error is False
            return PersonaExpressionResult(spoken_reply=" direct REPLY!!! ")

        middleware._generate_expression = generate_expression
        result = await restored_manager.submit_runtime_observation_event(
            event,
            "default",
            context,
            {"interaction_middleware": {"enabled": True}},
            middleware.handle_runtime_observation,
        )

        runtime = restored_manager.snapshot_diagnostics().sessions[0]
        assert result is None
        assert platform.sent == []
        assert event.get_extra("_interaction_runtime_observation_skipped_reason") == (
            "duplicate_previous_expression"
        )
        assert runtime.last_completion_feedback is not None
        assert (
            runtime.last_completion_feedback.delivery_status
            is PersonalDeliveryStatus.SUPPRESSED
        )
        assert runtime.state.daily_proactive_outputs == 0
    finally:
        if restored_manager is not None:
            await restored_manager.shutdown()
        if manager is not None:
            await manager.shutdown()
        await database.engine.dispose()


@pytest.mark.asyncio
async def test_failed_direct_reply_sets_neither_cooldown_nor_proactive_quota():
    metadata = _metadata(support_personal_runtime=True)
    context = _context_for_runtime(_RecordingPlatform(metadata))
    manager = PersonalRuntimeManager()
    event = _DirectEvent(metadata, fail_send=True)

    with pytest.raises(RuntimeError, match="Interaction output was not delivered"):
        await _submit_direct_output(manager=manager, context=context, event=event)

    runtime = manager.snapshot_diagnostics().sessions[0]
    assert event.sent == []
    assert runtime.state.reply_cooldown_until is None
    assert runtime.state.daily_proactive_outputs == 0


@pytest.mark.asyncio
async def test_personal_runtime_output_keeps_dual_tts_in_one_platform_send():
    metadata = _metadata(support_personal_runtime=True)
    platform = _RecordingPlatform(metadata)
    context = _context_for_runtime(platform)
    manager = PersonalRuntimeManager()
    event = _runtime_event(context, metadata)
    controller = InteractionOutputController()
    segment = {
        "output_segment": {
            "turn_id": "turn-1",
            "message_id": "message-1",
            "tts": {"tts_request_id": "tts-1", "status": "succeeded"},
        }
    }

    async def handler(runtime_event, _turn):
        await controller.capture_plugin_output(
            MessageChain(
                [
                    Record(
                        file="reply.wav",
                        delivery_metadata={**segment, "audio_attachment": "present"},
                    ),
                    Plain(
                        "reply",
                        delivery_metadata={**segment, "audio_attachment": "absent"},
                    ),
                ]
            ),
            runtime_event,
        )

    await manager.submit_runtime_observation_event(
        event,
        "default",
        context,
        {},
        handler,
    )

    assert len(platform.sent) == 1
    assert [type(comp) for comp in platform.sent[0][1].chain] == [Record, Plain]


@pytest.mark.asyncio
async def test_cron_send_uses_context_send_message_compatibility_path():
    metadata = _metadata()
    platform = _RecordingPlatform(metadata)
    context = _context_for_runtime(platform)
    manager = PersonalRuntimeManager()

    class Middleware:
        async def handle_runtime_output(self, event, _turn, message):
            await _deliver_runtime_output(event, message)

    async def dispatcher(session, message, finalize):
        return await manager.dispatch_proactive_message(
            context=context,
            middleware=Middleware(),
            config_id="default",
            runtime_config={},
            session=session,
            message=message,
            finalize=finalize,
        )

    context.set_proactive_message_dispatcher(dispatcher)
    session = MessageSession("demo", MessageType.FRIEND_MESSAGE, "target")
    event = CronMessageEvent(
        context=context,
        session=session,
        message="tick",
    )
    message = MessageChain([Plain("done")])

    await event.send(message)

    assert platform.sent == [(session, message)]


async def _wait_for_observation_evaluation(manager: PersonalRuntimeManager) -> None:
    for _ in range(100):
        snapshot = manager.snapshot_diagnostics()
        if snapshot.sessions and not snapshot.sessions[0].observation_evaluation_active:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Personal Runtime observation evaluation did not settle")


@pytest.mark.asyncio
async def test_heartbeat_only_wakes_unsettled_material():
    metadata = _metadata(support_personal_runtime=True)
    context = _RuntimeContext(metadata)
    manager = PersonalRuntimeManager(observation_debounce_seconds=0)
    policy_batches: list[ObservationBatch] = []

    async def evaluate_policy(**kwargs):
        batch = kwargs["batch"]
        policy_batches.append(batch)
        return PersonalPolicyEvaluation(
            batch_id=batch.batch_id,
            status=PersonalPolicyEvaluationStatus.EVALUATED,
            decision=PersonalPolicyDecision(
                action=PersonalPolicyAction.EXPRESS,
                reason_code=PersonalPolicyReason.SOCIAL_OPPORTUNITY,
                reply_intent="say something useful",
                importance=0.8,
                defer_seconds=0,
            ),
            evaluated_at=batch.closed_at,
            provider_id="test",
            provider_call_started=False,
        )

    manager._personal_policy_agent.evaluate = evaluate_policy
    manager.bind_personal_expression_handler(AsyncMock(return_value=True))
    target = RuntimeObservationTarget(
        platform_id=metadata.id,
        platform_name=metadata.name,
        message_type=MessageType.FRIEND_MESSAGE,
        session_id="target",
        support_proactive_message=True,
        support_personal_runtime=True,
    )

    async def submit(kind: str, *, payload=None, coalesce_key=None):
        result = await manager.submit_observation(
            RuntimeObservation(
                kind=kind,
                source="test",
                occurred_at=1.0 + len(policy_batches),
                target_session=target,
                coalesce_key=coalesce_key,
                payload=payload or {},
            ),
            config_id="default",
            plugin_context=context,
            runtime_config={},
        )
        await _wait_for_observation_evaluation(manager)
        return result

    try:
        result = await submit("heartbeat", coalesce_key="heartbeat")
        assert result.status is ObservationAdmissionStatus.IGNORED
        assert len(policy_batches) == 0
        initial_state = manager.snapshot_diagnostics().sessions[0].state
        assert initial_state.reply_cooldown_until is None
        assert initial_state.daily_proactive_outputs == 0
        assert initial_state.material_revision == 0
        assert initial_state.last_settled_material_revision == 0

        await submit("sensor_state", payload={"value": 1}, coalesce_key="state")
        assert len(policy_batches) == 1
        first_batch = policy_batches[0]
        state = manager.snapshot_diagnostics().sessions[0].state
        assert first_batch.material_count == 1
        assert state.last_settled_material_revision == first_batch.material_revision

        result = await submit("heartbeat", coalesce_key="heartbeat")
        assert result.status is ObservationAdmissionStatus.IGNORED
        assert len(policy_batches) == 1

        await submit("sensor_state", payload={"value": 1}, coalesce_key="state")
        assert len(policy_batches) == 1
        assert (
            manager.snapshot_diagnostics()
            .sessions[0]
            .last_observation_gate_result.reason_code
            is ObservationGateReason.MISSING_MATERIAL
        )

        await submit("sensor_state", payload={"value": 2}, coalesce_key="state")
        assert len(policy_batches) == 2
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_heartbeat_does_not_disturb_a_cooldown_held_batch():
    metadata = _metadata(support_personal_runtime=True)
    platform = _RecordingPlatform(metadata)
    context = _context_for_runtime(platform)
    manager = PersonalRuntimeManager(observation_debounce_seconds=0)
    scheduled: list[tuple[object, float]] = []

    class Scheduler:
        def schedule(self, key, due_at):
            scheduled.append((key, due_at))

        def cancel(self, _key):
            return None

    manager.bind_observation_wake_scheduler(Scheduler())
    event = _DirectEvent(metadata)
    await _submit_direct_output(manager=manager, context=context, event=event)
    target = RuntimeObservationTarget(
        platform_id=metadata.id,
        platform_name=metadata.name,
        message_type=MessageType.FRIEND_MESSAGE,
        session_id="target",
        support_proactive_message=True,
        support_personal_runtime=True,
    )

    async def submit(kind: str):
        result = await manager.submit_observation(
            RuntimeObservation(
                kind=kind,
                source="test",
                occurred_at=time.time(),
                target_session=target,
                coalesce_key=kind,
                payload={"value": kind},
            ),
            config_id="default",
            plugin_context=context,
            runtime_config={},
        )
        await _wait_for_observation_evaluation(manager)
        return result

    try:
        await submit("sensor_state")
        runtime = manager.snapshot_diagnostics().sessions[0]
        held_wake_at = runtime.next_observation_wake_at
        assert held_wake_at is not None
        assert len(scheduled) == 1

        result = await submit("heartbeat")
        runtime = manager.snapshot_diagnostics().sessions[0]
        assert result.status is ObservationAdmissionStatus.ADMITTED
        assert runtime.next_observation_wake_at == held_wake_at
        assert runtime.observation_evaluation_active is False
        assert len(scheduled) == 1
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_rejected_material_is_settled_before_a_later_heartbeat():
    metadata = _metadata(support_personal_runtime=True)
    context = _RuntimeContext(metadata)
    manager = PersonalRuntimeManager(observation_debounce_seconds=0)
    policy = AsyncMock()
    manager._personal_policy_agent.evaluate = policy
    target = RuntimeObservationTarget(
        platform_id=metadata.id,
        platform_name=metadata.name,
        message_type=MessageType.FRIEND_MESSAGE,
        session_id="target",
        support_proactive_message=False,
        support_personal_runtime=True,
    )

    async def submit(kind: str, payload=None):
        result = await manager.submit_observation(
            RuntimeObservation(
                kind=kind,
                source="test",
                occurred_at=time.time(),
                target_session=target,
                coalesce_key=kind,
                payload=payload or {},
            ),
            config_id="default",
            plugin_context=context,
            runtime_config={},
        )
        await _wait_for_observation_evaluation(manager)
        return result

    try:
        await submit("sensor_state", {"value": 1})
        runtime = manager.snapshot_diagnostics().sessions[0]
        assert (
            runtime.last_observation_gate_result.reason_code
            is ObservationGateReason.TARGET_UNAVAILABLE
        )
        assert runtime.state.last_settled_material_revision == 1
        assert policy.await_count == 0

        result = await submit("heartbeat")
        assert result.status is ObservationAdmissionStatus.IGNORED
        assert policy.await_count == 0
        assert manager.snapshot_diagnostics().sessions[0].state.pending_observation_count == 0
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_failed_personal_action_settles_material_without_accounting():
    metadata = _metadata(support_personal_runtime=True)
    context = _RuntimeContext(metadata)
    manager = PersonalRuntimeManager(observation_debounce_seconds=0)
    policy_calls = 0

    async def evaluate_policy(**kwargs):
        nonlocal policy_calls
        policy_calls += 1
        batch = kwargs["batch"]
        return PersonalPolicyEvaluation(
            batch_id=batch.batch_id,
            status=PersonalPolicyEvaluationStatus.EVALUATED,
            decision=PersonalPolicyDecision(
                action=PersonalPolicyAction.EXPRESS,
                reason_code=PersonalPolicyReason.SOCIAL_OPPORTUNITY,
                reply_intent="say something useful",
                importance=0.8,
                defer_seconds=0,
            ),
            evaluated_at=batch.closed_at,
            provider_id="test",
            provider_call_started=False,
        )

    manager._personal_policy_agent.evaluate = evaluate_policy
    manager.bind_personal_expression_handler(
        AsyncMock(side_effect=RuntimeError("platform send failed"))
    )
    target = RuntimeObservationTarget(
        platform_id=metadata.id,
        platform_name=metadata.name,
        message_type=MessageType.FRIEND_MESSAGE,
        session_id="target",
        support_proactive_message=True,
        support_personal_runtime=True,
    )

    async def submit(kind: str, payload=None):
        result = await manager.submit_observation(
            RuntimeObservation(
                kind=kind,
                source="test",
                occurred_at=time.time(),
                target_session=target,
                coalesce_key=kind,
                payload=payload or {},
            ),
            config_id="default",
            plugin_context=context,
            runtime_config={},
        )
        await _wait_for_observation_evaluation(manager)
        return result

    try:
        await submit("sensor_state", {"value": 1})
        runtime = manager.snapshot_diagnostics().sessions[0]
        assert policy_calls == 1
        assert runtime.state.last_settled_material_revision == 1
        assert runtime.state.reply_cooldown_until is None
        assert runtime.state.daily_proactive_outputs == 0

        result = await submit("heartbeat")
        assert result.status is ObservationAdmissionStatus.IGNORED
        assert policy_calls == 1
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_model_continuation_preserves_handler_takeover_before_route(
    monkeypatch,
):
    metadata = _metadata(support_personal_runtime=True)
    event = _DirectEvent(
        metadata,
        message_type=MessageType.GROUP_MESSAGE,
        session_id="group-1",
    )
    runtime_config = {
        "admins_id": [],
        "wake_prefix": ["/"],
        "plugin_set": ["*"],
        "platform_settings": {},
        "provider_settings": {"enable": True},
        "interaction_middleware": {"enabled": True},
    }
    handler_lookup = Mock(return_value=[])
    monkeypatch.setattr(
        "astrbot.core.pipeline.waking_check.stage.star_handlers_registry.get_handlers_by_event_type",
        handler_lookup,
    )
    waking = WakingCheckStage()
    await waking.initialize(
        SimpleNamespace(
            astrbot_config=runtime_config,
            astrbot_config_id="default",
            personal_runtime_manager=SimpleNamespace(
                classify_group_conversation_continuation=lambda *_args, **_kwargs: (
                    "model"
                )
            ),
        )
    )

    await waking.process(event)

    assert event.get_extra(GROUP_REPLY_CANDIDATE_EXTRA) is True
    assert event.get_extra("activated_handlers") == []
    handler_lookup.assert_called_once()

    plugin_context = SimpleNamespace(get_config=lambda **_kwargs: runtime_config)
    runtime_context = _context_for_runtime(_RecordingPlatform(metadata))
    manager = PersonalRuntimeManager()
    middleware = InteractionMiddleware(
        runtime_config,
        InteractionOutputController(),
        plugin_context,
    )
    persona_started = asyncio.Event()

    async def hold_persona(*_args, **_kwargs):
        persona_started.set()
        await asyncio.Future()

    async def route_after_persona(*_args, **_kwargs):
        await persona_started.wait()
        return InteractionRouteDecision(route_mode=InteractionRouteMode.SILENT)

    middleware.router_agent.route = AsyncMock(side_effect=route_after_persona)
    middleware.persona_runtime.express_visible_reply = AsyncMock(
        side_effect=hold_persona
    )
    middleware._materialize_inbound_media = AsyncMock()
    monkeypatch.setattr(
        "astrbot.core.interaction.middleware.dispatch_interaction_lifecycle",
        AsyncMock(),
    )

    class NeverAgent:
        called = False

        async def process(self, _event):
            self.called = True
            if False:
                yield

    process = ProcessStage()
    process.ctx = SimpleNamespace(
        astrbot_config=runtime_config,
        astrbot_config_id="default",
        interaction_middleware=middleware,
    )
    process.config = runtime_config
    process.plugin_manager = SimpleNamespace(context=runtime_context)
    process.personal_runtime_manager = manager
    process.agent_sub_stage = NeverAgent()
    process.star_request_sub_stage = SimpleNamespace()

    async for _ in process.process(event):
        pass

    middleware.router_agent.route.assert_awaited_once()
    middleware.persona_runtime.express_visible_reply.assert_awaited_once()
    assert process.agent_sub_stage.called is False
    assert event.sent == []
    assert event.is_stopped()
    assert manager.snapshot_diagnostics().sessions[0].state.material_revision == 0
    await manager.shutdown()

@pytest.mark.asyncio
async def test_handler_group_reply_candidate_reaches_silent_router_once(monkeypatch):
    metadata = _metadata(support_personal_runtime=True)
    event = _DirectEvent(
        metadata,
        message_type=MessageType.GROUP_MESSAGE,
        session_id="group-1",
    )
    event.set_extra("activated_handlers", [object()])
    runtime_config = {
        "provider_settings": {"enable": True},
        "platform_settings": {},
        "interaction_middleware": {"enabled": True},
    }
    plugin_context = SimpleNamespace(get_config=lambda **_kwargs: runtime_config)
    middleware = InteractionMiddleware(
        runtime_config,
        InteractionOutputController(),
        plugin_context,
    )
    persona_started = asyncio.Event()

    async def hold_persona(*_args, **_kwargs):
        persona_started.set()
        await asyncio.Future()

    async def route_after_persona(*_args, **_kwargs):
        await persona_started.wait()
        return InteractionRouteDecision(route_mode=InteractionRouteMode.SILENT)

    middleware.router_agent.route = AsyncMock(side_effect=route_after_persona)
    middleware.persona_runtime.express_visible_reply = AsyncMock(
        side_effect=hold_persona
    )
    middleware._materialize_inbound_media = AsyncMock()
    monkeypatch.setattr(
        "astrbot.core.interaction.middleware.dispatch_interaction_lifecycle",
        AsyncMock(),
    )

    class CandidateHandlerStage:
        async def process(self, handler_event):
            assert request_group_reply_candidate(handler_event)
            assert handler_event.is_wake is False
            assert handler_event.is_at_or_wake_command is False
            if False:
                yield

    class NeverAgent:
        called = False

        async def process(self, _event):
            self.called = True
            if False:
                yield

    process = ProcessStage()
    process.ctx = SimpleNamespace(
        astrbot_config=runtime_config,
        astrbot_config_id="default",
        interaction_middleware=middleware,
    )
    process.config = runtime_config
    process.plugin_manager = SimpleNamespace(context=plugin_context)
    process.personal_runtime_manager = None
    process.agent_sub_stage = NeverAgent()
    process.star_request_sub_stage = CandidateHandlerStage()

    async for _ in process.process(event):
        pass

    middleware.router_agent.route.assert_awaited_once()
    middleware.persona_runtime.express_visible_reply.assert_awaited_once()
    assert process.agent_sub_stage.called is False
    assert event.sent == []
    assert event.is_stopped()


@pytest.mark.asyncio
async def test_idle_initiation_is_once_per_user_activity_and_stale_after_new_input():
    key = PersonalRuntimeKey(
        config_id="default",
        persona_id="Alice",
        audience_key="test:FriendMessage:target",
        privacy_scope="direct",
    )
    runtime = PersonalSessionRuntime(
        key,
        observation_debounce_seconds=60.0,
    )
    target = RuntimeObservationTarget(
        platform_id="test",
        platform_name="test",
        message_type=MessageType.FRIEND_MESSAGE,
        session_id="target",
        support_proactive_message=True,
        support_personal_runtime=True,
    )
    occurred_at = time.time()
    runtime.state.last_user_activity_at = occurred_at - 300.0
    try:
        first = await runtime.submit_idle_initiation(
            target,
            occurred_at=occurred_at,
            minimum_idle_seconds=300.0,
        )
        assert first.admitted
        assert runtime.state.last_idle_initiation_activity_at == occurred_at - 300.0

        duplicate = await runtime.submit_idle_initiation(
            target,
            occurred_at=occurred_at + 100.0,
            minimum_idle_seconds=300.0,
        )
        assert duplicate.status is ObservationAdmissionStatus.IGNORED
        assert duplicate.reason_codes == ("idle_initiation_already_submitted",)

        runtime.state.last_user_activity_at = occurred_at + 1.0
        batch = runtime.observation_inbox.drain(
            runtime_key=key,
            closed_at=occurred_at + 1.0,
        )
        assert batch is not None
        snapshot = runtime.state.snapshot()
        features = ObservationFeatureBuilder.build(
            batch,
            state=snapshot,
            runtime_busy=False,
            settings=runtime.observation_gate_settings,
            evaluated_at=occurred_at + 1.0,
        )
        gate = DeterministicObservationGate.evaluate(
            batch,
            state=snapshot,
            features=features,
            settings=runtime.observation_gate_settings,
            evaluated_at=occurred_at + 1.0,
        )
        assert gate.reason_code is ObservationGateReason.STALE_IDLE_INITIATION

        second = await runtime.submit_idle_initiation(
            target,
            occurred_at=occurred_at + 301.0,
            minimum_idle_seconds=300.0,
        )
        assert second.admitted
        assert runtime.state.last_idle_initiation_activity_at == occurred_at + 1.0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_idle_initiation_retries_after_persistence_failure():
    class Repository:
        def __init__(self):
            self.fail = True
            self.saved_states = []

        async def save(self, _key, state):
            if self.fail:
                raise RuntimeError("temporary database failure")
            self.saved_states.append(state)

    key = PersonalRuntimeKey(
        config_id="default",
        persona_id="Alice",
        audience_key="test:FriendMessage:target",
        privacy_scope="direct",
    )
    target = RuntimeObservationTarget(
        platform_id="test",
        platform_name="test",
        message_type=MessageType.FRIEND_MESSAGE,
        session_id="target",
        support_proactive_message=True,
        support_personal_runtime=True,
    )
    repository = Repository()
    runtime = PersonalSessionRuntime(
        key,
        state_repository=repository,
        observation_debounce_seconds=60.0,
    )
    occurred_at = time.time()
    runtime.state.last_user_activity_at = occurred_at - 300.0
    try:
        failed = await runtime.submit_idle_initiation(
            target,
            occurred_at=occurred_at,
            minimum_idle_seconds=300.0,
        )
        assert failed.reason_codes == ("idle_initiation_persistence_failed",)
        assert runtime.state.last_idle_initiation_activity_at is None

        repository.fail = False
        retried = await runtime.submit_idle_initiation(
            target,
            occurred_at=occurred_at + 1.0,
            minimum_idle_seconds=300.0,
        )
        assert retried.admitted
        assert runtime.state.last_idle_initiation_activity_at == occurred_at - 300.0
        assert len(repository.saved_states) == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_user_activity_persists_without_a_visible_reply():
    class Repository:
        def __init__(self):
            self.states = []

        async def load(self, _key):
            return None

        async def save(self, _key, state):
            self.states.append(state)

    metadata = _metadata(support_personal_runtime=True)
    context = _context_for_runtime(_RecordingPlatform(metadata))
    repository = Repository()
    manager = PersonalRuntimeManager(state_repository=repository)
    event = _DirectEvent(metadata)
    runtime_config = {"interaction_middleware": {"enabled": True}}

    try:
        async with manager.submit_platform_event(
            event,
            "default",
            context,
            runtime_config,
        ) as submission:
            admission = await submission.admit(allow_follow_up=False)
            assert admission.lease is not None
            await admission.lease.release()

        assert repository.states
        assert repository.states[-1].last_user_activity_at is not None
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_process_stage_direct_reply_starts_runtime_cooldown():
    metadata = _metadata(support_personal_runtime=True)
    context = _context_for_runtime(_RecordingPlatform(metadata))
    manager = PersonalRuntimeManager()
    event = _DirectEvent(metadata)
    event.is_at_or_wake_command = True
    event.set_extra("activated_handlers", [])
    runtime_config = {
        "provider_settings": {"enable": True},
        "interaction_middleware": {
            "enabled": True,
            "personal_runtime_reply_cooldown_seconds": 45,
        },
    }
    middleware = InteractionMiddleware(
        runtime_config,
        InteractionOutputController(),
        SimpleNamespace(get_config=lambda **_kwargs: runtime_config),
    )
    middleware.handle_pipeline_event = AsyncMock()

    class ReplyAgent:
        async def process(self, agent_event):
            await agent_event.emit_output(
                MessageChain([Plain("process reply")]),
                mode="direct",
            )
            yield

    process = ProcessStage()
    process.ctx = SimpleNamespace(
        astrbot_config=runtime_config,
        astrbot_config_id="default",
        interaction_middleware=middleware,
    )
    process.config = runtime_config
    process.plugin_manager = SimpleNamespace(context=context)
    process.personal_runtime_manager = manager
    process.agent_sub_stage = ReplyAgent()
    process.star_request_sub_stage = SimpleNamespace()

    async for _ in process.process(event):
        pass

    runtime = manager.snapshot_diagnostics().sessions[0]
    feedback = runtime.last_completion_feedback
    assert event.sent[0].get_plain_text() == "process reply"
    assert feedback is not None
    assert feedback.delivery_status is PersonalDeliveryStatus.DELIVERED
    assert feedback.output_completed_at is not None
    assert runtime.state.reply_cooldown_until == pytest.approx(
        feedback.output_completed_at + 45
    )
    assert runtime.state.daily_proactive_outputs == 0
    assert runtime.state.material_revision == 0
    await manager.shutdown()


@pytest.mark.asyncio
async def test_process_stage_can_close_from_another_task():
    metadata = _metadata(support_personal_runtime=True)
    context = _context_for_runtime(_RecordingPlatform(metadata))
    manager = PersonalRuntimeManager()
    event = _DirectEvent(metadata)
    event.is_at_or_wake_command = True
    event.set_extra("activated_handlers", [])
    runtime_config = {
        "provider_settings": {"enable": True},
        "interaction_middleware": {"enabled": True},
    }
    middleware = InteractionMiddleware(
        runtime_config,
        InteractionOutputController(),
        SimpleNamespace(get_config=lambda **_kwargs: runtime_config),
    )
    middleware.handle_pipeline_event = AsyncMock()
    agent_closed = asyncio.Event()

    class YieldingAgent:
        async def process(self, _event):
            try:
                yield
            finally:
                agent_closed.set()

    process = ProcessStage()
    process.ctx = SimpleNamespace(
        astrbot_config=runtime_config,
        astrbot_config_id="default",
        interaction_middleware=middleware,
    )
    process.config = runtime_config
    process.plugin_manager = SimpleNamespace(context=context)
    process.personal_runtime_manager = manager
    process.agent_sub_stage = YieldingAgent()
    process.star_request_sub_stage = SimpleNamespace()
    generator = process.process(event)

    try:
        await anext(generator)
        assert manager.snapshot_diagnostics().sessions[0].active_turn_id is not None

        await asyncio.create_task(generator.aclose())

        assert agent_closed.is_set()
        assert manager.snapshot_diagnostics().sessions[0].active_turn_id is None
    finally:
        await generator.aclose()
        await manager.shutdown()


@pytest.mark.asyncio
async def test_pipeline_scheduler_preserves_active_turn_for_downstream_stages():
    metadata = _metadata(support_personal_runtime=True)
    platform = _RecordingPlatform(metadata)
    context = _context_for_runtime(platform)
    manager = PersonalRuntimeManager()
    event = _DirectEvent(metadata)
    event.is_at_or_wake_command = True
    event.set_extra("activated_handlers", [])
    runtime_config = {
        "provider_settings": {"enable": True},
        "interaction_middleware": {"enabled": True},
    }
    middleware = InteractionMiddleware(
        runtime_config,
        InteractionOutputController(),
        SimpleNamespace(get_config=lambda **_kwargs: runtime_config),
    )
    middleware.handle_pipeline_event = AsyncMock()
    middleware.handle_active_turn_output = AsyncMock()
    session = MessageSession("demo", MessageType.FRIEND_MESSAGE, "target")
    message = MessageChain([Plain("downstream")])

    async def dispatcher(target, chain, finalize):
        return await manager.dispatch_proactive_message(
            context=context,
            middleware=middleware,
            config_id="default",
            runtime_config=runtime_config,
            session=target,
            message=chain,
            finalize=finalize,
        )

    context.set_proactive_message_dispatcher(dispatcher)
    agent_closed = asyncio.Event()

    class YieldingAgent:
        async def process(self, _event):
            try:
                yield
            finally:
                agent_closed.set()

    class DownstreamStage:
        def __init__(self):
            self.called = False

        async def process(self, _event):
            if self.called:
                return
            self.called = True
            assert await context.send_message(session, message)
            _event.stop_event()

    process = ProcessStage()
    process.ctx = SimpleNamespace(
        astrbot_config=runtime_config,
        astrbot_config_id="default",
        interaction_middleware=middleware,
    )
    process.config = runtime_config
    process.plugin_manager = SimpleNamespace(context=context)
    process.personal_runtime_manager = manager
    process.agent_sub_stage = YieldingAgent()
    process.star_request_sub_stage = SimpleNamespace()
    scheduler = PipelineScheduler.__new__(PipelineScheduler)
    scheduler.ctx = SimpleNamespace(personal_runtime_manager=manager)
    scheduler.stages = [process, DownstreamStage()]

    try:
        await asyncio.wait_for(scheduler._process_stages(event), timeout=2)

        middleware.handle_active_turn_output.assert_awaited_once()
        call = middleware.handle_active_turn_output.await_args
        assert call.args[0].event is event
        assert call.args[1] is message
        assert call.kwargs == {"finalize": True}
        assert agent_closed.is_set()
        assert platform.sent == []
        assert manager.snapshot_diagnostics().sessions[0].active_turn_id is None
    finally:
        await manager.shutdown()
