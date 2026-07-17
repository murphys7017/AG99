# Personal Runtime 过渡结构调查

本文记录 Personal Runtime 前置主链 Phase 0 的第一轮源码调查。调查基于当前代码，
不把旧文档或目标设计当作运行事实。本轮不修改运行时行为，也不提前设计 Backend。

调查基线为提交 `2c91ebd59`。相关总体顺序见
`execution-backend-preparation-plan.md`。

## 调查结论

当前主链功能基线总体稳定，但仍处于明显的过渡所有权状态：

- 官方 EventBus 和 Pipeline 是唯一生产入站主链。
- `InteractionMiddleware` 同时承担 Pipeline adapter、Turn 协调器、任务容器、
  Persona/Core 仲裁器和完成 owner。
- `InteractionOutputController` 已经承担大部分语义输出和物理输出职责，但需要通过
  event 方法替换、extra 和 Middleware 私有回调完成闭环。
- `InteractionTurnState` 已是主要状态对象，但大量字段仍同步写入 event extra，形成
  两个可写事实表面。
- Prompt 已有统一 Collector/Builder/Projection/Render 主链，但共享 material 中的
  ContextPack 会被后续 Core enrichment 换版。
- Capability 只有分类阶段摘要；Native Core 仍独立解析并注入真正的工具、知识库、
  Skills 和 Subagent。
- `InteractionMemoryStore` 在生产代码中只有读取者，没有主写入调用。
- 当前代码没有 session 级 runtime，因此多 Turn、多轮任务、Third-party Runner、
  Subagent 和主动消息也没有统一 owner。

因此，下一步不应先创建 Backend，也不应直接重写 Output。应先删除已经确认的死入口，
再让 Personal Session Runtime 实际接管 Turn 和任务生命周期。

## 当前真实主链

```text
Platform Adapter
  -> event_queue
  -> EventBus
  -> PipelineScheduler
       -> Waking / whitelist / permission / preprocess
       -> ProcessStage
            -> prepare_pipeline_event()
                 -> TurnState
                 -> event.send* interceptor
            -> official Plugin Handler
            -> handle_pipeline_event(enqueue_core=False)
                 -> Router || speculative Persona
                 -> Planner when route=hybrid
                 -> local continuation into AgentRequestSubStage
            -> InternalAgentSubStage | ThirdPartyAgentSubStage
       -> ResultDecorateStage
       -> RespondStage
  -> InteractionOutputController
  -> Platform Event send implementation
  -> finalized turn / postprocess / conversation / memory
```

`InteractionMiddleware.handle_inbound()` 所代表的“在 Pipeline 之前接管并重新投递
event_queue”路径在生产源码中没有调用者。当前 Pipeline 路径固定使用
`handle_pipeline_event(..., enqueue_core=False)`，Core 由 `ProcessStage` 在当前 Pipeline
调用栈中继续执行。

这条无调用者路径不应再被视为兼容入口。仓库内没有动态注册、反射调用或公开 API 约定
要求保留它。

## Owner 盘点

### 1. 入站与 Turn 协调

当前 owner：

- EventBus 持有 Pipeline task。
- `ProcessStage` 决定何时调用插件、Personal Runtime 和 Core。
- `InteractionMiddleware` 的全局 `_inflight_tasks` 持有入站、Persona 和后台 task。
- Router task 与 Persona task 由单次方法调用中的局部变量持有。
- event 本身持有 TurnState 和继续执行 Core 所需标记。

问题：

- 没有 persona/session/audience 级长期 runtime。
- 同一 session 的两个 Turn 没有统一 mailbox、取消、替换或顺序策略。
- Pipeline task、Middleware task 和 Core session lock 分别管理不同生命周期。
- `_forward_to_core()` 同时支持当前调用栈继续执行和重新进入 event queue，但后者已经
  没有生产调用方。
- EventBus 为每个事件创建独立 Pipeline task；Router、Persona 和 Planner 都发生在
  Core session lock 之前。
- Native Core 只在 Agent 执行阶段按 `unified_msg_origin` 加锁；Third-party Runner 没有
  使用同一 session lock。因此当前既没有完整串行，也没有显式并发策略。

目标 owner：

- 官方 EventBus/Pipeline 继续拥有事件调度。
- Personal Runtime Adapter 只把通过官方过滤的事件转换为 Observation/Turn。
- `PersonalSessionRuntime` 持有 Router、Persona、Planner、ActiveTask、取消和 Turn 仲裁。

### 2. Runtime 状态

`InteractionTurnState` 已包含 route、planning、CoreTaskSpec、Prompt material、Persona
状态、utterance、stream、failure 和 completion。与此同时，helper 和调用方仍持续把
同一状态镜像到 `_interaction_*` extra。

本轮静态扫描在 Interaction、Pipeline 和 Main Agent 范围内找到约 686 行
`_interaction_*` 引用；核心状态读写与 helper 调用约 276 行。数量不是问题本身，真正
的问题是以下 extra 仍参与控制流，而不只是诊断：

- `_interaction_route_handled`
- `_interaction_delegate_to_core`
- `_interaction_output_origin`
- `_interaction_plugin_output_transaction_*`
- `_interaction_pipeline_output_suppressed`
- `_interaction_turn_finalization_*`
- `_interaction_original_send*`
- `_interaction_output_controller`

目标 owner：内部控制状态只写 `PersonalTurnState`；extra 只能是公开诊断、官方插件兼容
投影或指向 Runtime Context 的单一引用。

### 3. Output

当前 Output 闭环跨越四个 owner：

1. Middleware 替换 `event.send()`、`event.send_streaming()` 和
   `event.complete_visible_turn()`。
2. `ProcessStage` 直接开始和结束插件输出事务。
3. `RespondStage` 从 extra 取出 OutputController，直接 flush/cancel Turn finalization。
4. OutputController 形成 finalized material 后，通过 `_persist_callback` 反向请求
   Middleware 完成 Turn。

同时，OutputController 还反向调用 Middleware 提供的：

- `visible_reply_renderer`
- `core_reply_handler`
- `lifecycle_callback`
- `_persist_callback`

这些回调使 Output 无法成为单向依赖：Middleware 拥有 Controller，Controller 又依赖
Middleware 才能表达、发 lifecycle 和完成 Turn。

`AstrMessageEvent.emit_output()`、`emit_progress()`、`send_direct()` 和
`send_persona()` 已经是插件可见 API，应保护其行为；但其内部不应长期通过 event extra
查找具体 Controller。未来应由 Runtime Context/Output Port 适配。

### 4. Prompt Snapshot

已经正确的部分：

- Interaction 每个 Turn 使用 single-flight 收集基础 ContextPack。
- Router、Planner 和 Persona 使用独立 target projection。
- Prompt contributor 在规范事实包构建阶段统一收集一次。
- Persona phase material 通过 `PromptContextBuilder(base=...)` 形成派生 Pack。

仍属过渡的部分：

- `InteractionContextMaterial` 是可变对象。
- Main Agent 以 material 中 Pack 为 base 完成 Core enrichment 后，会把
  `context_material.prompt_context_pack` 替换成 Core 版本。
- 同一 material 因完成时序不同，可能代表 interaction base、contributor-derived 或
  Core-enriched Pack。
- event extra 同时发布 Interaction Pack 和 Main Agent Pack，调用方需要知道阶段才能
  正确解释。

目标 owner：不可变 Base Snapshot 加显式 Projection/Overlay；Core enrichment 产生新版本
并记录 lineage，不替换共享 material 的“当前 Pack”。

### 5. Capability

`InteractionCapabilityCollector` 使用 `ToolsCollector.resolve_toolset()` 生成 Router/Planner
可见摘要，只包含工具数量、少量名称以及 Knowledge/Subagent 是否存在。

Native Core 随后仍在 `build_main_agent()` 中独立完成：

- Persona toolset 解析与合并；
- Knowledge agentic/non-agentic 工具注入；
- Skills、MCP、Web Search、Sandbox、Cron 和主动消息工具注入；
- Subagent Handoff 注入与主 Agent 重复工具移除；
- Provider 和 modality 能力修正。

因此当前不存在统一 Capability Snapshot。Planner 的判断材料与最终可执行能力可能来自
不同时间点、不同规则和不同错误处理。

目标 owner：一个 Capability Resolver 生成带调用绑定的不可变 Snapshot；Router、Planner
和 Execution 只读取不同投影。

### 6. Conversation 与 Memory

当前存在三个概念层：

- ConversationManager 保存精确对话历史。
- MemoryService 通过 `AFTER_TURN_COMPLETED` 消费 finalized material。
- `InteractionMemoryStore` 从 session JSON 读取 recent turns、偏好、关系和风格字段。

生产源码没有调用 `InteractionMemoryStore.save_interaction_memory()` 或
`update_interaction_memory()`。它现在是一个可读取旧数据、但没有当前主写者的影子存储。
继续把它注入 Router、Planner 和 Persona 会让“历史来自哪里”变得不确定。

本轮只检查文件形态，没有读取内容。当前 `data/interaction_memory` 存在 3 个 JSON 文件，
合计 5243 字节，最后修改时间集中在 2026-05-08。代码 owner 可以迁移或删除，但这些
现存数据必须先确定导入 MemoryService、只读归档或显式废弃策略，不能随代码直接删除。

### 7. 插件、主动消息与 Subagent

需要保护：

- 官方 Handler/filter/priority/`yield`/`stop_event`/`ProviderRequest` 语义。
- 官方 LLM/Agent/Tool Hook。
- 已公开的 Interaction prompt/result/stream/lifecycle/effect 注册入口。
- 插件可见的 `emit_output()`、`emit_progress()`、`send_direct()`、`send_persona()`。

尚未收口：

- `Context.send_message()` 直接调用 platform `send_by_session()`，不创建 Turn，不经过
  Persona Expression、OutputController、Conversation 或 Memory。
- Local/Third-party Agent 在 Pipeline 初始化时二选一，准备链和事件语义不同。
- Subagent Handoff 与后台唤醒仍绑定 Native Tool Loop 和父 event。
- 多轮插件任务没有 Personal Session Runtime owner。

Third-party Runner 不是 Native Core 的等价执行壳。它会从 event 重新构造
`ProviderRequest`，应用 `CoreTaskSpec` 和 `OnLLMRequest` Hook 后直接初始化第三方 runner；
它不经过 Native `build_main_agent()` 的统一 Prompt/Capability 准备，也没有 Native 的
session lock 和 follow-up capture。

Subagent/后台任务当前还有两条独立生命周期：

- 前台 Handoff 在 Native Tool Loop 内执行，结果作为 Tool Result 返回父 Agent。
- 后台 Handoff 创建 `CronMessageEvent`，但不提交 EventBus/Pipeline，而是直接调用
  `build_main_agent()`；完成通知依赖 `send_message_to_user -> Context.send_message() ->
  platform.send_by_session()` 直达平台。

Native follow-up 另由全局 active-runner registry 和按 UMO 的 order state 管理。它能够把
新消息注入正在执行的 ToolLoopAgentRunner，但不属于 Interaction TurnState，也不归
Middleware `_inflight_tasks` 管理。

主动消息不能直接改成全部进入 Persona Runtime，因为协议通知和原始媒体也需要直接发送。
后续必须先建立显式 `persona / progress / protocol / raw` 输出意图，再决定默认策略。

## 过渡结构分类

| 当前结构 | 分类 | 长期处理 | 删除或切换条件 |
| --- | --- | --- | --- |
| EventBus/Pipeline/Plugin Handler | 保留 | 官方输入与插件兼容边界 | 不迁移 |
| `ProcessStage -> handle_pipeline_event` 接缝 | 公开边界适配 | 收缩为 Personal Runtime Adapter | 不再直接操作输出事务或 Runtime 内部状态 |
| `handle_inbound()` + `core_queue` 重投递 | 删除 | 只保留官方 Pipeline 主链 | 生产调用、动态注册和公开 API 约定均确认不存在 |
| Middleware `_inflight_tasks` | 迁移 | Session Runtime task registry | Router/Persona/Planner/ActiveTask 均由 session owner 持有 |
| event send 方法替换 | 替换 | Output Port/Dispatcher | 所有官方与插件输出都能显式进入唯一出口 |
| `emit_output()` 等插件 API | 保留并适配 | 稳定插件输出 API | 内部不再查找具体 Controller extra |
| `_interaction_*` 控制状态 | 迁移 | 类型化 Runtime Context | extra 只剩诊断和兼容投影 |
| Middleware/Output 私有反向回调 | 替换 | Runtime 调用 Expression/Output/Completion ports | 依赖方向变为 Runtime 单向编排 |
| ProcessStage 插件输出事务 | 迁移 | Turn/Output owner | Stage 不再调用 Controller 私有事务方法 |
| 可变 `InteractionContextMaterial` | 替换 | 不可变 Snapshot + Overlay | Core enrichment 不再换写共享 Pack |
| Interaction Capability 摘要 | 迁移 | Capability Snapshot 投影 | Planner 与执行能力来自同一 resolver |
| `InteractionMemoryStore` | 迁移后删除 | Conversation + MemoryService | 旧 JSON 数据策略确定且读取者清零 |
| Local/Third-party 平行准备链 | 后续替换 | 统一 Execution Preparation | 前置主链就绪复核通过 |
| `Context.send_message()` 旁路 | 显式化 | 主动输出边界 | persona/protocol/raw 语义和兼容策略确定 |
| Native follow-up 全局 registry | 迁移 | Session Runtime mailbox/ActiveTask | 多轮消息不再依赖 Runner 全局表 |
| 后台 Handoff 直接 build/send | 迁移 | ActiveTask completion Observation | 后台结果能恢复 persona/task/audience 并进入统一输出 |

## 文档冲突

本轮以源码和最新前置主链计划为准，确认以下非历史文档仍包含过时实施方向：

- `persona-system-final-goal.md` 把独立 Input Bus/Input Gateway 写成下一步入口。
- `legacy-plugin-hook-migration-plan.md` 明确要求实现 Input Bus，并把事件转交给
  `InteractionMiddleware.handle_inbound()`。

当前设计已经改为复用官方 EventBus/Pipeline，只在 Plugin Handler 后、Core Agent 前通过
Personal Runtime Adapter 接入。独立 Input Bus/Input Gateway 和 pre-Pipeline
`handle_inbound()` 不再是目标结构。

`output-unification-command-book.md` 已经标记为历史设计记录，其中“不得删除 send
interception”只约束当时的实现切片，不是长期兼容要求。`modules/interaction.md` 对
Middleware 仍为当前 Turn owner 的描述是当前事实，不是目标状态。

## 风险排序

### 高：没有 Session Runtime owner

当前单 Turn 主链能够运行，但多 Turn、多轮插件和后台任务没有统一生命周期。直接继续
增加功能会把取消、完成和错误恢复继续写进 Middleware 与 extra。

同一 session 当前是混合并发语义：前置 Router/Persona/Planner 可重叠，Native Core 在
执行阶段串行，Third-party Core 可继续并发，follow-up 又可能注入已有 Native Runner。
这不是一种可稳定扩展的会话策略。

### 高：Output 与 Turn completion 循环依赖

OutputController、Middleware、ProcessStage 和 RespondStage 都能推动输出或完成。现在依赖
细致的 deferral 标记保持顺序，后续任何新输出来源都容易再次形成重复回复或提前完成。

### 中：Prompt Snapshot 与 Capability 事实源不唯一

当前单请求可以工作，但 Planner、Native Core 和未来外部 Backend 无法证明消费同一版本
的事实与能力。

### 中：影子 Memory 与主动消息旁路

Interaction Memory 没有主写者，主动消息没有 Turn。二者会阻碍持续人格形成一致历史。

### 低但应立即清理：无调用者的 pre-Pipeline 入站路径

这条路径当前不影响生产行为，但会误导后续设计，并保留 event queue 重入语义。

## 建议实施顺序

### Step 1：补全源码与数据边界

1. 确认 `handle_inbound()`、`core_queue` 与重投递分支没有反射、动态注册或外部调用约定。
2. 确定同一 session 重叠 Turn 的目标策略：排队、取消替换或显式并发。
3. 画清 Internal/Third-party Core 的准备差异，但不设计 Backend。
4. 画清 Subagent 前台、后台、父任务恢复和主动消息的 owner 与回流位置。
5. 为已确认存在的 3 个 `data/interaction_memory` 文件确定迁移、归档或删除策略。

前期调查暂不补测试。测试策略在 owner 和迁移批次确定后再按实际风险制定，避免为即将
删除的过渡路径继续增加保护。

建议的 session 策略是：Observation 可以持续进入 mailbox，但同一
persona/session/audience 默认只有一个拥有可见输出完成权的 ActiveTurn。新用户消息优先
作为当前 ActiveTask 的 follow-up；无法吸收时排队形成下一 Turn。协议事件、原始媒体和
显式声明可并发的后台任务不强制占用对话 Turn。该策略需要单独确认后才能进入实现。

### Step 2：删除死的入站双轨

删除 `handle_inbound()`、`_spawn_inbound_task()` 和 `core_queue` 重投递分支。保留
`ProcessStage -> handle_pipeline_event(enqueue_core=False)` 唯一入口。

这一步只删除无生产调用者的路径，不创建新抽象。

### Step 3：迁移真正的 Runtime owner

引入实际持有状态和 task 的 `PersonalRuntimeManager` / `PersonalSessionRuntime`：

- manager 按 persona/session/audience 解析 session runtime；
- session runtime 持有 active turns、Router/Persona/Planner task、取消和超时；
- 把 `_handle_async_fast_response_and_route()` 的并发与仲裁迁入 session runtime；
- Middleware 只完成配置解析、Observation 投影和 Runtime 调用；
- 本步暂时沿用现有 OutputController 和 Prompt 实现，避免一次迁移多个 owner。

只有当上述对象真正接管 task 与 Turn 仲裁时才创建；不建立空壳 facade。

### Step 4：类型化状态和 Output 后续迁移

Session Runtime 稳定后，再依次迁移 extra、Output 回调、Prompt Snapshot、Capability、
Memory 和插件任务边界。Backend 仍保持最后。

## 暂不处理

- 不定义 `ExecutionBackend`、MCP 转换层或远程协议。
- 不移动官方 Plugin Handler。
- 不重写 Prompt Renderer。
- 不一次性拆分 Middleware 和 OutputController。
- 不删除插件公开 Hook 或输出 helper。
- 不根据未来外部执行器猜测 Capability 协议。

## Phase 0 完成条件

第一轮调查已经完成，但 Phase 0 尚未结束。至少满足以下条件后才能开始 Session Runtime
迁移：

- 生产主链唯一入口及其全部调用方已经确认。
- 同 session 重叠 Turn 的当前行为和目标策略都已确认。
- Internal/Third-party、Subagent 和主动消息的保留风险有明确记录。
- 所有主要过渡结构都有 owner、分类和删除条件。
- 第一批代码迁移只改变一个 owner，并有清晰的回滚边界。
