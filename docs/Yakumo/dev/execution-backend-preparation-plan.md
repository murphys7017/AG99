# Personal Runtime 前置主链清理计划

当前复核基线：2026-09-19。Phase 0 至 Phase 8 的主要前置边界已落地或进入真实验收；
Phase 9 已进入 Core Head 的进程内通信状态基础，但尚未实现完整 Core Head、统一队列或
可替换 Executor Body。本文的“已完成”只表示源码中已经存在的边界，不表示后续目标已经实现。

本文记录 Yakumo 下一阶段的总体实施计划。当前优先级不是实现可替换 Executor Body，
而是把执行阶段之前仍然存在的过渡结构清理为稳定的 Personal Runtime 主链。只有这些
边界完成后，Native、Claude Code、OpenCode 等 Executor Body 才进入
设计和实现。

本文是目标和实施顺序，不代表所述能力已经完成。当前运行事实以
`execution-backend-flow.mmd` 和源码为准。

## 2026-09 Interaction 主链修订

本计划早期章节中的 Router 描述是已完成的迁移历史，不是当前实现或后续任务。普通对话现在由一次 Personal Response Plan 生成 `reply / delegate / silent`；`delegate` 后 Planner 只生成 `execute + CoreTaskSpec`。后续 Core Head、Session Runtime 和 Executor Body 的设计应保留这一单一控制入口，不得重新将 Router 作为独立普通消息 Agent 接入。

## 上游参考边界

AG99 使用 AstrBot 的基础设施与公开兼容面，但当前运行时架构已经与上游主线明显分化。
因此上游是按需学习和选择性吸收修复、Provider/平台兼容与通用能力的参考来源，不是需要
追平或合并的开发主线。Git 历史分叉数量不构成待办；每项参考输入都必须先判断 AG99 是否
仍有相同问题、是否符合本项目 owner 边界，以及是否应以本地小改动实现。

Prompt、Memory、Interaction、Personal 输出链与 Core Head 的设计以本仓库文档和源码为准。
上游参考结论记录在 `docs/Yakumo/upstream-merge-ledger.md`，用于避免重复研究，而不是建立
历史对齐或大范围合并计划。

## 优先级调整

过去的计划以“为执行器解耦做准备”为主轴，容易把现有中间结构误认为必须长期兼容。
现在明确调整为：

1. 先确定 Personal Runtime、Personal Expression、Prompt、Capability、Output、Memory
   和插件的长期 owner。
2. 清理已经完成使命的过渡状态、旁路、镜像和反向回调。
3. 让官方插件与平台能力通过稳定边界继续工作。
4. 最后才从稳定的 Execution Preparation 接入不同 Executor Body。

执行后台是最后一段替换点，不是当前架构工作的中心。前置主链完成后，Executor Body 应只
负责“如何执行”，不再重新实现 Prompt、知识库、工具、插件、会话和输出。

## 2026-09-19 全局架构收口门槛

本次全局审阅确认：当前最大的风险不是缺少新的执行器抽象，而是多个局部正确的 owner
仍通过兼容路径、事件 extra 和双轨运行时互相连接。后续工作进入“架构收口优先”阶段，
在收口完成前不继续扩展新的 Executor Body、输出类型或插件主链。

当前必须冻结的四个长期 owner：

1. `InteractionTurnState`：一次 InteractionTurn 的内部事实和状态转换。
2. `InteractionOutputController` / 后续 `OutputRuntime`：所有用户可见输出的意图、
   产物、物理投递和完成语义。
3. `CoreExecutionHead`：CoreExecution 的命令、事件、取消、终态和结果事实。
4. `CapabilitySnapshot`：一轮执行中模型可见能力、可执行能力、策略和来源。

这四个 owner 不是要求立刻重写成四个新类，而是要求后续每一批代码只能新增或迁移一个
owner 的写入边界。旧路径可以短期保留为只读校验或公开兼容适配，但不得继续形成第二个
内部主写者。

### 收口前置条件

在进入下一个 Executor Body 设计或实现切片前，必须完成：

- 对 `event.extra` 的读写清单和单向兼容投影表；
- Output Controller、Plugin Artifact Delivery、Delayed Delivery、Turn Delivery
  的状态写入规则与端到端完成语义；
- 旧 Handler-first 路径与协调 Plugin Runtime 的真实私聊、群聊、取消、reload、
  迟到输出和重复投递验收；
- `astr_main_agent.py` 中 Provider、Prompt、Capability、Execution 装配职责的调用图；
- `CoreExecutionSpec -> ProviderRequest -> Runner` 的单向关系核对；
- `InteractionTurn -> PluginInvocation/CoreExecution -> OutputArtifact` 的身份关系表；
- Cron、主动任务和普通 Interaction 是否共用同一可见输出/语音完成边界的验收。

未满足这些条件时，Phase 9 的状态只能写作“Native 进程内事实边界已建立”，不能写作
“Core Head 已完成”或“Executor 已可替换”。

## 近期推进队列（2026-09-19）

以下队列是当前收口期的实际执行顺序。每一项先完成事实盘点和边界说明，再进行最小
代码迁移；后一项不得用设计稿替代前一项的真实验收。

| 顺序 | 工作切片 | 主要产物 | 进入下一项的条件 |
|---|---|---|---|
| 1 | `event.extra` owner 盘点 | 读写清单、字段 owner 表、单向兼容投影表 | 已完成第一轮清单；ProviderRequest 三种语义已拆分，待按边界迁移 |
| 2 | Output Runtime 边界收口 | 输出状态机、组件完成规则、Delayed/Artifact/Turn Delivery 关系图 | 已完成第一轮 owner 盘点；待建立 `DeliveryReceipt`/identity 关系并验收 |
| 3 | 双插件运行路径验收 | Handler-first 与协调 Plugin Runtime 对照 trace | 私聊、群聊、取消、reload、迟到输出、重复投递均有结论 |
| 4 | Agent 装配拆分 | `astr_main_agent.py` 调用图、Provider/Prompt/Capability/Execution 边界 | 新增能力不再要求修改主装配器的跨层逻辑 |
| 5 | Core 输入关系收口 | `CoreExecutionSpec -> ProviderRequest -> Runner` 关系和身份表 | 每次 Core 执行只存在一条规范化输入链，失败/取消仍可定位到同一执行 |
| 6 | 主动任务统一验收 | Cron/主动任务与普通 Interaction 的输出、TTS、历史、完成回执 trace | 成功、失败、取消、无目标和迟到场景语义一致 |
| 7 | Executor Body 决策闸门 | 前置主链就绪报告、Native Adapter 最小方案 | 仅当第 1-6 项通过后，才开始第二个 Executor Body 或正式 Adapter |

### 队列执行规则

- 第 1-6 项允许修复已确认的边界缺陷，但不得借机新增对外输出通道、第二个
  Personal 决策 Agent 或新的插件主链。
- 任何只通过静态代码推断的项目都只能记录为风险；必须有运行 trace、最小端到端
  验收或明确的调用图，才能改变状态。
- 若某一切片发现多个内部主写者，先停止扩展该方向，补齐 owner 和迁移边界后再继续。
- 旧 Handler-first、官方 Event API、`ProviderRequest` 和 `CoreExecutionSpec` 在
  调用图与真实验收完成前不得删除；它们可以被标记为待迁移，但不是当前的清理目标。
- 这 7 项完成前，Phase 9 仍标记为“通信状态边界/Execution Preparation”，不标记为
  “Core Head 完成”或“Executor 可替换”。

## 边界校正：Personal、Core Head 与 Executor Body

后续设计统一采用以下职责模型：

```text
Personal Agent
    <-> Core Head
          +-- Planner
          +-- CoreExecution 会话与状态机
          +-- Core 内部任务调度/队列
          +-- Executor 选择与生命周期协调
          +-- ExecutionEvent / Artifact 归一化
          +-- 可替换 Executor Body
```

- `Personal` 负责统一的对外沟通、快速响应、人格表达和最终用户可见输出。
- `Core Head` 是稳定的核心控制面，负责接收任务、规划、执行会话、状态、取消、重试、
  事件归一化和结果汇总。
- `Executor Body` 是 Core Head 内部的可替换执行实现。Native Agent Runner 是当前
  实现，未来可以增加其他执行器，但它们不直接接触 Personal、平台 Event 或 Output。
- Personal 与 Core Head 之间是同一进程内的异步双向通信；消息对象和事件对象用于隔离
  生命周期，不代表网络协议、跨进程协议或分布式部署。
- “委派”只建立或更新一条 `CoreExecution` 通信会话，不等于释放 Personal turn、转移
  Personal 输出所有权，也不等于 Core 可以直接向平台发言。
- `CoreExecution` 会话与 Personal turn 是关联但独立的生命周期：前者可继续执行、接收
  取消或产生事件，后者始终由 Personal 持有其会话准入、对外表达和完成语义。

因此，Core 内部可以有队列、worker、取消和事件订阅；Personal/Core 边界暂不引入
分布式消息系统或远程 Agent 发现。只有未来确实需要跨进程执行时，才在 Core Head 内部
增加相应 Adapter，而不是改变 Personal 的职责。

## 兼容边界

需要持续保护的兼容面：

- 官方 EventBus、Pipeline、filter、permission、whitelist 和 Handler 调用语义。
- 官方插件公开 API、Hook、`yield`、`stop_event`、`ProviderRequest` 和消息组件。
- 平台 adapter 的发送协议、配置、已有 conversation 和持久化数据。
- 未启用 Personal Runtime 时的官方路径。

不属于长期兼容目标的内部过渡结构：

- Local 与 Third-party Agent SubStage 的平行准备链。
- 运行时替换 `event.send()`、`event.send_streaming()` 和
  `event.complete_visible_turn()`。
- 分散的 `_interaction_*` extra 作为内部主状态。
- `InteractionMiddleware` 与 `InteractionOutputController` 之间的私有反向回调。
- 同一共享 `context_material` 被后续阶段替换为不同 ContextPack 版本。
- `ProcessStage` 直接操作 OutputController 内部事务。

迁移可以短暂保留边界适配器，但每个阶段完成后必须删除被替代的内部路径。不得以
“兼容”为理由长期维护两套 owner 或两条主链。

## 目标主链

```text
Platform / Internal Event
  -> Official EventBus / Pipeline filters and preprocess
  -> ProcessStage
       -> Personal Runtime Adapter reserves PendingTurn and Output Port
          (no Router / Persona / Planner call)
       -> Official Plugin Handler runs inside the reserved turn
       -> resolve effective persona and bind reservation to PersonalRuntimeKey
       -> Personal Runtime Adapter activates or settles the bound turn
            -> PersonalSessionRuntime mailbox
            -> Observation / active conversational turn
            -> Personal Response Plan
                -> reply -> Personal Expression
                -> delegate -> Core Head
                    -> CoreExecution session
                    -> Planner / internal scheduling
                    -> ContextSnapshot + CapabilitySnapshot
                    -> Execution Preparation
                    -> replaceable Executor Body
                    -> normalized Execution Events / Artifacts
                    -> Core Head -> Personal Expression
                -> Core result -> Personal Expression
       -> Output Dispatcher
  -> Official Platform Sink
  -> Finalized Turn
  -> Conversation / Memory / Lifecycle
```

关键所有权：

- `Personal Runtime` 持有 session、turn、任务、插件协作、路由和完成权。
- Plugin Handler 前的 reservation 只建立 transport/config/audience 范围内的 Turn identity 和
  输出归属，不提前解析最终 persona，也不运行分类或表达。
- `Personal Expression` 只形成统一人格表达，不执行业务能力。
- Prompt 系统收集事实并按目标投影；Planner 不构建执行上下文。
- Capability 系统是 Knowledge、Tools、Skills 和 Plugins 的唯一通用能力来源；SubAgent 仅作为 Native 兼容能力保留。
- Output Dispatcher 是所有可见输出的唯一内部出口。
- Core Head 是 Personal 委派的唯一接收者；Executor Body 只由 Core Head 调度和监督。
- Executor Body 只消费准备好的 Execution Request，并向 Core Head 返回统一 Execution Events；
  Personal 不直接选择或调用 Executor。Core Head 负责任务提交、取消、进度接收和结果归一化；
  Personal 保留最终是否表达、如何表达和何时表达的唯一决定权。

## 实施原则

- 从源码事实和实际运行日志出发，不从理想接口反推空置抽象。
- 一次只迁移一个 owner；新 owner 接管后删除旧 owner 的写入路径。
- 新旧路径短暂并存时只能有一个主写者，另一条只能做只读校验或边界适配。
- Personal Response Plan、Planner 和 Personal Expression 保持独立，但消费同一事实快照的不同投影。
- 不把所有官方能力转换成 MCP；内部先形成统一 Capability，再由 Core Head 为 Executor
  Body 选择直接调用、MCP、RPC、CLI 或其他桥接。
- 不为了文件变小而拆类；只有所有权、生命周期或测试边界发生变化时才拆模块。

## 执行器解耦的契约原则

Executor Body 的解耦需要明确任务委托、生命周期、进度和产物交付，但当前只设计进程
内的 Core Head 边界，不预设远程协议、部署形态或分布式实现。以下原则用于约束 Core
内部的执行边界，而不是替代 Personal/Core 的本地通信总线。

可以借鉴的原则：

- 将一次用户对话、一次 Core 执行、一次插件调用和一个可交付产物严格区分。推荐父子关系为
  `InteractionTurn -> CoreExecution -> PluginInvocation / ExecutorTask -> OutputArtifact`；各层有
  自己的 identity、终态和审计记录，不能通过 `event.extra` 或后台回调隐式互相替代。
- Executor Body 只接收经过授权和脱敏的执行请求，内部模型、工具、记忆和推理过程保持黑盒；
  Core Head 拥有任务提交、取消、进度接收和结果归一化，Personal 拥有最终对外表达决定权。
- 执行过程应回流为结构化 `ExecutionEvent`，至少能表达 `submitted`、`working`、
  `progress`、`input_required`、`artifact_ready`、`completed`、`failed` 和 `cancelled`。原始
  token、搜索材料或执行器日志不是用户可见输出；Persona 根据事件语义决定是否表达和如何表达。
- 执行结果以规范化 artifact 返回，再进入 Persona Expression 和 Output Runtime。任何
  Executor Body 或未来远程 Adapter 都不能绕过这两个边界，直接取得平台发送、TTS、effect
  或 AG99live 输出权限。
- Executor Body 的能力声明、某轮任务的授权和实际执行句柄是三个不同概念。未来可登记支持的输入输出、
  流式进度、取消、补输入、文件、网络和沙箱能力；本轮能否使用仍由 Capability Snapshot、权限与
  策略决定。
- 取消、超时、重试、重启恢复和重复投递必须围绕稳定 `execution_id` 设计，并保留明确的终态，
  不能依赖进程内 task handle 是否还存在。

明确不做的事：

- 不把 Persona、Personal Response Plan、Core Planner、本地插件或 `FunctionTool` 全部抽象成互相通信的 Agent。
- 不让 Executor Body 的能力登记或未来远程 Adapter 绕过管理员配置、会话权限、Capability Snapshot 或网络边界。
- 不把执行器的消息或 artifact 直接映射为平台消息；它们先是执行事实，是否形成用户表达仍由
  Persona 和 Output Runtime 决定。

若未来出现第二个经实际验证的 Executor Body，应保持
`Core Head -> Executor Adapter -> Executor Task -> normalized ExecutionEvent / Artifact`
这一 Core 内部单向边界。Personal 仍只与 Core Head 通信。在统一 Execution Event、取消、
Ledger owner、Output Port 和可传输的 Capability contract 尚未完成前，不创建远程 Adapter
或能力发现入口。

## Phase 0：过渡结构清单与运行事实

状态：已完成。无入口的 pre-Pipeline 路径、影子 Interaction Memory、重复能力摘要和
兼容状态镜像已经删除。后续发现的过渡结构直接在所属 Phase 清理，不再维护独立调查文档。

需要完成：

- 将现有结构标记为 `保留`、`迁移`、`替换`、`删除` 或 `公开边界适配`。
- 记录消息、插件直接回复、插件 `ProviderRequest`、Persona-only、Core 非流式、Core
  流式、Core 错误、主动消息、Subagent 前台和后台的运行事实。
- 记录每条路径的状态 owner、输出 owner、完成 owner、Prompt 版本和能力来源。
- 盘点所有 `_interaction_*` extra，区分公开诊断、兼容镜像和内部状态。
- 盘点 Local/Third-party 路径差异，但不在本阶段设计 Executor Body。

退出条件：每个现有过渡结构都有明确去向，不再把“当前可用”当作“目标保留”。

## Phase 1：Personal Runtime 所有权

目标是让 Personal Runtime 成为长期控制层，而不是每条消息上的协调函数集合。

当前状态（2026-07-21）：第一批所有权迁移与 Runtime Observation 纵向入口已经落地。Lifecycle 持有共享
`PersonalRuntimeManager`；`ProcessStage` 在 Handler 前 reserve，在 Router/Persona 前完成
persona bind、follow-up admission 和 Turn lease；Native 与 Third-party Core 共用同一
Runtime 串行策略。Native 原有的 UMO session lock 和全局 follow-up registry 已退出生产
主链。插件显式 `ProviderRequest` 在 Third-party 路径中会保留原对象和已有字段，再进入
现有兼容投影与 Hook。内部 `RuntimeObservation` 已可通过通用 Intake 进入同一个 Session
Runtime 的有界 Inbox；该路径不检查主动消息能力、不创建 event，也不触发输出。已经决定表达
的 Observation 则通过独立 event adapter 校验发送能力，绕过 Router/Core，复用唯一 Persona
Expression、Output Controller、assistant-only Conversation 提交和完整 lifecycle 终态。Inbox
关闭的 batch 已进入纯本地 Deterministic Gate，只形成 `evaluate / hold / reject` diagnostics；
不调用模型，hold batch 会返回 Inbox。

本阶段已完成：持久状态接线、Gate settings、Personal Policy、多目标 Heartbeat Observation
生产者、受控的 `express / defer` Action Coordinator、受限 Plugin Runtime Sensor，以及覆盖配置
观察群聊目标的 `conversation_activity` Source。Heartbeat 只能检查 retained batch，不能靠旧历史
或空 Inbox 制造材料。仍未完成：更广泛的 Runtime Sensor、后台任务 identity，以及未来后台执行的
权限和执行设计。

实施内容：

- 定义稳定 `PersonalRuntimeKey`：
  `config_id + persona_id + audience_key + privacy_scope`。
- `persona_id` 使用官方 PersonaManager 的稳定解析结果；未选择 persona 时使用配置范围内
  的显式 default identity。
- `audience_key` 使用规范 MessageSession/UMO 表达投递对象；群聊按群 audience 共享
  Runtime，私聊按对端 audience 隔离。actor 和 conversation_id 是 Turn 事实，不进入
  Runtime Key。
- Handler 前先建立 `PendingTurnReservation`，键只包含
  `config_id + audience_key + privacy_scope + turn_id`。Handler 结束并获得 conversation、
  `ProviderRequest` 等最终事实后，通过官方 PersonaManager 解析 effective persona，再绑定
  到完整 `PersonalRuntimeKey`。
- Manager 按 Runtime Key 解析 `PersonalSessionRuntime`，并定义空闲回收、配置重载和关闭
  时的 task 取消规则。
- 官方过滤和 preprocess 完成后、Plugin Handler 前先 reserve PendingTurn。Reservation
  只绑定 turn/transport identity 和 Output Port，不启动 Router、Persona 或 Planner。
- Plugin Handler 在 reserved Turn 内运行。Handler 结束后解析 effective persona，把
  reservation 绑定到 Session Runtime，再根据 stopped、final result、`ProviderRequest`
  和 Core candidate 状态 activate、queue 或 settle Turn。
- PendingTurn 状态固定为 `reserved -> bound -> queued|active -> settled`。`reserved` 没有
  conversational completion 权；Handler 期间的普通语义输出先记为 provisional/progress，
  显式 raw/protocol 输出可以投递，但不会隐式完成对话 Turn。
- Session Runtime 持有 mailbox、active turns、Router/Persona/Planner task handle、取消和
  超时。同一 Runtime Key 默认只有一个拥有用户可见输出完成权的 conversational Turn。
- 新用户消息优先作为当前 ActiveTask 的 follow-up；无法吸收时进入 mailbox 排队。协议
  事件、原始媒体和显式可并发后台任务不占用 conversational Turn。
- 将 Router/Persona 并发启动、Planner 调度、turn 仲裁和最终完成迁入 Session Runtime。
- `InteractionMiddleware` 收缩为官方 Pipeline 的薄适配器，不再拥有业务编排。
- 普通显式对话并发启动 Router 与 Persona；`persona` 以即时表达完成，`hybrid` 继续 Planner，
  Planner 委托 Core 时保留已经提交的即时表达，并由 Core-final 结果再次进入统一 Persona。
  未被 Handler 接管的群聊候选进入同一并行主链；Router `silent` 原子压制 pending Persona，
  已经提交或送达的表达不回滚。
- Phase 1 继续以现有 `InteractionTurnState` 作为唯一可写 Turn 状态，不创建平行
  `PersonalTurnState`。类型化改名和 extra 迁移留给 Phase 2。
- Phase 1 只登记插件、Native follow-up、Subagent 和后台任务的稳定 identity/task handle；
  不提前迁移它们的执行与完成生命周期，实际 owner 迁移留给 Phase 7。

退出条件：一轮任务的 owner 不再是 `AstrMessageEvent` 或 Middleware 全局 task 集合；
Plugin Handler 前产生的输出能够关联 PendingTurn，并在 persona 解析后绑定正确 Runtime；
多轮插件和后台任务能够关联稳定的 runtime/task identity，但仍可由 Phase 7 的兼容
adapter 执行。

## Phase 2：类型化 Runtime Context

实施内容：

- 建立 `PersonalRuntimeContext` 和 `PersonalSessionState`，将 Phase 1 继续使用的
  `InteractionTurnState` 原位迁移为 `PersonalTurnState`，不建立第二套并行状态。
- event 只挂一个 Runtime Context 引用，内部模块通过类型化对象交换状态。
- 将 route、planner、prompt、stream、output、completion 和 failure 状态从散落 extra
  迁入 TurnState。
- 保留必要的官方插件兼容 extra，但由一个边界适配器单向投影，不允许反向成为主状态。
- 为状态转换建立封闭方法和运行时不变量，禁止模块直接修改其他 owner 的字段。

退出条件：内部主链不再依赖魔法字符串协作；同一状态不存在 TurnState 与 extra 两个
可写事实源。

## Phase 3：统一 Output Dispatcher

实施内容：

- 定义 `OutputIntent`、`ExpressionIntent`、`OutputEnvelope` 和 Platform Sink 边界。
- 即时 Persona、Core 结果、插件输出、任务进度、主动表达和面向用户的原始媒体都进入
  同一 Dispatcher。
- Personal Expression 在 Dispatcher 物化和平台发送之前运行。
- 文本、TTS、媒体和客户端对象是同一逻辑 utterance 的 rendition，不是独立回复。
- 官方 `OnDecoratingResult`、`OnAfterMessageSent`、内容安全和 postprocess 在明确阶段运行。
- 逐步删除 event 方法替换和 `_interaction_original_send*` 回退。
- `Context.send_message()` 保留公开调用方式，但内部必须形成主动 OutputIntent。
- `raw` / `protocol` / `direct` 表示不做 Persona 改写或保持协议内容，不表示绕过
  Dispatcher。只有平台握手、ACK 等非用户可见协议控制允许在 Platform Sink 内部处理。

退出条件：所有用户可见输出只有一个内部 owner；重复回复防护不再依赖文本比对和来源
猜测；raw 输出仍有 Envelope、delivery identity 和完成语义。

## Phase 4：Prompt 快照生命周期

实施内容：

- 将基础事实固定为不可变 `BaseContextSnapshot`。
- Personal Response Plan、Planner、Persona、Execution 使用显式 Projection 和 Phase Overlay。
- 静态与动态 collector 由 Prompt 系统统一调度，业务模块不自行查询同类事实。
- Core 需要的工具绑定、任务材料和执行时状态进入 Execution Overlay，不替换基础 Pack。
- ContextSnapshot 记录版本、来源、阶段和 lineage，诊断能够还原每次模型请求使用的事实。
- Planner 只生成 `execute/not_required + CoreTaskSpec`，不拥有执行上下文构建。

退出条件：模型请求不受 Personal Response Plan、Persona、Planner 或 Core 的完成顺序影响；同一阶段使用
哪个快照可以被确定地重放。

## Phase 5：统一 Capability Snapshot

实施内容：

- 建立唯一 Capability Resolver，统一解析 Knowledge、Tools、Skills 和 Plugins。
- 同一个 Snapshot 提供不同投影：Router 看极简摘要，Planner 看能力目录，执行阶段看
  完整描述与调用绑定。
- 当前 Interaction 已直接复用统一 Prompt collectors，不再维护平行的能力摘要事实源；
  后续继续统一执行绑定。
- 插件能力声明包含 owner、scope、权限、side effect、timeout 和可挂载位置。
- 默认能力归属 Personal Runtime；显式声明后才允许挂载 Core/Execution。

退出条件：Planner 判断依据与后续实际可执行能力来自同一快照；插件能力不依赖特定
AgentRunner 才能被发现。

## Phase 6：Conversation 与 Memory 收口

实施内容：

- 官方 Conversation 保存精确对话记录。
- MemoryService 保存短期摘要、长期记忆、人格状态和关系状态。
- assistant-only 主动表达只保留 `TurnRecord` 与 Conversation 语义历史；不会更新短期、长期或
  PersonaState，也不会触发 consolidation。
- Interaction 私有 Memory Store 已删除；ConversationHistoryCollector 与 MemoryCollector
  是当前唯一读取入口。
- Persona、Personal Response Plan、Planner 和 Execution 通过 Prompt Projection 使用相同的历史与记忆
  事实，不各自维护副本。
- finalized turn 是 Conversation 和 Memory 的唯一提交材料，cancelled/failed 有
  明确持久化策略。

退出条件：近期对话没有多套互相竞争的来源；人格状态不再按单个平台 session JSON
孤立保存。

## Phase 7：插件、任务与 Subagent 边界

实施内容：

- 将分散的 prompt/result/stream/lifecycle 注册收口为类型化扩展点描述。
- 保留官方插件 Handler 位置和公开 Hook，通过 Personal Runtime 适配到稳定阶段。
- ProcessStage 不再直接操作 OutputController 内部事务。
- 多轮插件任务由 Session Runtime 持有，插件输出明确区分 progress、final、protocol 和
  raw media。
- 当前 SubAgent 定义、Collector、Orchestrator 和 Handoff 继续作为 Native 官方兼容路径，
  不迁入通用 Capability 或 Personal Runtime 契约；新的专业能力优先由插件 Tool 提供。

退出条件：插件能力不依赖某个具体 Runner 的内部对象即可参与主流程；Native SubAgent
被明确隔离在兼容边界，主动和后台结果能够恢复正确的 persona、task 和 audience。

## Phase 8：Execution Preparation 就绪复核

这一阶段仍不以接入新的 Executor Body 为目标，只验证 Personal 与 Core Head 的前置
通信边界和 Core 内部准备链是否已经稳定。

需要确认：

- ContextSnapshot、CapabilitySnapshot 和 CoreTaskSpec 均有唯一 owner。
- Personal Runtime 能形成完整、不可变的 Execution Preparation 输入。
- Native 当前使用的 Prompt、工具、知识库、Skills 和插件均能从前置边界获得，不要求
  Executor Body 自行查询；SubAgent handoff 由 Native 兼容路径自行持有，不属于此验收条件。
- Output、错误、取消、进度和完成通过统一事件返回 Personal Runtime。
- Local/Third-party 平行准备链可以被删除，而不是继续扩展。

当前已经建立 `CoreExecutionSpec`，它只保存统一 ContextPack、CoreTaskSpec、执行历史、
通用能力快照和执行身份，不保存目标渲染结果或 ProviderRequest。Spec 形成时深拷贝所有
事实数据，只有 Native `ToolSet` 作为明确的实时执行句柄保留。Native 在 Spec 形成后执行
目标投影和渲染，再通过 `NativeExecutionAdapter` 转换为官方 `ProviderRequest`；这仍是 Core
内部的 Native Executor 适配边界，不是 Personal/Core 之间的远程接口，而且 Spec 当前仍在
Native `build_main_agent` 内形成。其他 Executor Body 只有在 Output、取消和 Execution Event
边界稳定后才接入；Dify/Coze/DashScope/DeerFlow 继续作为
官方兼容路径。

这里的 `CoreExecutionSpec` 是单次进程内、由 Core Head 交给 Executor Body 的事实契约，
不是 Personal/Core 通信协议，也不是可持久化或可跨进程传输的 wire contract。当前
`CoreCapabilitySnapshot.tools` 仍保留 Native `ToolSet` 运行时对象，同时提供规范化 tool
schema；后续 Executor Adapter 只能消费规范化能力描述或显式 capability handle，不能依赖
`FunctionTool`、`AgentRunner` 或 `ProviderRequest` 对象。

`CoreCapabilitySnapshot` 不再为 SubAgent 设置独立字段。Native 继续通过 `SubagentCollector`、
`SubAgentOrchestrator` 和 `HandoffTool` 保持官方兼容，因此当前 Native ContextPack/ToolSet
仍携带 handoff 信息；该绑定应在 Capability Resolver 阶段分离。其他 Executor Body 不承担该能力，
新增场景优先通过插件 Tool 表达。

Phase 0 已确认的准备边界：

- 官方 `ProviderRequest` 是必须保留的插件兼容输入，不是未来统一执行契约。
- TaskSpec、Context/Prompt Projection、规范化附件和 CapabilitySnapshot 必须在 Core Head
  选择 Executor Adapter 之前形成。
- Executor Adapter 只负责执行能力校验、协议字段投影、任务句柄、stream、cancel/close
  和错误翻译，不重新收集 Prompt、人格、知识库或插件事实。
- 官方 `OnLLMRequest` 保留在最终低层 request projection 之后、实际执行之前；其他
  Agent/LLM/Tool Hook 按后台可观测能力映射，不伪造后台未暴露的工具生命周期。
- Third-party Stage 丢弃插件 `ProviderRequest` 的兼容缺口已经修复：显式请求直接进入
  `CoreTaskSpec` 兼容投影和 `OnLLMRequest` Hook；只有普通事件输入才从文本、图片和录音
  构建请求。现有 Dify/Coze/DashScope/DeerFlow runners 仍是兼容对象，不是新接口模板。

## Phase 9：Core Head 与 Executor Body 内部解耦（进行中：通信状态边界）

这一阶段才开始实现可替换执行器，但仍限定在 Core 内部，不改变 Personal 的对外职责，
也不引入分布式部署。

实施内容：

- 已建立进程内最小通信类型：`CoreCommand` 明确 Personal 到 Core Head 的提交、补充输入
  和取消方向；`CoreEvent` 为执行事实增加单会话递增序号；`CoreExecutionSession` 持有执行
  身份、命令幂等、会话状态和终态冲突保护。这些类型只管理协调事实，不创建队列、不运行
  Executor，也不访问平台输出。
- 当前 Native 生命周期已经在最终 `CoreExecutionSpec` 形成后，由 `InternalAgentSubStage`
  启动 event-scoped `CoreExecutionLifecycle`。它接受一次 `submit` 命令并成为执行事件写入
  `CoreExecutionSession` 的唯一协调点；既有事实再写入 turn journal 和 trace。event extra
  仅是当前 Native/Interaction 的桥接，不将 Lifecycle 放入 Personal turn state，也不代表
  完整的 Core Head 已完成。
- Lifecycle 当前已接受 `cancel` 命令并生成唯一的 `cancelled` 终态；Native runner、deadline
  和 Stage 仍保留请求停止、超时抛出与错误传播等实际副作用。后续再迁移取消句柄、超时收口、
  artifact 汇总和最终 Ledger 调用。
- 将 `CoreCommand` 与 `CoreEvent` 接入进程内双向通信：Personal 只向 Core Head 发送任务/
  补充输入/取消，Core Head 向 Personal 发布状态/进度/产物/终态；具体队列和消费策略仍待
  生命周期 owner 明确后实现。
- 将 Planner、Executor 选择、内部任务队列、重试、取消、Ledger 和事件归一化归入 Core
  Head；Personal 不直接选择或调用 Executor Body。
- 为 Executor Body 建立最小内部 Adapter 边界，先接入当前 Native Agent Runner；Adapter
  返回统一 `ExecutionEvent` 和规范化 Artifact。
- 保持 Output Dispatcher 和 Personal Expression 为唯一对外输出路径；Core Head 不直接
  调用平台发送接口。
- 为旧 Core 事件与新 Personal turn 建立 execution/turn 相关性判断，避免旧任务结果覆盖
  新对话；这属于通信消费策略，不改变 Personal turn lease 的定义。

退出条件：Core Head 可以在不修改 Personal 主链的情况下替换 Executor Body；同一任务的
状态、取消、结果和错误均通过统一事件回流；Native Executor 与未来 Executor 使用相同的
Core Head 内部契约；没有新增远程协议、跨进程队列或第二套对外输出路径。

## 当前进度（截至 2026-09-13）

已经完成：

- 根据源码重画当前消息流程。
- 建立 Personal Runtime、Personal Expression 和 Native Core 的术语映射。
- 完成插件、Prompt/Tool、Native Core 和 Subagent 的第一轮依赖盘点。
- 建立 `PersonalRuntimeKey`、PendingTurn 状态和每 Runtime 单 Turn lease。
- 将 follow-up admission 移到 Router/Persona 之前，并删除 Native 私有 follow-up owner。
- 让 Native/Third-party 共用 Runtime 串行策略，保留插件显式 `ProviderRequest`。
- 建立不可变 `RuntimeObservation`、显式 Observation event adapter 和同 Session Runtime
  admission；不把系统观察伪装成用户消息。
- 建立独立 `submit_observation()`、有界 Inbox、expiry、显式 coalesce、overflow、单 Runtime
  固定聚合窗口 task 和只读 `ObservationBatch` diagnostics；不进入 EventBus 或输出路径。
- 建立 Deterministic Gate，从规范 batch 与 Runtime state 构建 features，执行 expiry、busy、
  mute、quiet hours、cooldown、budget 和 target capability 检查；只写稳定 diagnostics。
- Observation 复用唯一 Persona 与 Output 路径，写入 assistant-only Conversation，并在
  发送失败、取消和异常时保留正确终态；多目标 Heartbeat 只重评 retained Observation batch，
  空 Inbox 不创建材料或进入该输出路径。
- 完成 Native/Third-party Runner 请求准备、Prompt、能力、Hook、session、输出和持久化
  差异审计，并确定其长期 owner。
- 删除无生产调用者的 `handle_inbound()`、`core_queue` 和 `enqueue_core` 重投递双轨，
  `ProcessStage -> handle_pipeline_event()` 成为唯一生产入口。
- 恢复 Interaction 非流式输出的内容安全与 `OnDecoratingResult` 兼容。
- 修正 RespondStage 驱动输出的发送后 Hook、visible completion 和 Turn 最终化顺序。
- 将可见 Dialogue History 与独立 Core Execution Ledger 分离；Interaction 只向 Conversation
  写入规范化用户输入和最终 Personal Expression。Ledger 使用 execution_id 记录每次执行尝试，
  不进入普通会话 API。
- Conversation 使用 `turn_id` 做持久幂等标记，并在进程内按 conversation 串行追加；
  提交失败不再把 Turn 标记为 completed。
- 规范化输入保存 `AssetRef` 元数据和已有图片转述，不复制图片二进制，也不隐式创建
  长期资产缓存。
- Native Core 已通过 `NativeExecutionAdapter` 消费 `CoreExecutionSpec` 与其后的 Native
  RenderResult；Token 统计和 Core 执行连续性独立持久化，不再依赖可见对话历史，也不绕过
  Prompt Renderer 手动追加 ProviderRequest 上下文。这里的 Adapter 仍是 Native 适配边界，
  不是可替换 Executor Body 的实现。

当前仍存在、但不应继续扩展的准备阶段边界：

- 2026-09-12：Native Interaction 已落地首个进程内执行事件切片。它基于既有
  `CoreExecutionSpec`，在所属 turn 的有界 journal 中记录 `submitted`、`working`、
  可见 Core progress、`artifact_ready` 和三种终态；事件元数据是不可变快照，终态首写获胜，
  并写入现有运行 trace。成功终态只在 Native Stage 取得稳定 runner 最终状态后写入，避免
  工具回流与最终完成之间的短暂状态误判。该切片不改变可见输出、不替代 Core Execution Ledger，
  也不构成 Executor Body、远程协议或 Third-party Agent 的适配层。它是未来 Core Head
  归一化内部事件的第一块事实基础。下一步先统一取消/超时 owner 和 Ledger 写入归属，再在
  Core 内部建立 CoreExecutionSession 与 Native Executor Adapter。

- Core Execution Ledger 现在从 `CoreExecutionSpec` 自行构造成功、失败和中止记录，并保留
  既有追加、重试和留存策略；`InternalAgentSubStage` 仍负责 Native 运行证据提取、终态选择和
  调用时机。取消/超时与最终 Ledger 调用 owner 尚未迁移到统一执行生命周期。
- Third-party Agent Stage 仍走官方兼容准备链，尚未以 `CoreExecutionSpec` 作为统一输入。
  它可以复用部分 Core task、Hook 和 capability 授权边界，但仍是需要保留的兼容现状，
  不是新 Executor Body 的实现模板。
- 通用 `Context.send_message()` 保留公开调用方式；纯文本主动消息现在经 Personal Runtime
  排队和 Output Controller 投递。同一 active turn 的 Core 工具消息明确作为 progress，
  跨 session 输出建立独立 proactive turn。纯媒体主动消息尚未形成可持久化语义材料，当前
  仍保留平台直发。显式 Context、Cron 和插件发送保持精确投递兼容，不作为 Personal Policy
  行动或自主表达去重对象。
- 已经决定发送的 Observation 输出会形成 assistant-only Conversation、Prompt History 和
  Memory history projection；通用 Inbox facts 不写 Conversation。转换层使用空 user payload
  表达 assistant-only，不伪造用户消息；Memory 只保留该回合的 `TurnRecord`。真实附件或媒体
  用户输入归一化为 `[attachment]`，不被误判为 assistant-only。
- Interaction 物理发送现在会在全量投递失败时阻止 turn completion；分段部分成功时仍缺少
  结构化 delivery receipt，canonical history 暂时无法精确表达“仅部分内容送达”。
- 可见输出完成后才同步提交 Conversation；当前有进程内锁和 `turn_id` 幂等，但没有持久化
  Turn Journal/outbox。进程在发送成功、提交历史之前退出时，仍可能留下“用户已看到、历史
  未记录”的窗口。
- `AssetRef` 在没有 Asset Store 时只提供不可解析的来源身份与已有转述，不承诺历史图片可
  再次读取。

### 2026-07-21 整体链路复核

本轮按源码重新核对 EventBus、Pipeline、插件、Personal Runtime、Prompt、Core、Output、
Conversation 和 Memory 后，确认总体分层方向成立，但以下问题是继续接 Heartbeat 或替换
执行器前的优先阻断项：

- 插件 Handler `yield ProviderRequest` 后的生成器恢复语义已修正：Core 返回后继续
  post-yield 和剩余 Handler，随后结束 delegated turn，不重复启动默认 Core。
- Personal Runtime 现在在插件 Handler 前完成 persona bind、follow-up admission 和 session
  lease；插件、Router/Persona、Core 与输出共享同一 turn 生命周期。存在 activated handler
  时不尝试 active-runner follow-up，避免插件命令被提前吸收。
- Personal Response Plan、Planner、Persona、Context Material 和 Stream Observation task 已归属 TurnExecutionScope；
  普通显式消息并发启动 Router 与 Persona，`hybrid` 放行 Core 时保留已提交的即时表达，lease
  释放前统一完成或取消所有 turn-owned task。
- Persona-only、即时 Persona 与 Core-final 使用同一 turn 级 materialization 和 completion 边界。
  Final-output reservation 会取消仍未提交的 pending Persona，但不会撤回已经送达的表达。
- 当前 session 的 `send_message_to_user` 已作为 progress 进入现有 Output Controller，不会
  重入同 session lease 或提前完成 turn；跨 session 文本输出使用独立 proactive turn。
- 全量物理发送失败和 canonical material 缺失已在本轮修正；分段部分成功仍缺 delivery
  receipt，after-send hook 的 stop 语义也可能让已送达内容被标记 cancelled。
- Observation 已有输入/输出契约；多目标 Heartbeat 和受控群聊 `conversation_activity` 已通过现有
  生命周期与官方 Pipeline 接入 Inbox。assistant-only history projection 和受控 Action 已完成，
  因此系统已有最小主动表达能力；其他 Runtime Sensor 与未来后台执行仍未完成。
- Native 已消费 `CoreExecutionSpec`，Third-party 仍是官方兼容请求链。两者的上下文、
  capability、execution identity、ledger 和错误状态尚未统一，暂不适合直接抽象成等价
  Executor。
- EventBus 在逐事件任务创建前的配置解析与 scheduler 查找缺少异常隔离。该问题属于官方
  调度基础设施风险，不应在 Interaction 内打补丁，但后续吸收上游或修改官方边界时需要
  单独处理。

本轮静态依赖复核覆盖当前 474 个 `astrbot.core` 模块。修正 Process SubStage 对
`process_stage.stage` 的偶然反向导入，以及 `star_manager` 对 `star` 包初始化顺序的依赖后，
顶层运行时 import 强连通分量为 0。
当前没有已知顶层 import cycle，但仍有以下接口方向债务：

- Prompt 直接消费 `AstrMessageEvent`、插件 `Context` 和 `ProviderRequest`，尚未只依赖
  runtime fact ports。
- Provider 的 output-contract tool adapter 已迁入 Provider 协议层，不再反向依赖 Prompt。
- Interaction 使用 `agent.tool` 描述 Persona 工具，能力契约尚未从 Native Agent 包中独立。
- `CoreCapabilitySnapshot` 仍携带 Native `ToolSet` 运行时对象，只是浅层 frozen，不是
  可跨 Executor 或跨进程的不可变契约。
- `PersonalTurnContext` 已建立，但平台主链仍通过 117 个 literal event extra key 协作；
  typed context 还不是实际唯一事实源。

依赖结构图见 `runtime-dependency-structure.mmd`。

下一步继续收口 Execution Event、取消、Ledger owner 和 Output Port；随后在 Core 内部
建立稳定的 Core Head，不直接把现有 Third-party Agent SubStage 改名或包装成新执行器接口。

### 2026-09-02 方案与源码复核结论

当前方案的方向与 `target-state.md`、`current-state.md` 和本流程图一致，但尚未满足
正式实现 Core 内部可替换 Executor Body 的退出条件：

- `CoreExecutionSpec` 已是 Native 的进程内准备事实契约，但仍在 `build_main_agent` 内形成，
  且 `CoreCapabilitySnapshot.tools` 保留 Native `ToolSet` 实时句柄。
- Core Execution Ledger 的成功、失败和取消记录仍由 `InternalAgentSubStage` 收尾；统一
  Execution Event 尚未成为 Native 与 Third-party 的共同回流协议。
- `event.send()` / `event.send_streaming()` 仍有 MethodType interception 和
  `_interaction_original_send*` 兼容面，正式 Output Port 尚未完全接管。
- typed Runtime Context 尚未成为唯一内部事实源，部分 `_interaction_*` extra 仍参与主链协作。
- 发送回执与 Conversation 提交之间仍存在进程退出窗口，分段部分成功也缺少完整 delivery
  receipt。

因此当前允许继续做 Execution Preparation 收口和 Core Head 通信边界设计；暂不创建空置的
`ExecutionBackend` 抽象，也不把现有 Native/Third-party Stage 直接包装成 Executor。

### 2026-09-12 Personal/Core/Executor 复核结论

- 已提交的 `1b40f151d` 只建立 Native Core 执行事件事实记录；它没有引入 Personal 与 Core
  的远程边界，也没有决定 Executor 的部署形态，符合当前阶段目标。
- 当前未提交的 Native 终态收口修复把成功、产物和失败事件移动到稳定的 runner 最终状态处，
  解决了工具回流阶段的过早 `max_steps_exhausted` 误判；它属于 Native Executor 内部修复，
  不应被解释为 Core Head 已经完成。
- 在这次 2026-09-12 的复核时，Personal 与 Core Head 的完整消息/事件通信、Core 内部
  Executor 调度仍是设计项，不能通过当时的 `event.extra`、turn lock 或直接回调隐式替代。
  后续同日的 Phase 9 切片已经实现 `CoreExecutionSession` 与 Lifecycle 基础；完整通信和
  调度边界仍未落地。
- 后续实现必须保持三条边界：Personal 统一对外输出；Core Head 持有任务与事件协调；
  Executor Body 只负责被 Core Head 调度的复杂执行。委派只建立通信会话，不改变 Personal
  turn 的生命周期或输出所有权。

### 2026-09-12 Phase 9 首个实现切片

- `CoreCommand`、`CoreEvent`、`CoreExecutionHead` 和 `CoreExecutionSession` 已以纯进程内类型进入
  `astrbot.core.execution`。Session 只接受匹配 `execution_id` 与 `turn_id` 的命令和事件，
  提交命令按 `command_id` 去重；状态事件按类型首写，progress 保留每次观察，artifact 则按稳定
  `artifact_id` 去重，因此同一执行可携带多个不同 artifact。终态首写后拒绝冲突终态。
- 当前 `InternalAgentSubStage` 作为 Native lifecycle adapter，在 Hook 后形成的最终 Spec
  上绑定 Session。`run_agent`、Stage 收尾和异常路径仍通过既有 event journal 入口记录事实；
  入口会通过 Session 统一校验、编号，再保留原有 journal/trace 行为。该切片不改变 Personal
  输出、turn lease、执行和 Ledger 行为，也不创建真实队列或新的 Executor Body。下一步才是
  将这个临时 adapter 收口为明确的 Core Head lifecycle owner。

### 2026-09-12 Phase 9 第二个实现切片

- `CoreExecutionLedger.append_execution()` 现在以 `CoreExecutionSpec`、会话 ID、执行器 ID、
  状态和 Native 已提取的证据构造 `CoreExecutionRecord`，随后复用既有 `append()` 的唯一 ID、
  SQLite 重试与按会话留存行为。数据库 schema、读取投影和可见对话历史均未改变。
- `InternalAgentSubStage` 不再导入或直接构造持久化对象；它只保留 Native runner 的消息证据
  提取和 completed/failed/aborted 状态选择。这是 Ledger 的记录材料归属收口，不代表 Stage
  已不再参与生命周期，也不代表取消/超时 owner 或新的 Executor Body 已落地。
- 该切片完成前，下一阶段是定义明确的 Core Head lifecycle owner；其后才迁移取消、超时和
  最终 Ledger 调用，且不引入传输队列、远程协议或输出路径变更。

### 2026-09-13 Phase 9 第三个实现切片

- `CoreExecutionLifecycle` 成为一个 execution-scoped 的进程内协调 owner：它只接受一次
  `submit` 命令，并让 Native 的 `submitted`、`working`、progress、artifact 与终态事实通过
  同一 Session 排序和校验。重复启动兼容地无操作，避免旧 Session bridge 造成二次提交异常。
- Lifecycle 还从 Session 的唯一终态归一化现有 Ledger 状态：`completed`、`failed` 和
  `cancelled`，用户主动停止保留兼容的 `aborted` 标记。这样 Native runner 内部已失败、但未向
  Stage 抛异常时，不会再被普通收尾路径错误记作 `completed`。
- `InteractionTurnState` 的既有 journal/trace 投影现在通过 Lifecycle 写入，而非直接操作
  `CoreExecutionSession`；`InternalAgentSubStage` 只负责在最终 Spec 形成后启动 Lifecycle。
  Ledger 的最终调用位置尚在 Stage，但其状态依据 Lifecycle 终态而非 Stage 局部推断；当前没有
  迁移 executor runner、取消句柄或任何平台输出职责。
- 下一步仍是将超时、artifact 汇总和最终 Ledger 调用移动到这个 owner；先以 Native 实际
  生命周期验证，不提前创建队列、可替换 Executor 接口或远程通信层。

### 2026-09-13 Phase 9 第四个实现切片

- 所有已经通过既有 `record_interaction_turn_core_execution_event()` 记录的 `cancelled` 事实，
  在存在 `CoreExecutionLifecycle` 时都会先进入 `CoreExecutionLifecycle.cancel()`：它接受一条
  `CoreCommand.cancel`，写入一次 `cancelled` 终态，再由 Interaction 继续投影到既有 journal 和
  trace。重复取消保持幂等；完成或失败后的取消仍被拒绝。
- Native runner 的 `request_stop()`、deadline 的异常传播、Stage 的 cleanup 和 Personal turn
  lease 均未迁移。此次仅收拢取消的 Core 会话语义与事件来源，不改变何时请求停止或谁发送输出。

### 2026-09-13 Phase 9 第五个实现切片

- `CoreExecutionLifecycle` 现在可在启动后的 active Native runner 上绑定一次幂等停止回调。
  它仅在成功写入唯一 `cancelled` 终态后请求 runner 停止；重复取消复用已有终态，不会重复
  调用回调，已完成或失败的执行仍拒绝取消。Core Head 因而拥有取消命令到 Executor Body 停止
  请求的单向通信点，但不拥有 runner 的轮询、异常传播或 cleanup。
- Native Stage 仍构造、运行和清理 `AgentRunner`，并在 runner 已创建、最终 Spec 已确定后绑定
  `agent_runner.request_stop`。这保持当前同进程边界和既有 stop watcher，不改变 Personal 对外
  输出和 turn 生命周期；后续才评估 timeout、artifact 汇总与 Ledger 最终调用的迁移。

### 2026-09-13 Phase 9 第六个实现切片

- Native adapter 的 `TurnDeadlineExceeded`、外层 task cancellation 和 runner 内部异常现在都会
  收敛为已经排序的 Session 终态后，再写入同一个 `CoreExecutionLedger` 投影。deadline 在
  `asyncio.CancelledError` 形式穿过 runner 时会保留为 `deadline_exceeded`，不再退化成笼统的
  `stage_cancelled`；取消记录保留工具证据和终止原因，但不写入可见对话历史或 Conversation token。
- runner 内部异常会携带受限长度的实际错误文本进入 failed 终态，Ledger 优先保留该证据；外层
  Native 异常也走相同投影。执行记录继续按 execution_id 保持一次性写入，Stage 仍是当前 SQLite
  调用位置，Lifecycle 只提供终态与终止证据，不直接依赖数据库。
- 可替换 Executor 的 stop callback 若抛出普通异常，取消终态和 turn journal 仍会完成，并额外
  写入 `core_execution_stop_callback_failed` trace 事实。Native `request_stop()` 不会触发此路径；
  callback 的重试策略、timeout owner、artifact 汇总和最终 Ledger owner 仍留待后续切片。
- 外层 deadline/task cancellation 固定投影为 `cancelled`，不会因 runner 同时携带 stop 标记而
  误分类为用户 `aborted`。终态错误在 Lifecycle 投影处统一限制为 2000 字符；正常、取消和失败
  的 Ledger 写入失败都会留下同一组 event diagnostics 及
  `core_execution_ledger_persist_failed` trace，供后续运行验证定位。

### 2026-09-13 Phase 9 第七个实现切片

- 增加显式 `CoreExecutionHead`，作为一个 execution-scoped 的进程内同步入口，统一承接
  初始提交、Core command、执行事件记录、取消和本地事件订阅。Head 通过事件序号保证同一
  事实不会向订阅者重复发布，且单个订阅者失败只留下诊断、不影响执行状态或其他订阅者；它不
  创建后台队列、不选择 Executor、不持有平台输出，也不改变 Personal turn 的生命周期。
- 增加 `bind_core_execution_head()` / `start_core_execution_head()`；旧的 Lifecycle 绑定和启动
  API 继续保留并转发到 Head，Native Interaction 主路径改用 Head 入口。这样后续可以先迁移
  Core Head 的 owner，再逐步接入命令/事件消费，而不需要一次性改写现有调用者。

### 2026-09-14 Phase 9 第八个实现切片

- Lifecycle 的旧事件入口与 Head 共享同一发布路径；Head 订阅者逐个隔离异常，单个观察者失败
  不会阻断执行状态、其他订阅者或 Interaction turn journal。
- 绑定时校验 `CoreExecutionHead`、`CoreExecutionLifecycle` 与 `CoreExecutionSession` 的对象和
  执行身份一致，拒绝残留或错配的 event extra，避免事件写入错误会话。
- 事件重放键从“所有非 progress 事件按类型唯一”收紧为：状态事件按类型首写、progress 保留
  每次观察、artifact 按稳定 `artifact_id` 去重；Native 最终结果使用 `final_response` 作为聚合
  artifact ID。

### 2026-09-14 Phase 9 第九个实现切片

- Interaction turn 的 bounded execution journal 与 trace 现在作为 `CoreExecutionHead` 的明确本地
  event consumer 绑定。Head 或旧 Lifecycle 入口产生的事件均先由 Session 校验和编号，再经 Head
  发布并投影到既有 journal/trace；Native Stage 只在启动 Head 后完成一次绑定。
- 既有 `record_interaction_turn_core_execution_event()` 保留为 Native 兼容入口，但在存在 Head 时
  只负责提交事实，不再自行维护第二套 journal 写入路径。没有 Head 的旧桥接仍保留原有投影行为。
- `cancelled` 终态会先通过 Head 发布并投影，再请求已绑定 Executor 停止；stop callback 失败会
  作为紧随终态的独立 trace 诊断记录，因此不阻断或延迟 Core Session 的取消收敛。
- 当本地 journal 达到上限时优先淘汰最旧 progress；若新事实为终态且没有 progress，则淘汰最旧
  artifact，保证终态和对应 trace 不会因 artifact 饱和而丢失。
- 此处的 consumer 仍是同步、进程内的投影，不是命令/事件队列、Personal 消费协议或平台输出通道。

### 2026-09-14 Phase 9 第十个实现切片

- `CoreExecutionOutcome` 由 Lifecycle/Head 根据唯一终态和已确认的 `artifact_ready` 事实生成，提供
  后续 Ledger 投影所需的 status、受限 terminal error、terminal event 与 artifact 汇总；它不携带
  Runner 的消息、最终文本或 token usage。
- Native Stage 继续拥有 Runner 证据提取及现有 SQLite Ledger 调用，但不再分别向 Head/Lifecycle
  询问状态和终止错误，而是消费同一份 Outcome。这是 final Ledger preparation 的收口，不是 Ledger
  owner 或数据库依赖迁移。
- timeout owner、取消 cleanup、持久化端口、队列和 Executor Adapter 都没有随本切片提前引入。

### 2026-09-15 Phase 9 第十一个实现切片

- `CoreExecutionLedgerPreparation` 成为 Lifecycle/Head 面向现有 Ledger 边界的非持久化结果材料：
  它使用已确认终态统一确定 status、error 与 result 保留规则，并保留 Outcome 供后续 Adapter 使用；
  它不引用 SQLite、conversation、平台 Event 或可见输出。
- Native Stage 继续提取 Runner 消息、token usage 与 conversation ID，并调用现有 Ledger；但不再
  自行组合成功、失败、取消或中止的 result/error 规则。只有旧 Native 路径无法形成终态事件时，
  才向 Core 提供受限 fallback 事实。
- Personal 的总 turn deadline 仍是唯一总预算。Core 只消费由上游 timeout/cancellation 形成的
  终态事实；本切片没有把 deadline 移入 Core，也没有创建独立 timeout 或队列。

### 2026-09-15 Phase 9 第十二个实现切片

- `CoreExecutionDeadlineView` 将 Personal 持有的 `TurnDeadlineBudget` 暴露为只读剩余时间视图；
  Core 不复制计时器、不延长总预算，也不取得 deadline 的可变 owner。
- Native Head 启动后绑定该视图。ProcessStage 在 Personal 报告 turn deadline 到期时，通过
  `CoreExecutionHead.cancel_for_deadline()` 统一写入 `cancelled` 终态并请求当前 Executor stop；
  Core 只负责执行终态收敛，Personal 仍负责用户可见的超时处理。
- 本切片不引入 Core 自主 watchdog、第二个 timeout、队列、持久化迁移或新的输出路径。

### 2026-09-15 Phase 9 第十三个验证切片

- 通过现有 Interaction journal bridge 验证 deadline 取消后的重复取消与迟到终态保护：同一执行
  只保留一次 `cancelled`，迟到的 `completed/failed` 不会重新打开 Session、重复触发 stop
  callback 或污染 Outcome。
- 本验证没有增加事件类型、队列或 Executor 适配层。

### 2026-09-16 Phase 9 第十四个真实 trace 审计切片

- 对当前可读取的 `astrbot.trace.log` 中 `core_execution_event` 记录按
  `execution_id` 重组后，发现两条完整执行链：一条以 `failed/max_steps_exhausted` 终止，
  另一条按 `submitted -> working -> progress -> artifact_ready -> completed` 正常终止。
- 本次样本没有发现终态之后继续回流事件、重复终态或跨执行身份混入；同时没有找到
  `cancelled`、deadline expiry、迟到终态或 stop callback failure 样本，因此不能把真实
  deadline 时序标记为已验收。
- 本切片只做日志审计，不修改运行逻辑；下一步仍需一次可控的 OLV deadline/cancellation
  现场验证，再决定是否进入 Native Executor Adapter。

### Phase 9 当前复核结论

截至本次复核，Phase 9 已完成十二个连续的 Native 基础切片，并完成一个 deadline 时序验证切片：执行会话与事件类型、Lifecycle
协调、Ledger 材料归属、取消命令、Executor stop callback、deadline/异常/取消终态与证据收口、
显式 `CoreExecutionHead` 同步入口、Interaction journal/trace 的本地事件消费，以及无持久化依赖的
Ledger 结果材料准备、Personal deadline 的只读协作与 Core 取消入口。它已经提供了 Core Head 后续
扩展可使用的进程内事实边界，但当前仍由
`InternalAgentSubStage` 创建和运行 Native Runner。

因此当前状态应表述为：

- **已具备**：统一的执行身份、事件序号、命令幂等、终态保护、取消入口、受限终止证据与
  生命周期拥有的 Outcome 与 LedgerPreparation 汇总，以及 Personal deadline 的只读协作入口；
- **尚未具备**：Core Head 的完整生命周期 owner、命令/事件队列、Executor Adapter、统一 Artifact
  回流、第三方 Runner 迁移和可替换 Executor Body；
- **明确不做**：Personal/Core 远程化、分布式消息系统、第二套对外输出路径。

下一步验证 deadline 到期、外层 task cancellation、重复取消和旧任务迟到结果的真实 Interaction
时序；确认 Head 的终态、stop callback 和 LedgerPreparation 只收敛一次。该边界经过真实验证后，
才建立 Native Executor Adapter。
不得把现有 Lifecycle 直接更名为 `ExecutionBackend`，也不得先接入第二个执行器来反向逼迫接口设计。

### 2026-09-19 Phase 9 第十五个通信契约切片

- `CoreExecutionHead` 新增 `dispatch_command()`，通过 `CoreCommandReceipt` 明确返回
  `accepted` 或幂等 `duplicate`。命令接收不再被误解为执行完成；真正的执行状态仍只能由
  `CoreEvent` 回流。
- `CoreCommand` 现在显式记录 `origin`（默认 `personal`），回执同时返回命令来源和接收后的
  session 状态。该字段只描述进程内通信方向，不把 Personal 变成 `CoreExecutionSession`
  的 owner；session 仍由 Core Head/Lifecycle 持有。
- `CoreExecutionHead.subscribe_mailbox()` 提供了第一版异步事件消费边界。邮箱只接收订阅
  后的新 `CoreEvent`，在终态事件后关闭；它不重放历史、不运行 Executor，也不拥有
  Personal turn。邮箱有界，压力下只丢弃旧 `progress`，并支持主动取消订阅；原有同步
  订阅继续保留，便于逐步迁移观察者。
- mailbox 在检查队列前清除唤醒标记，确保发布发生在检查与等待之间时不会丢失唤醒；
  该顺序是异步消费边界的必要不变量。
- `CoreExecutionHead.subscribe_command_mailbox()` 现在观察订阅后被接受的
  `CoreCommand`，包括后续 `cancel` 和 `provide_input`，以及在订阅前尚未启动时的
  初始 `submit`；它不回放订阅前历史，重复 command 不会再次发布。它是旁路观察者而不是可靠命令队列：压力下只允许丢弃旧的
  `provide_input` 观察项，`submit/cancel` 控制事实保留；实际命令是否接受仍以 Head
  回执和 Session 为准。它不执行命令、不拥有 session，也不引入后台 worker。
- `CoreExecutionHead.close()` 现在只关闭事件投递，不改变 `CoreExecutionSession` 状态；
  适配器拆除或外层取消可以释放邮箱而不伪造 `cancelled` 事件。终态事件仍由 Head 自动
  关闭所有邮箱。
- `CoreExecutionHead.emit_event()` 现在负责从执行身份创建、排序并发布事件；Interaction
  桥接层只负责准入校验和投影，不再在 Head 路径中自行构造事实对象。Legacy 无 Head
  路径仍保留为兼容边界，尚未宣称完成 Native 生命周期迁移。
- 本切片没有引入后台队列、长期消费 worker、远程传输或新的 Executor Body，也没有改变
  Native Runner、Personal turn lease、Output 或 Ledger owner。
- 下一步仍是明确 Personal/Core Head 的 session ownership 与异步消费边界，再将 Native
  生命周期适配逐步迁入 Core Head。
- 结算归属继续按“材料与幂等由 Core、持久化由 Native Ledger”拆分：`CoreExecutionHead`
  现在持有一次性 Ledger settlement claim，避免取消/失败/正常收尾路径重复解释同一个
  execution；现有 SQLite Ledger 仍由 Native 调用，写入失败会释放 claim 允许重试。
- 2026-09-19 起，`CoreExecutionHead.settle_ledger()` 负责执行这一次性结算的
  claim/release 协调，并接收一个异步 append 回调；`InternalAgentSubStage` 不再直接
  操作 claim 或在异常时自行释放。Head 不依赖 SQLite 或具体 Ledger，实现仍由 Native
  通过回调提供，因此这是 owner 收口而不是存储层迁移。
- 2026-09-19 时序复核确认：deadline 取消后的迟到终态不会覆盖已取消 Session，重复
  取消保持幂等，Ledger 写入失败后 claim 可释放并重试。该验证只覆盖当前 Native
  适配路径，尚未宣称 Personal/Core session ownership 或 Ledger 持久化 owner 已迁移。
- `CoreExecutionHead.complete()` / `fail()` 收拢了 Native 的终态收尾入口：成功路径由
  Head 保证 `artifact_ready -> completed` 顺序，失败路径统一发布 `failed`；无 Head
  的 Legacy 桥接继续使用原有投影。该切片不改变可见输出、Ledger 持久化或 Executor
  选择，只减少 Native 分支对终态顺序的重复解释。
- 终态 API 的回归验证覆盖了合法的 `submitted -> working -> artifact_ready ->
  completed` 顺序，以及失败终态的单一性；测试不会把 `start()` 的命令接收误当成
  `submitted` 事实。
- 终态入口全覆盖审计确认：有 `CoreExecutionHead` 的 Native 主路径均经由 Head
  生成终态，`astr_agent_run_util` 的取消/失败事实也只通过 Interaction bridge 投影；
  仅 Legacy 无 Head 分支保留旧 Lifecycle 投影。当前已有的 `NativeExecutionAdapter`
  仍只是 Prompt/Capability 投影器，不是 Executor Adapter，不能通过改名提前宣称
  可替换执行器已经落地。
- 因取消、终态顺序、结算幂等和迟到结果边界已经完成聚焦验证，下一切片可以开始设计
  Native Executor Adapter 的真实最小契约；实现范围应只覆盖 Native runner 的启动、
  事件回流、停止和关闭，不迁移 Output、TTS、Ledger 持久化或 Personal turn owner。

### 2026-09-19 Phase 9 第十六个 Native Adapter 最小切片

- 新增 `NativeExecutorAdapter`，将当前 Native `ToolLoopAgentRunner` 暴露为
  Core 内部的控制/观测边界。当前契约只包括 `request_stop()`、完成状态、取消状态、
  最终响应、消息证据、统计数据和 provider 观测。
- `InternalAgentSubStage` 的正常完成、取消、失败、历史保存和 provider 统计路径现在
  统一通过该适配器读取执行证据；底层 runner 仍仅保留给运行时注册/注销和现有流式桥接
  使用，避免出现成功路径已解耦、异常路径仍直接读取 runner 的半套边界。
- 本切片没有迁移 `step()`、stream contract、Output/TTS、Ledger 持久化或 Personal
  turn owner，也没有伪造 `close()`。当前 `runner` 属性是明确标注的过渡桥接访问面，
  后续在定义执行事件/流契约后再移除。
- 增加适配器的最小控制与观测边界验证。下一步应在不改变可见输出的前提下，继续明确
  Native 执行事件如何由 Adapter 回流到 Core Head，再决定是否需要独立的关闭语义。

### 2026-09-20 Native step 驱动与资源回收

- `run_agent()` 通过 `NativeExecutorAdapter.step()` 消费 Native 响应，停止监视通过
  Adapter 请求停止。返回值仍是 Native `AgentResponse`，不是跨执行器事件协议。
- 每个 step 的消费方在 `finally` 中取消并等待停止监视任务、关闭 step 异步生成器，
  覆盖正常结束、提前返回、deadline、任务取消和消费方关闭。
- Prompt、最大步数处理、Hook、输出转换和 TTS 仍在现有边界；没有增加 worker，
  没有接入 Personal 异步消费，也没有提前移除 runner 桥接访问。
- 验证：Adapter 与 Core execution 定向测试 48 项通过；尚未做真实 OLV 验收。

### 2026-09-20 Native 事实回流收口

- `NativeExecutorAdapter.emit_event()` 成为 Native 执行事实进入 Core Head/Interaction
  journal 的入口；`run_agent()` 的 working、cancelled、failed 事件不再直接调用
  Interaction 记录函数。
- 该入口只负责事件归属和 executor identity，不转换用户输出、不改变 TTS、Prompt、
  Personal 或 Ledger 行为。Native 响应仍是当前过渡期的 Adapter 内部类型。
- Adapter 边界测试覆盖事件回流；Core/Interaction 定向测试 83 项通过。

### 2026-09-20 Native 执行循环内部边界统一

- `run_agent()`、`run_live_agent()` 和 TTS feeder 的内部状态读取统一经由
  `NativeExecutorAdapter`，包括上下文、流式模式、完成状态、统计数据和 provider。
- 生产调用链已全部在 Stage 构造一次 `NativeExecutorAdapter`，因此这两个执行入口现在只接受 Adapter，不再在入口处隐式包装裸 `AgentRunner`。
  这能让绕开 Core Head 的内部调用在立即暴露，而不改变响应、工具状态、TTS 或输出时序。
- `AgentRunner` 类型别名仍仅供 Adapter 构造和统计 fallback 读取使用；它不再是主执行循环的入口契约。
- 这一步不引入新执行器协议，不迁移执行循环 owner，也不触动现有可见输出、TTS、历史或 Ledger。

### 2026-09-20 Internal Agent 统计兼容边界标注

- `InternalAgentSubStage` 的生产路径已经始终通过 `NativeExecutorAdapter` 读取
  provider、stats 和 aborted 状态。
- `_record_internal_agent_stats()` 仍保留旧的 positional runner 参数，仅作为历史
  内部调用的 fallback，并明确命名为 `legacy_agent_runner`；这次不改变旧调用行为，
  也不把它重新作为 Core 执行边界。

### 2026-09-20 Personal Follow-up 执行器命名收口

- Personal Runtime 的 follow-up 协调器内部状态、actor 查找和注册参数统一使用
  `executor` 命名；它实际保存的是 `NativeExecutorAdapter`，不再把 Native runner
  误称为 Personal 的长期执行对象。
- 对外已有的 `register_active_runner()` / `unregister_active_runner()` 方法名暂不改，
  以避免扩大 Personal Runtime 公共调用面的变更；方法内部已转交 executor。
- 该切片只改变命名和边界表达，不改变 follow-up 捕获、顺序激活、取消或清理行为。

### 2026-09-19 Native 生命周期事实归属收口

- `InternalAgentSubStage` 不再直接写入 Native 的 `submitted`、最终 artifact、
  `completed`、`failed` 或 `cancelled` 执行事实；它只负责装配、输出桥接、历史和
  现有持久化调用。
- `NativeExecutorAdapter` 现在拥有这些事实到 Core Head/Interaction bridge 的投影，
  并在同一边界生成 final response 的可诊断 artifact metadata。
- Adapter 在投递成功 artifact 前检查已存在的 Head 终态，确保取消或失败先到时，
  Native 的迟到成功不会追加 artifact 或覆盖既有终态。
- 这仍不是跨执行器 response/stream 协议：`AgentResponse` 留在 Native Adapter 和
  现有输出桥接内部，尚未迁移 Core 的完整执行循环或 Artifact 实体。

### 2026-09-19 Native 进度事实与输出投递分离

- `NativeExecutorAdapter.observe_response()` 仅将 Native tool call / tool result
  转为非可见的 `progress` 事实，记录调用 ID、工具名、消息类型、组件数和结果长度；
  不记录工具结果正文、模型 token 或推理文本。
- `_send_core_event_message()` 只保留当前的可见投递/抑制职责，不再以是否发送状态消息
  反向决定 Core progress。Personal 仍是 delegated Interaction 的唯一对外表达窗口。
- Native 的取消和失败分支也统一调用 Adapter 的终态 API，避免执行循环绕过终态 owner。

### 2026-09-19 Core Head Executor 绑定与终态释放

- `CoreExecutionLifecycle` 现在显式绑定一个 executor identity 与停止回调；绑定期间，
  其他 executor 的执行事实会被拒绝，防止同一 Core session 的状态来源漂移。
- 只有 session 已进入终态后才能释放 executor identity 和停止回调。外层异常路径会再次
  尝试释放，确保在其写入 `failed` / `cancelled` 终态后完成清理；活跃任务不会提前失去
  Core cancel 能力。
- `NativeExecutorAdapter` 负责绑定和释放自身，Stage 不再直接操作 Core Head 的 Native
  stop callback。该语义仍不伪造 runner `close()`，Native step stream 的关闭继续由其
  消费方在 `finally` 负责。

### 2026-09-19 Native 响应容器收口

- `NativeExecutorAdapter.stream()` 将 Native `AgentResponse` 转换为内部
  `ExecutorStreamItem(kind, chain)`；`run_agent()` 不再直接读取 `AgentResponse.data`，
  Native response 容器只保留在 Adapter 内部。
- Adapter 在 `stream()` 的 `finally` 中关闭底层 Native step 流，因此提前结束、取消、
  超时和异常不会只关闭包装层而遗留 Native 生成器。
- 流式完成阶段所需的最终可见 `MessageChain` 也由 Adapter 构造；Stage 不再解释
  `LLMResponse.completion_text/result_chain` 的优先级，但无最终响应时仍保持原有“不设置
  `STREAMING_FINISH`”行为。
- 现有输出桥接仍消费 `MessageChain`，因此没有改变文本、工具状态、流式 TTS 或平台
  投递行为；这一步只是缩小 Core 执行循环对 Native response 结构的依赖。
- 该流项目仍是进程内过渡契约，不等同于最终跨 Executor 的 `ExecutionEvent`/Artifact
  协议；后续仍需把第三方 Runner 映射到相同的 Core 事实边界。

### 2026-09-20 类型化执行产物描述

- 新增 `CoreExecutionArtifact`，显式区分 `artifact_id`、`artifact_kind` 与只读诊断属性；
  Native Adapter 不再向 Core Head 传递松散的 artifact metadata 字典。
- Artifact 只描述执行产物身份和有界诊断，不承载回复正文、平台消息、TTS 或 effect；
  可见内容仍由现有 Output Runtime 和 Personal 边界负责。
- 属性在构造时冻结并禁止覆盖 `artifact_id` / `artifact_kind` 保留字段，Core Head 再将其
  投影为现有 `artifact_ready` 事件 metadata，因此事件排序和按 artifact ID 去重保持不变。
- `CoreExecutionOutcome.artifacts` 返回类型化 `CoreExecutionArtifact`，不再要求 Ledger
  或后续协调者重新解析原始事件 metadata；原始 `CoreEvent` 仍保留在 Session journal
  作为审计事实。
- 这仍不是最终 Output Artifact 或跨进程传输协议；它先稳定 Core 内部 Executor 结果边界，
  后续再根据第二个真实 Executor 的需求扩展内容引用和产物交付。

### 2026-09-20 类型化执行进度描述

- 新增 `CoreExecutionProgress`，将执行进度的 `source`、`phase` 与有界属性分开；Native
  Adapter 先构造该对象，再投影为现有 `progress` 事件 metadata。
- 现阶段保留已有 `response_type`、工具 ID、工具名、组件数和结果长度字段，避免改变日志、
  trace 或 Personal 的可见行为；进度正文、模型推理和原始工具结果仍不进入 Core 事件。
- `CoreExecutionProgress` 的保留字段不可被属性覆盖，属性同样采用不可变快照。它是 Core
  内部事实边界，不是新的输出消息或跨进程传输协议。

### 2026-09-20 Native 输入结果携带执行规格

- `NativeExecutionInput` 现在显式携带生成该 `ProviderRequest` 的同一份
  `CoreExecutionSpec`，与 `prompt_apply_result` 和请求本身保持同一输入身份。
- 这只是把执行身份、任务和能力快照沿 Adapter 结果返回，避免未来 Executor Body 通过
  ProviderRequest 反向猜测 `execution_id` 或能力来源；当前 Native 请求内容和调用时序不变。
- `CoreExecutionSpec` 仍是进程内准备事实，不是远程协议；第三方 Executor 尚未接入。

### 2026-09-20 Native 终态投影收口

- `NativeExecutorAdapter.finalize()` 统一根据 Native 的完成状态和最终响应投影
  `artifact_ready -> completed` 或 `failed`；`InternalAgentSubStage` 不再重新解释
  Native 成功/失败并选择 artifact metadata。
- 外层取消、deadline 和异常仍通过显式 `cancel()` / `fail()` 路径处理，保留 Core Head
  的终态优先级和迟到结果保护；本切片不改变历史保存、输出、TTS 或 Ledger 时序。
- 这一步进一步缩小了 Stage 对 Native runner 状态的解释职责，但尚未把执行循环本身迁入
  Core Head，也没有创建新的 Executor Body。

### 2026-09-20 Native 收尾证据读取

- `NativeExecutionEvidence` 汇总正常收尾和 Live 历史保存使用的响应、消息、统计和中止标记，
  由 Adapter 提供。它是 Native 内部证据集合，不是通用执行协议或深度不可变快照；
  响应、消息和统计仍引用现有对象。
- 取消及失败路径保留分项读取和容错，避免某一项无法读取时丢失其他可保存证据。
- Ledger 仍消费 Core Head 的终态准备结果并通过其结算入口写入；本次没有迁移历史或
  Ledger owner。定向验证 67 项通过，真实 OLV 验收尚未执行。

### 2026-09-20 Core Head 执行器激活边界

- `CoreExecutionHead.activate_executor()` 现在作为一次 Core 操作完成会话启动、唯一
  executor identity/stop callback 绑定，以及 `submitted` 事实投影。Stage 只提供 Native
  executor 的 ID、停止回调和提交诊断，不再分别调用 Adapter 的 bind/submit 方法。
- 同一执行器与同一停止回调的重复激活复用已有 `submitted` 事实，不重复写入事件；终态后
  的激活明确失败。取消仍由 Head 先写入 `cancelled` 再请求已绑定的停止回调。
- 该边界不把命令 mailbox 误作执行队列，也不让 Core Head 运行 `run_agent()`；Native 执行循环、
  可见输出、TTS、历史保存与平台投递仍在当前 owner。定向 Core/Adapter 验证 102 项通过。

### 2026-09-20 Head-first 停止信号取消

- Native step loop 和停止观察器检测到 turn stop signal 后，统一调用
  `NativeExecutorAdapter.request_cancellation()`。存在 Core Head 时，先由 Head 接受并写入
  `cancelled`，再通过已绑定的 stop callback 请求 Native runner 停止。
- 没有 Core Head 的兼容路径才直接请求 Native stop，并继续投影原有 cancelled 事实；这保证
  非 Interaction 或旧桥接路径不会因迁移而失去停止能力。
- 终态仍遵从 first-write 规则，重复 stop signal 不会产生第二个取消事件或重复 callback；用户
  中止仍保持既有 `agent_aborted` 原因。此切片不改变命令 mailbox、可见输出、TTS 或执行队列语义。

### 2026-09-20 主动 Core 轮次纳入 Native Adapter

- Cron 与后台完成回调共用的主动 Core 轮次不再直接调用
  `AgentRunner.step_until_done()`；`NativeExecutorAdapter.run_until_done()` 现在统一负责响应归一化和底层异步生成器关闭。
- 只有构建结果已带 `CoreExecutionSpec` 时，主动轮次才绑定并激活 Core Head，再回流
  `submitted -> working -> progress -> terminal` 事实。没有执行身份的旧路径仍只使用 Adapter 运行，不伪造新的执行会话。
- 正常、取消和失败分支只在 Head 已激活后写入终态；Ledger 持久化仍留在主动轮次，但在有 Head 时经其一次性 settlement claim 协调。
- 此切片不改变主动消息的发送、Personal 的可见表达责任、Cron 重试、历史保留或台平发送路径。
- 新增 Adapter 生成器关闭边界测试；当前 Windows 运行环境下，这两个定向 pytest 模块会在导入初始化链卡住，因此未将 pytest 记为通过；`compileall`、Ruff 和 diff 校验已通过，仍需一次实际 Cron/后台任务验收。

## 非目标

- 当前不实现 Claude Code、OpenCode 或新的 Executor Body。
- 当前不创建空置的 Executor 接口、Capability Gateway、远程协议或分布式队列。
- 不把所有插件转换成 MCP。
- 不为了旧内部过渡结构保留双轨主链。
- 不移动官方插件 Handler 到 Router 或 Personal Expression 之后。
- 不让 Router 承担规划、工具选择或执行 Prompt 构建。
- 不一次性重写所有平台 adapter、官方插件 API 或持久化数据。

## 计划产物

1. 过渡结构清理清单与删除条件。
2. Personal Runtime owner 和 session/turn/task 生命周期图。
3. 类型化 Runtime Context 与兼容 extra 映射表。
4. Output Dispatcher 时序与 Hook 归属表。
5. Prompt Snapshot/Overlay 和 Capability Snapshot 契约。
6. Conversation/Memory 收口与迁移说明。
7. 插件、主动任务和 Subagent 生命周期基线。
8. 前置主链就绪报告。
9. Core Head 内部 Executor Body 的实现计划。
