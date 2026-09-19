# event.extra Owner Inventory

更新时间：2026-09-20

本清单记录当前源码中 `AstrMessageEvent.extra` 的高风险内部字段，重点确认
`ProviderRequest` 与 Interaction/Core 解耦的真实边界。它是迁移前的事实基线，不代表
本文件列出的字段都应迁入 `InteractionTurnState`。

## 1. 判定规则

- **Typed owner**：内部状态的唯一主写者；`event.extra` 只能保留兼容投影。
- **Runtime owner**：跨 turn、插件任务、平台或生命周期拥有独立寿命的对象；不应塞进
  `InteractionTurnState`。
- **Ephemeral binding**：只在一次 Hook、Provider 调用或插件交接窗口内有效的绑定；
  应由显式上下文或生命周期对象携带，不能被误认为 turn 的规范请求。
- **Compatibility projection**：为官方插件 API、诊断或旧适配器保留的镜像；不得再成为
  内部主读取路径。

## 2. ProviderRequest 三种语义

### 2.1 Main Agent canonical request

`build_main_agent()` 会构造或接收 `ProviderRequest`，在完成 Provider、Prompt、Capability
和 Core 渲染后再次写回 `event.extra["provider_request"]`。Prompt Context Builder、
Output Lifecycle、Capability Resolver、Dialogue、Postprocess 和部分工具上下文会读取该
key。

目标不是把这个可变对象直接塞进 `InteractionTurnState`，而是建立显式
`ProviderRequestBuilder` / Runner 边界。`CoreExecutionSpec` 仍保存规范 ContextPack、
执行身份和能力快照；最终 Provider 格式请求由 Adapter/Runner 拥有。

### 2.2 Agent lifecycle hook exposure

`AgentRequestLifecycle.expose_request()` 在 Hook 调用期间临时把自身的
`provider_request` 暴露为 `event.extra["provider_request"]`，完成后恢复原值。该值可能
属于 Persona、Core 或兼容 Runner。

这是 Hook API 的临时绑定，不是 turn 的 canonical request，不能用全局 typed 字段替换，
也不能让并发 Persona/Core 请求共享一个值。后续应由 lifecycle overlay / 显式 Hook
Context 承载，extra 只作为兼容投影。

### 2.3 Plugin delegated request

并行插件路径中，插件 `yield ProviderRequest` 后，ProcessStage 将命令请求短暂写入
`event.extra["provider_request"]` 交给同一 Core turn，处理完再删除。该请求属于
`PluginInvocation -> CoreExecution` 交接消息，不应污染 Main Agent canonical request，
也不应成为下一次 Prompt 构建的隐式输入。

下一步应让 `PluginJob`/command 直接携带请求，Core 消费侧通过显式参数读取，最后删除
ProcessStage 对共享 event extra 的写入和清理路径。

## 3. 其他字段初步分类

| 字段类别 | 代表字段 | 当前主要写入者 | 目标 owner | 当前动作 |
|---|---|---|---|---|
| Interaction turn 状态 | `_interaction_turn_state`、`_turn_id`、完成/失败状态 | Middleware、TurnState helper | `InteractionTurnState` | 第一批已迁移，旧 extra 投影保留 |
| Core 执行事实 | `_core_execution_spec`、`_core_execution_head`、session/lifecycle | Core execution bridge | `CoreExecutionHead` + TurnState identity | Spec 第一批已迁移，Head/session 待收口 |
| 运行配置 | `_astrbot_config`、`_astrbot_config_id` | Middleware/主动任务入口 | turn config snapshot / runtime config | 已准入 turn 使用快照，旧 key 兼容 |
| Provider canonical request | `provider_request` | Main Agent、Prompt、Output、Plugin Stage | ProviderRequestBuilder + Runner | 暂不直接迁移，先拆语义 |
| Hook 临时请求 | `provider_request` 临时覆盖 | `AgentRequestLifecycle` | Lifecycle overlay | 保持临时绑定，后续去除内部对 extra 的依赖 |
| 插件交接请求 | `provider_request` 临时写入 | ProcessStage | PluginJob/ProviderRequest command | 待显式参数化 |
| 停止状态 | `agent_stop_requested`、`agent_user_aborted` | Agent runner/stop API | Turn cancellation / Core Head | 需区分用户取消、deadline 和平台停止 |
| Prompt 结果 | `prompt_context_pack`、render/apply result | Prompt/Main Agent | ContextPack/RenderResult/Adapter | 结果对象已分层，旧 extra 读取仍需盘点 |
| 输出控制引用 | output controller、原始 send 方法 | Middleware/Event adapter | Output Runtime / Event Adapter | 保留兼容边界，待输出阶段收口 |
| 诊断投影 | `*_failed`、`*_reason`、`*_metrics` | 各对应 owner | owner + diagnostics | 先查外部消费者，再决定删除或投影 |

## 4. ProviderRequest 迁移顺序

1. 为 `ProviderRequestBuilder` 明确输入：`ContextPack`、Provider、CapabilitySnapshot、
   OutputContract 和目标执行面；禁止从事件 extra 隐式补齐核心字段。
2. 让 `build_main_agent()` 返回并传递规范请求，`event.extra["provider_request"]` 只在
   官方 Hook/插件兼容边界按需投影。
3. 让 `AgentRequestLifecycle` 通过 lifecycle overlay 暴露当前 Hook 请求，不改变并发
   Persona/Core 请求的独立性。
4. 让插件 ProviderRequest 交接完全走 `PluginJob`/command 参数，移除 ProcessStage 对
   共享 event extra 的写入和删除。
5. 对 Prompt、Output、Postprocess 和工具读取点逐一改成显式参数；每完成一类读取就跑
   对应定向测试和一次真实 trace。

## 5. 本阶段非目标

- 不删除官方插件可见的 `event.get_extra("provider_request")` 兼容能力；
- 不把 Persona、Core、插件请求合成一个“当前请求”字段；
- 不在 ProviderRequest 迁移前拆 `astr_main_agent.py` 的全部职责；
- 不开始第二个 Executor Body 或远程通信协议。

## 6. 下一步验收

- 同一 Interaction 中 Persona 与 Core 并发请求不会互相覆盖；
- 插件 delegated request 消费结束后不会残留为下一轮 Prompt 输入；
- Hook 能看到它所属 lifecycle 的请求，Hook 返回后原值正确恢复；
- Core `CoreExecutionSpec -> ProviderRequest -> Runner` 的 identity 可由 trace 还原；
- 失败、取消、fallback 不会重放或误用另一执行面的 ProviderRequest。
