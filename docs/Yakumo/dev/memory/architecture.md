# Memory Architecture

本文描述当前 `astrbot/core/memory` 的源码边界。Memory Service 是 Yakumo 唯一的抽象记忆
系统；Interaction 不维护 session JSON 记忆副本，官方 Conversation 仍负责精确对话历史。

## 所有权

```text
Finalized Turn Material
  -> Postprocess(AFTER_TURN_COMPLETED)
  -> MemoryPostProcessor
  -> MemoryService.update_from_postprocess()
  -> TurnRecord
  -> assistant-only: stop
  -> user turn: Short-Term Update
  -> MemoryJobScheduler(background Postprocessor)
        -> scoped Consolidation / Experience / Long-Term Promotion
  -> MemoryJobScheduler(background Recall refresh / Vector sync)
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

`MemoryService.update_from_postprocess()` 先无条件保留可接受 finalized material 的
`TurnRecord`，随后按回合类型分支：

1. 所有回合写入 `TurnRecord`。
2. `assistant_only=True` 时结束写入：该回合保留精确历史，但不更新 `TopicState`、
   `ShortTermMemory`、`PersonaState`，也不运行 consolidation、Experience 或长期记忆
   promotion。
3. 普通用户回合更新 `TopicState` 与 `ShortTermMemory`，再按 `MemoryScopeContext` 枚举可贡献的 USER/GROUP scope。
4. 生产 Postprocessor 将每个 scope 提交给 `MemoryJobScheduler`；同一 scope 串行执行，同一 conversation 的重复待处理任务合并。任务内部按阈值运行 consolidation，产生对应的 `SessionInsight` 与 `Experience`；GROUP 会聚合同一群组不同成员的回合。
5. 每个 scope 的任务继续推进长期沉淀、文档和向量索引；共享 scope 使用稳定 scope owner key，当前贡献者仍保留在 `platform_user_key`、回合和 source refs 中。Recall refresh 与 dirty vector sync 也通过同一 scheduler 托管；PersonaState 已有原子演进与回滚服务，但自动 reflection 尚未接入 scheduler。

`MemoryService.update_from_postprocess()` 的生产调用显式使用 `background_jobs=True`，不会等待 analyzer 或整理任务；直接管理调用默认同步执行并传播异常，避免把后台异常吞成同步 API 的假成功。Recall refresh 通过同一 scheduler 提交，但仍由 `RecallSnapshotManager` 保持 stale-while-revalidate 的缓存语义；dirty vector sync 通过 `schedule_dirty_long_term_vector_indexes()` 提交，原有同步修复入口继续保留给管理调用。

assistant-only 是 Conversation 历史转换层的显式标记，不由文本是否为空推断。主动 Persona
表达以空 `user_message` 形成该标记，因此可继续提供给 Conversation 和 Prompt 作为语义上下文，
却不会把 Bot 自己的表达反馈成抽象记忆。真实的附件或媒体用户输入会归一化为
`[attachment]`，仍按用户回合处理。缺少 canonical user identity 的用户回合仍可写入回合和
短期层；若存在稳定 GROUP scope，GROUP 中长期沉淀仍可继续，否则中长期链路停止，不用平台身份
做隐式 USER fallback。平台白名单关闭 Memory 写入时，
Postprocessor 直接跳过该事件。

## 读取链路

```text
PromptContextBuilder
  -> MemoryCollector
  -> MemoryService.get_prompt_snapshot()
  -> local snapshot(topic / short-term / persona)
  -> RecallSnapshotManager(stale-while-revalidate)
  -> background scoped recall(USER / GROUP / GLOBAL)
  -> memory.* ContextSlot
  -> target projection
```

当前可产生：

- `memory.topic_state`
- `memory.short_term`
- `memory.experiences`
- `memory.long_term_memories`
- `memory.persona_state`

是否读取以及最终 top-k 由统一 `memory.injection` 配置决定。长期检索 query 来自当前
Short-Term Memory 的 summary/focus，而不是当前消息；缓存键包含 short-term revision、
fingerprint、scope set 和 retrieval profile。没有缓存时 Prompt 立即使用本地快照，有旧缓存时
先复用并后台刷新。

`memory.recall.scope_priority` 决定 USER/GROUP/GLOBAL 的启用与冲突优先级；USER 读取绑定
当前 canonical user，GROUP/GLOBAL 只按稳定 scope key 读取，因此可以消费其他成员在同一群组
作用域形成的记忆。向量结果水合时会再次校验 owner/scope，避免陈旧索引元数据越界。

Router、Planner、Persona 和 Core 不直接查询 Memory Service，只消费 Prompt target 投影。
`MemoryCollector` 是 optional Collector；读取失败会记录诊断，但不会创建第二套 fallback 记忆。

## 主要模块

- `config.py`：把 AstrBot 统一配置中的 `memory` mapping 解析为类型化配置。
- `types.py`：MemoryUpdateRequest、TurnRecord、TopicState、ShortTermMemory、Experience、
  LongTermMemory、PersonaState、MemorySnapshot 等公共数据类型。
- `store.py`：SQLite 结构化持久化。
- `service.py`：统一读写编排与按配置隔离的 service 实例。
- `job_scheduler.py`：按作用域串行、按任务类型和 dedupe key 合并并消费后台 Memory 任务异常。
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
- Conversation 为后续语义理解保留 assistant-only 主动表达；这一可见历史不会自行产生
  Memory 状态、Policy 材料或唤醒权限。
- 静态 Persona 不由 Memory 改写；`PersonaState` 是独立动态状态。
- `PersonaStateService` 只接受 USER scope 语义变化，负责中性基线、置信度、delta 限幅、间隔判断，以及 state + evolution log 的原子写入和显式回滚。
- PersonaState reflection 只消费 consolidation 后的 SessionInsight/Experience；相关开关和 Prompt 注入继续默认关闭，管理入口和真实运行验收仍待完成。
- Prompt 负责读取和可见范围，不负责 consolidation 或持久化。
- Interaction finalized material 是 Interaction 回合的提交材料，不再另存私有记忆。
- 长期文档和向量索引是检索载体，SQLite 中的 index/link/status 仍是结构化真源。
