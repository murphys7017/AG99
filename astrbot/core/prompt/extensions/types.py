"""Prompt extension types for plugin-contributed prompt context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

PromptExtensionMount = Literal[
    "system",
    "context",
    "input",
    "conversation",
    "memory",
    "capability",
]

PromptExtensionValueKind = Literal["text", "mapping", "sequence"]

PROMPT_EXTENSION_MOUNTS: tuple[PromptExtensionMount, ...] = (
    "system",
    "context",
    "input",
    "conversation",
    "memory",
    "capability",
)

PROMPT_EXTENSION_VALUE_KINDS: tuple[PromptExtensionValueKind, ...] = (
    "text",
    "mapping",
    "sequence",
)


@dataclass(slots=True)
class PromptExtension:
    """Structured prompt contribution produced by a plugin collector."""

    plugin_id: str
    mount: PromptExtensionMount
    title: str | None = None
    value: Any = None
    value_kind: PromptExtensionValueKind = "mapping"
    order: int = 100
    meta: dict[str, Any] = field(default_factory=dict)
