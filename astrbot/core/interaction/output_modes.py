"""
Output mode definitions for the interaction middleware plugin output path.

This module defines the minimal identity model for outbound interaction output:

    output_origin:  core | plugin   (who produced the output)
    core_output_delivery: progress | final  (whether Core output may finalize a turn)
    plugin_output_mode: direct | persona  (whether to persona-rewrite first)
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any

from astrbot.core.message.message_event_result import MessageChain


class PluginOutputMode(str, Enum):
    """Plugin output mode enumeration.

    DIRECT:  deliver the message as-is without persona rewriting.
    PERSONA: run the message through a persona expression path first.
    """

    DIRECT = "direct"
    PERSONA = "persona"


class OutputOrigin(str, Enum):
    """Origin of an outbound message.

    CORE:  output produced by the core agent / LLM / tool execution.
    PLUGIN: output produced by a plugin calling event.send().
    """

    CORE = "core"
    PLUGIN = "plugin"


class CoreOutputDelivery(str, Enum):
    """Lifecycle role of a Core-origin visible message."""

    PROGRESS = "progress"
    FINAL = "final"


@dataclass(slots=True)
class PluginOutputRequest:
    """Encapsulates a plugin output request for the Output Runtime."""

    message: MessageChain
    mode: PluginOutputMode = PluginOutputMode.DIRECT
    source: str = "plugin"


# Extra keys used on AstrMessageEvent for output-origin tracking.
OUTPUT_ORIGIN_EXTRA_KEY = "_interaction_output_origin"
CORE_OUTPUT_DELIVERY_EXTRA_KEY = "_interaction_core_output_delivery"
PLUGIN_OUTPUT_MODE_EXTRA_KEY = "_interaction_plugin_output_mode"
# Diagnostic extras (read-only, for testing / debugging).
PLUGIN_OUTPUT_LAST_MODE_EXTRA_KEY = "_interaction_plugin_output_last_mode"
PLUGIN_OUTPUT_LAST_KIND_EXTRA_KEY = "_interaction_plugin_output_last_kind"


@contextmanager
def temporary_output_origin(event: Any, origin: str) -> Iterator[None]:
    """Temporarily set the outbound origin marker on an event-like object."""
    previous = event.get_extra(OUTPUT_ORIGIN_EXTRA_KEY)
    event.set_extra(OUTPUT_ORIGIN_EXTRA_KEY, origin)
    try:
        yield
    finally:
        event.set_extra(OUTPUT_ORIGIN_EXTRA_KEY, previous)


@contextmanager
def temporary_core_output_delivery(
    event: Any,
    delivery: str,
) -> Iterator[None]:
    """Temporarily declare whether Core output is progress or a final result."""
    previous = event.get_extra(CORE_OUTPUT_DELIVERY_EXTRA_KEY)
    event.set_extra(CORE_OUTPUT_DELIVERY_EXTRA_KEY, delivery)
    try:
        yield
    finally:
        event.set_extra(CORE_OUTPUT_DELIVERY_EXTRA_KEY, previous)
