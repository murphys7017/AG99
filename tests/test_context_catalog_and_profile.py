"""测试 Context Catalog & Profile 配置加载与校验。"""

import pytest
from pathlib import Path

from src.agent.context import (
    load_catalog,
    load_profiles,
    load_presets,
    validate_profiles,
    resolve_profile_items,
    ContextCatalog,
)
from src.agent.context.profile import PromptProfile, PromptProfileLoader, IncludeConfig, LayoutConfig, TemplatesConfig, OutputConfig, BudgetConfig
from src.agent.context.presets import ContextPresetsLoader


def test_catalog_loads_successfully():
    """测试 catalog 能正常加载。"""
    catalog = load_catalog("config/context_catalog.yaml")
    
    assert catalog is not None
    assert len(catalog.items) > 0
    assert catalog.version == "0.1"
    
    # 验证必需项存在
    assert catalog.has("current_input.text")
    assert catalog.has("conversation.recent_raw")
    assert catalog.has("plan.task_type")


def test_catalog_item_validation():
    """测试 catalog item 字段校验。"""
    catalog = load_catalog("config/context_catalog.yaml")
    
    # 检查所有 item 字段完整性
    for item in catalog.items:
        assert item.id
        assert item.category in {
            "input", "conversation", "plan", "identity", "persona",
            "policy", "memory", "knowledge", "tool", "session"
        }
        assert item.source
        assert isinstance(item.default_priority, int)
        assert item.default_placement in {"prefix", "middle", "suffix"}
        assert item.default_render_mode in {"raw", "summary", "pinned", "structured"}
        assert item.llm_exposure in {"allowed", "redacted", "never"}


def test_presets_load_successfully():
    """测试 presets 能正常加载。"""
    presets = load_presets("config/context_presets.yaml")
    
    assert presets is not None
    assert len(presets.presets) > 0
    
    # 验证预设存在
    assert presets.has("chat_base")
    
    # 测试展开
    chat_base = presets.get("chat_base")
    assert chat_base is not None
    assert "current_input.text" in chat_base.items


def test_presets_validate_with_catalog():
    """测试 presets 引用的 items 在 catalog 中存在。"""
    catalog = load_catalog("config/context_catalog.yaml")
    presets = load_presets("config/context_presets.yaml")
    
    # 不应抛出异常
    ContextPresetsLoader.validate_presets_with_catalog(presets, catalog)


def test_profiles_load_successfully():
    """测试 profiles 能正常加载。"""
    profiles = load_profiles("config/agent/prompt_profiles")
    
    assert profiles is not None
    assert len(profiles) > 0
    assert "chat.single_pass" in profiles


def test_profile_validation_with_catalog():
    """测试 profile 校验通过。"""
    catalog = load_catalog("config/context_catalog.yaml")
    presets = load_presets("config/context_presets.yaml")
    profiles = load_profiles("config/agent/prompt_profiles")
    
    # 不应抛出异常
    validate_profiles(profiles, catalog, presets=presets)


def test_profile_fields_complete():
    """测试 chat.single_pass profile 字段完整性。"""
    profiles = load_profiles("config/agent/prompt_profiles")
    profile = profiles.get("chat.single_pass")
    
    assert profile is not None
    assert profile.id == "chat.single_pass"
    assert profile.pool == "chat"
    assert profile.role
    assert profile.purpose
    
    # Include
    assert len(profile.include.required_items) > 0
    assert "current_input.text" in profile.include.required_items
    
    # Layout
    assert profile.layout.layout_policy_id
    assert "prefix" in profile.layout.placement_overrides
    
    # Budget
    assert profile.budget.max_tokens is not None
    assert profile.budget.truncate_policy in {"priority_then_window", "window_only", "priority_only"}


def test_profile_validation_fails_on_unknown_item():
    """测试 profile 引用不存在的 item 时校验失败。"""
    catalog = load_catalog("config/context_catalog.yaml")
    
    # 创建一个引用不存在 item 的 profile
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
    
    # 应该抛出 ValueError
    with pytest.raises(ValueError, match="not found in catalog"):
        PromptProfileLoader.validate_profile(bad_profile, catalog)


def test_profile_validation_fails_on_placement_override_not_included():
    """测试 placement_override 中的 item 不在 include 中时校验失败。"""
    catalog = load_catalog("config/context_catalog.yaml")
    
    # 创建一个 placement_override 引用未 include 的 item 的 profile
    bad_profile = PromptProfile(
        version="0.1",
        id="test_bad_placement",
        role="test",
        pool="test",
        purpose="test",
        include=IncludeConfig(required_items=["current_input.text"]),
        layout=LayoutConfig(
            placement_overrides={"prefix": ["plan.task_type"]}  # 未在 include 中
        ),
        templates=TemplatesConfig(),
        output=OutputConfig(),
        budget=BudgetConfig(),
    )
    
    # 应该抛出 ValueError
    with pytest.raises(ValueError, match="not in include"):
        PromptProfileLoader.validate_profile(bad_profile, catalog)


def test_profile_validation_fails_on_unknown_preset():
        """测试 profile 引用不存在 preset 时校验失败。"""
        catalog = load_catalog("config/context_catalog.yaml")
        presets = load_presets("config/context_presets.yaml")

        bad_profile = PromptProfile(
                version="0.1",
                id="test_bad_preset",
                role="test",
                pool="test",
                purpose="test",
                include=IncludeConfig(
                        required_items=["current_input.text"],
                        use_presets=["nonexistent_preset"],
                ),
                layout=LayoutConfig(),
                templates=TemplatesConfig(),
                output=OutputConfig(),
                budget=BudgetConfig(),
        )

        with pytest.raises(ValueError, match="Preset 'nonexistent_preset' not found"):
                PromptProfileLoader.validate_profile(bad_profile, catalog, presets=presets)


def test_profile_resolves_items_from_presets():
        """测试 resolve_profile_items 会展开 presets。"""
        presets = load_presets("config/context_presets.yaml")
        profiles = load_profiles("config/agent/prompt_profiles")
        profile = profiles["chat.single_pass"]

        resolved = resolve_profile_items(profile, presets=presets)
        assert "current_input.text" in resolved
        assert "conversation.recent_raw" in resolved
        assert "plan.task_type" in resolved


def test_load_profile_fails_on_invalid_render_mode(tmp_path):
        """测试 render_mode_overrides 非法值会报错（不再静默忽略）。"""
        bad_profile = tmp_path / "bad_profile.yaml"
        bad_profile.write_text(
                """
version: "0.1"
id: test.invalid_render
role: test
pool: chat
purpose: test
include:
    required_items:
        - current_input.text
layout:
    layout_policy_id: linear_prefix_middle_suffix
    render_mode_overrides:
        current_input.text: not_a_mode
templates:
    system_template: s
    user_template: u
output:
    format: text
budget:
    max_tokens: 100
    truncate_policy: priority_then_window
""",
                encoding="utf-8",
        )

        with pytest.raises(ValueError, match="Invalid render_mode"):
                PromptProfileLoader.load_profile(bad_profile)


def test_catalog_has_future_items():
    """测试 catalog 包含 future 类型的占位项。"""
    catalog = load_catalog("config/context_catalog.yaml")
    
    # 验证 future 类型项存在
    future_items = [
        "identity.core",
        "persona.style",
        "memory.pinned_facts",
        "knowledge.snippets_raw",
        "tool_results.raw",
    ]
    
    for item_id in future_items:
        assert catalog.has(item_id), f"Future item '{item_id}' not found"
        item = catalog.get(item_id)
        assert item.source.startswith("future:") or item.source.startswith("slot:")


def test_tool_results_raw_has_llm_exposure_never():
    """测试 tool_results.raw 的 llm_exposure 为 never。"""
    catalog = load_catalog("config/context_catalog.yaml")
    
    item = catalog.get("tool_results.raw")
    assert item is not None
    assert item.llm_exposure == "never", "tool_results.raw must not be exposed to LLM"
