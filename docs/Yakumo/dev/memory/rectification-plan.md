# Memory 整改计划

## 目标

把 Memory 调整为 Personal Expression 可直接消费、但不拖慢首回复的异步记忆系统：

- 精确对话继续由 Conversation 保存；Memory 只保存短期状态和模糊语义事实。
- Personal 使用 Short-Term Memory 与长期记忆；Router 默认只使用轻量的 Short-Term Memory。
- 长期检索由 Short-Term Memory 的 `revision`/`fingerprint` 驱动，不再以每轮当前消息直接触发完整检索。
- 长期读取使用 Recall Snapshot，采用 stale-while-revalidate：优先复用旧快照，后台刷新，首次没有快照时不等待。
- 支持 `USER`、`GROUP`、`GLOBAL` 作用域；群聊可同时贡献当前用户记忆和群组记忆。
- 读取、写入、整理、向量同步尽量后台异步化，并按作用域串行、合并过期任务。

## 数据边界

内部标识可以保存，也必须保留在结构化存储和 provenance 中，用于更新、合并、去重、回溯和向量同步，例如：
`memory_id`、`conversation_id`、`turn_id`、`source_refs`、`scope_id`。

这些字段不得进入模型可见的语义内容，不能出现在 Prompt、Memory Markdown 的语义正文或模型侧的长期记忆卡片中。Prompt 只接收脱离技术标识的语义字段；诊断、管理接口和内部存储可以继续使用完整元数据。

## 实施阶段

当前进度：Phase 1 已完成基础实现与轻量验证；Phase 2 及之后尚未开始。

### Phase 1：先修基础稳定性，不改变记忆语义

1. `MemoryPostProcessor` 不再通过单例实例字段切换 `memory_service`；每次事件在局部解析服务，避免不同配置并发串线。
2. 真正执行 analyzer 的 `timeout_seconds`，超时按现有 postprocess 失败语义记录并跳过本次抽象更新，不阻塞主回复。
3. 给 `MemoryVectorIndex` 初始化增加单飞锁，避免并发首次访问重复建索引。
4. 将 FAISS 初始化、搜索、写入等同步操作移出事件循环；保持现有存储契约和结果顺序不变。

Phase 1 的验收重点是：配置隔离、超时可控、并发首次检索不重复初始化、主事件循环不被同步向量操作长时间阻塞。

### Phase 2：作用域与短期版本

引入统一的 `MemoryScopeContext`，从事件解析用户、群组和全局作用域；补充 `GROUP` 类型及稳定 scope key。为 Short-Term Memory 增加单调 `revision` 与内容 fingerprint，更新时递增并可判断是否需要刷新 Recall Snapshot。

### Phase 3：Recall Snapshot 与 Personal 接入

按 `(scope set, short_term revision, retrieval profile)` 缓存长期检索结果。Personal 读取 Short-Term 和最新可用 Snapshot；Snapshot 过期时后台刷新，刷新期间继续使用旧结果。Router 不等待长期向量检索。移除当前消息直接驱动长期检索的默认路径。

### Phase 4：群聊长期记忆

用户回合在群聊中按策略分别贡献 `USER` 和 `GROUP` 记忆；Personal 检索时合并用户、群组和全局结果，并保持作用域优先级、top-k 和冲突处理可配置。不存在群组身份时只跳过 GROUP，不影响 USER/GLOBAL。

### Phase 5：语义投影与异步调度收口

将 semantic payload 与内部 provenance 分成两个明确结构。MemoryCollector 默认只序列化语义字段，调试字段也不得默认进入模型 Prompt。建立后台 `MemoryJobScheduler`，负责短期更新后的 consolidation、promotion、快照刷新和向量同步；按 scope 串行、限流、去重并记录失败诊断。

### Phase 6：删除旧路径并更新文档

移除 USER 硬编码、按当前消息逐轮长期检索和同步写入的旧旁路；更新 Memory 架构、进度、配置和 Personal Prompt 文档，明确 Router/Personal 的读取边界及 ID 脱敏规则。

## 非目标

- 不删除 `memory_id`、`conversation_id`、`turn_id` 等内部字段。
- 不把精确 Conversation 历史改造成长期记忆。
- 不让插件建立独立的 Memory 真源。
- 不在第一阶段重写长期记忆算法或扩大测试套件；只保留基础验证。

## 执行顺序

先完成 Phase 1 并单独验证，再依次推进作用域、Short-Term revision、Recall Snapshot、Personal 接入和群聊记忆。每个阶段都保持可回滚，避免同时引入“作用域变化”和“读取时序变化”导致问题难以归因。
