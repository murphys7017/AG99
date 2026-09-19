# Output Runtime Owner Inventory

更新时间：2026-09-19

本清单是 Output Runtime 收口前的事实盘点。目标是区分“用户可见输出的规范事实”、
“插件/平台投递过程状态”和“诊断兼容投影”，避免把所有状态都继续写进
`AstrMessageEvent.extra` 或让多个 coordinator 共同完成同一件事。

## 1. 当前组件职责

| 组件 | 当前实际职责 | 收口方向 |
|---|---|---|
| `InteractionOutputController` | 生成可见输出意图、物化消息、处理 Persona/Core/Plugin 输出、观察工具阶段 | 作为逻辑输出与 turn completion 的主要 owner |
| `InteractionOutputAdapter` | 拦截事件发送方法并转给 Controller | 作为平台兼容边界，逐步只保留适配与委托 |
| `PluginArtifactDeliveryCoordinator` | 投递插件 artifact、T1/T2 边界和重复投递保护 | 只拥有 artifact 投递状态，不决定普通 turn completion |
| `DelayedPluginDeliveryCoordinator` | 保存并恢复延迟插件输出 | 只拥有延迟队列/迟到投递，不复活已完成 turn |
| `TurnDeliveryCoordinator` | 组织整轮可见消息的完成回执 | 只拥有 turn-level completion，不确认单个 artifact 组件 |
| `RespondStage` | 兼容旧 Pipeline 结果发送和 streaming finished 标记 | 逐步成为旧路径适配，不再新增输出状态 |

## 2. `event.extra` 输出字段分类

### 2.1 已有 typed owner 或明确专属 owner

- `_interaction_plugin_output_transaction_*`：插件 Handler 输出事务，主状态已迁入
  `InteractionTurnState`，extra 仅兼容投影。
- `_interaction_output_controller`：Controller 引用，属于 Event/Adapter 绑定，不是
  turn 事实。
- `_interaction_original_*`：平台原始发送方法，属于 Event Adapter 兼容边界。
- artifact delivery reservation、delivery key、duplicate fingerprint：属于
  `PluginArtifactDeliveryCoordinator` / Ledger，不应复制进 turn state。

### 2.2 仍由 Controller 写入的输出过程或诊断字段

- `_interaction_plugin_output_mode`
- `_interaction_plugin_output_last_mode`
- `_interaction_plugin_output_last_kind`
- `_interaction_plugin_output_effect_calls`
- `_interaction_plugin_streaming_failed`
- `_interaction_plugin_streaming_failure_reason`
- `_interaction_core_streaming_failed`
- `_interaction_core_streaming_failure_reason`
- `_interaction_tool_stage_observation_tasks`
- `_interaction_tool_stage_observation_state`
- `_interaction_stream_decider_failures`
- `_interaction_result_contributor_failures`
- `_interaction_outbound_materialization_failed`
- `_interaction_outbound_materialization_stage`
- `_interaction_outbound_materialization_failure_reason`

这些字段混合了结果事实和诊断信息。当前不能直接删除，需先确认是否有插件、Dashboard、
测试或平台适配器读取；但它们不能继续作为第二个输出状态机。

其中 Tool Stage observation 的 `tasks/state` 已在 2026-09-19 迁入
`InteractionTurnState`，旧 key 仅作为兼容投影；下一步仍需确认是否应拆成独立
`ToolObservationRuntime`，目前不影响逻辑输出完成语义。

插件输出的 `last_mode`、`last_kind` 和 `effect_calls` 也已在 2026-09-19 迁入
`InteractionTurnState`，Output Controller 与 Persona Runtime 通过 typed accessor 写入，
旧 key 仅作为兼容投影。它们仍属于当前 turn 的输出事实，不是平台适配状态。

### 2.3 TurnState 中已有但仍需核对读取面的状态

- `visible_outputs`
- `assistant_artifacts`
- `utterances`
- `visible_message_fingerprints`
- `final_output_status`
- `completion_state`
- `finalized_turn_material`

这些字段是逻辑输出事实，但物理投递成功、部分失败、重复和迟到行为仍跨多个组件，
尚未形成一份公开的 `DeliveryReceipt`/`OutputSegmentReceipt` 契约。

2026-09-19 起，Controller 会在每次物理投递后把
`message_id/message_kind/attempted_count/failed_count/status` 写入
`InteractionTurnState.output_delivery_receipts`，并同步写入 trace。2026-09-19 起，
插件 Artifact 与 Delayed Delivery 通过内部
`_interaction_output_delivery_identity` 传入可序列化的 identity：

- inline artifact：`scope=plugin_artifact`、`delivery_mode=inline`、单个
  `delivery_key` 和可选 `delivery_group_id`；
- delayed artifact group：`scope=plugin_artifact_group`、`delivery_mode=delayed`、
  `delivery_group_id` 和该组的 `delivery_keys`。

Controller 在平台发送前剥离这个内部字段，只将它写入 turn receipt/trace，因此
平台适配器不会依赖插件 Runtime ledger 的对象类型。Ledger 仍负责 reservation、
duplicate suppression 和 disposition；TurnState receipt 只记录可见投递事实。重复抑制、
目标不支持和投递失败的 artifact 不会伪造 `delivered` receipt。

## 3. 必须保持的完成语义

1. 一条逻辑消息的所有物理组件成功后，才能发出该逻辑消息完成回调。
2. 单个组件失败不能被“至少有一个组件成功”解释成逻辑消息完成。
3. `complete_visible_turn()` 只在本轮没有未结算的逻辑消息、artifact 或延迟投递约束
   时执行。
4. T1/T2 artifact 投递不能重新打开已完成的普通 turn；迟到输出必须拥有独立 delivery
   identity 和明确的历史策略。
5. Core、Personal、Plugin 都只能提交输出意图；平台发送由 Output Adapter/平台 Sink
   完成，不能各自直接写历史或完成状态。

## 4. 下一步最小迁移

1. 画出 `OutputIntent -> MaterializedMessage -> PhysicalDelivery -> DeliveryReceipt`
   的字段和 identity 关系。
2. 把 Controller 目前写入的 `last_mode/last_kind` 与失败字段分为规范结果和 diagnostics。
3. 确认 Tool Stage observation 的 task/state 是否属于 turn state，或应由独立
   `ToolObservationRuntime` 持有。
4. 让 Plugin Artifact、Delayed Delivery 和 Turn Delivery 只通过 receipt/identity
   与 Controller 协作，不直接修改彼此的内部状态。（Artifact 与 Delayed Delivery
   的第一步 identity 接入已完成。）
5. 在真实私聊、群聊、取消、reload、迟到输出和重复投递 trace 通过前，不删除旧
   `send*` 兼容拦截。

## 5. 当前结论

Output Controller 可以作为长期逻辑输出 owner，但还不能直接改名为完整
`OutputRuntime`：物理组件完成、artifact、delayed delivery 和 turn completion 的
身份关系尚未完全收口。下一批应先建立 receipt/identity 表，不做大规模类合并。
