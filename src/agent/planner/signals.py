"""RulePoolSelector 的文本信号提取。"""

from __future__ import annotations

import re
from typing import Iterable

from ..types import AgentRequest
from .types import PoolSelectorSignals


_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\btraceback\b", re.IGNORECASE),
    re.compile(r"\bpytest\b", re.IGNORECASE),
    re.compile(r"\bassertionerror\b", re.IGNORECASE),
    re.compile(r"\berror\b", re.IGNORECASE),
    re.compile(r"\bexception\b", re.IGNORECASE),
    re.compile(r"\bstack trace\b", re.IGNORECASE),
    re.compile(r"```[\s\S]*?```", re.IGNORECASE),
    re.compile(r"报错"),
)

_PLAN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"设计"),
    re.compile(r"方案"),
    re.compile(r"架构"),
    re.compile(r"\bdesign\b", re.IGNORECASE),
    re.compile(r"\barchitecture\b", re.IGNORECASE),
    re.compile(r"\bplan\b", re.IGNORECASE),
)

_CREATIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"创意"),
    re.compile(r"脑暴"),
    re.compile(r"小说"),
    re.compile(r"故事"),
    re.compile(r"\bbrainstorm\b", re.IGNORECASE),
    re.compile(r"\bcreative\b", re.IGNORECASE),
)


def extract_signals(req: AgentRequest, *, text_override: str | None = None) -> PoolSelectorSignals:
    """从请求中抽取 RulePoolSelector 需要的简单信号。"""
    text = text_override if text_override is not None else _extract_text(req)
    return PoolSelectorSignals(
        text=text,
        has_code_signal=_matches_any(text, _CODE_PATTERNS),
        has_plan_signal=_matches_any(text, _PLAN_PATTERNS),
        has_creative_signal=_matches_any(text, _CREATIVE_PATTERNS),
    )


def _extract_text(req: AgentRequest) -> str:
    payload = getattr(req.obs, "payload", None)
    text = getattr(payload, "text", None)
    if isinstance(text, str):
        return text.strip()
    return ""


def _matches_any(text: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    if not text:
        return False
    return any(p.search(text) is not None for p in patterns)
