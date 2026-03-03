"""Context Catalog & Profile 配置加载验证脚本。

用于验证 catalog、profiles、presets 的加载与校验功能。
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger

from src.agent.context import (
    load_catalog,
    load_profiles,
    load_presets,
    validate_profiles,
    resolve_profile_items,
    ContextCatalog,
    ContextPresetsCollection,
)
from src.agent.context.presets import ContextPresetsLoader


# Configure logger
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")


def check_load_catalog():
    """测试 catalog 加载。"""
    logger.info("=" * 60)
    logger.info("Testing catalog loading...")
    
    try:
        catalog = load_catalog("config/context_catalog.yaml")
        logger.info(f"✓ Catalog loaded: {len(catalog.items)} items")
        
        # 打印各类别统计
        categories = {}
        for item in catalog.items:
            categories[item.category] = categories.get(item.category, 0) + 1
        
        logger.info("Catalog items by category:")
        for cat, count in sorted(categories.items()):
            logger.info(f"  - {cat}: {count}")
        
        # 打印一些示例 item
        logger.info("\nSample items:")
        for item in catalog.items[:5]:
            logger.info(
                f"  - {item.id} (category={item.category}, "
                f"priority={item.default_priority}, "
                f"placement={item.default_placement}, "
                f"llm_exposure={item.llm_exposure})"
            )
        
        return catalog
    except Exception as e:
        logger.error(f"✗ Catalog loading failed: {e}")
        raise


def check_load_presets(catalog: ContextCatalog):
    """测试 presets 加载。"""
    logger.info("=" * 60)
    logger.info("Testing presets loading...")
    
    try:
        presets = load_presets("config/context_presets.yaml")
        logger.info(f"✓ Presets loaded: {len(presets.presets)} presets")
        
        # 打印 presets
        logger.info("\nPresets:")
        for preset in presets.presets:
            logger.info(f"  - {preset.id}: {len(preset.items)} items")
        
        # 校验 presets 引用的 items 是否存在
        ContextPresetsLoader.validate_presets_with_catalog(presets, catalog)
        logger.info("✓ Presets validation with catalog passed")
        
        # 测试 preset 展开
        test_preset_id = "chat_base"
        if presets.has(test_preset_id):
            expanded = presets.expand([test_preset_id])
            logger.info(f"\nExpanded preset '{test_preset_id}': {expanded}")
        
        return presets
    except Exception as e:
        logger.error(f"✗ Presets loading failed: {e}")
        raise


def check_load_profiles(catalog: ContextCatalog, presets: ContextPresetsCollection):
    """测试 profiles 加载。"""
    logger.info("=" * 60)
    logger.info("Testing profiles loading...")
    
    try:
        profiles = load_profiles("config/agent/prompt_profiles")
        logger.info(f"✓ Profiles loaded: {len(profiles)} profiles")
        
        # 打印 profiles
        logger.info("\nProfiles:")
        for profile_id, profile in profiles.items():
            logger.info(f"  - {profile_id} (pool={profile.pool}, role={profile.role})")
        
        # 校验 profiles
        validate_profiles(profiles, catalog, presets=presets)
        logger.info("✓ All profiles validation passed")
        
        return profiles
    except Exception as e:
        logger.error(f"✗ Profiles loading failed: {e}")
        raise


def check_profile_details(
    profiles: dict,
    catalog: ContextCatalog,
    presets: ContextPresetsCollection,
):
    """测试 profile 详细信息解析。"""
    logger.info("=" * 60)
    logger.info("Testing profile details...")
    
    profile_id = "chat.single_pass"
    if profile_id not in profiles:
        logger.warning(f"Profile '{profile_id}' not found, skipping details test")
        return
    
    profile = profiles[profile_id]
    logger.info(f"\nProfile: {profile_id}")
    logger.info(f"  Version: {profile.version}")
    logger.info(f"  Role: {profile.role}")
    logger.info(f"  Pool: {profile.pool}")
    logger.info(f"  Purpose: {profile.purpose}")
    
    # Include
    logger.info(f"\n  Include:")
    logger.info(f"    Required items ({len(profile.include.required_items)}):")
    for item in profile.include.required_items:
        cat_item = catalog.get(item)
        cat_info = f" [{cat_item.category}]" if cat_item else " [NOT IN CATALOG]"
        logger.info(f"      - {item}{cat_info}")
    
    logger.info(f"    Optional items ({len(profile.include.optional_items)}):")
    for item in profile.include.optional_items:
        cat_item = catalog.get(item)
        cat_info = f" [{cat_item.category}]" if cat_item else " [NOT IN CATALOG]"
        logger.info(f"      - {item}{cat_info}")

    if profile.include.use_presets:
        logger.info(f"    Use presets: {profile.include.use_presets}")

    resolved_items = resolve_profile_items(profile, presets=presets)
    logger.info(f"    Resolved items ({len(resolved_items)}): {resolved_items}")
    
    # Layout
    logger.info(f"\n  Layout:")
    logger.info(f"    Policy: {profile.layout.layout_policy_id}")
    logger.info(f"    Placement overrides:")
    for placement, items in profile.layout.placement_overrides.items():
        logger.info(f"      {placement}: {items}")
    
    logger.info(f"    Render mode overrides:")
    for item, mode in profile.layout.render_mode_overrides.items():
        logger.info(f"      {item}: {mode}")
    
    # Budget
    logger.info(f"\n  Budget:")
    logger.info(f"    Max tokens: {profile.budget.max_tokens}")
    logger.info(f"    Truncate policy: {profile.budget.truncate_policy}")
    if profile.budget.per_item_max:
        logger.info(f"    Per-item max:")
        for item, max_val in profile.budget.per_item_max.items():
            logger.info(f"      {item}: {max_val}")
    
    # Output
    logger.info(f"\n  Output:")
    logger.info(f"    Format: {profile.output.format}")
    logger.info(f"    Schema ID: {profile.output.schema_id}")
    
    logger.info(f"\n  Notes: {profile.notes}")


def check_validation_failures():
    """测试校验失败场景。"""
    logger.info("=" * 60)
    logger.info("Testing validation failures...")
    
    # 测试 1: Profile 引用不存在的 item
    from src.agent.context.profile import PromptProfile, IncludeConfig, LayoutConfig, TemplatesConfig, OutputConfig, BudgetConfig
    
    catalog = load_catalog("config/context_catalog.yaml")
    
    bad_profile = PromptProfile(
        version="0.1",
        id="test_bad",
        role="test",
        pool="test",
        purpose="test",
        include=IncludeConfig(required_items=["nonexistent_item"]),
        layout=LayoutConfig(),
        templates=TemplatesConfig(),
        output=OutputConfig(),
        budget=BudgetConfig(),
    )
    
    try:
        from src.agent.context.profile import PromptProfileLoader
        PromptProfileLoader.validate_profile(bad_profile, catalog)
        logger.error("✗ Should have raised validation error for nonexistent item")
    except ValueError as e:
        logger.info(f"✓ Correctly caught validation error: {str(e)[:100]}...")


def main():
    """主函数。"""
    logger.info("=" * 60)
    logger.info("Context Catalog & Profile Validation Script")
    logger.info("=" * 60)
    
    try:
        # 1. 加载 catalog
        catalog = check_load_catalog()
        
        # 2. 加载 presets
        presets = check_load_presets(catalog)
        
        # 3. 加载 profiles
        profiles = check_load_profiles(catalog, presets)
        
        # 4. 显示 profile 详情
        check_profile_details(profiles, catalog, presets)
        
        # 5. 测试校验失败
        check_validation_failures()
        
        logger.info("=" * 60)
        logger.info("✓ All tests passed!")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error("=" * 60)
        logger.error(f"✗ Test failed: {e}")
        logger.error("=" * 60)
        raise


if __name__ == "__main__":
    main()
