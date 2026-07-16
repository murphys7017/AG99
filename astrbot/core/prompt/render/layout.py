"""Provider-neutral prompt tree layout contract."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextPack, ContextSlot
from .interfaces import BasePromptRenderer
from .prompt_tree import NodeRef


class PromptLayoutInterface(Protocol):
    """Describe semantic tree placement without owning provider serialization."""

    def get_name(self) -> str: ...

    def get_root_tag(self) -> str: ...

    def get_enabled_slot_groups(self) -> tuple[str, ...]: ...

    def get_node_structure(self) -> dict[str, str]: ...

    def include_session_in_system_prompt(self) -> bool: ...

    def render_group(
        self,
        group: str,
        target: NodeRef,
        slots: list[ContextSlot],
        *,
        pack: ContextPack,
        resolve_node: Callable[[str], NodeRef],
        event: AstrMessageEvent | None,
        plugin_context: Context | None,
        config: Any,
        provider_request: ProviderRequest | None,
    ) -> list[str]: ...


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

    def render_group(
        self,
        group: str,
        target: NodeRef,
        slots: list[ContextSlot],
        *,
        pack: ContextPack,
        resolve_node: Callable[[str], NodeRef],
        event: AstrMessageEvent | None,
        plugin_context: Context | None,
        config: Any,
        provider_request: ProviderRequest | None,
    ) -> list[str]:
        render_method = getattr(self._rules, f"render_{group}_context")
        return render_method(
            target,
            slots,
            pack=pack,
            resolve_node=resolve_node,
            event=event,
            plugin_context=plugin_context,
            config=config,
            provider_request=provider_request,
        )


__all__ = ["DefaultPromptLayout", "PromptLayoutInterface"]
