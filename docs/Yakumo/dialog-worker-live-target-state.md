# AstrBot Interaction Middleware Target State

本文档描述 AstrBot 交互中间件的目标状态。

需要先明确两层目标：

1. **当前已落地的首期形态**
   - 在 adapter 与 core 之间插入一层 middleware
   - 建立 `InteractionTurnState` / utterance ledger / stream state
   - 接管 interaction turn 的输入 materialization、路由决策、输出 materialization 与 completion handoff
   - core 旧流程与 middleware 新流程共享 STT/TTS voice service
2. **长期目标形态**
   - 把 middleware 提升为真正的 interaction agent layer
   - 承载人格、独立记忆、交互路由、拟人化进度表达与最终结果再表达

换句话说，当前版本已经不只是 transport shell，而是已经具备 turn owner
语义的 interaction orchestration layer；长期目标仍是继续增强人格 runtime、
正式 output gateway 与 live audio diagnostics。

## 核心定位

本次改造不是替代 AstrBot 现有任务型对话链路，而是在任务型对话链路外侧增加交互中间件。

```text
Core Task Layer
    负责做事：
    LLM、tools、plugins、skills、subagents、search、knowledge base、推理、后台任务

Interaction Middleware
    负责交互：
    人格、独立记忆、路由判断、拟人化表达、进度表达、结果再包装
```

一句话：

```text
Core 负责把事做成，Middleware 负责像“这个角色本人”一样和用户互动。
```

## 当前实现与长期目标

当前这版实现，已经完成：

- adapter -> middleware -> core queue 的输入接入点
- 按 `platform_id` 可配置启用
- `self_reply / delegate_to_core / hybrid` 的路由决策
- state-first streaming phase、stream interjection 与 finalized material
- prompt / result / stream 插件扩展点的只读阶段视图
- interaction outbound phase：finalizer、result contributor、reply prefix、reasoning display、TTS、t2i
- SELF_REPLY / HYBRID / DELEGATE 的统一 turn completion handoff
- memory 写入 owner 收口到 postprocess / memory service
- core 普通流程和 middleware interaction 流程共享 voice service

当前这版实现，还没有完成：

- 正式 output gateway 替换当前 `event.send()` / `event.send_streaming()` interception 形态
- middleware 自己完整的人格 runtime
- core 中间事件的人格化进度转述
- live audio 缺 provider / 文本降级 / completion diagnostics 的完整统一
- 真实平台日志断点与手动验证

因此，当前版本应理解为：

```text
当前 = state-first interaction orchestration layer
下一步 = formal output gateway + live audio diagnostics + persona runtime
```

## 目标链路

```text
Platform Adapter
    -> AstrMessageEvent
    -> Interaction Middleware
    -> AstrBot Core Task Layer
    -> Interaction Middleware
    -> Platform Adapter Outbound
```

核心变化：

```text
旧模式：
Adapter 输入 -> Core 执行 -> Core 直接调用 Adapter 输出

目标模式：
Adapter 输入 -> Middleware -> Core 执行
Core 中间结果/最终结果 -> Middleware -> Adapter 输出
```

## 边界定义

### Platform Adapter

Adapter 仍负责平台边界转换。

输入侧职责：

- 接收平台原始消息
- 解析平台协议
- 生成 `AstrMessageEvent`

输出侧职责：

- 接收中间件生成的标准输出
- 转换成平台消息、WebSocket payload、Webhook response 或主动发送 API

Adapter 不负责：

- 判断 Worker 输出是否可见
- 决定中途状态是否要说
- 合并 tool progress
- 维护 turn/task 表达策略
- 生成自然语言转述

### AstrBot Core Task Layer

Core 原有任务层继续负责执行。

保留能力：

- Provider / LLM 调度
- tools
- plugin handlers
- skills
- subagent / handoff
- background task
- knowledge base
- search
- prompt pipeline

Core 可以继续保留自己的执行上下文与工作态状态，但不再承担“最终人格表达层”的职责。

Core 不再直接拥有最终用户表达主导权。它可以产出：

- 原始结果
- 流式 delta
- tool call / tool result
- task state
- error
- metrics

但这些产物应先进入 middleware，由 middleware 决定是否变成用户可见输出。

### Interaction Middleware

中间件位于 adapter 与 core task layer 之间。

输入侧职责：

- 接收 adapter 标准化后的 `AstrMessageEvent`
- 创建 `turn_id`
- 维护 interaction session / persona runtime / middleware memory
- 执行 session 级 turn 冲突裁决
- 判断当前输入属于：
  - `self_reply`
  - `delegate_to_core`
  - `hybrid`
- 决定是否立即 ack、是否立即回复、是否委托 core
- 将需要执行的任务放行到原 `event_queue` / pipeline
- 处理打断、取消、替换任务

输出侧职责：

- 捕获 core 执行过程中的中间结果
- 捕获 core 最终结果
- 对已启用 middleware 的平台，接管 `event.send()` / `event.send_streaming()` 两个 outbound API
- 维护 turn state / utterance ledger / stream state
- 判断是否显示原始进度、拟人化进度，还是完全静默
- 调用 Dialog/Expression 层生成用户可见表达
- 对 core 最终结果做再表达
- 将表达结果交给 adapter outbound
- 产出 finalized turn material，并调度 `AFTER_TURN_COMPLETED` postprocess

## Memory / Knowledge Boundary

中间件与 core 之间，需要明确区分“人格记忆”和“事实知识”：

- **Middleware Memory**
  - 用户偏好
  - 关系状态
  - 情绪连续性
  - 互动风格
  - 角色口吻与 persona state
- **Core Knowledge / Capability**
  - knowledge base
  - search
  - tools
  - subagents
  - execution-oriented task context

判断原则：

```text
记忆回答“我们之间发生了什么”
知识库回答“世界里有什么事实”
```

因此：

- 人格记忆应优先放在 middleware
- 当前 interaction turn completion 的 memory 写入 owner 是 postprocess / memory service；middleware 只生产 finalized material 并调度 postprocess
- knowledge base 应优先保留在 core
- middleware 决定是否调用 core 的 knowledge / tools / search

## 中间件核心能力

### 1. Input Mediation

输入中介负责 adapter 到 core 的入口控制。

能力：

- 创建 turn
- 执行 session 级 turn 冲突裁决
- 识别普通消息、stop、cancel、replace
- 产出结构化交互决策，而不是只做打标放行
- 决定是否立即 ack
- 决定是否立即回复
- 决定是否放行到 core pipeline

建议的最小决策对象：

```python
class InteractionDecision:
    should_delegate_to_core: bool
    route_mode: Literal["self_reply", "delegate_to_core", "hybrid"]
    immediate_reply: str | None
    core_task_spec: dict | None
    progress_render_mode: Literal["raw", "humanized", "silent"]
    final_response_mode: Literal["middleware_wrap", "core_direct", "suppress"]
```

### 2. Output Routing

不再使用全局 suppress 开关在各处判断。改用路由机制：对已启用 middleware 的平台，把 `event.send()` / `event.send_streaming()` 视为唯一 outbound seam。

```text
enabled platform:
    event.send(chain)
        -> output controller
        -> expression policy
        -> adapter outbound

    event.send_streaming(generator)
        -> output controller
        -> expression policy
        -> adapter outbound

disabled platform:
    event.send(...) / event.send_streaming(...)
        -> legacy adapter path
```

要求：

- 首期不要求全平台覆盖，只要求覆盖配置中启用的 platform id
- 对已启用平台，`send` 与 `send_streaming` 必须一起接管，不能只接一个
- 被路由到 middleware 的输出必须有诊断记录，不能静默丢弃

### 3. Core Result Capture

中间件需要捕获 core 执行过程。核心集成点是 `InternalAgentSubStage.process()`。

捕获策略：

- `InternalAgentSubStage.process()` 中，`run_agent()` / `run_live_agent()` 产出的 `MessageChain` 先经过 output controller
- `RespondStage.process()` 的最终 result 先经过 output controller
- `FunctionToolExecutor` 中 tool 的直接输出，如果最终走到已启用平台的 `event.send()`，则进入 output controller
- plugin 调用 `event.send()` 同样适用该规则

### 4. Output Ownership

用户可见输出由 middleware 接管。

旧路径：

```text
Core -> event.send() -> Adapter -> User
```

目标路径：

```text
Core -> event.send() -> [routing] -> Middleware Output Buffer -> Expression Policy -> Adapter -> User
```

要求：

- 对已启用平台，`send` / `send_streaming` 是唯一输出控制点
- 所有 public 输出有明确来源和 turn_id
- adapter 只发送 middleware 决策后的内容

### 5. Expression Policy

表达策略负责判断如何和用户说。

它接收：

- task state
- final result
- stable partial
- tool progress
- error
- metrics
- interrupt/cancel 状态

它决定：

- 是否输出
- 何时输出
- 输出文本怎么写
- 是否需要 TTS
- 是否需要 presentation intent
- 是否合并或延迟
- 是否丢弃过期 turn 的结果

长期来看，这里不只是“结果修饰器”，而是 middleware persona layer 的一部分。

### 6. Progress Rendering Policy

中间件不应把“是否展示 core 中间事件”的决策交给前端。

前端只负责渲染，中间件负责策略：

- `raw`
  - 直接展示 core 原始中间事件
- `humanized`
  - 不直接展示原始事件，由 middleware 大模型转成拟人化过程表达
- `silent`
  - 中间过程不上屏，只在最终结果时说话

这应当是用户可配置能力，而不是固定行为。

例如：

```text
core event:
    tool_start(knowledge_base_search)

humanized progress:
    “我先帮你翻一下资料，等我一下。”
```

### 7. Session Turn Queue

每个 session 维护一个 turn 队列，解决并发 turn 冲突。

### 8. Task State Store

任务状态存储是 core 任务层和表达策略之间的缓冲。

### 9. Core Output Event

为了避免长期依赖零散 hook，需要引入正式内部输出事件。

`CoreOutputEvent` 包裹现有 `MessageChain`，复用其 chain/type 结构，增加 routing metadata。

## 推荐交互路由

第一版不要追求完美意图识别，而是先稳定分三类：

- `self_reply`
  - 寒暄、情绪承接、关系确认、轻陪伴
- `delegate_to_core`
  - 明确命令、明确任务、搜索、工具、知识库、subagent、执行型工作
- `hybrid`
  - 先由 middleware 接一句，再把任务委托给 core，最后对 core 结果再表达

可以遵循一句简单标准：

```text
如果用户期待的是“被理解”，优先 middleware
如果用户期待的是“问题被解决”，优先 core
```

## 阶段目标

### Phase 1: CoreOutputEvent + Middleware Skeleton

目标：

- 定义 `CoreOutputEvent`、`OutputVisibility`、`PublicOutput`
- 在 adapter 标准化输入和 core event_queue 之间建立 middleware 入口
- 为事件创建 turn_id
- 定义按 `platform_id` 启用 middleware 的配置语义
- 对已启用平台建立 `send` / `send_streaming` routing
- 支持 ack / stop / cancel / replace 的最小语义

说明：

- 当前仓库中的已实现版本属于这一阶段
- 它解决的是“接线与控制权”，还不是 interaction persona layer 本体

### Phase 2: Agent 输出接入 CoreOutputEvent

目标：

- `run_agent()` yield `CoreOutputEvent`
- `InternalAgentSubStage.process()` 将 `CoreOutputEvent` 交给 output controller
- `RespondStage.process()` 输出通过已启用平台的 `send` / `send_streaming` routing 进入 output controller

### Phase 3: Direct Output Routing 验证

目标：

- 确认已启用平台的 `send` / `send_streaming` 覆盖所有目标输出路径
- `PipelineScheduler.execute()` 中的 `send(None)` 改为显式 `control.end` 事件

### Phase 4: Expression Policy + Outbound

目标：

- ExpressionPolicy 接管分段、间隔、合并等决策
- `InteractionOutboundDispatcher` 对接所有 adapter outbound
- WebChat outbound adapter 将 `PublicOutput` 转为 webchat back queue payload

### Phase 5: Persona / Memory / Router

目标：

- middleware 维护 persona runtime
- middleware 维护独立 interaction memory
- middleware 产出 `InteractionDecision`
- middleware 能独立回答轻交互消息
- middleware 能把 core 最终结果再包装为人格化表达

### Phase 6: Humanized Progress + Configurable Display

目标：

- core 中间事件可转译为拟人化进度表达
- 用户可配置：
  - `raw`
  - `humanized`
  - `silent`
- middleware 决定是否把原始执行事件转为用户可见过程话术

## 成功标准

- AstrBot 原任务型对话能力保留
- 对已启用平台，Adapter 输入先经过 middleware，再进入 core
- 对已启用平台，`event.send()` / `event.send_streaming()` routing 是唯一输出控制点
- Core 执行中间结果能被 middleware 捕获
- 对已启用平台，Core 最终结果不再默认直接输出给 adapter
- 用户可见表达由 middleware 的表达策略决定
- 对已启用平台，Worker/tool/subagent/background task 不能绕过 middleware public 输出
- WebChat / live audio / 其他平台都只作为下游消费者，不成为 core 架构中心
- 中长期目标上，middleware 能逐步承载人格、独立记忆、交互路由与拟人化进度表达
