# Persona Runtime 最终目标

本文只定义 Yakumo 持续人格运行时的长期边界，不记录已经完成的迁移步骤。当前实现以
`current-state.md` 和源码为准，实施顺序以 `execution-backend-preparation-plan.md` 为准。
自主人格观察、策略和 Heartbeat 的详细实施计划见
`autonomous-persona-runtime-initial-plan.md`。

## 目标

Yakumo 要把 AstrBot 从一次消息触发一次回复的 Bot Runtime，演进为持续观察、按需执行、
统一表达的 Persona Runtime。官方 EventBus、Pipeline、权限、平台适配器和插件 Hook 继续
作为输入基础设施，不再建立平行 Input Bus 或 Input Gateway。

```text
Platform Adapter
  -> official EventBus / Pipeline / Plugin Handler
  -> Personal Runtime
       -> Router || Persona Expression
       -> optional Core Planner / Execution Backend
  -> Persona Expression
  -> Output Runtime
  -> Finalized Turn Material
  -> Postprocess / Conversation / Memory
```

## 核心职责

### Personal Runtime

Personal Runtime 是控制层，负责：

- 以有效 persona、audience 和 privacy scope 识别持续运行实例。
- 管理 turn、mailbox、并发、follow-up、取消和完成权。
- 并发启动 Router 与即时 Persona Expression。
- 根据 Router 和 Planner 结果决定是否进入执行层。
- 仲裁即时表达、执行结果和插件输出，避免重复完成同一 turn。

它不拥有 Persona、Memory、Provider 或平台数据本体，只持有一轮运行所需的引用与快照。

### Persona Expression

Persona Expression 是唯一拟人层。即时回复、Core 结果、插件 persona 输出和流式插话都以
“待表达材料”调用同一个入口，不再维护多个文案生成器。

Persona Expression 负责“怎么以这个人格表达”，不负责执行工具、投递平台消息或解释
插件领域 effect。静态 Persona、动态人格状态、对话历史和 Memory 由 Prompt 系统收集后，
按 Persona target 渲染。

### Router 与 Core Planner

Router 是极简分类器，只判断当前输入可由 Persona 直接回应，还是需要进入 Core 候选路径。
它不生成回复、不规划任务、不接收工具 schema。

Core Planner 与 Router 独立。它在 `hybrid` 路径上根据同一规范事实包的 Planner 投影判断
`execute` 或 `not_required`；只有 `execute` 才生成 `CoreTaskSpec`。两者不共享模型决策、
Prompt 或临时状态。

### Execution Backend

执行层负责工具、知识库、Skills、Subagent、搜索、文件、代码和其他任务执行。Native Runner、
Claude Code、OpenCode 等后端位于同一执行契约之后。

执行层产出结构化进度与结果材料，不直接决定最终人格文案，也不直接拥有平台发送语义。

### Output Runtime

所有用户可见输出进入同一 Output Runtime。文本、流式文本、TTS、媒体和插件 effect 是同一
逻辑 utterance 的不同 rendition；物理发送不能反向决定逻辑消息身份。

`direct` 或 `protocol` 只表示跳过 Persona 改写或保持协议内容，不表示绕过 output identity、
投递记录和 completion。平台握手、ACK 等非用户可见控制消息可由 Platform Sink 内部处理。

### Conversation、Memory 与 Postprocess

官方 Conversation 保存精确对话历史，Memory Service 保存短期摘要、长期记忆、关系和动态
人格状态。Interaction 不维护私有记忆副本。

一轮结束后形成 Finalized Turn Material，Conversation、Memory 和其他 Postprocessor 只消费
这份稳定材料，不分别从 event extra、平台消息或可见文本中猜测本轮事实。

## Prompt 数据边界

Prompt 系统是所有模型调用的事实入口：

```text
Collectors
  -> canonical ContextPack
  -> target projection
  -> target-local Render Profile
  -> Layout / PromptTree
  -> Provider Renderer
  -> ProviderRequest
```

Collector 负责事实，Projection 决定 Router、Planner、Persona 和 Execution 各自可见内容，
Renderer 只负责编译 Provider 输入。业务模块不得重新查询或拼装同一类事实。

## 插件边界

现有官方 Plugin Handler、decorator、Hook 和 `AstrMessageEvent` 公共接口继续保留。新扩展点按
稳定阶段提供，而不是暴露 AgentRunner 私有对象：

- Prompt Extension：贡献模型可见事实，并声明适用 target。
- Persona effect：注册通用结构化 effect contract，由适用平台或插件消费。
- Output contribution：补充或转换统一输出材料。
- Lifecycle observation：观察 received、routing、delegated、speaking、completed 等状态。
- Execution capability：以 tool、skill、subagent 或 backend 能力挂入执行层。
- Postprocessor：消费 finalized material，更新 Memory、统计或其他持久状态。

默认插件仍从官方 Handler 位置生效。插件直接依赖 AgentRunner 内部对象的能力需要通过执行
适配边界逐步迁移，不能成为可替换 backend 的公共契约。

## 长期运行约束

持续人格不等于持续调用大模型。心跳、主动表达、后台反思和环境观察必须经过预算、冷却、
重要度和可见性判断；不同模型角色可使用独立 context lane，并记录 usage/cost ledger。

## 下一步

1. 将 turn、mailbox、follow-up 和任务 owner 收口到 Personal Session Runtime。
2. 将剩余可写状态收口到唯一 TurnState，extra 只保留官方兼容或只读诊断投影。
3. 统一 Output Dispatcher 和主动消息入口。
4. 固化 Context Snapshot 与 Capability Snapshot 的生命周期。
5. 最后接入可替换 Execution Backend。

## 非目标

- 不重写官方 EventBus、Pipeline、平台适配器和公开插件 Hook。
- 不建立第二套输入总线、状态仓库或输出网关。
- 不为已经删除的内部过渡 API 保留兼容层。
- 不让 Execution、Plugin 或 effect consumer 绕过统一 Persona/Output 边界发送普通用户回复。
