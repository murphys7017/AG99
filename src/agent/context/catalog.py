"""Context Catalog 加载与校验模块。

提供 Context 信息单元目录的加载、校验、查询功能。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set

import yaml
from loguru import logger


# 枚举类型定义
CategoryType = Literal[
    "input", "conversation", "plan", "identity", "persona", 
    "policy", "memory", "knowledge", "tool", "session"
]
PlacementType = Literal["prefix", "middle", "suffix"]
RenderModeType = Literal["raw", "summary", "pinned", "structured"]
LLMExposureType = Literal["allowed", "redacted", "never"]


@dataclass
class ContextItem:
    """单个上下文信息单元定义。"""
    
    id: str
    category: CategoryType
    source: str
    default_priority: int
    default_placement: PlacementType
    default_render_mode: RenderModeType
    llm_exposure: LLMExposureType
    notes: str = ""


@dataclass
class ContextCatalog:
    """Context Catalog 容器。"""
    
    version: str
    items: List[ContextItem] = field(default_factory=list)
    _index: Dict[str, ContextItem] = field(default_factory=dict, init=False, repr=False)
    
    def __post_init__(self) -> None:
        """构建索引。"""
        self._index = {item.id: item for item in self.items}
    
    def get(self, item_id: str) -> Optional[ContextItem]:
        """根据 ID 获取 item。"""
        return self._index.get(item_id)
    
    def has(self, item_id: str) -> bool:
        """检查 item 是否存在。"""
        return item_id in self._index
    
    def list_by_category(self, category: CategoryType) -> List[ContextItem]:
        """获取指定类别的所有 items。"""
        return [item for item in self.items if item.category == category]


class ContextCatalogLoader:
    """Context Catalog 加载器。"""
    
    # 合法枚举值
    VALID_CATEGORIES: Set[str] = {
        "input", "conversation", "plan", "identity", "persona",
        "policy", "memory", "knowledge", "tool", "session"
    }
    VALID_PLACEMENTS: Set[str] = {"prefix", "middle", "suffix"}
    VALID_RENDER_MODES: Set[str] = {"raw", "summary", "pinned", "structured"}
    VALID_LLM_EXPOSURES: Set[str] = {"allowed", "redacted", "never"}
    
    @classmethod
    def load(cls, path: str | Path) -> ContextCatalog:
        """加载 catalog 配置文件。"""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Context catalog not found: {path}")
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            raise ValueError(f"Failed to load catalog yaml: {e}") from e
        
        if not isinstance(data, dict):
            raise ValueError("Catalog must be a dict")
        
        version = str(data.get("version", "0.1"))
        items_raw = data.get("items", [])
        
        if not isinstance(items_raw, list):
            raise ValueError("Catalog items must be a list")
        
        items: List[ContextItem] = []
        for idx, item_data in enumerate(items_raw):
            if not isinstance(item_data, dict):
                raise ValueError(f"Item {idx} must be a dict")
            
            try:
                item = cls._parse_item(item_data)
                items.append(item)
            except Exception as e:
                raise ValueError(f"Failed to parse item {idx}: {e}") from e
        
        catalog = ContextCatalog(version=version, items=items)
        cls.validate(catalog)
        
        logger.info(f"Loaded context catalog: {len(items)} items, version={version}")
        return catalog
    
    @classmethod
    def _parse_item(cls, data: Dict[str, Any]) -> ContextItem:
        """解析单个 item。"""
        item_id = data.get("id")
        if not item_id or not isinstance(item_id, str):
            raise ValueError("Item id is required and must be a string")
        
        category = data.get("category")
        if category not in cls.VALID_CATEGORIES:
            raise ValueError(f"Invalid category '{category}' for item '{item_id}'")
        
        source = data.get("source")
        if not source or not isinstance(source, str):
            raise ValueError(f"Item '{item_id}' source is required and must be a string")
        
        default_priority = data.get("default_priority")
        if not isinstance(default_priority, int):
            raise ValueError(f"Item '{item_id}' default_priority must be an integer")
        
        default_placement = data.get("default_placement")
        if default_placement not in cls.VALID_PLACEMENTS:
            raise ValueError(f"Invalid default_placement '{default_placement}' for item '{item_id}'")
        
        default_render_mode = data.get("default_render_mode")
        if default_render_mode not in cls.VALID_RENDER_MODES:
            raise ValueError(f"Invalid default_render_mode '{default_render_mode}' for item '{item_id}'")
        
        llm_exposure = data.get("llm_exposure")
        if llm_exposure not in cls.VALID_LLM_EXPOSURES:
            raise ValueError(f"Invalid llm_exposure '{llm_exposure}' for item '{item_id}'")
        
        notes = str(data.get("notes", ""))
        
        return ContextItem(
            id=item_id,
            category=category,
            source=source,
            default_priority=default_priority,
            default_placement=default_placement,
            default_render_mode=default_render_mode,
            llm_exposure=llm_exposure,
            notes=notes,
        )
    
    @classmethod
    def validate(cls, catalog: ContextCatalog) -> None:
        """校验 catalog 完整性。"""
        # 1. 检查 ID 唯一性
        id_set: Set[str] = set()
        duplicates: List[str] = []
        for item in catalog.items:
            if item.id in id_set:
                duplicates.append(item.id)
            id_set.add(item.id)
        
        if duplicates:
            raise ValueError(f"Duplicate item IDs found: {duplicates}")
        
        # 2. 检查 source 格式（简单检查是否为 slot:/derived:/future:）
        for item in catalog.items:
            if not any(item.source.startswith(prefix) for prefix in ["slot:", "derived:", "future:"]):
                logger.warning(
                    f"Item '{item.id}' source '{item.source}' does not match expected format "
                    f"(slot:xxx / derived:xxx / future:xxx)"
                )
        
        # 3. 检查 llm_exposure=never 的 item（工具原始结果等敏感信息）
        never_exposed = [item.id for item in catalog.items if item.llm_exposure == "never"]
        if never_exposed:
            logger.debug(f"Items with llm_exposure=never: {never_exposed}")
        
        logger.debug(f"Context catalog validation passed: {len(catalog.items)} items")


# 默认加载函数
def load_catalog(path: str | Path = "config/context_catalog.yaml") -> ContextCatalog:
    """加载默认 catalog。"""
    return ContextCatalogLoader.load(path)
