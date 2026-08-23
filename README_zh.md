# AG99

> 一个以持续人格、低延迟表达为核心的多平台对话 Runtime。

AG99 是这个仓库当前对外使用的项目名称，由 YakumoAki 创建并基于 AstrBot 独立演进而来。项目保留 AstrBot 的平台适配器、模型 Provider、插件 API、Dashboard 和 CLI 兼容基础设施，同时重新组织了 Personal Runtime、Router、Core Planner、结构化 Prompt、统一 Persona Expression、Memory 与主动观察链路。`docs/Yakumo` 作为作者的架构文档命名空间继续保留。

[项目主页](./README.md) · [项目身份](./docs/Yakumo/project-identity.md) · [架构文档](./docs/Yakumo/) · [问题反馈](https://github.com/murphys7017/AG99/issues)

## AG99 与上游的区别

AG99 不是只修改默认配置的 AstrBot 分支，而是在兼容基础设施之上维护自己的交互运行时：

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

- **Personal Runtime**：跨 turn 保留有界状态，管理准入、会话租约、连续对话窗口、冷却、预算和主动观察。
- **Router**：只做轻量的 `persona / hybrid / silent` 判断；`silent` 仅对有界群聊候选开放，只取消尚未发送的表达。
- **Core Planner**：独立判断 hybrid 是否需要执行层，不复用 Router 的模型决策或 Prompt。
- **Persona Expression**：所有用户可见自然语言统一经过同一个表达入口，即时回复、插件人格输出和 Core 结果不会各走一套文案生成器。
- **结构化 Prompt**：一次采集规范事实，再投影到 Router、Planner、Persona 和 Core，各 Provider 只负责格式渲染。
- **主动观察**：遵循 `Observation -> Gate -> Policy -> ActionIntent -> Persona -> Output`，不会直接调用 Core、工具或 Output。

普通消息可以先得到即时 Persona 表达；只有 Router 选择 `hybrid` 且 Core Planner 判断确有必要时，才进入 Core。Core 的结果仍然回到同一个 Persona Expression。

## 兼容边界

以下命名会有意保留：

- Python 包和导入路径：`astrbot`
- CLI 入口：`astrbot`
- 插件前缀：`astrbot_plugin_`
- 现有平台适配器、Provider、Pipeline Handler、插件 API 和 Dashboard 路由

兼容不等于行为完全相同。当前代码和 [Yakumo 架构文档](./docs/Yakumo/) 是本仓库的事实来源；`docs/zh`、`docs/en` 中的部署、平台、Provider 和插件页面主要承担兼容基础文档的职责。

## 当前状态

AG99 仍处于持续开发和真实链路验证阶段。

| 领域 | 状态 |
| --- | --- |
| Interaction Middleware | 主链路已实现，边界场景持续验证 |
| Personal Runtime | 跨 turn 状态、Observation Intake、Gate、Policy 边界和投递反馈已接入 |
| Router / Core Planner | 职责分离和 fail-closed 边界已接入 |
| Persona Expression | 统一可见回复链路已接入，Provider 能力差异仍需持续收口 |
| 结构化 Prompt | collect/build/project/render/apply 主链路已实现，模块仍在拆分稳定化 |
| AstrBot 兼容 | 在明确记录的范围内保持平台、Provider、插件和 CLI 兼容 |

请先在自己的平台适配器和插件上完成验证，不要把本仓库直接视为上游 AstrBot 的稳定替代品。

## 快速开始

```bash
uv sync
uv run main.py
```

默认 WebUI/API 地址为 `http://localhost:6185`。如需启动 Dashboard 开发服务器：

```bash
cd dashboard
pnpm install
pnpm dev
```

## 文档入口

- [项目身份](./docs/Yakumo/project-identity.md)：名称、定位、兼容边界和术语。
- [Yakumo 架构索引](./docs/Yakumo/README.md)：当前边界和推荐阅读顺序。
- [当前状态](./docs/Yakumo/current-state.md)：已经实现的代码事实。
- [Interaction Middleware](./docs/Yakumo/modules/interaction.md)：消息 turn、插件和输出归属。
- [结构化 Prompt](./docs/Yakumo/modules/prompt.md)：规范事实和目标投影。
- [Memory 设计](./docs/Yakumo/dev/memory/index.md)：记忆边界与进度。
- [兼容基础文档](./docs/)：部署、平台、Provider 和插件指南。

`dev/` 与 `target-state.md` 默认表示设计或后续计划，除非文档明确标注为当前实现。文档与代码冲突时，以代码为准并同步修订当前状态记录。

## 许可证

AG99 继续使用上游项目的 `AGPL-3.0-or-later` 许可证，并遵守适用的 AstrBot 兼容说明，详见 [LICENSE](./LICENSE) 和 [EULA.md](./EULA.md)。
