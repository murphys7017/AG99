# AstrBot Yakumo Fork

> 实验性分支：让机器人不只是「问答机器」，而是更像一个真实的对话伙伴。

**AstrBot** 是一个成熟的多平台 LLM 聊天机器人与 Agent 框架。这个 fork 在其基础上增加了一层「拟人化交互」能力，目标让机器人的回复更自然、互动更有节奏感。

[上游仓库](https://github.com/AstrBotDevs/AstrBot) · [官方文档](https://docs.astrbot.app/) · [本分支详细文档](./docs/Yakumo/)

---

## 和上游的区别

| 能力 | 上游 AstrBot | Yakumo Fork |
|------|:------------:|:-----------:|
| 核心交互方式 | 消息 → Agent → 回复 | 消息 → **轻量路由分类** → 按需调用 Persona / Core → 统一拟人化 → 回复 |
| 快速回复 | 不支持 | 唯一拟人层可先产生即时表达，不必等待 Core |
| 回复风格控制 | 仅靠 prompt | 拟人层统一管理表达方式 |
| 记忆系统 | 会话历史 | 会话历史 + **长期记忆沉淀** |
| Prompt 组织 | 字符串拼接 | **结构化上下文**（collect → build → project → render → apply） |
| Interaction 语义 | 分散在各处 | **Interaction Middleware** 统一接管 |
| 前端展示 | 最终回复 | 临时回复 / 核心结果 / 最终表达 分阶段展示 |
| 本地 provider 支持 | 基础 | 保留并扩展 Ark / Doubao 等本地场景 |

---

## 核心理念：什么是「拟人层」

大多数 Agent 框架的流程是：**收到消息 → 交给大模型 → 等待完整答案 → 回复用户**。

这个 fork 在官方 Pipeline 与核心 Agent 之间增加 Interaction Middleware，并把用户可见表达收口到唯一的 Persona Runtime：

```
用户发消息
    ↓
官方 EventBus / Pipeline 完成事件过滤、权限和插件处理
    ↓
Interaction Middleware 建立本轮交互并整理输入
    ↓
Prompt Collectors 构建本轮唯一的 ContextPack
    ↓
Router 与 Persona Runtime 并发启动
    ├── Persona Runtime → 尽快生成并提交拟人表达
    └── Router → 当前只返回 persona / hybrid
         ├── persona → 不启动 Core，保留 Persona 表达
         └── hybrid → 独立 Core Planner 再判断执行层是否必要
                      ├── not_required → 不启动 Core，保留 Persona 表达
                      └── execute → 立即执行 Core，不等待 Persona
                                   Core 的中间材料与最终结果回到同一个 Persona Runtime
    ↓
Output Runtime 负责文本、流式与 TTS 等输出物化和平台发送
    ↓
Finalized Turn Material → Postprocess / Memory
```

“快速拟人回复”不是第二套回复生成器，只是 Persona Runtime 在 Core 完成前的一次调用。Core 结果、插件提交的待表达材料和流式插话也复用同一个入口。Motion、Live2D 等具体表现能力由插件通过通用 effect 契约扩展；插件可以按当前事件决定是否向 Persona 暴露 effect，核心交互流程只校验和传递 effect，不理解具体动作含义。

**事实统一、视图分离** — Prompt 层只采集一次规范事实，Router、Core Planner、Persona 和 Core 从同一个 ContextPack 投影各自视图：

| 目标视图 | 用途 |
|----------|------|
| Router | 用极简人格摘要和近期上下文判断 persona / hybrid |
| Core Planner | 独立复核执行层是否必要，并整理 CoreTaskSpec |
| Persona | 使用完整人格、历史、记忆和待表达材料生成用户可见表达 |
| Core | 使用任务、工具、知识库和执行上下文完成工作，不注入人格表达规则 |

Router 与 Core Planner 只共享事实源，不共享模型决策、Prompt 指令或输出结果。

持续人格 Runtime 已具备独立的 Observation Intake：内部事实按会话人格解析到同一个
RuntimeKey，在每个 Runtime 的有界 Inbox 中执行过期清理、显式合并和 1.5 秒聚合窗口，最后
形成只读 `ObservationBatch`。确定性 Gate 随后只根据运行状态、quiet hours、冷却、预算和目标
能力给出 `evaluate / hold / reject` 及稳定原因码；`hold` 会保留事实，整个阶段不构造平台消息、
不进入 EventBus，也不调用模型或主动回复。已经决定发送的主动输出仍走单独的 Persona
Expression 与 Output 路径。

---

## Interaction Middleware

这是本 fork 的核心架构之一，一个通用的交互中间件：

- **位置**：复用官方 EventBus、Pipeline、权限与插件过滤，位于这些处理之后、核心 Agent 开始之前
- **输入侧**：完成 turn state、入站媒体 materialization、STT，由 Prompt Collectors 构建规范 ContextPack；Router 与 Persona 并发消费各自投影，hybrid 再由独立 Core Planner 复核是否执行
- **输出侧**：接管 `event.send` / `event.send_streaming` 语义，统一 finalizer、result contributor、TTS、t2i、stream observation、utterance ledger 与 finalized turn material
- **表达侧**：所有需要拟人化的可见材料进入同一个 Persona Runtime；Output Runtime 不再自行生成另一套文案
- **扩展侧**：effect 是通用插件协议，按当前事件过滤后才进入 Persona 输出契约；Motion 或 Live2D 的解析和执行不属于主流程
- **Completion 收口**：middleware 产出 finalized material，postprocess / memory service 消费同一份 material 写记忆
- **Voice 共享**：core 旧流程和 middleware 新流程共享 `voice/*`，failure policy 由调用方决定

当前主链路开发期 **fail-fast**，不把 fallback 当正确性证明。

---

## Prompt 结构化上下文

上游的 prompt 是直接在 `astr_main_agent.py` 里组织模型可见上下文。这个 fork 推进了一套新的 prompt 子系统：

```
collect → build → target projection → render profile → prompt layout/tree → provider render → apply
```

- **collect**：把 persona、input、session、policy、memory、history、skills、tools、subagent、knowledge 等信息结构化收集成 `ContextPack`
- **build**：合并为带版本的规范 `ContextPack`，冲突不再静默覆盖
- **target projection**：为 Router、Core Planner、Persona、Core 生成范围明确的确定性视图
- **render profile**：应用目标专属 system、request prompt、输出契约和隐藏规则，不修改规范 `ContextPack`
- **prompt layout/tree**：通过独立 layout contract 构建与 provider 无关的语义树
- **provider render**：序列化为对应 provider 的消息、媒体和工具协议
- **apply**：把 render 结果投影回 `ProviderRequest`

边界上，Collector 只提供事实，Render Profile 只提供目标局部指令，Layout 只负责语义落位，Renderer 只负责 provider 格式。Prompt 系统不做路由判断、不写 memory、不执行工具、不发送消息，也不理解 Motion、Live2D 等插件语义。实际可执行工具仍由 Main Agent 装配到 `func_tool`，不能仅靠 Prompt 中的 tool schema 注册。

插件需要贡献模型可见事实时使用 Prompt Extension Collector；`on_llm_request` 只保留为统一渲染之后的 Core 低层请求钩子。完整边界见 [Prompt Module](./docs/Yakumo/modules/prompt.md)。

---

## 当前状态

| 功能 | 状态 | 说明 |
|------|:----:|------|
| 路由与拟人表达 | 🟡 开发中 | Router、Core Planner 与 Persona 职责独立，关键路径继续验证 |
| 即时表达 | 🟡 开发中 | 已复用统一 Persona Runtime，流式体验继续优化 |
| 长期记忆 | 🟡 开发中 | 框架已搭，部分场景验证 |
| Interaction Middleware | 🟡 开发中 | 主链路已通，部分边界场景仍需收口 |
| 持续人格 Runtime | 🟡 开发中 | 跨 turn 状态、Observation Inbox 与确定性 Gate 已完成，Policy、Heartbeat 尚未接入 |
| 结构化 Prompt | 🟡 开发中 | collect/build/project/profile/layout/tree/render/apply 已跑通，继续物理拆分默认 Layout 并统一工具与 Provider capability |
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
