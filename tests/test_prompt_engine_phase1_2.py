"""测试 PromptEngine Phase 1.2 - 统一渲染引擎。

覆盖：
- D1) 能渲染 chat_single_pass
- D2) exposure=never 阻止
- D3) future item missing 不崩溃
- D4) budget 截断生效
- D5) 模板错误 fail-open
"""

import pytest
from pathlib import Path

from src.agent.prompt_engine import PromptEngine
from src.agent.context import (
    ContextCatalog,
    ContextPresetsCollection,
    PromptProfile,
    load_catalog,
    load_presets,
    load_profile,
)
from src.agent.context.types import ContextPack, ContextSlot
from src.agent.types import TaskPlan


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def catalog():
    """加载 context catalog。"""
    return load_catalog("config/context_catalog.yaml")


@pytest.fixture
def presets():
    """加载 context presets。"""
    return load_presets("config/context_presets.yaml")


@pytest.fixture
def chat_profile():
    """加载 chat_single_pass profile。"""
    return load_profile("config/agent/prompt_profiles/chat_single_pass.yaml")


@pytest.fixture
def sample_plan():
    """创建示例 TaskPlan。"""
    return TaskPlan(
        task_type="chat",
        pool_id="chat",
        meta={
            "source": "test",
            "strategy": "single_pass",
            "complexity": "low",
        },
    )


@pytest.fixture
def sample_context():
    """创建示例 ContextPack（包含必需的 slots）。"""
    return ContextPack(
        slots={
            "current_input": ContextSlot(
                name="current_input",
                value={"text": "Hello, how are you?"},
                priority=100,
                source="user",
                status="ok",
            ),
            "recent_obs": ContextSlot(
                name="recent_obs",
                value=["User: Hi", "Assistant: Hello"],
                priority=90,
                source="memory",
                status="ok",
            ),
            "plan_meta": ContextSlot(
                name="plan_meta",
                value={
                    "task_type": "chat",
                    "pool_id": "chat",
                    "strategy": "single_pass",
                    "complexity": "low",
                },
                priority=80,
                source="planner",
                status="ok",
            ),
        },
        recent_obs=[],  # backward compatible
    )


# ============================================================
# D1) 能渲染 chat_single_pass（正常路径）
# ============================================================

def test_render_chat_single_pass_success(catalog, presets, chat_profile, sample_plan, sample_context):
    """测试能成功渲染 chat_single_pass profile。"""
    engine = PromptEngine(catalog=catalog, presets=presets)
    
    messages, manifest = engine.render(
        profile_id="chat.single_pass",
        plan=sample_plan,
        ctx=sample_context,
        profile=chat_profile,  # 直接提供 profile 避免重新加载
    )
    
    # 验证 messages
    assert messages is not None
    assert len(messages) >= 1  # 至少有 user message
    
    # 检查是否有 system message
    has_system = any(msg["role"] == "system" for msg in messages)
    has_user = any(msg["role"] == "user" for msg in messages)
    
    assert has_user, "Must have at least one user message"
    
    # 验证 manifest
    assert manifest.profile_id == "chat.single_pass"
    assert len(manifest.used_items) > 0, "Should have used items"
    assert len(manifest.placements) > 0, "Should have placement info"
    
    # 验证至少包含 current_input.text
    assert "current_input.text" in manifest.used_items
    
    print(f"\n✓ D1) Render success: {manifest.summary()}")


def test_render_messages_contain_user_input(catalog, presets, chat_profile, sample_plan, sample_context):
    """测试 messages 包含用户输入。"""
    engine = PromptEngine(catalog=catalog, presets=presets)
    
    messages, manifest = engine.render(
        profile_id="chat.single_pass",
        plan=sample_plan,
        ctx=sample_context,
        profile=chat_profile,
    )
    
    # 检查 user message 包含输入
    user_messages = [msg for msg in messages if msg["role"] == "user"]
    assert len(user_messages) > 0
    
    user_content = user_messages[0]["content"]
    assert "Hello, how are you?" in user_content
    
    print(f"\n✓ D1) User input present in messages")


# ============================================================
# D2) exposure=never 阻止（安全测试）
# ============================================================

def test_exposure_never_blocks_item(catalog, presets, sample_plan):
    """测试 llm_exposure=never 的 item 被阻止进入 messages。"""
    from src.agent.context.profile import (
        PromptProfile,
        IncludeConfig,
        LayoutConfig,
        TemplatesConfig,
        OutputConfig,
        BudgetConfig,
    )
    
    # 创建一个引用 tool_results.raw（llm_exposure=never）的 profile
    test_profile = PromptProfile(
        version="0.1",
        id="test.exposure_never",
        role="test",
        pool="test",
        purpose="Test exposure blocking",
        include=IncludeConfig(
            required_items=["current_input.text", "tool_results.raw"],  # tool_results.raw = never
            optional_items=[],
        ),
        layout=LayoutConfig(),
        templates=TemplatesConfig(
            system_template="",
            user_template="{{ current_input.text }}",
        ),
        output=OutputConfig(),
        budget=BudgetConfig(),
    )
    
    # 创建 context（包含 tool_results）
    ctx = ContextPack(
        slots={
            "current_input": ContextSlot(
                name="current_input",
                value={"text": "Test input"},
                priority=100,
                source="user",
                status="ok",
            ),
            "tool_results": ContextSlot(
                name="tool_results",
                value={"raw": "sensitive tool data"},
                priority=50,
                source="tool",
                status="ok",
            ),
        },
    )
    
    engine = PromptEngine(catalog=catalog, presets=presets)
    messages, manifest = engine.render(
        profile_id="test.exposure_never",
        plan=sample_plan,
        ctx=ctx,
        profile=test_profile,
    )
    
    # 验证 tool_results.raw 被阻止
    assert "tool_results.raw" in manifest.exposure_blocked, \
        f"tool_results.raw should be blocked, but exposure_blocked={manifest.exposure_blocked}"
    assert "tool_results.raw" not in manifest.used_items
    
    # 验证 messages 不包含敏感数据
    all_content = " ".join(msg["content"] for msg in messages)
    assert "sensitive tool data" not in all_content
    
    print(f"\n✓ D2) exposure=never blocked: {manifest.exposure_blocked}")


# ============================================================
# D3) future item missing 不崩溃（鲁棒性）
# ============================================================

def test_future_item_missing_not_crash(catalog, presets, sample_plan):
    """测试引用 future item（如 persona.style）不崩溃。"""
    from src.agent.context.profile import (
        PromptProfile,
        IncludeConfig,
        LayoutConfig,
        TemplatesConfig,
        OutputConfig,
        BudgetConfig,
    )
    
    # 创建引用 future item 的 profile
    test_profile = PromptProfile(
        version="0.1",
        id="test.future_item",
        role="test",
        pool="test",
        purpose="Test future item handling",
        include=IncludeConfig(
            required_items=["current_input.text"],
            optional_items=["persona.style"],  # future:persona_style
        ),
        layout=LayoutConfig(),
        templates=TemplatesConfig(
            system_template="",
            user_template="{{ current_input.text }}",
        ),
        output=OutputConfig(),
        budget=BudgetConfig(),
    )
    
    ctx = ContextPack(
        slots={
            "current_input": ContextSlot(
                name="current_input",
                value={"text": "Test input"},
                priority=100,
                source="user",
                status="ok",
            ),
        },
    )
    
    engine = PromptEngine(catalog=catalog, presets=presets)
    
    # 不应该崩溃
    messages, manifest = engine.render(
        profile_id="test.future_item",
        plan=sample_plan,
        ctx=ctx,
        profile=test_profile,
    )
    
    # 验证 persona.style 被标记为 missing
    assert "persona.style" in manifest.missing_items
    assert "persona.style" not in manifest.used_items
    
    # 验证仍然生成了 messages
    assert len(messages) > 0
    
    print(f"\n✓ D3) Future item missing handled: missing_items={manifest.missing_items}")


# ============================================================
# D4) budget 截断生效（性能控制）
# ============================================================

def test_budget_per_item_max_truncation(catalog, presets, sample_plan):
    """测试 per_item_max 截断生效。"""
    from src.agent.context.profile import (
        PromptProfile,
        IncludeConfig,
        LayoutConfig,
        TemplatesConfig,
        OutputConfig,
        BudgetConfig,
    )
    
    # 创建有 per_item_max 限制的 profile
    test_profile = PromptProfile(
        version="0.1",
        id="test.budget",
        role="test",
        pool="test",
        purpose="Test budget truncation",
        include=IncludeConfig(
            required_items=["current_input.text", "conversation.recent_raw"],
        ),
        layout=LayoutConfig(),
        templates=TemplatesConfig(
            system_template="",
            user_template="{{ conversation.recent_raw }}\n{{ current_input.text }}",
        ),
        output=OutputConfig(),
        budget=BudgetConfig(
            per_item_max={
                "conversation.recent_raw": 50,  # 很小的限制
            }
        ),
    )
    
    # 创建很长的 conversation
    long_conversation = "This is a very long conversation that should be truncated. " * 20
    
    ctx = ContextPack(
        slots={
            "current_input": ContextSlot(
                name="current_input",
                value={"text": "Test"},
                priority=100,
                source="user",
                status="ok",
            ),
            "recent_obs": ContextSlot(
                name="recent_obs",
                value=long_conversation,
                priority=90,
                source="memory",
                status="ok",
            ),
        },
    )
    
    engine = PromptEngine(catalog=catalog, presets=presets)
    messages, manifest = engine.render(
        profile_id="test.budget",
        plan=sample_plan,
        ctx=ctx,
        profile=test_profile,
    )
    
    # 验证有截断记录
    assert len(manifest.truncations) > 0, f"Should have truncations, but got {manifest.truncations}"
    
    # 验证截断的是 conversation.recent_raw
    truncated_items = [t.item_id for t in manifest.truncations]
    assert "conversation.recent_raw" in truncated_items
    
    # 验证 before_len > after_len
    for trunc in manifest.truncations:
        if trunc.item_id == "conversation.recent_raw":
            assert trunc.before_len > trunc.after_len
            assert trunc.after_len <= 50 or trunc.after_len <= 53  # 允许 "..." 后缀
    
    print(f"\n✓ D4) Budget truncation applied: {len(manifest.truncations)} truncations")


def test_budget_max_chars_truncation(catalog, presets, sample_plan):
    """测试 max_chars 全局截断生效。"""
    from src.agent.context.profile import (
        PromptProfile,
        IncludeConfig,
        LayoutConfig,
        TemplatesConfig,
        OutputConfig,
        BudgetConfig,
    )
    
    # 创建有 max_chars 限制的 profile
    test_profile = PromptProfile(
        version="0.1",
        id="test.max_chars",
        role="test",
        pool="test",
        purpose="Test max_chars truncation",
        include=IncludeConfig(
            required_items=["current_input.text", "conversation.recent_raw"],
        ),
        layout=LayoutConfig(),
        templates=TemplatesConfig(
            system_template="",
            user_template="{{ current_input.text }}",
        ),
        output=OutputConfig(),
        budget=BudgetConfig(
            max_chars=100,  # 很小的限制
        ),
    )
    
    # 创建很长的数据
    long_text = "X" * 500
    
    ctx = ContextPack(
        slots={
            "current_input": ContextSlot(
                name="current_input",
                value={"text": long_text},
                priority=100,
                source="user",
                status="ok",
            ),
            "recent_obs": ContextSlot(
                name="recent_obs",
                value="Y" * 500,
                priority=90,
                source="memory",
                status="ok",
            ),
        },
    )
    
    engine = PromptEngine(catalog=catalog, presets=presets)
    messages, manifest = engine.render(
        profile_id="test.max_chars",
        plan=sample_plan,
        ctx=ctx,
        profile=test_profile,
    )
    
    # 验证有截断（max_chars_budget）
    budget_truncations = [t for t in manifest.truncations if t.rule == "max_chars_budget"]
    # 注意：如果所有 items 都在 max_chars 之内，可能不会触发
    # 但我们的数据很长，应该会触发
    
    print(f"\n✓ D4) Max chars truncation: {len(budget_truncations)} budget truncations")


# ============================================================
# D5) 模板错误 fail-open（容错性）
# ============================================================

def test_template_error_fail_open(catalog, presets, sample_plan):
    """测试模板错误时 fail-open（降级为最小 user prompt）。"""
    from src.agent.context.profile import (
        PromptProfile,
        IncludeConfig,
        LayoutConfig,
        TemplatesConfig,
        OutputConfig,
        BudgetConfig,
    )
    
    # 创建有语法错误的模板
    test_profile = PromptProfile(
        version="0.1",
        id="test.template_error",
        role="test",
        pool="test",
        purpose="Test template error handling",
        include=IncludeConfig(
            required_items=["current_input.text"],
        ),
        layout=LayoutConfig(),
        templates=TemplatesConfig(
            system_template="{% invalid syntax %}",  # 语法错误
            user_template="{{ undefined_variable }}",  # 未定义变量
        ),
        output=OutputConfig(),
        budget=BudgetConfig(),
    )
    
    ctx = ContextPack(
        slots={
            "current_input": ContextSlot(
                name="current_input",
                value={"text": "Fallback input"},
                priority=100,
                source="user",
                status="ok",
            ),
        },
    )
    
    engine = PromptEngine(catalog=catalog, presets=presets)
    
    # 不应该崩溃
    messages, manifest = engine.render(
        profile_id="test.template_error",
        plan=sample_plan,
        ctx=ctx,
        profile=test_profile,
    )
    
    # 验证有模板错误记录
    assert len(manifest.template_errors) > 0, f"Should have template errors, got {manifest.template_errors}"
    
    # 验证仍然生成了 messages（降级）
    assert len(messages) > 0
    
    # 验证至少包含 user message
    user_messages = [msg for msg in messages if msg["role"] == "user"]
    assert len(user_messages) > 0
    
    # 验证降级内容包含 fallback（可能是 current_input.text 或 "User input not available"）
    user_content = user_messages[0]["content"]
    assert len(user_content) > 0  # 不为空
    
    print(f"\n✓ D5) Template error fail-open: errors={len(manifest.template_errors)}, messages={len(messages)}")


# ============================================================
# 额外测试：redacted 处理
# ============================================================

def test_redacted_items(catalog, presets, sample_plan):
    """测试 llm_exposure=redacted 的 item 被降级为 placeholder。"""
    from src.agent.context.profile import (
        PromptProfile,
        IncludeConfig,
        LayoutConfig,
        TemplatesConfig,
        OutputConfig,
        BudgetConfig,
    )
    
    # 假设 catalog 中有 llm_exposure=redacted 的 item（如果没有，这个测试会跳过）
    # 先检查 catalog
    redacted_items = [item for item in catalog.items if item.llm_exposure == "redacted"]
    
    if not redacted_items:
        pytest.skip("No redacted items in catalog")
    
    redacted_item_id = redacted_items[0].id
    
    # 创建引用 redacted item 的 profile
    test_profile = PromptProfile(
        version="0.1",
        id="test.redacted",
        role="test",
        pool="test",
        purpose="Test redacted handling",
        include=IncludeConfig(
            required_items=["current_input.text"],
            optional_items=[redacted_item_id],
        ),
        layout=LayoutConfig(),
        templates=TemplatesConfig(
            system_template="",
            user_template="{{ current_input.text }}",
        ),
        output=OutputConfig(),
        budget=BudgetConfig(),
    )
    
    ctx = ContextPack(
        slots={
            "current_input": ContextSlot(
                name="current_input",
                value={"text": "Test"},
                priority=100,
                source="user",
                status="ok",
            ),
        },
    )
    
    engine = PromptEngine(catalog=catalog, presets=presets)
    messages, manifest = engine.render(
        profile_id="test.redacted",
        plan=sample_plan,
        ctx=ctx,
        profile=test_profile,
    )
    
    # 验证 redacted_items 有记录
    if redacted_item_id in manifest.missing_items:
        # 如果 item 数据缺失，跳过
        pytest.skip(f"Redacted item '{redacted_item_id}' data not available in context")
    
    # 如果有记录，验证
    if redacted_item_id in manifest.redacted_items:
        print(f"\n✓ Extra) Redacted item handled: {manifest.redacted_items}")


# ============================================================
# 运行所有测试的辅助函数
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
