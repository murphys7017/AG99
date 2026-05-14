import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot.core.interaction.contributors import (
    InteractionResultContribution,
    InteractionResultView,
)
from astrbot.core.interaction.finalizer import InteractionFinalizerError
from astrbot.core.interaction.memory_store import (
    build_interaction_memory_reply_from_visible_outputs,
)
from astrbot.core.interaction.output_controller import InteractionOutputController
from astrbot.core.interaction.turn_state import (
    InteractionContextMaterial,
    InteractionStreamState,
    InteractionTurnState,
    append_interaction_turn_visible_output,
    get_interaction_turn_state,
    mark_interaction_turn_completed,
    set_interaction_turn_decision,
    set_interaction_turn_finalized_material,
)
from astrbot.core.interaction.types import (
    FinalizerMode,
    InteractionAgentConfig,
    InteractionDecision,
    RouteMode,
)
from astrbot.core.message.components import Image, Json, Plain, Record
from astrbot.core.message.message_event_result import (
    MessageChain,
    MessageEventResult,
    ResultContentType,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.webchat.webchat_event import WebChatMessageEvent


class ConcreteMessageEvent(AstrMessageEvent):
    async def send(self, message):
        await super().send(message)


@pytest.fixture
def webchat_event():
    platform_meta = PlatformMetadata(
        name="webchat",
        description="webchat",
        id="webchat",
    )
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = "webchat"
    message.session_id = "webchat!user!session123"
    message.message_id = "msg123"
    message.sender = MessageMember(user_id="user123", nickname="TestUser")
    message.message = [Plain(text="帮我查一下天气")]
    message.message_str = "帮我查一下天气"
    event = WebChatMessageEvent(
        message_str="帮我查一下天气",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="webchat!user!session123",
    )
    event.set_extra("_turn_id", "turn-1")
    set_interaction_turn_decision(event, InteractionDecision(reason="test"))
    return event


@pytest.fixture
def generic_event():
    platform_meta = PlatformMetadata(
        name="generic",
        description="generic",
        id="generic",
    )
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = "generic"
    message.session_id = "generic-session"
    message.message_id = "generic-msg"
    message.sender = MessageMember(user_id="user123", nickname="TestUser")
    message.message = [Plain(text="hello")]
    message.message_str = "hello"
    event = ConcreteMessageEvent(
        message_str="hello",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="generic-session",
    )
    event.set_extra("_turn_id", "turn-generic")
    return event


class ResultContributor:
    plugin_id = "result_plugin"
    priority = 10
    expected_core_result = "dry result"
    final_text_override = "wrapped result"

    async def collect(self, event, plugin_context, result_view):
        assert result_view.turn_id == "turn-1"
        assert result_view.session_id == event.unified_msg_origin
        assert result_view.core_result == self.expected_core_result
        return InteractionResultContribution(
            plugin_id=self.plugin_id,
            platform_extras={"adapter_object": {"ok": True}},
            client_objects=[{"kind": "card"}],
            final_text_override=self.final_text_override,
            metadata={"source": "unit"},
            priority=self.priority,
        )


class ImmediateResultContributor:
    plugin_id = "immediate_result_plugin"

    def __init__(self):
        self.view = None

    async def collect(self, event, plugin_context, result_view):
        assert result_view.turn_id == "turn-1"
        assert result_view.session_id == event.unified_msg_origin
        assert result_view.core_result is None
        assert result_view.final_result == "嗯，我来看看。"
        assert result_view.immediate_reply == "嗯，我来看看。"
        assert result_view.metadata["phase"] == "immediate"
        assert result_view.metadata["message_kind"] == "immediate_reply"
        assert result_view.metadata["is_immediate"] is True
        assert result_view.metadata["is_final"] is False
        assert result_view.final_candidate_material["visible_outputs"][-1] == {
            "turn_id": "turn-1",
            "kind": "immediate_reply",
            "text": "嗯，我来看看。",
            "memory_relevant": True,
        }
        self.view = result_view
        return InteractionResultContribution(
            plugin_id=self.plugin_id,
            platform_extras={"adapter_object": {"phase": "immediate"}},
            client_objects=[{"kind": "motion"}],
            final_text_override="嗯，我马上看。",
            metadata={"source": "immediate-unit"},
        )


class MutatingResultContributor:
    plugin_id = "mutating_plugin"

    async def collect(self, event, plugin_context, result_view):
        with pytest.raises(TypeError):
            result_view.decision["route_mode"] = "self_reply"
        with pytest.raises(TypeError):
            result_view.metadata["bad"] = True
        with pytest.raises(TypeError):
            result_view.visible_outputs[0]["text"] = "changed"
        with pytest.raises(TypeError):
            result_view.utterances[0]["text"] = "changed"
        with pytest.raises(TypeError):
            result_view.turn_material_snapshot["assistant"] = "changed"
        with pytest.raises(TypeError):
            result_view.final_candidate_material["assistant_text"] = "changed"
        return None


class FailingResultContributor:
    plugin_id = "failing_plugin"

    async def collect(self, event, plugin_context, result_view):
        raise RuntimeError("contributor broken")


class InspectingResultContributor:
    plugin_id = "inspecting_plugin"

    def __init__(self):
        self.view = None

    async def collect(self, event, plugin_context, result_view):
        assert isinstance(result_view, InteractionResultView)
        assert result_view["turn_id"] == "turn-1"
        assert result_view["decision"]["route_mode"] == "delegate_to_core"
        assert result_view.visible_outputs[0]["kind"] == "immediate_reply"
        assert result_view.utterances[0]["kind"] == "immediate_reply"
        assert result_view.turn_material_snapshot["assistant"] == "final answer"
        assert result_view.finalized_turn_material["assistant"] == "final answer"
        assert result_view.metadata["phase"] == "final"
        assert result_view.metadata["message_kind"] == "core_reply"
        assert result_view.metadata["is_immediate"] is False
        assert result_view.metadata["is_final"] is True
        assert result_view.final_candidate_material["assistant_text"] == "dry result"
        assert result_view.final_candidate_material["visible_outputs"][-1] == {
            "turn_id": "turn-1",
            "kind": "core_reply",
            "text": "dry result",
            "memory_relevant": True,
        }
        self.view = result_view
        return None


async def _mark_completed_callback(event):  # noqa: ANN001
    turn_state = get_interaction_turn_state(event)
    assert turn_state is not None
    visible_outputs = [dict(output) for output in turn_state.visible_outputs]
    canonical_reply = build_interaction_memory_reply_from_visible_outputs(
        visible_outputs,
        turn_id=turn_state.turn_id,
        utterances=turn_state.utterances,
    )
    if canonical_reply:
        set_interaction_turn_finalized_material(
            event,
            {
                "turn_id": turn_state.turn_id,
                "user_text": (event.message_str or "").strip(),
                "assistant_text": canonical_reply,
                "visible_outputs": visible_outputs,
                "history_source": "interaction.turn.material",
            },
        )
    mark_interaction_turn_completed(event)


class StreamInterjectionDecider:
    plugin_id = "stream_plugin"

    def __init__(self):
        self.views = []

    async def decide(self, event, plugin_context, stream_view):
        assert stream_view["turn_id"] == "turn-1"
        assert stream_view.turn_id == "turn-1"
        with pytest.raises(TypeError):
            stream_view["metadata"]["bad"] = True
        self.views.append(dict(stream_view))
        if stream_view["window_index"] != 1:
            return {
                "should_interject": False,
                "reason": "only_first_window",
            }
        assert stream_view["is_final"] is False
        assert stream_view["observed_text"] == "hello"
        assert stream_view["total_text"] == "hello"
        return {
            "should_interject": True,
            "reply": "嗯，我听着。",
            "reason": "unit",
        }


class FinalStreamInterjectionDecider:
    plugin_id = "final_stream_plugin"

    def __init__(self):
        self.views = []

    async def decide(self, event, plugin_context, stream_view):
        self.views.append(dict(stream_view))
        return {
            "should_interject": True,
            "reply": "收到了。",
            "reason": "final_window",
        }


class SlowStreamInterjectionDecider:
    plugin_id = "slow_stream_plugin"

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def decide(self, event, plugin_context, stream_view):
        self.started.set()
        await self.release.wait()
        return {
            "should_interject": False,
            "reason": "slow",
        }


class MutatingStreamViewDecider:
    plugin_id = "mutating_stream_plugin"

    def __init__(self):
        self.view = None

    async def decide(self, event, plugin_context, stream_view):
        self.view = stream_view
        with pytest.raises(TypeError):
            stream_view.metadata["bad"] = True
        with pytest.raises(AttributeError):
            stream_view.utterances.append("bad")
        return {
            "should_interject": False,
            "reason": "read_only",
        }


class FailingStreamInterjectionDecider:
    plugin_id = "failing_stream_plugin"

    async def decide(self, event, plugin_context, stream_view):
        raise RuntimeError("decider failed")


class InvalidStreamInterjectionDecider:
    plugin_id = "invalid_stream_plugin"

    async def decide(self, event, plugin_context, stream_view):
        return "not a decision"


@pytest.mark.asyncio
async def test_capture_message_chain_collects_result_contributors(webchat_event):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.list_interaction_result_contributors.return_value = [
        ResultContributor()
    ]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
        persist_callback=_mark_completed_callback,
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        webchat_event.set_result(
            MessageEventResult(
                chain=[Plain("dry result")],
                result_content_type=ResultContentType.LLM_RESULT,
            )
        )
        await controller.capture_message_chain(
            MessageChain([Plain("dry result")]),
            webchat_event,
        )

    payload = queue.get_nowait()
    assert payload["data"] == "wrapped result"
    assert payload["platform_extras"]["turn_id"] == "turn-1"
    assert payload["platform_extras"]["adapter_object"] == {"ok": True}
    assert payload["platform_extras"]["client_objects"] == [{"kind": "card"}]
    assert queue.empty()
    assert webchat_event.get_extra("_visible_turn_completion_sent") is None


@pytest.mark.asyncio
async def test_immediate_reply_collects_result_contributors(webchat_event):
    queue = asyncio.Queue()
    contributor = ImmediateResultContributor()
    plugin_context = MagicMock()
    plugin_context.list_interaction_result_contributors.return_value = [contributor]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    decision = InteractionDecision(
        should_emit_immediate_reply=True,
        immediate_spoken_reply="嗯，我来看看。",
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.emit_immediate_spoken_reply(decision, webchat_event)

    payload = queue.get_nowait()
    assert payload["data"] == "嗯，我马上看。"
    assert payload["platform_extras"]["turn_id"] == "turn-1"
    assert payload["platform_extras"]["message_kind"] == "immediate_reply"
    assert payload["platform_extras"]["adapter_object"] == {"phase": "immediate"}
    assert payload["platform_extras"]["client_objects"] == [{"kind": "motion"}]
    assert payload["platform_extras"]["metadata"] == {"source": "immediate-unit"}
    assert (
        payload["platform_extras"]["visible_message_id"]
        == "turn-1::immediate_reply::0001"
    )
    assert queue.empty()
    plugin_context.list_interaction_result_contributors.assert_called_once_with()
    assert contributor.view is not None
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert turn_state.immediate_reply == "嗯，我马上看。"
    assert turn_state.visible_outputs[0]["text"] == "嗯，我马上看。"


@pytest.mark.asyncio
async def test_immediate_reply_materializes_tts_without_reasoning_or_t2i(webchat_event):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.get_config.return_value = {
        "provider_tts_settings": {
            "enable": True,
            "dual_output": False,
            "use_file_service": False,
            "trigger_probability": 1.0,
        },
        "provider_settings": {},
        "t2i": True,
        "t2i_word_threshold": 1,
    }
    tts_provider = MagicMock()
    tts_provider.meta.return_value.id = "tts-provider"
    tts_provider.get_audio = AsyncMock(return_value="voice.wav")
    plugin_context.get_using_tts_provider.return_value = tts_provider
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    controller.show_reasoning = True
    webchat_event.set_extra("_llm_reasoning_content", "hidden chain of thought")
    decision = InteractionDecision(
        should_emit_immediate_reply=True,
        immediate_spoken_reply="嗯，我来看看。",
    )

    with (
        patch(
            "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
            return_value=queue,
        ),
        patch.object(
            Record,
            "convert_to_base64",
            new=AsyncMock(return_value="dm9pY2U="),
        ),
        patch(
            "astrbot.core.interaction.output_controller.SessionServiceManager.should_process_tts_request",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "astrbot.core.interaction.output_controller.html_renderer.render_t2i",
            new=AsyncMock(side_effect=AssertionError("immediate reply must not use t2i")),
        ),
    ):
        await controller.emit_immediate_spoken_reply(decision, webchat_event)

    payload = queue.get_nowait()
    assert payload["type"] == "record"
    assert payload["platform_extras"]["message_kind"] == "immediate_reply"
    assert payload["platform_extras"]["semantic_text"] == "嗯，我来看看。"
    assert queue.empty()
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert turn_state.utterances[0].text == "嗯，我来看看。"
    assert turn_state.utterances[0].metadata["delivered_as"] == "record"
    assert turn_state.utterances[0].metadata["tts"][0]["tts_audio_path"] == "voice.wav"
    assert (
        turn_state.utterances[0].metadata["tts"][0]["tts_provider_id"]
        == "tts-provider"
    )


@pytest.mark.asyncio
async def test_immediate_reply_uses_session_scoped_tts_config(webchat_event):
    queue = asyncio.Queue()
    session_config = {
        "provider_tts_settings": {
            "enable": True,
            "dual_output": False,
            "use_file_service": False,
            "trigger_probability": 1.0,
        },
        "provider_settings": {},
        "t2i": False,
    }
    global_config = {
        "provider_tts_settings": {
            "enable": False,
            "dual_output": False,
            "use_file_service": False,
            "trigger_probability": 1.0,
        },
        "provider_settings": {},
        "t2i": False,
    }
    plugin_context = MagicMock()
    plugin_context.get_config.side_effect = (
        lambda umo=None: session_config
        if umo == webchat_event.unified_msg_origin
        else global_config
    )
    tts_provider = MagicMock()
    tts_provider.meta.return_value.id = "tts-provider"
    tts_provider.get_audio = AsyncMock(return_value="voice.wav")
    plugin_context.get_using_tts_provider.return_value = tts_provider
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    decision = InteractionDecision(
        should_emit_immediate_reply=True,
        immediate_spoken_reply="嗯，我来看看。",
    )

    with (
        patch(
            "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
            return_value=queue,
        ),
        patch.object(
            Record,
            "convert_to_base64",
            new=AsyncMock(return_value="dm9pY2U="),
        ),
        patch(
            "astrbot.core.interaction.output_controller.SessionServiceManager.should_process_tts_request",
            new=AsyncMock(return_value=True),
        ),
    ):
        await controller.emit_immediate_spoken_reply(decision, webchat_event)

    payload = queue.get_nowait()
    assert payload["type"] == "record"
    plugin_context.get_config.assert_any_call(umo=webchat_event.unified_msg_origin)


@pytest.mark.asyncio
async def test_immediate_reply_dual_output_keeps_single_semantic_text(
    webchat_event,
):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.get_config.return_value = {
        "provider_tts_settings": {
            "enable": True,
            "dual_output": True,
            "use_file_service": False,
            "trigger_probability": 1.0,
        },
        "provider_settings": {},
        "t2i": False,
    }
    tts_provider = MagicMock()
    tts_provider.meta.return_value.id = "tts-provider"
    tts_provider.get_audio = AsyncMock(return_value="voice.wav")
    plugin_context.get_using_tts_provider.return_value = tts_provider
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    decision = InteractionDecision(
        should_emit_immediate_reply=True,
        immediate_spoken_reply="行，马上。",
    )

    with (
        patch(
            "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
            return_value=queue,
        ),
        patch.object(
            Record,
            "convert_to_base64",
            new=AsyncMock(return_value="dm9pY2U="),
        ),
        patch(
            "astrbot.core.interaction.output_controller.SessionServiceManager.should_process_tts_request",
            new=AsyncMock(return_value=True),
        ),
    ):
        await controller.emit_immediate_spoken_reply(decision, webchat_event)

    record_payload = queue.get_nowait()
    plain_payload = queue.get_nowait()
    assert record_payload["type"] == "record"
    assert plain_payload["type"] == "plain"
    assert (
        record_payload["platform_extras"]["semantic_text"]
        == plain_payload["platform_extras"]["semantic_text"]
        == "行，马上。"
    )
    assert (
        record_payload["platform_extras"]["visible_message_id"]
        != plain_payload["platform_extras"]["visible_message_id"]
    )
    assert queue.empty()
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert len(turn_state.utterances) == 1
    assert turn_state.utterances[0].text == "行，马上。"
    assert turn_state.utterances[0].delivered_message_ids == [
        record_payload["platform_extras"]["visible_message_id"],
        plain_payload["platform_extras"]["visible_message_id"],
    ]


@pytest.mark.asyncio
async def test_hybrid_visible_outputs_share_turn_id_but_get_distinct_message_ids(
    webchat_event,
):
    queue = asyncio.Queue()
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    decision = InteractionDecision(
        should_emit_immediate_reply=True,
        immediate_spoken_reply="行，等我查一下。",
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("设计问题，我改不了。")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.emit_immediate_spoken_reply(decision, webchat_event)
        await controller.capture_message_chain(
            MessageChain([Plain("设计问题，我改不了。")]),
            webchat_event,
        )

    immediate_payload = queue.get_nowait()
    core_payload = queue.get_nowait()

    assert immediate_payload["platform_extras"]["turn_id"] == "turn-1"
    assert core_payload["platform_extras"]["turn_id"] == "turn-1"
    assert immediate_payload["platform_extras"]["message_kind"] == "immediate_reply"
    assert core_payload["platform_extras"]["message_kind"] == "core_reply"
    assert (
        immediate_payload["platform_extras"]["visible_message_id"]
        == "turn-1::immediate_reply::0001"
    )
    assert (
        core_payload["platform_extras"]["visible_message_id"]
        == "turn-1::core_reply::0002"
    )
    assert (
        immediate_payload["platform_extras"]["visible_message_id"]
        != core_payload["platform_extras"]["visible_message_id"]
    )
    assert webchat_event.get_extra("_visible_turn_outputs") == [
        {
            "turn_id": "turn-1",
            "kind": "immediate_reply",
            "text": "行，等我查一下。",
            "memory_relevant": True,
        },
        {
            "turn_id": "turn-1",
            "kind": "core_reply",
            "text": "设计问题，我改不了。",
            "memory_relevant": True,
        },
    ]
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert [utterance.message_id for utterance in turn_state.utterances] == [
        "turn-1::immediate_reply::0001",
        "turn-1::core_reply::0002",
    ]
    assert [utterance.delivered_message_ids for utterance in turn_state.utterances] == [
        ["turn-1::immediate_reply::0001"],
        ["turn-1::core_reply::0002"],
    ]
    assert queue.empty()


@pytest.mark.asyncio
async def test_immediate_reply_uses_generic_event_send_for_non_webchat(generic_event):
    controller = InteractionOutputController()
    generic_event.send = AsyncMock()
    decision = InteractionDecision(
        should_emit_immediate_reply=True,
        immediate_spoken_reply="嗯，我在。",
    )

    await controller.emit_immediate_spoken_reply(decision, generic_event)

    generic_event.send.assert_awaited_once()
    message = generic_event.send.await_args.args[0]
    assert message.get_plain_text() == "嗯，我在。"
    assert generic_event.get_extra("_output_controller") is None


@pytest.mark.asyncio
async def test_immediate_reply_does_not_mark_generic_event_as_core_sent(
    generic_event,
):
    controller = InteractionOutputController()
    decision = InteractionDecision(
        should_emit_immediate_reply=True,
        immediate_spoken_reply="嗯，我在。",
    )

    await controller.emit_immediate_spoken_reply(decision, generic_event)

    assert generic_event._has_send_oper is False


@pytest.mark.asyncio
async def test_general_result_is_passthrough_without_final_contributors(webchat_event):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.list_interaction_result_contributors.return_value = [
        ResultContributor()
    ]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
        persist_callback=_mark_completed_callback,
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("command result")],
            result_content_type=ResultContentType.GENERAL_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("command result")]),
            webchat_event,
        )

    payload = queue.get_nowait()
    assert payload["data"] == "command result"
    assert payload["platform_extras"]["turn_id"] == "turn-1"
    assert payload["platform_extras"]["message_kind"] == "passthrough"
    assert webchat_event.get_extra("_visible_turn_outputs") == [
        {
            "turn_id": "turn-1",
            "kind": "passthrough",
            "text": "command result",
            "memory_relevant": True,
        }
    ]
    assert queue.empty()
    plugin_context.list_interaction_result_contributors.assert_not_called()
    assert webchat_event.get_extra("_interaction_finalized_turn_material") == {
        "turn_id": "turn-1",
        "user_text": "帮我查一下天气",
        "assistant_text": "command result",
        "visible_outputs": [
            {
                "turn_id": "turn-1",
                "kind": "passthrough",
                "text": "command result",
                "memory_relevant": True,
            }
        ],
        "history_source": "interaction.turn.material",
    }


@pytest.mark.asyncio
async def test_hybrid_stream_followup_send_is_not_classified_as_passthrough(
    webchat_event,
):
    queue = asyncio.Queue()
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
        persist_callback=_mark_completed_callback,
    )
    set_interaction_turn_decision(
        webchat_event,
        InteractionDecision(
            route_mode=RouteMode.HYBRID,
            should_emit_immediate_reply=True,
            immediate_spoken_reply="我看看。",
            reason="hybrid",
        ),
    )

    async def generator():
        yield MessageChain([Plain("stream final")])

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_streaming(generator(), webchat_event)
        webchat_event.set_result(
            MessageEventResult(
                chain=[Plain("可以执行cmd，限制当前工作目录。没联网权限。")],
                result_content_type=ResultContentType.GENERAL_RESULT,
            )
        )
        await controller.capture_message_chain(
            MessageChain([Plain("可以执行cmd，限制当前工作目录。没联网权限。")]),
            webchat_event,
        )

    payloads = []
    while not queue.empty():
        payloads.append(queue.get_nowait())

    streamed_payload = payloads[0]
    final_payload = payloads[-1]
    assert streamed_payload["data"] == "stream final"
    assert final_payload["data"] == "可以执行cmd，限制当前工作目录。没联网权限。"
    assert final_payload["platform_extras"]["message_kind"] == "core_reply"
    assert webchat_event.get_extra("_visible_turn_outputs") == [
        {
            "turn_id": "turn-1",
            "kind": "core_stream",
            "text": "stream final",
            "memory_relevant": True,
        },
        {
            "turn_id": "turn-1",
            "kind": "core_reply",
            "text": "可以执行cmd，限制当前工作目录。没联网权限。",
            "memory_relevant": True,
        },
    ]


@pytest.mark.asyncio
async def test_core_final_result_is_consumed_only_once_for_segmented_sends(
    webchat_event,
):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.list_interaction_result_contributors.return_value = [
        ResultContributor()
    ]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("dry result")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("dry result")]),
            webchat_event,
        )
        await controller.capture_message_chain(
            MessageChain([Plain("second segment")]),
            webchat_event,
        )

    first_payload = queue.get_nowait()
    assert first_payload["data"] == "wrapped result"
    assert queue.empty()
    plugin_context.list_interaction_result_contributors.assert_called_once()


@pytest.mark.asyncio
async def test_result_contributor_receives_read_only_view(webchat_event):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    inspecting_contributor = InspectingResultContributor()
    plugin_context.list_interaction_result_contributors.return_value = [
        inspecting_contributor,
        MutatingResultContributor(),
    ]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("dry result")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.emit_immediate_spoken_reply(
            InteractionDecision(
                should_emit_immediate_reply=True,
                immediate_spoken_reply="行，等我查一下。",
            ),
            webchat_event,
        )
        set_interaction_turn_finalized_material(
            webchat_event,
            {
                "turn_id": "turn-1",
                "user_text": "帮我查一下天气",
                "assistant": "final answer",
            },
        )
        await controller.capture_message_chain(
            MessageChain([Plain("dry result")]),
            webchat_event,
        )

    immediate_payload = queue.get_nowait()
    payload = queue.get_nowait()
    assert immediate_payload["data"] == "行，等我查一下。"
    assert payload["data"] == "dry result"
    assert queue.empty()
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert turn_state.visible_outputs[0]["text"] == "行，等我查一下。"
    assert turn_state.utterances[0].text == "行，等我查一下。"
    assert turn_state.finalized_turn_material is not None
    assert turn_state.finalized_turn_material["assistant_text"] == (
        "行，等我查一下。 dry result"
    )
    assert inspecting_contributor.view is not None
    assert inspecting_contributor.view.get("session_id") == webchat_event.unified_msg_origin


@pytest.mark.asyncio
async def test_result_contributor_failure_is_recorded(webchat_event):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.list_interaction_result_contributors.return_value = [
        FailingResultContributor()
    ]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("dry result")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("dry result")]),
            webchat_event,
        )

    payload = queue.get_nowait()
    assert payload["data"] == "dry result"
    assert queue.empty()
    failures = webchat_event.get_extra("_interaction_result_contributor_failures")
    assert failures == [{"plugin_id": "failing_plugin", "error": "contributor broken"}]


@pytest.mark.asyncio
async def test_output_controller_requires_persist_callback_for_interaction_completion(
    webchat_event,
):
    queue = asyncio.Queue()
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("dry result")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("dry result")]),
            webchat_event,
        )

    payload = queue.get_nowait()
    assert payload["data"] == "dry result"
    assert queue.empty()
    assert webchat_event.get_extra("_interaction_persist_callback_missing") is True
    assert (
        webchat_event.get_extra("_interaction_turn_finalization_failure_reason")
        == "missing_persist_callback"
    )
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert turn_state.completion_state.legacy_memory_persisted is False
    assert turn_state.completion_state.completed is False
    assert turn_state.completion_state.failure_reason == "missing_persist_callback"


@pytest.mark.asyncio
async def test_outbound_final_material_uses_visible_outputs_as_canonical_reply(
    webchat_event,
):
    queue = asyncio.Queue()
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
        persist_callback=_mark_completed_callback,
    )
    append_interaction_turn_visible_output(
        webchat_event,
        message_kind="immediate_reply",
        text="等我看看。",
    )
    append_interaction_turn_visible_output(
        webchat_event,
        message_kind="stream_interjection",
        text="还在查。",
        memory_relevant=False,
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("你可以执行工作区命令。")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("你可以执行工作区命令。")]),
            webchat_event,
        )

    assert webchat_event.get_extra("_interaction_finalized_turn_material") == {
        "turn_id": "turn-1",
        "user_text": "帮我查一下天气",
        "assistant_text": "等我看看。 你可以执行工作区命令。",
        "visible_outputs": [
            {
                "turn_id": "turn-1",
                "kind": "immediate_reply",
                "text": "等我看看。",
                "memory_relevant": True,
            },
            {
                "turn_id": "turn-1",
                "kind": "stream_interjection",
                "text": "还在查。",
                "memory_relevant": False,
            },
            {
                "turn_id": "turn-1",
                "kind": "core_reply",
                "text": "你可以执行工作区命令。",
                "memory_relevant": True,
            },
        ],
        "history_source": "interaction.turn.material",
    }


@pytest.mark.asyncio
async def test_force_finalizer_failure_fail_fast_does_not_send_raw_core_result(
    webchat_event,
):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.get_provider_by_id.return_value = None
    plugin_context.list_interaction_result_contributors.return_value = []
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.FORCE,
            finalizer_provider_id="missing",
        ),
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("raw core result")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        with pytest.raises(RuntimeError, match="provider_unavailable"):
            await controller.capture_message_chain(
                MessageChain([Plain("raw core result")]),
                webchat_event,
            )

    assert queue.empty()
    assert webchat_event.get_extra("_interaction_finalizer_failed") is True
    assert (
        webchat_event.get_extra("_interaction_finalizer_failure_reason")
        == "provider_unavailable"
    )
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert turn_state.failures[-1].stage == "finalizer"
    assert turn_state.failures[-1].reason == "provider_unavailable"


@pytest.mark.asyncio
async def test_force_finalizer_failure_observable_protect_does_not_send_notice(
    webchat_event,
):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.get_provider_by_id.return_value = None
    plugin_context.list_interaction_result_contributors.return_value = []
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.FORCE,
            finalizer_provider_id="missing",
        ),
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("raw core result")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        with pytest.raises(InteractionFinalizerError, match="provider_unavailable"):
            await controller.capture_message_chain(
                MessageChain([Plain("raw core result")]),
                webchat_event,
            )

    assert queue.empty()
    assert webchat_event.get_extra("_interaction_finalizer_failed") is True
    assert (
        webchat_event.get_extra("_interaction_finalizer_failure_reason")
        == "provider_unavailable"
    )


@pytest.mark.asyncio
async def test_segmented_core_final_uses_full_result_once(webchat_event):
    queue = asyncio.Queue()
    contributor = ResultContributor()
    contributor.expected_core_result = "dry result  second segment"
    plugin_context = MagicMock()
    plugin_context.list_interaction_result_contributors.return_value = [contributor]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("dry result"), Plain(" second segment")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("dry result")]),
            webchat_event,
        )
        await controller.capture_message_chain(
            MessageChain([Plain(" second segment")]),
            webchat_event,
        )

    payload = queue.get_nowait()
    assert payload["data"] == "wrapped result"
    assert queue.empty()
    plugin_context.list_interaction_result_contributors.assert_called_once()


@pytest.mark.asyncio
async def test_core_final_result_reuses_segmented_delivery_rules(webchat_event):
    queue = asyncio.Queue()
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
        platform_settings={
            "segmented_reply": {
                "enable": True,
                "only_llm_result": True,
                "interval_method": "random",
                "interval": "0,0",
            }
        },
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("first"), Plain("second")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("first")]),
            webchat_event,
        )

    first_payload = queue.get_nowait()
    second_payload = queue.get_nowait()
    assert first_payload["data"] == "first"
    assert second_payload["data"] == "second"
    assert first_payload["platform_extras"]["turn_id"] == "turn-1"
    assert second_payload["platform_extras"]["turn_id"] == "turn-1"
    assert (
        first_payload["platform_extras"]["visible_message_id"]
        == "turn-1::core_reply::0001"
    )
    assert (
        second_payload["platform_extras"]["visible_message_id"]
        == "turn-1::core_reply::0002"
    )
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert len(turn_state.utterances) == 1
    assert turn_state.utterances[0].message_id == "turn-1::core_reply::0001"
    assert turn_state.utterances[0].delivered_message_ids == [
        "turn-1::core_reply::0001",
        "turn-1::core_reply::0002",
    ]
    assert queue.empty()


@pytest.mark.asyncio
async def test_capture_streaming_observes_core_chunks_without_interjection(
    webchat_event,
):
    queue = asyncio.Queue()
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_observation_min_chars=5,
            stream_interjection_enabled=False,
        ),
    )

    async def generator():
        yield MessageChain([Plain("hello")])
        yield MessageChain([Plain(" world")])

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_streaming(generator(), webchat_event)

    payloads = []
    while not queue.empty():
        payloads.append(queue.get_nowait())

    assert [payload["data"] for payload in payloads] == [
        "hello",
        " world",
        "hello world",
    ]
    assert payloads[-1]["type"] == "complete"
    assert webchat_event.get_extra("_interaction_core_stream_text") == "hello world"
    assert webchat_event.get_extra("_interaction_core_stream_observation_count") == 3
    assert (
        webchat_event.get_extra("_interaction_core_streaming_result_consumed") is True
    )
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert turn_state.stream_state.total_text == "hello world"
    assert turn_state.stream_state.pending_text == ""
    assert turn_state.stream_state.observation_count == 3
    assert turn_state.stream_state.result_consumed is True


@pytest.mark.asyncio
async def test_capture_streaming_tracks_text_when_observation_disabled(webchat_event):
    queue = asyncio.Queue()
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_observation_enabled=False,
            stream_interjection_enabled=False,
        ),
        persist_callback=_mark_completed_callback,
    )

    async def generator():
        yield MessageChain([Plain("hello")])
        yield MessageChain([Plain(" world")])

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_streaming(generator(), webchat_event)

    assert webchat_event.get_extra("_interaction_core_stream_text") == "hello world"
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert turn_state.stream_state.total_text == "hello world"
    assert turn_state.stream_state.pending_text == ""
    assert webchat_event.get_extra("_visible_turn_outputs") == [
        {
            "turn_id": "turn-1",
            "kind": "core_stream",
            "text": "hello world",
            "memory_relevant": True,
        }
    ]
    assert webchat_event.get_extra("_interaction_finalized_turn_material") == {
        "turn_id": "turn-1",
        "user_text": "帮我查一下天气",
        "assistant_text": "hello world",
        "visible_outputs": [
            {
                "turn_id": "turn-1",
                "kind": "core_stream",
                "text": "hello world",
                "memory_relevant": True,
            }
        ],
        "history_source": "interaction.turn.material",
    }
    assert len(turn_state.utterances) == 1
    assert turn_state.utterances[0].kind == "core_stream"
    assert turn_state.utterances[0].text == "hello world"
    assert turn_state.completion_state.material_finalized is True
    assert turn_state.completion_state.completed is True


@pytest.mark.asyncio
async def test_capture_streaming_uses_audio_chunk_text_for_live_material(
    webchat_event,
):
    queue = asyncio.Queue()
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_observation_enabled=False,
            stream_interjection_enabled=False,
        ),
        persist_callback=_mark_completed_callback,
    )

    async def generator():
        audio_chunk = MessageChain([Plain("audio-base64"), Json({"text": "spoken"})])
        audio_chunk.type = "audio_chunk"
        yield audio_chunk

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_streaming(generator(), webchat_event)

    payloads = []
    while not queue.empty():
        payloads.append(queue.get_nowait())

    assert payloads[0]["type"] == "audio_chunk"
    assert payloads[0]["data"] == "audio-base64"
    assert payloads[0]["text"] == "spoken"
    assert webchat_event.get_extra("_interaction_core_stream_text") == "spoken"
    assert webchat_event.get_extra("_interaction_finalized_turn_material") == {
        "turn_id": "turn-1",
        "user_text": "帮我查一下天气",
        "assistant_text": "spoken",
        "visible_outputs": [
            {
                "turn_id": "turn-1",
                "kind": "core_stream",
                "text": "spoken",
                "memory_relevant": True,
            }
        ],
        "history_source": "interaction.turn.material",
    }
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert turn_state.stream_state.total_text == "spoken"
    assert turn_state.utterances[0].kind == "core_stream"
    assert turn_state.utterances[0].text == "spoken"
    assert turn_state.completion_state.completed is True


@pytest.mark.asyncio
async def test_capture_streaming_does_not_block_core_chunks(webchat_event):
    queue = asyncio.Queue()
    decider = SlowStreamInterjectionDecider()
    plugin_context = MagicMock()
    plugin_context.list_interaction_stream_deciders.return_value = [decider]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_observation_min_chars=5,
            stream_interjection_enabled=True,
        ),
    )

    async def generator():
        yield MessageChain([Plain("hello")])
        yield MessageChain([Plain(" world")])

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        task = asyncio.create_task(
            controller.capture_streaming(generator(), webchat_event)
        )
        await asyncio.wait_for(decider.started.wait(), timeout=1)
        assert queue.get_nowait()["data"] == "hello"
        assert queue.get_nowait()["data"] == " world"
        decider.release.set()
        await task


@pytest.mark.asyncio
async def test_capture_streaming_interjection_is_separate_from_core_stream(
    webchat_event,
):
    queue = asyncio.Queue()
    decider = StreamInterjectionDecider()
    plugin_context = MagicMock()
    plugin_context.list_interaction_stream_deciders.return_value = [decider]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_observation_min_chars=5,
            stream_interjection_enabled=True,
        ),
    )

    async def generator():
        yield MessageChain([Plain("hello")])
        yield MessageChain([Plain(" core")])

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_streaming(generator(), webchat_event)

    payloads = []
    while not queue.empty():
        payloads.append(queue.get_nowait())

    assert [payload["data"] for payload in payloads if payload["streaming"]] == [
        "hello",
        " core",
        "hello core",
    ]
    interjection_payloads = [
        payload
        for payload in payloads
        if payload.get("chain_type") == "interaction_stream_reply"
    ]
    assert len(interjection_payloads) == 1
    assert interjection_payloads[0]["data"] == "嗯，我听着。"
    assert interjection_payloads[0]["streaming"] is False
    assert (
        interjection_payloads[0]["platform_extras"]["interaction_stream_reply"] is True
    )
    assert payloads[-1]["type"] == "complete"
    assert payloads[-1]["data"] == "hello core"
    assert [view["window_index"] for view in decider.views] == [1, 2]
    assert decider.views[0]["pending_text"] == ""
    assert decider.views[0]["utterances"] == ()
    assert webchat_event.get_extra("_visible_turn_outputs") == [
        {
            "turn_id": "turn-1",
            "kind": "stream_interjection",
            "text": "嗯，我听着。",
            "memory_relevant": False,
        },
        {
            "turn_id": "turn-1",
            "kind": "core_stream",
            "text": "hello core",
            "memory_relevant": True,
        },
    ]
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert [utterance.kind for utterance in turn_state.utterances] == [
        "stream_interjection",
        "core_stream",
    ]
    assert turn_state.utterances[0].memory_relevant is False
    assert turn_state.utterances[0].delivered_message_ids == [
        "turn-1::stream_interjection::0002"
    ]
    assert turn_state.utterances[1].text == "hello core"


@pytest.mark.asyncio
async def test_capture_streaming_observes_final_short_output(webchat_event):
    queue = asyncio.Queue()
    decider = FinalStreamInterjectionDecider()
    plugin_context = MagicMock()
    plugin_context.list_interaction_stream_deciders.return_value = [decider]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_observation_min_chars=200,
            stream_interjection_enabled=True,
        ),
    )

    async def generator():
        yield MessageChain([Plain("short result")])

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_streaming(generator(), webchat_event)

    payloads = []
    while not queue.empty():
        payloads.append(queue.get_nowait())

    assert len(decider.views) == 1
    assert decider.views[0]["is_final"] is True
    assert decider.views[0]["observed_text"] == "short result"
    assert decider.views[0]["total_text"] == "short result"
    assert [payload["data"] for payload in payloads] == [
        "short result",
        "收到了。",
        "short result",
    ]
    assert payloads[-1]["type"] == "complete"


@pytest.mark.asyncio
async def test_stream_decider_receives_read_only_stream_view(webchat_event):
    queue = asyncio.Queue()
    decider = MutatingStreamViewDecider()
    plugin_context = MagicMock()
    plugin_context.list_interaction_stream_deciders.return_value = [decider]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_observation_min_chars=5,
            stream_interjection_enabled=True,
        ),
    )

    async def generator():
        yield MessageChain([Plain("hello")])

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_streaming(generator(), webchat_event)

    assert decider.view is not None
    assert decider.view.turn_id == "turn-1"
    assert decider.view.observed_text == "hello"
    assert decider.view.total_text == "hello"
    assert decider.view.window_index == 1
    assert decider.view.metadata["stream_observation_count"] == 1
    assert decider.view.utterances == ()


@pytest.mark.asyncio
async def test_stream_decider_failure_records_turn_failure(webchat_event):
    queue = asyncio.Queue()
    decider = FailingStreamInterjectionDecider()
    plugin_context = MagicMock()
    plugin_context.list_interaction_stream_deciders.return_value = [decider]
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_observation_min_chars=5,
            stream_interjection_enabled=True,
        ),
    )

    async def generator():
        yield MessageChain([Plain("hello")])

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_streaming(generator(), webchat_event)

    payloads = []
    while not queue.empty():
        payloads.append(queue.get_nowait())

    assert [payload["data"] for payload in payloads if payload["streaming"]] == [
        "hello",
        "hello",
    ]
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert any(
        failure.stage == "stream_interjection"
        and failure.reason == "plugin_error"
        and failure.exception_type == "RuntimeError"
        and failure.user_visible_action == "continue_core_stream"
        for failure in turn_state.failures
    )
    failures = webchat_event.get_extra("_interaction_stream_decider_failures")
    assert failures == [
        {
            "plugin_id": "failing_stream_plugin",
            "error": "decider failed",
        }
    ]


@pytest.mark.asyncio
async def test_stream_interjection_provider_missing_records_turn_failure(
    webchat_event,
):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.list_interaction_stream_deciders.return_value = [
        InvalidStreamInterjectionDecider()
    ]
    plugin_context.get_provider_by_id.return_value = None
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_observation_min_chars=5,
            stream_interjection_enabled=True,
        ),
    )

    async def generator():
        yield MessageChain([Plain("hello")])

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_streaming(generator(), webchat_event)

    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    failure_reasons = [
        failure.reason
        for failure in turn_state.failures
        if failure.stage == "stream_interjection"
    ]
    assert failure_reasons == [
        "invalid_plugin_payload",
        "provider_unavailable",
    ]
    assert all(
        failure.user_visible_action == "continue_core_stream"
        for failure in turn_state.failures
        if failure.stage == "stream_interjection"
    )


@pytest.mark.asyncio
async def test_stream_prompt_reuses_turn_context_material(webchat_event):
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_interjection_enabled=True,
        ),
    )
    turn_state = InteractionTurnState(
        turn_id="turn-1",
        context_material=InteractionContextMaterial(
            persona_payload={"persona_id": "alice"},
            memory_payload={"recent_turns": [{"user": "u1", "assistant": "a1"}]},
            recent_messages=[
                {
                    "source": "interaction_memory",
                    "user_message": {"role": "user", "content": "u1"},
                    "assistant_message": {"role": "assistant", "content": "a1"},
                }
            ],
        ),
    )
    webchat_event.set_extra("_interaction_turn_state", turn_state)

    with patch(
        "astrbot.core.interaction.output_controller.build_interaction_context_pack",
        new=AsyncMock(side_effect=AssertionError("should not rebuild context")),
    ):
        prompt = await controller._build_stream_interjection_prompt(
            webchat_event,
            observed_text="hello",
            total_text="hello",
            window_index=1,
            is_final=False,
        )

    assert '"persona_id": "alice"' in prompt
    assert '"recent_turns"' in prompt
    assert '"existing_turn_utterances"' in prompt


@pytest.mark.asyncio
async def test_stream_prompt_uses_stream_state_for_current_buffer(webchat_event):
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_interjection_enabled=True,
        ),
    )
    turn_state = InteractionTurnState(
        turn_id="turn-1",
        stream_state=InteractionStreamState(
            total_text="hello from state",
            pending_text="from state",
        ),
    )
    webchat_event.set_extra("_interaction_turn_state", turn_state)

    prompt = await controller._build_stream_interjection_prompt(
        webchat_event,
        observed_text="hello",
        total_text="stale total",
        window_index=1,
        is_final=False,
    )

    assert '"core_stream_so_far": "hello from state"' in prompt
    assert '"core_stream_pending": "from state"' in prompt


@pytest.mark.asyncio
async def test_stream_prompt_records_missing_context_store(webchat_event):
    plugin_context = MagicMock()
    plugin_context.get_config.return_value = {
        "interaction_middleware": {},
        "provider_settings": {},
        "provider_stt_settings": {},
        "provider_tts_settings": {},
    }
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_interjection_enabled=True,
        ),
    )

    with pytest.raises(RuntimeError, match="interaction_memory_store"):
        await controller._build_stream_interjection_prompt(
            webchat_event,
            observed_text="hello",
            total_text="hello",
            window_index=1,
            is_final=False,
        )

    assert webchat_event.get_extra("_interaction_stream_context_build_failed") is True
    assert "interaction_memory_store" in webchat_event.get_extra(
        "_interaction_stream_context_build_failure_reason",
    )


@pytest.mark.asyncio
async def test_streaming_finish_marker_is_not_sent_after_streaming_delivery(
    webchat_event,
):
    queue = asyncio.Queue()
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_observation_min_chars=20,
            stream_interjection_enabled=False,
        ),
    )

    async def generator():
        yield MessageChain([Plain("stream final")])

    with patch(
        "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
        return_value=queue,
    ):
        await controller.capture_streaming(generator(), webchat_event)
        webchat_event.set_result(
            MessageEventResult(
                chain=[Plain("stream final")],
                result_content_type=ResultContentType.STREAMING_FINISH,
            )
        )
        await controller.capture_message_chain(
            MessageChain([Plain("stream final")]),
            webchat_event,
        )

    payloads = []
    while not queue.empty():
        payloads.append(queue.get_nowait())

    assert [payload["data"] for payload in payloads] == [
        "stream final",
        "stream final",
    ]
    assert payloads[-1]["type"] == "complete"


@pytest.mark.asyncio
async def test_tts_materialization_records_record_delivery_but_memory_uses_text(
    webchat_event,
):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.get_config.return_value = {
        "provider_tts_settings": {
            "enable": True,
            "dual_output": False,
            "use_file_service": False,
            "trigger_probability": 1.0,
        },
        "provider_settings": {},
        "t2i": False,
    }
    tts_provider = MagicMock()
    tts_provider.meta.return_value.id = "tts-provider"
    tts_provider.get_audio = AsyncMock(return_value="voice.wav")
    plugin_context.get_using_tts_provider.return_value = tts_provider
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
        persist_callback=_mark_completed_callback,
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("semantic answer")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with (
        patch(
            "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
            return_value=queue,
        ),
        patch.object(
            Record,
            "convert_to_base64",
            new=AsyncMock(return_value="dm9pY2U="),
        ),
        patch(
            "astrbot.core.interaction.output_controller.SessionServiceManager.should_process_tts_request",
            new=AsyncMock(return_value=True),
        ),
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("semantic answer")]),
            webchat_event,
        )

    payload = queue.get_nowait()
    assert payload["type"] == "record"
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert turn_state.utterances[0].text == "semantic answer"
    assert turn_state.utterances[0].metadata["delivered_as"] == "record"
    assert turn_state.utterances[0].metadata["tts"][0]["tts_audio_path"] == "voice.wav"
    assert (
        turn_state.utterances[0].metadata["tts"][0]["tts_provider_id"]
        == "tts-provider"
    )
    assert webchat_event.get_extra("_interaction_finalized_turn_material")[
        "assistant_text"
    ] == "semantic answer"


@pytest.mark.asyncio
async def test_core_reply_tts_merges_default_and_session_config(webchat_event):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.get_config.return_value = {
        "provider_tts_settings": {
            "enable": True,
            "trigger_probability": 1.0,
        },
    }
    tts_provider = MagicMock()
    tts_provider.meta.return_value.id = "tts-provider"
    tts_provider.get_audio = AsyncMock(return_value="voice.wav")
    plugin_context.get_using_tts_provider.return_value = tts_provider
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
        persist_callback=_mark_completed_callback,
    )
    webchat_event.set_extra(
        "_astrbot_config",
        {
            "provider_tts_settings": {
                "enable": False,
                "dual_output": False,
                "use_file_service": False,
                "trigger_probability": 0.0,
            },
            "provider_settings": {},
            "t2i": False,
        },
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("semantic answer")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with (
        patch(
            "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
            return_value=queue,
        ),
        patch.object(
            Record,
            "convert_to_base64",
            new=AsyncMock(return_value="dm9pY2U="),
        ),
        patch(
            "astrbot.core.interaction.output_controller.SessionServiceManager.should_process_tts_request",
            new=AsyncMock(return_value=True),
        ),
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("semantic answer")]),
            webchat_event,
        )

    payload = queue.get_nowait()
    assert payload["type"] == "record"
    assert tts_provider.get_audio.await_args.args == ("semantic answer",)


@pytest.mark.asyncio
async def test_streaming_core_chunks_are_not_materialized_per_chunk(webchat_event):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.get_config.return_value = {
        "provider_tts_settings": {
            "enable": True,
            "dual_output": False,
            "use_file_service": False,
            "trigger_probability": 1.0,
        },
        "provider_settings": {},
        "t2i": False,
    }
    tts_provider = MagicMock()
    tts_provider.meta.return_value.id = "tts-provider"
    tts_provider.get_audio = AsyncMock(return_value="voice.wav")
    plugin_context.get_using_tts_provider.return_value = tts_provider
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(
            finalizer_mode=FinalizerMode.OFF,
            stream_interjection_enabled=False,
        ),
        persist_callback=_mark_completed_callback,
    )

    async def generator():
        yield MessageChain([Plain("stream answer")])

    with (
        patch(
            "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
            return_value=queue,
        ),
        patch.object(
            Record,
            "convert_to_base64",
            new=AsyncMock(return_value="dm9pY2U="),
        ),
        patch(
            "astrbot.core.interaction.output_controller.SessionServiceManager.should_process_tts_request",
            new=AsyncMock(return_value=True),
        ),
    ):
        await controller.capture_streaming(generator(), webchat_event)

    payloads = []
    while not queue.empty():
        payloads.append(queue.get_nowait())
    assert [payload["type"] for payload in payloads] == ["plain", "complete"]
    tts_provider.get_audio.assert_not_awaited()
    assert webchat_event.get_extra("_interaction_finalized_turn_material")[
        "assistant_text"
    ] == "stream answer"


@pytest.mark.asyncio
async def test_t2i_materialization_records_image_delivery_but_memory_uses_text(
    webchat_event,
):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.get_config.return_value = {
        "provider_tts_settings": {"enable": False},
        "provider_settings": {},
        "t2i": True,
        "t2i_word_threshold": 50,
        "t2i_strategy": "remote",
        "t2i_active_template": "base",
        "t2i_use_file_service": False,
    }
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
        persist_callback=_mark_completed_callback,
    )
    long_text = "这是一段很长的语义回复，" * 8
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain(long_text)],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with (
        patch(
            "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
            return_value=queue,
        ),
        patch.object(
            Image,
            "convert_to_base64",
            new=AsyncMock(return_value="aW1hZ2U="),
        ),
        patch(
            "astrbot.core.interaction.output_controller.html_renderer.render_t2i",
            new=AsyncMock(return_value="https://example.test/render.png"),
        ),
    ):
        await controller.capture_message_chain(
            MessageChain([Plain(long_text)]),
            webchat_event,
        )

    payload = queue.get_nowait()
    assert payload["type"] == "image"
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert turn_state.utterances[0].text == long_text
    assert turn_state.utterances[0].metadata["delivered_as"] == "image"
    assert (
        turn_state.utterances[0].metadata["t2i_image_url"]
        == "https://example.test/render.png"
    )
    assert webchat_event.get_extra("_interaction_finalized_turn_material")[
        "assistant_text"
    ] == long_text


@pytest.mark.asyncio
async def test_tts_materialization_failure_is_not_downgraded_to_text(webchat_event):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.get_config.return_value = {
        "provider_tts_settings": {
            "enable": True,
            "dual_output": False,
            "use_file_service": False,
            "trigger_probability": 1.0,
        },
        "provider_settings": {},
        "t2i": False,
    }
    plugin_context.get_using_tts_provider.return_value = None
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
        persist_callback=_mark_completed_callback,
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("semantic answer")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with (
        patch(
            "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
            return_value=queue,
        ),
        patch(
            "astrbot.core.interaction.output_controller.SessionServiceManager.should_process_tts_request",
            new=AsyncMock(return_value=True),
        ),
        pytest.raises(RuntimeError, match="Voice TTS provider unavailable"),
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("semantic answer")]),
            webchat_event,
        )

    assert queue.empty()
    assert webchat_event.get_extra("_interaction_outbound_materialization_failed") is True
    assert webchat_event.get_extra("_interaction_outbound_materialization_stage") == "tts"
    assert (
        webchat_event.get_extra("_interaction_outbound_materialization_failure_reason")
        == "provider_unavailable"
    )


@pytest.mark.asyncio
async def test_tts_file_registration_failure_is_not_downgraded_to_text(
    webchat_event,
):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.get_config.return_value = {
        "provider_tts_settings": {
            "enable": True,
            "dual_output": False,
            "use_file_service": True,
            "trigger_probability": 1.0,
        },
        "provider_settings": {},
        "t2i": False,
        "callback_api_base": "http://localhost:6185",
    }
    tts_provider = MagicMock()
    tts_provider.meta.return_value.id = "tts-provider"
    tts_provider.get_audio = AsyncMock(return_value="voice.wav")
    plugin_context.get_using_tts_provider.return_value = tts_provider
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
        persist_callback=_mark_completed_callback,
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("semantic answer")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with (
        patch(
            "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
            return_value=queue,
        ),
        patch(
            "astrbot.core.interaction.output_controller.SessionServiceManager.should_process_tts_request",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "astrbot.core.voice.service.file_token_service.register_file",
            new=AsyncMock(side_effect=RuntimeError("registry down")),
        ),
        pytest.raises(RuntimeError, match="registry down"),
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("semantic answer")]),
            webchat_event,
        )

    assert queue.empty()
    assert webchat_event.get_extra("_interaction_outbound_materialization_failed") is True
    assert webchat_event.get_extra("_interaction_outbound_materialization_stage") == "tts"
    assert (
        webchat_event.get_extra("_interaction_outbound_materialization_failure_reason")
        == "file_registration_failed"
    )
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert any(
        failure.stage == "outbound_materialization"
        and failure.reason == "file_registration_failed"
        for failure in turn_state.failures
    )


@pytest.mark.asyncio
async def test_tts_file_service_config_missing_is_not_downgraded_to_text(
    webchat_event,
):
    queue = asyncio.Queue()
    plugin_context = MagicMock()
    plugin_context.get_config.return_value = {
        "provider_tts_settings": {
            "enable": True,
            "dual_output": False,
            "use_file_service": True,
            "trigger_probability": 1.0,
        },
        "provider_settings": {},
        "t2i": False,
        "callback_api_base": "",
    }
    tts_provider = MagicMock()
    tts_provider.meta.return_value.id = "tts-provider"
    tts_provider.get_audio = AsyncMock(return_value="voice.wav")
    plugin_context.get_using_tts_provider.return_value = tts_provider
    controller = InteractionOutputController(
        plugin_context=plugin_context,
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
        persist_callback=_mark_completed_callback,
    )
    webchat_event.set_result(
        MessageEventResult(
            chain=[Plain("semantic answer")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )

    with (
        patch(
            "astrbot.core.platform.sources.webchat.webchat_event.webchat_queue_mgr.get_or_create_back_queue",
            return_value=queue,
        ),
        patch(
            "astrbot.core.interaction.output_controller.SessionServiceManager.should_process_tts_request",
            new=AsyncMock(return_value=True),
        ),
        pytest.raises(RuntimeError, match="callback_api_base"),
    ):
        await controller.capture_message_chain(
            MessageChain([Plain("semantic answer")]),
            webchat_event,
        )

    assert queue.empty()
    assert (
        webchat_event.get_extra("_interaction_outbound_materialization_failure_reason")
        == "file_registration_config_missing"
    )
    turn_state = get_interaction_turn_state(webchat_event)
    assert turn_state is not None
    assert any(
        failure.stage == "outbound_materialization"
        and failure.reason == "file_registration_config_missing"
        for failure in turn_state.failures
    )


@pytest.mark.asyncio
async def test_end_payload_keeps_turn_id(webchat_event):
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )
    webchat_event.complete_visible_turn = AsyncMock()

    await controller.capture_message_chain(None, webchat_event)

    webchat_event.complete_visible_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_none_uses_event_visible_completion_and_propagates_failure(
    webchat_event,
):
    webchat_event.complete_visible_turn = AsyncMock(
        side_effect=RuntimeError("queue closed")
    )
    controller = InteractionOutputController(
        interaction_config=InteractionAgentConfig(finalizer_mode=FinalizerMode.OFF),
    )

    with pytest.raises(RuntimeError, match="queue closed"):
        await controller.capture_message_chain(None, webchat_event)

    webchat_event.complete_visible_turn.assert_awaited_once()
