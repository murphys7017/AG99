# Yakumo Notes

`docs/Yakumo` 记录的是当前这个分支上的 AstrBot 架构笔记、重构方案和实现进度，不是官方主线文档的镜像副本。

如果你想看官方产品说明、部署方式、插件/平台适配器的标准用法，优先看上游官方文档：

- 上游仓库 `https://github.com/AstrBotDevs/AstrBot`
- 官方文档站 `https://docs.astrbot.app/`

如果你想快速了解本 fork 和上游的区别，先看仓库根目录 `README.md`。

如果你想看这个分支到底改了什么、现在做到哪一步、后面准备怎么改，再看 `docs/Yakumo`。

## 和官方主线的区别

当前 `docs/Yakumo` 关注的是“这个分支上的实际代码”和“这套重构中的目标结构”，因此和官方主线有几个关键差异：

### 1. Prompt 链路不是官方那套直拼流程

官方主线更偏向在 `astrbot/core/astr_main_agent.py` 里直接组织模型可见上下文。

这个分支额外推进了一套新的 prompt 子系统，核心代码在 `astrbot/core/prompt/*`，当前方向是：

- 先 collect：把 persona、input、session、policy、memory、history、skills、tools、subagent、knowledge、extension 等信息结构化收集成 `ContextPack`
- 再 select：给后续筛选层预留接口
- 再 render：由 renderer 决定节点结构和模型可见输出
- 再 apply：把 render 结果投影回 `ProviderRequest`

也就是说，这里的 prompt 文档描述的是“新 prompt pipeline 的设计和落地情况”，不是官方旧链路的逐字复述。

### 2. Memory 是这个分支重点推进的新增能力

这个分支额外推进了 `astrbot/core/memory/*`：

- short-term topic / summary
- consolidation
- experience persistence
- long-term memory compose / promote
- projection / document search / vector index

所以 `docs/Yakumo/dev/memory/*` 记录的是这套 memory 子系统的真实实现进度和设计约束，和官方主线并不完全一致。

### 3. 文档里会同时出现“现状”“目标态”“开发中方案”

`docs/Yakumo` 不只写现状，还会保留：

- 当前代码现状
- 目标结构
- 开发计划
- 历史设计文档

因此这里的文档不都表示“已经正式接入主链路”。阅读时要区分：

- `current-state.md` / `modules/*`：偏现状
- `dev/*`：偏设计与实现进度
- `target-state.md` / `prompt-development-plan.md`：偏目标态
- `dev/history/*`：偏历史讨论，不代表当前实现

### 4. Interaction middleware 已进入当前架构线

这个分支新增并持续收口 `astrbot/core/interaction/*`。它不是单纯的
WebChat/Live2D 专用逻辑，而是一个通用 interaction middleware：

- 输入侧：在 core decision 之前完成 turn state、入站媒体 materialization、STT、路由决策。
- 输出侧：接管 interaction turn 的 send / streaming 语义，统一 finalizer、result contributor、TTS、t2i、utterance ledger 与 finalized turn material。
- Completion：middleware 只产出 finalized material 并调度 `AFTER_TURN_COMPLETED` postprocess；memory 写入由 postprocess / memory service 消费同一份 material。
- Voice：core 旧流程和 middleware 新流程共享 `astrbot/core/voice/*`，但 failure policy 由调用方决定。middleware 内部主链路开发期 fail-fast，不把 fallback 当正确性证明。

### 5. 这个分支强调“先接管模型可见输入，再逐步替换旧链路”

尤其在 prompt 方向，这个分支的策略不是一次性把官方链路全部替掉，而是分阶段推进：

- 先把 collect / render / apply 跑通
- 先接管模型可见上下文
- 工具执行、subagent、旧 hook 等链路先尽量复用已有实现
- 再逐步把旧的 prompt 组织逻辑收口

所以你会在代码和文档里同时看到“新 prompt 系统”和“旧 Agent 主链路”并存，这属于当前阶段的刻意设计，不是文档写错。

## 阅读建议

建议按这个顺序看：

1. `docs/Yakumo/current-state.md`
2. `docs/Yakumo/modules/README.md`
3. `docs/Yakumo/modules/prompt.md`
4. `docs/Yakumo/dialog-worker-live-target-state.md`
5. `docs/Yakumo/dev/interaction-middleware-architecture-review-and-plan.md`
6. `docs/Yakumo/dev/memory/index.md`
7. 具体专题文档

## 使用约定

- 这里优先描述“当前分支的真实代码状态”
- 如果文档和代码冲突，以代码为准
- 如果文档写的是目标态，会明确写成 plan / target / dev，而不是伪装成已完成
