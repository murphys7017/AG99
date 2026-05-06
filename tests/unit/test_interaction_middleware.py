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
from astrbot.core.interaction.types import InteractionDecision, RouteMode
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.postprocess.types import PostProcessTrigger
from astrbot.core.star.context import Context


class ConcreteAstrMessageEvent(AstrMessageEvent):
    async def send(self, message):
        await super().send(message)


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
        assert webchat_event.get_extra("_output_controller") is controller
        assert (
            webchat_event.get_extra("_interaction_output_interceptor_installed") is True
        )

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

        async def _emit_immediate_spoken_reply(*_args):
            webchat_event._has_send_oper = True

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

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)

        controller.emit_immediate_spoken_reply.assert_awaited_once()
        forwarded_event = queue.get_nowait()
        assert forwarded_event is webchat_event
        assert forwarded_event._has_send_oper is False

    @pytest.mark.asyncio
    async def test_hybrid_immediate_reply_is_persisted_for_next_decision(
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
        middleware.memory_store.save_interaction_memory = AsyncMock()

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)

        assert queue.get_nowait() is webchat_event
        middleware.memory_store.save_interaction_memory.assert_awaited_once()
        snapshot = middleware.memory_store.save_interaction_memory.await_args.args[1]
        assert snapshot.recent_turns[0] == {
            "user": "Hello world",
            "assistant": "等我看看。",
            "turn_id": webchat_event.get_extra("_turn_id"),
        }

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
        assert decision.is_fallback is False
        assert decision.reason == "protocol command bypass"

    @pytest.mark.asyncio
    async def test_missing_plugin_context_records_fallback_and_forwards_core(
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

        assert queue.get_nowait() is webchat_event
        decision = webchat_event.get_extra("_interaction_decision")
        assert decision.is_fallback is True
        assert decision.fallback_reason == "plugin_context_unavailable"

    @pytest.mark.asyncio
    async def test_decision_pipeline_error_records_fallback_and_forwards_core(
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

        assert queue.get_nowait() is webchat_event
        decision = webchat_event.get_extra("_interaction_decision")
        assert decision.is_fallback is True
        assert decision.fallback_reason == "decision_pipeline_error"

    @pytest.mark.asyncio
    async def test_hybrid_immediate_reply_failure_still_forwards_core(
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

        assert queue.get_nowait() is webchat_event
        assert webchat_event.get_extra("_interaction_immediate_reply_failed") is True

    @pytest.mark.asyncio
    async def test_self_reply_immediate_reply_failure_forwards_core(
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

        assert queue.get_nowait() is webchat_event
        assert webchat_event.get_extra("_interaction_immediate_reply_failed") is True

    @pytest.mark.asyncio
    async def test_self_reply_without_immediate_reply_forwards_core(
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

        assert queue.get_nowait() is webchat_event
        assert webchat_event.get_extra("_interaction_self_reply_invalid") is True
        assert (
            webchat_event.get_extra("_interaction_self_reply_invalid_reason")
            == "missing_immediate_reply"
        )

    @pytest.mark.asyncio
    async def test_self_reply_memory_persist_failure_is_recorded(
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
        middleware.memory_store.save_interaction_memory = AsyncMock(
            side_effect=RuntimeError("disk full")
        )

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)

        assert queue.empty()
        complete_visible_turn.assert_awaited_once()
        controller.capture_visible_completion.assert_awaited_once_with(webchat_event)
        assert webchat_event.get_extra("_interaction_memory_persist_failed") is True
        assert (
            webchat_event.get_extra("_interaction_memory_persist_failure_reason")
            == "disk full"
        )

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
        middleware.memory_store.save_interaction_memory = AsyncMock()

        middleware.handle_inbound(webchat_event)
        await asyncio.sleep(0)

        assert queue.empty()
        complete_visible_turn.assert_awaited_once()
        controller.capture_visible_completion.assert_awaited_once_with(webchat_event)
        middleware.memory_store.save_interaction_memory.assert_not_awaited()
        assert webchat_event.get_extra("_interaction_visible_completion_failed") is True

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
        assert (
            dispatch.await_args.kwargs["trigger"]
            == PostProcessTrigger.AFTER_TURN_COMPLETED
        )
        assert dispatch.await_args.kwargs["turn_id"] == webchat_event.get_extra(
            "_turn_id"
        )


class TestCoreInputGateway:
    def test_put_nowait_delegates_to_middleware(self, webchat_event):
        middleware = MagicMock()
        gateway = CoreInputGateway(middleware)

        gateway.put_nowait(webchat_event)

        middleware.handle_inbound.assert_called_once_with(webchat_event)
