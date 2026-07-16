# Prompt Development Plan

## 文档状态

这是当前 Prompt 子系统的后续收口计划，不是早期 Selector 方案。当前实现和功能边界以 `docs/Yakumo/modules/prompt.md` 为准。

## 已稳定的主链路

```text
collect facts
  -> build canonical or derived ContextPack
  -> project by target
  -> apply target-local PromptRenderProfile
  -> build provider-neutral tree through PromptLayoutInterface
  -> serialize with Provider Renderer
  -> apply RenderResult to ProviderRequest
```

已经确认：

- Router、Core Planner、Persona 和 Core 使用同一事实模型与隔离投影。
- Router 与 Planner 独立，不共享模型决策。
- Interaction 跨阶段 enrichment 使用 `PromptContextBuilder(base=...)`，不直接修改共享 Pack。
- 目标 system/request prompt、输出契约和隐藏规则由 `PromptRenderProfile` 提供。
- `PromptTreeBuilder` 不再依赖选中的 Provider Renderer 决定布局。
- Main Agent 模型可见输入只来自 Prompt 管线；官方 `on_llm_request` 作为 Apply 后低层兼容钩子保留。
- 插件显式 contexts/content parts、群聊上下文和 CoreTaskSpec 都进入 Collector/Builder，而不是在渲染后重复追加。

## 当前问题与处理顺序

### 1. 完成 Layout 实现的物理迁移

`PromptLayoutInterface` 已收口为稳定的 `render_group(...)` 接口，Builder 不再动态查找 `render_<group>_context`。当前剩余工作是把 `DefaultPromptLayout` 内部委托的 provider-neutral 落位规则从 `BasePromptRenderer` 迁出。

处理：

- 把 provider-neutral 的 slot 落位和树构建规则迁入独立 Layout 实现。
- 保留 Base Renderer 的序列化职责，删除 Layout 对 Renderer 实例的实现依赖。

### 2. 统一 Provider Prompt Capability

renderer family、原生 tool call、输出契约策略和受控降级能力目前分别声明，可能出现“选对 Renderer 但 Provider 不支持契约”的组合。

处理：建立通用 capability 描述和启动/请求期校验，不按 Provider ID 打补丁。

### 3. 统一工具事实来源

`RenderResult.tool_schema` 与 `ProviderRequest.func_tool` 当前分离。Prompt 可以渲染一个 schema，但实际 Tool Loop 仍以 `func_tool` 为准。

处理：选择一个 capability snapshot 作为工具可见性和执行注册的共同来源；在此之前明确 `tool_schema` 只是渲染/诊断结果。

### 4. 强化 ContextPack 派生契约

Interaction 已不再直接修改 Pack，但 `ContextPack` 公开类型仍可静默覆盖 slot，其他调用方仍可能绕过 Builder。

处理：

- 将直接修改限制在 Collector/Builder/Render 内部。
- 为替换、隐藏和派生提供显式 API 与审计 metadata。
- 逐步让目标视图只读，避免插件持有并原地修改共享快照。

### 5. 修复 DeepSeek 首轮 Marker 生命周期

当前首轮判断仍主要依赖当前 Pack 历史与 event extra，不是持久会话状态。

处理：结合官方 conversation history 和会话级状态判断，只把 Marker 作为 Profile 输入后缀，不污染规范事实。

### 6. 处理 Catalog 的虚假约束

Catalog 当前主要用于声明和未知 slot 告警，required、multiple、lifecycle、redaction 并未全部执行。

`llm_exposure="never"` 已在显式 Target Projection 和无 target 的普通 Main Agent 渲染入口统一过滤。Catalog 的其他声明仍未全部成为运行时约束。

处理：继续判断 Catalog 的 required、multiple、lifecycle、redaction 应成为可执行契约还是删除；敏感信息默认仍应在 Collector 产生前完成最小化。

### 7. 最后优化性能与预算

边界稳定后再处理：

- 只并发确认无副作用且相互独立的动态 Collector。
- 对目标投影增加可观测的 token/字符预算，而不是重新引入 LLM Selector。
- 缓存仍要求明确 event/session/global 生命周期和失效协议。

## 非目标

- 不重新引入 LLM Selector。
- 不让业务模块或插件绕过 Collector 直接拼模型 Prompt。
- 不针对单个插件修改 Router、Planner 或通用输出契约。
- 不让 Prompt 系统写 memory、执行工具、发送消息或理解 Motion/Live2D 语义。
- 不删除官方插件钩子；只明确它们与统一事实管线的先后和适用范围。
