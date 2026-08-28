# AG99 人格架构收口与迁移计划

## 文档状态

- **状态**：方案评审修订稿（2026-08-28）
- **适用项目**：AG99（基于 AstrBot 的持续演进版本）
- **当前阶段**：已纳入当前代码基线；本文档只列出尚未完成的边界收口，不把已完成工作重复列为重构任务
- **风险等级**：高。该方案会影响 Interaction、Prompt、Memory、Personal Runtime 和日志协议，但第一阶段只允许做文档与观测准备
- **提交策略**：方案评审已通过；后续按 Phase 单独提交，每个提交保持可回滚和可验证

这份文档解决的不是“再写一版人格提示词”，而是收口人格相关职责。当前人格能力已经具备较完整的运行链路，但静态人格、关系状态、运行控制、模型决策和最终表达仍分散在多个模块中，导致命名重叠、调用链难以解释、日志难以归因，也使性能优化容易变成局部打补丁。

## 当前基线状态

本计划必须建立在当前实现之上，而不是假设所有边界都尚未存在：

| 状态 | 当前结论 |
| --- | --- |
| 已完成 | Persona Expression 已是 visible-reply 的统一入口；Router、Core Planner、Personal Policy 已使用结构化决策并禁止生成用户台词；普通消息中的 Personal/Router 并行、Core-final 回到 Persona、Delivery Receipt 驱动冷却和额度等主语义已经存在 |
| 已完成 | PersonalState 已区分进程态快照与 `PersonalPersistentState`，并由 `PersonalStateRepository` 持久化最小控制字段；Heartbeat/idle initiation 的安静结果已经不再逐次以 INFO 输出 |
| 部分完成 | 静态 Persona、Memory PersonaState、Personal Runtime 控制状态已经有独立代码位置，但领域命名、作用域和 Prompt 槽位仍未完全统一 |
| 部分完成 | Prompt target、ContextPack single-flight、插件 enrichment、主动 Observation Inbox 和 Wake Scheduler 已存在，但仍需要把数据所有权和失败语义写成稳定契约 |
| 尚未完成 | 统一的领域术语、无动作结果枚举、idle initiation 的 attempt/observation/batch 关联指标、PersonaCollector 失败降级策略，以及旧入口的清理和退出条件 |

因此，Phase 1 至 Phase 4 不是从零实现，而是围绕现有实现做类型化、命名收口、失败语义审计和兼容迁移。

## 1. 目标与非目标

### 1.1 目标

本次重构计划的目标是：

1. 明确“人格是什么”和“本轮是否表达”是两个不同问题。
2. 将稳定人格、关系状态、运行控制、决策和可见表达拆成稳定边界。
3. 让 Router、Core Planner、Personal Policy 只做决策，不生成用户可见台词。
4. 让所有需要人格改写的语义文本继续经过唯一的 Persona Expression 入口；direct/media 等兼容输出保持明确的旁路语义。
5. 让普通对话、群聊候选、主动观察、插件/Core 委派都复用同一套人格表达协议。
6. 让 Prompt target 只决定“某个目标可以看到哪些事实以及要遵守什么输出约束”，不在不同 Agent 内部重复拼接人格。
7. 让日志能够回答：哪一层做了什么、等待了多久、为什么没有动作、为什么没有发送、是否重复发送。
8. 在不阻塞普通回复的前提下，减少不必要的等待、重复 Provider 调用和高频 idle initiation 日志。
9. 保留现有外部插件、平台、配置和数据兼容性，并为每个迁移阶段提供回滚点。

### 1.2 非目标

本计划不包含：

- 立即重写全部人格提示词或重新训练模型。
- 立即把 AG99 拆成多进程或分布式服务。
- 重新引入 LLM Selector 来决定 Prompt target。
- 让 Memory 在 Prompt collect 阶段写入状态。
- 让 Router、Planner 或 Policy 直接发送消息、执行工具或生成用户台词。
- 一次性删除所有旧字段和旧入口。
- 为某个单独插件、某个平台或某个 Provider 添加专用分支。

## 2. 当前问题诊断

### 2.1 人格职责分散

当前链路中的人格相关行为大致分布如下：

| 领域 | 当前主要位置 | 现状问题 |
| --- | --- | --- |
| 静态人格 | `astrbot/core/prompt/collectors/persona_collector.py`、`persona_segments.py` | 负责内容收集，但容易被误认为拥有全部人格状态 |
| 关系与长期状态 | `astrbot/core/memory/persona_state_service.py`、`memory.persona_state` | 与静态 Persona、Conversation、PersonalState 的命名边界不够清楚 |
| 本轮运行状态 | `astrbot/core/interaction/personal_state.py`、`personal_runtime.py` | 同时承载忙闲、冷却、观察、表达和诊断字段，容易形成大状态对象 |
| 参与/路由决策 | `router_agent.py` | 负责是否参与，但历史上容易与“人格表达”概念混淆 |
| Core 是否执行 | `core_planner.py` | 负责执行计划，不应拥有可见表达权限 |
| 主动观察决策 | `personal_policy.py`、`personal_gate.py` | 负责是否形成行动意图，但必须与普通回复人格表达隔离 |
| 用户可见表达 | `expression_agent.py`、`persona_runtime.py` | 已经接近统一入口，但请求字段和调用来源仍较平铺 |
| 输出与送达 | `output_controller.py`、`turn_delivery_coordinator` 相关链路 | 负责物化和送达，不应重新解释人格语义 |

### 2.2 “人格”与“控制”混在一起

以下信息都可能被称为“人格状态”，但生命周期完全不同：

- “我是谁、如何说话、哪些表达规则稳定存在”是配置级事实。
- “我和当前用户熟悉程度如何”是关系级状态。
- “我当前是否忙、是否在冷却、今天主动表达额度是否用完”是运行控制。
- “这一轮是否应该参与”是决策结果。
- “这一轮最终说了什么”是表达结果和送达事实。

如果这些信息继续放在同一对象或同一 Prompt 片段中，后续会出现三类风险：

1. 一次短期冷却变化被误认为人格变化。
2. Router/Policy 的无动作结果被误认为 Persona 没有回复能力。
3. 已生成但未送达的文本被当成“最近表达”，造成错误去重和冷却。

### 2.3 无动作回复与慢响应的共同根因

“无动作回复”和“响应慢”并不一定是同一个 bug，但都容易由边界不清放大：

- Personal 与 Router/Planner 的启动时机不透明，无法判断是在并行等待还是串行等待。
- Persona 请求可能携带过多决策字段，导致表达模型重复承担路由工作。
- 插件 enrichment、Core 材料和表达材料没有明确的 best-effort / wait-complete 策略时，Persona 可能被不必要地阻塞。
- idle initiation 的调度检查、排队、合并、唤醒和无新事实如果没有统一关联，会让日志看起来像频繁推送，掩盖真正的发送次数；当前日志级别已完成初步降噪，剩余问题是关联和聚合。

### 2.4 日志当前最需要澄清的事实

对每个主动观察周期，必须区分：

```text
事实进入 Inbox
  -> batch 被合并/保留
  -> Gate 评估
  -> Policy 形成 ActionIntent
  -> Output 获得发送许可
  -> 平台送达成功
```

其中只有最后一步代表用户真正看到消息。idle initiation 的调度检查最多只能表示“提交了一次内部唤醒/检查请求”，不能被记录成“已推送”。后续实现应继续采用聚合诊断，并对空 Inbox、重复 revision 和未到期 wake deadline 使用计数或采样日志。

## 3. 目标架构

### 3.1 五层人格模型

```text
PersonaDefinition
    + PersonaRelationshipState
    + MemorySnapshot
    + RuntimeControlSnapshot
    + CurrentInteractionMaterial
    -> EffectivePersonaContext
         ├─ Ordinary turn: Personal Expression branch
         │       └─ PersonaExpression -> Output Controller -> Delivery Receipt
         └─ Control branch: Router -> Core Planner -> Core (仅 hybrid/execute)
                                └─ Core material -> PersonaExpression -> Delivery Receipt

Observation branch:
ObservationBatch -> Gate -> Personal Policy -> PersonalActionIntent
                 -> PersonaExpression -> Output Controller -> Delivery Receipt
```

五层职责固定如下：

1. **PersonaDefinition**：稳定的人格定义，回答“我是谁、我应该遵守什么稳定表达规则”。
2. **PersonaRelationshipState**：按用户、群体或隐私作用域隔离的关系状态，回答“我和当前对象处于什么关系状态”。
3. **RuntimeControlState**：进程运行和主动表达控制，回答“现在是否允许继续做某件事”。
4. **Decision Plane**：Router、Core Planner、Personal Policy 等决策组件，回答“这一轮是否参与、是否执行、是否形成行动意图”。
5. **PersonaExpression**：唯一的可见自然语言表达组件，回答“在已获准表达的前提下，应该如何说”。

这五层不是五个必须立即新增的类，而是必须先固定的所有权边界。迁移初期可以由现有类提供适配，但禁止继续扩大旧类的职责。

### 3.2 有效人格上下文

建议以现有 `PersonalStateSnapshot`、Memory snapshot 和 ContextPack 为来源，逐步形成一个只读、可追踪的领域快照；不应在迁移初期再创建一套平行的运行状态对象：

```python
EffectivePersonaContext(
    definition: PersonaDefinition,
    relationship: PersonaRelationshipState,
    memory: MemorySnapshot,
    runtime: RuntimeControlSnapshot,  # 初期由 PersonalStateSnapshot 适配提供
    interaction: InteractionPersonaMaterial,
    provenance: PersonaContextProvenance,
)
```

约束：

- `definition` 不得被本轮模型调用原地修改。
- `relationship` 只能由关系状态服务按作用域读取和提交。
- `runtime` 只提供当前轮所需的只读控制快照；锁、task、provider 等活对象不进入 Prompt。
- `memory` 是只读召回结果，不允许 Persona 直接写入 Memory。
- `interaction` 只保存本轮事实和表达材料，不保存决策模型的隐式结论。
- `provenance` 只用于诊断和内部追踪，不进入普通用户 Prompt。

### 3.3 决策平面与表达平面

决策平面只输出结构化结果：

```text
Router       -> RouteDecision
Core Planner -> CoreExecutionDecision
Policy       -> PersonalActionIntent
```

表达平面只接收材料和约束：

```text
PersonaExpressionRequest -> PersonaExpressionResult
```

表达平面不得再次判断是否应该参与、是否应该调用工具、是否应该静默。普通 turn 中 Personal Expression 与 Router 可以并行启动；只有 Core-final 和主动 Observation 按各自上游结果进入表达平面。若上游没有表达许可，表达组件不应被调用；若上游已经获得表达许可但材料为空，应返回明确的结构化空结果，而不是生成“无动作回复”。

## 4. 领域模型建议

### 4.1 `PersonaDefinition`

稳定字段建议包括：

- `persona_id`
- `display_name`
- `system_segments`
- `begin_dialogs`
- `style_rules`
- `safety_rules`
- `expression_capabilities`（仅表达效果/语音/动作提示，不代表业务工具权限）
- `definition_version`

不应包含：冷却时间、当前忙闲、每日额度、最近一条回复、观察 Inbox、当前 turn id、Provider task、业务工具权限或 effect 执行结果。

来源优先使用现有 `PersonaCollector` 和 `persona_segments.py`，先统一命名，再决定是否物理迁移文件。

### 4.2 `PersonaRelationshipState`

建议以完整作用域键隔离：

```text
persona_id + audience_key + privacy_scope
```

必要时再加入平台或配置作用域，但不能默认把 `conversation_id` 当作长期人格状态的唯一主键。

可逐步纳入：

- 熟悉度、信任度、亲密度等关系量
- 称呼与正式程度偏好
- 最近关系事件摘要
- 关系状态版本和更新时间

关系状态不应直接覆盖静态 Prompt，而应在 `EffectivePersonaContext` 中作为受控动态材料提供。

### 4.3 `RuntimeControlState`

建议拆成“持久控制字段”和“进程运行字段”：

持久控制字段：

- 最近一次确认送达的表达指纹
- 自主表达冷却截止时间
- 静音/禁用状态
- 每日主动表达用量
- 延迟唤醒截止时间

进程运行字段：

- 当前 lease / turn / task
- busy 状态
- Inbox 与 retained batch
- 当前 deadline budget
- pending 输出 reservation
- 诊断计数器

持久化继续通过窄化的 `PersonalStateRepository` 完成，保存失败应降级为进程态并记录诊断，不阻塞 Core 关闭。

### 4.4 `PersonaExpressionRequest`

当前请求字段较平铺，建议引入分组结构：

```python
PersonaExpressionRequest(
    material=ExpressionMaterial(
        source_text=None,
        immediate_reply=None,
        delegated_task_summary=None,
        observed_text=None,
        total_text=None,
        pending_text=None,
    ),
    intent=ExpressionIntent(
        kind="reply|follow_up|proactive|interjection|error",
        source="user|plugin|core|policy|observation",
        action_id=None,
    ),
    continuity=ExpressionContinuity(
        preserve_facts=True,
        avoid_previous_reply=True,
        previous_delivery_fingerprint=None,
    ),
    constraints=ExpressionConstraints(
        short_reply=False,
        allow_empty=False,
        output_mode="persona|direct|media",
        effect_policy=None,
    ),
)
```

迁移要求：

- 旧平铺字段保留一个适配层，先转换成新结构。
- 新代码只读取分组字段。
- 适配层必须记录 `request_schema_version`，便于定位仍未迁移的调用方。
- `allow_empty` 只表示协议是否允许空结果，不得用来掩盖上游没有表达许可或没有材料。

### 4.5 无动作结果

“无动作”必须拆成可观察的终态，不能把所有空字符串都归为同一类：

| 结果 | 含义 |
| --- | --- |
| `not_admitted` | 上游没有授予表达资格，Persona Expression 未调用 |
| `no_material` | 已获准表达，但没有可表达材料 |
| `intentional_empty` | 当前协议允许空结果，例如可跳过的 stream interjection |
| `suppressed_duplicate` | 生成前被最近送达表达指纹抑制 |
| `expression_failed` | Persona Expression 生成失败 |
| `delivery_failed` | 已生成或已预留，但平台送达失败 |
| `policy_ignore` | Personal Policy 明确选择 ignore/observe |

`allow_empty=True` 只能产生 `intentional_empty`，不能把 `not_admitted` 或 `no_material` 转换成成功回复。

### 4.6 输出结果和送达事实

建议明确区分：

```text
PersonaExpressionResult
  -> OutputArtifact
  -> DeliveryAttempt
  -> DeliveryReceipt
```

只有 `DeliveryReceipt(status="delivered")` 才能：

- 更新最近表达指纹
- 启动自主表达冷却
- 在带 `action_id` 的主动行动中消耗主动表达额度
- 写入 assistant-only 历史（适用时）

生成成功、获得发送许可、调用平台发送和平台送达成功不能使用同一个状态名。

## 5. Prompt target 边界

Prompt 管线继续遵循：

```text
Collectors -> ContextPack -> target projection -> Render Profile -> Layout -> Provider Renderer
```

目标边界建议固定为：

| Target | 可以看到 | 不可以看到/执行 |
| --- | --- | --- |
| `persona_expression` | PersonaDefinition、关系摘要、必要 MemorySnapshot、当前表达材料、表达约束、已授权 effect schema | Core capability、工具执行细节、Router/Planner/Policy 的内部推理、原始诊断 provenance |
| `router` | 当前输入、最小会话事实、唤醒/续接资格、必要历史摘要 | Persona Expression prompt、插件扩展、工具、effect、Core 执行能力 |
| `core_planner` | 当前输入、任务说明、执行历史摘要、能力可用性、路由结果 | 用户可见台词、Persona Expression 私有规则、Policy 内部状态 |
| `personal_policy` | 结构化 ObservationBatch、控制快照、最近表达时间/是否存在的摘要、时间/预算条件 | 原始表达指纹、工具、Skills、知识库、effect 执行、原始平台事件、可见回复文本生成 |
| `native_core` | 完整 CoreExecutionSpec 和已装配 ToolSet | 其他 target 的隐藏材料和未授权插件扩展 |

所有 target 都必须通过显式 projection 获得视图；禁止调用方直接从共享 `ContextPack` 拼 Prompt。

## 6. 四条主流程

### 6.1 普通私聊/明确唤醒消息

```text
官方 EventBus / Pipeline
  -> InteractionTurnState
  -> 基础 ContextPack
  -> Personal Runtime lease
  -> Personal Expression 与 Router 并行
  -> Personal 获得输出 reservation 后直接表达
  -> Router 决定 persona / hybrid / silent
  -> hybrid 时 Core Planner / Core 执行
  -> Core 结果回到 Persona Expression
  -> Output Controller
  -> Delivery Receipt
  -> Conversation commit / Memory postprocess
```

规则：

- Personal 负责即时可见回复，不等待 Router 或 Planner 完成。
- Router 的 `silent` 只能取消仍处于 pending 的 Personal，不得撤回已提交或已送达表达。
- Core Planner 不能压制已经获得发送权的 Persona 回复。
- Core 成功、失败、工具错误都作为表达材料交给 Persona，不由 Core 自行拼最终台词。

### 6.2 群聊候选消息

```text
群聊文本
  -> WakingCheckStage
  -> WhitelistCheckStage / SessionStatusCheckStage
  -> ConversationActivity / continuation eligibility
  -> Router + Personal（仅合格候选）
  -> silent / persona / hybrid
```

规则：

- 未明确唤醒时，必须先经过官方唤醒、白名单和会话状态检查。
- 同一发送者的续接资格由拥有者和窗口决定，Conversation 历史本身不授予唤醒权。
- 群聊环境观察只产生结构化 Observation，不把原文伪装成用户消息。
- `silent` 是 Router 的群聊参与决策，不是 Persona Expression 的输出模式。

### 6.3 主动观察与 idle initiation

```text
Runtime Sensor / ConversationActivity
  -> submit_observation
  -> Personal Runtime Inbox
  -> 固定聚合窗口
  -> Deterministic Gate
  -> Personal Policy
  -> PersonalActionIntent
  -> Persona Expression（仅 express）
  -> Output Controller
  -> Delivery Receipt
```

规则：

- 空 Inbox 不创建 batch、不调用 Provider、不调用 Persona。
- Heartbeat 只检查保留中的 batch 或到期 wake deadline，不制造新消息。
- `hold` / `defer` 保留事实；`ignore` / `observe` / `reject` / `fail-closed` 结算本次 revision。
- Policy 只生成内部 `PersonalActionIntent`，不生成用户可见文本。
- 重复表达指纹检查必须发生在 effect、TTS 和平台发送之前。

#### idle initiation 日志收口方案

当前实现已经将 `Personal Runtime idle initiation result` 的安静结果降为不逐次输出；后续重点不是再次修改级别，而是建立“调度检查”和“真实事实/批次”的关联，并用聚合指标判断是否存在频繁空转：

| 事件 | 建议级别 | 记录方式 |
| --- | --- | --- |
| 首次创建或真正提交新的 idle initiation | `INFO` | 每个 `runtime_key + initiation_attempt_id` 一次 |
| 同一 initiation 的合并、去重、续接 | `DEBUG` | 只记录计数和被合并的 revision |
| 空 Inbox 被跳过 | `DEBUG` 或计数器 | 不逐次打印完整文本 |
| 已有更早 wake deadline、无需重复唤醒 | `DEBUG` | 记录 `skipped_reason` 和 deadline |
| Gate / Policy / Output 终态 | `INFO` | 每个 batch 一次，使用稳定 reason code |
| 异常、状态丢失、投递失败 | `WARNING` / `ERROR` | 保留完整上下文和异常 |

建议统一字段：`turn_id`、`runtime_key_hash`、`initiation_attempt_id`、`observation_id`、`batch_id`、`material_revision`、`wake_deadline`、`gate_outcome`、`policy_action`、`delivery_status`、`reason_code`。其中 `initiation_attempt_id` 表示每次调度检查，`observation_id` 只在事实真正进入 Inbox 时存在，`batch_id` 只在批次关闭并评估时存在；不得为 ignored/not_due 检查伪造 batch。

日志中不得出现原始观察文本、完整回复文本或隐私作用域明文。需要查看频率时，应通过聚合指标回答“每分钟提交多少次、多少次被合并、多少次真正形成行动、多少次送达”。

### 6.4 插件/Core 委派

```text
Plugin Handler
  -> plugin material / ProviderRequest
  -> 同一 InteractionTurnState
  -> CoreExecutionSpec
  -> Core 执行
  -> delegated result
  -> Persona Expression
  -> Output Controller
```

规则：

- 插件可提供材料、ProviderRequest 或直接输出，但显式 `persona` 输出最终仍经统一 Persona Expression。
- Router、Planner、Policy 不直接接收插件工具或插件内部扩展，除非通过已声明的 target projection。
- `DELEGATED` 失败不得重放 ProviderRequest；已完成但尚未送达的产物走既有 T2 ledger 路径。
- direct/media 兼容路径保持原语义，但不得反向污染 Persona 状态。

## 7. 分阶段实施计划

### Phase 0：边界冻结与观测基线

**目标**：不改变行为，先确认所有入口和日志事实。

工作项：

- 在代码注释和内部文档中固定五层命名。
- 盘点所有 `Persona`、`Personal`、`Policy`、`Expression`、`State` 字段的读写方。
- 为普通回复、群聊候选、观察主动表达、插件委派建立基线 trace。
- 统计 idle initiation 的提交、合并、跳过、Policy express、实际送达数量。

主要范围：

- `astrbot/core/interaction/personal_state.py`
- `astrbot/core/interaction/personal_runtime.py`
- `astrbot/core/interaction/personal_heartbeat.py`
- `astrbot/core/interaction/expression_agent.py`
- `astrbot/core/interaction/personal_policy.py`
- `astrbot/core/prompt/collectors/persona_collector.py`
- `astrbot/core/memory/persona_state_service.py`
- `docs/Yakumo/persona-architecture-refactor-plan.md`（本计划与基线）

退出条件：

- 能用同一个 `turn_id` 或 `batch_id` 串起一次完整流程。
- 能区分“内部提交”和“用户实际收到”。
- 没有发现未登记的用户可见自然语言出口。

回滚方式：仅删除新增诊断和文档，不触及运行逻辑。

### Phase 1：新增领域数据模型与适配器

**目标**：在现有状态模型之上建立类型边界，不创建第二套并行状态。

工作项：

- 新增 `PersonaDefinition`、`PersonaRelationshipState`、`EffectivePersonaContext` 的最小不可变模型；`RuntimeControlSnapshot` 初期由现有 `PersonalStateSnapshot` 适配提供。
- 为现有 `PersonalStateSnapshot`、`PersonalPersistentState`、`PersonaCollector`、Memory service 编写只读适配器，不复制持久化字段。
- 增加 schema/version 字段和作用域键计算函数。
- 禁止把 task、lock、Provider、Event 等活对象放入 Persona 快照。

退出条件：

- 普通回复和主动观察都能构造 `EffectivePersonaContext`。
- 快照序列化不包含原始回复、Provider 对象或隐私明文。
- 关系状态和运行控制状态不会因为同名字段发生覆盖或双写。
- 旧路径行为保持不变。

回滚方式：保留适配器，调用方切回旧对象读取。

### Phase 2：统一 Persona 收集

**目标**：静态人格只由一个收集边界提供，关系和运行状态通过显式槽位进入。

工作项：

- 收口 `PersonaCollector` 与 `persona_segments.py` 的职责。
- 将关系状态、MemorySnapshot、运行控制快照作为独立 Collector 输出。
- 移除 Prompt 中重复注入的 Persona 片段。
- 保持 `llm_exposure="never"`、target projection 和 provider renderer 现有安全边界。
- 明确 PersonaManager/PersonaCollector 解析失败时的降级：使用安全默认人格、终止当前表达，或转入明确的错误表达；不得静默返回空人格后继续生成。

退出条件：

- `persona_expression` target 的人格材料来源可枚举。
- Router、Planner、Policy 不再通过通用 Persona collector 获得超出其白名单的内容。
- 同一轮多次表达复用同一份基础快照，不重复执行昂贵收集。

### Phase 3：迁移 Persona Expression

**目标**：统一表达请求、结果和送达状态。

工作项：

- 引入分组版 `PersonaExpressionRequest`。
- 为 `expression_agent.py`、`persona_runtime.py`、`personal_expression_guard.py` 增加新旧字段适配。
- 将 `first_response`、插件 persona 输出、Core final、stream interjection、Policy express 统一映射到 `ExpressionIntent`。
- 统一空材料处理：返回结构化 `no_material` 或 `intentional_empty`，不生成占位台词；保留 stream interjection 的合法空结果语义。
- 将重复检查、effect/TTS 触发、Conversation 写入绑定到 `DeliveryReceipt`。

退出条件：

- 所有需要人格改写的语义文本只有一个 Persona Expression 入口，direct/media 兼容输出有明确登记的旁路。
- 生成成功但送达失败不会更新冷却、最近表达和主动额度。
- “无动作”可区分为 `not_admitted`、`no_material`、`intentional_empty`、`suppressed_duplicate`、`policy_ignore`、`expression_failed`、`delivery_failed`。

### Phase 4：隔离 Router、Core Planner、Personal Policy

**目标**：审计并固化现有决策边界，不重复实现已经存在的结构化结果。

工作项：

- 核对 Router、Core Planner、Personal Policy 的现有返回契约和发送权限，补齐遗漏调用方。
- 保留 Router 的 `silent/persona/hybrid`、Planner 的 `execute/not_required`、Policy 的 `ignore/observe/express/defer` 语义。
- 清理仍存在的三类组件对 `event.send()`、Output Controller、Conversation 的直接依赖；已经符合边界的代码不再迁移。
- 为每种决策增加 reason code 和耗时字段。

退出条件：

- 决策模块可以在无平台发送能力的测试环境中独立运行。
- Planner 失败不会导致 Persona 无法表达已有 Core 错误材料。
- Policy 失败按 fail-closed 处理，不产生空回复或重复唤醒。

### Phase 5：整理状态命名和生命周期

**目标**：把跨 turn 的人格关系状态与本进程运行控制的语义彻底分开；不重复迁移已经落到 Repository 的字段。

工作项：

- 审计 `PersonalState`、`PersonalPersistentState` 与 `PersonalStateRepository` 的所有权，必要时只做重命名或适配，不重复搬迁持久字段。
- 观察 Inbox、wake scheduler、lease、deadline、reservation 留在 Runtime Control。
- Memory PersonaState 只负责关系/长期状态，不再承载主动输出冷却和发送额度。
- 明确 RuntimeKey、audience_key、privacy_scope 的作用域与生命周期。
- 保持现有 idle runtime TTL/LRU 策略，并确认有 pending observation、wake deadline 或 active task 时不可回收。

退出条件：

- 重启后能恢复控制所需的最小字段。
- 关系状态更新不会改变冷却或主动额度。
- Runtime 释放、插件 reload、Core shutdown 不会丢失已确认送达的控制状态。

### Phase 6：日志收口与性能优化

**目标**：在边界稳定后减少等待和噪声，避免用日志放大“系统很忙”的错觉。

工作项：

- 当前 idle initiation 的安静结果已经降为不逐次输出；本阶段只补充 attempt/observation/batch 关联指标和聚合计数，不再重复调整已完成的日志级别。
- 为每个阶段记录 `started_at`、`completed_at`、`wait_reason`、`budget_remaining`。
- 只并发无副作用且有明确 single-flight 约束的 Collector。
- Persona 使用基础 ContextPack 时采用 best-effort；Core 需要完整插件 enrichment 时复用同一 task，不重复收集。
- 将 Router、Persona、插件 Runtime 的同一 `t0` 并行关系写入 trace，而非依赖人工猜测时间。
- 对 Provider 调用增加按 target、intent、cache 命中情况的统计。

退出条件：

- 普通回复不因 idle scheduler 或观察空转而增加 Provider 调用。
- 同一 `material_revision` 不会重复进入 Policy。
- 能用日志解释慢响应发生在收集、路由、表达、Core、输出还是平台送达。

## 8. 具体文件范围

### 首批允许修改

- `astrbot/core/interaction/personal_state.py`
- `astrbot/core/interaction/personal_runtime.py`
- `astrbot/core/interaction/persona_runtime.py`
- `astrbot/core/interaction/expression_agent.py`
- `astrbot/core/interaction/personal_policy.py`
- `astrbot/core/interaction/personal_gate.py`
- `astrbot/core/interaction/router_agent.py`
- `astrbot/core/interaction/core_planner.py`
- `astrbot/core/interaction/output_controller.py`
- `astrbot/core/prompt/collectors/persona_collector.py`
- `astrbot/core/prompt/persona_segments.py`
- `astrbot/core/memory/persona_state_service.py`

### 需要保持稳定的边界

- 官方 EventBus / Pipeline 入站语义
- 插件公开 Handler、`ProviderRequest` 委派和输出兼容接口
- 平台 Adapter 的发送契约
- `Conversation` 与 Memory 的已有持久化格式
- `data/cmd_config.json`（不加入跟踪，也不作为本计划的修改对象）

### 暂不触碰

- Provider 具体实现和单个模型的提示词补丁
- Motion、Live2D 等插件私有 effect 语义
- AG99live 前端或 Desktop Body 的具体渲染实现
- 与人格边界无关的生命周期、数据库和 Dashboard 重构

## 9. 兼容、迁移和回滚策略

### 9.1 兼容原则

- 旧字段先读、新字段优先写；稳定后再删除旧字段。
- 新旧请求结构在 `PersonaExpressionAdapter` 中转换，不在每个调用点重复判断。
- 旧入口必须保留清晰的 deprecated 标记和调用计数。
- 任何跨作用域状态迁移都必须带版本号和幂等迁移键。
- 已存在的 `personal_runtime_states` 数据必须先做字段映射和作用域审计，再决定是否改表；迁移失败时保留旧记录，不执行破坏性覆盖。
- 关系状态与运行控制状态不得长期双写；过渡期若必须双写，应记录版本、比较结果和明确的结束条件。

### 9.2 灰度开关

建议使用以下独立开关，禁止一个总开关同时改变所有语义：

- `persona_context_model_enabled`
- `persona_expression_request_v2_enabled`
- `persona_decision_boundary_logging_enabled`
- `idle_initiation_aggregated_logging_enabled`

默认关闭会改变行为的开关；日志和只读快照可以先默认开启，但不得记录敏感原文。每个行为开关必须同时登记 owner、启用条件、回滚方式和删除日期，避免长期维护双轨。

### 9.3 回滚条件

出现以下任一情况，应暂停当前阶段并回滚行为开关：

- 普通明确消息的首个可见回复明显变慢。
- 送达成功后冷却、指纹或主动额度未更新。
- Router `silent` 撤回了已提交或已送达的 Persona 回复。
- 插件 `DELEGATED` 请求被重复执行。
- Policy 或 Heartbeat 产生合成用户消息、Conversation 污染或重复主动消息。
- idle initiation 日志数量增加但无法对应真实 batch 或 wake deadline。
- 日志出现原始观察文本、完整用户回复或隐私作用域明文。

## 10. 日志与可观测性规范

### 10.1 统一上下文

所有人格链路日志至少携带：

- `turn_id`（普通交互）或 `batch_id`（主动观察）
- `runtime_key_hash`
- `persona_id`
- `target`
- `intent_kind`
- `phase`
- `elapsed_ms`
- `budget_remaining_ms`
- `reason_code`

### 10.2 状态事件

建议固定以下事件名，避免同义词泛滥：

```text
persona_context_built
persona_expression_started
persona_expression_completed
persona_output_reserved
persona_delivery_succeeded
persona_delivery_failed
personal_initiation_checked
personal_batch_submitted
personal_batch_coalesced
personal_gate_resolved
personal_policy_decided
personal_action_suppressed
personal_wake_scheduled
personal_wake_skipped
```

`personal_batch_submitted` 表示事实或 batch 进入内部处理，不表示平台发送；旧的 idle initiation 诊断应按 attempt/observation/batch 的实际语义映射到这些事件之一。

### 10.3 频率控制

- 同一 `runtime_key_hash + batch_id + phase` 只允许一条 `INFO` 终态。
- 重复的 `DEBUG` 事件按固定窗口聚合，例如 30 秒或 100 次计数后汇总一次，具体数值在 Phase 0 基线后确定。
- 空 Inbox、重复 wake、同一 revision 的重复检查不得逐条输出完整堆栈。
- 异常必须保留第一次和最后一次，连续相同异常中间部分使用计数。
- `personal_initiation_checked` 与 `personal_batch_submitted` 必须分开统计，不能用一次调度检查代替一次事实提交。

## 11. 测试与验收标准

### 11.1 静态检查

- `git diff --check`
- Python 格式与类型检查按当前项目可用工具执行
- 搜索并审计所有旧 `PersonaExpressionRequest` 字段调用方
- 搜索所有直接可见输出出口，确认都能归属到 Output Controller 或明确兼容路径

### 11.2 单元边界

至少覆盖：

- `PersonaExpressionRequest` 新旧结构互转
- 空材料不生成占位文本
- `DeliveryReceipt` 成功/失败对冷却、指纹、额度的差异
- 不同 `RuntimeKey` 的状态隔离
- 相同 `material_revision` 的 Policy 幂等
- idle initiation 的 `initiation_attempt_id`、合并、跳过和真实提交计数
- PersonaCollector/PersonaManager 失败时不会静默生成空人格

### 11.3 集成流程

至少验证四条真实路径：

1. 私聊明确消息：Personal 与 Router 同一 `t0` 启动，Personal 可以先于 Planner 表达。
2. 群聊未唤醒消息：不满足资格时不调用 Persona、不调用 Core、不产生平台输出。
3. 主动观察：空 Inbox 不调用 Provider；Policy express 只在送达成功后更新控制状态。
4. 插件委派：ProviderRequest 只执行一次，Core 结果只经过一次 Persona Expression。

### 11.4 运行验收

- 使用真实私聊和目标群日志确认同一 turn 的阶段耗时。
- 统计 idle initiation 提交数与真实主动送达数的比例。
- 观察冷却、静音、每日额度和重启恢复。
- 检查异常时是否仍能释放 lease、session queue、PostProcess 和插件 Job。
- 验证日志无原文泄露，且能通过 reason code 解释“为什么没有动作”。

## 12. 性能策略

性能优化必须建立在边界稳定之后，优先级如下：

1. **去掉重复工作**：同一 turn 复用 ContextPack、enrichment task 和已构造的表达材料。
2. **提前开始无副作用工作**：Personal、Router、可安全并行的插件 Runtime 从同一 `t0` 启动。
3. **缩短等待而不是降低质量**：Persona 首次表达使用基础上下文，Core 再等待完整执行材料。
4. **减少空转唤醒**：Heartbeat 不为无新事实或已有更早 deadline 的 Runtime 创建重复 initiation。
5. **控制模型调用数**：Router/Planner/Policy 各自只在有明确职责时调用；表达模型不承担路由判断。
6. **按阶段消耗统一预算**：所有阶段共享同一个 turn deadline，不允许某个内部重试悄悄延长整轮响应。

不得通过以下方式“优化”：

- 直接删除 Persona Expression 以换取速度。
- 让 Router 或 Core 自己生成可见台词。
- 让 idle scheduler 以更高频率轮询来弥补状态不清。
- 用静默 fallback 掩盖 Provider、插件或平台送达失败。

## 13. 风险与开放问题

### 已识别风险

- 旧 `PersonalState` 字段含义可能已经被外部插件或诊断脚本依赖。
- Persona 与 Memory 的作用域迁移可能造成历史状态无法直接匹配。
- 统一表达入口会暴露现有 direct/media 兼容路径的隐含差异。
- Provider 对严格 Persona 输出契约的支持能力不一致。
- 并行启动 Personal、Router、插件 Runtime 会产生不可见的 speculative cost，需要通过 trace 证明收益。
- 日志聚合过度可能降低故障定位能力，必须保留首尾异常和稳定关联 ID。

### 需要在 Phase 0/1 评审时确认

1. `audience_key` 的规范化规则是否沿用当前 `PersonalRuntimeKey`，还是单独定义关系作用域。
2. 关系状态是否允许按群体共享，还是第一版只支持用户级状态。
3. `MemorySnapshot` 是否允许携带关系摘要，还是严格由 `PersonaRelationshipState` 提供。
4. `ExpressionIntent.kind` 的枚举是否需要把 `progress` 与 `interjection` 分开。
5. idle initiation 聚合窗口和采样比例应根据真实日志确定，不在文档阶段拍死。
6. 是否需要为 Desktop Body 单独增加 `body_expression` target；在没有真实调用方前不应提前扩展。

## 14. 完成定义

人格重构只有同时满足以下条件，才算完成，而不是“新类已经创建”：

- 稳定人格、关系状态、运行控制、决策和表达拥有清晰且可追踪的所有权。
- 所有用户可见自然语言都能定位到唯一 Persona Expression 或明确兼容出口。
- Router、Planner、Policy 不拥有发送权限，也不生成用户台词。
- 普通回复、群聊候选、主动观察和插件委派共享统一的表达/送达协议。
- 无动作结果可解释、可测试，且不会产生空占位回复。
- idle initiation 日志能够区分内部调度与真实推送，重复检查不会制造 INFO 噪声。
- 冷却、指纹、额度只在确认送达后更新，失败不会伪装成成功。
- 每个阶段都有可运行的验收和独立回滚开关。
- 文档、代码命名和日志事件使用同一套术语。

## 15. 推荐执行顺序

```text
先评审本文档
  -> Phase 0 基线与日志盘点
  -> Phase 1 只读领域模型
  -> Phase 2 Persona 收集收口
  -> Phase 3 表达请求与送达协议
  -> Phase 4 决策/表达隔离
  -> Phase 5 状态生命周期整理
  -> Phase 6 日志收口与性能优化
```

在 Phase 0 和 Phase 1 的边界没有得到确认前，不建议直接大规模移动 `personal_runtime.py`、`expression_agent.py` 或 `persona_state_service.py` 的业务逻辑。先让数据流、状态流和日志流可见，再做物理拆分，才能避免把当前的“散和混乱”搬到一组新名字里。

## 执行记录

### 2026-08-28：Phase 0 第一批诊断已落地

本次只修改诊断和测试，不改变路由、Persona Expression、Personal Policy、Core 或平台发送行为：

- idle initiation 每次调度检查拥有独立的 `initiation_attempt_id`。
- 诊断区分 `initiation_attempt_id`、真正进入 Inbox 的 `observation_id`，以及后续批次使用的 `batch_id`。
- Heartbeat diagnostics 增加 idle initiation 的 admitted/coalesced/ignored/expired/failed 聚合计数。
- Observation 入队、batch 关闭、Gate 终态增加统一的 `runtime_key_hash` 和关联 ID。
- Router、Core Planner、Persona Expression 的成功日志补充 `turn_id` 和 target；Router 成功日志不再输出模型原始结果。
- 保留事件内部的截断诊断字段，便于异常分析；该字段不进入普通用户 Prompt。

已验证：

- `tests/unit/test_personal_heartbeat.py`：2 passed
- `tests/unit/test_personal_policy.py` 与相关 Heartbeat：5 passed
- Router/Planner/Expression 相关测试：52 passed
- `tests/unit/test_personal_runtime_capability.py`：44 passed
- `compileall`、`git diff --check`：通过

尚未完成的 Phase 0 工作仍包括真实平台日志采样、Policy/Expression/Delivery 的统一终态事件和长期聚合指标；这些工作需要真实运行数据后再决定，不在本批次扩大实现范围。

### 2026-08-28：Phase 1 第一批只读领域模型已落地

本次建立类型边界，不迁移现有状态所有权，也不接管现有 Prompt 调用链：

- 新增不可变 `PersonaDefinition`，从 `PersonaCollector` 的 ContextSlot 输出适配静态人格、分段、开场对话和工具/技能白名单。
- 新增不可变 `PersonaRelationshipState`，只读适配 Memory 的 `PersonaState`；序列化只保留 `scope_id_hash`，不暴露作用域明文。
- 新增不可变 `RuntimeControlSnapshot`，由现有 `PersonalStateSnapshot` 适配，不创建第二套可变运行状态。
- 新增 `EffectivePersonaContext`，显式组合静态人格定义（`definition`）、关系状态和运行控制快照（`runtime`）。
- 增加 `PersonalPersistentState` 的只读映射适配器、MemorySnapshot 关系适配器和稳定作用域键函数。
- 所有快照值在构造时深度冻结，序列化结果不携带 Provider、事件或 ContextSlot 元数据。

已验证：

- `tests/unit/test_persona_domain.py`：9 passed（含不可序列化值、非有限浮点和领域状态边界拒绝）
- 人格/Memory/Personal Runtime/Router/Planner/Expression 相关回归：113 passed
- Ruff、`compileall`：通过

本批次尚未把 `EffectivePersonaContext` 接入 Persona Expression 或主动观察链路；接入前仍需先完成 Phase 0 的真实 OLV 日志采样和 Phase 1 适配器评审。

### 2026-08-28：Phase 2 第一批 Persona 解析复用与失败语义已实现

- 为 `PersonaCollector` 增加事件级解析结果缓存；同一事件内不同 `ProviderRequest` 分支复用已解析并分段的人格槽位。
- 缓存键只包含解析函数的实际输入：事件作用域、平台、会话人格、`default_personality` 和 PersonaManager 实例；解析失败不写入缓存。
- 缓存返回值使用深拷贝，避免调用方修改事件级快照；无人格结果也会缓存，避免同一事件重复访问持久化 Session 配置。
- `PersonaCollector` 保持 required collector 语义：PersonaManager 缺失或解析异常会向上抛出，由 `collect_context_pack()` 统一终止本次 Prompt 构建，不再把失败伪装成空人格继续生成。

已验证：

- PersonaCollector 跨 ProviderRequest 复用及配置变化失效：通过
- PersonaCollector 解析异常向上透传：通过
- InteractionContextMaterial 保留 `PersonaDefinition` 快照，表达阶段优先使用快照人格 ID：通过
- Prompt collect、人格/Memory/Personal Runtime/Router/Planner/Expression 相关回归：197 passed
- 本轮未修改 `data/cmd_config.json`，也未改变 Persona Expression、Router 或输出行为

仍待下一批处理：

- `astr_main_agent._prepare_persona_and_subagents()`、CapabilityResolver 和错误表达路径仍可能绕过 PersonaCollector 直接解析人格；需要统一事件级解析适配器后再合并，避免重复访问 Session 配置。
- `resolve_selected_persona()` 还依赖 `session_service_config` 这一隐藏输入；在事件级缓存扩大到更多调用方前，需要为该配置建立明确的版本或事件快照边界。
