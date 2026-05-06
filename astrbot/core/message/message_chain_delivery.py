from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Awaitable, Callable
from typing import Any

import astrbot.core.message.components as Comp
from astrbot.core import logger
from astrbot.core.message.components import BaseMessageComponent, ComponentType
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.utils.path_util import path_Mapping

_COMPONENT_VALIDATORS = {
    Comp.Plain: lambda comp: bool(comp.text and comp.text.strip()),
    Comp.Face: lambda comp: comp.id is not None,
    Comp.Record: lambda comp: bool(comp.file),
    Comp.Video: lambda comp: bool(comp.file),
    Comp.At: lambda comp: bool(comp.qq) or bool(comp.name),
    Comp.Image: lambda comp: bool(comp.file),
    Comp.Reply: lambda comp: bool(comp.id) and comp.sender_id is not None,
    Comp.Poke: lambda comp: comp.target_id() is not None,
    Comp.Node: lambda comp: bool(comp.content),
    Comp.Nodes: lambda comp: bool(comp.nodes),
    Comp.File: lambda comp: bool(comp.file_ or comp.url),
    Comp.Json: lambda comp: bool(comp.data),
    Comp.Share: lambda comp: bool(comp.url) or bool(comp.title),
    Comp.Music: lambda comp: (
        (comp.id and comp._type and comp._type != "custom")
        or (comp._type == "custom" and comp.url and comp.audio and comp.title)
    ),
    Comp.Forward: lambda comp: bool(comp.id),
    Comp.Location: lambda comp: bool(comp.lat is not None and comp.lon is not None),
    Comp.Contact: lambda comp: bool(comp._type and comp.id),
    Comp.Shake: lambda _: True,
    Comp.Dice: lambda _: True,
    Comp.RPS: lambda _: True,
    Comp.Unknown: lambda comp: bool(comp.text and comp.text.strip()),
}

_RECORD_COMPONENT_TYPES = {ComponentType.Record}
_HEADER_COMPONENT_TYPES = {ComponentType.Reply, ComponentType.At}
_SEGMENTATION_DISABLED_PLATFORMS = {
    "qq_official",
    "weixin_official_account",
    "dingtalk",
}


async def deliver_message_chain(
    event: AstrMessageEvent,
    message: MessageChain,
    *,
    send_message: Callable[[MessageChain], Awaitable[None]],
    platform_settings: dict[str, Any] | None = None,
    result_is_model_result: bool = False,
    allow_segmented_reply: bool = True,
) -> bool:
    working_chain = list(message.chain)
    _apply_path_mapping(working_chain, platform_settings or {})

    try:
        if await _is_empty_message_chain(working_chain):
            logger.info("Message chain is empty after delivery validation; skipping send.")
            return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("Message chain empty check failed: %s", exc, exc_info=True)

    working_chain = [
        comp
        for comp in working_chain
        if not (
            isinstance(comp, Comp.Plain)
            and (not comp.text or not comp.text.strip())
        )
    ]
    if not working_chain:
        return False

    if _is_segmented_reply_required(
        event,
        platform_settings or {},
        result_is_model_result=result_is_model_result,
        allow_segmented_reply=allow_segmented_reply,
    ):
        return await _deliver_segmented_message_chain(
            event,
            message,
            working_chain,
            send_message,
            platform_settings or {},
        )

    return await _deliver_regular_message_chain(
        message,
        working_chain,
        send_message,
    )


def _apply_path_mapping(
    chain: list[BaseMessageComponent],
    platform_settings: dict[str, Any],
) -> None:
    mappings = platform_settings.get("path_mapping", [])
    if not mappings:
        return
    for idx, component in enumerate(chain):
        if isinstance(component, Comp.File) and component.file:
            component.file = path_Mapping(mappings, component.file)
            chain[idx] = component


async def _deliver_segmented_message_chain(
    event: AstrMessageEvent,
    message: MessageChain,
    working_chain: list[BaseMessageComponent],
    send_message: Callable[[MessageChain], Awaitable[None]],
    platform_settings: dict[str, Any],
) -> bool:
    header_comps = _extract_comp(
        working_chain,
        _HEADER_COMPONENT_TYPES,
        modify_raw_chain=True,
    )
    if not working_chain:
        logger.warning(
            "Actual message chain is empty after extracting header components; skipping send."
        )
        return False

    sent_any = False
    for comp in working_chain:
        await _sleep_before_segment(comp, platform_settings)
        try:
            if comp.type in _RECORD_COMPONENT_TYPES:
                await send_message(message.derive([comp]))
            else:
                await send_message(message.derive([*header_comps, comp]))
                header_comps.clear()
            sent_any = True
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to send segmented message chain: chain=%s error=%s",
                MessageChain([comp]),
                exc,
                exc_info=True,
            )
    return sent_any


async def _deliver_regular_message_chain(
    message: MessageChain,
    working_chain: list[BaseMessageComponent],
    send_message: Callable[[MessageChain], Awaitable[None]],
) -> bool:
    if all(comp.type in _HEADER_COMPONENT_TYPES for comp in working_chain):
        logger.warning(
            "Message chain contains only Reply and At components; skipping send."
        )
        return False

    sent_any = False
    sep_comps = _extract_comp(
        working_chain,
        _RECORD_COMPONENT_TYPES,
        modify_raw_chain=True,
    )
    for comp in sep_comps:
        chain = message.derive([comp])
        try:
            await send_message(chain)
            sent_any = True
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to send standalone message component: chain=%s error=%s",
                chain,
                exc,
                exc_info=True,
            )

    if not working_chain:
        return sent_any

    chain = message.derive(working_chain)
    try:
        await send_message(chain)
        sent_any = True
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to send message chain: chain=%s error=%s",
            chain,
            exc,
            exc_info=True,
        )
    return sent_any


def _is_segmented_reply_required(
    event: AstrMessageEvent,
    platform_settings: dict[str, Any],
    *,
    result_is_model_result: bool,
    allow_segmented_reply: bool,
) -> bool:
    if not allow_segmented_reply:
        return False

    segmented_reply = platform_settings.get("segmented_reply", {})
    if not segmented_reply.get("enable", False):
        return False
    if segmented_reply.get("only_llm_result", True) and not result_is_model_result:
        return False
    if event.get_platform_name() in _SEGMENTATION_DISABLED_PLATFORMS:
        return False
    return True


async def _sleep_before_segment(
    comp: BaseMessageComponent,
    platform_settings: dict[str, Any],
) -> None:
    await_comp_interval = await _calc_comp_interval(comp, platform_settings)
    await asyncio.sleep(await_comp_interval)


async def _calc_comp_interval(
    comp: BaseMessageComponent,
    platform_settings: dict[str, Any],
) -> float:
    segmented_reply = platform_settings.get("segmented_reply", {})
    interval_method = str(segmented_reply.get("interval_method", "random") or "random")
    if interval_method == "log":
        log_base = float(segmented_reply.get("log_base", 2.6) or 2.6)
        if isinstance(comp, Comp.Plain):
            word_count = await _word_count(comp.text)
            interval = math.log(word_count + 1, log_base)
            return random.uniform(interval, interval + 0.5)
        return random.uniform(1, 1.75)

    interval_text = str(segmented_reply.get("interval", "1.5,3.5") or "1.5,3.5")
    interval_range = [1.5, 3.5]
    try:
        parsed = [float(item) for item in interval_text.replace(" ", "").split(",")]
        if len(parsed) == 2:
            interval_range = parsed
    except (TypeError, ValueError):
        logger.warning(
            "Failed to parse segmented reply interval=%s; using default range.",
            interval_text,
        )
    return random.uniform(interval_range[0], interval_range[1])


async def _word_count(text: str) -> int:
    if all(ord(char) < 128 for char in text):
        return len(text.split())
    return len([char for char in text if char.isalnum()])


async def _is_empty_message_chain(chain: list[BaseMessageComponent]) -> bool:
    if not chain:
        return True
    for comp in chain:
        validator = _COMPONENT_VALIDATORS.get(type(comp))
        if validator and validator(comp):
            return False
    return True


def _extract_comp(
    raw_chain: list[BaseMessageComponent],
    extract_types: set[ComponentType],
    *,
    modify_raw_chain: bool = True,
) -> list[BaseMessageComponent]:
    extracted: list[BaseMessageComponent] = []
    if modify_raw_chain:
        remaining = []
        for comp in raw_chain:
            if comp.type in extract_types:
                extracted.append(comp)
            else:
                remaining.append(comp)
        raw_chain[:] = remaining
        return extracted
    return [comp for comp in raw_chain if comp.type in extract_types]
