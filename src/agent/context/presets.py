"""Context Presets 加载与校验模块。

提供预设组合的加载、展开功能。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Set

import yaml
from loguru import logger

from .catalog import ContextCatalog


@dataclass
class ContextPreset:
    """单个预设定义。"""
    
    id: str
    items: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class ContextPresetsCollection:
    """Presets 集合。"""
    
    version: str
    presets: List[ContextPreset] = field(default_factory=list)
    _index: Dict[str, ContextPreset] = field(default_factory=dict, init=False, repr=False)
    
    def __post_init__(self) -> None:
        """构建索引。"""
        self._index = {preset.id: preset for preset in self.presets}
    
    def get(self, preset_id: str) -> ContextPreset | None:
        """根据 ID 获取 preset。"""
        return self._index.get(preset_id)
    
    def has(self, preset_id: str) -> bool:
        """检查 preset 是否存在。"""
        return preset_id in self._index
    
    def expand(self, preset_ids: List[str]) -> List[str]:
        """展开多个 preset 为 item 列表（去重）。"""
        items: List[str] = []
        seen: Set[str] = set()
        
        for preset_id in preset_ids:
            preset = self.get(preset_id)
            if preset is None:
                logger.warning(f"Preset '{preset_id}' not found, skipping")
                continue
            
            for item in preset.items:
                if item not in seen:
                    seen.add(item)
                    items.append(item)
        
        return items


class ContextPresetsLoader:
    """Presets 加载器。"""
    
    @classmethod
    def load(cls, path: str | Path) -> ContextPresetsCollection:
        """加载 presets 配置文件。"""
        path = Path(path)
        if not path.exists():
            logger.warning(f"Context presets not found: {path}, returning empty collection")
            return ContextPresetsCollection(version="0.1", presets=[])
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            raise ValueError(f"Failed to load presets yaml: {e}") from e
        
        if not isinstance(data, dict):
            raise ValueError("Presets must be a dict")
        
        version = str(data.get("version", "0.1"))
        presets_raw = data.get("presets", [])
        
        if not isinstance(presets_raw, list):
            raise ValueError("Presets must be a list")
        
        presets: List[ContextPreset] = []
        for idx, preset_data in enumerate(presets_raw):
            if not isinstance(preset_data, dict):
                raise ValueError(f"Preset {idx} must be a dict")
            
            try:
                preset = cls._parse_preset(preset_data)
                presets.append(preset)
            except Exception as e:
                raise ValueError(f"Failed to parse preset {idx}: {e}") from e
        
        collection = ContextPresetsCollection(version=version, presets=presets)
        cls.validate(collection)
        
        logger.info(f"Loaded context presets: {len(presets)} presets, version={version}")
        return collection
    
    @classmethod
    def _parse_preset(cls, data: Dict[str, Any]) -> ContextPreset:
        """解析单个 preset。"""
        preset_id = data.get("id")
        if not preset_id or not isinstance(preset_id, str):
            raise ValueError("Preset id is required and must be a string")
        
        items = data.get("items", [])
        if not isinstance(items, list):
            raise ValueError(f"Preset '{preset_id}' items must be a list")
        
        notes = str(data.get("notes", ""))
        
        return ContextPreset(
            id=preset_id,
            items=[str(x) for x in items],
            notes=notes,
        )
    
    @classmethod
    def validate(cls, collection: ContextPresetsCollection) -> None:
        """校验 presets 集合。"""
        # 检查 ID 唯一性
        id_set: Set[str] = set()
        duplicates: List[str] = []
        for preset in collection.presets:
            if preset.id in id_set:
                duplicates.append(preset.id)
            id_set.add(preset.id)
        
        if duplicates:
            raise ValueError(f"Duplicate preset IDs found: {duplicates}")
        
        logger.debug(f"Context presets validation passed: {len(collection.presets)} presets")
    
    @classmethod
    def validate_presets_with_catalog(
        cls, 
        collection: ContextPresetsCollection, 
        catalog: ContextCatalog
    ) -> None:
        """校验 presets 引用的 items 是否在 catalog 中存在。"""
        errors: List[str] = []
        
        for preset in collection.presets:
            for item_id in preset.items:
                if not catalog.has(item_id):
                    errors.append(f"Preset '{preset.id}' references unknown item '{item_id}'")
        
        if errors:
            raise ValueError("Presets validation with catalog failed:\n" + "\n".join(errors))
        
        logger.debug(f"Presets validation with catalog passed")


# 默认加载函数
def load_presets(path: str | Path = "config/context_presets.yaml") -> ContextPresetsCollection:
    """加载默认 presets。"""
    return ContextPresetsLoader.load(path)
