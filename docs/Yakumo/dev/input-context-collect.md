# Input Context Collect

> **文档状态：阶段实现快照。** 本文的“不改变 ProviderRequest”“不接入 render”等边界只描述 InputCollector 首次落地时的提交范围，不是当前状态。当前 input 已进入 collect/build/project/profile/layout/render/apply 主链路；现行边界见 `docs/Yakumo/modules/prompt.md`。

本文件记录本次 `InputCollector` 链路开发的实际改动、接入位置、数据结构、约束和验证结果。

## 本次目标

- 完成 `input` 类 context 的 collect
- 将当前输入整理为 `ContextPack`
- 先用于日志调试和后续 render 准备
- 不改变现有 `ProviderRequest` 的下游渲染和执行行为
- 不在本次实现中加入 prompt render、image caption、file extract 注入

## 本次改动摘要

- 新增 `InputCollector`
- 将默认 collector 链路扩展为 `PersonaCollector -> InputCollector`
- 让 collector 接口显式接收 `provider_request`
- 收集当前文本、当前图片、引用文本、引用图片、文件
- 为输入数据建立统一的结构化 value 形状
- 补充 input collect 的单元测试

## 新增文件

### `astrbot/core/prompt/collectors/input_collector.py`

新增 `InputCollector`。

职责：

- 收集 `input.text`
- 收集 `input.images`
- 收集 `input.quoted_text`
- 收集 `input.quoted_images`
- 收集 `input.files`

主要内部函数：

- `_resolve_effective_text(...)`
- `_collect_current_images(...)`
- `_collect_files_from_components(...)`
- `_collect_reply_payloads(...)`
- `_build_image_record(...)`
- `_build_image_record_from_ref(...)`
- `_build_file_record(...)`
- `_get_quoted_message_parser_settings(...)`
- `_infer_transport(...)`

核心设计：

- `input.text` 优先取 `provider_request.prompt`
- 没有 `provider_request.prompt` 时，回退到 `event.message_str`，并按 `provider_wake_prefix` 裁剪
- 当前图片直接读取 `Image` 组件原始字段，不做压缩和 caption
- 当前文件直接读取 `File` 组件原始字段，不调用 `get_file()` 触发下载
- 引用文本复用 `extract_quoted_message_text(...)`
- 引用图片分两路：
  - reply chain 中直接带图片时，记为 `resolution=embedded`
  - reply-id-only 或占位场景回退 `extract_quoted_message_images(...)`，记为 `resolution=fallback`
- 引用文件只读取 reply chain 中已经存在的 `File` 组件
- 失败策略为 fail-open，局部失败只记录 warning，不中断整体 collect

## 修改文件

### `astrbot/core/prompt/context_collect.py`

本次修改：

- 新增 `InputCollector` 导入
- 修改 `_default_collectors()`
- 默认 collector 顺序变为：
  - `PersonaCollector`
  - `InputCollector`
- 在执行 `collector.collect(...)` 时显式透传 `provider_request`

结果：

- `collect_context_pack(...)` 现在会在原有 persona collect 基础上继续收集 input context
- `ContextPack.meta["collectors"]` 中会包含 `InputCollector`

### `astrbot/core/prompt/interfaces/context_collector_inferface.py`

本次修改：

- `ContextCollectorInterface.collect(...)` 新增参数：
  - `provider_request: ProviderRequest | None = None`

目的：

- collector 不再需要隐式依赖 `event.get_extra("provider_request")`
- collect 数据来源更明确
- 后续新增 collector 时接口保持统一

### `astrbot/core/prompt/collectors/persona_collector.py`

本次修改：

- `PersonaCollector.collect(...)` 增加 `provider_request` 参数
- 优先使用显式传入的 `provider_request`
- 只有没有传入时才回退到 `event.get_extra("provider_request")`

目的：

- 对齐新的 collector 接口
- 降低对 event extra 的隐式耦合

### `astrbot/core/prompt/collectors/__init__.py`

本次修改：

- 导出 `InputCollector`

### `astrbot/core/prompt/__init__.py`

本次修改：

- 导出 `InputCollector`

### `tests/unit/test_prompt_context_collect.py`

本次新增测试：

- `test_collect_context_pack_collects_effective_input_text_and_attachments()`
- `test_collect_context_pack_collects_attachment_only_input_without_text()`
- `test_collect_context_pack_prefers_provider_request_prompt_for_input_text()`
- `test_collect_context_pack_collects_quoted_input_payloads()`
- `test_collect_context_pack_collects_fallback_quoted_images_with_limit()`
- `test_collect_context_pack_fail_open_when_a_collector_raises()`

覆盖点：

- 文本输入 collect
- wake prefix 裁剪
- 附件-only 输入
- 当前图片和文件 collect
- 引用文本 collect
- 引用图片 embedded/fallback collect
- 引用文件 collect
- fallback 图片数量限制
- fail-open collector 行为

## 当前 input slot 结构

### `input.text`

value:

- `str`

meta:

- `source_field`

### `input.images`

value:

- `list[dict]`

单项结构：

- `ref`
- `transport`
- `source`

其中：

- `source = "current"`
- `transport` 可能为：
  - `url`
  - `file`
  - `path`
  - `base64`
  - `resolved_path`

### `input.quoted_text`

value:

- `str`

说明：

- 保存原始引用正文
- 不带 `<Quoted Message>` 包装

### `input.quoted_images`

value:

- `list[dict]`

单项结构：

- `ref`
- `transport`
- `source`
- `resolution`
- `reply_id`

其中：

- `source = "quoted"`
- `resolution` 为：
  - `embedded`
  - `fallback`

### `input.files`

value:

- `list[dict]`

单项结构：

- `name`
- `file`
- `url`
- `source`
- `reply_id`

其中：

- `source` 为：
  - `current`
  - `quoted`

## 本次实现边界

- 不修改 `build_main_agent()` 中原有 `ProviderRequest` 组装逻辑
- 不把 `ContextPack` 反向渲染回 `req`
- 不改 persona 渲染逻辑
- 不改 quoted message 的 provider 注入文本格式
- 不改 image caption 行为
- 不改 file extract 行为

## 一个需要说明的点

`data/config/prompt/context_catalog.yaml` 在当前工作区已经是预期的 input 定义状态，包括：

- `input.text.required = false`
- input notes 已说明附件-only 场景
- `input.images`
- `input.quoted_images`
- `input.files`

但这个文件当前不在 git 跟踪中，因此本次提交不会包含它。

## 验证结果

本次执行：

- `uv run ruff format .`
- `uv run pytest tests/unit/test_prompt_context_collect.py`
- `uv run ruff check astrbot/core/prompt/context_collect.py astrbot/core/prompt/collectors/input_collector.py astrbot/core/prompt/collectors/persona_collector.py astrbot/core/prompt/collectors/__init__.py astrbot/core/prompt/interfaces/context_collector_inferface.py astrbot/core/prompt/__init__.py tests/unit/test_prompt_context_collect.py`

结果：

- `tests/unit/test_prompt_context_collect.py` 全部通过
- 本次涉及文件的 `ruff check` 通过

额外说明：

- `uv run ruff check .` 仍然会因 `astrbot/core/prompt/context_catalog.py` 和 `astrbot/core/prompt/context_types.py` 中的历史问题报错
- 这些不是本次 `InputCollector` 改动引入的问题

## 本次思路

- 先把输入数据从现有主链路中抽出为独立 collect 阶段
- 只做“准备好数据”，不提前进入 render 阶段
- 结构上优先保证：
  - 可日志观察
  - 可测试
  - 可被后续 renderer 直接消费
- 对引用消息保持和现有主链路一致的主要语义，但不复用 provider-facing 的装饰文本
- 对文件和图片尽量保留原始引用，避免 collect 阶段引入额外副作用
