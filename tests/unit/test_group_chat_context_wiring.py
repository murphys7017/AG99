from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.api.message_components import Image, Plain, Reply
from astrbot.api.platform import MessageType
from astrbot.builtin_stars.astrbot.group_chat_context import (
    GROUP_CONTEXT_RAW_IDX_EXTRA,
    GROUP_CONTEXT_RECORD_ID_EXTRA,
    GroupChatContext,
)
from astrbot.builtin_stars.astrbot.main import Main
from astrbot.core.prompt import PROMPT_APPLY_RESULT_EXTRA_KEY
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
        sender=SimpleNamespace(nickname="Alice", user_id="10001"),
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


def make_config(
    *,
    group_icl_enable: bool = True,
    active_reply: bool = False,
    image_caption: bool = False,
    image_caption_whitelist: list[str] | None = None,
):
    return {
        "provider_ltm_settings": {
            "group_icl_enable": group_icl_enable,
            "group_message_max_cnt": 300,
            "image_caption": image_caption,
            "image_caption_provider_id": "caption-provider" if image_caption else "",
            "image_caption_whitelist": image_caption_whitelist or [],
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


def test_group_chat_context_collector_is_dynamic():
    group_context = GroupChatContext(MagicMock(), MagicMock())

    assert group_context.lifecycle == "dynamic"


@pytest.mark.asyncio
async def test_group_chat_context_collects_structured_prompt_extension():
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
        MagicMock(),
        provider_request=ProviderRequest(prompt="hello"),
    )

    assert len(extensions) == 1
    extension = extensions[0]
    assert extension.mount == "conversation"
    assert extension.value_kind == "mapping"
    assert extension.value["records"] == ["[Bob/10:00:00]: previous"]
    assert "[Alice/10:01:00]: current" not in extension.value["text"]
    assert list(group_context.raw_records[event.unified_msg_origin]) == [
        "[Bob/10:00:00]: previous",
        "[Alice/10:01:00]: current",
    ]


@pytest.mark.asyncio
async def test_group_chat_context_collector_has_no_pipeline_mode_switch():
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
        MagicMock(),
        provider_request=ProviderRequest(prompt="hello"),
    )

    assert len(extensions) == 1
    assert "previous" in extensions[0].value["text"]


@pytest.mark.asyncio
async def test_group_chat_context_directed_message_sees_all_prior_ambient_records():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.is_at_or_wake_command = True
    group_context.raw_records[event.unified_msg_origin] = deque(
        ["[Bob/10:00:00]: first", "[Carol/10:01:00]: second"]
    )
    group_context._record_ids[event.unified_msg_origin] = deque(["r1", "r2"])

    extensions = await group_context.collect(
        event,
        context,
        MagicMock(),
        provider_request=ProviderRequest(prompt="@bot answer me"),
    )

    assert extensions[0].value["records"] == [
        "[Bob/10:00:00]: first",
        "[Carol/10:01:00]: second",
    ]


@pytest.mark.asyncio
async def test_external_agent_request_receives_group_context_through_hook_bridge():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.is_at_or_wake_command = True
    group_context.raw_records[event.unified_msg_origin] = deque(
        ["[Bob/10:00:00]: first", "[Carol/10:01:00]: second"]
    )
    group_context._record_ids[event.unified_msg_origin] = deque(["r1", "r2"])
    req = ProviderRequest(prompt="@bot answer me")

    await group_context.decorate_external_agent_request(event, req)

    assert len(req.extra_user_content_parts) == 1
    assert "[Bob/10:00:00]: first" in req.extra_user_content_parts[0].text
    assert "[Carol/10:01:00]: second" in req.extra_user_content_parts[0].text


@pytest.mark.asyncio
async def test_external_agent_hook_bridge_skips_canonical_prompt_request():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.set_extra(PROMPT_APPLY_RESULT_EXTRA_KEY, object())
    group_context.raw_records[event.unified_msg_origin] = deque(
        ["[Bob/10:00:00]: first"]
    )
    group_context._record_ids[event.unified_msg_origin] = deque(["r1"])
    req = ProviderRequest(prompt="hello")

    await group_context.decorate_external_agent_request(event, req)

    assert req.extra_user_content_parts == []


@pytest.mark.asyncio
async def test_main_on_llm_request_delegates_external_group_context_bridge():
    main = Main.__new__(Main)
    main.group_chat_context = SimpleNamespace(
        decorate_external_agent_request=AsyncMock()
    )
    event = make_event()
    req = ProviderRequest(prompt="hello")

    await main.preserve_group_context_for_external_agent(event, req)

    main.group_chat_context.decorate_external_agent_request.assert_awaited_once_with(
        event,
        req,
    )


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
async def test_group_chat_context_formats_reply_message_content():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.message_obj.message = [
        Reply(
            id="reply-1",
            sender_nickname="Bob",
            message_str="quoted content",
        ),
        Plain("new message"),
    ]
    event.get_messages.return_value = event.message_obj.message

    text = await group_context._format_message(event, group_context.cfg(event))

    assert "[Quote(Bob: quoted content)]" in text
    assert "new message" in text


@pytest.mark.asyncio
async def test_group_chat_context_includes_stable_sender_ids():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.message_obj.message = [
        Reply(
            id="reply-1",
            sender_id="20002",
            sender_nickname="Alice",
            message_str="quoted content",
        ),
        Plain("new message"),
    ]
    event.get_messages.return_value = event.message_obj.message

    text = await group_context._format_message(event, group_context.cfg(event))

    assert "[Alice (user_id=10001)/" in text
    assert "[Quote(Alice (user_id=20002): quoted content)]" in text


@pytest.mark.asyncio
async def test_group_chat_context_describes_reply_chain_and_truncates_text():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.message_obj.message = [
        Reply(
            id="reply-1",
            sender_nickname="Bob",
            chain=[Plain("quoted chain"), Image(file="image.png")],
            message_str="",
        ),
        Reply(
            id="reply-2",
            sender_nickname="Carol",
            message_str="x" * 240,
        ),
    ]
    event.get_messages.return_value = event.message_obj.message

    text = await group_context._format_message(event, group_context.cfg(event))

    assert "[Quote(Bob: quoted chain[Image])]" in text
    assert f"[Quote(Carol: {'x' * 200}...)]" in text
    assert "x" * 240 not in text


@pytest.mark.asyncio
async def test_group_chat_context_captions_image_when_caption_whitelist_empty():
    context = MagicMock()
    context.get_config.return_value = make_config(image_caption=True)
    group_context = GroupChatContext(MagicMock(), context)
    group_context.get_image_caption = AsyncMock(return_value="a cat")
    event = make_event()
    event.message_obj.message = [Image(file="image.png")]
    event.get_messages.return_value = event.message_obj.message

    text = await group_context._format_message(event, group_context.cfg(event))

    assert "[Image: a cat]" in text
    group_context.get_image_caption.assert_awaited_once_with(
        "image.png",
        "caption-provider",
        "describe",
    )


@pytest.mark.asyncio
async def test_group_chat_context_captions_image_when_group_matches_caption_whitelist():
    context = MagicMock()
    context.get_config.return_value = make_config(
        image_caption=True,
        image_caption_whitelist=["456"],
    )
    group_context = GroupChatContext(MagicMock(), context)
    group_context.get_image_caption = AsyncMock(return_value="a cat")
    event = make_event()
    event.message_obj.message = [Image(file="image.png")]
    event.get_messages.return_value = event.message_obj.message

    text = await group_context._format_message(event, group_context.cfg(event))

    assert "[Image: a cat]" in text
    group_context.get_image_caption.assert_awaited_once()


@pytest.mark.asyncio
async def test_group_chat_context_skips_image_caption_when_caption_whitelist_misses():
    context = MagicMock()
    context.get_config.return_value = make_config(
        image_caption=True,
        image_caption_whitelist=["999"],
    )
    group_context = GroupChatContext(MagicMock(), context)
    group_context.get_image_caption = AsyncMock(return_value="a cat")
    event = make_event()
    event.message_obj.message = [Image(file="image.png")]
    event.get_messages.return_value = event.message_obj.message

    text = await group_context._format_message(event, group_context.cfg(event))

    assert "[Image]" in text
    assert "[Image:" not in text
    group_context.get_image_caption.assert_not_awaited()


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
