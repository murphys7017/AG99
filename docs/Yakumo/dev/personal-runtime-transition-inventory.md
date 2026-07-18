# Personal Runtime 过渡结构调查

本文记录 Personal Runtime 前置主链 Phase 0 的第一轮源码调查，并持续标记后续实现结果。
调查基于源码，不把旧文档或目标设计当作运行事实。

初始调查基线为提交 `2c91ebd59`；实现状态已更新至 2026-07-18 的当前源码。相关总体
顺序见 `execution-backend-preparation-plan.md`。

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
- 当前已经有按 config、persona、audience 和 privacy scope 建立的 session runtime，负责
  conversational Turn admission、follow-up 和 Native/Third-party 串行；多轮插件、
  Subagent、主动消息和完整 task lifecycle 仍没有统一 owner。

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
            -> reserve PendingTurn
            -> official Plugin Handler
            -> bind PersonalRuntimeKey / admit follow-up or Turn lease
            -> handle_pipeline_event()
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
event_queue”路径已经从生产源码删除。当前 Pipeline 路径固定使用
`handle_pipeline_event()`，Core 由 `ProcessStage` 在当前 Pipeline 调用栈中继续执行。

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

- Session Runtime 已按 persona/audience 建立，但当前只持有 Turn lease 和 Native
  follow-up coordinator，还不是完整长期人格状态容器。
- 同一 Runtime 的 Turn 已统一串行和 follow-up 顺序；取消、替换、超时和跨任务恢复策略
  仍未统一。
- Pipeline task、Middleware task 和 Personal Runtime lease 仍分别管理不同层级生命周期。
- `_forward_to_core()` 只标记当前 Turn 继续 Core，不再重新进入 event queue。
- EventBus 为每个事件创建独立 Pipeline task；`ProcessStage` 在 Router、Persona 和
  Planner 前取得 Runtime Turn lease，同一 Runtime Key 的 conversational Turn 因此串行。
- Native active runner follow-up 在 Router/Persona 前尝试吸收；不能吸收以及 Third-party
  请求都进入同一 Runtime 队列。Router/Persona/Planner task 本身仍由 Middleware 持有，
  尚未迁入 Session Runtime task registry。

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

Third-party Runner 不是 Native Core 的等价执行壳。插件显式提供 `ProviderRequest` 时会
保留该对象；普通事件才从 event 构造 request。随后应用 `CoreTaskSpec` 和
`OnLLMRequest` Hook 并直接初始化第三方 runner。它仍不经过 Native
`build_main_agent()` 的统一 Prompt/Capability 准备，但已经与 Native 共用 Personal
Runtime Turn admission 和串行策略。

### 8. Native / Third-party 执行准备审计

当前两条路径在 `AgentRequestSubStage` 初始化时二选一，分叉发生在执行准备之前，而不是
只在最后的调用协议处发生。

Native 路径：

```text
event / plugin ProviderRequest
  -> conversation and provider resolution
  -> persona/tool/subagent/knowledge/search/sandbox preparation
  -> canonical ContextPack collection and Core projection
  -> ProviderRequest render/apply and modality normalization
  -> OnLLMRequest
  -> ToolLoopAgentRunner reset/run
  -> history, stats and result handling
```

Third-party 路径：

```text
event / plugin ProviderRequest
  -> preserve plugin request, otherwise create from text/Image/Record
  -> append CoreTaskSpec compatibility block
  -> OnLLMRequest
  -> choose Dify/Coze/DashScope/DeerFlow runner
  -> runner-specific remote session/run/result handling
```

此前 Plugin Handler 产出的 `ProviderRequest` 会被 Third-party Stage 重建覆盖。该兼容
缺口已经在公共 Stage 边界修复：显式请求保留 prompt、contexts、media、tools、model 和
output contract，并继续经过 `CoreTaskSpec` 兼容投影及官方 `OnLLMRequest` Hook。

| 维度 | Native 当前行为 | Third-party 当前行为 | 目标 owner |
| --- | --- | --- | --- |
| 请求来源 | 复用插件 request，否则从 event 建立并关联 conversation | 复用插件 request，否则从 event 文本、图片和录音构建 | Execution Preparation 接收 event facts 与官方 `ProviderRequest` 兼容输入 |
| Prompt 与历史 | 收集 ContextPack，按 Core target 渲染 system/history/current input | 不经过 Prompt Pipeline；部分 runner 自己使用 contexts 或远端历史 | ContextSnapshot/Prompt Projection；远端 thread 仅是 Adapter 私有状态 |
| Persona 与错误文案 | persona 同时影响工具集和错误文案 | 只额外解析 persona 错误文案 | Personal Runtime 提供 persona identity；Output 层形成可见失败表达 |
| Tools/Knowledge/Skills | 注入插件工具、知识库、Skills、MCP、搜索、sandbox、cron 和 Subagent | 不接收 AstrBot 可执行能力；远端平台自行持有能力 | CapabilitySnapshot；Adapter 只投影后台实际支持的能力 |
| Provider 能力 | 处理 model、fallback、modality、上下文限制与 tool schema | runner 类型来自当前 Pipeline 配置，provider 详情从全局配置查找，未做统一 capability 验证 | Backend capability validation 与 Adapter projection |
| 执行策略 | max step、tool timeout、压缩、fallback 等来自当前配置 | max step 固定为 30，wrapper tool timeout 固定为 120，另有独立 stream close timeout | Execution Preparation 固化本轮策略；Adapter 只消费适用项 |
| 插件 Hook | 有 Waiting、LLM Request、Agent、LLM Response 和 host tool hooks | 有 LLM Request、Agent/LLM Response；没有 Waiting 和 host tool 生命周期 | 官方兼容 adapter 按明确阶段保留；后台内部工具仅在可观测时映射 |
| Session 并发 | Personal Runtime 在 Router/Persona 前仲裁；Native runner 支持 follow-up | Personal Runtime 使用同一 Turn lease；远端 thread 仍由 runner 管理 | PersonalSessionRuntime 仲裁；Adapter 只声明 follow-up/cancel 能力 |
| Streaming 与结果 | `run_agent` 产生官方 result/streaming finish | 自建 aggregator、watchdog 和 fallback result | Adapter 归一化执行事件；Runtime/Dispatcher 决定可见输出和完成 |
| 错误、取消与清理 | Stage 捕获错误并直接发送，Runner 有 abort 语义 | Runner/Stage 共同转成 error chain，并显式 close 部分 client | Runtime 持有失败/取消策略；Adapter 负责协议取消、关闭和错误翻译 |
| 持久化与观测 | 保存官方 conversation，写 provider stats 和 trace | 主要依赖远端 conversation ID，只上传基础 metric | finalized turn 提交 Conversation/Memory；统一 telemetry 接收 Adapter 数据 |

责任分类如下：

- Execution Preparation 必须统一：TaskSpec、不可变 ContextSnapshot、Prompt Projection、
  规范化当前输入和附件、CapabilitySnapshot、persona/turn/audience identity，以及插件
  `ProviderRequest` 兼容输入的合并结果。
- Backend Adapter 必须保留差异：远端认证与配置、字段和媒体投影、远端 thread ID、流协议
  解析、协议级取消/关闭，以及后台内部能力是否可映射为执行事件。
- 官方兼容边界必须保留：Handler `yield ProviderRequest`、`OnLLMRequest` 和现有
  Agent/LLM/Tool Hook。`OnLLMRequest` 仍作用于最终的低层 request projection，不重新成为
  Prompt 事实源。
- 已删除 Native 私有 session/follow-up owner，并修复 Third-party 覆盖显式 request。
  后续仍应删除 Local/Third-party 在准备前分叉，以及各 Stage 各自决定可见错误和最终完成。

现有 Third-party runners 只能作为需要适配的官方能力，不能作为未来 Backend 接口模板。
`ProviderRequest` 也不能直接成为统一 Execution Preparation 契约：它既是官方插件公开兼容
对象，又混合了模型可见字段和 Native Runner 输入。长期结构应先形成统一、不可变的准备
结果，再由兼容 adapter 投影为 Native `ProviderRequest` 或第三方协议输入。

Subagent/后台任务当前还有两条独立生命周期：

- 前台 Handoff 在 Native Tool Loop 内执行，结果作为 Tool Result 返回父 Agent。
- 后台 Handoff 创建 `CronMessageEvent`，但不提交 EventBus/Pipeline，而是直接调用
  `build_main_agent()`；完成通知依赖 `send_message_to_user -> Context.send_message() ->
  platform.send_by_session()` 直达平台。

Native follow-up registry 和顺序状态已经迁入 `PersonalSessionRuntime`。新消息先尝试注入
同一 Runtime 的 active `ToolLoopAgentRunner`；已消费消息不会启动 Middleware/Core，未
消费消息按捕获顺序取得下一 Turn lease。Runner 的实际任务生命周期仍未迁入 Runtime。

主动消息的目标边界已经确定：所有面向用户的输出都进入 Output Dispatcher；
`persona / progress / protocol / raw` 是显式 OutputIntent 模式。`protocol` 和 `raw` 不进行
Persona 改写，但仍然拥有 Envelope、delivery identity 和完成语义。只有平台内部握手或
ACK 等非用户可见控制留在 Platform Sink 内部。

## 过渡结构分类

| 当前结构 | 分类 | 长期处理 | 删除或切换条件 |
| --- | --- | --- | --- |
| EventBus/Pipeline/Plugin Handler | 保留 | 官方输入与插件兼容边界 | 不迁移 |
| `ProcessStage -> handle_pipeline_event` 接缝 | 公开边界适配 | 收缩为 Personal Runtime Adapter | 不再直接操作输出事务或 Runtime 内部状态 |
| `handle_inbound()` + `core_queue` 重投递 | 已删除 | 只保留官方 Pipeline 主链 | 2026-07-18 已移除生产入口、队列依赖和 `enqueue_core` 分支 |
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
| `Context.send_message()` 当前旁路 | 替换 | 主动 OutputIntent | 保留公开 API，所有面向用户的 persona/progress/protocol/raw 输出进入 Dispatcher |
| Native follow-up 全局 registry | 已删除 | Session Runtime follow-up coordinator | 2026-07-18 已迁移并覆盖消费、排队、取消和清理测试 |
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

同一 Runtime Key 的 conversational Turn 已在 Router/Persona 前串行，Native follow-up
可以被 active runner 吸收，Third-party 也使用同一 lease。剩余风险是 Session Runtime
尚未持有 Router/Persona/Planner、插件、Subagent 和后台任务的完整 task lifecycle。

### 高：Output 与 Turn completion 循环依赖

OutputController、Middleware、ProcessStage 和 RespondStage 都能推动输出或完成。现在依赖
细致的 deferral 标记保持顺序，后续任何新输出来源都容易再次形成重复回复或提前完成。

### 中：Prompt Snapshot 与 Capability 事实源不唯一

当前单请求可以工作，但 Planner、Native Core 和未来外部 Backend 无法证明消费同一版本
的事实与能力。

### 中：影子 Memory 与主动消息旁路

Interaction Memory 没有主写者，主动消息没有 Turn。二者会阻碍持续人格形成一致历史。

### 已清理：无调用者的 pre-Pipeline 入站路径

这条路径没有生产调用者。2026-07-18 已删除同步入口、后台 spawn、`core_queue` 注入和
`enqueue_core` 分支；Core 委派只设置 Turn 状态，由官方 `ProcessStage` 在当前 Pipeline
内继续执行。

## 建议实施顺序

### Step 1：补全源码与数据边界

1. 已确认 `handle_inbound()`、`core_queue` 与重投递分支没有反射、动态注册或外部调用约定，
   并在第一批代码清理中删除。
2. 已确定 Runtime Key 为
   `config_id + persona_id + audience_key + privacy_scope`；actor 和 conversation 是 Turn
   事实，不参与 Runtime 隔离。
3. 已确定同一 Runtime Key 默认只有一个拥有用户可见输出完成权的 conversational Turn；
   新消息优先作为 follow-up，无法吸收时排队。
4. 已确定 Plugin Handler 前只 reserve 不含 persona 的 PendingTurn/Output Port；Handler
   后解析 effective persona，绑定完整 Runtime Key，再 activate Observation 和模型调用。
5. 已完成 Internal/Third-party Core 准备差异审计，并明确 Execution Preparation、Backend
   Adapter、官方兼容边界和待删除过渡结构的归属；本阶段不设计 Backend 接口。
6. 继续画清 Subagent 前台、后台、父任务恢复和主动消息的 owner 与回流位置。
7. 为已确认存在的 3 个 `data/interaction_memory` 文件确定迁移、归档或删除策略。

前期调查暂不补测试。测试策略在 owner 和迁移批次确定后再按实际风险制定，避免为即将
删除的过渡路径继续增加保护。

已采用的 session 策略是：Observation 可以持续进入 mailbox，但同一四元 Runtime Key
默认只有一个拥有可见输出完成权的 ActiveTurn。新用户消息优先
作为当前 ActiveTask 的 follow-up；无法吸收时排队形成下一 Turn。协议事件、原始媒体和
显式声明可并发的后台任务不强制占用对话 Turn。

### Step 2：删除死的入站双轨

状态：已完成。

已删除 `handle_inbound()`、`_spawn_inbound_task()`、构造期 `core_queue` 依赖和全部
`enqueue_core` 分支。当前唯一生产入口为 `ProcessStage -> handle_pipeline_event()`；
Middleware 只标记 Core 委派，不再把 event 重新放回官方队列。

这一步只删除无生产调用者的路径，不创建新抽象。

### Step 3：迁移真正的 Runtime owner

引入实际持有状态和 task 的 `PersonalRuntimeManager` / `PersonalSessionRuntime`：

- manager 按 `config_id + persona_id + audience_key + privacy_scope` 解析 session runtime；
- 官方过滤/preprocess 后、Plugin Handler 前 reserve PendingTurn 和 Output Port，但不
  解析最终 persona，也不调用模型；
- Handler 后根据 conversation、`ProviderRequest` 和官方配置解析 effective persona，绑定
  完整 Runtime Key，再根据 stopped、final result 和 Core candidate activate、queue 或
  settle Turn；
- PendingTurn 使用 `reserved -> bound -> queued|active -> settled`，reserved 状态没有
  conversational completion 权；
- session runtime 持有 active turns、Router/Persona/Planner task、取消和超时；
- 把 `_handle_async_fast_response_and_route()` 的并发与仲裁迁入 session runtime；
- Middleware 只完成配置解析、Observation 投影和 Runtime 调用；
- 本步暂时沿用现有 OutputController 和 Prompt 实现，避免一次迁移多个 owner。
- 本步继续复用现有 `InteractionTurnState`，不创建平行 Turn 状态。
- 插件、follow-up、Subagent 和后台任务只登记 identity/task handle，实际生命周期迁移留给
  后续插件任务阶段。

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
- Runtime Key、PendingTurn 绑定时机、同 session 重叠 Turn 策略和 reservation 状态机已经
  确认。
- Internal/Third-party 准备差异已经分类；Subagent 和主动消息的保留风险有明确记录。
- 所有主要过渡结构都有 owner、分类和删除条件。
- 第一批代码迁移只改变一个 owner，并有清晰的回滚边界。
