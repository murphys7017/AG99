# Context Catalog & PromptProfile 阶段总结报告

**实施日期**: 2026-03-04  
**版本**: Phase 1.1 骨架版  
**状态**: ✅ 全部验证通过  
**最后校验**: 2026-03-04

---

## 1. 信息收集报告（简版）

### 现有 Config 体系
- **Config 根目录**: `config/`
- **Config 加载方式**: `yaml.safe_load` + dataclass + 手写校验
- **已有配置提供者**: GateConfigProvider, LLMConfig, AgentConfigRegistry, MemoryConfigProvider
- **Agent 配置体系**: `config/agent/` 包含 pool_selector/pools/prompts/subagents 子目录
- **校验机制**: dataclass + isinstance + 自定义 validate 方法（不使用 jsonschema/pydantic）

### 设计决策
- 沿用现有 yaml + dataclass + 手写校验风格
- 配置文件放在现有目录体系中（`config/` 根目录与 `config/agent/` 子目录）
- 加载模块放在 `src/agent/context/` 中，与现有 context builder 同目录
- fail-open 设计：配置加载失败不阻断主流程，在 AgentQueen 中用 `enable_catalog_loading` 控制

---

## 2. 新增配置文件清单

### 配置文件

| 文件路径 | 类型 | 条目数 | 说明 |
|---------|------|--------|------|
| `config/context_catalog.yaml` | Catalog | 22 items | 信息单元目录 |
| `config/agent/prompt_profiles/chat_single_pass.yaml` | Profile | 1 profile | 聊天单通道 profile |
| `config/context_presets.yaml` | Presets | 5 presets | 可选预设组合 |

### 加载/校验模块

| 文件路径 | 说明 |
|---------|------|
| `src/agent/context/catalog.py` | Catalog 加载与校验 |
| `src/agent/context/profile.py` | PromptProfile 加载与校验 |
| `src/agent/context/presets.py` | Presets 加载与校验 |
| `src/agent/context/__init__.py` | 更新导出 |

### 验证工具

| 文件路径 | 说明 |
|---------|------|
| `tools/test_context_config.py` | 配置加载验证脚本 |
| `tests/test_context_catalog_and_profile.py` | pytest 测试套件（14 tests） |

---

## 3. Catalog 字段结构

### ContextItem 字段

```python
@dataclass
class ContextItem:
    id: str                         # 唯一标识，如 "current_input.text"
    category: CategoryType          # 类别，枚举 10 种
    source: str                     # 来源，格式 "slot:xxx" / "derived:xxx" / "future:xxx"
    default_priority: int           # 默认优先级（越大越优先）
    default_placement: PlacementType # 默认位置，枚举 "prefix/middle/suffix"
    default_render_mode: RenderModeType # 默认渲染模式，枚举 "raw/summary/pinned/structured"
    llm_exposure: LLMExposureType   # LLM 暴露策略，枚举 "allowed/redacted/never"
    notes: str = ""                 # 备注
```

### 信息单元分类（10 类）

| Category | 说明 | 示例 |
|----------|------|------|
| `input` | 用户输入 | current_input.text, current_input.attachments |
| `conversation` | 对话历史 | conversation.recent_raw, conversation.recent_pinned |
| `plan` | 任务规划 | plan.task_type, plan.pool_id, plan.strategy |
| `identity` | 核心身份 | identity.core (future) |
| `persona` | 回复风格 | persona.style, persona.do_dont (future) |
| `policy` | 运行时策略 | policy.runtime_constraints (future) |
| `memory` | 持久记忆 | memory.pinned_facts, memory.recent_turns (future) |
| `knowledge` | 知识检索 | knowledge.snippets_raw, knowledge.snippets_pinned (future) |
| `tool` | 工具结果 | tool_results.raw (llm_exposure=never), tool_results.llm_view |
| `session` | 会话状态 | session.metadata |

### Catalog 统计

- **总条目**: 22 items
- **已实现（slot:）**: 4 items（current_input, recent_obs, plan_meta, session_state）
- **待实现（future:）**: 15 items（identity, persona, policy, memory, knowledge, tool）
- **派生项（derived:）**: 3 items（pinned/summary 变体）

---

## 4. PromptProfile 字段结构

### PromptProfile 完整定义

```python
@dataclass
class PromptProfile:
    version: str                    # 配置版本
    id: str                         # Profile 唯一 ID（如 "chat.single_pass"）
    role: str                       # 角色（如 "chat_assistant"）
    pool: str                       # 目标 pool（如 "chat"）
    purpose: str                    # 用途说明
    include: IncludeConfig          # 包含的信息单元
    layout: LayoutConfig            # 布局配置
    templates: TemplatesConfig      # 模板配置
    output: OutputConfig            # 输出配置
    budget: BudgetConfig            # 预算配置
    notes: str = ""                 # 备注
```

### Include 配置

```python
@dataclass
class IncludeConfig:
    required_items: List[str]       # 必需项（缺失报错）
    optional_items: List[str]       # 可选项（缺失跳过）
    use_presets: List[str]          # 引用 presets（可选）
```

### Layout 配置

```python
@dataclass
class LayoutConfig:
    layout_policy_id: str           # 布局策略 ID
    placement_overrides: Dict[PlacementType, List[str]]  # 位置覆盖
    render_mode_overrides: Dict[str, RenderModeType]     # 渲染模式覆盖
```

### Budget 配置

```python
@dataclass
class BudgetConfig:
    max_tokens: Optional[int]       # 最大 token 数
    max_chars: Optional[int]        # 最大字符数
    truncate_policy: TruncatePolicyType  # 截断策略
    per_item_max: Dict[str, int]    # 单个 item 的最大长度
```

---

## 5. 校验规则说明

### Catalog 校验

✅ **已实现校验**:
1. ID 唯一性检查
2. 枚举值合法性（category/placement/render_mode/llm_exposure）
3. Source 格式检查（slot:/derived:/future:）

⚠️ **告警规则**:
- Source 格式不规范时输出 warning（不阻断）
- llm_exposure=never 的 item 会被记录（用于审计）

### Profile 校验

✅ **已实现校验**:
1. required_items 必须在 catalog 中存在
2. optional_items 必须在 catalog 中存在
3. placement_overrides 中的 item 必须在 include 中
4. render_mode_overrides 中的 item 必须在 include 中
5. render_mode 必须为合法枚举值
6. per_item_max 中的 item 如不在 include 中会输出 warning

### Presets 校验

✅ **已实现校验**:
1. Preset ID 唯一性检查
2. Preset 引用的 items 必须在 catalog 中存在
3. Profile 引用的 presets 必须存在（若使用 use_presets）

---

## 6. 如何在 Prompt Engine 中使用

### Phase 1.2: Prompt 渲染引擎（骨架已接入，LLM 调用待接入）

```python
# 伪代码示例
from src.agent.context import (
    load_catalog,
    load_presets,
    load_profiles,
    validate_profiles,
    resolve_profile_items,
)

# 1. 加载配置
catalog = load_catalog()
presets = load_presets()
profiles = load_profiles()
validate_profiles(profiles, catalog, presets=presets)

# 2. 选择 profile
profile = profiles["chat.single_pass"]
item_ids = resolve_profile_items(profile, presets=presets)

# 3. 获取 ContextPack（已有）
ctx = await context_builder.build(req, plan)

# 4. 渲染（当前最小链路）
engine = PromptEngine(catalog=catalog, presets=presets, profiles=profiles)
messages, manifest = engine.render("chat.single_pass", plan, ctx)

# 5. 调用 LLM
llm_response = await llm_provider.call(messages)
```

### 渲染流程（Phase 1.2 设计）

```
1. 用 `resolve_profile_items` 展开 include（required/optional/use_presets）
2. 从 ctx.slots 提取 item 对应值，并应用 llm_exposure
3. 根据 placement_overrides 排序（按 prefix/middle/suffix 分组）
4. 根据 render_mode_overrides 选择渲染方式
   - raw: 原样输出
   - summary: 调用摘要函数
   - pinned: 高亮显示
   - structured: JSON/key-value 格式
5. 应用 budget.per_item_max 截断
6. 填充 templates（支持 Jinja2 变量替换）
7. 返回 messages + manifest
```

### 扩展点

- **自定义 render_mode**: 在 catalog 中扩展 render_mode 枚举
- **自定义 layout_policy**: 实现更复杂的布局策略（如动态优先级）
- **动态 profile 选择**: 根据 plan.task_type / pool_id 自动选择 profile
- **预算动态调整**: 根据 gate_hint.budget 调整 profile.budget

---

## 7. 验证结果

### ✅ 配置加载验证（tools/test_context_config.py）

```
✓ Catalog loaded: 22 items
  - 10 categories
  - 5 presets
  - 1 profile (chat.single_pass)

✓ Presets validation with catalog passed
✓ All profiles validation passed
✓ Correctly caught validation error for nonexistent item
✓ All tests passed!
```

### ✅ pytest 测试套件（14 tests, 100% pass）

| 测试用例 | 状态 |
|---------|------|
| test_catalog_loads_successfully | ✅ PASSED |
| test_catalog_item_validation | ✅ PASSED |
| test_presets_load_successfully | ✅ PASSED |
| test_presets_validate_with_catalog | ✅ PASSED |
| test_profiles_load_successfully | ✅ PASSED |
| test_profile_validation_with_catalog | ✅ PASSED |
| test_profile_fields_complete | ✅ PASSED |
| test_profile_validation_fails_on_unknown_item | ✅ PASSED |
| test_profile_validation_fails_on_placement_override_not_included | ✅ PASSED |
| test_profile_validation_fails_on_unknown_preset | ✅ PASSED |
| test_profile_resolves_items_from_presets | ✅ PASSED |
| test_load_profile_fails_on_invalid_render_mode | ✅ PASSED |
| test_catalog_has_future_items | ✅ PASSED |
| test_tool_results_raw_has_llm_exposure_never | ✅ PASSED |

### ✅ 现有测试不受影响

| 测试套件 | 状态 |
|---------|------|
| tests/test_agent_phase0.py (4 tests) | ✅ ALL PASSED |
| tests/test_agent_context_builder_phase1_1.py (6 tests) | ✅ ALL PASSED |
| tests/test_agent_hybrid_planner_phase1.py (6 tests) | ✅ ALL PASSED（兼容别名） |

---

## 8. 集成点

### AgentQueen 最小集成

```python
class AgentQueen:
    def __init__(self, ..., enable_catalog_loading: bool = True):
        # ...现有代码...
        
        # Phase 1.1: 加载 catalog / presets / profiles（可选）
        self._catalog: Optional[ContextCatalog] = None
        self._presets: Optional[ContextPresetsCollection] = None
        self._profiles: Dict[str, Any] = {}
        if enable_catalog_loading:
            self._load_catalog_and_profiles()
    
    def _load_catalog_and_profiles(self) -> None:
        """加载 catalog、presets、profiles 并做联动校验。"""
        try:
            self._catalog = load_catalog("config/context_catalog.yaml")
            logger.debug(f"AgentQueen loaded context catalog: {len(self._catalog.items)} items")
        except Exception as e:
            logger.warning(f"AgentQueen catalog loading failed (non-blocking): {e}")

        try:
            self._presets = load_presets("config/context_presets.yaml")
            logger.debug(f"AgentQueen loaded context presets: {len(self._presets.presets)} presets")
        except Exception as e:
            logger.warning(f"AgentQueen presets loading failed (non-blocking): {e}")

        try:
            self._profiles = load_profiles("config/agent/prompt_profiles")
            logger.debug(f"AgentQueen loaded prompt profiles: {len(self._profiles)} profiles")
            if self._catalog is not None:
                validate_profiles(self._profiles, self._catalog, presets=self._presets)
        except Exception as e:
            logger.warning(f"AgentQueen profiles loading failed (non-blocking): {e}")
```

**特点**:
- fail-open: 加载失败只输出 warning，不影响主流程
- 可观测: 输出 debug 日志，包含 catalog/presets/profiles 条目数
- 一致性: 启动时执行 profile + preset + catalog 联动校验
- 可选: 通过 `enable_catalog_loading=False` 关闭（用于测试或降级）

---

## 9. 下一步建议

### Phase 1.2: 实现 Prompt 渲染引擎

**任务清单**:
- [ ] 实现 `PromptRenderer` 类
  - [ ] 从 ContextPack 提取 items
  - [ ] 根据 placement_overrides 排序
  - [ ] 根据 render_mode_overrides 渲染
  - [ ] 应用 budget 截断
- [ ] 实现 `MessageComposer` 类
  - [ ] 支持 Jinja2 模板变量替换
  - [ ] 生成 LLM messages 格式
- [ ] 改造 ChatPool 接入 PromptRenderer
  - [ ] 读取 profile（如 "chat.single_pass"）
  - [ ] 调用 renderer.render(ctx)
  - [ ] LLMProvider.call(messages)
  - [ ] 返回 {"draft": ..., "meta": {...trace...}}

### Phase 1.3: 多 Pool 与蜂群

**任务清单**:
- [ ] 为 code/plan/creative pool 创建独立 profiles
- [ ] 实现 code_pool / plan_pool / creative_pool（不再 stub）
- [ ] 升级 Aggregator：多池评估 → 选优

### Phase 2: Context 完善

**任务清单**:
- [ ] 实现 PersonaProvider（读取 identity.core / persona.style）
- [ ] 实现 MemoryProvider（读取 memory.pinned_facts）
- [ ] 实现 KnowledgeProvider（读取 knowledge.snippets_raw）
- [ ] 实现 ToolResultsProvider（读取 tool_results.raw，过滤生成 llm_view）
- [ ] 实现 RuntimePolicyProvider（读取 policy.runtime_constraints）

---

## 10. 文件导航快速索引

### 配置文件
- [config/context_catalog.yaml](../config/context_catalog.yaml)
- [config/context_presets.yaml](../config/context_presets.yaml)
- [config/agent/prompt_profiles/chat_single_pass.yaml](../config/agent/prompt_profiles/chat_single_pass.yaml)

### 加载/校验模块
- [src/agent/context/catalog.py](../src/agent/context/catalog.py)
- [src/agent/context/profile.py](../src/agent/context/profile.py)
- [src/agent/context/presets.py](../src/agent/context/presets.py)
- [src/agent/context/__init__.py](../src/agent/context/__init__.py)

### 集成点
- [src/agent/queen.py](../src/agent/queen.py#L60-L90) - AgentQueen._load_catalog_and_profiles()

### 验证工具
- [tools/test_context_config.py](../tools/test_context_config.py)
- [tests/test_context_catalog_and_profile.py](../tests/test_context_catalog_and_profile.py)

---

## 附录：配置示例

### 最小 Profile 示例

```yaml
version: "0.1"
id: minimal
role: assistant
pool: chat
purpose: "最小可用示例"

include:
  required_items:
    - current_input.text

layout:
  layout_policy_id: "linear"
  placement_overrides:
    middle:
      - current_input.text

templates:
  system_template: "You are a helpful assistant."
  user_template: "{{ current_input.text }}"

output:
  format: text

budget:
  max_tokens: 1024
  truncate_policy: priority_then_window
```

### 使用 Presets 的 Profile 示例

```yaml
version: "0.1"
id: chat_with_identity
role: chat_assistant
pool: chat
purpose: "聊天 + 完整身份信息"

include:
  use_presets:
    - chat_base           # 引用 chat_base preset（包含 current_input.text 等）
    - full_identity       # 引用 full_identity preset（包含 identity.core 等）
  required_items:
    - session.metadata    # 额外必需项（当前实现字段）

layout:
  # ... 布局配置 ...

# ... 其他配置 ...
```

---

**总结**: Context Catalog & PromptProfile 骨架版已完整实现并验证通过。配置结构清晰、校验完备、集成无侵入、测试全覆盖。为 Phase 1.2 Prompt 渲染引擎奠定了坚实基础。✅

