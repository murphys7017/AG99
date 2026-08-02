# Prompt Module

`astrbot/core/prompt/*` 负责把运行时事实确定性地转换成模型请求。它是模型可见输入的唯一主链路，但不负责决定是否回复、执行工具、写入记忆或发送消息。

Prompt 自身使用的 safety、persona fallback、tool-call、live mode、sandbox 和 citation
文本由 `prompt.resources` 持有。Cron/background-task 唤醒提示仍属于 Agent 资源，不由
Prompt 模块反向读取 `astr_main_agent_resources`。

## 当前主链路

```text
Fact Sources
  -> Context Collectors
  -> PromptContextBuilder
  -> canonical / derived ContextPack
  -> CoreExecutionSpec（Core 目标）
  -> project_context_pack(target)
  -> PromptRenderProfile
  -> PromptLayoutInterface
  -> PromptTreeBuilder / PromptTree
  -> Provider Renderer
  -> RenderResult
  -> NativeExecutionAdapter / ProviderRequestAdapter
  -> Provider / Agent Runner
```

这是一条确定性数据管线。目标投影、布局和序列化都不调用 LLM，也不存在 LLM Selector。

## 功能边界

| 层 | 负责 | 不负责 |
|---|---|---|
| Collector | 从官方运行时、Interaction 和插件读取事实，输出命名明确的 `ContextSlot` | 拼最终 Prompt、做路由决策、写 memory、调用模型 |
| `PromptContextBuilder` | 合并事实、检测冲突、生成带版本的新 `ContextPack` 快照 | 按目标裁剪、决定物理消息布局 |
| Target Projection | 按 Router、Core Planner、Persona、Core 做白名单、裁剪和诊断清理 | 生成指令、调用模型、修改规范 Pack |
| `PromptRenderProfile` | 提供目标局部的 system/request prompt、输出契约、输入后缀和精确隐藏项 | 声明共享事实、判断 Provider 能力、修改原始 Pack |
| Layout / Tree | 把逻辑 slot 放入 provider-neutral 语义树 | 选择业务事实、生成 Provider 私有 payload |
| Provider Renderer | 编译 system/messages/media/tool schema/output contract | 选择目标上下文、执行工具、决定业务路由 |
| Execution Preparation | 在目标渲染前把 Core 的 `ContextPack`、TaskSpec、执行历史、能力和执行身份组合成 provider-neutral `CoreExecutionSpec` | 保存 RenderResult、执行 Provider 协议、重做事实收集 |
| Native Adapter | 复用 `ProviderRequestAdapter` 把 `RenderResult` 写入官方 `ProviderRequest`，并带入已装配的实际工具 | 重新投影 Prompt、选择任务、替换官方 Hook |
| Provider / Runner | 落地协议并执行模型或工具循环 | 回头收集、投影或修补 Prompt 事实 |

## 收集与构建

### Collector

默认 Collector 覆盖 system、persona、input、session、policy、memory、official conversation history、插件显式 context、skills、tools、subagent、knowledge 和 Core Execution History。Execution History 是 optional、Core-only 的独立 ledger 投影，不属于可见 Conversation。Interaction 在同一规范 Pack 上增加附件摘要、Interaction Prompt Contributor；Persona 阶段再派生本轮待表达材料。

Collector 只返回事实：

- 同一次收集中，同名 slot 的不同值会触发 `PromptContextConflictError`。
- Core Collector 默认 `required`，异常会终止构建；只有显式 `failure_policy="optional"` 的 Collector 才记录诊断后继续。当前 `MemoryCollector` 是 optional。
- 插件 Prompt Extension Collector 采用插件隔离策略：异常或无效项会记录告警并跳过，不中断核心 Collector。
- `lifecycle="static"` 只表示同一 event、同一 config 和同一 `ProviderRequest` 对象内可复用，是 turn-static，不是跨回合或全局缓存。
- Collector 不应执行 memory 写入、路由判断或有副作用的工具调用。

### PromptContextBuilder

`PromptContextBuilder` 是规范构建和阶段派生的统一入口。`build(base=...)` 每次返回新快照，不修改输入 Pack，并维护：

- `context_version`
- `collection_scopes`
- `slot_count`
- Collector 与缓存诊断

跨阶段新增或替换事实必须经过 Builder。`ContextPack` 数据类型本身仍然可变，供收集和渲染内部使用；业务模块不得把直接 `add_slot()`、`slots.pop()` 或原地改值当作跨阶段 API。进入 `CoreExecutionSpec` 时，slots、meta、TaskSpec、执行历史和可序列化 capability 描述会被深拷贝，避免后续构建侧变更影响已经准备的执行事实；Native `ToolSet` 是唯一明确保留的实时执行句柄。

Interaction 当前通过默认 Collector 建立一份完整的本轮共享事实包，Router、Planner 和 Persona 只消费各自的极简投影。后续性能优化应由 Collector 生命周期、缓存、并发和按需采集策略完成，不能让业务模块重新建立同类事实源。

## 目标投影

`project_context_pack(...)` 从 Pack 深拷贝出隔离视图。所有模型渲染都会先排除 `llm_exposure="never"`；显式目标还会同时执行固定代码规则和 slot 级 `meta.targets`。无 target 的普通 Main Agent 不套用 Core 白名单，但仍执行 exposure 过滤。敏感事实仍应在 Collector 产生前最小化，不能把渲染过滤当作日志或进程内保密机制。

| 目标 | 当前可见范围 | 明确排除 |
|---|---|---|
| Router | 当前输入、附件计数、时间、说话者、近期历史、群聊近期上下文、人格摘要、topic/short-term memory、插件目录 | 完整人格、媒体正文、工具 schema、effect、Core/Planner 决策 |
| Core Planner | 当前输入、附件计数、时间、说话者、清理后的近期历史、topic/short-term memory、插件目录 | 完整人格、Router 决策、effect、实际工具 schema |
| Persona | 完整人格、官方历史、群聊上下文、memory/persona state、当前输入、待表达材料和 Core 结果 | policy、knowledge、执行能力、Core 私有执行上下文 |
| Core | 官方历史、群聊上下文、当前输入和附件、system/policy、tools、skills、knowledge、subagent、插件执行上下文、`CoreTaskSpec`、有限 Core Execution History | 完整人格、persona state、待表达材料、effect 语义 |

Router 和 Core Planner 只共享事实来源，不共享模型 Prompt、决策或输出。投影中的历史长度、字段清理和诊断移除属于确定性安全边界，不是“让模型自己忽略”。

Prompt Extension 的 `meta.targets` 可声明 `router`、`core_planner`、`persona`、`core`。普通 extension 未声明目标时只属于 Core。Router/Planner 的插件目录只保留明确授权的插件 `name` 和 `description`。

## Render Profile

`PromptRenderProfile` 在目标投影后应用到一个新的目标视图，当前支持：

- `system_prompt`：替换目标视图中的 `system.base`。
- `request_prompt`：成为最终模型请求命令，不写入共享事实。
- `output_contract`：写入目标树的输出契约元数据。
- `input_text_suffix`：只追加到字符串类型的 `input.text`。
- `hidden_slot_names`：按完整 slot 名精确隐藏，不支持通配符，也不能替代目标投影的安全规则。

Profile 是“如何使用事实”的局部策略，不是 Collector。Router、Core Planner 和 Persona 的指令与输出协议属于 Profile；当前消息、历史、待表达材料和插件信息仍必须由 Collector 提供。

## Layout、Tree 与 Renderer

`PromptTreeBuilder` 只接收目标视图和 `PromptLayoutInterface`。Layout 决定逻辑 group 的启用范围、节点路径和 slot 到树节点的落位；PromptTree 是 provider-neutral 中间表示。

`PromptLayoutInterface` 通过单一 `render_group(...)` 明确 Builder 的完整依赖，不再要求调用方隐式实现一组动态方法。默认实现仍处于过渡态：`DefaultPromptLayout.render_group(...)` 内部委托 `BasePromptRenderer` 中既有的 provider-neutral 落位方法；选中的 OpenAI/Anthropic/MiniMax Renderer 不参与目标数据选择。后续只需迁移默认实现，不再改变 Layout 公共契约。

Provider Renderer 只编译已经形成的树：

- `OpenAIPromptRenderer`
- `AnthropicPromptRenderer`
- `MiniMaxPromptRenderer`
- `BasePromptRenderer`

它负责 system/messages、content blocks、媒体、工具 schema 和 `OutputContract` 的协议策略。Provider metadata 的 `prompt_renderer_family` 只选择序列化家族，不改变目标投影或 Layout。

## RenderResult 与 Apply

`RenderResult` 承载 `prompt_tree`、`system_prompt`、`messages`、`tool_schema`、输出契约、metadata 和可选 `request_prompt`。

完整 PromptTree 只保留在进程内 `prompt_tree` 字段供 Apply 使用，不复制到常规 metadata，也不写入 DEBUG 结构日志；日志只记录截断预览、slot 名和计数。

`ProviderRequestAdapter` 的规则是：

- 没有 `request_prompt`：最后一条 user message 拆成 `ProviderRequest.prompt`，此前消息进入 `contexts`，媒体转为 content parts。
- 存在 `request_prompt`：所有渲染消息保留为 `contexts`，Profile 命令成为 `ProviderRequest.prompt`。
- Adapter 会替换模型可见的 system、contexts、prompt、媒体和输出契约。
- Adapter 不修改 `func_tool`、provider、conversation、session 或 runner 配置。

`RenderResult.tool_schema` 与 `ProviderRequest.func_tool` 当前不是同一执行事实源。前者是渲染/诊断产物，后者仍由 Main Agent 的能力装配负责；在统一工具能力模型完成前，不得假定修改 `tool_schema` 就会注册可执行工具。

## 主链路接入

### Interaction

Interaction 每轮先建立共享 Pack。Router、Core Planner 和 Persona 从该 Pack 的独立投影渲染；Persona 的待表达材料通过专用 Collector 派生。Planner 选择执行后，Main Agent 复用共享 Pack，并加入阶段性的 `CoreTaskSpec` 后渲染 Core 目标。

### 非 Interaction Core

普通 Main Agent 直接运行默认 Collector，不使用 Router/Planner/Persona Profile。`astr_main_agent` 装配运行时工具和 Runner，从完整 Pack 形成 `CoreExecutionSpec`，随后按 Native 目标渲染并由 Native Adapter 转为官方请求，不再手写另一套模型可见 Prompt。SubAgent Collector 仍属于这一 Native 收集路径；通用 Snapshot 不再设置独立 SubAgent 字段，但 Native Pack/ToolSet 暂时保留兼容信息。

### 官方钩子

官方 `on_llm_request` 是最终路由分支的低层请求钩子，执行顺序在该分支的统一 Prompt Apply 之后。非 Interaction 流程保持 Core 行为；Interaction turn 中，LLM 生命周期目标按配置、插件类 `interaction_runtime_target` 声明、Persona 默认值依次解析，只有最终为 `core` 的插件才进入 Core。插件拥有的 LLM Tool 独立按 `plugin_tool_targets` 用户覆盖、工具 `tool_targets` 声明和 Core 默认值解析。该钩子适合修改最终请求参数或兼容旧插件，不是给 Router、Planner 或 Persona 内部工具调用贡献共享事实的入口，也不保证覆盖这些轻量模型调用。

需要贡献模型可见事实的插件应使用 `PromptExtensionCollectorInterface`。插件开发接口见中英文 Prompt Extension 指南。

## 输出契约边界

```text
OutputContract
  -> CompiledOutputContract
  -> RenderResult / ProviderRequest
  -> provider protocol or controlled prompt-only fallback
  -> response parser
```

Router 只返回固定分类词，不使用工具或 JSON。Core Planner 使用独立的 `core_execution_plan` 契约。Persona 优先通过虚拟 `persona_expression` tool call 返回 `spoken_reply` 和按当前事件过滤后的 `effect_calls`；具体 Motion、Live2D 或设备协议属于插件，不属于 Prompt 主流程。

## 当前限制

- Provider renderer family、输出契约能力和工具能力还没有统一成一个 capability 声明。
- `ContextCatalog` 的 required/lifecycle/redaction 等字段多数仍是描述和告警，不是完整运行时强约束。
- `ContextPack` 仍是可变数据类型，跨阶段不可变性依赖 Builder 使用约定和测试。
- `DefaultPromptLayout` 内部仍复用 Base Renderer 的 provider-neutral 落位实现，但 Builder 依赖的 `render_group(...)` 契约已经稳定。
- `tool_schema` 与实际 `func_tool` 尚未统一事实源。
- 上下文预算、Collector 并发和更细的敏感字段脱敏需要在上述边界稳定后继续处理。
- Collector 的官方兼容签名仍直接接收 `AstrMessageEvent`、插件 `Context` 和
  `ProviderRequest`；这使 Prompt 可以统一事实，却还不能独立于 AstrBot runtime contracts。
- Provider 的协议 tool adapter 已迁入 `provider.output_contract_tools`，不再反向导入
  Prompt。该 adapter 仍使用 Native `ToolSet`；完整中性 capability contract 尚未形成。

后续处理顺序见 `docs/Yakumo/prompt-development-plan.md`。
