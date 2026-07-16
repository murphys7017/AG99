"""Provider-neutral prompt tree layout contract."""

from __future__ import annotations

from typing import Protocol

from .interfaces import BasePromptRenderer


class PromptLayoutInterface(Protocol):
    """Describe semantic tree placement without owning provider serialization."""

    def get_name(self) -> str: ...

    def get_root_tag(self) -> str: ...

    def get_enabled_slot_groups(self) -> tuple[str, ...]: ...

    def get_node_structure(self) -> dict[str, str]: ...

    def include_session_in_system_prompt(self) -> bool: ...


class DefaultPromptLayout:
    """Provider-neutral layout policy backed by the established slot rules."""

    def __init__(self) -> None:
        self._rules = BasePromptRenderer()

    def get_name(self) -> str:
        return "default"

    def get_root_tag(self) -> str:
        return self._rules.get_root_tag()

    def get_enabled_slot_groups(self) -> tuple[str, ...]:
        return self._rules.get_enabled_slot_groups()

    def get_node_structure(self) -> dict[str, str]:
        return self._rules.get_node_structure()

    def include_session_in_system_prompt(self) -> bool:
        return self._rules.include_session_in_system_prompt()

    def __getattr__(self, name: str):
        if name.startswith("render_") and name.endswith("_context"):
            return getattr(self._rules, name)
        raise AttributeError(name)


__all__ = ["DefaultPromptLayout", "PromptLayoutInterface"]
