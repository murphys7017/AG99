# Persona Format Current

记录当前 AstrBot 人格设定格式。

## 当前状态

当前系统中，人格的原始内容仍然是 legacy prompt 文本。

当前没有原生的 persona segments 存储格式。

当前系统做的是：

- 保留原始 `persona.prompt`
- 在 collect 阶段把 `persona.prompt` 解析为 `persona.segments`
- 同时生成给 Router 使用的 `persona.summary`
- 将三者作为事实放入 `ContextPack`
- Persona/Core 的目标投影决定是否可见；默认 Layout 在存在 `persona.segments` 时优先渲染 segments，只有没有 segments 时才回退到原始 `persona.prompt`

当前系统没有做：

- 原生以 YAML segments 存储 persona
- 原生以 XML 存储 persona
- 让 Collector 直接写最终 system prompt
- 让 Router 读取完整 persona segments

## 当前 persona 来源

当前 persona 的核心字段来自运行时 persona 数据。

主要字段：

- `prompt`
- `begin_dialogs`
- `tools`
- `skills`
- `custom_error_message`

运行时 collect 相关字段：

- `persona.prompt`
- `persona.segments`
- `persona.begin_dialogs`
- `persona.tools_whitelist`
- `persona.skills_whitelist`

## 当前原始 persona 格式

当前推荐理解为：

- 一个大段文本 prompt
- 使用 section 标题分块
- section 内部主要使用列表和短句

当前 parser 针对的格式形态：

```text
身份
- ...
- ...

核心人格
- ...
- ...

示例语气
「...」
「...」

对话风格
- ...

互动反应
被夸：
「...」

被取外号：
...

暧昧/关心：
「...」

渐进式理解
- ...

认知偏差（Rational Bias）
- ...

Memory Hooks（持续兴趣）
- ...

Personality Drives
1. ...
2. ...

Personality State Machine
Normal：...
Teaching：...

Relationship Layer
当前关系值：100（最高亲近）

行为特征：
- ...

Interaction Memory
- ...

稳定规则
- ...
```

## 当前支持的一级 section

当前 parser 可识别这些标题：

- `身份`
- `核心人格`
- `示例语气`
- `对话风格`
- `互动反应`
- `渐进式理解`
- `认知偏差（Rational Bias）`
- `Memory Hooks（持续兴趣）`
- `Personality Drives`
- `Personality State Machine`
- `Relationship Layer`
- `Interaction Memory`
- `稳定规则`

这些标题会映射为内部 key：

- `identity`
- `core_persona`
- `tone_examples`
- `dialogue_style`
- `interaction_reactions`
- `progressive_understanding`
- `rational_bias`
- `memory_hooks`
- `personality_drives`
- `personality_state_machine`
- `relationship_layer`
- `interaction_memory`
- `stable_rules`

## 当前支持的子结构

### 1. 普通列表

形态：

```text
- 内容
```

解析结果：

- 进入当前 section 的 `list[str]`

### 2. 示例语气

形态：

```text
「这逻辑明显不对吧。」
```

解析结果：

- 去掉 `「」`
- 进入对应 section 的 `list[str]`

### 3. 互动反应

当前支持的子标题：

- `被夸`
- `被取外号`
- `暧昧/关心`

映射结果：

- `praised`
- `nickname`
- `affection_or_care`

当前支持的写法：

```text
被夸：
「哼，这不是理所当然的吗。」
```

或：

```text
被取外号：
否认 → 转移话题。
```

### 4. Personality State Machine

当前支持的状态：

- `Normal`
- `Teaching`
- `Mocking`
- `Curious`
- `Tsundere`

当前支持的写法：

```text
Normal：默认理性 + 轻毒舌
Teaching：用户认真提问 → 更耐心解释
```

解析结果：

```python
{
    "normal": "...",
    "teaching": "...",
    "mocking": "...",
    "curious": "...",
    "tsundere": "...",
}
```

### 5. Relationship Layer

当前支持的结构：

- `当前关系值：100`
- `行为特征：`
- 后续列表项

解析结果：

```python
{
    "current_affinity": 100,
    "traits": [...],
}
```

## 当前 `persona.segments` 结构

当前 collect 阶段输出的 `persona.segments` 结构：

```python
{
    "identity": list[str],
    "core_persona": list[str],
    "tone_examples": list[str],
    "dialogue_style": list[str],
    "interaction_reactions": {
        "praised": list[str],
        "nickname": list[str],
        "affection_or_care": list[str],
    },
    "progressive_understanding": list[str],
    "rational_bias": list[str],
    "memory_hooks": list[str],
    "personality_drives": list[str],
    "personality_state_machine": {
        "normal": str,
        "teaching": str,
        "mocking": str,
        "curious": str,
        "tsundere": str,
    },
    "relationship_layer": {
        "current_affinity": int | None,
        "traits": list[str],
    },
    "interaction_memory": list[str],
    "stable_rules": list[str],
    "unparsed_sections": list[str],
}
```

## `unparsed_sections`

这是当前 parser 的兜底字段。

用途：

- 保存无法识别的 section 或行
- 保证 parser 不因局部格式变化直接失效
- 方便调试和后续补规则

当前行为：

- 无法识别的内容不会丢失
- 会落入 `unparsed_sections`

## 当前格式要求

为了让 parser 稳定工作，当前 persona prompt 最好满足：

- section 标题单独占一行
- 列表统一使用 `- `
- 互动反应子标题单独占一行
- 状态机每行一个状态
- 关系值写成 `当前关系值：数字`

## 当前边界

- 不建议现在把 persona 改成只有 XML
- 不建议现在直接删除 legacy `prompt`
- 不应由业务模块把 `persona.segments` 手工拼进 system prompt

原因：

- segments 已进入统一 Layout/Renderer，但原始存储仍是 legacy prompt
- 目标局部 system 指令属于 `PromptRenderProfile`，人格事实仍属于 Collector

## 当前链路位置

当前 persona format 的处理链路：

1. 运行时读取 persona
2. 收集 `persona.prompt`
3. 调用 legacy parser
4. 生成 `persona.segments`
5. 生成精简 `persona.summary`
6. 将结果放入 `ContextPack`
7. 按 Router、Core Planner、Persona、Core 做目标投影
8. Layout 优先渲染 segments，缺失时回退原始 prompt

## 当前结论

当前 AstrBot 的人格设定格式可以概括为：

- 原始输入仍然是分块式 legacy prompt 文本
- 系统会在 collect 阶段把它解析成结构化 `persona.segments`
- Persona 已消费结构化 segments；Router 只消费精简 summary，Core 默认排除人格
- 原始 persona 存储格式仍未迁移，Collector/parser 是兼容边界
