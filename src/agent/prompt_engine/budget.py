"""Budget & Truncation - 预算控制与截断策略。

支持 per_item_max 和 max_chars 预算控制。
"""

from __future__ import annotations

from typing import List

from loguru import logger

from ..context import PromptProfile
from .view import RenderedBlock
from .layout import LayoutResult
from .manifest import RenderManifest


class BudgetController:
    """预算控制器（截断策略）。"""
    
    def apply_budget(
        self,
        profile: PromptProfile,
        layout_result: LayoutResult,
        manifest: RenderManifest,
    ) -> LayoutResult:
        """应用预算控制（截断）。
        
        策略：
        1. 先应用 per_item_max（单项截断）
        2. 再应用 max_chars（全局截断，优先截断 middle 段的低优先级 items）
        
        Args:
            profile: Prompt profile
            layout_result: Layout result
            manifest: 记录截断信息
        
        Returns:
            截断后的 LayoutResult
        """
        # 1. Per-item max 截断
        if profile.budget.per_item_max:
            layout_result = self._apply_per_item_max(
                profile.budget.per_item_max,
                layout_result,
                manifest,
            )
        
        # 2. Max chars 截断（如果配置了）
        if profile.budget.max_chars:
            layout_result = self._apply_max_chars(
                profile.budget.max_chars,
                layout_result,
                manifest,
            )
        
        return layout_result
    
    def _apply_per_item_max(
        self,
        per_item_max: dict[str, int],
        layout_result: LayoutResult,
        manifest: RenderManifest,
    ) -> LayoutResult:
        """应用单项最大长度截断。"""
        for blocks in [layout_result.prefix, layout_result.middle, layout_result.suffix]:
            for block in blocks:
                if block.item_id in per_item_max:
                    max_len = per_item_max[block.item_id]
                    before_len = self._get_value_length(block.value)
                    
                    if before_len > max_len:
                        block.value = self._truncate_value(block.value, max_len)
                        after_len = self._get_value_length(block.value)
                        
                        manifest.add_truncation(
                            item_id=block.item_id,
                            rule="per_item_max",
                            before_len=before_len,
                            after_len=after_len,
                        )
                        
                        logger.debug(
                            f"Truncated item '{block.item_id}' from {before_len} to {after_len} chars"
                        )
        
        return layout_result
    
    def _apply_max_chars(
        self,
        max_chars: int,
        layout_result: LayoutResult,
        manifest: RenderManifest,
    ) -> LayoutResult:
        """应用全局最大字符数截断。
        
        策略：优先截断 middle 段中的低优先级 items。
        """
        # 计算当前总长度
        total_len = self._calculate_total_length(layout_result)
        
        if total_len <= max_chars:
            logger.debug(f"Total length {total_len} <= max_chars {max_chars}, no truncation needed")
            return layout_result
        
        logger.debug(f"Total length {total_len} > max_chars {max_chars}, applying truncation")
        
        # 需要减少的长度
        to_remove = total_len - max_chars
        
        # 从 middle 段开始截断（按 priority 从低到高）
        middle_blocks_sorted = sorted(layout_result.middle, key=lambda b: b.priority)
        
        for block in middle_blocks_sorted:
            if to_remove <= 0:
                break
            
            before_len = self._get_value_length(block.value)
            
            # 尝试截断 50%
            new_len = max(before_len // 2, 0)
            block.value = self._truncate_value(block.value, new_len)
            after_len = self._get_value_length(block.value)
            
            removed = before_len - after_len
            to_remove -= removed
            
            manifest.add_truncation(
                item_id=block.item_id,
                rule="max_chars_budget",
                before_len=before_len,
                after_len=after_len,
            )
            
            logger.debug(
                f"Truncated item '{block.item_id}' from {before_len} to {after_len} chars (budget)"
            )
        
        return layout_result
    
    def _calculate_total_length(self, layout_result: LayoutResult) -> int:
        """计算所有 blocks 的总长度。"""
        total = 0
        for block in layout_result.all_blocks():
            total += self._get_value_length(block.value)
        return total
    
    def _get_value_length(self, value: any) -> int:
        """获取值的长度（字符数）。"""
        if value is None:
            return 0
        if isinstance(value, str):
            return len(value)
        if isinstance(value, (list, dict)):
            return len(str(value))
        return len(str(value))
    
    def _truncate_value(self, value: any, max_len: int) -> any:
        """截断值到指定长度。"""
        if value is None:
            return None
        
        if isinstance(value, str):
            if len(value) <= max_len:
                return value
            return value[:max_len] + "..."
        
        if isinstance(value, list):
            # 截断列表（保留前 N 项）
            str_value = str(value)
            if len(str_value) <= max_len:
                return value
            # 简单策略：减少列表长度
            new_len = max(len(value) // 2, 1)
            return value[:new_len]
        
        # 其他类型转为字符串后截断
        str_value = str(value)
        if len(str_value) <= max_len:
            return value
        return str_value[:max_len] + "..."
