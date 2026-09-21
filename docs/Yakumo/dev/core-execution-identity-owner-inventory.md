# Core Execution 身份与 Owner 盘点

更新时间：2026-09-21

本文是 execution-backend-preparation-plan.md 的 D1 盘点产物。它只记录当前源码事实、
唯一主写者和兼容投影，不引入新的 Executor Body 契约，也不改变运行时行为。

## 1. 关系总览

```text
InteractionTurn
  └─ InteractionTurnState.turn_id
      └─ CoreExecutionSpec.turn_id + execution_id
          └─ CoreExecutionSession / CoreExecutionHead
              └─ CoreExecutionEvent / CoreEvent.sequence
                  └─ CoreExecutionArtifact / 可见输出与投递 identity 投影
```

这里的关系不是“一层复用另一层的状态”：

- `turn_id` 关联一次对话轮次；它不能代替 `execution_id`。
- `execution_id` 标识一次 Core 执行；它不能代替 `turn_id` 或平台消息 ID。
- `CoreEvent` 是 Core 的非可见生命周期事实；它不等于用户可见输出。
- `message_id`/`output_segment_id` 是逻辑可见输出段的身份；它不等于 artifact 或执行终态。
- `visible_message_id` 是一次物理平台发送的身份；它不等于逻辑输出段。

## 2. 四层身份与状态表

| 层级 | canonical identity | 状态主写者 | 终态主写者 | 创建者 | 主要读取者 |
| --- | --- | --- | --- | --- | --- |
| `InteractionTurn` | `InteractionTurnState.turn_id` | `InteractionTurnState` 及其 helper | `InteractionTurnState.completion_state` | Interaction middleware / `ensure_interaction_turn_state()` | Personal、Output Controller、Prompt、Core bridge |
| `CoreExecution` | `CoreExecutionSpec.execution_id`，关联 `turn_id`、`core_task_id` | `CoreExecutionHead` → `CoreExecutionLifecycle` → `CoreExecutionSession` | Head/Lifecycle 的 terminal event 与 session status | `CoreExecutionSpec.from_context_pack()` | Core bridge、Native adapter、Ledger projection、Turn journal |
| `ExecutorTask` | 当前由 `CoreExecutionSpec.core_task_id` 表达，Executor 自身没有第二套通用 task identity | 当前由 Core session 接受命令并由 Executor adapter 产生事件 | Core Head 接收 `completed/failed/cancelled` 并裁决 | Planner/agent stage 生成 `CoreTaskSpec`，Core spec 生成 execution | Native loop、Core Head、历史 ledger |
| `OutputArtifact` | 逻辑输出使用 `output_segment_id/message_id`；插件 artifact 使用 `PluginDeliveryKey` | `InteractionOutputController`（普通可见输出）或 `PluginArtifactDeliveryCoordinator`（插件 artifact 预约/投递） | 当前由 `output_delivery_receipts` 记录普通投递事实，插件由 artifact disposition 记录 | Controller 物化输出；Plugin Branch 创建插件 artifact | 平台 adapter、历史写入、延迟投递、诊断 trace |

## 3. 各层事实与边界

### 3.1 InteractionTurn

`InteractionTurnState` 是一轮 Interaction 的 typed owner。当前字段包括：

- `turn_id`、路由和 Core 委派状态；
- `core_task_spec`、`core_execution_spec` 关联信息；
- `visible_outputs`、`utterances`、`output_delivery_receipts`；
- `completion_state`、失败列表、延迟历史和最终 material；
- `core_execution_events`，作为 Core 事件的本轮有界投影。

`event.extra` 中的 `_turn_id`、`_core_execution_spec`、输出诊断字段是兼容投影或适配器
绑定，不是第二个规范状态机。`set_interaction_turn_core_execution_spec()` 只允许 typed
state 成为主存储，并把 spec 投影回 extra 供旧插件和诊断读取；读取时允许从 legacy extra
回填 typed state，这是当前仍保留的迁移入口。

**取消、失败、完成归属：** 本轮是否完成、是否失败、是否取消由
`InteractionTurnState.completion_state` 负责。Core terminal event 只能作为执行事实，不能
直接把 Interaction turn 标成完成。

### 3.2 CoreExecution

`CoreExecutionSpec` 在构造时生成唯一 `execution_id`，并固定 `turn_id`、`core_task_id`、
`ContextPack`、执行历史和 capability snapshot。它是从 Prompt/Context 到执行器的单向输入
快照，不应在执行过程中重新从 `event.extra` 拼装。

`CoreExecutionHead` 是当前 Core 命令和事件的入口，`CoreExecutionLifecycle` 负责命令、
executor callback、deadline view 和终态准备，`CoreExecutionSession` 负责状态机、命令去重、
事件顺序和 terminal 冲突拒绝。三者共同构成一个 CoreExecution 的生命周期 owner，不能再
由 Native runner 或 Interaction stage 另行维护终态。

允许的 Core 状态序列是：

```text
created -> submitted -> working -> completed
                              ├-> failed
                              └-> cancelled
```

`progress`、`artifact_ready` 是 working 阶段事实；terminal event 之后拒绝迟到事件。Head
发布 `CoreEvent(sequence, execution)`，Personal/Interaction 通过订阅或本轮 journal 投影
读取，不直接修改 Head 的事件列表。

### 3.3 ExecutorTask

当前没有独立的通用 `ExecutorTask` 类型。实际 task identity 由
`CoreTaskSpec`/`CoreExecutionSpec.core_task_id` 表达，执行器通过 `executor_id` 标明当前
实现（Native 为 `native`）。因此当前不能把 Native runner 的 step、tool loop、stream 或
`AgentResponse` 当成通用 task 状态。

`NativeExecutorAdapter` 仍暴露少量 Native-specific surface（runner context、request、hooks、
streaming 等），`NativeExecutionLoop` 负责 Native step 驱动，`NativeExecutionOutputBridge`
负责把 Native stream item 投影到现有 Output 边界。这是 D3/D5 的待收口区域，不属于 D1 的代码
修改范围。

### 3.4 OutputArtifact

当前存在两类不可混用的产物：

1. `CoreExecutionArtifact`：Core event 中的 executor-neutral artifact 描述，只有
   `artifact_id`、`artifact_kind` 和受限 metadata；它不携带平台消息链。
2. `PluginOutputArtifact`：插件分支产生的可投递 `MessageChain`，用
   `PluginDeliveryKey(plugin_job_id, handler_invocation_id, artifact_sequence)` 去重，
   由 `PluginArtifactDeliveryCoordinator` 和 `DelayedPluginDeliveryCoordinator` 管理。

普通用户可见输出由 `InteractionOutputController` 分配逻辑段 ID：

```text
output_segment_id / message_id
  └─ 可包含多个物理 MessageChain
       └─ visible_message_id（每次物理发送一个）
```

逻辑段的全部物理组件成功后，Controller 才能记录消息完成并调用
`complete_visible_message(message_id)`；一轮最终完成由 `complete_visible_turn()` 表示。
插件 artifact 的 produced/reserved/delivered/suppressed/failed disposition 不得直接伪造
普通 turn 或 Core 的 terminal 状态。

## 4. 创建、写入、读取与投影关系

| 事实 | 规范写入者 | 当前兼容写入/读取 | 目标约束 |
| --- | --- | --- | --- |
| `turn_id` | `InteractionTurnState` helper | `event.extra["_turn_id"]` | extra 只能单向投影，不得创建第二个 turn state |
| `CoreExecutionSpec` | Core preparation / `set_interaction_turn_core_execution_spec()` | `event.extra["_core_execution_spec"]` | spec 与 turn identity 必须一致；不能跨 turn 复用 |
| Core command | `CoreExecutionHead/Lifecycle/Session` | Native adapter 的 follow-up/cancel 入口 | 所有 command 必须带 `execution_id`、`turn_id`、`command_id` |
| Core event | Head publish → Session event list | Turn journal `core_execution_events`、trace | journal 是有界投影；不得反向写 Head |
| execution terminal | Session/Head | Ledger preparation、trace、Turn journal | terminal event 只写一次；迟到/冲突 terminal 丢弃或拒绝 |
| logical output | `InteractionOutputController` | output extras、TurnState receipts | `message_id` 不作为 turn 或 execution identity |
| plugin artifact delivery | Artifact coordinator + ledger | delayed metadata / delivery identity extra | reservation/disposition 与普通 visible completion 分离 |
| history write | 现有 conversation/history owner | Turn metadata 与 delayed delivery context | Core/Executor/Plugin 不直接写历史 |

## 5. 取消、失败和迟到事件归属

### 取消

- 用户取消、deadline 取消首先由 Personal/Interaction 形成取消事实；Core Head 接收
  `cancel`，向 Executor Body 请求停止，并发布唯一 `cancelled` terminal event。
- Executor 的停止异常记录为 stop callback diagnostics，不能替换 Core terminal event。
- Output Controller 仍独立决定是否需要发送取消提示或最终表达。

### 失败

- Executor 失败由 Core Head 发布 `failed`；Native runner 的异常、Provider 错误和工具失败
  必须归一化为事件 metadata 或 terminal error，不直接修改 Interaction completion state。
- Interaction/Pipeline 阶段失败由 `InteractionTurnState.failures` 记录；不能伪造 Core failed
  event。
- Ledger persistence 失败是持久化诊断，不能把已经确认的 Core terminal 改写成另一状态。

### 迟到事件

- Core terminal 之后的 progress/artifact/terminal 事件不得重新打开 Core session。
- 已完成的普通 turn 不因迟到插件 artifact 重新打开；延迟 artifact 必须创建独立
  `delayed_turn_id` 和 delivery identity。
- 迟到可见输出由 Output/Delayed Delivery 的策略决定，不能由 Executor Body 直接发送平台消息。

## 6. 当前重复或隐式关联风险

以下风险已确认存在，但本批只记录，不修改运行逻辑：

1. `InteractionTurnState.core_execution_events` 是 Head 事件的有界投影，若后续代码直接把
   它当成 Head 的事实源，会形成第二个 Core event store。
2. `event.extra` 仍同时承载 `_core_execution_spec`、Head/session/lifecycle 引用和诊断值；
   这些字段必须继续按“typed owner → extra projection”方向迁移，禁止新代码直接把 extra 当
   canonical input。
3. `NativeExecutorAdapter` 暴露 runner-specific context/request/hooks，当前仍存在 Core
   Head 与 Native runner 细节耦合；需在 D3/D5 收口，D1 不提前抽象。
4. `CoreExecutionArtifact` 与 `PluginOutputArtifact` 都叫 artifact，但 identity、生命周期、
   投递 owner 不同。后续公共命名或接口必须显式区分 execution artifact 与 visible delivery
   artifact。
5. `CoreTaskSpec.core_task_id` 是稳定任务关联，而 `execution_id` 是一次执行实例；重试或
   parent execution 不能复用 execution_id。
6. `OutputController`、Plugin Artifact Coordinator、Delayed Delivery Coordinator 和
   Turn Delivery 之间已经有 identity 投影，但尚未形成统一公开的 `DeliveryReceipt` 类型；
   这属于 Output Runtime 后续收口，不应在 D1 伪造新类型。

## 7. D1 结论与下一步

### 已满足

- 每次 Core 执行都有 `execution_id`，并能通过 `turn_id` 关联到 Interaction turn。
- Core Head/Session 拥有命令去重、事件顺序和 terminal 状态；Native adapter 不应再拥有第二套
  Core terminal 状态。
- 逻辑输出段、物理消息、Core artifact、插件 artifact 的身份已可以区分。
- 兼容 extra 的主要方向已明确：只读/投影，不作为新 canonical owner。

### 尚未满足

- 没有独立且 executor-neutral 的 `ExecutorTask` 类型；当前以 `core_task_id` 和 Native
  adapter 组合表达。
- `ProviderRequest`、Native runner context 和部分执行证据仍跨越 Core/Stage/Adapter 边界。
- Output artifact 的 receipt/identity 仍分散在 Controller、TurnState 和插件 coordinator。
- 尚未以第二个 Body 证明 Core Head 的替换性。

### D2 入口

D2 应只处理 Personal 与 Core Head 的通信收口：明确 submit/provide_input/cancel 的调用者、
事件订阅者、迟到事件处理和独立生命周期；不得在 D2 引入新的 Executor Body 或把 Output
artifact 合并进 Core event。

## 8. D2 通信事实盘点

当前进程内通信路径如下：

```text
Personal / Interaction
  ├─ start_core_execution_head() -> CoreExecutionHead.start()
  ├─ CoreExecutionHead.provide_input()
  └─ CoreExecutionHead.cancel()
          │
          ├─ CoreCommand(execution_id, turn_id, command_id, origin)
          ├─ CoreExecutionSession 校验身份、去重和状态
          └─ CoreEventMailbox / subscriber -> Personal journal / diagnostics

Executor Body
  └─ emit submitted / working / progress / artifact_ready / completed / failed
```

命令和事件都在同一进程内传递，不代表网络协议或分布式队列。Personal 只拥有自己的
Interaction turn 和 deadline；Core Head 拥有 Core execution session；Executor Body 只能
通过 Head 回报事实或接受 stop/input。

### D2 已收口的规则

- `submit` 只能由 Core Head 接受一次；同一 `execution_id + command_id` 重复提交不重复生效。
- `provide_input` 必须带同一 `execution_id`、`turn_id`，由 Head 转发给当前 Executor Body；
  它不会直接修改 Personal prompt 或历史。
- `cancel` 先由 Head 记录命令，再发布唯一 `cancelled` terminal event，最后请求 Body 停止。
- deadline 取消使用 `CoreCommandOrigin.CORE_HEAD`，用户主动取消默认使用
  `CoreCommandOrigin.PERSONAL`；来源只描述控制事实，不改变 terminal 状态。
- terminal event 后，Head 不再接受新的 execution event；迟到结果只能记录为拒绝/诊断，不重新打开
  session。
- `CoreExecutionEventMailbox` 只接收订阅后的事件，不负责历史重放，也不承担 Executor 调度。

### D2 当前未完成项

- Personal 对同一 Core session 的“后续追问”仍通过 Native adapter 的 follow-up 入口接入，尚未
  由独立的通用输入事件处理器承接。
- 事件订阅目前主要用于 Turn journal 和诊断，尚未形成统一的 Personal output policy 消费层。
- `event.extra` 仍是 Head/session/lifecycle 的兼容绑定位置；后续只能增加 typed accessor，不能
  新增第二个通信状态源。

## 9. D3 最小 Body 接入点

`CoreExecutorBody` 现在只定义 Core Head 必需的最小控制面：`executor_id`、`request_stop()`
和 `request_follow_up(message_text)`。`CoreExecutionHead.activate_executor_body()` 将该协议
转换为现有 lifecycle 的 callback 绑定；旧的显式 callback 方法暂时保留给兼容调用者，但新的
Core/Stage 装配点应优先使用 Body 入口。

这一步没有把 Native 的 stream、Provider、Prompt、Tool Loop 或可见输出暴露进通用协议。
`NativeExecutorAdapter` 只是当前第一个 Body 实现，`NativeExecutionLoop` 和
`NativeExecutionOutputBridge` 仍然保留 Native 专属职责。

## 10. 2026-09-21 D5 后续边界

D5-A/D5-B 已让 `CoreExecutionPort` 成为 Native Body 的显式控制端口。它解决了 Body 对
Event 反查 Head 的隐式依赖，但没有新增一个可通用于所有 Body 的运行或结果协议。

后续 R1-R8 的目标边界是：

```text
PreparedCoreExecution
  -> ExecutorRun
  -> Core coordinator
  -> executor-neutral ExecutionResult
  -> shared result bridge
  -> InteractionOutputController
```

- `PreparedCoreExecution` 复用现有 CoreExecutionSpec、ContextPack、能力和 deadline 事实；
  `ProviderRequest` 留给 Native adapter，不能成为通用输入；
- `ExecutorRun` 接受控制、报告执行事实并关闭自身资源；它不直接调用平台、写历史或裁决终态；
- coordinator 是 Body 生命周期、唯一终态和迟到结果抑制的运行 owner；
- `ExecutionResult` 只包含执行器中立材料。MessageChain、物理投递和 DeliveryReceipt 继续由
  输出边界拥有；
- 当前 Scripted Body 只验证 Head 控制协议。只有它通过同一 factory/coordinator/输出桥运行后，
  才能把“替换性”写为生产装配链事实。

本节是后续实现约束，不改变本文此前记录的当前源码事实。详细实施顺序见
[Core 内部执行器替换实施方案](internal-executor-replacement-plan.md)。
