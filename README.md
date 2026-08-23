# AG99

> 由 YakumoAki 创建、基于 AstrBot 独立演进的 Persona-first 多平台对话 Runtime。

AG99 的目标不是把每条消息简单地交给一个 Agent，而是让一个持续存在的 Persona 管理对话、表达、记忆和受控的主动观察；需要完成实质任务时，再把工作交给 Core 执行层。

[项目身份](./docs/Yakumo/project-identity.md) · [架构文档](./docs/Yakumo/) · [当前状态](./docs/Yakumo/current-state.md) · [问题反馈](https://github.com/murphys7017/AG99/issues)

## 核心流程

```text
平台适配器
  -> EventBus / Pipeline / Handler
  -> Interaction Middleware
  -> Personal Runtime + Router
  -> Core Planner
  -> Core 执行层
  -> Persona Expression
  -> Output Runtime
  -> Conversation / Memory
```

- **Personal Runtime**：跨 turn 管理人格状态、会话租约、连续对话、冷却、预算和主动观察。
- **Router**：只判断 `persona / hybrid / silent`；`silent` 仅对有界群聊候选开放，并且只取消尚未发送的表达。
- **Core Planner**：独立判断是否需要进入 Core，不复用 Router 的模型决策或 Prompt。
- **Persona Expression**：所有用户可见自然语言统一经过同一个表达入口，即时回复和 Core 结果不会各走一套文案生成器。
- **Structured Prompt**：通过 `collect -> build -> project -> render -> apply` 形成目标明确的模型上下文。
- **主动观察**：遵循 `Observation -> Gate -> Policy -> ActionIntent -> Persona -> Output`，不会直接调用 Core、工具或发送消息。

## 基于 AstrBot，但不是简单 Fork

AG99 基于 AstrBot 代码和生态独立演进，同时保留明确的兼容边界：

- Python 包和导入路径继续使用 `astrbot`。
- CLI 入口继续使用 `astrbot`。
- 插件前缀继续使用 `astrbot_plugin_`。
- 平台适配器、Provider、Pipeline Handler、插件 API、Dashboard 和配置体系继续兼容。

兼容不代表行为完全相同。AG99 的当前源码和 [Yakumo 架构文档](./docs/Yakumo/) 是本仓库的事实来源；AstrBot 官方文档主要作为共享部署、平台和插件能力的兼容参考。

## 当前状态

AG99 仍处于持续开发和真实链路验证阶段：

| 领域 | 状态 |
| --- | --- |
| Interaction Middleware | 主链路已实现，边界场景持续验证 |
| Personal Runtime | 跨 turn 状态、Observation Intake、Gate、Policy 边界和投递反馈已接入 |
| Router / Core Planner | 职责分离和 fail-closed 边界已接入 |
| Persona Expression | 统一可见回复链路已接入，Provider 差异仍在收口 |
| Structured Prompt | 主链路已实现，模块仍在拆分稳定化 |

请先在自己的平台适配器和插件上完成验证，不要把 AG99 直接视为上游 AstrBot 的稳定替代品。

## 快速开始

```bash
uv sync
uv run main.py
```

默认 WebUI/API 地址：`http://localhost:6185`

如需启动 Dashboard 开发服务器：

```bash
cd dashboard
pnpm install
pnpm dev
```

## 文档入口

- [项目身份](./docs/Yakumo/project-identity.md)：AG99、YakumoAki 与 AstrBot 的关系。
- [架构索引](./docs/Yakumo/README.md)：当前模块边界和推荐阅读顺序。
- [当前状态](./docs/Yakumo/current-state.md)：已经实现的代码事实。
- [Interaction Middleware](./docs/Yakumo/modules/interaction.md)：消息 turn、插件和输出归属。
- [结构化 Prompt](./docs/Yakumo/modules/prompt.md)：规范事实和目标投影。
- [Memory 设计](./docs/Yakumo/dev/memory/index.md)：记忆边界与进度。
- [兼容基础文档](./docs/)：部署、平台、Provider 和插件指南。

## 许可证与来源

AG99 基于 AstrBot 独立演进，继续遵守适用的 AstrBot 兼容说明，并使用 `AGPL-3.0-or-later` 许可证。

- [LICENSE](./LICENSE)
- [EULA](./EULA.md)
- [AstrBot 上游仓库](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 官方文档](https://docs.astrbot.app/)
