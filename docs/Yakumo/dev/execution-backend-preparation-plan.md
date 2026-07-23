# Personal Runtime 前置主链清理计划

本文记录 Yakumo 下一阶段的总体实施计划。当前优先级不是实现可替换
`ExecutionBackend`，而是把执行阶段之前仍然存在的过渡结构清理为稳定的 Personal
Runtime 主链。只有这些边界完成后，Native、Claude Code、OpenCode 等执行后台才进入
设计和实现。

本文是目标和实施顺序，不代表所述能力已经完成。当前运行事实以
`execution-backend-flow.mmd` 和源码为准。

## 优先级调整

过去的计划以“为执行器解耦做准备”为主轴，容易把现有中间结构误认为必须长期兼容。
现在明确调整为：

1. 先确定 Personal Runtime、Personal Expression、Prompt、Capability、Output、Memory
   和插件的长期 owner。
2. 清理已经完成使命的过渡状态、旁路、镜像和反向回调。
3. 让官方插件与平台能力通过稳定边界继续工作。
4. 最后才从稳定的 Execution Preparation 接入不同 Backend。

执行后台是最后一段替换点，不是当前架构工作的中心。前置主链完成后，Backend 应只
负责“如何执行”，不再重新实现 Prompt、知识库、工具、插件、会话和输出。

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
            -> Router || speculative Personal Expression
            -> Core Planner when execution is a candidate
            -> ContextSnapshot + CapabilitySnapshot
            -> Execution Preparation
            -> Execution Backend (last phase)
            -> Execution Events
       -> Personal Expression
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
- Backend 只消费准备好的 Execution Request，并返回统一 Execution Events。

## 实施原则

- 从源码事实和实际运行日志出发，不从理想接口反推空置抽象。
- 一次只迁移一个 owner；新 owner 接管后删除旧 owner 的写入路径。
- 新旧路径短暂并存时只能有一个主写者，另一条只能做只读校验或边界适配。
- Router、Planner 和 Personal Expression 保持独立，但消费同一事实快照的不同投影。
- 不把所有官方能力转换成 MCP；内部先形成统一 Capability，再由未来 Backend Adapter
  选择直接调用、MCP、RPC、CLI 或其他桥接。
- 不为了文件变小而拆类；只有所有权、生命周期或测试边界发生变化时才拆模块。

## Phase 0：过渡结构清单与运行事实

状态：已完成。无入口的 pre-Pipeline 路径、影子 Interaction Memory、重复能力摘要和
兼容状态镜像已经删除。后续发现的过渡结构直接在所属 Phase 清理，不再维护独立调查文档。

需要完成：

- 将现有结构标记为 `保留`、`迁移`、`替换`、`删除` 或 `公开边界适配`。
- 记录消息、插件直接回复、插件 `ProviderRequest`、Persona-only、Core 非流式、Core
  流式、Core 错误、主动消息、Subagent 前台和后台的运行事实。
- 记录每条路径的状态 owner、输出 owner、完成 owner、Prompt 版本和能力来源。
- 盘点所有 `_interaction_*` extra，区分公开诊断、兼容镜像和内部状态。
- 盘点 Local/Third-party 路径差异，但不在本阶段设计 Backend。

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
Expression、Output Controller、assistant-only Conversation 提交和完整 lifecycle 终态。

本阶段尚未完成：Heartbeat/Runtime Sensor 等 Observation 生产者、目标 session registry、
quiet-hours/cooldown 等本地 eligibility policy、插件和后台任务 identity，以及完整 Gate / Policy /
Action Coordinator。

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
- 将 Router/Persona 并发、Planner 调度、turn 仲裁和最终完成迁入 Session Runtime。
- `InteractionMiddleware` 收缩为官方 Pipeline 的薄适配器，不再拥有业务编排。
- 保持 Router 与 Persona 从 turn 开始并发；Core 最终结果先提交时由 Runtime 抑制尚未提交的即时表达。
- Core 或最终结果先完成时，统一由 Session Runtime 仲裁尚未发送的推测表达。
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
- Router、Planner、Persona、Execution 使用显式 Projection 和 Phase Overlay。
- 静态与动态 collector 由 Prompt 系统统一调度，业务模块不自行查询同类事实。
- Core 需要的工具绑定、任务材料和执行时状态进入 Execution Overlay，不替换基础 Pack。
- ContextSnapshot 记录版本、来源、阶段和 lineage，诊断能够还原每次模型请求使用的事实。
- Planner 只生成 `execute/not_required + CoreTaskSpec`，不拥有执行上下文构建。

退出条件：模型请求不受 Router、Persona、Planner 或 Core 的完成顺序影响；同一阶段使用
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
- Interaction 私有 Memory Store 已删除；ConversationHistoryCollector 与 MemoryCollector
  是当前唯一读取入口。
- Persona、Router、Planner 和 Execution 通过 Prompt Projection 使用相同的历史与记忆
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

这一阶段仍不以接入新 Backend 为目标，只验证前置主链是否已经稳定。

需要确认：

- ContextSnapshot、CapabilitySnapshot 和 CoreTaskSpec 均有唯一 owner。
- Personal Runtime 能形成完整、不可变的 Execution Preparation 输入。
- Native 当前使用的 Prompt、工具、知识库、Skills 和插件均能从前置边界获得，不要求
  Backend 自行查询；SubAgent handoff 由 Native 兼容路径自行持有，不属于此验收条件。
- Output、错误、取消、进度和完成通过统一事件返回 Personal Runtime。
- Local/Third-party 平行准备链可以被删除，而不是继续扩展。

当前已经建立 `CoreExecutionSpec`，它只保存统一 ContextPack、CoreTaskSpec、执行历史、
通用能力快照和执行身份，不保存目标渲染结果或 ProviderRequest。Native 在 Spec 形成后执行
目标投影和渲染，再通过 `NativeExecutionAdapter` 转换为官方 `ProviderRequest`；这不是完整的
`ExecutionBackend` 接口，而且 Spec 当前仍在 Native `build_main_agent` 内形成。Claude Code、OpenCode 等只有在
Output、取消和 Execution Event 边界稳定后才接入；Dify/Coze/DashScope/DeerFlow 继续作为
官方兼容路径。

这里的 `CoreExecutionSpec` 是单次进程内的事实契约，不是可持久化或可跨进程传输的
Backend 协议。当前 `CoreCapabilitySnapshot.tools` 仍保留 Native `ToolSet` 运行时对象，
同时提供规范化 tool schema；后续 Backend 契约只能消费规范化能力描述或显式 capability
handle，不能依赖 `FunctionTool`、`AgentRunner` 或 `ProviderRequest` 对象。

`CoreCapabilitySnapshot` 不再为 SubAgent 设置独立字段。Native 继续通过 `SubagentCollector`、
`SubAgentOrchestrator` 和 `HandoffTool` 保持官方兼容，因此当前 Native ContextPack/ToolSet
仍携带 handoff 信息；该绑定应在 Capability Resolver 阶段分离。其他 Backend 不承担该能力，
新增场景优先通过插件 Tool 表达。

Phase 0 已确认的准备边界：

- 官方 `ProviderRequest` 是必须保留的插件兼容输入，不是未来统一执行契约。
- TaskSpec、Context/Prompt Projection、规范化附件和 CapabilitySnapshot 必须在选择
  Backend Adapter 之前形成。
- Adapter 只负责后台能力校验、协议字段投影、远端 thread、stream、cancel/close 和错误
  翻译，不重新收集 Prompt、人格、知识库或插件事实。
- 官方 `OnLLMRequest` 保留在最终低层 request projection 之后、实际执行之前；其他
  Agent/LLM/Tool Hook 按后台可观测能力映射，不伪造后台未暴露的工具生命周期。
- Third-party Stage 丢弃插件 `ProviderRequest` 的兼容缺口已经修复：显式请求直接进入
  `CoreTaskSpec` 兼容投影和 `OnLLMRequest` Hook；只有普通事件输入才从文本、图片和录音
  构建请求。现有 Dify/Coze/DashScope/DeerFlow runners 仍是兼容对象，不是新接口模板。

## 当前进度

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
- Observation 复用唯一 Persona 与 Output 路径，写入 assistant-only Conversation，并在
  发送失败、取消和异常时保留正确终态；当前尚无 Heartbeat 生产者。
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
  RenderResult；Token 统计和
  Core 执行连续性独立持久化，不再依赖可见对话历史，也不绕过 Prompt Renderer 手动追加
  ProviderRequest 上下文。

当前仍存在、但不应继续扩展的准备阶段边界：

- Core Execution Ledger 的成功、失败和取消记录仍由 `InternalAgentSubStage` 收尾；在统一
  Execution Event 建立后，应由执行生命周期 owner 记录，而不是由 Native Stage 私有持有。
- Third-party Agent Stage 仍走官方兼容准备链，尚未消费 `CoreExecutionSpec`。它是需要
  保留的现状，不是新 Backend 的实现模板。
- 通用 `Context.send_message()` 保留公开调用方式；纯文本主动消息现在经 Personal Runtime
  排队和 Output Controller 投递。同一 active turn 的 Core 工具消息明确作为 progress，
  跨 session 输出建立独立 proactive turn。纯媒体主动消息尚未形成可持久化语义材料，当前
  仍保留平台直发。
- 已经决定发送的 Observation 输出会形成 assistant-only Conversation、Prompt History 和
  Memory history projection；通用 Inbox facts 不写 Conversation。转换层使用空 user payload
  表达 assistant-only，不伪造用户消息。
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
- Router、Persona、Context Material 和 Stream Observation task 已归属 TurnExecutionScope；
  Hybrid 放行 Core 后 speculative Persona 不再转入 Middleware 全局集合，lease 释放前统一
  完成或取消。
- immediate/final 使用同一 turn lock 原子预留输出槽。Final 先预留时取消 pending Persona；
  Immediate 已预留时允许按 Hybrid 语义先发即时回复，再发最终结果。
- 当前 session 的 `send_message_to_user` 已作为 progress 进入现有 Output Controller，不会
  重入同 session lease 或提前完成 turn；跨 session 文本输出使用独立 proactive turn。
- 全量物理发送失败和 canonical material 缺失已在本轮修正；分段部分成功仍缺 delivery
  receipt，after-send hook 的 stop 语义也可能让已送达内容被标记 cancelled。
- Observation 已有输入/输出契约，但 assistant-only history projection、目标 session
  registry、policy 和 producer 尚未完成，因此还不能称为可用 Heartbeat。
- Native 已消费 `CoreExecutionSpec`，Third-party 仍是官方兼容请求链。两者的上下文、
  capability、execution identity、ledger 和错误状态尚未统一，暂不适合直接抽象成等价
  Backend。
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
  可跨 backend 或跨进程的不可变契约。
- `PersonalTurnContext` 已建立，但平台主链仍通过 117 个 literal event extra key 协作；
  typed context 还不是实际唯一事实源。

依赖结构图见 `runtime-dependency-structure.mmd`。

下一步继续收口 Execution Event、取消和 Output Port，再评估 Backend Adapter；不直接
把现有 Third-party Agent SubStage 改名或包装成新执行器接口。

## 非目标

- 当前不实现 Claude Code、OpenCode 或新的 Backend。
- 当前不创建空置 ExecutionBackend、Capability Gateway 或远程协议。
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
9. 经单独确认的 Backend 实现计划。
