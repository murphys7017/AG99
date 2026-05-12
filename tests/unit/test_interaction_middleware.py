import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot.core.interaction.config import (
    is_middleware_enabled_for_platform,
    load_interaction_agent_config,
)
from astrbot.core.interaction.decision_agent import InteractionDecisionAgent
from astrbot.core.interaction.input_gateway import CoreInputGateway
from astrbot.core.interaction.middleware import InteractionMiddleware
from astrbot.core.interaction.output_controller import InteractionOutputController
from astrbot.core.interaction.turn_state import (
    InteractionTurnState,
    get_interaction_turn_state,
)
from astrbot.core.interaction.types import (
    FinalizerMode,
    InteractionAgentConfig,
    InteractionDecision,
    RouteMode,
)
from astrbot.core.message.components import Plain, Record
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.pipeline.preprocess_stage.stage import PreProcessStage
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.postprocess.types import PostProcessTrigger
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


async def _call_original_visible_completion(event):
    await event.get_extra("_interaction_original_complete_visible_turn")()


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
                "default_enabled_for_platforms": ["webchat"],
                "platforms": {"webchat": {"enabled": True}},
            }
        }
        assert is_middleware_enabled_for_platform("webchat", config) is False

    def test_explicit_platform_override_is_used(self):
        config = {
            "interaction_middleware": {
                "enabled": True,
                "default_enabled_for_platforms": [],
                "platforms": {"webchat": {"enabled": True}},
            }
        }
        assert is_middleware_enabled_for_platform("webchat", config) is True

    def test_default_platform_policy_is_used(self):
        config = {
            "interaction_middleware": {
                "enabled": True,
                "default_enabled_for_platforms": ["webchat"],
                "platforms": {},
            }
        }
        assert is_middleware_enabled_for_platform("webchat", config) is True
        assert is_middleware_enabled_for_platform("telegram", config) is False

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


class TestInteractionMiddleware:
    @pytest.mark.asyncio
    async def test_handle_inbound_schedules_async_for_enabled_platform(
        self, webchat_event
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.DELEGATE_TO_CORE,
                should_emit_immediate_reply=False,
                confidence=0.9,
                reason="delegate",
            )
        )

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)

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
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                },
                "provider_stt_settings": {"enable": True},
            },
            queue,
            controller,
            plugin_context=plugin_context,
        )
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.DELEGATE_TO_CORE,
                should_emit_immediate_reply=False,
                confidence=0.9,
                reason="delegate",
            )
        )

        middleware.handle_inbound(voice_event)
        await asyncio.sleep(0)

        forwarded_event = queue.get_nowait()
        middleware.decision_agent.decide.assert_awaited_once()
        decision_event = middleware.decision_agent.decide.await_args.args[0]
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
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                },
                "provider_stt_settings": {"enable": True},
            },
            queue,
            controller,
            plugin_context=plugin_context,
        )
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock()

        middleware.handle_inbound(voice_event)
        await asyncio.sleep(0)

        assert queue.empty()
        middleware.decision_agent.decide.assert_not_awaited()
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
    async def test_core_send_is_intercepted_after_forwarding(self, webchat_event):
        queue = asyncio.Queue()
        controller = MagicMock()
        controller.capture_message_chain = AsyncMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.DELEGATE_TO_CORE,
                should_emit_immediate_reply=False,
                confidence=0.9,
                reason="delegate",
            )
        )

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)
        forwarded_event = queue.get_nowait()
        message = MessageChain([Plain("core reply")])

        await forwarded_event.send(message)

        controller.capture_message_chain.assert_awaited_once_with(
            message,
            forwarded_event,
        )
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
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.DELEGATE_TO_CORE,
                should_emit_immediate_reply=False,
                confidence=0.9,
                reason="delegate",
            )
        )

        async def generator():
            yield MessageChain([Plain("chunk")])

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)
        forwarded_event = queue.get_nowait()

        await forwarded_event.send_streaming(generator(), use_fallback=True)

        controller.capture_streaming.assert_awaited_once()
        assert forwarded_event._has_send_oper is True

    @pytest.mark.asyncio
    async def test_core_streaming_finalizes_turn_after_stream_completion(
        self,
        streaming_event,
    ):
        queue = asyncio.Queue()
        controller = InteractionOutputController(
            interaction_config=InteractionAgentConfig(
                finalizer_mode=FinalizerMode.OFF,
                stream_observation_enabled=False,
                stream_interjection_enabled=False,
            )
        )
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.DELEGATE_TO_CORE,
                should_emit_immediate_reply=False,
                confidence=0.9,
                reason="delegate",
            )
        )
        middleware.memory_store.update_interaction_memory = AsyncMock()

        async def generator():
            yield MessageChain([Plain("stream final")])

        with patch(
            "astrbot.core.interaction.middleware.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch:
            middleware.handle_inbound(streaming_event)
            await asyncio.sleep(0)
            forwarded_event = queue.get_nowait()
            await forwarded_event.send_streaming(generator())
            await asyncio.sleep(0)

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
                    "kind": "core_stream",
                    "text": "stream final",
                    "memory_relevant": True,
                }
            ],
            "history_source": "interaction.turn.material",
        }

    def test_handle_inbound_skips_context_for_disabled_platform(self, webchat_event):
        queue = asyncio.Queue()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "default_enabled_for_platforms": [],
                    "platforms": {},
                }
            },
            queue,
            MagicMock(),
        )

        middleware.handle_inbound(webchat_event)

        assert queue.get_nowait() is webchat_event
        assert webchat_event.get_extra("_interaction_enabled") is None
        assert webchat_event.get_extra("_turn_id") is None
        assert webchat_event.get_extra("_output_controller") is None

    @pytest.mark.asyncio
    async def test_hybrid_emits_reply_before_forwarding(self, webchat_event):
        queue = asyncio.Queue()
        controller = MagicMock()
        release_persist = asyncio.Event()

        async def _emit_immediate_spoken_reply(*_args):
            webchat_event._has_send_oper = True

        async def _wait_for_persist_release(*_args):
            await release_persist.wait()

        controller.emit_immediate_spoken_reply = AsyncMock(
            side_effect=_emit_immediate_spoken_reply
        )
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.HYBRID,
                should_emit_immediate_reply=True,
                immediate_spoken_reply="嗯，我来处理。",
                confidence=0.9,
                reason="hybrid",
            )
        )
        middleware.memory_store.update_interaction_memory = AsyncMock(
            side_effect=_wait_for_persist_release
        )

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        controller.emit_immediate_spoken_reply.assert_awaited_once()
        forwarded_event = queue.get_nowait()
        assert forwarded_event is webchat_event
        assert forwarded_event._has_send_oper is False
        release_persist.set()
        await asyncio.sleep(0)

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
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.HYBRID,
                should_emit_immediate_reply=True,
                immediate_spoken_reply="等我看看。",
                confidence=0.9,
                reason="hybrid",
            )
        )
        persisted = asyncio.Event()
        middleware.memory_store.update_interaction_memory = AsyncMock(
            side_effect=lambda *a, **kw: persisted.set()
        )

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)

        assert queue.get_nowait() is webchat_event
        controller.emit_immediate_spoken_reply.assert_awaited_once()
        middleware.memory_store.update_interaction_memory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_inbound_refreshes_runtime_interaction_config(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        config = {
            "interaction_middleware": {
                "enabled": True,
                "default_enabled_for_platforms": ["webchat"],
                "platforms": {},
                "decision_provider_id": "",
            }
        }
        middleware = InteractionMiddleware(config, queue, controller)
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.DELEGATE_TO_CORE,
                should_emit_immediate_reply=False,
                confidence=0.9,
                reason="delegate",
            )
        )
        config["interaction_middleware"]["decision_provider_id"] = "runtime_provider"

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)

        assert middleware.interaction_config.decision_provider_id == "runtime_provider"
        assert controller.interaction_config.decision_provider_id == "runtime_provider"
        middleware.decision_agent.decide.assert_awaited_once()

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
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.plugin_context.get_config.return_value = {"wake_prefix": ["/"]}
        middleware.decision_agent = InteractionDecisionAgent(middleware.memory_store)

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)

        assert queue.get_nowait() is webchat_event
        controller.emit_immediate_spoken_reply.assert_not_awaited()
        decision = webchat_event.get_extra("_interaction_decision")
        assert decision.route_mode == RouteMode.DELEGATE_TO_CORE
        assert decision.reason == "protocol command bypass"

    @pytest.mark.asyncio
    async def test_missing_plugin_context_fail_fast_records_failure(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)

        assert queue.empty()
        assert webchat_event.get_extra("_interaction_decision_failed") is True
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.failures[-1].stage == "decision"
        assert turn_state.failures[-1].reason == "plugin_context_unavailable"

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
                        "default_enabled_for_platforms": ["webchat"],
                        "platforms": {},
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
                "default_enabled_for_platforms": ["webchat"],
                "platforms": {},
            }
        }
        middleware = InteractionMiddleware(config, queue, controller)
        config["interaction_middleware"]["fallback_policy"] = "observable_protect"

        with pytest.raises(RuntimeError, match="fallback_policy is disabled"):
            middleware.refresh_interaction_config()

    @pytest.mark.asyncio
    async def test_decision_pipeline_error_fail_fast_records_failure(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            side_effect=RuntimeError("decision broken")
        )

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)

        assert queue.empty()
        assert webchat_event.get_extra("_interaction_decision_failed") is True
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.failures[-1].stage == "decision"
        assert turn_state.failures[-1].reason == "decision_pipeline_error"

    @pytest.mark.asyncio
    async def test_hybrid_immediate_reply_failure_fail_fast_records_failure(
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
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.HYBRID,
                should_emit_immediate_reply=True,
                immediate_spoken_reply="嗯，我来处理。",
                confidence=0.9,
                reason="hybrid",
            )
        )

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)

        assert queue.empty()
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
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock()

        middleware.handle_inbound(live_event)
        await asyncio.sleep(0)

        assert queue.get_nowait() is live_event
        assert queue.empty()
        middleware.decision_agent.decide.assert_not_awaited()
        controller.emit_immediate_spoken_reply.assert_not_awaited()
        assert (
            live_event.get_extra("_interaction_live_mode_protocol_route")
            == "core_audio_stream"
        )
        turn_state = get_interaction_turn_state(live_event)
        assert turn_state is not None
        assert turn_state.decision is not None
        assert turn_state.decision.route_mode == RouteMode.DELEGATE_TO_CORE
        assert turn_state.decision.should_emit_immediate_reply is False
        assert turn_state.decision.reason == "live_mode_requires_audio_chunk_stream"
        assert turn_state.failures == []

    @pytest.mark.asyncio
    async def test_self_reply_immediate_reply_failure_fail_fast_records_failure(
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
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.SELF_REPLY,
                should_emit_immediate_reply=True,
                immediate_spoken_reply="嗯。",
                confidence=0.9,
                reason="self",
            )
        )

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)

        assert queue.empty()
        assert webchat_event.get_extra("_interaction_immediate_reply_failed") is True
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.failures[-1].stage == "immediate_reply"
        assert turn_state.failures[-1].reason == "send_failed"

    @pytest.mark.asyncio
    async def test_self_reply_without_immediate_reply_is_rejected(
        self,
        webchat_event,
    ):
        queue = asyncio.Queue()
        controller = MagicMock()
        middleware = InteractionMiddleware(
            {
                "interaction_middleware": {
                    "enabled": True,
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.SELF_REPLY,
                should_emit_immediate_reply=False,
                immediate_spoken_reply=None,
                confidence=0.9,
                reason="invalid self reply",
            )
        )

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)

        assert queue.empty()
        assert webchat_event.get_extra("_interaction_self_reply_invalid") is True
        assert (
            webchat_event.get_extra("_interaction_self_reply_invalid_reason")
            == "missing_immediate_reply"
        )
        turn_state = get_interaction_turn_state(webchat_event)
        assert turn_state is not None
        assert turn_state.failures[-1].stage == "decision"
        assert turn_state.failures[-1].reason == "missing_self_reply"

    @pytest.mark.asyncio
    async def test_self_reply_completion_does_not_write_legacy_interaction_memory(
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
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.SELF_REPLY,
                should_emit_immediate_reply=True,
                immediate_spoken_reply="嗯。",
                confidence=0.9,
                reason="self",
            )
        )
        middleware.memory_store.update_interaction_memory = AsyncMock()

        with patch(
            "astrbot.core.interaction.middleware.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch:
            middleware.handle_inbound(webchat_event)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

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
    async def test_self_reply_does_not_persist_if_visible_completion_fails(
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
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.SELF_REPLY,
                should_emit_immediate_reply=True,
                immediate_spoken_reply="嗯。",
                confidence=0.9,
                reason="self",
            )
        )
        middleware.memory_store.update_interaction_memory = AsyncMock()

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)

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
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
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
    async def test_self_reply_completes_visible_turn_after_immediate_reply(
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
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.SELF_REPLY,
                should_emit_immediate_reply=True,
                immediate_spoken_reply="嗯。",
                confidence=0.9,
                reason="self",
            )
        )
        middleware.memory_store.update_interaction_memory = AsyncMock()

        with patch(
            "astrbot.core.interaction.middleware.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch:
            middleware.handle_inbound(webchat_event)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

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
    async def test_self_reply_dispatches_postprocess_as_memory_owner(
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
                    "default_enabled_for_platforms": ["webchat"],
                    "platforms": {},
                }
            },
            queue,
            controller,
        )
        middleware.plugin_context = MagicMock(spec=Context)
        middleware.decision_agent = MagicMock(spec=InteractionDecisionAgent)
        middleware.decision_agent.decide = AsyncMock(
            return_value=InteractionDecision(
                route_mode=RouteMode.SELF_REPLY,
                should_emit_immediate_reply=True,
                immediate_spoken_reply="嗯。",
                confidence=0.9,
                reason="self",
            )
        )
        middleware.memory_store.update_interaction_memory = AsyncMock()
        order: list[str] = []

        with patch(
            "astrbot.core.interaction.middleware.dispatch_postprocess",
            new=AsyncMock(side_effect=lambda **_kwargs: order.append("postprocess")),
        ):
            middleware.handle_inbound(webchat_event)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        middleware.memory_store.update_interaction_memory.assert_not_awaited()
        assert order == ["postprocess"]

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


class TestCoreInputGateway:
    def test_put_nowait_delegates_to_middleware(self, webchat_event):
        middleware = MagicMock()
        gateway = CoreInputGateway(middleware)

        gateway.put_nowait(webchat_event)

        middleware.handle_inbound.assert_called_once_with(webchat_event)
