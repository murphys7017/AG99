"""Narrow correction for capability-denial replies that contradict turn facts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.core.execution_capabilities import WEB_RESEARCH_CAPABILITY

from .execution_capability_summary import ExecutionCapabilitySummary
from .types import PersonalResponseAction

if TYPE_CHECKING:
    from .expression_agent import PersonaExpressionResult

_WEB_RESEARCH_REQUEST_MARKERS = (
    "天气",
    "气温",
    "实时",
    "最新",
    "新闻",
    "联网查",
    "网络查",
    "网上查",
    "搜索",
    "搜一下",
    "查一下",
    "weather",
    "latest",
    "current news",
    "search",
    "look up",
)

_CAPABILITY_DENIAL_MARKERS = (
    "没有联网工具",
    "没联网工具",
    "没有网络工具",
    "无法联网",
    "不能联网",
    "查不了",
    "无法查询",
    "没有搜索工具",
    "no web tool",
    "cannot access the internet",
    "can't access the internet",
    "unable to search",
)


def correct_contradictory_capability_denial(
    *,
    request_text: str,
    expression: PersonaExpressionResult,
    capability_summary: ExecutionCapabilitySummary | None,
) -> bool:
    """Turn an explicit false capability denial into a Core delegation."""
    if expression.turn_action is not PersonalResponseAction.REPLY:
        return False
    if capability_summary is None or not capability_summary.is_delegatable(
        WEB_RESEARCH_CAPABILITY
    ):
        return False

    normalized_request = str(request_text or "").strip().lower()
    normalized_reply = str(expression.spoken_reply or "").strip().lower()
    if not any(marker in normalized_request for marker in _WEB_RESEARCH_REQUEST_MARKERS):
        return False
    if not any(marker in normalized_reply for marker in _CAPABILITY_DENIAL_MARKERS):
        return False

    expression.turn_action = PersonalResponseAction.DELEGATE
    expression.spoken_reply = "我去查一下，稍等。"
    expression.speech_cues = []
    expression.metadata = dict(expression.metadata or {})
    expression.metadata["route_correction_reason"] = (
        "capability_denial_route_corrected"
    )
    return True


__all__ = ["correct_contradictory_capability_denial"]
