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
8. interaction middleware 创建 turn state；协议任务走独立 Core bypass，普通对话并发启动 Router 与统一 Persona Expression
9. Router 选择 `persona` 时不启动 Core；选择 `hybrid` 时调用 Planner，并只在 Planner 返回 `execute` 后继续调用 core agent
10. pipeline 内部调用插件、主 Agent、工具等能力

interaction turn 的输出路径与普通事件不同：

- 普通事件继续走旧 pipeline result decoration / respond
- interaction 事件由 `InteractionOutputController` 接管 send / streaming 语义
- interaction 的 finalized material 先由 middleware 同步幂等提交到官方 Conversation；提交成功后才完成 turn 并调度 postprocess
- memory service 在 `AFTER_TURN_COMPLETED` 消费 finalized material；Core 执行连续性写入独立 Execution Ledger，不混入可见对话

内部系统观察不进入官方平台消息 Pipeline。当前代码提供
`RuntimeObservationEvent -> PersonalRuntimeManager -> Personal Expression -> Output
Controller` 的显式入口，并与平台消息共享 session runtime 锁。该入口尚未由 Heartbeat
或 Scheduler 自动触发。普通插件 `Context.send_message()` 的纯文本输出现在通过
Personal Runtime 排队；同一 active turn 的 Core 工具输出作为 progress 进入现有 Output
Controller，跨 session 输出建立独立 proactive turn。纯媒体主动消息暂时保留平台直发。

`PersonalSessionRuntime` 当前按 `config_id + persona_id + audience_key + privacy_scope` 在进程内
跨 turn 保留 `PersonalState`。空闲 Runtime 最长保留 24 小时，空闲集合最多 1024 条；Manager
在 bind、settle 和 shutdown 边界惰性执行回收，不运行独立清理线程。该状态当前只服务运行控制
和 diagnostics，尚未持久化，也尚未接入 Inbox、Gate 或 Policy。

Turn lease 在关闭本轮 `TurnExecutionScope` 后、释放 session 锁前形成一次
`CompletionFeedback`。投递终态以 `InteractionUtterance.delivered_message_ids` 为准，再结合 turn
的 completed / failed / cancelled 和 final output 的 suppressed / failed 状态；不能仅根据发送意图
或 final output 标记推测成功。只有真实 delivered 的可见输出更新 `last_expression_at`。最后一份
不可变反馈保存在 Runtime diagnostics，不写入 event extra。主动输出预算尚未计数，因为当前还没有
可以区分主动行动与普通回复的 `ActionIntent/action_id`。

现有 `RuntimeObservationEvent` 和 observation submission 是已经决定输出后的平台适配入口，
不是未来通用 Observation Inbox。后续 Inbox 接收事实时不会构造用户消息、进入 EventBus 或
要求目标 Adapter 支持主动发送；只有策略决定表达后才复用现有 Persona 和 Output 路径。

无显式目标的主动输出通过 `Context.get_proactive_message_target()` 读取
`platform_settings.proactive_message_target`。该值是完整 UMO；WebUI 仅列出当前支持主动
消息的已知会话，运行时仍会重新验证 Adapter。`Context.send_message(None, ...)` 和无目标
主动 Cron 使用它，显式 session 不会被覆盖。这个机制只提供 delivery target，不创建
Heartbeat、Sensor 或主动回复策略。

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
