# Render Engine Implementation Spec

记录当前 render 子系统已经落地的实现骨架，以及每个核心类的职责边界。

## 实现目标

本轮实现的目标不是完成最终 prompt 样式，而是先把 render 层的稳定协议搭起来。

本轮已经确认的关系：

- `renderer` 定义规则
- `engine` 执行规则
- `builder` 是 engine 的内部能力
- `selector` 先保留最小占位接口

## 已落地组件

### `BasePromptRenderer`

文件：

- `astrbot/core/prompt/render/interfaces.py`
- `astrbot/core/prompt/render/base_renderer.py`

定位：

- 当前默认可直接使用的基础 renderer
- 不是纯接口，而是一个可工作的基础实现

当前主要能力：

1. `get_name()`
   - 返回稳定 renderer 名称，当前为 `base`
2. `get_root_tag()`
   - 返回 prompt tree 根节点 tag，当前为 `prompt`
3. `get_enabled_slot_groups()`
   - 返回当前 renderer 启用的逻辑分组
   - 默认启用全部 group
4. `get_node_structure()`
   - 返回逻辑分组到 tree 节点路径的映射
5. `render_prompt_tree(...)`
   - 将已经构建好的 prompt tree 转成 `RenderResult`
6. `render_system_context()` / `render_persona_context()` / ...
   - 提供各逻辑分组的默认渲染入口
7. `serialize_group_slots()`
   - 统一序列化一个 group 下的全部 slot
8. `serialize_slot_value()`
   - 序列化单个 slot，生成 `SerializedRenderValue`
9. `render_serialized_value()`
   - 将结构化中间值转成节点文本

### `SerializedRenderValue`

文件：

- `astrbot/core/prompt/render/interfaces.py`

作用：

- 表示 renderer 序列化后的中间值
- 让 slot value 先进入结构化 render object，而不是直接退化成字符串

当前字段：

- `slot_name`
- `group`
- `tag`
- `kind`
- `value`
- `meta`

当前 `kind` 主要包括：

- `text`
- `mapping`
- `sequence`
- `scalar`

### `PromptRenderEngine`

文件：

- `astrbot/core/prompt/render/engine.py`

定位：

- render 阶段的统一执行器

当前执行流程：

1. `_select_context_pack(...)`
   - 调用 selector，当前默认 passthrough
2. `_resolve_renderer(...)`
   - 选择 renderer，当前默认 `BasePromptRenderer`
3. `_group_slots(...)`
   - 按 slot name 前缀分组
4. `_build_prompt_tree(...)`
   - 根据 renderer 定义的 group 和 node structure 构树
5. `_render_group_context(...)`
   - 调用 `render_xxx_context()` 渲染各个 group
6. `_attach_engine_metadata(...)`
   - 将 engine 层调试信息写入 `RenderResult.metadata`

engine 当前明确不负责：

- 不定义 section 样式
- 不决定 slot 的文本格式
- 不处理 provider-specific payload 细节

### `PromptBuilder` / `PromptNode` / `NodeRef`

文件：

- `astrbot/core/prompt/render/prompt_tree.py`

作用：

- 作为 engine 内部的 prompt tree 构建工具

当前支持的能力：

- 创建 tag 节点
- 创建 container 节点
- 添加文本节点
- `include()`
- `extend()`
- `build()` 输出文本
- `debug_tree()` 输出调试树结构

`PromptNode` 当前保留：

- `text`
- `priority`
- `children`
- `parent`
- `enabled`
- `meta`

### `RenderResult`

文件：

- `astrbot/core/prompt/render/interfaces.py`

作用：

- 承载 render 阶段最终输出

当前字段：

- `prompt_tree`
- `system_prompt`
- `messages`
- `tool_schema`
- `metadata`

当前阶段里，最主要的输出仍然是：

- `prompt_tree`
- `system_prompt`
- `metadata`

### `PassthroughPromptSelector`

文件：

- `astrbot/core/prompt/render/selector.py`

作用：

- 作为 selector 占位实现
- 当前直接返回原始 `ContextPack`

这样做的意义是：

- render 流程已经完整
- 但不会因为 selector 逻辑未定而阻塞后续开发

## 当前分组规则

engine 当前按 slot name 前缀分组：

- `system.* -> system`
- `persona.* -> persona`
- `policy.* -> policy`
- `input.* -> input`
- `session.* -> session`
- `conversation.* -> conversation`
- `knowledge.* -> knowledge`
- `capability.* -> capability`
- `memory.* -> memory`
- `extension.* -> extension`

`BasePromptRenderer` 默认启用全部这些 group。

## 插件 Prompt Extension V1

本轮新增了一条插件向 prompt 主流程贡献结构化上下文的通路，用来替代直接树补丁或任意 `on_llm_request` 拼接文案的方式。

### 目标

- 插件只负责“提供什么内容”
- collect 层负责“收集并结构化聚合”
- renderer 负责“挂到哪一类节点、怎么渲染”
- 不允许插件指定任意 prompt tree 内部 path

### 插件接口

插件通过 `Context.register_prompt_extension_collector(...)` 显式注册 collector。

collector 需要实现：

- `PromptExtensionCollectorInterface`
- `plugin_id`
- `priority`
- `collect(...) -> list[PromptExtension]`

`PromptExtension` 当前固定字段：

- `plugin_id`
- `mount`
- `title`
- `value`
- `value_kind`
- `order`
- `meta`

### Collect 聚合规则

collect 阶段不会为每条扩展生成动态 slot，而是固定聚合为 6 个 slot：

- `extension.system`
- `extension.context`
- `extension.input`
- `extension.conversation`
- `extension.memory`
- `extension.capability`

每个 slot 的 `value` 结构固定为：

- `format: "prompt_extensions_v1"`
- `mount`
- `items`

### Render 挂载规则

`BasePromptRenderer.render_extension_context()` 当前固定把各 mount 挂到这些节点：

- `system -> system/extensions`
- `context -> context/extensions`
- `input -> user_input/extensions`
- `conversation -> system/conversation_extensions`
- `memory -> context/memory/extensions`
- `capability -> system/capability/extensions`

其中：

- `conversation` 在 V1 先走 system 侧说明，不生成 synthetic 历史消息
- `context` 用于当前请求动态事实，随 `context/extensions` 编译为 history 后、memory/knowledge 前的 `_no_save` user context message
- `memory` 随 `context/memory` 编译为 history 后、current input 前的 `_no_save` user context message，不进入 `system_prompt`
- `input` 会在 `_compile_user_input_message()` 中被单独编译成一个 text content part
- 同一 mount 下按 `plugin_id` 聚合成“一个插件一个节点”
- 原始 `plugin_id` 会作为可见子节点保留，便于插件认领自身输出

## 当前默认序列化规则

`serialize_slot_value()` 当前默认策略：

- `knowledge` group 下如果 value 是 `dict` 且存在 `text`，优先直接取 `text`
- 普通非空字符串 -> `kind="text"`
- `dict` -> `kind="mapping"`
- `list` -> `kind="sequence"`
- `bool/int/float` -> `kind="scalar"`
- `None` -> 不产出序列化结果
- 其他对象 -> `kind="scalar"`，值为 `str(value)`

`render_serialized_value()` 当前默认策略：

- `text` 直接输出文本
- 其余类型用 `json.dumps(..., ensure_ascii=False, sort_keys=True, default=str)` 输出

这样做已经避免了把结构化对象直接渲染成 Python `repr`。

## 当前测试覆盖

当前 render 层已有测试覆盖：

- `tests/unit/test_prompt_selector.py`
- `tests/unit/test_prompt_tree_renderer.py`

重点验证内容包括：

- `PromptBuilder` 能正确构建嵌套 tag tree
- `include()` / `extend()` 行为正常
- `BasePromptRenderer` 默认启用全部 groups
- `BasePromptRenderer` 返回基础 node structure
- `dict` / `list` slot 先进入结构化序列化路径
- `PromptRenderEngine` 能按 renderer 定义构建 prompt tree
- 派生 renderer 可以覆写 serializer，而不需要修改 engine

## 当前限制

当前实现仍然是 render 骨架，不代表最终渲染策略已经完成。

目前仍未完成的部分：

- `llm_exposure` 的真正过滤策略
- 各 section 的精细化渲染格式
- provider-specific renderer
- 将 `RenderResult` 接回真实主链路请求拼装
- 针对 multimodal / tools / subagent 的专门输出形态优化

## 后续扩展点

下一阶段最自然的扩展方式是继承 `BasePromptRenderer`。

典型扩展点包括：

- 覆盖 `get_enabled_slot_groups()`
- 覆盖 `get_node_structure()`
- 覆盖 `serialize_slot_value()`
- 覆盖 `render_xxx_context()`
- 覆盖 `render_prompt_tree()` 生成 provider 更合适的结果

也就是说，后续的重点不是推翻当前实现，而是在当前协议上继续细化各 provider 的规则。
