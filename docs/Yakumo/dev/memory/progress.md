# Memory Current Status

本文只记录当前源码已经具备的能力和仍存在的边界，不保留历史实施步骤。

## 已完成

- Memory Service 在 Core Lifecycle 初始化，并按有效配置隔离实例。
- `AFTER_TURN_COMPLETED` Postprocessor 已成为自动写入入口。
- Interaction finalized material 与普通 Conversation 回合都可形成 `MemoryUpdateRequest`。
- 所有可接受回合保留 `TurnRecord`；assistant-only 主动表达保留精确历史，但不会更新
  TopicState、ShortTermMemory、PersonaState 或触发 consolidation / promotion。
- 真实用户回合（包括附件或媒体输入）形成 TopicState、ShortTermMemory 的短期闭环。
- canonical identity 映射、SessionInsight、Experience 和长期记忆 promotion 已接入。
- 长期记忆 Markdown、结构化索引、证据链接、向量同步状态和文档搜索已实现。
- `MemorySnapshotBuilder` 可读取 topic、short-term、experience、long-term 和 persona state。
- Prompt 使用异步 Recall Snapshot：当前回合只同步读取 topic、short-term 和 persona state，长期记忆由 short-term revision/fingerprint 驱动后台刷新，首次无缓存时不等待。
- Recall 可按 `USER`、`GROUP`、`GLOBAL` 合并；USER 绑定当前 canonical user，GROUP/GLOBAL 按稳定 scope key 共享，并按配置的作用域优先级、最终 top-k 和冲突去重策略收口。
- 群聊用户回合可同时贡献 `USER` 与 `GROUP`；GROUP consolidation 会聚合同一群组的不同成员回合，使用稳定群组 owner key 运行 Experience/Long-Term promotion，贡献者身份仍保留在回合 provenance 中。
- Memory Phase 5 第一批已接入 `MemoryJobScheduler`：后台 Postprocessor 提交 consolidation/promotion，任务按 scope 串行、同一 conversation 合并，后台异常被消费并记录诊断；直接调用 `MemoryService.update_from_postprocess()` 仍保持同步执行和异常传播。
- Memory Phase 5 第二批已将 Recall refresh 和 dirty vector sync 接入同一 scheduler；Recall 继续保持 stale-while-revalidate，向量修复增加显式 `schedule_dirty_long_term_vector_indexes()` 非等待入口，并按任务类型和 dedupe key 合并。
- `MemoryCollector` 已进入统一 Prompt ContextPack，并由 target projection 控制 Router、
  Planner、Persona 和 Core 的可见范围。
- Interaction 私有 Memory Store 和 `memory.interaction` slot 已删除。

## 当前限制

- 自动 PersonaState 演进尚未形成与短期/长期链路同等完整的 service；默认注入也关闭。
- consolidation、长期 promotion、Recall 刷新和 dirty vector sync 已由后台 scheduler 承担；PersonaState 自动演进仍未形成完整 service、触发和审核链路。
- canonical identity 缺失的用户回合只跳过 USER 中长期沉淀；若回合带有稳定 GROUP scope，GROUP 中长期沉淀仍可执行。
- Memory analyzer 依赖配置的 Provider；分析失败按 Postprocessor 失败语义记录并跳过该次更新。
- 向量检索、文档回表和 analyzer 调用仍需要持续关注延迟、超时和可观测性。
- Context Catalog 的生命周期与脱敏字段尚未全部成为运行时强约束。

## 下一步

1. 明确 PersonaState 自动演进的触发、审核和回滚边界。
2. 明确并实现 PersonaState 自动演进的触发、审核、回滚和作用域边界。
3. 完善 Memory read/write latency、降级组件和后台任务诊断。
4. 固化 finalized material 到 MemoryUpdateRequest 的版本化契约，并进行真实运行验收。

具体模块关系见 `architecture.md`；配置事实以 `astrbot/core/memory/config.py`、
`astrbot/core/memory_config_defaults.py` 和统一配置 schema 为准。
