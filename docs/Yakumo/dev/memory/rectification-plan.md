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

正常的 Personal/Router 语义 Prompt 默认不携带这些字段，避免技术标识干扰模型表达；但在明确的诊断、管理、精确引用或内部调试场景，可以按需显式携带。Memory Markdown 的语义正文和模型侧长期记忆卡片默认只投影语义字段；诊断、管理接口和内部存储继续保留完整元数据。

## 实施阶段

当前进度：Phase 1、Phase 2、Phase 3 和 Phase 4 已完成；Phase 5 已完成 consolidation/promotion、Recall 刷新、dirty vector sync 和 USER-scoped PersonaState reflection 的统一后台调度。

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

Phase 4 已同时完成读取与写入：`memory.recall.scope_priority` 控制启用顺序，现有 injection top-k 继续控制最终数量，`deduplicate_across_scopes` 控制同类同标题/摘要冲突是否由高优先级作用域胜出。USER 读取绑定当前 canonical user；GROUP/GLOBAL 按稳定 scope key 共享读取。群聊回合按可用 scope 同时贡献 USER/GROUP；共享作用域使用稳定 scope owner key 进行 consolidation、Experience 和 Long-Term promotion，当前贡献者只作为 provenance 保留，不决定共享记忆的可见范围。没有群组身份时只跳过 GROUP，不影响 USER。

### Phase 5：语义投影与异步调度收口

将 semantic payload 与内部 provenance 分成两个明确结构。MemoryCollector 默认只序列化语义字段，调试字段也不得默认进入模型 Prompt。`MemoryJobScheduler` 统一承载短期更新后的 consolidation、promotion、Recall refresh、dirty vector sync 和 PersonaState reflection。任务按 scope 串行、按 dedupe key 合并，并消费后台异常形成诊断；Recall 仍保持 stale-while-revalidate，dirty vector sync 提供显式的非等待提交入口。

`MemoryService.update_from_postprocess()` 保留两种明确语义：生产 Postprocessor 传入 `background_jobs=True`，只完成回合与短期写入并提交后台任务；其他直接调用默认同步执行 scope job，并继续传播 analyzer/整理异常，保持管理和测试入口的可观察行为。

### Phase 6：删除旧路径并更新文档

移除 USER 硬编码、按当前消息逐轮长期检索和同步写入的旧旁路；更新 Memory 架构、进度、配置和 Personal Prompt 文档，明确 Router/Personal 的读取边界及 ID 脱敏规则。

## 非目标

- 不删除 `memory_id`、`conversation_id`、`turn_id` 等内部字段。
- 不把精确 Conversation 历史改造成长期记忆。
- 不让插件建立独立的 Memory 真源。
- 不在第一阶段重写长期记忆算法或扩大测试套件；只保留基础验证。

## 执行顺序

先完成 Phase 1 并单独验证，再依次推进作用域、Short-Term revision、Recall Snapshot、Personal 接入和群聊记忆。Phase 5 已按“consolidation/promotion 调度 → Recall 刷新与向量同步调度 → PersonaState reflection 调度”推进完成；后续只保留真实运行验收和延迟/诊断优化。每个阶段都保持可回滚，避免同时引入“作用域变化”和“读取时序变化”导致问题难以归因。
