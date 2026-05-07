from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any


@dataclass(slots=True)
class InteractionPromptContribution:
    plugin_id: str
    title: str | None = None
    content: dict[str, Any] = field(default_factory=dict)
    priority: int = 100


@dataclass(slots=True)
class InteractionResultContribution:
    plugin_id: str
    platform_extras: dict[str, Any] = field(default_factory=dict)
    client_objects: list[dict[str, Any]] = field(default_factory=list)
    final_text_override: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    priority: int = 100


@dataclass(slots=True)
class InteractionStreamView:
    turn_id: str
    platform_id: str
    session_id: str
    observed_text: str
    total_text: str
    pending_text: str
    window_index: int
    is_final: bool
    utterances: tuple[Any, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_read_only_mapping(self) -> MappingProxyType:
        return MappingProxyType(
            {
                "turn_id": self.turn_id,
                "platform_id": self.platform_id,
                "session_id": self.session_id,
                "observed_text": self.observed_text,
                "total_text": self.total_text,
                "pending_text": self.pending_text,
                "window_index": self.window_index,
                "is_final": self.is_final,
                "utterances": self.utterances,
                "metadata": MappingProxyType(dict(self.metadata)),
            }
        )

    def copy_read_only(self) -> InteractionStreamView:
        return replace(
            self,
            utterances=tuple(self.utterances),
            metadata=MappingProxyType(dict(self.metadata)),
        )

    def __getitem__(self, key: str) -> Any:
        return self.as_read_only_mapping()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_read_only_mapping())

    def __len__(self) -> int:
        return len(self.as_read_only_mapping())

    def keys(self):
        return self.as_read_only_mapping().keys()

    def items(self):
        return self.as_read_only_mapping().items()

    def values(self):
        return self.as_read_only_mapping().values()

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_read_only_mapping().get(key, default)


@dataclass(slots=True)
class InteractionResultView:
    turn_id: str
    platform_id: str
    decision: Any
    immediate_reply: str | None = None
    core_result: str | None = None
    final_result: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_read_only_mapping(self) -> MappingProxyType:
        return MappingProxyType(
            {
                "turn_id": self.turn_id,
                "platform_id": self.platform_id,
                "decision": self.decision,
                "immediate_reply": self.immediate_reply,
                "core_result": self.core_result,
                "final_result": self.final_result,
                "metadata": dict(self.metadata),
            }
        )

    def copy_read_only(self) -> InteractionResultView:
        return replace(self, metadata=MappingProxyType(dict(self.metadata)))


def coerce_priority(value: Any, default: int = 100) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def merge_result_contributions(
    contributions: list[InteractionResultContribution],
) -> InteractionResultContribution:
    merged = InteractionResultContribution(plugin_id="__merged__")
    for contribution in sorted(
        contributions,
        key=lambda item: (item.priority, item.plugin_id),
    ):
        merged.platform_extras.update(contribution.platform_extras)
        merged.client_objects.extend(contribution.client_objects)
        merged.metadata.update(contribution.metadata)
        if contribution.final_text_override is not None:
            merged.final_text_override = contribution.final_text_override
    return merged
