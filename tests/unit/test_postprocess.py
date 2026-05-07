from __future__ import annotations

import asyncio
from datetime import timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import astrbot.core.message.components as Comp
from astrbot.core.astr_agent_hooks import MainAgentHooks
from astrbot.core.interaction.turn_state import ensure_interaction_turn_state
from astrbot.core.message.message_event_result import (
    MessageEventResult,
    ResultContentType,
)
from astrbot.core.pipeline.result_decorate.stage import ResultDecorateStage
from astrbot.core.pipeline.respond.stage import RespondStage
from astrbot.core.postprocess import (
    build_postprocess_context,
    get_postprocess_manager,
    unregister_postprocessor,
)
from astrbot.core.postprocess.manager import PostProcessManager
from astrbot.core.postprocess.types import (
    PostProcessContext,
    PostProcessor,
    PostProcessTrigger,
)
from astrbot.core.provider.entities import LLMResponse, ProviderRequest


def _make_event():
    extras: dict[str, object] = {}
    event = MagicMock()
    event.unified_msg_origin = "test:private:user"
    event.get_platform_name.return_value = "test_platform"
    event.get_platform_id.return_value = "test_platform"
    event.is_stopped.return_value = False

    def _get_extra(key, default=None):
        return extras.get(key, default)

    def _set_extra(key, value):
        extras[key] = value

    event.get_extra.side_effect = _get_extra
    event.set_extra.side_effect = _set_extra
    event.clear_result = MagicMock()
    return event, extras


def _make_result(
    chain: list | None = None,
    *,
    result_content_type: ResultContentType = ResultContentType.LLM_RESULT,
    async_stream=None,
) -> MessageEventResult:
    result = MessageEventResult(
        chain=list(chain or []),
        result_content_type=result_content_type,
    )
    result.async_stream = async_stream
    return result


class _Processor(PostProcessor):
    def __init__(
        self,
        name: str,
        triggers: tuple[PostProcessTrigger, ...],
        calls: list[str],
        *,
        should_raise: bool = False,
    ) -> None:
        self.name = name
        self.triggers = triggers
        self._calls = calls
        self._should_raise = should_raise

    async def run(self, ctx: PostProcessContext) -> None:
        self._calls.append(f"{self.name}:{ctx.trigger.value}")
        if self._should_raise:
            raise RuntimeError(f"{self.name} failed")


@pytest.mark.asyncio
async def test_postprocess_manager_dispatches_matching_processors_in_order():
    event, _ = _make_event()
    calls: list[str] = []
    manager = PostProcessManager()
    manager.register(
        _Processor(
            "first",
            (PostProcessTrigger.ON_LLM_RESPONSE,),
            calls,
        )
    )
    manager.register(
        _Processor(
            "second",
            (PostProcessTrigger.ON_LLM_RESPONSE,),
            calls,
        )
    )
    manager.register(
        _Processor(
            "ignored",
            (PostProcessTrigger.AFTER_MESSAGE_SENT,),
            calls,
        )
    )

    ctx = PostProcessContext(
        event=event,
        trigger=PostProcessTrigger.ON_LLM_RESPONSE,
    )
    await manager.dispatch(PostProcessTrigger.ON_LLM_RESPONSE, ctx)

    assert calls == [
        "first:on_llm_response",
        "second:on_llm_response",
    ]


@pytest.mark.asyncio
async def test_postprocess_manager_isolates_processor_failures():
    event, _ = _make_event()
    calls: list[str] = []
    manager = PostProcessManager()
    manager.register(
        _Processor(
            "broken",
            (PostProcessTrigger.ON_LLM_RESPONSE,),
            calls,
            should_raise=True,
        )
    )
    manager.register(
        _Processor(
            "healthy",
            (PostProcessTrigger.ON_LLM_RESPONSE,),
            calls,
        )
    )

    ctx = PostProcessContext(
        event=event,
        trigger=PostProcessTrigger.ON_LLM_RESPONSE,
    )
    await manager.dispatch(PostProcessTrigger.ON_LLM_RESPONSE, ctx)

    assert calls == [
        "broken:on_llm_response",
        "healthy:on_llm_response",
    ]


def test_postprocess_manager_skips_duplicate_registration():
    calls: list[str] = []
    manager = PostProcessManager()
    processor = _Processor(
        "deduped",
        (PostProcessTrigger.ON_LLM_RESPONSE,),
        calls,
    )

    first_registered = manager.register(processor)
    second_registered = manager.register(processor)

    assert first_registered is True
    assert second_registered is False
    assert manager.get_processors(PostProcessTrigger.ON_LLM_RESPONSE) == [processor]


def test_postprocess_manager_unregisters_processor():
    calls: list[str] = []
    manager = PostProcessManager()
    processor = _Processor(
        "remove-me",
        (PostProcessTrigger.ON_LLM_RESPONSE,),
        calls,
    )
    manager.register(processor)

    removed = manager.unregister(processor)

    assert removed is True
    assert manager.get_processors(PostProcessTrigger.ON_LLM_RESPONSE) == []
    assert manager.has_processors() is False


@pytest.mark.asyncio
async def test_postprocess_manager_rejects_mismatched_trigger_context():
    event, _ = _make_event()
    manager = PostProcessManager()
    ctx = PostProcessContext(
        event=event,
        trigger=PostProcessTrigger.ON_LLM_RESPONSE,
    )

    with pytest.raises(ValueError, match="postprocess trigger mismatch"):
        await manager.dispatch(PostProcessTrigger.AFTER_MESSAGE_SENT, ctx)


def test_build_postprocess_context_uses_provider_request_and_conversation():
    event, extras = _make_event()
    req = ProviderRequest(prompt="hello")
    conversation = MagicMock()
    req.conversation = conversation
    extras["provider_request"] = req
    extras["_interaction_finalized_turn_material"] = {
        "turn_id": "turn-1",
        "assistant_text": "done",
    }

    ctx = build_postprocess_context(
        event=event,
        trigger=PostProcessTrigger.ON_LLM_RESPONSE,
        turn_material=extras["_interaction_finalized_turn_material"],
    )

    assert ctx.provider_request is req
    assert ctx.conversation is conversation
    assert ctx.trigger == PostProcessTrigger.ON_LLM_RESPONSE
    assert ctx.turn_material == {
        "turn_id": "turn-1",
        "assistant_text": "done",
    }
    assert ctx.timestamp is not None
    assert ctx.timestamp.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_dispatch_postprocess_resolves_conversation_from_plugin_context():
    event, extras = _make_event()
    req = ProviderRequest(prompt="hello")
    extras["provider_request"] = req

    conversation = MagicMock()
    conversation_manager = MagicMock()
    conversation_manager.get_curr_conversation_id = AsyncMock(return_value="conv-1")
    conversation_manager.get_conversation = AsyncMock(return_value=conversation)
    plugin_context = MagicMock()
    plugin_context.conversation_manager = conversation_manager

    manager = get_postprocess_manager()
    captured_contexts: list[PostProcessContext] = []

    class _CaptureProcessor(PostProcessor):
        name = "capture"
        triggers = (PostProcessTrigger.ON_LLM_RESPONSE,)

        async def run(self, ctx: PostProcessContext) -> None:
            captured_contexts.append(ctx)

    manager.clear()
    manager.register(_CaptureProcessor())

    try:
        from astrbot.core.postprocess import dispatch_postprocess

        await dispatch_postprocess(
            event=event,
            trigger=PostProcessTrigger.ON_LLM_RESPONSE,
            plugin_context=plugin_context,
        )
    finally:
        manager.clear()

    conversation_manager.get_curr_conversation_id.assert_awaited_once_with(
        event.unified_msg_origin
    )
    conversation_manager.get_conversation.assert_awaited_once_with(
        event.unified_msg_origin,
        "conv-1",
    )
    assert len(captured_contexts) == 1
    assert captured_contexts[0].conversation is conversation


@pytest.mark.asyncio
async def test_dispatch_postprocess_skips_context_resolution_without_processors():
    event, extras = _make_event()
    req = ProviderRequest(prompt="hello")
    extras["provider_request"] = req

    conversation_manager = MagicMock()
    conversation_manager.get_curr_conversation_id = AsyncMock(return_value="conv-1")
    conversation_manager.get_conversation = AsyncMock()
    plugin_context = MagicMock()
    plugin_context.conversation_manager = conversation_manager

    manager = get_postprocess_manager()
    manager.clear()

    try:
        from astrbot.core.postprocess import dispatch_postprocess

        await dispatch_postprocess(
            event=event,
            trigger=PostProcessTrigger.ON_LLM_RESPONSE,
            plugin_context=plugin_context,
        )
    finally:
        manager.clear()

    conversation_manager.get_curr_conversation_id.assert_not_awaited()
    conversation_manager.get_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_main_agent_hooks_dispatches_postprocess_after_response_hook():
    event, _ = _make_event()
    run_context = MagicMock()
    run_context.context.event = event
    llm_response = LLMResponse(role="assistant", completion_text="done")

    hooks = MainAgentHooks()

    with (
        patch("astrbot.core.astr_agent_hooks.call_event_hook", new=AsyncMock()) as hook,
        patch(
            "astrbot.core.astr_agent_hooks.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch,
    ):
        await hooks.on_agent_done(run_context, llm_response)

    assert hook.await_count == 2
    dispatch.assert_awaited_once()
    kwargs = dispatch.await_args.kwargs
    assert kwargs["event"] is event
    assert kwargs["trigger"] == PostProcessTrigger.ON_LLM_RESPONSE
    assert kwargs["llm_response"] is llm_response


@pytest.mark.asyncio
async def test_main_agent_hooks_does_not_dispatch_postprocess_if_response_hook_stops():
    event, _ = _make_event()
    event.is_stopped.return_value = True
    run_context = MagicMock()
    run_context.context.event = event
    run_context.context.context = MagicMock()
    llm_response = LLMResponse(role="assistant", completion_text="done")

    hooks = MainAgentHooks()

    with (
        patch("astrbot.core.astr_agent_hooks.call_event_hook", new=AsyncMock()) as hook,
        patch(
            "astrbot.core.astr_agent_hooks.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch,
    ):
        await hooks.on_agent_done(run_context, llm_response)

    assert hook.await_count == 2
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_respond_stage_does_not_dispatch_postprocess_if_no_message_was_sent():
    event, _ = _make_event()
    result = _make_result([])
    event.get_result.return_value = result

    stage = RespondStage()

    with (
        patch(
            "astrbot.core.pipeline.respond.stage.call_event_hook",
            new=AsyncMock(return_value=False),
        ) as hook,
        patch(
            "astrbot.core.pipeline.respond.stage.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch,
    ):
        await stage.process(event)

    hook.assert_not_awaited()
    dispatch.assert_not_awaited()
    event.clear_result.assert_called_once()


@pytest.mark.asyncio
async def test_respond_stage_does_not_dispatch_postprocess_if_after_send_hook_stops():
    event, _ = _make_event()
    result = _make_result([Comp.Plain("hello")])
    event.get_result.return_value = result
    event.send = AsyncMock()

    stage = RespondStage()
    stage.enable_seg = False
    stage.platform_settings = {}
    stage.ctx = MagicMock()
    stage.ctx.plugin_manager.context = MagicMock()

    with (
        patch(
            "astrbot.core.pipeline.respond.stage.call_event_hook",
            new=AsyncMock(return_value=True),
        ) as hook,
        patch(
            "astrbot.core.pipeline.respond.stage.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch,
    ):
        await stage.process(event)

    hook.assert_awaited_once()
    dispatch.assert_not_awaited()
    event.clear_result.assert_not_called()


@pytest.mark.asyncio
async def test_respond_stage_dispatches_postprocess_after_streaming_send():
    event, _ = _make_event()
    result = _make_result([], result_content_type=ResultContentType.STREAMING_RESULT)
    result.async_stream = object()
    event.get_result.return_value = result
    event.send_streaming = AsyncMock()
    event.complete_visible_turn = AsyncMock()

    stage = RespondStage()
    stage.config = {"provider_settings": {}}
    stage.ctx = MagicMock()
    stage.ctx.plugin_manager.context = MagicMock()

    with (
        patch(
            "astrbot.core.pipeline.respond.stage.call_event_hook",
            new=AsyncMock(return_value=False),
        ) as hook,
        patch(
            "astrbot.core.pipeline.respond.stage.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch,
    ):
        await stage.process(event)
        await asyncio.sleep(0)

    event.send_streaming.assert_awaited_once_with(result.async_stream, True)
    hook.assert_awaited_once()
    assert dispatch.await_count == 2
    triggers = [call.kwargs["trigger"] for call in dispatch.await_args_list]
    assert triggers == [
        PostProcessTrigger.AFTER_MESSAGE_SENT,
        PostProcessTrigger.AFTER_TURN_COMPLETED,
    ]
    assert all(call.kwargs["event"] is event for call in dispatch.await_args_list)
    event.clear_result.assert_not_called()


@pytest.mark.asyncio
async def test_respond_stage_skips_turn_completed_postprocess_for_interaction_turn():
    event, extras = _make_event()
    result = _make_result([Comp.Plain("hello")])
    event.get_result.return_value = result
    event.send = AsyncMock()
    event.complete_visible_turn = AsyncMock()
    extras["_interaction_enabled"] = True
    extras["_turn_id"] = "turn-1"
    ensure_interaction_turn_state(event, turn_id="turn-1")

    stage = RespondStage()
    stage.enable_seg = False
    stage.platform_settings = {}
    stage.ctx = MagicMock()
    stage.ctx.plugin_manager.context = MagicMock()

    with (
        patch(
            "astrbot.core.pipeline.respond.stage.call_event_hook",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "astrbot.core.pipeline.respond.stage.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch,
    ):
        await stage.process(event)
        await asyncio.sleep(0)

    assert [call.kwargs["trigger"] for call in dispatch.await_args_list] == [
        PostProcessTrigger.AFTER_MESSAGE_SENT
    ]


@pytest.mark.asyncio
async def test_result_decorate_stage_skips_interaction_turn_reply_prefix():
    event, extras = _make_event()
    result = _make_result([Comp.Plain("hello")])
    event.get_result.return_value = result
    extras["_interaction_enabled"] = True
    extras["_turn_id"] = "turn-1"
    ensure_interaction_turn_state(event, turn_id="turn-1")

    stage = ResultDecorateStage()
    stage.content_safe_check_reply = False
    stage.content_safe_check_stage = None
    stage.reply_prefix = "[bot] "

    with patch(
        "astrbot.core.pipeline.result_decorate.stage.star_handlers_registry.get_handlers_by_event_type",
        return_value=[],
    ):
        async for _ in stage.process(event):
            pass

    assert result.chain[0].text == "hello"


@pytest.mark.asyncio
async def test_respond_stage_schedules_postprocess_without_waiting_after_send():
    event, _ = _make_event()
    result = _make_result([Comp.Plain("hello")])
    event.get_result.return_value = result
    event.send = AsyncMock()
    event.complete_visible_turn = AsyncMock()

    stage = RespondStage()
    stage.enable_seg = False
    stage.platform_settings = {}
    stage.ctx = MagicMock()
    stage.ctx.plugin_manager.context = MagicMock()
    release = asyncio.Event()
    started = asyncio.Event()

    async def _slow_postprocess(**kwargs):
        started.set()
        await release.wait()

    with (
        patch(
            "astrbot.core.pipeline.respond.stage.call_event_hook",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "astrbot.core.pipeline.respond.stage.dispatch_postprocess",
            new=AsyncMock(side_effect=_slow_postprocess),
        ) as dispatch,
    ):
        await stage.process(event)
        await asyncio.wait_for(started.wait(), timeout=1)
        assert dispatch.await_count == 2
        event.complete_visible_turn.assert_awaited_once()
        event.clear_result.assert_called_once()
        release.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_respond_stage_passes_postprocess_provider_request_snapshot():
    event, extras = _make_event()
    result = _make_result([Comp.Plain("hello")])
    event.get_result.return_value = result
    event.send = AsyncMock()
    event.complete_visible_turn = AsyncMock()

    conversation = MagicMock()
    conversation.cid = "conv-1"
    conversation.history = '[{"role":"user","content":"before"}]'
    req = ProviderRequest(prompt="hello", contexts=[{"role": "user", "content": "hi"}])
    req.conversation = conversation
    extras["provider_request"] = req

    stage = RespondStage()
    stage.enable_seg = False
    stage.platform_settings = {}
    stage.ctx = MagicMock()
    stage.ctx.plugin_manager.context = MagicMock()

    captured_kwargs: list[dict] = []

    async def _capture_postprocess(**kwargs):
        captured_kwargs.append(kwargs)

    with (
        patch(
            "astrbot.core.pipeline.respond.stage.call_event_hook",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "astrbot.core.pipeline.respond.stage.dispatch_postprocess",
            new=AsyncMock(side_effect=_capture_postprocess),
        ),
    ):
        await stage.process(event)
        req.prompt = "mutated"
        req.contexts.append({"role": "assistant", "content": "mutated"})
        conversation.history = '[{"role":"user","content":"mutated"}]'
        await asyncio.sleep(0)

    turn_kwargs = next(
        item
        for item in captured_kwargs
        if item["trigger"] == PostProcessTrigger.AFTER_TURN_COMPLETED
    )
    snapshot = turn_kwargs["provider_request"]
    conversation_snapshot = turn_kwargs["conversation"]
    assert snapshot is not req
    assert snapshot.prompt == "hello"
    assert snapshot.contexts == [{"role": "user", "content": "hi"}]
    assert snapshot.conversation is not conversation
    assert snapshot.conversation.history == '[{"role":"user","content":"before"}]'
    assert conversation_snapshot is not conversation
    assert conversation_snapshot.history == '[{"role":"user","content":"before"}]'


@pytest.mark.asyncio
async def test_respond_stage_completes_visible_turn_before_postprocess_after_send():
    event, _ = _make_event()
    result = _make_result([Comp.Plain("hello")])
    event.get_result.return_value = result
    event.send = AsyncMock()
    calls: list[str] = []

    async def _complete_visible_turn():
        calls.append("complete")

    async def _postprocess(**kwargs):
        calls.append("postprocess")

    event.complete_visible_turn = AsyncMock(side_effect=_complete_visible_turn)

    stage = RespondStage()
    stage.enable_seg = False
    stage.platform_settings = {}
    stage.ctx = MagicMock()
    stage.ctx.plugin_manager.context = MagicMock()

    with (
        patch(
            "astrbot.core.pipeline.respond.stage.call_event_hook",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "astrbot.core.pipeline.respond.stage.dispatch_postprocess",
            new=AsyncMock(side_effect=_postprocess),
        ),
    ):
        await stage.process(event)
        await asyncio.sleep(0)

    assert calls == ["complete", "postprocess", "postprocess"]


@pytest.mark.asyncio
async def test_respond_stage_completes_visible_turn_once_after_segmented_sends():
    event, _ = _make_event()
    result = _make_result([Comp.Plain("hello"), Comp.Plain("world")])
    event.get_result.return_value = result
    event.send = AsyncMock()
    event.complete_visible_turn = AsyncMock()

    stage = RespondStage()
    stage.platform_settings = {
        "segmented_reply": {
            "enable": True,
            "only_llm_result": False,
            "interval_method": "random",
            "interval": "0,0",
        }
    }
    stage.ctx = MagicMock()
    stage.ctx.plugin_manager.context = MagicMock()

    with (
        patch(
            "astrbot.core.pipeline.respond.stage.call_event_hook",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "astrbot.core.pipeline.respond.stage.dispatch_postprocess",
            new=AsyncMock(),
        ),
    ):
        await stage.process(event)
        await asyncio.sleep(0)

    assert event.send.await_count == 2
    event.complete_visible_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_respond_stage_does_not_dispatch_postprocess_if_streaming_send_fails():
    event, _ = _make_event()
    result = _make_result([], result_content_type=ResultContentType.STREAMING_RESULT)
    result.async_stream = object()
    event.get_result.return_value = result
    event.send_streaming = AsyncMock(side_effect=RuntimeError("stream failed"))

    stage = RespondStage()
    stage.config = {"provider_settings": {}}
    stage.ctx = MagicMock()
    stage.ctx.plugin_manager.context = MagicMock()

    with patch(
        "astrbot.core.pipeline.respond.stage.dispatch_postprocess",
        new=AsyncMock(),
    ) as dispatch:
        with pytest.raises(RuntimeError, match="stream failed"):
            await stage.process(event)

    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_respond_stage_does_not_dispatch_postprocess_if_all_non_stream_sends_fail():
    event, _ = _make_event()
    result = _make_result([Comp.Plain("hello")])
    event.get_result.return_value = result
    event.send = AsyncMock(side_effect=RuntimeError("send failed"))

    stage = RespondStage()
    stage.enable_seg = False
    stage.platform_settings = {}
    stage.ctx = MagicMock()
    stage.ctx.plugin_manager.context = MagicMock()

    with (
        patch(
            "astrbot.core.pipeline.respond.stage.call_event_hook",
            new=AsyncMock(),
        ) as hook,
        patch(
            "astrbot.core.pipeline.respond.stage.dispatch_postprocess",
            new=AsyncMock(),
        ) as dispatch,
    ):
        await stage.process(event)

    hook.assert_not_awaited()
    dispatch.assert_not_awaited()
    event.clear_result.assert_called_once()


@pytest.mark.asyncio
async def test_get_postprocess_manager_clear_makes_dispatch_a_noop():
    event, _ = _make_event()
    calls: list[str] = []
    manager = get_postprocess_manager()
    manager.clear()
    manager.register(
        _Processor(
            "registered",
            (PostProcessTrigger.ON_LLM_RESPONSE,),
            calls,
        )
    )
    manager.clear()

    try:
        ctx = PostProcessContext(
            event=event,
            trigger=PostProcessTrigger.ON_LLM_RESPONSE,
        )
        await manager.dispatch(PostProcessTrigger.ON_LLM_RESPONSE, ctx)
    finally:
        manager.clear()

    assert calls == []


def test_unregister_postprocessor_helper_returns_false_for_unknown_processor():
    calls: list[str] = []
    processor = _Processor(
        "unknown",
        (PostProcessTrigger.ON_LLM_RESPONSE,),
        calls,
    )

    assert unregister_postprocessor(processor) is False
