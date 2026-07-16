# AstrBot Docs

这个目录大部分内容仍然来自上游 AstrBot 官方文档，用于保留插件开发、平台接入、部署和功能使用说明。

本 fork 自己的架构说明集中在：

- `docs/Yakumo/`
- `docs/Yakumo/README.md`
- `docs/Yakumo/current-state.md`
- `docs/Yakumo/modules/README.md`
- `docs/Yakumo/upstream-merge-ledger.md`

面向插件作者的 fork 扩展 API 已同步到普通中英文开发文档，而不只存在于 Yakumo 笔记：

- `docs/zh/dev/star/guides/persona-effects.md`
- `docs/en/dev/star/guides/persona-effects.md`
- `docs/zh/dev/star/guides/prompt-extensions.md`
- `docs/en/dev/star/guides/prompt-extensions.md`

Persona Effect 是 Persona 输出协议，不是 Agent Tool。Router 仍只返回固定分类词，不注册工具，也不接收 effect schema。

Prompt Extension 用于在统一 Prompt 管线中贡献模型可见事实。它不是 LLM Tool；`on_llm_request` 则是统一 Prompt Apply 后的 Core 低层请求钩子，不应当作 Router、Planner、Persona 的事实入口。

`docs/Yakumo` 下的 `dev/*`、`target-state.md` 和早期中文详解文档包含历史设计记录，可能落后于当前代码。判断本 fork 与上游差异时，优先看 `README.md`、`docs/Yakumo/current-state.md` 和 `docs/Yakumo/modules/*`。

如果需要查看上游官方文档，请访问：

- https://docs.astrbot.app/
- https://github.com/AstrBotDevs/AstrBot
