# Memory 文档索引

Memory Service 是抽象记忆的唯一 owner。官方 Conversation 保存精确对话；Prompt 通过
`ConversationHistoryCollector` 和 `MemoryCollector` 分别读取两类事实；Interaction 不维护
私有记忆副本。

## 文档

- `architecture.md`：当前读写链路、模块、配置、存储和所有权边界。
- `progress.md`：已经实现的能力、当前限制和下一步。

## 维护规则

- 文档以当前源码为准，不保存已完成的 MVP 步骤或早期建议接口。
- 配置只描述 AstrBot 统一 `memory` 配置，不再记录独立配置文件方案。
- Prompt 只读取 snapshot；Postprocess/Memory Service 负责写入。
- 新能力进入现有 Memory Service，不建立 Interaction 或插件私有的并行事实源。
