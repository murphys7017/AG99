# Persona Runtime Phase Plan

这份文档记录 Yakumo 从消息驱动机器人演进为持续人格运行时的实施计划。它是阶段计划，不是当前代码说明，也不代表所有目标都已完成。

## 当前共识

- Yakumo 的目标不是增强一条“收到消息后回复”的链路，而是让 persona 成为跨消息、跨 conversation、跨平台持续存在的主体。
- 消息、平台事件、任务进度和定时信号都是 persona 收到的 `Observation`；消息平台只是感知与表达 channel。
- 官方 AstrBot 不是需要被替换的旧系统，而是 Yakumo 的运行底座。
- Yakumo 主要增加持续人格所需的主体、状态、任务和表达编排，不重复实现官方已有能力。
- 第一优先级是把 AstrBot 改造成目标中的持续人格系统；复用和吸收官方上游能力服务于这个目标，而不是约束这个目标。
- Interaction Middleware 是官方 Pipeline 后、Core Agent 前的一轮交互边界，不是长期 Persona 本体。
- 第一阶段不引入常驻 LLM 循环，不重写 `AstrMessageEvent`，也不新建一套平行的 Input/Output/Pipeline。

## 官方运行底座

Yakumo 直接依赖并复用官方已经实现和测试的能力：

```text
Official AstrBot Runtime Foundation
├── EventBus / Pipeline / Filter / Permission
├── Plugin Handler / Hook / LLM Tool
├── Provider / Model / STT / TTS
├── Knowledge / Search / Sandbox / SubAgent
├── Session / Conversation / Database / Config
└── Platform Adapter / Delivery
                ↓
Yakumo Persona Control Layer
├── Observation
├── PersonaRuntime
├── TurnContextSnapshot
├── ActiveTask
├── Unified Persona Expression
└── OutputEnvelope / FinalizedMaterial
```

### 复用原则

- 官方 EventBus、Pipeline、权限、白名单、唤醒和插件 Handler 先处理事件。
- Observation 只消费已经通过官方处理的事件，不重复实现平台事件过滤。
- Native Core 继续使用官方 Agent、Tool Loop、插件工具、知识库、搜索和 sandbox。
- 外部执行器只能通过 Capability Gateway 使用官方已经筛选和授权的能力。
- Output Runtime 继续调用官方 platform event / adapter 发送，不复制平台协议。
- Persona、conversation、provider、config、database 和插件生命周期继续由官方 manager 提供。
- 新代码优先增加 orchestration、projection 和 protocol，不复制 capability implementation。
- 能通过 Yakumo 自有模块、组合或稳定扩展点实现的能力，不无谓侵入官方实现；目标语义确实要求改变核心时，应直接改造，并保持职责和边界清楚。
- 引入官方上游更新时，以 Yakumo 的目标架构和行为为判断基准：吸收适用的能力与修复，调整或拒绝与目标冲突的变化。

旧插件兼容是有价值的次级目标，因为它能继续利用官方生态，但不是架构约束。如果旧插件行为与持续人格语义或正确性冲突，可以提供迁移路径而不强行保留。若官方接口无法表达持续人格所需的主体、任务或生命周期语义，就应有记录地改造；在此之前先确认 projection、adapter 或 delegation 是否能以更低成本实现相同目标。

## 目标流程

```text
Platform / WebUI / Official Internal Event
  -> Official EventBus / Pipeline / Plugin Handlers
  -> Interaction Boundary
      -> Observation projection
      -> PersonaRuntime
          -> TurnContextSnapshot
          -> Router: silent / persona / hybrid
              -> silent: complete without visible output
              -> persona: Unified Persona Expression
              -> hybrid: independent Core Planner
                  -> not_required: Unified Persona Expression
                  -> execute: Core and delegation acknowledgement start concurrently
                      -> ActiveTask progress / result
                      -> Unified Persona Expression
              -> Output Arbiter
      -> Existing Interaction Output Runtime
      -> Official Platform Adapter
  -> FinalizedMaterial
  -> Postprocess / Memory / Persona State
```

一次消息 turn 是 PersonaRuntime 消费 Observation 的一种情况，不再是 Persona 的完整生命周期。

## 当前链路复核

当前实现已经形成可继续演进的单轮 Interaction 外壳：

- 官方 Waking、Whitelist、Session Status、Rate Limit、Content Safety 和 PreProcess 先执行。
- `ProcessStage` 在插件 Handler 执行前准备输出接管，并在 Core Agent 前调用 Interaction Middleware。
- 对话 Router 只输出 `silent`、`persona` 或 `hybrid`；直播音频和协议命令使用独立的内部 Core bypass，不伪装成 Router 结果。
- Prompt 层统一采集本轮事实并形成规范 `ContextPack`；Router、Core Planner、Persona 和 Core 从同一 Pack 投影不同视图，不重复查询同一份身份、历史和记忆。
- Router 与 Persona Expression 并发启动；`silent` 抑制尚未提交的 Persona，`persona` 不启动 Core，`hybrid` 再由独立 Core Planner 复核执行必要性。Persona 已经 committed/emitted 时不因 late-silent 撤回。
- Core Planner 不读取 Router 决策内容，只根据 Planner 事实投影返回 `execute` / `not_required`；只有 `execute` 才生成 `CoreTaskSpec` 并委派 Core。
- 即时表达、Core 最终结果和显式 persona 插件输出都复用 `InteractionPersonaRuntime` 的表达入口。
- `InteractionOutputController` 统一承担 materialization、TTS、平台发送、可见输出记录和 finalized material。
- Core 只处理通用 persona effect 注册与结构化调用，不理解 Motion、Live2D 等插件领域语义。

但它目前仍然是一条消息回复链路，而不是持续 PersonaRuntime：

1. Interaction 只在插件产生 `ProviderRequest`，或官方流程已经准备调用 Core LLM 时处理输入。未触发 Core 的有效平台事件、任务事件和内部事件不能成为 Observation。
2. `InteractionPersonaRuntime` 只是 Expression Agent 的薄包装，没有 persona runtime identity、Observation 调度、ActiveTask 或跨 turn 生命周期。
3. 推测式 Persona 当前使用 turn-local 提交状态完成 silent/Core 竞态仲裁；它还不是跨 Observation、跨任务的通用 Output Arbiter。
4. Core 工具状态、工具直出和部分中间消息仍通过普通 `event.send()` 进入输出分类，可能被当作 `passthrough` 提前完成 turn。
5. 普通插件输出默认是 `direct`，语义文本仍可绕过唯一 Persona Expression。
6. 当前共享 `ContextPack` 已消除 Router、Planner、Persona、Core 的重复基础采集，但 Interaction Memory 仍是按 session 保存的独立 JSON，不是跨 conversation、跨平台的人格状态。
7. Local / Third-party Runner 在 Pipeline 初始化时选择，还不是 PersonaRuntime 按 ActiveTask 解析的 ExecutionBackend。

这些问题的处理顺序应服从目标架构，而不是为了保持当前链路形状只做局部补丁。

## 核心对象

### `Observation`

Observation 是只读输入事实，表达“人格观察到了什么”，不代表一定需要回复。

最小字段：

- observation id、kind、timestamp
- persona id 与 source channel
- sender、audience、session / conversation reference
- visibility、privacy、permission
- text、attachments、quoted material 或结构化事件 payload
- 可选的、仅本轮有效的原始 `AstrMessageEvent` 只读兼容引用

第一阶段建议只支持：

- `user_message`
- `platform_event`
- `system_event`
- `task_event`

普通消息、Notice、戳一戳和任务进度不能互相伪装。是否创建 Observation、是否交给 Persona，仍以官方事件类型和 Pipeline 处理结果为前提。

原始 event 引用只用于本轮委派官方能力，不能进入长期 Persona 状态、ActiveTask 持久化或 Memory。跨 turn 保存时只保留规范化事实与官方稳定标识，避免绑定具体 adapter 和上游内部对象生命周期。

### `PersonaRuntime`

PersonaRuntime 是围绕 persona identity 的长生命周期编排者，负责：

- 接收 Observation
- 解析本轮 Effective Persona 与 audience scope
- 决定是否表达、保持静默或启动任务
- 消费任务进度与结果
- 把待表达材料交给唯一 Persona Expression 入口

PersonaRuntime 不直接拥有官方数据库、Provider、Memory、插件或平台 adapter。它通过现有 manager/service 使用这些能力。

“持续存在”也不等于无边界全局单例：

- persona identity 跨 turn 保持连续
- relationship、privacy、conversation 和 audience state 按 scope 隔离
- active task 有独立 identity 和授权上下文
- 持久状态由 Memory / PersonaState service 管理，不只保存在 Python 对象内

### `TurnContextSnapshot`

一次 Observation 处理期间共享的只读上下文快照：

- identity / audience
- persona / persona state
- history / episode
- memory snapshot
- input / attachments
- filtered capabilities

Router、Core Planner、Persona Expression 和 Core 使用不同 Prompt Profile，但不应分别重复查询同一份身份、历史和记忆。Router 与 Planner 的模型决策不属于快照事实，不能相互注入。

### `ActiveTask`

ActiveTask 表示 Persona 委派给 Native Core、Codex、OpenCode 或其他执行器的持续任务。

统一状态：

- `queued`
- `running`
- `thinking`
- `tool_running`
- `completed`
- `failed`
- `cancelled`

执行器通过 task event 返回进度与结果，不直接把普通 `event.send()` 当作任务生命周期协议。

### `ExpressionIntent` / `OutputEnvelope`

- `ExpressionIntent` 描述 Persona 想表达什么、面向谁、是否允许静默。
- `OutputEnvelope` 表示一次逻辑 utterance，包含 semantic text、文本/语音 rendition、目标 channel、delivery identity 与可选的不透明插件扩展数据。
- 即时表达、Core 结果、任务进度和插件 persona 输出都复用唯一 Persona Expression。
- 主流程不定义也不解释 Motion、Live2D 或其他具体效果；相关插件只通过扩展点消费自身的数据。

## 改造与上游复用策略

优先级从高到低为：

1. 实现 Yakumo 持续人格系统的目标语义和使用体验。
2. 最大限度复用官方已经成熟的能力，避免重复开发。
3. 在不偏离目标的前提下吸收官方上游能力与修复，控制长期维护成本。
4. 在不妨碍前三项的前提下兼容官方插件生态和既有行为。

### 上游协同

- 不重复实现已经满足需求的官方模块；Yakumo 优先通过组合、投影和委派接入。
- 核心语义需要改变时允许修改官方代码，但应形成明确的 Yakumo 边界，避免同一职责散落到 EventBus、Pipeline、Core 和 Adapter 内部。
- Yakumo 自有对象不成为官方对象的替代品；`Observation`、`PersonaRuntime` 和 `ActiveTask` 只负责官方当前没有表达的持续人格语义。
- 上游更新进入后，先验证 Yakumo 的目标语义和主流程，再验证可复用的官方行为；不能为了保持上游原样而退回消息机器人模型。
- 对官方模块的改造要记录目的和边界，便于后续判断上游新能力可以直接复用、适配还是替换现有实现。

### 第一阶段沿用的官方入口

- 不给 `AstrMessageEvent` 增加新的必需公开 API。
- 不要求官方 platform adapter 为 PersonaRuntime 重写协议。
- 不改变官方 Handler、`MessageEventResult`、`ProviderRequest`、LLM Tool 和 Hook 的基本入口。
- 未启用 Interaction / Persona Runtime 的平台继续走官方路径。

### 过渡方式

- 在 `ProcessStage` 的插件处理与 Core 执行之间建立明确的 Persona Observation 接缝；它不以当前事件是否准备调用 LLM 为前提。
- `ObservationFactory.from_event(event)` 在 Interaction 内部做只读投影，不修改 event 类型，也不把所有平台服务通知伪装成用户消息。
- Observation 优先保存在 `InteractionTurnState`；`event.extra` 只在已有兼容点需要时镜像。
- 第一阶段继续使用现有 `InteractionOutputController`，不另建一套 Output Gateway。
- 第一阶段继续使用现有入站 materialization，不另建一套通用 Input Runtime。
- 第一阶段继续使用现有 Core bridge，不提前重写 Agent、插件、工具和知识库。

### 修改官方边界的判断

1. 改造必须直接服务于持续人格语义、正确性、体验或长期可维护性，而不是无目的重写。
2. 修改前比较直接复用、投影适配和核心改造三种方式，选择最符合目标且总体成本合理的方案。
3. 必须记录受影响的上游接缝、API、平台和迁移方式，方便继续评估官方更新。
4. 不长期维护两套拥有相同语义的主链路。
5. 不为兼容而接受重复回复、错误 completion 或权限绕过。

## 实施阶段

### Phase 1：Observation 接缝与 PersonaRuntime 入口

目标：把 Persona 从“Core 调用前的回复中间件”提升为官方 Pipeline 后的独立观察与编排主体，同时保持现有用户可见回复语义稳定。

实施内容：

1. 定义只读 `Observation`、kind、source、actor、audience 和 privacy 数据类型。
2. 调整 `ProcessStage` 内部边界：插件输出接管仍可在 Handler 前准备；Observation 在官方过滤、预处理和插件处理之后、Core 执行之前分发。
3. Observation eligibility 使用官方事件类型与插件扩展判断，不能仅依赖 `is_at_or_wake_command`、`call_llm` 或 `ProviderRequest`。
4. 使用官方 persona manager 的解析结果确定 persona identity，不另建 persona repository。
5. 增加轻量 `PersonaRuntimeManager`，按 persona identity 提供 runtime handle，并按 audience、privacy 和 relationship scope 隔离状态。
6. 将 Observation 和 runtime identity 保存到 `InteractionTurnState`；原始 event 只在本轮委派官方能力时使用。
7. `PersonaRuntime.handle_observation(...)` 第一阶段复用现有 Router、Persona Expression、Core bridge 和 OutputController；非回复型 Observation 默认只记录或通知，不主动发言。
8. 保持 Router 与 Persona Expression 从回合开始并发；Router 选择 `hybrid` 且独立 Core Planner 返回 `execute` 后立即启动 Core。Core 不等待即时表达，silent/Core 与 Persona 通过同一个提交状态仲裁。
9. 将 Core thinking、tool call、tool result 和执行状态映射为 lifecycle / task progress；中间进度不得触发 finalized material 或 turn completion。

这一阶段明确不做：

- 不修改 `AstrMessageEvent` 公共接口
- 不迁移平台 adapter
- 不新建 EventStateStore、InputRuntime 或 OutputGateway
- 不增加后台模型调用
- 不改变插件事件类型
- 不实现主动回复
- 不引入可替换执行器

验收条件：

- Observation 只在官方 Pipeline 过滤之后创建。
- 有效 Observation 的创建不依赖当前事件是否准备调用 Core LLM。
- Notice、戳一戳、普通消息、任务进度和平台服务状态保持不同 kind；无意义服务通知不会触发回复。
- QQ、WebChat 等现有消息行为保持一致。
- 同一 persona 可以得到稳定 runtime identity。
- 不同 audience、session、privacy scope 不串线。
- `silent` 不调用 Core，并抑制仍为 pending 的 Persona；若 Persona 已 committed/emitted，则保留回复并以 replied material 完成，否则以无可见输出的 silent material 完成。
- 直播音频和协议命令不进入对话 Router，也不产生伪造的 Router 决策。
- `hybrid` 中 Core 委派不等待即时表达完成；Core 提前完成时，尚未发送的即时表达会被取消或抑制。
- 即时表达和 Core 最终表达调用同一个 Persona Expression，不形成两套拟人层。
- Core 工具和思考进度不会提前完成 turn，也不会造成重复最终回复。
- 未启用 Persona Runtime 的路径不受影响。
- Yakumo 接入点保持集中，后续同步官方 Pipeline、Core 或 Adapter 更新时不需要重写 PersonaRuntime。

### Phase 2：共享 TurnContextSnapshot

- 一次 Observation 只解析一次 identity、history、memory、persona 和 attachments。
- Router、Core Planner、Persona 和 Core 从同一 snapshot 投影不同 Prompt Profile。
- required / optional collector、超时和降级诊断在 snapshot 边界统一生效。
- Router 继续保持极简 Profile，但不再单独重复查询 conversation 和 memory。
- 区分 conversation history、relationship state 和 persona state；逐步用官方 Memory / Persona 能力替代按 session 保存的 Interaction JSON 主状态。

### Phase 3：ActiveTask 与可替换执行器

- 把 Core 委派改为 ActiveTask。
- 抽出 `ExecutionPlan`、`ExecutionBackend`、`ExecutionEvent` 和 `ExecutionResult`。
- 先用 `NativeAstrBotBackend` 包住官方现有执行路径，不改变行为。
- 外部执行器通过 execution-scoped Capability Gateway 使用官方插件、工具和知识库能力。
- Codex、OpenCode 和 Native Core 都向 PersonaRuntime 返回统一 task event。
- ExecutionBackend 由 PersonaRuntime 针对 ActiveTask 解析，不在 Pipeline 初始化时全局固定。

### Phase 4：ExpressionIntent 与 OutputEnvelope

- 即时表达、任务进度、最终结果和插件 persona 输出统一形成 ExpressionIntent。
- 一次逻辑 utterance 只创建一个 OutputEnvelope。
- 文本和 TTS 是同一 envelope 的 rendition，不是多条独立回复；插件扩展也不能额外创建重复的逻辑回复。
- 普通插件最终语义文本默认进入 Persona Expression；`direct` 只用于明确的协议输出、不可改写内容和原始媒体投递。
- 现有 OutputController 逐步承载 envelope，不另建平行输出链路。

### Phase 5：Background Mind 与主动存在

- heartbeat、idle tick、scheduled reminder、task state 和 reflection trigger 作为内部 Observation 接入。
- 主动表达必须经过 audience、privacy、importance、cooldown 和 interruption policy。
- 后台分析使用有界队列，不与前台 Persona/Core 请求无约束争抢 Provider 和数据库连接。
- Background Mind 不直接发送平台消息，也不直接改写 Memory 或 Persona 底座。

## 非目标

近期不追求：

- 重新实现官方 AstrBot
- 完整服务化拆分
- 一次性重写所有平台和插件 API
- 为兼容任意旧插件而冻结官方能力或 Yakumo 架构
- 默认小模型常驻循环
- 无限制主动回复
- 把所有状态塞进 PersonaRuntime Python 对象
- 让 AG99live 或其他客户端直接监听所有 session 原文

近期目标只包括：建立 Observation、持续 PersonaRuntime identity、共享 TurnContextSnapshot，以及输入、执行、表达和 finalized material 的稳定边界。
