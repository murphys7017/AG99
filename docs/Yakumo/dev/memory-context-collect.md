# Memory Context Collect

> **文档状态：阶段实现快照。** 本文的“只供日志、不改 render”只描述 MemoryCollector v1 的提交范围。当前 memory slot 已由目标投影和统一 Render 链路消费；memory 仍只提供读取快照，写入属于 postprocess/memory service。现行边界见 `docs/Yakumo/modules/prompt.md` 与 `dev/memory/index.md`。

记录本次 `MemoryCollector` v1 的实现范围、代码改动、数据结构和验证结果。

## 范围

- 新增 `MemoryCollector`
- 从现有 `MemorySnapshot` 读取 prompt collect 可用的 memory 数据
- 写入 `ContextPack` 供日志调试
- 不改 render
- 不改 `ProviderRequest` 后续消费逻辑
- 不改 memory 系统自身的 snapshot 构建逻辑

## 本次实现

### 新增类

#### `astrbot/core/prompt/collectors/memory_collector.py`

新增 `MemoryCollector`。

职责：

- 读取当前会话的 memory snapshot
- 收集 `memory.topic_state`
- 收集 `memory.short_term`
- 收集 `memory.experiences`
- 收集 `memory.long_term_memories`
- 收集 `memory.persona_state`

主要函数：

- `collect(...)`
- `_build_topic_state_slot(...)`
- `_build_short_term_slot(...)`
- `_build_experiences_slot(...)`
- `_build_long_term_memories_slot(...)`
- `_build_persona_state_slot(...)`
- `_resolve_conversation_id(...)`
- `_resolve_query(...)`
- `_serialize_datetime(...)`

实现要点：

- 使用 `get_memory_service().get_snapshot(...)` 读取 snapshot
- `umo` 来自 `event.unified_msg_origin`
- `conversation_id` 优先来自 `provider_request.conversation.cid`
- `query` 优先来自 `provider_request.prompt`，否则回退 `event.message_str`
- 只在 snapshot 对应字段存在时产出 slot
- fail-open，snapshot 读取失败只打 warning，不中断 collect

## 修改文件

### `data/config/prompt/context_catalog.yaml`

新增 memory catalog 项：

- `memory.topic_state`
- `memory.short_term`
- `memory.experiences`
- `memory.long_term_memories`
- `memory.persona_state`

当前定义：

- `memory.topic_state`
  - category: `memory`
  - slots: `history`
  - lifecycle: `rolling`
- `memory.short_term`
  - category: `memory`
  - slots: `history`
  - lifecycle: `rolling`

### `astrbot/core/prompt/context_collect.py`

默认 collector 链扩展为：

- `SystemCollector`
- `PersonaCollector`
- `InputCollector`
- `SessionCollector`
- `PolicyCollector`
- `MemoryCollector`
- `ConversationHistoryCollector`
- `SkillsCollector`
- `ToolsCollector`
- `SubagentCollector`
- `KnowledgeCollector`

### `astrbot/core/prompt/collectors/__init__.py`

新增导出：

- `MemoryCollector`

### `astrbot/core/prompt/__init__.py`

新增导出：

- `MemoryCollector`

### `tests/unit/test_prompt_context_collect.py`

新增 memory collect 测试，并为默认 collector 链测试增加 memory service patch。

新增测试：

- `test_collect_context_pack_collects_memory_slots_from_snapshot()`
- `test_collect_context_pack_memory_skips_empty_snapshot()`
- `test_collect_context_pack_memory_uses_none_conversation_id_without_request()`
- `test_collect_context_pack_memory_fail_open_when_snapshot_request_raises()`

调整测试：

- `test_collect_context_pack_default_collectors_include_session_collector()`
  - 默认 collector 列表新增 `MemoryCollector`

## 当前 slot 结构

### `memory.topic_state`

value:

- `umo`
- `conversation_id`
- `current_topic`
- `topic_summary`
- `topic_confidence`
- `last_active_at`

meta:

- `snapshot_field=topic_state`
- `has_value=true`

### `memory.short_term`

value:

- `umo`
- `conversation_id`
- `short_summary`
- `active_focus`
- `updated_at`

meta:

- `snapshot_field=short_term_memory`
- `has_value=true`

### `memory.experiences`

value:

- `count`
- `items`

meta:

- `snapshot_field=experiences`
- `has_value=true`
- `count=<int>`

### `memory.long_term_memories`

value:

- `count`
- `items`

meta:

- `snapshot_field=long_term_memories`
- `has_value=true`
- `count=<int>`

### `memory.persona_state`

value:

- `state_id`
- `scope_type`
- `scope_id`
- `persona_id`
- `familiarity`
- `trust`
- `warmth`
- `formality_preference`
- `directness_preference`
- `updated_at`

meta:

- `snapshot_field=persona_state`
- `has_value=true`

## snapshot 读取边界

本次 collector 只读取当前 snapshot 已稳定提供的数据：

- `topic_state`
- `short_term_memory`
- `experiences`
- `long_term_memories`
- `persona_state`

## 设计思路

- 先对接已有 memory read path，不在 prompt collect 阶段重复实现 memory 逻辑
- 当前 collector 直接复用 snapshot 已暴露的短期、中长期和 persona state 结果
- value 使用结构化 dict，方便日志观察，也方便后续 renderer 直接消费
- `conversation_id` 和 `query` 都保持“尽量传入”，为后续 snapshot 扩展预留接口
- 保持 collect-only，不把 memory 渲染策略混进本次实现

## 验证

执行：

- `uv run pytest tests/unit/test_prompt_context_collect.py`
- `uv run ruff format astrbot/core/prompt/collectors/memory_collector.py astrbot/core/prompt/collectors/__init__.py astrbot/core/prompt/__init__.py astrbot/core/prompt/context_collect.py tests/unit/test_prompt_context_collect.py`
- `uv run ruff check astrbot/core/prompt/collectors/memory_collector.py astrbot/core/prompt/collectors/__init__.py astrbot/core/prompt/__init__.py astrbot/core/prompt/context_collect.py tests/unit/test_prompt_context_collect.py`

结果以实际命令输出为准。
