# Runtime Modules

## 入口文件

### `main.py`

职责：

- 初始化运行环境
- 创建数据目录、配置目录、插件目录、临时目录
- 检查或下载 WebUI dist
- 创建 `InitialLoader`

说明：

- 这是进程入口
- 这里只做准备和启动，不承载业务逻辑

### `astrbot/core/initial_loader.py`

职责：

- 创建 `AstrBotCoreLifecycle`
- 调用 `initialize()`
- 并行启动核心运行时和 Dashboard

说明：

- 这是“启动器”
- 它负责把核心运行时和 Web 面板绑到同一个进程生命周期里

## 生命周期装配

### `astrbot/core/core_lifecycle.py`

职责：

- 初始化数据库
- 初始化配置路由器和配置管理器
- 初始化 PersonaManager、ProviderManager、PlatformManager、ConversationManager
- 初始化 KnowledgeBaseManager、CronJobManager、SubAgentOrchestrator
- 创建 `star.Context`
- 创建并重载 `PluginManager`
- 初始化 Provider 和平台适配器
- 初始化 PipelineScheduler 和 EventBus
- 启动核心后台任务

说明：

- 当前系统的总装配中心
- 也是未来最需要拆边界的文件之一

重构关注点：

- 当前文件直接实例化太多具体实现
- 更适合未来演化为 `AppAssembler` 或 `PlatformBootstrap`

## 事件分发

### `astrbot/core/event_bus.py`

职责：

- 从 `event_queue` 读取 `AstrMessageEvent`
- 根据配置选择对应 `PipelineScheduler`
- 异步调度 `scheduler.execute(event)`

说明：

- EventBus 不处理业务逻辑
- 它是平台适配器和消息流水线之间的桥

### `astrbot/core/pipeline/scheduler.py`

职责：

- 初始化所有 pipeline stages
- 依次执行 stage
- 支持带 `yield` 的“洋葱模型”处理方式

说明：

- 每条消息都会经过它
- 它是消息处理主链路的中枢

## 当前运行路径

当前消息大致路径：

1. 平台适配器接收消息
2. 平台适配器构造 `AstrMessageEvent`
3. 写入官方 `event_queue`
4. `EventBus.dispatch()`
5. `PipelineScheduler.execute()`
6. 官方前置 stage 执行：唤醒、白名单、会话状态、限流、内容安全、预处理
7. 进入 `ProcessStage`
8. interaction middleware 创建 turn state；协议任务走独立 Core bypass，普通对话先完成 Router
9. Router 选择 `persona` 时进入 Persona Expression；选择 `hybrid` 时调用 Planner，Planner 仅在返回 `execute` 后继续调用 Core，未委托 Core 的路径才进入 Persona Expression；群聊模型续接候选还可选择 `silent`
10. 路由后的最终 Persona 或 Core 分支调用相应插件生命周期与工具；关键词、命令等 Pipeline Handler 仍在官方 pipeline 中运行

群聊的 Conversation 历史只为语义判断提供上下文，不自行扩大 Waking 边界。当前 active turn 的同一
发送者可立即 follow-up；Bot 成功回复后的前 10 秒可直接续接，此后到配置窗口截止只进入现有 Router，
由它判断 `silent / persona / hybrid`。该候选路径先等 Router 再启动 Persona，Router 失败按 `silent`
完成；窗口外和其他发送者仍需唤醒词、@ 或引用 Bot。

interaction turn 的输出路径与普通事件不同：

- 普通事件继续走旧 pipeline result decoration / respond
- interaction 事件由 `InteractionOutputController` 接管 send / streaming 语义
- interaction 的 finalized material 先由 middleware 同步幂等提交到官方 Conversation；提交成功后才完成 turn 并调度 postprocess
- memory service 在 `AFTER_TURN_COMPLETED` 消费 finalized material；后台 postprocess 任务由 `PostProcessManager` 统一持有，Core shutdown 会在卸载插件和 Provider 前停止并等待它们
- Core 执行连续性写入独立 Execution Ledger，不混入可见对话

内部系统观察不进入官方平台消息 Pipeline。当前代码已经分开两个入口：

```text
RuntimeObservation
  -> PersonalRuntimeManager.submit_observation
  -> per-Runtime bounded Inbox
  -> fixed 1.5-second aggregation window
  -> immutable ObservationBatch
  -> deterministic Gate
     -> evaluate: optional Personal Policy
     -> hold: restore to Inbox
     -> reject: stable diagnostics
  -> Policy decision / fail-closed observe
     -> express: ActionIntent -> RuntimeObservationEvent -> Persona -> Output
     -> defer: persist no-action deadline -> Wake Scheduler
     -> ignore / observe: Runtime diagnostics only

已经决定发送的主动输出
  -> RuntimeObservationEvent
  -> submit_runtime_observation_event
  -> Personal Expression -> Output Controller
```

通用 Observation Intake 不创建平台事件、不取得 turn lease，也不要求目标支持主动发送。Gate
只读取 batch、PersonalState、Runtime 忙闲和目标能力，返回稳定 disposition、reason 与 features；
`reject` 和 `hold` 零 Provider 调用。`evaluate` 在 Personal Policy 显式启用时通过规范 Prompt target
调用独立 Provider，严格要求协议级 tool-call，失败统一记录为 `observe`。Policy 不持有工具、
Skills、知识库或输出能力；`express` 只形成 `ActionIntent` 并交回 Runtime，最终可见内容始终由
Persona Expression 生成。`defer` 写入无动作截止时间并保留 batch；生命周期托管的 Wake Scheduler
会在 defer、quiet hours 或冷却到期后重新评估。`hold` 会恢复 batch；busy hold 在当前 turn settle 后
重新评估。多目标 Heartbeat Source 已由现有 Core
Lifecycle 托管：`platform_settings.personal_runtime_observation_targets` 留空时回退到默认主动目标；全局 Source 汇总所有已加载配置文件中声明、且 UMO 实际路由回该配置的目标。每个目标的开关和间隔读取其实际命中的 Runtime 配置，并维护独立 due time。配置关闭时不提交事实，启用后每个到期 target 只检查既有 retained batch；空 Inbox 不创建 Observation 材料、批次或唤醒任务。它不构造 event/message，也不调用 Persona、Core 或 Output。默认关闭的群聊
环境 Source 复用这份目标范围：配置目标中的非唤醒群聊文本通过官方白名单和会话状态检查后，
仅提交不含原文的 `conversation_activity` fact，并在普通限流、插件、Router 和 Core 前结束原事件。
插件可通过 `Context.register_runtime_observation_sensor(sensor)` 注册受限的事实 Source；返回的
handle 只能向同一通用 Intake 提交带目标会话、类别、过期时间和结构化 payload 的 Observation。
生命周期装配器负责把它交给既有 `PersonalRuntimeManager`，因此插件不会拿到 Runtime、EventBus、
Provider、ToolSet 或平台直发能力。插件卸载时注册会按 module prefix 自动删除，旧 handle 随即失效。
`Context.send_message()` 仍是已经决定发送的兼容 API，不是 Sensor 或 Personal Policy
决策；它保持 `support_proactive_message` 的精确发送语义，不进入自主表达防重。
主动输出兼容
入口继续与平台消息共享 session runtime 锁，并在 admission 时校验目标发送能力。普通插件 `Context.send_message()` 的纯文本输出仍走已经决定发送
的路径；同一 active turn 的 Core 工具输出作为 progress 进入现有 Output Controller，跨 session
输出建立独立 proactive turn。纯媒体主动消息暂时保留平台直发。

`PersonalSessionRuntime` 当前按 `config_id + persona_id + audience_key + privacy_scope` 在进程内
跨 turn 保留 `PersonalState`。空闲 Runtime 最长保留 24 小时，空闲集合最多 1024 条；Manager
在 bind、settle 和 observation admission 边界惰性执行回收，不运行独立清理线程。每个 Runtime
拥有最多 64 条 Observation 的 Inbox 和唯一 1.5 秒固定聚合窗口 task；窗口内的新事实不延长
截止时间，Policy 调用期间新增的事实会顺序进入下一批，pending facts 或 task 存在时不属于 idle。
尚未成功落盘的控制状态同样不属于 idle，不能被 TTL / LRU 静默回收。
Personal Policy 的开关、独立 Provider、temperature、timeout 和每日调用上限已接入配置。首次创建
Runtime 时，窄化的 Personal State Repository 按 RuntimeKey 恢复最近表达、冷却、静音和每日
用量；Policy 调用计数在 Provider 请求前持久化，写入失败时零 Provider 调用。最后 decision、Gate
状态、Inbox、active turn 和 attention 只服务进程内运行控制与 diagnostics，不持久化。
Repository 恢复失败会降级为当前进程内状态，最终保存失败只记录诊断，不会中断 Core shutdown。
主动人格静音、安静时段、回复/不动作冷却时长和每日主动输出上限也已接入配置。安静时段复用
官方全局 IANA timezone；Gate 当前执行静音、安静时段和输出预算。两个 cooldown 时长只作为后续
Action 配置：`defer` 使用不动作冷却作为最小等待时间；`express` 只有在可见输出确认送达后才写入
回复冷却和每日主动输出计数。

Policy 对近期已表达且当前 batch 没有新事实的同一意图应返回 `ignore / observe`。Conversation 和
Memory 可投影为 Policy 的受限语义上下文，但不能单独产生 batch、material revision 或唤醒权限。只有携带
`PersonalActionIntent` 的自主 Persona 请求会启用上一条回复防重：Runtime 从真实投递回执保留不可逆
规范化指纹，Persona 的 Conversation history 快照提供重启兜底；生成后若与上一条一致，则在 effect、
TTS、平台投递和 Conversation 提交前以 `suppressed` 完成。抑制不会推进冷却或主动配额。显式
`Context.send_message()`、Cron 和插件主动输出不进入该检查。

Turn lease 在关闭本轮 `TurnExecutionScope` 后、释放 session 锁前形成一次
`CompletionFeedback`。投递终态以 `InteractionUtterance.delivered_message_ids` 为准，再结合 turn
的 completed / failed / cancelled 和 final output 的 suppressed / failed 状态；不能仅根据发送意图
或 final output 标记推测成功。只有真实 delivered 的可见输出更新 `last_expression_at`。最后一份
不可变反馈保存在 Runtime diagnostics，不写入 event extra。带 `ActionIntent/action_id` 的表达和普通
回复可区分；前者只有在物理投递回执确认后才消耗主动输出预算。

现有 `RuntimeObservationEvent` 和 `submit_runtime_observation_event()` 是已经决定输出后的平台
适配入口，不是通用 Observation Inbox。通用 `submit_observation()` 已按相同 Runtime 身份接收
不可变事实，并执行 expiry、coalesce、overflow、batch close 和确定性 Gate；只有后续策略决定
表达后才会复用现有 Persona 和 Output 路径。

无显式目标的主动输出通过 `Context.get_proactive_message_target()` 读取
`platform_settings.proactive_message_target`。该值是完整 UMO；WebUI 仅列出当前支持主动
消息的已知会话，运行时仍会重新验证 Adapter。`Context.send_message(None, ...)` 和无目标
主动 Cron 使用它，显式 session 不会被覆盖。`platform_settings.personal_runtime_observation_targets`
单独定义 Personal Runtime 的多目标观察范围；留空时才兼容使用默认主动目标。Heartbeat 仅为这些目标创建周期
Observation，群聊环境 Source 只在显式开关开启时把这些群的非唤醒文本转为结构化
`conversation_activity`。这份范围由 Context 跨已加载配置解析，并只保留 UMO 实际路由回声明配置的
目标；群聊当前命中的配置决定环境观察开关和该 Runtime 的 Policy 设置。二者都不直接创建 Action 或主动回复。

`/stat/personal-runtime` 是只读运行诊断入口。除已实体化 Runtime 的 batch、Gate、Policy 和投递
终态外，它还列出已配置 Heartbeat 目标的开关、间隔、调度状态和下一次到期时间；不输出
Observation payload、用户原文或 Persona 文案。

## 重构意义

Yakumo 架构下，这一层未来应只保留：

- 应用装配
- 事件路由
- 消息调度
- 生命周期管理

不再直接承担所有能力实现的初始化细节

## 静态依赖复核

2026-07-21 对当前 `astrbot.core` 的 474 个模块做了顶层运行时 import 结构分析。
Process SubStage 的基础类导入曾绕回 `process_stage.stage`，依赖该模块“先定义 Stage
再导入 SubStage”的初始化顺序；`star_manager` 也曾通过 `star` 包级导出读取
`StarMetadata`。两处现已改为直接依赖定义模块，运行时 import SCC 降为 0。

无循环不表示边界已经完成。当前高 fan-out 装配点仍包括 `astr_main_agent`、
`star.Context`、`CoreLifecycle`、`InteractionMiddleware` 和 `PromptContextCollector`。
其中 Lifecycle 的高 fan-out 符合 composition root 定位；其余模块仍混有运行时协议、
兼容对象和业务编排。完整当前依赖方向见
`../dev/runtime-dependency-structure.mmd`。
