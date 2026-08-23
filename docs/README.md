# AG99 文档

这个目录包含两层文档：AG99 当前运行时文档，以及为 AstrBot 兼容基础设施保留的部署、平台、Provider 和插件指南。`docs/Yakumo` 是作者 YakumoAki 保留的架构文档命名空间。

## 先读 AG99

- [项目身份](./Yakumo/project-identity.md)：项目名称、定位、与 AstrBot 的关系和术语边界。
- [架构索引](./Yakumo/README.md)：当前模块边界与推荐阅读顺序。
- [当前状态](./Yakumo/current-state.md)：以代码为依据的实现事实。
- [模块索引](./Yakumo/modules/README.md)：Interaction、Prompt、Runtime 等模块说明。
- [Memory](./Yakumo/dev/memory/index.md)：记忆系统的边界与进度。

`docs/Yakumo` 是 AG99 当前运行时的 canonical 文档入口。`current-state.md` 和模块文档描述已经存在的代码；`dev/`、`target-state.md` 及带有 plan/design 标记的页面描述设计或后续工作。

## 兼容基础文档

以下内容主要服务于仍然兼容的 AstrBot 基础设施：

- `docs/zh`：中文的部署、消息平台、模型 Provider、插件和使用指南。
- `docs/en`：对应的英文指南。
- `docs/.vitepress`：文档站点配置与主题。

这些页面可能沿用 `AstrBot` 的 API、配置项和路径名称。它们不是项目品牌声明；涉及 AG99 行为时，以 `docs/Yakumo` 和源码为准。

## 插件作者

AG99 扩展 API 已同步到普通开发文档：

- [Persona Effect（中文）](./zh/dev/star/guides/persona-effects.md)
- [Persona Effect（English）](./en/dev/star/guides/persona-effects.md)
- [Prompt Extension（中文）](./zh/dev/star/guides/prompt-extensions.md)
- [Prompt Extension（English）](./en/dev/star/guides/prompt-extensions.md)

Persona Effect 是结构化表现协议，不是 Agent Tool。Prompt Extension 是目标明确的事实贡献，也不是 LLM Tool。可执行插件工具默认属于 Core，只有明确声明或配置授权后才进入 Persona。

## 上游参考

本仓库源自 AstrBot，并保留兼容基础设施。需要查看上游项目的原始文档或实现时，请访问：

- [AstrBot 上游仓库](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 官方文档](https://docs.astrbot.app/)
