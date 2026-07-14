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

同一次收集中，同名 slot 不能用不同值静默覆盖。两个生产者对同一事实有分歧时直接失败；跨阶段确实需要刷新某个 slot 时，调用方必须通过 `replace_slots` 明确声明。

Collector 默认 required。只有明确声明 optional 的 Collector 才允许局部失败并把诊断写入 `ContextPack.meta["collector_failures"]`。当前 `MemoryCollector` 是 optional。

所谓 static Collector 只表示同一个 event、同一个 config 和同一个 `ProviderRequest` 对象内可复用，即 turn-static；它不是跨回合或全局缓存。

### PromptContextBuilder

`PromptContextBuilder` 是构建和增量丰富 `ContextPack` 的统一入口。每次合并返回新快照，不修改输入 Pack，并维护：

- `context_version`
- `collection_scopes`
- `slot_count`
- 各收集片段提供的诊断 metadata

插件 extension 也先规范化成 slot，再进入同一条构建链路。插件原有 `ProviderRequest.contexts` 与 `extra_user_content_parts` 由 `ExplicitContextCollector` 保留，不在渲染后补丁式追加。

### Target Projection

`project_context_pack(...)` 从同一份规范 Pack 生成目标视图。投影是白名单和裁剪规则，不是一次额外模型调用。

| 目标 | 当前上下文范围 |
|---|---|
| Router | 当前输入、附件摘要、最近几轮历史、群聊近期上下文、人格摘要、精简 interaction memory、插件目录 |
| Persona | 完整人格、官方对话历史、群聊上下文、memory/persona state、当前输入、待表达材料与 Core 结果 |
| Core | 官方对话历史、群聊上下文、当前输入与附件、system/policy、tools、skills、knowledge、subagent 与插件执行上下文；不读取人格和 effect 语义 |

Prompt extension 的 `meta.targets` 对 Router、Persona 和 Core 一致生效。未声明 targets 的普通 extension 默认属于 Core；interaction contributor 会明确标记 Persona 或 Router。

### PromptTreeBuilder

`PromptTreeBuilder` 把目标视图转换成 provider-neutral 的语义树。它负责 slot 分组、节点布局和 rendered-slot trace。`PromptRenderEngine` 只编排目标投影、建树、renderer 选择和日志，不再自己遍历业务 slot。

当前仍保留 `BasePromptRenderer.render_*_context` 扩展点，以兼容已有自定义布局。后续如要继续收紧，可以把这些语义布局方法迁到独立 layout policy；这不是当前 provider serializer 的职责扩张理由。

### Provider Renderer

Renderer 将语义树序列化为 provider 可用格式：

- `OpenAIPromptRenderer`
- `AnthropicPromptRenderer`
- `MiniMaxPromptRenderer`
- `BasePromptRenderer`

Renderer 处理 system/messages、content blocks、图片来源、tool schema 和 `OutputContract` 的协议落地。它不重新选择 Router、Persona 或 Core 的业务上下文。

### Apply

`ProviderRequestAdapter` 把 `RenderResult` 应用到现有 `ProviderRequest`。结构化文本块和插件显式 content parts 保持各自边界，不为了兼容单字符串字段而全局合并。

应用范围包括 system prompt、history、当前 user message、媒体 content parts，以及 output contract。工具运行时对象和 conversation 等非模型可见状态保持不变。

会话持久化使用单独生成的、去除 request context 和 Prompt 标签的用户消息，避免把内部脚手架写入官方历史。

## 主 Agent 模式

- `apply_visible`：当前默认，RenderResult 应用到 live request。
- `shadow`：应用到克隆 request，仅记录差异。
- `legacy`：显式保留旧链路。

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

## 群聊上下文

`GroupChatContext` 是动态 Prompt Extension Collector。它提供结构化 `conversation.group_recent`，不消费滚动记录；当前唤醒消息没有自己的 ambient record 时，会读取此前全部环境消息。非 `apply_visible` 模式仍保留官方 `on_llm_request` 兼容出口，并通过 consumed 标记避免双重注入。

## 仍需继续收口

- `astr_main_agent.py` 仍承担能力装配和 request 生命周期，尚未完全拆成可替换执行器端口。
- Base renderer 中的语义布局兼容方法仍可进一步迁移到独立 layout policy。
- 真实平台的多模态、长历史预算和 provider token 上限仍需持续验证。
