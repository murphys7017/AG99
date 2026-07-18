import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot.core.interaction.config import (
    is_middleware_enabled,
    load_interaction_agent_config,
)
from astrbot.core.interaction.core_planner import CorePlannerError
from astrbot.core.interaction.expression_agent import PersonaExpressionResult
from astrbot.core.interaction.middleware import (
    InteractionMiddleware as RuntimeInteractionMiddleware,
)
from astrbot.core.interaction.output_controller import InteractionOutputController
from astrbot.core.interaction.output_modes import OutputOrigin, temporary_output_origin
from astrbot.core.interaction.turn_state import (
    InteractionSpeculativePersonaStatus,
    InteractionTurnOutcome,
    InteractionTurnState,
    get_interaction_turn_finalized_material,
    get_interaction_turn_state,
)
from astrbot.core.interaction.types import (
    CorePlanningAction,
    CorePlanningDecision,
    CoreTaskSpec,
    InteractionAgentConfig,
    InteractionRouteDecision,
    InteractionRouteMode,
)
from astrbot.core.message.components import Image, Plain, Record, Reply
from astrbot.core.message.message_event_result import (
    MessageChain,
    MessageEventResult,
    ResultContentType,
)
from astrbot.core.pipeline.preprocess_stage.stage import PreProcessStage
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.pipeline.respond.stage import RespondStage
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.postprocess.types import PostProcessTrigger
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context


class ConcreteAstrMessageEvent(AstrMessageEvent):
    async def send(self, message):
        await super().send(message)


class StreamingAstrMessageEvent(ConcreteAstrMessageEvent):
    async def send_streaming(self, generator, use_fallback: bool = False) -> None:
        async for _chain in generator:
            pass
        await super().send_streaming(generator, use_fallback=use_fallback)


class FakeSTTProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[str] = []

    async def get_text(self, audio_url: str) -> str:
        self.calls.append(audio_url)
        return self.text


class InteractionMiddleware(RuntimeInteractionMiddleware):
    """Exercise the Pipeline entry while retaining queue-oriented test assertions."""

    def __init__(
        self,
        config,
        continuation_queue,
        output_controller,
        plugin_context=None,
    ) -> None:
        super().__init__(config, output_controller, plugin_context)
        self._test_continuation_queue = continuation_queue

    def continue_pipeline(self, event: AstrMessageEvent) -> None:
        async def _run_pipeline_and_continue() -> None:
            runtime_config = self._get_runtime_config(event)
            if not is_middleware_enabled(runtime_config):
                self._test_continuation_queue.put_nowait(event)
                return
            await self.handle_pipeline_event(event)
            if event.get_extra("_interaction_delegate_to_core"):
                self._test_continuation_queue.put_nowait(event)

        self._spawn_background_task(
            _run_pipeline_and_continue(),
            name=f"test_interaction_pipeline_{event.get_platform_id()}",
        )


async def _call_original_visible_completion(event):
    await event.get_extra("_interaction_original_complete_visible_turn")()


async def _drain_inbound_tasks(middleware: InteractionMiddleware) -> None:
    for _ in range(20):
        tasks = list(middleware._inflight_tasks)
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)


def _stub_fast_response_route(
    middleware: InteractionMiddleware,
    *,
    first_response: str = "嗯。",
    mode: InteractionRouteMode = InteractionRouteMode.HYBRID,
) -> None:
    if not isinstance(
        getattr(middleware.output_controller, "emit_immediate_spoken_reply", None),
        AsyncMock,
    ):
        middleware.output_controller.emit_immediate_spoken_reply = AsyncMock()
    middleware.persona_runtime = MagicMock()
    middleware.persona_runtime.express_visible_reply = AsyncMock(
        return_value=PersonaExpressionResult(spoken_reply=first_response)
    )
    middleware.router_agent = MagicMock()
    middleware.router_agent.route = AsyncMock(
        return_value=InteractionRouteDecision(route_mode=mode)
    )
    _stub_core_planner(middleware)


def _stub_core_planner(
    middleware: InteractionMiddleware,
    *,
    action: CorePlanningAction = CorePlanningAction.EXECUTE,
) -> None:
    task_spec = None
    if action is CorePlanningAction.EXECUTE:
        task_spec = CoreTaskSpec(
            task_intent="general",
            task_summary="处理当前请求",
            execution_prompt="完成当前用户请求。",
        )
    middleware.core_planner = MagicMock()
    middleware.core_planner.plan = AsyncMock(
        return_value=CorePlanningDecision(
            action=action,
            task_spec=task_spec,
        )
    )


@pytest.fixture
def webchat_event():
    platform_meta = PlatformMetadata(
        name="webchat",
        description="webchat",
        id="webchat",
    )
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = "bot123"
    message.session_id = "webchat!user!session123"
    message.message_id = "msg123"
    message.sender = MessageMember(user_id="user123", nickname="TestUser")
    message.message = []
    message.message_str = "Hello world"
    return ConcreteAstrMessageEvent(
        message_str="Hello world",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="webchat!user!session123",
    )


@pytest.fixture
def image_event(webchat_event):
    platform_meta = webchat_event.platform_meta
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = "bot123"
    message.session_id = "webchat!user!session123"
    message.message_id = "image-msg-123"
    message.sender = MessageMember(user_id="user123", nickname="TestUser")
    message.message = [
        Image(file="image.png", url="https://example.com/image.png")
    ]
    message.message_str = ""
    return ConcreteAstrMessageEvent(
        message_str="",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="webchat!user!session123",
    )


@pytest.fixture
def aiocqhttp_empty_notice_event():
    platform_meta = PlatformMetadata(
        name="aiocqhttp",
        description="aiocqhttp",
        id="alice",
    )
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = "2762018040"
    message.session_id = "815049548"
    message.message_id = "notice123"
    message.sender = MessageMember(user_id="815049548", nickname="815049548")
    message.message = []
    message.message_str = ""
    message.raw_message = {
        "post_type": "notice",
        "notice_type": "notify",
        "sub_type": "input_status",
        "status_text": "对方正在输入...",
    }
    event = ConcreteAstrMessageEvent(
        message_str="",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="815049548",
    )
    event.is_at_or_wake_command = True
    return event


@pytest.fixture
def streaming_event(webchat_event):
    event = StreamingAstrMessageEvent(
        message_str=webchat_event.message_str,
        message_obj=webchat_event.message_obj,
        platform_meta=webchat_event.platform_meta,
        session_id=webchat_event.session_id,
    )
    event.message_obj.message_str = webchat_event.message_obj.message_str
    return event


@pytest.fixture
def group_event():
    platform_meta = PlatformMetadata(
        name="aiocqhttp",
        description="aiocqhttp",
        id="aiocqhttp",
    )
    message = AstrBotMessage()
    message.type = MessageType.GROUP_MESSAGE
    message.self_id = "bot123"
    message.session_id = "group_456"
    message.group_id = "456"
    message.message_id = "group-msg-123"
    message.sender = MessageMember(user_id="user123", nickname="GroupUser")
    message.message = []
    message.message_str = "group hello"
    return ConcreteAstrMessageEvent(
        message_str="group hello",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="group_456",
    )


@pytest.fixture
def voice_event(webchat_event, tmp_path):
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"fake-wav")
    webchat_event.message_str = ""
    webchat_event.message_obj.message_str = ""
    webchat_event.message_obj.message = [
        Record.fromFileSystem(str(audio_path)),
    ]
    return webchat_event


@pytest.fixture
def live_event(webchat_event):
    webchat_event.set_extra("action_type", "live")
    return webchat_event


class TestInteractionMiddlewareConfig:
    def test_global_disable_takes_precedence(self):
        config = {
            "interaction_middleware": {
                "enabled": False,
            }
        }
        assert is_middleware_enabled(config) is False

    def test_global_enable_is_used(self):
        config = {
            "interaction_middleware": {
                "enabled": True,
            }
        }
        assert is_middleware_enabled(config) is True

    def test_enable_applies_to_all_platforms(self):
        config = {
            "interaction_middleware": {
                "enabled": True,
            }
        }
        assert is_middleware_enabled(config) is True

    def test_stream_interjection_zero_limit_is_preserved(self):
        config = {
            "interaction_middleware": {
                "enabled": True,
                "stream_observation_min_chars": 0,
                "stream_interjection_max_per_turn": 0,
            }
        }

        loaded = load_interaction_agent_config(config)

        assert loaded.stream_observation_min_chars == 1
        assert loaded.stream_interjection_max_per_turn == 0

    def test_role_specific_model_config_is_independent(self):
        config = {
            "interaction_middleware": {
                "expression_provider_id": "persona",
                "router_provider_id": "router",
                "planner_provider_id": "planner",
            }
        }

        loaded = load_interaction_agent_config(config)

        assert loaded.expression_provider_id == "persona"
        assert loaded.router_provider_id == "router"
        assert loaded.planner_provider_id == "planner"


class TestInteractionMiddleware:
    def test_runtime_exposes_only_pipeline_inbound_entry(self):
        assert "handle_inbound" not in RuntimeInteractionMiddleware.__dict__
        assert "_spawn_inbound_task" not in RuntimeInteractionMiddleware.__dict__
        assert (
            "core_queue"
            not in inspect.signature(RuntimeInteractionMiddleware.__init__).parameters
        )

    @pytest.mark.asyncio
    async def test_core_reply_handler_persona_renders_before_output_materialization(
        self,
        webchat_event,
    ):
        controller = MagicMock()
        controller.deliver_prepared_core_reply = AsyncMock()
        middleware = InteractionMiddleware({}, asyncio.Queue(), controller)
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.persona_runtime.express_visible_reply = AsyncMock(
            return_value=PersonaExpressionResult(spoken_reply="整理后的回复")
        )

        await controller.core_reply_handler(
            MessageChain([Plain("raw core reply")]),
            webchat_event,
        )

        middleware.persona_runtime.express_visible_reply.assert_awaited_once()
        request = middleware.persona_runtime.express_visible_reply.await_args.kwargs[
            "request"
        ]
        assert request.source_text == "raw core reply"
        prepared = controller.deliver_prepared_core_reply.await_args.args[1]
        assert prepared.spoken_reply == "整理后的回复"

    @pytest.mark.asyncio
    async def test_pipeline_harness_schedules_enabled_event(
        self, webchat_event
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "stream_observation_enabled": False,
                    "stream_interjection_enabled": False,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        controller.emit_immediate_spoken_reply = AsyncMock()
        _stub_fast_response_route(middleware)

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)

        assert queue.get_nowait() is webchat_event
        assert webchat_event.get_extra("_interaction_enabled") is True
        assert isinstance(webchat_event.get_extra("_turn_id"), str)
        turn_state = get_interaction_turn_state(webchat_event)
        assert isinstance(turn_state, InteractionTurnState)
        assert turn_state.turn_id == webchat_event.get_extra("_turn_id")
        assert webchat_event.get_extra("_output_controller") is controller
        assert (
            webchat_event.get_extra("_interaction_output_interceptor_installed") is True
        )

    @pytest.mark.asyncio
    async def test_inbound_stt_materializes_voice_before_decision(self, voice_event):
        queue = asyncio.Queue()
        controller = MagicMock()
        stt_provider = FakeSTTProvider("recognized voice text")
        plugin_context = MagicMock(spec=Context)
        plugin_context.get_using_stt_provider.return_value = stt_provider
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                },
                "provider_stt_settings": {"enable": True},
            },
            queue,
            controller,
            plugin_context=plugin_context,
        )
        _stub_fast_response_route(middleware)

        middleware.continue_pipeline(voice_event)
        await _drain_inbound_tasks(middleware)

        forwarded_event = queue.get_nowait()
        middleware.persona_runtime.express_visible_reply.assert_awaited_once()
        decision_event = (
            middleware.persona_runtime.express_visible_reply.await_args.args[0]
        )
        assert decision_event.message_str == "recognized voice text"
        assert forwarded_event.message_obj.message_str == "recognized voice text"
        assert isinstance(forwarded_event.message_obj.message[0], Plain)
        assert forwarded_event.get_extra("_interaction_stt_transcribed") is True
        assert (
            forwarded_event.get_extra("_interaction_inbound_media_materialized") is True
        )
        assert len(stt_provider.calls) == 1

    @pytest.mark.asyncio
    async def test_inbound_stt_provider_missing_fail_fast_records_failure(
        self,
        voice_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        plugin_context = MagicMock(spec=Context)
        plugin_context.get_using_stt_provider.return_value = None
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                },
                "provider_stt_settings": {"enable": True},
            },
            queue,
            controller,
            plugin_context=plugin_context,
        )
        _stub_fast_response_route(middleware)

        middleware.continue_pipeline(voice_event)
        await _drain_inbound_tasks(middleware)

        assert queue.empty()
        middleware.persona_runtime.express_visible_reply.assert_not_awaited()
        middleware.router_agent.route.assert_not_awaited()
        assert voice_event.get_extra("_interaction_stt_failed") is True
        assert (
            voice_event.get_extra("_interaction_stt_failure_reason")
            == "provider_unavailable"
        )
        turn_state = get_interaction_turn_state(voice_event)
        assert turn_state is not None
        assert turn_state.failures[-1].stage == "inbound_stt"
        assert turn_state.failures[-1].reason == "provider_unavailable"

    @pytest.mark.asyncio
    async def test_prepare_pipeline_event_intercepts_plugin_send_before_routing(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.capture_message_chain = AsyncMock()
        controller.capture_plugin_output = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )

        middleware.prepare_pipeline_event(webchat_event)
        message = MessageChain([Plain("plugin early send")])

        await webchat_event.send(message)

        controller.capture_plugin_output.assert_awaited_once_with(
            message,
            webchat_event,
            mode="direct",
        )
        controller.capture_message_chain.assert_not_awaited()
        assert webchat_event.get_extra("_interaction_enabled") is True
        assert webchat_event.get_extra("_interaction_output_prepared") is True
        assert webchat_event.get_extra("_interaction_route_handled") is None
        assert isinstance(webchat_event.get_extra("_turn_id"), str)
        assert get_interaction_turn_state(webchat_event) is not None

    @pytest.mark.asyncio
    async def test_handle_pipeline_event_runs_route_after_output_prepare(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "stream_observation_enabled": False,
                    "stream_interjection_enabled": False,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(middleware)

        middleware.prepare_pipeline_event(webchat_event)
        await middleware.handle_pipeline_event(webchat_event)

        middleware.router_agent.route.assert_awaited_once()
        assert webchat_event.get_extra("_interaction_route_handled") is True
        assert queue.empty()

        await _drain_inbound_tasks(middleware)
        middleware.persona_runtime.express_visible_reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_pipeline_event_skips_empty_notice_event(
        self,
        aiocqhttp_empty_notice_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "stream_observation_enabled": False,
                    "stream_interjection_enabled": False,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(middleware)

        middleware.prepare_pipeline_event(aiocqhttp_empty_notice_event)
        await middleware.handle_pipeline_event(aiocqhttp_empty_notice_event)

        middleware.persona_runtime.express_visible_reply.assert_not_awaited()
        middleware.router_agent.route.assert_not_awaited()
        assert (
            aiocqhttp_empty_notice_event.get_extra(
                "_interaction_route_skipped_reason"
            )
            == "empty_non_content_event"
        )
        assert (
            aiocqhttp_empty_notice_event.get_extra("_interaction_route_handled")
            is True
        )
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_process_stage_prepares_output_before_plugin_handler_send(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.capture_message_chain = AsyncMock()
        controller.capture_plugin_output = AsyncMock()
        controller.finalize_plugin_output_transaction = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        stage = ProcessStage()
        stage.ctx = MagicMock()
        stage.ctx.interaction_middleware = middleware
        stage.ctx.astrbot_config = {"provider_settings": {"enable": False}}
        stage.star_request_sub_stage = MagicMock()
        message = MessageChain([Plain("plugin handler send")])

        async def _plugin_process(event):
            assert event.get_extra("_interaction_output_prepared") is True
            assert event.get_extra("_interaction_route_handled") is None
            await event.send(message)
            yield None

        stage.star_request_sub_stage.process = _plugin_process
        webchat_event.set_extra("activated_handlers", [MagicMock()])

        async for _ in stage.process(webchat_event):
            pass

        controller.capture_plugin_output.assert_awaited_once_with(
            message,
            webchat_event,
            mode="direct",
        )
        controller.capture_message_chain.assert_not_awaited()
        controller.finalize_plugin_output_transaction.assert_awaited_once_with(
            webchat_event,
            delegated_to_core=False,
        )
        assert webchat_event.get_extra("_interaction_route_handled") is None
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_process_stage_treats_plugin_send_before_provider_request_as_progress(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.capture_message_chain = AsyncMock()
        controller.capture_plugin_output = AsyncMock()
        controller.finalize_plugin_output_transaction = AsyncMock()
        middleware = InteractionMiddleware(
            {"interaction_middleware": {"enabled": True}},
            queue,
            controller,
        )
        stage = ProcessStage()
        stage.ctx = MagicMock()
        stage.ctx.interaction_middleware = middleware
        stage.ctx.astrbot_config = {"provider_settings": {"enable": False}}
        stage.star_request_sub_stage = MagicMock()
        stage.agent_sub_stage = MagicMock()
        stage._run_interaction_before_core_agent = AsyncMock()
        message = MessageChain([Plain("working")])
        request = ProviderRequest(prompt="complete this")

        async def _plugin_process(event):
            await event.send(message)
            yield request

        async def _agent_process(event):
            assert event.get_extra("provider_request") is request
            yield None

        stage.star_request_sub_stage.process = _plugin_process
        stage.agent_sub_stage.process = _agent_process
        webchat_event.set_extra("activated_handlers", [MagicMock()])

        async for _ in stage.process(webchat_event):
            pass

        controller.finalize_plugin_output_transaction.assert_awaited_once_with(
            webchat_event,
            delegated_to_core=True,
        )
        stage._run_interaction_before_core_agent.assert_awaited_once_with(webchat_event)

    @pytest.mark.asyncio
    async def test_plugin_send_defaults_to_plugin_output_after_forwarding(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.capture_message_chain = AsyncMock()
        controller.capture_plugin_output = AsyncMock()
        controller.capture_visible_completion = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        controller.emit_immediate_spoken_reply = AsyncMock()
        _stub_fast_response_route(middleware)

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)
        forwarded_event = queue.get_nowait()
        message = MessageChain([Plain("core reply")])

        await forwarded_event.send(message)

        controller.capture_plugin_output.assert_awaited_once_with(
            message,
            forwarded_event,
            mode="direct",
        )
        controller.capture_message_chain.assert_not_awaited()
        assert forwarded_event._has_send_oper is True

    @pytest.mark.asyncio
    async def test_core_send_is_intercepted_after_forwarding(self, webchat_event):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.capture_message_chain = AsyncMock()
        controller.capture_plugin_output = AsyncMock()
        controller.capture_visible_completion = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "stream_observation_enabled": False,
                    "stream_interjection_enabled": False,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        controller.emit_immediate_spoken_reply = AsyncMock()
        _stub_fast_response_route(middleware)

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)
        forwarded_event = queue.get_nowait()
        message = MessageChain([Plain("core reply")])

        with temporary_output_origin(forwarded_event, OutputOrigin.CORE.value):
            await forwarded_event.send(message)

        controller.capture_message_chain.assert_awaited_once_with(
            message,
            forwarded_event,
        )
        controller.capture_plugin_output.assert_not_awaited()
        assert forwarded_event._has_send_oper is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("previous_has_send_oper", [False, True])
    async def test_suppressed_core_send_preserves_previous_send_state(
        self,
        webchat_event,
        previous_has_send_oper,
    ):
        controller = MagicMock()

        async def _suppress_output(_message, event):
            event.set_extra("_interaction_pipeline_output_suppressed", True)

        controller.capture_message_chain = AsyncMock(side_effect=_suppress_output)
        controller.capture_plugin_output = AsyncMock()
        middleware = InteractionMiddleware(
            {"interaction_middleware": {"enabled": True}},
            asyncio.Queue(),
            controller,
        )
        middleware.prepare_pipeline_event(webchat_event)
        webchat_event._has_send_oper = previous_has_send_oper

        with temporary_output_origin(webchat_event, OutputOrigin.CORE.value):
            await webchat_event.send(MessageChain([Plain("blocked")]))

        controller.capture_message_chain.assert_awaited_once()
        controller.capture_plugin_output.assert_not_awaited()
        assert webchat_event._has_send_oper is previous_has_send_oper

    @pytest.mark.asyncio
    async def test_respond_stage_routes_official_plugin_result_as_plugin_output(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.capture_message_chain = AsyncMock()
        controller.capture_plugin_output = AsyncMock()
        controller.capture_visible_completion = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "stream_observation_enabled": False,
                    "stream_interjection_enabled": False,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        controller.emit_immediate_spoken_reply = AsyncMock()
        _stub_fast_response_route(middleware)

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)
        forwarded_event = queue.get_nowait()
        forwarded_event.set_result(MessageEventResult().message("respond stage reply"))

        stage = RespondStage()
        await stage.initialize(
            MagicMock(
                astrbot_config={"platform_settings": {}, "provider_settings": {}},
                plugin_manager=MagicMock(context=MagicMock()),
            )
        )

        await stage.process(forwarded_event)

        controller.capture_plugin_output.assert_awaited_once()
        sent_message = controller.capture_plugin_output.await_args.args[0]
        assert sent_message.get_plain_text() == "respond stage reply"
        assert controller.capture_plugin_output.await_args.args[1] is forwarded_event
        assert controller.capture_plugin_output.await_args.kwargs == {"mode": "direct"}
        controller.capture_message_chain.assert_not_awaited()
        assert forwarded_event.get_extra("_interaction_output_origin") is None

    @pytest.mark.asyncio
    async def test_respond_stage_keeps_model_result_on_core_output_path(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.capture_message_chain = AsyncMock()
        controller.capture_plugin_output = AsyncMock()
        controller.capture_visible_completion = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "stream_observation_enabled": False,
                    "stream_interjection_enabled": False,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        controller.emit_immediate_spoken_reply = AsyncMock()
        _stub_fast_response_route(middleware)

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)
        forwarded_event = queue.get_nowait()
        forwarded_event.set_result(
            MessageEventResult()
            .message("core model reply")
            .set_result_content_type(ResultContentType.LLM_RESULT)
        )

        stage = RespondStage()
        await stage.initialize(
            MagicMock(
                astrbot_config={"platform_settings": {}, "provider_settings": {}},
                plugin_manager=MagicMock(context=MagicMock()),
            )
        )

        await stage.process(forwarded_event)

        controller.capture_message_chain.assert_awaited_once()
        sent_message = controller.capture_message_chain.await_args.args[0]
        assert sent_message.get_plain_text() == "core model reply"
        controller.capture_plugin_output.assert_not_awaited()
        assert forwarded_event.get_extra("_interaction_output_origin") is None

    @pytest.mark.asyncio
    async def test_plugin_streaming_defaults_to_plugin_output_after_forwarding(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.capture_streaming = AsyncMock()
        controller.capture_plugin_streaming = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        controller.emit_immediate_spoken_reply = AsyncMock()
        _stub_fast_response_route(middleware)

        async def generator():
            yield MessageChain([Plain("plugin chunk")])

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)
        forwarded_event = queue.get_nowait()

        await forwarded_event.send_streaming(generator(), use_fallback=True)

        controller.capture_plugin_streaming.assert_awaited_once()
        assert controller.capture_plugin_streaming.await_args.args[0] is not None
        assert controller.capture_plugin_streaming.await_args.args[1] is forwarded_event
        assert controller.capture_plugin_streaming.await_args.kwargs == {
            "mode": "direct",
            "use_fallback": True,
        }
        controller.capture_streaming.assert_not_awaited()
        assert forwarded_event._has_send_oper is True

    @pytest.mark.asyncio
    async def test_core_streaming_is_intercepted_after_forwarding(self, webchat_event):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.capture_streaming = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        controller.emit_immediate_spoken_reply = AsyncMock()
        _stub_fast_response_route(middleware)

        async def generator():
            yield MessageChain([Plain("chunk")])

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)
        forwarded_event = queue.get_nowait()

        with temporary_output_origin(forwarded_event, OutputOrigin.CORE.value):
            await forwarded_event.send_streaming(generator(), use_fallback=True)

        controller.capture_streaming.assert_awaited_once()
        assert forwarded_event._has_send_oper is True

    @pytest.mark.asyncio
    async def test_plugin_streaming_records_plugin_output_without_core_stream_state(
        self,
        streaming_event,
    ):
        queue = asyncio.Queue()
        controller = InteractionOutputController(
            interaction_config=InteractionAgentConfig(
                stream_observation_enabled=False,
                stream_interjection_enabled=False,
            )
        )
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "stream_observation_enabled": False,
                    "stream_interjection_enabled": False,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(middleware)
        middleware.memory_store.update_interaction_memory = AsyncMock()

        async def generator():
            yield MessageChain([Plain("plugin ")])
            yield MessageChain([Plain("stream")])

        with patch(
            "astrbot.core.interaction.middleware.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch:
            middleware.continue_pipeline(streaming_event)
            await _drain_inbound_tasks(middleware)
            forwarded_event = queue.get_nowait()
            await forwarded_event.send_streaming(generator())
            await _drain_inbound_tasks(middleware)

        turn_state = get_interaction_turn_state(forwarded_event)
        assert turn_state is not None
        assert (
            forwarded_event.get_extra("_interaction_plugin_streaming_consumed") is True
        )
        assert (
            forwarded_event.get_extra("_interaction_plugin_streaming_text")
            == "plugin stream"
        )
        assert (
            forwarded_event.get_extra("_interaction_core_streaming_result_consumed")
            is None
        )
        assert turn_state.visible_outputs == [
            {
                "turn_id": forwarded_event.get_extra("_turn_id"),
                "message_id": (
                    f"{forwarded_event.get_extra('_turn_id')}::plugin_direct::0001"
                ),
                "delivered_message_ids": [
                    f"{forwarded_event.get_extra('_turn_id')}::plugin_direct::0001"
                ],
                "kind": "plugin_direct",
                "text": "plugin stream",
                "memory_relevant": True,
            }
        ]
        assert turn_state.utterances[0].kind == "plugin_direct"
        middleware.memory_store.update_interaction_memory.assert_not_awaited()
        dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_core_streaming_finalizes_turn_after_stream_completion(
        self,
        streaming_event,
    ):
        queue = asyncio.Queue()
        controller = InteractionOutputController(
            interaction_config=InteractionAgentConfig(
                stream_observation_enabled=False,
                stream_interjection_enabled=False,
            )
        )
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "stream_observation_enabled": False,
                    "stream_interjection_enabled": False,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(middleware)
        middleware.memory_store.update_interaction_memory = AsyncMock()

        async def generator():
            yield MessageChain([Plain("stream final")])

        with patch(
            "astrbot.core.interaction.middleware.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch:
            middleware.continue_pipeline(streaming_event)
            await _drain_inbound_tasks(middleware)
            forwarded_event = queue.get_nowait()
            with temporary_output_origin(forwarded_event, OutputOrigin.CORE.value):
                await forwarded_event.send_streaming(generator())
            await _drain_inbound_tasks(middleware)

        turn_state = get_interaction_turn_state(forwarded_event)
        assert turn_state is not None
        assert turn_state.completion_state.material_finalized is True
        assert turn_state.completion_state.legacy_memory_persisted is False
        assert turn_state.completion_state.postprocess_dispatched is True
        assert turn_state.completion_state.completed is True
        middleware.memory_store.update_interaction_memory.assert_not_awaited()
        dispatch.assert_awaited_once()
        assert dispatch.await_args.kwargs["turn_material"] == {
            "turn_id": forwarded_event.get_extra("_turn_id"),
            "user_text": "Hello world",
            "assistant_text": "stream final",
            "visible_outputs": [
                {
                    "turn_id": forwarded_event.get_extra("_turn_id"),
                    "message_id": (
                        f"{forwarded_event.get_extra('_turn_id')}::core_stream::0001"
                    ),
                    "delivered_message_ids": [
                        f"{forwarded_event.get_extra('_turn_id')}::core_stream::0001"
                    ],
                    "kind": "core_stream",
                    "text": "stream final",
                    "memory_relevant": True,
                }
            ],
            "history_source": "interaction.turn.material",
        }

    @pytest.mark.asyncio
    async def test_pipeline_skips_context_when_globally_disabled(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": False,
                }
            },
            queue,
            MagicMock(),
        )

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)

        assert queue.get_nowait() is webchat_event
        assert webchat_event.get_extra("_interaction_enabled") is None
        assert webchat_event.get_extra("_turn_id") is None
        assert webchat_event.get_extra("_output_controller") is None

    @pytest.mark.asyncio
    async def test_hybrid_forwards_core_while_persona_is_still_generating(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="嗯，我来处理。",
            mode=InteractionRouteMode.HYBRID,
        )
        expression_started = asyncio.Event()
        release_expression = asyncio.Event()

        async def _generate_expression(*_args, **_kwargs):
            expression_started.set()
            await release_expression.wait()
            return PersonaExpressionResult(spoken_reply="嗯，我来处理。")

        middleware.persona_runtime.express_visible_reply = AsyncMock(
            side_effect=_generate_expression
        )

        middleware.continue_pipeline(webchat_event)
        await expression_started.wait()
        await asyncio.sleep(0)

        forwarded_event = queue.get_nowait()
        assert forwarded_event is webchat_event
        assert forwarded_event._has_send_oper is False
        controller.emit_immediate_spoken_reply.assert_not_awaited()

        release_expression.set()
        await _drain_inbound_tasks(middleware)
        controller.emit_immediate_spoken_reply.assert_awaited_once()
        request = middleware.persona_runtime.express_visible_reply.await_args.kwargs[
            "request"
        ]
        assert request.delegated_task_summary == ""
        assert request.short_reply is False

    @pytest.mark.asyncio
    async def test_pipeline_hybrid_returns_before_persona_finishes(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        middleware = InteractionMiddleware(
            {"interaction_middleware": {"enabled": True}},
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="嗯，我来处理。",
            mode=InteractionRouteMode.HYBRID,
        )
        expression_started = asyncio.Event()
        release_expression = asyncio.Event()

        async def _generate_expression(*_args, **_kwargs):
            expression_started.set()
            await release_expression.wait()
            return PersonaExpressionResult(spoken_reply="嗯，我来处理。")

        middleware.persona_runtime.express_visible_reply = AsyncMock(
            side_effect=_generate_expression
        )

        pipeline_task = asyncio.create_task(
            middleware.handle_pipeline_event(webchat_event)
        )
        await expression_started.wait()
        await asyncio.wait_for(pipeline_task, timeout=0.5)

        assert queue.empty()
        assert webchat_event.get_extra("_interaction_delegate_to_core") is True
        assert webchat_event.get_extra("_interaction_route_handled") is True
        controller.emit_immediate_spoken_reply.assert_not_awaited()

        release_expression.set()
        await _drain_inbound_tasks(middleware)
        controller.emit_immediate_spoken_reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hybrid_immediate_reply_waits_for_core_before_turn_completion(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="等我看看。",
            mode=InteractionRouteMode.HYBRID,
        )
        persisted = asyncio.Event()
        middleware.memory_store.update_interaction_memory = AsyncMock(
            side_effect=lambda *a, **kw: persisted.set()
        )

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)

        assert queue.get_nowait() is webchat_event
        controller.emit_immediate_spoken_reply.assert_awaited_once()
        middleware.memory_store.update_interaction_memory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hybrid_suppresses_immediate_reply_when_core_finishes_first(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        middleware = InteractionMiddleware(
            {"interaction_middleware": {"enabled": True}},
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="我先看看。",
            mode=InteractionRouteMode.HYBRID,
        )
        expression_started = asyncio.Event()
        release_expression = asyncio.Event()

        async def _generate_expression(*_args, **_kwargs):
            expression_started.set()
            await release_expression.wait()
            return PersonaExpressionResult(spoken_reply="我先看看。")

        middleware.persona_runtime.express_visible_reply = AsyncMock(
            side_effect=_generate_expression
        )

        middleware.continue_pipeline(webchat_event)
        await expression_started.wait()
        assert queue.get_nowait() is webchat_event
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        turn_state.core_final_result_consumed = True
        release_expression.set()
        await _drain_inbound_tasks(middleware)

        controller.emit_immediate_spoken_reply.assert_not_awaited()
        assert (
            webchat_event.get_extra("_interaction_immediate_reply_suppressed_reason")
            == "core_completed_first"
        )

    @pytest.mark.asyncio
    async def test_planner_not_required_finishes_with_single_persona_reply(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        controller.capture_visible_completion = AsyncMock()
        middleware = InteractionMiddleware(
            {"interaction_middleware": {"enabled": True}},
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="这不是很明显嘛。",
            mode=InteractionRouteMode.HYBRID,
        )
        _stub_core_planner(
            middleware,
            action=CorePlanningAction.NOT_REQUIRED,
        )
        middleware.memory_store.update_interaction_memory = AsyncMock()

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)

        assert queue.empty()
        controller.emit_immediate_spoken_reply.assert_awaited_once()
        assert webchat_event.is_stopped()
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.core_planning_decision is not None
        assert (
            turn_state.core_planning_decision.action
            is CorePlanningAction.NOT_REQUIRED
        )
        assert turn_state.core_task_spec is None

    @pytest.mark.asyncio
    async def test_hybrid_media_keeps_persona_reply_committed_before_planner(
        self,
        image_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="在等你提问题啊，笨蛋。",
            mode=InteractionRouteMode.HYBRID,
        )

        middleware.continue_pipeline(image_event)
        await _drain_inbound_tasks(middleware)

        controller.emit_immediate_spoken_reply.assert_awaited_once()
        assert queue.get_nowait() is image_event
        assert image_event.get_extra("_interaction_immediate_reply_suppressed_reason") is None
        turn_state = get_interaction_turn_state(image_event)
        assert turn_state is not None
        assert turn_state.route_decision is not None
        assert turn_state.route_decision.route_mode == InteractionRouteMode.HYBRID
        assert (
            turn_state.speculative_persona_status
            is InteractionSpeculativePersonaStatus.EMITTED
        )

    @pytest.mark.asyncio
    async def test_persona_media_input_keeps_immediate_reply(
        self,
        image_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="这张图我能直接看。",
            mode=InteractionRouteMode.PERSONA,
        )

        middleware.continue_pipeline(image_event)
        await _drain_inbound_tasks(middleware)

        controller.emit_immediate_spoken_reply.assert_awaited_once()
        assert queue.empty()
        turn_state = get_interaction_turn_state(image_event)
        assert turn_state is not None
        assert turn_state.route_decision is not None
        assert turn_state.route_decision.route_mode == InteractionRouteMode.PERSONA

    @pytest.mark.asyncio
    async def test_silent_route_completes_without_visible_output_or_core(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        middleware = InteractionMiddleware(
            {"interaction_middleware": {"enabled": True}},
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="这条回复不应该发出。",
            mode=InteractionRouteMode.SILENT,
        )
        persona_started = asyncio.Event()

        async def _slow_persona(*_args, **_kwargs):
            persona_started.set()
            await asyncio.Event().wait()

        middleware.persona_runtime.express_visible_reply = AsyncMock(
            side_effect=_slow_persona
        )

        middleware.continue_pipeline(webchat_event)
        await persona_started.wait()
        await _drain_inbound_tasks(middleware)

        controller.emit_immediate_spoken_reply.assert_not_awaited()
        middleware.persona_runtime.express_visible_reply.assert_awaited_once()
        assert queue.empty()
        assert webchat_event.is_stopped()
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.route_decision is not None
        assert turn_state.route_decision.route_mode == InteractionRouteMode.SILENT
        assert turn_state.completion_state.completed is True
        assert turn_state.completion_state.outcome == InteractionTurnOutcome.SILENT
        assert (
            turn_state.speculative_persona_status
            is InteractionSpeculativePersonaStatus.SUPPRESSED
        )
        material = get_interaction_turn_finalized_material(webchat_event)
        assert material is not None
        assert material["outcome"] == "silent"
        assert material["assistant_text"] == ""
        assert material["visible_outputs"] == []

    @pytest.mark.asyncio
    async def test_late_silent_keeps_already_emitted_persona_reply(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        controller.capture_visible_completion = AsyncMock()
        middleware = InteractionMiddleware(
            {"interaction_middleware": {"enabled": True}},
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        persona_emitted = asyncio.Event()
        release_router = asyncio.Event()

        async def _emit_reply(*_args):
            persona_emitted.set()

        async def _slow_silent_router(*_args, **_kwargs):
            await release_router.wait()
            return InteractionRouteDecision(
                route_mode=InteractionRouteMode.SILENT
            )

        controller.emit_immediate_spoken_reply = AsyncMock(side_effect=_emit_reply)
        middleware.persona_runtime = MagicMock()
        middleware.persona_runtime.express_visible_reply = AsyncMock(
            return_value=PersonaExpressionResult(spoken_reply="已经发出的回复。")
        )
        middleware.router_agent = MagicMock()
        middleware.router_agent.route = AsyncMock(side_effect=_slow_silent_router)

        middleware.continue_pipeline(webchat_event)
        await persona_emitted.wait()
        release_router.set()
        await _drain_inbound_tasks(middleware)

        controller.emit_immediate_spoken_reply.assert_awaited_once()
        assert queue.empty()
        assert webchat_event.is_stopped()
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.route_decision is not None
        assert turn_state.route_decision.route_mode is InteractionRouteMode.SILENT
        assert (
            turn_state.speculative_persona_status
            is InteractionSpeculativePersonaStatus.EMITTED
        )
        assert turn_state.completion_state.outcome is InteractionTurnOutcome.REPLIED

    @pytest.mark.asyncio
    async def test_pipeline_harness_refreshes_runtime_interaction_config(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        default_config = {
            "interaction_middleware": {
                "enabled": True,
                "router_provider_id": "",
            }
        }
        runtime_config = {
            "interaction_middleware": {
                "enabled": True,
                "router_provider_id": "runtime_provider",
                "memory_window_size": 3,
            }
        }
        middleware = InteractionMiddleware(default_config, queue, controller)
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.plugin_context.get_config.side_effect = lambda umo=None: (
            runtime_config
            if umo == webchat_event.unified_msg_origin
            else default_config
        )
        _stub_fast_response_route(middleware)

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)

        middleware.router_agent.route.assert_awaited_once()
        decision_config = middleware.router_agent.route.await_args.args[2]
        assert decision_config.router_provider_id == "runtime_provider"
        assert decision_config.memory_window_size == 3
        assert middleware.interaction_config.router_provider_id == ""
        assert controller.interaction_config.router_provider_id == ""

    @pytest.mark.asyncio
    async def test_protocol_command_bypass_does_not_emit_immediate_reply(
        self,
        webchat_event,
    ):
        webchat_event.message_str = "/sid"
        webchat_event.message_obj.message_str = "/sid"
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.plugin_context.get_config.return_value = {"wake_prefix": ["/"]}

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)

        assert queue.get_nowait() is webchat_event
        controller.emit_immediate_spoken_reply.assert_not_awaited()
        assert webchat_event.get_extra("_interaction_route_decision") is None
        assert webchat_event.get_extra("_interaction_protocol_core_bypass") is True
        assert (
            webchat_event.get_extra("_interaction_protocol_core_bypass_reason")
            == "protocol_command_bypass"
        )

    @pytest.mark.asyncio
    async def test_missing_plugin_context_fails_before_core_execution(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)

        assert queue.empty()
        assert webchat_event.get_extra("_interaction_router_failed") is True
        assert webchat_event.get_extra("_interaction_core_planner_failed") is True
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.failures[-1].stage == "core_planner"
        assert turn_state.route_decision is not None
        assert turn_state.route_decision.route_mode is InteractionRouteMode.HYBRID
        controller.emit_immediate_spoken_reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_planner_failure_completes_already_emitted_persona_turn(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        controller.capture_visible_completion = AsyncMock()
        middleware = InteractionMiddleware(
            {"interaction_middleware": {"enabled": True}},
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="我先陪你看看。",
            mode=InteractionRouteMode.HYBRID,
        )
        middleware.core_planner.plan = AsyncMock(
            side_effect=CorePlannerError("timeout")
        )

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)

        assert queue.empty()
        controller.emit_immediate_spoken_reply.assert_awaited_once()
        assert webchat_event.is_stopped()
        assert (
            webchat_event.get_extra(
                "_interaction_core_planner_recovered_via_persona"
            )
            is True
        )
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.completion_state.completed is True
        assert turn_state.completion_state.outcome is InteractionTurnOutcome.REPLIED
        assert turn_state.failures[-1].stage == "core_planner"
        assert turn_state.failures[-1].user_visible_action == "persona_only"

    def test_fallback_policy_is_rejected_during_development(
        self,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()

        with pytest.raises(RuntimeError, match="fallback_policy is disabled"):
            InteractionMiddleware(
                {
                    "interaction_middleware": {
                        "enabled": True,
                        "fallback_policy": "observable_protect",
                    }
                },
                queue,
                controller,
            )

    def test_fallback_policy_refresh_is_rejected_during_development(
        self,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        config = {
            "interaction_middleware": {
                "enabled": True,
            }
        }
        middleware = InteractionMiddleware(config, queue, controller)
        config["interaction_middleware"]["fallback_policy"] = "observable_protect"

        with pytest.raises(RuntimeError, match="fallback_policy is disabled"):
            middleware.refresh_interaction_config()

    def test_fallback_policy_refresh_uses_runtime_config_for_event(self, webchat_event):
        queue = asyncio.Queue()
        controller = MagicMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        plugin_context = MagicMock(spec=Context)
        plugin_context.get_config.side_effect = lambda umo=None: {
            "interaction_middleware": {
                "enabled": True,
                "fallback_policy": "observable_protect",
            }
        }
        middleware.set_plugin_context(plugin_context)

        with pytest.raises(RuntimeError, match="fallback_policy is disabled"):
            middleware.refresh_interaction_config(webchat_event)

    @pytest.mark.asyncio
    async def test_router_pipeline_error_falls_back_to_hybrid_records_failure(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.persona_runtime = MagicMock()
        middleware.persona_runtime.express_visible_reply = AsyncMock(
            return_value=PersonaExpressionResult(spoken_reply="我先看一下。")
        )
        middleware.router_agent = MagicMock()
        middleware.router_agent.route = AsyncMock(
            side_effect=RuntimeError("router broken")
        )
        _stub_core_planner(middleware)

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)

        assert queue.get_nowait() is webchat_event
        assert webchat_event.get_extra("_interaction_router_failed") is True
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.failures[-1].stage == "router"
        assert turn_state.failures[-1].reason == "router_pipeline_error"

    @pytest.mark.asyncio
    async def test_hybrid_immediate_reply_failure_does_not_cancel_started_core(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock(
            side_effect=RuntimeError("send failed")
        )
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="嗯，我来处理。",
            mode=InteractionRouteMode.HYBRID,
        )

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)

        assert queue.get_nowait() is webchat_event
        assert webchat_event.get_extra("_interaction_immediate_reply_failed") is True
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.failures[-1].stage == "immediate_reply"
        assert turn_state.failures[-1].reason == "send_failed"

    @pytest.mark.asyncio
    async def test_live_mode_routes_directly_to_core_audio_stream(self, live_event):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.persona_runtime = MagicMock()
        middleware.persona_runtime.express_visible_reply = AsyncMock()
        middleware.router_agent = MagicMock()
        middleware.router_agent.route = AsyncMock()

        middleware.continue_pipeline(live_event)
        await _drain_inbound_tasks(middleware)

        assert queue.get_nowait() is live_event
        assert queue.empty()
        middleware.persona_runtime.express_visible_reply.assert_not_awaited()
        middleware.router_agent.route.assert_not_awaited()
        controller.emit_immediate_spoken_reply.assert_not_awaited()
        assert (
            live_event.get_extra("_interaction_live_mode_protocol_route")
            == "core_audio_stream"
        )
        turn_state = get_interaction_turn_state(live_event)
        assert turn_state is not None
        assert turn_state.route_decision is None
        assert live_event.get_extra("_interaction_protocol_core_bypass") is True
        assert (
            live_event.get_extra("_interaction_protocol_core_bypass_reason")
            == "live_mode_requires_audio_chunk_stream"
        )
        assert turn_state.failures == []

    @pytest.mark.asyncio
    async def test_persona_reply_failure_fail_fast_records_failure(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock(
            side_effect=RuntimeError("send failed")
        )
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="嗯。",
            mode=InteractionRouteMode.PERSONA,
        )

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)

        assert queue.empty()
        assert webchat_event.get_extra("_interaction_immediate_reply_failed") is True
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.failures[-1].stage == "immediate_reply"
        assert turn_state.failures[-1].reason == "send_failed"

    @pytest.mark.asyncio
    async def test_persona_without_immediate_reply_is_rejected(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="",
            mode=InteractionRouteMode.PERSONA,
        )

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)

        assert queue.empty()
        assert webchat_event.get_extra("_interaction_persona_reply_invalid") is True
        assert (
            webchat_event.get_extra("_interaction_persona_reply_invalid_reason")
            == "missing_immediate_reply"
        )
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.failures[-1].stage == "persona_expression"
        assert turn_state.failures[-1].reason == "missing_persona_reply"

    @pytest.mark.asyncio
    async def test_persona_completion_does_not_write_legacy_interaction_memory(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        controller.capture_visible_completion = AsyncMock(
            side_effect=_call_original_visible_completion
        )
        complete_visible_turn = AsyncMock()
        webchat_event.complete_visible_turn = complete_visible_turn
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="嗯。",
            mode=InteractionRouteMode.PERSONA,
        )
        middleware.memory_store.update_interaction_memory = AsyncMock()

        with patch(
            "astrbot.core.interaction.middleware.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch:
            middleware.continue_pipeline(webchat_event)
            await _drain_inbound_tasks(middleware)
            await _drain_inbound_tasks(middleware)

        assert queue.empty()
        complete_visible_turn.assert_awaited_once()
        controller.capture_visible_completion.assert_awaited_once_with(webchat_event)
        middleware.memory_store.update_interaction_memory.assert_not_awaited()
        dispatch.assert_awaited_once()
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.completion_state.legacy_memory_persisted is False
        assert turn_state.completion_state.postprocess_dispatched is True
        assert turn_state.completion_state.completed is True
        assert turn_state.completion_state.failure_reason is None

    @pytest.mark.asyncio
    async def test_persona_does_not_persist_if_visible_completion_fails(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        controller.capture_visible_completion = AsyncMock(
            side_effect=_call_original_visible_completion
        )
        complete_visible_turn = AsyncMock(side_effect=RuntimeError("queue closed"))
        webchat_event.complete_visible_turn = complete_visible_turn
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="嗯。",
            mode=InteractionRouteMode.PERSONA,
        )
        middleware.memory_store.update_interaction_memory = AsyncMock()

        middleware.continue_pipeline(webchat_event)
        await _drain_inbound_tasks(middleware)

        assert queue.empty()
        complete_visible_turn.assert_awaited_once()
        controller.capture_visible_completion.assert_awaited_once_with(webchat_event)
        middleware.memory_store.update_interaction_memory.assert_not_awaited()
        assert webchat_event.get_extra("_interaction_visible_completion_failed") is True
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.failures[-1].stage == "visible_completion"
        assert turn_state.failures[-1].reason == "completion_failed"

    @pytest.mark.asyncio
    async def test_finalize_turn_requires_explicit_finalized_material(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.memory_store.update_interaction_memory = AsyncMock()
        webchat_event.set_extra("_turn_id", "turn-1")

        with patch(
            "astrbot.core.interaction.middleware.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch:
            await middleware._finalize_turn(webchat_event)

        middleware.memory_store.update_interaction_memory.assert_not_awaited()
        dispatch.assert_not_awaited()
        assert webchat_event.get_extra("_interaction_turn_finalization_failed") is True
        assert (
            webchat_event.get_extra("_interaction_turn_finalization_failure_reason")
            == "missing_finalized_turn_material"
        )
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.completion_state.material_finalized is False
        assert turn_state.completion_state.legacy_memory_persisted is False
        assert turn_state.completion_state.postprocess_dispatched is False
        assert turn_state.completion_state.completed is False
        assert (
            turn_state.completion_state.failure_reason
            == "missing_finalized_turn_material"
        )

    @pytest.mark.asyncio
    async def test_persona_completes_visible_turn_after_immediate_reply(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        controller.capture_visible_completion = AsyncMock(
            side_effect=_call_original_visible_completion
        )
        complete_visible_turn = AsyncMock()
        webchat_event.complete_visible_turn = complete_visible_turn
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="嗯。",
            mode=InteractionRouteMode.PERSONA,
        )
        middleware.memory_store.update_interaction_memory = AsyncMock()

        with patch(
            "astrbot.core.interaction.middleware.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch:
            middleware.continue_pipeline(webchat_event)
            await _drain_inbound_tasks(middleware)
            await _drain_inbound_tasks(middleware)

        assert queue.empty()
        controller.emit_immediate_spoken_reply.assert_awaited_once()
        complete_visible_turn.assert_awaited_once()
        controller.capture_visible_completion.assert_awaited_once_with(webchat_event)
        dispatch.assert_awaited_once()
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.completion_state.material_finalized is True
        assert turn_state.completion_state.legacy_memory_persisted is False
        assert turn_state.completion_state.postprocess_dispatched is True
        assert turn_state.completion_state.completed is True
        assert (
            dispatch.await_args.kwargs["trigger"]
            == PostProcessTrigger.AFTER_TURN_COMPLETED
        )
        assert dispatch.await_args.kwargs["turn_id"] == webchat_event.get_extra(
            "_turn_id"
        )
        assert dispatch.await_args.kwargs["turn_material"] == {
            "turn_id": webchat_event.get_extra("_turn_id"),
            "user_text": "Hello world",
            "assistant_text": "嗯。",
            "visible_outputs": [],
            "history_source": "interaction.turn.material",
        }

    @pytest.mark.asyncio
    async def test_persona_dispatches_postprocess_as_memory_owner(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        controller.capture_visible_completion = AsyncMock(
            side_effect=_call_original_visible_completion
        )
        complete_visible_turn = AsyncMock()
        webchat_event.complete_visible_turn = complete_visible_turn
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        _stub_fast_response_route(
            middleware,
            first_response="嗯。",
            mode=InteractionRouteMode.PERSONA,
        )
        middleware.memory_store.update_interaction_memory = AsyncMock()
        order: list[str] = []

        with patch(
            "astrbot.core.interaction.middleware.dispatch_postprocess",
            new=AsyncMock(side_effect=lambda **_kwargs: order.append("postprocess")),
        ):
            middleware.continue_pipeline(webchat_event)
            await _drain_inbound_tasks(middleware)
            await _drain_inbound_tasks(middleware)

        middleware.memory_store.update_interaction_memory.assert_not_awaited()
        assert order == ["postprocess"]

    @pytest.mark.asyncio
    async def test_persona_sets_runtime_config_for_postprocess(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        controller.capture_visible_completion = AsyncMock(
            side_effect=_call_original_visible_completion
        )
        webchat_event.complete_visible_turn = AsyncMock()
        default_config = {
            "interaction_middleware": {
                "enabled": True,
            },
            "platform_settings": {
                "enable_id_white_list": False,
                "id_whitelist": [],
            },
        }
        runtime_config = {
            "interaction_middleware": {
                "enabled": True,
            },
            "platform_settings": {
                "enable_id_white_list": True,
                "id_whitelist": ["webchat!user!session123"],
            },
        }
        middleware = InteractionMiddleware(default_config, queue, controller)
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.plugin_context.get_config.side_effect = lambda umo=None: (
            runtime_config
            if umo == webchat_event.unified_msg_origin
            else default_config
        )
        _stub_fast_response_route(
            middleware,
            first_response="嗯。",
            mode=InteractionRouteMode.PERSONA,
        )

        with patch(
            "astrbot.core.interaction.middleware.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch:
            middleware.continue_pipeline(webchat_event)
            await _drain_inbound_tasks(middleware)
            await _drain_inbound_tasks(middleware)

        assert webchat_event.get_extra("_astrbot_config") == runtime_config
        assert (
            dispatch.await_args.kwargs["event"].get_extra("_astrbot_config")
            == runtime_config
        )

    @pytest.mark.asyncio
    async def test_persona_does_not_persist_conversation_history_inline(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        controller.capture_visible_completion = AsyncMock(
            side_effect=_call_original_visible_completion
        )
        webchat_event.complete_visible_turn = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        conversation_manager = MagicMock()
        conversation_manager.get_curr_conversation_id = AsyncMock(return_value="conv-1")
        conversation_manager.add_message_pair = AsyncMock()
        middleware.plugin_context = MagicMock(
            spec=Context,
            conversation_manager=conversation_manager,
        )
        _stub_fast_response_route(
            middleware,
            first_response="嗯。",
            mode=InteractionRouteMode.PERSONA,
        )

        with patch(
            "astrbot.core.interaction.middleware.dispatch_postprocess",
            new=AsyncMock(),
        ):
            middleware.continue_pipeline(webchat_event)
            await _drain_inbound_tasks(middleware)
            await _drain_inbound_tasks(middleware)

        conversation_manager.get_curr_conversation_id.assert_not_awaited()
        conversation_manager.add_message_pair.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_persona_does_not_record_conversation_history_failure_inline(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.emit_immediate_spoken_reply = AsyncMock()
        controller.capture_visible_completion = AsyncMock(
            side_effect=_call_original_visible_completion
        )
        webchat_event.complete_visible_turn = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                }
            },
            queue,
            controller,
        )
        conversation_manager = MagicMock()
        conversation_manager.get_curr_conversation_id = AsyncMock(return_value="conv-1")
        conversation_manager.add_message_pair = AsyncMock(
            side_effect=RuntimeError("db unavailable")
        )
        middleware.plugin_context = MagicMock(
            spec=Context,
            conversation_manager=conversation_manager,
        )
        _stub_fast_response_route(
            middleware,
            first_response="嗯。",
            mode=InteractionRouteMode.PERSONA,
        )

        with patch(
            "astrbot.core.interaction.middleware.dispatch_postprocess",
            new=AsyncMock(),
        ):
            middleware.continue_pipeline(webchat_event)
            await _drain_inbound_tasks(middleware)
            await _drain_inbound_tasks(middleware)

        assert (
            webchat_event.get_extra("_interaction_conversation_history_failed") is None
        )
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.completion_state.completed is True

    @pytest.mark.asyncio
    async def test_preprocess_skips_media_after_interaction_materialization(
        self,
        voice_event,
    ):
        stt_provider = FakeSTTProvider("duplicate text")
        context = MagicMock()
        context.get_using_stt_provider.return_value = stt_provider
        stage = PreProcessStage()
        await stage.initialize(
            MagicMock(
                astrbot_config={
                    "provider_stt_settings": {"enable": True},
                    "platform_settings": {},
                },
                plugin_manager=MagicMock(context=context),
            )
        )
        voice_event.set_extra("_interaction_inbound_media_materialized", True)

        await stage.process(voice_event)

        assert stt_provider.calls == []
        assert voice_event.message_str == ""

    @pytest.mark.asyncio
    async def test_preprocess_transcribes_record_inside_reply_chain(
        self,
        webchat_event,
        tmp_path,
        monkeypatch,
    ):
        audio_path = tmp_path / "reply.wav"
        audio_path.write_bytes(b"fake-wav")
        reply = Reply(id="reply-1")
        reply.chain = [Record.fromFileSystem(str(audio_path))]
        webchat_event.message_str = ""
        webchat_event.message_obj.message_str = ""
        webchat_event.message_obj.message = [reply]

        async def fake_ensure_wav(path):
            return path

        async def fake_transcribe_record(ctx, event, record, provider, stage):
            assert stage == "pipeline.preprocess_stt"
            return type("Result", (), {"text": "引用语音"})()

        context = MagicMock()
        context.get_using_stt_provider.return_value = FakeSTTProvider("unused")
        stage = PreProcessStage()
        await stage.initialize(
            MagicMock(
                astrbot_config={
                    "provider_stt_settings": {"enable": True},
                    "platform_settings": {},
                },
                plugin_manager=MagicMock(context=context),
            )
        )
        monkeypatch.setattr(
            "astrbot.core.pipeline.preprocess_stage.stage.ensure_wav",
            fake_ensure_wav,
        )
        monkeypatch.setattr(
            "astrbot.core.pipeline.preprocess_stage.stage.transcribe_record",
            fake_transcribe_record,
        )

        await stage.process(webchat_event)

        assert isinstance(reply.chain[0], Plain)
        assert reply.chain[0].text == "引用语音"
        assert webchat_event.message_str == "引用语音"
