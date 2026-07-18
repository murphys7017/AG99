# Memory Current Status

本文只记录当前源码已经具备的能力和仍存在的边界，不保留历史实施步骤。

## 已完成

- Memory Service 在 Core Lifecycle 初始化，并按有效配置隔离实例。
- `AFTER_TURN_COMPLETED` Postprocessor 已成为自动写入入口。
- Interaction finalized material 与普通 Conversation 回合都可形成 `MemoryUpdateRequest`。
- TurnRecord、TopicState、ShortTermMemory 已形成短期闭环。
- canonical identity 映射、SessionInsight、Experience 和长期记忆 promotion 已接入。
- 长期记忆 Markdown、结构化索引、证据链接、向量同步状态和文档搜索已实现。
- `MemorySnapshotBuilder` 可读取 topic、short-term、experience、long-term 和 persona state。
- `MemoryCollector` 已进入统一 Prompt ContextPack，并由 target projection 控制 Router、
  Planner、Persona 和 Core 的可见范围。
- Interaction 私有 Memory Store 和 `memory.interaction` slot 已删除。

## 当前限制

- 自动 PersonaState 演进尚未形成与短期/长期链路同等完整的 service；默认注入也关闭。
- consolidation 与长期 promotion 当前主要由回合写入阈值触发，独立后台调度还不是主链。
- canonical identity 缺失时只保留回合与短期写入，中长期沉淀会明确停止。
- Memory analyzer 依赖配置的 Provider；分析失败按 Postprocessor 失败语义记录并跳过该次更新。
- 向量检索、文档回表和 analyzer 调用仍需要持续关注延迟、超时和可观测性。
- Context Catalog 的生命周期与脱敏字段尚未全部成为运行时强约束。

## 下一步

1. 明确 PersonaState 自动演进的触发、审核和回滚边界。
2. 将后台 consolidation/promotion 接入统一的预算、调度和任务 owner。
3. 完善 Memory read/write latency、降级组件和向量同步诊断。
4. 固化 finalized material 到 MemoryUpdateRequest 的版本化契约。

具体模块关系见 `architecture.md`；配置事实以 `astrbot/core/memory/config.py`、
`astrbot/core/memory_config_defaults.py` 和统一配置 schema 为准。
