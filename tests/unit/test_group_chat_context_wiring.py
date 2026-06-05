from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.api.message_components import Plain
from astrbot.api.platform import MessageType
from astrbot.builtin_stars.astrbot.group_chat_context import (
    GROUP_CONTEXT_PROMPT_CONSUMED_EXTRA,
    GROUP_CONTEXT_RAW_IDX_EXTRA,
    GROUP_CONTEXT_RECORD_ID_EXTRA,
    GroupChatContext,
)
from astrbot.builtin_stars.astrbot.main import Main
from astrbot.core.agent.message import TextPart
from astrbot.core.provider.entities import ProviderRequest


def make_main_with_conversation_manager(conv_mgr):
    main = Main.__new__(Main)
    main.context = MagicMock()
    main.context.conversation_manager = conv_mgr
    return main


def make_event(umo: str = "aiocqhttp:GroupMessage:user_123_group_456"):
    extras = {}
    event = MagicMock()
    event.unified_msg_origin = umo
    event.get_platform_id.return_value = "aiocqhttp"
    event.get_message_type.return_value = MessageType.GROUP_MESSAGE
    event.message_obj = SimpleNamespace(
        message=[Plain("hello")],
        sender=SimpleNamespace(nickname="Alice"),
    )
    event.get_messages.return_value = event.message_obj.message
    event.message_str = "hello"
    event.session_id = "session-1"
    event.is_at_or_wake_command = False
    event.get_group_id.return_value = "456"
    event.get_self_id.return_value = "bot"
    event.set_extra.side_effect = lambda key, value: extras.__setitem__(key, value)
    event.get_extra.side_effect = lambda key, default=None: extras.get(key, default)
    event._extras = extras
    return event


def make_config(*, group_icl_enable: bool = True, active_reply: bool = False):
    return {
        "provider_ltm_settings": {
            "group_icl_enable": group_icl_enable,
            "group_message_max_cnt": 300,
            "image_caption": False,
            "active_reply": {
                "enable": active_reply,
                "method": "possibility_reply",
                "possibility_reply": 0,
                "whitelist": [],
            },
        },
        "provider_settings": {"image_caption_prompt": "describe"},
    }


def test_main_registers_group_chat_context_collector():
    context = MagicMock()

    main = Main(context)

    assert isinstance(main.group_chat_context, GroupChatContext)
    context.register_prompt_extension_collector.assert_called_once_with(
        main.group_chat_context
    )


@pytest.mark.asyncio
async def test_group_chat_context_collects_prompt_extension_and_skips_legacy_double_inject():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.set_extra(GROUP_CONTEXT_RECORD_ID_EXTRA, "r2")
    event.set_extra(GROUP_CONTEXT_RAW_IDX_EXTRA, 1)
    group_context.raw_records[event.unified_msg_origin] = deque(
        ["[Bob/10:00:00]: previous", "[Alice/10:01:00]: current"]
    )
    group_context._record_ids[event.unified_msg_origin] = deque(["r1", "r2"])

    extensions = await group_context.collect(
        event,
        context,
        MagicMock(prompt_pipeline_mode="apply_visible"),
        provider_request=ProviderRequest(prompt="hello"),
    )
    req = ProviderRequest(prompt="hello")
    await group_context.on_req_llm(event, req)

    assert len(extensions) == 1
    extension = extensions[0]
    assert extension.mount == "context"
    assert extension.value_kind == "text"
    assert "previous" in extension.value
    assert "current" not in extension.value
    assert event.get_extra(GROUP_CONTEXT_PROMPT_CONSUMED_EXTRA) is True
    assert req.extra_user_content_parts == []
    assert list(group_context.raw_records[event.unified_msg_origin]) == []


@pytest.mark.asyncio
async def test_group_chat_context_collector_treats_empty_prompt_mode_as_apply_visible():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.set_extra(GROUP_CONTEXT_RECORD_ID_EXTRA, "r2")
    event.set_extra(GROUP_CONTEXT_RAW_IDX_EXTRA, 1)
    group_context.raw_records[event.unified_msg_origin] = deque(
        ["[Bob/10:00:00]: previous", "[Alice/10:01:00]: current"]
    )
    group_context._record_ids[event.unified_msg_origin] = deque(["r1", "r2"])

    extensions = await group_context.collect(
        event,
        context,
        MagicMock(prompt_pipeline_mode=""),
        provider_request=ProviderRequest(prompt="hello"),
    )
    req = ProviderRequest(prompt="hello")
    await group_context.on_req_llm(event, req)

    assert len(extensions) == 1
    assert "previous" in extensions[0].value
    assert req.extra_user_content_parts == []


@pytest.mark.asyncio
async def test_group_chat_context_legacy_request_injects_when_prompt_pipeline_did_not_consume():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.set_extra(GROUP_CONTEXT_RECORD_ID_EXTRA, "r2")
    event.set_extra(GROUP_CONTEXT_RAW_IDX_EXTRA, 1)
    group_context.raw_records[event.unified_msg_origin] = deque(
        ["[Bob/10:00:00]: previous", "[Alice/10:01:00]: current"]
    )
    group_context._record_ids[event.unified_msg_origin] = deque(["r1", "r2"])
    req = ProviderRequest(prompt="hello")

    await group_context.on_req_llm(event, req)

    assert len(req.extra_user_content_parts) == 1
    assert isinstance(req.extra_user_content_parts[0], TextPart)
    assert "previous" in req.extra_user_content_parts[0].text
    assert "current" not in req.extra_user_content_parts[0].text
    assert list(group_context.raw_records[event.unified_msg_origin]) == []


@pytest.mark.asyncio
async def test_group_chat_context_collector_does_not_consume_in_non_visible_prompt_mode():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.set_extra(GROUP_CONTEXT_RECORD_ID_EXTRA, "r2")
    event.set_extra(GROUP_CONTEXT_RAW_IDX_EXTRA, 1)
    group_context.raw_records[event.unified_msg_origin] = deque(
        ["[Bob/10:00:00]: previous", "[Alice/10:01:00]: current"]
    )
    group_context._record_ids[event.unified_msg_origin] = deque(["r1", "r2"])
    config = MagicMock(prompt_pipeline_mode="legacy")

    extensions = await group_context.collect(
        event,
        context,
        config,
        provider_request=ProviderRequest(prompt="hello"),
    )
    req = ProviderRequest(prompt="hello")
    await group_context.on_req_llm(event, req)

    assert extensions == []
    assert len(req.extra_user_content_parts) == 1
    assert "previous" in req.extra_user_content_parts[0].text


@pytest.mark.asyncio
async def test_handle_message_ignores_wake_commands():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.is_at_or_wake_command = True

    await group_context.handle_message(event)

    assert event.unified_msg_origin not in group_context.raw_records


@pytest.mark.asyncio
async def test_active_reply_does_not_create_conversation_when_current_missing():
    conv_mgr = SimpleNamespace(
        get_curr_conversation_id=AsyncMock(return_value=None),
        new_conversation=AsyncMock(),
        get_conversation=AsyncMock(),
    )
    main = make_main_with_conversation_manager(conv_mgr)
    main.context.get_config.return_value = make_config(
        group_icl_enable=False,
        active_reply=True,
    )
    main.context.get_using_provider.return_value = object()
    main.group_chat_context = SimpleNamespace(
        need_active_reply=AsyncMock(return_value=True),
        handle_message=AsyncMock(),
    )
    event = make_event()

    results = [item async for item in main.on_message(event)]

    assert results == []
    conv_mgr.get_curr_conversation_id.assert_awaited_once_with(event.unified_msg_origin)
    conv_mgr.new_conversation.assert_not_called()
    conv_mgr.get_conversation.assert_not_called()
    event.request_llm.assert_not_called()


@pytest.mark.asyncio
async def test_active_reply_reuses_current_umo_conversation():
    conv = SimpleNamespace(cid="cid-1")
    conv_mgr = SimpleNamespace(
        get_curr_conversation_id=AsyncMock(return_value="cid-1"),
        new_conversation=AsyncMock(),
        get_conversation=AsyncMock(return_value=conv),
    )
    main = make_main_with_conversation_manager(conv_mgr)
    main.context.get_config.return_value = make_config(
        group_icl_enable=False,
        active_reply=True,
    )
    main.context.get_using_provider.return_value = object()
    main.group_chat_context = SimpleNamespace(
        need_active_reply=AsyncMock(return_value=True),
        handle_message=AsyncMock(),
    )
    event = make_event("aiocqhttp:GroupMessage:user_999_group_456")
    llm_request = object()
    event.request_llm.return_value = llm_request

    results = [item async for item in main.on_message(event)]

    assert results == [llm_request]
    conv_mgr.get_curr_conversation_id.assert_awaited_once_with(event.unified_msg_origin)
    conv_mgr.new_conversation.assert_not_called()
    conv_mgr.get_conversation.assert_awaited_once_with(
        event.unified_msg_origin,
        "cid-1",
    )
    event.request_llm.assert_called_once_with(
        prompt="hello",
        session_id="session-1",
        image_urls=[],
        conversation=conv,
    )


@pytest.mark.asyncio
async def test_on_message_does_not_clear_group_context_on_first_enabled_message():
    main = Main.__new__(Main)
    main.context = MagicMock()
    main.context.get_config.return_value = make_config()
    main.group_chat_context = SimpleNamespace(
        need_active_reply=AsyncMock(return_value=False),
        handle_message=AsyncMock(),
        remove_session=AsyncMock(),
    )
    event = make_event()

    async for _ in main.on_message(event):
        pass

    main.group_chat_context.need_active_reply.assert_awaited_once_with(event)
    main.group_chat_context.handle_message.assert_awaited_once_with(event)
    main.group_chat_context.remove_session.assert_not_called()
