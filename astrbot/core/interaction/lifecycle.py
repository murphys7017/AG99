from __future__ import annotations

import asyncio
import inspect
from collections.abc import Iterable
from typing import Any

from astrbot import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .contributors import InteractionLifecycleView
from .turn_state import (
    InteractionLifecycleStage,
    ensure_interaction_turn_state,
    transition_interaction_lifecycle,
)

LIFECYCLE_OBSERVER_TIMEOUT_SECONDS = 0.1


async def dispatch_interaction_lifecycle(
    event: AstrMessageEvent,
    plugin_context: Any | None,
    stage: InteractionLifecycleStage,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    previous_stage, transition = transition_interaction_lifecycle(
        event,
        stage,
        metadata=metadata,
    )
    state = ensure_interaction_turn_state(event)
    view = InteractionLifecycleView(
        turn_id=state.turn_id,
        platform_id=event.get_platform_id(),
        session_id=event.session_id,
        stage=stage.value,
        previous_stage=(previous_stage.value if previous_stage is not None else None),
        turn_status=state.completion_state.status.value,
        transition=transition,
        metadata=dict(metadata or {}),
    ).copy_read_only()

    observers = _list_lifecycle_observers(plugin_context)
    if not observers:
        return
    results = await asyncio.gather(
        *(
            _notify_observer(observer, event, plugin_context, view)
            for observer in observers
        ),
        return_exceptions=True,
    )
    failures: list[dict[str, str]] = []
    for observer, result in zip(observers, results, strict=True):
        if not isinstance(result, BaseException):
            continue
        failure = {
            "plugin_id": str(getattr(observer, "plugin_id", "") or ""),
            "stage": stage.value,
            "reason": str(result) or type(result).__name__,
        }
        failures.append(failure)
        logger.warning(
            "Interaction lifecycle observer failed: plugin_id=%s stage=%s error=%s",
            failure["plugin_id"],
            stage.value,
            result,
        )
    if failures:
        existing = event.get_extra("_interaction_lifecycle_observer_failures", [])
        event.set_extra(
            "_interaction_lifecycle_observer_failures",
            [*(existing if isinstance(existing, list) else []), *failures],
        )


def _list_lifecycle_observers(plugin_context: Any | None) -> list[Any]:
    if plugin_context is None:
        return []
    list_observers = getattr(
        plugin_context,
        "list_interaction_lifecycle_observers",
        None,
    )
    if not callable(list_observers):
        return []
    observers = list_observers()
    if not isinstance(observers, Iterable) or isinstance(observers, str | bytes | dict):
        return []
    return list(observers)


async def _notify_observer(
    observer: Any,
    event: AstrMessageEvent,
    plugin_context: Any,
    view: InteractionLifecycleView,
) -> None:
    callback = getattr(observer, "on_interaction_lifecycle", None)
    if not callable(callback):
        raise TypeError("lifecycle observer must define on_interaction_lifecycle")
    result = callback(event, plugin_context, view)
    if inspect.isawaitable(result):
        await asyncio.wait_for(result, timeout=LIFECYCLE_OBSERVER_TIMEOUT_SECONDS)
