# Prompt Module

`astrbot/core/prompt/*` 负责把运行时事实转换成不同模型角色可消费的 Prompt。当前主链路是：

```text
Collectors
  -> PromptContextBuilder / ContextPack
  -> project_context_pack(target)
  -> PromptTreeBuilder
  -> Provider Renderer
  -> RenderResult
  -> ProviderRequestAdapter
```

这是一条确定性数据管线。它不使用 LLM Selector，也不让 provider renderer 决定应该读取哪些业务数据。

## 边界

### Collectors

Collector 只读取事实，并输出命名明确的 `ContextSlot`。默认来源包括 system、persona、input、session、policy、memory、official conversation history、skills、tools、subagent、knowledge，以及插件显式写入 `ProviderRequest` 的上下文。

同一次收集中，同名 slot 不能用不同值静默覆盖。两个生产者对同一事实有分歧时直接失败。当前跨阶段 enrichment 仍存在直接修改 Pack 的路径，尚未全部收口到 `replace_slots` 或派生快照 API。

Collector 默认 required。只有明确声明 optional 的 Collector 才允许局部失败并把诊断写入 `ContextPack.meta["collector_failures"]`。当前 `MemoryCollector` 是 optional。

所谓 static Collector 只表示同一个 event、同一个 config 和同一个 `ProviderRequest` 对象内可复用，即 turn-static；它不是跨回合或全局缓存。

### PromptContextBuilder

`PromptContextBuilder` 是构建和增量丰富 `ContextPack` 的统一入口。每次合并返回新快照，不修改输入 Pack，并维护：

- `context_version`
- `collection_scopes`
- `slot_count`
- 各收集片段提供的诊断 metadata

插件 extension 也先规范化成 slot，再进入同一条构建链路。插件原有 `ProviderRequest.contexts`、`extra_user_content_parts` 和显式媒体由 Collector 收集，不在渲染后补丁式追加。消息顺序固定为 persona begin dialogs、官方历史、插件显式 contexts、当前输入。

### Target Projection

`project_context_pack(...)` 从同一份规范 Pack 生成目标视图。投影是白名单和裁剪规则，不是一次额外模型调用。

| 目标 | 当前上下文范围 |
|---|---|
| Router | 当前输入、附件摘要、最近几轮历史、群聊近期上下文、人格摘要、精简 interaction memory、插件目录 |
| Persona | 完整人格、官方对话历史、群聊上下文、memory/persona state、当前输入、待表达材料与 Core 结果 |
| Core | 官方对话历史、群聊上下文、当前输入与附件、system/policy、tools、skills、knowledge、subagent 与插件执行上下文；排除人格、interaction memory 和 effect 语义 |

Prompt extension 的 `meta.targets` 对 Router、Persona 和 Core 一致生效。未声明 targets 的普通 extension 默认属于 Core；interaction contributor 会明确标记 Persona 或 Router。

### PromptTreeBuilder

`PromptTreeBuilder` 把目标视图转换成 provider-neutral 的语义树。它负责 slot 分组、节点布局和 rendered-slot trace。`PromptRenderEngine` 只编排目标投影、建树、renderer 选择和日志，不再自己遍历业务 slot。

`BasePromptRenderer.render_*_context` 是语义布局接口。后续如要继续收紧，可以把这些方法迁到独立 layout policy；provider serializer 不负责选择业务上下文。

### Provider Renderer

Renderer 将语义树序列化为 provider 可用格式：

- `OpenAIPromptRenderer`
- `AnthropicPromptRenderer`
- `MiniMaxPromptRenderer`
- `BasePromptRenderer`

Renderer 处理 system/messages、content blocks、图片来源、tool schema 和 `OutputContract` 的协议落地。它不重新选择 Router、Persona 或 Core 的业务上下文。当前 renderer family 与 Provider 输出契约能力仍分别声明，尚缺统一能力校验。

### Apply

`ProviderRequestAdapter` 把 `RenderResult` 应用到现有 `ProviderRequest`。结构化文本块和插件显式 content parts 保持各自边界，不为了兼容单字符串字段而全局合并。

应用范围包括 system prompt、history、当前 user message、媒体 content parts，以及 output contract。工具运行时对象和 conversation 等非模型可见状态保持不变，因此 RenderResult tool schema 与实际 `func_tool` 目前仍不是同一事实来源。

## 插件扩展边界

官方 `filter.on_llm_request` 钩子继续保留。Core 主链路会先完成统一 Prompt 渲染，再把最终 `ProviderRequest` 交给该钩子；插件已有的底层请求修改不会被后续 Prompt 渲染覆盖。需要贡献模型上下文的新插件应优先注册 `PromptExtensionCollectorInterface`，只有确实需要修改最终请求、工具或 provider 参数时才使用 `on_llm_request`。

Core 主管线中的群聊上下文只通过 `conversation.group_recent` 进入统一管线，不再由 `on_llm_request` 重复注入。Dify、Coze 等尚未接入 ContextPack 的官方 Agent runner 仍通过同一个官方钩子获得等价上下文；桥接会检查 Prompt Apply 标记并跳过已完成统一渲染的请求。这是非主管线的能力兼容，不是恢复旧的双重 Prompt 来源。`apply_interaction_core_task_spec` 作为显式管理 `ProviderRequest` 的兼容接口继续导出；主链路使用 `CoreTaskCollector`，不会同时调用两者。

会话持久化使用单独生成的、去除 request context 和 Prompt 标签的用户消息，避免把内部脚手架写入官方历史。

## 输出契约

结构化输出链路为：

```text
OutputContract
  -> CompiledOutputContract
  -> ProviderRequest
  -> provider protocol or prompt-only fallback
  -> parser
```

Persona Expression 优先使用虚拟 tool call；只有 renderer/provider 明确不支持工具协议时才受控降级为 prompt-only JSON。Router 只返回固定路由词，不使用工具调用或 JSON 契约。

DeepSeek Provider 按有效 `thinking.type` 配置选择思考或非思考请求，不由 Prompt 系统替用户切换模式。两种模式都透传输出契约生成的 `tool_choice`；如果服务端拒绝该组合，应返回明确错误，而不是静默删除约束后产生不符合契约的自由文本。

## 群聊上下文

`GroupChatContext` 是动态 Prompt Extension Collector。对 Router、Persona、Core 统一管线，它只提供结构化 `conversation.group_recent`，不消费滚动记录；当前唤醒消息没有自己的 ambient record 时，会读取此前全部环境消息。对尚未接入统一管线的官方 Agent runner，它提供受 Prompt Apply 标记保护的 `on_llm_request` 兼容桥接。

## 仍需继续收口

- Provider renderer、输出契约和工具能力需要统一能力声明。
- ContextPack enrichment 需要统一派生 API，禁止静默覆盖或删除 slot。
- Context Catalog 需要从描述文件收口为真实契约，或删除未执行的声明。

具体问题与处理顺序以 `docs/Yakumo/prompt-development-plan.md` 为准。
