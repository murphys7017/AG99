# Render Engine Implementation Spec

## 文档状态

本文描述当前 Render 子系统的真实实现。早期 `Selector -> Renderer -> Engine` 方案已废止；历史背景可查看 `render-engine-plan.md`，但不能作为当前 API 依据。

## 调用关系

```text
ContextPack
  -> optional PromptTarget projection
  -> optional PromptRenderProfile
  -> PromptTreeBuilder(DefaultPromptLayout)
  -> selected Provider Renderer
  -> RenderResult
```

`PromptRenderEngine` 是编排器，不收集业务事实，也不执行模型调用。

## 核心类型

### `PromptRenderEngine`

职责：

1. 对规范 Pack 做目标投影。
2. 在投影副本上应用 Render Profile。
3. 解析 provider 的 `prompt_renderer_family`。
4. 用独立 Layout 和 `PromptTreeBuilder` 构建语义树。
5. 让选中的 Renderer 编译树。
6. 附加 target、layout、renderer、slot、output contract 等诊断 metadata。

不负责：Collector 调度、Router/Planner 决策、Provider 私有请求执行、工具注册和响应解析。

### `PromptRenderProfile`

目标局部策略，字段包括：

- `name`
- `system_prompt`
- `request_prompt`
- `output_contract`
- `input_text_suffix`
- `hidden_slot_names`

Engine 会先深拷贝目标 Pack，再应用 Profile。`system_prompt` 替换 `system.base`；suffix 只作用于字符串 `input.text`；hidden slot 是精确名称过滤。Profile 不修改输入 Pack。

### `PromptLayoutInterface` / `DefaultPromptLayout`

Layout 决定：

- root tag
- 启用的逻辑 groups
- group 到节点路径的映射
- session 是否并入 system
- 各 group 的 slot 如何落入 PromptTree

Protocol 显式声明查询方法与统一的 `render_group(...)` 落位入口，Builder 不再动态调用 `render_<group>_context`。`DefaultPromptLayout` 当前仍通过这个入口委托 `BasePromptRenderer` 复用旧的 provider-neutral 落位方法；公共契约已经稳定，默认布局实现尚未完全迁出 Renderer 类。

### `PromptTreeBuilder`

Builder 只负责：

- 按 slot 名前缀分组。
- 按 Layout 建立节点路径。
- 调用 Layout 落位。
- 写入 rendered slots/groups、layout 和 output contract metadata。

Builder 不选择目标、不解析 provider family、不编译最终 messages。

### `PromptBuilder` / `PromptNode` / `NodeRef`

这是 provider-neutral 的树形中间表示，支持 tag、container、text、include、extend、build 和 debug tree。树节点可同时携带正文与结构化 metadata。

### Provider Renderer

当前 family：

- `base`
- `openai`
- `anthropic`
- `minimax`

Renderer 编译完成的树，产出 system prompt、messages、媒体 content blocks、tool schema 和 compiled output contract。它不读取未进入树的业务 slot，也不改变目标投影。

### `RenderResult`

字段：

- `prompt_tree`
- `system_prompt`
- `messages`
- `tool_schema`
- `output_contract`
- `compiled_output_contract`
- `metadata`
- `request_prompt`

`request_prompt` 追加在数据类字段末尾，以保持旧位置参数构造顺序。

完整树不会复制到 metadata 或 DEBUG 结构日志；诊断只输出截断后的 Prompt/messages 预览、slot 名称和计数。

## Request Adapter 边界

`ProviderRequestAdapter` 不属于 Engine，但承接 Render 输出：

- 无 `request_prompt` 时，最后一条 user message 成为请求 prompt。
- 有 `request_prompt` 时，全部 messages 成为 contexts，Profile 命令成为请求 prompt。
- Adapter 重建模型可见字段，但保留 `func_tool`、provider、conversation 和其他运行时对象。

`RenderResult.tool_schema` 不会自动写入 `func_tool`。实际可执行工具仍由 Main Agent 装配。

## 扩展边界

- 新事实：实现 Collector 或插件 Prompt Extension Collector。
- 新目标视图：修改确定性的 `PromptTarget` 投影规则。
- 新目标指令：使用 `PromptRenderProfile`。
- 新语义布局：实现 `PromptLayoutInterface`，不要修改 Provider Renderer 来选择业务数据。
- 新 Provider 格式：实现 Provider Renderer 并声明 `prompt_renderer_family`。
- 新执行工具：走能力注册/`func_tool`，不要只写 Prompt tool schema。

## 诊断要求

Render metadata 至少应可看到：

- `prompt_target`
- `render_profile`
- `layout_name`
- `renderer_name`
- `source_slot_names` / `selected_slot_names`
- `rendered_slots` / `rendered_groups`
- output contract strategy/degradation

日志预览不得被当作事实来源，也不能重新注入 Router 或历史。
