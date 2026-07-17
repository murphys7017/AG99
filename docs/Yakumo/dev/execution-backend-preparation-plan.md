# Personal Runtime 前置主链清理计划

本文记录 Yakumo 下一阶段的总体实施计划。当前优先级不是实现可替换
`ExecutionBackend`，而是把执行阶段之前仍然存在的过渡结构清理为稳定的 Personal
Runtime 主链。只有这些边界完成后，Native、Claude Code、OpenCode 等执行后台才进入
设计和实现。

本文是目标和实施顺序，不代表所述能力已经完成。当前运行事实仍以
`execution-backend-flow.mmd` 和源码为准，第一轮依赖事实见
`execution-backend-dependency-review.md`。

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
- 没有主写入链路的 `InteractionMemoryStore`。
- 同一共享 `context_material` 被后续阶段替换为不同 ContextPack 版本。
- `ProcessStage` 直接操作 OutputController 内部事务。

迁移可以短暂保留边界适配器，但每个阶段完成后必须删除被替代的内部路径。不得以
“兼容”为理由长期维护两套 owner 或两条主链。

## 目标主链

```text
Platform / Internal Event
  -> Official EventBus / Pipeline / Plugin Handler
  -> Personal Runtime Adapter
       -> PersonalSessionRuntime
            -> Observation / PersonalTurn
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
- `Personal Expression` 只形成统一人格表达，不执行业务能力。
- Prompt 系统收集事实并按目标投影；Planner 不构建执行上下文。
- Capability 系统是 Knowledge、Tools、Skills、Plugins 和 Subagent 的唯一能力来源。
- Output Dispatcher 是所有可见输出的唯一内部出口。
- Backend 只消费准备好的 Execution Request，并返回统一 Execution Events。

## 实施原则

- 从源码事实和现有行为测试出发，不从理想接口反推空置抽象。
- 一次只迁移一个 owner；新 owner 接管后删除旧 owner 的写入路径。
- 新旧路径短暂并存时只能有一个主写者，另一条只能做只读校验或边界适配。
- Router、Planner 和 Personal Expression 保持独立，但消费同一事实快照的不同投影。
- 不把所有官方能力转换成 MCP；内部先形成统一 Capability，再由未来 Backend Adapter
  选择直接调用、MCP、RPC、CLI 或其他桥接。
- 不为了文件变小而拆类；只有所有权、生命周期或测试边界发生变化时才拆模块。

## Phase 0：过渡结构清单与行为基线

状态：进行中，第一轮源码审阅和输出兼容基线已完成。

需要完成：

- 将现有结构标记为 `保留`、`迁移`、`替换`、`删除` 或 `公开边界适配`。
- 为消息、插件直接回复、插件 `ProviderRequest`、Persona-only、Core 非流式、Core
  流式、Core 错误、主动消息、Subagent 前台和后台建立行为基线。
- 记录每条路径的状态 owner、输出 owner、完成 owner、Prompt 版本和能力来源。
- 盘点所有 `_interaction_*` extra，区分公开诊断、兼容镜像和内部状态。
- 盘点 Local/Third-party 路径差异，但不在本阶段设计 Backend。

退出条件：每个现有过渡结构都有明确去向，不再把“当前可用”当作“目标保留”。

## Phase 1：Personal Runtime 所有权

目标是让 Personal Runtime 成为长期控制层，而不是每条消息上的协调函数集合。

实施内容：

- 建立 `PersonalRuntimeManager` 和按 persona/session/audience 隔离的
  `PersonalSessionRuntime`。
- Session Runtime 持有 mailbox、active turns、running tasks、取消和超时。
- 将 Router/Persona 并发、Planner 调度、turn 仲裁和最终完成迁入 Session Runtime。
- `InteractionMiddleware` 收缩为官方 Pipeline 的薄适配器，不再拥有业务编排。
- 保持 Router 与 Persona 从 turn 开始并发；silent 只抑制尚未提交的 Persona。
- Core 或最终结果先完成时，统一由 Session Runtime 仲裁尚未发送的推测表达。

退出条件：一轮任务的 owner 不再是 `AstrMessageEvent` 或 Middleware 全局 task 集合；
多轮插件和后台任务能够关联稳定的 runtime/task identity。

## Phase 2：类型化 Runtime Context

实施内容：

- 建立 `PersonalRuntimeContext`、`PersonalSessionState` 和 `PersonalTurnState`。
- event 只挂一个 Runtime Context 引用，内部模块通过类型化对象交换状态。
- 将 route、planner、prompt、stream、output、completion 和 failure 状态从散落 extra
  迁入 TurnState。
- 保留必要的官方插件兼容 extra，但由一个边界适配器单向投影，不允许反向成为主状态。
- 为状态转换建立封闭方法和不变量测试，禁止模块直接修改其他 owner 的字段。

退出条件：内部主链不再依赖魔法字符串协作；同一状态不存在 TurnState 与 extra 两个
可写事实源。

## Phase 3：统一 Output Dispatcher

实施内容：

- 定义 `OutputIntent`、`ExpressionIntent`、`OutputEnvelope` 和 Platform Sink 边界。
- 即时 Persona、Core 结果、插件 persona 输出、任务进度和主动表达进入同一 Dispatcher。
- Personal Expression 在 Dispatcher 物化和平台发送之前运行。
- 文本、TTS、媒体和客户端对象是同一逻辑 utterance 的 rendition，不是独立回复。
- 官方 `OnDecoratingResult`、`OnAfterMessageSent`、内容安全和 postprocess 在明确阶段运行。
- 逐步删除 event 方法替换和 `_interaction_original_send*` 回退。
- 明确 `Context.send_message()`：创建主动 Observation/OutputEnvelope，或作为显式原始平台
  旁路；不能继续成为无声明的漏口。

退出条件：所有可见输出只有一个内部 owner；重复回复防护不再依赖文本比对和来源猜测。

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

- 建立唯一 Capability Resolver，统一解析 Knowledge、Tools、Skills、Plugins 和 Subagent。
- 同一个 Snapshot 提供不同投影：Router 看极简摘要，Planner 看能力目录，执行阶段看
  完整描述与调用绑定。
- 消除 `InteractionCapabilityCollector` 与 `build_main_agent()` 后续工具注入之间的双重
  能力事实源。
- 插件能力声明包含 owner、scope、权限、side effect、timeout 和可挂载位置。
- 默认能力归属 Personal Runtime；显式声明后才允许挂载 Core/Execution。

退出条件：Planner 判断依据与后续实际可执行能力来自同一快照；插件能力不依赖特定
AgentRunner 才能被发现。

## Phase 6：Conversation 与 Memory 收口

实施内容：

- 官方 Conversation 保存精确对话记录。
- MemoryService 保存短期摘要、长期记忆、人格状态和关系状态。
- 迁移 `InteractionMemoryStore` 中仍有价值的字段，删除无主写入链路和重复 recent turns。
- Persona、Router、Planner 和 Execution 通过 Prompt Projection 使用相同的历史与记忆
  事实，不各自维护副本。
- finalized turn 是 Conversation 和 Memory 的唯一提交材料，silent/cancelled/failed 有
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
- Subagent 定义与生命周期归 Personal Runtime；当前 Handoff 继续作为 Native 执行适配，
  直到任务边界完成迁移。

退出条件：插件和 Subagent 不依赖某个具体 Runner 的内部对象才能参与主流程；主动和
后台结果能够恢复正确的 persona、task 和 audience。

## Phase 8：Execution Preparation 就绪复核

这一阶段仍不以接入新 Backend 为目标，只验证前置主链是否已经稳定。

需要确认：

- ContextSnapshot、CapabilitySnapshot 和 CoreTaskSpec 均有唯一 owner。
- Personal Runtime 能形成完整、不可变的 Execution Preparation 输入。
- Native 当前使用的 Prompt、工具、知识库、Skills、插件和 Subagent 均能从前置边界
  获得，不要求 Backend 自行查询。
- Output、错误、取消、进度和完成通过统一事件返回 Personal Runtime。
- Local/Third-party 平行准备链可以被删除，而不是继续扩展。

只有这些条件满足后，才单独设计 `ExecutionRequest`、`ExecutionEvent` 和 Backend
Adapter，并先让 Native 成为第一个实现。Claude Code、OpenCode 等随后接入同一边界。

## 当前进度

已经完成：

- 根据源码重画当前消息流程。
- 建立 Personal Runtime、Personal Expression 和 Native Core 的术语映射。
- 完成插件、Prompt/Tool、Native Core 和 Subagent 的第一轮依赖盘点。
- 恢复 Interaction 非流式输出的内容安全与 `OnDecoratingResult` 兼容。
- 修正 RespondStage 驱动输出的发送后 Hook、visible completion 和 Turn 最终化顺序。

下一步不是抽取 Backend，而是完成 Phase 0 清单，并从 Phase 1 的 Personal Runtime
所有权开始迁移。

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
