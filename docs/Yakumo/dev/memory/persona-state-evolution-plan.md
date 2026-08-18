# PersonaState 自动演进设计

## 目标

`PersonaState` 描述 Personal 与当前用户长期互动形成的动态关系和表达偏好，不修改静态 Persona，
也不替代 Conversation、Short-Term Memory 或长期事实记忆。

第一版必须满足：默认关闭、只处理 USER 作用域、完全后台运行、更新幅度受限、每次变化可审计且可回滚。

## 冻结边界

1. 只创建 `ScopeType.USER` 状态。GROUP/GLOBAL 记忆可以作为事实被 Personal 消费，但不能形成“整个群共享一份人格关系状态”。
2. 只在成功 consolidation 后评估，输入使用持久化的 `SessionInsight`、`Experience` 和当前 `PersonaState`，不读取本轮原始消息重新推断。
3. 同时满足 `memory.persona.enabled=true` 和 `memory.jobs.persona_reflection_enabled=true` 才允许提交后台任务；`memory.injection.persona_state` 只控制 Prompt 可见性，不能反向启用写入。
4. Reflection 通过 `MemoryJobScheduler` 按 USER scope 串行和去重，不阻塞当前回复、Recall 或 Core。
5. 静态 Persona 永远不被 Memory 写回。PersonaState 只作为 Persona 与 Personal Policy 的动态只读材料，Router、Planner 和 Core 不可见。

## 状态语义

五个字段统一为 `[0, 1]`：

- `familiarity`：互动熟悉程度。
- `trust`：关系中可依赖、可坦诚的程度。
- `warmth`：适合采用的情感温度。
- `formality_preference`：用户偏好的正式程度，越高越正式。
- `directness_preference`：用户偏好的直接程度，越高越直接。

“没有状态”表示尚无足够证据，不等价于五项都是 `0`。首次评估使用内部中性基线：
`familiarity=0`，其余字段为 `0.5`；只有 analyzer 明确给出高置信证据时才创建状态。

## Analyzer 契约

新增 `persona_reflect_v1`，归入独立 `persona_reflection` stage。输入只包含语义字段：

- 当前 PersonaState 的五个数值；没有状态时使用中性基线。
- 最新 SessionInsight 的 topic/progress/summary。
- 本次 consolidation 产生的 Experience category/summary/detail/importance/confidence。
- 静态 Persona ID 仅作为状态归属校验，不进入普通语义正文。

输出：

```json
{
  "should_update": true,
  "confidence": 0.82,
  "reason": "The user repeatedly prefers concise and direct responses.",
  "deltas": {
    "familiarity": 0.04,
    "trust": 0.02,
    "warmth": 0.01,
    "formality_preference": -0.03,
    "directness_preference": 0.06
  }
}
```

服务端必须重新验证 JSON，不信任模型边界：

- `confidence < 0.7` 或 `should_update=false` 时不写入。
- 单字段单次变化限制为 `[-0.08, 0.08]`。
- 应用后统一 clamp 到 `[0, 1]`。
- 所有 delta 归零时不创建状态或日志。
- `reason` 只进入审计日志，不进入普通 Prompt。

## 触发与调度

触发点位于 USER scope consolidation 成功之后、长期 promotion 之前。满足以下条件才提交
`persona_reflection` Job：

- PersonaState 与 reflection job 两个开关均开启。
- 本次产生至少一个 Experience。
- 距离当前状态 `updated_at` 已达到 `reflection_interval_hours`；首次状态不受该间隔限制。
- scope 是 USER 且 canonical user 已解析。

Job 的 scope key 使用 canonical user，dedupe key 使用 user + consolidation batch。失败采用 fail-open：
只记录诊断，不回滚已经成功的 Short-Term、Experience 或 Long-Term 数据，也不影响用户回复。

## 原子写入与回滚

新增 `PersonaStateService`，禁止调用方分别执行 `upsert_persona_state()` 和
`save_persona_evolution_log()`。Store 必须提供一个事务边界，在同一事务中：

1. 锁定或重新读取当前 USER PersonaState。
2. 校验本次 reflection 基于的旧状态仍然有效。
3. 写入新 PersonaState。
4. 写入包含 `before_state`、`after_state`、reason 和 source refs 的 EvolutionLog。

回滚同样走 Store 原子事务：按 evolution log 恢复 `before_state`；首次创建的状态回滚时删除该
scope state。回滚行为追加新的审计日志，不删除历史日志。

## Prompt 边界

现有 `MemoryCollector` 已默认只投影五个语义字段。`state_id`、`scope_id`、`persona_id`、
`updated_at` 继续只在 `include_debug_fields=true` 时出现。

PersonaState 只允许进入：

- `PromptTarget.PERSONA`
- `PromptTarget.PERSONAL_POLICY`

不得进入 Router、Core Planner 或 Core，不得改变工具权限、插件挂载或 Core 决策。

## 实施批次

### 6A：持久化与服务边界（已完成）

- 新增 `PersonaStateService`。
- Persona reflection analyzer 与 `persona_reflection` 后台 Job 已接入 consolidation 成功后的 USER scope 链路。
- 新增原子 apply、读取 evolution log 和 rollback Store API。
- 定义中性基线、delta 限幅和 interval 判断。
- 不接 Provider，不接自动调度。

### 6B：Analyzer 与后台 Job（已完成）

- 增加 `persona_reflect_v1` prompt、schema contract 和 analysis stage。
- `MemoryJobScheduler` 增加 `persona_reflection` kind。
- USER consolidation 成功后按开关和 interval 提交。
- 默认配置保持全部关闭。

### 6C：管理与验收

- 提供只读状态、演进日志和显式 rollback 管理入口。
- 记录 submitted/skipped/applied/rejected/failed/rolled_back 诊断。
- 使用一个私聊用户进行真实运行观察，再决定是否开启 `memory.injection.persona_state`。

## 最小验证

只保留公开边界验证：

1. 开关关闭时不提交 reflection。
2. GROUP scope 不创建 PersonaState。
3. 高置信 delta 被限幅并原子写入 state + log。
4. analyzer 失败不影响 consolidation 结果。
5. rollback 恢复 before_state；首次创建可回滚为无状态。
6. Prompt 默认没有技术 ID，且 Router/Core 看不到 PersonaState。

## 非目标

- 不让模型改写静态 Persona 文本。
- 不从单条消息即时调整关系分数。
- 不让 GROUP/GLOBAL 共享 PersonaState。
- 不实现随时间自动衰减。
- 不默认开启自动演进或 Prompt 注入。
