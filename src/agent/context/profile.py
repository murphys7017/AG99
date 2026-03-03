"""Prompt Profile 加载与校验模块。

提供 PromptProfile 的加载、校验、解析功能。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set

import yaml
from loguru import logger

from .catalog import ContextCatalog, PlacementType, RenderModeType
from .presets import ContextPresetsCollection


TruncatePolicyType = Literal["priority_then_window", "window_only", "priority_only"]
OutputFormatType = Literal["text", "json", "markdown"]


@dataclass
class IncludeConfig:
    """包含的信息单元配置。"""
    
    required_items: List[str] = field(default_factory=list)
    optional_items: List[str] = field(default_factory=list)
    use_presets: List[str] = field(default_factory=list)


@dataclass
class LayoutConfig:
    """布局配置。"""
    
    layout_policy_id: str = "linear_prefix_middle_suffix"
    placement_overrides: Dict[PlacementType, List[str]] = field(default_factory=dict)
    render_mode_overrides: Dict[str, RenderModeType] = field(default_factory=dict)


@dataclass
class TemplatesConfig:
    """模板配置。"""
    
    system_template: str = ""
    user_template: str = ""


@dataclass
class OutputConfig:
    """输出配置。"""
    
    format: OutputFormatType = "text"
    schema_id: Optional[str] = None


@dataclass
class BudgetConfig:
    """预算配置。"""
    
    max_tokens: Optional[int] = None
    max_chars: Optional[int] = None
    truncate_policy: TruncatePolicyType = "priority_then_window"
    per_item_max: Dict[str, int] = field(default_factory=dict)


@dataclass
class PromptProfile:
    """Prompt Profile 完整定义。"""
    
    version: str
    id: str
    role: str
    pool: str
    purpose: str
    include: IncludeConfig
    layout: LayoutConfig
    templates: TemplatesConfig
    output: OutputConfig
    budget: BudgetConfig
    notes: str = ""
    
    def get_all_items(self, preset_items: Optional[List[str]] = None) -> List[str]:
        """获取所有涉及的 item（required + optional + expanded presets）。"""
        items = list(self.include.required_items) + list(self.include.optional_items)
        if preset_items:
            items.extend(preset_items)
        # 去重并保持顺序
        seen: Set[str] = set()
        result: List[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result
    
    def get_placement(self, item_id: str) -> Optional[PlacementType]:
        """获取指定 item 的 placement（如果有覆盖）。"""
        for placement, items in self.layout.placement_overrides.items():
            if item_id in items:
                return placement
        return None
    
    def get_render_mode(self, item_id: str) -> Optional[RenderModeType]:
        """获取指定 item 的 render_mode（如果有覆盖）。"""
        return self.layout.render_mode_overrides.get(item_id)


class PromptProfileLoader:
    """Prompt Profile 加载器。"""
    
    VALID_OUTPUT_FORMATS: Set[str] = {"text", "json", "markdown"}
    VALID_TRUNCATE_POLICIES: Set[str] = {"priority_then_window", "window_only", "priority_only"}
    
    @classmethod
    def load_profile(cls, path: str | Path) -> PromptProfile:
        """加载单个 profile。"""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Prompt profile not found: {path}")
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            raise ValueError(f"Failed to load profile yaml: {e}") from e
        
        if not isinstance(data, dict):
            raise ValueError("Profile must be a dict")
        
        # 解析各部分
        version = str(data.get("version", "0.1"))
        profile_id = str(data.get("id", ""))
        if not profile_id:
            raise ValueError("Profile id is required")
        
        role = str(data.get("role", ""))
        pool = str(data.get("pool", ""))
        purpose = str(data.get("purpose", ""))
        
        # Include
        include_data = data.get("include", {})
        include = cls._parse_include(include_data)
        
        # Layout
        layout_data = data.get("layout", {})
        layout = cls._parse_layout(layout_data)
        
        # Templates
        templates_data = data.get("templates", {})
        templates = cls._parse_templates(templates_data)
        
        # Output
        output_data = data.get("output", {})
        output = cls._parse_output(output_data)
        
        # Budget
        budget_data = data.get("budget", {})
        budget = cls._parse_budget(budget_data)
        
        notes = str(data.get("notes", ""))
        
        profile = PromptProfile(
            version=version,
            id=profile_id,
            role=role,
            pool=pool,
            purpose=purpose,
            include=include,
            layout=layout,
            templates=templates,
            output=output,
            budget=budget,
            notes=notes,
        )
        
        logger.debug(f"Loaded prompt profile: {profile_id} (pool={pool})")
        return profile
    
    @classmethod
    def load_profiles_from_dir(cls, dir_path: str | Path) -> Dict[str, PromptProfile]:
        """从目录加载所有 profiles。"""
        dir_path = Path(dir_path)
        if not dir_path.exists() or not dir_path.is_dir():
            logger.warning(f"Prompt profiles directory not found: {dir_path}")
            return {}
        
        profiles: Dict[str, PromptProfile] = {}
        for file_path in dir_path.glob("*.yaml"):
            try:
                profile = cls.load_profile(file_path)
                if profile.id in profiles:
                    logger.warning(f"Duplicate profile ID '{profile.id}' in {file_path}")
                profiles[profile.id] = profile
            except Exception as e:
                logger.error(f"Failed to load profile {file_path}: {e}")
        
        logger.info(f"Loaded {len(profiles)} prompt profiles from {dir_path}")
        return profiles
    
    @classmethod
    def _parse_include(cls, data: Dict[str, Any]) -> IncludeConfig:
        """解析 include 配置。"""
        required = data.get("required_items", [])
        optional = data.get("optional_items", [])
        presets = data.get("use_presets", [])
        
        if not isinstance(required, list):
            required = []
        if not isinstance(optional, list):
            optional = []
        if not isinstance(presets, list):
            presets = []
        
        return IncludeConfig(
            required_items=[str(x) for x in required],
            optional_items=[str(x) for x in optional],
            use_presets=[str(x) for x in presets],
        )
    
    @classmethod
    def _parse_layout(cls, data: Dict[str, Any]) -> LayoutConfig:
        """解析 layout 配置。"""
        layout_policy_id = str(data.get("layout_policy_id", "linear_prefix_middle_suffix"))
        
        placement_overrides_raw = data.get("placement_overrides", {})
        placement_overrides: Dict[PlacementType, List[str]] = {}
        if isinstance(placement_overrides_raw, dict):
            for placement, items in placement_overrides_raw.items():
                if placement in {"prefix", "middle", "suffix"} and isinstance(items, list):
                    placement_overrides[placement] = [str(x) for x in items]
        
        render_mode_overrides_raw = data.get("render_mode_overrides", {})
        render_mode_overrides: Dict[str, RenderModeType] = {}
        if isinstance(render_mode_overrides_raw, dict):
            for item_id, mode in render_mode_overrides_raw.items():
                if mode not in {"raw", "summary", "pinned", "structured"}:
                    raise ValueError(
                        f"Invalid render_mode '{mode}' for item '{item_id}' in render_mode_overrides"
                    )
                render_mode_overrides[str(item_id)] = mode
        
        return LayoutConfig(
            layout_policy_id=layout_policy_id,
            placement_overrides=placement_overrides,
            render_mode_overrides=render_mode_overrides,
        )
    
    @classmethod
    def _parse_templates(cls, data: Dict[str, Any]) -> TemplatesConfig:
        """解析 templates 配置。"""
        system = str(data.get("system_template", ""))
        user = str(data.get("user_template", ""))
        return TemplatesConfig(system_template=system, user_template=user)
    
    @classmethod
    def _parse_output(cls, data: Dict[str, Any]) -> OutputConfig:
        """解析 output 配置。"""
        fmt = str(data.get("format", "text"))
        if fmt not in cls.VALID_OUTPUT_FORMATS:
            logger.warning(f"Invalid output format '{fmt}', using 'text'")
            fmt = "text"
        
        schema_id = data.get("schema_id")
        if schema_id is not None:
            schema_id = str(schema_id)
        
        return OutputConfig(format=fmt, schema_id=schema_id)
    
    @classmethod
    def _parse_budget(cls, data: Dict[str, Any]) -> BudgetConfig:
        """解析 budget 配置。"""
        max_tokens = data.get("max_tokens")
        if max_tokens is not None and not isinstance(max_tokens, int):
            max_tokens = None
        
        max_chars = data.get("max_chars")
        if max_chars is not None and not isinstance(max_chars, int):
            max_chars = None
        
        truncate_policy = str(data.get("truncate_policy", "priority_then_window"))
        if truncate_policy not in cls.VALID_TRUNCATE_POLICIES:
            logger.warning(f"Invalid truncate_policy '{truncate_policy}', using 'priority_then_window'")
            truncate_policy = "priority_then_window"
        
        per_item_max_raw = data.get("per_item_max", {})
        per_item_max: Dict[str, int] = {}
        if isinstance(per_item_max_raw, dict):
            for k, v in per_item_max_raw.items():
                if isinstance(v, int):
                    per_item_max[str(k)] = v
        
        return BudgetConfig(
            max_tokens=max_tokens,
            max_chars=max_chars,
            truncate_policy=truncate_policy,
            per_item_max=per_item_max,
        )
    
    @classmethod
    def validate_profile(
        cls,
        profile: PromptProfile,
        catalog: ContextCatalog,
        presets: Optional[ContextPresetsCollection] = None,
    ) -> None:
        """校验 profile 完整性（需要 catalog 作为依赖）。"""
        errors: List[str] = []

        expanded_preset_items: List[str] = []
        if profile.include.use_presets:
            if presets is None:
                errors.append(
                    "Profile uses presets but presets collection is not provided for validation"
                )
            else:
                for preset_id in profile.include.use_presets:
                    if not presets.has(preset_id):
                        errors.append(f"Preset '{preset_id}' not found")
                if not errors:
                    expanded_preset_items = presets.expand(profile.include.use_presets)
        
        # 1. 检查 required_items 是否在 catalog 中存在
        for item_id in profile.include.required_items:
            if not catalog.has(item_id):
                errors.append(f"Required item '{item_id}' not found in catalog")
        
        # 2. 检查 optional_items 是否在 catalog 中存在
        for item_id in profile.include.optional_items:
            if not catalog.has(item_id):
                errors.append(f"Optional item '{item_id}' not found in catalog")

        for item_id in expanded_preset_items:
            if not catalog.has(item_id):
                errors.append(f"Preset expanded item '{item_id}' not found in catalog")
        
        # 3. 检查 placement_overrides 中的 item 是否在 include 中
        all_included = set(profile.get_all_items(preset_items=expanded_preset_items))
        for placement, items in profile.layout.placement_overrides.items():
            for item_id in items:
                if item_id not in all_included:
                    errors.append(
                        f"Item '{item_id}' in placement_overrides[{placement}] not in include"
                    )
        
        # 4. 检查 render_mode_overrides 中的 item 是否在 include 中
        for item_id in profile.layout.render_mode_overrides.keys():
            if item_id not in all_included:
                errors.append(
                    f"Item '{item_id}' in render_mode_overrides not in include"
                )
        
        # 5. 检查 per_item_max 中的 item 是否在 include 中
        for item_id in profile.budget.per_item_max.keys():
            if item_id not in all_included:
                logger.warning(
                    f"Item '{item_id}' in budget.per_item_max not in include (will be ignored)"
                )
        
        if errors:
            raise ValueError(f"Profile '{profile.id}' validation failed:\n" + "\n".join(errors))
        
        logger.debug(f"Profile '{profile.id}' validation passed")

    @classmethod
    def resolve_profile_items(
        cls,
        profile: PromptProfile,
        presets: Optional[ContextPresetsCollection] = None,
    ) -> List[str]:
        """解析 profile 实际包含 items（展开 presets）。"""
        expanded_preset_items: List[str] = []
        if presets is not None and profile.include.use_presets:
            expanded_preset_items = presets.expand(profile.include.use_presets)
        return profile.get_all_items(preset_items=expanded_preset_items)


# 便捷加载函数
def load_profile(path: str | Path) -> PromptProfile:
    """加载单个 profile。"""
    return PromptProfileLoader.load_profile(path)


def load_profiles(dir_path: str | Path = "config/agent/prompt_profiles") -> Dict[str, PromptProfile]:
    """从目录加载所有 profiles。"""
    return PromptProfileLoader.load_profiles_from_dir(dir_path)


def validate_profiles(
    profiles: Dict[str, PromptProfile],
    catalog: ContextCatalog,
    presets: Optional[ContextPresetsCollection] = None,
) -> None:
    """批量校验 profiles。"""
    for profile_id, profile in profiles.items():
        PromptProfileLoader.validate_profile(profile, catalog, presets=presets)


def resolve_profile_items(
    profile: PromptProfile,
    presets: Optional[ContextPresetsCollection] = None,
) -> List[str]:
    """便捷函数：解析 profile 实际包含 items（展开 presets）。"""
    return PromptProfileLoader.resolve_profile_items(profile, presets=presets)
