# Render Engine Plan

记录当前 prompt render 子系统的目标关系、职责边界和下一阶段演进方向。

## 当前结论

目前已经明确采用三层关系：

- `renderer` 负责定义规则
- `engine` 负责调度执行
- `builder` 作为 `engine` 内部的构树工具

这里的重点不是先把所有 section 的最终文案定死，而是先把渲染流程的骨架和扩展点搭稳。

## 目标

新的 render 层要解决的问题，不是“再拼一个大 `system_prompt`”，而是让 collect 后的结构化数据有统一出口：

1. selector 先决定本轮要不要裁剪
2. renderer 决定哪些逻辑分组启用、挂到哪棵树上、如何序列化
3. engine 负责调度 renderer 并构建 prompt tree
4. 最后统一得到 `RenderResult`

简化表达：

`Collect -> Select -> Render -> Execute`

其中当前阶段已经进入：

- collect 基本成型
- selector 已有占位接口
- render engine 基础骨架已落地
- provider-specific renderer 已实现（AnthropicPromptRenderer、MiniMaxPromptRenderer）

## 核心职责划分

### 1. Renderer

`BasePromptRenderer` 是当前可直接使用的基础 renderer。

它负责：

- 声明启用哪些逻辑分组
- 声明这些分组在 prompt tree 中的节点路径
- 提供各个分组的默认渲染入口
- 提供统一的 slot 序列化能力
- 定义最终 `RenderResult` 的基础输出形态

后续如果需要面向不同模型提供商做差异优化，可以继续派生：

- `OpenAIRenderer`
- `AnthropicRenderer`
- `GeminiRenderer`

这些派生 renderer 的主要扩展方式应该是：

- 关闭部分 group
- 调整 node structure
- 覆盖局部 `render_xxx_context()`
- 覆盖 slot serializer

### 2. Engine

`PromptRenderEngine` 是 render 阶段的执行器。

它负责：

- 调用 selector
- 选择 renderer
- 按 slot name 前缀分组
- 根据 renderer 提供的 node structure 建树
- 调用 renderer 的 group render 方法
- 汇总为 `RenderResult`

engine 不定义 prompt 规则，只执行 prompt 规则。

### 3. Builder

`PromptBuilder` / `PromptNode` / `NodeRef` 属于 engine 内部能力。

它们负责：

- 创建 tag/container/text 节点
- 支持 include / extend
- 保留 priority / enabled / meta
- 将树结构 build 成最终文本

builder 不承担策略定义职责，也不关心某个 slot 应该如何渲染。

### 4. Selector

当前 selector 只保留稳定接口，不做复杂裁剪逻辑。

当前默认策略：

- 接收 `ContextPack`
- 原样返回

后续可以在 selector 中继续接入：

- token budget
- llm exposure 过滤
- history window
- memory / knowledge 裁剪
- provider profile 下的输入选择策略

## 当前基础实现

目前基础 render 子系统已经包括：

- `BasePromptRenderer`
- `PromptRenderEngine`
- `PromptBuilder`
- `PromptNode`
- `NodeRef`
- `RenderResult`
- `SerializedRenderValue`
- `PassthroughPromptSelector`

### 当前启用的逻辑分组

`BasePromptRenderer` 默认启用全部已收集的大类：

- `system`
- `persona`
- `policy`
- `input`
- `session`
- `conversation`
- `knowledge`
- `capability`
- `memory`

### 当前默认节点结构

当前基础结构保持一组一节点：

- `system -> system`
- `persona -> persona`
- `policy -> policy`
- `input -> input`
- `session -> session`
- `conversation -> conversation`
- `knowledge -> knowledge`
- `capability -> capability`
- `memory -> memory`

这只是基础骨架，不代表最终 provider 最优结构已经确定。

## 当前序列化方向

render 层已经开始承担“通用序列化器”职责，而不是直接对所有 slot 值执行 `str(value)`。

当前默认规则：

- `str` -> `text`
- `dict` -> `mapping`
- `list` -> `sequence`
- `bool/int/float` -> `scalar`
- `None` -> 跳过
- 其他对象 -> 退化成 `scalar(str(value))`

这样做的意义是：

- collector 保持结构化输出
- renderer 可以基于结构化中间值继续定制
- engine 不需要知道每种 slot 的具体文本格式

## 当前边界

本轮 render 子系统明确不做：

- 不替换 `astr_main_agent.py` 真实请求拼装逻辑
- 不细化每个 section 的最终文案模板
- 不在 collect 阶段回头修改 slot 协议
- 不实现完整的 `llm_exposure` 过滤策略，只预留后续接口空间

已完成的 provider-specific renderer：

- `AnthropicPromptRenderer`：输出 Anthropic 原生 content blocks、tool schema（`input_schema`）、image source（base64/url）
- `MiniMaxPromptRenderer`：继承 Anthropic renderer，适配 MiniMax Token Plan 的 Anthropic 兼容 API

## 下一步

下一阶段的重点不再是补骨架，而是细化各 section 的局部渲染规则，优先级建议为：

1. `input` / `session`
2. `conversation`
3. `capability`
4. `memory`
5. 新增更多 provider-specific renderer（Gemini、VolcEngine Ark 等）

总体原则保持不变：

- collect 负责准备数据
- selector 负责决定取舍
- renderer 负责定义规则
- engine 负责执行规则
