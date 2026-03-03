# 文档总览

本文档用于标记“当前生效文档”和“历史归档文档”。

## Active（当前生效）

1. `README.md`：项目中文入口
2. `README-EN.md`：项目英文入口
3. `docs/DEPLOYMENT.md`：部署与运行
4. `docs/TESTING.md`：测试分层与执行方式
5. `docs/AGENT_SYSTEM_ARCHITECTURE.md`：Agent 子系统架构与链路说明
6. `docs/MEMORY.md`：Memory 当前实现
7. `docs/PROJECT_MODULE_DEEP_DIVE.md`：代码对齐的模块深潜
8. `docs/DESIGN_DECISIONS.md`：仍有效的 ADR
9. `docs/GATE_COMPLETE_SPECIFICATION.md`：Gate 规范
10. `docs/SYSTEM_REFLEX_SPECIFICATION.md`：System Reflex 规范
11. `docs/ROADMAP.md`：阶段计划

## Reference（参考文档）

1. `docs/AGENT_REQUEST_STRUCTURE.md`：AgentRequest 契约说明
2. `docs/AGENT_REQUEST_QUICK_REFERENCE.md`：AgentRequest 速查
3. `docs/CONTEXT_CATALOG_PROFILE_SUMMARY.md`：Context Catalog / PromptProfile / Presets 配置规范与阶段总结

这两份文档用于开发调试参考，不作为“架构规范”的唯一来源。

## Experimental（实验文档）

1. `docs/demo_e2e.md`：CLI E2E 演示说明（对应脚本处于实验状态，可能与当前主干不完全一致）

## Archive（历史归档）

统一放在 `docs/archive/`：

1. `docs/archive/legacy/`：旧架构/旧规范
2. `docs/archive/reports/`：阶段性报告
3. `docs/archive/notes/`：临时笔记
4. `docs/archive/copilot/`：一次性任务书与草案
5. `docs/archive/analysis/`：历史分析文档
6. `docs/archive/memory/`：旧版 Memory 设计与总结

## 维护规则

1. 代码行为发生变化时，同步更新对应 Active 文档。
2. 同一主题只保留一份主文档，避免并行版本漂移。
3. 阶段性记录完成后，移入 `docs/archive/`。
4. 文档尽量避免固定测试数值；若写入数值需标明日期。
