"""Semantic execution capabilities projected from admitted tools."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from astrbot.core.agent.tool import (
    normalize_action_semantic_capabilities,
    normalize_semantic_capabilities,
)
from astrbot.core.tools.web_search_tools import is_web_search_tool_name

WEB_RESEARCH_CAPABILITY = "web_research"

_EXTRACTION_ONLY_WEB_TOOLS = frozenset(
    {
        "tavily_extract_web_page",
        "firecrawl_extract_web_page",
        "exa_get_contents",
    }
)


@dataclass(frozen=True, slots=True)
class SemanticCapabilityBinding:
    capability_id: str
    tool_name: str
    actions: tuple[str, ...] = ()


def tool_semantic_capability_bindings(
    tool: object,
) -> tuple[SemanticCapabilityBinding, ...]:
    """Return explicit declarations plus the centralized legacy web fallback."""
    tool_name = str(getattr(tool, "name", "") or "").strip()
    capability_actions: dict[str, set[str]] = defaultdict(set)

    for capability_id in normalize_semantic_capabilities(
        getattr(tool, "semantic_capabilities", None)
    ):
        capability_actions[capability_id]
    for action, capabilities in normalize_action_semantic_capabilities(
        getattr(tool, "action_semantic_capabilities", None)
    ).items():
        for capability_id in capabilities:
            capability_actions[capability_id].add(action)

    if (
        WEB_RESEARCH_CAPABILITY not in capability_actions
        and tool_name not in _EXTRACTION_ONLY_WEB_TOOLS
        and (tool_name == "web_search" or is_web_search_tool_name(tool_name))
    ):
        capability_actions[WEB_RESEARCH_CAPABILITY]

    return tuple(
        SemanticCapabilityBinding(
            capability_id=capability_id,
            tool_name=tool_name,
            actions=tuple(sorted(actions)),
        )
        for capability_id, actions in sorted(capability_actions.items())
    )


def collect_semantic_capability_bindings(
    tools: Iterable[object],
) -> dict[str, tuple[SemanticCapabilityBinding, ...]]:
    grouped: dict[str, list[SemanticCapabilityBinding]] = defaultdict(list)
    for tool in tools:
        for binding in tool_semantic_capability_bindings(tool):
            grouped[binding.capability_id].append(binding)
    return {
        capability_id: tuple(
            sorted(
                bindings,
                key=lambda item: (item.tool_name, item.actions),
            )
        )
        for capability_id, bindings in sorted(grouped.items())
    }


def semantic_capability_tool_names(
    tools: Iterable[object],
    capability_id: str,
) -> list[str]:
    bindings = collect_semantic_capability_bindings(tools).get(
        capability_id,
        (),
    )
    return sorted({binding.tool_name for binding in bindings if binding.tool_name})


__all__ = [
    "SemanticCapabilityBinding",
    "WEB_RESEARCH_CAPABILITY",
    "collect_semantic_capability_bindings",
    "semantic_capability_tool_names",
    "tool_semantic_capability_bindings",
]
