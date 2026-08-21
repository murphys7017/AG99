from importlib import import_module
from typing import Any

import pytest
from aiocqhttp import Event


def _adapter_without_starting_platform() -> Any:
    # Match the application import order. Importing the adapter first exposes
    # an existing StarTools <-> platform-adapter circular import in isolation.
    import_module("astrbot.core.star.star_tools")
    adapter_module = import_module(
        "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter",
    )

    return object.__new__(adapter_module.AiocqhttpAdapter)


@pytest.mark.asyncio
async def test_input_status_notice_is_not_converted_to_message():
    adapter = _adapter_without_starting_platform()
    event = Event(
        {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "input_status",
            "self_id": 2762018040,
            "user_id": 815049548,
            "group_id": 792615362,
        },
    )

    assert await adapter.convert_message(event) is None


@pytest.mark.asyncio
async def test_other_notice_events_are_still_converted():
    adapter = _adapter_without_starting_platform()
    poke_event = Event(
        {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "self_id": 2762018040,
            "user_id": 815049548,
            "group_id": 792615362,
            "target_id": 2762018040,
        },
    )
    group_card_event = Event(
        {
            "post_type": "notice",
            "notice_type": "group_card",
            "sub_type": "set",
            "self_id": 2762018040,
            "user_id": 815049548,
            "group_id": 792615362,
        },
    )

    poke = await adapter.convert_message(poke_event)
    group_card = await adapter.convert_message(group_card_event)

    assert poke is not None
    assert len(poke.message) == 1
    assert group_card is not None
    assert group_card.message == []
