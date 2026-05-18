# Memory Config

本文件定义 AstrBot memory 系统第一版配置。

第一版配置文件位置：

- `data/memory/config.yaml`

第一版目标：

- 支持 memory 系统独立运行
- 不立即并入 AstrBot 统一配置系统
- 后续可迁移到 AstrBot 正式配置

## 1. 根配置结构

第一版建议结构：

```yaml
enabled: true

storage:
  sqlite_path: data/memory/memory.db
  docs_root: data/memory/long_term
  projections_root: data/memory/projections

short_term:
  enabled: true
  recent_turns_window: 8
  update_interval_turns: 6
  update_min_chars: 0

injection:
  enabled: true
  topic_state: true
  short_term: true
  experiences:
    enabled: false
    top_k: 0
  long_term:
    enabled: true
    top_k: 3
    query_required: true
  persona_state: false
  include_debug_fields: false

consolidation:
  enabled: true
  min_short_term_updates: 12
  batch_window_hours: 6

long_term:
  enabled: true
  min_experience_importance: 0.7

vector_index:
  enabled: true
  provider: simple
  experience_top_k: 5
  long_term_top_k: 5

persona:
  enabled: false
  reflection_interval_hours: 24

jobs:
  consolidation_enabled: true
  long_term_enabled: true
  persona_reflection_enabled: false
```

## 2. 顶层字段

### 2.1 `enabled`

类型：

- `bool`

作用：

- 控制 memory 系统总开关

第一版默认值：

- `true`

## 3. `storage`

职责：

- 定义 memory 数据根路径
- 定义 sqlite 与文档目录位置

### 3.1 `storage.sqlite_path`

类型：

- `str`

作用：

- memory sqlite 数据库文件路径

默认值：

- `data/memory/memory.db`

### 3.2 `storage.docs_root`

类型：

- `str`

作用：

- 长期记忆文档根目录

默认值：

- `data/memory/long_term`

### 3.3 `storage.projections_root`

类型：

- `str`

作用：

- 经历时间线等审阅投影目录

默认值：

- `data/memory/projections`

## 4. `short_term`

职责：

- 控制短期层即时更新行为

### 4.1 `short_term.enabled`

类型：

- `bool`

作用：

- 是否启用 `TopicState` 与 `ShortTermMemory`

默认值：

- `true`

### 4.2 `short_term.recent_turns_window`

类型：

- `int`

作用：

- 更新短期层时最多读取多少轮最近历史

默认值：

- `8`

### 4.3 `short_term.update_interval_turns`

类型：

- `int`

作用：

- 冷启动后，每累计多少个新 turn 才再次运行短期分析。

默认值：

- `6`

说明：

- `TurnRecord` 仍然每轮写入。
- 该配置只控制 `TopicState` / `ShortTermMemory` 的分析频率。

### 4.4 `short_term.update_min_chars`

类型：

- `int`

作用：

- 自上次短期分析后，累计用户与助手文本达到多少字符也可触发短期分析。

默认值：

- `0`

说明：

- `0` 表示只按 turn 数触发。
- 统计范围不包含上次已经分析过的 turn。

## 5. `consolidation`

职责：

- 控制中期抽象阶段

### 5.1 `consolidation.enabled`

类型：

- `bool`

作用：

- 是否启用 `SessionInsight` / `Experience` 批量抽象

默认值：

- `true`

### 5.2 `consolidation.min_short_term_updates`

类型：

- `int`

作用：

- 当前实现按最新 `SessionInsight.window_end_at` 之后的新 raw turn 数触发 consolidation

默认值：

- `12`

说明：

- 字段名保留为 `min_short_term_updates` 以兼容既有配置。
- 短期分析降频后，该阈值不等于短期分析实际运行次数。

### 5.3 `consolidation.batch_window_hours`

类型：

- `int`

作用：

- consolidation 的时间窗口参考值

默认值：

- `6`

## 6. `long_term`

职责：

- 控制长期记忆对象沉淀

### 6.1 `long_term.enabled`

类型：

- `bool`

作用：

- 是否启用长期记忆对象生成

默认值：

- `true`

### 6.2 `long_term.min_experience_importance`

类型：

- `float`

作用：

- `Experience` 提升为 `LongTermMemory` 的最低重要性阈值

默认值：

- `0.7`

## 7. `vector_index`

职责：

- 控制第一版简单向量检索

### 7.1 `vector_index.enabled`

类型：

- `bool`

作用：

- 是否启用向量索引

默认值：

- `true`

### 7.2 `vector_index.provider`

类型：

- `str`

作用：

- 向量索引实现标识

第一版建议值：

- `simple`

说明：

- 第一版只需要简单实现
- 这里先预留 provider 名称，后续再扩展

### 7.3 `vector_index.experience_top_k`

类型：

- `int`

作用：

- 请求前默认召回多少条 `Experience`

默认值：

- `5`

### 7.4 `vector_index.long_term_top_k`

类型：

- `int`

作用：

- 请求前默认召回多少条 `LongTermMemory`

默认值：

- `5`

## 8. `persona`

职责：

- 控制动态人格状态更新

### 8.1 `persona.enabled`

类型：

- `bool`

作用：

- 是否启用 `PersonaState` 更新

第一版默认值：

- `false`

说明：

- 第一版先打通 memory 主链路
- 人格状态建议后置

### 8.2 `persona.reflection_interval_hours`

类型：

- `int`

作用：

- 人格状态更新任务的默认间隔

默认值：

- `24`

## 9. `jobs`

职责：

- 控制各类 memory 后台任务是否启用

### 9.1 `jobs.consolidation_enabled`

类型：

- `bool`

作用：

- 是否运行中期抽象任务

默认值：

- `true`

### 9.2 `jobs.long_term_enabled`

类型：

- `bool`

作用：

- 是否运行长期记忆沉淀任务

默认值：

- `true`

### 9.3 `jobs.persona_reflection_enabled`

类型：

- `bool`

作用：

- 是否运行人格状态更新任务

默认值：

- `false`

## 10. 第一版必须支持的配置

第一版最低要求：

- `enabled`
- `storage.sqlite_path`
- `storage.docs_root`
- `storage.projections_root`
- `short_term.recent_turns_window`
- `short_term.update_interval_turns`
- `short_term.update_min_chars`
- `injection.enabled`
- `consolidation.min_short_term_updates`
- `long_term.min_experience_importance`
- `vector_index.enabled`
- `vector_index.experience_top_k`
- `vector_index.long_term_top_k`

## 10.5 `injection`

职责：

- 控制 Prompt System 消费 `MemorySnapshot` 时的轻量注入策略。
- 不改变 `MemoryService.get_snapshot(...)` 的默认完整只读出口语义。

### 10.5.1 `injection.enabled`

类型：

- `bool`

作用：

- 是否向 prompt 注入 memory slot。

默认值：

- `true`

### 10.5.2 `injection.topic_state`

类型：

- `bool`

作用：

- 是否注入 `memory.topic_state`。

默认值：

- `true`

### 10.5.3 `injection.short_term`

类型：

- `bool`

作用：

- 是否注入 `memory.short_term`。

默认值：

- `true`

### 10.5.4 `injection.experiences`

类型：

- `enabled: bool`
- `top_k: int`

作用：

- 控制 `memory.experiences` 是否进入 prompt 以及最多注入多少条。

默认值：

- `enabled: false`
- `top_k: 0`

### 10.5.5 `injection.long_term`

类型：

- `enabled: bool`
- `top_k: int`
- `query_required: bool`

作用：

- 控制 `memory.long_term_memories` 是否进入 prompt、最多读取多少条，以及是否要求当前请求提供 query。

默认值：

- `enabled: true`
- `top_k: 3`
- `query_required: true`

### 10.5.6 `injection.persona_state`

类型：

- `bool`

作用：

- 是否注入 `memory.persona_state`。

默认值：

- `false`

### 10.5.7 `injection.include_debug_fields`

类型：

- `bool`

作用：

- 是否在 prompt slot 中保留 ID、source refs、时间戳等工程字段。

默认值：

- `false`

## 11. 第一版不建议先放进去的配置

当前建议后置：

- 图数据库连接配置
- 多 provider embedding 路由
- 复杂人格衰减策略参数
- 多级 memory selector 策略配置
- 高级 rerank / recall planner 配置

## 12. 目录默认布局

第一版建议默认布局：

- `data/memory/config.yaml`
- `data/memory/memory.db`
- `data/memory/long_term/`
- `data/memory/projections/`

## 13. 当前结论

第一版 memory 配置应遵循：

- 独立 YAML 文件
- 独立数据根目录
- 配置只覆盖第一版实际会用到的能力
- 后续再迁移到 AstrBot 统一配置系统
