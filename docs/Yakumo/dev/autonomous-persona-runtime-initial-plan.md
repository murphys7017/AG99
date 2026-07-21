# 自主人格运行时初期计划草案

本文规划 Yakumo 如何从“能够主动投递消息”演进为“持续观察、谨慎判断、按需行动”的
自主人格运行时。它是初期设计草案，不代表当前代码已经实现；当前事实以源码、
`current-state.md` 和 `消息处理流程详解.md` 为准。

## 背景与参考原则

`kawayiYokami/astrbot_plugin_angel_heart` 证明了几项产品机制可以改善群聊参与体验：跨消息的
在场状态、确定性规则优先、轻量模型只做参与判断、回复与不回复使用不同冷却、突发消息合并、
失败时保持安静。Yakumo 学习这些原则，但不复制其插件架构、状态名称或内部实现。

Yakumo 的目标范围更大：群聊活动、私聊、Heartbeat、Cron、插件 Sensor、后台执行结果和
Memory 承诺都是世界观察来源。系统中心必须是通用 Personal Runtime，而不是某一种群聊
回复状态机。

## 当前基础

当前源码已经具备：

- `RuntimeObservation`：不可变系统事实，不伪装成用户消息。
- `PersonalRuntimeManager`：按 persona、audience 和 privacy scope 管理 session lease。
- `TurnExecutionScope`：持有单 turn 的异步任务。
- `Persona Expression`：唯一用户可见人格表达层。
- `InteractionOutputController`：统一文本输出、完成状态和可见记录。
- assistant-only Conversation / Memory history。
- 默认主动消息目标和 Adapter 主动消息能力校验。
- Cron 和插件主动文本的投递入口。

当前缺失的是位于 Observation 与 Action 之间的持续状态、策略和成本控制层。

## 目标

```text
World Observation
  -> Observation Inbox
  -> Deterministic Gate
  -> Personal Policy
  -> Action Coordinator
       -> observe / defer
       -> Persona Expression
       -> Core Planner / Execution Backend
  -> Output Runtime
  -> Completion Feedback
  -> Personal State / Memory / Usage Ledger
```

系统应做到：

- 没有用户消息时仍可被 Heartbeat、Cron 或插件事实唤醒。
- 多个短时间观察先合并，再进行一次判断。
- 可由代码判断的事实不调用模型。
- “不行动”和“继续观察”是一等正常结果。
- Personal Policy 只决定是否行动，不生成最终用户文案。
- 所有表达继续通过 Persona Expression 和 Output Runtime。
- 所有 Core 工作继续通过 Planner 和 Execution Boundary。
- 输出完成、失败和用户后续反应会反馈到持续状态。

## 非目标

初期不做：

- 不复制 AngelHeart 的 FrontDesk、Secretary、ConversationLedger 或 Prompt 模块。
- 不建立第二套 EventBus、Conversation、Memory、图片转述或主动任务管理器。
- 不通过修改 `event.is_at_or_wake_command` 间接唤醒主链。
- 不让 Heartbeat 每次 tick 都调用模型。
- 不在第一阶段允许自主执行 Core 工具。
- 不把 AG99live、Motion 或 Live2D 状态写进通用策略协议。

## 核心契约

### RuntimeObservation

继续作为“世界发生了什么”的唯一内部事实载体。后续允许的来源包括：

- `heartbeat`
- `conversation_activity`
- `scheduled_task`
- `execution_progress`
- `execution_completed`
- `memory_commitment_due`
- `plugin_sensor`
- `presence_changed`

Observation 不包含“应该回复”的决定。

### PersonalState

`PersonalState` 属于 `PersonalSessionRuntime`，跨 turn 持续存在，不放入单轮
`InteractionTurnState`。初期字段建议为：

```text
attention_state
availability_state
last_observation_at
last_user_activity_at
last_expression_at
reply_cooldown_until
no_action_cooldown_until
mute_until
pending_observation_count
daily_model_calls
daily_proactive_outputs
```

当前话题、关系和长期人格事实仍属于 Prompt / Conversation / Memory，不在 Runtime State
中建立副本。

### ObservationFeatures

确定性 Feature Builder 从 Observation Inbox 和规范上下文中生成：

```text
is_explicitly_summoned
is_follow_up_candidate
message_count
participant_count
echo_count
activity_density
seconds_since_user_activity
seconds_since_last_expression
has_pending_commitment
is_runtime_busy
is_quiet_hours
is_muted
budget_available
```

这些是事实，不是模型决策。

### PersonalPolicyDecision

初期策略输出保持极简：

```json
{
  "action": "ignore | observe | express | defer | execute",
  "reason": "简短稳定原因码",
  "reply_intent": "供 Persona Expression 使用的待表达材料",
  "task_intent": "供 Planner 使用的任务材料",
  "importance": 0.0,
  "defer_seconds": 0
}
```

约束：

- `ignore`：丢弃本次低价值观察，不调用 Persona 或 Core。
- `observe`：更新状态，保留观察，不产生输出。
- `express`：进入 Persona Expression。
- `defer`：将规范 Action Intent 延后，不保存模型私有上下文。
- `execute`：后续阶段才开放，进入 Core Planner。
- `reply_intent` 不是最终文案。
- 决策通过 OutputContract / tool call 生成，不手工解析自由文本 JSON。

### CompletionFeedback

输出或执行结束后形成反馈：

```text
action_id
delivery_status
execution_status
output_completed_at
failure_code
user_follow_up_observed
```

只有真实 completion 才更新 `last_expression_at` 和主动输出预算。

## Prompt 边界

Personal Policy 必须使用现有 Prompt 主链：

```text
Collectors
  -> canonical ContextPack
  -> personal_policy projection
  -> Personal Policy Render Profile
  -> Provider Renderer
```

Policy 初期可见内容：

- 简要 Persona 身份和行为边界。
- `PersonalState` 的只读投影。
- ObservationFeatures。
- 最近有限对话窗口和必要 Memory 摘要。
- 当前 Observation batch。

Policy 不接收：

- 完整工具 schema。
- Core Execution Ledger 全量细节。
- Motion / Live2D 等插件 effect schema。
- 已失败、已取消或过期的临时决策痕迹。

## 与现有 Router 的关系

Personal Policy 与 Router 独立：

- Personal Policy 判断世界观察是否需要转化为人格行动。
- Router 判断一条已进入对话主链的输入是否需要 Core 候选路径。
- Core Planner 判断具体任务是否值得执行，并构建 `CoreTaskSpec`。
- Persona Expression 决定最终怎么说。

四者共享 Prompt 收集的规范事实，但不共享模型决策或临时 Prompt。

## 实施阶段

### 阶段 1：契约与只读状态

实现 `PersonalState`、`ObservationFeatures`、`PersonalPolicyDecision` 和
`CompletionFeedback` 的类型边界。状态挂在 `PersonalSessionRuntime`，补充 diagnostics，
但不改变任何现有回复行为。

验收：

- 状态不写入 event extra 作为主存储。
- session runtime 释放和重建行为明确。
- 当前平台消息、插件和 Cron 行为不变。

### 阶段 2：Observation Inbox 与确定性 Gate

为每个 Personal Runtime 增加有界 Inbox 和 debounce/coalescing：

- 短时间连续观察合并为一个 batch。
- 同一来源的过期观察可被更新事实替换。
- 当前 turn 忙碌时延后，不创建平行 Persona task。
- quiet hours、mute、cooldown、预算和目标能力先由代码过滤。

验收：

- 高频观察不会线性增加任务和模型调用。
- Gate 的每次拒绝都有稳定 reason code。
- 不阻塞官方 EventBus / Pipeline。

### 阶段 3：影子 Personal Policy

增加 `personal_policy` Prompt target 和可配置小模型。Policy 只记录决策，不执行输出，先在
真实流量中对照人工预期。

验收：

- 模型只在 Gate 通过后调用。
- 输出严格符合 `PersonalPolicyDecision`。
- 影子模式不会修改 wake、Router、Core 或平台发送。
- diagnostics 能比较 ObservationFeatures、决策和后续真实用户行为。

### 阶段 4：单目标 Heartbeat Express

接入本地 Heartbeat Source。初期只允许 `ignore / observe / express / defer`，只对配置的默认
主动目标生效；`express` 通过 Persona Expression 和 Output Runtime 发送。

建议初期配置：

- enable
- interval
- provider_id
- quiet_hours
- reply_cooldown
- no_action_cooldown
- max_policy_calls_per_day
- max_proactive_outputs_per_day
- default_target

验收：

- 无新观察时零模型调用。
- 未配置目标、目标不可用或处于安静时段时零输出。
- 每次主动输出都有 action_id、决策原因和 completion feedback。
- 重启后的预算和冷却策略明确，不因状态丢失连续打扰。

### 阶段 5：环境对话观察

将经过官方过滤的非唤醒群聊活动作为 `conversation_activity` Observation。确定性 Feature
Builder 负责呼唤、连续追问候选、复读、消息密度和参与人数；Policy 决定是否进入
`express`，不直接改写原事件。

验收：

- 关闭功能时完全保持官方行为。
- 未批准的环境消息不会进入 Core。
- 同一批群聊活动最多形成一次 Policy 判断和一次表达。
- 明确呼唤仍保留现有低延迟入站路径。

### 阶段 6：Execute 与插件 Sensor

在前述阶段稳定后开放 `execute`，并提供插件提交结构化 Observation 的公共 API：

```text
Personal Policy execute
  -> Core Planner
  -> Execution Backend
  -> result / error material
  -> Persona Expression
  -> Output Runtime
```

插件只提交事实，不获得绕过 Policy、Persona 或 Output 的通用用户文本发送权。

## 首期建议范围

第一轮只实施阶段 1 和阶段 2：

- 建立持续状态和契约。
- 建立 Inbox、合并和确定性 Gate。
- 只输出 diagnostics，不调用新模型、不改变发送行为。

这能先验证状态所有权和并发边界，避免同时引入 Heartbeat、模型策略和主动输出后难以定位
问题。阶段 3 的影子 Policy 只有在前两阶段日志稳定后再开始。

## 风险与约束

- **打扰风险**：主动输出默认关闭，必须有目标、预算、冷却和安静时段。
- **并发风险**：所有观察和 Action 必须归属 Personal Runtime，不创建旁路 task owner。
- **成本风险**：Heartbeat tick 不等于模型调用；所有后台调用经过 Budget Gate。
- **上下文风险**：不建立私有 ConversationLedger，不重写官方历史。
- **重复输出风险**：Action 继续使用 turn 级最终输出仲裁和 completion contract。
- **隐私风险**：Observation 按 audience 和 privacy scope 隔离，插件 Sensor 必须声明目标。
- **状态膨胀**：PersonalState 只保存运行控制事实，语义记忆交给 Memory。

## 开放问题

- `PersonalState` 哪些字段需要持久化，哪些只保留进程内状态。
- Heartbeat 是每 persona、每 audience，还是仅对配置目标创建实例。
- quiet hours 使用配置时区还是目标会话时区。
- `defer` 使用 Cron 持久化还是 Personal Runtime 内部短期定时器。
- 环境群聊 Observation 应在官方 Pipeline 的哪个只读阶段形成。
- Policy 的 `importance` 是否保留连续数值，还是改为固定等级。

这些问题应在阶段 1 开工前形成明确决策，不通过实现中的默认值隐式决定。
