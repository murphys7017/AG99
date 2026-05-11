# AstrBot Yakumo Fork

> 实验性分支：让机器人不只是「问答机器」，而是更像一个真实的对话伙伴。

**AstrBot** 是一个成熟的多平台 LLM 聊天机器人与 Agent 框架。这个 fork 在其基础上增加了一层「拟人化交互」能力，目标让机器人的回复更自然、互动更有节奏感。

[上游仓库](https://github.com/AstrBotDevs/AstrBot) · [官方文档](https://docs.astrbot.app/) · [本分支详细文档](./docs/Yakumo/)

---

## 和上游的区别

| 能力 | 上游 AstrBot | Yakumo Fork |
|------|:------------:|:-----------:|
| 核心交互方式 | 消息 → Agent → 回复 | 消息 → **拟人层** → 决策 → 核心处理 → 拟人层整理 → 回复 |
| 快速回复 | 不支持 | 支持「临时回复」，边想边说 |
| 回复风格控制 | 仅靠 prompt | 拟人层统一管理表达方式 |
| 记忆系统 | 会话历史 | 会话历史 + **长期记忆沉淀** |
| Prompt 组织 | 字符串拼接 | **结构化上下文**（collect → select → render → apply） |
| Interaction 语义 | 分散在各处 | **Interaction Middleware** 统一接管 |
| 前端展示 | 最终回复 | 临时回复 / 核心结果 / 最终表达 分阶段展示 |
| 本地 provider 支持 | 基础 | 保留并扩展 Ark / Doubao 等本地场景 |

---

## 核心理念：什么是「拟人层」

大多数 Agent 框架的流程是：**收到消息 → 交给大模型 → 等待完整答案 → 回复用户**。

这个 fork 在中间加了一层「拟人层」：

```
用户发消息
    ↓
拟人层接住消息，判断这一轮该怎么处理
    ↓
    ├── 轻量互动（寒暄、确认、简单问答）→ 拟人层直接回复
    └── 重度任务（查资料、调工具、写代码）→ 交给核心 Agent
         ↓
    核心执行中，拟人层实时提取中间结果反馈给用户
         ↓
    核心处理完毕
         ↓
    拟人层整理最终结果，用更自然的表达方式呈现
         ↓
    写入记忆链路（长期记忆 + 本轮上下文）
```

**上下文分离** — 拟人层和核心 Agent 各自维护独立的上下文：

| 上下文 | 负责方 | 用途 |
|--------|--------|------|
| 拟人层上下文 | 拟人层 | 互动节奏、临时回复、表达风格、人格记忆 |
| 核心历史 | 核心 Agent | 任务规划、工具调用、知识库检索、代码执行 |

---

## Interaction Middleware

这是本 fork 的核心架构之一，一个通用的交互中间件：

- **输入侧**：在核心 decision 之前完成 turn state、入站媒体 materialization、STT、路由决策
- **输出侧**：接管 `event.send` / `event.send_streaming` 语义，统一 finalizer、result contributor、TTS、t2i、stream observation、utterance ledger 与 finalized turn material
- **Completion 收口**：middleware 产出 finalized material，postprocess / memory service 消费同一份 material 写记忆
- **Voice 共享**：core 旧流程和 middleware 新流程共享 `voice/*`，failure policy 由调用方决定

当前主链路开发期 **fail-fast**，不把 fallback 当正确性证明。

---

## Prompt 结构化上下文

上游的 prompt 是直接在 `astr_main_agent.py` 里组织模型可见上下文。这个 fork 推进了一套新的 prompt 子系统：

```
collect → select → render → apply
```

- **collect**：把 persona、input、session、policy、memory、history、skills、tools、subagent、knowledge 等信息结构化收集成 `ContextPack`
- **select**：给后续筛选层预留接口
- **render**：由 renderer 决定节点结构和模型可见输出
- **apply**：把 render 结果投影回 `ProviderRequest`

---

## 当前状态

| 功能 | 状态 | 说明 |
|------|:----:|------|
| 拟人层决策 | 🟡 开发中 | 核心逻辑已通，关键路径验证中 |
| 临时回复 | 🟡 开发中 | 流式交互已支持，表达优化进行中 |
| 长期记忆 | 🟡 开发中 | 框架已搭，部分场景验证 |
| Interaction Middleware | 🟡 开发中 | 主链路已通，部分边界场景仍需收口 |
| 结构化 Prompt | 🟡 开发中 | collect/render/apply 已跑通，select 筛选层待完善 |
| 上游兼容 | 🟢 稳定 | 安全修复、provider 稳定修复持续同步 |

> [!NOTE]
> 本 fork 目标是**暴露真实链路问题**，而非快速迭代发行版。如果你需要开箱即用的稳定版本，请使用[上游 AstrBot](https://github.com/AstrBotDevs/AstrBot)。

---

## 快速开始

```bash
# Core
uv sync
uv run main.py

# Dashboard（可选）
cd dashboard
pnpm install
pnpm dev
```

- Core / API: http://localhost:6185
- Dashboard: http://localhost:3000

---

## 深入了解

`docs/Yakumo/` 记录了本 fork 的架构设计、实现进度和开发记录。建议阅读顺序：

**想快速了解当前状态：**
1. [docs/Yakumo/README.md](./docs/Yakumo/README.md) — 文档索引和阅读建议
2. [docs/Yakumo/current-state.md](./docs/Yakumo/current-state.md) — 当前代码状态总览
3. [docs/Yakumo/modules/README.md](./docs/Yakumo/modules/README.md) — 各模块职责说明

**想了解具体子系统：**
- [docs/Yakumo/modules/interaction.md](./docs/Yakumo/modules/interaction.md) — Interaction Middleware 详解
- [docs/Yakumo/modules/prompt.md](./docs/Yakumo/modules/prompt.md) — Prompt 结构化上下文
- [docs/Yakumo/dev/memory/index.md](./docs/Yakumo/dev/memory/index.md) — 记忆系统设计
- [docs/Yakumo/upstream-merge-ledger.md](./docs/Yakumo/upstream-merge-ledger.md) — 上游合并记录

> `dev/*` 下的文档为阶段性设计与实现记录，不代表当前已完成实现。阅读时请注意区分「当前事实」和「设计记录」。

---

## 许可证

继承上游 AGPL-3.0-or-later。详见 [LICENSE](./LICENSE)。
