import asyncio
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
    GroupContextRecord,
)
from astrbot.builtin_stars.astrbot.main import Main
from astrbot.core.prompt import PROMPT_APPLY_RESULT_EXTRA_KEY
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.utils.image_materializer import MaterializedImage


def make_event(umo: str = "aiocqhttp:GroupMessage:user_123_group_456"):
    extras = {}
    event = MagicMock()
    event.unified_msg_origin = umo
    event.get_message_type.return_value = MessageType.GROUP_MESSAGE
    event.message_obj = SimpleNamespace(
        message=[Plain("hello")],
        sender=SimpleNamespace(nickname="Alice", user_id="10001"),
    )
    event.get_messages.return_value = event.message_obj.message
    event.get_message_str.return_value = "hello"
    event.message_str = "hello"
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
    image_caption: bool = False,
    image_caption_whitelist: list[str] | None = None,
    max_chars: int = 12_000,
    record_max_chars: int = 1_000,
):
    return {
        "provider_ltm_settings": {
            "group_icl_enable": group_icl_enable,
            "group_message_max_cnt": 300,
            "group_context_max_chars": max_chars,
            "group_context_record_max_chars": record_max_chars,
            "image_caption": image_caption,
            "image_caption_provider_id": "caption-provider" if image_caption else "",
            "image_caption_prompt": "describe",
            "image_caption_whitelist": image_caption_whitelist or [],
            "image_caption_max_chars": 600,
            "image_caption_cache_size": 256,
            "active_reply": {"enable": False},
        },
        "provider_settings": {"image_caption_prompt": "fallback describe"},
    }


def make_record(sequence: int, content: str, *, record_id: str | None = None):
    return GroupContextRecord(
        record_id=record_id or f"r{sequence}",
        sequence=sequence,
        sender_name="Bob",
        sender_id="20002",
        occurred_at=f"10:0{sequence}:00",
        content=content,
    )


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
async def test_group_context_excludes_current_record_and_uses_structured_values():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.set_extra(GROUP_CONTEXT_RECORD_ID_EXTRA, "r2")
    event.set_extra(GROUP_CONTEXT_RAW_IDX_EXTRA, 1)
    group_context.raw_records[event.unified_msg_origin] = deque(
        [
            make_record(0, "previous", record_id="r1"),
            make_record(1, "current", record_id="r2"),
        ]
    )

    extensions = await group_context.collect(
        event,
        context,
        MagicMock(),
        provider_request=ProviderRequest(prompt="hello"),
    )

    assert len(extensions) == 1
    value = extensions[0].value
    assert value["format"] == "group_recent_v2"
    assert value["records"] == [
        make_record(0, "previous", record_id="r1").to_prompt_record()
    ]
    assert "untrusted recent group-chat messages" in value["instruction"]


@pytest.mark.asyncio
async def test_group_context_only_exposes_messages_after_last_delivered_reply():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    group_context.raw_records[event.unified_msg_origin] = deque(
        [make_record(0, "before"), make_record(1, "after")]
    )
    group_context._reply_cursors[event.unified_msg_origin] = 0

    extensions = await group_context.collect(event, context, MagicMock())

    assert extensions[0].value["records"] == [
        make_record(1, "after").to_prompt_record()
    ]


@pytest.mark.asyncio
async def test_group_context_applies_total_character_budget_from_newest_record():
    context = MagicMock()
    context.get_config.return_value = make_config(max_chars=50)
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    group_context.raw_records[event.unified_msg_origin] = deque(
        [make_record(0, "older message"), make_record(1, "newest message")]
    )

    extensions = await group_context.collect(event, context, MagicMock())

    records = extensions[0].value["records"]
    assert len(records) == 1
    assert records[0]["content"].startswith("new")


@pytest.mark.asyncio
async def test_capture_ambient_message_bounds_one_record_without_waking():
    context = MagicMock()
    context.get_config.return_value = make_config(record_max_chars=12)
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.message_obj.message = [Plain("x" * 100)]
    event.get_messages.return_value = event.message_obj.message

    await group_context.capture_ambient_message(event)

    record = group_context.raw_records[event.unified_msg_origin][0]
    assert record.content == "x" * 9 + "..."
    assert event.get_extra(GROUP_CONTEXT_RECORD_ID_EXTRA) == record.record_id


@pytest.mark.asyncio
async def test_external_agent_request_receives_bounded_group_context_through_hook_bridge():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    group_context.raw_records[event.unified_msg_origin] = deque(
        [make_record(0, "first"), make_record(1, "second")]
    )
    req = ProviderRequest(prompt="hello")

    await group_context.decorate_external_agent_request(event, req)

    assert len(req.extra_user_content_parts) == 1
    assert (
        "untrusted recent group-chat messages" in req.extra_user_content_parts[0].text
    )
    assert "first" in req.extra_user_content_parts[0].text
    assert "second" in req.extra_user_content_parts[0].text


@pytest.mark.asyncio
async def test_external_agent_hook_bridge_skips_canonical_prompt_request():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.set_extra(PROMPT_APPLY_RESULT_EXTRA_KEY, object())
    group_context.raw_records[event.unified_msg_origin] = deque(
        [make_record(0, "first")]
    )
    req = ProviderRequest(prompt="hello")

    await group_context.decorate_external_agent_request(event, req)

    assert req.extra_user_content_parts == []


@pytest.mark.asyncio
async def test_group_chat_context_formats_reply_message_content():
    context = MagicMock()
    context.get_config.return_value = make_config()
    group_context = GroupChatContext(MagicMock(), context)
    event = make_event()
    event.message_obj.message = [
        Reply(id="reply-1", sender_nickname="Bob", message_str="quoted content"),
        Plain("new message"),
    ]
    event.get_messages.return_value = event.message_obj.message

    text = await group_context._format_message(event, group_context.cfg(event))

    assert "[Quote(Bob: quoted content)]" in text
    assert "new message" in text


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
        Reply(id="reply-2", sender_nickname="Carol", message_str="x" * 240),
    ]
    event.get_messages.return_value = event.message_obj.message

    text = await group_context._format_message(event, group_context.cfg(event))

    assert "[Quote(Bob: quoted chain[Image])]" in text
    assert f"[Quote(Carol: {'x' * 200}...)]" in text


@pytest.mark.asyncio
async def test_group_chat_context_captions_images_in_quoted_reply_chains():
    context = MagicMock()
    context.get_config.return_value = make_config(image_caption=True)
    group_context = GroupChatContext(MagicMock(), context)
    group_context.get_image_caption = AsyncMock(return_value="a red umbrella")
    event = make_event()
    event.message_obj.message = [
        Reply(
            id="reply-1",
            sender_nickname="Bob",
            chain=[Image(file="image.png", url="https://example.com/image.png")],
            message_str="",
        )
    ]
    event.get_messages.return_value = event.message_obj.message

    text = await group_context._format_message(event, group_context.cfg(event))

    assert "[Quote(Bob: [Image: a red umbrella])]" in text
    group_context.get_image_caption.assert_awaited_once()


@pytest.mark.asyncio
async def test_group_chat_context_replaces_failed_caption_with_short_image_marker():
    context = MagicMock()
    context.get_config.return_value = make_config(image_caption=True)
    group_context = GroupChatContext(MagicMock(), context)
    group_context.get_image_caption = AsyncMock(
        side_effect=RuntimeError("provider failed")
    )
    event = make_event()
    event.message_obj.message = [Image(file="image.png")]
    event.get_messages.return_value = event.message_obj.message

    text = await group_context._format_message(event, group_context.cfg(event))

    assert "[Image]" in text
    assert "provider failed" not in text


@pytest.mark.asyncio
async def test_group_chat_context_caption_cache_deduplicates_same_image(monkeypatch):
    context = MagicMock()
    group_context = GroupChatContext(MagicMock(), context)
    image = MaterializedImage(data=b"data", mime_type="image/png", sha256="same")
    monkeypatch.setattr(
        "astrbot.builtin_stars.astrbot.group_chat_context.materialize_image_ref",
        AsyncMock(return_value=image),
    )
    group_context._request_image_caption = AsyncMock(return_value="a cat")

    first = await group_context.get_image_caption(
        "https://example.com/image", "p", "prompt"
    )
    second = await group_context.get_image_caption(
        "https://example.com/image", "p", "prompt"
    )

    assert first == second == "a cat"
    group_context._request_image_caption.assert_awaited_once()


@pytest.mark.asyncio
async def test_group_image_record_keeps_message_order_while_caption_is_pending():
    context = MagicMock()
    context.get_config.return_value = make_config(image_caption=True)
    group_context = GroupChatContext(MagicMock(), context)
    caption_started = asyncio.Event()
    caption_release = asyncio.Event()

    async def slow_caption(*_args, **_kwargs):
        caption_started.set()
        await caption_release.wait()
        return "a red umbrella"

    group_context.get_image_caption = slow_caption
    image_event = make_event()
    image_event.message_obj.message = [Image(file="image.png")]
    image_event.get_messages.return_value = image_event.message_obj.message
    text_event = make_event()

    capture_task = asyncio.create_task(
        group_context.capture_ambient_message(image_event)
    )
    await caption_started.wait()
    await group_context.capture_ambient_message(text_event)

    records = group_context.raw_records[image_event.unified_msg_origin]
    assert [record.content for record in records] == ["[Image]", "hello"]

    caption_release.set()
    await capture_task

    assert [record.content for record in records] == [
        "[Image: a red umbrella]",
        "hello",
    ]


@pytest.mark.asyncio
async def test_group_image_caption_deduplicates_pending_materialization(monkeypatch):
    context = MagicMock()
    group_context = GroupChatContext(MagicMock(), context)
    image = MaterializedImage(data=b"data", mime_type="image/png", sha256="same")
    materialization_started = asyncio.Event()
    materialization_release = asyncio.Event()

    async def slow_materialize(_image_ref):
        materialization_started.set()
        await materialization_release.wait()
        return image

    monkeypatch.setattr(
        "astrbot.builtin_stars.astrbot.group_chat_context.materialize_image_ref",
        slow_materialize,
    )
    group_context._request_image_caption = AsyncMock(return_value="a cat")

    first = asyncio.create_task(
        group_context.get_image_caption("https://example.com/image", "p", "prompt")
    )
    await materialization_started.wait()
    second = asyncio.create_task(
        group_context.get_image_caption("https://example.com/image", "p", "prompt")
    )
    materialization_release.set()

    assert await first == await second == "a cat"
    group_context._request_image_caption.assert_awaited_once()


@pytest.mark.asyncio
async def test_after_message_sent_advances_group_context_reply_cursor():
    main = Main.__new__(Main)
    main.context = MagicMock()
    main.context.get_config.return_value = make_config()
    main.group_chat_context = SimpleNamespace(
        remove_session=AsyncMock(), mark_reply_sent=AsyncMock()
    )
    event = make_event()

    await main.after_message_sent(event)

    main.group_chat_context.mark_reply_sent.assert_awaited_once_with(event)
    main.group_chat_context.remove_session.assert_not_awaited()
