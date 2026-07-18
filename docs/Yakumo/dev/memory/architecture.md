# Memory Architecture

本文描述当前 `astrbot/core/memory` 的源码边界。Memory Service 是 Yakumo 唯一的抽象记忆
系统；Interaction 不维护 session JSON 记忆副本，官方 Conversation 仍负责精确对话历史。

## 所有权

```text
Finalized Turn Material
  -> Postprocess(AFTER_TURN_COMPLETED)
  -> MemoryPostProcessor
  -> MemoryService.update_from_postprocess()
  -> TurnRecord / Short-Term Update
  -> optional Consolidation / Experience / Long-Term Promotion
```

- Interaction 或普通 Pipeline 负责形成稳定回合材料。
- `MemoryPostProcessor` 负责把回合材料转换为 `MemoryUpdateRequest`。
- `MemoryService` 负责写入编排和读取快照。
- `MemoryStore` 是 SQLite 结构化真源。
- `Prompt MemoryCollector` 只读取 `MemorySnapshot`，不写入 Memory。

## 写入链路

`MemoryPostProcessor` 只监听 `AFTER_TURN_COMPLETED`。它优先读取 Interaction finalized
material；普通 Pipeline 则读取官方 Conversation 或当前 Provider 回合。没有稳定回合材料时
跳过，不从物理发送顺序或媒体投递结果猜测对话内容。

`MemoryService.update_from_postprocess()` 的当前顺序：

1. 写入 `TurnRecord`。
2. 更新 `TopicState` 与 `ShortTermMemory`。
3. 解析 canonical user identity。
4. 达到阈值时运行 consolidation，产生 `SessionInsight` 与 `Experience`。
5. 达到长期沉淀阈值时创建或更新 `LongTermMemory`，同步文档和向量索引状态。

缺少 canonical user identity 时，回合和短期层仍可写入；中长期链路停止，不用平台身份做
隐式 fallback。平台白名单关闭 Memory 写入时，Postprocessor 直接跳过该事件。

## 读取链路

```text
PromptContextBuilder
  -> MemoryCollector
  -> MemoryService.get_snapshot()
  -> MemorySnapshotBuilder
  -> memory.* ContextSlot
  -> target projection
```

当前可产生：

- `memory.topic_state`
- `memory.short_term`
- `memory.experiences`
- `memory.long_term_memories`
- `memory.persona_state`

是否读取以及 top-k 由统一 `memory.injection` 配置决定。Router、Planner、Persona 和 Core
不直接查询 Memory Service，只消费 Prompt target 投影。`MemoryCollector` 是 optional
Collector；读取失败会记录诊断，但不会创建第二套 fallback 记忆。

## 主要模块

- `config.py`：把 AstrBot 统一配置中的 `memory` mapping 解析为类型化配置。
- `types.py`：MemoryUpdateRequest、TurnRecord、TopicState、ShortTermMemory、Experience、
  LongTermMemory、PersonaState、MemorySnapshot 等公共数据类型。
- `store.py`：SQLite 结构化持久化。
- `service.py`：统一读写编排与按配置隔离的 service 实例。
- `short_term_service.py`：近期主题、摘要和 active focus。
- `consolidation_service.py` / `experience_service.py`：中期抽象与经历沉淀。
- `long_term_service.py`：长期记忆创建、更新和证据关联。
- `document_search.py` / `vector_index.py`：长期记忆检索和向量索引。
- `snapshot_builder.py`：按读取选项形成模型侧快照。
- `postprocessor.py`：回合完成后的唯一自动写入入口。

## 配置与存储

Memory 配置已进入 AstrBot 统一配置，不存在 `data/memory/config.yaml`。默认值由
`memory_config_defaults.py` 提供，`get_memory_config(event_config)` 解析当前事件的有效配置。

默认持久化位置：

- `data/memory/memory.db`：结构化真源。
- `data/memory/long_term/`：长期记忆正文。
- `data/memory/projections/`：可审阅投影。
- `data/memory/vector_index/`：向量索引。
- `data/memory/identity_mappings.yaml`：显式身份映射输入。

这些路径可通过统一 `memory.storage`、`memory.vector_index` 和 `memory.identity` 配置覆盖。

## 边界约束

- Conversation 保存精确消息，Memory 保存抽象状态，两者不能互相替代。
- 静态 Persona 不由 Memory 改写；`PersonaState` 是独立动态状态。
- Prompt 负责读取和可见范围，不负责 consolidation 或持久化。
- Interaction finalized material 是 Interaction 回合的提交材料，不再另存私有记忆。
- 长期文档和向量索引是检索载体，SQLite 中的 index/link/status 仍是结构化真源。
