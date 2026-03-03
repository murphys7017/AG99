"""LayoutPolicy - 布局策略（prefix/middle/suffix 分组与排序）。

将 RenderedBlocks 按照 placement 和 priority 进行分组与排序。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from loguru import logger

from .view import RenderedBlock
from .manifest import RenderManifest


@dataclass
class LayoutResult:
    """布局结果。"""
    
    prefix: List[RenderedBlock] = field(default_factory=list)
    middle: List[RenderedBlock] = field(default_factory=list)
    suffix: List[RenderedBlock] = field(default_factory=list)
    
    def all_blocks(self) -> List[RenderedBlock]:
        """返回所有 blocks（按 prefix/middle/suffix 顺序）。"""
        return self.prefix + self.middle + self.suffix
    
    def to_dict(self) -> Dict[str, List[str]]:
        """转为字典形式（item_id 列表）。"""
        return {
            "prefix": [b.item_id for b in self.prefix],
            "middle": [b.item_id for b in self.middle],
            "suffix": [b.item_id for b in self.suffix],
        }


class LayoutPolicy:
    """布局策略：将 blocks 按 placement 分组并按 priority 排序。"""
    
    def apply_layout(
        self,
        blocks: List[RenderedBlock],
        manifest: RenderManifest,
    ) -> LayoutResult:
        """应用布局策略。
        
        策略：
        1. 按 placement 分组（prefix/middle/suffix）
        2. 每组内按 priority 由高到低排序（稳定排序）
        
        Args:
            blocks: 输入 blocks
            manifest: 记录布局决策
        
        Returns:
            LayoutResult
        """
        result = LayoutResult()
        
        # 按 placement 分组
        prefix_blocks = [b for b in blocks if b.placement == "prefix"]
        middle_blocks = [b for b in blocks if b.placement == "middle"]
        suffix_blocks = [b for b in blocks if b.placement == "suffix"]
        
        # 每组内按 priority 排序（高到低）
        result.prefix = sorted(prefix_blocks, key=lambda b: b.priority, reverse=True)
        result.middle = sorted(middle_blocks, key=lambda b: b.priority, reverse=True)
        result.suffix = sorted(suffix_blocks, key=lambda b: b.priority, reverse=True)
        
        # 记录到 manifest
        manifest.placements = result.to_dict()
        
        logger.debug(
            f"LayoutPolicy: prefix={len(result.prefix)}, "
            f"middle={len(result.middle)}, suffix={len(result.suffix)}"
        )
        
        return result
