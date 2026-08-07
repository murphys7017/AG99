# Personal / Router / Plugin 三线并行设计计划

## 文档状态

- 状态：最终设计冻结（2026-08-06）；Phase 5B-1 至 5B-8 的生产实现和代码侧诊断已完成，并由默认关闭的全局开关保护；Phase 5B-0 基线与 Phase 5B-8 启用仍等待真实日志验收。
- 所属阶段：runtime-function-unification-plan.md 的 Phase 5B。
- 风险：高。
- 目标：官方 Plugin Handler 执行不再位于 Personal 首回复关键路径。
- 实施顺序：统一 Handler 执行器，再做事件隔离，然后开启三线并行、Core Gate、后台脱离和迟到投递。

本文已经冻结最终目标、owner 边界、状态与时间模型、迟到交付边界、实施顺序、停止线和完成标准。
Phase 5B-0 已加入默认关闭的全局开关、窗口配置、WebUI 文本和 discovery/Handler body 诊断，仍需
真实日志记录 Personal/Router/Core 对照基线。Phase 5B-1 至 5B-3 已完成统一 Handler 执行器、
branch-local event、PluginExecutionRuntime、module lease、reload draining 和 delivery ledger。
Phase 5B-4 至 5B-7 已把同一 t0 三线启动、Core Gate、窗口到期脱离、T1 artifact delivery、低优先级
T2、父对话绑定和 direct/media assistant-only 历史接入生产 ProcessStage。全局开关仍保持 false；
真实私聊/群聊时间线完成前不得默认启用。

Prompt Context 已同步拆成两层 single-flight：基础事实层只收集官方可信控制面扩展，Router、Core
Planner 和 Persona 的首个请求只等待这一层；普通 Prompt Extension 与 Interaction Prompt
Contributor 在基础事实完成后立即后台预取，每轮只收集一次。Persona 只在插件 Pack 已经就绪时
尽力使用，否则直接用基础 Pack；Core 则等待并复用同一个插件 Pack。慢 Contributor 因而不再把
自身耗时叠加到 Personal 首回复或 Router / Planner 控制面判断。

本文只处理普通 Interaction turn 中官方 Pipeline Handler 的执行位置、状态隔离、Core 仲裁和
迟到结果交付。Prompt Extension、LLM 生命周期 Hook、FunctionTool 和 Persona Effect 继续遵守
各自已有的 target 边界，不进入本文所称的 Official Plugin Task。

本文不承诺“首回复零等待”或“所有插件行为永不阻塞”。Handler Filter discovery、Personal
Runtime admission、基础 Prompt 构建、Provider 排队、模型加载和平台发送仍可能影响首回复。
Phase 5B 的准确承诺是：实际 Handler body 与普通插件 Prompt enrichment 都不再串行阻塞 Personal
和 Router 启动。

## 一、最终目标

系统的第一优先级是 Personal 首个用户可见回复。Phase 5B 的成功标准不是让所有插件更快，而是
让慢 Plugin Handler 不再把自身耗时叠加到 Personal 首回复上；插件兼容、Router 仲裁和 Core
执行必须围绕这一优先级工作，而不是重新把 Personal 放回等待链路。

平台消息完成官方准入、Handler Filter discovery 和 Personal Runtime lease 后，以同一个 t0
同时启动三条执行线：

~~~text
platform admission
  -> Handler Filter discovery
  -> Personal Runtime lease
  -> InteractionTurnCoordinator
  -> t0
       -> Personal Task
       -> Router Task
       -> Official Plugin Job
       -> Core Gate / Plugin watcher
~~~

三线只能由 Interaction 层的 InteractionTurnCoordinator 创建。ProcessStage 在 admission、Handler
discovery 和 Personal Runtime lease 完成后调用 Coordinator，继续负责 Pipeline 推进和异步生成器
桥接，但不再拥有 Router、Personal、Plugin Job 的仲裁。Middleware 中现有的 Personal/Router
编排迁入 Coordinator，避免 ProcessStage 和 Middleware 各自持有半套 turn 生命周期。

Personal 生成结果后立即尝试发送，不等待 Router、Plugin、Planner 或 Core。Router 只产生
silent、persona、hybrid 分类。Official Plugin Job 按官方顺序执行全部 activated Handlers。

普通 Core 只在以下两个条件同时满足时启动：

~~~text
Router 已完成并要求 Core
AND
Plugin Gate 已解析且允许普通 Core
~~~

Plugin Gate 等待的是插件对旧 turn 的“决定”，不是等待插件的所有后台工作完成。

## 二、官方插件兼容基线

Phase 5B 不增加 claim、accept、插件路由模型、工具预判模型或 per-plugin timeout。

必须保留：

1. WakingCheckStage 继续执行官方 Handler discovery 和 Filter。
2. 权限、session plugin filtering、Handler 优先级和执行顺序不变。
3. StarRequestSubStage 继续按顺序推进 activated Handlers。
4. stop_event、MessageEventResult、event.send 和异常行为保持官方语义。
5. 窗口内 Handler yield ProviderRequest 后的官方委托与 post-yield 语义继续保留。
6. 一个 Handler 停止事件后，后续 Handler 不再执行。
7. 非 Interaction 流程继续走官方串行 Plugin Handler 路径。

“所有插件都过一遍”指官方 discovery 仍检查所有相关 Handler 的 Filter；真正执行的仍是通过
Filter、权限和 session plugin filtering 的 activated Handlers。

## 三、插件能力边界

五类插件能力分别归属：

| 能力 | 归属 |
| --- | --- |
| Pipeline Handler | Official Plugin Job |
| Prompt Extension / Contributor | 自身声明的 Personal 或 Core Prompt target |
| LLM 生命周期 Hook | plugin_runtime_targets |
| FunctionTool | plugin_tool_targets |
| Persona Effect | Personal |

plugin_runtime_targets 和 plugin_tool_targets 不控制 Pipeline Handler、Prompt Extension 或
Persona Effect。Router 和 Planner 不加载普通插件 Prompt、Hook 或工具，但官方群聊上下文等
系统可信控制面事实必须保留。

Prompt Extension / Contributor 的收集 owner 是独立插件上下文 pack，而不是 Router 或 Persona。
该 pack 每个 turn 只构建一次，并按 `meta.targets` 投影给 Persona 或 Core；Router / Core Planner
只消费基础 pack。Persona 对该 pack 采用 non-blocking best-effort：已就绪才消费，pending、失败或
取消时立即回退基础 pack；Core 必须等待同一个 single-flight task，不得重新执行 Contributor。
单个 Contributor 失败或返回无效产物只记录并跳过，不使其他 Contributor 或整个 Core pack 失败；
取消由 turn 总生命周期负责。不得为了降低等待再增加第二套 Collector target 配置、插件预判模型
或按插件超时。

从业务行为看，本计划只承认两类插件：

1. 上下文修改类：在 Personal 或 Core 请求形成前，通过 Prompt Extension / Contributor 修改
   结构化上下文。它们不进入 Official Plugin Job，也不产生迟到消息。
2. 独立系统类：拥有自己的处理、模型和状态，只借用 AstrBot 的消息输入输出边界。它们进入
   Official Plugin Job，最终只产出 semantic、direct、媒体或 progress 产物。

ProviderRequest 不是第三类插件，也不是迟到结果通道。它只作为旧插件在窗口内的官方兼容行为
保留；Plugin Gate 已经 EXPIRED 后出现的 ProviderRequest 不再被系统承认。

提交 fbb68ab51 已完成普通插件能力与 Router / Planner 控制面的隔离。本计划只复核该边界，
不重复改造 target。

## 四、冻结设计决策

1. Personal、Router、Official Plugin Job 在同一个 t0 启动。
2. Personal 结果就绪后立即尝试取得即时输出权。
3. Official Plugin Job 内部串行执行 Handlers，不并发每个插件。
4. 只增加一个对话窗口 plugin_parallel_window_seconds，从 t0 绝对计时。
5. 对话窗口到期只停止旧 turn 等待，不取消真实 Plugin Job。
6. Plugin Job 从创建起由 PluginExecutionRuntime 持有，TurnExecutionScope 只持有 watcher。
7. Plugin Gate 状态与 Plugin Job 状态分开。
8. Plugin Gate 仍为 PENDING 且窗口未到期时，第一次 yield ProviderRequest 即把旧 turn 解析为
   DELEGATED，不等待 Provider 执行结束。
9. 已发送 Personal 不撤回；Router 或插件只能压制 pending Personal。
10. pending Personal 的压制采用尽力取消；无法取消时丢弃结果，不等待模型任务结束。
11. 独立系统插件的 semantic 结果经 Personal 表达；direct、命令、权限、协议和媒体输出保留
    兼容路径。
12. detached Job 不得修改旧 turn 的 stop、result、ProviderRequest、reservation 或 completion。
13. 迟到结果通过新的受限后台 turn T2 交付。
14. T2 分为 delayed_plugin_expression 和 delayed_plugin_direct 两种 profile。
15. 两种 T2 都携带 delayed metadata，并写入 assistant-only 历史。
16. T2 不运行 Router、Planner、普通 Core、官方 Plugin Handler、FunctionTool 或 subagent。
17. T2 的执行 deadline 在真正获得 admission 后开始。
18. normal platform turn 始终优先于 delayed turn。
19. 同一插件产物只能投递一次，窗口内和迟到投递共享 delivery_key。
20. 一个 Plugin Job 可以包含多个插件和多个 Handler，产物不得按整个 job 粗暴合并。
21. Plugin Job 持有插件模块/runtime lease，直到 Job 终态后释放。
22. Plugin Job 使用 branch-local output sink 和隔离的 ContextVar。
23. 第一实施批次不设置 detached Job 容量拒绝或第二个 TTL。
24. 后台 Job 只增加存活数量、最长存活时间和失败数量诊断。
25. shutdown 是第一实施批次唯一强制取消后台 Job 的生命周期边界。
26. Plugin Gate 已经 EXPIRED 后出现的 ProviderRequest 不执行、不启动 Core、不进入 T2，只记录
    provider_request_ignored_after_detach 并安全收口当前 Handler invocation。
27. T2 固定携带 parent_conversation_id；父对话已经重置或不可用时，不得把迟到结果写入新的
    conversation。
28. direct/media T1 与 T2 共用 assistant artifact history serializer，不依赖仅支持文本的
    assistant_text 提交路径。
29. InteractionTurnCoordinator 是三线 task、Plugin watcher、Core Gate 和取消仲裁的唯一创建者，
    归属 Interaction 层，由 ProcessStage 在 admission 后调用。
30. branch event 创建时快照所有 Prompt 可见消息输入；adapter、session 和平台活对象只读共享，
    不得把可变 message chain 当作不可变输入共享。
31. Plugin Job EXPIRED 后允许出现 Personal immediate、Core final 和迟到插件 T2 三段输出；T2
    只用确定性标准化指纹抑制与 T1 已发送可见内容完全等价的产物，不增加语义判断模型。
32. 目标不支持 proactive message 时，迟到产物直接丢弃并记录
    delayed_delivery_target_unsupported，不重试，也不回灌普通平台事件。
33. 功能开关控制整个新 Plugin Job 主路径，不支持 per-plugin 新旧路径混用。
34. Router 要求 Core 时必须记录插件窗口造成的实际 Core 启动延迟。
35. reload/unload 先进入 draining，等待活跃 Plugin Job lease 释放后再完成 unbind 和模块清理；
    draining 期间到达的新 turn 跳过整条 Official Plugin Job，并以 PASSED fail-open 继续
    Personal/Router/Core，不回退到仍可能调用该模块的旧串行 Handler 路径。
36. 窗口内第一条 final 立即把 Gate 解析为 HANDLED，并只把当时已经形成的 artifact 快照交给
    T1；同一官方 Handler 链随后形成的 final 在 T1 收口后进入低优先级 T2，不静默丢弃，也不让
    迟到 ProviderRequest 重新取得 Core 权限。

## 五、状态与时间模型

### 5.1 Plugin Gate 状态

~~~text
PluginGateResolution:
  PENDING
  PASSED
  HANDLED
  STOPPED
  DELEGATED
  FAILED
  EXPIRED
~~~

Gate 描述 Plugin Job 对原始 turn T1 的控制结论。

### 5.2 Plugin Job 状态

~~~text
PluginJobState:
  RUNNING
  COMPLETED
  FAILED
  CANCELLED
~~~

Job 描述真实插件协程是否仍在运行。EXPIRED + RUNNING 精确表示“已脱离 T1，但仍在后台执行”。

### 5.3 时间定义

~~~text
t0                       三条执行线共同启动时间
tp                       Personal 完成时间
tr                       Router 完成时间
plugin_resolved_at       Plugin Gate 首次离开 PENDING 的时间
plugin_completed_at      Plugin Job 真正结束时间
plugin_window_deadline   t0 + plugin_parallel_window_seconds
~~~

Personal 首回复时间：

~~~text
first_visible_reply_at = tp + output_materialization
~~~

普通 Core 最早启动时间：

~~~text
core_gate_at = max(tr, plugin_resolved_at)
~~~

Router 要求 Core 时记录：

~~~text
core_start_delay_due_to_plugin_ms = max(0, plugin_resolved_at - tr)
~~~

该指标只计算 Plugin Gate 相对 Router 完成时间额外造成的等待，不把 Router 自身耗时记为插件延迟。

plugin_resolved_at 取以下事件中第一个发生者：

1. Handler 链正常结束且没有取得处理权：PASSED。
2. 最终 semantic/direct 产物被确认：HANDLED。
3. stop_event 生效：STOPPED。
4. 窗口内第一次 yield ProviderRequest：DELEGATED。
5. Plugin Job 执行器、媒体物化、branch sink 或 Runtime 在取得处理权前失败：FAILED。
6. 绝对插件窗口到期：EXPIRED。

Router 运行 2.5 秒且插件窗口为 3 秒时，Core 最多只再等待共同窗口剩余的 0.5 秒，不在 Router
完成后重新增加一个完整窗口。

activated_handlers 为空时，不创建 Plugin Job 或 branch event，Plugin Gate 在 t0 直接视为
PASSED，不增加任何等待。

## 六、三线协调规则

### 6.0 InteractionTurnCoordinator

InteractionTurnCoordinator 位于 ProcessStage 与 Interaction 运行时之间，是普通 Interaction turn 的
唯一并发协调点，负责：

1. 固定共同 t0，并创建 Personal Task、Router Task、Plugin Job 和绝对窗口 watcher。
2. 持有 Core Gate，消费 Router 结果与 Plugin Gate resolution。
3. 只取消或压制 turn-owned 的 pending task，不取得真实 Plugin Job 的生命周期所有权。
4. 将 Plugin Job 注册到 PluginExecutionRuntime，并在 T1 收口时只关闭 watcher。
5. 统一记录三线时间戳、Gate 原因、输出仲裁和 terminal outcome。

ProcessStage 保留 admission、Pipeline stage 推进和 async-generator 桥接职责；Middleware 保留
Interaction 服务入口和兼容适配，但两者都不得再单独创建另一套 Personal/Router/Plugin
仲裁。不得把 Coordinator 做成新的业务巨类：Handler 执行、Plugin Job 生命周期、输出交付和
session admission 分别委托给已有或本计划定义的专属 owner。

### 6.1 Personal Task

Personal 保持现有即时表达状态：

~~~text
pending -> committed -> emitted
        -> suppressed
        -> failed
~~~

Personal 模型请求已经发出但仍处于 pending 时，Router silent 或窗口内插件取得处理权可以尽力
取消该任务。任务不可取消时不等待其结束；结果返回后直接丢弃，不进入输出控制器。

committed 或 emitted 的 Personal 不撤回。

### 6.2 Router Task

Router 只产生 silent、persona、hybrid：

1. Router 不读取 Plugin Job 的运行结果作为 Prompt。
2. Router 可以先完成并暂存，但不能绕过 Plugin Gate 启动普通 Core。
3. 插件取得处理权后，Router 结果不再消费；未完成 Router 任务可以尽力取消。
4. Router 已经与插件并行启动，因此不能承诺“插件接管时 Router 从未执行”。
5. Router silent 只压制原始 pending Personal，不能吞掉插件明确产生的 HANDLED 输出。

### 6.3 Official Plugin Job

一个 Interaction turn 只创建一个 Official Plugin Job。该 Job 按官方顺序运行所有 activated
Handlers，因此一个 Job 可能包含多个插件、多个 Handler invocation，以及窗口内旧插件兼容所需
的 ProviderRequest。

Job 由 PluginExecutionRuntime 从 t0 起持有。T1 只等待 watcher：

~~~text
plugin_job = plugin_runtime.start(...)
plugin_watcher = turn_scope.create_task(
    plugin_job.wait_for_gate(plugin_window_deadline),
    role="plugin_window",
)
~~~

窗口到期只使 watcher 返回 EXPIRED。真实 Job 从未进入 TurnExecutionScope，无需“摘除”，也不会
被 T1 close 取消。

## 七、统一 PluginHandlerExecutor

Phase 5B 必须先从 ProcessStage 中抽出统一 PluginHandlerExecutor。StarRequestSubStage 继续负责
按顺序调用 Handler，但不能单独承担完整执行语义，因为当前 ProviderRequest 的消费、Agent
执行和生成器继续推进仍由 ProcessStage 持有。

PluginHandlerExecutor 负责：

1. 推进 StarRequestSubStage。
2. 捕获和分类 Handler 产物。
3. 处理窗口内旧插件兼容所需的零到多个 ProviderRequest。
4. 在每次 ProviderRequest 执行后恢复 Handler 生成器。
5. 保留 post-yield 和后续 Handler 执行。
6. 应用 stop_event 和官方异常语义。
7. 生成类型化 PluginBranchResult。
8. 向 Plugin Gate 发布首次解析事件。

旧串行路径必须先改用 PluginHandlerExecutor，并验证行为不变。并行路径只能复用该执行器，不得
复制第二套 ProviderRequest 或 Handler 循环。

## 八、branch-local event

Personal、Router 和 Plugin Job 不能并发写同一个 AstrMessageEvent。Plugin Job 使用与原事件
具体平台类型兼容的 branch-local event：

~~~text
main event
  -> Personal / Router / Core 控制状态

plugin branch event
  -> branch result / stop / send / extras
  -> branch-local output transaction
  -> shared live handles and snapshotted prompt-visible input
~~~

实现要求：

1. 使用受控分支构造，保留平台事件具体类型和 isinstance 兼容。
2. 在 branch 创建时快照 message_str、消息组件、sender/group 可见字段和其他 Prompt 可见标量。
3. 消息组件优先使用受控的 model_copy(deep=True) 或等价组件级复制，不 deepcopy 整个 event。
4. 只读共享 adapter、session、平台连接和 raw platform handle 等活对象。
5. 隔离 result、stop、send 状态、ProviderRequest、输出 transaction，以及 extras 中普通
   dict/list/tuple/set 等可变容器；Context、Provider、锁和插件实例等不透明活对象只读共享。
6. 禁止 deepcopy Context、Provider、Future、锁、队列、插件实例或完整 raw platform event。
7. 同一 branch event 在 Handlers 间持续存在，插件间临时 extras 可以在分支内部传递。
8. 分支结束禁止把整个 extras 覆盖回 main event。
9. 只允许经过审计的极小兼容字段显式投影。

Plugin Job 还必须使用 branch-local ContextVar 和 output sink。asyncio task 会复制创建时的
ContextVar；如果继承旧 Personal turn，插件主动发送可能绕过 branch transaction。因此创建 Job
时必须清理旧 turn 激活上下文，并将插件输出绑定到 branch sink。

## 九、PluginBranchResult 与产物

内部结果不作为公共插件 API：

~~~text
PluginBranchResult:
  gate_resolution
  job_state
  output_artifacts
  provider_executions
  stopped
  failure
~~~

产物分为：

~~~text
progress
  可见进度，不拥有 final

semantic
  需要 Personal 表达的业务结果

direct
  命令、权限、协议、显式 direct 或媒体结果
~~~

窗口内 progress 继续遵守现有输出 transaction，不应误判为 HANDLED。Job 脱离后，progress 不再
直接穿透 T1；它可以作为同一 Handler invocation 的上下文被最终结果吸收，但不会单独创建大量
迟到 turn。明确的 direct final 除外。

每个产物至少记录：

~~~text
plugin_job_id
origin_plugin_id
origin_handler_name
handler_invocation_id
artifact_sequence
artifact_kind
delivery_group_id
created_at
~~~

delivery_key：

~~~text
plugin_job_id + handler_invocation_id + artifact_sequence
~~~

delivery ledger 状态：

~~~text
produced
-> delivery_reserved
-> delivered_inline
-> delivered_delayed
-> suppressed_duplicate
-> delivery_failed
~~~

delivery reservation 必须由 PluginExecutionRuntime 原子持有。窗口内插件 final 和迟到 T2 使用同一
delivery_key；窗口内已经投递的产物不得迟到重发。ledger 记录保留到该 Job 的 T1 与 T2 全部收口，
随后按 plugin_job_id 释放；它不是跨 Job 的永久历史索引。

只允许合并同一 handler_invocation_id 或同一 delivery_group_id 下的相关产物。一个 Job 中来自
不同插件或不同 Handler invocation 的产物不得合并成一条 Personal 回复。

## 十、窗口内仲裁与输出

建议 Gate 仲裁优先级：

~~~text
stop_event
-> first ProviderRequest
-> confirmed final output
-> Handler chain completed without ownership
-> terminal failure
~~~

stop_event 控制后续执行，但不自动丢弃已经形成的输出：

1. STOPPED 且存在 semantic/direct 产物：交付产物，不启动普通 Core。
2. STOPPED 且没有产物：静默结束，不启动普通 Core。
3. DELEGATED：普通 Router Core 不启动，由 Plugin Job 的 ProviderRequest 路径继续。
4. HANDLED：普通 Core 不启动，插件 final 成为 T1 final。
5. PASSED：使用 Router 结果，hybrid 时启动普通 Core。
6. EXPIRED：插件失去 T1 仲裁权，按 Router 结果继续。
7. FAILED：按 fail-open 继续等待 Router，并保留已经发送或仍在生成的 Personal；Router 要求
   hybrid 时允许普通 Core 继续。官方 Handler 自身的普通异常仍由现有错误产物与 stop 语义归一
   为 HANDLED/STOPPED，不使用裸 FAILED 接管 T1。

FAILED 只描述 Plugin Job 在尚未取得处理权时的基础设施或执行边界故障，不代表插件接管。它不得
取消 Router、压制 Personal 或静默吞掉原始 turn；诊断必须同时保留 Plugin Job failure 与最终
Router 决策。

STOPPED 是控制结论，不是空产物快照。插件常见的“先 stop_event，再 yield/send 最终结果”必须保留：
T1 交付收口前已经形成的产物仍走 T1；T1 收口后才形成的 final 走低优先级 T2，并与 T1 共用
delivery ledger。这样 stop 可以立即阻止 Personal 和普通 Core，又不会因为冻结空快照而丢失官方
Handler 随后的最终输出。

窗口内 HANDLED 时：

1. 原始 Personal pending：尽力取消；不可取消时丢弃其结果，不阻塞。
2. 原始 Personal committed/emitted：保留已发送内容。
3. 插件 semantic final 经 Personal 表达后取得 T1 final-output reservation。
4. 插件 direct final 经兼容输出路径取得 T1 final-output reservation。
5. immediate Personal + plugin final 是允许的两段式输出，与现有 immediate + Core-final 类似。
6. 插件 final 同样受 delivery_key 约束，只能投递一次。
7. T1 只交付 Gate 解析后取得的 artifact 快照，不等待完整 Handler 链结束。
8. 快照之后同一 Handler 链继续形成的 semantic/direct final 在 T1 完成后按第十三节进入 T2；
   已经 T1 投递的 delivery_key 会被 ledger 跳过。

## 十一、后台 Job 与插件 lease

PluginExecutionRuntime 是所有 Official Plugin Job 的唯一 owner，负责：

1. Job registry 和 Job 状态。
2. Gate 状态和 plugin_resolved_at。
3. 插件模块/runtime lease。
4. branch event 和 branch output sink。
5. 窗口内 ProviderRequest 兼容执行记录。
6. delivery ledger。
7. 后台 Job 完成通知。
8. shutdown 收口。

Job 可能包含多个插件，因此 Job 元数据不能使用单一 plugin_id。plugin_id 和 handler_name 属于
Handler invocation 或 artifact。

Plugin Job 在 T1 完成后继续持有相关插件 lease，防止插件 reload/unload 销毁仍在执行的 Handler。
Job 终态后释放 lease。Core Lifecycle shutdown 必须先停止 PluginExecutionRuntime，再释放插件和
Provider 资源。

插件 reload/unload 必须采用 draining 时序：先阻止该插件产生新的 Handler invocation，再等待
引用该插件模块/runtime 的活跃 Job lease 释放，最后才从 star_map、Handler registry、Tool registry
和模块缓存中完成 unbind/purge。reload 可以等待或明确报告仍在 draining，但不得先销毁对象再让
后台 Job 继续运行旧引用。

第一阶段不设置 PluginTaskBin 容量拒绝，也不设置第二个对话 TTL。Job 在 t0 已经启动，detach
时无法无损“拒绝新 Job”；强行设置容量上限只会转化为取消插件、阻塞 Core 或泄漏 task。

第一阶段只增加诊断：

~~~text
active_plugin_job_count
detached_plugin_job_count
oldest_plugin_job_age_seconds
background_job_completed_count
background_job_failed_count
background_job_cancelled_on_shutdown_count
~~~

这些指标只提供可见性，不改变执行。是否增加孤儿任务保护根据真实日志另立计划。

## 十二、ProviderRequest 兼容边界

ProviderRequest 只保留为窗口内旧插件兼容行为：

1. Plugin Gate 仍为 PENDING 且窗口未到期时，第一次 yield ProviderRequest 将 T1 Gate 解析为
   DELEGATED。
2. PluginHandlerExecutor 使用现有统一 Core execution boundary 执行请求。
3. 请求完成后恢复插件生成器，并允许旧插件继续执行 post-yield 或后续 Handler。
4. 一旦 Gate 已经解析为 DELEGATED，该插件执行路径继续属于 T1，不再转为 detached Job；
   ProviderRequest rendezvous 结束时以 Runtime Job task 的终态作为唯一收口，不再追加一次重复的
   `wait_completed` 完成屏障。
5. Gate 已经 EXPIRED 后才出现的 ProviderRequest 不被承认。
6. EXPIRED 后的 ProviderRequest 不执行、不启动普通或后台 Core、不进入 T2，也不重新写回旧
   event。
7. Runtime 记录 provider_request_ignored_after_detach，并安全关闭产生该请求的当前 Handler
   invocation，避免未消费异步生成器产生异常；随后继续执行后续已激活 Handler，不得关闭整条
   StarRequestSubStage Handler 链。

迟到输出链路只接受独立系统插件已经形成的 semantic、direct、媒体和必要的 progress 事实。
系统不为迟到 ProviderRequest 建立第二条 Core 执行路径。

## 十三、T1 后续投递 T2

T2 接受三种已经失去 T1 同步交付位置的产物：

1. Plugin Gate 因绝对窗口到期成为 EXPIRED 后，Job 最终形成的可交付产物。
2. Plugin Gate 已由第一条 final 解析为 HANDLED，T1 已交付当时快照后，同一官方 Handler 链继续
   形成的新 final 产物。
3. Plugin Gate 已由 stop_event 解析为 STOPPED，但官方 Handler 在 T1 收口后才形成的 final 产物。

三种情况中的 stop、result 和 send 都只作用于 branch。ProviderRequest 仍严格按第十二节处理：
EXPIRED、HANDLED 或 STOPPED 后出现的 ProviderRequest 都不执行、不启动 Core、不进入 T2。Job 完成后，
DelayedPluginDeliveryCoordinator 必须先等待 T1 完全收口，再读取 T1 可见输出、申请 delivery
reservation，并按需创建新的后台 Personal turn T2。

~~~text
T1
  -> Plugin Job EXPIRED + RUNNING
  -> T1 按 Router 结果继续并收口

Plugin Job
  -> COMPLETED
  -> wait T1 settled
  -> delivery reservation
  -> T2
~~~

EXPIRED 后 T1 可能按 Router 进入 hybrid 并发送 Core final，因此系统明确允许：Personal immediate、
Core final、插件 T2 三段输出。该行为只适用于已经形成的 semantic/direct/media 可交付产物；
EXPIRED 后才出现的 ProviderRequest 仍按第十二节丢弃。

HANDLED 后续产物不会重新打开 T1。第一条 final 仍以最低延迟成为 T1 final；后续 Handler 或
post-yield 形成的 final 使用相同 T2 profile 和 delivery ledger。这样既保留官方 Handler 链继续执行
的兼容语义，也不会让慢后续 Handler 重新阻塞首批插件回复。

STOPPED 不冻结空的 T1 artifact boundary。T1 读取收口时已经形成的产物；随后才到达的 final 进入
相同 T2 profile，已经由 T1 投递的 delivery_key 会被跳过。

T2 标识：

~~~text
turn_id = 新 ID
parent_turn_id = T1
parent_conversation_id = T1 所属 conversation
plugin_job_id = Job ID
handler_invocation_id = 产物来源
source = plugin_delayed_output
~~~

T2 不是平台用户输入，不伪造 user message。它复用 RuntimeObservationEvent 和 Personal Runtime
admission，生成新的 PersonalTurnContext、InteractionTurnState 和 final-output reservation。

父对话规则：

1. T1 admission 或首次历史解析时固定 parent_conversation_id，并由 Plugin Job 保留该不可变身份。
2. T2 Prompt 和历史提交都使用 parent_conversation_id，不根据当前 UMO 重新选择 conversation。
3. 用户在 T1 后执行 reset 或切换 conversation 时，T2 不得写入新的 conversation。
4. 父 conversation 已删除或不可用时，仍可按 delayed output policy 决定是否发送，但跳过历史
   写入并记录 delayed_history_skipped_parent_conversation_changed。
5. T1 没有可固定的 parent_conversation_id 时，T2 不得创建新 conversation；允许发送，但历史
   记录为 parent_conversation_unavailable。

### 13.1 delayed_plugin_expression

用于只包含非空纯文本的 semantic 产物：

1. 将插件事实和必要的原始输入关联构造成 visible_reply_material。
2. 调用 Personal Expression。
3. allow_plugin_tools 固定为 false。
4. 不运行 Router、Planner、普通 Core、Plugin Handler 或 subagent。
5. Personal target 上已有的 Prompt Extension 和 LLM 生命周期 Hook 仍可按现有规则生效。
6. 发送后写入 assistant-only 历史。

### 13.2 delayed_plugin_direct

用于 direct、命令、权限、协议和媒体产物；声明为 semantic/persona 但包含媒体或其他非纯文本
组件的 MessageChain 也降级到该 profile，避免 Persona Observation 丢失组件：

1. 不调用 Persona 模型。
2. 使用受控 Runtime output 路径原样发送 MessageChain。
3. 同样经过 admission、lease 和 final-output reservation。
4. 同样携带 delayed metadata。
5. 同样写入 assistant-only 历史。
6. direct/media 历史通过与 T1 共用的 assistant artifact history serializer 持久化 MessageChain 的可表示内容，
   不伪造 assistant_text；纯媒体无法表示时记录结构化媒体摘要和 delivery metadata。

两种 profile 都携带：

~~~text
plugin_delayed_output = true
plugin_job_id
origin_plugin_id
origin_handler_name
handler_invocation_id
parent_turn_id
parent_conversation_id
detached_at
delivery_key
~~~

metadata 随 send_message_with_extras 投递，正文零改动。direct 路径也必须带标签并写历史，保证
诊断可审计且后续对话上下文完整。

创建 T2 reservation 前，对产物正文或可稳定序列化的 artifact 表示生成确定性标准化指纹，并与
T1 已经实际发送的 Personal/Core 可见输出比较。完全等价时标记 suppressed_duplicate_visible_output
并结束交付；不完全等价时允许 T2。第一实施批次不增加 LLM 语义去重，也不增加 per-plugin
delivery policy。

## 十四、T2 admission 与 deadline

当前 PersonalSessionRuntime 使用普通 asyncio.Lock，给 reservation 增加 priority 字段并不能保证
后来到达的用户消息越过已经等待的 delayed turn。因此低优先级必须由统一 admission owner 实现，
不能依赖 Lock 的等待顺序。

目标队列：

~~~text
normal platform turns
  higher priority

delayed plugin turns
  lower priority
~~~

规则：

1. active 或 queued normal turn 存在时，不放行 delayed turn。
2. delayed turn 不打断 active turn。
3. delayed turn 已进入执行后，后续用户 turn 不强制取消它。
4. T2 的 TurnDeadlineBudget 在 admission 成功后创建。
5. delayed delivery 在排队期间只受 delivery coordinator 生命周期管理，不消耗 turn_timeout。
6. 同一 delivery_group_id 只能创建一个 T2。
7. admission 前再次检查 delivery ledger，避免重复创建。
8. reservation 前检查目标 support_proactive_message；不支持时丢弃产物，记录
   delayed_delivery_target_unsupported，不重试、不创建 T2，也不降级为普通平台事件。

实现可以引入 TurnAdmissionCoordinator，或在 PersonalSessionRuntime 内建立明确的 normal/delayed
双队列；不得只在 _reserve 上增加一个无效 priority 字段。

## 十五、配置

第一阶段只新增：

~~~json
{
  "interaction_middleware": {
    "parallel_plugin_runtime_enabled": false,
    "plugin_parallel_window_seconds": 3.0
  }
}
~~~

配置约束：

1. 从共同 t0 计算。
2. 受 T1 剩余 deadline 限制。
3. activated_handlers 为空时不生效。
4. 不增加 per-plugin timeout。
5. 不增加 claim、执行模式或动态模型判断。
6. 不增加后台 Job TTL 或容量拒绝配置。
7. parallel_plugin_runtime_enabled 只控制整条新 Plugin Job 路径，不允许按插件开启；关闭时整个
   turn 继续走旧串行路径，开启时该 turn 的全部 activated Handlers 统一走新路径。

## 十六、诊断

Handler discovery：

~~~text
handler_discovery_started_at
handler_discovery_completed_at
handler_discovery_duration_ms
activated_handler_count
~~~

每个 turn：

~~~text
turn_id / t0
personal_started_at / completed_at / emitted_at
router_started_at / completed_at / route_mode
plugin_job_id / plugin_started_at
plugin_window_deadline
plugin_resolved_at / plugin_gate_resolution
plugin_completed_at / plugin_job_state
core_gate_at / core_gate_reason
core_start_delay_due_to_plugin_ms
~~~

每个 Handler invocation：

~~~text
origin_plugin_id
origin_handler_name
handler_invocation_id
started_at / completed_at
provider_request_count
artifact_count
~~~

每个迟到交付：

~~~text
delivery_key
delivery_profile
parent_turn_id / delayed_turn_id
reserved_at / delivered_at
written_to_history
duplicate_disposition
target_proactive_supported
delivery_drop_reason
~~~

Runtime 聚合：

~~~text
active_plugin_job_count
detached_plugin_job_count
oldest_plugin_job_age_seconds
background_job_completed_count
background_job_failed_count
~~~

聚合日志使用同一 `turn_id` 和 `plugin_job_id` 串联三个快照：

1. `DIAG interaction.parallel_turn phase=control_resolved`：Router 与 Plugin Gate 已解析，记录
   `core_gate_at` 和插件额外等待。
2. `DIAG interaction.parallel_turn phase=t1_settled`：本轮 T1 已结束，记录 Personal 是否真正发送、
   Plugin Job 是否仍在后台运行。
3. `DIAG interaction.parallel_turn phase=plugin_completed`：Plugin Job 真正结束且迟到交付已处理。

`DIAG plugin.handler_invocation` 记录每个 Handler 的 ProviderRequest 与 artifact 数量；
`DIAG plugin.delayed_delivery` 记录 delivery key、T2 turn、reservation、投递、历史写入、去重与目标能力；
`DIAG plugin.runtime` 在 T1 收口和 Job 完成时记录活跃、脱离、最老 Job 年龄与后台终态累计值。
`DIAG interaction.context_material` 分别记录 `scope=base` 与 `scope=plugin` 的构建耗时、slot 数量和
扩展 Collector；真实日志应能证明 Router / Planner 在 base 完成后即可继续，而不等待 plugin scope。

真实日志必须证明：

1. Personal、Router、Plugin Job 共享同一 t0 启动窗口。
2. Handler body 不再位于 Personal 首回复关键路径。
3. Filter discovery 耗时可以被单独识别。
4. Core 等待的是 plugin_resolved_at，而不是 plugin_completed_at。
5. 窗口从 t0 计算，不在 Router 后重新计时。
6. Core 延迟指标能单独量化 Plugin Gate 在 Router 完成后的额外等待。
7. 不支持 proactive message 的目标和 T1 可见内容完全重复的 T2 都有稳定原因码。

## 十七、实施计划

### Phase 5B-0：文档、基线与配置

状态：配置、WebUI 和 discovery/Handler body 诊断已实现；真实运行基线待验证。

1. 以本文已经冻结的决策建立实现基线；若现场证据要求改变冻结边界，先返回文档审阅，不在代码
   中临时改设计。
2. 记录 Handler discovery、Handler body、Personal、Router 和 Core 基线时间戳。
3. 增加全局 parallel_plugin_runtime_enabled、plugin_parallel_window_seconds 配置、类型和 WebUI
   说明。
4. 配置保持关闭或不接入主链。
5. 复核 fbb68ab51 的 Router / Planner 边界。

验收：配置可解析，诊断能区分 discovery 与 Handler body，运行行为不变。

### Phase 5B-1：统一 PluginHandlerExecutor

状态：实现完成，基础边界验证通过。

1. 从 ProcessStage 抽取 Handler generator 消费和窗口内 ProviderRequest 恢复逻辑。
2. 支持窗口内多次 ProviderRequest 和后续 Handler。
3. 旧串行路径改用统一执行器。
4. 不引入并行或 branch event。

验收：现有 stop、send、result、ProviderRequest、post-yield 和错误行为不变。

实现结果：ProcessStage 不再手工消费插件生成器或恢复 ProviderRequest；统一执行器保持多次
ProviderRequest、每次委托后的生成器恢复、后续 Handler 和插件输出事务收口。旧串行主链继续
使用该执行器，新并行开关默认关闭。

### Phase 5B-2：branch-local event 与类型化结果

状态：实现完成，隔离边界验证通过，生产三线主链已使用该 branch。

1. 建立同平台类型的受控 branch 构造。
2. 快照 message_str、消息组件和其他 Prompt 可见输入，只读共享平台活对象。
3. 建立 branch output transaction。
4. 形成 PluginBranchResult 和 progress、semantic、direct 三类 artifact。
5. 禁止 extras 整体回写。
6. 继续保持串行主链作为安全对照。

验收：branch event 行为与旧 event 可见行为一致，嵌套普通 extras 不回写主事件，且没有活对象
deepcopy。

实现结果：Prompt 可见消息输入按组件快照；branch extras 递归复制普通容器并保留不透明运行时
句柄，避免 detached Handler 通过嵌套配置或参数结构修改旧 turn。result、stop、send、输出事务、
ProviderRequest、临时媒体和 ContextVar 均保持 branch-local。

### Phase 5B-3：PluginExecutionRuntime

状态：实现完成，Runtime owner 与生命周期边界验证通过。

1. 建立 Job registry、Gate/Job 双状态和 delivery ledger。
2. Job 从创建起由 Runtime 持有。
3. Job 持有插件 lease。
4. 建立 branch-local ContextVar 和 output sink。
5. reload/unload 建立 draining，等待活跃 lease 释放后再 unbind/purge。
6. 增加 Job 存活诊断，不增加容量拒绝或 TTL。

验收：Job owner、lease 和 shutdown 顺序明确，旧 turn close 不影响 Runtime-owned Job。

实现结果：PluginExecutionRuntime 已挂入 Core Lifecycle 和 PipelineContext；绝对窗口 watcher 可把
Gate 解析为 EXPIRED 而不取消真实 Job。Runtime 持有 module lease、reload draining、活跃 Job
诊断和原子 delivery ledger，Core shutdown 在插件与 Provider 清理前先关闭 Runtime。生产主链在
全局开关开启时创建 Runtime-owned Plugin Job；开关默认关闭时继续使用旧串行路径。插件 reload、
update、uninstall 和 disable 均先进入 draining 并等待活跃 lease 释放，再 terminate 和 unbind；
取消等待会清理 draining 状态，不会永久阻止后续 Job。

### Phase 5B-4：同一 t0 三线启动

状态：实现完成并接入生产 ProcessStage；全局开关默认关闭，等待真实日志验收。

1. 新增 InteractionTurnCoordinator，由 ProcessStage 在 Handler discovery 和 Personal admission
   完成后调用。
2. Coordinator 固定共同 t0，同时创建 Personal、Router、Plugin Job 和绝对窗口 watcher。
3. 空 Handler 路径不创建 Job。
4. T1 只创建 watcher。
5. 插件窗口使用绝对 deadline。
6. 全局功能开关保护整条新路径，不支持 per-plugin 混用。

验收：慢 Handler 下 Personal 先发送，三条 task 时间戳重叠。

实现结果：InteractionTurnCoordinator 已从同一 t0 创建 Personal、Router、Runtime-owned Plugin
Job 和绝对窗口 watcher；开关开启时所有合格普通 turn 都由 Coordinator 创建 Personal/Router，
空 Handler 不创建 branch、Job 或 watcher，Plugin Gate 在 t0 直接 PASSED。Plugin Job 使用 branch-local ContextVar，插件
ProviderRequest 通过显式 rendezvous 交回 T1 Core owner，不能直接并发修改主事件。Middleware 已
把 Personal/Router 的 task 创建与业务仲裁拆开。ProcessStage 已按整条全局开关选择新三线或旧
串行主链，不支持 per-plugin 混用。

### Phase 5B-5：Plugin Gate 与窗口内仲裁

状态：实现完成并接入生产主链；全局开关默认关闭，等待真实日志验收。

1. 使用 plugin_resolved_at，而不是 Job 完成时间。
2. 窗口内第一次 ProviderRequest 立即解析为 DELEGATED。
3. 落地 PASSED、HANDLED、STOPPED、DELEGATED、FAILED。
4. 落地 pending Personal 尽力取消和结果丢弃。
5. 插件 final 使用 T1 final reservation 和 delivery_key。
6. 记录 core_start_delay_due_to_plugin_ms。
7. 删除 Plugin-first 旧 owner。

实现结果：统一 Handler 执行器现在会在 branch Handler yield 后捕获 `MessageEventResult`，branch sink
实现 transaction finalize 边界；T1 交付协调器要求 final artifact 先取得 final-output reservation，
并使用 Runtime delivery ledger。Middleware 提供一次性 coordinated route acceptance 和不等待任务完成
的 pending Personal 压制接口。ProcessStage 已消费 Router/Gate resolution，记录真实
`plugin_resolved_at` 与 `core_start_delay_due_to_plugin_ms`，并保证普通 Core 只从统一 Core Gate
进入。

验收：普通 Core 只执行一次；immediate Personal 和插件 final 顺序正确；Router silent 不吞插件
HANDLED 输出。

### Phase 5B-6：窗口到期与后台执行

状态：实现完成并接入生产主链；全局开关默认关闭，等待真实日志验收。

1. 窗口到期将 Gate 解析为 EXPIRED。
2. watcher 结束，Plugin Job 继续。
3. detached stop、result、send 不再修改 T1。
4. detached ProviderRequest 不执行、不启动 Core、不进入 T2，记录诊断并安全收口当前
   invocation；后续已激活 Handler 继续按官方顺序执行。
5. T1 按 Router 结果继续。
6. shutdown 显式关闭残留生成器。

验收：Job 不被 T1 close 取消，迟到行为不污染旧 turn。

实现结果：绝对窗口只结束 watcher，Runtime-owned Job 保持运行；branch stop/result/send 与主事件
隔离，Gate 已 EXPIRED 或其他终态后出现的 ProviderRequest 会被拒绝，并通过统一执行器控制信号
只关闭产生该请求的 Handler invocation，后续已激活 Handler 仍继续执行。Job、completion task 和
branch ContextVar 使用干净 Context，媒体由 Job lease 持有到 T1/T2 终态。

### Phase 5B-7：迟到投递 T2

状态：生产实现完成；文本、完整组件链与同一 delivery_key 去重、双 profile、低优先级 admission、
父对话和 artifact 历史均已接线。

1. 建立 DelayedPluginDeliveryCoordinator。
2. 建立 delayed expression/direct 双 profile。
3. 建立 normal/delayed admission owner。
4. admission 后创建 T2 deadline。
5. 复用 RuntimeObservationEvent、Personal Runtime、reservation 和 assistant-only 历史。
6. direct 同样携带 metadata 并写历史。
7. 按 handler invocation / delivery group 合并产物。
8. 固定 parent_conversation_id，reset 后不污染新 conversation。
9. 为 direct/media 建立 T1/T2 共用的 assistant artifact history serializer。
10. 明确允许 Personal/Core/T2 三段输出，并用确定性指纹抑制完全等价的迟到产物。
11. 不支持 proactive message 的目标直接丢弃并记录稳定原因码。

验收：T2 不抢占用户消息；semantic 经 Personal；direct/media 原样；delivery_key 不重复。

实现结果：DelayedPluginDeliveryCoordinator 在 T1 settled 后处理 EXPIRED 产物、HANDLED 首批快照
之后形成的 final，以及 STOPPED 后未赶上 T1 收口的 final；normal turn 优先于 delayed turn，
执行 deadline 从 admission 后开始。非空纯文本 semantic
进入无工具、无 Router/Planner/Core/subagent 的 Personal Expression；direct/media 以及包含媒体或
其他非纯文本组件的 semantic 原样发送。T2 合并 MessageChain 时继承首个 artifact 的
`type/use_markdown_/use_t2i_` 元数据，保证渲染与完整链指纹稳定；两者
都携带 delayed metadata，并按固定 parent_conversation_id 写 assistant-only 历史。目标不支持
proactive message 时直接记录稳定原因码并丢弃。

T1 实际发送成功后记录完整 MessageChain 指纹；T2 的纯文本继续使用标准化文本指纹，含媒体或
其他组件的输出则比较完整组件链指纹。远程媒体按精确 URL，本地文件按规范路径、size 与 mtime，
base64/data 按长度与 SHA-256 归一化；空媒体字段保持为空，不受当前工作目录影响。该策略只抑制
可证明完全相同的输出，不进行 LLM 语义去重，也不扫描大文件正文。

### Phase 5B-8：诊断、WebUI 与启用

状态：关键时间聚合诊断、Handler/T2 细节、后台 Job 指标、WebUI 配置说明和纯媒体跨路径确定性
指纹已完成；全局开关仍为 false，等待旧过渡路径复核与真实私聊、目标群验收。

1. 完成聚合诊断和后台 Job 指标。
2. 完成 WebUI 配置说明。
3. 已补齐纯媒体与 T1 已发送媒体之间的跨路径确定性指纹，不再只比较文本。
4. 已清理开关开启分支下的旧并行过渡路径：空 Handler 与有 Handler 的合格 turn 均由唯一
   Coordinator 创建 Personal/Router 和 Core Gate；旧 Middleware 并行路径仅在开关关闭时作为
   安全对照保留。开关开启但依赖不完整时显式失败，不静默回退到旧 owner；Interaction 总开关
   关闭时不进入三线路径。
5. 真实私聊和目标群验证后启用。

验收：日志可以解释首回复、Plugin Gate、Core Gate、后台 Job 和 T2 的完整时间线。

### 启用前最小真实日志验收

本节是启用前的人工验收清单，不是新增自动化测试，也不要求为验收新建插件。测试时临时将
`parallel_plugin_runtime_enabled` 设为 `true`，保持 `plugin_parallel_window_seconds=3.0`，重启后只做
下列少量真实消息；出现任一停止线问题立即改回 `false`，保留日志再修边界。

日志只认真实平台记录：`platform_id=alice`，私聊 `session_id=815049548`，或目标群
`session_id=1083316872`。`platform_id=test/demo` 的条目来自单测，不能作为启用证据。

1. 私聊基线：从 `815049548` 发送一条不会触发 Pipeline Handler 的普通短消息。确认
   `DIAG interaction.parallel_turn` 中 Personal 与 Router 具有相同 `t0`，空 Handler 路径的
   `plugin_gate=passed` 且没有 `plugin_job_id`；实际回复可正常送达。
2. 慢插件脱离：在私聊或目标群只触发一次现有的、确定会运行超过 3 秒的独立系统 Handler。
   确认 Personal、Router、Plugin Job 的 `*_started_at` 都与同一 `t0` 对齐，`personal_emitted_at`
   不等待 `plugin_completed_at`；窗口到期时 Gate 为 `expired`，T1 仍按 Router 正常结束，随后
   `plugin_completed` 快照显示 Job 继续完成。若 Router 选择 hybrid，`core_gate_at` 必须等于
   Router 与 `plugin_resolved_at` 的较晚者，而不能等到 `plugin_completed_at`。
3. 插件接管与迟到投递：各触发一次会 `stop_event`/直接输出的既有 Handler，以及会在窗口外形成
   semantic 或 direct/media 最终产物的既有 Handler。前者确认 `handled/stopped` 只尽力压制 pending
   Personal、不会撤回已发送回复，且 T1 只投递一次；后者确认 `DIAG plugin.delayed_delivery` 的
   profile 与产物类型一致、没有重复 `delivery_key`，direct/media 未经 Persona 改写，semantic 未调用
   Router、Planner、Core、Handler、工具或 subagent。
4. T2 优先级与历史：在迟到产物准备投递时发送一条新的普通消息。确认普通消息先被 admission，
   T2 随后投递；如在迟到投递前执行 `/reset`，确认日志记录
   `parent_conversation_unavailable` 或 `parent_conversation_changed`，且不会创建新的父会话历史。

每个 case 只保留一段按 `turn_id` 聚合的
`DIAG interaction.parallel_plugin_runtime_selected`、`DIAG interaction.parallel_turn`、
`DIAG plugin.handler_invocation`、`DIAG plugin.delayed_delivery` 和 `DIAG plugin.runtime`。前者确认本轮
实际选择了新路径，后四类用于还原时序与投递。不要把全量 DEBUG 日志或测试执行产生的 `test/demo`
诊断混入结论。

## 十八、最小验证

只保留公开行为和边界测试，不测试私有调用次数或临时实现细节：

1. 慢 Handler 不阻塞 Personal 首回复。
2. Core 等待从 t0 计算的剩余窗口。
3. 窗口内 stop 可以压 pending Personal 和普通 Core，但不撤回 emitted Personal。
4. 窗口内 ProviderRequest 只执行一次，支持多次 yield 和 post-yield 恢复。
5. detached Job 不被 T1 close 取消，迟到状态不污染 T1。
6. EXPIRED 后的 ProviderRequest 不执行 Core、不进入 T2，并被安全收口。
7. T2 排在 queued normal turn 后，排队不消耗 T2 execution deadline。
8. semantic 经 Personal，direct/media 原样发送。
9. direct 和 expression 都带 delayed metadata 并写 assistant-only 历史。
10. reset 后 T2 不写入新的 conversation。
11. 同一 delivery_key 只能投递一次。
12. 一个 Job 内不同 Handler invocation 的产物不会错误合并。
13. 插件原地修改 branch message chain 或嵌套普通 extras，不会污染 Personal/Router 的输入与
    主事件状态。
14. hybrid T1 可以产生 Personal、Core 和非重复 T2；与已发送 T1 内容完全等价的 T2 被抑制。
15. 不支持 proactive message 的目标不创建 T2、不重试，也不回灌普通平台事件。
16. 全局开关关闭时整个 turn 使用旧路径，开启时全部 activated Handlers 使用新路径。
17. reload/unload 在活跃 Job 期间保持 draining，直到 lease 释放才完成清理；draining 窗口内的新
    消息跳过 Official Plugin Job 并继续 Personal/Router/Core，不得整轮失败。
18. 慢 Prompt Extension / Contributor 不阻塞 Router / Core Planner，且 Persona 与 Core 并发读取时
    同一插件上下文 pack 只收集一次。

真实运行由私聊 815049548 和目标群日志验证 Provider wait 重叠、Filter discovery、窗口耗时、
Core Gate、后台 Job 存活数量和 T2 行为。

## 十九、非目标

1. 不并发执行每个 Handler。
2. 不新增插件 claim 或路由模型。
3. 不改公开插件 API。
4. 不重新设计 Prompt Extension。
5. 不修改 plugin_runtime_targets 或 plugin_tool_targets 体系。
6. 不处理完整流式插件输出。
7. 不在 Phase 5B 处理群聊准入策略质量。
8. 不在第一实施批次增加后台 Job TTL、容量拒绝或持久化恢复。
9. 不向 Router / Planner 开放普通插件能力。

## 二十、停止线

出现以下任一情况应停止启用新主链并先修边界：

1. 无法隔离旧插件对共享 event 的并发写入。
2. 同一 Handler、窗口内 ProviderRequest、工具副作用或产物执行两次。
3. detached Job 被 T1 close 取消。
4. detached Job 可以修改 T1 状态或输出 reservation。
5. 插件 reload/unload 可以销毁仍运行的 Handler。
6. T2 抢占 normal platform turn。
7. direct/media 被错误强制人格改写。
8. 需要修改旧插件公开 API 才能继续。
9. 为通过测试而禁用 Handler、插件输出或 Personal 即时表达。
10. EXPIRED 后的 ProviderRequest 仍能启动 Core 或进入 T2。
11. T2 将旧对话结果写入 reset 后的新 conversation。
12. branch 共享可变 message chain，导致插件修改污染 Personal 或 Router 输入。
13. 同一 turn 的部分 Handler 走旧路径、部分 Handler 走新 Plugin Job 路径。
14. reload/unload 在活跃 lease 释放前移除 Handler、Tool 或模块对象。

## 二十一、完成标准

1. 三线真实 trace 共享同一 t0 启动窗口。
2. Handler body 不再位于 Personal 首回复关键路径。
3. Handler Filter discovery 耗时可单独观测。
4. Plugin Gate 与 Plugin Job 使用独立状态。
5. Core 等待 plugin_resolved_at，不等待 plugin_completed_at。
6. 窗口从 t0 计算，不在 Router 后重复计时。
7. 窗口内官方 stop、result、send 和 ProviderRequest 语义兼容。
8. 超时 Job 继续执行但彻底脱离 T1。
9. Plugin Job 持有插件 lease 并使用 branch-local output sink。
10. EXPIRED 后的 ProviderRequest 不执行、不启动 Core、不进入 T2。
11. 同一窗口内 ProviderRequest、工具副作用和 delivery_key 不重复执行。
12. semantic 结果经 Personal；direct、权限、协议和媒体结果保持兼容。
13. T2 使用低优先级 admission，排队不消耗执行 deadline。
14. expression/direct T2 都带标签并写 assistant-only 历史。
15. T2 固定使用 parent_conversation_id，reset 后不污染新 conversation。
16. direct/media 通过 artifact history serializer 持久化，不伪造文本。
17. 一个 Job 内不同插件或 Handler invocation 的产物不被错误合并。
18. 普通插件 Prompt、LLM Hook 和 FunctionTool 不进入 Router / Planner；Pipeline Handler 只进入
    Official Plugin Job。
19. InteractionTurnCoordinator 是三线和 Core Gate 的唯一创建者，ProcessStage/Middleware 不再
    各自保留并行仲裁。
20. branch 创建时快照 Prompt 可见消息输入和嵌套普通 extras，不共享可变 message chain，也不
    deepcopy 活对象。
21. Core 延迟指标可以量化 Plugin Gate 在 Router 完成后的额外等待。
22. Personal/Core/T2 三段输出被明确支持，完全等价的迟到产物通过确定性指纹抑制。
23. 不支持 proactive message 的目标丢弃迟到产物并记录稳定原因码。
24. 新路径使用全局开关，插件 reload/unload 等待活跃 Job lease 释放后才完成清理。
