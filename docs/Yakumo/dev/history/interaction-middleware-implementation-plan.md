# AstrBot Interaction Middleware Implementation Plan

> Historical note: this was the first function-level implementation plan for
> inserting interaction middleware between adapter and core. It is archived
> because the current code has already moved beyond the transport/routing MVP:
> turn state, stream phase, readonly plugin views, outbound materialization,
> voice service integration, fail-fast policy, and postprocess-owned memory
> completion are now tracked in
> `docs/Yakumo/dev/interaction-middleware-architecture-review-and-plan.md`.

本文档是 `docs/Yakumo/dialog-worker-live-target-state.md` 的函数级实现拆解。

需要明确：

- **当前实现计划的前几期**主要解决“把 middleware 插进去，拿到输入输出控制点”
- **长期目标**是把 middleware 做成 interaction persona layer

因此，本实现计划分两段看：

1. **Transport / Routing MVP**
   - 输入打标
   - `send` / `send_streaming` 接管
   - WebChat 首个平台验证
2. **Interaction Agent Phase**
   - persona runtime
   - middleware memory
   - route decision
   - humanized progress
   - final response wrapping

## 当前实现边界

本文件里的函数级拆解，需要遵循新的系统边界：

- middleware 负责人格化交互，而不是重新实现 tools / search / knowledge base / subagent
- core 负责执行能力，而不是直接负责“最终人格表达”
- knowledge base 放在 core，更像执行能力
- 人格记忆放在 middleware，更像 interaction state

因此，本计划中的模块应分成两组：

### A. 先落地的基础层

- `CoreInputGateway`
- `InteractionMiddleware`
- `InteractionOutputController`
- `TaskStateStore`
- `ExpressionPolicy`
- outbound dispatch

### B. 下一阶段补上的 interaction layer

- `InteractionPersonaRuntime`
- `InteractionMemoryStore`
- `InteractionRouter`
- `InteractionProgressRenderer`
- `CoreBridge`

## 当前链路确认

### Adapter 到 Core 输入

当前行为：

```text
Platform.commit_event(event)
    -> self._event_queue.put_nowait(event)
    -> EventBus.dispatch() 消费
    -> PipelineScheduler.execute(event)
```

判断：

- 输入主链路不需要重写
- 需要在 `Platform.commit_event()` 与 `event_queue.put_nowait()` 之间增加 middleware 入口

### Core 到 Adapter 输出

原则：

- 不在 `run_agent()`、`FunctionToolExecutor`、`RespondStage` 等十几处调用点各加条件判断
- 对已启用 middleware 的平台，只接管两个 outbound API：
  - `event.send(...)`
  - `event.send_streaming(...)`
- 未启用的平台继续走 legacy path

## 新增模块设计

建议新增包：

```text
astrbot/core/interaction/
```

### 1. `astrbot/core/interaction/middleware.py`

#### `InteractionMiddleware.handle_inbound(event: AstrMessageEvent) -> None`

职责：

- 接收 adapter 标准化后的事件
- 创建或恢复 `turn_id`
- 恢复或加载 middleware session state
- 执行 session turn 冲突裁决
- 判断是否为 control input
- 调用 interaction router
- 产出结构化交互决策
- 决定是否立刻回复
- 决定是否放行到 core event queue
- 在 event 上设置 `_output_controller` 引用

长期职责扩展：

- 若 router 产出 `core_task_spec`，则向 core 发送结构化任务描述，而不是只无差别转发原始用户输入

#### `InteractionMiddleware.handle_core_output(output: CoreOutputEvent) -> None`

职责：

- 接收 core 中间结果或最终结果
- 写入 output buffer / task state
- 调用 expression policy 判断是否 public 输出
- 在 `humanized` 模式下，将 core 中间事件改写成拟人化进度表达
- 在最终结果阶段，决定是否需要再包装为人格化回复

### 2. `astrbot/core/interaction/input_controller.py`

#### `InteractionInputController.handle(event: AstrMessageEvent) -> InputDecision`

职责：

- 识别普通输入、stop、cancel、replace
- 执行 session turn 冲突裁决
- 生成 turn metadata
- 决定是否立即 ack
- 决定是否放行 core
- 与 router 协同，决定 `self_reply` / `delegate_to_core` / `hybrid`

#### `InputDecision`

建议字段：

- `turn_id`
- `forward_to_core`
- `route_mode`
- `immediate_reply`
- `emit_ack`
- `control_type`
- `cancel_previous_turn_id`
- `core_task_spec`
- `progress_render_mode`
- `final_response_mode`
- `metadata`

### 3. `astrbot/core/interaction/output_controller.py`

#### `InteractionOutputController.capture_message_chain(chain: MessageChain, event: AstrMessageEvent) -> None`

职责：

- 接收来自 `event.send()` 基类路由的所有输出
- 将 `MessageChain` 转为 `CoreOutputEvent`
- 写入 output buffer / task state
- 调用 expression policy

注意：

- 这里捕获到的是“core 或 plugin/tool 已经决定要发什么”
- 长期目标不是只 pass-through，而是给 middleware 一次“是否原样发 / 是否拟人化改写 / 是否吞掉”的决策机会

### 4. `astrbot/core/interaction/output_event.py`

建议后续 `PublicOutput` 扩展字段：

- `render_mode`  # raw | humanized | silent
- `audience`     # user | debug | internal
- `source_event_ids`

### 5. `astrbot/core/interaction/state_store.py`

#### `TurnState`

建议后续扩展字段：

- `route_mode`
- `progress_render_mode`

### 6. `astrbot/core/interaction/expression_policy.py`

#### `ExpressionPolicy.naturalize(output: CoreOutputEvent, state: TurnState) -> PublicOutput`

职责：

- 将 core 原始结果转成 public 输出
- MVP 先 pass-through，验证链路通畅
- 后续接 DialogAgent / Persona Runtime

#### `ExpressionPolicy.render_progress(output: CoreOutputEvent, state: TurnState) -> PublicOutput | None`

职责：

- 当 mode 为 `humanized` 时，把原始执行进度转成拟人化过程表达
- 当 mode 为 `raw` 时，直接返回结构化 progress
- 当 mode 为 `silent` 时，返回 `None`

### 7. `astrbot/core/interaction/outbound.py`

要求：

- outbound dispatcher 只负责协议转换
- 不承担“要不要说”“怎么拟人化说”的策略

### 8. 配置语义

建议配置结构：

```yaml
interaction_middleware:
  enabled: false
  default_enabled_for_platforms: false
  platforms:
    webchat:
      enabled: true
    wecom_ai_bot_main:
      enabled: true
  progress_render_mode: humanized
```

说明：

- 配置粒度按 `platform_id`，不是按 platform type
- `progress_render_mode` 允许用户选择：
  - `raw`
  - `humanized`
  - `silent`

## 现有函数改造点

### `send()` / `send_streaming()` — 唯一输出控制点

原则：

- 对已启用平台，`send()` / `send_streaming()` 是唯一输出控制点
- 对未启用平台，保持 legacy path
- `send(None)` 视为非流式 control send，属于 `send()` 语义

长期补充：

- 这两个 API 不只是转发 seam
- 它们也是 middleware 拿到最终表达所有权的稳定出口

### `CoreLifecycle.initialize()`

建议后续新增字段：

- `self.interaction_middleware`
- `self.interaction_outbound_dispatcher`
- `self.interaction_persona_runtime`
- `self.interaction_memory_store`
- `self.interaction_router`

### `InternalAgentSubStage.process()`

长期要求：

- 允许 middleware 在这里插入 progress humanization
- 允许中间件决定是否把 tool/search/knowledge 中间过程直接暴露给用户

### `run_agent()`

长期目标：

- core 返回“执行结果”和“执行过程”
- middleware 决定哪些部分转成拟人化过程语言，哪些部分只保留内部可见

### `FunctionToolExecutor`

需要在 middleware 层面关注：

- background task 完成时，middleware 应收到 `task.state(final)` 事件
- handoff subagent 的中间状态应可观察
- knowledge base / search 等执行型能力，也应遵循同样模式：保留在 core，由 middleware 决定是否展示过程与最终包装

## 阶段实施计划

### Phase 1: Middleware Skeleton + send() Routing

目标：

- 新增 `astrbot/core/interaction/` 包
- 建立 `CoreInputGateway`
- 建立 `InteractionMiddleware`
- 建立 `InteractionOutputController`
- 完成按 `platform_id` 的 enablement
- 对首批启用平台建立 `send` / `send_streaming` routing

完成标准：

- 原消息仍能正常进入 pipeline
- 每个事件有 `turn_id`
- 已启用平台的 `send` / `send_streaming` routing 就位

当前仓库中的实现，大体属于这一阶段。

### Phase 2: CoreOutputEvent Capture

目标：

- `run_agent()` / `run_live_agent()` yield `CoreOutputEvent`
- `InternalAgentSubStage.process()` 将 `CoreOutputEvent` 交给 output controller
- `RespondStage` 输出通过 routing 进入 output controller

### Phase 3: Outbound Ownership

目标：

- ExpressionPolicy 接管 public output 决策
- `PipelineScheduler.execute()` 中的 `send(None)` 改为显式 `control.end`
- WebChat / WeCom AI Bot 等特定 outbound payload 由 dispatcher 生成

### Phase 4: Interaction Router + Persona Runtime

目标：

- middleware 能判断：
  - `self_reply`
  - `delegate_to_core`
  - `hybrid`
- middleware 持有 persona runtime
- middleware 持有独立 interaction memory

完成标准：

- 轻交互消息可由 middleware 独立回复
- 执行型消息可由 middleware 委托给 core
- mixed intent 可先接一句再委托 core

### Phase 5: Humanized Progress + Final Wrap

目标：

- middleware 完整接管 public 输出
- core 中间事件可按配置转为拟人化 progress
- core 最终结果可由 middleware 再包装
- stop_speaking / cancel_work / replace_task 全链路实现

完成标准：

- 已启用平台上的 Worker/tool/subagent/background 不能绕过 middleware
- 用户可以选择是否看到原始 progress，或只看到 middleware 生成人格化过程表达

## 风险与边界

### 不替代 core task layer

LLM、tool、plugin、subagent 仍由 AstrBot core 负责。

知识库、搜索、subagent、工具调用都应优先保留在 core，不应在 middleware 再复制一套执行系统。

### 不把 middleware 做成第二个 core

middleware 的重点是：

- 人格
- 记忆
- 路由
- 表达

而不是：

- 重新实现工具执行
- 重新实现知识库检索
- 重新实现搜索能力
