# AG99

> 由 YakumoAki 创建，基于 AstrBot 独立演进的 Persona-first 多平台对话 Runtime。

AG99 不是把每一条消息都直接交给模型处理的聊天机器人。它让一个持续存在的 Persona
负责理解对话、保持表达一致性、管理有界状态，并在需要检索、工具调用、定时任务或复杂
执行时，把工作委派给 Core。

[项目身份](./docs/Yakumo/project-identity.md) · [架构文档](./docs/Yakumo/) · [当前状态](./docs/Yakumo/current-state.md) · [问题反馈](https://github.com/murphys7017/AG99/issues)

## AG99 在做什么

- **持续人格**：`Personal Runtime` 跨 turn 管理会话租约、连续对话、冷却、预算和受控观察。
- **统一表达**：即时回复、插件 persona 输出与 Core 任务结果都经过同一个 `Persona Expression`，避免一轮对话出现两套口吻或重复回复。
- **复杂任务委派**：`Personal Response Plan` 决定 `reply`、`delegate` 或允许场景下的 `silent`；只有已委派的任务才进入 `Core Planner` 与 Core 执行层。
- **结构化上下文**：Prompt 按 `collect -> build -> project -> render -> apply` 构建。不同 Agent 只看到自己真正需要的事实与能力。
- **受控主动性**：后台事实先经过 `Observation -> Gate -> Policy -> ActionIntent`，再决定是否表达；观察本身不会直接调用工具或发送消息。

## 工作方式

```text
Platform Adapter
  -> EventBus / Pipeline / Plugin Handler
  -> Personal Runtime
      -> reply -------------------------> Persona Expression -> Output Runtime
      -> delegate -> Core Planner -> Core Execution
                                      -> Persona Expression -> Output Runtime
      -> silent (eligible group only) --> no visible reply
  -> Conversation / Memory / Postprocess
```

Personal 是统一的对外交流窗口；Core 只负责被委派的复杂工作。Core 的结果不能绕过
Persona 或 Output 直接发送到平台。

## 基于 AstrBot，独立演进

AG99 保留 AstrBot 的成熟基础设施：平台适配器、Provider、Pipeline Handler、插件 API、
Dashboard、CLI、配置体系，以及 `astrbot` Python 包和插件前缀。

这不表示行为与上游完全相同。AG99 在 Interaction、Personal Runtime、Persona Expression、
Prompt 和 Core 执行边界上有自己的设计与实现；涉及运行时行为时，以本仓库源码和
[Yakumo 架构文档](./docs/Yakumo/) 为准。

## 快速开始

前提：Python `3.12+`，以及 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

```bash
uv sync
uv run main.py
```

启动后打开 `http://localhost:6185` 进入 Dashboard。首次启动生成的初始账号和密码会输出到
控制台；随后在 Dashboard 中配置 Provider 和消息平台适配器。

源码部署与平台接入仍复用兼容基础文档：

- [源码部署指南](./docs/zh/deploy/astrbot/cli.md)
- [消息平台接入](./docs/zh/platform/start.md)
- [AG99 文档入口](./docs/README.md)

开发 Dashboard 时：

```bash
cd dashboard
pnpm install
pnpm dev
```

## 当前开发状态

AG99 正在持续开发，并以真实运行链路验证为准：

| 领域 | 当前情况 |
| --- | --- |
| Personal Runtime | 跨 turn 状态、连续对话、主动观察与投递反馈已接入，边界场景仍在持续验证。 |
| Persona Expression | 统一可见回复链路已接入，不同 Provider 的结构化输出差异仍在收口。 |
| Structured Prompt | 主数据流已建立，模块职责继续拆分和稳定化。 |
| Core 执行 | 默认 Native Body 已通过显式 `CoreExecutionPort` 接入 Core Head；可替换执行器的 factory、通用运行协调、结果桥与生产验证仍在推进。 |
| 平台验收 | OLV、Cron、Live 等真实成功、取消和迟到结果场景持续验证。 |

AG99 不应被直接视为上游 AstrBot 的稳定替代品。部署到真实平台前，请先在自己的 Provider、
平台适配器和插件组合上完成验证。

## 文档

**使用与部署**

- [项目身份](./docs/Yakumo/project-identity.md)：AG99、YakumoAki 与 AstrBot 的关系。
- [当前状态](./docs/Yakumo/current-state.md)：已经实现的运行时事实。
- [兼容部署与平台文档](./docs/README.md)：Provider、平台和 Dashboard 基础指南。

**插件与扩展**

- [Persona Effect](./docs/zh/dev/star/guides/persona-effects.md)：声明式表现协议，不是 Agent Tool。
- [Prompt Extension](./docs/zh/dev/star/guides/prompt-extensions.md)：向指定 Prompt 目标贡献结构化事实。
- [插件能力模型](./docs/Yakumo/dev/plugin-capability-model-plan.md)：插件 owner、准入、适用性与 target 的设计记录。

**架构与开发**

- [Yakumo 架构索引](./docs/Yakumo/README.md)：模块边界和推荐阅读顺序。
- [Interaction Runtime](./docs/Yakumo/modules/interaction.md)：turn、插件、Core 与输出归属。
- [Structured Prompt](./docs/Yakumo/modules/prompt.md)：规范事实、目标投影和渲染边界。
- [Core 内部执行器替换方案](./docs/Yakumo/dev/internal-executor-replacement-plan.md)：Core 内部替换边界、阶段与验收口径。
- [执行器实施手册](./docs/Yakumo/dev/internal-executor-replacement-implementation-guide.md)：面向编码工作的逐批范围、约束和验证命令。

## 许可证与来源

AG99 基于 AstrBot 独立演进，使用 `AGPL-3.0-or-later` 许可证，并继续遵守适用的 AstrBot
兼容说明。

- [LICENSE](./LICENSE)
- [EULA](./EULA.md)
- [AstrBot 上游仓库](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 官方文档](https://docs.astrbot.app/)
