# Yakumo 架构文档

`docs/Yakumo` 是 AG99 当前运行时的 canonical 文档入口。项目对外名称是 **AG99**，Yakumo 是作者名（YakumoAki）；代码包、CLI、插件前缀和兼容基础设施仍保留 `astrbot` 命名。名称、定位和兼容边界见 [项目身份](./project-identity.md)。

官方部署、平台和插件基础用法仍以 `docs/zh`、`docs/en` 中的兼容基础文档为准，但涉及 Yakumo 交互运行时行为时，以本目录和源码为准。

文档与源码冲突时，以源码为准。已经完成的实施步骤、过渡兼容方案和调查记录不在这里长期保留。

## 项目目标

AG99 将 AstrBot 从面向单次消息的 Bot Runtime 演进为持续运行的 Persona Runtime：

- `session` 负责平台来源、权限和隔离。
- `conversation` 是一段对话 episode。
- `persona` 是持续存在的交互主体。
- `memory` 通过统一 Memory Service 为 Prompt 提供事实，不再建立 Interaction 私有记忆副本。
- `Personal Runtime` 在官方 EventBus 和 Pipeline 完成过滤、权限与 Handler 准入后、核心执行器之前管理 turn、并发和 follow-up。
- `Persona Expression` 是所有用户可见文本进入 Output 前的唯一拟人层。
- `Core Planner` 只准备执行意图；Native、Claude Code、OpenCode 等执行后台位于统一执行边界之后。
- `effect_calls` 是插件扩展协议，AstrBot 不理解 Motion、Live2D 等插件领域语义。

普通、明确面向 Bot 的消息与未被 Handler 接管的有界群聊候选都并发启动 Router 与 Persona Expression。Router 返回 `persona`、`hybrid`，并只对群聊候选开放 `silent`；`silent` 会取消仍处于 pending 的 Persona，但不会撤回已经提交或送达的表达。`hybrid` 再经 Core Planner 判断是否执行，已启动的 Persona 可以先产生即时表达，Core 的最终结果仍经同一个 Persona Expression 输出。群聊连续对话 owner 由 `PersonalRuntimeManager` 按群级 audience 统一仲裁，不随 Persona 切换产生多个 owner；只有通过唤醒命令、`@Bot` 或回复 Bot 明确触发对话的用户可以取得 owner。Bot 成功回复后的前 `personal_runtime_direct_continuation_seconds` 秒仅该用户可直接续接，此后到总窗口结束仍只接受该用户，但开放 Router `silent`。其他群成员不会继承窗口，没有显式触发 Bot 的 Handler-only turn 也不会建立或清空 owner。

## 当前稳定边界

- Prompt 统一按 `Collector -> ContextPack -> target projection -> render profile -> Provider Renderer` 工作；Interaction 先形成 canonical base facts，再后台预取 Persona/Core 共用的 plugin enrichment。Persona 只消费已就绪结果，Router 与 Planner 不等待普通插件扩展，Core 等待并复用同一 task。
- Core 执行前形成 `CoreExecutionSpec`，把任务、上下文、执行历史和能力快照与 Native `ProviderRequest` 分开；第三方 Backend 尚未接入这一边界。
- Personal Runtime 在 Plugin Handler body 执行前取得 session lease，并通过 `TurnExecutionScope` 持有 Router、Persona、Context Material 和流式观察任务；即时表达、Core 最终结果和插件最终输出共享 turn 级仲裁。默认兼容路径仍先保留 Handler 接管机会；默认关闭的并行插件路径会在 discovery 后从同一 `t0` 启动 Personal、Router 和 Plugin Job。
  reservation 同时启动一个 `TurnDeadlineBudget`；binding、queue、Router、Planner、Persona、
  Core、Provider fallback 与工具循环共享默认 120 秒的单调递减总预算。
- `PersonalSessionRuntime` 现在按 RuntimeKey 在进程内跨 turn 保留控制状态；空闲实例受 24 小时 TTL 和 1024 条 LRU 上限约束。窄化的 Personal State Repository 只持久化最近表达、冷却、静音和每日用量，重启后按同一 RuntimeKey 恢复；Inbox、active turn、attention 和模型临时状态仍只存在于进程内。每个 Runtime 还持有最多 64 条 Observation 的有界 Inbox、唯一固定聚合窗口 task、确定性 Gate 和最后一次 Personal Policy 结果。Turn 结束时根据真实物理投递回执形成 Completion Feedback；所有已送达可见回复都会推进最近表达时间并启动自主表达冷却，只有携带 `ActionIntent.action_id` 的已送达输出才消耗每日主动输出配额，失败发送两者都不更新。
- 通用 Runtime Observation 通过 `submit_observation()` 合并为只读 `ObservationBatch`，再由 Gate 生成 `evaluate / hold / reject` 及稳定原因码。`evaluate` 仅在显式启用时调用独立 Personal Policy Provider，并以严格 tool-call 契约形成 decision；Provider、超时或解析失败统一记录为 fail-closed `observe`。`express` 先形成内部 `ActionIntent`，再通过独立的 `RuntimeObservationEvent` 兼容路径复用 Persona Expression、Output Controller 与 assistant-only 历史；Policy 对无新事实且近期已表达的同一意图不得再次 `express`，自主 Persona 生成还会在 effect、TTS 和投递前与上一条真实送达表达做规范化指纹比较，重复时以 `suppressed` 结束且不写 Conversation、冷却或主动配额。`defer`、冷却和 quiet-hours 的 held batch 由生命周期托管的 Wake Scheduler 到期后重新评估；Heartbeat 在没有更早 wake deadline 时也会请求 retained batch 重评。它不创造材料，也不唤醒空 Inbox；Conversation 或 Memory 历史只提供语义上下文，从不单独授予 Policy 唤醒权限。普通 Intake 不直接进入 Persona、Core 或 Output；Policy 不调用 Core 或工具。
- Persona target 不接收 `extension.capability`；执行能力契约保留在 Core lane。显式支持 Personal Runtime 的 Observation 输出会把同一逻辑 TTS segment 的 Record 与双输出文本作为一个物理消息链发送，避免一个自主表达被 Adapter 拆成多个 proactive turn。
- Interaction turn 中，插件 LLM 生命周期默认挂载到 Persona Expression，由 `interaction_middleware.plugin_runtime_targets`、插件的 `interaction_runtime_target` 声明和 Persona 默认值解析。插件拥有的可执行工具独立解析且默认进入 Core；工具 `tool_targets` 声明和用户 `plugin_tool_targets` 配置可以明确选择 Persona。Persona 通过一个共享 Agent 循环同时暴露授权业务工具和 terminal `persona_expression`，不再额外调用模型预判是否使用工具；旧式工具输出留在同一 Agent context 并成为模型可见材料，最终 Persona Expression 独占用户可见回复。Pipeline Handler 保持原有位置和终止事件语义，不被迁移为 Persona 插件。

## 当前主链

```text
Platform Adapter
  -> EventBus
  -> official Pipeline / filters / Handler discovery
  -> Personal Runtime turn admission / session lease
  -> default path: Handler takeover -> unclaimed turn enters TurnExecutionScope
  -> optional parallel-plugin path (default off): TurnExecutionScope at one t0
       -> Router --------------------------------+
       -> Persona Expression -> immediate Output
       -> Official Plugin Job -------------------+
            Router persona --------------------+-> complete
            Router hybrid -> Core Planner
                 -> not_required ---------------+-> complete
                 -> execute: CoreExecutionSpec -> Native Core Executor
                             -> Persona Expression -> final Output

  bounded unaddressed group candidate -> Router first -> silent or admitted path
```

Prompt 使用唯一数据流：

```text
Collectors
  -> base PromptContextBuilder / ContextPack
  -> background plugin-enrichment ContextPack for Persona/Core
  -> CoreExecutionSpec
  -> Native target projection
  -> PromptRenderProfile
  -> Layout / PromptTreeBuilder
  -> Provider Renderer
  -> RenderResult
  -> NativeExecutionAdapter -> ProviderRequest
```

Collector 负责收集事实，Projection 决定 Router、Planner、Personal Policy、Persona 和 Core 各自可见的内容，Renderer 只负责编译 Provider 格式。Prompt 系统不负责路由、工具执行、Memory 写入或消息发送。Router 和 Planner 只消费 base facts；Persona 只在 enrichment 已就绪时合并，Core 在需要执行时等待并复用同一 enrichment task。因此普通插件 Prompt 贡献是尽力增强，不是首回复的硬依赖。

可见 Dialogue History 与 Core Execution Ledger 是两个事实源：Conversation 保存规范用户输入、最终 Persona 表达和明确的 assistant-only 主动表达；后者会作为 `TurnRecord` 保留并供 Prompt 理解上下文，但不会更新抽象 Memory 状态或反向产生自主表达材料。Ledger 保存 Core task、工具证据、结果和错误，并且只投影给 Core。当前 Native 已接入执行准备边界，完整 Backend/Event/取消协议仍属于后续工作。

主动消息目标复用统一 `platform_id:message_type:session_id`。未携带 session 的通用
`Context.send_message(None, ...)` 和无目标主动 Cron 使用基础设置中的默认目标；已经明确
指定 session 的插件或任务不受覆盖。发送前仍按当前已加载 Adapter 的主动消息能力进行校验。
上一条回复防重只作用于携带 `PersonalActionIntent` 的自主表达；这些显式 Context、Cron 和插件
主动发送仍保留精确发送内容与 `support_proactive_message` 兼容语义。

## 文档边界

当前事实：

- `current-state.md`
- `消息处理流程详解.md`
- `modules/*`
- `dev/render-engine-implementation-spec.md`
- `dev/output-contract.md`
- `dev/interaction-output-plugin-contract.md`
- `dev/execution-backend-flow.mmd`
- `dev/runtime-dependency-structure.mmd`

长期目标和下一步：

- `target-state.md`
- `dev/persona-system-final-goal.md`
- `dev/autonomous-persona-runtime-initial-plan.md`
- `dev/runtime-function-unification-plan.md`
- `dev/parallel-plugin-runtime-plan.md`
- `dev/execution-backend-preparation-plan.md`
- `prompt-development-plan.md`
- `dev/cost-context-runtime-plan.md`

Memory 子系统：

- `dev/memory/index.md`
- `dev/memory/progress.md`
- `dev/memory/architecture.md`

## 阅读顺序

1. `current-state.md`
2. `消息处理流程详解.md`
3. `modules/README.md`
4. `modules/interaction.md`
5. `modules/prompt.md`
6. `dev/execution-backend-flow.mmd`
7. `dev/runtime-dependency-structure.mmd`
8. `target-state.md`
9. `dev/autonomous-persona-runtime-initial-plan.md`
10. `dev/runtime-function-unification-plan.md`
11. `dev/execution-backend-preparation-plan.md`

## 维护规则

- 现状文档只描述已经存在的代码。
- 目标文档明确标记尚未实现的部分。
- 已完成的迁移步骤直接从计划中删除或改写为当前边界。
- 不为已经删除的兼容 API、影子状态或旧 Prompt 管线保留说明。
