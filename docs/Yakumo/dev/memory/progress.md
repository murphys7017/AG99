# Memory Progress

本文件只记录当前 memory 子系统的实现完成度，不重复展开完整设计。

## 1. 当前阶段

当前 memory 处于：

- 已完成短期闭环
- 已完成 snapshot 只读出口扩张
- 已完成中期 consolidation 第一版
- 已完成 `Experience` 的 Markdown 投影
- 已完成 `LongTermMemory + Document Search V1`
- 已完成长期记忆一致性修复第一轮
- 已完成 identity 三层拆分第一版
- 已完成向量检索主链路与严格失败校验
- 未进入人格演进与完整 retrieval 接入

当前可以认为已经完成了：

1. `TurnRecord`
2. `TopicState`
3. `ShortTermMemory`
4. `MemorySnapshot`
5. `SessionInsight`
6. `Experience`

## 2. 已完成链路

### 2.1 回合后写入链路

当前已落地：

1. `AFTER_MESSAGE_SENT` 或 interaction middleware 调度的 `AFTER_TURN_COMPLETED`
2. `MemoryPostProcessor`
3. `MemoryService.update_from_postprocess(...)`
4. `TurnRecordService.ingest_turn(...)`
5. `ShortTermMemoryService.update_after_turn(...)`

结果：

- 写入 `TurnRecord`
- 更新 `TopicState`
- 更新 `ShortTermMemory`

当前身份解析已固定为只看事件对象：

- `umo = event.unified_msg_origin`
- `platform_user_key = event.get_platform_id() + ":" + event.get_sender_id()`
- `canonical_user_id` 只通过 SQLite 显式映射表解析

当前行为约束：

- 短期层继续按 `umo + conversation_id` 工作
- `canonical_user_id` 缺失时，不阻断短期写入
- `canonical_user_id` 缺失时，中长期链路直接停止，不做 fallback

interaction turn 约束：

- middleware 是 finalized material producer
- postprocess / memory service 是 memory 写入 owner
- interaction memory 不从 visible outputs 兜底反推 assistant reply
- 音频、图片、文件等物理投递形态只作为 utterance metadata，不污染 canonical memory text

### 2.2 短期分析链路

当前已落地：

- memory analyzer 基础设施
- `analysis.enabled`
- `analysis.strict`
- `analysis.prompts_root`
- `analysis.analyzers.*`
- `analysis.stages.short_term_update`
- `short_term.update_interval_turns`
- `short_term.update_min_chars`

当前短期层支持两种运行模式：

- `analysis.enabled=false`
  - 使用当前确定性最小逻辑
- `analysis.enabled=true`
  - 使用配置驱动 analyzer

当前短期 analyzer 契约已经固定为：

- `topic_v1`
  - `current_topic`
  - `topic_summary`
  - `topic_confidence`
- `focus_v1`
  - `active_focus`
- `summary_v1`
  - `short_summary`

当前短期更新节奏：

- `TurnRecord` 每轮都会写入。
- 第一次没有 `ShortTermMemory` 时会立即分析。
- 之后按 `short_term.update_interval_turns` 或 `short_term.update_min_chars` 触发短期分析。
- `TopicState` 与 `ShortTermMemory` 在一次 `short_term_update` stage 中同时更新，不再为同一轮重复运行短期 analyzer。

### 2.3 Snapshot 读取链路

当前已落地：

1. `MemoryService.get_snapshot(...)`
2. `MemorySnapshotBuilder.build_snapshot(...)`
3. `MemoryStore` 读取短期层

当前 snapshot 返回：

- `topic_state`
- `short_term_memory`
- `experiences`
- `long_term_memories`
- `persona_state`

说明：

- `MemoryService.get_snapshot(...)` 是 memory 的完整只读出口，默认不套用 prompt 注入裁剪策略。
- Prompt 侧由 `MemoryCollector` 把 `memory.injection` 转换成 `MemorySnapshotReadOptions`，再决定读取多少中长期层数据以及最终注入哪些 slot。
- `canonical_user_id` 只看当前 latest turn，不做历史 turn 回溯补全
- latest turn 没有 `canonical_user_id` 时，snapshot 只返回短期层
- 无 query 时：
  - `experiences` 返回当前用户最近经验
  - `long_term_memories` 返回当前用户最近长期记忆
- 有 query 时：
  - `long_term_memories` 通过 `DocumentSearchService` 按 query 检索
  - `experiences` 优先通过命中 story 的 `LongTermMemoryLink` 回查
  - 不足部分再用最近经验补齐
- 这里的中长期字段已进入 snapshot；prompt 消费链路可通过 `memory.injection` 做轻量注入裁剪。

### 2.4 中期 consolidation 链路

当前已落地：

1. `MemoryService.update_from_postprocess(...)`
2. 短期更新完成后检查 consolidation 阈值
3. `MemoryService.run_consolidation(...)`
4. `ConsolidationService.run_for_scope(...)`
5. `MemoryStore.save_session_insight(...)`
6. `ExperienceService.persist_experiences(...)`

当前已补齐：

- `ExperienceProjectionService`
- `data/memory/projections/experiences/...` Markdown 投影写入

当前触发方式：

- 不是 scheduler
- 不是 jobs
- 是回合后阈值触发

当前阈值语义：

- 按 `canonical_user_id + conversation_id` 判断
- 统计最新 `SessionInsight.window_end_at` 之后的新 turn 数
- 达到 `consolidation.min_short_term_updates` 才触发
- 该阈值仍按 raw turn 数触发，不按短期分析实际执行次数触发；短期分析降频后，consolidation 可能读取到较旧的 `ShortTermMemory`，但仍以 raw turns 作为主要整理材料。

当前中期 analyzer 契约已经固定为：

- `session_insight_update`
  - `topic_summary`
  - `progress_summary`
  - `summary_text`
- `experience_extract`
  - `experiences`
  - 每项包含：
    - `category`
    - `summary`
    - `detail_summary`
    - `importance`
    - `confidence`

### 2.5 长期记忆与文档搜索链路

当前已落地：

1. `LongTermMemoryService.run_promotion(...)`
2. `MemoryStore.upsert_long_term_memory_index(...)`
3. `DocumentSerializer`
4. `DocumentLoader`
5. `DocumentSearchService`
6. `MemoryVectorIndex` 接口第一版

当前状态：

- 长期记忆文档与索引第一版已存在
- 文档搜索第一版已存在
- 手动导入 / 更新入口已存在
- 长期归属已切到 `canonical_user_id`
- 长期文档写入已改成 staging + 原子替换
- 向量索引开启时，长期导入 / promotion 会先校验 provider 绑定与索引可用性
- 向量索引 provider 缺失 / 类型错误时按 strict failure 暴露
- `importance / confidence / topic_confidence` 已收紧到 `0..1`
- 已接入 `MemorySnapshot.long_term_memories`
- 已接入 query-aware 的 snapshot 长期读取链路
- 已接入 prompt collector 读取链路
- render / 主链路消费仍未完整接管

## 3. 已完成模块

当前已实现模块：

- `astrbot/core/memory/config.py`
- `astrbot/core/memory/types.py`
- `astrbot/core/memory/store.py`
- `astrbot/core/memory/service.py`
- `astrbot/core/memory/history_source.py`
- `astrbot/core/memory/identity.py`
- `astrbot/core/memory/turn_record_service.py`
- `astrbot/core/memory/short_term_service.py`
- `astrbot/core/memory/snapshot_builder.py`
- `astrbot/core/memory/postprocessor.py`
- `astrbot/core/memory/consolidation_service.py`
- `astrbot/core/memory/experience_service.py`
- `astrbot/core/memory/projection.py`
- `astrbot/core/memory/long_term_service.py`
- `astrbot/core/memory/document_serializer.py`
- `astrbot/core/memory/document_loader.py`
- `astrbot/core/memory/document_search.py`
- `astrbot/core/memory/vector_index.py`

当前已补齐的 store 能力：

- `save_turn_record(...)`
- `get_recent_turn_records(...)`
- `upsert_topic_state(...)`
- `get_topic_state(...)`
- `upsert_short_term_memory(...)`
- `get_short_term_memory(...)`
- `save_session_insight(...)`
- `get_latest_session_insight(...)`
- `save_experience(...)`
- `get_experience(...)`
- `list_recent_experiences(...)`
- `list_experiences_for_scope(...)`
- `list_experiences_by_time_range(...)`
- `list_turn_records_by_time_range(...)`
- `list_turn_records_by_canonical_user(...)`
- `upsert_long_term_memory_index(...)`
- `list_long_term_memory_indexes(...)`
- `get_long_term_memory_index(...)`
- `list_long_term_memories_by_vector_status(...)`
- `update_long_term_vector_sync_state(...)`
- `save_long_term_memory_link(...)`
- `list_long_term_memory_links(...)`
- `upsert_long_term_promotion_cursor(...)`
- `get_long_term_promotion_cursor(...)`
- `save_identity_mapping(...)`
- `get_identity_mapping(...)`
- `delete_identity_mapping(...)`
- `list_identity_mappings_for_canonical_user(...)`

## 4. 当前未完成部分

当前明确未做：

- `retriever.py`
- `persona_state_service.py`
- `jobs.py`
- `graph_store.py`

当前能力边界：

- memory 已负责短期写入、中期 consolidation、长期文档与索引第一版
- prompt system 当前通过 `MemoryCollector` 消费 snapshot
- memory 还不负责 prompt render
- memory 还不负责 selector / router / chat state
- snapshot 的 query-aware `experiences` 当前仍基于命中 story 的 links 回查，不是独立 experience 向量检索
- memory 还不负责人格演进更新

## 5. 当前完成度判断

如果按当前规划分层看：

- Phase 1 短期写入闭环：已完成
- Phase 2 snapshot 读取闭环：已完成
- Phase 3 中期抽象链路：已部分完成
- 长期记忆层：已完成第一版基础服务与 snapshot 读取闭环
- 人格演进层：未开始
- retrieval 层：仅完成文档搜索基础，未完成统一召回链路

如果按“能不能给后续 prompt system 提供稳定 memory 输入”来看：

- 短期层：可以
- 中期层：已可通过 snapshot 暴露，但 retrieval 仍未统一
- 长期层：已可通过 snapshot 暴露，但还未形成稳定 prompt 消费入口

## 6. 当前主要限制

当前最大的限制不是写入，而是读取范围还刻意收窄：

- `SessionInsight` 已写入，但不进入 snapshot
- 还没有统一 query 驱动的 retrieval
- 向量索引已可服务长期记忆文档搜索，但仍未进入统一 retrieval

所以当前对外稳定开放的 memory snapshot 结果现在包括：

- `TopicState`
- `ShortTermMemory`
- `Experience`
- `LongTermMemory`
- `PersonaState`

## 7. 下一步建议顺序

建议后续顺序：

1. `snapshot-and-read-path.md`
2. `jobs-and-scheduling.md`
3. `retriever.py`
4. 将 `SessionInsight` / `Experience` / `LongTermMemory` 以受控方式接入 snapshot
5. 将长期层接入 prompt collector / renderer
6. `persona_state_service.py`

如果继续坚持“memory 先独立收口，再让 prompt 使用”，那当前最合理的下一步是：

1. 完成中长期 read path 设计
2. 明确 snapshot 什么时候开始暴露 `SessionInsight` / `LongTermMemory`
3. 再决定 retrieval 和长期沉淀的先后

## 8. 当前结论

当前 memory 已经不是“只有设计”，而是已经完成了第一条真实工作链路：

`Post Process -> TurnRecord -> ShortTermMemory -> Consolidation -> SessionInsight / Experience -> LongTermPromotion -> Snapshot`

当前这个链路里，snapshot 已经能稳定暴露短期层 + 中长期只读结果。
当前这个链路里，真正还没有完整接管的是 render 与主请求拼装。

所以当前最准确的判断是：

- memory 基础设施已成立
- 中期抽象已落地到 store
- 长期层基础服务与文档搜索第一版已落地，并完成第一轮一致性修复
- `Experience` 已完成 projection，可供内部审阅
- prompt collector 已能读取 snapshot
- 系统整体正处于“短期完成，中期可读，长期第一版已落地，collect 已接入但 render 尚未完整接管”的阶段
