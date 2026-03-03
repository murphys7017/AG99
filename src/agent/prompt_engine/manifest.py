"""RenderManifest - Prompt 渲染清单（可观测性核心）。

记录渲染过程中的所有决策、截断、错误，便于调试 lost-in-the-middle 等问题。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class SkippedItem:
    """被跳过的 item 记录。"""
    
    item_id: str
    reason: str  # missing/exposure_blocked/optional_missing/error


@dataclass
class TruncationRecord:
    """截断记录。"""
    
    item_id: str
    rule: str  # per_item_max/max_chars_budget
    before_len: int
    after_len: int


@dataclass
class RenderManifest:
    """Prompt 渲染清单（可观测性核心）。
    
    记录整个渲染过程的决策与状态，便于调试和分析。
    """
    
    # 基础信息
    profile_id: str
    version: str = "0.1"
    
    # Items 使用情况
    used_items: List[str] = field(default_factory=list)
    skipped_items: List[SkippedItem] = field(default_factory=list)
    missing_items: List[str] = field(default_factory=list)
    
    # LLM Exposure 控制
    exposure_blocked: List[str] = field(default_factory=list)  # llm_exposure=never
    redacted_items: List[str] = field(default_factory=list)    # llm_exposure=redacted
    
    # 布局信息
    placements: Dict[str, List[str]] = field(default_factory=dict)  # {prefix: [...], middle: [...], suffix: [...]}
    render_modes: Dict[str, str] = field(default_factory=dict)      # {item_id: render_mode}
    
    # 截断信息
    truncations: List[TruncationRecord] = field(default_factory=list)
    
    # 模板信息
    template_ids: Dict[str, str] = field(default_factory=dict)      # {system: ..., user: ...}
    template_errors: List[str] = field(default_factory=list)
    
    # 输出长度估算
    output_lengths: Dict[str, int] = field(default_factory=dict)    # {system_chars: ..., user_chars: ..., total_chars: ...}
    
    # 额外元信息
    meta: Dict[str, Any] = field(default_factory=dict)
    
    def add_skipped(self, item_id: str, reason: str) -> None:
        """添加跳过记录。"""
        self.skipped_items.append(SkippedItem(item_id=item_id, reason=reason))
    
    def add_truncation(self, item_id: str, rule: str, before_len: int, after_len: int) -> None:
        """添加截断记录。"""
        self.truncations.append(
            TruncationRecord(item_id=item_id, rule=rule, before_len=before_len, after_len=after_len)
        )
    
    def summary(self) -> str:
        """返回简短摘要（用于日志）。"""
        return (
            f"RenderManifest(profile={self.profile_id}, "
            f"used={len(self.used_items)}, "
            f"skipped={len(self.skipped_items)}, "
            f"missing={len(self.missing_items)}, "
            f"blocked={len(self.exposure_blocked)}, "
            f"redacted={len(self.redacted_items)}, "
            f"truncations={len(self.truncations)}, "
            f"output_chars={self.output_lengths.get('total_chars', 0)})"
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """转为字典（用于序列化或详细输出）。"""
        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "used_items": self.used_items,
            "skipped_items": [{"item_id": si.item_id, "reason": si.reason} for si in self.skipped_items],
            "missing_items": self.missing_items,
            "exposure_blocked": self.exposure_blocked,
            "redacted_items": self.redacted_items,
            "placements": self.placements,
            "render_modes": self.render_modes,
            "truncations": [
                {"item_id": tr.item_id, "rule": tr.rule, "before_len": tr.before_len, "after_len": tr.after_len}
                for tr in self.truncations
            ],
            "template_ids": self.template_ids,
            "template_errors": self.template_errors,
            "output_lengths": self.output_lengths,
            "meta": self.meta,
        }
