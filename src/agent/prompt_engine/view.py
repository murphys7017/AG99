"""ContextViewBuilder - 从 ContextPack 提取并构建 Item 视图。

将 ContextPack.slots → Catalog Items 映射，应用 llm_exposure 控制。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from ..context import ContextCatalog, ContextItem, ContextPresetsCollection, PromptProfile
from ..context.types import ContextPack
from ..types import TaskPlan
from .manifest import RenderManifest


@dataclass
class RenderedBlock:
    """单个渲染后的 item block。"""
    
    item_id: str
    value: Any
    render_mode: str  # raw/summary/pinned/structured
    priority: int
    placement: str    # prefix/middle/suffix
    meta: Dict[str, Any] = field(default_factory=dict)


class ContextViewBuilder:
    """从 ContextPack 构建 item 视图，应用 llm_exposure 控制。"""
    
    def __init__(
        self,
        catalog: ContextCatalog,
        presets: Optional[ContextPresetsCollection] = None,
    ):
        self.catalog = catalog
        self.presets = presets
    
    def build_view(
        self,
        profile: PromptProfile,
        plan: TaskPlan,
        ctx: ContextPack,
        manifest: RenderManifest,
    ) -> List[RenderedBlock]:
        """构建 item 视图列表。
        
        Args:
            profile: Prompt profile
            plan: Task plan
            ctx: Context pack
            manifest: Manifest to record decisions
        
        Returns:
            List of RenderedBlock
        """
        blocks: List[RenderedBlock] = []
        
        # 1. 获取所有需要的 item_ids（展开 presets）
        item_ids = self._get_all_item_ids(profile)
        
        # 2. 遍历每个 item，尝试从 ctx 提取
        for item_id in item_ids:
            item = self.catalog.get(item_id)
            if item is None:
                logger.warning(f"Item '{item_id}' not found in catalog, skipping")
                manifest.add_skipped(item_id, "not_in_catalog")
                continue
            
            # 3. 检查 llm_exposure
            if item.llm_exposure == "never":
                logger.debug(f"Item '{item_id}' has llm_exposure=never, blocking")
                manifest.exposure_blocked.append(item_id)
                manifest.add_skipped(item_id, "exposure_blocked")
                continue
            
            # 4. 尝试提取值
            value = self._extract_value(item, plan, ctx)
            
            if value is None:
                # 检查是否为必需项
                is_required = item_id in profile.include.required_items
                if is_required:
                    logger.warning(f"Required item '{item_id}' missing from context")
                    manifest.missing_items.append(item_id)
                    manifest.add_skipped(item_id, "required_missing")
                else:
                    logger.debug(f"Optional item '{item_id}' missing, skipping")
                    manifest.missing_items.append(item_id)
                    manifest.add_skipped(item_id, "optional_missing")
                continue
            
            # 5. 应用 llm_exposure=redacted
            if item.llm_exposure == "redacted":
                value = "[REDACTED]"
                manifest.redacted_items.append(item_id)
                logger.debug(f"Item '{item_id}' redacted due to llm_exposure=redacted")
            
            # 6. 获取 render_mode（优先使用 profile override）
            render_mode = profile.layout.render_mode_overrides.get(
                item_id, item.default_render_mode
            )
            
            # 7. 获取 placement（优先使用 profile override）
            placement = profile.get_placement(item_id) or item.default_placement
            
            # 8. 创建 block
            block = RenderedBlock(
                item_id=item_id,
                value=value,
                render_mode=render_mode,
                priority=item.default_priority,
                placement=placement,
                meta={"source": item.source, "category": item.category},
            )
            blocks.append(block)
            manifest.used_items.append(item_id)
            manifest.render_modes[item_id] = render_mode
        
        return blocks
    
    def _get_all_item_ids(self, profile: PromptProfile) -> List[str]:
        """获取所有 item IDs（展开 presets）。"""
        item_ids = list(profile.include.required_items) + list(profile.include.optional_items)
        
        # 展开 presets
        if self.presets and profile.include.use_presets:
            preset_items = self.presets.expand(profile.include.use_presets)
            item_ids.extend(preset_items)
        
        # 去重并保持顺序
        seen = set()
        result = []
        for item_id in item_ids:
            if item_id not in seen:
                seen.add(item_id)
                result.append(item_id)
        
        return result
    
    def _extract_value(
        self, item: ContextItem, plan: TaskPlan, ctx: ContextPack
    ) -> Any:
        """从 ContextPack 提取 item 值。
        
        根据 item.source 的前缀决定提取策略：
        - slot:xxx -> 从 ctx.slots[xxx] 提取
        - future:xxx -> 标记为缺失（未来功能）
        - derived:xxx -> 标记为缺失（需要计算）
        """
        source = item.source
        
        # slot:xxx - 从 ContextPack.slots 提取
        if source.startswith("slot:"):
            slot_name = source.split(":", 1)[1]
            return self._extract_from_slot(item.id, slot_name, plan, ctx)
        
        # future:xxx - 标记为 missing（Phase 1.2 不实现）
        elif source.startswith("future:"):
            logger.debug(f"Item '{item.id}' has future source, marking as missing")
            return None
        
        # derived:xxx - 标记为 missing（需要计算逻辑，Phase 1.2 不实现）
        elif source.startswith("derived:"):
            logger.debug(f"Item '{item.id}' has derived source, marking as missing")
            return None
        
        else:
            logger.warning(f"Unknown source type for item '{item.id}': {source}")
            return None
    
    def _extract_from_slot(
        self, item_id: str, slot_name: str, plan: TaskPlan, ctx: ContextPack
    ) -> Any:
        """从 slot 提取值，支持子字段访问。
        
        例如：
        - item_id="current_input.text", slot_name="current_input"
          -> 从 ctx.slots["current_input"].value["text"] 提取
        - item_id="plan.task_type", slot_name="plan_meta"
          -> 从 ctx.slots["plan_meta"].value["task_type"] 提取
        """
        # 获取 slot
        slot = ctx.slots.get(slot_name)
        
        # 特殊处理：recent_obs 可能在 ctx.recent_obs（向后兼容）
        if slot is None and slot_name == "recent_obs" and ctx.recent_obs:
            return ctx.recent_obs
        
        if slot is None:
            logger.debug(f"Slot '{slot_name}' not found for item '{item_id}'")
            return None
        
        if slot.status != "ok":
            logger.debug(f"Slot '{slot_name}' has status '{slot.status}', skipping item '{item_id}'")
            return None
        
        value = slot.value
        
        # 尝试提取子字段（例如 current_input.text -> value["text"]）
        if "." in item_id and item_id.startswith(slot_name.replace("_", ".")):
            parts = item_id.split(".")
            if len(parts) > 1:
                sub_field = parts[-1]  # 取最后一个部分
                if isinstance(value, dict) and sub_field in value:
                    return value[sub_field]
        
        # 特殊处理：plan_meta -> plan.*
        if slot_name == "plan_meta" and "." in item_id:
            parts = item_id.split(".")
            if len(parts) > 1 and parts[0] == "plan":
                sub_field = parts[1]
                # 优先从 value（dict）取
                if isinstance(value, dict) and sub_field in value:
                    return value[sub_field]
                # 然后从 plan 对象的属性取
                if hasattr(plan, sub_field):
                    return getattr(plan, sub_field)
                # 最后从 plan.meta 取
                if hasattr(plan, "meta") and isinstance(plan.meta, dict) and sub_field in plan.meta:
                    return plan.meta[sub_field]
        
        return value
