# Yakumo 架构文档

`docs/Yakumo` 只记录这个项目当前有效的架构、稳定接口和下一步计划。官方部署、平台和插件基础用法仍以 `docs/zh`、`docs/en` 及上游 AstrBot 文档为准。

文档与源码冲突时，以源码为准。已经完成的实施步骤、过渡兼容方案和调查记录不在这里长期保留。

## 项目目标

Yakumo 将 AstrBot 从面向单次消息的 Bot Runtime 演进为持续运行的 Persona Runtime：

- `session` 负责平台来源、权限和隔离。
- `conversation` 是一段对话 episode。
- `persona` 是持续存在的交互主体。
- `memory` 通过统一 Memory Service 为 Prompt 提供事实，不再建立 Interaction 私有记忆副本。
- `Personal Runtime` 在官方 EventBus 和 Pipeline 之后、核心执行器之前管理 turn、并发和 follow-up。
- `Persona Expression` 是所有用户可见文本进入 Output 前的唯一拟人层。
- `Core Planner` 只准备执行意图；Native、Claude Code、OpenCode 等执行后台位于统一执行边界之后。
- `effect_calls` 是插件扩展协议，AstrBot 不理解 Motion、Live2D 等插件领域语义。

当前 Router 与 Persona Expression 并发启动。Router 只返回 `persona` 或 `hybrid`；`silent` 类型暂时保留在数据结构中，但当前 Prompt 不会产生该标签。

## 当前稳定边界

- Prompt 统一按 `Collector -> ContextPack -> target projection -> render profile -> Provider Renderer` 工作；Router、Planner、Personal Policy、Persona 和 Core 不再各自采集或拼接 Prompt。
- Core 执行前形成 `CoreExecutionSpec`，把任务、上下文、执行历史和能力快照与 Native `ProviderRequest` 分开；第三方 Backend 尚未接入这一边界。
- Personal Runtime 在插件 Handler 前取得 session lease，并通过 `TurnExecutionScope` 持有 Router、Persona、Context Material 和流式观察任务；即时表达、Core 最终结果和插件最终输出共享 turn 级仲裁。
- `PersonalSessionRuntime` 现在按 RuntimeKey 在进程内跨 turn 保留控制状态；空闲实例受 24 小时 TTL 和 1024 条 LRU 上限约束。窄化的 Personal State Repository 只持久化最近表达、冷却、静音和每日用量，重启后按同一 RuntimeKey 恢复；Inbox、active turn、attention 和模型临时状态仍只存在于进程内。每个 Runtime 还持有最多 64 条 Observation 的有界 Inbox、唯一固定聚合窗口 task、确定性 Gate 和最后一次 Personal Policy 结果。Turn 结束时根据真实物理投递回执形成 Completion Feedback，只有已送达可见输出会推进并持久化最近表达时间。
- 通用 Runtime Observation 通过 `submit_observation()` 合并为只读 `ObservationBatch`，再由 Gate 生成 `evaluate / hold / reject` 及稳定原因码。`evaluate` 仅在显式启用时调用独立 Personal Policy Provider，并以严格 tool-call 契约形成 decision；Provider、超时或解析失败统一记录为 fail-closed `observe`。`express` 先形成内部 `ActionIntent`，再通过独立的 `RuntimeObservationEvent` 兼容路径复用 Persona Expression、Output Controller 与 assistant-only 历史；`defer`、冷却和 quiet-hours 的 held batch 由生命周期托管的 Wake Scheduler 到期后重新评估。普通 Intake 不直接进入 Persona、Core 或 Output；Policy 不调用 Core 或工具。

## 当前主链

```text
Platform Adapter
  -> EventBus
  -> official Pipeline / plugin filters
  -> Personal Runtime turn admission / session lease
  -> official Plugin Handlers
  -> TurnExecutionScope
  -> Router || Persona Expression
  -> persona: Output
  -> hybrid: Core Planner -> CoreExecutionSpec -> Native Core Executor -> Persona Expression -> Output
```

Prompt 使用唯一数据流：

```text
Collectors
  -> PromptContextBuilder / ContextPack
  -> CoreExecutionSpec
  -> Native target projection
  -> PromptRenderProfile
  -> Layout / PromptTreeBuilder
  -> Provider Renderer
  -> RenderResult
  -> NativeExecutionAdapter -> ProviderRequest
```

Collector 负责收集事实，Projection 决定 Router、Planner、Personal Policy、Persona 和 Core 各自可见的内容，Renderer 只负责编译 Provider 格式。Prompt 系统不负责路由、工具执行、Memory 写入或消息发送。

可见 Dialogue History 与 Core Execution Ledger 是两个事实源：Conversation 保存规范用户输入、最终 Persona 表达和明确的 assistant-only 主动表达；Ledger 保存 Core task、工具证据、结果和错误，并且只投影给 Core。当前 Native 已接入执行准备边界，完整 Backend/Event/取消协议仍属于后续工作。

主动消息目标复用统一 `platform_id:message_type:session_id`。未携带 session 的通用
`Context.send_message(None, ...)` 和无目标主动 Cron 使用基础设置中的默认目标；已经明确
指定 session 的插件或任务不受覆盖。发送前仍按当前已加载 Adapter 的主动消息能力进行校验。

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
10. `dev/execution-backend-preparation-plan.md`

## 维护规则

- 现状文档只描述已经存在的代码。
- 目标文档明确标记尚未实现的部分。
- 已完成的迁移步骤直接从计划中删除或改写为当前边界。
- 不为已经删除的兼容 API、影子状态或旧 Prompt 管线保留说明。
